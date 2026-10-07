"""STRUCTURAL_RANGE_SHIFT (RANGE_TOP_EXIT / RANGE_BOTTOM_EXIT) — retest-into-band exit (shared scalar+vec).

LIVE SOURCE: tradier_manage.py TradierStopEvaluator.evaluate_stop() lines ~6264-6300.
  Gated by STRUCTURAL_RANGE_SHIFT_EXIT (default False); tf selects band via _srs_field_map.
  band = STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS/10000 (default 100bps -> 0.01)
  k_high = STRUCTURAL_RANGE_SHIFT_K_HIGH (default 80), k_low = ..._K_LOW (default 20)
  LONG (only when entry_price > srs_high, i.e. entered above the range):
    prox   = abs(price - srs_high)/srs_high <= band
    fires when entry>srs_high>0 AND srs_low>0 AND prox AND k_1h>k_high AND k_15m>k_high AND k_5m<k_5m_prev
  SHORT (only when entry_price < srs_low):
    prox   = abs(price - srs_low)/srs_low <= band
    fires when entry<srs_low (srs_low>0) AND srs_high>0 AND prox AND k_1h<k_low AND k_15m<k_low AND k_5m>k_5m_prev
  Outer guard: entry>0 AND srs_high>0 AND srs_low>0.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_structural_range_shift) AND vec (check_structural_range_shift_vec) share the SAME
pure per-bar predicate _structural_range_shift_fires(), so the two paths CANNOT drift.

STATE SEAM: entry_price is per-position state (live position.entry_price). It is passed as
a per-bar array into the vec path (the engine knows the position entry) and as a scalar
into the wrapper — faithful to live. The band/k thresholds are passed in (no config in
the core). No gain/age gate (SRS is allowed to exit at a loss, runs above NOLOSS).

NPZ / indicator fields read:
  close (current_price), <band-high>, <band-low> (per tf, e.g. bb_upper_1h/bb_lower_1h),
  stoch_k_1h, stoch_k_15m, stoch_k_5m, stoch_k_5m_prev. entry_price = state.
"""
from typing import Tuple

_FIELD_MAP = {
    "dc_1h": ("dc_high_1h", "dc_low_1h"), "dc_4h": ("dc_high_4h", "dc_low_4h"),
    "dc_D": ("dc_high_D", "dc_low_D"), "bb_1h": ("bb_upper_1h", "bb_lower_1h"),
    "bb_4h": ("bb_upper_4h", "bb_lower_4h"), "bb_D": ("bb_upper_D", "bb_lower_D"),
}


def _structural_range_shift_fires(entry_price: float, current_price: float, srs_high: float,
                                  srs_low: float, k_1h: float, k_15m: float, k_5m: float,
                                  k_5m_prev: float, band: float, k_high: float, k_low: float,
                                  is_long: bool) -> bool:
    """PURE per-bar condition. Faithful replica of tradier_manage.py lines 6284-6298."""
    if not (entry_price > 0 and srs_high > 0 and srs_low > 0):
        return False
    if is_long:
        if not (entry_price > srs_high):
            return False
        prox = abs(current_price - srs_high) / srs_high <= band
        return prox and k_1h > k_high and k_15m > k_high and k_5m < k_5m_prev
    if not (entry_price < srs_low):
        return False
    prox = abs(current_price - srs_low) / srs_low <= band
    return prox and k_1h < k_low and k_15m < k_low and k_5m > k_5m_prev


def _srs_params(config):
    tf = getattr(config, "STRUCTURAL_RANGE_SHIFT_TF", "bb_1h")
    band = float(getattr(config, "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", 100.0)) / 10000.0
    k_high = float(getattr(config, "STRUCTURAL_RANGE_SHIFT_K_HIGH", 80.0))
    k_low = float(getattr(config, "STRUCTURAL_RANGE_SHIFT_K_LOW", 20.0))
    return tf, band, k_high, k_low


def check_structural_range_shift(config, indicators: dict, entry_price: float,
                                 current_price: float, is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (should_exit, reason). Fire from the shared predicate.
    Live .get defaults mirror tradier_manage.py lines 6274-6283 (bands=0, k=50)."""
    if not bool(getattr(config, "STRUCTURAL_RANGE_SHIFT_EXIT", False)):
        return False, ""
    tf, band, k_high, k_low = _srs_params(config)
    hk, lk = _FIELD_MAP.get(tf, ("bb_upper_1h", "bb_lower_1h"))
    srs_high = float(indicators.get(hk, 0) or 0)
    srs_low = float(indicators.get(lk, 0) or 0)
    k_1h = float(indicators.get("stoch_k_1h", 50) or 50)
    k_15m = float(indicators.get("stoch_k_15m", 50) or 50)
    k_5m = float(indicators.get("stoch_k_5m", 50) or 50)
    k_5m_prev = float(indicators.get("stoch_k_5m_prev", 50) or 50)
    if not _structural_range_shift_fires(entry_price, current_price, srs_high, srs_low, k_1h,
                                         k_15m, k_5m, k_5m_prev, band, k_high, k_low, is_long):
        return False, ""
    if is_long:
        return True, f"STRUCTURAL_RANGE_SHIFT_LONG_{tf}_top={srs_high:.4f}_k1h={k_1h:.0f}_k15m={k_15m:.0f}_k5m={k_5m:.0f}"
    return True, f"STRUCTURAL_RANGE_SHIFT_SHORT_{tf}_bot={srs_low:.4f}_k1h={k_1h:.0f}_k15m={k_15m:.0f}_k5m={k_5m:.0f}"


def check_structural_range_shift_vec(config, entry_price_arr, current_price_arr, srs_high_arr,
                                     srs_low_arr, k_1h_arr, k_15m_arr, k_5m_arr, k_5m_prev_arr,
                                     is_long):
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate + SAME thresholds as the
    live scalar. entry_price is the per-position state seam (per-bar array). Returns a bool
    ndarray. Arrays per-bar (NPZ + state): entry_price, close, <band-high>, <band-low>,
    stoch_k_1h, stoch_k_15m, stoch_k_5m, stoch_k_5m_prev."""
    import numpy as np
    e = np.asarray(entry_price_arr, dtype=float)
    if not bool(getattr(config, "STRUCTURAL_RANGE_SHIFT_EXIT", False)):
        return np.zeros(len(e), dtype=bool)
    _tf, band, k_high, k_low = _srs_params(config)
    p = np.asarray(current_price_arr, dtype=float)
    hi = np.asarray(srs_high_arr, dtype=float)
    lo = np.asarray(srs_low_arr, dtype=float)
    k1 = np.asarray(k_1h_arr, dtype=float)
    k15 = np.asarray(k_15m_arr, dtype=float)
    k5 = np.asarray(k_5m_arr, dtype=float)
    k5p = np.asarray(k_5m_prev_arr, dtype=float)
    guard = (e > 0) & (hi > 0) & (lo > 0)
    if is_long:
        prox = np.abs(p - hi) / np.where(hi == 0, 1.0, hi) <= band
        return guard & (e > hi) & prox & (k1 > k_high) & (k15 > k_high) & (k5 < k5p)
    prox = np.abs(p - lo) / np.where(lo == 0, 1.0, lo) <= band
    return guard & (e < lo) & prox & (k1 < k_low) & (k15 < k_low) & (k5 > k5p)
