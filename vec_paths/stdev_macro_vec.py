"""Vectorized macro stdev z-score computation.

Mirrors the scalar logic in stdev_macro.compute_z_scalar but operates on full
close-price arrays at D, W, M timeframes. Used by precompute (to emit
macro_z_{D,W,M} arrays into NPZ) AND by v8_vec_sweep (to derive per-bar state
without re-reading per-bar dicts).

Parity guarantee: for every (log_close, mean, std) triple, the vec path
produces a float that matches the scalar path within 1e-9 (uses pandas
rolling.mean / rolling.std, same algorithm as numpy scalar reference). Tested
via tools/test_stdev_macro_parity.py.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

DEFAULT_WINDOWS = {"D": 200, "W": 52, "M": 24}
STRONG_THRESHOLD = 2.5
MODERATE_THRESHOLD = 1.5


def rolling_log_zscore(close: np.ndarray, window: int) -> np.ndarray:
    """Compute z-score of log(close) vs rolling mean/std over `window` bars.

    Returns float64 array same length as close. NaN-safe (non-positive or
    NaN inputs → 0.0 output for affected bars). First (window-1) outputs
    are 0.0 (insufficient warmup). std < 1e-9 → 0.0 (constant series).
    """
    close = np.asarray(close, dtype=np.float64)
    n = close.shape[0]
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0 or window > n:
        return out
    positive = close > 0
    log_close = np.zeros(n, dtype=np.float64)
    log_close[positive] = np.log(close[positive])
    log_close = np.where(np.isfinite(log_close), log_close, 0.0)
    s = pd.Series(log_close)
    means = s.rolling(window=window, min_periods=window).mean().to_numpy()
    stds = s.rolling(window=window, min_periods=window).std(ddof=0).to_numpy()
    z = np.zeros(n, dtype=np.float64)
    valid = np.isfinite(means) & np.isfinite(stds) & (stds > 1e-9)
    z[valid] = (log_close[valid] - means[valid]) / stds[valid]
    z = np.where(np.isfinite(z), z, 0.0)
    return z


def derive_state_vec(z_d: np.ndarray, z_w: np.ndarray) -> np.ndarray:
    """Return state code per bar: 0=MID, 1=TOP, 2=STRONG_TOP, -1=BOT, -2=STRONG_BOT.

    Matches scalar derive_state semantics: opposite-sign extremes between D
    and W (one TF top, other TF bot) classify as MID (conflicting signal).
    """
    z_d = np.asarray(z_d, dtype=np.float64)
    z_w = np.asarray(z_w, dtype=np.float64)
    z_d = np.where(np.isfinite(z_d), z_d, 0.0)
    z_w = np.where(np.isfinite(z_w), z_w, 0.0)
    top_any = (z_d > MODERATE_THRESHOLD) | (z_w > MODERATE_THRESHOLD)
    bot_any = (z_d < -MODERATE_THRESHOLD) | (z_w < -MODERATE_THRESHOLD)
    conflict = top_any & bot_any
    strong_top = (z_d > STRONG_THRESHOLD) & (z_w > MODERATE_THRESHOLD)
    strong_bot = (z_d < -STRONG_THRESHOLD) & (z_w < -MODERATE_THRESHOLD)
    state = np.zeros(z_d.shape, dtype=np.int8)
    state = np.where(top_any & ~conflict, 1, state)
    state = np.where(bot_any & ~conflict, -1, state)
    state = np.where(strong_top & ~conflict, 2, state)
    state = np.where(strong_bot & ~conflict, -2, state)
    return state


STATE_NAME = {2: "STRONG_TOP", 1: "TOP", 0: "MID", -1: "BOT", -2: "STRONG_BOT"}


def state_code_to_name(code: int) -> str:
    return STATE_NAME.get(int(code), "MID")
