#!/usr/bin/env python3
"""limitless_sniper.py — WebSocket latency arb scanner.

Connects to Binance WebSocket for sub-second price updates.
When price moves sharply (>0.3% in <10s), immediately checks Limitless
orderbook for stale asks/bids that haven't updated to reflect the move.

A "stale order" = the market outcome is now nearly certain but someone's
limit order is still sitting at the old price. We snipe it.

Example: BTC at $74,000, strike=$73,500. YES should be ~$0.98.
BTC drops to $73,400 in 3 seconds. YES ask still at $0.85 (stale).
Now strike is ABOVE price → YES is worth $0.00. We buy NO at $0.15.
That NO is worth $1.00 → instant 6x profit.

This is the ONLY edge: speed. Not prediction. Speed.

PAPER ONLY — tracks theoretical P&L from detected opportunities.
"""

import asyncio
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("sniper")

LIMITLESS_API = "https://api.limitless.exchange"
BINANCE_WS = "wss://stream.binance.com:9443/ws"

DATA_DIR = Path("./data/poly/limitless_sniper")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATS_FILE = DATA_DIR / "stats.json"
LOG_FILE = DATA_DIR / "opportunities.jsonl"

TICKERS = {
    "btcusdt": ("BTC", "BTCUSDC"),
    "ethusdt": ("ETH", "ETHUSDC"),
    "solusdt": ("SOL", "SOLUSDC"),
    "xrpusdt": ("XRP", "XRPUSDC"),
    "dogeusdt": ("DOGE", "DOGEUSDT"),
    "bnbusdt": ("BNB", "BNBUSDC"),
}

# Minimum price move to trigger a snipe check
MIN_MOVE_PCT = 0.15  # lowered from 0.3 to catch more moves
MOVE_WINDOW = 30  # expanded from 10s to 30s window
# Minimum profit on a $5 bet to count as opportunity
MIN_PROFIT = 1.0


def now_utc():
    return datetime.now(timezone.utc)


def extract_strike(title):
    m = re.search(r'above \$([0-9,]+\.?\d*)', title)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


class PriceTracker:
    """Track price history for each ticker to detect fast moves."""

    def __init__(self):
        self.prices = {}  # ticker -> [(timestamp, price), ...]
        self.current = {}  # ticker -> price

    def update(self, ticker, price, ts):
        self.current[ticker] = price
        if ticker not in self.prices:
            self.prices[ticker] = []
        self.prices[ticker].append((ts, price))
        # Keep only last 60 seconds
        cutoff = ts - 60
        self.prices[ticker] = [(t, p) for t, p in self.prices[ticker] if t > cutoff]

    def get_move(self, ticker, window_sec=MOVE_WINDOW):
        """Get the max price move in the last window_sec seconds."""
        if ticker not in self.prices or len(self.prices[ticker]) < 2:
            return 0, 0, 0
        now = time.time()
        recent = [(t, p) for t, p in self.prices[ticker] if t > now - window_sec]
        if len(recent) < 2:
            return 0, 0, 0
        prices_only = [p for _, p in recent]
        high = max(prices_only)
        low = min(prices_only)
        current = prices_only[-1]
        move_pct = (high - low) / low * 100 if low > 0 else 0
        direction = 1 if current > prices_only[0] else -1
        return move_pct, direction, current


class Sniper:
    def __init__(self):
        self.tracker = PriceTracker()
        self.session = None
        self.markets_cache = []
        self.markets_cache_ts = 0
        self.stats = {"scans": 0, "fast_moves": 0, "stale_orders": 0, "paper_trades": 0, "paper_pnl": 0.0, "best_opportunity": 0.0}
        self._load_stats()

    def _load_stats(self):
        if STATS_FILE.exists():
            try:
                self.stats = json.loads(STATS_FILE.read_text())
            except Exception:
                pass

    def _save_stats(self):
        with open(STATS_FILE, "w") as f:
            json.dump(self.stats, f, indent=2)

    async def _fetch_markets(self):
        """Cache markets, refresh every 60s."""
        now = time.time()
        if now - self.markets_cache_ts < 60 and self.markets_cache:
            return self.markets_cache
        try:
            all_m = []
            for page in range(1, 15):
                async with self.session.get(f"{LIMITLESS_API}/markets/active", params={"limit": 25, "page": page}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    resp = await r.json(content_type=None)
                batch = resp.get("data", []) if isinstance(resp, dict) else resp
                if not batch:
                    break
                all_m.extend(batch)
                if len(batch) < 25:
                    break
            self.markets_cache = all_m
            self.markets_cache_ts = now
        except Exception as e:
            logger.warning(f"Market fetch: {e}")
        return self.markets_cache

    async def _check_stale_orders(self, ticker, move_pct, direction, spot):
        """After a fast move, check if any Limitless orderbooks have stale orders."""
        markets = await self._fetch_markets()
        now = now_utc()
        opportunities = []
        for m in markets:
            title = m.get("title", "")
            if f"${ticker}" not in title:
                continue
            strike = extract_strike(title)
            if not strike:
                continue
            ts = m.get("expirationTimestamp", 0)
            if not ts:
                continue
            exp_ts = int(ts) // 1000 if int(ts) > 1e12 else int(ts)
            exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
            mins_left = (exp_dt - now).total_seconds() / 60
            if mins_left < 0 or mins_left > 30:
                continue
            # What SHOULD the price be after the move?
            above_strike = spot > strike
            dist_from_strike = abs(spot - strike) / strike * 100
            # Log what we see even if no opportunity
            if dist_from_strike < 0.1:
                continue  # truly at the strike
            # Check the orderbook for stale orders
            slug = m.get("slug", "")
            try:
                async with self.session.get(f"{LIMITLESS_API}/markets/{slug}/orderbook", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status != 200:
                        continue
                    book = await r.json(content_type=None)
            except Exception:
                continue
            asks = sorted([(float(a["price"]), float(a.get("size", 0)) / 1e6) for a in book.get("asks", []) if float(a.get("size", 0)) > 0])
            bids = sorted([(float(b["price"]), float(b.get("size", 0)) / 1e6) for b in book.get("bids", []) if float(b.get("size", 0)) > 0], reverse=True)
            if above_strike:
                # YES should be high (>0.80). If there's a cheap YES ask, it's stale.
                fair_yes = min(0.99, 0.5 + dist_from_strike * 10)  # rough fair value
                if asks:
                    best_ask = asks[0][0]
                    ask_size = asks[0][1]
                    if best_ask < fair_yes - 0.10 and ask_size >= 5:
                        # STALE YES ASK — buy it!
                        profit = (1.0 / best_ask - 1) * 5
                        if profit >= MIN_PROFIT:
                            opportunities.append({"type": "STALE_YES_ASK", "ticker": ticker, "side": "YES", "strike": strike, "spot": spot, "dist": round(dist_from_strike, 3), "fair": round(fair_yes, 3), "ask": best_ask, "size": round(ask_size, 2), "profit": round(profit, 2), "mins_left": round(mins_left, 1), "slug": slug, "title": title[:60]})
            else:
                # NO should be high. If YES bid is still high (= NO ask is low), it's stale.
                fair_no = min(0.99, 0.5 + dist_from_strike * 10)
                if bids:
                    best_bid = bids[0][0]
                    bid_size = bids[0][1]
                    no_cost = 1.0 - best_bid
                    if no_cost < (1.0 - fair_no) - 0.10 and bid_size >= 5:
                        profit = (1.0 / no_cost - 1) * 5
                        if profit >= MIN_PROFIT:
                            opportunities.append({"type": "STALE_YES_BID", "ticker": ticker, "side": "NO", "strike": strike, "spot": spot, "dist": round(dist_from_strike, 3), "fair_no": round(fair_no, 3), "bid": best_bid, "no_cost": round(no_cost, 3), "size": round(bid_size, 2), "profit": round(profit, 2), "mins_left": round(mins_left, 1), "slug": slug, "title": title[:60]})
            # Log the best ask/bid vs fair value for debugging
            if asks and above_strike:
                best_ask = asks[0][0]
                fair_yes = min(0.99, 0.5 + dist_from_strike * 10)
                gap = fair_yes - best_ask
                if gap > 0.03:
                    logger.info(f"  NEAR-STALE YES {ticker} | ask={best_ask:.3f} fair={fair_yes:.3f} gap={gap:.3f} dist={dist_from_strike:.2f}% {mins_left:.0f}min | {title[:40]}")
            elif bids and not above_strike:
                best_bid = bids[0][0]
                fair_no = min(0.99, 0.5 + dist_from_strike * 10)
                no_cost = 1.0 - best_bid
                fair_no_cost = 1.0 - fair_no
                gap = fair_no - (1 - no_cost)
                if no_cost < 0.5:
                    logger.info(f"  NEAR-STALE NO {ticker} | bid={best_bid:.3f} no_cost={no_cost:.3f} fair_no={fair_no:.3f} dist={dist_from_strike:.2f}% {mins_left:.0f}min | {title[:40]}")
            await asyncio.sleep(0.1)
        return opportunities

    async def _handle_price_update(self, ticker, price):
        ts = time.time()
        self.tracker.update(ticker, price, ts)
        move_pct, direction, spot = self.tracker.get_move(ticker)
        if move_pct >= MIN_MOVE_PCT:
            self.stats["fast_moves"] += 1
            dir_str = "UP" if direction > 0 else "DOWN"
            logger.info(f"FAST MOVE {ticker} {dir_str} {move_pct:.2f}% in {MOVE_WINDOW}s | spot=${spot:,.2f}")
            # Check for stale orders
            opps = await self._check_stale_orders(ticker, move_pct, direction, spot)
            if opps:
                self.stats["stale_orders"] += len(opps)
                for opp in opps:
                    self.stats["paper_trades"] += 1
                    self.stats["paper_pnl"] += opp["profit"]
                    if opp["profit"] > self.stats["best_opportunity"]:
                        self.stats["best_opportunity"] = opp["profit"]
                    logger.info(f"  STALE ORDER: {opp['type']} {opp['side']} {opp['ticker']} | profit=${opp['profit']:.2f} | ask/bid={opp.get('ask', opp.get('bid', 0)):.3f} fair={opp.get('fair', opp.get('fair_no', 0)):.3f} | dist={opp['dist']:.2f}% {opp['mins_left']:.0f}min | {opp['title']}")
                    with open(LOG_FILE, "a") as f:
                        f.write(json.dumps({"ts": now_utc().isoformat(), **opp}) + "\n")
            self._save_stats()
            # Reset price history after processing a fast move
            self.tracker.prices[ticker] = [(ts, spot)]

    async def run(self):
        logger.info(f"SNIPER | Watching {len(TICKERS)} tickers via Binance WebSocket")
        logger.info(f"  Trigger: >{MIN_MOVE_PCT}% move in {MOVE_WINDOW}s → check Limitless orderbook for stale orders")
        logger.info(f"  Paper only — logging opportunities, no real trades")
        self.session = aiohttp.ClientSession()
        # Pre-fetch markets
        await self._fetch_markets()
        logger.info(f"  Loaded {len(self.markets_cache)} Limitless markets")
        # Subscribe to Binance WebSocket
        streams = "/".join(f"{sym}@trade" for sym in TICKERS.keys())
        ws_url = f"{BINANCE_WS}/{streams}"
        while True:
            try:
                async with self.session.ws_connect(ws_url, heartbeat=30) as ws:
                    logger.info("Connected to Binance WebSocket")
                    self.stats["scans"] = 0
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            symbol = data.get("s", "").lower()
                            price = float(data.get("p", 0))
                            if symbol in TICKERS and price > 0:
                                ticker = TICKERS[symbol][0]
                                await self._handle_price_update(ticker, price)
                                self.stats["scans"] += 1
                                if self.stats["scans"] % 5000 == 0:
                                    logger.info(f"  Heartbeat: {self.stats['scans']} ticks | fast_moves={self.stats['fast_moves']} stale={self.stats['stale_orders']} paper_pnl=${self.stats['paper_pnl']:.2f}")
                                    self._save_stats()
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except Exception as e:
                logger.warning(f"WebSocket error: {e}")
            logger.info("Reconnecting in 5s...")
            await asyncio.sleep(5)


async def main():
    sniper = Sniper()
    try:
        await sniper.run()
    except KeyboardInterrupt:
        logger.info(f"Stopped. fast_moves={sniper.stats['fast_moves']} stale={sniper.stats['stale_orders']} paper_pnl=${sniper.stats['paper_pnl']:.2f}")
        if sniper.session:
            await sniper.session.close()

if __name__ == "__main__":
    asyncio.run(main())
