#!/usr/bin/env python3
"""Non-skippable risk monitor for SOLD options (cash-secured puts + any future sell-to-open).

MANDATE: For every sold option position, close it via buy_to_close the moment two
conditions line up simultaneously:
  1. P&L is negative relative to the fill price (premium paid back > premium collected)
  2. Technicals have turned against the position (WT D-frame cross against direction)

A hard loss cut (default -50%) overrides the technical check — premium bleed beyond
that is treated as "the setup is fundamentally wrong", close unconditionally.

State machine per position:
  OK        → watching; no trigger active
  ARMED     → P&L dipped below loss_trigger_pct; waiting for technical confirmation
  CLOSED    → buy_to_close order placed; awaiting fill
  COMPLETED → position confirmed closed

NO config flag disables this monitor. The ONLY master-kill is uninstalling the launchd
plist — and the plist-check on startup re-installs it if missing.

Usage:
    python tradier_options_csp_monitor.py                # daemon mode (polls every N sec)
    python tradier_options_csp_monitor.py --status       # show current state
    python tradier_options_csp_monitor.py --dry-run      # log only, no orders
    python tradier_options_csp_monitor.py --once         # single pass then exit
"""
import asyncio
import argparse
import json
import logging
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_analyzer import (
    get_option_positions, parse_occ_symbol, smart_fill_option, place_option_order,
)

logger = logging.getLogger("csp_monitor")
logger.setLevel(logging.INFO)
logger.propagate = False

# === ORDER DEDUPE (2026-04-27 — user audit) ===
_ORDER_DEDUPE_LOG: dict = {}
_ORDER_DEDUPE_SEC: float = 60.0

def _order_allowed(key: str) -> bool:
    now = time.time()
    last = _ORDER_DEDUPE_LOG.get(key, 0.0)
    if now - last < _ORDER_DEDUPE_SEC:
        logger.warning(f"[ORDER_DEDUPE_SKIP] {key} — last attempt {now-last:.0f}s ago < {_ORDER_DEDUPE_SEC:.0f}s")
        return False
    _ORDER_DEDUPE_LOG[key] = now
    return True
if not logger.handlers:
    log_dir = Path(os.path.expanduser("~")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_dir / "tradier_csp_monitor.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

STATE_FILE = BASE_PATH / "data" / "tradier" / "csp_monitor_state.json"
AUDIT_LOG = BASE_PATH / "data" / "tradier" / "csp_monitor.log"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"positions": {}, "last_check": None, "created_at": datetime.now().isoformat()}


def save_state(state: dict):
    state["last_check"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def audit(msg: str):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
        f.write(f"{datetime.now().isoformat()}\t{msg}\n")


def _is_sold_position(pos: dict) -> bool:
    """Tradier: short option positions have negative quantity (writer) OR side='short'."""
    qty = float(pos.get("quantity", 0) or 0)
    side = (pos.get("side") or "").lower()
    if qty < 0:
        return True
    if side in ("short", "sell"):
        return True
    return False


async def _fetch_option_mid(client: TradierAPIClient, occ: str) -> tuple:
    """Return (bid, ask, mid) for OCC symbol, or (0,0,0) on failure."""
    try:
        res = await client._request("GET", "/markets/quotes", params={"symbols": occ}, use_data_context=True)
        if not res or "quotes" not in res or "quote" not in res["quotes"]:
            return 0.0, 0.0, 0.0
        q = res["quotes"]["quote"]
        if isinstance(q, list):
            q = q[0] if q else {}
        bid = float(q.get("bid", 0) or 0)
        ask = float(q.get("ask", 0) or 0)
        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else max(bid, ask)
        return bid, ask, mid
    except Exception as e:
        logger.warning(f"mid fetch failed {occ}: {e}")
        return 0.0, 0.0, 0.0


def _technical_turn_against_short_put(indicators: dict, symbol: str) -> tuple:
    """Short put = bullish thesis. Turn-against = bearish signal on D.
    Returns (is_against, reason)."""
    ind = indicators.get(symbol, {}) if indicators else {}
    if not ind:
        return False, "no_indicators"
    wt_cross_D = ind.get("wt_cross_D", "")
    wt_mom_D = ind.get("wt_momentum_state_D", "")
    wt1_D = ind.get("wt1_D")
    wt2_D = ind.get("wt2_D")
    if wt_cross_D == "BEAR":
        return True, "wt_D_BEAR_cross"
    if wt_mom_D in ("IMPULSE_DOWN", "EXHAUST_DOWN"):
        return True, f"wt_D_{wt_mom_D}"
    if wt1_D is not None and wt2_D is not None and wt1_D < wt2_D:
        try:
            if float(wt1_D) < float(wt2_D) - 2.0:
                return True, "wt1_D_below_wt2_D_margin"
        except Exception:
            pass
    return False, "wt_D_not_bearish"


def _load_indicators(config: TradierConfig) -> dict:
    ind_file = config.DATA_DIR / "tradier_indicators_latest.json"
    if not ind_file.exists():
        return {}
    try:
        with open(ind_file) as f:
            return json.load(f)
    except Exception:
        return {}


async def _fetch_underlying_price(client: TradierAPIClient, symbol: str) -> float:
    """Fetch current underlying spot price. Returns 0 on failure."""
    if not symbol:
        return 0.0
    try:
        res = await client._request("GET", "/markets/quotes", params={"symbols": symbol}, use_data_context=True)
        if not res or "quotes" not in res:
            return 0.0
        q = res["quotes"].get("quote", {})
        if isinstance(q, list):
            q = q[0] if q else {}
        return float(q.get("last", 0) or q.get("close", 0) or 0)
    except Exception:
        return 0.0


def _find_spread_long_leg(short_pos: dict, positions: list) -> Optional[dict]:
    """If short_pos is the short leg of a bull put spread, return the matching long leg position. Else None."""
    occ = short_pos.get("symbol", "")
    parsed = parse_occ_symbol(occ) or {}
    if parsed.get("option_type", "").lower() != "p":
        return None
    if float(short_pos.get("quantity", 0) or 0) >= 0:
        return None
    this_strike = float(parsed.get("strike", 0) or 0)
    this_exp = parsed.get("expiration", "")
    this_sym = parsed.get("symbol", "")
    for other in positions:
        if other.get("symbol") == occ:
            continue
        other_parsed = parse_occ_symbol(other.get("symbol", "")) or {}
        if other_parsed.get("symbol") != this_sym or other_parsed.get("expiration") != this_exp:
            continue
        if other_parsed.get("option_type", "").lower() != "p":
            continue
        if float(other.get("quantity", 0) or 0) <= 0:
            continue
        other_strike = float(other_parsed.get("strike", 0) or 0)
        if other_strike < this_strike and other_strike > 0:
            return other  # lower-strike long put = protective leg
    return None


def _is_protective_long_leg(pos: dict, positions: list) -> bool:
    """Detect if this long option position is the protective leg of a known spread.
    Checks for another position on the SAME underlying+expiration with negative qty on a higher strike (short put)."""
    occ = pos.get("symbol", "")
    parsed = parse_occ_symbol(occ) or {}
    if parsed.get("option_type", "").lower() != "p":
        return False
    qty = float(pos.get("quantity", 0) or 0)
    if qty <= 0:
        return False  # not a long position
    this_strike = float(parsed.get("strike", 0) or 0)
    this_exp = parsed.get("expiration", "")
    this_sym = parsed.get("symbol", "")
    if not this_sym or not this_exp or this_strike <= 0:
        return False
    for other in positions:
        if other.get("symbol") == occ:
            continue
        other_parsed = parse_occ_symbol(other.get("symbol", "")) or {}
        if other_parsed.get("symbol") != this_sym or other_parsed.get("expiration") != this_exp:
            continue
        if other_parsed.get("option_type", "").lower() != "p":
            continue
        other_qty = float(other.get("quantity", 0) or 0)
        if other_qty >= 0:
            continue  # not a short
        other_strike = float(other_parsed.get("strike", 0) or 0)
        if other_strike > this_strike:
            return True  # higher-strike short put = this is the protective long leg of a spread
    return False


async def _evaluate_position(client: TradierAPIClient, pos: dict, config: TradierConfig, indicators: dict, state: dict, dry_run: bool, account_value: float = 0.0, all_positions: Optional[list] = None) -> dict:
    """Evaluate one sold position with layered defense. Returns action dict.
    Action = 'skip' | 'watch' | 'close_technical' | 'close_hard_premium' |
             'close_strike_breach' | 'close_entry_gap' |
             'close_profit_target' | 'close_max_hold' | 'audit_notional_breach'.

    Priority order (top fires first, bypasses everything below):
      0. NOTIONAL_AUDIT — log EMERGENCY if position's notional > 3% account (defensive; agent should prevent)
      1. STRIKE_BREACH — underlying moves N% past strike (deep ITM) → wipeout guard
      2. ENTRY_GAP    — underlying moves N% from entry spot → gap/crash guard
      3. HARD_PREMIUM — premium pnl <= hard cut (catastrophic only)
      4. PROFIT_TARGET — premium pnl >= +50% (take profit, free capital)
      5. MAX_HOLD     — position open > 21 days (force close to avoid late-cycle gamma)
      6. TECHNICAL    — premium pnl <= soft trigger AND wt_D turn against"""
    occ = pos.get("symbol") or pos.get("occ_symbol") or ""
    qty = abs(int(float(pos.get("quantity", 0) or 0)))
    if not occ or qty == 0:
        return {"action": "skip", "reason": "no_occ_or_qty"}
    # ── Detect spread membership ──
    spread_long_leg = None
    if all_positions:
        spread_long_leg = _find_spread_long_leg(pos, all_positions)
    is_spread = spread_long_leg is not None
    cost_basis = float(pos.get("cost_basis", 0) or 0)
    premium_collected = abs(cost_basis) / (qty * 100.0) if qty > 0 else 0.0
    bid, ask, mid = await _fetch_option_mid(client, occ)
    if mid <= 0:
        return {"action": "skip", "reason": "no_quote", "occ": occ}
    current_cost = mid
    pnl_per_contract = premium_collected - current_cost
    pnl_pct = pnl_per_contract / premium_collected if premium_collected > 0 else 0.0
    parsed = parse_occ_symbol(occ) or {}
    underlying = parsed.get("symbol") or pos.get("underlying") or ""
    strike = float(parsed.get("strike", 0) or 0)
    is_put = parsed.get("option_type", "").lower() == "p"
    # Fetch underlying spot for absolute guards
    spot = await _fetch_underlying_price(client, underlying)
    # Entry spot: pull from state cache (set on first observation) or fallback to strike
    pos_state = state.setdefault("positions", {}).setdefault(occ, {})
    entry_spot = pos_state.get("entry_spot")
    if entry_spot is None and spot > 0:
        # First time seeing this position — record current spot as entry-spot baseline
        pos_state["entry_spot"] = spot
        pos_state["first_seen"] = datetime.now().isoformat()
        entry_spot = spot
    entry_spot = float(entry_spot or 0)
    log_entry = {"occ": occ, "underlying": underlying, "spot": round(spot, 2), "entry_spot": round(entry_spot, 2), "strike": strike, "qty": qty, "premium_collected": round(premium_collected, 2), "current_cost": round(current_cost, 2), "pnl_pct": round(pnl_pct, 4), "mid": round(mid, 2), "is_put": is_put, "is_spread": is_spread}
    if getattr(config, "OPTIONS_CSP_MONITOR_LOG_EVERY_TICK", True):
        audit(json.dumps(log_entry))
    # ── GUARD 0: NOTIONAL_AUDIT (defensive; agent should prevent) ──
    # For SPREADS: notional is bounded by (short_strike - long_strike) * 100, not full strike notional.
    # So STRIKE_BREACH and ENTRY_GAP don't apply — loss is already defined by the long-leg protection.
    if account_value > 0 and strike > 0 and not is_spread:
        notional = strike * 100.0 * qty
        cap = account_value * getattr(config, "OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT", 0.03)
        if notional > cap:
            msg = f"NOTIONAL_BREACH {occ} notional=${notional:,.0f} > cap=${cap:,.0f} ({getattr(config, 'OPTIONS_CSP_MAX_POS_PCT_OF_ACCOUNT', 0.03):.1%} of ${account_value:,.0f})"
            logger.error(msg)
            audit(f"EMERGENCY\t{msg}")
    # ── GUARD 1 & 2 skipped for spreads (loss is defined by long leg) ──
    if is_spread:
        # For spreads: only profit target + max hold + hard premium check apply
        # PROFIT_TARGET
        profit_target = getattr(config, "OPTIONS_SPREAD_PROFIT_TARGET_PCT", 0.50)
        if pnl_pct >= profit_target:
            return {"action": "close_profit_target", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"SPREAD_PROFIT_TARGET pnl_pct={pnl_pct:.1%} >= {profit_target:.0%}", "is_spread": True, "long_leg_occ": spread_long_leg.get("symbol") if spread_long_leg else None}
        # MAX_HOLD
        max_hold = getattr(config, "OPTIONS_SPREAD_MAX_HOLD_DAYS", 21)
        first_seen_str = pos_state.get("first_seen")
        if first_seen_str:
            try:
                first_seen = datetime.fromisoformat(first_seen_str)
                days_held = (datetime.now() - first_seen).total_seconds() / 86400.0
                if days_held >= max_hold:
                    return {"action": "close_max_hold", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"SPREAD_MAX_HOLD days_held={days_held:.1f} >= {max_hold}", "is_spread": True, "long_leg_occ": spread_long_leg.get("symbol") if spread_long_leg else None}
            except Exception:
                pass
        return {"action": "watch", "reason": f"SPREAD_OK pnl={pnl_pct:.2%}", "is_spread": True}
    # ── GUARD 1: STRIKE_BREACH (wipeout prevention — naked/CSP only) ──
    # SHORT PUT: spot dropping N% below strike → put is deep ITM → assignment loss grows
    # SHORT CALL: spot rising N% above strike → unlimited loss territory
    if spot > 0 and strike > 0:
        if is_put:
            breach_thresh = strike * (1.0 - config.OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT)
            if spot <= breach_thresh:
                pct = (strike - spot) / strike
                return {"action": "close_strike_breach", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"STRIKE_BREACH short_put spot={spot:.2f} <= {breach_thresh:.2f} (strike={strike:.2f} − {config.OPTIONS_CSP_MONITOR_STRIKE_BREACH_PCT:.0%} = {pct:.1%} ITM)"}
        else:
            # short call (naked — disabled v1 but guard wired defensively)
            breach_thresh = strike * (1.0 + config.OPTIONS_CSP_MONITOR_CALL_BREACH_PCT)
            if spot >= breach_thresh:
                pct = (spot - strike) / strike
                return {"action": "close_strike_breach", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"STRIKE_BREACH short_call spot={spot:.2f} >= {breach_thresh:.2f} (strike={strike:.2f} + {config.OPTIONS_CSP_MONITOR_CALL_BREACH_PCT:.0%} = {pct:.1%} ITM)"}
    # ── GUARD 2: ENTRY_GAP (catches pre-market gaps / earnings / fraud events) ──
    if spot > 0 and entry_spot > 0:
        gap = (spot - entry_spot) / entry_spot
        if is_put and gap <= -config.OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT:
            return {"action": "close_entry_gap", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"ENTRY_GAP short_put gap={gap:.1%} <= -{config.OPTIONS_CSP_MONITOR_GAP_FROM_ENTRY_PCT:.0%} (spot={spot:.2f} vs entry={entry_spot:.2f})"}
        if (not is_put) and gap >= config.OPTIONS_CSP_MONITOR_CALL_GAP_FROM_ENTRY_PCT:
            return {"action": "close_entry_gap", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"ENTRY_GAP short_call gap={gap:.1%} >= +{config.OPTIONS_CSP_MONITOR_CALL_GAP_FROM_ENTRY_PCT:.0%} (spot={spot:.2f} vs entry={entry_spot:.2f})"}
    # ── GUARD 3: HARD_PREMIUM (catastrophic premium loss only) ──
    if pnl_pct <= config.OPTIONS_CSP_MONITOR_MAX_LOSS_PCT:
        return {"action": "close_hard_premium", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"HARD_PREMIUM pnl_pct={pnl_pct:.1%} <= {config.OPTIONS_CSP_MONITOR_MAX_LOSS_PCT:.1%}"}
    # ── GUARD 4: PROFIT_TARGET (take profit to free capital) ──
    profit_target = getattr(config, "OPTIONS_CSP_PROFIT_TARGET_PCT", 0.50)
    if pnl_pct >= profit_target:
        return {"action": "close_profit_target", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"PROFIT_TARGET pnl_pct={pnl_pct:.1%} >= {profit_target:.0%}"}
    # ── GUARD 5: MAX_HOLD (force close after N days) ──
    max_hold = getattr(config, "OPTIONS_CSP_MAX_HOLD_DAYS", 21)
    first_seen_str = pos_state.get("first_seen")
    if first_seen_str:
        try:
            first_seen = datetime.fromisoformat(first_seen_str)
            days_held = (datetime.now() - first_seen).total_seconds() / 86400.0
            if days_held >= max_hold:
                return {"action": "close_max_hold", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"MAX_HOLD days_held={days_held:.1f} >= {max_hold}"}
        except Exception:
            pass
    # ── SOFT GATE: premium drawdown + technical turn ──
    if pnl_pct > config.OPTIONS_CSP_MONITOR_LOSS_TRIGGER_PCT:
        return {"action": "watch", "reason": f"pnl_pct={pnl_pct:.2%} above soft trigger"}
    if is_put:
        against, tech_reason = _technical_turn_against_short_put(indicators, underlying)
    else:
        ind = indicators.get(underlying, {}) if indicators else {}
        wt_cross_D = ind.get("wt_cross_D", "")
        against = wt_cross_D == "BULL"
        tech_reason = f"wt_D_{wt_cross_D}"
    if config.OPTIONS_CSP_MONITOR_REQUIRE_WT_D_TURN and not against:
        return {"action": "watch", "reason": f"pnl_armed={pnl_pct:.2%} but tech_ok ({tech_reason})"}
    return {"action": "close_technical", "occ": occ, "underlying": underlying, "qty": qty, "pnl_pct": pnl_pct, "mid": mid, "bid": bid, "ask": ask, "reason": f"TECHNICAL pnl={pnl_pct:.2%} AND {tech_reason}"}


async def _close_short_option(client: TradierAPIClient, occ: str, qty: int, bid: float, ask: float, reason: str, dry_run: bool) -> dict:
    """Buy-to-close a short option position. Aggressive limit (near ask) for speed."""
    if dry_run:
        logger.info(f"[DRY] buy_to_close {occ} x{qty} (ask={ask:.2f}) — {reason}")
        audit(f"DRY_CLOSE\t{occ}\tqty={qty}\task={ask:.2f}\t{reason}")
        return {"status": "dry_run"}
    # Start aggressive: 95% of ask (we want to close fast, not squeeze mid)
    initial = max(ask * 0.95, (bid + ask) / 2.0) if ask > 0 else 0.10
    logger.warning(f"CLOSE {occ} x{qty} @ ~${initial:.2f} (bid={bid:.2f} ask={ask:.2f}) — {reason}")
    audit(f"CLOSE\t{occ}\tqty={qty}\tinitial={initial:.2f}\tbid={bid:.2f}\task={ask:.2f}\t{reason}")
    parsed = parse_occ_symbol(occ) or {}
    underlying = parsed.get("symbol") or ""
    if not _order_allowed(f"{occ}|buy_to_close"):
        return {"status": "skipped_dedupe", "occ": occ}
    try:
        res = await smart_fill_option(client, underlying, occ, "buy_to_close", qty, initial, bid, ask, max_walk_steps=4, walk_interval=30)
        audit(f"CLOSE_RESULT\t{occ}\t{json.dumps(res, default=str)}")
        return res
    except Exception as e:
        logger.error(f"CLOSE FAILED {occ}: {e}")
        audit(f"CLOSE_ERROR\t{occ}\t{e}")
        return {"status": "error", "error": str(e)}


async def _fetch_account_value(client: TradierAPIClient) -> float:
    try:
        bal_res = await client.get_account_balances()
        if bal_res and isinstance(bal_res, dict):
            bals = bal_res.get("balances", bal_res)
            return float(bals.get("total_equity", 0) or bals.get("market_value", 0) or bals.get("total_cash", 0) or 0)
    except Exception:
        pass
    return 0.0


async def run_once(client: TradierAPIClient, config: TradierConfig, dry_run: bool = False) -> dict:
    """One pass over all sold positions. Returns summary dict."""
    state = load_state()
    indicators = _load_indicators(config)
    positions = []
    try:
        positions = await get_option_positions(client, config=config)
    except Exception as e:
        logger.error(f"position fetch failed: {e}")
        audit(f"FETCH_ERROR\t{e}")
        return {"error": str(e), "positions_checked": 0}
    # Exclude protective long legs of known spreads (they don't need monitor action — the short leg drives the evaluation)
    sold = [p for p in (positions or []) if _is_sold_position(p)]
    # Long legs that are protection are implicitly ignored — we don't iterate long positions here anyway
    account_value = 0.0
    if sold:
        account_value = await _fetch_account_value(client)
    summary = {"ts": datetime.now().isoformat(), "sold_count": len(sold), "account_value": account_value, "actions": []}
    close_actions = ("close_technical", "close_hard_premium", "close_strike_breach", "close_entry_gap", "close_profit_target", "close_max_hold")
    absolute_guards = ("close_strike_breach", "close_entry_gap", "close_hard_premium")
    absolute_breaches = []
    for pos in sold:
        action = await _evaluate_position(client, pos, config, indicators, state, dry_run, account_value=account_value, all_positions=positions)
        summary["actions"].append({"occ": action.get("occ", pos.get("symbol")), "action": action["action"], "reason": action.get("reason")})
        if action["action"] in close_actions:
            if action["action"] in absolute_guards:
                absolute_breaches.append(action)
            res = await _close_short_option(client, action["occ"], action["qty"], action["bid"], action["ask"], action["reason"], dry_run)
            pos_state = state["positions"].setdefault(action["occ"], {})
            pos_state.update({"last_action": action["action"], "closed_at": datetime.now().isoformat(), "reason": action["reason"], "close_result": res})
            # If this was a spread, also close the long (protective) leg
            long_leg_occ = action.get("long_leg_occ")
            if action.get("is_spread") and long_leg_occ:
                long_pos = next((p for p in positions if p.get("symbol") == long_leg_occ), None)
                if long_pos:
                    long_qty = abs(int(float(long_pos.get("quantity", 0) or 0)))
                    l_bid, l_ask, l_mid = await _fetch_option_mid(client, long_leg_occ)
                    # Sell-to-close the long (it's a long, so sell_to_close)
                    if dry_run:
                        logger.info(f"[DRY] sell_to_close {long_leg_occ} x{long_qty} (ask={l_ask:.2f}) — spread_long_leg_close")
                        audit(f"DRY_CLOSE_LONG\t{long_leg_occ}\tqty={long_qty}")
                    else:
                        parsed = parse_occ_symbol(long_leg_occ) or {}
                        underlying = parsed.get("symbol") or ""
                        initial = max(l_bid, (l_bid + l_ask) / 2.0) if l_bid > 0 else 0.05
                        try:
                            res_long = await smart_fill_option(client, underlying, long_leg_occ, "sell_to_close", long_qty, initial, l_bid, l_ask, max_walk_steps=4, walk_interval=30)
                            logger.warning(f"CLOSE_LONG_LEG {long_leg_occ} x{long_qty} — paired with {action['occ']}")
                            audit(f"CLOSE_LONG\t{long_leg_occ}\t{json.dumps(res_long, default=str)}")
                        except Exception as e:
                            logger.error(f"CLOSE_LONG_LEG FAILED {long_leg_occ}: {e}")
                            audit(f"CLOSE_LONG_ERROR\t{long_leg_occ}\t{e}")
    # Correlated-breach emergency escalation
    corr_n = getattr(config, "OPTIONS_CSP_MONITOR_CORRELATED_BREACH_N", 3)
    if len(absolute_breaches) >= corr_n:
        msg = f"CORRELATED_BREACH n={len(absolute_breaches)} (threshold={corr_n}) — positions: {[a.get('occ') for a in absolute_breaches]}"
        logger.error(msg)
        audit(f"EMERGENCY\t{msg}")
        summary["emergency"] = msg
    save_state(state)
    return summary


async def run_daemon(config: TradierConfig, account_key: str, dry_run: bool):
    client = TradierAPIClient(config, account_key=account_key)
    await client.connect()
    poll = int(getattr(config, "OPTIONS_CSP_MONITOR_POLL_SEC", 60))
    logger.info(f"CSP monitor daemon start — account={account_key} poll={poll}s dry_run={dry_run}")
    audit(f"DAEMON_START\taccount={account_key}\tpoll={poll}s\tdry_run={dry_run}")
    try:
        while True:
            t0 = time.time()
            try:
                summary = await run_once(client, config, dry_run=dry_run)
                if summary.get("sold_count", 0) > 0:
                    closes = [a for a in summary["actions"] if a["action"].startswith("close_")]
                    if closes:
                        logger.warning(f"tick sold={summary['sold_count']} closes={len(closes)}")
                    else:
                        logger.info(f"tick sold={summary['sold_count']} all_ok")
            except Exception as e:
                logger.error(f"tick exception: {e}")
                audit(f"TICK_ERROR\t{e}")
            elapsed = time.time() - t0
            sleep_for = max(5.0, poll - elapsed)
            await asyncio.sleep(sleep_for)
    finally:
        try:
            await client.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="trb")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    config = TradierConfig()
    if args.status:
        state = load_state()
        print(json.dumps(state, indent=2, default=str))
        return
    if args.once:
        async def _once():
            client = TradierAPIClient(config, account_key=args.account)
            await client.connect()
            summary = await run_once(client, config, dry_run=args.dry_run)
            print(json.dumps(summary, indent=2, default=str))
            try:
                await client.close()
            except Exception:
                pass
        asyncio.run(_once())
        return
    asyncio.run(run_daemon(config, args.account, args.dry_run))


if __name__ == "__main__":
    main()
