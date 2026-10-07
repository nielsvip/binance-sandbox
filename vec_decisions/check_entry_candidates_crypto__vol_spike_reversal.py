"""check_entry_candidates_crypto__vol_spike_reversal.py

SHARED scalar+vectorized predicate for VOL_SPIKE_REVERSAL in
check_entry_candidates_for_account (ez_positions_quick.py:15637-15671), plus the
underlying detector detect_volume_spike (ez_positions_quick.py:11510-11535).

The detector is a PURE per-bar test (no rolling state): a high-relative-volume,
strong-body 15m candle is FADED — a bearish spike opens a LONG, a bullish spike a
SHORT. The surrounding GATE adds alignment + a DC_LOW4/HIGH4 structural-floor check
(both NPZ-pure). The account L/S-ratio sub-gate is RUNTIME-BLOCKED (account
aggregate, never per-symbol NPZ — CLAUDE.md 2d) so it is passed in as a caller
boolean `ratio_ok` (the engine supplies True when the feed is absent, exactly the
report's classification of this branch as 'DC-floor + alignment portion is
NPZ-pure'). The per-symbol cooldown is a state seam handled by the caller.

FAITHFUL EXTRACTION:

detect_volume_spike (11514-11534):
    relvol = ind.get('relative_volume_15m', 0)
    if relvol < VOL_SPIKE_RELVOL_THRESHOLD (3.0): return None
    range = high_15m - low_15m
    if range<=0 or high_15m<=0 or open_15m<=0 or close_15m<=0: return None
    body_ratio = abs(close_15m - open_15m) / range
    if body_ratio < VOL_SPIKE_BODY_RATIO (0.7): return None
    is_bearish = close_15m < open_15m
    if is_bearish and is_long: return 'BUY'
    if (not is_bearish) and (not is_long): return 'SELL'
    return None

GATE (15644-15671):
    alignment >= VOL_SPIKE_MIN_ALIGNMENT (3)
    floor_ok: LONG blocked if dc_low4_15m>0 and price<dc_low4_15m
              SHORT blocked if dc_high4_15m>0 and price>dc_high4_15m
    ratio_ok: (RUNTIME — caller flag)
    fires = signal AND alignment_ok AND ratio_ok AND floor_ok

PURITY: per-bar NPZ fields only (relative_volume_15m, high/low/open/close_15m,
alignment, dc_low4_15m, dc_high4_15m, current_price). ratio_ok is the single
runtime-blocked input, isolated as a flag. Mirrors strategy_enhancements.py
_pyramid_fires (one shared core for scalar + vec).
"""
from typing import Tuple
import numpy as np

_RELVOL_THR = 3.0
_BODY_RATIO_THR = 0.7
_MIN_ALIGNMENT = 3.0


def _vol_spike_signal(relvol, high_15m, low_15m, open_15m, close_15m, is_long,
                      relvol_thr=_RELVOL_THR, body_ratio_thr=_BODY_RATIO_THR):
    """PURE per-bar detector. Mirrors detect_volume_spike. Returns 'BUY'/'SELL'/None."""
    if relvol < relvol_thr:
        return None
    rng = high_15m - low_15m
    if rng <= 0 or high_15m <= 0 or open_15m <= 0 or close_15m <= 0:
        return None
    body_ratio = abs(close_15m - open_15m) / rng
    if body_ratio < body_ratio_thr:
        return None
    is_bearish = close_15m < open_15m
    if is_bearish and is_long:
        return "BUY"
    if (not is_bearish) and (not is_long):
        return "SELL"
    return None


def _vol_spike_fires(relvol, high_15m, low_15m, open_15m, close_15m, alignment,
                     dc_low4_15m, dc_high4_15m, current_price, is_long, ratio_ok,
                     relvol_thr=_RELVOL_THR, body_ratio_thr=_BODY_RATIO_THR,
                     min_alignment=_MIN_ALIGNMENT):
    """Full fire = detector signal AND alignment AND ratio_ok AND DC-floor.
    Mirrors ez_positions_quick.py:15642-15671. Returns (fires, signal)."""
    sig = _vol_spike_signal(relvol, high_15m, low_15m, open_15m, close_15m, is_long,
                            relvol_thr, body_ratio_thr)
    if sig is None:
        return False, None
    floor_ok = True
    if is_long and dc_low4_15m > 0 and current_price < dc_low4_15m:
        floor_ok = False
    elif (not is_long) and dc_high4_15m > 0 and current_price > dc_high4_15m:
        floor_ok = False
    if (alignment >= min_alignment) and ratio_ok and floor_ok:
        return True, sig
    return False, sig


def check_vol_spike_reversal(config, indicators: dict, metrics: dict, is_long: bool,
                             current_price: float, ratio_ok: bool = True) -> Tuple[bool, float, str]:
    """LIVE/scalar path. Returns (fires, score_floor, reason). score_floor mirrors the
    live `score = max(score, 20)`. ratio_ok supplied by the caller (runtime L/S feed)."""
    def g(k, d):
        v = (indicators or {}).get(k, d)
        try:
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d
    relvol = g("relative_volume_15m", 0.0)
    high_15m = g("high_15m", 0.0)
    low_15m = g("low_15m", 0.0)
    open_15m = g("open_15m", 0.0)
    close_15m = g("close_15m", 0.0)
    alignment = g("alignment", 0.0)
    dc_low4 = g("dc_low4_15m", 0.0)
    dc_high4 = g("dc_high4_15m", 0.0)
    relvol_thr = float(getattr(config, "VOL_SPIKE_RELVOL_THRESHOLD", _RELVOL_THR))
    body_thr = float(getattr(config, "VOL_SPIKE_BODY_RATIO", _BODY_RATIO_THR))
    min_align = float(getattr(config, "VOL_SPIKE_MIN_ALIGNMENT", _MIN_ALIGNMENT))
    fires, sig = _vol_spike_fires(relvol, high_15m, low_15m, open_15m, close_15m,
                                  alignment, dc_low4, dc_high4, float(current_price),
                                  is_long, ratio_ok, relvol_thr, body_thr, min_align)
    if not fires:
        return False, 0.0, ""
    reason = f"VOL_SPIKE_REVERSAL_{sig}_relvol={relvol:.1f}_align={alignment:.0f}"
    return True, 20.0, reason


def check_vol_spike_reversal_vec(config, relvol_arr, high_15m_arr, low_15m_arr,
                                 open_15m_arr, close_15m_arr, alignment_arr,
                                 dc_low4_15m_arr, dc_high4_15m_arr, price_arr,
                                 is_long, ratio_ok_arr):
    """VECTORIZED per-bar fire mask. SAME thresholds + SAME math as the scalar core.
    ratio_ok_arr is the per-bar runtime flag (engine supplies True when no L/S feed)."""
    relvol = np.asarray(relvol_arr, dtype=float)
    hi = np.asarray(high_15m_arr, dtype=float)
    lo = np.asarray(low_15m_arr, dtype=float)
    op = np.asarray(open_15m_arr, dtype=float)
    cl = np.asarray(close_15m_arr, dtype=float)
    al = np.asarray(alignment_arr, dtype=float)
    dcl4 = np.asarray(dc_low4_15m_arr, dtype=float)
    dch4 = np.asarray(dc_high4_15m_arr, dtype=float)
    px = np.asarray(price_arr, dtype=float)
    ratio_ok = np.asarray(ratio_ok_arr, dtype=bool)
    relvol_thr = float(getattr(config, "VOL_SPIKE_RELVOL_THRESHOLD", _RELVOL_THR))
    body_thr = float(getattr(config, "VOL_SPIKE_BODY_RATIO", _BODY_RATIO_THR))
    min_align = float(getattr(config, "VOL_SPIKE_MIN_ALIGNMENT", _MIN_ALIGNMENT))
    rng = hi - lo
    valid = (rng > 0) & (hi > 0) & (op > 0) & (cl > 0)
    body_ratio = np.divide(np.abs(cl - op), rng, out=np.zeros_like(rng), where=valid)
    det_ok = (relvol >= relvol_thr) & valid & (body_ratio >= body_thr)
    is_bearish = cl < op
    if is_long:
        sig_ok = det_ok & is_bearish
        floor_ok = ~((dcl4 > 0) & (px < dcl4))
    else:
        sig_ok = det_ok & (~is_bearish)
        floor_ok = ~((dch4 > 0) & (px > dch4))
    return sig_ok & (al >= min_align) & ratio_ok & floor_ok
