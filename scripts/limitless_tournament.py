#!/usr/bin/env python3
"""limitless_tournament.py v2 — ALL markets, ALL strategies, REAL orderbook prices, binary only.
Covers: crypto (Binance oracle), sports, politics, entertainment, pre-TGE — everything on Limitless.
12 strategies x every market. No fake numbers. No hedge exits."""
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
from typing import Optional, Dict, List, Tuple
import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg
load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("tournament")

LIMITLESS_API = "https://api.limitless.exchange"
BINANCE_API = "https://api.binance.com"
DATA_DIR = Path("./data/poly/limitless_tournament")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TICKER_TO_BINANCE = {"BTC": "BTCUSDC", "ETH": "ETHUSDC", "SOL": "SOLUSDC", "DOGE": "DOGEUSDT", "XRP": "XRPUSDC", "BNB": "BNBUSDC", "ADA": "ADAUSDC", "AVAX": "AVAXUSDC", "SUI": "SUIUSDT", "LINK": "LINKUSDC", "PEPE": "PEPEUSDT", "WIF": "WIFUSDT", "LTC": "LTCUSDC", "DOT": "DOTUSDT", "NEAR": "NEARUSDT", "UNI": "UNIUSDC"}

INDICATOR_FILE = Path("./data/latest_market_data.json")
_ind_cache: dict = {}
_ind_ts: float = 0

def load_indicators() -> dict:
    global _ind_cache, _ind_ts
    now = time.time()
    if now - _ind_ts < 10 and _ind_cache:
        return _ind_cache
    try:
        if INDICATOR_FILE.exists():
            with open(INDICATOR_FILE) as f:
                _ind_cache = json.load(f)
            _ind_ts = now
    except Exception:
        pass
    try:
        import redis as r
        rc = r.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        for sym in list(_ind_cache.keys()):
            raw = rc.get(f"hot_metrics:{sym}")
            if raw:
                hot = json.loads(raw)
                if hot.get("_tick_ts", 0) > _ind_cache[sym].get("ts", 0):
                    for k, dst in [("k_1m", "stoch_k_1m"), ("d_1m", "stoch_d_1m"), ("k_3m", "stoch_k_3m"), ("d_3m", "stoch_d_3m"), ("price", "current_price")]:
                        if k in hot:
                            _ind_cache[sym][dst] = hot[k]
    except Exception:
        pass
    return _ind_cache

def now_utc():
    return datetime.now(timezone.utc)

# ============================================================
# MARKET CLASSIFICATION
# ============================================================

def classify_market(m: dict) -> dict:
    """Classify any Limitless market into a tradeable opportunity."""
    title = m.get("title", "")
    slug = m.get("slug", "")
    exp_ts = m.get("expirationTimestamp", 0)
    if exp_ts and int(exp_ts) > 1e12:
        exp_ts = int(exp_ts) // 1000
    prices = m.get("prices", [0, 0])
    yes_display = float(prices[0]) if len(prices) > 0 and prices[0] else 0
    no_display = 1.0 - yes_display if yes_display > 0 else 0
    # Determine market type
    crypto_match = re.search(r'\$([A-Z]{2,6}).*above \$([0-9,]+\.?\d*)', title)
    is_crypto = bool(crypto_match)
    is_sports = bool(re.search(r'⚽|🏀|🏈|NFL|NBA|NHL|UEFA|Premier League|La Liga|Serie A|Bundesliga|Champions|EFL|goal|offside|card|corner|foul|win.*vs|defeat|match', title, re.IGNORECASE))
    is_politics = bool(re.search(r'election|vote|turnout|president|prime minister|parliament|congress|senate|poll|party|democrat|republican', title, re.IGNORECASE))
    mtype = "crypto" if is_crypto else "sports" if is_sports else "politics" if is_politics else "other"
    ticker = crypto_match.group(1) if crypto_match else None
    strike = float(crypto_match.group(2).replace(",", "")) if crypto_match else None
    return {"id": str(m.get("id", "")), "slug": slug, "title": title[:100], "type": mtype, "ticker": ticker, "strike": strike, "expiry_ts": exp_ts, "yes_display": yes_display, "no_display": no_display}

# ============================================================
# PROBABILITY MODELS
# ============================================================

def crypto_probability(spot: float, strike: float, hours_left: float) -> float:
    """Probability of 'price above strike' using Binance oracle."""
    if hours_left <= 0:
        return 1.0 if spot > strike else 0.0
    if strike <= 0 or spot <= 0:
        return 0.5
    d = (spot - strike) / strike * 100
    v = 0.5 * math.sqrt(max(0.01, hours_left))
    if v < 0.01:
        return 1.0 if d > 0 else 0.0
    z = d / v
    return max(0.01, min(0.99, 1.0 / (1.0 + math.exp(-1.7 * z))))

def display_price_probability(yes_display: float) -> float:
    """For non-oracle markets, the display price IS our best probability estimate."""
    if yes_display <= 0 or yes_display >= 1:
        return 0.5
    return yes_display

def get_lr_direction(ticker, indicators):
    sym = TICKER_TO_BINANCE.get(ticker, "").replace("USDT", "USDC")
    ind = indicators.get(sym, {})
    slope = 0
    for tf, cm, w in [("3m", 3, 0.5), ("15m", 15, 0.3), ("1h", 60, 0.2)]:
        s = ind.get(f"lr_trend_{tf}", 0)
        if s:
            slope += (s / cm) * w
    return slope

def get_stoch_alignment(ticker, indicators):
    sym = TICKER_TO_BINANCE.get(ticker, "").replace("USDT", "USDC")
    ind = indicators.get(sym, {})
    bullish = 0
    for tf in ["1m", "3m", "15m"]:
        k = ind.get(f"stoch_k_{tf}", 50)
        k_prev = ind.get(f"k_{tf}_prev", k)
        if k > k_prev:
            bullish += 1
        elif k < k_prev:
            bullish -= 1
    return bullish

# ============================================================
# 12 STRATEGIES — every market type
# ============================================================

STRATEGIES = {
    # CRYPTO-SPECIFIC (use Binance oracle)
    "S01_crypto_value":     {"max_entry": 0.55, "min_ev": 12, "max_hours": 1.0, "min_dist": 0.30, "types": {"crypto"}, "desc": "Crypto value: entry<0.55, dist>0.3%, ev>12%"},
    "S02_crypto_sniper":    {"max_entry": 0.40, "min_ev": 20, "max_hours": 1.0, "min_dist": 0.50, "types": {"crypto"}, "desc": "Crypto sniper: entry<0.40, ev>20%"},
    "S03_crypto_momentum":  {"max_entry": 0.60, "min_ev": 10, "max_hours": 0.75,"min_dist": 0.25, "types": {"crypto"}, "require_momentum": True, "desc": "Crypto momentum-aligned, entry<0.60"},
    # SPORTS-SPECIFIC
    "S04_sports_value":     {"max_entry": 0.50, "min_ev": 10, "max_hours": 48.0, "types": {"sports"}, "desc": "Sports value: entry<0.50, ev>10%"},
    "S05_sports_cheap":     {"max_entry": 0.25, "min_ev": 5,  "max_hours": 48.0, "types": {"sports"}, "desc": "Sports cheap tickets <0.25"},
    "S06_sports_favorite":  {"max_entry": 0.65, "min_ev": 8,  "max_hours": 48.0, "types": {"sports"}, "min_yes_display": 0.70, "desc": "Sports: back favorites at value price"},
    # UNIVERSAL (any market type)
    "S07_arb_hunter":       {"max_entry": 0.70, "min_ev": 0,  "max_hours": 999,  "types": None, "require_arb": True, "desc": "Arb: YES+NO < 0.97 on orderbook"},
    "S08_stale_hunter":     {"max_entry": 0.60, "min_ev": 15, "max_hours": 999,  "types": None, "desc": "Stale markets: entry<0.60, ev>15% (any type)"},
    "S09_cheap_universal":  {"max_entry": 0.30, "min_ev": 5,  "max_hours": 999,  "types": None, "desc": "Cheap tickets <0.30 any market"},
    "S10_wide_spread":      {"max_entry": 0.65, "min_ev": 8,  "max_hours": 999,  "types": None, "min_spread": 0.10, "desc": "Wide spread >10% = stale pricing"},
    # POLITICS + OTHER
    "S11_politics_value":   {"max_entry": 0.50, "min_ev": 10, "max_hours": 999,  "types": {"politics", "other"}, "desc": "Politics/other: entry<0.50, ev>10%"},
    # COMBINED BEST (adapts per market)
    "S12_best_combined":    {"max_entry": 0.55, "min_ev": 10, "max_hours": 999,  "types": None, "desc": "Combined best: entry<0.55, ev>10% any market"},
    # V2 REFINED — derived from win/loss pattern analysis
    "S13_spread_edge":      {"max_entry": 0.65, "min_ev": 18, "max_hours": 0.25, "types": {"crypto"}, "min_spread": 0.08, "desc": "EDGE: spread>8%, ev>18%, <15min crypto"},
    "S14_high_conv":        {"max_entry": 0.55, "min_ev": 25, "max_hours": 0.25, "types": {"crypto"}, "min_dist": 0.10, "desc": "High conviction: ev>25%, dist>0.1%, <15min"},
    "S15_quick_scalp":      {"max_entry": 0.50, "min_ev": 20, "max_hours": 0.12, "types": {"crypto"}, "desc": "Quick scalp: entry<0.50, ev>20%, <7min"},
    "S16_spread_plus_dist": {"max_entry": 0.60, "min_ev": 15, "max_hours": 0.30, "types": {"crypto"}, "min_spread": 0.05, "min_dist": 0.10, "desc": "Spread>5% + dist>0.1%, ev>15%"},
    # V3 — DATA-DRIVEN from 51 trade analysis
    "S17_no_only_high_ev":  {"max_entry": 0.65, "min_ev": 25, "max_hours": 0.25, "types": {"crypto"}, "force_side": "NO", "desc": "NO-only, ev>25%, <15min (YES=22%WR death trap)"},
    "S18_no_spread_combo":  {"max_entry": 0.65, "min_ev": 18, "max_hours": 0.25, "types": {"crypto"}, "force_side": "NO", "min_spread": 0.05, "desc": "NO + spread>5% + ev>18%"},
    "S19_no_sol_xrp":       {"max_entry": 0.65, "min_ev": 15, "max_hours": 0.50, "types": {"crypto"}, "force_side": "NO", "tickers": {"SOL", "XRP"}, "desc": "NO SOL/XRP only (best WR tickers)"},
    "S20_entry_above_40":   {"max_entry": 0.65, "min_ev": 15, "max_hours": 0.25, "types": {"crypto"}, "min_entry": 0.40, "desc": "Entry 0.40-0.65 only (sweet spot from data)"},
}

class StrategyTracker:
    def __init__(self, name, budget=100.0):
        self.name = name
        self.budget = budget
        self.positions: Dict[str, dict] = {}
        self.closed: List[dict] = []
        self.bet_size = 5.0
    def enter(self, key, data):
        if key in self.positions:
            return False
        if self.exposure() + self.bet_size > self.budget:
            return False
        self.positions[key] = data
        return True
    def close(self, key, exit_price, reason):
        pos = self.positions.pop(key, None)
        if not pos:
            return None
        entry = pos.get("entry_price", 0)
        pnl = (exit_price - entry) * self.bet_size / entry if entry > 0 else 0
        result = {**pos, "exit_price": exit_price, "pnl_usdc": round(pnl, 4), "reason": reason, "closed_at": now_utc().isoformat()}
        self.closed.append(result)
        return result
    def exposure(self):
        return len(self.positions) * self.bet_size
    def stats(self):
        wins = sum(1 for c in self.closed if c["pnl_usdc"] > 0)
        losses = sum(1 for c in self.closed if c["pnl_usdc"] <= 0)
        total = len(self.closed)
        pnl = sum(c["pnl_usdc"] for c in self.closed)
        deployed = total * self.bet_size if total > 0 else 1
        return {"name": self.name, "total": total, "wins": wins, "losses": losses, "win_rate": round(wins / total * 100, 1) if total > 0 else 0, "pnl": round(pnl, 2), "roi_pct": round(pnl / deployed * 100, 1) if deployed > 0 else 0, "open": len(self.positions), "exposure": self.exposure()}

class Tournament:
    def __init__(self, interval=15.0):
        self.interval = interval
        self.trackers: Dict[str, StrategyTracker] = {name: StrategyTracker(name) for name in STRATEGIES}
        self.binance_prices: Dict[str, float] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self._scan_count = 0
        self._start_time = time.time()
        self._book_cache: Dict[str, Tuple[float, dict]] = {}
        self._markets_seen = 0
        self._opps_found = 0
    async def _fetch_binance_prices(self):
        try:
            async with self.session.get(f"{BINANCE_API}/api/v3/ticker/price", timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    for t in await r.json():
                        sym = t["symbol"]
                        for ticker, bsym in TICKER_TO_BINANCE.items():
                            if sym == bsym:
                                self.binance_prices[ticker] = float(t["price"])
        except Exception as e:
            logger.warning(f"Binance fetch: {e}")
    async def _fetch_all_markets(self) -> List[dict]:
        """Fetch ALL active markets from Limitless — every page, every type."""
        all_markets = []
        try:
            for page in range(1, 20):
                async with self.session.get(f"{LIMITLESS_API}/markets/active?limit=25&page={page}", timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        break
                    data = await r.json(content_type=None)
                    markets = data if isinstance(data, list) else data.get("data", [])
                    if not markets:
                        break
                    all_markets.extend(markets)
        except Exception as e:
            logger.warning(f"Limitless fetch: {e}")
        return all_markets
    async def _get_book(self, slug: str) -> dict:
        now = time.time()
        cached = self._book_cache.get(slug)
        if cached and now - cached[0] < 10:
            return cached[1]
        try:
            async with self.session.get(f"{LIMITLESS_API}/markets/{slug}/orderbook", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    book = await r.json(content_type=None)
                    self._book_cache[slug] = (now, book)
                    return book
        except Exception:
            pass
        return {}
    def _get_orderbook_prices(self, book: dict) -> dict:
        """Extract real tradeable prices from orderbook."""
        asks = book.get("asks", [])
        bids = book.get("bids", [])
        valid_asks = [float(a["price"]) for a in asks if float(a.get("size", 0)) > 0]
        valid_bids = [float(b["price"]) for b in bids if float(b.get("size", 0)) > 0]
        best_ask = min(valid_asks) if valid_asks else 0
        best_bid = max(valid_bids) if valid_bids else 0
        yes_cost = best_ask
        no_cost = round(1.0 - best_bid, 4) if best_bid > 0 else 0
        spread = round(best_ask - best_bid, 4) if best_ask > 0 and best_bid > 0 else 0
        combined_cost = round(yes_cost + no_cost, 4) if yes_cost > 0 and no_cost > 0 else 0
        return {"yes_cost": yes_cost, "no_cost": no_cost, "best_ask": best_ask, "best_bid": best_bid, "spread": spread, "combined_cost": combined_cost, "has_asks": len(valid_asks) > 0, "has_bids": len(valid_bids) > 0, "n_asks": len(valid_asks), "n_bids": len(valid_bids)}
    async def _evaluate_market(self, m: dict) -> Optional[dict]:
        """Evaluate ANY market for trading opportunity."""
        info = classify_market(m)
        now = now_utc()
        hours_left = 999
        if info["expiry_ts"]:
            expiry_dt = datetime.fromtimestamp(info["expiry_ts"], tz=timezone.utc)
            hours_left = (expiry_dt - now).total_seconds() / 3600
            if hours_left < -0.5:
                return None
        book = await self._get_book(info["slug"])
        if not book:
            return None
        ob = self._get_orderbook_prices(book)
        if not ob["has_asks"] and not ob["has_bids"]:
            return None
        # Determine true probability
        if info["type"] == "crypto" and info["ticker"] and info["strike"]:
            spot = self.binance_prices.get(info["ticker"], 0)
            if spot <= 0:
                return None
            true_prob_yes = crypto_probability(spot, info["strike"], max(0, hours_left))
            dist_pct = abs(spot - info["strike"]) / info["strike"] * 100
        else:
            spot = 0
            true_prob_yes = display_price_probability(info["yes_display"])
            dist_pct = 0
        # Calculate edge for YES and NO sides from REAL orderbook
        ev_yes = (true_prob_yes - ob["yes_cost"]) * 100 if ob["yes_cost"] > 0 else -999
        ev_no = ((1 - true_prob_yes) - ob["no_cost"]) * 100 if ob["no_cost"] > 0 else -999
        best_side = "YES" if ev_yes > ev_no else "NO"
        best_ev = max(ev_yes, ev_no)
        entry_price = ob["yes_cost"] if best_side == "YES" else ob["no_cost"]
        # Arb check: can we buy BOTH sides for < $1?
        is_arb = ob["combined_cost"] > 0 and ob["combined_cost"] < 0.97
        # Indicator data for crypto
        indicators = load_indicators()
        lr_dir = get_lr_direction(info["ticker"], indicators) if info["ticker"] else 0
        stoch_align = get_stoch_alignment(info["ticker"], indicators) if info["ticker"] else 0
        return {"id": info["id"], "slug": info["slug"], "title": info["title"], "type": info["type"], "ticker": info["ticker"], "strike": info["strike"], "spot": spot, "hours_left": hours_left, "dist_pct": dist_pct, "true_prob_yes": true_prob_yes, "side": best_side, "entry_price": entry_price, "ev": best_ev, "ev_yes": ev_yes, "ev_no": ev_no, "ob": ob, "is_arb": is_arb, "lr_dir": lr_dir, "stoch_align": stoch_align, "expiry_ts": info["expiry_ts"], "yes_display": info["yes_display"]}
    def _strategy_accepts(self, strat_name: str, opp: dict) -> bool:
        cfg = STRATEGIES[strat_name]
        if cfg.get("types") and opp["type"] not in cfg["types"]:
            return False
        if opp["entry_price"] <= 0 or opp["entry_price"] > cfg["max_entry"]:
            return False
        if opp["hours_left"] > cfg["max_hours"]:
            return False
        if cfg.get("require_arb"):
            return opp["is_arb"]
        if opp["ev"] < cfg["min_ev"]:
            return False
        if cfg.get("min_dist") and opp["type"] == "crypto" and opp["dist_pct"] < cfg["min_dist"]:
            return False
        if cfg.get("require_momentum") and opp["type"] == "crypto":
            if opp["side"] == "YES" and opp["lr_dir"] <= 0:
                return False
            if opp["side"] == "NO" and opp["lr_dir"] >= 0:
                return False
        if cfg.get("min_spread") and opp["ob"]["spread"] < cfg["min_spread"]:
            return False
        if cfg.get("min_yes_display") and opp["yes_display"] < cfg["min_yes_display"]:
            return False
        if cfg.get("force_side") and opp["side"] != cfg["force_side"]:
            return False
        if cfg.get("min_entry") and opp["entry_price"] < cfg["min_entry"]:
            return False
        if cfg.get("tickers") and opp.get("ticker") and opp["ticker"] not in cfg["tickers"]:
            return False
        return True
    async def _check_resolutions(self):
        now = now_utc()
        for strat_name, tracker in self.trackers.items():
            for key in list(tracker.positions.keys()):
                pos = tracker.positions.get(key)
                if not pos:
                    continue
                expiry_ts = pos.get("expiry_ts", 0)
                if not expiry_ts:
                    continue
                expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
                if now < expiry_dt + timedelta(minutes=2):
                    continue
                slug = pos.get("slug", "")
                side = pos.get("side", "YES")
                resolved_yes = None
                try:
                    async with self.session.get(f"{LIMITLESS_API}/markets/{slug}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            mdata = await r.json(content_type=None)
                            winner = mdata.get("winningOutcomeIndex")
                            if winner is not None:
                                resolved_yes = (winner == 0)
                except Exception:
                    pass
                # Crypto fallback: use Binance
                if resolved_yes is None and pos.get("type") == "crypto":
                    ticker = pos.get("ticker", "")
                    strike = pos.get("strike", 0)
                    spot = self.binance_prices.get(ticker, 0)
                    if spot > 0 and strike > 0:
                        age_min = (now - expiry_dt).total_seconds() / 60
                        dist = abs(spot - strike) / strike * 100
                        if dist >= 2.0 or age_min >= 30:
                            resolved_yes = spot > strike
                if resolved_yes is None:
                    age_min = (now - expiry_dt).total_seconds() / 60
                    if age_min > 120:
                        tracker.positions.pop(key, None)
                        logger.warning(f"  EXPIRED [{strat_name}] {pos.get('title','')[:40]} — no resolution after 2h, dropped")
                    continue
                won = (side == "YES" and resolved_yes) or (side == "NO" and not resolved_yes)
                exit_price = 1.0 if won else 0.0
                result = tracker.close(key, exit_price, "RESOLVED")
                if result:
                    tag = "WIN" if result["pnl_usdc"] > 0 else "LOSS"
                    logger.info(f"  {tag} [{strat_name}] {side} {pos.get('type','')} pnl=${result['pnl_usdc']:+.2f} entry={pos.get('entry_price',0):.3f} | {result.get('title','')[:50]}")
    async def _scan_cycle(self):
        self._scan_count += 1
        await self._fetch_binance_prices()
        markets = await self._fetch_all_markets()
        self._markets_seen = len(markets)
        opps = []
        book_fetches = 0
        for m in markets:
            opp = await self._evaluate_market(m)
            if opp and (opp["ev"] > 0 or opp["is_arb"]):
                opps.append(opp)
                book_fetches += 1
            if book_fetches >= 50:
                break
        self._opps_found = len(opps)
        entries_this_cycle = 0
        for opp in sorted(opps, key=lambda o: -o["ev"]):
            for strat_name, tracker in self.trackers.items():
                if not self._strategy_accepts(strat_name, opp):
                    continue
                key = f"{strat_name}:{opp['id']}"
                pos_data = {"title": opp["title"], "type": opp["type"], "ticker": opp.get("ticker"), "side": opp["side"], "strike": opp.get("strike"), "spot": opp.get("spot"), "entry_price": opp["entry_price"], "ev": opp["ev"], "dist_pct": opp.get("dist_pct", 0), "hours_left": opp["hours_left"], "slug": opp["slug"], "expiry_ts": opp["expiry_ts"], "ob_spread": opp["ob"]["spread"], "ob_combined": opp["ob"]["combined_cost"], "entered_at": now_utc().isoformat()}
                if tracker.enter(key, pos_data):
                    entries_this_cycle += 1
                    logger.info(f"  ENTER [{strat_name}] {opp['side']} {opp['type']} entry={opp['entry_price']:.3f} ev={opp['ev']:.1f}% | {opp['title'][:55]}")
        await self._check_resolutions()
        if self._scan_count % 20 == 0 or entries_this_cycle > 0:
            self._print_leaderboard()
        self._save_state()
    def _print_leaderboard(self):
        elapsed_h = (time.time() - self._start_time) / 3600
        print(f"\n{'='*100}")
        print(f"TOURNAMENT v2 — Scan #{self._scan_count} — {elapsed_h:.1f}h — {self._markets_seen} markets scanned, {self._opps_found} opps")
        print(f"{'='*100}")
        print(f"{'Strategy':<22s} {'Type':<8s} {'Trades':>6s} {'W':>3s} {'L':>3s} {'WR%':>5s} {'P&L':>8s} {'ROI%':>7s} {'Open':>4s} {'Description'}")
        print(f"{'-'*100}")
        rows = []
        for name in sorted(STRATEGIES.keys()):
            s = self.trackers[name].stats()
            types = STRATEGIES[name].get("types")
            tstr = ",".join(sorted(types)) if types else "all"
            rows.append((s["pnl"], s["total"], name, s, tstr))
        rows.sort(key=lambda x: (-x[0], -x[1]))
        for _, _, name, s, tstr in rows:
            desc = STRATEGIES[name]["desc"][:35]
            pnl_str = f"${s['pnl']:+.2f}"
            print(f"{name:<22s} {tstr:<8s} {s['total']:>6d} {s['wins']:>3d} {s['losses']:>3d} {s['win_rate']:>5.1f} {pnl_str:>8s} {s['roi_pct']:>6.1f}% {s['open']:>4d} {desc}")
        print(f"{'='*100}")
        total_trades = sum(self.trackers[n].stats()["total"] for n in STRATEGIES)
        total_open = sum(self.trackers[n].stats()["open"] for n in STRATEGIES)
        total_pnl = sum(self.trackers[n].stats()["pnl"] for n in STRATEGIES)
        print(f"TOTAL: {total_trades} closed, {total_open} open, ${total_pnl:+.2f} P&L")
        # Type breakdown
        type_pnl: Dict[str, float] = {}
        type_count: Dict[str, int] = {}
        for tracker in self.trackers.values():
            for c in tracker.closed:
                t = c.get("type", "?")
                type_pnl[t] = type_pnl.get(t, 0) + c["pnl_usdc"]
                type_count[t] = type_count.get(t, 0) + 1
        if type_pnl:
            print(f"By type: {', '.join(f'{t}={ct}T ${type_pnl[t]:+.2f}' for t, ct in sorted(type_count.items()))}")
        print()
    def _save_state(self):
        state = {}
        for name, tracker in self.trackers.items():
            state[name] = {"stats": tracker.stats(), "positions": {k: v for k, v in list(tracker.positions.items())[:20]}, "closed": tracker.closed[-50:]}
        with open(DATA_DIR / "tournament_state.json", "w") as f:
            json.dump(state, f, indent=2)
        lb_data = []
        for name in sorted(STRATEGIES.keys()):
            s = self.trackers[name].stats()
            s["types"] = list(STRATEGIES[name].get("types") or ["all"])
            s["desc"] = STRATEGIES[name]["desc"]
            lb_data.append(s)
        lb_data.sort(key=lambda x: -x["pnl"])
        with open(DATA_DIR / "leaderboard.json", "w") as f:
            json.dump({"updated": now_utc().isoformat(), "scan_count": self._scan_count, "elapsed_hours": round((time.time() - self._start_time) / 3600, 2), "markets_seen": self._markets_seen, "leaderboard": lb_data}, f, indent=2)
        # Also write trades log
        with open(DATA_DIR / "all_trades.jsonl", "a") as f:
            pass  # file exists
    async def run(self):
        logger.info(f"TOURNAMENT v2 START — 12 strategies x ALL market types, REAL orderbook, binary only")
        for name, cfg in STRATEGIES.items():
            logger.info(f"  {name}: {cfg['desc']}")
        connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            while True:
                try:
                    await self._scan_cycle()
                except Exception as e:
                    logger.error(f"Cycle error: {e}", exc_info=True)
                await asyncio.sleep(self.interval)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()
    t = Tournament(interval=args.interval)
    try:
        asyncio.run(t.run())
    except KeyboardInterrupt:
        t._print_leaderboard()
        t._save_state()
        logger.info("Tournament stopped.")

if __name__ == "__main__":
    main()
