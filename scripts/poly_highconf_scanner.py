#!/usr/bin/env python3
"""poly_highconf_scanner.py — Polymarket last-minutes scalper.

STRATEGY: Only bet on markets closing within MAX_HOURS_TO_CLOSE (default 4h).
At 90%+ with <4h left, the outcome is virtually certain — but capital is only
tied up for hours, not weeks. This eliminates the capital-lockup problem.

BACKTEST RESULTS (1000 resolved markets, CLOB price-history verified):
  Category       | N    | WinRate | AvgGross | EV@92%
  soccer_match   | 120  | 99.2%   | +0.34%   | +7.79%  ← 1 false positive in 120
  esports        |  14  | 100.0%  | +1.67%   | +8.70%
  nba            |  30  | 100.0%  | +5.99%   | +8.70%  ← best avg gross
  crypto         |  77  | 100.0%  | +0.91%   | +8.70%
  over_under     |  23  | 100.0%  | +0.69%   | +8.70%
  other          |  26  |  96.2%  | -1.94%   | +4.52%  ← handle with care

POSITION SIZING: flat $5/trade — keep capital moving, maximize bet count

AVOID categories: nhl (too few samples), nfl (seasonal), spread (cover risk)
SAFE categories: esports, nba, crypto, over_under, soccer_match, entertainment, other@97%+
"""

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import sys
import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_highconf")

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
POLY_FEE = 0.02
_DEFAULT_DATA_DIR = Path("./data/poly/highconf")

DATA_DIR = _DEFAULT_DATA_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
OPP_LOG = DATA_DIR / "opportunities.jsonl"
PAPER_FILE = DATA_DIR / "paper_positions.json"
LIVE_FILE = DATA_DIR / "live_positions.json"
EXEC_LOG = DATA_DIR / "executions.jsonl"
CLOB_MIN_USDC = 5.0

# Maximum hours to close — reject anything further out (capital lockup risk)
# Reduced from 4h to 2h: less risk, faster capital turnover
MAX_HOURS_TO_CLOSE = 2.0
# Also accept past-end markets up to this many hours after end (awaiting resolution)
MAX_HOURS_PAST_END = 6.0
# If price drops this much below entry, sell back to cut losses
STOP_LOSS_DROP = 0.03
# Force-close zombie positions older than this many hours
ZOMBIE_HOURS = 48.0

THRESHOLDS = [0.92, 0.95, 0.97, 0.98]

BLACKLIST_PATTERNS = [
    "up or down", "o/u ", "over/under", "spread:", "ufc ", "fight night",
    "flyweight", "welterweight", "heavyweight", "bantamweight",
    "featherweight", "middleweight", "lightweight", "mma ", "boxing ",
    "knockout", "submission", "round 1", "round 2", "round 3",
    "bellator", "pfl ", "one championship",
]

CATS = {
    "esports": ["counter-strike", "valorant", "dota 2", "league of legends", "map 1 winner", "map 2 winner", "map 3 winner", "bo3", "bo5", "esl pro", "game 1 winner", "game 2 winner", "game 3 winner"],
    "soccer_match": ["win on 2025", "win on 2026", "both teams to score", "btts", "total goals", "o/u 1.5", "o/u 2.5", "o/u 3.5", "over 2.5", "under 2.5", "clean sheet", "first goal"],
    "soccer_season": ["premier league", "la liga", "bundesliga", "serie a", "ligue 1", "champions league", "europa league"],
    "nba": ["nba", "lakers", "celtics", "warriors", "nets", "knicks", "heat", "bucks", "nuggets", "76ers", "suns", "grizzlies", "thunder", "hornets", "spurs", "hawks", "pistons", "rockets", "maverick", "clippers", "timberwolves", "cavaliers", "raptors", "bulls"],
    "nhl": ["nhl", "stanley cup", "bruins", "maple leafs", "rangers", "penguins", "blackhawks", "senators", "canucks", "oilers", "avalanche", "lightning", "capitals", "golden knights", "jets", "wild", "flames", "ducks", "sharks", "sabres"],
    "economics": ["fed rate", "federal reserve", "interest rate", " bps ", "fomc", "ecb rate", "bank of england", "bank of japan", "tariff"],
    "crypto": ["bitcoin", "btc ", "ethereum", "eth ", "solana", "sol ", "bnb", "xrp ", "ripple", "doge", "up or down"],
    "entertainment": ["oscar", "academy award", "grammy", "emmy", "bafta", "98th", "rotten tomatoes"],
    "weather": ["temperature", "rainfall", "hurricane", "earthquake", "celsius", "fahrenheit"],
    "politics_us": ["president 2028", "senate", "governor", "republican nominee", "democrat nominee", "primary 2026", "primary 2028"],
    "politics_intl": ["prime minister", "parliament", "election", "referendum", "chancellor"],
    "college": ["ncaa", "march madness", "flyers vs.", "billikens", "gators", "commodores", "tar heels", "blue devils", "jayhawks"],
    "over_under": ["o/u", "over/under", "total points", "total kills", "total runs"],
    "tech": [" ipo", "merger", "acquisition", "openai", "anthropic", "spacex", "gpt-"],
}

# Only categories with proven positive EV in paper trading
# Removed: crypto (-$302 on 50 trades), nba (-$71), over_under (-$97), college (no data)
SAFE_CATS_92 = {"esports", "soccer_match", "entertainment"}
SAFE_CATS_97_ONLY = {"nba", "college"}
# These categories are EXCLUDED entirely (negative EV even at 97%+):
# crypto, over_under, other, politics_us, politics_intl, economics, tech


def classify(q: str) -> str:
    ql = q.lower()
    for cat, kws in CATS.items():
        if any(k in ql for k in kws):
            return cat
    return "other"

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("+00", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def tier_label(price: float) -> str:
    if price >= 0.98:
        return "TIER_98"
    if price >= 0.97:
        return "TIER_97"
    if price >= 0.95:
        return "TIER_95"
    return "TIER_92"

def net_return(entry: float) -> float:
    return round((1.0 - entry) / entry * 100 * (1 - POLY_FEE), 2)

def is_tradeable(cat: str, entry: float) -> bool:
    if cat in SAFE_CATS_92:
        return True
    if cat in SAFE_CATS_97_ONLY:
        return entry >= 0.97
    return False

def _tiered_bet_size(_entry: float) -> float:
    return 5.0

def _init_data_dir(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


async def fetch_all_active(session: aiohttp.ClientSession) -> List[dict]:
    all_m = []
    for offset in range(0, 4001, 500):
        try:
            async with session.get(f"{GAMMA_URL}/markets", params={"active": "true", "closed": "false", "order": "volumeNum", "ascending": "false", "limit": 500, "offset": offset}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                batch = await r.json(content_type=None)
            all_m.extend(batch)
            if len(batch) < 500:
                break
        except Exception as e:
            logger.warning(f"fetch page offset={offset}: {e}")
            break
    return all_m


def analyze_market(m: dict, now: datetime, max_hours: float = MAX_HOURS_TO_CLOSE) -> Optional[dict]:
    """Return opportunity dict if market passes all filters, else None.

    Core filter: end date must be known AND close within max_hours (or recently past).
    This ensures capital is never locked up for long durations.
    """
    end_dt = parse_iso(m.get("endDateIso", ""))
    if end_dt is None:
        return None  # no known close time = unknown lockup duration, skip
    hrs_to_close = (end_dt - now).total_seconds() / 3600
    past_end = hrs_to_close < 0
    hrs_past = abs(hrs_to_close) if past_end else 0.0
    # Reject: closes more than max_hours away (capital would be locked up)
    if not past_end and hrs_to_close > max_hours:
        return None
    # Reject: past end but too long ago (should have resolved by now — bad data)
    if past_end and hrs_past > MAX_HOURS_PAST_END:
        return None
    lp = float(m.get("lastTradePrice", 0) or 0)
    if lp < 0.92:
        return None
    ask = float(m.get("bestAsk", lp) or lp)
    entry = ask if ask and ask > lp * 0.95 else lp + 0.001
    if entry >= 1.0:
        return None
    q = m.get("question", "")
    ql = q.lower()
    if any(bp in ql for bp in BLACKLIST_PATTERNS):
        return None
    cat = classify(q)
    if not is_tradeable(cat, entry):
        return None
    h1chg = float(m.get("oneHourPriceChange", 0) or 0)
    vol24 = float(m.get("volume24hr", 0) or 0)
    # Reject: past-end market with active declining price (informed sellers know it resolved NO)
    if past_end and h1chg < -0.02 and vol24 > 5000:
        return None
    tok_raw = m.get("clobTokenIds", "[]")
    try:
        toks = json.loads(tok_raw) if isinstance(tok_raw, str) else tok_raw
    except Exception:
        toks = []
    # Urgency label for display
    if past_end:
        urgency = f"PAST_END +{hrs_past:.1f}h"
    elif hrs_to_close < 1.0:
        urgency = f"<{hrs_to_close*60:.0f}min"
    else:
        urgency = f"<{hrs_to_close:.1f}h"
    return {
        "market_id": m.get("id"),
        "question": q[:90],
        "cat": cat,
        "last_price": lp,
        "best_ask": ask,
        "entry": round(entry, 4),
        "net": net_return(entry),
        "tier": tier_label(lp),
        "past_end": past_end,
        "hrs_past": round(hrs_past, 2),
        "hrs_to_close": round(hrs_to_close, 2),
        "urgency": urgency,
        "end_date": m.get("endDateIso", "")[:16] if m.get("endDateIso") else "N/A",
        "vol": float(m.get("volumeNum", 0) or 0),
        "vol24": vol24,
        "liq": float(m.get("liquidityNum", 0) or 0),
        "h1chg": h1chg,
        "h24chg": float(m.get("oneDayPriceChange", 0) or 0),
        "suggested_bet": _tiered_bet_size(entry),
        "yes_token": toks[0] if toks else None,
        "no_token": toks[1] if len(toks) > 1 else None,
        "condition_id": m.get("conditionId", ""),
        "neg_risk": m.get("negRisk", False),
        "scanned_at": now.isoformat(),
    }


class PositionTracker:
    def __init__(self, positions_file: Path):
        self.positions: Dict[str, dict] = {}
        self.closed: List[dict] = []
        self._file = positions_file
        self._load()

    def _load(self):
        if self._file.exists():
            try:
                with open(self._file) as f:
                    d = json.load(f)
                self.positions = d.get("positions", {})
                self.closed = d.get("closed", [])
            except Exception:
                pass

    def _save(self):
        with open(self._file, "w") as f:
            json.dump({"positions": self.positions, "closed": self.closed}, f, indent=2)

    def enter(self, opp: dict, bet: float) -> bool:
        key = opp["market_id"]
        if key in self.positions:
            return False
        self.positions[key] = {"question": opp["question"], "cat": opp["cat"], "tier": opp["tier"], "entry_price": opp["entry"], "shares": bet / opp["entry"], "bet_usdc": bet, "net_pct": opp["net"], "past_end": opp["past_end"], "hrs_past": opp["hrs_past"], "hrs_to_close": opp["hrs_to_close"], "urgency": opp["urgency"], "entered_at": now_utc().isoformat(), "yes_token": opp.get("yes_token"), "condition_id": opp.get("condition_id", ""), "neg_risk": opp.get("neg_risk", False), "h1chg": opp["h1chg"]}
        self._save()
        return True

    def close(self, market_id: str, exit_price: float, reason: str) -> Optional[dict]:
        if market_id not in self.positions:
            return None
        pos = self.positions.pop(market_id)
        pnl = (exit_price - pos["entry_price"]) * pos["shares"]
        pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
        pos.update({"exit_price": exit_price, "pnl_usdc": round(pnl, 4), "pnl_pct": round(pnl_pct, 2), "reason": reason, "closed_at": now_utc().isoformat()})
        self.closed.append(pos)
        self._save()
        return pos

    def exposure(self) -> float:
        return sum(p["bet_usdc"] for p in self.positions.values())

    def stats(self) -> dict:
        out = {}
        total_pnl = sum(p["pnl_usdc"] for p in self.closed)
        total_bet = sum(p["bet_usdc"] for p in self.closed)
        wins = [p for p in self.closed if p["pnl_usdc"] > 0]
        losses = [p for p in self.closed if p["pnl_usdc"] <= 0]
        out["total"] = len(self.closed)
        out["wins"] = len(wins)
        out["losses"] = len(losses)
        out["win_rate"] = round(len(wins) / len(self.closed) * 100, 1) if self.closed else 0
        out["total_pnl"] = round(total_pnl, 2)
        out["total_bet"] = round(total_bet, 2)
        out["roi_pct"] = round(total_pnl / total_bet * 100, 2) if total_bet else 0
        out["open"] = len(self.positions)
        by_cat = {}
        for p in self.closed:
            c = p.get("cat", "?")
            if c not in by_cat:
                by_cat[c] = {"n": 0, "wins": 0, "pnl": 0}
            by_cat[c]["n"] += 1
            by_cat[c]["pnl"] += p["pnl_usdc"]
            if p["pnl_usdc"] > 0:
                by_cat[c]["wins"] += 1
        out["by_cat"] = by_cat
        return out


class HighConfScanner:
    def __init__(self, is_paper: bool, min_tier: float, scan_interval: float, max_exposure: float, max_hours: float, min_net: float = 0.5, bet_size: float = 5.0):
        self.is_paper = is_paper
        self.min_tier = min_tier
        self.scan_interval = scan_interval
        self.max_exposure = max_exposure
        self.max_hours = max_hours
        self.min_net = min_net
        self.bet_size = bet_size
        self.tracker = PositionTracker(positions_file=PAPER_FILE if is_paper else LIVE_FILE)
        self.session: Optional[aiohttp.ClientSession] = None
        self._scan_count = 0
        self._clob_client = None
        self._cached_balance: float = 0.0

    def _sync_clob_balance(self):
        if not self._clob_client:
            return
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            self._clob_client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            logger.info("CLOB balance synced")
        except Exception as e:
            logger.warning(f"CLOB balance sync failed: {e}")

    def _init_clob(self):
        if self.is_paper:
            return
        try:
            from py_clob_client.client import ClobClient
            from eth_account import Account
            pk = os.getenv("POLY_API_WALLET_PK")
            addr = Account.from_key(pk).address
            base_client = ClobClient(host=CLOB_URL, key=pk, funder=addr, chain_id=137)
            creds = base_client.derive_api_key()
            self._clob_client = ClobClient(host=CLOB_URL, key=pk, funder=addr, chain_id=137, creds=creds)
            self._sync_clob_balance()
            bal = self._check_usdc_balance()
            logger.info(f"CLOB initialized | wallet={addr} | bal=${bal:,.2f}")
            if bal < 5:
                logger.warning(f"LOW USDC BALANCE: ${bal:.2f}")
        except Exception as e:
            logger.error(f"CLOB init failed: {e}")

    def _check_usdc_balance(self) -> float:
        if not self._clob_client:
            return 0.0
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            bal = self._clob_client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            return int(bal.get("balance", 0)) / 1e6
        except Exception as e:
            logger.warning(f"Balance check failed: {e}")
            return 0.0

    async def _submit_sell_order(self, market_id: str, pos: dict, bid_price: float) -> bool:
        if not self._clob_client:
            return False
        yes_token = pos.get("yes_token")
        if not yes_token:
            return False
        shares = int(pos.get("shares", 0))
        if shares < 1:
            return False
        try:
            from py_clob_client import OrderArgs
            price = max(round(bid_price, 3), 0.001)
            resp = self._clob_client.create_and_post_order(OrderArgs(price=price, size=shares, side="SELL", token_id=yes_token))
            self._cached_balance += bid_price * shares
            with open(EXEC_LOG, "a") as f:
                f.write(json.dumps({"ts": now_utc().isoformat(), "action": "SELL_EXIT", "market_id": market_id, "question": pos.get("question", ""), "cat": pos.get("cat", ""), "entry": pos.get("entry_price"), "exit_bid": bid_price, "shares": shares, "usdc_out": round(bid_price * shares, 4), "resp": str(resp)}) + "\n")
            logger.info(f"SELL EXIT placed: {resp}")
            return True
        except Exception as e:
            logger.error(f"Sell order failed {market_id}: {e}")
            return False

    async def _submit_live_order(self, opp: dict, size_usdc: float, _retry: bool = False) -> bool:
        if not self._clob_client:
            return False
        try:
            from py_clob_client import OrderArgs
            price = min(round(opp["entry"], 3), 0.999)
            shares = max(1, int(size_usdc / price))
            resp = self._clob_client.create_and_post_order(OrderArgs(price=price, size=shares, side="BUY", token_id=opp["yes_token"]))
            self._cached_balance -= size_usdc
            with open(EXEC_LOG, "a") as f:
                f.write(json.dumps({"ts": now_utc().isoformat(), "market_id": opp["market_id"], "question": opp["question"], "cat": opp["cat"], "tier": opp["tier"], "urgency": opp["urgency"], "entry": opp["entry"], "shares": shares, "usdc": size_usdc, "net_pct": opp["net"], "resp": str(resp)}) + "\n")
            logger.info(f"LIVE ORDER placed: {resp}")
            return True
        except Exception as e:
            if not _retry and "not enough balance" in str(e).lower():
                logger.warning(f"Balance error — syncing CLOB and retrying: {e}")
                self._sync_clob_balance()
                return await self._submit_live_order(opp, size_usdc, _retry=True)
            logger.error(f"Live order failed: {e}")
            return False

    async def _scan_once(self) -> List[dict]:
        now = now_utc()
        markets = await fetch_all_active(self.session)
        self._scan_count += 1
        opps = []
        for m in markets:
            opp = analyze_market(m, now, max_hours=self.max_hours)
            if opp and opp["entry"] >= self.min_tier:
                opps.append(opp)
        # Sort: past_end first, then by urgency (soonest closing first), then by price
        opps.sort(key=lambda o: (not o["past_end"], o["hrs_to_close"], -o["last_price"]))
        return opps

    async def _trade(self, opp: dict):
        exposure = self.tracker.exposure()
        if exposure >= self.max_exposure:
            return
        if opp["market_id"] in self.tracker.positions:
            return
        if opp["net"] < self.min_net:
            return
        bet = self.bet_size
        bet = min(bet, self.max_exposure - exposure)
        if bet < CLOB_MIN_USDC:
            return
        if not self.is_paper and self._cached_balance < bet:
            return
        if self.is_paper:
            if self.tracker.enter(opp, bet):
                logger.info(f"  PAPER {opp['tier']} [{opp['urgency']}] {opp['cat']} | entry={opp['entry']:.4f} net={opp['net']:.1f}% bet=${bet:.0f} | {opp['question'][:60]}")
        else:
            if not opp.get("yes_token"):
                return
            ok = await self._submit_live_order(opp, bet)
            if ok and self.tracker.enter(opp, bet):
                logger.info(f"  LIVE  {opp['tier']} [{opp['urgency']}] {opp['cat']} | entry={opp['entry']:.4f} net={opp['net']:.1f}% bet=${bet:.0f} | {opp['question'][:60]}")

    async def _cleanup_zombies(self):
        """Force-close positions stuck open longer than ZOMBIE_HOURS."""
        if not self.tracker.positions:
            return
        now = now_utc()
        mode = "PAPER" if self.is_paper else "LIVE"
        for market_id in list(self.tracker.positions.keys()):
            pos = self.tracker.positions.get(market_id)
            if not pos:
                continue
            entered = parse_iso(pos.get("entered_at", ""))
            if not entered:
                continue
            age_hours = (now - entered).total_seconds() / 3600
            if age_hours < ZOMBIE_HOURS:
                continue
            entry = pos.get("entry_price", 1.0)
            bid = 0.0
            try:
                async with self.session.get(f"{GAMMA_URL}/markets/{market_id}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        m = await r.json(content_type=None)
                        bid = float(m.get("bestBid", 0) or 0)
                        if m.get("closed", False) or not m.get("active", True):
                            out_raw = m.get("outcomePrices", "[]")
                            try:
                                out = json.loads(out_raw) if isinstance(out_raw, str) else out_raw
                                bid = float(out[0]) if out else bid
                            except Exception:
                                pass
            except Exception:
                pass
            if not self.is_paper and bid > 0:
                await self._submit_sell_order(market_id, pos, bid)
            exit_price = bid if bid > 0 else entry * 0.5
            result = self.tracker.close(market_id, exit_price, f"ZOMBIE_{age_hours:.0f}h")
            if result:
                logger.warning(f"  ZOMBIE {mode} {result['cat']} | age={age_hours:.0f}h entry={entry:.4f} exit={exit_price:.4f} pnl=${result['pnl_usdc']:+.2f} | {result['question'][:55]}")

    async def _stop_loss_exit(self):
        """Sell back positions where current price dropped significantly below entry."""
        if not self.tracker.positions:
            return
        mode = "PAPER" if self.is_paper else "LIVE"
        for market_id in list(self.tracker.positions.keys()):
            pos = self.tracker.positions.get(market_id)
            if not pos:
                continue
            entry = pos.get("entry_price", 1.0)
            try:
                async with self.session.get(f"{GAMMA_URL}/markets/{market_id}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        continue
                    m = await r.json(content_type=None)
                if m.get("closed", False) or not m.get("active", True):
                    continue
                lp = float(m.get("lastTradePrice", 0) or 0)
                bid = float(m.get("bestBid", 0) or 0)
                exit_price = bid if bid > 0 else lp
                if exit_price <= 0 or exit_price >= entry - STOP_LOSS_DROP:
                    continue
                if not self.is_paper and bid > 0:
                    ok = await self._submit_sell_order(market_id, pos, bid)
                    if not ok:
                        continue
                result = self.tracker.close(market_id, exit_price, "STOP_LOSS")
                if result:
                    logger.warning(f"  STOP {mode} {result['cat']} | entry={entry:.4f} exit={exit_price:.4f} drop={entry - exit_price:.4f} pnl=${result['pnl_usdc']:+.2f} | {result['question'][:55]}")
            except Exception as e:
                logger.debug(f"stop_loss {market_id}: {e}")

    async def _exit_stale_positions(self):
        """For any open position where the market closes more than max_hours away,
        exit immediately if bestBid >= entry_price (no-loss exit, frees capital)."""
        if not self.tracker.positions:
            return
        now = now_utc()
        for market_id in list(self.tracker.positions.keys()):
            pos = self.tracker.positions.get(market_id)
            if not pos:
                continue
            try:
                async with self.session.get(f"{GAMMA_URL}/markets/{market_id}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        continue
                    m = await r.json(content_type=None)
                if m.get("closed", False) or not m.get("active", True):
                    continue  # let _check_resolutions handle it
                end_dt = parse_iso(m.get("endDateIso", ""))
                if end_dt is None:
                    continue
                hrs_to_close = (end_dt - now).total_seconds() / 3600
                if hrs_to_close <= self.max_hours:
                    continue  # closing soon enough, keep it
                bid = float(m.get("bestBid", 0) or 0)
                entry = pos.get("entry_price", 1.0)
                if bid < entry:
                    logger.debug(f"  stale lockup {market_id[:8]} but bid={bid:.4f} < entry={entry:.4f}, holding")
                    continue
                mode = "PAPER" if self.is_paper else "LIVE"
                if self.is_paper:
                    result = self.tracker.close(market_id, bid, "EXIT_STALE_NO_LOSS")
                    if result:
                        logger.info(f"  EXIT_STALE {mode} {result['cat']} | entry={entry:.4f} bid={bid:.4f} pnl=${result['pnl_usdc']:+.2f} hrs_left={hrs_to_close:.1f}h | {result['question'][:55]}")
                else:
                    ok = await self._submit_sell_order(market_id, pos, bid)
                    if ok:
                        result = self.tracker.close(market_id, bid, "EXIT_STALE_NO_LOSS")
                        if result:
                            logger.info(f"  EXIT_STALE {mode} {result['cat']} | entry={entry:.4f} bid={bid:.4f} pnl=${result['pnl_usdc']:+.2f} hrs_left={hrs_to_close:.1f}h | {result['question'][:55]}")
            except Exception as e:
                logger.debug(f"exit_stale {market_id}: {e}")

    async def _check_resolutions(self):
        if not self.tracker.positions:
            return
        for market_id in list(self.tracker.positions.keys()):
            try:
                async with self.session.get(f"{GAMMA_URL}/markets/{market_id}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        continue
                    m = await r.json(content_type=None)
                lp = float(m.get("lastTradePrice", 0) or 0)
                active = m.get("active", True)
                closed = m.get("closed", False)
                out_raw = m.get("outcomePrices", "[]")
                try:
                    out = json.loads(out_raw) if isinstance(out_raw, str) else out_raw
                    final_yes = float(out[0]) if out else lp
                except Exception:
                    final_yes = lp
                if closed or not active:
                    result = self.tracker.close(market_id, final_yes, "RESOLVED")
                    if result:
                        emoji = "✅" if result["pnl_usdc"] > 0 else "❌"
                        mode = "PAPER" if self.is_paper else "LIVE"
                        logger.info(f"  {emoji} {mode} {result['cat']} {result['tier']} | pnl=${result['pnl_usdc']:+.2f} ({result['pnl_pct']:+.1f}%) exit={final_yes:.3f} | {result['question'][:55]}")
                elif lp < 0.30 and float(m.get("volume24hr", 0) or 0) > 5000:
                    result = self.tracker.close(market_id, lp, "STOP_LOSS_COLLAPSE")
                    if result:
                        mode = "PAPER" if self.is_paper else "LIVE"
                        logger.warning(f"  ❌ {mode} STOP {result['cat']} | pnl=${result['pnl_usdc']:+.2f} price={lp:.3f} | {result['question'][:55]}")
            except Exception as e:
                logger.debug(f"resolve check {market_id}: {e}")

    def _print_stats(self):
        s = self.tracker.stats()
        if s["total"] == 0 and s["open"] == 0:
            return
        mode = "PAPER" if self.is_paper else "LIVE"
        logger.info(f"--- {mode}: {s['wins']}W/{s['losses']}L ({s['win_rate']:.0f}% WR) | pnl=${s['total_pnl']:+.2f} ROI={s['roi_pct']:+.2f}% | open={s['open']} exposure=${self.tracker.exposure():.0f}")
        for cat, cs in s["by_cat"].items():
            wr = cs["wins"] / cs["n"] * 100
            logger.info(f"    {cat}: {cs['n']} trades {wr:.0f}% WR pnl=${cs['pnl']:+.2f}")

    async def run(self):
        mode = "PAPER" if self.is_paper else "LIVE"
        logger.info(f"HighConf Scanner | mode={mode} | min_tier={self.min_tier:.0%} | min_net={self.min_net}% | bet=${self.bet_size} | max_hours={self.max_hours}h | max_exposure=${self.max_exposure} | interval={self.scan_interval}s")
        cycle = 0
        while True:
            t0 = time.time()
            try:
                if not self.is_paper:
                    self._cached_balance = self._check_usdc_balance()
                opps = await self._scan_once()
                past = [o for o in opps if o["past_end"]]
                imminent = [o for o in opps if not o["past_end"] and o["hrs_to_close"] < 1.0]
                closing = [o for o in opps if not o["past_end"] and o["hrs_to_close"] >= 1.0]
                bal_str = f" bal=${self._cached_balance:.2f}" if not self.is_paper else ""
                logger.info(f"Scan #{self._scan_count}: {len(opps)} opps (PAST_END={len(past)} <1h={len(imminent)} 1-{self.max_hours:.0f}h={len(closing)}) exposure=${self.tracker.exposure():.0f}{bal_str}")
                for opp in opps:
                    with open(OPP_LOG, "a") as f:
                        f.write(json.dumps(opp) + "\n")
                    await self._trade(opp)
                await self._cleanup_zombies()
                await self._stop_loss_exit()
                await self._exit_stale_positions()
                await self._check_resolutions()
                if cycle % 5 == 0:
                    self._print_stats()
                if cycle % 10 == 0 and not self.is_paper:
                    self._sync_clob_balance()
                cycle += 1
            except Exception as e:
                logger.error(f"Monitor cycle error: {e}")
            elapsed = time.time() - t0
            await asyncio.sleep(max(0, self.scan_interval - elapsed))

    async def main(self):
        self._init_clob()
        connector = aiohttp.TCPConnector(limit=20, keepalive_timeout=30)
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            await self.run()


def main():
    parser = argparse.ArgumentParser(description="Polymarket last-minutes scalper — only bets minutes before close")
    parser.add_argument("--live", action="store_true", help="Real trades (default: paper)")
    parser.add_argument("--min-tier", type=float, default=0.92, help="Minimum YES price to consider (default: 0.92)")
    parser.add_argument("--max-hours", type=float, default=MAX_HOURS_TO_CLOSE, help=f"Only bet on markets closing within this many hours (default: {MAX_HOURS_TO_CLOSE})")
    parser.add_argument("--max-exposure", type=float, default=200.0, help="Max total USDC exposure at any time (default: 200)")
    parser.add_argument("--interval", type=float, default=300.0, help="Scan interval in seconds (default: 300 = 5min)")
    parser.add_argument("--min-net", type=float, default=0.5, help="Min net return %% to enter (default: 0.5 — skip 0.1%% bets at 0.999)")
    parser.add_argument("--bet", type=float, default=5.0, help="Flat bet size in USDC (default: 5 — CLOB minimum)")
    args = parser.parse_args()
    scanner = HighConfScanner(is_paper=not args.live, min_tier=args.min_tier, scan_interval=args.interval, max_exposure=args.max_exposure, max_hours=args.max_hours, min_net=args.min_net, bet_size=args.bet)
    try:
        asyncio.run(scanner.main())
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
