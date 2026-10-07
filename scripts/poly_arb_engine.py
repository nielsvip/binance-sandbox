#!/usr/bin/env python3
"""poly_arb_engine.py — Cross-platform prediction market arbitrage engine.

STRATEGY 1: Intra-platform YES+NO arb (Polymarket or Limitless)
  Buy YES + NO on same market when combined ask < $1.00 → guaranteed profit.

STRATEGY 2: Cross-platform arb (Polymarket <-> Limitless)
  Same event, different prices. Buy YES on cheaper platform, buy NO on the other.
  If combined cost < $1.00 - fees → guaranteed profit regardless of outcome.

STRATEGY 3: Spread monitoring (for future market-making)
  Track bid-ask spreads across both platforms. Log when spreads widen beyond
  normal (opportunity for MM). Does not trade — just logs and alerts.

Both platforms use the Conditional Token Framework (CTF) with USDC collateral.
Limitless is on Base, Polymarket is on Polygon. Same YES/NO binary model.

CURRENT STATE (2026-03-16):
  - Polymarket: very efficient (spreads = 1 tick / 0.1%). No intra arb.
  - Limitless: wider spreads (1-5%). Possible intra arb during fast moves.
  - Cross-platform: limited market overlap (different market types).
  - Best opportunity: Limitless MM or cross-platform during fast crypto moves.

MARKET MATCHING:
  Crypto hourly markets are the primary arb target:
  - Polymarket: "Bitcoin Up or Down - March 16, 5:00PM-5:15PM ET"
  - Limitless:  "$BTC above $84,250.00 on Mar 16, 21:00 UTC?"
  These resolve to the same underlying price at the same time.
  Match by: ticker (BTC/ETH/SOL/DOGE) + expiration timestamp.

Usage:
  python poly_arb_engine.py                           # scan-only, both platforms
  python poly_arb_engine.py --execute-paper           # paper trade arb opportunities
  python poly_arb_engine.py --min-profit 0.005        # 0.5% min combined discount
  python poly_arb_engine.py --interval 10             # scan every 10s
  python poly_arb_engine.py --intra-only              # only intra-platform arb
  python poly_arb_engine.py --cross-only              # only cross-platform arb
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("arb_engine")

# --- Platform endpoints ---
POLY_GAMMA = "https://gamma-api.polymarket.com"
POLY_CLOB = "https://clob.polymarket.com"
LIMITLESS_API = "https://api.limitless.exchange"

# --- Data ---
DATA_DIR = Path("./data/poly/arb_engine")
DATA_DIR.mkdir(parents=True, exist_ok=True)
ARB_LOG = DATA_DIR / "arb_opportunities.jsonl"
SPREAD_LOG = DATA_DIR / "spread_monitor.jsonl"
PAPER_FILE = DATA_DIR / "paper_positions.json"
STATS_FILE = DATA_DIR / "stats.json"

# --- Fees ---
POLY_FEE_RATE = 0.02  # 2% of profit (most markets fee-free, crypto taker ~1.56%)
LIMITLESS_FEE_RATE = 0.02  # ~2% estimate (baked into spread)
COMBINED_FEE_BUFFER = 0.005  # 0.5% safety buffer on top of fees

# --- Crypto tickers we track across platforms ---
CRYPTO_TICKERS = {"BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "eth"], "SOL": ["solana", "sol"], "DOGE": ["doge", "dogecoin"], "XRP": ["xrp", "ripple"], "BNB": ["bnb"], "ADA": ["ada", "cardano"], "AVAX": ["avax", "avalanche"], "LINK": ["link", "chainlink"], "SUI": ["sui"], "PEPE": ["pepe"], "WIF": ["wif"]}
# Football/soccer teams for cross-platform matching
FOOTBALL_TEAMS = ["arsenal", "chelsea", "liverpool", "man city", "manchester city", "man united", "manchester united", "tottenham", "spurs", "barcelona", "real madrid", "bayern", "juventus", "inter milan", "ac milan", "psg", "paris saint", "dortmund", "atletico", "napoli", "roma", "lazio", "fiorentina", "sporting", "porto", "benfica", "ajax", "feyenoord", "celtic", "rangers", "brentford", "wolves", "watford", "wrexham", "portsmouth", "derby", "rayo vallecano", "levante", "cremonese"]

PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def extract_ticker(title: str) -> Optional[str]:
    tl = title.lower()
    for ticker, keywords in CRYPTO_TICKERS.items():
        if any(k in tl for k in keywords):
            return ticker
    # Try $TICKER pattern
    m = re.search(r'\$([A-Z]{2,6})', title)
    if m:
        return m.group(1)
    return None


def extract_expiry_ts(m: dict, platform: str) -> Optional[int]:
    """Extract expiration timestamp in epoch seconds."""
    if platform == "limitless":
        ts = m.get("expirationTimestamp") or m.get("expiration_timestamp")
        if ts:
            return int(ts) // 1000 if int(ts) > 1e12 else int(ts)
    elif platform == "polymarket":
        end = m.get("endDateIso", "")
        dt = parse_iso(end)
        if dt:
            return int(dt.timestamp())
    return None


def extract_strike(title: str) -> Optional[float]:
    """Extract strike price from market title like '$BTC above $84,250.00'."""
    m = re.search(r'\$([0-9,]+\.?\d*)', title)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


class NormalizedMarket:
    """Platform-agnostic market representation for matching."""
    __slots__ = ("platform", "market_id", "slug", "title", "ticker", "strike", "expiry_ts", "yes_token", "no_token", "yes_price", "no_price", "volume", "raw")

    def __init__(self, platform: str, market_id: str, slug: str, title: str, ticker: str, strike: Optional[float], expiry_ts: Optional[int], yes_token: str, no_token: str, yes_price: float, no_price: float, volume: float, raw: dict):
        self.platform = platform
        self.market_id = market_id
        self.slug = slug
        self.title = title
        self.ticker = ticker
        self.strike = strike
        self.expiry_ts = expiry_ts
        self.yes_token = yes_token
        self.no_token = no_token
        self.yes_price = yes_price
        self.no_price = no_price
        self.volume = volume
        self.raw = raw

    def match_key(self) -> Optional[str]:
        """Generate a key for matching across platforms."""
        if not self.ticker or not self.expiry_ts:
            return None
        return f"{self.ticker}:{self.expiry_ts}"

    def __repr__(self):
        return f"NM({self.platform}:{self.ticker}@{self.expiry_ts} yes={self.yes_price:.3f} no={self.no_price:.3f})"


# ─── Polymarket fetcher ────────────────────────────────────────────

async def fetch_poly_markets(session: aiohttp.ClientSession, max_pages: int = 4) -> list[NormalizedMarket]:
    """Fetch active Polymarket markets and normalize."""
    all_raw = []
    for offset in range(0, max_pages * 500, 500):
        try:
            async with session.get(f"{POLY_GAMMA}/markets", params={"active": "true", "closed": "false", "order": "volumeNum", "ascending": "false", "limit": 500, "offset": offset}, proxy=PROXY, timeout=aiohttp.ClientTimeout(total=20)) as r:
                batch = await r.json(content_type=None)
            if not isinstance(batch, list):
                break
            all_raw.extend(batch)
            if len(batch) < 500:
                break
        except Exception as e:
            logger.warning(f"poly fetch offset={offset}: {e}")
            break
    markets = []
    for m in all_raw:
        title = m.get("question", "")
        tok_raw = m.get("clobTokenIds", "[]")
        try:
            toks = json.loads(tok_raw) if isinstance(tok_raw, str) else tok_raw
        except Exception:
            continue
        if len(toks) < 2:
            continue
        lp = float(m.get("lastTradePrice", 0) or 0)
        if lp < 0.05 or lp > 0.95:
            continue  # extreme prices = no arb possible (spread too tight)
        ask = float(m.get("bestAsk", lp) or lp)
        liq = float(m.get("liquidityNum", 0) or 0)
        if liq < 50:
            continue  # too thin to fill
        ticker = extract_ticker(title)
        strike = extract_strike(title)
        nm = NormalizedMarket(platform="polymarket", market_id=m.get("id", ""), slug=m.get("slug", ""), title=title[:100], ticker=ticker or "OTHER", strike=strike, expiry_ts=extract_expiry_ts(m, "polymarket"), yes_token=toks[0], no_token=toks[1], yes_price=ask if ask > 0 else lp, no_price=1.0 - (ask if ask > 0 else lp), volume=float(m.get("volumeNum", 0) or 0), raw=m)
        markets.append(nm)
    return markets


# ─── Limitless fetcher ─────────────────────────────────────────────

async def fetch_limitless_markets(session: aiohttp.ClientSession, max_pages: int = 20) -> list[NormalizedMarket]:
    """Fetch active Limitless markets and normalize. API max limit=25, uses page= param."""
    all_raw = []
    for page in range(1, max_pages + 1):
        try:
            async with session.get(f"{LIMITLESS_API}/markets/active", params={"limit": 25, "page": page}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                resp = await r.json(content_type=None)
            batch = resp.get("data", resp) if isinstance(resp, dict) else resp
            if not isinstance(batch, list) or not batch:
                break
            all_raw.extend(batch)
            if len(batch) < 25:
                break
        except Exception as e:
            logger.warning(f"limitless fetch page={page}: {e}")
            break
    markets = []
    for m in all_raw:
        title = m.get("title", "")
        tokens = m.get("tokens", {})
        yes_tok = tokens.get("yes", "")
        no_tok = tokens.get("no", "")
        if not yes_tok:
            continue
        prices = m.get("prices", [0, 0])
        yes_p = float(prices[0]) if len(prices) > 0 else 0
        no_p = float(prices[1]) if len(prices) > 1 else 1.0 - yes_p
        if yes_p < 0.05 or yes_p > 0.95:
            continue  # extreme prices = no arb room
        ticker = extract_ticker(title)
        strike = extract_strike(title)
        nm = NormalizedMarket(platform="limitless", market_id=str(m.get("id", "")), slug=m.get("slug", ""), title=title[:100], ticker=ticker or "OTHER", strike=strike, expiry_ts=extract_expiry_ts(m, "limitless"), yes_token=yes_tok, no_token=no_tok, yes_price=yes_p, no_price=no_p, volume=float(m.get("volumeFormatted", "0").replace(",", "") or 0), raw=m)
        markets.append(nm)
    return markets


# ─── Order book fetching ──────────────────────────────────────────

async def fetch_poly_book(session: aiohttp.ClientSession, token_id: str) -> dict:
    try:
        async with session.get(f"{POLY_CLOB}/book", params={"token_id": token_id}, proxy=PROXY, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


async def fetch_limitless_book(session: aiohttp.ClientSession, slug: str) -> dict:
    try:
        async with session.get(f"{LIMITLESS_API}/markets/{slug}/orderbook", timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


def best_ask(book: dict) -> tuple[float, float]:
    """Return (price, size) of best ask. Price=inf if no asks."""
    asks = book.get("asks", [])
    if not asks:
        return float("inf"), 0.0
    parsed = []
    for a in asks:
        p = float(a.get("price", 1.0))
        s = float(a.get("size", 0))
        if s > 0:
            parsed.append((p, s))
    if not parsed:
        return float("inf"), 0.0
    parsed.sort()
    return parsed[0]


def best_bid(book: dict) -> tuple[float, float]:
    """Return (price, size) of best bid. Price=0 if no bids."""
    bids = book.get("bids", [])
    if not bids:
        return 0.0, 0.0
    parsed = []
    for b in bids:
        p = float(b.get("price", 0))
        s = float(b.get("size", 0))
        if s > 0:
            parsed.append((p, s))
    if not parsed:
        return 0.0, 0.0
    parsed.sort(reverse=True)
    return parsed[0]


# ─── Arb detection ────────────────────────────────────────────────

class ArbOpportunity:
    __slots__ = ("arb_type", "platform_a", "platform_b", "market_a", "market_b", "ask_yes_a", "ask_no_b", "combined_cost", "gross_profit", "net_profit", "max_shares", "max_profit_usdc", "ticker", "expiry_ts", "found_at")

    def __init__(self, arb_type: str, platform_a: str, platform_b: str, market_a: NormalizedMarket, market_b: Optional[NormalizedMarket], ask_yes_a: float, ask_no_b: float, combined_cost: float, gross_profit: float, net_profit: float, max_shares: float, max_profit_usdc: float, ticker: str, expiry_ts: Optional[int], found_at: str):
        self.arb_type = arb_type
        self.platform_a = platform_a
        self.platform_b = platform_b
        self.market_a = market_a
        self.market_b = market_b
        self.ask_yes_a = ask_yes_a
        self.ask_no_b = ask_no_b
        self.combined_cost = combined_cost
        self.gross_profit = gross_profit
        self.net_profit = net_profit
        self.max_shares = max_shares
        self.max_profit_usdc = max_profit_usdc
        self.ticker = ticker
        self.expiry_ts = expiry_ts
        self.found_at = found_at

    def to_dict(self) -> dict:
        return {"arb_type": self.arb_type, "platform_a": self.platform_a, "platform_b": self.platform_b, "title_a": self.market_a.title, "title_b": self.market_b.title if self.market_b else "", "ticker": self.ticker, "expiry_ts": self.expiry_ts, "ask_yes": self.ask_yes_a, "ask_no": self.ask_no_b, "combined": round(self.combined_cost, 6), "gross_profit_pct": round(self.gross_profit * 100, 4), "net_profit_pct": round(self.net_profit * 100, 4), "max_shares": round(self.max_shares, 2), "max_profit_usdc": round(self.max_profit_usdc, 4), "found_at": self.found_at}


# ─── Main engine ──────────────────────────────────────────────────

class ArbEngine:
    def __init__(self, min_profit: float, execute_paper: bool, interval: float, intra_only: bool, cross_only: bool):
        self.min_profit = min_profit
        self.execute_paper = execute_paper
        self.interval = interval
        self.intra_only = intra_only
        self.cross_only = cross_only
        self.session: Optional[aiohttp.ClientSession] = None
        self.paper: dict = {}
        self.stats = {"cycles": 0, "poly_markets": 0, "limitless_markets": 0, "intra_arbs": 0, "cross_arbs": 0, "cross_matches": 0, "paper_entered": 0, "paper_pnl": 0.0, "best_arb_pct": 0.0}
        if PAPER_FILE.exists():
            try:
                with open(PAPER_FILE) as f:
                    self.paper = json.load(f)
            except Exception:
                pass

    def _save_paper(self):
        with open(PAPER_FILE, "w") as f:
            json.dump(self.paper, f, indent=2)

    def _save_stats(self):
        with open(STATS_FILE, "w") as f:
            json.dump(self.stats, f, indent=2)

    async def _scan_intra_platform(self, markets: list[NormalizedMarket], platform: str) -> list[ArbOpportunity]:
        """Scan for YES+NO arb within a single platform."""
        arbs = []
        sem = asyncio.Semaphore(40)
        async def check_one(m: NormalizedMarket):
            async with sem:
                if platform == "polymarket":
                    yes_book = await fetch_poly_book(self.session, m.yes_token)
                    no_book = await fetch_poly_book(self.session, m.no_token)
                else:
                    book = await fetch_limitless_book(self.session, m.slug)
                    if not book:
                        return None
                    # Limitless returns single book for the market — asks are YES sells, need NO book separately
                    # Actually the limitless book is for YES token. We need to construct NO from it.
                    # For YES token: best_ask = cheapest YES share
                    # For NO: best_ask ≈ 1 - best_bid of YES (since buying NO = selling YES)
                    ask_y, sz_y = best_ask(book)
                    bid_y, sz_bid = best_bid(book)
                    if ask_y == float("inf"):
                        return None
                    # NO ask ≈ 1 - YES bid (if someone is willing to sell YES at bid, you can buy NO at 1-bid)
                    ask_n = 1.0 - bid_y if bid_y > 0 else float("inf")
                    if ask_n == float("inf"):
                        return None
                    combined = ask_y + ask_n
                    profit = 1.0 - combined
                    if profit >= self.min_profit:
                        fee = profit * (LIMITLESS_FEE_RATE + COMBINED_FEE_BUFFER)
                        net = profit - fee
                        if net > 0:
                            max_sh = min(sz_y, sz_bid)
                            return ArbOpportunity(arb_type="INTRA", platform_a=platform, platform_b=platform, market_a=m, market_b=None, ask_yes_a=ask_y, ask_no_b=ask_n, combined_cost=combined, gross_profit=profit, net_profit=net, max_shares=max_sh, max_profit_usdc=net * max_sh, ticker=m.ticker, expiry_ts=m.expiry_ts, found_at=now_utc().isoformat())
                    return None
                if not yes_book or not no_book:
                    return None
                ask_y, sz_y = best_ask(yes_book)
                ask_n, sz_n = best_ask(no_book)
                if ask_y == float("inf") or ask_n == float("inf"):
                    return None
                combined = ask_y + ask_n
                profit = 1.0 - combined
                if profit >= self.min_profit:
                    fee = profit * (POLY_FEE_RATE + COMBINED_FEE_BUFFER)
                    net = profit - fee
                    if net > 0:
                        max_sh = min(sz_y, sz_n)
                        return ArbOpportunity(arb_type="INTRA", platform_a=platform, platform_b=platform, market_a=m, market_b=None, ask_yes_a=ask_y, ask_no_b=ask_n, combined_cost=combined, gross_profit=profit, net_profit=net, max_shares=max_sh, max_profit_usdc=net * max_sh, ticker=m.ticker, expiry_ts=m.expiry_ts, found_at=now_utc().isoformat())
                return None
        results = await asyncio.gather(*[check_one(m) for m in markets], return_exceptions=True)
        for r in results:
            if isinstance(r, ArbOpportunity):
                arbs.append(r)
        return arbs

    async def _scan_cross_platform(self, poly_markets: list[NormalizedMarket], limitless_markets: list[NormalizedMarket]) -> list[ArbOpportunity]:
        """Scan for cross-platform arb between Polymarket and Limitless."""
        arbs = []
        # Index by match key
        poly_by_key: dict[str, list[NormalizedMarket]] = {}
        for m in poly_markets:
            key = m.match_key()
            if key:
                poly_by_key.setdefault(key, []).append(m)
        limitless_by_key: dict[str, list[NormalizedMarket]] = {}
        for m in limitless_markets:
            key = m.match_key()
            if key:
                limitless_by_key.setdefault(key, []).append(m)
        # Find overlapping keys
        common_keys = set(poly_by_key.keys()) & set(limitless_by_key.keys())
        self.stats["cross_matches"] = len(common_keys)
        if not common_keys:
            return arbs
        sem = asyncio.Semaphore(30)
        async def check_pair(poly_m: NormalizedMarket, lim_m: NormalizedMarket):
            async with sem:
                # Get books from both platforms
                poly_yes_book, poly_no_book = await asyncio.gather(fetch_poly_book(self.session, poly_m.yes_token), fetch_poly_book(self.session, poly_m.no_token))
                lim_book = await fetch_limitless_book(self.session, lim_m.slug)
                if not lim_book:
                    return []
                poly_ask_yes, poly_sz_yes = best_ask(poly_yes_book) if poly_yes_book else (float("inf"), 0)
                poly_ask_no, poly_sz_no = best_ask(poly_no_book) if poly_no_book else (float("inf"), 0)
                lim_ask_yes, lim_sz_yes = best_ask(lim_book)
                lim_bid_yes, lim_sz_bid = best_bid(lim_book)
                lim_ask_no = 1.0 - lim_bid_yes if lim_bid_yes > 0 else float("inf")
                lim_sz_no = lim_sz_bid
                results = []
                # Arb 1: Buy YES on Poly, buy NO on Limitless
                if poly_ask_yes < float("inf") and lim_ask_no < float("inf"):
                    combined = poly_ask_yes + lim_ask_no
                    profit = 1.0 - combined
                    if profit >= self.min_profit:
                        fee = profit * (POLY_FEE_RATE + LIMITLESS_FEE_RATE + COMBINED_FEE_BUFFER)
                        net = profit - fee
                        if net > 0:
                            max_sh = min(poly_sz_yes, lim_sz_no)
                            results.append(ArbOpportunity(arb_type="CROSS", platform_a="polymarket", platform_b="limitless", market_a=poly_m, market_b=lim_m, ask_yes_a=poly_ask_yes, ask_no_b=lim_ask_no, combined_cost=combined, gross_profit=profit, net_profit=net, max_shares=max_sh, max_profit_usdc=net * max_sh, ticker=poly_m.ticker, expiry_ts=poly_m.expiry_ts, found_at=now_utc().isoformat()))
                # Arb 2: Buy YES on Limitless, buy NO on Poly
                if lim_ask_yes < float("inf") and poly_ask_no < float("inf"):
                    combined = lim_ask_yes + poly_ask_no
                    profit = 1.0 - combined
                    if profit >= self.min_profit:
                        fee = profit * (POLY_FEE_RATE + LIMITLESS_FEE_RATE + COMBINED_FEE_BUFFER)
                        net = profit - fee
                        if net > 0:
                            max_sh = min(lim_sz_yes, poly_sz_no)
                            results.append(ArbOpportunity(arb_type="CROSS", platform_a="limitless", platform_b="polymarket", market_a=lim_m, market_b=poly_m, ask_yes_a=lim_ask_yes, ask_no_b=poly_ask_no, combined_cost=combined, gross_profit=profit, net_profit=net, max_shares=max_sh, max_profit_usdc=net * max_sh, ticker=lim_m.ticker, expiry_ts=lim_m.expiry_ts, found_at=now_utc().isoformat()))
                return results
        tasks = []
        for key in common_keys:
            for pm in poly_by_key[key]:
                for lm in limitless_by_key[key]:
                    tasks.append(check_pair(pm, lm))
        if tasks:
            pair_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in pair_results:
                if isinstance(r, list):
                    arbs.extend(r)
        return arbs

    async def _scan_cycle(self):
        t0 = time.time()
        self.stats["cycles"] += 1
        # Fetch markets from both platforms in parallel
        poly_task = fetch_poly_markets(self.session)
        lim_task = fetch_limitless_markets(self.session)
        poly_markets, limitless_markets = await asyncio.gather(poly_task, lim_task)
        self.stats["poly_markets"] = len(poly_markets)
        self.stats["limitless_markets"] = len(limitless_markets)
        all_arbs: list[ArbOpportunity] = []
        # Intra-platform arb
        if not self.cross_only:
            if poly_markets:
                poly_intra = await self._scan_intra_platform(poly_markets, "polymarket")
                all_arbs.extend(poly_intra)
            if limitless_markets:
                lim_intra = await self._scan_intra_platform(limitless_markets, "limitless")
                all_arbs.extend(lim_intra)
        # Cross-platform arb
        if not self.intra_only and poly_markets and limitless_markets:
            cross = await self._scan_cross_platform(poly_markets, limitless_markets)
            all_arbs.extend(cross)
        elapsed = time.time() - t0
        # Sort by net profit descending
        all_arbs.sort(key=lambda a: -a.net_profit)
        # Count
        intra = [a for a in all_arbs if a.arb_type == "INTRA"]
        cross = [a for a in all_arbs if a.arb_type == "CROSS"]
        self.stats["intra_arbs"] += len(intra)
        self.stats["cross_arbs"] += len(cross)
        if all_arbs:
            best = all_arbs[0]
            if best.net_profit * 100 > self.stats["best_arb_pct"]:
                self.stats["best_arb_pct"] = round(best.net_profit * 100, 4)
            logger.info(f"=== CYCLE #{self.stats['cycles']} | {len(all_arbs)} ARB(S) ({len(intra)} intra, {len(cross)} cross) | {elapsed:.1f}s | poly={len(poly_markets)} lim={len(limitless_markets)} matches={self.stats['cross_matches']} ===")
            for a in all_arbs:
                logger.info(f"  {a.arb_type} {a.platform_a}→{a.platform_b} | {a.ticker} | YES={a.ask_yes_a:.4f} + NO={a.ask_no_b:.4f} = {a.combined_cost:.4f} | net={a.net_profit*100:.3f}% max_profit=${a.max_profit_usdc:.2f} | {a.market_a.title[:50]}")
                with open(ARB_LOG, "a") as f:
                    f.write(json.dumps(a.to_dict()) + "\n")
                if self.execute_paper:
                    paper_key = f"{a.arb_type}:{a.market_a.market_id}:{a.market_b.market_id if a.market_b else 'self'}"
                    if paper_key not in self.paper:
                        bet = 10.0
                        shares = bet / a.combined_cost if a.combined_cost > 0 else 0
                        self.paper[paper_key] = {"arb_type": a.arb_type, "platform_a": a.platform_a, "platform_b": a.platform_b, "ticker": a.ticker, "combined": a.combined_cost, "net_profit_pct": round(a.net_profit * 100, 4), "bet_usdc": bet, "shares": round(shares, 2), "expected_profit": round(a.net_profit * shares, 4), "entered_at": now_utc().isoformat(), "title_a": a.market_a.title, "title_b": a.market_b.title if a.market_b else ""}
                        self.stats["paper_entered"] += 1
                        self._save_paper()
                        logger.info(f"  PAPER: ${bet} @ combined={a.combined_cost:.4f} | expected=${a.net_profit * shares:.4f}")
        else:
            logger.info(f"Cycle #{self.stats['cycles']}: 0 arbs | {elapsed:.1f}s | poly={len(poly_markets)} lim={len(limitless_markets)} crypto_matches={self.stats['cross_matches']}")
        # Log tightest spreads from Limitless (most likely to flip to arb)
        if limitless_markets and self.stats["cycles"] % 5 == 1:
            await self._log_tight_spreads(limitless_markets)
        self._save_stats()

    async def _log_tight_spreads(self, markets: list[NormalizedMarket]):
        """Log markets with tightest spreads (closest to arb)."""
        results = []
        for m in markets[:80]:
            try:
                book = await fetch_limitless_book(self.session, m.slug)
                if not book:
                    continue
                ay, sy = best_ask(book)
                by, sby = best_bid(book)
                if ay == float("inf") or by <= 0:
                    continue
                spread = ay - by
                combined = ay + (1.0 - by)
                profit = 1.0 - combined
                results.append({"ts": now_utc().isoformat(), "platform": "limitless", "title": m.title[:70], "ticker": m.ticker, "ask_yes": round(ay, 4), "bid_yes": round(by, 4), "spread": round(spread, 4), "combined_cost": round(combined, 4), "arb_gap_pct": round(profit * 100, 3), "volume": m.volume})
            except Exception:
                pass
            await asyncio.sleep(0.35)
        results.sort(key=lambda x: -x["arb_gap_pct"])
        if results:
            top = results[:5]
            logger.info(f"  Tightest Limitless spreads: {top[0]['title'][:40]} gap={top[0]['arb_gap_pct']:+.2f}% spread={top[0]['spread']:.3f}")
            with open(SPREAD_LOG, "a") as f:
                for r in top:
                    f.write(json.dumps(r) + "\n")

    async def run(self):
        mode_str = "INTRA-ONLY" if self.intra_only else ("CROSS-ONLY" if self.cross_only else "FULL")
        logger.info(f"Arb Engine | mode={mode_str} | min_profit={self.min_profit:.2%} | paper={'YES' if self.execute_paper else 'SCAN'} | interval={self.interval}s")
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            while True:
                try:
                    await self._scan_cycle()
                except Exception as e:
                    logger.error(f"Cycle error: {e}", exc_info=True)
                await asyncio.sleep(self.interval)


def main():
    parser = argparse.ArgumentParser(description="Cross-platform prediction market arb engine (Polymarket + Limitless)")
    parser.add_argument("--min-profit", type=float, default=0.003, help="Min arb profit fraction after fees (default 0.003 = 0.3%%)")
    parser.add_argument("--execute-paper", action="store_true", help="Paper trade every arb opportunity")
    parser.add_argument("--interval", type=float, default=15.0, help="Seconds between scan cycles (default 15)")
    parser.add_argument("--intra-only", action="store_true", help="Only scan intra-platform arb (YES+NO on same platform)")
    parser.add_argument("--cross-only", action="store_true", help="Only scan cross-platform arb (Polymarket <-> Limitless)")
    args = parser.parse_args()
    engine = ArbEngine(min_profit=args.min_profit, execute_paper=args.execute_paper, interval=args.interval, intra_only=args.intra_only, cross_only=args.cross_only)
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logger.info(f"Stopped. intra_arbs={engine.stats['intra_arbs']} cross_arbs={engine.stats['cross_arbs']} paper_pnl=${engine.stats['paper_pnl']:.4f}")


if __name__ == "__main__":
    main()
