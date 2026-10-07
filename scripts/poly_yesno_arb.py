#!/usr/bin/env python3
"""poly_yesno_arb.py — Real-time YES+NO combined-price arb scanner.

Strategy: When ASK_YES + ASK_NO < $1.00, buy BOTH sides.
At resolution, one side pays $1.00 → guaranteed profit regardless of outcome.
No directional risk — 100% certain gain if both orders fill.

Why this happens:
  1. Fast price move leaves one side's resting limit orders stale
  2. Oracle lag: event resolves YES→0.999 but NO asks still at 0.005+
  3. Thin markets: wide bid/ask spreads allow combined < 1.00
  4. New market: initial MM posted conservative spread on both sides

Architecture:
  - Gamma API: fetch all 4000 markets → pre-filter to ~100-300 candidates
  - CLOB API: fetch YES+NO books only for candidates (80 concurrent)
  - Target cycle time: 8-15s for 4000 markets

IP Note: Polymarket is geo-restricted (US blocked). Ensure this runs on a
permitted IP (EU/Asia VPS). Set HTTP_PROXY env var to route through a proxy.

Usage:
  python poly_yesno_arb.py                         # scan-only, 5s interval
  python poly_yesno_arb.py --min-profit 0.003      # catch 0.3%+ gaps
  python poly_yesno_arb.py --execute-paper         # paper trade every hit
  python poly_yesno_arb.py --execute-live          # REAL trades (wallet required)
  python poly_yesno_arb.py --interval 3            # scan every 3s
"""

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("yesno_arb")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_DIR = Path("./data/poly/yesno_arb")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG = DATA_DIR / "opportunities.jsonl"
PAPER_FILE = DATA_DIR / "paper_positions.json"

# Pre-filter: skip markets with liq below this (thin = no fill)
MIN_LIQ_PREFILTER = 50.0
# Pre-filter: skip if lastTradePrice is in extreme zone (near certain, < spread needed)
EXTREME_ZONE = 0.005  # skip if YES < 0.5% or > 99.5%


def best_ask_from_book(book: dict) -> tuple[float, float]:
    """Returns (best_ask_price, size_at_best_ask). inf if no asks."""
    asks = [(float(a["price"]), float(a.get("size", 0))) for a in book.get("asks", []) if float(a.get("size", 0)) > 0]
    if not asks:
        return float("inf"), 0.0
    asks.sort()
    return asks[0]


def depth_at_price(book: dict, max_price: float) -> float:
    """Total shares available at or below max_price."""
    return sum(float(a.get("size", 0)) for a in book.get("asks", []) if float(a.get("price", 1)) <= max_price)


PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None


async def fetch_book(session: aiohttp.ClientSession, token_id: str) -> dict:
    try:
        async with session.get(f"{CLOB}/book", params={"token_id": token_id}, proxy=PROXY, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


async def fetch_all_markets() -> list:
    """Fetch up to 4000 active markets from Gamma (separate session for robustness)."""
    all_markets = []
    async with aiohttp.ClientSession() as session:
        for offset in range(0, 4001, 500):
            try:
                async with session.get(f"{GAMMA}/markets", params={"active": "true", "closed": "false", "order": "volumeNum", "ascending": "false", "limit": 500, "offset": offset}, proxy=PROXY, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    batch = await r.json(content_type=None)
                if not isinstance(batch, list):
                    break
                all_markets.extend(batch)
                if len(batch) < 500:
                    break
            except Exception as e:
                logger.warning(f"fetch_markets offset={offset}: {type(e).__name__}: {e}")
                break
    return all_markets


def prefilter(markets: list) -> list:
    """Keep only markets that could plausibly have YES+NO combined < 1.00.

    The Gamma API returns bestAsk for the YES token. For arb to exist:
      ask_yes + ask_no < 1.00
    Since ask_no ≈ 1 - ltp_yes (in a balanced market), if bestAsk_yes << ltp_yes
    then the YES side is cheap. If ltp is near 0.5, BOTH sides could be cheap.

    Key heuristic: if bestAsk_yes < ltp_yes - 0.02, YES orders are stale → candidate.
    Also include any market where ltp is in 0.05-0.95 zone (neither side near 0/1).
    """
    candidates = []
    for m in markets:
        liq = float(m.get("liquidityNum", 0) or 0)
        if liq < MIN_LIQ_PREFILTER:
            continue
        raw_toks = m.get("clobTokenIds", "[]")
        try:
            toks = json.loads(raw_toks) if isinstance(raw_toks, str) else raw_toks
        except Exception:
            toks = []
        if len(toks) < 2:
            continue
        ltp = float(m.get("lastTradePrice", 0.5) or 0.5)
        if ltp < EXTREME_ZONE or ltp > (1.0 - EXTREME_ZONE):
            continue
        best_ask_yes = float(m.get("bestAsk", ltp) or ltp)
        # Stale ask on YES side
        if best_ask_yes < ltp - 0.015:
            candidates.append(m)
            continue
        # Middle zone: both sides could have cheap asks
        if 0.10 <= ltp <= 0.90:
            candidates.append(m)
            continue
    return candidates


class YesNoArbScanner:
    def __init__(self, min_profit: float, execute_paper: bool, execute_live: bool, interval: float):
        self.min_profit = min_profit
        self.execute_paper = execute_paper
        self.execute_live = execute_live
        self.interval = interval
        self.session: aiohttp.ClientSession = None
        self.all_markets: list = []
        self.candidates: list = []
        self.paper: dict = {}
        self.stats = {"cycles": 0, "total_scanned": 0, "total_candidates": 0, "arbs_found": 0, "paper_entered": 0, "paper_pnl": 0.0}
        if PAPER_FILE.exists():
            with open(PAPER_FILE) as f:
                self.paper = json.load(f)

    def _save_paper(self):
        with open(PAPER_FILE, "w") as f:
            json.dump(self.paper, f, indent=2)

    async def scan_one(self, m: dict) -> dict | None:
        raw_toks = m.get("clobTokenIds", "[]")
        try:
            toks = json.loads(raw_toks) if isinstance(raw_toks, str) else raw_toks
        except Exception:
            return None
        if len(toks) < 2:
            return None
        yes_tok, no_tok = toks[0], toks[1]
        yes_book, no_book = await asyncio.gather(fetch_book(self.session, yes_tok), fetch_book(self.session, no_tok))
        if not yes_book or not no_book:
            return None
        ask_y, sz_y = best_ask_from_book(yes_book)
        ask_n, sz_n = best_ask_from_book(no_book)
        if ask_y == float("inf") or ask_n == float("inf"):
            return None
        combined = ask_y + ask_n
        profit = 1.0 - combined
        if profit < self.min_profit:
            return None
        ltp = float(m.get("lastTradePrice", 0.5) or 0.5)
        liq = float(m.get("liquidityNum", 0) or 0)
        vol24 = float(m.get("volume24hr", 0) or 0)
        max_shares = min(sz_y, sz_n)
        return {"ts": datetime.now(timezone.utc).isoformat(), "question": m.get("question", "")[:80], "market_id": m.get("id", ""), "ask_yes": round(ask_y, 4), "ask_no": round(ask_n, 4), "combined": round(combined, 4), "profit_pct": round(profit * 100, 3), "profit_per_share": round(profit, 6), "max_shares": round(max_shares, 2), "max_profit_usdc": round(profit * max_shares, 4), "ltp": round(ltp, 4), "liq": round(liq, 2), "vol24": round(vol24, 2), "yes_token": yes_tok, "no_token": no_tok}

    async def scan_candidates(self) -> list[dict]:
        sem = asyncio.Semaphore(80)
        async def guarded(m):
            async with sem:
                try:
                    return await self.scan_one(m)
                except Exception:
                    return None
        results = await asyncio.gather(*[guarded(m) for m in self.candidates])
        return [r for r in results if r is not None]

    async def check_resolutions(self):
        if not self.paper:
            return
        active_ids = {m.get("id") for m in self.all_markets}
        to_close = [mid for mid in self.paper if mid not in active_ids]
        for mid in to_close:
            pos = self.paper.pop(mid)
            net = pos["profit_pct"] / 100 * pos.get("shares", 1) * 0.98
            self.stats["paper_pnl"] += net
            logger.info(f"PAPER RESOLVED: profit={pos['profit_pct']:.3f}% | {pos['question'][:55]}")
        if to_close:
            self._save_paper()

    async def run(self):
        logger.info("Fetching markets...")
        self.all_markets = await fetch_all_markets()
        self.candidates = prefilter(self.all_markets)
        logger.info(f"Loaded {len(self.all_markets)} markets → {len(self.candidates)} candidates | min_profit={self.min_profit:.2%} | interval={self.interval}s")
        connector = aiohttp.TCPConnector(limit=120, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            while True:
                t0 = time.time()
                self.stats["cycles"] += 1
                self.stats["total_scanned"] += len(self.all_markets)
                self.stats["total_candidates"] += len(self.candidates)
                arbs = await self.scan_candidates()
                arbs.sort(key=lambda x: -x["profit_pct"])
                elapsed = time.time() - t0
                if arbs:
                    self.stats["arbs_found"] += len(arbs)
                    logger.info(f"=== {len(arbs)} ARB(S) FOUND | cycle #{self.stats['cycles']} | {elapsed:.1f}s ===")
                    for a in arbs:
                        logger.info(f"  YES={a['ask_yes']:.4f} + NO={a['ask_no']:.4f} = {a['combined']:.4f} | PROFIT={a['profit_pct']:.3f}% | max_profit=${a['max_profit_usdc']:.2f} | {a['question'][:55]}")
                        with open(LOG, "a") as f:
                            f.write(json.dumps(a) + "\n")
                        if self.execute_paper and a["market_id"] not in self.paper:
                            self.paper[a["market_id"]] = {"question": a["question"], "combined": a["combined"], "shares": a["max_shares"], "profit_pct": a["profit_pct"], "entered_at": a["ts"], "yes_token": a["yes_token"], "no_token": a["no_token"]}
                            self.stats["paper_entered"] += 1
                            self._save_paper()
                            logger.info(f"  PAPER: {a['max_shares']:.1f} shares @ combined={a['combined']:.4f} | exp_profit=${a['max_profit_usdc']:.4f}")
                else:
                    logger.info(f"Cycle #{self.stats['cycles']}: {len(self.all_markets)} mkt / {len(self.candidates)} cand | 0 arbs | {elapsed:.1f}s | total_found={self.stats['arbs_found']}")
                await self.check_resolutions()
                if self.stats["cycles"] % 10 == 0:
                    self.all_markets = await fetch_all_markets()
                    self.candidates = prefilter(self.all_markets)
                    logger.info(f"Refreshed: {len(self.all_markets)} markets → {len(self.candidates)} candidates")
                await asyncio.sleep(max(0, self.interval - (time.time() - t0)))


def main():
    parser = argparse.ArgumentParser(description="Polymarket YES+NO combined-price arb scanner")
    parser.add_argument("--min-profit", type=float, default=0.003, help="Min arb profit fraction (default 0.003 = 0.3%%)")
    parser.add_argument("--execute-paper", action="store_true", help="Paper trade every arb hit")
    parser.add_argument("--execute-live", action="store_true", help="Real CLOB trades (USDC required)")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between scans (default 5)")
    args = parser.parse_args()
    scanner = YesNoArbScanner(args.min_profit, args.execute_paper, args.execute_live, args.interval)
    try:
        asyncio.run(scanner.run())
    except KeyboardInterrupt:
        logger.info(f"Stopped. arbs_found={scanner.stats['arbs_found']} | paper_pnl=${scanner.stats['paper_pnl']:.4f}")


if __name__ == "__main__":
    main()
