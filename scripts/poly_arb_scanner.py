#!/usr/bin/env python3
"""poly_arb_scanner.py — Real-time Polymarket YES+NO order book arb detector.

True YES+NO arb: when ASK_YES + ASK_NO < $1.00, you can buy both sides
and collect $1 at resolution (guaranteed profit, no directional risk).

This happens when:
  1. A fast price move leaves one side's asks stale
  2. Liquidity is thin and bots haven't updated both sides simultaneously
  3. Oracle lag: external event hits Polymarket asymmetrically

This scanner runs continuously, logs all arb opportunities, and optionally
executes paper trades or real CLOB orders.

Usage:
  python poly_arb_scanner.py                  # paper mode, logs only
  python poly_arb_scanner.py --execute-paper  # paper trade on every opportunity
  python poly_arb_scanner.py --min-profit 0.02 --interval 2
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import redis.asyncio as redis

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_arb_scanner")

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_DIR = Path("./data/poly")
DATA_DIR.mkdir(parents=True, exist_ok=True)
ARB_LOG = DATA_DIR / "arb_opportunities.jsonl"
PAPER_POS_FILE = DATA_DIR / "arb_paper_positions.json"


class ArbScanner:
    def __init__(self, min_profit: float = 0.015, execute_paper: bool = False, interval: float = 3.0):
        self.min_profit = min_profit
        self.execute_paper = execute_paper
        self.interval = interval
        self.redis_client = None
        self.session = None
        self.paper_positions = {}
        self.stats = {"scanned": 0, "opportunities": 0, "paper_trades": 0, "paper_pnl": 0.0}
        self.active_markets: dict = {}

    async def init(self):
        self.redis_client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        await self.redis_client.ping()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        if PAPER_POS_FILE.exists():
            with open(PAPER_POS_FILE) as f:
                self.paper_positions = json.load(f)
        logger.info(f"Arb Scanner started | min_profit={self.min_profit:.2%} | interval={self.interval}s | paper={'YES' if self.execute_paper else 'SCAN-ONLY'}")

    async def fetch_active_markets(self):
        try:
            async with self.session.get(f"{GAMMA_URL}/markets", params={"active": "true", "closed": "false", "order": "volumeNum", "ascending": "false", "limit": 100}) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    result = {}
                    for m in markets:
                        token_ids = json.loads(m.get("clobTokenIds", "[]"))
                        if len(token_ids) < 2:
                            continue
                        events = m.get("events", [])
                        ticker = events[0].get("ticker", m.get("slug", f"PM:{m['id']}")) if events else m.get("slug", f"PM:{m['id']}")
                        result[ticker] = {"market_id": m["id"], "yes_token": token_ids[0], "no_token": token_ids[1], "question": m.get("question", "")[:80], "volume": float(m.get("volumeNum", 0) or 0)}
                    self.active_markets = result
        except Exception as e:
            logger.warning(f"fetch_active_markets: {e}")

    async def get_order_book(self, token_id: str) -> dict:
        try:
            async with self.session.get(f"{CLOB_HOST}/book", params={"token_id": token_id}) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return {}

    def best_ask(self, book: dict) -> float:
        asks = book.get("asks", [])
        if not asks:
            return float("inf")
        return min(float(a.get("price", 1.0)) for a in asks if float(a.get("size", 0)) > 0)

    def best_bid(self, book: dict) -> float:
        bids = book.get("bids", [])
        if not bids:
            return 0.0
        return max(float(b.get("price", 0.0)) for b in bids if float(b.get("size", 0)) > 0)

    async def scan_market(self, ticker: str, m: dict):
        yes_book, no_book = await asyncio.gather(self.get_order_book(m["yes_token"]), self.get_order_book(m["no_token"]))
        if not yes_book or not no_book:
            return
        ask_yes = self.best_ask(yes_book)
        ask_no = self.best_ask(no_book)
        if ask_yes == float("inf") or ask_no == float("inf"):
            return
        combined_ask = ask_yes + ask_no
        profit = 1.0 - combined_ask
        self.stats["scanned"] += 1
        if profit >= self.min_profit:
            self.stats["opportunities"] += 1
            opportunity = {"ts": datetime.now(timezone.utc).isoformat(), "ticker": ticker, "question": m["question"][:60], "ask_yes": round(ask_yes, 4), "ask_no": round(ask_no, 4), "combined": round(combined_ask, 4), "gross_profit": round(profit, 4), "net_profit": round(profit * 0.98, 4), "volume_24h": m["volume"]}
            logger.info(f"ARB FOUND: {ticker} | YES={ask_yes:.4f} + NO={ask_no:.4f} = {combined_ask:.4f} | PROFIT={profit:.2%}")
            with open(ARB_LOG, "a") as f:
                f.write(json.dumps(opportunity) + "\n")
            if self.execute_paper and ticker not in self.paper_positions:
                self.paper_positions[ticker] = {"yes_cost": ask_yes, "no_cost": ask_no, "combined": combined_ask, "gross_profit": profit, "opened_at": opportunity["ts"]}
                self.stats["paper_trades"] += 1
                logger.info(f"PAPER ARB ENTERED: {ticker} | expected profit ${profit:.4f}/share")
                with open(PAPER_POS_FILE, "w") as f:
                    json.dump(self.paper_positions, f, indent=2)

    async def check_paper_resolutions(self):
        to_close = []
        for ticker, pos in self.paper_positions.items():
            if ticker in self.active_markets:
                continue
            resolved_profit = 1.0 - pos["combined"]
            fee = 0.02 * resolved_profit
            net = resolved_profit - fee
            self.stats["paper_pnl"] += net
            logger.info(f"ARB RESOLVED: {ticker} | profit=${net:.4f}")
            to_close.append(ticker)
        for t in to_close:
            del self.paper_positions[t]
        if to_close:
            with open(PAPER_POS_FILE, "w") as f:
                json.dump(self.paper_positions, f, indent=2)

    async def run_loop(self):
        await self.init()
        cycle = 0
        while True:
            try:
                if cycle % 20 == 0:
                    await self.fetch_active_markets()
                    await self.check_paper_resolutions()
                if not self.active_markets:
                    await asyncio.sleep(10)
                    continue
                scan_tasks = [self.scan_market(ticker, m) for ticker, m in self.active_markets.items()]
                await asyncio.gather(*scan_tasks, return_exceptions=True)
                if cycle % 60 == 0:
                    logger.info(f"Stats: scanned={self.stats['scanned']} | opportunities={self.stats['opportunities']} | paper_trades={self.stats['paper_trades']} | paper_pnl=${self.stats['paper_pnl']:.4f} | open_positions={len(self.paper_positions)}")
                cycle += 1
                await asyncio.sleep(self.interval)
            except Exception as e:
                logger.error(f"Scan loop error: {e}")
                await asyncio.sleep(5)

    async def close(self):
        if self.session:
            await self.session.close()
        if self.redis_client:
            await self.redis_client.close()


def main():
    parser = argparse.ArgumentParser(description="Real-time Polymarket YES+NO arb scanner")
    parser.add_argument("--min-profit", type=float, default=0.015, help="Minimum combined discount to log (default 0.015 = 1.5%%)")
    parser.add_argument("--execute-paper", action="store_true", help="Paper trade every arb opportunity")
    parser.add_argument("--interval", type=float, default=3.0, help="Scan interval in seconds (default 3)")
    args = parser.parse_args()
    scanner = ArbScanner(args.min_profit, args.execute_paper, args.interval)
    try:
        asyncio.run(scanner.run_loop())
    except KeyboardInterrupt:
        logger.info("Shutdown.")

if __name__ == "__main__":
    main()
