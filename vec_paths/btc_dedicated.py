"""
vec_paths/btc_dedicated.py — Vectorised BTC_DEDICATED decision loop.

LIVE SOURCE: btc_loop.py (decision module) + ez_positions_quick.py:1825-1937 (caller).

The live module is the source of truth — vec_paths mirrors its decision shape
bar-by-bar so v8_vec_sweep can simulate BTC_* knobs without scalar overhead.

PARITY MAPPING (live btc_loop fn → vec mask):

  should_enter_btc_long(rz, accel, div, cfg)
    → build_btc_long_entry_mask(npz, n, cfg)
        Returns bool[n] = True at bars where the live function would return ok=True
        for entry on the LONG side.

  should_enter_btc_short(...)
    → build_btc_short_entry_mask(npz, n, cfg)  (mirror)

  should_exit_btc(position, accel, div, wt_against_count, ...)
    → build_btc_exit_mask(npz, n, is_long, cfg)
        Returns bool[n] = True at bars where the live function would return ok=True
        for exit (LOSS-tier; BREAKOUT/BOUNCE branches handled inline).

  detect_btc_breakout(price, prev_dc_high_3m, prev_dc_low_3m, accel, div, htf, cfg)
    → build_btc_breakout_mask(npz, n, side, cfg)
        Returns bool[n] = True at bars where the LONG/SHORT breakout fires.

VEC APPROXIMATION CAVEATS:
  - Divergence: live aggregates pivot-divergence across 5 indicators × 5 TFs.
    Vec approximates with WT-vs-price divergence at each TF (the dominant
    contributor in live; RSI/MFI/OBV/CVD pivots are off NPZ scope). The
    BTC_DIVERGENCE_MIN_TF knob is honored (3m→0, 15m→1, 1h→2, 4h→3, D→4).
  - WT-velocity-prev derivation: live computes v_prev = velocity - acceleration
    (see ez_positions_quick.py:1842-1844 — wt_velocity_*_prev never existed in
    NPZ); vec mirrors that identity.
  - Red Zone: live uses fib + round + wt_dc_zone proxy (bb_pct_b_4h tier).
    Vec uses bb_pct_b_4h tier as the RZ flag (TOP/BOTTOM/BASELINE matches the
    live caller's `wt_dc_zone` proxy at ez_positions_quick.py:1859-1865).
    Fib levels aren't materialised in NPZ; round-number proximity is computed
    from `close` directly.

NOTE: All builders are SAFE to call with BTC_DEDICATED_ENABLED=False; they
short-circuit to all-False masks (no entries / no exits generated).
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np


# TF ordering must match btc_loop._TF_INDEX
_TF_INDEX = {"3m": 0, "15m": 1, "1h": 2, "4h": 3, "D": 4}
_TFS = ("3m", "15m", "1h", "4h", "D")


def _get(npz: Dict[str, np.ndarray], key: str, n: int, default: float = 0.0) -> np.ndarray:
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default, dtype=np.float32)
    arr = np.asarray(arr, dtype=np.float32)
    if arr.size != n:
        # Pad / truncate defensively
        out = np.full(n, default, dtype=np.float32)
        m = min(n, arr.size)
        out[:m] = arr[:m]
        return out
    return np.nan_to_num(arr, nan=default, posinf=default, neginf=default)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — accel ramp + red zone + divergence (vectorised)
# ─────────────────────────────────────────────────────────────────────────────


def _compute_accel_ramp_masks(npz: Dict[str, np.ndarray], n: int, cfg: Any) -> Dict[str, np.ndarray]:
    """Vectorised port of btc_loop.compute_accel_ramp.

    For each TF, derive v_prev = velocity - acceleration (matches live).
    Returns dict with:
      bull_aligned_tfs: int[n]  — count of TFs where (v > v_prev > 0) (or (v > v_prev) when require_positive=False)
      bear_aligned_tfs: int[n]
      side_bull:        bool[n] — bull_aligned > bear_aligned AND bull_aligned > 0
      side_bear:        bool[n] — bear_aligned > bull_aligned AND bear_aligned > 0
    """
    require_positive = bool(getattr(cfg, "BTC_ACCEL_RAMP_REQUIRE_POSITIVE", True))
    bull = np.zeros(n, dtype=np.int8)
    bear = np.zeros(n, dtype=np.int8)
    for tf in _TFS:
        v = _get(npz, f"wt_velocity_{tf}", n, 0.0)
        a = _get(npz, f"wt_acceleration_{tf}", n, 0.0)
        v_prev = v - a
        if require_positive:
            bull_tf = (v > v_prev) & (v_prev > 0)
            bear_tf = (v < v_prev) & (v_prev < 0)
        else:
            bull_tf = (v > v_prev)
            bear_tf = (v < v_prev)
        bull += bull_tf.astype(np.int8)
        bear += bear_tf.astype(np.int8)
    side_bull = (bull > bear) & (bull > 0)
    side_bear = (bear > bull) & (bear > 0)
    return {
        "bull_aligned_tfs": bull,
        "bear_aligned_tfs": bear,
        "side_bull": side_bull,
        "side_bear": side_bear,
    }


def _compute_red_zone_mask(npz: Dict[str, np.ndarray], n: int, cfg: Any) -> np.ndarray:
    """Vectorised port of red-zone "active" flag.

    Live caller derives wt_dc_zone from bb_pct_b_4h (TOP if >=0.85, BOTTOM if <=0.15,
    BASELINE otherwise — ez_positions_quick.py:1859-1865). All three are "active"
    per build_red_zone_state (any in {TOP/BOTTOM/BASELINE} = active). Vec replicates
    by treating bb_pct_b_4h presence > 0 as RZ-active (always true on real data).

    Additionally honors round-number proximity (BTC_RZ_USE_ROUND) — if disabled,
    only the wt_dc tier flag drives active. The bb_pct_b_4h proxy = always-active
    on a real instrument, so RZ.active is effectively True everywhere on BTC.
    This matches the live behaviour observed in /data/history (the RZ gate rarely
    blocks BTC since the bb_pct_b_4h proxy almost always classifies into one of
    TOP/BOTTOM/BASELINE).
    """
    bb_4h = _get(npz, "bb_pct_b_4h", n, 0.5)
    wt_dc_active = (bb_4h >= 0.85) | (bb_4h <= 0.15) | ((bb_4h > 0.15) & (bb_4h < 0.85))
    # The line above is always True for finite bb_pct_b_4h; preserve as parity placeholder.
    # Disabling RZ entirely is handled by BTC_RZ_AS_BOOST_ENABLED in the caller.
    use_round = bool(getattr(cfg, "BTC_RZ_USE_ROUND", True))
    use_wt_dc = bool(getattr(cfg, "BTC_RZ_USE_WT_DC", True))
    active = np.zeros(n, dtype=bool)
    if use_wt_dc:
        active = active | wt_dc_active
    if use_round:
        # Round-number proximity on `close`. Use BTC defaults — caller can override.
        close = _get(npz, "close", n, 0.0)
        primary_inc = float(getattr(cfg, "BTC_ROUND_INC_PRIMARY_USD", 5000.0))
        secondary_inc = float(getattr(cfg, "BTC_ROUND_INC_SECONDARY_USD", 1000.0))
        proximity_pct = float(getattr(cfg, "BTC_RZ_PROXIMITY_PCT", 0.5)) / 100.0
        with np.errstate(divide="ignore", invalid="ignore"):
            # Distance to nearest primary band
            mod_primary = np.mod(close, primary_inc)
            d_primary = np.minimum(mod_primary, primary_inc - mod_primary)
            near_primary = (d_primary / np.maximum(close, 1e-9)) <= proximity_pct
            mod_secondary = np.mod(close, secondary_inc)
            d_secondary = np.minimum(mod_secondary, secondary_inc - mod_secondary)
            near_secondary = (d_secondary / np.maximum(close, 1e-9)) <= proximity_pct
        active = active | near_primary | near_secondary
    return active


def _compute_divergence_counts(
    npz: Dict[str, np.ndarray], n: int, cfg: Any
) -> Dict[str, np.ndarray]:
    """Vectorised approximation of WT divergence across TFs.

    Approximation: for each TF, compute pivot-style divergence with a fixed
    lookback. Bull div = price made a new low in the window but WT did not.
    Bear div = price made a new high but WT did not.

    Lookback per TF: BTC_DIVERGENCE_LOOKBACK_BARS (default 20).
    BTC_DIVERGENCE_MIN_TF gates which TFs are counted.

    Returns:
      bull_inds_aligned: int[n]  — count of TFs (0..5) with bull divergence
      bear_inds_aligned: int[n]
    """
    lookback = int(getattr(cfg, "BTC_DIVERGENCE_LOOKBACK_BARS", 20))
    min_tf = str(getattr(cfg, "BTC_DIVERGENCE_MIN_TF", "3m"))
    min_idx = _TF_INDEX.get(min_tf, 0)
    close = _get(npz, "close", n, 0.0)
    bull_count = np.zeros(n, dtype=np.int8)
    bear_count = np.zeros(n, dtype=np.int8)
    for tf in _TFS:
        if _TF_INDEX[tf] < min_idx:
            continue
        wt1 = _get(npz, f"wt1_{tf}", n, 0.0)
        if not np.any(wt1):
            continue
        # Vectorised rolling-min / rolling-max via shifts
        # Window slice [i-lookback : i] — pivot from prior window
        # We compute rolling min/max of price and the WT value at the pivot index.
        # For speed, approximate "pivot WT value" with min/max WT of same window
        # — sufficient for ranking divergence presence.
        p_min = _rolling_min(close, lookback)
        p_max = _rolling_max(close, lookback)
        w_min = _rolling_min(wt1, lookback)
        w_max = _rolling_max(wt1, lookback)
        # Bull div: price made NEW low (p_now < p_min from prior window)
        #           AND WT failed to make new low (wt_now > w_min)
        # Bear div mirror.
        bull_tf = (close < p_min) & (wt1 > w_min)
        bear_tf = (close > p_max) & (wt1 < w_max)
        bull_count += bull_tf.astype(np.int8)
        bear_count += bear_tf.astype(np.int8)
    return {
        "bull_inds_aligned": bull_count,
        "bear_inds_aligned": bear_count,
    }


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling-min over `window` bars (prior window, EXCLUSIVE of bar i).
    Result at index i = min(arr[max(0, i-window):i]). For i<1 returns arr[0].
    """
    n = arr.size
    if window <= 0 or n == 0:
        return arr.copy()
    out = np.empty(n, dtype=arr.dtype)
    # Use a simple stride approach; for length-N typical (<= 200k bars on 3m × 4y)
    # this is acceptable. We compute a shifted rolling-min using minimum.accumulate
    # over reverse-cumulative windows — but easiest is via np.lib.stride_tricks.
    # Fall back to plain loop in chunks for clarity + correctness.
    if n <= 1:
        out[:] = arr
        return out
    # Use pandas-style approach: shift-by-1 then rolling-min
    # Vectorised via numpy stride: build a (n, window) view padded with arr[0].
    pad = np.full(window, np.inf, dtype=arr.dtype)
    shifted = np.concatenate([pad, arr[:-1]])  # exclusive of current bar
    # Rolling min over `window` of `shifted`
    # Use np.minimum.accumulate trick: NO, that's prefix min. Use sliding view.
    try:
        from numpy.lib.stride_tricks import sliding_window_view as _swv
        view = _swv(shifted, window)
        rmin = view.min(axis=1)
        out = rmin[: n]
        # For first (window-1) positions the window includes inf padding — they'll
        # still be valid as the surviving real values dominate; positions where ALL
        # are inf produce inf → coerce to current arr value.
        out = np.where(np.isfinite(out), out, arr)
    except Exception:
        # Fallback loop
        out[0] = arr[0]
        for i in range(1, n):
            lo = max(0, i - window)
            out[i] = arr[lo:i].min() if i > lo else arr[i]
    return out


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    n = arr.size
    if window <= 0 or n == 0:
        return arr.copy()
    if n <= 1:
        return arr.copy()
    pad = np.full(window, -np.inf, dtype=arr.dtype)
    shifted = np.concatenate([pad, arr[:-1]])
    try:
        from numpy.lib.stride_tricks import sliding_window_view as _swv
        view = _swv(shifted, window)
        rmax = view.max(axis=1)
        out = rmax[: n]
        out = np.where(np.isfinite(out), out, arr)
    except Exception:
        out = np.empty(n, dtype=arr.dtype)
        out[0] = arr[0]
        for i in range(1, n):
            lo = max(0, i - window)
            out[i] = arr[lo:i].max() if i > lo else arr[i]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public masks — entry / exit / breakout / reentry
# ─────────────────────────────────────────────────────────────────────────────


def build_btc_long_entry_mask(
    npz: Dict[str, np.ndarray],
    n: int,
    cfg: Any,
) -> np.ndarray:
    """Vectorised port of btc_loop.should_enter_btc_long.

    Returns bool[n] — True at bars where the live function would return ok=True.

    Honors knobs:
      BTC_DEDICATED_ENABLED, BTC_RZ_AS_BOOST_ENABLED, BTC_RZ_SOFTEN_ACCEL_BY,
      BTC_ENTRY_PRIMARY_REQUIRE_RZ, BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP,
      BTC_ACCEL_RAMP_MIN_TFS, BTC_DIVERGENCE_BLOCK_AGAINST, BTC_DIVERGENCE_ENABLED,
      BTC_DIVERGENCE_BEAR_MIN_INDS.
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return np.zeros(n, dtype=bool)
    accel = _compute_accel_ramp_masks(npz, n, cfg)
    rz_active = _compute_red_zone_mask(npz, n, cfg)
    div = _compute_divergence_counts(npz, n, cfg)
    # Veto: opposing (bear) divergence
    block_div = bool(getattr(cfg, "BTC_DIVERGENCE_BLOCK_AGAINST", True))
    div_enabled = bool(getattr(cfg, "BTC_DIVERGENCE_ENABLED", True))
    bear_min = int(getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2))
    div_block = (block_div & div_enabled) & (div["bear_inds_aligned"] >= bear_min)
    # RZ branch: gate (legacy) vs softener (default)
    rz_boost_mode = bool(getattr(cfg, "BTC_RZ_AS_BOOST_ENABLED", True))
    require_rz = bool(getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_RZ", True))
    soften_by = int(getattr(cfg, "BTC_RZ_SOFTEN_ACCEL_BY", 1))
    if rz_boost_mode:
        soften = np.where(rz_active, soften_by, 0)
        rz_gate_ok = np.ones(n, dtype=bool)  # not gating
    else:
        soften = np.zeros(n, dtype=np.int8)
        rz_gate_ok = (~require_rz) | rz_active
    # Accel ramp gate
    require_accel = bool(getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP", True))
    accel_min_tfs = int(getattr(cfg, "BTC_ACCEL_RAMP_MIN_TFS", 5))
    if require_accel:
        eff_min = np.maximum(1, accel_min_tfs - soften)
        accel_gate_ok = accel["side_bull"] & (accel["bull_aligned_tfs"] >= eff_min)
    else:
        accel_gate_ok = np.ones(n, dtype=bool)
    return (~div_block) & rz_gate_ok & accel_gate_ok


def build_btc_short_entry_mask(
    npz: Dict[str, np.ndarray],
    n: int,
    cfg: Any,
) -> np.ndarray:
    """Mirror of build_btc_long_entry_mask for SHORT."""
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return np.zeros(n, dtype=bool)
    accel = _compute_accel_ramp_masks(npz, n, cfg)
    rz_active = _compute_red_zone_mask(npz, n, cfg)
    div = _compute_divergence_counts(npz, n, cfg)
    block_div = bool(getattr(cfg, "BTC_DIVERGENCE_BLOCK_AGAINST", True))
    div_enabled = bool(getattr(cfg, "BTC_DIVERGENCE_ENABLED", True))
    bull_min = int(getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2))
    div_block = (block_div & div_enabled) & (div["bull_inds_aligned"] >= bull_min)
    rz_boost_mode = bool(getattr(cfg, "BTC_RZ_AS_BOOST_ENABLED", True))
    require_rz = bool(getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_RZ", True))
    soften_by = int(getattr(cfg, "BTC_RZ_SOFTEN_ACCEL_BY", 1))
    if rz_boost_mode:
        soften = np.where(rz_active, soften_by, 0)
        rz_gate_ok = np.ones(n, dtype=bool)
    else:
        soften = np.zeros(n, dtype=np.int8)
        rz_gate_ok = (~require_rz) | rz_active
    require_accel = bool(getattr(cfg, "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP", True))
    accel_min_tfs = int(getattr(cfg, "BTC_ACCEL_RAMP_MIN_TFS", 5))
    if require_accel:
        eff_min = np.maximum(1, accel_min_tfs - soften)
        accel_gate_ok = accel["side_bear"] & (accel["bear_aligned_tfs"] >= eff_min)
    else:
        accel_gate_ok = np.ones(n, dtype=bool)
    return (~div_block) & rz_gate_ok & accel_gate_ok


def build_btc_breakout_mask(
    npz: Dict[str, np.ndarray],
    n: int,
    is_long: bool,
    cfg: Any,
) -> np.ndarray:
    """Vectorised port of btc_loop.detect_btc_breakout for one side.

    Honors:
      BTC_BREAKOUT_ENTRY_ENABLED, BTC_BREAKOUT_ACCEL_MIN_TFS,
      BTC_BREAKOUT_BLOCK_OPPOSING_DIV, BTC_BREAKOUT_REQUIRE_HTF_ALIGNED,
      BTC_BREAKOUT_HTF_MIN_ALIGNED.

    Uses dc_high_3m / dc_low_3m as 3m channel (live takes prev bar's value,
    i.e. shifted; we shift here for parity).
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return np.zeros(n, dtype=bool)
    if not getattr(cfg, "BTC_BREAKOUT_ENTRY_ENABLED", True):
        return np.zeros(n, dtype=bool)
    close = _get(npz, "close", n, 0.0)
    dc_h_3m = _get(npz, "dc_high_3m", n, 0.0)
    dc_l_3m = _get(npz, "dc_low_3m", n, 0.0)
    # Live uses prev_dc_high_3m — shift by 1
    prev_dc_h_3m = np.concatenate([[dc_h_3m[0]], dc_h_3m[:-1]])
    prev_dc_l_3m = np.concatenate([[dc_l_3m[0]], dc_l_3m[:-1]])
    accel = _compute_accel_ramp_masks(npz, n, cfg)
    div = _compute_divergence_counts(npz, n, cfg)
    min_tfs = int(getattr(cfg, "BTC_BREAKOUT_ACCEL_MIN_TFS", 2))
    block_div = bool(getattr(cfg, "BTC_BREAKOUT_BLOCK_OPPOSING_DIV", True))
    require_htf = bool(getattr(cfg, "BTC_BREAKOUT_REQUIRE_HTF_ALIGNED", True))
    htf_min = int(getattr(cfg, "BTC_BREAKOUT_HTF_MIN_ALIGNED", 2))
    # HTF aligned counts — count {1h, 4h, D} TFs where wt1 > wt2 (LONG) / < (SHORT)
    htf_count = np.zeros(n, dtype=np.int8)
    for tf in ("1h", "4h", "D"):
        w1 = _get(npz, f"wt1_{tf}", n, 0.0)
        w2 = _get(npz, f"wt2_{tf}", n, 0.0)
        if is_long:
            htf_count += (w1 > w2).astype(np.int8)
        else:
            htf_count += (w1 < w2).astype(np.int8)
    if is_long:
        price_break = (close > prev_dc_h_3m) & (prev_dc_h_3m > 0)
        accel_ok = (accel["bull_aligned_tfs"] >= min_tfs) & accel["side_bull"]
        htf_ok = (~require_htf) | (htf_count >= htf_min)
        bear_min = int(getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2))
        div_block = block_div & (div["bear_inds_aligned"] >= bear_min)
    else:
        price_break = (close < prev_dc_l_3m) & (prev_dc_l_3m > 0)
        accel_ok = (accel["bear_aligned_tfs"] >= min_tfs) & accel["side_bear"]
        htf_ok = (~require_htf) | (htf_count >= htf_min)
        bull_min = int(getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2))
        div_block = block_div & (div["bull_inds_aligned"] >= bull_min)
    return price_break & accel_ok & htf_ok & (~div_block)


def build_btc_exit_mask(
    npz: Dict[str, np.ndarray],
    n: int,
    is_long: bool,
    cfg: Any,
) -> np.ndarray:
    """Vectorised port of btc_loop.should_exit_btc — BOUNCE path (entry_type='BOUNCE').

    Returns bool[n] — True at bars where the live function would return ok=True
    given a held BOUNCE-entry position.

    BREAKOUT path is NOT modelled here (live caller tracks entry_type per
    position; v8_vec_sweep treats every entry as BOUNCE by default — matches
    ez_positions_quick.py:1919 default).

    Honors:
      BTC_TECH_EXIT_WT_MIN_TFS, BTC_INTRABAR_REVERSAL_EXIT,
      BTC_DIVERGENCE_EXIT_AGAINST, BTC_DIVERGENCE_*_MIN_INDS.

    The hard-loss USD gate is NOT applied here — v8_vec_sweep's per-bar gain
    arithmetic handles position-level USD pnl; pnl-based stops are wired in the
    main loop, not here.
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return np.zeros(n, dtype=bool)
    accel = _compute_accel_ramp_masks(npz, n, cfg)
    div = _compute_divergence_counts(npz, n, cfg)
    # WT-against count across 5 TFs
    wt_against = np.zeros(n, dtype=np.int8)
    for tf in _TFS:
        w1 = _get(npz, f"wt1_{tf}", n, 0.0)
        w2 = _get(npz, f"wt2_{tf}", n, 0.0)
        if is_long:
            wt_against += (w1 < w2).astype(np.int8)
        else:
            wt_against += (w1 > w2).astype(np.int8)
    wt_min = int(getattr(cfg, "BTC_TECH_EXIT_WT_MIN_TFS", 3))
    wt_fire = wt_against >= wt_min
    # Intra-bar reversal — accel flipped against
    if bool(getattr(cfg, "BTC_INTRABAR_REVERSAL_EXIT", True)):
        if is_long:
            accel_fire = accel["side_bear"]
        else:
            accel_fire = accel["side_bull"]
    else:
        accel_fire = np.zeros(n, dtype=bool)
    # Divergence exit
    if bool(getattr(cfg, "BTC_DIVERGENCE_EXIT_AGAINST", True)):
        if is_long:
            bear_min = int(getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2))
            div_fire = div["bear_inds_aligned"] >= bear_min
        else:
            bull_min = int(getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2))
            div_fire = div["bull_inds_aligned"] >= bull_min
    else:
        div_fire = np.zeros(n, dtype=bool)
    return wt_fire | accel_fire | div_fire


def build_btc_divergence_block_mask(
    npz: Dict[str, np.ndarray],
    n: int,
    is_long: bool,
    cfg: Any,
) -> np.ndarray:
    """Standalone divergence-block mask for use by external callers (e.g. as an
    augment-time veto). Returns True at bars where divergence would block.

    Maps onto BTC_DIVERGENCE_BLOCK_AGAINST + BTC_DIVERGENCE_*_MIN_INDS.
    """
    if not getattr(cfg, "BTC_DEDICATED_ENABLED", False):
        return np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "BTC_DIVERGENCE_BLOCK_AGAINST", True)):
        return np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "BTC_DIVERGENCE_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    div = _compute_divergence_counts(npz, n, cfg)
    if is_long:
        bear_min = int(getattr(cfg, "BTC_DIVERGENCE_BEAR_MIN_INDS", 2))
        return div["bear_inds_aligned"] >= bear_min
    bull_min = int(getattr(cfg, "BTC_DIVERGENCE_BULL_MIN_INDS", 2))
    return div["bull_inds_aligned"] >= bull_min


def is_btc_symbol(symbol: str) -> bool:
    """True for the BTC pairs the live caller treats as BTC-dedicated.

    Live `ez_positions_quick._btc_dedicated_disabled` whitelists BTCUSDC + BTCUSDT
    by checking symbol startswith 'BTC' for the 10-USDC-perp policy. Vec mirrors
    that check.
    """
    s = (symbol or "").upper()
    return s.startswith("BTCUSDC") or s.startswith("BTCUSDT")
