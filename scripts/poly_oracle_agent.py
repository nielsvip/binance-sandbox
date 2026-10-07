#!/usr/bin/env python3
"""poly_oracle_agent.py — Real-time oracle lag agent with ms-level advantage.

Architecture:
  Multiple concurrent data feeds → OutcomeRegistry → MarketMatcher → OrderEngine

Feeds (all async, no polling sleep between requests):
  NHLFeed       0.5s  api-web.nhle.com (fastest free sports feed)
  NBAFeed       1.0s  cdn.nba.com live scoreboard
  ESPNFeed      1.5s  NFL, MLB, UFC, Boxing, MLS, Tennis
  SofaScoreFeed 1.5s  Soccer (60+ live events), Tennis, Basketball
  BinanceFeed   WS    Crypto — millisecond WebSocket feed
  ElectionFeed  5.0s  AP RSS + Wikipedia (elections, politics)

Outcome confirmation:
  1 source  → WATCH    (pre-position: load market, warm CLOB connection)
  2 sources → EXECUTE  (fire order immediately)

Pre-positioning: when a game enters final 3 minutes / 4th period, the agent
pre-fetches market token IDs and pre-computes order parameters so the actual
order submission is instant when the final whistle fires.

Usage:
  python poly_oracle_agent.py                     # paper mode
  python poly_oracle_agent.py --live              # real trades
  python poly_oracle_agent.py --min-sources 1     # fire on first confirmation
  python poly_oracle_agent.py --max-bet 50        # max USDC per trade
  python poly_oracle_agent.py --feed nhl,nba      # only specific feeds
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("oracle_agent")

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
BINANCE_WS = "wss://stream.binance.com:9443/ws"
DATA_DIR = Path("./data/poly/oracle_agent")
DATA_DIR.mkdir(parents=True, exist_ok=True)
EXEC_LOG = DATA_DIR / "executions.jsonl"
TIMING_LOG = DATA_DIR / "timing.jsonl"

POLY_FEE = 0.02
DEFAULT_BET_SIZE = 20.0
PREPOSITION_PROB_THRESHOLD = 0.80


# ── Data Model ─────────────────────────────────────────────────────────────────

@dataclass
class FeedEvent:
    source: str
    league: str
    event_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    status: str          # FINAL, IN_PROGRESS, SCHEDULED
    period: str          # "3rd Period", "Q4", "90+2"
    clock: str           # "00:00", "2:34"
    winner: Optional[str]
    detected_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        teams = sorted([self.home_team.lower(), self.away_team.lower()])
        return f"{self.league}:{teams[0]}_{teams[1]}"

    @property
    def is_final(self) -> bool:
        return self.status == "FINAL"

    @property
    def is_late_game(self) -> bool:
        if self.status != "IN_PROGRESS":
            return False
        period_finals = {"Q4", "OT", "4th", "3rd Period", "period3", "period4", "2nd half", "period2"}
        if any(p in self.period for p in ("Q4", "4th", "OT", "Overtime", "period3", "2nd half")):
            return True
        return False

@dataclass
class MarketSignal:
    poly_market: dict
    event: FeedEvent
    token_id: str
    side: str            # BUY
    direction: str       # YES or NO
    entry_price: float
    expected_exit: float
    profit_pct: float
    confirmed_by: List[str]
    signal_ms: int = field(default_factory=lambda: int(time.time() * 1000))


# ── Outcome Registry ───────────────────────────────────────────────────────────

class OutcomeRegistry:
    def __init__(self):
        self._finals: Dict[str, List[FeedEvent]] = {}
        self._late_game: Dict[str, List[FeedEvent]] = {}
        self._notified_final: Set[str] = set()
        self._notified_late: Set[str] = set()
        self._lock = asyncio.Lock()
        self.on_final: Optional[Callable] = None
        self.on_late_game: Optional[Callable] = None

    async def push(self, event: FeedEvent):
        async with self._lock:
            key = event.key
            if event.is_final:
                existing = self._finals.setdefault(key, [])
                sources = {e.source for e in existing}
                if event.source not in sources:
                    existing.append(event)
                    logger.info(f"FINAL [{len(existing)} src] {event.source}: {event.home_team} {event.home_score} - {event.away_score} {event.away_team} (league={event.league})")
                if key not in self._notified_final and self.on_final:
                    self._notified_final.add(key)
                    asyncio.ensure_future(self.on_final(existing))
            elif event.is_late_game:
                existing = self._late_game.setdefault(key, [])
                sources = {e.source for e in existing}
                if event.source not in sources:
                    existing.append(event)
                if key not in self._notified_late and self.on_late_game:
                    self._notified_late.add(key)
                    asyncio.ensure_future(self.on_late_game(existing))

    def get_final_events(self) -> List[List[FeedEvent]]:
        return list(self._finals.values())

    def source_count(self, key: str) -> int:
        return len(self._finals.get(key, []))


# ── Data Feeds ─────────────────────────────────────────────────────────────────

class BaseFeed:
    NAME = "base"
    INTERVAL = 2.0

    def __init__(self, session: aiohttp.ClientSession, registry: OutcomeRegistry):
        self.session = session
        self.registry = registry
        self._seen_finals: Set[str] = set()

    async def run(self):
        logger.info(f"Feed started: {self.NAME} (interval={self.INTERVAL}s)")
        while True:
            t0 = time.time()
            try:
                events = await self.poll()
                for ev in events:
                    await self.registry.push(ev)
            except Exception as e:
                logger.debug(f"{self.NAME} poll error: {e}")
            elapsed = time.time() - t0
            await asyncio.sleep(max(0, self.INTERVAL - elapsed))

    async def poll(self) -> List[FeedEvent]:
        raise NotImplementedError


class NHLFeed(BaseFeed):
    NAME = "nhl"
    INTERVAL = 0.5

    async def poll(self) -> List[FeedEvent]:
        async with self.session.get("https://api-web.nhle.com/v1/score/now", timeout=aiohttp.ClientTimeout(total=3)) as resp:
            data = await resp.json(content_type=None)
        events = []
        for g in data.get("games", []):
            state = g.get("gameState", "")
            clock = g.get("clock", {})
            period_desc = g.get("periodDescriptor", {})
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            home_name = home.get("name", {}).get("default", home.get("abbrev", ""))
            away_name = away.get("name", {}).get("default", away.get("abbrev", ""))
            h_score = int(home.get("score", 0))
            a_score = int(away.get("score", 0))
            period_num = period_desc.get("number", 0)
            period_type = period_desc.get("periodType", "")
            period_str = f"P{period_num}" + (f" {period_type}" if period_type and period_type != "REG" else "")
            clock_str = clock.get("timeRemaining", "")
            is_final = state in ("OFF", "FINAL", "OFFICIAL")
            is_live = state in ("LIVE", "CRIT")
            if not (is_final or is_live):
                continue
            winner = None
            if is_final:
                winner = home_name if h_score > a_score else away_name
            ev = FeedEvent(source="nhl", league="nhl", event_id=str(g.get("id")), home_team=home_name, away_team=away_name, home_score=h_score, away_score=a_score, status="FINAL" if is_final else "IN_PROGRESS", period=period_str, clock=clock_str, winner=winner, raw=g)
            events.append(ev)
        return events


class NBAFeed(BaseFeed):
    NAME = "nba"
    INTERVAL = 1.0

    async def poll(self) -> List[FeedEvent]:
        async with self.session.get("https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json", timeout=aiohttp.ClientTimeout(total=3)) as resp:
            data = await resp.json(content_type=None)
        events = []
        for g in data.get("scoreboard", {}).get("games", []):
            status_code = int(g.get("gameStatus", 1))
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            h_name = home.get("teamName", home.get("teamCity", ""))
            a_name = away.get("teamName", away.get("teamCity", ""))
            h_score = int(home.get("score", 0))
            a_score = int(away.get("score", 0))
            period = g.get("period", 0)
            clock = g.get("gameClock", "")
            is_final = status_code == 3
            is_live = status_code == 2
            if not (is_final or is_live):
                continue
            winner = None
            if is_final:
                winner = h_name if h_score > a_score else a_name
            ev = FeedEvent(source="nba", league="nba", event_id=g.get("gameId", ""), home_team=h_name, away_team=a_name, home_score=h_score, away_score=a_score, status="FINAL" if is_final else "IN_PROGRESS", period=f"Q{period}", clock=clock, winner=winner, raw=g)
            events.append(ev)
        return events


class ESPNFeed(BaseFeed):
    NAME = "espn"
    INTERVAL = 1.5
    LEAGUES = [("football", "nfl"), ("baseball", "mlb"), ("mma", "ufc"), ("basketball", "wnba"), ("soccer", "usa.1")]

    async def poll(self) -> List[FeedEvent]:
        all_events = []
        for sport, league in self.LEAGUES:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    data = await resp.json(content_type=None)
                for ev_raw in data.get("events", []):
                    ev = self._parse_espn_event(ev_raw, league)
                    if ev:
                        all_events.append(ev)
            except Exception:
                pass
        return all_events

    def _parse_espn_event(self, ev_raw: dict, league: str) -> Optional[FeedEvent]:
        status = ev_raw.get("status", {}).get("type", {})
        if not (status.get("completed") or status.get("state") == "in"):
            return None
        comps = ev_raw.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if len(competitors) < 2:
            return None
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        h_name = home.get("team", {}).get("displayName", "")
        a_name = away.get("team", {}).get("displayName", "")
        h_score = int(home.get("score", 0) or 0)
        a_score = int(away.get("score", 0) or 0)
        is_final = bool(status.get("completed"))
        winner = None
        if is_final:
            w = next((c.get("team", {}).get("displayName") for c in competitors if c.get("winner")), None)
            winner = w or (h_name if h_score > a_score else a_name)
        return FeedEvent(source="espn", league=league, event_id=ev_raw.get("id", ""), home_team=h_name, away_team=a_name, home_score=h_score, away_score=a_score, status="FINAL" if is_final else "IN_PROGRESS", period=ev_raw.get("status", {}).get("type", {}).get("shortDetail", ""), clock=ev_raw.get("status", {}).get("displayClock", ""), winner=winner, raw=ev_raw)


class SofaScoreFeed(BaseFeed):
    NAME = "sofascore"
    INTERVAL = 1.5
    SPORTS = ["football", "basketball", "baseball", "hockey", "american-football"]
    HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", "Referer": "https://www.sofascore.com/"}

    async def poll(self) -> List[FeedEvent]:
        all_events = []
        for sport in self.SPORTS:
            try:
                url = f"https://api.sofascore.com/api/v1/sport/{sport}/events/live"
                async with self.session.get(url, headers=self.HEADERS, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    data = await resp.json(content_type=None)
                for ev_raw in data.get("events", []):
                    ev = self._parse_sofa_event(ev_raw, sport)
                    if ev:
                        all_events.append(ev)
            except Exception:
                pass
        # Also check recent finished events
        try:
            url = "https://api.sofascore.com/api/v1/sport/football/events/last/0"
            async with self.session.get(url, headers=self.HEADERS, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                data = await resp.json(content_type=None)
            for ev_raw in data.get("events", []):
                ev = self._parse_sofa_event(ev_raw, "football", only_recent=True)
                if ev:
                    all_events.append(ev)
        except Exception:
            pass
        return all_events

    def _parse_sofa_event(self, ev_raw: dict, sport: str, only_recent: bool = False) -> Optional[FeedEvent]:
        status = ev_raw.get("status", {})
        s_type = status.get("type", "")
        s_code = status.get("code", 0)
        is_final = s_type == "finished" or s_code in (100, 110, 120, 70, 60)
        is_live = s_type == "inprogress"
        if only_recent and not is_final:
            return None
        if not (is_final or is_live):
            return None
        h = ev_raw.get("homeTeam", {})
        a = ev_raw.get("awayTeam", {})
        h_name = h.get("name", h.get("shortName", ""))
        a_name = a.get("name", a.get("shortName", ""))
        h_score = ev_raw.get("homeScore", {}).get("current", 0) or 0
        a_score = ev_raw.get("awayScore", {}).get("current", 0) or 0
        winner = None
        if is_final:
            if h_score > a_score:
                winner = h_name
            elif a_score > h_score:
                winner = a_name
            else:
                winner = "DRAW"
        time_info = ev_raw.get("time", {})
        period = ev_raw.get("lastPeriod", status.get("description", ""))
        return FeedEvent(source="sofascore", league=sport, event_id=str(ev_raw.get("id", "")), home_team=h_name, away_team=a_name, home_score=int(h_score), away_score=int(a_score), status="FINAL" if is_final else "IN_PROGRESS", period=str(period), clock="", winner=winner, raw=ev_raw)


class BinanceFeed(BaseFeed):
    NAME = "binance"
    INTERVAL = 0.1
    SYMBOLS = {"BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC"}
    PRICE_THRESHOLD_PCT = 0.005

    def __init__(self, session, registry, price_callback: Callable = None):
        super().__init__(session, registry)
        self.prices: Dict[str, float] = {}
        self.price_callback = price_callback

    async def run(self):
        logger.info(f"Feed started: {self.NAME} (WebSocket)")
        streams = "/".join(f"{s.lower()}@aggTrade" for s in self.SYMBOLS)
        url = f"{BINANCE_WS}/{streams}"
        while True:
            try:
                async with self.session.ws_connect(url, heartbeat=20) as ws:
                    logger.info(f"Binance WS connected: {len(self.SYMBOLS)} symbols")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            stream = data.get("stream", "")
                            symbol = stream.split("@")[0].upper()
                            price = float(data.get("data", data).get("p", 0))
                            if price > 0 and symbol in self.prices:
                                old = self.prices[symbol]
                                pct_chg = abs(price - old) / old
                                if pct_chg > self.PRICE_THRESHOLD_PCT and self.price_callback:
                                    await self.price_callback(symbol, price, pct_chg, int(time.time() * 1000))
                            self.prices[symbol] = price
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
            except Exception as e:
                logger.warning(f"Binance WS error: {e} — reconnecting in 2s")
                await asyncio.sleep(2)


class ElectionFeed(BaseFeed):
    NAME = "election"
    INTERVAL = 5.0
    SOURCES = [("ap_rss", "https://rsshub.app/apnews/topics/politics"), ("bbc_rss", "https://feeds.bbci.co.uk/news/politics/rss.xml"), ("reuters_rss", "https://feeds.reuters.com/reuters/politicsNews")]

    async def poll(self) -> List[FeedEvent]:
        all_events = []
        for source_name, url in self.SOURCES:
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    text = await resp.text()
                items = re.findall(r"<title><!\[CDATA\[(.+?)\]\]></title>|<title>(.+?)</title>", text, re.DOTALL)
                for item in items[:20]:
                    title = (item[0] or item[1]).strip()
                    if any(kw in title.lower() for kw in ["wins", "elected", "victory", "defeats", "beats", "named president", "sworn in", "inaugurated", "confirmed as"]):
                        all_events.append(FeedEvent(source=source_name, league="election", event_id=re.sub(r"\W+", "_", title[:40]), home_team=title[:50], away_team="", home_score=1, away_score=0, status="FINAL", period="called", clock="", winner=title[:50], raw={"title": title}))
            except Exception:
                pass
        return all_events


# ── Market Matcher ─────────────────────────────────────────────────────────────

class MarketMatcher:
    def __init__(self):
        self.poly_markets: List[dict] = []
        self._last_refresh = 0
        self.REFRESH_INTERVAL = 300  # 5 min

    async def refresh(self, session: aiohttp.ClientSession):
        if time.time() - self._last_refresh < self.REFRESH_INTERVAL:
            return
        try:
            # Fetch top 500 by volume (catches game markets ~$1M+ that fall below top-300 cutoff)
            async with session.get(f"{GAMMA_URL}/markets", params={"active": "true", "closed": "false", "order": "volumeNum", "ascending": "false", "limit": 500}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                top_markets = await resp.json(content_type=None)
            # Also fetch recently-created near-term markets (game markets expire in 1-3 days)
            async with session.get(f"{GAMMA_URL}/markets", params={"active": "true", "closed": "false", "order": "startDate", "ascending": "false", "limit": 200}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                recent_markets = await resp.json(content_type=None)
            seen_ids = {m.get("id") for m in top_markets}
            combined = top_markets + [m for m in recent_markets if m.get("id") not in seen_ids]
            self.poly_markets = combined
            self._last_refresh = time.time()
            logger.info(f"MarketMatcher refreshed: {len(self.poly_markets)} active markets ({len(top_markets)} top-vol + {len(combined)-len(top_markets)} recent)")
        except Exception as e:
            logger.warning(f"MarketMatcher refresh failed: {e}")

    def _normalize_team(self, name: str) -> str:
        """Normalize team name for matching."""
        return re.sub(r'\s+', ' ', name.strip().lower())

    def _hint_words(self, text: str) -> Set[str]:
        stop = {"will", "the", "win", "beat", "vs", "against", "game", "match", "championship", "series", "playoffs", "tonight", "today", "over", "their", "who", "which", "advance", "make", "reach", "nba", "nfl", "mlb", "nhl", "soccer", "ufc", "mma", "and", "for", "from", "this"}
        words = set(re.findall(r"[a-z]{3,}", text.lower()))
        return words - stop

    def find_markets(self, event: FeedEvent) -> List[Tuple[dict, float]]:
        home_norm = self._normalize_team(event.home_team)
        away_norm = self._normalize_team(event.away_team)
        results = []
        for m in self.poly_markets:
            q = m.get("question", "").lower()
            # Primary: check if BOTH team names appear in the question (strongest match)
            home_in = home_norm in q or any(w in q for w in home_norm.split() if len(w) >= 4)
            away_in = away_norm in q or any(w in q for w in away_norm.split() if len(w) >= 4)
            if home_in and away_in:
                results.append((m, 1.0))
            elif home_in or away_in:
                # Single team match — check it's the right context (vs/game/spread/o-u)
                game_context = any(kw in q for kw in ["vs", "spread", "o/u", "moneyline", "game", "match"])
                if game_context:
                    results.append((m, 0.7))
                else:
                    # Word-level fallback for partial matches
                    event_hints = self._hint_words(f"{event.home_team} {event.away_team}")
                    market_hints = self._hint_words(q)
                    overlap = len(event_hints & market_hints)
                    if overlap >= 2:
                        results.append((m, overlap / max(len(event_hints), 1) * 0.5))
        results.sort(key=lambda x: -x[1])
        return results[:5]

    def build_signal(self, event: FeedEvent, market: dict, confirmed_by: List[str]) -> Optional[MarketSignal]:
        if not event.winner:
            return None
        try:
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
        except Exception:
            return None
        if len(token_ids) < 2:
            return None
        q_lower = market.get("question", "").lower()
        # FILTER: skip season-long bets (finals, cup, champion, league winner)
        season_keywords = ["finals", "stanley cup", "champion", "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "world cup", "super bowl", "mvp", "cy young", "ballon d'or", "presidential", "election", "nominee", "senate", "governor", "russia"]
        if any(kw in q_lower for kw in season_keywords):
            return None
        # FILTER: market must expire within 48h (game-day markets only)
        end_date = market.get("endDateIso", "")
        if end_date:
            try:
                from datetime import datetime, timezone, timedelta
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if end_dt > datetime.now(timezone.utc) + timedelta(hours=48):
                    return None
            except Exception:
                pass
        last_price = float(market.get("lastTradePrice", 0.5) or 0.5)
        best_ask = float(market.get("bestAsk", last_price) or last_price)
        best_bid = float(market.get("bestBid", 0) or 0)
        # Use best_ask as entry (what we'd actually pay), not last_price
        entry_yes = best_ask if best_ask > 0 else last_price
        winner_words = self._hint_words(event.winner)
        if event.winner == "DRAW":
            return None
        winner_in_q = bool(winner_words & self._hint_words(q_lower))
        loser = event.home_team if event.winner == event.away_team else event.away_team
        loser_words = self._hint_words(loser)
        loser_in_q = bool(loser_words & self._hint_words(q_lower))
        if winner_in_q and entry_yes < PREPOSITION_PROB_THRESHOLD:
            token_id = token_ids[0]
            profit_pct = round((1.0 - entry_yes) / entry_yes * 100 * (1 - POLY_FEE), 2)
            return MarketSignal(poly_market=market, event=event, token_id=token_id, side="BUY", direction="YES", entry_price=entry_yes, expected_exit=1.0, profit_pct=profit_pct, confirmed_by=confirmed_by)
        if loser_in_q and not winner_in_q:
            entry_no = 1.0 - best_bid if best_bid > 0 else 1.0 - last_price
            if 0.05 < entry_no < 0.85:
                token_id = token_ids[1]
                profit_pct = round((1.0 - entry_no) / entry_no * 100 * (1 - POLY_FEE), 2)
                return MarketSignal(poly_market=market, event=event, token_id=token_id, side="BUY", direction="NO", entry_price=entry_no, expected_exit=1.0, profit_pct=profit_pct, confirmed_by=confirmed_by)
        return None


# ── Pre-Positioner ─────────────────────────────────────────────────────────────

class PrePositioner:
    def __init__(self, session: aiohttp.ClientSession, matcher: MarketMatcher):
        self.session = session
        self.matcher = matcher
        self.pre_loaded: Dict[str, dict] = {}

    async def maybe_preposition(self, events: List[FeedEvent]):
        for ev in events:
            if not ev.is_late_game:
                continue
            matches = self.matcher.find_markets(ev)
            for market, score in matches:
                key = market.get("id", "")
                if key in self.pre_loaded:
                    continue
                try:
                    token_ids = json.loads(market.get("clobTokenIds", "[]"))
                    if not token_ids:
                        continue
                    async with self.session.get(f"{CLOB_URL}/last-trade-price", params={"token_id": token_ids[0]}, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        price_data = await resp.json(content_type=None)
                    live_price = float(price_data.get("price", market.get("lastTradePrice", 0.5)) or 0.5)
                    self.pre_loaded[key] = {"market": market, "token_ids": token_ids, "live_price": live_price, "preloaded_at": int(time.time() * 1000), "event_key": ev.key}
                    logger.info(f"PRE-POSITIONED: {market.get('question','')[:60]} | price={live_price:.3f} | event={ev.home_team} vs {ev.away_team}")
                except Exception as e:
                    logger.debug(f"Pre-position failed: {e}")


# ── Order Engine ───────────────────────────────────────────────────────────────

class OrderEngine:
    def __init__(self, is_paper: bool = True, max_bet: float = DEFAULT_BET_SIZE, min_profit_pct: float = 5.0, min_sources: int = 2):
        self.is_paper = is_paper
        self.max_bet = max_bet
        self.min_profit_pct = min_profit_pct
        self.min_sources = min_sources
        self.clob_client = None
        self.executed: Set[str] = set()

    def init_clob(self):
        if self.is_paper:
            return
        try:
            from py_clob_client.client import ClobClient
            self.clob_client = ClobClient(host=CLOB_URL, key=os.getenv("POLY_API_WALLET_PK"), funder=os.getenv("POLY_API_WALLET_ADDRESS"), chain_id=137)
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}")

    async def verify_orderbook(self, signal: MarketSignal, session: aiohttp.ClientSession) -> tuple:
        """Check ACTUAL orderbook price — not stale LTP. Returns (real_entry, real_profit_pct, book_size)."""
        try:
            async with session.get(f"{CLOB_URL}/book", params={"token_id": signal.token_id}, timeout=aiohttp.ClientTimeout(total=2)) as r:
                if r.status != 200:
                    return signal.entry_price, signal.profit_pct, 0
                book = await r.json(content_type=None)
            if signal.direction == "YES":
                asks = sorted([(float(a.get("price", 1)), float(a.get("size", 0))) for a in book.get("asks", []) if float(a.get("size", 0)) > 0])
                if not asks:
                    return 1.0, -100, 0
                real_entry = asks[0][0]
                real_size = asks[0][1]
            else:
                bids = sorted([(float(b.get("price", 0)), float(b.get("size", 0))) for b in book.get("bids", []) if float(b.get("size", 0)) > 0], reverse=True)
                if not bids:
                    return 1.0, -100, 0
                real_entry = 1.0 - bids[0][0]
                real_size = bids[0][1]
            real_profit = round((1.0 - real_entry) / real_entry * 100 * (1 - POLY_FEE), 2) if real_entry > 0 else -100
            return real_entry, real_profit, real_size
        except Exception:
            return signal.entry_price, signal.profit_pct, 0

    async def maybe_execute(self, signal: MarketSignal, session: aiohttp.ClientSession = None):
        if signal.profit_pct < self.min_profit_pct:
            logger.debug(f"Skipping low-profit signal: {signal.profit_pct:.1f}% < {self.min_profit_pct}% min")
            return
        if len(signal.confirmed_by) < self.min_sources:
            logger.info(f"WATCH ({len(signal.confirmed_by)}/{self.min_sources} src): {signal.event.home_team} vs {signal.event.away_team} winner={signal.event.winner}")
            return
        exec_key = f"{signal.poly_market.get('id')}:{signal.direction}"
        if exec_key in self.executed:
            return
        # VERIFY: check actual orderbook price before executing
        if session:
            real_entry, real_profit, book_size = await self.verify_orderbook(signal, session)
            logger.info(f"BOOK-VERIFY: LTP_entry={signal.entry_price:.4f} → real_entry={real_entry:.4f} real_profit={real_profit:.1f}% size={book_size:.0f}")
            if real_profit < self.min_profit_pct:
                logger.info(f"SKIP: orderbook already updated (real_profit={real_profit:.1f}% < {self.min_profit_pct}%)")
                return
            signal.entry_price = real_entry
            signal.profit_pct = real_profit
        self.executed.add(exec_key)
        delay_ms = int(time.time() * 1000) - signal.event.detected_ms
        logger.info(f"{'PAPER' if self.is_paper else 'LIVE'} EXECUTE: {signal.direction} on '{signal.poly_market.get('question','')[:60]}' | price={signal.entry_price:.4f} | profit={signal.profit_pct:.1f}% | lag_from_event={delay_ms}ms | sources={signal.confirmed_by}")
        record = {"ts": datetime.now(timezone.utc).isoformat(), "mode": "paper" if self.is_paper else "live", "question": signal.poly_market.get("question", ""), "market_id": signal.poly_market.get("id"), "direction": signal.direction, "token_id": signal.token_id, "entry_price": signal.entry_price, "bet_usdc": self.max_bet, "expected_profit_pct": signal.profit_pct, "confirmed_sources": signal.confirmed_by, "event": f"{signal.event.home_team} {signal.event.home_score}-{signal.event.away_score} {signal.event.away_team}", "winner": signal.event.winner, "detection_delay_ms": delay_ms}
        with open(EXEC_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
        if not self.is_paper and self.clob_client:
            await self._submit_real_order(signal)

    async def _submit_real_order(self, signal: MarketSignal):
        from py_clob_client import OrderArgs
        t_submit = int(time.time() * 1000)
        try:
            size = int(self.max_bet / signal.entry_price)
            resp = self.clob_client.create_and_post_order(OrderArgs(price=round(signal.entry_price, 2), size=size, side="BUY", token_id=signal.token_id))
            t_fill = int(time.time() * 1000)
            logger.info(f"LIVE ORDER SENT: {resp} | submit→fill={t_fill-t_submit}ms")
            with open(TIMING_LOG, "a") as f:
                f.write(json.dumps({"event_ms": signal.event.detected_ms, "signal_ms": signal.signal_ms, "submit_ms": t_submit, "fill_ms": t_fill, "total_lag_ms": t_fill - signal.event.detected_ms}) + "\n")
        except Exception as e:
            logger.error(f"LIVE ORDER FAILED: {e}")


# ── Main Agent ─────────────────────────────────────────────────────────────────

class OracleAgent:
    def __init__(self, enabled_feeds: List[str], is_paper: bool, max_bet: float, min_sources: int, min_profit_pct: float):
        self.enabled_feeds = set(enabled_feeds)
        self.is_paper = is_paper
        self.registry = OutcomeRegistry()
        self.matcher = MarketMatcher()
        self.order_engine = OrderEngine(is_paper, max_bet, min_profit_pct, min_sources)
        self.session: Optional[aiohttp.ClientSession] = None
        self.pre_positioner: Optional[PrePositioner] = None
        self._crypto_price_alerts: List[dict] = []

    async def _on_final(self, events: List[FeedEvent]):
        first = events[0]
        confirmed_by = [e.source for e in events]
        t0 = int(time.time() * 1000)
        matches = self.matcher.find_markets(first)
        if not matches:
            logger.debug(f"No Polymarket match for: {first.home_team} vs {first.away_team}")
            return
        await self.matcher.refresh(self.session)
        for market, match_score in matches:
            signal = self.matcher.build_signal(first, market, confirmed_by)
            if signal:
                logger.info(f"SIGNAL ({match_score:.0%} match): {signal.direction} | market='{market.get('question','')[:55]}' | profit={signal.profit_pct:.1f}%")
                await self.order_engine.maybe_execute(signal, self.session)
        logger.info(f"Match→order pipeline: {int(time.time()*1000)-t0}ms")

    async def _on_late_game(self, events: List[FeedEvent]):
        if self.pre_positioner:
            await self.pre_positioner.maybe_preposition(events)

    async def _on_crypto_move(self, symbol: str, price: float, pct_chg: float, ts_ms: int):
        self._crypto_price_alerts.append({"symbol": symbol, "price": price, "pct_chg": pct_chg, "ts_ms": ts_ms})
        for market in self.matcher.poly_markets:
            q = market.get("question", "")
            q_lower = q.lower()
            crypto_kw = {"btc": "BTCUSDC", "bitcoin": "BTCUSDC", "eth": "ETHUSDC", "ethereum": "ETHUSDC", "sol": "SOLUSDC"}
            matched_pair = next((v for k, v in crypto_kw.items() if k in q_lower and v == symbol), None)
            if not matched_pair:
                continue
            price_match = re.search(r"\$([0-9,]+(?:\.[0-9]+)?)", q)
            if not price_match:
                continue
            target = float(price_match.group(1).replace(",", ""))
            last_price = float(market.get("lastTradePrice", 0.5) or 0.5)
            outcome_yes = price >= target if ("above" in q_lower or "reach" in q_lower or "hit" in q_lower) else price <= target
            entry = last_price if outcome_yes else (1 - last_price)
            profit_pct = round((1.0 - entry * 1.005) / (entry * 1.005) * 100 * (1 - POLY_FEE), 2)
            if entry < 0.90 and profit_pct > self.order_engine.min_profit_pct:
                try:
                    token_ids = json.loads(market.get("clobTokenIds", "[]"))
                except Exception:
                    continue
                if not token_ids:
                    continue
                signal = MarketSignal(poly_market=market, event=FeedEvent(source="binance", league="crypto", event_id=symbol, home_team=symbol, away_team=f"${target:.0f}", home_score=int(price), away_score=int(target), status="FINAL", period="spot", clock="", winner=symbol if outcome_yes else f"NOT_{symbol}"), token_id=token_ids[0] if outcome_yes else token_ids[1], side="BUY", direction="YES" if outcome_yes else "NO", entry_price=entry * 1.005, expected_exit=1.0, profit_pct=profit_pct, confirmed_by=["binance_ws"])
                signal.signal_ms = ts_ms
                logger.info(f"CRYPTO SIGNAL: {symbol}={price:.2f} vs target=${target:,.0f} → BUY {'YES' if outcome_yes else 'NO'} | profit={profit_pct:.1f}%")
                await self.order_engine.maybe_execute(signal, self.session)

    def _build_feeds(self) -> List[BaseFeed]:
        feeds = []
        if "nhl" in self.enabled_feeds:
            feeds.append(NHLFeed(self.session, self.registry))
        if "nba" in self.enabled_feeds:
            feeds.append(NBAFeed(self.session, self.registry))
        if "espn" in self.enabled_feeds:
            feeds.append(ESPNFeed(self.session, self.registry))
        if "sofascore" in self.enabled_feeds:
            feeds.append(SofaScoreFeed(self.session, self.registry))
        if "election" in self.enabled_feeds:
            feeds.append(ElectionFeed(self.session, self.registry))
        if "binance" in self.enabled_feeds:
            feeds.append(BinanceFeed(self.session, self.registry, price_callback=self._on_crypto_move))
        return feeds

    async def run(self):
        connector = aiohttp.TCPConnector(limit=50, keepalive_timeout=30, enable_cleanup_closed=True)
        async with aiohttp.ClientSession(connector=connector, headers={"Connection": "keep-alive"}) as session:
            self.session = session
            self.pre_positioner = PrePositioner(session, self.matcher)
            self.registry.on_final = self._on_final
            self.registry.on_late_game = self._on_late_game
            self.order_engine.init_clob()
            await self.matcher.refresh(session)
            feeds = self._build_feeds()
            logger.info(f"OracleAgent starting | feeds={[f.NAME for f in feeds]} | paper={self.is_paper} | min_sources={self.order_engine.min_sources} | max_bet=${self.order_engine.max_bet}")
            tasks = [asyncio.create_task(f.run()) for f in feeds]
            tasks.append(asyncio.create_task(self._refresh_loop()))
            tasks.append(asyncio.create_task(self._status_loop()))
            await asyncio.gather(*tasks)

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(300)
            await self.matcher.refresh(self.session)

    async def _status_loop(self):
        while True:
            await asyncio.sleep(60)
            n_finals = sum(len(v) for v in self.registry._finals.values())
            n_late = len(self.registry._late_game)
            logger.info(f"Status: finals_tracked={n_finals} late_games={n_late} pre_positioned={len(self.pre_positioner.pre_loaded if self.pre_positioner else {})} orders_fired={len(self.order_engine.executed)}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket real-time oracle lag agent")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default: paper mode)")
    parser.add_argument("--max-bet", type=float, default=DEFAULT_BET_SIZE, help=f"Max USDC per trade (default {DEFAULT_BET_SIZE})")
    parser.add_argument("--min-sources", type=int, default=2, help="Minimum confirming feeds before executing (default 2)")
    parser.add_argument("--min-profit", type=float, default=5.0, help="Minimum profit %% to trade (default 5.0)")
    parser.add_argument("--feed", type=str, default="nhl,nba,espn,sofascore,binance,election", help="Comma-separated feed list")
    args = parser.parse_args()
    feeds = [f.strip() for f in args.feed.split(",")]
    agent = OracleAgent(enabled_feeds=feeds, is_paper=not args.live, max_bet=args.max_bet, min_sources=args.min_sources, min_profit_pct=args.min_profit)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Agent stopped.")

if __name__ == "__main__":
    main()
