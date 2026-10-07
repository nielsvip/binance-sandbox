"""HTF_QUICK_TP — 1h exhausted but 4h trend intact, take quick profit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py evaluate_stop() lines ~6733-6746. Guard: gain >= noloss (state).
  Reads:
    k_1h = stoch_k_1h (default 50)
    k4h  = stoch_k_4h (default 50) ; d4h = stoch_d_4h (default 50)
    wt_bull_4h = wt1_4h > wt2_4h
    k5m, k5m_prev (default 50), k15m, k15m_prev (default 50)
    _ltf_down = k5m < k5m_prev AND k15m < k15m_prev
    _ltf_up   = k5m > k5m_prev AND k15m > k15m_prev
  LONG  fires when k_1h > 90 AND _ltf_down AND (wt_bull_4h OR k4h > d4h) AND k4h < 60
  SHORT fires when k_1h < 10 AND _ltf_up   AND (NOT wt_bull_4h OR k4h < d4h) AND k4h > 40

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar and vec
share the SAME pure per-bar predicate _htf_quick_tp_fires(), so the two paths CANNOT drift.

STATE SEAM: gain >= noloss is per-position state — applied by the wrapper/caller. The pure
core is indicator-only. (Note: _noloss_min_t here is NOLOSS_MIN_PROFIT_PCT_TRADIER default
1.0 at this live site — see line 6749's earlier rebinding; faithful default kept as 1.0.)

NPZ / indicator fields read by the core:
  stoch_k_1h, stoch_k_4h, stoch_d_4h, wt1_4h, wt2_4h, stoch_k_5m, stoch_k_5m_prev,
  stoch_k_15m, stoch_k_15m_prev.
"""
from typing import Tuple


def _htf_quick_tp_fires(k_1h: float, k4h: float, d4h: float, wt_bull_4h: bool, k5m: float,
                        k5m_prev: float, k15m: float, k15m_prev: float, is_long: bool) -> bool:
    """PURE per-bar condition. Faithful replica of tradier_manage.py lines 6743 / 6745."""
    ltf_down = k5m < k5m_prev and k15m < k15m_prev
    ltf_up = k5m > k5m_prev and k15m > k15m_prev
    if is_long:
        return k_1h > 90 and ltf_down and (wt_bull_4h or k4h > d4h) and k4h < 60
    return k_1h < 10 and ltf_up and ((not wt_bull_4h) or k4h < d4h) and k4h > 40


def _htf_quick_tp_min_gain(config) -> float:
    return float(getattr(config, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 1.0))


def check_htf_quick_tp(config, indicators: dict, gain_pct: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Indicator-only fire from the shared
    predicate; gain>=noloss state gate applied here (faithful to line 6735)."""
    if gain_pct < _htf_quick_tp_min_gain(config):
        return False, ""
    k_1h = float(indicators.get("stoch_k_1h", 50) or 50)
    k4h = float(indicators.get("stoch_k_4h", 50) or 50)
    d4h = float(indicators.get("stoch_d_4h", 50) or 50)
    wt_bull_4h = float(indicators.get("wt1_4h", 0) or 0) > float(indicators.get("wt2_4h", 0) or 0)
    k5m = float(indicators.get("stoch_k_5m", 50) or 50)
    k5m_prev = float(indicators.get("stoch_k_5m_prev", 50) or 50)
    k15m = float(indicators.get("stoch_k_15m", 50) or 50)
    k15m_prev = float(indicators.get("stoch_k_15m_prev", 50) or 50)
    if not _htf_quick_tp_fires(k_1h, k4h, d4h, wt_bull_4h, k5m, k5m_prev, k15m, k15m_prev, is_long):
        return False, ""
    if is_long:
        return True, f"HTF_QUICK_TP_L(g={gain_pct:.2f}%,k1h={k_1h:.0f}>90,ltf↓,k4h={k4h:.0f}<60↑)_REENTER_1.5x"
    return True, f"HTF_QUICK_TP_S(g={gain_pct:.2f}%,k1h={k_1h:.0f}<10,ltf↑,k4h={k4h:.0f}>40↓)_REENTER_1.5x"


def check_htf_quick_tp_vec(config, k_1h_arr, k4h_arr, d4h_arr, wt1_4h_arr, wt2_4h_arr,
                           k5m_arr, k5m_prev_arr, k15m_arr, k15m_prev_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as the live scalar.
    Returns a bool ndarray; caller layers gain>=noloss state gate.
    Arrays per-bar (NPZ): stoch_k_1h, stoch_k_4h, stoch_d_4h, wt1_4h, wt2_4h, stoch_k_5m,
    stoch_k_5m_prev, stoch_k_15m, stoch_k_15m_prev."""
    import numpy as np
    k1 = np.asarray(k_1h_arr, dtype=float)
    k4 = np.asarray(k4h_arr, dtype=float)
    d4 = np.asarray(d4h_arr, dtype=float)
    bull4 = np.asarray(wt1_4h_arr, dtype=float) > np.asarray(wt2_4h_arr, dtype=float)
    k5 = np.asarray(k5m_arr, dtype=float)
    k5p = np.asarray(k5m_prev_arr, dtype=float)
    k15 = np.asarray(k15m_arr, dtype=float)
    k15p = np.asarray(k15m_prev_arr, dtype=float)
    ltf_down = (k5 < k5p) & (k15 < k15p)
    ltf_up = (k5 > k5p) & (k15 > k15p)
    if is_long:
        return (k1 > 90) & ltf_down & (bull4 | (k4 > d4)) & (k4 < 60)
    return (k1 < 10) & ltf_up & ((~bull4) | (k4 < d4)) & (k4 > 40)
