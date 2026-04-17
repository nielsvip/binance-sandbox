#!/usr/bin/env python3
"""Nightly chain archiver — snapshots full option chains for CSP forward-test validation.

For each tradeable symbol (trb_long + trb_short), pulls the next 3 expirations in the
14-45 DTE window and saves full chain (bid/ask/greeks/vol/OI) to JSON.

Output: data/tradier/chain_archive/YYYY-MM-DD/<SYMBOL>.json

Starts the forward-test clock — after ~30-60 days of archives we can replay actual
chain history against the CSP strategy using real (not synthetic) pricing.

Usage:
    python archive_options_chains.py                 # snapshot all tradeable symbols
    python archive_options_chains.py --symbols SPY,AAPL
    python archive_options_chains.py --dte-min 14 --dte-max 45
"""
import asyncio
import argparse
import json
import logging
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_analyzer import fetch_expirations, fetch_option_chain

logger = logging.getLogger("chain_archiver")
logger.setLevel(logging.INFO)
if not logger.handlers:
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(sh)


def _load_tradeable(config: TradierConfig) -> list:
    syms = set()
    for fname in ("tradeable_keys_trb_long.json", "tradeable_keys_trb_short.json"):
        fpath = config.DATA_DIR / fname
        if not fpath.exists():
            continue
        try:
            with open(fpath) as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in data.keys():
                    s = k.split(":")[1].replace("_LONG", "").replace("_SHORT", "") if ":" in k else k.replace("_LONG", "").replace("_SHORT", "")
                    if s:
                        syms.add(s)
            elif isinstance(data, list):
                syms.update(data)
        except Exception:
            pass
    return sorted(syms)


async def archive_symbol(client: TradierAPIClient, symbol: str, dte_min: int, dte_max: int, out_dir: Path) -> dict:
    expirations = await fetch_expirations(client, symbol)
    if not expirations:
        return {"symbol": symbol, "status": "no_expirations"}
    now = datetime.now()
    target = [(e, (datetime.strptime(e, "%Y-%m-%d") - now).days) for e in expirations]
    target = [(e, d) for e, d in target if dte_min <= d <= dte_max]
    if not target:
        return {"symbol": symbol, "status": "no_expirations_in_window"}
    target = sorted(target, key=lambda x: x[1])[:3]
    snapshot = {"symbol": symbol, "captured_at": datetime.now().isoformat(), "expirations": {}}
    for exp, dte in target:
        chain = await fetch_option_chain(client, symbol, exp)
        if not chain:
            continue
        snapshot["expirations"][exp] = {"dte": dte, "contracts": chain}
        await asyncio.sleep(0.2)
    if not snapshot["expirations"]:
        return {"symbol": symbol, "status": "empty_chains"}
    out_file = out_dir / f"{symbol}.json"
    with open(out_file, "w") as f:
        json.dump(snapshot, f, default=str)
    total_contracts = sum(len(v["contracts"]) for v in snapshot["expirations"].values())
    return {"symbol": symbol, "status": "ok", "expirations": len(snapshot["expirations"]), "contracts": total_contracts}


async def main_async(args):
    config = TradierConfig()
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = _load_tradeable(config)
    if not symbols:
        logger.error("No symbols to archive. Aborting.")
        return
    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = config.DATA_DIR / "chain_archive" / today
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Archiving {len(symbols)} symbols → {out_dir}")
    client = TradierAPIClient(config, account_key=args.account)
    await client.connect()
    results = []
    try:
        for sym in symbols:
            try:
                res = await archive_symbol(client, sym, args.dte_min, args.dte_max, out_dir)
            except Exception as e:
                res = {"symbol": sym, "status": "error", "error": str(e)}
            results.append(res)
            if res.get("status") == "ok":
                logger.info(f"  {sym}: {res['expirations']} exps, {res['contracts']} contracts")
            else:
                logger.warning(f"  {sym}: {res.get('status')}")
            await asyncio.sleep(0.3)
    finally:
        try:
            await client.close()
        except Exception:
            pass
    summary = {"date": today, "symbols_attempted": len(symbols), "symbols_ok": sum(1 for r in results if r.get("status") == "ok"), "results": results}
    with open(out_dir / "_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"Done: {summary['symbols_ok']}/{summary['symbols_attempted']} OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="trb")
    ap.add_argument("--symbols", default=None, help="Comma-separated override (default: tradeable_keys)")
    ap.add_argument("--dte-min", type=int, default=14)
    ap.add_argument("--dte-max", type=int, default=45)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
