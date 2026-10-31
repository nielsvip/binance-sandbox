"""NumPy twin of native MTF/GR indicator votes over policy-selected inputs."""
from __future__ import annotations

import numpy as np
from vec_decisions.asr_input_adapter import paired_npz_view

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
    return v


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
    supplied_dcp = _a(npz, f"dc_pct_{tf}", n)
    fallback_dcp = np.where((rng > 0) & (close > 0), (close - dcl) / np.where(rng > 0, rng, 1.0), 0.0)
    dcp = np.where(supplied_dcp <= 0, fallback_dcp, supplied_dcp)
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
    if not bool(getattr(cfg, "MTF_GR_FILTER_ENABLED", True)):
        return np.ones(n, dtype=bool)
    min_tfs = int(getattr(cfg, "MTF_GR_MIN_TFS", getattr(cfg, "GOLDEN_RULE_HTF_MIN_TFS", 3)))
    min_ind = int(getattr(cfg, "MTF_GR_MIN_IND", getattr(cfg, "GOLDEN_RULE_MIN_IND", 5)))
    invert = bool(getattr(cfg, "MTF_GR_INVERT_DC_BB", False))
    stock = str(getattr(cfg, "MODE", "crypto")) == "tradier"
    use_ltf = bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED" if stock else "USE_1M_3M_SIGNALS_ENABLED", False))
    npz = paired_npz_view(npz, use_ltf)
    conf = np.zeros(n, dtype=np.int16)
    for tf in (("5m" if stock else "3m"), *_TFS):
        conf += (tf_score(npz, n, tf, is_long, invert, close) >= min_ind).astype(np.int16)
    return conf >= min_tfs
