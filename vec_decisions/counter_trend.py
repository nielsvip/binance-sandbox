"""counter_trend.py — SHARED scalar+vectorized COUNTER_TREND_ADD_BLOCK predicate.

Single source of truth for the live COUNTER_TREND_ADD_BLOCK gate.
The live scalar path (ez_manage.py:28745-28790)
AND the vectorized backtest path (v12_quick_engine compute_entry_signals)
BOTH derive their block decision from the same pure predicate below.

Live logic (ez_manage.py):

    if COUNTER_TREND_ADD_BLOCK_ENABLED and symbol and OPEN/ENTRY/REENTRY
       and CLOSE not in action and REDUCE not in action and HEDGE not in action
       and "OBLIGATORY" not in reason and "REENTRY" not in reason
       and "PRICE_CROSS" not in reason:
        w1 = indicators["wt1_1h"]; w2 = indicators["wt2_1h"]
        sma200 = indicators["sma_200_15m"]; price = current_price
        # 2026-06-04 SMA200 + 1h structure bypass
        if COUNTER_TREND_SMA200_BYPASS_ENABLED:
            if is_long:
                hh_n = indicators["high_1h"]; hh_p = indicators["high_1h_prev"]
                struct = (hh_n>0 and hh_p>0 and hh_n>hh_p) or (w1>w2)
                aligned = (sma200>0 and price>0 and price>sma200 and struct)
            else:
                ll_n = indicators["low_1h"]; ll_p = indicators["low_1h_prev"]
                struct = (ll_n>0 and ll_p>0 and ll_n<ll_p) or (w1<w2)
                aligned = (sma200>0 and price>0 and price<sma200 and struct)
        nonzero = abs(w1)>1e-9 or abs(w2)>1e-9
        against = (w1<w2) if is_long else (w1>w2)
        BLOCK if nonzero and not aligned and against

Fail-open for missing/zero WT data and for bypass alignment.
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

_EPS = 1e-9


def _num(m: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = (m or {}).get(key, default)
        fv = float(v)
        return fv if math.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _counter_trend_blocks(
    w1: float, w2: float, sma200: float, price: float,
    high_1h: float, high_1h_prev: float, low_1h: float, low_1h_prev: float,
    is_long: bool, bypass_enabled: bool,
) -> bool:
    """Pure predicate: True if this bar should be BLOCKED."""
    nonzero = (abs(w1) > _EPS or abs(w2) > _EPS)
    if not nonzero:
        return False
    # SMA200 bypass
    aligned = False
    if bypass_enabled:
        if sma200 > 0 and price > 0:
            if is_long:
                struct = (high_1h > 0 and high_1h_prev > 0 and high_1h > high_1h_prev) or (w1 > w2)
                aligned = (price > sma200) and struct
            else:
                struct = (low_1h > 0 and low_1h_prev > 0 and low_1h < low_1h_prev) or (w1 < w2)
                aligned = (price < sma200) and struct
    if aligned:
        return False
    against = (w1 < w2) if is_long else (w1 > w2)
    return bool(against)


def counter_trend_blocks(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    is_long: bool,
    current_price: float = 0.0,
) -> bool:
    """Live scalar: whether COUNTER_TREND_ADD_BLOCK blocks entry.

    Mirrors ez_manage.py:28752-28788.
    Returns True if BLOCKED, False if allowed or fail-open.
    """
    if not bool(getattr(cfg, "COUNTER_TREND_ADD_BLOCK_ENABLED", True)):
        return False
    bypass = bool(getattr(cfg, "COUNTER_TREND_SMA200_BYPASS_ENABLED", True))
    w1 = _num(indicators, "wt1_1h", 0.0)
    w2 = _num(indicators, "wt2_1h", 0.0)
    sma200 = _num(indicators, "sma_200_15m", 0.0)
    price = float(current_price) if current_price else _num(indicators, "close", 0.0)
    high_1h = _num(indicators, "high_1h", 0.0)
    high_1h_prev = _num(indicators, "high_1h_prev", 0.0)
    low_1h = _num(indicators, "low_1h", 0.0)
    low_1h_prev = _num(indicators, "low_1h_prev", 0.0)
    return _counter_trend_blocks(w1, w2, sma200, price, high_1h, high_1h_prev, low_1h, low_1h_prev, is_long, bypass)


def counter_trend_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool,
) -> np.ndarray:
    """Vectorized COUNTER_TREND_ADD_BLOCK block mask. SAME predicate as scalar.

    Returns bool ndarray shape (n,): True where entry should be BLOCKED.
    Reads npz keys: wt1_1h, wt2_1h, sma_200_15m, close/high_1h/high_1h_prev/low_1h/low_1h_prev.
    Missing keys fail open (all False).
    """
    if not bool(getattr(cfg, "COUNTER_TREND_ADD_BLOCK_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    bypass_enabled = bool(getattr(cfg, "COUNTER_TREND_SMA200_BYPASS_ENABLED", True))

    def _arr(key: str, default: float = 0.0) -> np.ndarray:
        if key in npz:
            a = np.asarray(npz[key], dtype=float)
            if a.size < n:
                tmp = np.full(n, default, dtype=float)
                tmp[: min(n, a.size)] = a[: min(n, a.size)]
                a = tmp
            else:
                a = a[:n]
            return np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)
        return np.full(n, default, dtype=float)

    w1 = _arr("wt1_1h")
    w2 = _arr("wt2_1h")
    sma200 = _arr("sma_200_15m")
    # current_price proxy: use close, and high_1h/low_1h for structure
    close = _arr("close")
    high_1h = _arr("high_1h")
    high_1h_prev = _arr("high_1h_prev")
    low_1h = _arr("low_1h")
    low_1h_prev = _arr("low_1h_prev")

    nonzero = (np.abs(w1) > _EPS) | (np.abs(w2) > _EPS)
    if is_long:
        struct = ((high_1h > 0) & (high_1h_prev > 0) & (high_1h > high_1h_prev)) | (w1 > w2)
        aligned = (sma200 > 0) & (close > 0) & (close > sma200) & struct if bypass_enabled else np.zeros(n, dtype=bool)
        against = w1 < w2
    else:
        struct = ((low_1h > 0) & (low_1h_prev > 0) & (low_1h < low_1h_prev)) | (w1 < w2)
        aligned = (sma200 > 0) & (close > 0) & (close < sma200) & struct if bypass_enabled else np.zeros(n, dtype=bool)
        against = w1 > w2
    blocked = nonzero & against & ~aligned
    return blocked


def counter_trend_allowed_vec(npz: Mapping[str, Any], n: int, cfg: Any, is_long: bool) -> np.ndarray:
    """Convenience: allowed mask (inverse of blocks)."""
    return ~counter_trend_vec(npz, n, cfg, is_long)
