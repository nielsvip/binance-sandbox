#!/usr/bin/env python3
"""
ez_copilot.py — Autonomous Trading Oversight Agent

Continuously monitors BOTH crypto (ez_) and stock (tradier_) systems.
PRIORITIZES Tradier during market hours (8am-4pm ET = 12:00-20:00 UTC).
Compares our trades with winning traders from Bitget + Binance leaderboard.
Intervenes autonomously when the system misses opportunities or makes mistakes.

Three core loops:
  1. MONITOR  (30s stocks / 60s crypto) — positions, P&L, indicators, anomalies
  2. COMPARE  (5min stocks / 15min crypto) — vs top traders, missed opportunities
  3. INTERVENE (on-demand) — publish to existing Redis channels for execution

Integrates with existing infrastructure:
  - Redis pubsub: haiku_agent_reversals, haiku_agent_reversals_tradier, haiku_agent_augments
  - ez_haiku_bridge.py picks up commands and executes via TradeManager
  - Decision JSONL for audit trail
  - Claude Haiku for reasoning on complex decisions

Runs as systemd service: binance-copilot.service
"""
import asyncio
import json
import logging
import os
import platform
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiofiles
import redis.asyncio as aioredis

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config
from utils import load_environment_from_gpg, parse_position_key, pk_is_long, pk_is_short, pk_symbol, safe_fetch_float

load_environment_from_gpg(None)

_config = Config()
BASE_PATH = _config.BASE_PATH
LOG_DIR = Path(_config.LOG_DIR) if hasattr(_config, "LOG_DIR") else (Path("/home/niels/logs") if platform.system() != "Darwin" else Path.home() / "logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s [COPILOT] %(message)s", handlers=[logging.FileHandler(LOG_DIR / "ez_copilot.log"), logging.StreamHandler()])
logger = logging.getLogger("copilot")

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CRYPTO_ACCOUNTS = ["ang", "inf", "men", "fin", "flz"]
TRADIER_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + TRADIER_ACCOUNTS
STRICT_NO_LOSS = {"ang", "inf", "men", "fin", "flz"}

HAIKU_MODEL = "claude-haiku-4-5-20251001"
DECISION_DIR = BASE_PATH / "data" / "decisions"

# Timing — all in seconds
MONITOR_INTERVAL_STOCKS = 30
MONITOR_INTERVAL_CRYPTO = 60
COMPARE_INTERVAL_STOCKS = 300   # 5min during market hours
COMPARE_INTERVAL_CRYPTO = 900   # 15min
INTERVENTION_COOLDOWN = 180     # 3min per position
MAX_INTERVENTIONS_PER_HOUR = 20
COMPARE_CONFIDENCE_THRESHOLD = 0.70  # min confidence to act on trader comparison

# Position size limits for copilot interventions
COPILOT_MAX_ORDER_STOCKS = 800.0    # max $ per stock order
COPILOT_MAX_ORDER_CRYPTO = 50.0     # max $ per crypto order
COPILOT_MAX_POSITIONS = 3           # max new positions copilot can open per hour

# Trader data paths
BITGET_DATA_DIR = BASE_PATH / "data" / "bitget_traders"
BINANCE_LB_DIR = BASE_PATH / "data" / "binance_leaderboard"

# ═══════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════

_intervention_cooldowns: Dict[str, float] = {}
_interventions_this_hour: List[float] = []
_copilot_opens_this_hour: List[float] = []
_last_compare_stocks = 0.0
_last_compare_crypto = 0.0
_last_monitor_stocks = 0.0
_last_monitor_crypto = 0.0
_anomaly_history: deque = deque(maxlen=200)
_missed_trades: deque = deque(maxlen=100)
_trader_signals: deque = deque(maxlen=100)
_running = True


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def is_market_hours() -> bool:
    """Check if US stock market is open (9:30 AM - 4:00 PM ET = 13:30 - 20:00 UTC)."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    if weekday >= 5:
        return False
    h, m = now.hour, now.minute
    t = h * 60 + m
    return 810 <= t < 1200  # 13:30 = 810min, 20:00 = 1200min

def is_tradier_priority_window() -> bool:
    """8am-4pm ET = 12:00-20:00 UTC — extended window including pre-market."""
    now = datetime.now(timezone.utc)
    weekday = now.weekday()
    if weekday >= 5:
        return False
    h = now.hour
    return 12 <= h < 20

def _prune_hourly_list(lst: List[float]):
    cutoff = time.time() - 3600
    while lst and lst[0] < cutoff:
        lst.pop(0)

def can_intervene(position_key: str) -> bool:
    now = time.time()
    if now - _intervention_cooldowns.get(position_key, 0) < INTERVENTION_COOLDOWN:
        return False
    _prune_hourly_list(_interventions_this_hour)
    if len(_interventions_this_hour) >= MAX_INTERVENTIONS_PER_HOUR:
        return False
    return True

def can_open_new() -> bool:
    _prune_hourly_list(_copilot_opens_this_hour)
    return len(_copilot_opens_this_hour) < COPILOT_MAX_POSITIONS

def record_intervention(position_key: str):
    now = time.time()
    _intervention_cooldowns[position_key] = now
    _interventions_this_hour.append(now)

def record_open():
    _copilot_opens_this_hour.append(time.time())


async def get_redis() -> aioredis.Redis:
    return aioredis.Redis(host='127.0.0.1', port=6379, decode_responses=True)


_haiku_available = None  # None = unknown, True = working, False = no API key
async def call_haiku(prompt: str) -> Optional[dict]:
    global _haiku_available
    if _haiku_available is False:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(model=HAIKU_MODEL, max_tokens=500, messages=[{"role": "user", "content": prompt}])
        _haiku_available = True
        text = response.content[0].text.strip()
        if "{" in text:
            json_str = text[text.index("{"):text.rindex("}") + 1]
            return json.loads(json_str)
        return None
    except Exception as e:
        err_str = str(e)
        if "authentication" in err_str.lower() or "api_key" in err_str.lower() or "auth_token" in err_str.lower():
            if _haiku_available is not False:
                logger.warning(f"[COPILOT] Haiku API unavailable (no API key) — running RULE-BASED ONLY mode")
                _haiku_available = False
            return None
        logger.error(f"Haiku call failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# SECTION 1 — MONITOR LOOP
# ═══════════════════════════════════════════════════════════════

async def load_positions(account: str, side: str) -> Dict[str, dict]:
    """Load positions from disk. side = 'long' or 'short'."""
    fpath = BASE_PATH / account / f"{side}_positions.json"
    if not fpath.exists():
        return {}
    try:
        async with aiofiles.open(fpath, "r") as f:
            content = await f.read()
        return json.loads(content) if content.strip() else {}
    except Exception:
        return {}


async def load_all_positions(accounts: List[str]) -> Dict[str, Dict[str, dict]]:
    """Load all positions for given accounts. Returns {acct: {pk: pos_data}}."""
    result = {}
    for acct in accounts:
        acct_positions = {}
        for side in ["long", "short"]:
            positions = await load_positions(acct, side)
            for pk, data in positions.items():
                acct_positions[pk] = data
        result[acct] = acct_positions
    return result


# Cache for bulk Redis lookups (refreshed each cycle)
_tradier_indicators_cache: Dict[str, dict] = {}
_tradier_prices_cache: Dict[str, dict] = {}
_tradier_cache_ts: float = 0.0
_crypto_indicators_cache: Dict[str, dict] = {}
_crypto_cache_ts: float = 0.0


async def _refresh_tradier_cache(redis_client: aioredis.Redis):
    """Refresh tradier indicators + prices from bulk Redis keys."""
    global _tradier_indicators_cache, _tradier_prices_cache, _tradier_cache_ts
    now = time.time()
    if now - _tradier_cache_ts < 10:
        return
    try:
        raw = await redis_client.get("tradier_indicators_latest")
        if raw:
            _tradier_indicators_cache = json.loads(raw)
    except Exception:
        pass
    try:
        raw = await redis_client.get("tradier_prices_latest")
        if raw:
            _tradier_prices_cache = json.loads(raw)
    except Exception:
        pass
    _tradier_cache_ts = now


async def _refresh_crypto_cache(redis_client: aioredis.Redis):
    """Refresh crypto indicators from Redis. Crypto uses per-symbol keys."""
    global _crypto_indicators_cache, _crypto_cache_ts
    now = time.time()
    if now - _crypto_cache_ts < 30:
        return
    # Crypto indicators are per-symbol in Redis, but there's also a bulk key
    try:
        raw = await redis_client.get("latest_market_data")
        if raw:
            _crypto_indicators_cache = json.loads(raw)
    except Exception:
        pass
    _crypto_cache_ts = now


async def get_indicators_from_redis(redis_client: aioredis.Redis, symbol: str, system: str = "crypto") -> dict:
    """Get latest indicators from Redis for a symbol."""
    if system == "tradier":
        await _refresh_tradier_cache(redis_client)
        return _tradier_indicators_cache.get(symbol, _tradier_indicators_cache.get(symbol.upper(), {}))
    else:
        await _refresh_crypto_cache(redis_client)
        result = _crypto_indicators_cache.get(symbol, {})
        if result:
            return result
        # Fallback: try per-symbol key
        try:
            raw = await redis_client.get(f"indicators:{symbol}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return {}


async def get_price_from_redis(redis_client: aioredis.Redis, symbol: str, system: str = "crypto") -> float:
    if system == "tradier":
        await _refresh_tradier_cache(redis_client)
        entry = _tradier_prices_cache.get(symbol, _tradier_prices_cache.get(symbol.upper(), {}))
        if isinstance(entry, dict):
            return float(entry.get("price") or entry.get("last") or entry.get("mark") or 0)
        elif entry:
            return float(entry)
        # Fallback: check indicators cache for price
        ind = _tradier_indicators_cache.get(symbol, {})
        if ind:
            return float(ind.get("last") or ind.get("close_1m") or ind.get("price") or 0)
    else:
        try:
            raw = await redis_client.get(f"price_cache:{symbol}")
            if raw:
                d = json.loads(raw) if isinstance(raw, str) and raw.startswith("{") else {"price": raw}
                return float(d.get("price") or d.get("last") or d.get("mark") or raw or 0)
        except Exception:
            pass
    return 0.0


def analyze_position_health(pk: str, pos: dict, price: float) -> List[dict]:
    """Detect anomalies in a single position."""
    anomalies = []
    amt = abs(safe_fetch_float(pos.get("positionAmt") or pos.get("quantity", 0), 0))
    if amt == 0:
        return anomalies
    entry = safe_fetch_float(pos.get("entryPrice") or pos.get("entry_price") or pos.get("average_fill_price", 0), 0)
    mark = price if price > 0 else safe_fetch_float(pos.get("markPrice") or pos.get("mark_price", 0), 0)
    if entry <= 0 or mark <= 0:
        return anomalies
    is_long = pk.endswith("_LONG") or "_LONG" in pk
    if is_long:
        pnl_pct = ((mark - entry) / entry) * 100
    else:
        pnl_pct = ((entry - mark) / entry) * 100
    value = amt * mark
    # Big loser alert
    if pnl_pct < -8.0 and value > 50:
        anomalies.append({"type": "BIG_LOSER", "pk": pk, "pnl_pct": pnl_pct, "value": value, "severity": "HIGH"})
    # Big winner not being augmented
    if pnl_pct > 5.0 and value < 200:
        anomalies.append({"type": "WINNER_SMALL", "pk": pk, "pnl_pct": pnl_pct, "value": value, "severity": "MEDIUM"})
    # Stale position — held too long with tiny gain
    opened_at = pos.get("opened_at") or pos.get("open_time", "")
    if opened_at:
        try:
            opened_ts = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - opened_ts).total_seconds() / 3600
            if age_hours > 48 and abs(pnl_pct) < 1.0 and value > 20:
                anomalies.append({"type": "STALE_POSITION", "pk": pk, "age_hours": age_hours, "pnl_pct": pnl_pct, "severity": "LOW"})
        except Exception:
            pass
    return anomalies


async def check_indicator_staleness(redis_client: aioredis.Redis, system: str = "crypto") -> List[dict]:
    """Check if indicators are stale by checking timestamps inside the data."""
    anomalies = []
    try:
        if system == "tradier":
            await _refresh_tradier_cache(redis_client)
            # Check a few symbols' timestamps inside the bulk data
            for sym, data in list(_tradier_indicators_cache.items())[:3]:
                ts_str = data.get("1m_updated_at") or data.get("timestamp") or data.get("updated_at", "")
                if ts_str:
                    ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    if age > 120:
                        anomalies.append({"type": "STALE_INDICATORS", "system": system, "age_seconds": age, "symbol": sym, "severity": "HIGH"})
                    break
        else:
            # Try bulk timestamp key first
            ts_str = await redis_client.get("indicators_timestamp")
            if not ts_str:
                ts_str = await redis_client.get("indicators_updated_at")
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age > 300:
                    anomalies.append({"type": "STALE_INDICATORS", "system": system, "age_seconds": age, "severity": "HIGH"})
    except Exception:
        pass
    return anomalies


async def check_ratio_balance(positions: Dict[str, dict]) -> List[dict]:
    """Check L/S ratio balance across positions."""
    anomalies = []
    long_value = 0.0
    short_value = 0.0
    for pk, pos in positions.items():
        amt = abs(safe_fetch_float(pos.get("positionAmt") or pos.get("quantity", 0), 0))
        mark = safe_fetch_float(pos.get("markPrice") or pos.get("mark_price", 0), 0)
        if amt == 0 or mark == 0:
            continue
        val = amt * mark
        if pk.endswith("_LONG") or "_LONG" in pk:
            long_value += val
        else:
            short_value += val
    total = long_value + short_value
    if total < 50:
        return anomalies
    ratio = long_value / max(short_value, 1)
    if ratio > 3.0 or ratio < 0.33:
        anomalies.append({"type": "RATIO_IMBALANCE", "long_value": long_value, "short_value": short_value, "ratio": ratio, "severity": "HIGH"})
    elif ratio > 2.0 or ratio < 0.5:
        anomalies.append({"type": "RATIO_DRIFT", "long_value": long_value, "short_value": short_value, "ratio": ratio, "severity": "MEDIUM"})
    return anomalies


async def find_best_ratio_candidate(redis_client: aioredis.Redis, account: str, need_side: str, system: str, existing_positions: Dict[str, dict]) -> Optional[dict]:
    """Find the best symbol to open on the underweight side for ratio compensation.
    need_side = 'LONG' or 'SHORT'. Returns {symbol, side, score, price} or None."""
    existing_symbols_with_side = set()
    for pk in existing_positions:
        pos = existing_positions[pk]
        amt = abs(safe_fetch_float(pos.get("positionAmt") or pos.get("quantity", 0), 0))
        if amt <= 0:
            continue
        sym = pk.split(":")[1].rsplit("_", 1)[0] if ":" in pk else pk.rsplit("_", 1)[0]
        side = "LONG" if pk.endswith("_LONG") else "SHORT"
        existing_symbols_with_side.add(f"{sym}_{side}")
    candidates = []
    if system == "tradier":
        await _refresh_tradier_cache(redis_client)
        cache = _tradier_indicators_cache
        price_cache = _tradier_prices_cache
        symbols_file = BASE_PATH / "symbols_tradier.json"
    else:
        await _refresh_crypto_cache(redis_client)
        cache = _crypto_indicators_cache
        price_cache = {}
        symbols_file = BASE_PATH / "symbols.json"
    try:
        with open(symbols_file, "r") as f:
            all_symbols = json.loads(f.read())
    except Exception:
        all_symbols = list(cache.keys())
    for sym in all_symbols:
        if f"{sym}_{need_side}" in existing_symbols_with_side:
            continue
        ind = cache.get(sym, {})
        if not ind:
            continue
        wt1_15m = safe_fetch_float(ind.get("wt1_15m"), 0)
        wt2_15m = safe_fetch_float(ind.get("wt2_15m"), 0)
        wt1_1h = safe_fetch_float(ind.get("wt1_1h"), 0)
        wt2_1h = safe_fetch_float(ind.get("wt2_1h"), 0)
        k_15m = safe_fetch_float(ind.get("k_15m") or ind.get("k_15m_prev") or ind.get("stoch_k_15m"), 50)
        price = safe_fetch_float(ind.get("current_price") or ind.get("price") or ind.get("last") or ind.get("mark_price"), 0)
        if price <= 0:
            if system == "tradier":
                p_entry = price_cache.get(sym, {})
                price = float(p_entry.get("price") or p_entry.get("last") or 0) if isinstance(p_entry, dict) else float(p_entry or 0)
            if price <= 0:
                continue
        score = 0.0
        if need_side == "LONG":
            if wt1_15m < wt2_15m:
                score += 2.0
            if wt1_1h < wt2_1h:
                score += 1.5
            if k_15m < 30:
                score += 2.0
            elif k_15m < 50:
                score += 1.0
        else:
            if wt1_15m > wt2_15m:
                score += 2.0
            if wt1_1h > wt2_1h:
                score += 1.5
            if k_15m > 70:
                score += 2.0
            elif k_15m > 50:
                score += 1.0
        if score >= 2.0:
            candidates.append({"symbol": sym, "side": need_side, "score": score, "price": price, "k_15m": k_15m, "wt1_15m": wt1_15m})
    if not candidates:
        return None
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


_ratio_compensation_cooldown: Dict[str, float] = {}
RATIO_COMPENSATION_COOLDOWN = 300  # 5min between ratio compensations per account

async def compensate_ratio(redis_client: aioredis.Redis, account: str, system: str, ratio: float, positions: Dict[str, dict]):
    """Find best candidate on underweight side and open a position to fix ratio."""
    now = time.time()
    cd_key = f"{account}:{system}"
    if now - _ratio_compensation_cooldown.get(cd_key, 0) < RATIO_COMPENSATION_COOLDOWN:
        return
    need_side = "LONG" if ratio < 0.5 else "SHORT"
    candidate = await find_best_ratio_candidate(redis_client, account, need_side, system, positions)
    if not candidate:
        logger.warning(f"[COPILOT RATIO] {account} ratio={ratio:.2f} needs {need_side} but no good candidates found")
        return
    sym = candidate["symbol"]
    price = candidate["price"]
    score = candidate["score"]
    is_long = need_side == "LONG"
    pk = f"{account}:{sym}_{need_side}"
    channel = "haiku_agent_augments" if system == "crypto" else "haiku_agent_reversals_tradier"
    if system == "crypto":
        channel = "haiku_agent_augments"
        qty_value = 10.0
        qty = qty_value / price if price > 0 else 0
    else:
        channel = "haiku_agent_augments"
        qty = 1
    if qty <= 0:
        return
    cmd = {"position_key": pk, "account_key": account, "action": "OPEN", "reason": f"COPILOT_RATIO_FIX: ratio={ratio:.2f} need {need_side}, best={sym} score={score:.1f} k15m={candidate.get('k_15m', 0):.0f}", "price": price, "qty": qty, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "copilot_ratio"}
    await redis_client.publish(channel, json.dumps(cmd))
    _ratio_compensation_cooldown[cd_key] = now
    logger.warning(f"[COPILOT RATIO] {account} ratio={ratio:.2f} → OPENING {pk} qty={qty:.6f} @ {price:.8f} (score={score:.1f}, ${qty*price:.2f})")
    record_intervention(pk)


async def monitor_system(redis_client: aioredis.Redis, system: str):
    """Full monitoring pass for crypto or tradier."""
    accounts = TRADIER_ACCOUNTS if system == "tradier" else CRYPTO_ACCOUNTS
    all_positions = await load_all_positions(accounts)
    all_anomalies = []
    # Indicator staleness
    stale = await check_indicator_staleness(redis_client, system)
    all_anomalies.extend(stale)
    for acct, positions in all_positions.items():
        # Per-position health
        for pk, pos in positions.items():
            symbol = pk_symbol(pk) if pk_symbol(pk) else pk.split("_")[0]
            price = await get_price_from_redis(redis_client, symbol, system)
            health = analyze_position_health(pk, pos, price)
            for a in health:
                a["account"] = acct
                a["system"] = system
            all_anomalies.extend(health)
        # Ratio check
        ratio_issues = await check_ratio_balance(positions)
        for a in ratio_issues:
            a["account"] = acct
            a["system"] = system
        all_anomalies.extend(ratio_issues)
    # Log anomalies
    for a in all_anomalies:
        _anomaly_history.append({**a, "detected_at": datetime.now(timezone.utc).isoformat()})
        if a["severity"] == "HIGH":
            logger.warning(f"[{system.upper()}] ANOMALY: {a['type']} — {json.dumps({k: v for k, v in a.items() if k != 'severity'}, default=str)}")
        else:
            logger.info(f"[{system.upper()}] anomaly: {a['type']} — {a.get('pk', a.get('account', ''))}")
    return all_anomalies


# ═══════════════════════════════════════════════════════════════
# SECTION 2 — COMPARE LOOP (vs winning traders)
# ═══════════════════════════════════════════════════════════════

def load_trader_trades(data_dir: Path, max_age_hours: int = 24) -> List[dict]:
    """Load recent trades from trader scraper output (Bitget or Binance LB)."""
    trades = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    # Try JSONL trade log
    trade_log = data_dir / "trade_log.jsonl"
    if trade_log.exists():
        try:
            with open(trade_log, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        t = json.loads(line)
                        ts_str = t.get("timestamp") or t.get("open_time") or t.get("time", "")
                        if ts_str:
                            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                            if ts >= cutoff:
                                trades.append(t)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            pass
    # Try CSV export
    csv_file = data_dir / "completed_trades.csv"
    if csv_file.exists():
        try:
            import csv
            with open(csv_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts_str = row.get("open_time") or row.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                            if ts >= cutoff:
                                trades.append(row)
                        except (ValueError, TypeError):
                            continue
        except Exception:
            pass
    # Try individual trader files
    if data_dir.exists():
        for tf in sorted(data_dir.glob("trader_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            try:
                with open(tf) as f:
                    data = json.loads(f.read())
                if isinstance(data, list):
                    for t in data:
                        trades.append(t)
                elif isinstance(data, dict) and "trades" in data:
                    for t in data["trades"]:
                        trades.append(t)
            except Exception:
                continue
    return trades


def extract_trader_signals(trades: List[dict]) -> List[dict]:
    """Extract actionable signals from trader trades."""
    signals = []
    symbol_actions = defaultdict(list)
    for t in trades:
        symbol = (t.get("symbol") or t.get("pair") or t.get("ticker") or "").upper().replace("USDT", "")
        if not symbol:
            continue
        side = (t.get("side") or t.get("direction") or t.get("posSide") or "").upper()
        action = (t.get("action") or t.get("type") or "").upper()
        pnl = safe_fetch_float(t.get("pnl") or t.get("realizedPnl") or t.get("profit", 0), 0)
        roi = safe_fetch_float(t.get("roi") or t.get("roe") or t.get("pnl_pct", 0), 0)
        trader_id = t.get("trader_id") or t.get("traderId") or t.get("nickname") or "unknown"
        # Normalize direction
        is_long = any(x in side for x in ["LONG", "BUY"]) or any(x in action for x in ["BUY", "OPEN_LONG"])
        is_short = any(x in side for x in ["SHORT", "SELL"]) or any(x in action for x in ["SELL", "OPEN_SHORT"])
        is_open = any(x in action for x in ["OPEN", "BUY", "INCREASE"])
        is_close = any(x in action for x in ["CLOSE", "REDUCE", "DECREASE"])
        if not (is_long or is_short):
            continue
        symbol_actions[symbol].append({"symbol": symbol, "is_long": is_long, "is_short": is_short, "is_open": is_open, "is_close": is_close, "pnl": pnl, "roi": roi, "trader_id": trader_id})
    # Aggregate: if multiple top traders open same direction = strong signal
    for symbol, actions in symbol_actions.items():
        opens_long = [a for a in actions if a["is_open"] and a["is_long"]]
        opens_short = [a for a in actions if a["is_open"] and a["is_short"]]
        closes = [a for a in actions if a["is_close"]]
        winning_closes = [a for a in closes if a["pnl"] > 0]
        if len(opens_long) >= 2:
            signals.append({"symbol": symbol, "direction": "LONG", "type": "MULTI_TRADER_OPEN", "count": len(opens_long), "traders": [a["trader_id"] for a in opens_long[:5]], "confidence": min(0.5 + len(opens_long) * 0.15, 0.95)})
        if len(opens_short) >= 2:
            signals.append({"symbol": symbol, "direction": "SHORT", "type": "MULTI_TRADER_OPEN", "count": len(opens_short), "traders": [a["trader_id"] for a in opens_short[:5]], "confidence": min(0.5 + len(opens_short) * 0.15, 0.95)})
        if len(winning_closes) >= 2:
            signals.append({"symbol": symbol, "direction": "EXIT", "type": "MULTI_TRADER_EXIT", "count": len(winning_closes), "traders": [a["trader_id"] for a in winning_closes[:5]], "confidence": min(0.4 + len(winning_closes) * 0.15, 0.90)})
    return signals


async def get_recent_decisions(account: str, minutes: int = 60) -> List[dict]:
    """Get recent decisions for an account."""
    decisions = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)
    for day_offset in [0, 1]:
        day = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
        fpath = DECISION_DIR / f"decisions_{account}_{day}.jsonl"
        if not fpath.exists():
            continue
        try:
            async with aiofiles.open(fpath, "r") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        ts_str = d.get("timestamp", "")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts >= cutoff:
                                decisions.append(d)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            continue
    return decisions


def find_missed_opportunities(signals: List[dict], our_positions: Dict[str, Dict[str, dict]], system: str) -> List[dict]:
    """Compare trader signals against our positions to find misses."""
    missed = []
    # Build a set of symbols we hold
    our_symbols = defaultdict(set)  # {symbol: set of directions}
    for acct, positions in our_positions.items():
        for pk, pos in positions.items():
            amt = abs(safe_fetch_float(pos.get("positionAmt") or pos.get("quantity", 0), 0))
            if amt == 0:
                continue
            sym = pk_symbol(pk) if pk_symbol(pk) else pk.split("_")[0]
            if pk.endswith("_LONG"):
                our_symbols[sym].add("LONG")
            elif pk.endswith("_SHORT"):
                our_symbols[sym].add("SHORT")
    for sig in signals:
        symbol = sig["symbol"]
        direction = sig["direction"]
        if direction == "EXIT":
            continue
        # For stocks, check if the symbol matches our tradeable universe
        if system == "tradier":
            # Map crypto symbols to stock equivalents if needed
            stock_symbol = symbol.replace("USDT", "").replace("USD", "")
            if stock_symbol in our_symbols and direction in our_symbols[stock_symbol]:
                continue  # We already have this position
            if sig["confidence"] >= COMPARE_CONFIDENCE_THRESHOLD:
                missed.append({"symbol": stock_symbol, "direction": direction, "signal": sig, "system": system, "reason": f"{sig['count']} top traders opened {direction} on {stock_symbol}"})
        else:
            crypto_sym = symbol + "USDT" if not symbol.endswith("USDT") else symbol
            base_sym = symbol.replace("USDT", "")
            if base_sym in our_symbols and direction in our_symbols[base_sym]:
                continue
            if crypto_sym.replace("USDT", "") in our_symbols and direction in our_symbols[crypto_sym.replace("USDT", "")]:
                continue
            if sig["confidence"] >= COMPARE_CONFIDENCE_THRESHOLD:
                missed.append({"symbol": crypto_sym, "direction": direction, "signal": sig, "system": system, "reason": f"{sig['count']} top traders opened {direction} on {crypto_sym}"})
    return missed


async def compare_with_traders(redis_client: aioredis.Redis, system: str):
    """Compare our positions with winning traders."""
    accounts = TRADIER_ACCOUNTS if system == "tradier" else CRYPTO_ACCOUNTS
    our_positions = await load_all_positions(accounts)
    # Load trader data from both sources
    all_trader_trades = []
    if BITGET_DATA_DIR.exists():
        all_trader_trades.extend(load_trader_trades(BITGET_DATA_DIR, max_age_hours=6))
    if BINANCE_LB_DIR.exists():
        all_trader_trades.extend(load_trader_trades(BINANCE_LB_DIR, max_age_hours=6))
    if not all_trader_trades:
        logger.debug(f"[{system.upper()}] No recent trader data found")
        return []
    signals = extract_trader_signals(all_trader_trades)
    if not signals:
        return []
    missed = find_missed_opportunities(signals, our_positions, system)
    for m in missed:
        _missed_trades.append({**m, "detected_at": datetime.now(timezone.utc).isoformat()})
        logger.info(f"[{system.upper()}] MISSED TRADE: {m['symbol']} {m['direction']} — {m['reason']}")
    _trader_signals.extend(signals)
    return missed


# ═══════════════════════════════════════════════════════════════
# SECTION 3 — INTERVENE
# ═══════════════════════════════════════════════════════════════

def build_intervention_prompt(anomaly_or_missed: dict, context: dict) -> str:
    """Build a Haiku prompt for intervention decision."""
    item_type = anomaly_or_missed.get("type", anomaly_or_missed.get("signal", {}).get("type", "UNKNOWN"))
    symbol = anomaly_or_missed.get("symbol") or anomaly_or_missed.get("pk", "UNKNOWN")
    system = anomaly_or_missed.get("system", "crypto")
    direction = anomaly_or_missed.get("direction", "")
    return f"""You are an autonomous trading copilot. Decide whether to intervene.

SITUATION:
- System: {system.upper()}
- Type: {item_type}
- Symbol: {symbol}
- Direction: {direction}
- Details: {json.dumps(anomaly_or_missed, default=str)[:500]}

CURRENT CONTEXT:
- Market hours active: {is_market_hours()}
- Tradier priority window: {is_tradier_priority_window()}
- Recent interventions this hour: {len(_interventions_this_hour)}
- Positions context: {json.dumps(context, default=str)[:300]}

RULES:
1. NEVER close or reduce a position AT A LOSS. ALL accounts are STRICT_NO_LOSS. The L/S ratio IS the hedge.
2. REDUCE is allowed ONLY on profitable positions (taking profit). Never reduce a losing position.
3. For MISSED_TRADE signals: only recommend opening if 2+ top traders agree AND indicators align
4. For BIG_LOSER anomalies: do NOT close — only recommend augmenting the opposite side for balance
5. For RATIO_IMBALANCE: recommend opening underweight side, NEVER closing overweight
6. For WINNER_SMALL: recommend augmenting if gain > 5% and indicators still favorable
7. For STALE_INDICATORS: recommend SKIP until fresh data
8. During Tradier priority window (8am-4pm ET): be more aggressive on stock opportunities
9. Max confidence 0.95 — always leave room for uncertainty

Respond ONLY with valid JSON:
{{"action": "OPEN_LONG"|"OPEN_SHORT"|"AUGMENT"|"REDUCE"|"SKIP"|"ALERT_ONLY",
  "symbol": "...",
  "system": "crypto"|"tradier",
  "confidence": 0.0-1.0,
  "reason": "brief explanation",
  "suggested_size_pct": 0.0-1.0}}

Say SKIP unless you are >= 70% confident. ALERT_ONLY for things the human should review."""


async def execute_intervention(redis_client: aioredis.Redis, decision: dict, source: dict):
    """Execute a copilot intervention via Redis pubsub."""
    action = decision.get("action", "SKIP")
    if action in ("SKIP", "ALERT_ONLY"):
        if action == "ALERT_ONLY":
            logger.warning(f"[COPILOT ALERT] {decision.get('reason', 'no reason')} — {decision.get('symbol', '?')}")
        return
    symbol = decision.get("symbol", "")
    system = decision.get("system", "crypto")
    confidence = safe_fetch_float(decision.get("confidence", 0), 0)
    reason = decision.get("reason", "COPILOT_INTERVENTION")
    if confidence < COMPARE_CONFIDENCE_THRESHOLD:
        logger.info(f"[COPILOT] Skipping {action} on {symbol}: confidence {confidence:.2f} < {COMPARE_CONFIDENCE_THRESHOLD}")
        return
    # Determine account and position key
    if system == "tradier":
        account = "trb"  # Primary tradier account
        if action in ("OPEN_LONG", "AUGMENT") and "LONG" in action:
            pk = f"{account}:{symbol}_LONG"
        elif action in ("OPEN_SHORT",) or "SHORT" in action:
            pk = f"{account}:{symbol}_SHORT"
        else:
            pk = f"{account}:{symbol}_LONG"
    else:
        account = "ang"  # Primary crypto account
        pk = f"{account}:{symbol}_LONG" if "LONG" in action else f"{account}:{symbol}_SHORT"
    if not can_intervene(pk):
        logger.info(f"[COPILOT] Cooldown active for {pk}, skipping")
        return
    is_open = action.startswith("OPEN_")
    if is_open and not can_open_new():
        logger.info(f"[COPILOT] Max opens per hour reached, skipping {pk}")
        return
    # Build Redis command
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if action in ("OPEN_LONG", "OPEN_SHORT"):
        # For opens: publish as augment command (bridge handles both)
        direction = "LONG" if "LONG" in action else "SHORT"
        cmd = {"position_key": pk, "account_key": account, "action": "OPEN", "reason": f"COPILOT_{action}: {reason} (conf={confidence:.2f})", "price": 0, "qty": 0, "timestamp": now_iso, "source": "copilot", "direction": direction}
        channel = "haiku_agent_augments" if system == "crypto" else "haiku_agent_reversals_tradier"
        # For tradier, we need a different approach — publish to a copilot-specific channel
        channel = "copilot_tradier_orders" if system == "tradier" else "copilot_crypto_orders"
        await redis_client.publish(channel, json.dumps(cmd))
        record_intervention(pk)
        if is_open:
            record_open()
        logger.warning(f"[COPILOT INTERVENE] {action} {pk} — {reason} (conf={confidence:.2f})")
    elif action == "AUGMENT":
        cmd = {"position_key": pk, "account_key": account, "action": "AUGMENT", "reason": f"COPILOT_AUGMENT: {reason} (conf={confidence:.2f})", "price": 0, "qty": 0, "timestamp": now_iso, "source": "copilot"}
        if system == "crypto":
            await redis_client.publish("haiku_agent_augments", json.dumps(cmd))
        else:
            await redis_client.publish("copilot_tradier_orders", json.dumps(cmd))
        record_intervention(pk)
        logger.warning(f"[COPILOT AUGMENT] {pk} — {reason} (conf={confidence:.2f})")
    elif action == "REDUCE":
        # STRICT_NO_LOSS: NEVER reduce/close at a LOSS. Profitable reduces are OK.
        # Copilot doesn't know current P&L here, so we publish and let the bridge/manage verify profitability
        cmd = {"position_key": pk, "account_key": account, "action": "REDUCE", "reason": f"COPILOT_REDUCE: {reason} (conf={confidence:.2f}) — ONLY IF PROFITABLE", "price": 0, "timestamp": now_iso, "source": "copilot", "strict_no_loss": True}
        if system == "crypto":
            await redis_client.publish("haiku_agent_reversals", json.dumps(cmd))
        else:
            await redis_client.publish("haiku_agent_reversals_tradier", json.dumps(cmd))
        record_intervention(pk)
        logger.warning(f"[COPILOT REDUCE] {pk} — {reason} (conf={confidence:.2f}) — ONLY IF PROFITABLE")
    # Log to decision JSONL
    decision_record = {"timestamp": now_iso, "position_key": pk, "account": account, "action": f"COPILOT_{action}", "reason": reason, "extra": {"confidence": confidence, "source_type": source.get("type", "unknown"), "copilot_version": "1.0"}}
    log_path = DECISION_DIR / f"decisions_{account}_{today}.jsonl"
    try:
        async with aiofiles.open(log_path, "a") as f:
            await f.write(json.dumps(decision_record, default=str) + "\n")
    except Exception as e:
        logger.error(f"Failed to log decision: {e}")


async def process_anomalies_for_intervention(redis_client: aioredis.Redis, anomalies: List[dict]):
    """Process high-severity anomalies — rule-based first, Haiku as optional enhancement."""
    high_severity = [a for a in anomalies if a.get("severity") == "HIGH"]
    for anomaly in high_severity[:5]:
        pk = anomaly.get("pk", "")
        if pk and not can_intervene(pk):
            continue
        atype = anomaly.get("type", "")
        account = anomaly.get("account", "")
        system = anomaly.get("system", "crypto")
        rule_decision = None
        if atype == "RATIO_IMBALANCE" and account:
            ratio = anomaly.get("ratio", 1.0)
            cd_key = f"{account}:{system}"
            if time.time() - _ratio_compensation_cooldown.get(cd_key, 0) < RATIO_COMPENSATION_COOLDOWN:
                continue
            _ratio_compensation_cooldown[cd_key] = time.time()
            acct_positions = {}
            try:
                all_pos = await load_all_positions([account])
                acct_positions = all_pos.get(account, {})
            except Exception:
                pass
            await compensate_ratio(redis_client, account, system, ratio, acct_positions)
            continue
        elif atype == "WINNER_SMALL" and pk:
            pnl_pct = anomaly.get("pnl_pct", 0)
            if pnl_pct >= 2.0:
                rule_decision = {"action": "AUGMENT", "reason": f"RULE: winner +{pnl_pct:.1f}% on small size — augment", "confidence": 0.8, "position_key": pk, "account": account}
        elif atype == "BIG_LOSER" and pk:
            pnl_pct = anomaly.get("pnl_pct", 0)
            logger.warning(f"[COPILOT RULE] BIG_LOSER {pk} at {pnl_pct:.1f}% — STRICT_NO_LOSS: holding, ratio will rebalance")
            continue
        elif atype == "STALE_INDICATORS":
            logger.warning(f"[COPILOT RULE] Stale indicators ({system}) — supervisor will auto-restart")
            continue
        if rule_decision:
            rule_decision.setdefault("position_key", pk)
            rule_decision.setdefault("account", account)
            rule_decision.setdefault("system", system)
            await execute_intervention(redis_client, rule_decision, anomaly)
            continue
        context = {"total_anomalies": len(anomalies), "high_count": len(high_severity)}
        prompt = build_intervention_prompt(anomaly, context)
        decision = await call_haiku(prompt)
        if decision:
            await execute_intervention(redis_client, decision, anomaly)


async def process_missed_trades_for_intervention(redis_client: aioredis.Redis, missed: List[dict]):
    """Process missed trades through Haiku for intervention decisions."""
    for m in missed[:3]:  # Cap at 3 per cycle
        symbol = m.get("symbol", "")
        system = m.get("system", "crypto")
        # Get current indicators for extra context
        indicators = await get_indicators_from_redis(redis_client, symbol, system)
        context = {"indicators_available": bool(indicators), "stoch_k_15m": indicators.get("stoch_k_15m", "N/A"), "wt1_15m": indicators.get("wt1_15m", "N/A"), "ha_15m": indicators.get("ha_15m", "N/A"), "is_market_hours": is_market_hours()}
        prompt = build_intervention_prompt(m, context)
        decision = await call_haiku(prompt)
        if decision:
            await execute_intervention(redis_client, decision, m)


# ═══════════════════════════════════════════════════════════════
# SECTION 4 — TRADIER ORDER EXECUTOR (copilot's own channel)
# ═══════════════════════════════════════════════════════════════

async def tradier_order_listener(redis_client: aioredis.Redis):
    """Listen for copilot's own tradier order commands and execute via TradierAPIClient."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("copilot_tradier_orders")
    logger.info("[COPILOT] Tradier order listener started")
    try:
        from tradier_api import TradierAPIClient
        from config_tradier import TradierConfig
        tconfig = TradierConfig()
        api = TradierAPIClient(tconfig)
    except Exception as e:
        logger.error(f"[COPILOT] Cannot init TradierAPIClient: {e} — tradier interventions disabled")
        return
    async for message in pubsub.listen():
        if not _running:
            break
        if message["type"] != "message":
            continue
        try:
            data = json.loads(message["data"])
            action = data.get("action", "")
            symbol = pk_symbol(data.get("position_key", "")) or data.get("symbol", "")
            account = data.get("account_key", "trb")
            reason = data.get("reason", "")
            direction = data.get("direction", "")
            if not symbol:
                continue
            # Safety: check market hours
            if not is_market_hours():
                logger.info(f"[COPILOT] Market closed, skipping tradier order: {symbol} {action}")
                continue
            # Determine side and quantity
            price = await get_price_from_redis(redis_client, symbol, "tradier")
            if price <= 0:
                # Try API quote
                try:
                    quote = await api.get_quote(symbol)
                    price = float(quote.get("last", 0) or quote.get("mark", 0) or 0)
                except Exception:
                    pass
            if price <= 0:
                logger.error(f"[COPILOT] No price for {symbol}, skipping")
                continue
            # Calculate quantity
            order_value = min(COPILOT_MAX_ORDER_STOCKS, 600.0)
            qty = max(1, int(order_value / price))
            if action == "OPEN" or action.startswith("OPEN_"):
                is_long = direction == "LONG" or "LONG" in action
                side = "buy" if is_long else "sell_short"
                logger.warning(f"[COPILOT TRADIER] Placing {side} order: {symbol} x{qty} @ ~${price:.2f} — {reason}")
                try:
                    await api.switch_account(account)
                    result = await api.place_order(account, symbol, side, qty, order_type="market")
                    logger.warning(f"[COPILOT TRADIER] Order result: {json.dumps(result, default=str)[:200]}")
                except Exception as e:
                    logger.error(f"[COPILOT TRADIER] Order failed: {e}")
            elif action == "AUGMENT":
                # Augment existing position
                is_long = "_LONG" in data.get("position_key", "")
                side = "buy" if is_long else "sell_short"
                aug_qty = max(1, int(qty * 0.5))  # 50% of base size for augments
                logger.warning(f"[COPILOT TRADIER] Augmenting {symbol} {side} x{aug_qty} — {reason}")
                try:
                    await api.switch_account(account)
                    result = await api.place_order(account, symbol, side, aug_qty, order_type="market")
                    logger.warning(f"[COPILOT TRADIER] Augment result: {json.dumps(result, default=str)[:200]}")
                except Exception as e:
                    logger.error(f"[COPILOT TRADIER] Augment failed: {e}")
        except Exception as e:
            logger.error(f"[COPILOT] Tradier order listener error: {e}")


async def crypto_order_listener(redis_client: aioredis.Redis):
    """Listen for copilot's own crypto order commands. These go through the existing haiku bridge."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("copilot_crypto_orders")
    logger.info("[COPILOT] Crypto order listener started")
    async for message in pubsub.listen():
        if not _running:
            break
        if message["type"] != "message":
            continue
        try:
            data = json.loads(message["data"])
            action = data.get("action", "")
            direction = data.get("direction", "LONG")
            pk = data.get("position_key", "")
            account = data.get("account_key", "ang")
            reason = data.get("reason", "")
            symbol = pk_symbol(pk) or ""
            if not symbol:
                continue
            price = await get_price_from_redis(redis_client, symbol, "crypto")
            if price <= 0:
                continue
            qty = COPILOT_MAX_ORDER_CRYPTO / price if price > 0 else 0
            if qty <= 0:
                continue
            # Forward to haiku_agent_augments which ez_haiku_bridge.py already handles
            cmd = {"position_key": pk, "account_key": account, "action": "AUGMENT" if action != "OPEN" else "AUGMENT", "reason": reason, "price": price, "qty": qty, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "copilot"}
            await redis_client.publish("haiku_agent_augments", json.dumps(cmd))
            logger.warning(f"[COPILOT CRYPTO] Forwarded to haiku bridge: {pk} {action} qty={qty:.6f}")
        except Exception as e:
            logger.error(f"[COPILOT] Crypto order listener error: {e}")


# ═══════════════════════════════════════════════════════════════
# SECTION 5 — DAILY 9AM ET ANALYSIS (sandbox + live → 100.md + 100.xlsx)
# ═══════════════════════════════════════════════════════════════

DAILY_ANALYSIS_HOUR_UTC = 13  # 9am ET = 13:00 UTC (EDT)
SERVER_HOST = "s1-int"
SANDBOX_RESULTS_DIR = "/home/niels/binance-sandbox/backtest_framework/results"
REPORT_MD = BASE_PATH / "100.md"
REPORT_XLS = BASE_PATH / "100.xlsx"
_last_daily_analysis_date: str = ""


def _run_ssh(cmd: str, timeout: int = 30) -> str:
    import subprocess
    try:
        result = subprocess.run(f"ssh -o ConnectTimeout=5 -o BatchMode=yes {SERVER_HOST} \"{cmd}\"", shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _fetch_sandbox_results() -> dict:
    """Pull latest backtest results from server SQLite DBs."""
    results = {"tournament": [], "trigger_sweep": [], "tradier": []}
    # Tournament top combos
    raw = _run_ssh(f"/home/niels/.conda/envs/binance_env/bin/python -c \"import sqlite3,json;c=sqlite3.connect('{SANDBOX_RESULTS_DIR}/tournament.db');rows=c.execute('SELECT combo_label,avg_sharpe,avg_wr,avg_pf,avg_maxdd,avg_return,total_trades FROM combo_scores ORDER BY avg_sharpe DESC LIMIT 20').fetchall();print(json.dumps(rows))\"", timeout=20)
    if raw:
        try:
            results["tournament"] = json.loads(raw)
        except Exception:
            pass
    # Trigger sweep latest
    raw = _run_ssh(f"/home/niels/.conda/envs/binance_env/bin/python -c \"import sqlite3,json;c=sqlite3.connect('{SANDBOX_RESULTS_DIR}/trigger_sweep.db');cols=[d[1] for d in c.execute('PRAGMA table_info(trigger_results)').fetchall()];rows=c.execute('SELECT * FROM trigger_results ORDER BY rowid DESC LIMIT 20').fetchall();print(json.dumps([dict(zip(cols,r)) for r in rows]))\"", timeout=20)
    if raw:
        try:
            results["trigger_sweep"] = json.loads(raw)
        except Exception:
            pass
    # Tradier backtest CSV (latest rows)
    raw = _run_ssh(f"tail -20 {SANDBOX_RESULTS_DIR}/tradier_backtest_results.csv 2>/dev/null", timeout=10)
    if raw:
        results["tradier_csv_tail"] = raw
    # Ultimate winners
    raw = _run_ssh(f"tail -10 {SANDBOX_RESULTS_DIR}/ultimate_winners.jsonl 2>/dev/null", timeout=10)
    if raw:
        winners = []
        for line in raw.strip().split("\n"):
            try:
                winners.append(json.loads(line))
            except Exception:
                pass
        results["ultimate_winners"] = winners
    return results


async def _fetch_live_performance(yesterday_str: str) -> dict:
    """Analyze yesterday's live trading decisions across all accounts."""
    perf = {"crypto": {}, "tradier": {}, "totals": {"opens": 0, "closes": 0, "profit_closes": 0, "loss_closes": 0, "total_realized": 0.0}}
    for acct in ALL_ACCOUNTS:
        fpath = DECISION_DIR / f"decisions_{acct}_{yesterday_str}.jsonl"
        if not fpath.exists():
            continue
        decisions = []
        try:
            async with aiofiles.open(fpath, "r") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decisions.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            continue
        opens = [d for d in decisions if d.get("action", "") in ("OPEN", "QUICK_OPEN", "AUGMENT", "REENTRY", "HAIKU_AUGMENT", "COPILOT_OPEN_LONG", "COPILOT_OPEN_SHORT")]
        closes = [d for d in decisions if d.get("action", "") in ("CLOSE", "QUICK_CLOSE", "REDUCE", "PROFIT_TAKE", "STRONG_REDUCE")]
        wt_blocks = [d for d in decisions if "WT_" in d.get("reason", "")]
        copilot_actions = [d for d in decisions if "COPILOT" in d.get("action", "") or "copilot" in d.get("reason", "").lower()]
        haiku_actions = [d for d in decisions if "HAIKU" in d.get("action", "")]
        # Estimate P&L from close decisions
        realized = 0.0
        profit_count = 0
        loss_count = 0
        for c in closes:
            snap = c.get("snapshot") or c.get("indicators") or c.get("extra") or {}
            pnl = safe_fetch_float(snap.get("pnl") or snap.get("realized_pnl") or snap.get("pnl_usd") or 0, 0)
            gain = safe_fetch_float(snap.get("gain") or snap.get("pnl_pct") or 0, 0)
            realized += pnl
            if gain > 0:
                profit_count += 1
            elif gain < 0:
                loss_count += 1
        acct_data = {"total_decisions": len(decisions), "opens": len(opens), "closes": len(closes), "wt_blocks": len(wt_blocks), "copilot_actions": len(copilot_actions), "haiku_actions": len(haiku_actions), "realized_pnl": realized, "profit_closes": profit_count, "loss_closes": loss_count, "win_rate": (profit_count / max(profit_count + loss_count, 1)) * 100}
        bucket = "tradier" if acct in TRADIER_ACCOUNTS else "crypto"
        perf[bucket][acct] = acct_data
        perf["totals"]["opens"] += len(opens)
        perf["totals"]["closes"] += len(closes)
        perf["totals"]["profit_closes"] += profit_count
        perf["totals"]["loss_closes"] += loss_count
        perf["totals"]["total_realized"] += realized
    total_closes = perf["totals"]["profit_closes"] + perf["totals"]["loss_closes"]
    perf["totals"]["overall_wr"] = (perf["totals"]["profit_closes"] / max(total_closes, 1)) * 100
    return perf


async def _fetch_position_summary() -> dict:
    """Current position snapshot for all accounts."""
    summary = {}
    for acct in ALL_ACCOUNTS:
        long_val, short_val, long_count, short_count = 0.0, 0.0, 0, 0
        winners, losers = 0, 0
        biggest_winner_pct, biggest_loser_pct = 0.0, 0.0
        for side in ["long", "short"]:
            positions = await load_positions(acct, side)
            for pk, pos in positions.items():
                amt = abs(safe_fetch_float(pos.get("positionAmt") or pos.get("quantity", 0), 0))
                if amt == 0:
                    continue
                entry = safe_fetch_float(pos.get("entryPrice") or pos.get("entry_price") or pos.get("average_fill_price", 0), 0)
                mark = safe_fetch_float(pos.get("markPrice") or pos.get("mark_price", 0), 0)
                if entry <= 0 or mark <= 0:
                    continue
                val = amt * mark
                is_long = side == "long"
                pnl_pct = ((mark - entry) / entry * 100) if is_long else ((entry - mark) / entry * 100)
                if is_long:
                    long_val += val
                    long_count += 1
                else:
                    short_val += val
                    short_count += 1
                if pnl_pct > 0:
                    winners += 1
                    biggest_winner_pct = max(biggest_winner_pct, pnl_pct)
                else:
                    losers += 1
                    biggest_loser_pct = min(biggest_loser_pct, pnl_pct)
        ratio = long_val / max(short_val, 1)
        summary[acct] = {"long_count": long_count, "short_count": short_count, "long_value": round(long_val, 2), "short_value": round(short_val, 2), "ratio": round(ratio, 2), "winners": winners, "losers": losers, "biggest_winner_pct": round(biggest_winner_pct, 2), "biggest_loser_pct": round(biggest_loser_pct, 2)}
    return summary


async def _haiku_analyze_and_recommend(sandbox: dict, live: dict, positions: dict) -> Optional[dict]:
    """Send full analysis to Haiku for recommendations."""
    # Build compact summary for Haiku
    live_summary = json.dumps({k: v for k, v in live["totals"].items()}, default=str)
    tradier_summary = json.dumps({k: {"opens": v["opens"], "closes": v["closes"], "wr": round(v["win_rate"], 1), "pnl": round(v["realized_pnl"], 2)} for k, v in live.get("tradier", {}).items()}, default=str)
    crypto_summary = json.dumps({k: {"opens": v["opens"], "closes": v["closes"], "wr": round(v["win_rate"], 1), "pnl": round(v["realized_pnl"], 2)} for k, v in live.get("crypto", {}).items()}, default=str)
    position_summary = json.dumps({k: {"L": v["long_count"], "S": v["short_count"], "ratio": v["ratio"], "W": v["winners"], "big_W": v["biggest_winner_pct"], "big_L": v["biggest_loser_pct"]} for k, v in positions.items() if v["long_count"] + v["short_count"] > 0}, default=str)
    # Sandbox top results
    tourn_top5 = sandbox.get("tournament", [])[:5]
    tourn_str = json.dumps(tourn_top5, default=str)[:400]
    winners_str = json.dumps(sandbox.get("ultimate_winners", [])[:5], default=str)[:400]
    prompt = f"""You are a daily trading system analyst. It is 9am ET. Analyze yesterday's results and recommend adjustments BEFORE market open at 9:30am ET.

LIVE PERFORMANCE (yesterday):
- Totals: {live_summary}
- Tradier (stocks): {tradier_summary}
- Crypto: {crypto_summary}

CURRENT POSITIONS:
{position_summary}

SANDBOX BACKTEST TOP RESULTS:
- Tournament winners: {tourn_str}
- Ultimate winners: {winners_str}

RULES:
1. NEVER recommend closing losing positions (L/S ratio IS the hedge)
2. NEVER recommend per-symbol optimization (general rules first)
3. Focus on Tradier (stocks) — that's where the real money is
4. Identify: what worked yesterday? What failed? What should change?
5. Compare sandbox winners with live performance — are we using the right params?
6. Flag any config mismatches between sandbox winners and live config
7. Recommend specific config changes with exact values (e.g. "ENTRY_ZONE_LONG: 25 → 22")
8. Max 5 recommendations, ranked by expected impact

Respond ONLY with valid JSON:
{{"date": "YYYY-MM-DD",
  "grade": "A/B/C/D/F",
  "live_summary": "1-2 sentence summary of yesterday",
  "tradier_assessment": "1-2 sentences on stock performance",
  "crypto_assessment": "1-2 sentences on crypto performance",
  "sandbox_vs_live": "key discrepancies between backtest winners and live config",
  "recommendations": [
    {{"id": 1, "change": "CONFIG_PARAM: old → new", "file": "config_tradier.py", "reason": "why", "impact": "HIGH/MED/LOW", "safe_to_auto_apply": true/false}},
    ...
  ],
  "watchlist": ["symbols to watch today with brief reason"]
}}"""
    return await call_haiku(prompt)


async def _write_100_md(analysis: dict, sandbox: dict, live: dict, positions: dict):
    """Append daily analysis to 100.md."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    # Read existing 100.md to append
    existing = ""
    if REPORT_MD.exists():
        async with aiofiles.open(REPORT_MD, "r") as f:
            existing = await f.read()
    # Find insertion point — after the header section, before first ## PART
    # We'll prepend a daily section right after the QA notice
    daily_section = f"""
---

## DAILY ANALYSIS — {date_str} (auto-generated by copilot)

**Grade: {analysis.get('grade', 'N/A')}** | Generated: {now.strftime('%H:%M:%S')} UTC

### Yesterday's Live Performance
{analysis.get('live_summary', 'N/A')}

**Tradier:** {analysis.get('tradier_assessment', 'N/A')}
**Crypto:** {analysis.get('crypto_assessment', 'N/A')}

| Account | Opens | Closes | WR% | Realized P&L |
|---------|-------|--------|-----|-------------|
"""
    for system_name, system_data in [("TRADIER", live.get("tradier", {})), ("CRYPTO", live.get("crypto", {}))]:
        for acct, data in system_data.items():
            daily_section += f"| {acct} ({system_name}) | {data['opens']} | {data['closes']} | {data['win_rate']:.1f}% | ${data['realized_pnl']:.2f} |\n"
    totals = live.get("totals", {})
    daily_section += f"| **TOTAL** | **{totals.get('opens', 0)}** | **{totals.get('closes', 0)}** | **{totals.get('overall_wr', 0):.1f}%** | **${totals.get('total_realized', 0):.2f}** |\n"
    daily_section += f"""
### Current Positions

| Account | Longs | Shorts | L/S Ratio | Winners | Best W% | Worst L% |
|---------|-------|--------|-----------|---------|---------|----------|
"""
    for acct, data in positions.items():
        if data["long_count"] + data["short_count"] == 0:
            continue
        daily_section += f"| {acct} | {data['long_count']} (${data['long_value']:.0f}) | {data['short_count']} (${data['short_value']:.0f}) | {data['ratio']:.2f} | {data['winners']} | +{data['biggest_winner_pct']:.1f}% | {data['biggest_loser_pct']:.1f}% |\n"
    daily_section += f"""
### Sandbox vs Live
{analysis.get('sandbox_vs_live', 'N/A')}

### Recommendations (ranked by impact)

| # | Change | File | Impact | Auto-apply? | Reason |
|---|--------|------|--------|-------------|--------|
"""
    for rec in analysis.get("recommendations", []):
        safe = "YES" if rec.get("safe_to_auto_apply") else "NO"
        daily_section += f"| {rec.get('id', '?')} | {rec.get('change', '')} | {rec.get('file', '')} | {rec.get('impact', '')} | {safe} | {rec.get('reason', '')} |\n"
    watchlist = analysis.get("watchlist", [])
    if watchlist:
        daily_section += f"\n### Today's Watchlist\n"
        for w in watchlist:
            daily_section += f"- {w}\n"
    daily_section += "\n"
    # Prepend the daily section after the QA notice (line ~20)
    if f"## DAILY ANALYSIS — {date_str}" in existing:
        logger.info(f"[DAILY] Analysis for {date_str} already in 100.md, skipping append")
        return
    # Insert after "---" following QA notice
    insert_marker = "## PART 1:"
    if insert_marker in existing:
        idx = existing.index(insert_marker)
        updated = existing[:idx] + daily_section + existing[idx:]
    else:
        updated = existing + daily_section
    async with aiofiles.open(REPORT_MD, "w") as f:
        await f.write(updated)
    logger.info(f"[DAILY] Updated 100.md with {date_str} analysis")


async def _write_100_xlsx(analysis: dict, live: dict, positions: dict):
    """Update 100.xlsx with daily performance row."""
    try:
        import openpyxl
    except ImportError:
        logger.warning("[DAILY] openpyxl not installed, skipping xlsx update. Install: pip install openpyxl")
        return
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    try:
        if REPORT_XLS.exists():
            wb = openpyxl.load_workbook(str(REPORT_XLS))
        else:
            wb = openpyxl.Workbook()
        # Daily Performance sheet
        if "Daily Performance" in wb.sheetnames:
            ws = wb["Daily Performance"]
        else:
            ws = wb.create_sheet("Daily Performance", 0)
            ws.append(["Date", "Grade", "Total Opens", "Total Closes", "Win Rate %", "Realized PnL", "Tradier Opens", "Tradier Closes", "Tradier WR%", "Tradier PnL", "Crypto Opens", "Crypto Closes", "Crypto WR%", "Crypto PnL", "L/S Ratio (trb)", "Recommendations"])
        # Check if today already exists
        for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
            if row[0] == date_str:
                logger.info(f"[DAILY] {date_str} already in xlsx, skipping")
                wb.close()
                return
        totals = live.get("totals", {})
        # Tradier totals
        t_opens = sum(v["opens"] for v in live.get("tradier", {}).values())
        t_closes = sum(v["closes"] for v in live.get("tradier", {}).values())
        t_profit = sum(v["profit_closes"] for v in live.get("tradier", {}).values())
        t_loss = sum(v["loss_closes"] for v in live.get("tradier", {}).values())
        t_pnl = sum(v["realized_pnl"] for v in live.get("tradier", {}).values())
        t_wr = (t_profit / max(t_profit + t_loss, 1)) * 100
        # Crypto totals
        c_opens = sum(v["opens"] for v in live.get("crypto", {}).values())
        c_closes = sum(v["closes"] for v in live.get("crypto", {}).values())
        c_profit = sum(v["profit_closes"] for v in live.get("crypto", {}).values())
        c_loss = sum(v["loss_closes"] for v in live.get("crypto", {}).values())
        c_pnl = sum(v["realized_pnl"] for v in live.get("crypto", {}).values())
        c_wr = (c_profit / max(c_profit + c_loss, 1)) * 100
        trb_ratio = positions.get("trb", {}).get("ratio", 0)
        recs_str = "; ".join(r.get("change", "") for r in analysis.get("recommendations", [])[:3])
        ws.append([date_str, analysis.get("grade", ""), totals.get("opens", 0), totals.get("closes", 0), round(totals.get("overall_wr", 0), 1), round(totals.get("total_realized", 0), 2), t_opens, t_closes, round(t_wr, 1), round(t_pnl, 2), c_opens, c_closes, round(c_wr, 1), round(c_pnl, 2), trb_ratio, recs_str])
        # Recommendations sheet
        if "Recommendations" not in wb.sheetnames:
            rs = wb.create_sheet("Recommendations")
            rs.append(["Date", "#", "Change", "File", "Impact", "Auto-apply", "Reason", "Applied"])
        else:
            rs = wb["Recommendations"]
        for rec in analysis.get("recommendations", []):
            rs.append([date_str, rec.get("id", ""), rec.get("change", ""), rec.get("file", ""), rec.get("impact", ""), "YES" if rec.get("safe_to_auto_apply") else "NO", rec.get("reason", ""), ""])
        wb.save(str(REPORT_XLS))
        wb.close()
        logger.info(f"[DAILY] Updated 100.xlsx with {date_str} data")
    except Exception as e:
        logger.error(f"[DAILY] Failed to update xlsx: {e}")


async def _auto_apply_safe_recommendations(analysis: dict):
    """Auto-apply recommendations marked safe_to_auto_apply=True."""
    applied = []
    for rec in analysis.get("recommendations", []):
        if not rec.get("safe_to_auto_apply"):
            continue
        change = rec.get("change", "")
        target_file = rec.get("file", "")
        if not change or not target_file:
            continue
        # Parse "PARAM: old_val → new_val" format
        if "→" not in change and "->" not in change:
            continue
        sep = "→" if "→" in change else "->"
        parts = change.split(":")
        if len(parts) < 2:
            continue
        param = parts[0].strip()
        val_parts = parts[1].split(sep)
        if len(val_parts) < 2:
            continue
        new_val = val_parts[1].strip()
        # Safety: only allow numeric or boolean changes to config files
        target_path = BASE_PATH / target_file
        if not target_path.exists():
            logger.warning(f"[DAILY] Cannot apply {param}: {target_file} not found")
            continue
        if not target_file.startswith("config"):
            logger.info(f"[DAILY] Skipping auto-apply to non-config file: {target_file}")
            continue
        # Read the config file and find the param
        try:
            async with aiofiles.open(target_path, "r") as f:
                content = await f.read()
            lines = content.split("\n")
            found = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f"{param}") and ("=" in stripped or ":" in stripped):
                    # Found the line — build replacement
                    if "=" in stripped:
                        prefix = line[:line.index("=") + 1]
                    else:
                        prefix = line[:line.index(":") + 1]
                    # Determine value type
                    try:
                        if new_val.lower() in ("true", "false"):
                            typed_val = f" {new_val.capitalize()}"
                        elif "." in new_val:
                            typed_val = f" {float(new_val)}"
                        else:
                            typed_val = f" {int(new_val)}"
                    except ValueError:
                        typed_val = f" {new_val}"
                    # Preserve any inline comment
                    comment = ""
                    for cstart in ["  #", " #"]:
                        if cstart in line:
                            cidx = line.index(cstart)
                            comment = line[cidx:]
                            break
                    new_line = f"{prefix}{typed_val}{comment}"
                    if new_line != line:
                        lines[i] = new_line
                        found = True
                        logger.warning(f"[DAILY AUTO-APPLY] {target_file}:{i+1} — {param} changed to {new_val}")
                    break
            if found:
                async with aiofiles.open(target_path, "w") as f:
                    await f.write("\n".join(lines))
                applied.append(f"{param}={new_val} in {target_file}")
        except Exception as e:
            logger.error(f"[DAILY] Auto-apply failed for {param}: {e}")
    return applied


async def run_daily_analysis():
    """Full daily analysis: sandbox results + live performance → Haiku → 100.md + 100.xlsx → auto-apply."""
    global _last_daily_analysis_date
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    if _last_daily_analysis_date == date_str:
        return
    logger.info("=" * 60)
    logger.info(f"[DAILY] Starting 9am ET analysis for {date_str}")
    logger.info("=" * 60)
    _last_daily_analysis_date = date_str
    # 1. Fetch sandbox backtest results from server
    logger.info("[DAILY] Fetching sandbox results from server...")
    sandbox = _fetch_sandbox_results()
    logger.info(f"[DAILY] Sandbox: tournament={len(sandbox.get('tournament', []))} combos, winners={len(sandbox.get('ultimate_winners', []))}")
    # 2. Analyze yesterday's live performance
    yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
    logger.info(f"[DAILY] Analyzing live performance for {yesterday}...")
    live = await _fetch_live_performance(yesterday)
    logger.info(f"[DAILY] Live: {live['totals']['opens']} opens, {live['totals']['closes']} closes, WR={live['totals']['overall_wr']:.1f}%, PnL=${live['totals']['total_realized']:.2f}")
    # 3. Current position snapshot
    logger.info("[DAILY] Taking position snapshot...")
    positions = await _fetch_position_summary()
    # 4. Send to Haiku for analysis
    logger.info("[DAILY] Sending to Haiku for analysis...")
    analysis = await _haiku_analyze_and_recommend(sandbox, live, positions)
    if not analysis:
        logger.error("[DAILY] Haiku analysis failed — writing raw data only")
        analysis = {"grade": "?", "live_summary": "Haiku analysis unavailable", "tradier_assessment": "N/A", "crypto_assessment": "N/A", "sandbox_vs_live": "N/A", "recommendations": [], "watchlist": []}
    logger.info(f"[DAILY] Grade: {analysis.get('grade', '?')}, {len(analysis.get('recommendations', []))} recommendations")
    # 5. Write reports
    logger.info("[DAILY] Writing 100.md...")
    await _write_100_md(analysis, sandbox, live, positions)
    logger.info("[DAILY] Writing 100.xlsx...")
    await _write_100_xlsx(analysis, live, positions)
    # 6. Auto-apply safe recommendations
    safe_recs = [r for r in analysis.get("recommendations", []) if r.get("safe_to_auto_apply")]
    if safe_recs:
        logger.info(f"[DAILY] Auto-applying {len(safe_recs)} safe recommendations...")
        applied = await _auto_apply_safe_recommendations(analysis)
        if applied:
            logger.warning(f"[DAILY] Applied: {', '.join(applied)}")
    else:
        logger.info("[DAILY] No safe auto-apply recommendations")
    logger.info(f"[DAILY] Analysis complete. Grade: {analysis.get('grade', '?')}")
    logger.info("=" * 60)


# ═══════════════════════════════════════════════════════════════
# SECTION 5.5 — OUTPERFORMER TRACKING + REENTRY ENFORCEMENT
# ═══════════════════════════════════════════════════════════════
# The #1 fault of ez_ and tradier_ systems: they exit winners on a small dip
# then NEVER get back in. This section tracks outperformers, detects
# consolidation pullbacks, and forces reentry via ranking injection.

OUTPERFORMER_CHECK_INTERVAL = 120  # 2min during market, 5min off
CONSOLIDATION_MAX_PULLBACK_PCT = 5.0  # max pullback from high to still be "consolidating" (not reversing)
CONSOLIDATION_MIN_PRIOR_GAIN_PCT = 3.0  # must have been up 3%+ before we care about reentry
REENTRY_RANKING_BOOST = 85.0  # ranking points to inject (>70 = broadcast-worthy in ez_rankings)
_outperformer_tracker: Dict[str, dict] = {}  # {symbol: {system, peak_pct, peak_price, entry_price, last_held_ts, direction, status}}
_last_outperformer_check = 0.0


async def _scan_outperformers(redis_client: aioredis.Redis, system: str):
    """Scan positions for current outperformers and track them even after exit."""
    accounts = TRADIER_ACCOUNTS if system == "tradier" else CRYPTO_ACCOUNTS
    all_positions = await load_all_positions(accounts)
    now_ts = time.time()
    for acct, positions in all_positions.items():
        for pk, pos in positions.items():
            amt = abs(safe_fetch_float(pos.get("positionAmt") or pos.get("quantity", 0), 0))
            entry = safe_fetch_float(pos.get("entryPrice") or pos.get("entry_price") or pos.get("average_fill_price", 0), 0)
            mark = safe_fetch_float(pos.get("markPrice") or pos.get("mark_price", 0), 0)
            max_gain = safe_fetch_float(pos.get("max_gain") or pos.get("peak_gain", 0), 0)
            symbol = pk_symbol(pk) if pk_symbol(pk) else pk.split("_")[0]
            is_long = pk.endswith("_LONG")
            direction = "LONG" if is_long else "SHORT"
            if entry <= 0:
                continue
            if mark > 0 and amt > 0:
                pnl_pct = ((mark - entry) / entry * 100) if is_long else ((entry - mark) / entry * 100)
            else:
                pnl_pct = 0.0
            tracker_key = f"{symbol}_{direction}_{system}"
            # Track outperformers (gain > threshold)
            if pnl_pct >= CONSOLIDATION_MIN_PRIOR_GAIN_PCT or max_gain >= CONSOLIDATION_MIN_PRIOR_GAIN_PCT:
                existing = _outperformer_tracker.get(tracker_key, {})
                peak = max(pnl_pct, max_gain, existing.get("peak_pct", 0))
                _outperformer_tracker[tracker_key] = {"symbol": symbol, "system": system, "direction": direction, "peak_pct": peak, "peak_price": mark if pnl_pct == peak else existing.get("peak_price", mark), "entry_price": entry, "current_pct": pnl_pct, "account": acct, "position_key": pk, "currently_held": amt > 0, "last_held_ts": now_ts if amt > 0 else existing.get("last_held_ts", now_ts), "value": amt * mark if amt > 0 else 0, "status": "HELD" if amt > 0 else "EXITED"}
            # If position was closed (amt=0) but we were tracking it, mark as exited
            if amt == 0 and tracker_key in _outperformer_tracker:
                tracked = _outperformer_tracker[tracker_key]
                if tracked.get("status") == "HELD":
                    tracked["status"] = "EXITED"
                    tracked["currently_held"] = False
                    tracked["exit_ts"] = now_ts
                    logger.warning(f"[OUTPERFORMER] {symbol} {direction} EXITED after peak {tracked['peak_pct']:.1f}% — watching for reentry")


async def _detect_consolidation_reentries(redis_client: aioredis.Redis) -> List[dict]:
    """Detect exited outperformers that are consolidating (not reversing) and should be reentered."""
    reentry_candidates = []
    now_ts = time.time()
    for key, tracked in list(_outperformer_tracker.items()):
        if tracked.get("status") != "EXITED":
            continue
        # Must have been out for at least 2 minutes but less than 4 hours
        exit_ts = tracked.get("exit_ts", 0)
        time_since_exit = now_ts - exit_ts
        if time_since_exit < 120 or time_since_exit > 14400:
            # Too soon (might still be closing) or too old (opportunity passed)
            if time_since_exit > 14400:
                tracked["status"] = "EXPIRED"
            continue
        symbol = tracked["symbol"]
        system = tracked["system"]
        direction = tracked["direction"]
        peak_pct = tracked["peak_pct"]
        entry_price = tracked["entry_price"]
        # Get current price
        current_price = await get_price_from_redis(redis_client, symbol, system)
        if current_price <= 0:
            continue
        # Calculate where price is relative to entry
        if direction == "LONG":
            current_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            current_pct = ((entry_price - current_price) / entry_price) * 100
        # Consolidation = price pulled back from peak but still in profit or only slightly below
        pullback_from_peak = peak_pct - current_pct
        # Still favorable: price hasn't reversed more than CONSOLIDATION_MAX_PULLBACK_PCT from peak
        if pullback_from_peak <= CONSOLIDATION_MAX_PULLBACK_PCT and current_pct > -1.0:
            # Check indicators for confirmation
            indicators = await get_indicators_from_redis(redis_client, symbol, system)
            # Basic confirmation: stoch not exhausted against our direction
            k15 = safe_fetch_float(indicators.get("stoch_k_15m", 50), 50)
            wt1_15m = safe_fetch_float(indicators.get("wt1_15m", 0), 0)
            # Don't reenter LONG if overbought, or SHORT if oversold
            if direction == "LONG" and k15 > 85:
                continue
            if direction == "SHORT" and k15 < 15:
                continue
            reentry_candidates.append({"symbol": symbol, "system": system, "direction": direction, "peak_pct": peak_pct, "current_pct": current_pct, "pullback": pullback_from_peak, "price": current_price, "entry_price": entry_price, "account": tracked.get("account", ""), "position_key": tracked.get("position_key", ""), "k15": k15, "wt1_15m": wt1_15m, "time_since_exit_min": time_since_exit / 60})
    return reentry_candidates


async def _inject_into_rankings(redis_client: aioredis.Redis, symbol: str, system: str, direction: str, boost_score: float, reason: str):
    """Inject a symbol into rankings via Redis so ez_rankings/tradier_rankings pick it up."""
    now_iso = datetime.now(timezone.utc).isoformat()
    if system == "tradier":
        # For tradier: write to tradier rankings boost key
        boost_data = {"symbol": symbol, "direction": direction, "boost_score": boost_score, "reason": reason, "timestamp": now_iso, "source": "copilot"}
        await redis_client.set(f"copilot_ranking_boost:{symbol}", json.dumps(boost_data), ex=1800)
        # Also publish to tradier rankings channel for immediate pickup
        signal = {"symbol": symbol, "event_type": f"COPILOT_REENTRY_{direction}", "action": "BUY" if direction == "LONG" else "SELL", "ranking_points": boost_score, "source": "copilot_outperformer", "reason": reason, "timestamp": now_iso, "priority": "HIGH"}
        await redis_client.publish("tradier_rankings_signal", json.dumps(signal))
        logger.warning(f"[OUTPERFORMER] Injected {symbol} {direction} into tradier rankings (boost={boost_score:.0f}): {reason}")
    else:
        # For crypto: use ez_rankings signal channel
        signal = {"symbol": symbol, "event_type": f"COPILOT_REENTRY_{direction}", "action": "BUY" if direction == "LONG" else "SELL", "ranking_points": boost_score, "final_score_norm": boost_score, "source": "copilot_outperformer", "reason": reason, "timestamp": now_iso}
        await redis_client.publish("ez_rankings_signal", json.dumps(signal))
        # Also set direct key for ez_positions_quick to read
        await redis_client.set(f"copilot_ranking_boost:{symbol}", json.dumps(signal), ex=1800)
        logger.warning(f"[OUTPERFORMER] Injected {symbol} {direction} into crypto rankings (boost={boost_score:.0f}): {reason}")


async def _execute_reentries(redis_client: aioredis.Redis, candidates: List[dict]):
    """Execute reentries for consolidating outperformers."""
    for cand in candidates[:5]:  # Max 5 reentries per cycle
        symbol = cand["symbol"]
        system = cand["system"]
        direction = cand["direction"]
        pk = cand.get("position_key", "")
        account = cand.get("account", "trb" if system == "tradier" else "ang")
        price = cand["price"]
        if not can_intervene(pk):
            continue
        if not can_open_new():
            continue
        # 1. Inject into rankings for sustained attention
        reason = f"Outperformer reentry: peaked +{cand['peak_pct']:.1f}%, pulled back {cand['pullback']:.1f}%, now +{cand['current_pct']:.1f}%"
        await _inject_into_rankings(redis_client, symbol, system, direction, REENTRY_RANKING_BOOST, reason)
        # 2. Direct reentry order via copilot channels
        now_iso = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if system == "tradier":
            if not is_market_hours():
                logger.info(f"[OUTPERFORMER] Market closed, skipping tradier reentry for {symbol}")
                continue
            cmd = {"position_key": pk or f"{account}:{symbol}_{'LONG' if direction == 'LONG' else 'SHORT'}", "account_key": account, "action": "OPEN", "direction": direction, "reason": f"COPILOT_REENTRY: {reason}", "price": price, "timestamp": now_iso, "source": "copilot_outperformer"}
            await redis_client.publish("copilot_tradier_orders", json.dumps(cmd))
        else:
            cmd = {"position_key": pk or f"{account}:{symbol}_{'LONG' if direction == 'LONG' else 'SHORT'}", "account_key": account, "action": "AUGMENT", "reason": f"COPILOT_REENTRY: {reason}", "price": price, "qty": COPILOT_MAX_ORDER_CRYPTO / price if price > 0 else 0, "timestamp": now_iso, "source": "copilot_outperformer"}
            await redis_client.publish("haiku_agent_augments" if direction == "LONG" else "copilot_crypto_orders", json.dumps(cmd))
        record_intervention(pk or f"{symbol}_{direction}")
        record_open()
        # Mark as reentered
        tracker_key = f"{symbol}_{direction}_{system}"
        if tracker_key in _outperformer_tracker:
            _outperformer_tracker[tracker_key]["status"] = "REENTERED"
        logger.warning(f"[OUTPERFORMER REENTRY] {symbol} {direction} @ {price:.4f} — {reason}")
        # Log decision
        decision_record = {"timestamp": now_iso, "position_key": pk, "account": account, "action": f"COPILOT_REENTRY_{direction}", "reason": reason, "extra": {"peak_pct": cand["peak_pct"], "current_pct": cand["current_pct"], "pullback": cand["pullback"], "k15": cand["k15"]}}
        try:
            log_path = DECISION_DIR / f"decisions_{account}_{today}.jsonl"
            async with aiofiles.open(log_path, "a") as f:
                await f.write(json.dumps(decision_record, default=str) + "\n")
        except Exception:
            pass


async def run_outperformer_cycle(redis_client: aioredis.Redis):
    """Full outperformer scan → consolidation detection → reentry."""
    # Scan both systems
    await _scan_outperformers(redis_client, "tradier")
    await _scan_outperformers(redis_client, "crypto")
    held = sum(1 for t in _outperformer_tracker.values() if t.get("status") == "HELD")
    exited = sum(1 for t in _outperformer_tracker.values() if t.get("status") == "EXITED")
    if exited > 0:
        candidates = await _detect_consolidation_reentries(redis_client)
        if candidates:
            # During tradier priority, prioritize stock reentries
            if is_tradier_priority_window():
                candidates.sort(key=lambda c: (0 if c["system"] == "tradier" else 1, -c["peak_pct"]))
            else:
                candidates.sort(key=lambda c: -c["peak_pct"])
            logger.info(f"[OUTPERFORMER] Tracking {held} held, {exited} exited, {len(candidates)} reentry candidates")
            for c in candidates[:3]:
                logger.info(f"  → {c['symbol']} {c['direction']} ({c['system']}): peaked +{c['peak_pct']:.1f}%, now +{c['current_pct']:.1f}%, pullback {c['pullback']:.1f}%, exit {c['time_since_exit_min']:.0f}m ago")
            await _execute_reentries(redis_client, candidates)
    elif held > 0:
        logger.debug(f"[OUTPERFORMER] Tracking {held} outperformers, none exited yet")


# ═══════════════════════════════════════════════════════════════
# SECTION 5.6 — MARKET-WIDE OUTLIER SCANNER (Tradier equities)
# ═══════════════════════════════════════════════════════════════
# Scans the broad equities universe and submits allowlisted movers as ranking
# hints. It must never mutate the authoritative symbols_tradier.json file.

# Top ~600 liquid US equities to scan (S&P500 + popular mid-caps)
# Tradier batch quotes allow up to ~100 symbols per call
SCAN_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ADBE",
    "CRM", "ORCL", "AMD", "INTC", "QCOM", "TXN", "MU", "LRCX", "AMAT", "KLAC",
    "ASML", "TSM", "ARM", "SMCI", "MRVL", "SNPS", "CDNS", "NXPI", "ON", "SWKS",
    # Software / Cloud
    "SNOW", "CRWD", "PANW", "DDOG", "ZS", "NET", "WDAY", "SHOP", "SPOT", "TTD",
    "PATH", "DUOL", "RBLX", "RDDT", "DASH", "PINS", "SNAP", "ROKU", "FIVN", "QLYS",
    "SAP", "IBM", "NOW", "INTU", "TEAM", "HUBS", "ZM", "OKTA", "MNDY", "VEEV",
    # Semis / Hardware
    "DELL", "HPQ", "HPE", "ANET", "JNPR", "CSCO", "KEYS", "ZBRA", "OLED", "LSCC",
    # Consumer / Retail
    "AMZN", "COST", "WMT", "HD", "LOW", "TGT", "SBUX", "MCD", "NKE", "LULU",
    "TJX", "ROST", "DG", "DLTR", "ULTA", "EL", "PG", "CL", "CLX", "KO", "PEP",
    "KDP", "MNST", "STZ", "BUD", "MO", "PM", "DEO",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BK", "SCHW", "BLK", "BX", "KKR",
    "APO", "COIN", "HOOD", "MA", "V", "AXP", "PYPL", "SQ", "AFRM", "SOFI",
    "CME", "ICE", "NDAQ", "CBOE", "MCO", "SPGI",
    # Healthcare
    "UNH", "JNJ", "PFE", "LLY", "MRK", "ABBV", "ABT", "TMO", "DHR", "GILD",
    "AMGN", "BIIB", "REGN", "VRTX", "MRNA", "ISRG", "MDT", "SYK", "BSX", "ZBH",
    "CVS", "HCA", "CI", "ELV", "HUM",
    # Energy
    "XOM", "CVX", "COP", "OXY", "SLB", "HAL", "EOG", "PXD", "DVN", "MPC",
    "PSX", "VLO", "APA", "FANG",
    # Industrials
    "BA", "LMT", "RTX", "NOC", "GD", "GE", "HON", "CAT", "DE", "MMM",
    "FDX", "UPS", "UNP", "CSX", "DAL", "UAL", "AAL", "LUV",
    "BWXT", "HII", "RKLB", "ASTS", "JOBY",
    # Materials / Commodities
    "NEM", "GOLD", "FCX", "COPX", "AA", "X", "CLF",
    # ETFs (big movers = market signals)
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLI", "XLU",
    "GLD", "SLV", "USO", "GDX", "IBIT", "BITO",
    # Crypto-adjacent
    "MSTR", "MARA", "RIOT", "CLSK", "BITF", "HIVE", "BTBT",
    # Meme / High-vol
    "GME", "AMC", "PLTR", "SOFI", "RIVN", "LCID", "NIO", "XPEV", "LI",
    # Telecom / Media
    "T", "VZ", "TMUS", "NFLX", "DIS", "CMCSA", "WBD", "PARA",
    # Auto
    "TSLA", "GM", "F", "RIVN", "LCID", "TM", "HMC",
    # Real estate / Utilities
    "AMT", "PLD", "EQIX", "CCI", "O", "SPG", "NEE", "DUK", "SO", "D",
    # China / International
    "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI", "TCEHY",
    # Quantum / Speculative
    "QBTS", "QUBT", "IONQ", "RGTI",
    # Space / Defense
    "RKLB", "ASTS", "JOBY", "LMT", "RTX", "BWXT", "CRWV",
    # Other notables
    "LYFT", "UBER", "ABNB", "DKNG", "PENN", "WYNN", "LVS", "MGM",
    "INOD", "ZETA", "USAR", "DIME", "APO", "BK",
]
# Deduplicate
SCAN_UNIVERSE = list(dict.fromkeys(SCAN_UNIVERSE))

MARKET_SCAN_INTERVAL = 900  # 15min during market hours
OUTLIER_THRESHOLD_PCT = 3.0  # abs % change to qualify as outlier
MAX_NEW_SYMBOLS_PER_SCAN = 5  # max symbols to add per scan
COPILOT_SYMBOLS_TTL_HOURS = 48  # remove copilot-added symbols after 48h if no longer outlier
COPILOT_SYMBOLS_FILE = BASE_PATH / "data" / "copilot_added_symbols.json"  # tracks what we added + when
_last_market_scan = 0.0
_last_symbol_cleanup = 0.0
_market_scan_results: Dict[str, dict] = {}


async def scan_market_outliers(redis_client: aioredis.Redis) -> List[dict]:
    """Scan the broad equities market for biggest movers via Tradier batch quotes."""
    try:
        from tradier_api import TradierAPIClient
        from config_tradier import TradierConfig
        tconfig = TradierConfig()
        api = TradierAPIClient(tconfig)
        await api.switch_account("trb")
    except Exception as e:
        logger.error(f"[SCANNER] Cannot init TradierAPIClient: {e}")
        return []
    all_quotes = {}
    # Batch in groups of 80 (Tradier limit ~100 per call)
    batch_size = 80
    for i in range(0, len(SCAN_UNIVERSE), batch_size):
        batch = SCAN_UNIVERSE[i:i + batch_size]
        try:
            quotes = await api.get_quotes(batch)
            all_quotes.update(quotes)
        except Exception as e:
            logger.error(f"[SCANNER] Quote batch {i}-{i+batch_size} failed: {e}")
        await asyncio.sleep(0.3)  # Rate limit
    if not all_quotes:
        return []
    # Calculate % change and find outliers
    outliers = []
    for symbol, quote in all_quotes.items():
        try:
            last = float(quote.get("last", 0) or 0)
            prev_close = float(quote.get("prevclose", 0) or quote.get("open", 0) or 0)
            volume = int(quote.get("volume", 0) or 0)
            avg_volume = int(quote.get("average_volume", 0) or 0)
            if last <= 0 or prev_close <= 0:
                continue
            change_pct = ((last - prev_close) / prev_close) * 100
            rel_volume = volume / max(avg_volume, 1)
            _market_scan_results[symbol] = {"symbol": symbol, "price": last, "change_pct": change_pct, "volume": volume, "rel_volume": rel_volume, "scanned_at": datetime.now(timezone.utc).isoformat()}
            if abs(change_pct) >= OUTLIER_THRESHOLD_PCT:
                outliers.append({"symbol": symbol, "price": last, "change_pct": change_pct, "direction": "UP" if change_pct > 0 else "DOWN", "volume": volume, "rel_volume": rel_volume})
        except (ValueError, TypeError):
            continue
    # Sort by absolute change
    outliers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    if outliers:
        logger.info(f"[SCANNER] Found {len(outliers)} outliers (>{OUTLIER_THRESHOLD_PCT}% move) from {len(all_quotes)} scanned")
        for o in outliers[:10]:
            logger.info(f"  {'🔥' if o['direction'] == 'UP' else '❄️'} {o['symbol']}: {o['change_pct']:+.2f}% @ ${o['price']:.2f} (vol: {o['rel_volume']:.1f}x)")
    return outliers


def _load_copilot_symbols() -> Dict[str, dict]:
    """Load the copilot-added symbols tracker. {symbol: {added_at, reason, last_outlier_at}}"""
    if COPILOT_SYMBOLS_FILE.exists():
        try:
            with open(COPILOT_SYMBOLS_FILE, "r") as f:
                return json.loads(f.read())
        except Exception:
            pass
    return {}


def _save_copilot_symbols(tracker: Dict[str, dict]):
    COPILOT_SYMBOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(COPILOT_SYMBOLS_FILE, "w") as f:
            f.write(json.dumps(tracker, indent=2))
    except Exception as e:
        logger.error(f"[SCANNER] Failed to save copilot symbols tracker: {e}")


async def inject_outliers_into_symbols(outliers: List[dict]):
    """Outliers are ranking hints only; the master trading allowlist is immutable."""
    return


async def cleanup_stale_copilot_symbols():
    """The copilot may not remove symbols from the master trading allowlist."""
    return


async def inject_outliers_into_rankings(redis_client: aioredis.Redis, outliers: List[dict]):
    """Inject outliers into tradier rankings so they get prioritized for trading."""
    for o in outliers[:10]:
        symbol = o["symbol"]
        change = o["change_pct"]
        direction = "LONG" if change > 0 else "SHORT"
        # Boost score proportional to move size (bigger move = higher score)
        boost = min(50.0 + abs(change) * 5.0, 95.0)
        reason = f"Market outlier: {change:+.1f}% today, vol={o['rel_volume']:.1f}x avg"
        await _inject_into_rankings(redis_client, symbol, "tradier", direction, boost, reason)


async def run_market_scan(redis_client: aioredis.Redis):
    """Full market scan cycle: quote universe → find outliers → inject into symbols + rankings."""
    if not is_market_hours():
        return
    logger.info(f"[SCANNER] Starting market-wide scan of {len(SCAN_UNIVERSE)} equities...")
    outliers = await scan_market_outliers(redis_client)
    if outliers:
        await inject_outliers_into_symbols(outliers)
        await inject_outliers_into_rankings(redis_client, outliers)
    logger.info(f"[SCANNER] Scan complete: {len(outliers)} outliers found")


# ═══════════════════════════════════════════════════════════════
# SECTION 5.7 — STATUS REPORT
# ═══════════════════════════════════════════════════════════════

async def write_status_report():
    """Write periodic status to disk for human review."""
    report_path = BASE_PATH / "COPILOT_STATUS.md"
    now = datetime.now(timezone.utc)
    _prune_hourly_list(_interventions_this_hour)
    _prune_hourly_list(_copilot_opens_this_hour)
    recent_anomalies = [a for a in _anomaly_history if (now - datetime.fromisoformat(a["detected_at"].replace("Z", "+00:00"))).total_seconds() < 3600]
    recent_missed = list(_missed_trades)[-20:]
    lines = [
        f"# Copilot Status — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "",
        f"**Market Hours:** {'YES' if is_market_hours() else 'NO'} | **Tradier Priority:** {'YES' if is_tradier_priority_window() else 'NO'}",
        f"**Interventions this hour:** {len(_interventions_this_hour)} / {MAX_INTERVENTIONS_PER_HOUR}",
        f"**New opens this hour:** {len(_copilot_opens_this_hour)} / {COPILOT_MAX_POSITIONS}",
        "",
        "## Recent Anomalies (last 1h)",
        "",
    ]
    high_anomalies = [a for a in recent_anomalies if a.get("severity") == "HIGH"]
    if high_anomalies:
        for a in high_anomalies[-10:]:
            lines.append(f"- **{a['type']}** [{a.get('system', '?')}] {a.get('pk', a.get('account', ''))} — {a.get('detected_at', '')[:19]}")
    else:
        lines.append("_None_")
    lines.extend(["", "## Missed Trades (trader comparison)", ""])
    if recent_missed:
        for m in recent_missed[-10:]:
            lines.append(f"- **{m['symbol']}** {m['direction']} — {m['reason']} (conf={m['signal']['confidence']:.2f})")
    else:
        lines.append("_None_")
    lines.extend(["", "## Trader Signals (last batch)", ""])
    recent_signals = list(_trader_signals)[-10:]
    if recent_signals:
        for s in recent_signals:
            lines.append(f"- **{s['symbol']}** {s['direction']} — {s['type']} ({s['count']} traders, conf={s['confidence']:.2f})")
    else:
        lines.append("_None_")
    lines.extend(["", "## Outperformer Tracker", ""])
    held = {k: v for k, v in _outperformer_tracker.items() if v.get("status") == "HELD"}
    exited = {k: v for k, v in _outperformer_tracker.items() if v.get("status") == "EXITED"}
    reentered = {k: v for k, v in _outperformer_tracker.items() if v.get("status") == "REENTERED"}
    lines.append(f"**Held:** {len(held)} | **Watching for reentry:** {len(exited)} | **Reentered:** {len(reentered)}")
    if held:
        lines.append("")
        for k, v in sorted(held.items(), key=lambda x: -x[1].get("peak_pct", 0))[:10]:
            lines.append(f"- HELD: **{v['symbol']}** {v['direction']} ({v['system']}) peak +{v['peak_pct']:.1f}%, now +{v.get('current_pct', 0):.1f}%")
    if exited:
        lines.append("")
        for k, v in sorted(exited.items(), key=lambda x: -x[1].get("peak_pct", 0))[:10]:
            mins_ago = (time.time() - v.get("exit_ts", 0)) / 60
            lines.append(f"- WATCHING: **{v['symbol']}** {v['direction']} ({v['system']}) peaked +{v['peak_pct']:.1f}%, exited {mins_ago:.0f}m ago")
    # Copilot-added symbols (temporary)
    tracker = _load_copilot_symbols()
    if tracker:
        lines.extend(["", "## Copilot-Added Symbols (temporary)", ""])
        for sym, info in sorted(tracker.items(), key=lambda x: x[1].get("added_at", ""), reverse=True):
            added = info.get("added_at", "?")[:16]
            last_outlier = info.get("last_outlier_at", "?")[:16]
            reason = info.get("reason", "")
            lines.append(f"- **{sym}** added {added}, last outlier {last_outlier} — {reason}")
    # Market scanner results
    if _market_scan_results:
        lines.extend(["", "## Market Scanner (top movers today)", ""])
        sorted_movers = sorted(_market_scan_results.values(), key=lambda x: abs(x.get("change_pct", 0)), reverse=True)[:15]
        for m in sorted_movers:
            arrow = "UP" if m["change_pct"] > 0 else "DN"
            lines.append(f"- **{m['symbol']}** {m['change_pct']:+.1f}% ({arrow}) @ ${m['price']:.2f} vol={m.get('rel_volume', 0):.1f}x")
    # Supervisor status
    lines.extend(["", "## Supervisor", ""])
    active_agents = {k: v for k, v in _opus_agents_running.items() if time.time() - v < 300}
    lines.append(f"**Active Opus agents:** {len(active_agents)} / {OPUS_MAX_CONCURRENT}")
    for key, started in active_agents.items():
        age_min = (time.time() - started) / 60
        lines.append(f"- `{key}` running {age_min:.0f}min")
    now_ts = time.time()
    recent_issues = [i for i in _supervisor_issues if (now_ts - datetime.fromisoformat(i["detected_at"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp() if "detected_at" in i else 9999) < 3600]
    if recent_issues:
        lines.append("")
        lines.append(f"**Issues (last 1h):** {len(recent_issues)}")
        for i in list(recent_issues)[-5:]:
            lines.append(f"- [{i['severity']}] {i['message']}")
    lines.append("")
    try:
        async with aiofiles.open(report_path, "w") as f:
            await f.write("\n".join(lines))
    except Exception as e:
        logger.error(f"Failed to write status report: {e}")


# ═══════════════════════════════════════════════════════════════
# SECTION 6 — SUPERVISOR: PROCESS HEALTH + OPUS AGENT DISPATCH
# ═══════════════════════════════════════════════════════════════

# Scripts that MUST be running during trading hours (MacBook local)
CRITICAL_SCRIPTS_CRYPTO = ["ez_prices.py", "ez_indicators.py", "ez_klines.py", "ez_rankings.py", "ez_market_data.py", "ez_share_ind.py"]  # ez_positions_watchdog removed — ez_manage embeds positions_service
CRITICAL_SCRIPTS_TRADIER = ["tradier_prices.py", "tradier_indicators.py", "tradier_rankings.py", "tradier_positions.py"]
CRITICAL_SCRIPTS_MANAGE_CRYPTO = ["ez_manage.py"]
CRITICAL_SCRIPTS_MANAGE_TRADIER = ["tradier_manage.py"]
SUPERVISOR_CHECK_INTERVAL = 60  # check every 60s
OPUS_AGENT_COOLDOWN = 600  # 10min between Opus agents for same issue
OPUS_MAX_CONCURRENT = 2  # max simultaneous Opus agents
_last_supervisor_check = 0.0
_opus_agents_running: Dict[str, float] = {}  # {issue_key: started_at}
_opus_cooldowns: Dict[str, float] = {}  # {issue_key: last_dispatched_at}
_supervisor_issues: deque = deque(maxlen=50)


def _check_process_running(script_name: str) -> bool:
    """Check if a script is running locally via pgrep."""
    import subprocess
    try:
        result = subprocess.run(["pgrep", "-f", f"python.*{script_name}"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _check_process_running_with_account(script_name: str, account: str) -> bool:
    import subprocess
    try:
        result = subprocess.run(["pgrep", "-f", f"python.*{script_name}.*{account}"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


async def check_system_health() -> List[dict]:
    """Check all critical scripts are running. Returns list of issues."""
    issues = []
    market_open = is_market_hours()
    # Always-on crypto services
    for script in CRITICAL_SCRIPTS_CRYPTO:
        if not _check_process_running(script):
            issues.append({"type": "PROCESS_DOWN", "script": script, "system": "crypto", "severity": "HIGH", "message": f"{script} is NOT running"})
    # Always-on tradier services
    for script in CRITICAL_SCRIPTS_TRADIER:
        if not _check_process_running(script):
            sev = "HIGH" if market_open else "LOW"
            issues.append({"type": "PROCESS_DOWN", "script": script, "system": "tradier", "severity": sev, "message": f"{script} is NOT running" + (" (MARKET OPEN!)" if market_open else "")})
    # Per-account manage scripts
    for acct in CRYPTO_ACCOUNTS:
        if not _check_process_running_with_account("ez_manage.py", acct):
            issues.append({"type": "PROCESS_DOWN", "script": f"ez_manage.py --account {acct}", "system": "crypto", "severity": "HIGH", "message": f"ez_manage.py for {acct} is NOT running"})
    for acct in TRADIER_ACCOUNTS:
        if not _check_process_running_with_account("tradier_manage.py", acct):
            sev = "CRITICAL" if market_open else "MEDIUM"
            issues.append({"type": "PROCESS_DOWN", "script": f"tradier_manage.py --accounts {acct}", "system": "tradier", "severity": sev, "message": f"tradier_manage.py for {acct} is NOT running" + (" (MARKET OPEN!)" if market_open else "")})
    # Redis connectivity
    try:
        r = aioredis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
        await r.ping()
        await r.aclose()
    except Exception:
        issues.append({"type": "REDIS_DOWN", "script": "redis", "system": "infra", "severity": "CRITICAL", "message": "Local Redis (port 6379) is NOT responding"})
    # Log file freshness — if log hasn't been written in 10min, script may be frozen
    for script in CRITICAL_SCRIPTS_CRYPTO + CRITICAL_SCRIPTS_TRADIER:
        base = script.replace(".py", "")
        log_path = LOG_DIR / f"{base}.log"
        if log_path.exists():
            age = time.time() - log_path.stat().st_mtime
            if age > 600 and _check_process_running(script):
                issues.append({"type": "PROCESS_FROZEN", "script": script, "system": "crypto" if "ez_" in script else "tradier", "severity": "MEDIUM", "message": f"{script} running but log stale ({age/60:.0f}min)"})
    for issue in issues:
        issue["detected_at"] = datetime.now(timezone.utc).isoformat()
        _supervisor_issues.append(issue)
    return issues


def _try_restart_script(script_name: str):
    """Attempt to restart a crashed script via the watchdog."""
    import subprocess
    watchdog = str(BASE_PATH / "run_with_watchdog.sh")
    try:
        subprocess.Popen(["bash", watchdog, script_name], cwd=str(BASE_PATH), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.warning(f"[SUPERVISOR] Restarted {script_name} via watchdog")
    except Exception as e:
        logger.error(f"[SUPERVISOR] Failed to restart {script_name}: {e}")


def _try_restart_script_with_args(script_name: str, *args):
    """Restart a script with arguments via watchdog."""
    import subprocess
    watchdog = str(BASE_PATH / "run_with_watchdog.sh")
    cmd = ["bash", watchdog, script_name] + list(args)
    try:
        subprocess.Popen(cmd, cwd=str(BASE_PATH), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.warning(f"[SUPERVISOR] Restarted {script_name} {' '.join(args)} via watchdog")
    except Exception as e:
        logger.error(f"[SUPERVISOR] Failed to restart {script_name} {' '.join(args)}: {e}")


def spawn_opus_agent(issue_key: str, prompt: str) -> Optional[int]:
    """Spawn a Claude Opus agent in the background to diagnose/fix an issue.
    Returns the subprocess PID, or None if blocked by cooldown/limit/killswitch/budget."""
    import subprocess, json as _json
    if os.environ.get("OPUS_SPAWN_ENABLED", "0") != "1":
        logger.warning(f"[SUPERVISOR] Opus spawn DISABLED (OPUS_SPAWN_ENABLED!=1) — skipping '{issue_key}'")
        return None
    now = time.time()
    _budget_path = LOG_DIR / "opus_spawn_budget.json"
    _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        _b = _json.loads(_budget_path.read_text()) if _budget_path.exists() else {}
    except Exception:
        _b = {}
    _daily_count = int(_b.get(_today, 0))
    _daily_cap = int(os.environ.get("OPUS_SPAWN_DAILY_CAP", "8"))
    if _daily_count >= _daily_cap:
        logger.warning(f"[SUPERVISOR] Opus daily budget {_daily_cap} exhausted ({_daily_count}) — skipping '{issue_key}'")
        return None
    if now - _opus_cooldowns.get(issue_key, 0) < OPUS_AGENT_COOLDOWN:
        return None
    active = {k: v for k, v in _opus_agents_running.items() if now - v < 300}
    _opus_agents_running.clear()
    _opus_agents_running.update(active)
    if len(_opus_agents_running) >= OPUS_MAX_CONCURRENT:
        logger.info(f"[SUPERVISOR] Opus agent limit reached ({OPUS_MAX_CONCURRENT}), queuing {issue_key}")
        return None
    _b[_today] = _daily_count + 1
    try:
        _budget_path.write_text(_json.dumps(_b))
    except Exception:
        pass
    # Spawn agent
    log_file = LOG_DIR / f"opus_agent_{issue_key.replace(' ', '_')[:40]}_{datetime.now(timezone.utc).strftime('%H%M')}.log"
    full_prompt = f"""You are a trading system supervisor agent. Diagnose and fix the following issue.

ISSUE: {prompt}

WORKING DIRECTORY: {BASE_PATH}
PYTHON: /opt/anaconda3/envs/binance_env/bin/python

RULES:
1. NEVER close positions at a loss. STRICT_NO_LOSS on ALL accounts.
2. NEVER use git reset/restore/checkout. Only move forward.
3. NEVER delete position dict entries.
4. NEVER zero entry_price, max_gain, opened_at.
5. Check LOCKED_FILES.md before editing any file.
6. Backup before editing: cp <file> backups/before_<desc>_<timestamp>.py
7. If the fix requires restarting a service, use: bash run_with_watchdog.sh <script.py> [args]
8. Log everything you do — the human will review your actions.
9. If you cannot fix it, write a clear summary to {log_file} explaining what you found.

Diagnose the issue, attempt a fix, and report what you did."""
    try:
        proc = subprocess.Popen(["claude", "-p", full_prompt, "--model", "opus", "--output-format", "text", "--dangerously-skip-permissions", "--no-session-persistence"], cwd=str(BASE_PATH), stdout=open(log_file, "w"), stderr=subprocess.STDOUT)
        _opus_agents_running[issue_key] = now
        _opus_cooldowns[issue_key] = now
        logger.warning(f"[SUPERVISOR] Spawned Opus agent for '{issue_key}' (PID {proc.pid}, log: {log_file})")
        return proc.pid
    except FileNotFoundError:
        logger.error("[SUPERVISOR] 'claude' CLI not found — cannot spawn Opus agents. Install: https://claude.ai/download")
        return None
    except Exception as e:
        logger.error(f"[SUPERVISOR] Failed to spawn Opus agent: {e}")
        return None


async def handle_supervisor_issues(issues: List[dict]):
    """Handle detected issues: auto-restart simple ones, Opus agent for complex ones."""
    for issue in issues:
        itype = issue["type"]
        script = issue.get("script", "")
        severity = issue.get("severity", "LOW")
        issue_key = f"{itype}:{script}"
        if itype == "PROCESS_DOWN":
            # Simple fix: restart via watchdog
            if "--account" in script or "--accounts" in script:
                parts = script.split()
                _try_restart_script_with_args(parts[0], *parts[1:])
            else:
                _try_restart_script(script)
            logger.warning(f"[SUPERVISOR] Auto-restarted {script}")
        elif itype == "REDIS_DOWN":
            # Redis is critical — try restart, then Opus if that fails
            import subprocess
            logger.warning("[SUPERVISOR] Attempting Redis restart...")
            try:
                subprocess.run(["redis-server", "--daemonize", "yes"], timeout=10)
            except Exception:
                pass
            # Verify
            await asyncio.sleep(3)
            try:
                r = aioredis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
                await r.ping()
                await r.aclose()
                logger.info("[SUPERVISOR] Redis recovered")
            except Exception:
                spawn_opus_agent("redis_down", f"Redis on port 6379 is down and restart failed. Check: is redis-server installed? Is port 6379 in use by another process? Logs at /usr/local/var/log/redis.log or ~/logs/redis.log")
        elif itype == "PROCESS_FROZEN":
            # Frozen process = kill and restart
            import subprocess
            frozen_script = script.replace(".py", "")
            logger.warning(f"[SUPERVISOR] Killing frozen {script}...")
            try:
                subprocess.run(["pkill", "-f", f"python.*{script}"], timeout=5)
            except Exception:
                pass
            await asyncio.sleep(3)
            _try_restart_script(script)
        # For any HIGH/CRITICAL issue that persists after auto-fix attempt, escalate to Opus
        if severity in ("HIGH", "CRITICAL"):
            # Check if the process came back
            await asyncio.sleep(10)
            still_down = not _check_process_running(script.split()[0]) if script else True
            if still_down:
                msg = issue.get("message", "Unknown issue")
                recent_log = ""
                base = script.split()[0].replace(".py", "") if script else "unknown"
                log_path = LOG_DIR / f"{base}_watchdog.log"
                if log_path.exists():
                    try:
                        with open(log_path) as f:
                            lines = f.readlines()
                        recent_log = "".join(lines[-20:])
                    except Exception:
                        pass
                spawn_opus_agent(issue_key, f"{msg}\n\nRecent watchdog log ({log_path}):\n{recent_log[:1000]}\n\nThe auto-restart attempt failed. Investigate why this script won't start. Check for import errors, missing dependencies, port conflicts, or corrupted state files.")


# ═══════════════════════════════════════════════════════════════
# SECTION 7 — MAIN LOOP + SELF-HEALING WRAPPER
# ═══════════════════════════════════════════════════════════════

async def main():
    global _running, _last_monitor_stocks, _last_monitor_crypto, _last_compare_stocks, _last_compare_crypto, _last_market_scan, _last_symbol_cleanup, _last_outperformer_check, _last_supervisor_check
    logger.info("=" * 60)
    logger.info("COPILOT v1.0 — Autonomous Trading Oversight Agent")
    logger.info(f"Crypto accounts: {CRYPTO_ACCOUNTS}")
    logger.info(f"Tradier accounts: {TRADIER_ACCOUNTS}")
    logger.info(f"Tradier priority window: 12:00-20:00 UTC (8am-4pm ET)")
    logger.info("=" * 60)
    redis_client = await get_redis()
    # Start order listeners as background tasks
    asyncio.create_task(tradier_order_listener(redis_client))
    asyncio.create_task(crypto_order_listener(redis_client))
    last_status_report = 0.0
    last_daily_analysis_check = 0.0
    cycle = 0
    while _running:
        try:
            now = time.time()
            tradier_priority = is_tradier_priority_window()
            market_open = is_market_hours()
            # ─── DAILY 9AM ET ANALYSIS ─── check every 60s
            if now - last_daily_analysis_check >= 60:
                last_daily_analysis_check = now
                utc_now = datetime.now(timezone.utc)
                if utc_now.hour == DAILY_ANALYSIS_HOUR_UTC and utc_now.minute < 15 and utc_now.weekday() < 5:
                    if _last_daily_analysis_date != utc_now.strftime("%Y-%m-%d"):
                        await run_daily_analysis()
            # ─── MONITOR ───
            # Stocks: every 30s during market, 120s off-hours
            stock_interval = MONITOR_INTERVAL_STOCKS if market_open else 120
            if now - _last_monitor_stocks >= stock_interval:
                anomalies_stocks = await monitor_system(redis_client, "tradier")
                _last_monitor_stocks = now
                if anomalies_stocks and tradier_priority:
                    await process_anomalies_for_intervention(redis_client, anomalies_stocks)
            # Crypto: every 60s always (24/7 market)
            if now - _last_monitor_crypto >= MONITOR_INTERVAL_CRYPTO:
                anomalies_crypto = await monitor_system(redis_client, "crypto")
                _last_monitor_crypto = now
                if anomalies_crypto and not tradier_priority:
                    # Only auto-intervene on crypto when NOT in tradier priority window
                    await process_anomalies_for_intervention(redis_client, anomalies_crypto)
            # ─── COMPARE WITH TRADERS ───
            # Stocks: every 5min during market hours
            compare_stock_interval = COMPARE_INTERVAL_STOCKS if market_open else 3600
            if now - _last_compare_stocks >= compare_stock_interval:
                missed_stocks = await compare_with_traders(redis_client, "tradier")
                _last_compare_stocks = now
                if missed_stocks and tradier_priority:
                    await process_missed_trades_for_intervention(redis_client, missed_stocks)
            # Crypto: every 15min
            if now - _last_compare_crypto >= COMPARE_INTERVAL_CRYPTO:
                missed_crypto = await compare_with_traders(redis_client, "crypto")
                _last_compare_crypto = now
                if missed_crypto:
                    await process_missed_trades_for_intervention(redis_client, missed_crypto)
            # ─── MARKET-WIDE OUTLIER SCAN ─── every 15min during market hours
            if market_open and now - _last_market_scan >= MARKET_SCAN_INTERVAL:
                await run_market_scan(redis_client)
                _last_market_scan = now
            # ─── STALE SYMBOL CLEANUP ─── every hour
            if now - _last_symbol_cleanup >= 3600:
                await cleanup_stale_copilot_symbols()
                _last_symbol_cleanup = now
            # ─── OUTPERFORMER TRACKING + REENTRY ───
            outperf_interval = OUTPERFORMER_CHECK_INTERVAL if market_open else 300
            if now - _last_outperformer_check >= outperf_interval:
                await run_outperformer_cycle(redis_client)
                _last_outperformer_check = now
            # ─── SUPERVISOR: PROCESS HEALTH ───
            if now - _last_supervisor_check >= SUPERVISOR_CHECK_INTERVAL:
                try:
                    issues = await check_system_health()
                    _last_supervisor_check = now
                    if issues:
                        high_issues = [i for i in issues if i["severity"] in ("HIGH", "CRITICAL")]
                        if high_issues:
                            logger.warning(f"[SUPERVISOR] {len(high_issues)} critical issues detected — handling...")
                            await handle_supervisor_issues(high_issues)
                        low_issues = [i for i in issues if i["severity"] not in ("HIGH", "CRITICAL")]
                        for i in low_issues:
                            logger.info(f"[SUPERVISOR] {i['severity']}: {i['message']}")
                except Exception as e:
                    logger.error(f"[SUPERVISOR] Health check error: {e}")
            # ─── STATUS REPORT ─── every 5 min
            if now - last_status_report >= 300:
                await write_status_report()
                last_status_report = now
            cycle += 1
            if cycle % 60 == 0:
                logger.info(f"[COPILOT] Heartbeat: cycle={cycle}, interventions_1h={len(_interventions_this_hour)}, anomalies_buffered={len(_anomaly_history)}, market_open={market_open}, tradier_priority={tradier_priority}")
            await asyncio.sleep(10)  # Main loop tick
        except KeyboardInterrupt:
            _running = False
            break
        except Exception as e:
            logger.error(f"[COPILOT] Main loop error: {e}", exc_info=True)
            await asyncio.sleep(30)
    logger.info("[COPILOT] Shutting down")
    await redis_client.aclose()


def _self_restart():
    """Re-exec ourselves. Called when main() crashes fatally."""
    import subprocess
    python = sys.executable
    script = os.path.abspath(__file__)
    logger.warning(f"[COPILOT] SELF-RESTARTING: {python} {script}")
    os.execv(python, [python, script])


if __name__ == "__main__":
    import signal as _signal
    def _shutdown(sig, frame):
        global _running
        _running = False
        logger.info(f"Received signal {sig}, shutting down...")
    _signal.signal(_signal.SIGINT, _shutdown)
    _signal.signal(_signal.SIGTERM, _shutdown)
    MAX_CRASHES = 10
    crash_count = 0
    while crash_count < MAX_CRASHES:
        try:
            asyncio.run(main())
            break  # Clean exit
        except KeyboardInterrupt:
            logger.info("[COPILOT] KeyboardInterrupt — exiting cleanly")
            break
        except SystemExit:
            break
        except Exception as e:
            crash_count += 1
            logger.error(f"[COPILOT] FATAL CRASH #{crash_count}/{MAX_CRASHES}: {e}", exc_info=True)
            if crash_count >= MAX_CRASHES:
                logger.error(f"[COPILOT] Max crashes ({MAX_CRASHES}) reached — spawning Opus agent to investigate")
                import traceback
                tb = traceback.format_exc()
                spawn_opus_agent("copilot_crash_loop", f"ez_copilot.py has crashed {MAX_CRASHES} times in a row. Last error:\n{tb}\n\nInvestigate the root cause. Check recent changes, import errors, Redis connectivity. Fix the issue so copilot can restart cleanly.")
                break
            logger.warning(f"[COPILOT] Restarting in {5 * crash_count}s...")
            time.sleep(5 * crash_count)  # Exponential backoff: 5s, 10s, 15s...
    logger.info("[COPILOT] Process exiting")
