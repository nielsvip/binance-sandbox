"""PROCESS_POSITION_STOCKS · DELTA_HTF_GATE — HTF veto on DELTA entry (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position lines ~2811-2842 (the HTF block portion
of the DELTA ENGINE entry). The DELTA signal itself (delta_tracker.update → entry_long /
entry_short) is RUNTIME-STATEFUL (a per-symbol streaming delta tracker NOT in NPZ) — that
is classified runtime_blocked and is NOT vectorized here. What IS pure and vectorizable is
the HTF GATE that blocks an otherwise-firing delta entry:

  gate = DELTA_HTF_GATE (default 'none'). When != 'none':
    '4h' / '4h_D' / '4h_D_strict':
       4h_ok = (wt1_4h>wt2_4h) if LONG else (wt1_4h<wt2_4h) ; block if not 4h_ok
    '4h_D' / '4h_D_strict' (only if not already blocked):
       D_ok  = (wt1_D>wt2_D) if LONG else (wt1_D<wt2_D)      ; block if not D_ok
    '4h_D_strict' (only if not already blocked):
       LONG  block if stoch_k_4h>80 OR stoch_k_D>80
       SHORT block if stoch_k_4h<20 OR stoch_k_D<20

  Plus DELTA_ATR_ENTRY_FILTER (lines ~2837-2842): block if atr_1h>0 AND
  |price - close_5m_prev| < atr_1h*0.3. (per-bar pure on atr_1h, close, close_5m_prev)

2026-05-30 PARITY: live scalar (delta_htf_blocked) AND vec twin (delta_htf_blocked_vec)
derive from the SAME pure predicate _delta_htf_blocked() — cannot drift.

CLASSIFICATION: vectorized (the gate only). Pure per-bar predicates on NPZ fields
wt1/2_4h, wt1/2_D, stoch_k_4h, stoch_k_D, atr_1h, close, close_5m_prev. The delta-fire
itself (whether entry_long/entry_short is True) is layered by the caller (runtime).

NPZ / indicator fields read: wt1_4h, wt2_4h, wt1_D, wt2_D, stoch_k_4h, stoch_k_D,
atr_1h, close, close_5m_prev. All present in backtest NPZ.
"""
from typing import Tuple


def _delta_htf_blocked(current_price: float, wt1_4h: float, wt2_4h: float, wt1_D: float,
                       wt2_D: float, stoch_k_4h: float, stoch_k_D: float, atr_1h: float,
                       close_5m_prev: float, is_long: bool, htf_gate: str,
                       atr_filter: bool) -> Tuple[bool, str]:
    """PURE per-bar DELTA-entry block predicate. Returns (blocked, reason).
    Faithful replica of tradier_manage.py lines 2811-2842."""
    if htf_gate != "none":
        if htf_gate in ("4h", "4h_D", "4h_D_strict"):
            h4_ok = (wt1_4h > wt2_4h) if is_long else (wt1_4h < wt2_4h)
            if not h4_ok:
                return True, "HTF_4h_against"
        if htf_gate in ("4h_D", "4h_D_strict"):
            d_ok = (wt1_D > wt2_D) if is_long else (wt1_D < wt2_D)
            if not d_ok:
                return True, "HTF_D_against"
        if htf_gate == "4h_D_strict":
            if is_long and (stoch_k_4h > 80 or stoch_k_D > 80):
                return True, "HTF_K_overextended"
            if (not is_long) and (stoch_k_4h < 20 or stoch_k_D < 20):
                return True, "HTF_K_oversold"
    if atr_filter and atr_1h > 0:
        bar_move = abs(current_price - close_5m_prev)
        if bar_move < atr_1h * 0.3:
            return True, "ATR_noise"
    return False, ""


def _delta_htf_params(config):
    return (str(getattr(config, "DELTA_HTF_GATE", "none")),
            bool(getattr(config, "DELTA_ATR_ENTRY_FILTER", False)))


def delta_htf_blocked(config, indicators: dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (blocked, reason). Block decision from the shared predicate.
    Live default reads mirror the .get(...) defaults (wt=0, k=50, atr=0, close_5m_prev=price)."""
    gate, atr_filter = _delta_htf_params(config)
    wt1_4h = float(indicators.get("wt1_4h", 0) or 0)
    wt2_4h = float(indicators.get("wt2_4h", 0) or 0)
    wt1_D = float(indicators.get("wt1_D", 0) or 0)
    wt2_D = float(indicators.get("wt2_D", 0) or 0)
    stoch_k_4h = float(indicators.get("stoch_k_4h", 50) or 50)
    stoch_k_D = float(indicators.get("stoch_k_D", 50) or 50)
    atr_1h = float(indicators.get("atr_1h", 0) or 0)
    close_5m_prev = float(indicators.get("close_5m_prev", current_price) or current_price)
    return _delta_htf_blocked(current_price, wt1_4h, wt2_4h, wt1_D, wt2_D, stoch_k_4h, stoch_k_D,
                              atr_1h, close_5m_prev, is_long, gate, atr_filter)


def delta_htf_blocked_vec(config, current_price_arr, wt1_4h_arr, wt2_4h_arr, wt1_D_arr,
                          wt2_D_arr, stoch_k_4h_arr, stoch_k_D_arr, atr_1h_arr,
                          close_5m_prev_arr, is_long):
    """VECTORIZED per-bar BLOCK mask — backtest path. SAME params + SAME predicate as the live
    scalar delta_htf_blocked (vectorized via numpy). Returns a bool ndarray (True = blocked)."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    gate, atr_filter = _delta_htf_params(config)
    w14 = np.asarray(wt1_4h_arr, dtype=float)
    w24 = np.asarray(wt2_4h_arr, dtype=float)
    w1D = np.asarray(wt1_D_arr, dtype=float)
    w2D = np.asarray(wt2_D_arr, dtype=float)
    k4 = np.asarray(stoch_k_4h_arr, dtype=float)
    kD = np.asarray(stoch_k_D_arr, dtype=float)
    a1 = np.asarray(atr_1h_arr, dtype=float)
    cp = np.asarray(close_5m_prev_arr, dtype=float)
    blocked = np.zeros(len(p), dtype=bool)
    if gate != "none":
        if gate in ("4h", "4h_D", "4h_D_strict"):
            h4_ok = (w14 > w24) if is_long else (w14 < w24)
            blocked |= ~h4_ok
        if gate in ("4h_D", "4h_D_strict"):
            d_ok = (w1D > w2D) if is_long else (w1D < w2D)
            blocked |= ~d_ok
        if gate == "4h_D_strict":
            if is_long:
                blocked |= (k4 > 80) | (kD > 80)
            else:
                blocked |= (k4 < 20) | (kD < 20)
    if atr_filter:
        bar_move = np.abs(p - cp)
        blocked |= (a1 > 0) & (bar_move < a1 * 0.3)
    return blocked
