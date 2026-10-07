"""vec_paths/golden_rule_htf_vote.py — score_entry_htf / score_exit_htf vec parity.

LIVE LOGIC MIRRORED FROM:
  golden_rule_htf.py:_ind_score (lines 41-101)
  golden_rule_htf.py:_run_gate  (lines 104-153)
  golden_rule_htf.py:score_entry_htf / score_exit_htf (lines 156-183)

WHY THIS EXISTS:
  score_entry_htf is called from v8_quick_engine in THREE places per bar
  (lines 2839, 3692, 3951). Each call iterates 5-6 TFs × 7 indicators ×
  multiple dict gets with NaN fallback — a pure scalar hot-path that
  dominates Tier-1 sweep runtime once GR is enabled.

  The dead-knob audit (2026-05-10) showed this gate is the single biggest
  Sharpe differentiator on tradier (HTF_MIN_TFS family 0.5353 vs 0.41
  baseline) and the only non-trivial GR knob with effect on crypto.

  Vectorizing here means a million-variant sweep over (min_tfs, min_ind,
  GR_TOTAL_VOTE_SCORE_MIN) collapses from per-bar/per-symbol Python
  dict gets to numpy boolean reductions.

INPUTS (vec form):
  Each per-TF indicator is a shape (N,) numpy array. Missing fields are
  passed as None or all-NaN arrays — module treats those as "skipped"
  exactly like the scalar version (which short-circuits via the `is None`
  and `>= 0` gates).

  features per TF:
    wt1, wt2, rsi, mfi, dc_position, bb_pct_b, relative_volume, stoch_k
  optional fallback for dc_position and bb_pct_b:
    dc_high, dc_low, bb_upper, bb_lower, close
    px (override price, shape (N,) or None to use close)

GATE MODES:
  - TOTAL_VOTE mode  : vote_min > 0  → sum-of-votes across all TFs vs vote_min
  - LEGACY MIN_TFS×MIN_IND : vote_min == 0 AND min_tfs > 0
                              → per-TF n >= min_ind, count confirmed TFs >= min_tfs
  - OFF              : vote_min == 0 AND min_tfs == 0 → always pass

RETURNS:
  evaluate_gr_htf_core(...)  → (passes: bool, count: int, detail: str)
                                count = vote total in vote mode, n_confirmed
                                TFs in legacy mode.
  evaluate_gr_htf_vec(...)   → (passes: np.ndarray bool shape (N,),
                                count: np.ndarray int32 shape (N,))

EXACT PARITY:
  - DC fallback formula matches scalar:
        dc_pos = (px - dc_l) / (dc_h - dc_l)  when dc_h > dc_l > 0 and px > 0
  - BB fallback formula matches scalar:
        bb_pct = (px - bb_l) / (bb_u - bb_l)  when bb_u > bb_l > 0 and px > 0
  - "px" precedence: px arg if > 0 else close_<tf>
  - "is_long" inversion applied for exit mode externally (live exit calls
     evaluate_gr_htf_core with not is_long; same here — caller flips).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Same TF lists as golden_rule_htf.py (W added to crypto 2026-10-06: live _CRYPTO_TFS has W since 2026-05-17)
_CRYPTO_TFS = ["3m", "15m", "1h", "4h", "D", "W"]
_TRADIER_TFS = ["5m", "15m", "1h", "4h", "D", "W"]

_DC_EXTENDED_LONG = 0.65
_DC_EXTENDED_SHORT = 0.35
_BB_EXTENDED_LONG = 0.75
_BB_EXTENDED_SHORT = 0.25


def _resolve_tfs(mode: str, allow_low_tf: bool = False) -> List[str]:
    # 2026-10-06 NO-1m/3m/5m parity: 3m/5m skipped unless explicitly allowed (live golden_rule_htf
    # applies the identical skip while USE_1M_3M_SIGNALS_ENABLED is False — NOTE_3M_REENABLE).
    tfs = _TRADIER_TFS if mode == "tradier" else _CRYPTO_TFS
    if allow_low_tf:
        return list(tfs)
    return [t for t in tfs if t not in ("3m", "5m")]


def _tf_score_core(
    ind: dict,
    tf: str,
    is_long: bool,
    px: float,
    invert_dc_bb: bool = False,
    dc_threshold: float = 0.0,
    bb_threshold: float = 0.0,
) -> Tuple[int, str]:
    """Exact scalar mirror of golden_rule_htf._ind_score.

    Kept here as the parity oracle so the test does NOT need to import the
    live module (vec_paths must be self-contained / runnable on a fresh
    checkout without the rest of the repo on PYTHONPATH).

    invert_dc_bb=True: breakout mode — DC/BB extension is bullish.
    """
    score = 0
    parts: List[str] = []

    # WT
    wt1 = float(ind.get(f"wt1_{tf}") or 0)
    wt2 = float(ind.get(f"wt2_{tf}") or 0)
    if wt1 != 0 or wt2 != 0:
        ok = wt1 > wt2 if is_long else wt1 < wt2
        score += int(ok)
        parts.append(f"WT{'+' if ok else '-'}")

    # RSI
    rsi_raw = ind.get(f"rsi_{tf}")
    rsi = float(rsi_raw) if rsi_raw is not None else -1.0
    if rsi >= 0:
        ok = rsi > 50 if is_long else rsi < 50
        score += int(ok)
        parts.append(f"RSI{'+' if ok else '-'}{rsi:.0f}")

    # MFI
    mfi_raw = ind.get(f"mfi_{tf}")
    mfi = float(mfi_raw) if mfi_raw is not None else -1.0
    if mfi >= 0:
        ok = mfi > 50 if is_long else mfi < 50
        score += int(ok)
        parts.append(f"MFI{'+' if ok else '-'}{mfi:.0f}")

    # DC position (computed if missing)
    dc_raw = ind.get(f"dc_position_{tf}")
    dc_pos = float(dc_raw) if dc_raw is not None else -1.0
    if dc_pos < 0:
        dc_h = float(ind.get(f"dc_high_{tf}") or 0)
        dc_l = float(ind.get(f"dc_low_{tf}") or 0)
        ref_px = px or float(ind.get(f"close_{tf}") or 0)
        if dc_h > dc_l > 0 and ref_px > 0:
            dc_pos = (ref_px - dc_l) / (dc_h - dc_l)
    _dc_l = dc_threshold if dc_threshold > 0 else _DC_EXTENDED_LONG
    _dc_s = 1.0 - _dc_l
    if dc_pos >= 0:
        if invert_dc_bb:
            ok = dc_pos >= _dc_l if is_long else dc_pos <= _dc_s
        else:
            ok = dc_pos < _dc_l if is_long else dc_pos > _dc_s
        score += int(ok)
        parts.append(f"DC{'+' if ok else '-'}{dc_pos:.2f}")

    # BB pct-b (computed if missing)
    bb_raw = ind.get(f"bb_pct_b_{tf}")
    bb_pctb = float(bb_raw) if bb_raw is not None else -1.0
    if bb_pctb < 0:
        bb_u = float(ind.get(f"bb_upper_{tf}") or 0)
        bb_l = float(ind.get(f"bb_lower_{tf}") or 0)
        ref_px = px or float(ind.get(f"close_{tf}") or 0)
        if bb_u > bb_l > 0 and ref_px > 0:
            bb_pctb = (ref_px - bb_l) / (bb_u - bb_l)
    _bb_l = bb_threshold if bb_threshold > 0 else _BB_EXTENDED_LONG
    _bb_s = 1.0 - _bb_l
    if bb_pctb >= 0:
        if invert_dc_bb:
            ok = bb_pctb >= _bb_l if is_long else bb_pctb <= _bb_s
        else:
            ok = bb_pctb < _bb_l if is_long else bb_pctb > _bb_s
        score += int(ok)
        parts.append(f"BB{'+' if ok else '-'}{bb_pctb:.2f}")

    # RVOL
    rvol_raw = ind.get(f"relative_volume_{tf}")
    rvol = float(rvol_raw) if rvol_raw is not None else -1.0
    if rvol >= 0:
        ok = rvol > 1.0
        score += int(ok)
        parts.append(f"RVOL{'+' if ok else '-'}{rvol:.2f}")

    # Stoch K
    stk_raw = ind.get(f"stoch_k_{tf}")
    stk = float(stk_raw) if stk_raw is not None else -1.0
    if stk >= 0:
        ok = stk < 80.0 if is_long else stk > 20.0
        score += int(ok)
        parts.append(f"K{'+' if ok else '-'}{stk:.0f}")

    return score, "|".join(parts)


def _check_activation_core(
    ind: dict,
    activation_tfs: List[str],
    is_long: bool,
    px: float,
    dc_thr: float,
    bb_thr: float,
) -> Tuple[bool, str]:
    """USER 2026-05-18 activation gate — bit-exact mirror of
    golden_rule_htf._check_activation. At least 1 activation TF must show
    breakout (bb_pctb or dc_position crosses extended threshold).
    """
    _bb_lvl = bb_thr if bb_thr > 0 else _BB_EXTENDED_LONG
    _dc_lvl = dc_thr if dc_thr > 0 else _DC_EXTENDED_LONG
    _bb_s = 1.0 - _bb_lvl
    _dc_s = 1.0 - _dc_lvl
    parts: List[str] = []
    for tf in activation_tfs:
        bb = ind.get(f"bb_pct_b_{tf}")
        dc = ind.get(f"dc_position_{tf}")
        try:
            bb_v = float(bb) if bb is not None else -1.0
            dc_v = float(dc) if dc is not None else -1.0
        except (TypeError, ValueError):
            bb_v, dc_v = -1.0, -1.0
        bb_ok = (bb_v >= _bb_lvl) if is_long else (0 <= bb_v <= _bb_s)
        dc_ok = (dc_v >= _dc_lvl) if is_long else (0 <= dc_v <= _dc_s)
        tf_active = bb_ok or dc_ok
        parts.append(f"{tf}:bb{bb_v:.2f}{'+' if bb_ok else '-'}/dc{dc_v:.2f}{'+' if dc_ok else '-'}")
        if tf_active:
            return True, f"ACT_OK[{','.join(parts)}]"
    return False, f"NO_ACTIVATION[{','.join(parts)}]"


def evaluate_gr_htf_core(
    ind: dict,
    is_long: bool,
    mode: str = "tradier",
    min_tfs: int = 2,
    min_ind: int = 2,
    px: float = 0.0,
    vote_min: int = 0,
    invert_dc_bb: bool = False,
    dc_threshold: float = 0.0,
    bb_threshold: float = 0.0,
    require_activation: bool = False,
    activation_tfs: Optional[List[str]] = None,
    entry_tfs: Optional[List[str]] = None,
    allow_low_tf: bool = False,
) -> Tuple[bool, int, str]:
    """Scalar parity oracle — bit-exact mirror of golden_rule_htf._run_gate.

    is_long==False for score_exit_htf is achieved by caller flipping
    (mirrors the live `_run_gate(ind, not is_long, ...)` for exits).

    invert_dc_bb=True: breakout mode — must match invert_dc_bb passed to golden_rule_htf.

    USER 2026-05-18 activation/entry split: when require_activation=True and
    activation_tfs is non-empty, runs a 2-stage gate:
      Stage 1 — activation: at least 1 TF in activation_tfs must show breakout.
      Stage 2 — entry score: score only entry_tfs (or all TFs if entry_tfs empty).
    """
    tfs_default = _resolve_tfs(mode, allow_low_tf)
    _act_list = [t for t in (activation_tfs or []) if allow_low_tf or t not in ("3m", "5m")]
    _entry_list = [t for t in (entry_tfs or []) if allow_low_tf or t not in ("3m", "5m")]

    # === USER 2026-05-18 activation/entry split ===
    if require_activation and _act_list:
        act_ok, act_detail = _check_activation_core(
            ind, _act_list, is_long, px, dc_threshold, bb_threshold,
        )
        if not act_ok:
            return False, 0, act_detail
        tfs = _entry_list if _entry_list else tfs_default
    else:
        tfs = tfs_default

    # TOTAL-VOTE-SCORE mode
    if vote_min > 0:
        total = 0
        parts: List[str] = []
        for tf in tfs:
            n, _ = _tf_score_core(ind, tf, is_long, px, invert_dc_bb, dc_threshold, bb_threshold)
            total += n
            parts.append(f"{tf}:{n}")
        passes = total >= vote_min
        _act_tag = "+act" if require_activation else ""
        return passes, total, f"vote_total={total}/{vote_min}req{_act_tag} [{' '.join(parts)}]"

    # OFF
    if min_tfs <= 0:
        return True, 0, "GATE_OFF"

    # LEGACY MIN_TFS × MIN_IND
    confirmed = 0
    parts: List[str] = []
    for tf in tfs:
        n, _detail = _tf_score_core(ind, tf, is_long, px, invert_dc_bb, dc_threshold, bb_threshold)
        ok = n >= min_ind
        if ok:
            confirmed += 1
        parts.append(f"{tf}:{n}/{min_ind}{'+' if ok else ''}")
    passes = confirmed >= min_tfs
    _act_tag = "+act" if require_activation else ""
    summary = f"tfs={confirmed}/{min_tfs}req{_act_tag} [{' '.join(parts)}]"
    return passes, confirmed, summary


# ════════════════════════════════════════════════════════════════════════════
# Vectorized — accept dict-of-arrays (one entry per indicator/TF), all shape (N,)
# ════════════════════════════════════════════════════════════════════════════

def _get_arr(features: Dict[str, np.ndarray], key: str, n: int) -> np.ndarray:
    """Return shape-(N,) float64 array. Missing → all-NaN (treated as 'skip')."""
    arr = features.get(key)
    if arr is None:
        out = np.empty(n, dtype=np.float64)
        out[:] = np.nan
        return out
    a = np.asarray(arr, dtype=np.float64)
    if a.shape != (n,):
        a = np.broadcast_to(a, (n,)).copy()
    return a


def _per_tf_score_vec(
    features: Dict[str, np.ndarray],
    tf: str,
    is_long: bool,
    px_arr: np.ndarray,
    n: int,
    invert_dc_bb: bool = False,
    dc_threshold: float = 0.0,
    bb_threshold: float = 0.0,
) -> np.ndarray:
    """Compute per-bar n_indicators_agreeing for one TF. Returns int32 (N,).

    invert_dc_bb=True: breakout mode — DC/BB extension is bullish (mirrors scalar).
    dc_threshold/bb_threshold: override module-level constants (0 = use default).
    """
    n_arr = np.zeros(n, dtype=np.int32)

    # ── WT ─────────────────────────────────────────────────────────────────
    wt1 = _get_arr(features, f"wt1_{tf}", n)
    wt2 = _get_arr(features, f"wt2_{tf}", n)
    # The scalar code does `float(ind.get(f"wt1_{tf}") or 0)`. Treat NaN as 0
    # (matches the `or 0` semantics).
    wt1_z = np.where(np.isnan(wt1), 0.0, wt1)
    wt2_z = np.where(np.isnan(wt2), 0.0, wt2)
    wt_present = (wt1_z != 0) | (wt2_z != 0)
    if is_long:
        wt_ok = wt1_z > wt2_z
    else:
        wt_ok = wt1_z < wt2_z
    n_arr += (wt_present & wt_ok).astype(np.int32)

    # ── RSI ────────────────────────────────────────────────────────────────
    rsi = _get_arr(features, f"rsi_{tf}", n)
    # scalar: rsi = float(...) if not None else -1.0; then if rsi >= 0
    # NaN treated as missing (== -1), so condition is "not nan and rsi>=0"
    rsi_present = ~np.isnan(rsi) & (rsi >= 0.0)
    if is_long:
        rsi_ok = rsi > 50.0
    else:
        rsi_ok = rsi < 50.0
    n_arr += (rsi_present & rsi_ok).astype(np.int32)

    # ── MFI ────────────────────────────────────────────────────────────────
    mfi = _get_arr(features, f"mfi_{tf}", n)
    mfi_present = ~np.isnan(mfi) & (mfi >= 0.0)
    if is_long:
        mfi_ok = mfi > 50.0
    else:
        mfi_ok = mfi < 50.0
    n_arr += (mfi_present & mfi_ok).astype(np.int32)

    # ── DC position (with fallback) ────────────────────────────────────────
    dc_pos = _get_arr(features, f"dc_position_{tf}", n)
    # Scalar logic: if dc_pos < 0 then try fallback. NaN treated as -1 (missing).
    dc_missing = np.isnan(dc_pos) | (dc_pos < 0)
    if np.any(dc_missing):
        dc_h = _get_arr(features, f"dc_high_{tf}", n)
        dc_l = _get_arr(features, f"dc_low_{tf}", n)
        close_tf = _get_arr(features, f"close_{tf}", n)
        # ref_px = px or close_<tf>; "or" → 0 falsy
        px_use = np.where(px_arr > 0, px_arr, 0.0)
        ref_px = np.where(px_use > 0, px_use, np.where(np.isnan(close_tf), 0.0, close_tf))
        # treat NaN dc_h/dc_l as 0
        dc_h_z = np.where(np.isnan(dc_h), 0.0, dc_h)
        dc_l_z = np.where(np.isnan(dc_l), 0.0, dc_l)
        fb_valid = dc_missing & (dc_h_z > dc_l_z) & (dc_l_z > 0) & (ref_px > 0)
        # Compute fallback only where valid
        denom = np.where(fb_valid, dc_h_z - dc_l_z, 1.0)
        dc_fb = np.where(fb_valid, (ref_px - dc_l_z) / denom, dc_pos)
        dc_pos = np.where(fb_valid, dc_fb, dc_pos)
    dc_present = ~np.isnan(dc_pos) & (dc_pos >= 0)
    _dc_l = dc_threshold if dc_threshold > 0 else _DC_EXTENDED_LONG
    _dc_s = 1.0 - _dc_l
    if invert_dc_bb:
        dc_ok = (dc_pos >= _dc_l) if is_long else (dc_pos <= _dc_s)
    else:
        dc_ok = (dc_pos < _dc_l) if is_long else (dc_pos > _dc_s)
    n_arr += (dc_present & dc_ok).astype(np.int32)

    # ── BB pct-b (with fallback) ───────────────────────────────────────────
    bb_pctb = _get_arr(features, f"bb_pct_b_{tf}", n)
    bb_missing = np.isnan(bb_pctb) | (bb_pctb < 0)
    if np.any(bb_missing):
        bb_u = _get_arr(features, f"bb_upper_{tf}", n)
        bb_l = _get_arr(features, f"bb_lower_{tf}", n)
        close_tf = _get_arr(features, f"close_{tf}", n)
        px_use = np.where(px_arr > 0, px_arr, 0.0)
        ref_px = np.where(px_use > 0, px_use, np.where(np.isnan(close_tf), 0.0, close_tf))
        bb_u_z = np.where(np.isnan(bb_u), 0.0, bb_u)
        bb_l_z = np.where(np.isnan(bb_l), 0.0, bb_l)
        fb_valid = bb_missing & (bb_u_z > bb_l_z) & (bb_l_z > 0) & (ref_px > 0)
        denom = np.where(fb_valid, bb_u_z - bb_l_z, 1.0)
        bb_fb = np.where(fb_valid, (ref_px - bb_l_z) / denom, bb_pctb)
        bb_pctb = np.where(fb_valid, bb_fb, bb_pctb)
    bb_present = ~np.isnan(bb_pctb) & (bb_pctb >= 0)
    _bb_l = bb_threshold if bb_threshold > 0 else _BB_EXTENDED_LONG
    _bb_s = 1.0 - _bb_l
    if invert_dc_bb:
        bb_ok = (bb_pctb >= _bb_l) if is_long else (bb_pctb <= _bb_s)
    else:
        bb_ok = (bb_pctb < _bb_l) if is_long else (bb_pctb > _bb_s)
    n_arr += (bb_present & bb_ok).astype(np.int32)

    # ── RVOL ───────────────────────────────────────────────────────────────
    rvol = _get_arr(features, f"relative_volume_{tf}", n)
    rvol_present = ~np.isnan(rvol) & (rvol >= 0)
    rvol_ok = rvol > 1.0
    n_arr += (rvol_present & rvol_ok).astype(np.int32)

    # ── Stoch K ────────────────────────────────────────────────────────────
    stk = _get_arr(features, f"stoch_k_{tf}", n)
    stk_present = ~np.isnan(stk) & (stk >= 0)
    if is_long:
        stk_ok = stk < 80.0
    else:
        stk_ok = stk > 20.0
    n_arr += (stk_present & stk_ok).astype(np.int32)

    return n_arr


def _check_activation_vec(
    features: Dict[str, np.ndarray],
    activation_tfs: List[str],
    is_long: bool,
    n: int,
    dc_threshold: float = 0.0,
    bb_threshold: float = 0.0,
) -> np.ndarray:
    """Vec mirror of _check_activation_core. Returns shape-(N,) bool mask
    of bars where at least one activation TF shows breakout.

    Parity contract with scalar:
      - Missing field → treated as -1.0 (scalar) / NaN (vec). NaN comparison
        with any number is False, matching scalar's `-1.0 >= bb_lvl == False`
        for LONG and `0 <= -1.0` == False for SHORT.
    """
    _bb_lvl = bb_threshold if bb_threshold > 0 else _BB_EXTENDED_LONG
    _dc_lvl = dc_threshold if dc_threshold > 0 else _DC_EXTENDED_LONG
    _bb_s = 1.0 - _bb_lvl
    _dc_s = 1.0 - _dc_lvl
    act_ok = np.zeros(n, dtype=bool)
    for tf in activation_tfs:
        bb = _get_arr(features, f"bb_pct_b_{tf}", n)
        dc = _get_arr(features, f"dc_position_{tf}", n)
        if is_long:
            bb_ok = bb >= _bb_lvl
            dc_ok = dc >= _dc_lvl
        else:
            bb_ok = (bb >= 0.0) & (bb <= _bb_s)
            dc_ok = (dc >= 0.0) & (dc <= _dc_s)
        # NaN propagation: comparisons with NaN are False, so missing fields
        # naturally contribute False (no breakout). Matches scalar's -1.0 path.
        act_ok |= (bb_ok | dc_ok)
    return act_ok


def evaluate_gr_htf_vec(
    features: Dict[str, np.ndarray],
    is_long: bool,
    mode: str = "tradier",
    min_tfs: int = 2,
    min_ind: int = 2,
    px: Optional[np.ndarray] = None,
    vote_min: int = 0,
    n: Optional[int] = None,
    invert_dc_bb: bool = False,
    dc_threshold: float = 0.0,
    bb_threshold: float = 0.0,
    require_activation: bool = False,
    activation_tfs: Optional[List[str]] = None,
    entry_tfs: Optional[List[str]] = None,
    allow_low_tf: bool = False,
) -> Tuple[np.ndarray, np.ndarray]:
    """Per-bar vectorized golden-rule gate.

    Args:
        features:     dict mapping `<field>_<tf>` → shape (N,) array. Missing
                      keys are treated as "indicator not present" (matches scalar).
        is_long:      bullish gate when True, bearish (exit / short entry) when False.
        mode:         'crypto' or 'tradier'.
        min_tfs:      legacy gate threshold (0 → OFF).
        min_ind:      legacy per-TF indicator threshold.
        px:           optional shape (N,) price override. None → use close_<tf>.
        vote_min:     TOTAL_VOTE gate threshold (>0 activates vote mode).
        n:            explicit bar count.
        invert_dc_bb: True = breakout mode (extension is bullish); False = room-to-run.
        dc_threshold: DC extension threshold override (0 = use module default 0.65).
        bb_threshold: BB pct-b threshold override (0 = use module default 0.75).

    Returns:
        (passes, count)
          passes: shape (N,) bool — True when gate fires (== passes).
          count:  shape (N,) int32 — vote total (vote mode) or n_confirmed_tfs
                  (legacy mode); zeros when gate is OFF.
    """
    if n is None:
        if not features:
            raise ValueError("evaluate_gr_htf_vec: features empty AND n=None")
        for arr in features.values():
            if arr is not None:
                a = np.asarray(arr)
                n = a.shape[0]
                break
        if n is None:
            raise ValueError("evaluate_gr_htf_vec: all features are None")

    # OFF fast path
    if vote_min <= 0 and min_tfs <= 0:
        return np.ones(n, dtype=bool), np.zeros(n, dtype=np.int32)

    px_arr = np.asarray(px, dtype=np.float64) if px is not None else np.zeros(n, dtype=np.float64)
    if px_arr.shape != (n,):
        px_arr = np.broadcast_to(px_arr, (n,)).copy()

    tfs_default = _resolve_tfs(mode, allow_low_tf)
    _act_list = [t for t in (activation_tfs or []) if allow_low_tf or t not in ("3m", "5m")]
    _entry_list = [t for t in (entry_tfs or []) if allow_low_tf or t not in ("3m", "5m")]

    # === USER 2026-05-18 activation/entry split ===
    if require_activation and _act_list:
        act_mask = _check_activation_vec(
            features, _act_list, is_long, n, dc_threshold, bb_threshold,
        )
        tfs = _entry_list if _entry_list else tfs_default
    else:
        act_mask = None
        tfs = tfs_default

    if vote_min > 0:
        # TOTAL vote: sum n_indicators_agreeing across selected TFs
        total = np.zeros(n, dtype=np.int32)
        for tf in tfs:
            total += _per_tf_score_vec(features, tf, is_long, px_arr, n, invert_dc_bb, dc_threshold, bb_threshold)
        passes = total >= vote_min
        if act_mask is not None:
            passes &= act_mask
            total = np.where(act_mask, total, 0)
        return passes, total

    # LEGACY: count TFs where n_agreeing >= min_ind, pass if count >= min_tfs
    confirmed = np.zeros(n, dtype=np.int32)
    for tf in tfs:
        tf_n = _per_tf_score_vec(features, tf, is_long, px_arr, n, invert_dc_bb, dc_threshold, bb_threshold)
        confirmed += (tf_n >= min_ind).astype(np.int32)
    passes = confirmed >= min_tfs
    if act_mask is not None:
        passes &= act_mask
        confirmed = np.where(act_mask, confirmed, 0)
    return passes, confirmed
