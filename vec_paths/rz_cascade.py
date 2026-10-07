"""
vec_paths/rz_cascade.py — RZ (Reverse Zone) cascade entry + exit, vectorized.

SOURCE (READ-ONLY references, not imported):
  1. ez_manage.py:32735-32763 — RZ_BREAKOUT entry trigger (bb_pct_b_1h in band
     just outside extreme zone → entry without alignment gates).
  2. wt_dc_exit_scorer.py:39-103 — score_exit() / EXIT_SCORER_DC_EXTREME path,
     N-of-5 condition counter (1h wt cross, 4h wt trend, D wt trend, K extreme,
     DC position extreme on 1h or 4h).
  3. old/v8_quick_engine.py:1802-1906 — compute_rz_cascade_signals(npz, n,
     is_long, cfg). Per-TF at_upper / at_lower / breakout / reversal masks,
     then LTF breakout + ≥MIN_TF_ALIGN HTFs aligned → entry signal; any TF
     reverse against → exit signal.

VEC RULES:
  - Operate on NPZ arrays only (close_<tf>, dc_high_<tf>, dc_low_<tf>,
    wt1_<tf>, wt2_<tf>, wt_velocity_<tf>, dc_position_<tf>, stoch_k_<tf>,
    bb_pct_b_1h). All bar-vectorized; no Python loop over n.
  - Side-aware (is_long).
  - Honor cfg.RZ_BREAKOUT_ENTRY_ENABLED / cfg.EXIT_SCORER_DC_EXTREME etc. as
    explicit gates. When the cluster master flag is False, return zero masks.
  - TF skip: if wt or dc fields for a TF are all-zero (not precomputed), drop
    that TF from the alignment count rather than fabricating signals.

Knobs honored (mirrored from live + active_config.json):
  RZ_BREAKOUT_ENTRY_ENABLED, RZ_BOT_BB_THRESHOLD, RZ_TOP_BB_THRESHOLD,
  RZ_BREAKOUT_BAND, RZ_CASCADE_ENABLED, RZ_CASCADE_MIN_TF_ALIGN,
  RZ_CASCADE_EXIT_MIN_REV_TFS, RZ_CASCADE_EXIT_ANY_TF,
  RZ_CASCADE_WT_DELTA_MIN, RZ_CASCADE_VEL_MIN,
  RZ_CASCADE_AT_RZ_BAND_PCT, RZ_CASCADE_HIGH_LOOKBACK,
  RZ_CASCADE_REQUIRE_NEW_HIGH, RZ_CASCADE_USE_W_M,
  EXIT_SCORER_DC_EXTREME, EXIT_SCORER_K_EXTREME,
  EXIT_SCORER_MIN_CONDITIONS, EXIT_SCORER_ENABLED.
"""
from __future__ import annotations
from typing import Dict, Any
import numpy as np


def _safe(npz: Dict[str, Any], key: str, n: int, default: float = 0.0) -> np.ndarray:
    """Fetch NPZ field as float32 of length n; substitute default if missing/nan."""
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default, dtype=np.float32)
    out = np.asarray(arr, dtype=np.float32)
    if out.size == 0:
        return np.full(n, default, dtype=np.float32)
    if out.size != n:
        # length mismatch (rare; precompute alignment normally matches base ts).
        if out.size > n:
            out = out[:n]
        else:
            pad = np.full(n - out.size, default, dtype=np.float32)
            out = np.concatenate([out, pad])
    if np.isnan(out).any():
        out = np.nan_to_num(out, nan=default).astype(np.float32)
    return out


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    n = arr.shape[0]
    if window <= 1 or n == 0:
        return arr.copy()
    out = np.empty(n, dtype=arr.dtype)
    for i in range(n):
        lo = max(0, i - window + 1)
        out[i] = arr[lo:i + 1].max() if i >= lo else arr[i]
    return out


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    n = arr.shape[0]
    if window <= 1 or n == 0:
        return arr.copy()
    out = np.empty(n, dtype=arr.dtype)
    for i in range(n):
        lo = max(0, i - window + 1)
        out[i] = arr[lo:i + 1].min() if i >= lo else arr[i]
    return out


# ────────────────────────────────────────────────────────────────────────────
# 1) RZ_BREAKOUT entry — mirrors ez_manage.py:32735+ (live cluster).
# ────────────────────────────────────────────────────────────────────────────
def check_rz_breakout_entry_vec(
    npz: Dict[str, Any], n: int, is_long: bool, cfg
) -> np.ndarray:
    """Return shape (n,) bool mask where RZ_BREAKOUT entry fires.

    Live rule (ez_manage.py:32735-32746):
      - LONG: rz_bot <= bb_pct_b_1h <= rz_bot + band
      - SHORT: rz_top - band <= bb_pct_b_1h <= rz_top
      Defaults: rz_top=0.85, rz_bot=0.15, band=0.05.

    Gated by RZ_BREAKOUT_ENTRY_ENABLED.
    """
    out = np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "RZ_BREAKOUT_ENTRY_ENABLED", False)):
        return out
    rz_bb = _safe(npz, "bb_pct_b_1h", n, default=0.5)
    rz_top = float(getattr(cfg, "RZ_TOP_BB_THRESHOLD", 0.85))
    rz_bot = float(getattr(cfg, "RZ_BOT_BB_THRESHOLD", 0.15))
    rz_band = float(getattr(cfg, "RZ_BREAKOUT_BAND", 0.05))
    if is_long:
        out = (rz_bb >= rz_bot) & (rz_bb <= (rz_bot + rz_band))
    else:
        out = (rz_bb >= (rz_top - rz_band)) & (rz_bb <= rz_top)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 2) RZ exit — wt_dc_exit_scorer.score_exit() vectorized, EXIT_SCORER_DC_EXTREME
#    path. Fires when N-of-5 conditions hit (default 5/strict).
# ────────────────────────────────────────────────────────────────────────────
def score_wt_dc_exit_vec(
    npz: Dict[str, Any], n: int, is_long: bool, cfg
) -> np.ndarray:
    """Vector mirror of ``wt_dc_exit_scorer.score_exit`` active N-of-5 branch."""
    scores = np.zeros(n, dtype=np.float64)
    min_cond = int(getattr(cfg, "EXIT_SCORER_MIN_CONDITIONS", 5))
    k_extreme = float(getattr(cfg, "EXIT_SCORER_K_EXTREME", 75.0))
    dc_extreme = float(getattr(cfg, "EXIT_SCORER_DC_EXTREME", 0.80))
    partial_score = float(getattr(cfg, "EXIT_SCORER_PARTIAL_SCORE", 40.0))
    full_score = float(getattr(cfg, "EXIT_SCORER_FULL_SCORE", 100.0))
    wt1_1h = _safe(npz, "wt1_1h", n)
    wt2_1h = _safe(npz, "wt2_1h", n)
    wt1_4h = _safe(npz, "wt1_4h", n)
    wt2_4h = _safe(npz, "wt2_4h", n)
    wt1_D = _safe(npz, "wt1_D", n)
    wt2_D = _safe(npz, "wt2_D", n)
    k_1h = _safe(npz, "stoch_k_1h", n, default=50.0)
    k_4h = _safe(npz, "stoch_k_4h", n, default=50.0)
    dc_pos_1h = _safe(npz, "dc_position_1h", n, default=0.5)
    dc_pos_4h = _safe(npz, "dc_position_4h", n, default=0.5)
    if is_long:
        conditions = (
            wt1_1h < wt2_1h,
            wt1_4h < wt2_4h,
            wt1_D < wt2_D,
            (k_1h >= k_extreme) | (k_4h >= k_extreme),
            (dc_pos_1h >= dc_extreme) | (dc_pos_4h >= dc_extreme),
        )
    else:
        conditions = (
            wt1_1h > wt2_1h,
            wt1_4h > wt2_4h,
            wt1_D > wt2_D,
            (k_1h <= 100.0 - k_extreme) | (k_4h <= 100.0 - k_extreme),
            (dc_pos_1h <= 1.0 - dc_extreme) | (dc_pos_4h <= 1.0 - dc_extreme),
        )
    hits = sum(condition.astype(np.int8) for condition in conditions)
    scores[hits >= max(min_cond - 1, 3)] = partial_score
    scores[hits >= min_cond] = full_score
    return scores


def check_rz_exit_vec(
    npz: Dict[str, Any], n: int, is_long: bool, cfg
) -> np.ndarray:
    """Return shape (n,) bool mask where RZ multi-TF EXIT scorer fires (full hit).

    Vectorized port of wt_dc_exit_scorer.score_exit, N-of-5 STRICT branch:
      c1 = 1h WT cross against side (vec uses wt1<wt2 fallback — same as live).
      c2 = 4h WT trending against side.
      c3 = D WT trending against side.
      c4 = K extreme on 1h or 4h (LONG: k >= EXIT_SCORER_K_EXTREME).
      c5 = DC position extreme on 1h or 4h (LONG: dc_pos >= EXIT_SCORER_DC_EXTREME).
    Fires when sum(c1..c5) >= EXIT_SCORER_MIN_CONDITIONS.

    Gated by EXIT_SCORER_ENABLED (live default True on tradier, False off crypto).
    """
    if not bool(getattr(cfg, "EXIT_SCORER_ENABLED", False)):
        return np.zeros(n, dtype=bool)
    return score_wt_dc_exit_vec(npz, n, is_long, cfg) >= float(
        getattr(cfg, "EXIT_SCORER_FULL_SCORE", 100.0)
    )


# ────────────────────────────────────────────────────────────────────────────
# 3) RZ_CASCADE entry/exit — port of old/v8_quick_engine.py:1802
#    compute_rz_cascade_signals (read-only reference, ported from scratch).
# ────────────────────────────────────────────────────────────────────────────
def compute_rz_cascade_signals_vec(
    npz: Dict[str, Any], n: int, is_long: bool, cfg, ltf: str = "3m"
):
    """Return (entry_sig: bool[n], exit_sig: bool[n]) for RZ cascade.

    Per-TF detection:
      at_upper      = close at dc_high (resistance RZ band)
      at_lower      = close at dc_low (support RZ band)
      breakout_up   = close > dc_high_prev AND wt_delta > min AND vel > min
                      (+ new close high if RZ_CASCADE_REQUIRE_NEW_HIGH)
      breakout_down = mirror for shorts
      reverse_down  = was at_upper, now dropping, wt_delta < 0, vel < 0
      reverse_up    = mirror for longs

    Entry (LONG): LTF breakout_up AND ≥MIN_TF_ALIGN other TFs aligned
                  (breakout_up | reverse_up).
    Exit  (LONG): if EXIT_ANY_TF, count reverse_down across TFs and fire
                  when count >= EXIT_MIN_REV_TFS; else use LTF reverse_down.

    Gated by RZ_CASCADE_ENABLED (returns all-False masks when False).
    """
    zero = np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "RZ_CASCADE_ENABLED", False)):
        return zero, zero

    tf_fields = {"LTF": ltf, "15m": "15m", "1h": "1h", "4h": "4h", "D": "D"}
    if bool(getattr(cfg, "RZ_CASCADE_USE_W_M", False)):
        tf_fields["W"] = "W"
        tf_fields["M"] = "M"

    wt_delta_min = float(getattr(cfg, "RZ_CASCADE_WT_DELTA_MIN", 0.1))
    vel_min = float(getattr(cfg, "RZ_CASCADE_VEL_MIN", 0.1))
    high_lb = int(getattr(cfg, "RZ_CASCADE_HIGH_LOOKBACK", 20))
    require_new_high = bool(getattr(cfg, "RZ_CASCADE_REQUIRE_NEW_HIGH", False))
    at_rz_band = float(getattr(cfg, "RZ_CASCADE_AT_RZ_BAND_PCT", 1.0)) / 100.0

    per_tf: Dict[str, Dict[str, np.ndarray]] = {}
    for tf_label, tf in tf_fields.items():
        close = _safe(npz, f"close_{tf}", n)
        dc_hi = _safe(npz, f"dc_high_{tf}", n)
        dc_lo = _safe(npz, f"dc_low_{tf}", n)
        wt1 = _safe(npz, f"wt1_{tf}", n)
        wt2 = _safe(npz, f"wt2_{tf}", n)
        wt_vel = _safe(npz, f"wt_velocity_{tf}", n)
        # Skip TF when its core fields are all zero (not precomputed for this sym).
        if (wt1.sum() == 0 and wt2.sum() == 0) or (dc_hi.sum() == 0 and dc_lo.sum() == 0):
            continue
        wt_delta = wt1 - wt2
        dc_hi_prev = np.empty_like(dc_hi)
        dc_hi_prev[0] = dc_hi[0]
        dc_hi_prev[1:] = dc_hi[:-1]
        dc_lo_prev = np.empty_like(dc_lo)
        dc_lo_prev[0] = dc_lo[0]
        dc_lo_prev[1:] = dc_lo[:-1]
        at_upper = (dc_hi_prev > 0) & (close >= dc_hi_prev * (1.0 - at_rz_band))
        at_lower = (dc_lo_prev > 0) & (close <= dc_lo_prev * (1.0 + at_rz_band))
        breakout_up = (
            (dc_hi_prev > 0)
            & (close > dc_hi_prev)
            & (wt_delta > wt_delta_min)
            & (wt_vel > vel_min)
        )
        breakout_down = (
            (dc_lo_prev > 0)
            & (close < dc_lo_prev)
            & (wt_delta < -wt_delta_min)
            & (wt_vel < -vel_min)
        )
        if require_new_high and high_lb > 1:
            px_max_lb = _rolling_max(close, high_lb)
            px_min_lb = _rolling_min(close, high_lb)
            px_max_prev = np.empty_like(px_max_lb)
            px_max_prev[0] = px_max_lb[0]
            px_max_prev[1:] = px_max_lb[:-1]
            px_min_prev = np.empty_like(px_min_lb)
            px_min_prev[0] = px_min_lb[0]
            px_min_prev[1:] = px_min_lb[:-1]
            breakout_up = breakout_up & (close > px_max_prev)
            breakout_down = breakout_down & (close < px_min_prev)
        close_prev = np.empty_like(close)
        close_prev[0] = close[0]
        close_prev[1:] = close[:-1]
        at_upper_prev = np.empty_like(at_upper)
        at_upper_prev[0] = at_upper[0]
        at_upper_prev[1:] = at_upper[:-1]
        at_lower_prev = np.empty_like(at_lower)
        at_lower_prev[0] = at_lower[0]
        at_lower_prev[1:] = at_lower[:-1]
        reverse_down = at_upper_prev & (close < close_prev) & (wt_delta < 0) & (wt_vel < 0)
        reverse_up = at_lower_prev & (close > close_prev) & (wt_delta > 0) & (wt_vel > 0)
        per_tf[tf_label] = {
            "breakout_up": breakout_up,
            "breakout_down": breakout_down,
            "reverse_up": reverse_up,
            "reverse_down": reverse_down,
        }

    if not per_tf or "LTF" not in per_tf:
        return zero, zero

    min_align = int(getattr(cfg, "RZ_CASCADE_MIN_TF_ALIGN", 1))
    exit_any_tf = bool(getattr(cfg, "RZ_CASCADE_EXIT_ANY_TF", True))
    min_rev_tfs = int(getattr(cfg, "RZ_CASCADE_EXIT_MIN_REV_TFS", 2))

    if is_long:
        ltf_break = per_tf["LTF"]["breakout_up"]
        align_count = np.zeros(n, dtype=np.int16)
        for tf_label, sigs in per_tf.items():
            if tf_label == "LTF":
                continue
            align_count = align_count + (sigs["breakout_up"] | sigs["reverse_up"]).astype(np.int16)
        entry_sig = ltf_break & (align_count >= min_align)
        if exit_any_tf:
            rev_count = np.zeros(n, dtype=np.int16)
            for sigs in per_tf.values():
                rev_count = rev_count + sigs["reverse_down"].astype(np.int16)
            exit_sig = rev_count >= min_rev_tfs
        else:
            exit_sig = per_tf["LTF"]["reverse_down"]
    else:
        ltf_break = per_tf["LTF"]["breakout_down"]
        align_count = np.zeros(n, dtype=np.int16)
        for tf_label, sigs in per_tf.items():
            if tf_label == "LTF":
                continue
            align_count = align_count + (sigs["breakout_down"] | sigs["reverse_down"]).astype(np.int16)
        entry_sig = ltf_break & (align_count >= min_align)
        if exit_any_tf:
            rev_count = np.zeros(n, dtype=np.int16)
            for sigs in per_tf.values():
                rev_count = rev_count + sigs["reverse_up"].astype(np.int16)
            exit_sig = rev_count >= min_rev_tfs
        else:
            exit_sig = per_tf["LTF"]["reverse_up"]
    return entry_sig.astype(bool), exit_sig.astype(bool)
