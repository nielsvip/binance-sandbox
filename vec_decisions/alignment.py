"""vec_decisions.alignment — SHARED side-aware trend-alignment score (USER 2026-10-06 full parity).

The bare live `alignment` indicator key was never populated anywhere (always 0), while vec
computed its own 0-24 score (user-approved 2026-09-28, formerly in bb_squeeze_gate.py).
Both sides now share THIS formula (scalar for live/backtest rows, vector for NPZ bars):

  trend 12 = ema_9 vs ema_20/50/200 at 15m/1h/4h (4 each)
  stoch  6 = stoch_k vs stoch_d at 15m/1h/4h (2 each)
  vol    2 = relative_volume_1h > 1
  regime 2 = close vs sma_200_1h
  wt     2 = wt1_1h vs wt2_1h

Side-aware: LONG counts bullish comparisons, SHORT bearish. Missing/zero EMA/SMA legs are
excluded (not counted); stoch defaults 50/50 (never counts); rvol defaults 1.0 (never counts).
Maximum 24. Live gates: VOL_SPIKE_MIN_ALIGNMENT=3, BB_SQUEEZE_MIN_ALIGNMENT=10.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def compute_alignment_scalar(indicators: Mapping, is_long: bool) -> float:
    """Scalar port — live EPQ BB/VOL gates and backtest rows call this (is_long known)."""

    def _f(key: str, default: float) -> float:
        try:
            v = indicators.get(key, default) if hasattr(indicators, "get") else default
        except Exception:
            v = default
        if v is None:
            return float(default)
        try:
            fv = float(v)
        except Exception:
            return float(default)
        return fv if np.isfinite(fv) else float(default)

    score = 0.0
    for fast_k, slow_k in (("ema_9_15m", "ema_20_15m"), ("ema_9_1h", "ema_50_1h"), ("ema_9_4h", "ema_200_4h")):
        f, s = _f(fast_k, 0.0), _f(slow_k, 0.0)
        if f != 0 and s != 0 and ((f > s) if is_long else (f < s)):
            score += 4.0
    for tf in ("15m", "1h", "4h"):
        k, d = _f(f"stoch_k_{tf}", 50.0), _f(f"stoch_d_{tf}", 50.0)
        if (k > d) if is_long else (k < d):
            score += 2.0
    if _f("relative_volume_1h", 1.0) > 1.0:
        score += 2.0
    c, sma = _f("close", 0.0), _f("sma_200_1h", 0.0)
    if c == 0.0:
        c = _f("current_price", 0.0)
    if sma != 0 and ((c > sma) if is_long else (c < sma)):
        score += 2.0
    w1, w2 = _f("wt1_1h", 0.0), _f("wt2_1h", 0.0)
    if (w1 > w2) if is_long else (w1 < w2):
        score += 2.0
    return score


def compute_alignment_vec(npz: Any, n: int, is_long: bool) -> np.ndarray:
    """Vector port — engines call per side. Identical math to the scalar port."""
    n = int(n)

    def _f(key: str, default: float) -> np.ndarray:
        v = npz.get(key) if hasattr(npz, "get") else None
        if v is None:
            return np.full(n, float(default), dtype=float)
        a = np.asarray(v, dtype=float)
        if len(a) != n:
            a = (a[:n] if len(a) > n else np.concatenate([a, np.full(n - len(a), float(default))]))
        return np.where(np.isfinite(a), a, float(default))

    score = np.zeros(n, dtype=float)
    for fast, slow in (("ema_9_15m", "ema_20_15m"), ("ema_9_1h", "ema_50_1h"), ("ema_9_4h", "ema_200_4h")):
        f, s = _f(fast, 0.0), _f(slow, 0.0)
        ok = (f > s) if is_long else (f < s)
        score += 4.0 * (ok & (f != 0) & (s != 0))
    for tf in ("15m", "1h", "4h"):
        k, d = _f(f"stoch_k_{tf}", 50.0), _f(f"stoch_d_{tf}", 50.0)
        score += 2.0 * ((k > d) if is_long else (k < d))
    score += 2.0 * (_f("relative_volume_1h", 1.0) > 1.0)
    c, sma = _f("close", 0.0), _f("sma_200_1h", 0.0)
    reg = (c > sma) if is_long else (c < sma)
    score += 2.0 * (reg & (sma != 0))
    w1, w2 = _f("wt1_1h", 0.0), _f("wt2_1h", 0.0)
    score += 2.0 * ((w1 > w2) if is_long else (w1 < w2))
    return score
