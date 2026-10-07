"""SHARED scalar+vectorized predicate for the LIVE decision MOMENTUM_TP.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~43751-43814.

"MANDATORY PROFIT TAKE (MOMENTUM EXHAUSTION)". Gated by
`current_gain > 0.5 and not _recently_reduced` (state seam). Then an
if/elif cascade sets tp_reason; fires REDUCE iff tp_reason is non-empty.

Replicated EXACTLY (order matters — elif short-circuits):

LONG:
  A PROFIT_TP_EXHAUSTION : k_15m>95 AND k_15m<k_15m_prev
  B OB_LOWER_LOW         : k_15m>95 AND low_3m>0 AND low_3m_prev>0 AND low_3m<low_3m_prev
  C K3M_BOUNCE_TURN_L    : k_3m>60 AND k_3m<k_3m_prev AND (k_15m>95 OR k_1h>85)
                           AND low_3m>0 AND low_3m_prev>0 AND low_3m<low_3m_prev
  D HARD_DROP_BELOW_PREV_LOW : low_3m_prev>0 AND current_price<low_3m_prev
SHORT:
  A PROFIT_TP_EXHAUSTION : k_15m<5 AND k_15m>k_15m_prev
  E OS_HIGHER_LOW        : k_15m<5 AND low_3m>0 AND low_3m_prev>0 AND low_3m>low_3m_prev
  F K3M_BOUNCE_TURN_S    : k_3m<40 AND k_3m>k_3m_prev AND (k_15m<5 OR k_1h<15)
                           AND high_3m>0 AND high_3m_prev>0 AND high_3m>high_3m_prev
  G HARD_RISE_ABOVE_PREV_HIGH : high_3m_prev>0 AND current_price>high_3m_prev

The cascade is mutually-exclusive-by-elif but the FIRE decision (any branch
True) equals the OR of all branches, evaluated independently — that is what the
vec path computes. (The chosen tp_reason label is only needed for the scalar log
string; the vec path only needs the boolean fire mask.)

CLASSIFICATION: stateful_seam. Pure per-bar over NPZ fields (stoch_k_3m/15m/1h
+ _prev, low_3m/high_3m + _prev, price) PLUS the per-position seam values
`current_gain` (the >0.5 entry gate) and `_recently_reduced` (state flag). The
core below returns the (gated) fire decision; caller supplies gain &
recently_reduced exactly like live.
"""
from typing import Tuple
import numpy as np


def _momentum_tp_signal(is_long: bool, price: float,
                        k_3m: float, k_3m_prev: float,
                        k_15m: float, k_15m_prev: float, k_1h: float,
                        low_3m: float, low_3m_prev: float,
                        high_3m: float, high_3m_prev: float) -> bool:
    """Pure per-bar momentum-exhaustion signal (the tp_reason cascade), IGNORING
    the gain>0.5 / not-recently-reduced state gate. True iff any branch fires."""
    if is_long:
        if k_15m > 95 and k_15m < k_15m_prev:
            return True
        if k_15m > 95 and low_3m > 0 and low_3m_prev > 0 and low_3m < low_3m_prev:
            return True
        if (k_3m > 60 and k_3m < k_3m_prev and (k_15m > 95 or k_1h > 85)
                and low_3m > 0 and low_3m_prev > 0 and low_3m < low_3m_prev):
            return True
        if low_3m_prev > 0 and price < low_3m_prev:
            return True
        return False
    else:
        if k_15m < 5 and k_15m > k_15m_prev:
            return True
        if k_15m < 5 and low_3m > 0 and low_3m_prev > 0 and low_3m > low_3m_prev:
            return True
        if (k_3m < 40 and k_3m > k_3m_prev and (k_15m < 5 or k_1h < 15)
                and high_3m > 0 and high_3m_prev > 0 and high_3m > high_3m_prev):
            return True
        if high_3m_prev > 0 and price > high_3m_prev:
            return True
        return False


def _momentum_tp_fires(is_long: bool, price: float, gain: float, recently_reduced: bool,
                       k_3m: float, k_3m_prev: float, k_15m: float, k_15m_prev: float,
                       k_1h: float, low_3m: float, low_3m_prev: float,
                       high_3m: float, high_3m_prev: float) -> bool:
    """Full live predicate incl. state gate (gain>0.5 AND not recently_reduced)."""
    if not (gain > 0.5 and not recently_reduced):
        return False
    return _momentum_tp_signal(is_long, price, k_3m, k_3m_prev, k_15m, k_15m_prev,
                               k_1h, low_3m, low_3m_prev, high_3m, high_3m_prev)


def check_momentum_tp(config, indicators: dict, is_long: bool, price: float,
                      gain: float, recently_reduced: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. gain & recently_reduced from per-position state."""
    g = indicators or {}
    fired = _momentum_tp_fires(
        is_long, price, gain, recently_reduced,
        float(g.get("stoch_k_3m", 50) or 50), float(g.get("stoch_k_3m_prev", 50) or 50),
        float(g.get("stoch_k_15m", 50) or 50), float(g.get("stoch_k_15m_prev", 50) or 50),
        float(g.get("stoch_k_1h", 50) or 50),
        float(g.get("low_3m", 0) or 0), float(g.get("low_3m_prev", 0) or 0),
        float(g.get("high_3m", 0) or 0), float(g.get("high_3m_prev", 0) or 0),
    )
    if not fired:
        return False, ""
    return True, f"MOMENTUM_TP_g{gain:.2f}%"


def check_momentum_tp_vec(config, price_arr, gain_arr, recently_reduced_arr,
                          k_3m_arr, k_3m_prev_arr, k_15m_arr, k_15m_prev_arr, k_1h_arr,
                          low_3m_arr, low_3m_prev_arr, high_3m_arr, high_3m_prev_arr, is_long):
    """VECTORIZED per-bar fire mask. SAME cascade-as-OR logic + state gate."""
    p = np.asarray(price_arr, dtype=float)
    g = np.asarray(gain_arr, dtype=float)
    rr = np.asarray(recently_reduced_arr, dtype=bool)
    k3 = np.asarray(k_3m_arr, dtype=float)
    k3p = np.asarray(k_3m_prev_arr, dtype=float)
    k15 = np.asarray(k_15m_arr, dtype=float)
    k15p = np.asarray(k_15m_prev_arr, dtype=float)
    k1h = np.asarray(k_1h_arr, dtype=float)
    lo = np.asarray(low_3m_arr, dtype=float)
    lop = np.asarray(low_3m_prev_arr, dtype=float)
    hi = np.asarray(high_3m_arr, dtype=float)
    hip = np.asarray(high_3m_prev_arr, dtype=float)
    gate = (g > 0.5) & (~rr)
    if is_long:
        a = (k15 > 95) & (k15 < k15p)
        b = (k15 > 95) & (lo > 0) & (lop > 0) & (lo < lop)
        c = (k3 > 60) & (k3 < k3p) & ((k15 > 95) | (k1h > 85)) & (lo > 0) & (lop > 0) & (lo < lop)
        d = (lop > 0) & (p < lop)
        sig = a | b | c | d
    else:
        a = (k15 < 5) & (k15 > k15p)
        e = (k15 < 5) & (lo > 0) & (lop > 0) & (lo > lop)
        f = (k3 < 40) & (k3 > k3p) & ((k15 < 5) | (k1h < 15)) & (hi > 0) & (hip > 0) & (hi > hip)
        h = (hip > 0) & (p > hip)
        sig = a | e | f | h
    return gate & sig
