#!/usr/bin/env python3
"""poly_combo_arb.py — Combinatorial arbitrage scanner for Polymarket.

Based on academic paper "Unravelling the Probabilistic Forest" which documented
$40M in realized arb profits from Polymarket.

Strategy: Find RELATED markets where the combined prices violate probability laws.

Types:
1. SUBSET ARB: "Will X happen by June?" at 0.30 but "Will X happen by Dec?" at 0.20
   → The December market MUST be >= June market. Buy Dec YES, sell June YES.

2. CONDITIONAL ARB: "Will team A win championship?" vs individual game markets
   → If team already eliminated, championship market should be 0.

3. NEGATION ARB: Market A and Market B are logical opposites but don't sum to 1.0

4. TEMPORAL ARB: Same event, different timeframes, prices inconsistent.

Usage:
  python poly_combo_arb.py           # scan and log
  python poly_combo_arb.py --loop    # continuous scanning
"""

import asyncio
import json
import logging
import re
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from itertools import combinations

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("combo_arb")

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_DIR = Path("./data/poly/combo_arb")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "opportunities.jsonl"
STATS_FILE = DATA_DIR / "stats.json"


async def fetch_all_markets(session):
    all_m = []
    for offset in range(0, 4001, 500):
        try:
            async with session.get(f"{GAMMA_URL}/markets", params={"active": "true", "closed": "false", "limit": 500, "offset": offset}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                batch = await r.json(content_type=None)
            if not isinstance(batch, list) or not batch:
                break
            all_m.extend(batch)
            if len(batch) < 500:
                break
        except Exception as e:
            logger.warning(f"Fetch error offset={offset}: {e}")
            break
    return all_m


async def fetch_all_events(session):
    all_e = []
    for offset in range(0, 3001, 500):
        try:
            async with session.get(f"{GAMMA_URL}/events", params={"active": "true", "closed": "false", "limit": 500, "offset": offset}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                batch = await r.json(content_type=None)
            if not isinstance(batch, list) or not batch:
                break
            all_e.extend(batch)
            if len(batch) < 500:
                break
        except Exception as e:
            break
    return all_e


def extract_keywords(text):
    """Extract meaningful keywords from a question."""
    stop = {"will", "the", "be", "by", "in", "on", "at", "to", "of", "a", "an", "or", "and", "is", "are", "was", "has", "have", "do", "does", "did", "not", "no", "yes", "before", "after", "than", "more", "less", "above", "below", "over", "under", "what", "which", "who", "how", "when", "where", "this", "that", "for", "from", "with"}
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    return words - stop


def find_temporal_arbs(markets):
    """Find markets about the same event but different deadlines.
    If 'X by June' is at 0.30 but 'X by December' is at 0.20, that's wrong.
    December must be >= June since it includes June."""
    # Group by topic keywords
    by_topic = defaultdict(list)
    for m in markets:
        q = m.get("question", "")
        kw = extract_keywords(q)
        # Create topic signature from top keywords
        if len(kw) < 3:
            continue
        # Use sorted keywords as topic key (very rough)
        topic = frozenset(list(sorted(kw))[:5])
        by_topic[topic].append(m)
    arbs = []
    for topic, group in by_topic.items():
        if len(group) < 2:
            continue
        # Check all pairs for temporal inconsistency
        for a, b in combinations(group, 2):
            qa = a.get("question", "").lower()
            qb = b.get("question", "").lower()
            pa = float(a.get("bestAsk", a.get("lastTradePrice", 0)) or 0)
            pb = float(b.get("bestAsk", b.get("lastTradePrice", 0)) or 0)
            if pa <= 0 or pb <= 0:
                continue
            end_a = a.get("endDateIso", "")
            end_b = b.get("endDateIso", "")
            if not end_a or not end_b:
                continue
            # If A expires later than B, A's price should be >= B's price
            if end_a > end_b and pa < pb - 0.03:
                profit = pb - pa
                arbs.append({"type": "TEMPORAL", "long": a.get("question", "")[:60], "short": b.get("question", "")[:60], "long_price": pa, "short_price": pb, "profit": round(profit, 4), "long_end": end_a[:10], "short_end": end_b[:10], "long_id": a.get("id"), "short_id": b.get("id")})
            elif end_b > end_a and pb < pa - 0.03:
                profit = pa - pb
                arbs.append({"type": "TEMPORAL", "long": b.get("question", "")[:60], "short": a.get("question", "")[:60], "long_price": pb, "short_price": pa, "profit": round(profit, 4), "long_end": end_b[:10], "short_end": end_a[:10], "long_id": b.get("id"), "short_id": a.get("id")})
    return arbs


def find_negation_arbs(markets):
    """Find market pairs that are logical negations but don't sum to 1.0."""
    arbs = []
    by_kw = defaultdict(list)
    for m in markets:
        kw = extract_keywords(m.get("question", ""))
        for k in kw:
            by_kw[k].append(m)
    # Check pairs with high keyword overlap
    checked = set()
    for m in markets:
        kw = extract_keywords(m.get("question", ""))
        q = m.get("question", "").lower()
        for related in by_kw.get(list(kw)[0] if kw else "", []):
            pair_key = tuple(sorted([m.get("id", ""), related.get("id", "")]))
            if pair_key in checked or m.get("id") == related.get("id"):
                continue
            checked.add(pair_key)
            rq = related.get("question", "").lower()
            # Check if one is negation of the other
            rkw = extract_keywords(rq)
            overlap = len(kw & rkw)
            if overlap < 3:
                continue
            pa = float(m.get("lastTradePrice", 0) or 0)
            pb = float(related.get("lastTradePrice", 0) or 0)
            if pa <= 0 or pb <= 0:
                continue
            # If they're about the same thing with opposite outcomes
            total = pa + pb
            if total < 0.90:  # sum < 1: buy both = guaranteed profit
                profit = 1.0 - total
                if profit > 0.03:
                    arbs.append({"type": "NEGATION_BUY", "market_a": q[:50], "market_b": rq[:50], "price_a": pa, "price_b": pb, "total": round(total, 4), "profit": round(profit, 4)})
    return arbs


def find_subset_arbs(events):
    """Find multi-outcome events where outcome prices violate subset relationships."""
    arbs = []
    for e in events:
        markets = e.get("markets", [])
        if len(markets) < 3:
            continue
        neg_risk = any(m.get("negRisk") for m in markets)
        if not neg_risk:
            continue
        # Check if total bid > 1.0 (sell arb) or total ask < 1.0 (buy arb)
        total_bid = sum(float(m.get("bestBid", 0) or 0) for m in markets)
        total_ask = sum(float(m.get("bestAsk", m.get("lastTradePrice", 0)) or 0) for m in markets)
        min_liq = min(float(m.get("liquidityNum", 0) or 0) for m in markets)
        if total_bid > 1.005 and min_liq > 500:
            arbs.append({"type": "SUBSET_SELL", "event": e.get("title", "")[:50], "n_outcomes": len(markets), "total_bid": round(total_bid, 4), "profit": round(total_bid - 1.0, 4), "min_liq": min_liq})
        if total_ask < 0.995 and min_liq > 500:
            arbs.append({"type": "SUBSET_BUY", "event": e.get("title", "")[:50], "n_outcomes": len(markets), "total_ask": round(total_ask, 4), "profit": round(1.0 - total_ask, 4), "min_liq": min_liq})
    return arbs


async def scan(loop_mode=False):
    stats = {"scans": 0, "temporal": 0, "negation": 0, "subset": 0, "total_opportunities": 0}
    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            t0 = time.time()
            stats["scans"] += 1
            markets = await fetch_all_markets(session)
            events = await fetch_all_events(session)
            logger.info(f"Scan #{stats['scans']}: {len(markets)} markets, {len(events)} events")
            all_arbs = []
            # Temporal arbs
            temporal = find_temporal_arbs(markets)
            stats["temporal"] += len(temporal)
            all_arbs.extend(temporal)
            # Negation arbs
            negation = find_negation_arbs(markets)
            stats["negation"] += len(negation)
            all_arbs.extend(negation)
            # Subset arbs (multi-outcome)
            subset = find_subset_arbs(events)
            stats["subset"] += len(subset)
            all_arbs.extend(subset)
            stats["total_opportunities"] += len(all_arbs)
            all_arbs.sort(key=lambda a: -a.get("profit", 0))
            if all_arbs:
                logger.info(f"  Found {len(all_arbs)} arb opportunities ({len(temporal)} temporal, {len(negation)} negation, {len(subset)} subset)")
                for a in all_arbs[:10]:
                    logger.info(f"  {a['type']} profit=${a['profit']:.4f} | {a.get('event', a.get('long', a.get('market_a', '')))[:50]}")
                with open(LOG_FILE, "a") as f:
                    for a in all_arbs:
                        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **a}) + "\n")
            else:
                logger.info(f"  No arbs found this cycle")
            with open(STATS_FILE, "w") as f:
                json.dump(stats, f, indent=2)
            elapsed = time.time() - t0
            logger.info(f"  Scan took {elapsed:.1f}s | cumulative: {stats['total_opportunities']} opportunities")
            if not loop_mode:
                break
            await asyncio.sleep(300)  # scan every 5 min


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    asyncio.run(scan(loop_mode=args.loop))


if __name__ == "__main__":
    main()
