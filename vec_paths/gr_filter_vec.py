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
    from vec_decisions.mtf_gr_filter import _a
    return _a(npz, key, n)


def _price(npz, n):
    current = _arr(npz, "current_price", n)
    return np.where(current != 0, current, _arr(npz, "close", n))


def _tf_score_vec(npz, tf, n, is_long, invert_dc_bb):
    from vec_decisions.mtf_gr_filter import tf_score
    return tf_score(npz, n, tf, is_long, invert_dc_bb, _price(npz, n))


def build_gr_filter_mask(npz, n, is_long, mode, config):
    if not bool(getattr(config, "MTF_GR_FILTER_ENABLED", True)):
        return np.ones(n, dtype=bool)
    from vec_decisions.asr_input_adapter import paired_npz_view
    min_tfs = int(getattr(config, "MTF_GR_MIN_TFS", getattr(config, "GOLDEN_RULE_HTF_MIN_TFS", 3)))
    raw_min_ind = getattr(config, "MTF_GR_MIN_IND", 7)
    min_ind = int(raw_min_ind if mode == "crypto" else (raw_min_ind or 7))
    invert = bool(getattr(config, "MTF_GR_INVERT_DC_BB", False))
    use_ltf = bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED" if mode == "crypto" else "LTF_SUB15M_TRADING_ENABLED", False))
    npz = paired_npz_view(npz, use_ltf)
    tfs = _CRYPTO_TFS if mode == "crypto" else _TRADIER_TFS
    sma200, close = _arr(npz, "sma_200_15m", n), _price(npz, n)
    breakout = (sma200 > 0) & ((close > sma200 * 1.05) if is_long else (close < sma200 * 0.95))
    threshold = np.where(breakout, min(min_ind, 4), min_ind)
    confirmed = np.zeros(n, dtype=np.int16)
    for tf in tfs:
        confirmed += (_tf_score_vec(npz, tf, n, is_long, invert) >= threshold).astype(np.int16)
    return confirmed >= min_tfs
