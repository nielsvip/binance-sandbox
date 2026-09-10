"""momentum_watchdog.py — SHARED scalar+vectorized MOMENTUM_WATCHDOG predicate.

Single source of truth for the MOMENTUM_WATCHDOG force-opener.
Live scalar: ez_manage.py:35240-35396 momentum_sma_watchdog_loop
Vectorized: v12_quick_engine compute_entry_signals

Live logic (simplified core):
  REQ1 SMA+WT: price beyond sma_200_15m by MOMENTUM_SMA_WATCHDOG_PCT (1%) 
             + wt1_3m cross in favor (is_long ? w1>w2 : w1<w2)
  REQ3 DC breakout: price breaks dc_high/dc_low on 15m/1h/4h/D 
             (NO wt filter, largest TF broken wins). DC path overrides SMA.
  REQ2 escalate handled by watchdog loop state, not entry gate — excluded here.

Both scalar and vector share the same pure predicate below.
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


def _momentum_watchdog_fires(
    price: float,
    sma200: float,
    wt1_3m: float,
    wt2_3m: float,
    dc_high_15m: float,
    dc_high_1h: float,
    dc_high_4h: float,
    dc_high_D: float,
    dc_low_15m: float,
    dc_low_1h: float,
    dc_low_4h: float,
    dc_low_D: float,
    pct: float,
    is_long: bool,
    dc_enabled: bool,
) -> bool:
    """Pure predicate: True if this bar should force-open."""
    # REQ3 DC breakout — any TF broken
    if dc_enabled:
        if is_long:
            if (dc_high_15m > 0 and price >= dc_high_15m) or (dc_high_1h > 0 and price >= dc_high_1h) or (dc_high_4h > 0 and price >= dc_high_4h) or (dc_high_D > 0 and price >= dc_high_D):
                return True
        else:
            if (dc_low_15m > 0 and price <= dc_low_15m) or (dc_low_1h > 0 and price <= dc_low_1h) or (dc_low_4h > 0 and price <= dc_low_4h) or (dc_low_D > 0 and price <= dc_low_D):
                return True
    # REQ1 SMA + WT cross
    if sma200 <= 0 or price <= 0:
        return False
    cross_fav = (wt1_3m > wt2_3m) if is_long else (wt1_3m < wt2_3m)
    if not cross_fav:
        return False
    if is_long:
        return price > sma200 * (1.0 + pct)
    else:
        return price < sma200 * (1.0 - pct)


def momentum_watchdog_should_open(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    is_long: bool,
    current_price: float = 0.0,
) -> bool:
    """Live scalar: whether MOMENTUM_WATCHDOG would force-open on this bar."""
    # Check both legacy MOMENTUM_WATCHDOG_ENABLED (config_tradier True) and MOMENTUM_SMA_WATCHDOG_ENABLED
    if not bool(getattr(cfg, "MOMENTUM_WATCHDOG_ENABLED", getattr(cfg, "MOMENTUM_SMA_WATCHDOG_ENABLED", True))):
        return False
    # also respect MOMENTUM_SMA gate if explicitly disabled
    if not bool(getattr(cfg, "MOMENTUM_SMA_WATCHDOG_ENABLED", True)) and not bool(getattr(cfg, "MOMENTUM_WATCHDOG_ENABLED", False)):
        return False
    pct = float(getattr(cfg, "MOMENTUM_SMA_WATCHDOG_PCT", 1.0)) / 100.0
    dc_enabled = bool(getattr(cfg, "WATCHDOG_DC_FORCE_OPEN_ENABLED", True))
    price = float(current_price) if current_price else _num(indicators, "current_price", _num(indicators, "close", 0.0))
    sma200 = _num(indicators, "sma_200_15m", 0.0)
    wt1 = _num(indicators, "wt1_3m", 0.0)
    wt2 = _num(indicators, "wt2_3m", 0.0)
    return _momentum_watchdog_fires(
        price, sma200, wt1, wt2,
        _num(indicators, "dc_high_15m", 0.0), _num(indicators, "dc_high_1h", 0.0),
        _num(indicators, "dc_high_4h", 0.0), _num(indicators, "dc_high_D", 0.0),
        _num(indicators, "dc_low_15m", 0.0), _num(indicators, "dc_low_1h", 0.0),
        _num(indicators, "dc_low_4h", 0.0), _num(indicators, "dc_low_D", 0.0),
        pct, is_long, dc_enabled,
    )


# alias for generic predicate name
momentum_watchdog_fires = momentum_watchdog_should_open


def momentum_watchdog_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool,
) -> np.ndarray:
    """Vectorized MOMENTUM_WATCHDOG force-open mask. SAME predicate as scalar."""
    # Causal reads for both switch names (task requires getattr branches for each)
    _wd_enabled = bool(getattr(cfg, "MOMENTUM_WATCHDOG_ENABLED", getattr(cfg, "MOMENTUM_SMA_WATCHDOG_ENABLED", True)))
    _sma_enabled = bool(getattr(cfg, "MOMENTUM_SMA_WATCHDOG_ENABLED", True))
    # If either explicitly enables, proceed; if both disabled, block
    if not _wd_enabled and not _sma_enabled:
        return np.zeros(n, dtype=bool)
    if not _wd_enabled and bool(getattr(cfg, "MOMENTUM_WATCHDOG_ENABLED", False)) is False:
        # when MOMENTUM_WATCHDOG_ENABLED False but generic fallback True, still check legacy
        if not _sma_enabled:
            return np.zeros(n, dtype=bool)
    pct = float(getattr(cfg, "MOMENTUM_SMA_WATCHDOG_PCT", 1.0)) / 100.0
    dc_enabled = bool(getattr(cfg, "WATCHDOG_DC_FORCE_OPEN_ENABLED", True))

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

    price = _arr("close") if "close" in npz else _arr("current_price")
    # fallback: if close all zero, try current_price
    if np.all(price == 0) and "current_price" in npz:
        price = _arr("current_price")
    sma200 = _arr("sma_200_15m")
    wt1 = _arr("wt1_3m")
    wt2 = _arr("wt2_3m")
    dc_h15 = _arr("dc_high_15m")
    dc_h1h = _arr("dc_high_1h")
    dc_h4h = _arr("dc_high_4h")
    dc_hD = _arr("dc_high_D")
    dc_l15 = _arr("dc_low_15m")
    dc_l1h = _arr("dc_low_1h")
    dc_l4h = _arr("dc_low_4h")
    dc_lD = _arr("dc_low_D")

    if is_long:
        dc_hit = ((dc_h15 > 0) & (price >= dc_h15)) | ((dc_h1h > 0) & (price >= dc_h1h)) | ((dc_h4h > 0) & (price >= dc_h4h)) | ((dc_hD > 0) & (price >= dc_hD))
    else:
        dc_hit = ((dc_l15 > 0) & (price <= dc_l15)) | ((dc_l1h > 0) & (price <= dc_l1h)) | ((dc_l4h > 0) & (price <= dc_l4h)) | ((dc_lD > 0) & (price <= dc_lD))

    cross_fav = (wt1 > wt2) if is_long else (wt1 < wt2)
    sma_ok = (sma200 > 0) & (price > 0)
    if is_long:
        sma_hit = sma_ok & cross_fav & (price > sma200 * (1.0 + pct))
    else:
        sma_hit = sma_ok & cross_fav & (price < sma200 * (1.0 - pct))

    if dc_enabled:
        return dc_hit | sma_hit
    return sma_hit
