"""vec_decisions/small_account_sizer.py — TWIN of live small-account order sizing.

Live source: ez_manage.py execute_now (crypto path):
  1. per-account MAX_ORDER clamp — ez_manage.py:~33975
       max_order_value_usd = MAX_ORDER_VALUE_MEN if men else MAX_ORDER_VALUE_FIN
       if men else MAX_ORDER_VALUE
       if quantity * price > max_order_value_usd: quantity = max/price
  2. inf/fin/men 0.15x rule — ez_manage.py:~34594
       if account in [inf,fin,men] and not hedge and not scalp_v3:
           quantity = min(0.15*quantity, 2*START_POSITION_SIZE) / price
     NOTE the live unit soup is replicated EXACTLY: quantity is in COINS,
     2*START is in USD, and min() compares them raw before dividing by price.
     Live is truth — do NOT "fix" the units here.
  3. ORDER_SIZE_CAP — ez_manage.py:~34604
       if |qty*price| > MAX_ORDER_VALUE: qty = MAX_ORDER_VALUE/price
  4. maker lot-step floor — place_maker_order, ez_manage.py:~29214
       qty_dec = (qty // step) * step   (Decimal; step from symbol_configs.json)
       if qty_dec == 0: ORDER NOT PLACED (MAKER_ZERO_QTY, suppress-sentinel)

Provenance (decisions<->history parity, Oct 4-5 2026, live LIGHT_MODE
START=16/MOV=80/MEN=440/FIN=18):
  fin:DASHUSDT_LONG GOLDEN_RULE $25/59 -> $18 FIN clamp -> 0.305 coins
    -> 0.15x -> 0.00077 < step 0.001 -> MAKER_ZERO_QTY (18x MISSING)
  men:DASHUSDT_LONG $25/59 -> no clamp -> 0.15x -> 0.001079 dust order placed,
    never filled -> MAKER_FAILED_SUPPRESS_WEBHOOK (6x MISSING)
  men:VETUSDT_LONG 2830 coins x1.28 mtf -> 0.15x rule -> min() picks $32 cap
    -> 3626 coins -> FILLED (proves the min()-with-USD-cap leg)
  ang:QNTUSDT_LONG 0.0937 < step 0.1 -> MAKER_ZERO_QTY (pure lot-step, no acct rule)

SCOPE: crypto MODE opens/augments only. Tradier passes through (live stocks
sizing is whole-share with its own TRC_* caps; vec _size_qty already floors).
Reduces are NOT covered here — vec has 7 partial-reduce sites; see
NEEDS-OPERATOR-DECISION (DH parity) for the follow-up.

GATING (BACKTEST_BIBLE section 14.1/17.3/21): live sizing zeroes sim sizing,
so this twin is OFF by default (SMALL_ACCOUNT_SIZER_ENABLED=False,
VEC_ACCOUNT_KEY=""). Enable ONLY for per-account forward parity runs, with
cap fields set to live's EFFECTIVE config for the window (market mode!).
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from functools import lru_cache
from typing import Any

SMALL_ACCOUNTS = ("inf", "fin", "men")


def _bare(symbol: str) -> str:
    s = str(symbol or "")
    for sfx in ("_LONG", "_SHORT"):
        if s.endswith(sfx):
            return s[: -len(sfx)]
    return s


@lru_cache(maxsize=1)
def _step_map(path: str = "") -> dict:
    """Binance lot-step per symbol — the SAME file live reads."""
    if not path:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "symbol_configs.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def step_size_for(symbol: str) -> float | None:
    """Lot step for a symbol, with live's USDC->USDT fallback. None if unknown."""
    symbol = _bare(symbol)
    m = _step_map()
    conf = m.get(symbol)
    if not conf and symbol.endswith("USDC"):
        conf = m.get(symbol.replace("USDC", "USDT"))
    if not conf:
        return None
    try:
        step = float(conf.get("step_size") or conf.get("stepSize") or 0)
        return step if step > 0 else None
    except (TypeError, ValueError):
        return None


def floor_to_lot_step(qty_coins: float, symbol: str) -> float:
    """Live place_maker_order lot floor: (qty // step) * step. Zero stays zero.

    Unknown symbol -> fail OPEN (return qty unchanged): live logs symbol-missing
    as MAKER_CRITICAL_FAIL, but vec must not invent blocks for unlisted syms.
    """
    if qty_coins <= 0:
        return 0.0
    step = step_size_for(symbol)
    if step is None:
        return float(qty_coins)
    try:
        floored = (Decimal(str(float(qty_coins))) // Decimal(str(step))) * Decimal(str(step))
        return float(floored)
    except Exception:
        return float(qty_coins)


def max_order_usd_for(account: str, cfg: Any) -> float:
    """Live per-account MAX_ORDER selection (ez_manage.py:~33975)."""
    if account == "men":
        return float(getattr(cfg, "MAX_ORDER_VALUE_MEN", 280.0) or 280.0)
    if account == "fin":
        return float(getattr(cfg, "MAX_ORDER_VALUE_FIN", 280.0) or 280.0)
    return float(getattr(cfg, "MAX_ORDER_VALUE", 180.0) or 180.0)


def apply_live_sizing(
    qty_coins: float,
    price: float,
    symbol: str,
    account: str,
    cfg: Any,
    *,
    is_hedge: bool = False,
    is_scalp_v3: bool = False,
) -> tuple[float, str]:
    """Apply the full live crypto sizing chain to an OPEN/AUGMENT coin qty.

    Returns (final_qty_coins, blocked_token). blocked_token is "" when the
    order would be placed, else the live token:
      MAKER_ZERO_QTY            lot floor -> 0 (order not placed)
      MAKER_DUST_NO_FILL_RISK   lot floor -> dust below $5 min-notional
                                (live places it but it never fills; men DASH
                                0.001 = $0.06 sat unfilled -> suppress)
    Steps mirror ez_manage.py order: MAX_ORDER clamp -> 0.15x rule ->
    ORDER_SIZE_CAP -> lot floor.
    """
    if price <= 0 or qty_coins <= 0:
        return 0.0, "MAKER_ZERO_QTY"
    symbol = _bare(symbol)
    # 1. per-account MAX_ORDER clamp
    cap = max_order_usd_for(account, cfg)
    if cap > 0 and qty_coins * price > cap:
        qty_coins = cap / price
    # 2. inf/fin/men 0.15x rule (exact live formula incl. unit soup)
    if account in SMALL_ACCOUNTS and not is_hedge and not is_scalp_v3:
        start = float(getattr(cfg, "START_POSITION_SIZE", 28.0) or 28.0)
        qty_coins = min(0.15 * qty_coins, 2.0 * start) / price
    # 3. ORDER_SIZE_CAP
    ocap = float(getattr(cfg, "MAX_ORDER_VALUE", 180.0) or 180.0)
    if ocap > 0 and abs(qty_coins * price) > ocap:
        qty_coins = ocap / price
    # 4. lot-step floor
    floored = floor_to_lot_step(qty_coins, symbol)
    if floored <= 0:
        return 0.0, "MAKER_ZERO_QTY"
    min_notional = float(getattr(cfg, "SMALL_ACCOUNT_MIN_NOTIONAL_USD", 5.0) or 5.0)
    if min_notional > 0 and floored * price < min_notional:
        # live places the dust order but the exchange min-notional rejects it
        # (or it sits unfilled); empirically men DASH 0.001 ($0.06) went
        # MISSING 6/6 via MAKER_FAILED_SUPPRESS on Oct 4.
        return 0.0, "MAKER_DUST_NO_FILL_RISK"
    return floored, ""


def size_open_qty(
    dollar_size: float,
    price: float,
    symbol: str,
    cfg: Any,
) -> tuple[float, str]:
    """Engine entry point: vec dollar notional -> placeable coin qty.

    Honors the BIBLE section 17.3 gate: returns the legacy continuous qty
    untouched unless SMALL_ACCOUNT_SIZER_ENABLED and VEC_ACCOUNT_KEY are set.
    Tradier MODE passes through (whole-share floor lives in _size_qty).
    """
    qty = dollar_size / price if price > 0 else 0.0
    if str(getattr(cfg, "MODE", "crypto") or "crypto") == "tradier":
        return qty, ""
    if not bool(getattr(cfg, "SMALL_ACCOUNT_SIZER_ENABLED", False)):
        return qty, ""
    account = str(getattr(cfg, "VEC_ACCOUNT_KEY", "") or "").strip().lower()
    if not account:
        return qty, ""
    return apply_live_sizing(qty, price, symbol, account, cfg)
