"""market_crash_blanket.py — SHARED scalar+vectorized MARKET_CRASH_BLANKET predicate.

Single source of truth for the blanket block during crash regimes.
Live scalar: ez_manage.py:23707-23719 market regime CRASH/JUMP gate
Also covers MARKET_CRASH_THRESHOLD_PCT blanket (config.py)

Live logic:
  regime = check_market_regime(score, indicators)  # CRASH if sentiment<30 etc
  if regime == "CRASH" and is_long: BLOCK
  if regime == "JUMP"  and is_short: BLOCK
  additionally, if price crash pct > MARKET_CRASH_THRESHOLD_PCT, block entries.

Vectorized: same thresholds applied per-bar via numpy.
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


def _market_crash_blocks(
    sentiment: float,
    crash_pct: float,
    threshold_pct: float,
    is_long: bool,
    enabled: bool,
) -> bool:
    """Pure predicate: True if BLOCKED by crash blanket."""
    if not enabled:
        return False
    # regime-based: sentiment 0-100, CRASH <30, JUMP >70 (mirrors ez_manage regime)
    if is_long and sentiment < 30.0:
        return True
    if not is_long and sentiment > 70.0:
        return True
    # blanket threshold: if absolute market crash pct exceeds threshold, block longs
    if threshold_pct > 0 and crash_pct <= -abs(threshold_pct):
        if is_long:
            return True
    return False


def market_crash_blanket_blocks(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    is_long: bool,
    crash_pct: float = 0.0,
) -> bool:
    """Live scalar: whether MARKET_CRASH_BLANKET blocks entry."""
    enabled = bool(getattr(cfg, "MARKET_CRASH_BLANKET_ENABLED", True))
    # also respect legacy threshold config
    threshold_pct = float(getattr(cfg, "MARKET_CRASH_THRESHOLD_PCT", 0.0))
    if not enabled and threshold_pct <= 0:
        # if blanket disabled and no threshold, fail open
        # but still honor threshold if >0 even when blanket flag off
        if threshold_pct <= 0:
            return False
        enabled = True
    sentiment = _num(indicators, "0market_sentiment_score", _num(indicators, "market_sentiment_score", 50.0))
    if crash_pct == 0.0:
        crash_pct = _num(indicators, "market_crash_pct", 0.0)
    return _market_crash_blocks(sentiment, crash_pct, threshold_pct, is_long, enabled)


# alias
market_crash_blocks = market_crash_blanket_blocks


def market_crash_blanket_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool,
) -> np.ndarray:
    """Vectorized MARKET_CRASH_BLANKET block mask. SAME predicate as scalar."""
    enabled = bool(getattr(cfg, "MARKET_CRASH_BLANKET_ENABLED", True))
    threshold_pct = float(getattr(cfg, "MARKET_CRASH_THRESHOLD_PCT", 0.0))
    if not enabled and threshold_pct <= 0:
        return np.zeros(n, dtype=bool)
    if not enabled:
        enabled = True

    def _arr(key: str, default: float = 50.0) -> np.ndarray:
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

    sentiment = _arr("0market_sentiment_score", 50.0)
    if "0market_sentiment_score" not in npz and "market_sentiment_score" in npz:
        sentiment = _arr("market_sentiment_score", 50.0)
    crash_pct = _arr("market_crash_pct", 0.0)

    if is_long:
        regime_block = sentiment < 30.0
    else:
        regime_block = sentiment > 70.0

    if threshold_pct > 0:
        blanket_block = crash_pct <= -abs(threshold_pct)
        if is_long:
            blanket_block = blanket_block
        else:
            blanket_block = np.zeros(n, dtype=bool)
        return regime_block | blanket_block
    return regime_block
