"""mtf_gr_filter — vector twin of mtf_live_evaluator.gr_filter_pass (crypto) used by the MOMENTUM_WATCHDOG LONG force-open gate (FORCE_OPEN_REQUIRE_MTF_GR).
Live (_tf_score, mtf_live_evaluator.py:251-293): per TF count of bullish(LONG)/bearish(SHORT) indicators among wt1/wt2, rsi, mfi, dc_pct (from dc_high/dc_low/close),
bb_pct_b, relative_volume>1, k, adx>20, macd_hist, ha_color, k vs d; TF 'confirmed' when score >= MTF_GR_MIN_IND; gate passes when confirmed TFs >= MTF_GR_MIN_TFS.
Crypto TF set live = 3m,15m,1h,4h,D,W. NO 3m data in the vector system (user order): the 3m term is 'not evaluable -> passes' (counted as a confirmed TF).
KNOWN PARITY GAP: live may block opens that the vector allows when the 3m TF would NOT have been confirmed (needs >= MIN_TFS-1 of the 5 HTF TFs here instead of MIN_TFS)."""
from __future__ import annotations

import numpy as np

_TFS = ("15m", "1h", "4h", "D", "W")


def _a(npz, key, n):
    try:
        v = np.asarray(npz[key], dtype=np.float64)
    except Exception:
        return np.zeros(n)
    if v.shape[0] != n:
        out = np.zeros(n)
        m = min(n, v.shape[0])
        out[:m] = v[:m]
        v = out
    return np.nan_to_num(v, nan=0.0)


def tf_score(npz, n, tf, is_long, invert, close):
    s = np.zeros(n, dtype=np.int16)
    w1, w2 = _a(npz, f"wt1_{tf}", n), _a(npz, f"wt2_{tf}", n)
    nz = (w1 != 0) | (w2 != 0)
    s += (nz & ((w1 > w2) if is_long else (w1 < w2))).astype(np.int16)
    rsi = _a(npz, f"rsi_{tf}", n)
    s += ((rsi > 0) & ((rsi > 50) if is_long else (rsi < 50))).astype(np.int16)
    mfi = _a(npz, f"mfi_{tf}", n)
    s += ((mfi > 0) & ((mfi > 50) if is_long else (mfi < 50))).astype(np.int16)
    dch, dcl = _a(npz, f"dc_high_{tf}", n), _a(npz, f"dc_low_{tf}", n)
    rng = dch - dcl
    dcp = np.where((rng > 0) & (close > 0), (close - dcl) / np.where(rng > 0, rng, 1.0), 0.0)
    if invert:
        c = (dcp >= 0.65) if is_long else (dcp <= 0.35)
    else:
        c = (dcp < 0.65) if is_long else (dcp > 0.35)
    s += ((dcp > 0) & c).astype(np.int16)
    bb = _a(npz, f"bb_pct_b_{tf}", n)
    if invert:
        c = (bb >= 0.75) if is_long else (bb <= 0.25)
    else:
        c = (bb < 0.75) if is_long else (bb > 0.25)
    s += ((bb > 0) & c).astype(np.int16)
    s += (_a(npz, f"relative_volume_{tf}", n) > 1.0).astype(np.int16)
    k = _a(npz, f"k_{tf}", n)
    k = np.where(k != 0, k, _a(npz, f"stoch_k_{tf}", n))
    s += ((k > 0) & ((k < 80) if is_long else (k > 20))).astype(np.int16)
    s += (_a(npz, f"adx_{tf}", n) > 20).astype(np.int16)
    mh = _a(npz, f"macd_hist_{tf}", n)
    s += ((mh > 0) if is_long else (mh < 0)).astype(np.int16)
    ha = _a(npz, f"ha_color_{tf}", n)
    s += ((ha > 0) if is_long else (ha < 0)).astype(np.int16)
    d = _a(npz, f"d_{tf}", n)
    d = np.where(d != 0, d, _a(npz, f"stoch_d_{tf}", n))
    s += ((k > 0) & (d > 0) & ((k > d) if is_long else (k < d))).astype(np.int16)
    return s


def gr_filter_pass_vec(npz, n, is_long, cfg, close):
    """bool[n]: live gr_filter_pass with the 3m TF treated as passing."""
    if not bool(getattr(cfg, "MTF_GR_FILTER_ENABLED", True)):
        return np.ones(n, dtype=bool)
    min_tfs = int(float(getattr(cfg, "MTF_GR_MIN_TFS", 3) or 3))
    min_ind = int(float(getattr(cfg, "MTF_GR_MIN_IND", 7) or 7))
    invert = bool(getattr(cfg, "MTF_GR_INVERT_DC_BB", True))
    conf = np.ones(n, dtype=np.int16)  # 3m: not evaluable -> passes
    for tf in _TFS:
        conf += (tf_score(npz, n, tf, is_long, invert, close) >= min_ind).astype(np.int16)
    return conf >= min_tfs
