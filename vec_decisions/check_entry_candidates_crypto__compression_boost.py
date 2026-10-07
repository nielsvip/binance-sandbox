"""check_entry_candidates_crypto__compression_boost.py

SHARED scalar+vectorized predicate for the COMPRESSION_BOOST branch in
check_entry_candidates_for_account (ez_positions_quick.py:15335-15348,
BACKTEST_CHANGE_150).

This branch is a SCORE BONUS (not a standalone open): when >=2 of 3 HTF ATR ranks
(1h/4h/D) are compressed (< 0.15) AND 15m WT is aligned with the side AND the
already-computed rate() score >= 10, it adds `10 + n_compressed*3` to the score.

FAITHFUL EXTRACTION of ez_positions_quick.py:15336-15348:

    _cb_atr_1h = ind.get('bar_atr_rank_1h', 0.5)
    _cb_atr_4h = ind.get('bar_atr_rank_4h', 0.5)
    _cb_atr_D  = ind.get('bar_atr_rank_D',  0.5)
    _cb_compressed = (atr_1h<0.15) + (atr_4h<0.15) + (atr_D<0.15)
    if _cb_compressed >= 2:
        _cb_wt1_15m = ind.get('wt1_15m', 0)
        _cb_wt2_15m = ind.get('wt2_15m', 0)
        _cb_wt_aligned = (is_long and wt1_15m>wt2_15m) or (short and wt1_15m<wt2_15m)
        if _cb_wt_aligned and score >= 10:
            score += 10 + _cb_compressed*3        # the boost FIRES

PURITY: pure per-bar predicate on NPZ fields (bar_atr_rank_1h/4h/D, wt1_15m,
wt2_15m) plus the incoming rate() `score` (a per-bar scalar the engine already has).
The boost AMOUNT (10 + n*3) is deterministic from n_compressed, so the core returns
both the fire-bool and the delta. The two paths share the same core so cannot drift
(mirrors strategy_enhancements.py _pyramid_fires pattern).
"""
from typing import Tuple
import numpy as np

_ATR_COMPRESS_THR = 0.15
_SCORE_GATE = 10.0


def _n_compressed(atr_1h: float, atr_4h: float, atr_D: float) -> int:
    """Exact replica: count of HTF ATR ranks below 0.15."""
    return int(atr_1h < _ATR_COMPRESS_THR) + int(atr_4h < _ATR_COMPRESS_THR) + int(atr_D < _ATR_COMPRESS_THR)


def _compression_boost_fires(atr_1h, atr_4h, atr_D, wt1_15m, wt2_15m, score,
                             is_long) -> Tuple[bool, float]:
    """PURE per-bar test. Returns (fires, boost_delta). Mirrors
    ez_positions_quick.py:15339-15346."""
    nc = _n_compressed(atr_1h, atr_4h, atr_D)
    if nc < 2:
        return False, 0.0
    wt_aligned = (is_long and wt1_15m > wt2_15m) or ((not is_long) and wt1_15m < wt2_15m)
    if wt_aligned and score >= _SCORE_GATE:
        return True, float(10 + nc * 3)
    return False, 0.0


def check_compression_boost(config, indicators: dict, score: float,
                            is_long: bool) -> Tuple[bool, float, str]:
    """LIVE/scalar path. Returns (fires, boost_delta, reason)."""
    def g(k, d):
        v = indicators.get(k, d)
        try:
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d
    atr_1h = g("bar_atr_rank_1h", 0.5)
    atr_4h = g("bar_atr_rank_4h", 0.5)
    atr_D = g("bar_atr_rank_D", 0.5)
    wt1_15m = g("wt1_15m", 0.0)
    wt2_15m = g("wt2_15m", 0.0)
    fires, delta = _compression_boost_fires(atr_1h, atr_4h, atr_D, wt1_15m,
                                            wt2_15m, float(score), is_long)
    if not fires:
        return False, 0.0, ""
    nc = _n_compressed(atr_1h, atr_4h, atr_D)
    reason = f"COMPRESSION_BOOST_{nc}TF_atr1h={atr_1h:.2f}_4h={atr_4h:.2f}_D={atr_D:.2f}_wt15={wt1_15m:.0f}/{wt2_15m:.0f}"
    return True, delta, reason


def check_compression_boost_vec(config, atr_1h_arr, atr_4h_arr, atr_D_arr,
                                wt1_15m_arr, wt2_15m_arr, score_arr, is_long):
    """VECTORIZED per-bar mask + boost-delta array. SAME thresholds + SAME math as
    scalar check_compression_boost. Returns (fire_mask, boost_delta_arr)."""
    a1 = np.asarray(atr_1h_arr, dtype=float)
    a4 = np.asarray(atr_4h_arr, dtype=float)
    aD = np.asarray(atr_D_arr, dtype=float)
    w1 = np.asarray(wt1_15m_arr, dtype=float)
    w2 = np.asarray(wt2_15m_arr, dtype=float)
    sc = np.asarray(score_arr, dtype=float)
    nc = (a1 < _ATR_COMPRESS_THR).astype(int) + (a4 < _ATR_COMPRESS_THR).astype(int) + (aD < _ATR_COMPRESS_THR).astype(int)
    if is_long:
        wt_aligned = w1 > w2
    else:
        wt_aligned = w1 < w2
    fires = (nc >= 2) & wt_aligned & (sc >= _SCORE_GATE)
    delta = np.where(fires, (10 + nc * 3).astype(float), 0.0)
    return fires, delta
