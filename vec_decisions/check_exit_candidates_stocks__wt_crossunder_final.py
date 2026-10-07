"""WT_CROSSUNDER_FINAL / WT_CROSSOVER_FINAL — final-resort multi-TF WT exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop()
  LONG  block (lines ~6427-6443 within delta gate, replicated standalone ~6484-6497):
    _ltf_down     = wt1_5m < wt2_5m
    _15m_confirm  = (wt1_15m < wt2_15m) OR (wt1_15m > 95)
    _htf_against  = (wt1_1h < wt2_1h) OR (wt1_4h < wt2_4h) OR (wt1_D < wt2_D)
    fires when _ltf_down AND _15m_confirm AND _htf_against  (then parabolic-bypass layered by caller)
  SHORT block (lines ~6444-6460 / standalone ~6498-6511):
    _ltf_up       = wt1_5m > wt2_5m
    _15m_confirm  = (wt1_15m > wt2_15m) OR (wt1_15m < -95)
    _htf_against  = (wt1_1h > wt2_1h) OR (wt1_4h > wt2_4h) OR (wt1_D > wt2_D)
    fires when _ltf_up AND _15m_confirm AND _htf_against

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_wt_crossunder_final) AND vec (check_wt_crossunder_final_vec) share the SAME pure
per-bar predicate _wt_crossunder_final_fires(), so the two paths CANNOT drift. Pure
core: no config, no state.

STATE/REGIME SEAM: the parabolic-uptrend (LONG) / parabolic-downtrend (SHORT) BYPASS
(_parabolic_state) suppresses the fire and is NOT in this core — it depends on a
separate regime computation (config-driven, multi-field) that the caller layers exactly
as the live code does (line 6436 / 6453). The NOLOSS / gain gate that precedes the delta
block is also caller state. This core is the pure indicator predicate (5m wt cross +
15m confirm + 1h/4h/D against) only.

5m note: live uses wt1_5m/wt2_5m with a crypto fallback to wt1_3m/wt2_3m. For stocks
(base TF 5m) the 5m fields are the live source; the wrapper passes those.

NPZ / indicator fields read by the core:
  wt1_5m,wt2_5m, wt1_15m,wt2_15m, wt1_1h,wt2_1h, wt1_4h,wt2_4h, wt1_D,wt2_D.
"""
from typing import Tuple


def _wt_crossunder_final_fires(wt1_5m: float, wt2_5m: float, wt1_15m: float, wt2_15m: float,
                               wt1_1h: float, wt2_1h: float, wt1_4h: float, wt2_4h: float,
                               wt1_D: float, wt2_D: float, is_long: bool) -> bool:
    """PURE per-bar indicator-only condition. Faithful replica of tradier_manage.py
    lines 6427-6434 (LONG) / 6444-6451 (SHORT)."""
    if is_long:
        ltf = wt1_5m < wt2_5m
        confirm15 = (wt1_15m < wt2_15m) or (wt1_15m > 95)
        htf = (wt1_1h < wt2_1h) or (wt1_4h < wt2_4h) or (wt1_D < wt2_D)
    else:
        ltf = wt1_5m > wt2_5m
        confirm15 = (wt1_15m > wt2_15m) or (wt1_15m < -95)
        htf = (wt1_1h > wt2_1h) or (wt1_4h > wt2_4h) or (wt1_D > wt2_D)
    return ltf and confirm15 and htf


def _wf(ind, k):
    return float((ind or {}).get(k, 0) or 0)


def check_wt_crossunder_final(config, indicators: dict, gain_pct: float, hold_time_min: float,
                              is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Indicator-only fire from the shared
    predicate. Parabolic-bypass + NOLOSS gate are caller state and NOT applied here.
    5m falls back to 3m exactly as live (line 6417/6474)."""
    wt1_5m = float((indicators or {}).get("wt1_5m", (indicators or {}).get("wt1_3m", 0)) or 0)
    wt2_5m = float((indicators or {}).get("wt2_5m", (indicators or {}).get("wt2_3m", 0)) or 0)
    wt1_15m, wt2_15m = _wf(indicators, "wt1_15m"), _wf(indicators, "wt2_15m")
    wt1_1h, wt2_1h = _wf(indicators, "wt1_1h"), _wf(indicators, "wt2_1h")
    wt1_4h, wt2_4h = _wf(indicators, "wt1_4h"), _wf(indicators, "wt2_4h")
    wt1_D, wt2_D = _wf(indicators, "wt1_D"), _wf(indicators, "wt2_D")
    if not _wt_crossunder_final_fires(wt1_5m, wt2_5m, wt1_15m, wt2_15m, wt1_1h, wt2_1h,
                                      wt1_4h, wt2_4h, wt1_D, wt2_D, is_long):
        return False, ""
    if is_long:
        xu_1h, xu_4h, xu_D = wt1_1h < wt2_1h, wt1_4h < wt2_4h, wt1_D < wt2_D
        return True, f"WT_CROSSUNDER_FINAL_5m_15m_1h{xu_1h}_4h{xu_4h}_D{xu_D}_g{gain_pct:.2f}%_hold{hold_time_min:.0f}m_MANDATORY_REENTRY"
    xo_1h, xo_4h, xo_D = wt1_1h > wt2_1h, wt1_4h > wt2_4h, wt1_D > wt2_D
    return True, f"WT_CROSSOVER_FINAL_5m_15m_1h{xo_1h}_4h{xo_4h}_D{xo_D}_g{gain_pct:.2f}%_hold{hold_time_min:.0f}m_MANDATORY_REENTRY"


def check_wt_crossunder_final_vec(config, wt1_5m_arr, wt2_5m_arr, wt1_15m_arr, wt2_15m_arr,
                                  wt1_1h_arr, wt2_1h_arr, wt1_4h_arr, wt2_4h_arr,
                                  wt1_D_arr, wt2_D_arr, is_long):
    """VECTORIZED per-bar indicator-only fire mask — backtest path. SAME predicate as the
    live scalar. Returns a bool ndarray; caller layers parabolic-bypass + NOLOSS state.
    Arrays per-bar (NPZ): wt1/wt2 for 5m,15m,1h,4h,D."""
    import numpy as np
    w15 = np.asarray(wt1_5m_arr, dtype=float)
    w25 = np.asarray(wt2_5m_arr, dtype=float)
    w115 = np.asarray(wt1_15m_arr, dtype=float)
    w215 = np.asarray(wt2_15m_arr, dtype=float)
    w11h = np.asarray(wt1_1h_arr, dtype=float)
    w21h = np.asarray(wt2_1h_arr, dtype=float)
    w14h = np.asarray(wt1_4h_arr, dtype=float)
    w24h = np.asarray(wt2_4h_arr, dtype=float)
    w1D = np.asarray(wt1_D_arr, dtype=float)
    w2D = np.asarray(wt2_D_arr, dtype=float)
    if is_long:
        ltf = w15 < w25
        confirm15 = (w115 < w215) | (w115 > 95)
        htf = (w11h < w21h) | (w14h < w24h) | (w1D < w2D)
    else:
        ltf = w15 > w25
        confirm15 = (w115 > w215) | (w115 < -95)
        htf = (w11h > w21h) | (w14h > w24h) | (w1D > w2D)
    return ltf & confirm15 & htf
