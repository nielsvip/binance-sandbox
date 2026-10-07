"""check_exit_candidates_crypto__structural_range_shift — STRUCTURAL_RANGE_SHIFT exit
predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
13758-13799 (STRUCTURAL RANGE SHIFT EXIT — CASCADE, USER 2026-04-14):

  GATE (caller): not hard_exit_reason, not is_hedge, not _in_grace_period,
       STRUCTURAL_RANGE_SHIFT_EXIT (default False)
  TF -> (high_field, low_field) via STRUCTURAL_RANGE_SHIFT_TF (default 'dc_4h')
  Requires entry_price > 0 and srs_high > 0 and srs_low > 0.
  LONG  (entry > srs_high):
      prox   = abs(price - srs_high)/srs_high <= band  (band = PROXIMITY_BPS/10000, default 100bps)
      cas_1h = k1h >= K_HIGH(75) AND k1h < k1h_prev AND wt1_1h < wt2_1h
      cas_15m= k15 >= K_HIGH    AND k15 < k15_prev  AND wt1_15m < wt2_15m
      cas_3m = wt1_3m < wt2_3m
      fire = prox AND cas_1h AND cas_15m AND cas_3m
  SHORT (entry > srs_low):  symmetric with K_LOW(25), turning UP, wt bullish.

A profit-take/structure exit for positions that broke out of a HTF range and the range
re-shifts (cascade across 1h+15m+3m). The entry-vs-level and proximity tests are STATE
seams (need entry_price); all the K/WT cascade tests are pure indicator predicates.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): scalar +
vec share the pure core _srs_fires() — CANNOT drift. entry_price + the breakout-side
selection are passed in as state so the core stays pure.

NPZ / indicator fields read (TF-dependent high/low + fixed K/WT set):
  - dc_high_<TF>/dc_low_<TF> or bb_upper_<TF>/bb_lower_<TF>
  - stoch_k_1h / stoch_k_1h_prev, stoch_k_15m / stoch_k_15m_prev
  - wt1_1h/wt2_1h, wt1_15m/wt2_15m, wt1_3m/wt2_3m
  All present in NPZ (precompute writes dc_*, bb_*, stoch_*_prev, wt*).

Config:
  STRUCTURAL_RANGE_SHIFT_EXIT          (default False)
  STRUCTURAL_RANGE_SHIFT_TF            (default 'dc_4h')
  STRUCTURAL_RANGE_SHIFT_K_HIGH        (default 75.0)
  STRUCTURAL_RANGE_SHIFT_K_LOW         (default 25.0)
  STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS (default 100.0)
"""
from typing import Tuple
import numpy as np

_FIELD_MAP = {
    "dc_1h": ("dc_high_1h", "dc_low_1h"), "dc_4h": ("dc_high_4h", "dc_low_4h"),
    "dc_D": ("dc_high_D", "dc_low_D"), "bb_1h": ("bb_upper_1h", "bb_lower_1h"),
    "bb_4h": ("bb_upper_4h", "bb_lower_4h"), "bb_D": ("bb_upper_D", "bb_lower_D"),
}


def _srs_fires(entry_price, current_price, srs_high, srs_low, k1h, k1h_prev, k15, k15_prev,
               w1_1h, w2_1h, w1_15m, w2_15m, w1_3m, w2_3m, is_long,
               k_hi, k_lo, band) -> bool:
    """PURE per-bar fire test. Mirrors live 13783-13797 EXACTLY."""
    if not (entry_price > 0 and srs_high > 0 and srs_low > 0):
        return False
    if is_long:
        if not (entry_price > srs_high):
            return False
        prox = abs(current_price - srs_high) / srs_high <= band
        cas_1h = (k1h >= k_hi) and (k1h < k1h_prev) and (w1_1h < w2_1h)
        cas_15m = (k15 >= k_hi) and (k15 < k15_prev) and (w1_15m < w2_15m)
        cas_3m = w1_3m < w2_3m
        return prox and cas_1h and cas_15m and cas_3m
    if not (entry_price > srs_low):
        return False
    prox = abs(current_price - srs_low) / srs_low <= band
    cas_1h = (k1h <= k_lo) and (k1h > k1h_prev) and (w1_1h > w2_1h)
    cas_15m = (k15 <= k_lo) and (k15 > k15_prev) and (w1_15m > w2_15m)
    cas_3m = w1_3m > w2_3m
    return prox and cas_1h and cas_15m and cas_3m


def _srs_params(config):
    tf = getattr(config, "STRUCTURAL_RANGE_SHIFT_TF", "dc_4h")
    hi_key, lo_key = _FIELD_MAP.get(tf, ("dc_high_4h", "dc_low_4h"))
    return (tf, hi_key, lo_key,
            float(getattr(config, "STRUCTURAL_RANGE_SHIFT_K_HIGH", 75.0)),
            float(getattr(config, "STRUCTURAL_RANGE_SHIFT_K_LOW", 25.0)),
            float(getattr(config, "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", 100.0)) / 10000.0)


def check_structural_range_shift(config, indicators: dict, current_price: float,
                                 is_long: bool, entry_price: float) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason)."""
    if not getattr(config, "STRUCTURAL_RANGE_SHIFT_EXIT", False):
        return False, ""
    tf, hi_key, lo_key, k_hi, k_lo, band = _srs_params(config)
    g = lambda k, d=0.0: float(indicators.get(k, d) if indicators.get(k) is not None else d)
    srs_high = g(hi_key); srs_low = g(lo_key)
    k1h = g("stoch_k_1h", 50); k1h_prev = g("stoch_k_1h_prev", 50)
    k15 = g("stoch_k_15m", 50); k15_prev = g("stoch_k_15m_prev", 50)
    if not _srs_fires(entry_price, current_price, srs_high, srs_low, k1h, k1h_prev, k15, k15_prev,
                      g("wt1_1h"), g("wt2_1h"), g("wt1_15m"), g("wt2_15m"), g("wt1_3m"), g("wt2_3m"),
                      is_long, k_hi, k_lo, band):
        return False, ""
    side = "LONG" if is_long else "SHORT"
    return True, f"STRUCTURAL_RANGE_SHIFT_{side}_CASCADE_{tf}_entry={entry_price:.6f}_k1h={k1h:.0f}_k15m={k15:.0f}"


def check_structural_range_shift_vec(config, current_price_arr, srs_high_arr, srs_low_arr,
                                     k1h_arr, k1h_prev_arr, k15_arr, k15_prev_arr,
                                     w1_1h_a, w2_1h_a, w1_15m_a, w2_15m_a, w1_3m_a, w2_3m_a,
                                     is_long, entry_price_arr) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    srs_high_arr/srs_low_arr are the TF-selected NPZ arrays. entry_price_arr is the
    per-bar entry price (state seam)."""
    p = np.asarray(current_price_arr, dtype=float)
    n = len(p)
    if not getattr(config, "STRUCTURAL_RANGE_SHIFT_EXIT", False):
        return np.zeros(n, dtype=bool)
    _tf, _hk, _lk, k_hi, k_lo, band = _srs_params(config)
    ep = np.asarray(entry_price_arr, dtype=float)
    sh = np.asarray(srs_high_arr, dtype=float); sl = np.asarray(srs_low_arr, dtype=float)
    k1h = np.asarray(k1h_arr, dtype=float); k1hp = np.asarray(k1h_prev_arr, dtype=float)
    k15 = np.asarray(k15_arr, dtype=float); k15p = np.asarray(k15_prev_arr, dtype=float)
    a1 = np.asarray(w1_1h_a, dtype=float); b1 = np.asarray(w2_1h_a, dtype=float)
    a15 = np.asarray(w1_15m_a, dtype=float); b15 = np.asarray(w2_15m_a, dtype=float)
    a3 = np.asarray(w1_3m_a, dtype=float); b3 = np.asarray(w2_3m_a, dtype=float)
    valid = (ep > 0) & (sh > 0) & (sl > 0)
    if is_long:
        side_ok = ep > sh
        prox = np.abs(p - sh) / np.where(sh != 0, sh, 1) <= band
        cas_1h = (k1h >= k_hi) & (k1h < k1hp) & (a1 < b1)
        cas_15m = (k15 >= k_hi) & (k15 < k15p) & (a15 < b15)
        cas_3m = a3 < b3
    else:
        side_ok = ep > sl
        prox = np.abs(p - sl) / np.where(sl != 0, sl, 1) <= band
        cas_1h = (k1h <= k_lo) & (k1h > k1hp) & (a1 > b1)
        cas_15m = (k15 <= k_lo) & (k15 > k15p) & (a15 > b15)
        cas_3m = a3 > b3
    return valid & side_ok & prox & cas_1h & cas_15m & cas_3m
