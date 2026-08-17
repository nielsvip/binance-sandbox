#!/usr/bin/env python3
"""Canonical adverse fill-price contract for vector research.

This tiny module exists so LONG and SHORT simulators cannot independently
reinterpret slippage.  ``opening=True`` means increasing absolute exposure;
``opening=False`` means reducing/closing it.
"""
from __future__ import annotations


def adverse_fill_price(
    raw_price: float,
    *,
    position_side: str,
    opening: bool,
    slippage_rate: float,
) -> float:
    side = str(position_side).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("position_side must be LONG or SHORT")
    raw = float(raw_price)
    slip = float(slippage_rate)
    if raw <= 0:
        raise ValueError("raw_price must be positive")
    if not 0.0 <= slip < 1.0:
        raise ValueError("slippage_rate must be in [0, 1)")
    # LONG opens buy and closes sell. SHORT opens sell and closes buy.
    is_buy = (side == "LONG") == bool(opening)
    return raw * (1.0 + slip if is_buy else 1.0 - slip)
