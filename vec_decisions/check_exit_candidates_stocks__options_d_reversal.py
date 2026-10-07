"""OPTIONS_D_REVERSAL — Daily WT-cross + Heikin-Ashi reversal options exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6512-6528.
  LONG  option exit: wt_cross_D == "BEAR" AND wt1_D < wt2_D AND ha_D == "red"
  SHORT option exit: wt_cross_D == "BULL" AND wt1_D > wt2_D AND ha_D == "green"
  Only reached when the position is an options position (caller state _is_options_pos).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_options_d_reversal) AND vec (check_options_d_reversal_vec) share the SAME pure
per-bar predicate _options_d_reversal_fires(), so the two paths CANNOT drift. Pure core:
no config, no state.

STATE SEAM: the _is_options_pos branch selection is caller state (only options positions
reach this path). The pure core covers the indicator-only D-reversal test.

CATEGORICAL FIELDS: wt_cross_D and ha_D are string-categorical NPZ fields. They are
encoded as integer codes for the vec path (caller maps "BEAR"->1/"BULL"->2/other->0 for
cross; "red"->1/"green"->2/other->0 for ha). The scalar wrapper uses the raw strings; the
shared core takes booleans so both paths feed the IDENTICAL predicate.

NPZ / indicator fields read:
  wt1_D, wt2_D (numeric); wt_cross_D, ha_D (categorical).
"""
from typing import Tuple


def _options_d_reversal_fires(wt1_D: float, wt2_D: float, cross_is_bear: bool,
                              cross_is_bull: bool, ha_is_red: bool, ha_is_green: bool,
                              is_long: bool) -> bool:
    """PURE per-bar condition. Faithful replica of tradier_manage.py lines 6522-6523.
    cross_is_bear/bull and ha_is_red/green are pre-decoded booleans so the scalar (string)
    and vec (int-code) paths share IDENTICAL logic."""
    if is_long:
        return cross_is_bear and wt1_D < wt2_D and ha_is_red
    return cross_is_bull and wt1_D > wt2_D and ha_is_green


def check_options_d_reversal(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Reads the raw string categoricals
    exactly as live (lines 6518-6519) then feeds the shared boolean predicate."""
    wt1_D = float(indicators.get("wt1_D", 0) or 0)
    wt2_D = float(indicators.get("wt2_D", 0) or 0)
    wt_cross_D = str(indicators.get("wt_cross_D", ""))
    ha_D = str(indicators.get("ha_D", "")).lower()
    fires = _options_d_reversal_fires(wt1_D, wt2_D, wt_cross_D == "BEAR", wt_cross_D == "BULL",
                                      ha_D == "red", ha_D == "green", is_long)
    if not fires:
        return False, ""
    return True, f"OPTIONS_D_REVERSAL_wt1D={wt1_D:.1f}_wt2D={wt2_D:.1f}_cross={wt_cross_D}_ha={ha_D}_g={gain_pct:.2f}%"


def check_options_d_reversal_vec(config, wt1_D_arr, wt2_D_arr, cross_D_code_arr, ha_D_code_arr,
                                 is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as the live scalar.
    Categoricals are passed as integer-code arrays (caller encodes:
      cross: BEAR=1, BULL=2, other=0 ; ha: red=1, green=2, other=0).
    Returns a bool ndarray. Arrays per-bar (NPZ): wt1_D, wt2_D, wt_cross_D code, ha_D code."""
    import numpy as np
    w1 = np.asarray(wt1_D_arr, dtype=float)
    w2 = np.asarray(wt2_D_arr, dtype=float)
    cross = np.asarray(cross_D_code_arr, dtype=int)
    ha = np.asarray(ha_D_code_arr, dtype=int)
    if is_long:
        return (cross == 1) & (w1 < w2) & (ha == 1)
    return (cross == 2) & (w1 > w2) & (ha == 2)
