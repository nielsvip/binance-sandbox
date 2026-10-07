"""vec_paths/gr_filter_vec.py — Phase G 2026-05-19 USER MANDATE.

USER: "MTF_WT_1h ALWAYS has to pass the GR filter which is NOT the same thing
as the GR entry signal or the GR exit signal."

This is a vectorized port of golden_rule_htf.score_entry_htf — same 11 indicators
per TF, same MIN_TFS / MIN_IND thresholds, but computed once-per-(sym,side) and
returned as a bool array indexed by base-TF bar i.

11 INDICATORS per TF (each contributes 1 to per-TF score if bullish for LONG /
bearish for SHORT):
  1. WT      wt1 > wt2   (LONG) / <    (SHORT)
  2. RSI     rsi > 50    / < 50
  3. MFI     mfi > 50    / < 50
  4. DC      room mode: dc_pct < 0.65 / > 0.35       (default)
            breakout : dc_pct >= 0.65 / <= 0.35      (invert_dc_bb)
  5. BB      room mode: bb_pct_b < 0.75 / > 0.25
            breakout : bb_pct_b >= 0.75 / <= 0.25
  6. RVOL    relative_volume > 1.0  (counts both)
  7. K       stoch_k < 80 / > 20
  8. ADX     adx > 20    (counts both)
  9. MACD_H  macd_hist > 0 / < 0
 10. HA      ha_color > 0 / < 0
 11. K>D     stoch_k > stoch_d / <

Pass if confirmed_tfs >= min_tfs where each TF needs >= min_ind indicators.

Mode TFs:
  crypto : ['3m', '15m', '1h', '4h', 'D', 'W']
  tradier: ['5m', '15m', '1h', '4h', 'D', 'W']

KNOBS:
  MTF_GR_FILTER_ENABLED          # master (default True)
  MTF_GR_MIN_TFS                 # default reuses GOLDEN_RULE_HTF_MIN_TFS
  MTF_GR_MIN_IND                 # default reuses GOLDEN_RULE_MIN_IND
  MTF_GR_INVERT_DC_BB            # breakout mode (default False = room-to-run)
"""
from __future__ import annotations
import numpy as np

GR_FILTER_VEC_KNOBS = (
    "MTF_GR_FILTER_ENABLED",
    "MTF_GR_MIN_TFS",
    "MTF_GR_MIN_IND",
    "MTF_GR_INVERT_DC_BB",
)

_CRYPTO_TFS = ["3m", "15m", "1h", "4h", "D", "W"]
_TRADIER_TFS = ["5m", "15m", "1h", "4h", "D", "W"]
_DC_LONG, _DC_SHORT = 0.65, 0.35
_BB_LONG, _BB_SHORT = 0.75, 0.25


def _arr(npz, key, n):
    a = npz.get(key)
    if a is None:
        return np.zeros(n, dtype=np.float32)
    return np.nan_to_num(a, nan=0.0).astype(np.float32)


def _tf_score_vec(npz, tf, n, is_long, invert_dc_bb):
    """Return int8 array: per-bar count of bullish-for-side indicators on this TF."""
    score = np.zeros(n, dtype=np.int8)
    # 1. WT
    wt1 = _arr(npz, f"wt1_{tf}", n)
    wt2 = _arr(npz, f"wt2_{tf}", n)
    if is_long: score += ((wt1 != 0) & (wt2 != 0) & (wt1 > wt2)).astype(np.int8)
    else:        score += ((wt1 != 0) & (wt2 != 0) & (wt1 < wt2)).astype(np.int8)
    # 2. RSI
    rsi = _arr(npz, f"rsi_{tf}", n)
    if is_long: score += ((rsi > 0) & (rsi > 50)).astype(np.int8)
    else:        score += ((rsi > 0) & (rsi < 50)).astype(np.int8)
    # 3. MFI
    mfi = _arr(npz, f"mfi_{tf}", n)
    if is_long: score += ((mfi > 0) & (mfi > 50)).astype(np.int8)
    else:        score += ((mfi > 0) & (mfi < 50)).astype(np.int8)
    # 4. DC
    dc_pct = _arr(npz, f"dc_pct_{tf}", n)
    if dc_pct.max() <= 0:
        # 2026-06-03 USER "FIX GR": dc_pct_{tf} absent from NPZ → indicator #4 was DEAD. Compute
        # channel position from dc_high/dc_low/close (all present): (close-low)/(high-low).
        _dch = _arr(npz, f"dc_high_{tf}", n); _dcl = _arr(npz, f"dc_low_{tf}", n)
        _cl = _arr(npz, "close", n)
        _rng = _dch - _dcl
        dc_pct = np.where(_rng > 0, (_cl - _dcl) / np.where(_rng > 0, _rng, 1.0), 0.0).astype(np.float32)
    if dc_pct.max() > 0:
        if invert_dc_bb:
            if is_long: score += (dc_pct >= _DC_LONG).astype(np.int8)
            else:        score += (dc_pct <= _DC_SHORT).astype(np.int8)
        else:
            if is_long: score += (dc_pct < _DC_LONG).astype(np.int8)
            else:        score += (dc_pct > _DC_SHORT).astype(np.int8)
    # 5. BB
    bb_pct_b = _arr(npz, f"bb_pct_b_{tf}", n)
    if bb_pct_b.max() > 0:
        if invert_dc_bb:
            if is_long: score += (bb_pct_b >= _BB_LONG).astype(np.int8)
            else:        score += (bb_pct_b <= _BB_SHORT).astype(np.int8)
        else:
            if is_long: score += (bb_pct_b < _BB_LONG).astype(np.int8)
            else:        score += (bb_pct_b > _BB_SHORT).astype(np.int8)
    # 6. RVOL — counts for both LONG and SHORT
    rvol = _arr(npz, f"relative_volume_{tf}", n)
    score += ((rvol > 0) & (rvol > 1.0)).astype(np.int8)
    # 7. K
    k = _arr(npz, f"k_{tf}", n)
    if k.max() == 0:  # fallback name
        k = _arr(npz, f"stoch_k_{tf}", n)
    if is_long: score += ((k > 0) & (k < 80)).astype(np.int8)
    else:        score += ((k > 0) & (k > 20)).astype(np.int8)
    # 8. ADX — counts both
    adx = _arr(npz, f"adx_{tf}", n)
    score += ((adx > 0) & (adx > 20)).astype(np.int8)
    # 9. MACD_H
    macd_h = _arr(npz, f"macd_hist_{tf}", n)
    if is_long: score += (macd_h > 0).astype(np.int8)
    else:        score += (macd_h < 0).astype(np.int8)
    # 10. HA color
    ha = _arr(npz, f"ha_color_{tf}", n)
    if is_long: score += (ha > 0).astype(np.int8)
    else:        score += (ha < 0).astype(np.int8)
    # 11. K > D
    d = _arr(npz, f"d_{tf}", n)
    if d.max() == 0:
        d = _arr(npz, f"stoch_d_{tf}", n)
    if is_long: score += ((k > 0) & (d > 0) & (k > d)).astype(np.int8)
    else:        score += ((k > 0) & (d > 0) & (k < d)).astype(np.int8)
    return score


def build_gr_filter_mask(npz, n, is_long, mode, config):
    """Returns boolean array: True = GR filter passes at this bar (entry allowed)."""
    if not bool(getattr(config, "MTF_GR_FILTER_ENABLED", True)):
        return np.ones(n, dtype=bool)
    min_tfs = int(getattr(config, "MTF_GR_MIN_TFS",
                          getattr(config, "GOLDEN_RULE_HTF_MIN_TFS", 3)))
    min_ind = int(getattr(config, "MTF_GR_MIN_IND",
                          getattr(config, "GOLDEN_RULE_MIN_IND", 5)))
    invert = bool(getattr(config, "MTF_GR_INVERT_DC_BB", False))
    tfs = _CRYPTO_TFS if mode == "crypto" else _TRADIER_TFS
    tf_pass_count = np.zeros(n, dtype=np.int8)
    for tf in tfs:
        tf_score = _tf_score_vec(npz, tf, n, is_long, invert)
        tf_pass_count += (tf_score >= min_ind).astype(np.int8)
    mask = (tf_pass_count >= min_tfs)
    # Per-bar min_ind relaxation: price >5% beyond sma_200_15m → min_ind drops to 4.
    # Mirrors ez_manage GR_FILTER_ALL_ENTRIES ~22907-22912: proven-breakout bars use looser gate.
    min_ind_relaxed = min(min_ind, 4)
    if min_ind_relaxed < min_ind:
        sma200 = _arr(npz, "sma_200_15m", n)
        close = _arr(npz, "close", n)
        breakout = ((sma200 > 0) & (close > sma200 * 1.05)) if is_long else ((sma200 > 0) & (close < sma200 * 0.95))
        if breakout.any():
            tf_pass_relax = np.zeros(n, dtype=np.int8)
            for tf in tfs:
                tf_score = _tf_score_vec(npz, tf, n, is_long, invert)
                tf_pass_relax += (tf_score >= min_ind_relaxed).astype(np.int8)
            mask = np.where(breakout, tf_pass_relax >= min_tfs, mask)
    return mask
