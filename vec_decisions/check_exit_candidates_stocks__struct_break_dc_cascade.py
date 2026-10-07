"""STRUCT_BREAK_DC cascade — time-tiered Donchian structural stop (shared scalar+vec).

LIVE SOURCE: tradier_manage.py evaluate_stop() lines ~6786-6832.
  Cascading DC level by hold age (state seam):
    hold <= 20 min : DC4_5m  (dc_low4_5m / dc_high4_5m), buf=0.004, REQUIRES close_5m_prev
                     to confirm the break (LONG: close_5m_prev < dc_stop ; SHORT: > dc_stop).
    20 < hold <= 60: DC_5m   (dc_low_5m / dc_high_5m),   buf=0.0015, confirmed=True.
    hold > 60 min  : DC_1h   (dc_low_1h / dc_high_1h),   buf=0.001,  confirmed=True.
  LONG  fires when dc_stop>0 AND price < dc_stop*(1-buf) AND confirmed AND gain>=noloss.
  SHORT fires when dc_stop<999999 AND price > dc_stop*(1+buf) AND confirmed AND gain>=noloss.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar and vec
share the SAME pure per-bar predicate _struct_break_dc_fires(), so the two paths CANNOT
drift. Pure core: takes the already-selected dc_stop, buffer and confirmed flag (no config).

STATE SEAM: hold_time_min selects which DC tier/buffer applies (per-position age state) and
gain>=noloss is per-position state. BOTH are layered by the caller. The age->tier selection
is provided as a helper (_select_tier) that the caller uses identically in scalar and vec
(per-bar age array -> per-bar tier). The pure core is the band-break-+-buffer test only.

NPZ / indicator fields read by the core:
  close (current_price), dc_low4_5m, dc_high4_5m, dc_low_5m, dc_high_5m, dc_low_1h, dc_high_1h,
  close_5m_prev. hold age + gain = state.
"""
from typing import Tuple


def _struct_break_dc_fires(current_price: float, dc_stop: float, buf: float, confirmed: bool,
                           is_long: bool) -> bool:
    """PURE per-bar condition (after tier/buffer/confirm have been selected). Faithful replica
    of tradier_manage.py lines 6811 (LONG) / 6831 (SHORT), minus the gain state gate."""
    if not confirmed:
        return False
    if is_long:
        return dc_stop > 0 and current_price < dc_stop * (1 - buf)
    return dc_stop < 999999 and current_price > dc_stop * (1 + buf)


def _select_tier(indicators: dict, hold_time_min: float, current_price: float, is_long: bool):
    """Replicate the live age->tier selection (lines 6793-6829). Returns (dc_stop, buf,
    confirmed, level_name). For the <=20m tier, confirmation uses close_5m_prev exactly as live."""
    close_5m_prev = float(indicators.get("close_5m_prev", current_price) or current_price)
    if is_long:
        if hold_time_min <= 20.0:
            dc_stop = float(indicators.get("dc_low4_5m", 0) or 0)
            confirmed = close_5m_prev < dc_stop if dc_stop > 0 else False
            return dc_stop, 0.004, confirmed, "DC4_5m"
        if hold_time_min <= 60.0:
            return float(indicators.get("dc_low_5m", 0) or 0), 0.0015, True, "DC_5m"
        return float(indicators.get("dc_low_1h", 0) or 0), 0.001, True, "DC_1h"
    if hold_time_min <= 20.0:
        dc_stop = float(indicators.get("dc_high4_5m", 999999) or 999999)
        confirmed = close_5m_prev > dc_stop if dc_stop < 999999 else False
        return dc_stop, 0.004, confirmed, "DC4_5m"
    if hold_time_min <= 60.0:
        return float(indicators.get("dc_high_5m", 999999) or 999999), 0.0015, True, "DC_5m"
    return float(indicators.get("dc_high_1h", 999999) or 999999), 0.001, True, "DC_1h"


def _sb_min_gain(config) -> float:
    return float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 3.0))


def check_struct_break_dc_cascade(config, indicators: dict, gain_pct: float,
                                  current_price: float, hold_time_min: float,
                                  is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Tier selection + band test from the
    shared helpers; gain>=noloss state gate applied here (faithful to lines 6811/6831)."""
    dc_stop, buf, confirmed, level = _select_tier(indicators, hold_time_min, current_price, is_long)
    if not _struct_break_dc_fires(current_price, dc_stop, buf, confirmed, is_long):
        return False, ""
    if gain_pct < _sb_min_gain(config):
        return False, ""
    side = "LOW" if is_long else "HIGH"
    return True, f"STRUCT_BREAK_{level}_{side}_({dc_stop:.2f})_gain:{gain_pct:.2f}%"


def check_struct_break_dc_cascade_vec(config, current_price_arr, dc_stop_arr, buf_arr,
                                      confirmed_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as the live scalar core.
    The caller pre-selects per-bar dc_stop / buf / confirmed via the age tier (state seam),
    then layers gain>=noloss. Returns a bool ndarray.
    Arrays per-bar: close, selected dc_stop, selected buf, confirmed."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    s = np.asarray(dc_stop_arr, dtype=float)
    b = np.asarray(buf_arr, dtype=float)
    c = np.asarray(confirmed_arr, dtype=bool)
    if is_long:
        return c & (s > 0) & (p < s * (1 - b))
    return c & (s < 999999) & (p > s * (1 + b))
