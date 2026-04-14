#!/usr/bin/env python3
"""BTC Crash Safety Net — auto-short IBIT+MSTR when BTC breaks below trigger.

Monitors BTC price via Binance API + Redis. When BTC < TRIGGER:
  1. SHORT IBIT + MSTR via Tradier (immediate crash protection)
  2. Monitor MSTR/IBIT put prices
  3. When a good put is found → close short + buy put (rotate to defined risk)

State machine:
  ARMED    → watching BTC price, no positions
  SHORTED  → shorts are open, scanning for puts
  ROTATED  → shorts closed, puts acquired — done
  DISABLED → manually disabled or already triggered

Usage:
    python btc_crash_safety_net.py                    # Run daemon (checks every 30s)
    python btc_crash_safety_net.py --status            # Show current state
    python btc_crash_safety_net.py --disable           # Disable the trigger
    python btc_crash_safety_net.py --arm               # Re-arm the trigger
    python btc_crash_safety_net.py --trigger 60000     # Change trigger price
"""
import asyncio
import json
import logging
import os
import platform
import sys
import argparse
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))

logger = logging.getLogger("btc_safety_net")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    log_dir = Path(os.path.expanduser("~")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_dir / "btc_crash_safety_net.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

# ── Configuration ────────────────────────────────────────────────────────────
DEFAULT_TRIGGER = 64680.0
SHORT_BUDGET_IBIT = 2000.0   # $ to short in IBIT
SHORT_BUDGET_MSTR = 2000.0   # $ to short in MSTR
PUT_ROTATION_MAX_SPREAD_PCT = 8.0  # max bid/ask spread % to accept for put rotation
PUT_TARGET_DTE = (60, 120)   # preferred DTE range for replacement puts
PUT_MAX_IV_PREMIUM = 0.80    # max IV vs surface (0.80 = 80% of surface, i.e. 20% cheap)
CHECK_INTERVAL = 30          # seconds between BTC price checks
STATE_FILE = BASE_PATH / "data" / "tradier" / "btc_safety_net_state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"status": "ARMED", "trigger_price": DEFAULT_TRIGGER, "btc_price_at_trigger": None, "shorted_at": None, "ibit_order_id": None, "mstr_order_id": None, "ibit_short_qty": 0, "mstr_short_qty": 0, "ibit_short_price": 0, "mstr_short_price": 0, "put_occ": None, "put_order_id": None, "rotated_at": None, "last_check": None, "created_at": datetime.now().isoformat()}


def save_state(state: dict):
    state["last_check"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def get_btc_price() -> float:
    """Get BTC price — try Redis first (fastest), then Binance API."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        raw = r.get("latest_kline:BTCUSDC")
        if raw:
            data = json.loads(raw)
            price = float(data.get("close", 0))
            if price > 0:
                return price
    except Exception:
        pass
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDC"
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        return float(data["price"])
    except Exception:
        pass
    return 0.0


def is_market_open() -> bool:
    """Check if US stock market is open (roughly)."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    return 13 <= now.hour < 20 or (now.hour == 20 and now.minute == 0)


async def execute_shorts(state: dict) -> dict:
    """Short IBIT and MSTR via Tradier."""
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    config = TradierConfig()
    client = TradierAPIClient(config, account_key="trb")
    await client.connect()
    try:
        # Get current prices
        quotes = await client.get_quotes(["IBIT", "MSTR"])
        ibit_price = quotes.get("IBIT", {}).get("last", 0) or quotes.get("IBIT", {}).get("bid", 0) or 0
        mstr_price = quotes.get("MSTR", {}).get("last", 0) or quotes.get("MSTR", {}).get("bid", 0) or 0
        if ibit_price <= 0 or mstr_price <= 0:
            logger.error(f"Cannot get prices: IBIT={ibit_price}, MSTR={mstr_price}")
            return state
        ibit_qty = max(1, int(SHORT_BUDGET_IBIT / ibit_price))
        mstr_qty = max(1, int(SHORT_BUDGET_MSTR / mstr_price))
        logger.warning(f"BTC CRASH TRIGGER — shorting IBIT x{ibit_qty} @ ~${ibit_price:.2f}, MSTR x{mstr_qty} @ ~${mstr_price:.2f}")
        # Short IBIT
        ibit_res = await client.place_order("trb", "IBIT", "sell_short", ibit_qty, order_type="market")
        ibit_order_id = None
        if ibit_res and "order" in ibit_res:
            ibit_order_id = ibit_res["order"].get("id")
            logger.info(f"IBIT short placed — order {ibit_order_id}")
        elif ibit_res and "errors" in ibit_res:
            logger.error(f"IBIT short FAILED: {ibit_res['errors']}")
        # Short MSTR
        mstr_res = await client.place_order("trb", "MSTR", "sell_short", mstr_qty, order_type="market")
        mstr_order_id = None
        if mstr_res and "order" in mstr_res:
            mstr_order_id = mstr_res["order"].get("id")
            logger.info(f"MSTR short placed — order {mstr_order_id}")
        elif mstr_res and "errors" in mstr_res:
            logger.error(f"MSTR short FAILED: {mstr_res['errors']}")
        state["status"] = "SHORTED"
        state["shorted_at"] = datetime.now().isoformat()
        state["ibit_order_id"] = ibit_order_id
        state["mstr_order_id"] = mstr_order_id
        state["ibit_short_qty"] = ibit_qty
        state["mstr_short_qty"] = mstr_qty
        state["ibit_short_price"] = ibit_price
        state["mstr_short_price"] = mstr_price
    finally:
        await client.close()
    return state


async def scan_for_put_rotation(state: dict) -> dict:
    """Look for good MSTR/IBIT puts to rotate shorts into."""
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    from tradier_options_analyzer import fetch_expirations, fetch_option_chain, place_option_order, build_occ_symbol
    config = TradierConfig()
    client = TradierAPIClient(config, account_key="trb")
    await client.connect()
    try:
        # Prefer MSTR puts (higher beta = more crash leverage)
        for symbol in ["MSTR", "IBIT"]:
            expirations = await fetch_expirations(client, symbol)
            if not expirations:
                continue
            now = datetime.now()
            target_exps = [(e, (datetime.strptime(e, "%Y-%m-%d") - now).days) for e in expirations if PUT_TARGET_DTE[0] <= (datetime.strptime(e, "%Y-%m-%d") - now).days <= PUT_TARGET_DTE[1]]
            if not target_exps:
                continue
            exp, dte = target_exps[0]
            chain = await fetch_option_chain(client, symbol, exp)
            if not chain:
                continue
            # Get current stock price
            quote = await client.get_quote(symbol)
            stock_price = quote.get("last", 0) or 0
            if stock_price <= 0:
                continue
            # Find ATM or slightly ITM put
            best_put = None
            for opt in chain:
                if opt.get("option_type") != "put":
                    continue
                strike = opt.get("strike", 0)
                bid = opt.get("bid", 0) or 0
                ask = opt.get("ask", 0) or 0
                if bid <= 0 or ask <= 0:
                    continue
                spread_pct = (ask - bid) / ((bid + ask) / 2) * 100
                if spread_pct > PUT_ROTATION_MAX_SPREAD_PCT:
                    continue
                # Want ATM or slightly ITM (strike >= stock price * 0.97)
                if strike < stock_price * 0.97 or strike > stock_price * 1.05:
                    continue
                oi = opt.get("open_interest", 0) or 0
                if oi < 100:
                    continue
                mid = (bid + ask) / 2
                if best_put is None or abs(strike - stock_price) < abs(best_put["strike"] - stock_price):
                    best_put = {"symbol": symbol, "strike": strike, "expiration": exp, "dte": dte, "bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct, "oi": oi}
            if best_put and best_put["spread_pct"] < PUT_ROTATION_MAX_SPREAD_PCT:
                logger.info(f"Found put for rotation: {best_put['symbol']} ${best_put['strike']}P {best_put['expiration']} ({best_put['dte']}d) bid=${best_put['bid']:.2f} ask=${best_put['ask']:.2f} spread={best_put['spread_pct']:.1f}% OI={best_put['oi']}")
                # Calculate how many puts to buy — match the notional of the short
                short_qty = state.get(f"{symbol.lower()}_short_qty", 0)
                short_price = state.get(f"{symbol.lower()}_short_price", 0)
                if short_qty > 0:
                    short_notional = short_qty * short_price
                    put_cost_per = best_put["ask"] * 100
                    put_qty = max(1, int(short_notional / put_cost_per))
                else:
                    put_qty = 1
                # Buy the put
                occ = build_occ_symbol(best_put["symbol"], best_put["expiration"], "put", best_put["strike"])
                buy_price = round(best_put["ask"] * 20) / 20  # pay the ask for speed
                logger.warning(f"ROTATING short to put: BUY {occ} x{put_qty} @ ${buy_price:.2f}")
                res = await place_option_order(client, best_put["symbol"], occ, "buy_to_open", put_qty, "limit", buy_price, duration="day")
                if "order" in res:
                    put_order_id = res["order"].get("id")
                    logger.info(f"Put order placed — {put_order_id}")
                    # Close the short
                    logger.warning(f"Closing {symbol} short — buy_to_cover x{short_qty}")
                    cover_res = await client.place_order("trb", symbol, "buy_to_cover", short_qty, order_type="market")
                    if cover_res and "order" in cover_res:
                        logger.info(f"Cover order placed — {cover_res['order'].get('id')}")
                    state["status"] = "ROTATED"
                    state["rotated_at"] = datetime.now().isoformat()
                    state["put_occ"] = occ
                    state["put_order_id"] = put_order_id
                    state["put_qty"] = put_qty
                    state["put_price"] = buy_price
                    state[f"{symbol.lower()}_covered"] = True
                    save_state(state)
                    # If we rotated MSTR, also cover IBIT short
                    other = "IBIT" if symbol == "MSTR" else "MSTR"
                    other_qty = state.get(f"{other.lower()}_short_qty", 0)
                    if other_qty > 0 and not state.get(f"{other.lower()}_covered"):
                        logger.warning(f"Also covering {other} short x{other_qty}")
                        await client.place_order("trb", other, "buy_to_cover", other_qty, order_type="market")
                        state[f"{other.lower()}_covered"] = True
                    break
                elif "errors" in res:
                    logger.error(f"Put order FAILED: {res['errors']}")
            await asyncio.sleep(0.3)
    finally:
        await client.close()
    return state


async def run_daemon(state: dict):
    """Main loop — check BTC price, manage state machine."""
    logger.info(f"Safety net ARMED — trigger at ${state['trigger_price']:,.0f}, checking every {CHECK_INTERVAL}s")
    while True:
        try:
            btc = get_btc_price()
            if btc <= 0:
                logger.warning("Could not get BTC price")
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            status = state["status"]
            trigger = state["trigger_price"]
            if status == "ARMED":
                if btc < trigger:
                    logger.warning(f"!!! BTC CRASH TRIGGER !!! BTC=${btc:,.2f} < ${trigger:,.0f}")
                    state["btc_price_at_trigger"] = btc
                    if is_market_open():
                        state = await execute_shorts(state)
                        save_state(state)
                    else:
                        # Market closed — queue for open. Place pre-market order if extended hours available
                        logger.warning(f"Market CLOSED — will short at next open. BTC=${btc:,.2f}")
                        state["status"] = "PENDING_OPEN"
                        state["btc_price_at_trigger"] = btc
                        save_state(state)
                elif btc < trigger * 1.03:
                    # Within 3% of trigger — log warning
                    pct_away = (btc - trigger) / trigger * 100
                    if int(time.time()) % 300 < CHECK_INTERVAL:
                        logger.info(f"BTC ${btc:,.2f} — {pct_away:+.1f}% from trigger ${trigger:,.0f}")
            elif status == "PENDING_OPEN":
                if is_market_open():
                    btc_now = get_btc_price()
                    if btc_now < trigger * 1.05:
                        logger.warning(f"Market opened, BTC still below trigger zone (${btc_now:,.2f}). Executing shorts.")
                        state = await execute_shorts(state)
                        save_state(state)
                    else:
                        logger.info(f"Market opened but BTC recovered to ${btc_now:,.2f}. Re-arming.")
                        state["status"] = "ARMED"
                        save_state(state)
            elif status == "SHORTED":
                # Shorts are open — scan for put rotation every 5 min during market hours
                if is_market_open() and int(time.time()) % 300 < CHECK_INTERVAL:
                    hours_since_short = 0
                    if state.get("shorted_at"):
                        hours_since_short = (datetime.now() - datetime.fromisoformat(state["shorted_at"])).total_seconds() / 3600
                    # Wait at least 30 min for option spreads to settle after crash
                    if hours_since_short >= 0.5:
                        logger.info(f"Scanning for put rotation... BTC=${btc:,.2f}")
                        state = await scan_for_put_rotation(state)
                        save_state(state)
            elif status == "ROTATED":
                logger.info(f"Safety net COMPLETE — rotated to puts. BTC=${btc:,.2f}")
                break
            elif status == "DISABLED":
                break
            save_state(state)
        except Exception as e:
            logger.error(f"Error in safety net loop: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="BTC Crash Safety Net")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--disable", action="store_true", help="Disable the trigger")
    parser.add_argument("--arm", action="store_true", help="Re-arm the trigger")
    parser.add_argument("--trigger", type=float, help="Set trigger price")
    args = parser.parse_args()
    state = load_state()
    if args.status:
        print(json.dumps(state, indent=2, default=str))
        btc = get_btc_price()
        pct = (btc - state["trigger_price"]) / state["trigger_price"] * 100 if btc > 0 else 0
        print(f"\nBTC: ${btc:,.2f} ({pct:+.1f}% from trigger ${state['trigger_price']:,.0f})")
        print(f"Status: {state['status']}")
        print(f"Market open: {is_market_open()}")
        return
    if args.disable:
        state["status"] = "DISABLED"
        save_state(state)
        print("Safety net DISABLED")
        return
    if args.arm:
        state["status"] = "ARMED"
        if args.trigger:
            state["trigger_price"] = args.trigger
        save_state(state)
        print(f"Safety net ARMED at ${state['trigger_price']:,.0f}")
        return
    if args.trigger:
        state["trigger_price"] = args.trigger
        save_state(state)
        print(f"Trigger updated to ${args.trigger:,.0f}")
    if state["status"] in ("ROTATED", "DISABLED"):
        print(f"Safety net is {state['status']}. Use --arm to re-enable.")
        return
    asyncio.run(run_daemon(state))


if __name__ == "__main__":
    main()
