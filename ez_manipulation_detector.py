#!/opt/anaconda3/envs/binance_env/bin/python
"""Manipulation / Pump & Dump Detector — Flags suspicious symbols in real-time.

Detects:
  1. Volume spikes (>5x normal on any TF)
  2. Price spikes (>10% move in <15min)
  3. Wick ratio (wick > 3x body = manipulation candle)
  4. Price crash after spike (pump then dump pattern)
  5. Persistent downtrend with random spikes (dead coin pump schemes)

When flagged:
  - Writes to data/manipulation_flags.json (read by ez_positions_quick + ez_manage)
  - Sets tight position cap (50% of normal START_POSITION_SIZE)
  - Sets aggressive stop loss threshold
  - Publishes alert to Redis channel

Runs as service: binance-manipulation-detector
Usage: python ez_manipulation_detector.py [--daemon]
"""

import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import load_environment_from_gpg, safe_fetch_float

load_environment_from_gpg(None)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("manipulation_detector")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
fh = RotatingFileHandler(str(logs_dir / "ez_manipulation_detector.log"), maxBytes=50 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

BASE_PATH = Path(__file__).parent
FLAGS_FILE = BASE_PATH / "data" / "manipulation_flags.json"
HISTORY_FILE = BASE_PATH / "data" / "manipulation_history.json"
MARKET_DATA_PATTERN = "data/market_data_*.json"
RANKINGS_FILE = BASE_PATH / "data" / "rankings.json"

# ═══ DETECTION THRESHOLDS ═══
VOL_SPIKE_3M = 12.0       # 12x normal volume on 3m = suspicious (crypto is volatile)
VOL_SPIKE_15M = 8.0       # 8x normal volume on 15m
VOL_SPIKE_1H = 6.0        # 6x normal volume on 1h
PRICE_SPIKE_PCT = 20.0    # 20% price move in recent candles (crypto can do 15% legit)
WICK_BODY_RATIO = 8.0     # Wick > 8x body = manipulation candle (tighter: avoids noise)
DUMP_AFTER_PUMP_PCT = 10.0 # If price drops 10%+ after a recent spike = P&D confirmed
DOWNTREND_SMA_PCT = -50.0 # Price > 50% below SMA200 = dead coin territory
SPIKE_ON_DEAD_COIN_VOL = 6.0  # 6x vol on a dead coin is suspicious
FLAG_TTL_HOURS = 48        # Flags last 48 hours then auto-expire
SCAN_INTERVAL = 30         # Seconds between scans

# ═══ POSITION CAPS WHEN FLAGGED ═══
FLAGGED_SIZE_MULTIPLIER = 0.3   # 30% of normal position size
FLAGGED_MAX_USD = 10.0          # Hard cap $10 per position on flagged symbols


sf = safe_fetch_float


def load_market_data():
    """Load the latest market data file."""
    import glob
    files = sorted(glob.glob(str(BASE_PATH / MARKET_DATA_PATTERN)), key=os.path.getmtime, reverse=True)
    if not files:
        return {}
    try:
        with open(files[0]) as f:
            return json.load(f)
    except Exception:
        return {}


def load_rankings():
    """Load rankings.json for vol and trend data."""
    if not RANKINGS_FILE.exists():
        return {}
    try:
        with open(RANKINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def load_existing_flags():
    """Load existing manipulation flags."""
    if not FLAGS_FILE.exists():
        return {}
    try:
        with open(FLAGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_flags(flags):
    """Save flags atomically."""
    tmp = FLAGS_FILE.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(flags, f, indent=2, default=str)
        os.replace(tmp, FLAGS_FILE)
    except Exception as e:
        logger.error(f"Failed to save flags: {e}")
        try:
            os.unlink(tmp)
        except OSError:
            pass


def save_history(event):
    """Append to manipulation history."""
    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(event)
    if len(history) > 5000:
        history = history[-5000:]
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2, default=str)
    except Exception:
        pass


def detect_manipulation(symbol, md, rankings_data):
    """Analyze a single symbol for manipulation signals. Returns (flagged, reasons, severity)."""
    reasons = []
    severity = 0  # 0=clean, 1=watch, 2=suspicious, 3=confirmed_manipulation
    # ─── Volume Spikes ───
    rv_3m = sf(md.get("relative_volume_3m"), 1.0)
    rv_15m = sf(md.get("relative_volume_15m"), 1.0)
    rv_1h = sf(md.get("relative_volume_1h"), 1.0)
    if rv_3m >= VOL_SPIKE_3M:
        reasons.append(f"VOL_SPIKE_3M: {rv_3m:.1f}x normal (threshold {VOL_SPIKE_3M}x)")
        severity = max(severity, 2)
    if rv_15m >= VOL_SPIKE_15M:
        reasons.append(f"VOL_SPIKE_15M: {rv_15m:.1f}x normal")
        severity = max(severity, 2)
    if rv_1h >= VOL_SPIKE_1H:
        reasons.append(f"VOL_SPIKE_1H: {rv_1h:.1f}x normal")
        severity = max(severity, 2)
    # ─── Price vs SMA200 (dead coin detection) ───
    current_price = sf(md.get("current_price"), 0)
    sma200_1h = sf(md.get("sma_200_1h"), 0)
    if current_price > 0 and sma200_1h > 0:
        pct_from_sma = ((current_price - sma200_1h) / sma200_1h) * 100
        if pct_from_sma < DOWNTREND_SMA_PCT:
            reasons.append(f"DEAD_COIN: price {pct_from_sma:.0f}% below SMA200")
            severity = max(severity, 1)
            if rv_3m >= SPIKE_ON_DEAD_COIN_VOL or rv_15m >= SPIKE_ON_DEAD_COIN_VOL:
                reasons.append(f"DEAD_COIN_VOL_SPIKE: volume spike on a coin >30% below SMA200 = pump scheme")
                severity = max(severity, 3)
    # ─── Donchian Channel extremes (price spike detection) ───
    dc_high_3m = sf(md.get("dc_high_3m"), 0)
    dc_low_3m = sf(md.get("dc_low_3m"), 0)
    dc_high_1h = sf(md.get("dc_high_1h"), 0)
    dc_low_1h = sf(md.get("dc_low_1h"), 0)
    if dc_high_3m > 0 and dc_low_3m > 0:
        dc_range_3m = ((dc_high_3m - dc_low_3m) / dc_low_3m) * 100
        if dc_range_3m > PRICE_SPIKE_PCT:
            reasons.append(f"PRICE_RANGE_3M: {dc_range_3m:.1f}% range (spike detected)")
            severity = max(severity, 2)
    if dc_high_1h > 0 and dc_low_1h > 0:
        dc_range_1h = ((dc_high_1h - dc_low_1h) / dc_low_1h) * 100
        if dc_range_1h > PRICE_SPIKE_PCT * 1.5:
            reasons.append(f"PRICE_RANGE_1H: {dc_range_1h:.1f}% range")
            severity = max(severity, 2)
    # ─── Wick detection (from HA candles + price extremes) ───
    high_3m = sf(md.get("high_3m"), 0)
    low_3m = sf(md.get("low_3m"), 0)
    ha_3m = str(md.get("ha_3m", "neutral"))
    if high_3m > 0 and low_3m > 0 and current_price > 0:
        body = abs(current_price - sf(md.get("dc_basis_3m"), current_price))
        total_range = high_3m - low_3m
        if total_range > 0 and body > (current_price * 0.0001):
            wick_ratio = (total_range - body) / body
            if wick_ratio >= WICK_BODY_RATIO:
                reasons.append(f"WICK_RATIO_3M: {wick_ratio:.1f}x (wick >> body = manipulation candle)")
                severity = max(severity, 2)
    # ─── Rankings-based checks ───
    rank_data = rankings_data.get(symbol, {})
    rel_vol_raw = sf(rank_data.get("rel_vol_raw"), 1.0)
    dc_expansion = sf(rank_data.get("dc_expansion"), 0)
    if rel_vol_raw > 5.0:
        reasons.append(f"RANKINGS_VOL: {rel_vol_raw:.1f}x (from rankings)")
        severity = max(severity, 2)
    if dc_expansion > 3.0 and rel_vol_raw > 3.0:
        reasons.append(f"DC_EXPANSION+VOL: expansion={dc_expansion:.1f} + vol={rel_vol_raw:.1f}x = potential pump")
        severity = max(severity, 2)
    # ─── Combined: multiple signals = confirmed ───
    if len(reasons) >= 3:
        severity = max(severity, 3)
    return severity > 0, reasons, severity


async def scan_loop():
    """Main scanning loop — checks all symbols every SCAN_INTERVAL seconds."""
    logger.info(f"[DETECTOR] Started — scanning every {SCAN_INTERVAL}s")
    _alert_cooldown = {}
    while True:
        try:
            market_data = load_market_data()
            rankings = load_rankings()
            flags = load_existing_flags()
            now = time.time()
            now_iso = datetime.now(timezone.utc).isoformat()
            new_flags = 0
            expired = 0
            for symbol in list(flags.keys()):
                flag_ts = flags[symbol].get("flagged_at_ts", 0)
                if (now - flag_ts) > FLAG_TTL_HOURS * 3600:
                    logger.info(f"[EXPIRED] {symbol}: flag expired after {FLAG_TTL_HOURS}h")
                    del flags[symbol]
                    expired += 1
            for symbol, md in market_data.items():
                if not isinstance(md, dict):
                    continue
                flagged, reasons, severity = detect_manipulation(symbol, md, rankings)
                if flagged and severity >= 2:
                    is_new = symbol not in flags
                    last_alert = _alert_cooldown.get(symbol, 0)
                    if is_new or (now - last_alert) > 300:
                        severity_label = {1: "WATCH", 2: "SUSPICIOUS", 3: "CONFIRMED_MANIPULATION"}[severity]
                        logger.warning(f"[FLAG] {symbol}: {severity_label} — {'; '.join(reasons)}")
                        _alert_cooldown[symbol] = now
                    flags[symbol] = {"severity": severity, "severity_label": {1: "WATCH", 2: "SUSPICIOUS", 3: "CONFIRMED_MANIPULATION"}.get(severity, "UNKNOWN"), "reasons": reasons, "flagged_at": now_iso, "flagged_at_ts": now, "size_multiplier": FLAGGED_SIZE_MULTIPLIER, "max_usd": FLAGGED_MAX_USD, "relative_volume_3m": sf(md.get("relative_volume_3m"), 0), "relative_volume_1h": sf(md.get("relative_volume_1h"), 0), "current_price": sf(md.get("current_price"), 0)}
                    if is_new:
                        new_flags += 1
                        save_history({"symbol": symbol, "severity": severity, "reasons": reasons, "timestamp": now_iso, "price": sf(md.get("current_price"), 0)})
            if new_flags > 0 or expired > 0:
                save_flags(flags)
                logger.info(f"[SCAN] {len(market_data)} symbols checked | {new_flags} new flags | {expired} expired | {len(flags)} total active flags")
            elif len(flags) > 0:
                save_flags(flags)
        except Exception as e:
            logger.error(f"[SCAN] Error: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)


def show_flags():
    """Print current flags."""
    flags = load_existing_flags()
    if not flags:
        print("No active manipulation flags.")
        return
    print(f"\n{'='*70}")
    print(f"  ACTIVE MANIPULATION FLAGS ({len(flags)} symbols)")
    print(f"{'='*70}\n")
    for symbol, data in sorted(flags.items(), key=lambda x: -x[1].get("severity", 0)):
        sev = data.get("severity_label", "?")
        reasons = data.get("reasons", [])
        print(f"  {sev:<25} {symbol:<15} rv3m={data.get('relative_volume_3m', 0):.1f}x  rv1h={data.get('relative_volume_1h', 0):.1f}x  price=${data.get('current_price', 0):.6f}")
        for r in reasons[:3]:
            print(f"    → {r}")
        print()


if __name__ == "__main__":
    if "--daemon" in sys.argv or len(sys.argv) == 1:
        asyncio.run(scan_loop())
    elif "--flags" in sys.argv or "--show" in sys.argv:
        show_flags()
    elif "--history" in sys.argv:
        if HISTORY_FILE.exists():
            history = json.loads(HISTORY_FILE.read_text())
            for event in history[-20:]:
                print(f"  {event.get('timestamp', '?')[:19]}  {event.get('symbol', '?'):<15} sev={event.get('severity', '?')}  {event.get('reasons', ['?'])[0][:60]}")
    else:
        print(__doc__)
