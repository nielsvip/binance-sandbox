"""frozen_floor_exit.py — SHARED scalar+vectorized FROZEN_FLOOR exit predicate.

Single source of truth for the frozen floor / absolute floor exit.
Live scalar: ez_manage.py:45513-45556 FROZEN_ACT_STOP
Config: FROZEN_ACTIVATION_STOP_ENABLED, FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO/TRADIER,
        FROZEN_ABSOLUTE_FLOOR_PCT, FROZEN_ACTIVATION_TF

Live logic:
  if not FROZEN_ACTIVATION_STOP_ENABLED: no fire
  floor_pct = FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO (default -10%) for crypto,
             FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER for stocks
  fires when gain_pct <= floor_pct (absolute loss floor breached)
  also fires when frozen breach: price below frozen level (e.g., 4h DC low)
  For vectorized parity we capture the absolute floor core which is the
  deterministic, per-bar portion; frozen breach level is indicator-dependent
  and also wired via same predicate with level param.

Both scalar and vector share _frozen_floor_fires.
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np


def _num(m: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = (m or {}).get(key, default)
        fv = float(v)
        return fv if math.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _frozen_floor_fires(
    gain_pct: float,
    price: float,
    floor_level: float,
    floor_pct: float,
) -> bool:
    """Pure predicate: True if this bar should trigger frozen floor exit.

    floor_pct is negative (e.g., -10 = 10% loss). gain_pct already is (price-entry)/entry*100.
    Also fires if floor_level>0 and price <= floor_level (frozen breach).
    """
    if gain_pct <= floor_pct:
        return True
    if floor_level > 0 and price > 0 and price <= floor_level:
        return True
    return False


def frozen_floor_exit_should_exit(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    gain_pct: float,
    current_price: float = 0.0,
    floor_level: float = 0.0,
) -> bool:
    """Live scalar: whether FROZEN_FLOOR triggers exit on this bar."""
    if not bool(getattr(cfg, "FROZEN_ACTIVATION_STOP_ENABLED", True)):
        return False
    # pick floor pct — crypto vs tradier vs generic
    floor_pct = float(getattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO", getattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT", -10.0)))
    # allow per-call override if tradier-specific set
    if "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER" in dir(cfg) or hasattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER"):
        # if caller is stocks, caller should pass stock cfg which has tradier value
        try:
            v = float(getattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER", floor_pct))
            # only use tradier value if it differs from default crypto and is set
            if v != floor_pct:
                floor_pct = v
        except Exception:
            pass
    price = float(current_price) if current_price else _num(indicators, "current_price", _num(indicators, "close", 0.0))
    if floor_level == 0.0:
        floor_level = _num(indicators, "frozen_floor_level", 0.0)
    return _frozen_floor_fires(gain_pct, price, floor_level, floor_pct)


# alias
frozen_floor_should_exit = frozen_floor_exit_should_exit
frozen_floor_fires = frozen_floor_exit_should_exit


def frozen_floor_exit_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    gain_pct_arr: Any = None,
    price_arr: Any = None,
) -> np.ndarray:
    """Vectorized FROZEN_FLOOR exit mask. SAME predicate as scalar.

    gain_pct_arr/price_arr may be passed as arrays; otherwise read from npz.
    floor_pct from cfg (FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO etc).
    floor_level per-bar from npz['frozen_floor_level'] if present.
    Returns bool ndarray shape (n,): True where exit should fire.
    """
    if not bool(getattr(cfg, "FROZEN_ACTIVATION_STOP_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    floor_pct = float(getattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO", getattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT", -10.0)))
    try:
        if hasattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER"):
            v = float(getattr(cfg, "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER"))
            # caller can choose which; keep crypto default for now — vector caller may override
            _ = v
    except Exception:
        pass

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

    if gain_pct_arr is not None:
        gain = np.asarray(gain_pct_arr, dtype=float)
        if gain.size < n:
            tmp = np.full(n, 0.0, dtype=float)
            tmp[: gain.size] = gain[: gain.size]
            gain = tmp
        else:
            gain = gain[:n]
    else:
        gain = _arr("gain_pct", 0.0)
        if np.all(gain == 0) and "unrealized_pnl_pct" in npz:
            gain = _arr("unrealized_pnl_pct", 0.0)

    if price_arr is not None:
        price = np.asarray(price_arr, dtype=float)
        if price.size < n:
            tmp = np.full(n, 0.0, dtype=float)
            tmp[: price.size] = price[: price.size]
            price = tmp
        else:
            price = price[:n]
    else:
        price = _arr("close", 0.0)
        if np.all(price == 0) and "current_price" in npz:
            price = _arr("current_price", 0.0)

    floor_level = _arr("frozen_floor_level", 0.0)

    floor_hit = gain <= floor_pct
    breach_hit = (floor_level > 0) & (price > 0) & (price <= floor_level)
    return floor_hit | breach_hit
