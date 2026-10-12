"""FIXED_STOP core — non-negotiable frozen-at-entry stop (USER 2026-10-12).

The stop is a simple fact, not a switch: NO config field, NO QuickConfig
field, NO template row, NO venue/mode gate. Every consumer (v12 vec,
ez_manage.process_position, ez_positions_service monitor+fetch, tradier
process_position, backtest_v12 via the real process_position) imports THIS
module so the math cannot drift between paths.

Levels:
  software (maker-first regular exit): LONG entry-bar dc_low_15m * 0.9975,
    SHORT entry-bar dc_high_15m * 1.0025. Frozen at first entry, never moved.
  exchange (STOP_MARKET taker backstop): 0.05% beyond software, so the
    regular exit is tried first and taker fees are the last resort.
Fallback when the entry-bar channel is missing/zero: entry price based.
Pure math only — no config/env imports (import-cycle safe).
"""
from __future__ import annotations
import math

REASON = "FIXED_STOP"
FIXED_STOP_BUFFER_PCT = 0.25
EXCHANGE_EXTRA_PCT = 0.05
STOP_ORDER_PREFIX = "EZMSTOP_FX_"


def freeze_software_level(is_long: bool, dc_low_15m: float, dc_high_15m: float, entry_price: float) -> float:
    try:
        ch = float(dc_low_15m) if is_long else float(dc_high_15m)
    except (TypeError, ValueError):
        ch = 0.0
    try:
        ep = float(entry_price or 0.0)
    except (TypeError, ValueError):
        ep = 0.0
    base = ch if ch > 0 else (ep if ep > 0 else 0.0)
    if base <= 0:
        return 0.0
    return base * 0.9975 if is_long else base * 1.0025


def exchange_level(is_long: bool, soft_level: float) -> float:
    try:
        s = float(soft_level or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0
    return s * 0.9995 if is_long else s * 1.0005


def breached(is_long: bool, price: float, soft_level: float) -> bool:
    try:
        px = float(price or 0.0)
        lv = float(soft_level or 0.0)
    except (TypeError, ValueError):
        return False
    if px <= 0 or lv <= 0:
        return False
    return px <= lv if is_long else px >= lv


def round_to_tick(is_long: bool, level: float, tick: float) -> float:
    try:
        lv = float(level or 0.0)
        tk = float(tick or 0.0)
    except (TypeError, ValueError):
        return float(level or 0.0)
    if lv <= 0 or tk <= 0:
        return lv
    q = lv / tk
    steps = math.floor(q + 1e-9) if is_long else math.ceil(q - 1e-9)
    out = steps * tk
    return float(f"{out:.10f}")


def build_stop_params(is_long: bool, soft_level: float, tick: float) -> dict:
    lvl = exchange_level(is_long, soft_level)
    return {
        "side": "SELL" if is_long else "BUY",
        "stopPrice": round_to_tick(is_long, lvl, tick),
        "closePosition": True,
        "workingType": "MARK_PRICE",
    }
