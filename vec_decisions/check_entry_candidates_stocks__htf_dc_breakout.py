"""HTF_DC_BREAKOUT (stocks entry) — additive breakout entry signal.

LIVE SOURCE: tradier_manage.py should_enter_long ~11884 / should_enter_short ~12167.
  LONG  fires (return True) when dch_prev>0 AND current_price > dch_prev*(1+thr)
        AND (not require_w OR (W WT not has) OR wt1_W>wt2_W)
  SHORT fires when dcl_prev>0 AND current_price>0 AND current_price < dcl_prev*(1-thr)
        AND (not require_w OR (W WT not has) OR wt1_W<wt2_W)
  thr = HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT/100; require_w = HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT (True).
  dch_prev = dc_high_<tf>_prev fallback dc_high_<tf>; dcl_prev = dc_low_<tf>_prev fallback dc_low_<tf>.
Returns whether breakout entry FIRES (True == enter).

W-WT gate semantics (faithful): w_ok defaults True; only overridden to (wt1_W>wt2_W for long /
wt1_W<wt2_W for short) when require_w AND (wt1_W!=0 or wt2_W!=0).

2026-05-30 PARITY: shared pure predicate _htf_dc_breakout_fires() drives scalar+vec.
NPZ fields: dc_high_<tf>_prev, dc_low_<tf>_prev, close(=current_price), wt1_W, wt2_W (all present).
"""
from typing import Tuple


def _htf_dc_breakout_fires(dc_prev: float, current_price: float, wt1_W: float, wt2_W: float,
                           is_long: bool, thr: float, require_w: bool) -> bool:
    """PURE per-bar fire predicate. dc_prev = dc_high_prev (long) / dc_low_prev (short)."""
    w_has = (wt1_W != 0 or wt2_W != 0)
    if is_long:
        if not (dc_prev > 0 and current_price > dc_prev * (1.0 + thr)):
            return False
        w_ok = True
        if require_w and w_has:
            w_ok = wt1_W > wt2_W
        return w_ok
    if not (dc_prev > 0 and current_price > 0 and current_price < dc_prev * (1.0 - thr)):
        return False
    w_ok = True
    if require_w and w_has:
        w_ok = wt1_W < wt2_W
    return w_ok


def _htf_dc_breakout_params(config):
    thr = float(getattr(config, "HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT", 0.0)) / 100.0
    require_w = bool(getattr(config, "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT", True))
    return thr, require_w


def check_htf_dc_breakout(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason)."""
    if not getattr(config, "HTF_DC_BREAKOUT_TRADIER_ENABLED", False):
        return False, ""
    tf = str(getattr(config, "HTF_DC_BREAKOUT_TRADIER_TF", "4h"))
    thr, require_w = _htf_dc_breakout_params(config)
    if is_long:
        dc_prev = float(indicators.get(f"dc_high_{tf}_prev", indicators.get(f"dc_high_{tf}", 0)) or 0)
    else:
        dc_prev = float(indicators.get(f"dc_low_{tf}_prev", indicators.get(f"dc_low_{tf}", 0)) or 0)
    current_price = float(indicators.get("current_price", 0) or 0)
    wt1_W = float(indicators.get("wt1_W", 0) or 0)
    wt2_W = float(indicators.get("wt2_W", 0) or 0)
    if _htf_dc_breakout_fires(dc_prev, current_price, wt1_W, wt2_W, is_long, thr, require_w):
        side = "LONG" if is_long else "SHORT"
        return True, f"HTF_DC_BREAKOUT_{side}_{tf}"
    return False, ""


def check_htf_dc_breakout_vec(config, dc_prev_arr, current_price_arr, wt1_W_arr, wt2_W_arr, is_long):
    """VECTORIZED fire mask. SAME predicate. dc_prev_arr = dc_high_<tf>_prev (long) / dc_low_<tf>_prev (short)."""
    import numpy as np
    dc = np.asarray(dc_prev_arr, dtype=float)
    p = np.asarray(current_price_arr, dtype=float)
    w1 = np.asarray(wt1_W_arr, dtype=float)
    w2 = np.asarray(wt2_W_arr, dtype=float)
    thr, require_w = _htf_dc_breakout_params(config)
    w_has = (w1 != 0) | (w2 != 0)
    if is_long:
        price_ok = (dc > 0) & (p > dc * (1.0 + thr))
        if require_w:
            w_ok = np.where(w_has, w1 > w2, True)
        else:
            w_ok = np.ones_like(p, dtype=bool)
    else:
        price_ok = (dc > 0) & (p > 0) & (p < dc * (1.0 - thr))
        if require_w:
            w_ok = np.where(w_has, w1 < w2, True)
        else:
            w_ok = np.ones_like(p, dtype=bool)
    return price_ok & w_ok
