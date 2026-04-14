#!/usr/bin/env python3
"""
USO/BNO Oil Spread Monitor — options pairs trade on ratio divergence.

Backtest results (2yr, 486 configs):
  Best Sharpe: 0.87 | WR: 93% | LB=200 Z=3.0 DTE=60 IV≤0.80
  Best P/L:    $51k | WR: 74% | LB=50  Z=2.0 DTE=60 $1k/leg

When USO/BNO ratio z-score exceeds threshold:
  z > +2.5 → USO overextended → buy USO puts + BNO calls
  z < -2.5 → BNO overextended → buy USO calls + BNO puts
Exit when z reverts toward 0.

Integration:
  - Called by daily cycle (tradier_options_agent.py --daily)
  - Standalone: python oil_spread_monitor.py [--scan | --status | --close]
"""
import asyncio
import json
import logging
import math
import os
import platform
import statistics
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_analyzer import (
    fetch_expirations, fetch_option_chain, analyze_chain_for_outliers,
    place_option_order, build_occ_symbol, parse_occ_symbol,
    DirectionalSignal
)

logger = logging.getLogger("oil_spread")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    log_dir = Path(os.path.expanduser("~")) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_dir / "oil_spread_monitor.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

# ── Configuration (backtest-optimized) ───────────────────────────────────────
Z_ENTRY_THRESHOLD = 2.5       # Enter spread at z > 2.5 or z < -2.5
Z_EXIT_THRESHOLD = 0.3        # Exit when z reverts to ±0.3
Z_STOP_THRESHOLD = 4.5        # Emergency exit if divergence blows out
LOOKBACK_BARS = 100           # 1h bars lookback for z-score (100 = ~2.5 weeks)
TARGET_DTE = (45, 75)         # Preferred option DTE range
BUDGET_PER_LEG = 750          # $ per leg (USO + BNO = $1500 total)
MAX_SPREAD_PCT = 8.0          # Max bid/ask spread % on options
MIN_OI = 50                   # Min open interest
MIN_SCORE = 40                # Min outlier score from scanner
STATE_FILE = BASE_PATH / "data" / "tradier" / "oil_spread_state.json"
KLINES_DIR = BASE_PATH / "klines_cache" / "tradier"


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"status": "WATCHING", "positions": [], "history": [], "last_z": 0, "last_check": None}


def save_state(state: dict):
    state["last_check"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def compute_ratio_zscore() -> dict:
    """Compute USO/BNO ratio z-score from 1h klines."""
    uso_file = KLINES_DIR / "USO_1h.json"
    bno_file = KLINES_DIR / "BNO_1h.json"
    if not uso_file.exists() or not bno_file.exists():
        return {"error": "Missing 1h klines — run yfinance fetch first"}
    with open(uso_file) as f:
        uso_bars = json.load(f)
    with open(bno_file) as f:
        bno_bars = json.load(f)
    bno_map = {b["timestamp"]: b["close"] for b in bno_bars if b.get("close", 0) > 0}
    ratios = []
    for u in uso_bars:
        ts = u["timestamp"]
        if ts in bno_map and bno_map[ts] > 0:
            ratios.append({"ts": ts, "ratio": u["close"] / bno_map[ts], "uso": u["close"], "bno": bno_map[ts]})
    if len(ratios) < LOOKBACK_BARS + 10:
        return {"error": f"Not enough aligned bars ({len(ratios)}), need {LOOKBACK_BARS + 10}"}
    # Compute z-score on latest bar
    recent_ratios = [r["ratio"] for r in ratios[-LOOKBACK_BARS-1:-1]]
    current_ratio = ratios[-1]["ratio"]
    mean_r = statistics.mean(recent_ratios)
    std_r = statistics.stdev(recent_ratios) if len(recent_ratios) > 1 else 0.001
    z = (current_ratio - mean_r) / max(std_r, 0.0001)
    return {
        "z_score": round(z, 3),
        "ratio": round(current_ratio, 5),
        "mean": round(mean_r, 5),
        "std": round(std_r, 5),
        "uso_price": ratios[-1]["uso"],
        "bno_price": ratios[-1]["bno"],
        "timestamp": ratios[-1]["ts"],
        "signal": "SHORT_SPREAD" if z > Z_ENTRY_THRESHOLD else ("LONG_SPREAD" if z < -Z_ENTRY_THRESHOLD else "NEUTRAL"),
        "exit_signal": abs(z) < Z_EXIT_THRESHOLD,
        "stop_signal": abs(z) > Z_STOP_THRESHOLD,
        "bars_used": len(recent_ratios)
    }


async def refresh_klines():
    """Refresh USO/BNO 1h klines from yfinance."""
    try:
        import yfinance as yf
        for symbol in ["USO", "BNO"]:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2y", interval="1h")
            if not df.empty:
                bars = [{"timestamp": idx.strftime("%Y-%m-%d %H:%M"), "open": round(row["Open"], 4), "high": round(row["High"], 4), "low": round(row["Low"], 4), "close": round(row["Close"], 4), "volume": int(row["Volume"])} for idx, row in df.iterrows()]
                with open(KLINES_DIR / f"{symbol}_1h.json", "w") as f:
                    json.dump(bars, f)
                logger.info(f"Refreshed {symbol} 1h: {len(bars)} bars")
    except Exception as e:
        logger.error(f"Failed to refresh klines: {e}")


async def find_spread_options(client: TradierAPIClient, direction: str, uso_price: float, bno_price: float) -> dict:
    """Find underpriced options for the spread trade.
    direction: SHORT_SPREAD (buy USO puts + BNO calls) or LONG_SPREAD (buy USO calls + BNO puts)
    """
    results = {"uso": None, "bno": None}
    for symbol, price, is_call_leg in [
        ("USO", uso_price, direction == "LONG_SPREAD"),
        ("BNO", bno_price, direction == "SHORT_SPREAD")
    ]:
        expirations = await fetch_expirations(client, symbol)
        if not expirations:
            logger.warning(f"No expirations for {symbol}")
            continue
        now = datetime.now()
        target_exps = []
        for exp in expirations:
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d") - now).days
                if TARGET_DTE[0] <= dte <= TARGET_DTE[1]:
                    target_exps.append((exp, dte))
            except ValueError:
                continue
        if not target_exps:
            # Fallback: closest expiry > 30 DTE
            for exp in expirations:
                try:
                    dte = (datetime.strptime(exp, "%Y-%m-%d") - now).days
                    if dte > 30:
                        target_exps.append((exp, dte))
                        break
                except ValueError:
                    continue
        if not target_exps:
            continue
        exp, dte = target_exps[0]
        # Create a signal for the scanner
        opt_type = "call" if is_call_leg else "put"
        sig_direction = "LONG" if is_call_leg else "SHORT"
        sig = DirectionalSignal(symbol=symbol, direction=sig_direction, conviction=80, price=price, atr_D=price * 0.05, signals={"oil_spread": True})
        chain = await fetch_option_chain(client, symbol, exp)
        if not chain:
            continue
        outliers, _ = analyze_chain_for_outliers(sig, chain, exp)
        # Filter for our leg type, underpriced, liquid
        candidates = [o for o in outliers if o.option_type == opt_type and o.score >= MIN_SCORE and o.open_interest >= MIN_OI]
        # Sort by score (best underpriced first)
        candidates.sort(key=lambda o: o.score, reverse=True)
        if not candidates:
            # Fallback: find ATM option manually
            atm_options = []
            for opt in chain:
                if opt.get("option_type") != opt_type:
                    continue
                strike = opt.get("strike", 0)
                bid = opt.get("bid", 0) or 0
                ask = opt.get("ask", 0) or 0
                if bid <= 0 or ask <= 0:
                    continue
                spread_pct = (ask - bid) / ((bid + ask) / 2) * 100
                if spread_pct > MAX_SPREAD_PCT:
                    continue
                if abs(strike - price) / price < 0.05:  # Within 5% of ATM
                    atm_options.append({"strike": strike, "bid": bid, "ask": ask, "mid": (bid+ask)/2, "oi": opt.get("open_interest", 0), "spread_pct": spread_pct, "expiration": exp, "dte": dte, "type": opt_type})
            if atm_options:
                atm_options.sort(key=lambda x: abs(x["strike"] - price))
                best = atm_options[0]
                results[symbol.lower()] = {"symbol": symbol, "strike": best["strike"], "expiration": exp, "dte": dte, "type": opt_type, "bid": best["bid"], "ask": best["ask"], "mid": best["mid"], "oi": best["oi"], "source": "ATM_FALLBACK"}
                logger.info(f"Oil spread {symbol}: ATM fallback {opt_type} ${best['strike']} {exp} mid=${best['mid']:.2f}")
        else:
            best = candidates[0]
            results[symbol.lower()] = {"symbol": symbol, "strike": best.strike, "expiration": best.expiration, "dte": best.dte, "type": opt_type, "bid": best.bid, "ask": best.ask, "mid": best.mid, "oi": best.open_interest, "score": best.score, "edge_pct": best.edge_pct, "iv_deviation": best.iv_deviation_pct, "source": "SCANNER"}
            logger.info(f"Oil spread {symbol}: SCANNER found {opt_type} ${best.strike} {best.expiration} mid=${best.mid:.2f} score={best.score} edge={best.edge_pct:+.1f}%")
        await asyncio.sleep(0.3)
    return results


async def place_spread_orders(client: TradierAPIClient, direction: str, options: dict, dry_run: bool = False) -> list:
    """Place GTC buy orders for both legs of the spread."""
    placed = []
    for leg_key in ["uso", "bno"]:
        opt = options.get(leg_key)
        if not opt:
            continue
        symbol = opt["symbol"]
        occ = build_occ_symbol(symbol, opt["expiration"], opt["type"], opt["strike"])
        # Price at 88% of ask (GTC discount — wait for dip)
        gtc_price = round(opt["ask"] * 0.88 * 20) / 20
        qty = max(1, int(BUDGET_PER_LEG / (opt["ask"] * 100)))
        cost = gtc_price * qty * 100
        print(f"  {'[DRY] ' if dry_run else ''}GTC BUY {occ} x{qty} @ ${gtc_price:.2f} (ask=${opt['ask']:.2f}, {gtc_price/opt['ask']*100:.0f}% of ask)  Cost: ${cost:.0f}")
        print(f"    {opt.get('source', '?')} — edge: {opt.get('edge_pct', 0):+.1f}%  OI: {opt['oi']}")
        if not dry_run:
            res = await place_option_order(client, symbol, occ, "buy_to_open", qty, "limit", gtc_price, duration="gtc")
            if "order" in res:
                order_id = res["order"].get("id")
                print(f"    PLACED ID={order_id}")
                logger.info(f"Oil spread {direction} {occ} x{qty} @ ${gtc_price:.2f} ID={order_id}")
                placed.append({"occ": occ, "symbol": symbol, "type": opt["type"], "strike": opt["strike"], "expiration": opt["expiration"], "qty": qty, "gtc_price": gtc_price, "order_id": order_id, "leg": leg_key, "direction": direction})
            elif "errors" in res:
                print(f"    FAILED: {res['errors']}")
        else:
            placed.append({"occ": occ, "symbol": symbol, "type": opt["type"], "strike": opt["strike"], "expiration": opt["expiration"], "qty": qty, "gtc_price": gtc_price, "order_id": "DRY_RUN", "leg": leg_key, "direction": direction})
        await asyncio.sleep(0.3)
    return placed


async def check_exit_conditions(client: TradierAPIClient, state: dict) -> list:
    """Check if open spread positions should be closed based on z-score reversion."""
    zscore_data = compute_ratio_zscore()
    if "error" in zscore_data:
        return []
    z = zscore_data["z_score"]
    exits = []
    for pos in state.get("positions", []):
        direction = pos.get("direction", "")
        should_exit = False
        reason = ""
        if direction == "SHORT_SPREAD":
            if z <= Z_EXIT_THRESHOLD:
                should_exit = True
                reason = f"MEAN_REVERT z={z:.2f} (entered at z={pos.get('entry_z', '?')})"
            elif z >= Z_STOP_THRESHOLD:
                should_exit = True
                reason = f"STOP z={z:.2f} (blowout)"
        elif direction == "LONG_SPREAD":
            if z >= -Z_EXIT_THRESHOLD:
                should_exit = True
                reason = f"MEAN_REVERT z={z:.2f} (entered at z={pos.get('entry_z', '?')})"
            elif z <= -Z_STOP_THRESHOLD:
                should_exit = True
                reason = f"STOP z={z:.2f} (blowout)"
        # Check age — exit after 30 days regardless
        if pos.get("entry_time"):
            try:
                entry_dt = datetime.fromisoformat(pos["entry_time"])
                age_days = (datetime.now() - entry_dt).days
                if age_days >= 30:
                    should_exit = True
                    reason = f"MAX_AGE {age_days} days"
            except Exception:
                pass
        if should_exit:
            exits.append({"position": pos, "reason": reason, "current_z": z})
    return exits


async def run_scan(args):
    """Main scan: check z-score, find options if triggered, place orders."""
    config = TradierConfig()
    dry_run = getattr(args, "dry_run", False)
    state = load_state()
    # Refresh klines first
    await refresh_klines()
    # Compute z-score
    zscore_data = compute_ratio_zscore()
    if "error" in zscore_data:
        print(f"ERROR: {zscore_data['error']}")
        return
    z = zscore_data["z_score"]
    signal = zscore_data["signal"]
    state["last_z"] = z
    print(f"\n{'='*70}")
    print(f"  OIL SPREAD MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"  USO: ${zscore_data['uso_price']:.2f}  BNO: ${zscore_data['bno_price']:.2f}")
    print(f"  Ratio: {zscore_data['ratio']:.4f} (mean={zscore_data['mean']:.4f} std={zscore_data['std']:.4f})")
    print(f"  Z-Score: {z:+.3f}  Signal: {signal}")
    print(f"  Threshold: entry={Z_ENTRY_THRESHOLD} exit={Z_EXIT_THRESHOLD}")
    print(f"{'='*70}")
    # Check exits first
    if state.get("positions"):
        print(f"\n  Open positions: {len(state['positions'])}")
        exits = await check_exit_conditions(None, state)
        if exits:
            print(f"  EXIT SIGNALS: {len(exits)}")
            for ex in exits:
                print(f"    {ex['reason']}")
            # Place GTC sells for exit (handled by the main watchdog)
    # Check for new entry
    if signal == "NEUTRAL":
        print(f"\n  No signal — z={z:+.2f} within ±{Z_ENTRY_THRESHOLD}")
        save_state(state)
        return
    # Check if we already have a spread open in this direction
    existing = [p for p in state.get("positions", []) if p.get("direction") == signal]
    if existing:
        print(f"\n  Already have {signal} spread open — skipping")
        save_state(state)
        return
    print(f"\n  SIGNAL: {signal} — scanning for underpriced options...")
    client = TradierAPIClient(config, account_key="trb")
    await client.connect()
    try:
        options = await find_spread_options(client, signal, zscore_data["uso_price"], zscore_data["bno_price"])
        if not options.get("uso") or not options.get("bno"):
            missing = []
            if not options.get("uso"):
                missing.append("USO")
            if not options.get("bno"):
                missing.append("BNO")
            print(f"  Could not find options for: {', '.join(missing)}")
            save_state(state)
            return
        print(f"\n  Spread legs found:")
        for leg in ["uso", "bno"]:
            o = options[leg]
            print(f"    {o['symbol']} {o['type'].upper()} ${o['strike']} exp {o['expiration']} ({o['dte']}d) mid=${o['mid']:.2f} OI={o['oi']}")
        print(f"\n  Placing GTC orders (88% of ask)...")
        placed = await place_spread_orders(client, signal, options, dry_run=dry_run)
        if placed:
            state["positions"].append({
                "direction": signal,
                "entry_z": z,
                "entry_time": datetime.now().isoformat(),
                "legs": placed,
                "uso_price_at_entry": zscore_data["uso_price"],
                "bno_price_at_entry": zscore_data["bno_price"],
                "ratio_at_entry": zscore_data["ratio"]
            })
            state["status"] = "IN_SPREAD"
            logger.warning(f"OIL SPREAD ENTERED: {signal} z={z:+.2f} USO=${zscore_data['uso_price']:.2f} BNO=${zscore_data['bno_price']:.2f}")
    finally:
        await client.close()
    save_state(state)


async def run_status(args):
    """Show current spread state."""
    state = load_state()
    await refresh_klines()
    zscore_data = compute_ratio_zscore()
    print(f"\n  Status: {state.get('status', '?')}")
    print(f"  Last z: {state.get('last_z', '?')}")
    if "error" not in zscore_data:
        z = zscore_data["z_score"]
        print(f"  Current z: {z:+.3f}  Ratio: {zscore_data['ratio']:.4f}")
        print(f"  USO: ${zscore_data['uso_price']:.2f}  BNO: ${zscore_data['bno_price']:.2f}")
        print(f"  Signal: {zscore_data['signal']}")
    if state.get("positions"):
        for i, pos in enumerate(state["positions"]):
            print(f"\n  Position {i+1}: {pos['direction']}")
            print(f"    Entry z: {pos.get('entry_z', '?')} at {pos.get('entry_time', '?')[:16]}")
            for leg in pos.get("legs", []):
                print(f"    {leg['occ']} x{leg['qty']} @ ${leg['gtc_price']:.2f}")
    print(f"\n  History: {len(state.get('history', []))} closed trades")


def main():
    parser = argparse.ArgumentParser(description="USO/BNO Oil Spread Monitor")
    parser.add_argument("--scan", action="store_true", help="Scan and place orders if signal")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--dry-run", action="store_true", help="Don't place real orders")
    args = parser.parse_args()
    if args.status:
        asyncio.run(run_status(args))
    else:
        asyncio.run(run_scan(args))


if __name__ == "__main__":
    main()
