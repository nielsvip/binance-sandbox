#!/usr/bin/env python3
"""limitless_sandbox.py — Paper trading with REAL number tracking.

RULES:
1. NO live orders. Paper only.
2. Entry price = actual orderbook ASK (not display price)
3. Resolution = Limitless API winningOutcomeIndex (not Binance)
4. PnL = (payout - cost). Win = $5/entry - $5. Loss = -$5. Nothing else.
5. Every cycle: log simulated balance
6. Position only counts if orderbook has liquidity at entry price

Usage:
  python limitless_sandbox.py --interval 15
"""

import argparse
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
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg
load_environment_from_gpg(None)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("lim_sandbox")

LIMITLESS_API = "https://api.limitless.exchange"
BINANCE_API = "https://api.binance.com"
DATA_DIR = Path("./data/poly/limitless_sandbox")
DATA_DIR.mkdir(parents=True, exist_ok=True)
POSITIONS_FILE = DATA_DIR / "positions.json"
TRADES_LOG = DATA_DIR / "trades.jsonl"
STATS_FILE = DATA_DIR / "stats.json"
INDICATOR_FILE = Path("./data/latest_market_data.json")

TICKER_TO_BINANCE = {"BTC": "BTCUSDC", "ETH": "ETHUSDC", "SOL": "SOLUSDC", "DOGE": "DOGEUSDT", "XRP": "XRPUSDC", "BNB": "BNBUSDC", "ADA": "ADAUSDC", "AVAX": "AVAXUSDC", "SUI": "SUIUSDT", "LINK": "LINKUSDC"}
TICKER_TO_SYM = {t: t + "USDC" for t in TICKER_TO_BINANCE}

STARTING_BALANCE = 100.0
BET_SIZE = 5.0
MAX_EXPOSURE = 40.0
MAX_HOURS = 0.05  # 3 min only — outcome is nearly certain, stale orders are the edge
EV_THRESHOLD = 10.0  # need 10%+ edge — only trade when we're very confident


def now_utc():
    return datetime.now(timezone.utc)


def extract_ticker(title):
    m = re.search(r'\$([A-Z]{2,6})', title)
    return m.group(1) if m else None


def extract_strike(title):
    m = re.search(r'above \$([0-9,]+\.?\d*)', title)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


# --- Indicator-based projection (same as limitless_trader.py) ---

_IND_CACHE = {}
_IND_TS = 0


def load_indicators():
    global _IND_CACHE, _IND_TS
    now = time.time()
    if now - _IND_TS < 5 and _IND_CACHE:
        return _IND_CACHE
    try:
        if INDICATOR_FILE.exists():
            with open(INDICATOR_FILE) as f:
                _IND_CACHE = json.load(f)
            _IND_TS = now
    except Exception:
        pass
    # Merge Redis hot_metrics
    try:
        import redis
        r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        for sym in list(_IND_CACHE.keys()):
            raw = r.get(f"hot_metrics:{sym}")
            if raw:
                hot = json.loads(raw)
                tick_ts = hot.get("_tick_ts", 0)
                if tick_ts > _IND_CACHE[sym].get("ts", 0):
                    for hk, ik in [("k_1m", "stoch_k_1m"), ("d_1m", "stoch_d_1m"), ("k_1m_prev", "k_1m_prev"), ("k_3m", "stoch_k_3m"), ("d_3m", "stoch_d_3m"), ("k_3m_prev", "k_3m_prev"), ("price", "current_price")]:
                        if hk in hot:
                            val = float(hot[hk]) if isinstance(hot[hk], (int, float)) else hot[hk]
                            # Skip stoch values of 0.0 — artifact of partial candle start
                            if hk in ("k_1m", "k_3m", "d_1m", "d_3m") and val == 0.0:
                                continue
                            _IND_CACHE[sym][ik] = val
    except Exception:
        pass
    return _IND_CACHE


def project_price(sym, minutes, indicators):
    price = indicators.get("current_price", 0)
    if not price:
        return 0, 0
    spm, wt = 0, 0
    for tf, cm, w in [("3m", 3, 0.40), ("15m", 15, 0.30), ("1h", 60, 0.20)]:
        s = indicators.get(f"lr_trend_{tf}", 0)
        if s:
            spm += (s / cm) * w
            wt += w
    if wt > 0:
        spm /= wt
    lr = price + spm * minutes
    sa = 0
    for tf, w in [("1m", 0.35), ("3m", 0.30), ("15m", 0.20), ("1h", 0.15)]:
        k = indicators.get(f"stoch_k_{tf}", indicators.get(f"k_{tf}_prev", 50))
        kp = indicators.get(f"k_{tf}_prev", k)
        dk = k - kp
        if k > 85 and dk < 0:
            sa -= w * 0.3
        elif k > 85:
            sa += w * 0.1
        elif k < 15 and dk > 0:
            sa += w * 0.3
        elif k < 15:
            sa -= w * 0.1
        else:
            sa += w * (dk / 30)
    atr = indicators.get("atr_3m", price * 0.003) if minutes <= 10 else indicators.get("atr_15m", price * 0.003)
    if not atr:
        atr = price * 0.003
    sa *= atr
    tf_dc = "3m" if minutes <= 10 else "15m"
    dh = indicators.get(f"dc_high_{tf_dc}", price * 1.1)
    dl = indicators.get(f"dc_low_{tf_dc}", price * 0.9)
    dr = dh - dl if dh > dl else 1
    dp = (price - dl) / dr
    dc = -0.15 * atr if dp > 0.9 else (0.15 * atr if dp < 0.1 else 0)
    hs = sum(1 if indicators.get(f"ha_{tf}") == "green" else (-1 if indicators.get(f"ha_{tf}") == "red" else 0) for tf in ["3m", "15m", "1h"])
    ha = hs * 0.05 * atr
    wta = sum(indicators.get(f"wt_score_{tf}", 0) * 0.005 * atr for tf in ["3m", "15m"])
    va = indicators.get("velocity", 0) * 0.02 * atr
    proj = lr + sa + dc + ha + wta + va
    comps = [spm * minutes, sa, dc, ha]
    bu = sum(1 for c in comps if c > 0)
    be = sum(1 for c in comps if c < 0)
    return proj, abs(bu - be) / max(len(comps), 1)


def get_prob(sym, strike, minutes, spot):
    indicators = load_indicators().get(sym, {})
    if not indicators:
        return 0.5
    proj, conf = project_price(sym, minutes, indicators)
    if proj <= 0:
        return 0.5
    dist = proj - strike
    atr = indicators.get("atr_3m", spot * 0.003) if minutes <= 10 else indicators.get("atr_15m", spot * 0.003)
    if not atr:
        atr = spot * 0.003
    # Use a fraction of ATR as uncertainty — ATR is the FULL candle range,
    # but price movement in a few minutes is much smaller
    # Scale: sqrt(minutes/candle_period) * ATR * damping_factor
    candle_min = 3 if minutes <= 10 else 15
    unc = atr * math.sqrt(max(0.5, minutes / candle_min)) * 0.3  # was 0.5, too wide
    if unc < 0.001:
        return 1.0 if dist > 0 else 0.0
    z = dist / unc
    steep = 2.0 + conf * 2.0  # was 1.5+1.5, steeper = more decisive
    return max(0.01, min(0.99, 1.0 / (1.0 + math.exp(-steep * z))))


class Sandbox:
    def __init__(self, interval):
        self.interval = interval
        self.balance = STARTING_BALANCE
        self.positions = {}  # market_id -> {side, entry_price, bet, slug, strike, ticker, expiry_ts, ...}
        self.closed = []
        self.session = None
        self.binance_prices = {}
        self._scan_count = 0
        self._load()

    def _load(self):
        if POSITIONS_FILE.exists():
            try:
                d = json.loads(POSITIONS_FILE.read_text())
                self.positions = d.get("positions", {})
                self.closed = d.get("closed", [])
                self.balance = d.get("balance", STARTING_BALANCE)
            except Exception:
                pass

    def _save(self):
        with open(POSITIONS_FILE, "w") as f:
            json.dump({"balance": round(self.balance, 4), "positions": self.positions, "closed": self.closed}, f, indent=2)

    def exposure(self):
        return sum(p.get("bet", 0) for p in self.positions.values())

    async def _fetch_prices(self):
        for ticker, bsym in TICKER_TO_BINANCE.items():
            try:
                async with self.session.get(f"{BINANCE_API}/api/v3/ticker/price", params={"symbol": bsym}, timeout=aiohttp.ClientTimeout(total=3)) as r:
                    if r.status == 200:
                        d = await r.json()
                        self.binance_prices[ticker] = float(d.get("price", 0))
            except Exception:
                pass

    async def _fetch_markets(self):
        all_m = []
        for page in range(1, 20):
            try:
                async with self.session.get(f"{LIMITLESS_API}/markets/active", params={"limit": 25, "page": page}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    resp = await r.json(content_type=None)
                batch = resp.get("data", []) if isinstance(resp, dict) else resp
                if not batch:
                    break
                all_m.extend(batch)
                if len(batch) < 25:
                    break
            except Exception:
                break
        return all_m

    async def _get_book(self, slug):
        try:
            async with self.session.get(f"{LIMITLESS_API}/markets/{slug}/orderbook", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
        except Exception:
            pass
        return {}

    async def _scan(self):
        self._scan_count += 1
        await self._fetch_prices()
        markets = await self._fetch_markets()
        now = now_utc()
        opportunities = []
        for m in markets:
            title = m.get("title", "")
            ticker = extract_ticker(title)
            if not ticker or ticker not in self.binance_prices:
                continue
            strike = extract_strike(title)
            if not strike:
                continue
            ts = m.get("expirationTimestamp", 0)
            if not ts:
                continue
            exp_ts = int(ts) // 1000 if int(ts) > 1e12 else int(ts)
            exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
            hours_left = (exp_dt - now).total_seconds() / 3600
            if hours_left < 0 or hours_left > MAX_HOURS:
                continue
            spot = self.binance_prices[ticker]
            sym = TICKER_TO_SYM.get(ticker, "")
            prob = get_prob(sym, strike, hours_left * 60, spot)
            prices = m.get("prices", [0, 0])
            mkt_yes = float(prices[0]) if prices else 0
            if mkt_yes < 0.01 or mkt_yes > 0.99:
                continue
            edge_yes = (prob - mkt_yes) * 100
            edge_no = ((1 - prob) - (1 - mkt_yes)) * 100
            best_side = "YES" if edge_yes > edge_no else "NO"
            best_edge = max(edge_yes, edge_no)
            if best_edge < 8:
                continue
            dist = abs(spot - strike) / strike * 100
            if dist < 0.50:  # need 0.5%+ from strike with <3min left = near certain
                continue
            opportunities.append({"slug": m.get("slug", ""), "market_id": str(m.get("id", "")), "title": title[:80], "ticker": ticker, "strike": strike, "spot": spot, "hours_left": hours_left, "prob": round(prob, 4), "mkt_yes": mkt_yes, "side": best_side, "edge": round(best_edge, 2), "expiry_ts": exp_ts, "tokens": m.get("tokens", {})})
        opportunities.sort(key=lambda o: -o["edge"])
        # LATENCY ARB: compare Binance price movement speed vs orderbook staleness
        # If Binance moved >0.5% in last 30s but orderbook hasn't updated, that's the edge
        for opp in opportunities:
            # Check: how stale is the orderbook vs Binance?
            sym_ind = load_indicators().get(TICKER_TO_SYM.get(opp["ticker"], ""), {})
            if sym_ind:
                spot = opp["spot"]
                prev = sym_ind.get("prev_price", spot)
                if prev and prev > 0:
                    move_pct = abs(spot - prev) / prev * 100
                    if move_pct > 0.3:
                        logger.info(f"  FAST-MOVE {opp['ticker']} {move_pct:.2f}% | {opp['side']} | spot={spot:.4f} prev={prev:.4f}")
        # Try to enter positions
        for opp in opportunities:
            if self.exposure() >= MAX_EXPOSURE:
                break
            key = opp["market_id"]
            if key in self.positions:
                continue
            # GET REAL ORDERBOOK PRICE
            book = await self._get_book(opp["slug"])
            if not book:
                continue
            asks = sorted([(float(a["price"]), float(a.get("size", 0))) for a in book.get("asks", []) if float(a.get("size", 0)) > 0])
            bids = sorted([(float(b["price"]), float(b.get("size", 0))) for b in book.get("bids", []) if float(b.get("size", 0)) > 0], reverse=True)
            if opp["side"] == "YES":
                if not asks:
                    continue
                entry = asks[0][0]  # ACTUAL ask price we'd pay
                size_available = asks[0][1] / 1e6  # USDC available at this price
                ev_edge = (opp["prob"] - entry) * 100
            else:
                if not bids:
                    continue
                entry = 1.0 - bids[0][0]  # NO cost = 1 - YES bid
                size_available = bids[0][1] / 1e6
                ev_edge = ((1 - opp["prob"]) - entry) * 100
            # EV CHECK: prob must exceed entry by threshold
            if ev_edge < EV_THRESHOLD:
                continue
            # TREND FILTER: require higher EV when betting against the trend
            sym_ind = load_indicators().get(TICKER_TO_SYM.get(opp["ticker"], ""), {})
            if sym_ind:
                ha_1h = sym_ind.get("ha_1h", "")
                ha_4h = sym_ind.get("ha_4h", "")
                against_trend = False
                if opp["side"] == "NO" and ha_1h == "green" and ha_4h == "green":
                    against_trend = True
                elif opp["side"] == "YES" and ha_1h == "red" and ha_4h == "red":
                    against_trend = True
                if against_trend and ev_edge < 15.0:
                    continue  # need 15%+ EV to go against trend (vs 5% with trend)
            if size_available < BET_SIZE:
                continue
            if self.balance < BET_SIZE:
                continue
            # ENTER — deduct from balance immediately (like real trading)
            self.balance -= BET_SIZE
            payout_if_win = BET_SIZE / entry  # shares * $1 at resolution
            real_profit_if_win = payout_if_win - BET_SIZE
            self.positions[key] = {"side": opp["side"], "ticker": opp["ticker"], "entry": round(entry, 4), "bet": BET_SIZE, "payout_if_win": round(payout_if_win, 4), "profit_if_win": round(real_profit_if_win, 4), "strike": opp["strike"], "spot": opp["spot"], "prob": opp["prob"], "ev_edge": round(ev_edge, 2), "slug": opp["slug"], "expiry_ts": opp["expiry_ts"], "entered_at": now.isoformat()}
            self._save()
            logger.info(f"  ENTER {opp['side']} {opp['ticker']} | entry={entry:.3f} ev={ev_edge:.1f}% | win=+${real_profit_if_win:.2f} lose=-${BET_SIZE:.2f} | bal=${self.balance:.2f} | {opp['title'][:40]}")
            with open(TRADES_LOG, "a") as f:
                f.write(json.dumps({"action": "ENTER", **self.positions[key]}) + "\n")
        # CHECK RESOLUTIONS via Limitless API
        for key in list(self.positions.keys()):
            pos = self.positions[key]
            expiry_ts = pos.get("expiry_ts", 0)
            exp_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
            if now < exp_dt + timedelta(minutes=3):
                continue
            slug = pos.get("slug", "")
            resolved = None
            try:
                async with self.session.get(f"{LIMITLESS_API}/markets/{slug}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        mdata = await r.json(content_type=None)
                        winner = mdata.get("winningOutcomeIndex")
                        if winner is not None:
                            resolved = (winner == 0)  # 0=YES wins
            except Exception:
                pass
            if resolved is None:
                # Only use Binance fallback after 30 min AND price is far from strike
                age_min = (now - exp_dt).total_seconds() / 60
                if age_min < 30:
                    continue
                ticker = pos.get("ticker", "")
                spot = self.binance_prices.get(ticker, 0)
                strike = pos.get("strike", 0)
                if spot <= 0:
                    continue
                dist = abs(spot - strike) / strike * 100
                if dist < 3.0:
                    continue  # too close, wait for API
                resolved = spot > strike
            side = pos.get("side", "YES")
            won = (side == "YES" and resolved) or (side == "NO" and not resolved)
            if won:
                pnl = pos.get("profit_if_win", 0)
                self.balance += pos.get("payout_if_win", BET_SIZE)
            else:
                pnl = -BET_SIZE
            pos.update({"pnl": round(pnl, 4), "won": won, "resolved_yes": resolved, "closed_at": now.isoformat()})
            self.closed.append(pos)
            del self.positions[key]
            self._save()
            tag = "WIN" if won else "LOSS"
            logger.info(f"  {tag} {side} {pos.get('ticker','')} | pnl=${pnl:+.2f} | entry={pos.get('entry',0):.3f} | bal=${self.balance:.2f} | {pos.get('title', pos.get('slug',''))[:40]}")
            with open(TRADES_LOG, "a") as f:
                f.write(json.dumps({"action": "CLOSE", **pos}) + "\n")
        # Stats
        wins = [c for c in self.closed if c.get("won")]
        losses = [c for c in self.closed if not c.get("won")]
        total_pnl = sum(c.get("pnl", 0) for c in self.closed)
        total_bet = len(self.closed) * BET_SIZE
        s = {"balance": round(self.balance, 2), "starting": STARTING_BALANCE, "real_pnl": round(self.balance - STARTING_BALANCE + self.exposure(), 2), "wins": len(wins), "losses": len(losses), "total": len(self.closed), "wr": round(len(wins) / len(self.closed) * 100, 1) if self.closed else 0, "roi": round(total_pnl / total_bet * 100, 1) if total_bet else 0, "open": len(self.positions), "exposure": round(self.exposure(), 2), "scan": self._scan_count}
        with open(STATS_FILE, "w") as f:
            json.dump(s, f, indent=2)
        logger.info(f"Scan #{self._scan_count}: bal=${self.balance:.2f} | {s['wins']}W/{s['losses']}L pnl=${total_pnl:+.2f} roi={s['roi']:+.1f}% | open={s['open']} exp=${s['exposure']:.0f}")

    async def run(self):
        logger.info(f"SANDBOX | bal=${self.balance:.2f} | bet=${BET_SIZE} | max_exp=${MAX_EXPOSURE} | ev_thresh={EV_THRESHOLD}% | interval={self.interval}s")
        connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            while True:
                try:
                    await self._scan()
                except Exception as e:
                    logger.error(f"Cycle error: {e}", exc_info=True)
                await asyncio.sleep(self.interval)


def main():
    parser = argparse.ArgumentParser(description="Limitless sandbox — paper trading with REAL numbers")
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()
    sandbox = Sandbox(interval=args.interval)
    try:
        asyncio.run(sandbox.run())
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
