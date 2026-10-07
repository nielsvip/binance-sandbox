"""PROCESS_POSITION_STOCKS · RULE_A_RETEST — D/W breakout-then-retest entry (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position lines ~2442-2480 (RULE_A_RETEST block).
  Data-ok guard: |wt1_D|>1e-9 AND |wt2_D|>1e-9 AND dc_basis_D>0 AND atr_D>0 AND price>0.
  dist_atr = |price - dc_basis_D| / atr_D
  armed_long  = wt1_D>wt2_D AND wt1_W>wt2_W AND price>dc_basis_D
  armed_short = wt1_D<wt2_D AND wt1_W<wt2_W AND price<dc_basis_D
  retest_band = dist_atr < retest_mult            (retest_mult default 0.30)
  k3m_xup = k_3m_prev<30 AND k_3m>d_3m AND k_3m>k_3m_prev
  k3m_xdn = k_3m_prev>70 AND k_3m<d_3m AND k_3m<k_3m_prev
  15m_bull = wt1_15m>wt2_15m ; 15m_bear = wt1_15m<wt2_15m
  1h_bull  = wt1_1h>wt2_1h  ; 1h_bear  = wt1_1h<wt2_1h
  FIRE LONG  = is_long AND armed_long AND retest_band AND k3m_xup AND 15m_bull AND 1h_bull
  FIRE SHORT = (not is_long) AND armed_short AND retest_band AND k3m_xdn AND 15m_bear AND 1h_bear

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_rule_a_retest) AND vectorized backtest (check_rule_a_retest_vec) BOTH derive their
fire decision from the SAME pure per-bar predicate _rule_a_retest_fires() below — cannot drift.
Pure core: no config (threshold passed in), no state.

CLASSIFICATION: vectorized. Pure per-bar predicate on NPZ fields. The (not has_position) +
is_symbol_tradeable gates are state/runtime gates layered by the caller (state seam).

NPZ / indicator fields read by the core (via the wrappers):
  - wt1_D, wt2_D, wt1_W, wt2_W, dc_basis_D, atr_D     (Daily/Weekly armed + retest band)
  - wt1_15m, wt2_15m, wt1_1h, wt2_1h                  (LTF confirmation)
  - stoch_k_3m, stoch_d_3m, k_3m_prev                 (3m stoch cross)
  - close (= current_price)
All present in backtest NPZ. Live reads stoch_k_3m or k_3m, stoch_d_3m or d_3m, and
k_3m_prev or stoch_k_3m_prev with defaults 50; the wrappers honor those defaults.
"""
from typing import Tuple


def _rule_a_retest_fires(current_price: float, wt1_D: float, wt2_D: float, wt1_W: float,
                         wt2_W: float, dc_basis_D: float, atr_D: float, wt1_15m: float,
                         wt2_15m: float, wt1_1h: float, wt2_1h: float, k_3m: float,
                         d_3m: float, k_3m_prev: float, is_long: bool,
                         retest_mult: float) -> bool:
    """PURE per-bar fire condition for RULE_A_RETEST.
    Faithful replica of tradier_manage.py lines 2458-2473 (incl. the data-ok guard)."""
    data_ok = (abs(wt1_D) > 1e-9 and abs(wt2_D) > 1e-9 and dc_basis_D > 0 and atr_D > 0 and current_price > 0)
    if not data_ok:
        return False
    dist_atr = abs(current_price - dc_basis_D) / atr_D
    retest_band = dist_atr < retest_mult
    if not retest_band:
        return False
    if is_long:
        armed = (wt1_D > wt2_D and wt1_W > wt2_W and current_price > dc_basis_D)
        k_cross = (k_3m_prev < 30 and k_3m > d_3m and k_3m > k_3m_prev)
        ltf = (wt1_15m > wt2_15m) and (wt1_1h > wt2_1h)
        return armed and k_cross and ltf
    armed = (wt1_D < wt2_D and wt1_W < wt2_W and current_price < dc_basis_D)
    k_cross = (k_3m_prev > 70 and k_3m < d_3m and k_3m < k_3m_prev)
    ltf = (wt1_15m < wt2_15m) and (wt1_1h < wt2_1h)
    return armed and k_cross and ltf


def _rule_a_retest_mult(config) -> float:
    return float(getattr(config, "BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT", 0.30))


def check_rule_a_retest(config, indicators: dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_open, reason). Fire decision from the shared predicate.
    Caller applies (not has_position) + is_symbol_tradeable (state). Defaults mirror live."""
    if not bool(getattr(config, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
        return False, ""
    mult = _rule_a_retest_mult(config)
    wt1_D = float(indicators.get("wt1_D", 0) or 0)
    wt2_D = float(indicators.get("wt2_D", 0) or 0)
    wt1_W = float(indicators.get("wt1_W", 0) or 0)
    wt2_W = float(indicators.get("wt2_W", 0) or 0)
    dc_basis_D = float(indicators.get("dc_basis_D", 0) or 0)
    atr_D = float(indicators.get("atr_D", 0) or 0)
    wt1_15m = float(indicators.get("wt1_15m", 0) or 0)
    wt2_15m = float(indicators.get("wt2_15m", 0) or 0)
    wt1_1h = float(indicators.get("wt1_1h", 0) or 0)
    wt2_1h = float(indicators.get("wt2_1h", 0) or 0)
    k_3m = float(indicators.get("stoch_k_3m") or indicators.get("k_3m") or 50)
    d_3m = float(indicators.get("stoch_d_3m") or indicators.get("d_3m") or 50)
    k_3m_prev = float(indicators.get("k_3m_prev") or indicators.get("stoch_k_3m_prev") or k_3m)
    if not _rule_a_retest_fires(current_price, wt1_D, wt2_D, wt1_W, wt2_W, dc_basis_D, atr_D,
                                wt1_15m, wt2_15m, wt1_1h, wt2_1h, k_3m, d_3m, k_3m_prev,
                                is_long, mult):
        return False, ""
    return True, f"RULE_A_RETEST_{'LONG' if is_long else 'SHORT'}_px{current_price:.4f}_dcBD{dc_basis_D:.4f}"


def check_rule_a_retest_vec(config, current_price_arr, wt1_D_arr, wt2_D_arr, wt1_W_arr,
                            wt2_W_arr, dc_basis_D_arr, atr_D_arr, wt1_15m_arr, wt2_15m_arr,
                            wt1_1h_arr, wt2_1h_arr, k_3m_arr, d_3m_arr, k_3m_prev_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate + threshold as the live
    scalar (vectorized via numpy). Returns a bool ndarray. Caller layers (no position) +
    tradeable state gates. atr_D==0 / non-data bars yield False (guarded)."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    if not bool(getattr(config, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
        return np.zeros(len(p), dtype=bool)
    mult = _rule_a_retest_mult(config)
    w1D = np.asarray(wt1_D_arr, dtype=float)
    w2D = np.asarray(wt2_D_arr, dtype=float)
    w1W = np.asarray(wt1_W_arr, dtype=float)
    w2W = np.asarray(wt2_W_arr, dtype=float)
    dcD = np.asarray(dc_basis_D_arr, dtype=float)
    aD = np.asarray(atr_D_arr, dtype=float)
    w115 = np.asarray(wt1_15m_arr, dtype=float)
    w215 = np.asarray(wt2_15m_arr, dtype=float)
    w11h = np.asarray(wt1_1h_arr, dtype=float)
    w21h = np.asarray(wt2_1h_arr, dtype=float)
    k3 = np.asarray(k_3m_arr, dtype=float)
    d3 = np.asarray(d_3m_arr, dtype=float)
    k3p = np.asarray(k_3m_prev_arr, dtype=float)
    data_ok = (np.abs(w1D) > 1e-9) & (np.abs(w2D) > 1e-9) & (dcD > 0) & (aD > 0) & (p > 0)
    aD_safe = np.where(aD > 0, aD, 1.0)
    dist_atr = np.abs(p - dcD) / aD_safe
    retest_band = dist_atr < mult
    if is_long:
        armed = (w1D > w2D) & (w1W > w2W) & (p > dcD)
        k_cross = (k3p < 30) & (k3 > d3) & (k3 > k3p)
        ltf = (w115 > w215) & (w11h > w21h)
    else:
        armed = (w1D < w2D) & (w1W < w2W) & (p < dcD)
        k_cross = (k3p > 70) & (k3 < d3) & (k3 < k3p)
        ltf = (w115 < w215) & (w11h < w21h)
    return data_ok & retest_band & armed & k_cross & ltf
