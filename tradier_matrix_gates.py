"""Small, pure stock-path gates shared by live Tradier and V8 replays.

These functions intentionally carry no state and do no I/O.  The matrix can
therefore prove that a knob reaches the same causal predicate in the live
manager and in a V8 invocation.  They are deliberately *not* stop losses:
the DC check confirms an already-valid Delta exit and the trend check only
describes a confirmed lower-high/lower-low (or inverse) turn.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


def _number(source: Mapping[str, Any] | None, key: str) -> float | None:
    """Return a finite scalar, or ``None`` when a live snapshot lacks it."""
    try:
        value = (source or {}).get(key)
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def bb_pullback_gate_blocks(
    indicators: Mapping[str, Any] | None,
    cfg: Any,
    is_long: bool,
) -> bool:
    """Return whether the configured BB pullback gate blocks an entry.

    Missing/invalid BB data must fail open: stale or incomplete snapshots are
    a data-quality condition, not evidence that an otherwise-valid setup is
    extended.  LONG blocks above ``LONG_MAX``; SHORT blocks below
    ``SHORT_MIN``.
    """
    if not bool(getattr(cfg, "BB_PULLBACK_GATE_ENABLED", False)):
        return False
    tf = str(getattr(cfg, "BB_PULLBACK_GATE_TF", "15m") or "15m")
    pct_b = _number(indicators, f"bb_pct_b_{tf}")
    if pct_b is None:
        return False
    try:
        # Keep the reads explicit (rather than a dynamically constructed
        # attribute name) so the matrix wiring inventory can prove both
        # direction-specific settings reach a live decision predicate.
        boundary = float(
            getattr(cfg, "BB_PULLBACK_GATE_LONG_MAX", 0.30)
            if is_long
            else getattr(cfg, "BB_PULLBACK_GATE_SHORT_MIN", 0.70)
        )
    except (TypeError, ValueError):
        return False
    return pct_b > boundary if is_long else pct_b < boundary


def delta_dc_floor_confirms(
    indicators: Mapping[str, Any] | None,
    current_price: float,
    is_long: bool,
    tf: str = "15m",
) -> bool:
    """Confirm a Delta exit with a directional Donchian boundary break.

    This does *not* independently close a trade and therefore cannot become a
    sell-at-the-bottom stop.  It only admits an exit which Delta has already
    signalled.  Absent DC data fails open so a data outage cannot make Delta
    exits disappear.
    """
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(price) or price <= 0:
        return True
    field = f"dc_low_{tf}" if is_long else f"dc_high_{tf}"
    boundary = _number(indicators, field)
    if boundary is None or boundary <= 0:
        return True
    return price <= boundary if is_long else price >= boundary


def trend_reversal_at_recovery_top(
    indicators: Mapping[str, Any] | None,
    current_price: float,
    is_long: bool,
) -> bool:
    """Confirm a structural reversal at a recovery top, never at the break.

    LONG: a 1h/4h bearish WT alignment plus a lower 1h DC low, followed by a
    5m recovery and renewed 5m momentum loss.  SHORT is the exact inverse.
    Requiring the recovery avoids treating the first break of a local low as
    an exit.  Missing inputs fail closed because this is an optional exit.
    """
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(price) or price <= 0:
        return False
    wt1_1h = _number(indicators, "wt1_1h")
    wt2_1h = _number(indicators, "wt2_1h")
    wt1_4h = _number(indicators, "wt1_4h")
    wt2_4h = _number(indicators, "wt2_4h")
    dc_low = _number(indicators, "dc_low_1h")
    dc_low_prev = _number(indicators, "dc_low_1h_ant")
    dc_high = _number(indicators, "dc_high_1h")
    dc_high_prev = _number(indicators, "dc_high_1h_ant")
    dc_low_5m = _number(indicators, "dc_low_5m")
    dc_high_5m = _number(indicators, "dc_high_5m")
    k5 = _number(indicators, "k_5m")
    k5_prev = _number(indicators, "k_5m_prev")
    required = (wt1_1h, wt2_1h, wt1_4h, wt2_4h, k5, k5_prev)
    if any(value is None for value in required):
        return False
    if is_long:
        if None in (dc_low, dc_low_prev, dc_low_5m):
            return False
        return (
            wt1_1h < wt2_1h
            and wt1_4h < wt2_4h
            and dc_low < dc_low_prev
            and price >= dc_low_5m
            and k5 < k5_prev
        )
    if None in (dc_high, dc_high_prev, dc_high_5m):
        return False
    return (
        wt1_1h > wt2_1h
        and wt1_4h > wt2_4h
        and dc_high > dc_high_prev
        and price <= dc_high_5m
        and k5 > k5_prev
    )
