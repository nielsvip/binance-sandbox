"""Pure c5 execution invariants used by the exact Tradier adapter."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def exact_side_allowed(requested_side, contract_side):
    requested = str(requested_side or "").upper()
    contract = str(contract_side or "").upper()
    return contract not in ("LONG", "SHORT") or requested == contract


def clamp_open_quantity(
    requested_qty,
    price,
    existing_qty,
    capacity_usd=16000.0,
    *,
    whole_shares=False,
):
    price = max(0.0, float(price or 0.0))
    requested = max(0.0, float(requested_qty or 0.0))
    existing = max(0.0, abs(float(existing_qty or 0.0)))
    capacity = max(0.0, float(capacity_usd or 0.0))
    if price <= 0.0:
        return 0.0
    remaining = max(0.0, capacity - existing * price)
    clamped = min(requested, remaining / price)
    # Stocks must never become fractional merely because the fill price moved
    # between signal and next-bar execution.  Do not round up: if the remaining
    # buying power cannot cover one share, the caller must reject the order.
    return float(math.floor(clamped)) if whole_shares else clamped


def actual_filled_quantity(
    events: Iterable[Mapping[str, Any]], position_key
):
    key = str(position_key or "")
    return sum(
        abs(
            float(
                event.get("executed_qty", event.get("quantity", 0)) or 0
            )
        )
        for event in events
        if str(event.get("position_key") or "") == key
    )
