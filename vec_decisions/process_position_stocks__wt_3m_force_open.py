"""PROCESS_POSITION_STOCKS · WT_3M_FORCE_OPEN — 3m WaveTrend force-open entry (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position lines ~2415-2432 (WT_3M_FORCE_OPEN block).
  Fires (OPEN) when (not has_position) AND symbol tradeable AND current_price>0 AND:
    LONG  : wt1_3m > wt2_3m
    SHORT : wt1_3m < wt2_3m

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_wt_3m_force_open) AND the vectorized backtest (check_wt_3m_force_open_vec)
BOTH derive their fire decision from the SAME pure per-bar predicate
_wt_3m_force_open_fires() below, so the two paths CANNOT drift. Pure core: no config,
no state. Data source differs (live market_data indicators vs NPZ arrays) but the
predicate is identical.

CLASSIFICATION: vectorized. The fire predicate is a pure per-bar WT-cross comparison on
NPZ fields wt1_3m / wt2_3m. The (not has_position), is_symbol_tradeable, and current_price>0
gates are STATE/runtime gates layered by the caller (state seam) — exactly as the live
code does (line 2415-2421) and as the entry-union loop in the vec engine does.

NPZ / indicator fields read by the core (via the wrappers):
  - wt1_3m, wt2_3m   (3m WaveTrend lines)
Both present in backtest NPZ (3m base TF for crypto; for stocks base TF is 5m, but the
live stocks code reads wt1_3m/wt2_3m explicitly here — kept faithful to the field names).
"""
from typing import Tuple


def _wt_3m_force_open_fires(wt1_3m: float, wt2_3m: float, is_long: bool) -> bool:
    """PURE per-bar fire condition for WT_3M_FORCE_OPEN.
    Faithful replica of tradier_manage.py line 2420."""
    if is_long:
        return wt1_3m > wt2_3m
    return wt1_3m < wt2_3m


def check_wt_3m_force_open(config, indicators: dict, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_open, reason). Indicator-only fire decision from the
    shared predicate. Caller applies (not has_position) + is_symbol_tradeable + price>0 (state).
    Live default reads mirror safe_fetch_float(..., 0)."""
    if not bool(getattr(config, "WT_3M_FORCE_OPEN_ENABLED", True)):
        return False, ""
    wt1_3m = float(indicators.get("wt1_3m", 0) or 0)
    wt2_3m = float(indicators.get("wt2_3m", 0) or 0)
    if not _wt_3m_force_open_fires(wt1_3m, wt2_3m, is_long):
        return False, ""
    return True, f"WT_3M_FORCE_OPEN_{'LONG' if is_long else 'SHORT'}_wt1={wt1_3m:.1f}_wt2={wt2_3m:.1f}"


def check_wt_3m_force_open_vec(config, wt1_3m_arr, wt2_3m_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as the live scalar
    (vectorized via numpy). Returns a bool ndarray. Caller layers (no position) + tradeable
    + price>0 state gates."""
    import numpy as np
    w1 = np.asarray(wt1_3m_arr, dtype=float)
    if not bool(getattr(config, "WT_3M_FORCE_OPEN_ENABLED", True)):
        return np.zeros(len(w1), dtype=bool)
    w2 = np.asarray(wt2_3m_arr, dtype=float)
    if is_long:
        return w1 > w2
    return w1 < w2
