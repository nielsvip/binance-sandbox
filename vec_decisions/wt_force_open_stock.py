"""Vectorized Tradier WT force-open signal predicate.

This mirrors the indicator gates at tradier_manage.process_position's
WT_3M_FORCE_OPEN producer. Position room, tradeability, cooldown/day counters,
size and OPEN/AUGMENT selection are stateful caller responsibilities.
"""
from __future__ import annotations

import numpy as np


def _array(npz, key, n, default=0.0):
    value = npz.get(key)
    if value is None:
        return np.full(n, default, dtype=np.float64)
    raw = np.asarray(value, dtype=object)
    if raw.shape != (n,):
        raise ValueError(f"{key}: expected {n} aligned stock bars")
    out = np.full(n, default, dtype=np.float64)
    for index, item in enumerate(raw):
        try:
            out[index] = default if item is None else float(item)
        except (TypeError, ValueError):
            out[index] = default
    return out


def _wt(npz, key, fallback, n):
    # Native dict.get(key, fallback) selects fallback only when key is absent.
    return _array(npz, key if key in npz else fallback, n)


def _fresh_rising(npz, key, n):
    raw = np.asarray(npz.get(key, np.full(n, None, dtype=object)), dtype=object)
    if raw.shape != (n,):
        raise ValueError(f"{key}: expected {n} aligned stock bars")
    return np.fromiter((item is True or isinstance(item, np.bool_) and bool(item) for item in raw), dtype=bool, count=n)


def _gr_pass(npz, close, n, is_long, vote_min, min_tfs, min_ind):
    from vec_decisions.golden_loop import _tf_ind_matrix

    tfs = ("5m", "15m", "1h", "4h", "D")
    scores = np.asarray(
        [_tf_ind_matrix(npz, tf, n, is_long, close, False, 0.0, 0.0) for tf in tfs]
    )
    passed = np.ones(n, dtype=bool)
    if vote_min > 0:
        passed &= scores.sum(axis=0) >= vote_min
    if min_tfs > 0:
        passed &= (scores >= min_ind).sum(axis=0) >= min_tfs
    return passed


def force_open_mask(npz, n, is_long, cfg, close):
    """Return the native Tradier force-open indicator mask, before stateful limits."""
    if str(getattr(cfg, "MODE", "crypto")).lower() != "tradier":
        return np.zeros(n, dtype=bool)
    if not bool(getattr(cfg, "WT_3M_FORCE_OPEN_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    price = np.asarray(close, dtype=np.float64)
    if price.shape != (n,):
        raise ValueError("close: expected aligned stock bars")
    anchor = _array(npz, "ema_200_15m", n)
    anchor = np.where(anchor > 0, anchor, _array(npz, "sma_200_15m", n))
    buf = float(getattr(cfg, "WT_3M_FORCE_OPEN_SMA_PCT", getattr(cfg, "WT_3M_FORCE_OPEN_DIST_PCT", 1.0))) / 100.0
    above = (price > anchor * (1.0 + buf)) if is_long else (price < anchor * (1.0 - buf))
    above &= anchor > 0

    wt1 = _wt(npz, "wt1_5m", "wt1_3m", n)
    wt2 = _wt(npz, "wt2_5m", "wt2_3m", n)
    favor = (wt1 > wt2) if is_long else (wt1 < wt2)
    if bool(getattr(cfg, "WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS", True)):
        cross = _array(npz, "wt_crossover_value_5m", n)
        prev = _array(npz, "wt_crossover_value_5m_prev", n)
        hh_ok = (cross > 0) & (prev > 0) & ((cross > prev) if is_long else (cross < prev))
        favor &= hh_ok | (cross == 0)

    if bool(getattr(cfg, "WT_FORCE_OPEN_FRESH_CROSS_ONLY", False)):
        max_bars = int(getattr(cfg, "WT_FORCE_OPEN_FRESH_MAX_BARS", 0))
        if max_bars <= 0:
            fresh = _array(npz, f"wt_cross_{'bull' if is_long else 'bear'}_5m", n) >= 1
        else:
            bars_ago = _array(npz, "wt_cross_bars_ago_5m", n, 999.0)
            rising = _fresh_rising(npz, "wt_cross_rising_5m", n)
            fresh = (bars_ago <= max_bars) & (rising if is_long else ~rising)
        favor &= fresh

    gate_on = bool(getattr(cfg, "WT_3M_FORCE_OPEN_GR_GATE_ENABLED", True))
    if not bool(getattr(cfg, "FORCE_OPEN_REQUIRE_MTF_GR", True)):
        gate_on = False
    vote_min = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_VOTE_MIN", 15))
    min_tfs = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_MIN_TFS", 0))
    min_ind = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF", 5))
    gr_ok = np.ones(n, dtype=bool)
    if gate_on and (vote_min > 0 or min_tfs > 0):
        gr_ok = _gr_pass(npz, price, n, is_long, vote_min, min_tfs, min_ind)

    trend_ok = np.ones(n, dtype=bool)
    if not is_long:
        rsi = _array(npz, "rsi_5m", n, 50.0)
        dc = _array(npz, "d_5m", n, 50.0)
        momentum = wt1 - wt2
        trend_ok &= ~(((dc > 80) & (rsi > 70) & (price > anchor * 1.02)) | ((price > anchor * 1.03) & (momentum > 0)))
    return above & favor & gr_ok & trend_ok & (price > 0)
