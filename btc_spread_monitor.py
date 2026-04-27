#!/usr/bin/env python3
"""
BTC Trio Spread Monitor — options pairs trades on crypto exposure divergence.

Three independent pairs, each with backtest-optimized configs:
  MSTR/IBIT: Sharpe 0.62 | WR 76% | +$28.9k | LB=15 Z=2.0 Zx=0.5 DTE=60
  COIN/IBIT: Sharpe 0.66 | WR 82% | +$31.3k | LB=10 Z=2.5 Zx=0.3 DTE=60
  COIN/MSTR: Sharpe 0.72 | WR 82% | +$30.7k | LB=20 Z=2.5 Zx=0.3 DTE=60

When pair ratio z-score exceeds threshold:
  z > entry → A overextended → buy A puts + B calls
  z < -entry → A undervalued → buy A calls + B puts

Each pair tracked independently. Max 1 spread per pair at a time.

Integration:
  - Called by daily cycle (tradier_options_agent.py --daily, Step 7)
  - Standalone: python btc_spread_monitor.py [--scan | --status | --close]
"""
import asyncio
import json
import logging
import os
import platform
import statistics
import sys
import argparse
import time
from datetime import datetime
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

logger = logging.getLogger("btc_spread")
logger.setLevel(logging.INFO)
logger.propagate = False

# === ORDER DEDUPE (2026-04-27) ===
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
    fh = RotatingFileHandler(log_dir / "btc_spread_monitor.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(sh)

# ── Per-pair configs (backtest-optimized) ───────────────────────────────────
PAIR_CONFIGS = {
    "MSTR/IBIT": {
        "sym_a": "MSTR", "sym_b": "IBIT",
        "lookback": 15, "z_entry": 2.0, "z_exit": 0.5,
        "z_stop": 4.5, "max_hold_days": 30,
        "target_dte": (45, 75), "budget_per_leg": 1000,
    },
    "COIN/IBIT": {
        "sym_a": "COIN", "sym_b": "IBIT",
        "lookback": 10, "z_entry": 2.5, "z_exit": 0.3,
        "z_stop": 4.5, "max_hold_days": 30,
        "target_dte": (45, 75), "budget_per_leg": 1000,
    },
    "COIN/MSTR": {
        "sym_a": "COIN", "sym_b": "MSTR",
        "lookback": 20, "z_entry": 2.5, "z_exit": 0.3,
        "z_stop": 4.5, "max_hold_days": 30,
        "target_dte": (45, 75), "budget_per_leg": 1000,
    },
}
ALL_SYMBOLS = {"MSTR", "IBIT", "COIN"}
MAX_SPREAD_PCT = 10.0
MIN_OI = 100
MIN_SCORE = 30
STATE_FILE = BASE_PATH / "data" / "tradier" / "btc_spread_state.json"
KLINES_DIR = BASE_PATH / "klines_cache" / "tradier"


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"positions": {}, "history": [], "last_zscores": {}, "last_check": None}


def save_state(state: dict):
    state["last_check"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_daily_prices(symbol) -> dict:
    """Load daily klines, return {date: close_price}."""
    path = KLINES_DIR / f"{symbol}_D.json"
    if not path.exists():
        return {}
    with open(path) as f:
        bars = json.load(f)
    result = {}
    for b in bars:
        ts = b["timestamp"]
        if "T" in ts:
            ts = ts.replace("T", " ")
        ts = ts[:10]
        if b.get("close", 0) > 0:
            result[ts] = b["close"]
    return result


def compute_pair_zscore(pair_name: str, prices: dict) -> dict:
    """Compute z-score for a pair from daily prices."""
    cfg = PAIR_CONFIGS[pair_name]
    sym_a, sym_b = cfg["sym_a"], cfg["sym_b"]
    lookback = cfg["lookback"]
    prices_a = prices.get(sym_a, {})
    prices_b = prices.get(sym_b, {})
    if not prices_a or not prices_b:
        return {"error": f"Missing prices for {sym_a} or {sym_b}"}
    common_dates = sorted(set(prices_a.keys()) & set(prices_b.keys()))
    if len(common_dates) < lookback + 5:
        return {"error": f"Not enough aligned bars ({len(common_dates)}), need {lookback + 5}"}
    ratios = [(d, prices_a[d] / prices_b[d]) for d in common_dates if prices_b[d] > 0]
    if len(ratios) < lookback + 5:
        return {"error": f"Not enough valid ratios ({len(ratios)})"}
    recent = [r[1] for r in ratios[-lookback - 1:-1]]
    current_date, current_ratio = ratios[-1]
    mean_r = statistics.mean(recent)
    std_r = statistics.stdev(recent) if len(recent) > 1 else 0.001
    z = (current_ratio - mean_r) / max(std_r, 0.0001)
    z_entry = cfg["z_entry"]
    return {
        "pair": pair_name,
        "z_score": round(z, 3),
        "ratio": round(current_ratio, 4),
        "mean": round(mean_r, 4),
        "std": round(std_r, 4),
        "price_a": prices_a.get(current_date, 0),
        "price_b": prices_b.get(current_date, 0),
        "date": current_date,
        "signal": f"SHORT_{sym_a}" if z > z_entry else (f"LONG_{sym_a}" if z < -z_entry else "NEUTRAL"),
        "exit_signal": abs(z) < cfg["z_exit"],
        "stop_signal": abs(z) > cfg["z_stop"],
    }


async def refresh_klines():
    """Refresh MSTR/IBIT/COIN daily klines from yfinance."""
    try:
        import yfinance as yf
        for symbol in sorted(ALL_SYMBOLS):
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2y", interval="1d")
            if not df.empty:
                bars = [{"timestamp": idx.strftime("%Y-%m-%d"), "open": round(row["Open"], 4), "high": round(row["High"], 4), "low": round(row["Low"], 4), "close": round(row["Close"], 4), "volume": int(row["Volume"])} for idx, row in df.iterrows()]
                with open(KLINES_DIR / f"{symbol}_D.json", "w") as f:
                    json.dump(bars, f)
                logger.info(f"Refreshed {symbol} daily: {len(bars)} bars")
    except Exception as e:
        logger.error(f"Failed to refresh klines: {e}")


async def find_pair_options(client: TradierAPIClient, pair_name: str, direction: str, price_a: float, price_b: float) -> dict:
    """Find underpriced options for both legs of a pair spread."""
    cfg = PAIR_CONFIGS[pair_name]
    sym_a, sym_b = cfg["sym_a"], cfg["sym_b"]
    target_dte = cfg["target_dte"]
    budget = cfg["budget_per_leg"]
    is_short_a = direction.startswith("SHORT")
    results = {}
    for symbol, price, is_call_leg in [
        (sym_a, price_a, not is_short_a),
        (sym_b, price_b, is_short_a),
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
                if target_dte[0] <= dte <= target_dte[1]:
                    target_exps.append((exp, dte))
            except ValueError:
                continue
        if not target_exps:
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
        target_exps.sort(key=lambda x: abs(x[1] - 60))
        exp, dte = target_exps[0]
        opt_type = "call" if is_call_leg else "put"
        sig_direction = "LONG" if is_call_leg else "SHORT"
        sig = DirectionalSignal(symbol=symbol, direction=sig_direction, conviction=80, price=price, atr_D=price * 0.05, signals={"btc_spread": True})
        chain = await fetch_option_chain(client, symbol, exp)
        if not chain:
            continue
        outliers, _ = analyze_chain_for_outliers(sig, chain, exp)
        candidates = [o for o in outliers if o.option_type == opt_type and o.score >= MIN_SCORE and o.open_interest >= MIN_OI]
        candidates.sort(key=lambda o: o.score, reverse=True)
        leg_key = symbol.lower()
        if not candidates:
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
                atm_pct = 0.03 if symbol == "IBIT" else 0.05
                if abs(strike - price) / price < atm_pct:
                    atm_options.append({"strike": strike, "bid": bid, "ask": ask, "mid": (bid + ask) / 2, "oi": opt.get("open_interest", 0), "spread_pct": spread_pct, "expiration": exp, "dte": dte, "type": opt_type})
            if atm_options:
                atm_options.sort(key=lambda x: abs(x["strike"] - price))
                best = atm_options[0]
                results[leg_key] = {"symbol": symbol, "strike": best["strike"], "expiration": exp, "dte": dte, "type": opt_type, "bid": best["bid"], "ask": best["ask"], "mid": best["mid"], "oi": best["oi"], "source": "ATM_FALLBACK"}
                logger.info(f"BTC spread {pair_name} {symbol}: ATM {opt_type} ${best['strike']} {exp} mid=${best['mid']:.2f}")
        else:
            best = candidates[0]
            results[leg_key] = {"symbol": symbol, "strike": best.strike, "expiration": best.expiration, "dte": best.dte, "type": opt_type, "bid": best.bid, "ask": best.ask, "mid": best.mid, "oi": best.open_interest, "score": best.score, "edge_pct": best.edge_pct, "iv_deviation": best.iv_deviation_pct, "source": "SCANNER"}
            logger.info(f"BTC spread {pair_name} {symbol}: SCANNER {opt_type} ${best.strike} {best.expiration} mid=${best.mid:.2f} score={best.score}")
        await asyncio.sleep(0.3)
    return results


async def place_spread_orders(client: TradierAPIClient, pair_name: str, direction: str, options: dict, dry_run: bool = False) -> list:
    """Place GTC buy orders for both legs."""
    cfg = PAIR_CONFIGS[pair_name]
    budget = cfg["budget_per_leg"]
    placed = []
    for leg_key in sorted(options.keys()):
        opt = options[leg_key]
        symbol = opt["symbol"]
        occ = build_occ_symbol(symbol, opt["expiration"], opt["type"], opt["strike"])
        gtc_price = round(opt["ask"] * 0.88 * 20) / 20
        qty = max(1, int(budget / (opt["ask"] * 100)))
        cost = gtc_price * qty * 100
        print(f"  {'[DRY] ' if dry_run else ''}GTC BUY {occ} x{qty} @ ${gtc_price:.2f} (ask=${opt['ask']:.2f}, {gtc_price / opt['ask'] * 100:.0f}% of ask)  Cost: ${cost:.0f}")
        print(f"    {opt.get('source', '?')} — edge: {opt.get('edge_pct', 0):+.1f}%  OI: {opt['oi']}")
        if not dry_run:
            if not _order_allowed(f"{occ}|buy_to_open"):
                continue
            res = await place_option_order(client, symbol, occ, "buy_to_open", qty, "limit", gtc_price, duration="gtc")
            if "order" in res:
                order_id = res["order"].get("id")
                print(f"    PLACED ID={order_id}")
                logger.info(f"BTC spread {pair_name} {direction} {occ} x{qty} @ ${gtc_price:.2f} ID={order_id}")
                placed.append({"occ": occ, "symbol": symbol, "type": opt["type"], "strike": opt["strike"], "expiration": opt["expiration"], "qty": qty, "gtc_price": gtc_price, "order_id": order_id, "leg": leg_key, "direction": direction, "pair": pair_name})
            elif "errors" in res:
                print(f"    FAILED: {res['errors']}")
        else:
            placed.append({"occ": occ, "symbol": symbol, "type": opt["type"], "strike": opt["strike"], "expiration": opt["expiration"], "qty": qty, "gtc_price": gtc_price, "order_id": "DRY_RUN", "leg": leg_key, "direction": direction, "pair": pair_name})
        await asyncio.sleep(0.3)
    return placed


def check_exit_conditions(pair_name: str, position: dict, zscore_data: dict) -> str:
    """Check if a position should be exited. Returns reason or empty string."""
    if "error" in zscore_data:
        return ""
    cfg = PAIR_CONFIGS[pair_name]
    z = zscore_data["z_score"]
    direction = position.get("direction", "")
    sym_a = cfg["sym_a"]
    if direction == f"SHORT_{sym_a}":
        if z <= cfg["z_exit"]:
            return f"MEAN_REVERT z={z:.2f} (entered at z={position.get('entry_z', '?')})"
        if z >= cfg["z_stop"]:
            return f"STOP z={z:.2f} (blowout)"
    elif direction == f"LONG_{sym_a}":
        if z >= -cfg["z_exit"]:
            return f"MEAN_REVERT z={z:.2f} (entered at z={position.get('entry_z', '?')})"
        if z <= -cfg["z_stop"]:
            return f"STOP z={z:.2f} (blowout)"
    if position.get("entry_time"):
        try:
            entry_dt = datetime.fromisoformat(position["entry_time"])
            age_days = (datetime.now() - entry_dt).days
            if age_days >= cfg["max_hold_days"]:
                return f"MAX_AGE {age_days} days"
        except Exception:
            pass
    return ""


async def run_scan(args):
    """Scan all 3 pairs, place orders for any active signals."""
    config = TradierConfig()
    dry_run = getattr(args, "dry_run", False)
    state = load_state()
    await refresh_klines()
    prices = {sym: load_daily_prices(sym) for sym in ALL_SYMBOLS}
    print(f"\n{'=' * 80}")
    print(f"  BTC TRIO SPREAD MONITOR — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'=' * 80}")
    # Compute all z-scores
    zscores = {}
    for pair_name in PAIR_CONFIGS:
        zdata = compute_pair_zscore(pair_name, prices)
        zscores[pair_name] = zdata
        state["last_zscores"][pair_name] = zdata.get("z_score", 0)
        if "error" in zdata:
            print(f"  {pair_name}: ERROR — {zdata['error']}")
        else:
            z = zdata["z_score"]
            cfg = PAIR_CONFIGS[pair_name]
            status = zdata["signal"]
            arrow = ">>>" if status != "NEUTRAL" else "   "
            print(f"  {arrow} {pair_name:<12} z={z:>+6.2f}  ratio={zdata['ratio']:.4f}  {cfg['sym_a']}=${zdata['price_a']:.2f}  {cfg['sym_b']}=${zdata['price_b']:.2f}  [{status}]")
    # Check exits for open positions
    client = None
    for pair_name, pos in list(state.get("positions", {}).items()):
        if not pos:
            continue
        zdata = zscores.get(pair_name, {})
        reason = check_exit_conditions(pair_name, pos, zdata)
        if reason:
            print(f"\n  EXIT SIGNAL for {pair_name}: {reason}")
            # Exit handling would go through options watchdog (sell_to_close)
    # Check entries for pairs without open positions
    signals = []
    for pair_name, zdata in zscores.items():
        if "error" in zdata or zdata["signal"] == "NEUTRAL":
            continue
        if pair_name in state.get("positions", {}) and state["positions"][pair_name]:
            existing_dir = state["positions"][pair_name].get("direction", "")
            if existing_dir == zdata["signal"]:
                print(f"\n  {pair_name}: Already have {existing_dir} spread open — skipping")
                continue
        signals.append((pair_name, zdata))
    if not signals:
        print(f"\n  No new signals across all pairs")
        save_state(state)
        return
    # Process signals
    if not client:
        client = TradierAPIClient(config, account_key="trb")
        await client.connect()
    try:
        for pair_name, zdata in signals:
            cfg = PAIR_CONFIGS[pair_name]
            direction = zdata["signal"]
            z = zdata["z_score"]
            print(f"\n  SIGNAL: {pair_name} → {direction} (z={z:+.2f}) — scanning options...")
            options = await find_pair_options(client, pair_name, direction, zdata["price_a"], zdata["price_b"])
            sym_a_key = cfg["sym_a"].lower()
            sym_b_key = cfg["sym_b"].lower()
            if not options.get(sym_a_key) or not options.get(sym_b_key):
                missing = [s for s in [cfg["sym_a"], cfg["sym_b"]] if s.lower() not in options]
                print(f"  Could not find options for: {', '.join(missing)}")
                continue
            print(f"\n  {pair_name} spread legs:")
            for leg_key in sorted(options.keys()):
                o = options[leg_key]
                print(f"    {o['symbol']} {o['type'].upper()} ${o['strike']} exp {o['expiration']} ({o['dte']}d) mid=${o['mid']:.2f} OI={o['oi']}")
            total_cost = sum(options[k]["ask"] * max(1, int(cfg["budget_per_leg"] / (options[k]["ask"] * 100))) * 100 for k in options)
            print(f"    Estimated total cost: ${total_cost:,.0f}")
            print(f"\n  Placing GTC orders (88% of ask)...")
            placed = await place_spread_orders(client, pair_name, direction, options, dry_run=dry_run)
            if placed:
                if "positions" not in state:
                    state["positions"] = {}
                state["positions"][pair_name] = {
                    "direction": direction,
                    "entry_z": z,
                    "entry_time": datetime.now().isoformat(),
                    "legs": placed,
                    "price_a_at_entry": zdata["price_a"],
                    "price_b_at_entry": zdata["price_b"],
                    "ratio_at_entry": zdata["ratio"],
                }
                logger.warning(f"BTC SPREAD ENTERED: {pair_name} {direction} z={z:+.2f} {cfg['sym_a']}=${zdata['price_a']:.2f} {cfg['sym_b']}=${zdata['price_b']:.2f}")
    finally:
        if client:
            await client.close()
    save_state(state)


async def run_status(args):
    """Show current state for all pairs."""
    state = load_state()
    await refresh_klines()
    prices = {sym: load_daily_prices(sym) for sym in ALL_SYMBOLS}
    print(f"\n{'=' * 80}")
    print(f"  BTC TRIO SPREAD STATUS")
    print(f"{'=' * 80}")
    for pair_name in PAIR_CONFIGS:
        cfg = PAIR_CONFIGS[pair_name]
        zdata = compute_pair_zscore(pair_name, prices)
        if "error" in zdata:
            print(f"\n  {pair_name}: ERROR — {zdata['error']}")
        else:
            z = zdata["z_score"]
            print(f"\n  {pair_name}:  z={z:>+6.2f}  ratio={zdata['ratio']:.4f}  signal={zdata['signal']}")
            print(f"    {cfg['sym_a']}=${zdata['price_a']:.2f}  {cfg['sym_b']}=${zdata['price_b']:.2f}")
            print(f"    Config: LB={cfg['lookback']} Ze={cfg['z_entry']} Zx={cfg['z_exit']}")
        pos = state.get("positions", {}).get(pair_name)
        if pos:
            print(f"    POSITION: {pos['direction']} | entry z={pos.get('entry_z', '?')} at {pos.get('entry_time', '?')[:16]}")
            for leg in pos.get("legs", []):
                print(f"      {leg['occ']} x{leg['qty']} @ ${leg['gtc_price']:.2f}")
        else:
            print(f"    No open position")
    n_history = len(state.get("history", []))
    print(f"\n  Total closed trades: {n_history}")


async def run_close(args):
    """Close all open spread positions across all pairs."""
    state = load_state()
    positions = state.get("positions", {})
    open_pairs = {k: v for k, v in positions.items() if v}
    if not open_pairs:
        print("  No open positions to close")
        return
    config = TradierConfig()
    dry_run = getattr(args, "dry_run", False)
    client = TradierAPIClient(config, account_key="trb")
    await client.connect()
    try:
        for pair_name, pos in open_pairs.items():
            print(f"\n  Closing {pair_name} {pos['direction']} spread...")
            for leg in pos.get("legs", []):
                occ = leg["occ"]
                qty = leg["qty"]
                print(f"    {'[DRY] ' if dry_run else ''}SELL_TO_CLOSE {occ} x{qty}")
                if not dry_run:
                    if not _order_allowed(f"{occ}|sell_to_close"):
                        continue
                    res = await place_option_order(client, leg["symbol"], occ, "sell_to_close", qty, "market")
                    if "order" in res:
                        print(f"      CLOSED ID={res['order'].get('id')}")
                    elif "errors" in res:
                        print(f"      FAILED: {res['errors']}")
            pos["closed_at"] = datetime.now().isoformat()
            pos["close_reason"] = "MANUAL"
            state.setdefault("history", []).append(pos)
            state["positions"][pair_name] = None
    finally:
        await client.close()
    save_state(state)


def main():
    parser = argparse.ArgumentParser(description="BTC Trio Spread Monitor (MSTR/IBIT/COIN)")
    parser.add_argument("--scan", action="store_true", help="Scan all pairs and place orders if signal")
    parser.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--close", action="store_true", help="Close all open positions")
    parser.add_argument("--dry-run", action="store_true", help="Don't place real orders")
    args = parser.parse_args()
    if args.status:
        asyncio.run(run_status(args))
    elif args.close:
        asyncio.run(run_close(args))
    else:
        asyncio.run(run_scan(args))


if __name__ == "__main__":
    main()
