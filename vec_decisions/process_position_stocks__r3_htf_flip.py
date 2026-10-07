"""PROCESS_POSITION_STOCKS · R3_HTF_FLIP — Daily + 4h structural HTF-flip exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position lines ~2228-2262 (R3_HTF_FLIP block).
  DAILY tier (fires if dc_basis_D > 0):
    LONG  : (price < dc_basis_D) OR (wt1_D < wt2_D AND wt1_W < wt2_W)
    SHORT : (price > dc_basis_D) OR (wt1_D > wt2_D AND wt1_W > wt2_W)
  4H tier (only if DAILY did not fire, and R3_HTF_FLIP_4H_TIER_ENABLED, and ema_20_4h>0 AND atr_4h>0):
    LONG  : price < (ema_20_4h - atr_4h) AND wt1_4h < wt2_4h
    SHORT : price > (ema_20_4h + atr_4h) AND wt1_4h > wt2_4h
  ANY tier fires → CLOSE. Reason tag R3_HTF_FLIP (DAILY) / R3_HTF_FLIP_4H (4H).

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): the live
scalar (check_r3_htf_flip) AND the vectorized backtest (check_r3_htf_flip_vec) BOTH
derive their fire decision from the SAME pure per-bar predicate _r3_htf_flip_fires()
below, so the two paths CANNOT drift. Pure core: no config, no state. Data source
differs (live market_data indicators vs NPZ arrays) but the predicate is identical.

CLASSIFICATION: vectorized. Pure per-bar predicate on NPZ-available indicator fields
(dc_basis_D, wt1/2_D, wt1/2_W, ema_20_4h, atr_4h, wt1/2_4h, close). No per-position
state. The position-held / enabled gates are layered by the caller (state seam).

NPZ / indicator fields read by the core (via the wrappers):
  - dc_basis_D, wt1_D, wt2_D, wt1_W, wt2_W   (Daily tier)
  - ema_20_4h, atr_4h, wt1_4h, wt2_4h        (4H tier)
  - close (= current_price)
All confirmed present in backtest NPZ (wt1/2_{D,W,4h}, dc_basis_D, ema_20_4h, atr_4h).

NOTE on data-guards (faithful to live): the live code only evaluates the DAILY tier
when dc_basis_D > 0, and the 4H tier only when ema_20_4h>0 AND atr_4h>0. The pure
core replicates these guards exactly (missing/zero data → that tier cannot fire).
"""
from typing import Tuple


def _r3_htf_flip_fires(current_price: float, dc_basis_D: float, wt1_D: float, wt2_D: float,
                       wt1_W: float, wt2_W: float, ema_20_4h: float, atr_4h: float,
                       wt1_4h: float, wt2_4h: float, is_long: bool,
                       enable_4h_tier: bool) -> Tuple[bool, str]:
    """PURE per-bar fire condition for R3_HTF_FLIP. Returns (fires, tier) where tier is
    'DAILY' / '4H' / ''. Faithful replica of tradier_manage.py lines 2242-2256."""
    if dc_basis_D > 0:
        if is_long:
            dc_break = current_price < dc_basis_D
            wt_flip = wt1_D < wt2_D and wt1_W < wt2_W
        else:
            dc_break = current_price > dc_basis_D
            wt_flip = wt1_D > wt2_D and wt1_W > wt2_W
        if dc_break or wt_flip:
            return True, "DAILY"
    if enable_4h_tier and ema_20_4h > 0 and atr_4h > 0:
        if is_long:
            if current_price < (ema_20_4h - atr_4h) and wt1_4h < wt2_4h:
                return True, "4H"
        else:
            if current_price > (ema_20_4h + atr_4h) and wt1_4h > wt2_4h:
                return True, "4H"
    return False, ""


def _r3_htf_flip_flags(config):
    return (bool(getattr(config, "R3_HTF_FLIP_EXIT_ENABLED", False)),
            bool(getattr(config, "R3_HTF_FLIP_4H_TIER_ENABLED", False)))


def check_r3_htf_flip(config, indicators: dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason).
    Fire decision comes from the shared _r3_htf_flip_fires() predicate. Live default
    reads mirror the safe_fetch_float(..., 0) defaults in tradier_manage.py."""
    enabled, enable_4h = _r3_htf_flip_flags(config)
    if not enabled:
        return False, ""
    dc_basis_D = float(indicators.get("dc_basis_D", 0) or 0)
    wt1_D = float(indicators.get("wt1_D", 0) or 0)
    wt2_D = float(indicators.get("wt2_D", 0) or 0)
    wt1_W = float(indicators.get("wt1_W", 0) or 0)
    wt2_W = float(indicators.get("wt2_W", 0) or 0)
    ema_20_4h = float(indicators.get("ema_20_4h", 0) or 0)
    atr_4h = float(indicators.get("atr_4h", 0) or 0)
    wt1_4h = float(indicators.get("wt1_4h", 0) or 0)
    wt2_4h = float(indicators.get("wt2_4h", 0) or 0)
    fires, tier = _r3_htf_flip_fires(current_price, dc_basis_D, wt1_D, wt2_D, wt1_W, wt2_W,
                                     ema_20_4h, atr_4h, wt1_4h, wt2_4h, is_long, enable_4h)
    if not fires:
        return False, ""
    tag = "R3_HTF_FLIP" if tier == "DAILY" else "R3_HTF_FLIP_4H"
    return True, f"{tag}_{tier}"


def check_r3_htf_flip_vec(config, current_price_arr, dc_basis_D_arr, wt1_D_arr, wt2_D_arr,
                          wt1_W_arr, wt2_W_arr, ema_20_4h_arr, atr_4h_arr, wt1_4h_arr,
                          wt2_4h_arr, is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME flags + SAME predicate as the
    live scalar check_r3_htf_flip (vectorized via numpy). Returns a bool ndarray (fires).
    Caller layers position-held gate on top (state seam)."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    enabled, enable_4h = _r3_htf_flip_flags(config)
    if not enabled:
        return np.zeros(len(p), dtype=bool)
    dcD = np.asarray(dc_basis_D_arr, dtype=float)
    w1D = np.asarray(wt1_D_arr, dtype=float)
    w2D = np.asarray(wt2_D_arr, dtype=float)
    w1W = np.asarray(wt1_W_arr, dtype=float)
    w2W = np.asarray(wt2_W_arr, dtype=float)
    e4 = np.asarray(ema_20_4h_arr, dtype=float)
    a4 = np.asarray(atr_4h_arr, dtype=float)
    w14 = np.asarray(wt1_4h_arr, dtype=float)
    w24 = np.asarray(wt2_4h_arr, dtype=float)
    daily_valid = dcD > 0
    if is_long:
        daily = daily_valid & ((p < dcD) | ((w1D < w2D) & (w1W < w2W)))
    else:
        daily = daily_valid & ((p > dcD) | ((w1D > w2D) & (w1W > w2W)))
    fires = daily.copy()
    if enable_4h:
        h4_valid = (e4 > 0) & (a4 > 0)
        if is_long:
            h4 = h4_valid & (p < (e4 - a4)) & (w14 < w24)
        else:
            h4 = h4_valid & (p > (e4 + a4)) & (w14 > w24)
        fires = fires | (h4 & ~daily)
    return fires
