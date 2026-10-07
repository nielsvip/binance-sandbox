#!/usr/bin/env python3
"""limitless_trader.py — Limitless Exchange crypto scalper.

STRATEGY: Trade hourly crypto price prediction markets on Limitless.
Markets like "$BTC above $84,250 on Mar 16, 21:00 UTC?" resolve via Pyth oracle.
We use Binance real-time prices to know the answer BEFORE the market prices it in.

EDGE: When BTC is at $85,000 and the market asks "$BTC above $84,250?",
YES should be ~0.98+. If the market lags at 0.92, we buy YES.
Conversely, if BTC drops to $83,000 and YES is still at 0.80, we buy NO.

KEY INSIGHT: Limitless hourly markets have 1-5% spreads vs Polymarket's 0.1%.
This wider spread = more pricing inefficiency = more opportunities.

RISK MANAGEMENT:
  - Only trade markets closing within MAX_HOURS (fast capital turnover)
  - Use Binance spot price as oracle to judge true probability
  - Min edge required before entering (MIN_EDGE_PCT)
  - Flat bet sizing ($5 default)
  - Stop if total loss exceeds MAX_LOSS

Usage:
  python limitless_trader.py                         # paper mode
  python limitless_trader.py --live                   # real trades
  python limitless_trader.py --interval 30            # scan every 30s
  python limitless_trader.py --min-edge 3.0           # require 3%+ edge
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
logger = logging.getLogger("lim_trader")

LIMITLESS_API = "https://api.limitless.exchange"
BINANCE_API = "https://api.binance.com"

DATA_DIR = Path("./data/poly/limitless_trader")
DATA_DIR.mkdir(parents=True, exist_ok=True)
POSITIONS_FILE = DATA_DIR / "positions.json"
TRADES_LOG = DATA_DIR / "trades.jsonl"
ALERTS_FILE = DATA_DIR / "alerts.json"
STATS_FILE = DATA_DIR / "stats.json"

# Pyth ticker → Binance symbol mapping
TICKER_TO_BINANCE = {
    "BTC": "BTCUSDC", "ETH": "ETHUSDC", "SOL": "SOLUSDC", "DOGE": "DOGEUSDT",
    "XRP": "XRPUSDC", "BNB": "BNBUSDC", "ADA": "ADAUSDC", "AVAX": "AVAXUSDC",
    "SUI": "SUIUSDT", "LINK": "LINKUSDC", "PEPE": "PEPEUSDT", "WIF": "WIFUSDT",
    "LTC": "LTCUSDC", "DOT": "DOTUSDT", "XLM": "XLMUSDT", "TRX": "TRXUSDT",
    "NEAR": "NEARUSDT", "UNI": "UNIUSDC", "AAVE": "AAVEUSDT", "MNT": "MNTUSDT",
    "LEO": "LEOUSDT", "XMR": "XMRUSDT",
}

MAX_HOURS = 0.10  # 6 min max — tighter window = more certain outcome
MIN_EDGE_PCT = 10.0  # raised from 8 — only strong edges
MIN_DISTANCE_PCT = 0.30  # raised from 0.10 — price must be 0.3%+ from strike (no coin flips)
MAX_ENTRY_PRICE = 0.70  # HARD CAP — never pay more than $0.70 per share (data: >0.80 = -$47.58 on 14 trades)
MAX_LOSS = 100.0  # paper mode budget
PREFERRED_TICKERS = {"BNB", "XRP", "ETH", "SOL", "DOGE", "BTC"}
PREFERRED_MIN_EDGE = 8.0  # raised from 5
EV_THRESHOLD = 8.0  # raised from 5 — require 8%+ EV at orderbook level


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def extract_ticker(title: str) -> Optional[str]:
    m = re.search(r'\$([A-Z]{2,6})', title)
    return m.group(1) if m else None


def extract_strike(title: str) -> Optional[float]:
    # Match "$BTC above $84,250.00" or "$ETH above $2,314.55"
    m = re.search(r'above \$([0-9,]+\.?\d*)', title)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def extract_expiry_ts(m: dict) -> Optional[int]:
    ts = m.get("expirationTimestamp", 0)
    if ts:
        return int(ts) // 1000 if int(ts) > 1e12 else int(ts)
    return None


INDICATOR_FILE = Path("./data/latest_market_data.json")
INDICATOR_CACHE: dict = {}
INDICATOR_CACHE_TS: float = 0


def load_indicators() -> dict:
    """Load indicators: market_data JSON (236 fields/symbol, ~30s old) merged with
    Redis hot_metrics (live 1m/3m stochastic, updated every second by indicators bridge)."""
    global INDICATOR_CACHE, INDICATOR_CACHE_TS
    now = time.time()
    if now - INDICATOR_CACHE_TS < 5 and INDICATOR_CACHE:
        return INDICATOR_CACHE
    try:
        if INDICATOR_FILE.exists():
            with open(INDICATOR_FILE) as f:
                INDICATOR_CACHE = json.load(f)
            INDICATOR_CACHE_TS = now
    except Exception:
        pass
    # Merge live 1m/3m stochastic from Redis hot_metrics (freshest data)
    try:
        import redis
        r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        for sym in list(INDICATOR_CACHE.keys()):
            raw = r.get(f"hot_metrics:{sym}")
            if raw:
                hot = json.loads(raw)
                tick_ts = hot.get("_tick_ts", 0)
                cache_ts = INDICATOR_CACHE[sym].get("ts", 0)
                if tick_ts > cache_ts:
                    # Merge fresh 1m/3m data into the full indicator set
                    for key in ["k_1m", "d_1m", "k_1m_prev", "d_1m_prev", "k_3m", "d_3m", "k_3m_prev", "d_3m_prev", "price", "_tick_ts"]:
                        if key in hot:
                            if key == "k_1m":
                                INDICATOR_CACHE[sym]["stoch_k_1m"] = hot[key]
                            elif key == "d_1m":
                                INDICATOR_CACHE[sym]["stoch_d_1m"] = hot[key]
                            elif key == "k_1m_prev":
                                INDICATOR_CACHE[sym]["k_1m_prev"] = hot[key]
                            elif key == "k_3m":
                                INDICATOR_CACHE[sym]["stoch_k_3m"] = hot[key]
                            elif key == "d_3m":
                                INDICATOR_CACHE[sym]["stoch_d_3m"] = hot[key]
                            elif key == "k_3m_prev":
                                INDICATOR_CACHE[sym]["k_3m_prev"] = hot[key]
                            elif key == "price":
                                INDICATOR_CACHE[sym]["current_price"] = hot[key]
                            elif key == "_tick_ts":
                                INDICATOR_CACHE[sym]["_tick_ts"] = hot[key]
    except Exception:
        pass  # Redis not available — use file data only
    return INDICATOR_CACHE


def project_price(sym: str, minutes_ahead: float, indicators: dict) -> tuple:
    """Project price using multi-factor model from 236 indicators.

    Combines:
    1. Linear regression slopes (weighted across timeframes)
    2. Stochastic momentum (K speed + overbought/oversold reversal)
    3. Donchian channel ceiling/floor resistance
    4. Heikin-Ashi trend alignment
    5. WaveTrend direction
    6. Velocity (divergence acceleration)

    Returns: (projected_price, confidence 0-1)
    """
    price = indicators.get("current_price", 0)
    if not price:
        return 0, 0
    # === 1. LINEAR REGRESSION SLOPE (primary projector) ===
    slope_per_min = 0
    w_total = 0
    for tf, candle_min, w in [("3m", 3, 0.40), ("15m", 15, 0.30), ("1h", 60, 0.20), ("4h", 240, 0.10)]:
        s = indicators.get(f"lr_trend_{tf}", 0)
        if s:
            slope_per_min += (s / candle_min) * w
            w_total += w
    if w_total > 0:
        slope_per_min /= w_total
    lr_proj = price + slope_per_min * minutes_ahead
    # === 2. STOCHASTIC MOMENTUM ===
    stoch_adj = 0
    for tf, w in [("1m", 0.35), ("3m", 0.30), ("15m", 0.20), ("1h", 0.15)]:
        k = indicators.get(f"stoch_k_{tf}", indicators.get(f"k_{tf}_prev", 50))
        k_prev = indicators.get(f"k_{tf}_prev", k)
        delta_k = k - k_prev
        if k > 85 and delta_k < 0:
            stoch_adj -= w * 0.3
        elif k > 85 and delta_k > 0:
            stoch_adj += w * 0.1
        elif k < 15 and delta_k > 0:
            stoch_adj += w * 0.3
        elif k < 15 and delta_k < 0:
            stoch_adj -= w * 0.1
        else:
            stoch_adj += w * (delta_k / 30)
    atr = indicators.get("atr_3m", 0) if minutes_ahead <= 10 else indicators.get("atr_15m", 0)
    if not atr:
        atr = price * 0.003
    stoch_price_adj = stoch_adj * atr
    # === 3. DONCHIAN CHANNEL RESISTANCE ===
    tf_dc = "3m" if minutes_ahead <= 10 else "15m"
    dc_h = indicators.get(f"dc_high_{tf_dc}", price * 1.1)
    dc_l = indicators.get(f"dc_low_{tf_dc}", price * 0.9)
    dc_range = dc_h - dc_l if dc_h > dc_l else 1
    dc_pos = (price - dc_l) / dc_range
    dc_cap = 0
    if dc_pos > 0.9:
        dc_cap = -0.15 * atr
    elif dc_pos < 0.1:
        dc_cap = 0.15 * atr
    # === 4. HEIKIN-ASHI ALIGNMENT ===
    ha_score = 0
    for tf in ["3m", "15m", "1h"]:
        ha = indicators.get(f"ha_{tf}", "")
        if ha == "green":
            ha_score += 1
        elif ha == "red":
            ha_score -= 1
    ha_adj = ha_score * 0.05 * atr
    # === 5. WAVETREND ===
    wt_adj = sum(indicators.get(f"wt_score_{tf}", 0) * 0.005 * atr for tf in ["3m", "15m"])
    # === 6. VELOCITY ===
    vel_adj = indicators.get("velocity", 0) * 0.02 * atr
    # === COMBINE ===
    projected = lr_proj + stoch_price_adj + dc_cap + ha_adj + wt_adj + vel_adj
    # Confidence = signal alignment
    components = [slope_per_min * minutes_ahead, stoch_price_adj, dc_cap, ha_adj]
    bullish = sum(1 for c in components if c > 0)
    bearish = sum(1 for c in components if c < 0)
    confidence = abs(bullish - bearish) / max(len(components), 1)
    return projected, confidence


def indicator_probability(ticker: str, strike: float, hours_left: float, spot: float) -> float:
    """Predict probability of 'price above strike' using projected price from 236 indicators."""
    if hours_left <= 0:
        return 1.0 if spot > strike else 0.0
    if strike <= 0 or spot <= 0:
        return 0.5
    sym = TICKER_TO_BINANCE.get(ticker, "").replace("USDT", "USDC")
    indicators = load_indicators().get(sym, {})
    if not indicators:
        return _fallback_probability(spot, strike, hours_left)
    minutes = hours_left * 60
    projected, confidence = project_price(sym, minutes, indicators)
    if projected <= 0:
        return _fallback_probability(spot, strike, hours_left)
    # How far is the projected price from the strike?
    proj_distance = projected - strike
    # Use ATR to normalize distance → probability
    atr_key = "atr_3m" if minutes <= 10 else ("atr_15m" if minutes <= 30 else "atr_1h")
    atr = indicators.get(atr_key, 0)
    if not atr:
        atr = spot * 0.003
    # Scale by time horizon: more uncertainty over longer periods
    time_scale = math.sqrt(max(1, minutes / 3))
    uncertainty = atr * time_scale * 0.5
    if uncertainty < 0.01:
        return 1.0 if proj_distance > 0 else 0.0
    z = proj_distance / uncertainty
    # Higher confidence = steeper sigmoid (more decisive)
    steepness = 1.5 + confidence * 1.5  # 1.5 (low conf) to 3.0 (high conf)
    prob = 1.0 / (1.0 + math.exp(-steepness * z))
    return max(0.01, min(0.99, prob))


def _fallback_probability(spot: float, strike: float, hours_left: float) -> float:
    """Simple fallback when no indicator data available."""
    distance_pct = (spot - strike) / strike * 100
    hourly_vol = 0.5
    vol = hourly_vol * math.sqrt(hours_left)
    if vol < 0.01:
        return 1.0 if distance_pct > 0 else 0.0
    z = distance_pct / vol
    return max(0.01, min(0.99, 1.0 / (1.0 + math.exp(-1.7 * z))))


class PositionTracker:
    def __init__(self):
        self.positions: dict = {}
        self.closed: list = []
        self._load()

    def _load(self):
        if POSITIONS_FILE.exists():
            try:
                with open(POSITIONS_FILE) as f:
                    d = json.load(f)
                self.positions = d.get("positions", {})
                self.closed = d.get("closed", [])
            except Exception:
                pass

    def _save(self):
        with open(POSITIONS_FILE, "w") as f:
            json.dump({"positions": self.positions, "closed": self.closed}, f, indent=2)

    def enter(self, key: str, data: dict) -> bool:
        if key in self.positions:
            return False
        self.positions[key] = data
        self._save()
        return True

    def close(self, key: str, exit_price: float, reason: str) -> Optional[dict]:
        if key not in self.positions:
            return None
        pos = self.positions.pop(key)
        side = pos.get("side", "YES")
        entry = pos.get("entry_price", 0)
        bet = pos.get("bet_usdc", 5)
        if side == "YES":
            pnl = (exit_price - entry) * (bet / entry)
        else:
            pnl = ((1.0 - exit_price) - (1.0 - entry)) * (bet / (1.0 - entry))
        pos.update({"exit_price": exit_price, "pnl_usdc": round(pnl, 4), "reason": reason, "closed_at": now_utc().isoformat()})
        self.closed.append(pos)
        self._save()
        return pos

    def exposure(self) -> float:
        return sum(p.get("bet_usdc", 0) for p in self.positions.values())

    def total_pnl(self) -> float:
        return sum(p.get("pnl_usdc", 0) for p in self.closed)

    def stats(self) -> dict:
        wins = [p for p in self.closed if p.get("pnl_usdc", 0) > 0]
        losses = [p for p in self.closed if p.get("pnl_usdc", 0) <= 0]
        total_bet = sum(p.get("bet_usdc", 0) for p in self.closed)
        total_pnl = self.total_pnl()
        return {"total": len(self.closed), "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(self.closed) * 100, 1) if self.closed else 0, "total_pnl": round(total_pnl, 2), "roi_pct": round(total_pnl / total_bet * 100, 2) if total_bet else 0, "open": len(self.positions), "exposure": round(self.exposure(), 2)}


class LimitlessTrader:
    def __init__(self, is_paper: bool, min_edge: float, interval: float, bet_size: float, max_exposure: float):
        self.is_paper = is_paper
        self.min_edge = min_edge
        self.interval = interval
        self.bet_size = bet_size
        self.max_exposure = max_exposure
        self.tracker = PositionTracker()
        self.session: Optional[aiohttp.ClientSession] = None
        self.binance_prices: dict = {}  # ticker -> price
        self._scan_count = 0
        # Limitless SDK for live order signing
        self._lim_pk = os.getenv("LIMITLESS_PK", "")
        self._lim_api_key = os.getenv("LIMITLESS_API_KEY", "")
        self._lim_owner_id = int(os.getenv("LIMITLESS_OWNER_ID", "0"))
        self._lim_exchange = "0x05c748E2f4DcDe0ec9Fa8DDc40DE6b867f923fa5"
        self._lim_account = None
        self._lim_signer = None
        if self._lim_pk:
            from eth_account import Account as EthAccount
            self._lim_account = EthAccount.from_key(self._lim_pk)

    async def _fetch_binance_prices(self):
        """Fetch current prices for all tracked tickers from Binance."""
        try:
            tasks = []
            for ticker, bsym in TICKER_TO_BINANCE.items():
                tasks.append((ticker, self.session.get(f"{BINANCE_API}/api/v3/ticker/price", params={"symbol": bsym}, timeout=aiohttp.ClientTimeout(total=3))))
            for ticker, ctx in tasks:
                try:
                    async with ctx as r:
                        if r.status == 200:
                            d = await r.json()
                            self.binance_prices[ticker] = float(d.get("price", 0))
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Binance prices: {e}")

    async def _fetch_limitless_markets(self) -> list:
        """Fetch active markets from Limitless."""
        all_markets = []
        for page in range(1, 20):
            try:
                async with self.session.get(f"{LIMITLESS_API}/markets/active", params={"limit": 25, "page": page}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    resp = await r.json(content_type=None)
                batch = resp.get("data", []) if isinstance(resp, dict) else resp
                if not isinstance(batch, list) or not batch:
                    break
                all_markets.extend(batch)
                if len(batch) < 25:
                    break
            except Exception as e:
                logger.warning(f"Limitless fetch page={page}: {e}")
                break
        return all_markets

    async def _get_book(self, slug: str) -> dict:
        try:
            async with self.session.get(f"{LIMITLESS_API}/markets/{slug}/orderbook", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    return await r.json(content_type=None)
        except Exception:
            pass
        return {}

    def _analyze_market(self, m: dict) -> Optional[dict]:
        """Check if a market has a tradeable edge based on Binance oracle price."""
        title = m.get("title", "")
        ticker = extract_ticker(title)
        if not ticker or ticker not in self.binance_prices:
            return None
        strike = extract_strike(title)
        if not strike:
            return None
        expiry_ts = extract_expiry_ts(m)
        if not expiry_ts:
            return None
        now = now_utc()
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        hours_left = (expiry_dt - now).total_seconds() / 3600
        if hours_left < 0 or hours_left > MAX_HOURS:
            return None
        current_price = self.binance_prices[ticker]
        true_prob = indicator_probability(ticker, strike, hours_left, current_price)
        # Get market prices — use last trade price for initial filter
        prices = m.get("prices", [0, 0])
        ltp_yes = float(prices[0]) if len(prices) > 0 else 0
        if ltp_yes < 0.01 or ltp_yes > 0.99:
            return None
        # Quick pre-filter on last-trade-price (cheap, no API call)
        edge_yes_ltp = (true_prob - ltp_yes) * 100
        edge_no_ltp = ((1 - true_prob) - (1 - ltp_yes)) * 100
        if max(edge_yes_ltp, edge_no_ltp) < self.min_edge * 0.5:
            return None  # not even close, skip orderbook fetch
        market_yes = ltp_yes
        market_no = 1.0 - ltp_yes
        # Calculate edge based on display price (will verify vs orderbook in _trade)
        edge_yes = edge_yes_ltp
        edge_no = edge_no_ltp
        best_side = "YES" if edge_yes > edge_no else "NO"
        best_edge = max(edge_yes, edge_no)
        # Preferred tickers get lower edge threshold (better backtested)
        min_edge = PREFERRED_MIN_EDGE if ticker in PREFERRED_TICKERS else self.min_edge
        if best_edge < min_edge:
            return None
        distance_pct = abs(current_price - strike) / strike * 100
        if distance_pct < MIN_DISTANCE_PCT:
            return None
        is_near_expiry = hours_left * 60 <= 10
        tokens = m.get("tokens", {})
        return {
            "slug": m.get("slug", ""),
            "market_id": str(m.get("id", "")),
            "title": title[:80],
            "ticker": ticker,
            "strike": strike,
            "current_price": current_price,
            "hours_left": round(hours_left, 2),
            "true_prob": round(true_prob, 4),
            "market_yes": market_yes,
            "market_no": market_no,
            "side": best_side,
            "edge_pct": round(best_edge, 2),
            "entry_price": market_yes if best_side == "YES" else market_no,
            "near_expiry": is_near_expiry,
            "yes_token": tokens.get("yes", ""),
            "no_token": tokens.get("no", ""),
            "expiry_ts": expiry_ts,
        }

    async def _trade(self, opp: dict):
        exposure = self.tracker.exposure()
        if exposure >= self.max_exposure:
            return
        key = opp["market_id"]
        if key in self.tracker.positions:
            return
        if self.tracker.total_pnl() < -MAX_LOSS:
            logger.warning(f"MAX_LOSS reached (${self.tracker.total_pnl():.2f}), skipping trades")
            return
        # Verify edge against orderbook
        book = await self._get_book(opp["slug"])
        if not book:
            return
        asks = book.get("asks", [])
        bids = book.get("bids", [])
        if opp["side"] == "YES":
            # Buy YES = buy from asks
            if not asks:
                return
            best_ask = min(float(a.get("price", 1)) for a in asks if float(a.get("size", 0)) > 0)
            actual_entry = best_ask
            actual_edge = (opp["true_prob"] - actual_entry) * 100
        else:
            # Buy NO = sell YES into bids (or buy NO asks if available)
            if not bids:
                return
            best_bid = max(float(b.get("price", 0)) for b in bids if float(b.get("size", 0)) > 0)
            actual_entry = 1.0 - best_bid  # NO cost = 1 - YES bid
            actual_edge = ((1 - opp["true_prob"]) - actual_entry) * 100 if opp["side"] == "NO" else 0
            # Recalculate: if buying NO, edge = true_no_prob - no_cost
            actual_edge = ((1 - opp["true_prob"]) - actual_entry) * 100
        # HARD CAP: never buy above MAX_ENTRY_PRICE (data: entries >0.80 lost -$47.58 on 14 trades)
        if actual_entry > MAX_ENTRY_PRICE:
            logger.info(f"  PRICE-CAP {opp['ticker']} {opp['side']}: entry={actual_entry:.3f} > {MAX_ENTRY_PRICE} BLOCKED")
            return
        # EV filter: model probability must exceed entry price by EV_THRESHOLD
        ev_edge = (opp["true_prob"] - actual_entry) * 100 if opp["side"] == "YES" else ((1 - opp["true_prob"]) - actual_entry) * 100
        if ev_edge < EV_THRESHOLD:
            if ev_edge > 0:
                logger.info(f"  EV-CHECK {opp['ticker']} {opp['side']}: ev={ev_edge:.1f}% < {EV_THRESHOLD}% | entry={actual_entry:.3f} prob={opp['true_prob']:.3f}")
            return
        bet = min(self.bet_size, self.max_exposure - exposure)
        if bet < 1.0:
            return
        pos_data = {"title": opp["title"], "ticker": opp["ticker"], "side": opp["side"], "strike": opp["strike"], "current_price": opp["current_price"], "true_prob": opp["true_prob"], "entry_price": round(actual_entry, 4), "edge_pct": round(actual_edge, 2), "bet_usdc": bet, "hours_left": opp["hours_left"], "expiry_ts": opp["expiry_ts"], "slug": opp["slug"], "entered_at": now_utc().isoformat()}
        mode = "PAPER" if self.is_paper else "LIVE"
        if self.is_paper:
            if self.tracker.enter(key, pos_data):
                logger.info(f"  {mode} {opp['side']} {opp['ticker']} | edge={actual_edge:.1f}% entry={actual_entry:.3f} true_p={opp['true_prob']:.3f} | strike=${opp['strike']:,.2f} spot=${opp['current_price']:,.2f} | {opp['hours_left']:.1f}h left | {opp['title'][:45]}")
                with open(TRADES_LOG, "a") as f:
                    f.write(json.dumps({"action": "ENTER", "mode": mode, **pos_data}) + "\n")
        else:
            if not self._lim_account:
                return
            try:
                ok = await self._place_live_order(opp, actual_entry, bet)
                if ok and self.tracker.enter(key, pos_data):
                    logger.info(f"  LIVE {opp['side']} {opp['ticker']} | edge={actual_edge:.1f}% entry={actual_entry:.3f} | strike=${opp['strike']:,.2f} spot=${opp['current_price']:,.2f} | {opp['hours_left']:.1f}h left | {opp['title'][:45]}")
                    with open(TRADES_LOG, "a") as f:
                        f.write(json.dumps({"action": "ENTER", "mode": "LIVE", **pos_data}) + "\n")
            except Exception as e:
                logger.warning(f"  LIVE ORDER FAILED {opp['ticker']}: {e}")

    async def _place_live_order(self, opp: dict, entry_price: float, bet_usdc: float) -> bool:
        """Place a live order on Limitless via SDK create_order + direct HTTP submit."""
        if not self._lim_account:
            return False
        try:
            from limitless_sdk import LimitlessClient
            client = LimitlessClient(private_key=self._lim_pk, api_key=self._lim_api_key)
            await client.create_session()
            # Patch _sign_order with correct contract address (SDK bug workaround)
            from eth_account.messages import encode_typed_data
            def patched_sign(order, is_negrisk=False):
                domain = {"name": "Limitless CTF Exchange", "version": "1", "chainId": 8453, "verifyingContract": self._lim_exchange}
                types = {"Order": [{"name": "salt", "type": "uint256"}, {"name": "maker", "type": "address"}, {"name": "signer", "type": "address"}, {"name": "taker", "type": "address"}, {"name": "tokenId", "type": "uint256"}, {"name": "makerAmount", "type": "uint256"}, {"name": "takerAmount", "type": "uint256"}, {"name": "expiration", "type": "uint256"}, {"name": "nonce", "type": "uint256"}, {"name": "feeRateBps", "type": "uint256"}, {"name": "side", "type": "uint8"}, {"name": "signatureType", "type": "uint8"}]}
                msg = {"salt": order.salt, "maker": order.maker, "signer": order.signer, "taker": order.taker, "tokenId": int(order.tokenId), "makerAmount": order.makerAmount, "takerAmount": order.takerAmount, "expiration": int(order.expiration) if order.expiration else 0, "nonce": order.nonce, "feeRateBps": order.feeRateBps, "side": order.side, "signatureType": order.signatureType}
                typed = {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}], **types}, "primaryType": "Order", "domain": domain, "message": msg}
                encoded = encode_typed_data(full_message=typed)
                signed = client.account.sign_message(encoded)
                sig = signed.signature.hex()
                return "0x" + sig if not sig.startswith("0x") else sig
            client._sign_order = patched_sign
            price = round(entry_price, 2)
            outcome_index = 0 if opp["side"] == "YES" else 1
            order_dto = await client.create_order(market_id=opp["market_id"], market_slug=opp["slug"], outcome_index=outcome_index, side=0, amount=bet_usdc, price=price, order_type="GTC")
            from dataclasses import asdict
            payload = asdict(order_dto)
            logger.info(f"  ORDER SUBMIT: {opp['side']} {opp['ticker']} price={price} slug={opp['slug'][:30]}")
            async with client.session.post(f"{LIMITLESS_API}/orders", json=payload) as r:
                if r.status == 201:
                    data = await r.json()
                    logger.info(f"  ORDER OK: {data.get('order', {}).get('id', '?')[:20]}")
                    await client.close_session()
                    return True
                else:
                    text = await r.text()
                    logger.warning(f"  ORDER REJECTED {r.status}: {text[:150]}")
            await client.close_session()
        except Exception as e:
            logger.warning(f"  ORDER ERROR: {type(e).__name__}: {str(e)[:150]}")
        return False

    async def _check_hedge(self):
        """DISABLED — Limitless positions resolve binary (0 or 1). No partial exit possible.
        HEDGE_EXIT was generating fake P&L (+$31.78 of phantom profits). Hold to resolution only."""
        if not self.tracker.positions:
            return
        for key in list(self.tracker.positions.keys()):
            pos = self.tracker.positions.get(key)
            if not pos:
                continue
            ticker = pos.get("ticker", "")
            strike = pos.get("strike", 0)
            side = pos.get("side", "YES")
            spot = self.binance_prices.get(ticker, 0)
            if spot <= 0 or strike <= 0:
                continue
            wrong_side = (side == "NO" and spot > strike * 1.002) or (side == "YES" and spot < strike * 0.998)
            if wrong_side:
                logger.warning(f"  CROSSED {side} {ticker} | strike=${strike:,.2f} spot=${spot:,.2f} | holding to resolution (binary outcome)")

    async def _check_resolutions(self):
        """Check if any open positions have resolved via Limitless API."""
        if not self.tracker.positions:
            return
        now = now_utc()
        mode = "PAPER" if self.is_paper else "LIVE"
        for key in list(self.tracker.positions.keys()):
            pos = self.tracker.positions.get(key)
            if not pos:
                continue
            expiry_ts = pos.get("expiry_ts", 0)
            if not expiry_ts:
                continue
            expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
            # Wait at least 2 min after expiry for Pyth oracle to settle
            if now < expiry_dt + timedelta(minutes=2):
                continue
            slug = pos.get("slug", "")
            side = pos.get("side", "YES")
            strike = pos.get("strike", 0)
            # Try to get resolution from Limitless API
            resolved_yes = None
            try:
                async with self.session.get(f"{LIMITLESS_API}/markets/{slug}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        mdata = await r.json(content_type=None)
                        winner = mdata.get("winningOutcomeIndex")
                        if winner is not None:
                            resolved_yes = (winner == 0)  # 0=YES wins, 1=NO wins
            except Exception:
                pass
            # Fallback to Binance if API didn't give resolution
            if resolved_yes is None:
                ticker = pos.get("ticker", "")
                current_price = self.binance_prices.get(ticker, 0)
                if current_price <= 0:
                    continue
                dist = abs(current_price - strike) / strike * 100
                if dist < 2.0:
                    # Too close — wait longer for Limitless API to provide official resolution
                    age_min = (now - expiry_dt).total_seconds() / 60
                    if age_min < 30:
                        continue  # wait up to 30 min for API
                    # After 30 min, use Binance as final fallback
                resolved_yes = current_price > strike
            if resolved_yes is None:
                continue
            if (side == "YES" and resolved_yes) or (side == "NO" and not resolved_yes):
                exit_price = 1.0
            else:
                exit_price = 0.0
            result = self.tracker.close(key, exit_price, "RESOLVED")
            if result:
                tag = "WIN" if result["pnl_usdc"] > 0 else "LOSS"
                logger.info(f"  {tag} {mode} {side} {pos.get('ticker','')} | pnl=${result['pnl_usdc']:+.2f} | strike=${strike:,.2f} resolved={'YES' if resolved_yes else 'NO'} | {result.get('title', '')[:45]}")
                with open(TRADES_LOG, "a") as f:
                    f.write(json.dumps({"action": "CLOSE", "mode": mode, **result}) + "\n")

    async def _scan_cycle(self):
        self._scan_count += 1
        t0 = time.time()
        await self._fetch_binance_prices()
        markets = await self._fetch_limitless_markets()
        opportunities = []
        for m in markets:
            opp = self._analyze_market(m)
            if opp:
                opportunities.append(opp)
        opportunities.sort(key=lambda o: -o["edge_pct"])
        elapsed = time.time() - t0
        mode = "PAPER" if self.is_paper else "LIVE"
        s = self.tracker.stats()
        logger.info(f"Scan #{self._scan_count}: {len(markets)} markets, {len(opportunities)} edges | {s['wins']}W/{s['losses']}L pnl=${s['total_pnl']:+.2f} open={s['open']} exp=${s['exposure']:.0f} | {elapsed:.1f}s")
        for opp in opportunities[:5]:
            tag = "LAST-MIN" if opp.get("near_expiry") else "EDGE"
            logger.info(f"  {tag} {opp['side']} {opp['ticker']} {opp['edge_pct']:+.1f}% | mkt_yes={opp['market_yes']:.3f} true_p={opp['true_prob']:.3f} | ${opp['strike']:,.2f} vs ${opp['current_price']:,.2f} | {opp['hours_left']*60:.0f}min | {opp['title'][:40]}")
        # Write live alerts for local watcher
        alert_data = {"ts": now_utc().isoformat(), "count": len(opportunities), "edges": [{"side": o["side"], "ticker": o["ticker"], "edge_pct": o["edge_pct"], "entry_price": o["entry_price"], "strike": o["strike"], "spot": o["current_price"], "mins_left": round(o["hours_left"] * 60, 1), "title": o["title"], "slug": o["slug"], "url": f"https://limitless.exchange/markets/{o['slug']}"} for o in opportunities[:5]]}
        with open(ALERTS_FILE, "w") as f:
            json.dump(alert_data, f, indent=2)
        for opp in opportunities:
            await self._trade(opp)
        await self._check_resolutions()
        # Save stats
        with open(STATS_FILE, "w") as f:
            json.dump({**s, "scan_count": self._scan_count, "last_scan": now_utc().isoformat(), "binance_prices": {k: v for k, v in list(self.binance_prices.items())[:5]}}, f, indent=2)

    async def run(self):
        mode = "PAPER" if self.is_paper else "LIVE"
        logger.info(f"Limitless Trader | mode={mode} | min_edge={self.min_edge}% | bet=${self.bet_size} | max_exp=${self.max_exposure} | max_hours={MAX_HOURS}h | interval={self.interval}s")
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
    parser = argparse.ArgumentParser(description="Limitless Exchange crypto scalper — Binance oracle edge")
    parser.add_argument("--live", action="store_true", help="Real trades (default: paper)")
    parser.add_argument("--min-edge", type=float, default=MIN_EDGE_PCT, help=f"Min edge %% vs Binance oracle (default: {MIN_EDGE_PCT})")
    parser.add_argument("--bet", type=float, default=5.0, help="Bet size in USDC (default: 5)")
    parser.add_argument("--max-exposure", type=float, default=40.0, help="Max total exposure (default: 40)")
    parser.add_argument("--interval", type=float, default=30.0, help="Scan interval seconds (default: 30)")
    args = parser.parse_args()
    trader = LimitlessTrader(is_paper=not args.live, min_edge=args.min_edge, interval=args.interval, bet_size=args.bet, max_exposure=args.max_exposure)
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        s = trader.tracker.stats()
        logger.info(f"Stopped. {s['wins']}W/{s['losses']}L pnl=${s['total_pnl']:+.2f} roi={s['roi_pct']:+.2f}%")


if __name__ == "__main__":
    main()
