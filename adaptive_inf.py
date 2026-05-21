#!/usr/bin/env python3
"""
Adaptive INF Monitor — Per-minute extreme detection + bounce exits

Monitors inf account's tradeable_keys via Redis hot_metrics every 60 seconds.
When k_3m or k_15m hits extremes (>95 or <5), flags for immediate exit on bounce.
Logs all signals to data/adaptive_inf/ for analysis.

This is NOT a parameter optimizer. It's a real-time watchdog that:
1. Reads inf's current tradeable_keys (changes every ~5min from rankings)
2. Polls Redis hot_metrics every 60s for each key
3. Detects extreme stoch/WT conditions
4. Logs bounce signals the moment they happen
5. Outputs recommended actions to data/adaptive_inf/signals.json

ez_manage.py can read signals.json to boost/block entries or trigger exits.

Usage:
    python3 adaptive_inf.py              # Single scan
    python3 adaptive_inf.py --daemon     # Run every 60s
    python3 adaptive_inf.py --status     # Show current signals
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = BASE_PATH / "data" / "adaptive_inf"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SIGNALS_FILE = DATA_DIR / "signals.json"
SIGNAL_LOG = DATA_DIR / "signal_log.jsonl"
STATE_FILE = DATA_DIR / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [INF_MON] %(message)s")
logger = logging.getLogger("inf_monitor")

ACCOUNT = "inf"
CYCLE_SECONDS = 60

# Extreme thresholds
K_EXTREME_HIGH = 95    # Stoch overbought extreme
K_EXTREME_LOW = 5      # Stoch oversold extreme
WT_EXTREME_HIGH = 60   # WT overbought
WT_EXTREME_LOW = -60   # WT oversold
# Bounce detection: was extreme, now reversing
K_BOUNCE_THRESHOLD = 10  # k dropped from >95 to <85 = bounce SHORT, or rose from <5 to >15 = bounce LONG


def get_redis():
    import redis
    return redis.Redis(host="localhost", port=6379, db=0)


def load_tradeable_keys():
    path = BASE_PATH / "tradeable_keys.json"
    if not path.exists():
        return {}
    with open(path) as f:
        keys = json.load(f)
    result = {}
    for k in keys:
        if not k.startswith(f"{ACCOUNT}:"):
            continue
        pk = k.split(":")[1]
        if pk.endswith("_LONG"):
            sym = pk[:-5]
            result.setdefault(sym, {})["long"] = True
        elif pk.endswith("_SHORT"):
            sym = pk[:-6]
            result.setdefault(sym, {})["short"] = True
    return result


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


SIGNAL_LOG_MAX_BYTES = 50 * 1024 * 1024


def log_signal(sig):
    try:
        if SIGNAL_LOG.exists() and SIGNAL_LOG.stat().st_size > SIGNAL_LOG_MAX_BYTES:
            rotated = SIGNAL_LOG.with_name(SIGNAL_LOG.name + ".1")
            if rotated.exists():
                rotated.unlink()
            SIGNAL_LOG.rename(rotated)
    except Exception:
        pass
    with open(SIGNAL_LOG, "a") as f:
        f.write(json.dumps(sig, default=str) + "\n")


def scan_cycle():
    """One scan: read all inf tradeable symbols from Redis, detect extremes and bounces."""
    r = get_redis()
    tradeable = load_tradeable_keys()
    state = load_state()
    now = datetime.now(timezone.utc)
    now_ts = now.isoformat()
    signals = []
    extremes = 0
    bounces = 0
    for sym, sides in sorted(tradeable.items()):
        raw = r.get(f"hot_metrics:{sym}")
        if not raw:
            continue
        try:
            metrics = json.loads(raw)
        except Exception:
            continue
        k_3m = float(metrics.get("k_3m", 50) or 50)
        d_3m = float(metrics.get("d_3m", 50) or 50)
        k_3m_prev = float(metrics.get("k_3m_prev", 50) or 50)
        wt1_3m = float(metrics.get("wt1_3m", 0) or 0)
        wt2_3m = float(metrics.get("wt2_3m", 0) or 0)
        wt_score_3m = float(metrics.get("wt_score_3m", 0) or 0)
        wt_vel_3m = float(metrics.get("wt_velocity_3m", 0) or 0)
        price = float(metrics.get("price", 0) or 0)
        wt_cross_bull = metrics.get("wt_cross_bull_3m", False)
        wt_cross_bear = metrics.get("wt_cross_bear_3m", False)
        # Also get 15m from klines-based indicators if available
        k_15m = None
        ind_raw = r.get(f"indicators:{sym}")
        if ind_raw:
            try:
                ind = json.loads(ind_raw)
                k_15m = float(ind.get("stoch_k_15m", 50) or 50)
            except Exception:
                pass
        # Track state per symbol
        prev = state.get(sym, {})
        was_extreme_high = prev.get("extreme_high", False)
        was_extreme_low = prev.get("extreme_low", False)
        was_k3m = prev.get("k_3m", 50)
        # Detect current state
        is_extreme_high = k_3m > K_EXTREME_HIGH
        is_extreme_low = k_3m < K_EXTREME_LOW
        # Detect bounces (was extreme, now reversing)
        bounce_short = was_extreme_high and k_3m < (K_EXTREME_HIGH - K_BOUNCE_THRESHOLD)
        bounce_long = was_extreme_low and k_3m > (K_EXTREME_LOW + K_BOUNCE_THRESHOLD)
        # WT extreme + cross = strong signal
        wt_extreme_bear = wt1_3m > WT_EXTREME_HIGH and wt_cross_bear
        wt_extreme_bull = wt1_3m < WT_EXTREME_LOW and wt_cross_bull
        # 15m confluence
        k15_extreme_high = k_15m is not None and k_15m > 90
        k15_extreme_low = k_15m is not None and k_15m < 10
        # Build signal
        sig = None
        if bounce_short:
            strength = "STRONG" if (wt_extreme_bear or k15_extreme_high) else "MEDIUM"
            sig = {"ts": now_ts, "symbol": sym, "signal": "BOUNCE_SHORT",
                   "strength": strength, "k_3m": k_3m, "k_3m_prev": was_k3m,
                   "wt1_3m": wt1_3m, "wt_cross_bear": wt_cross_bear,
                   "k_15m": k_15m, "price": price,
                   "action": f"EXIT_LONG or ENTER_SHORT on {sym}"}
            bounces += 1
        elif bounce_long:
            strength = "STRONG" if (wt_extreme_bull or k15_extreme_low) else "MEDIUM"
            sig = {"ts": now_ts, "symbol": sym, "signal": "BOUNCE_LONG",
                   "strength": strength, "k_3m": k_3m, "k_3m_prev": was_k3m,
                   "wt1_3m": wt1_3m, "wt_cross_bull": wt_cross_bull,
                   "k_15m": k_15m, "price": price,
                   "action": f"EXIT_SHORT or ENTER_LONG on {sym}"}
            bounces += 1
        elif is_extreme_high:
            sig = {"ts": now_ts, "symbol": sym, "signal": "EXTREME_HIGH",
                   "k_3m": k_3m, "wt1_3m": wt1_3m, "k_15m": k_15m, "price": price,
                   "action": f"WATCH {sym} — k_3m={k_3m:.0f} overbought, bounce SHORT imminent"}
            extremes += 1
        elif is_extreme_low:
            sig = {"ts": now_ts, "symbol": sym, "signal": "EXTREME_LOW",
                   "k_3m": k_3m, "wt1_3m": wt1_3m, "k_15m": k_15m, "price": price,
                   "action": f"WATCH {sym} — k_3m={k_3m:.0f} oversold, bounce LONG imminent"}
            extremes += 1
        elif wt_extreme_bear:
            sig = {"ts": now_ts, "symbol": sym, "signal": "WT_BEAR_CROSS_EXTREME",
                   "k_3m": k_3m, "wt1_3m": wt1_3m, "price": price,
                   "action": f"SHORT signal on {sym} — WT bear cross from extreme"}
        elif wt_extreme_bull:
            sig = {"ts": now_ts, "symbol": sym, "signal": "WT_BULL_CROSS_EXTREME",
                   "k_3m": k_3m, "wt1_3m": wt1_3m, "price": price,
                   "action": f"LONG signal on {sym} — WT bull cross from extreme"}
        if sig:
            signals.append(sig)
            log_signal(sig)
        # Update state
        state[sym] = {"extreme_high": is_extreme_high, "extreme_low": is_extreme_low,
                      "k_3m": k_3m, "wt1_3m": wt1_3m, "ts": now_ts}
    save_state(state)
    # Write current signals
    with open(SIGNALS_FILE, "w") as f:
        json.dump({"ts": now_ts, "signals": signals, "extremes": extremes,
                   "bounces": bounces, "symbols_scanned": len(tradeable)}, f, indent=2)
    if bounces > 0:
        for s in [s for s in signals if "BOUNCE" in s["signal"]]:
            logger.info(f"*** {s['signal']} {s['symbol']} k_3m={s['k_3m']:.0f} (was {s['k_3m_prev']:.0f}) wt1={s['wt1_3m']:.0f} {s['strength']} ***")
    if extremes > 0:
        ext_syms = [s["symbol"] for s in signals if "EXTREME" in s["signal"]]
        logger.info(f"Extremes: {', '.join(ext_syms)}")
    if not signals:
        logger.info(f"Scanned {len(tradeable)} symbols — no extremes or bounces")


def show_status():
    print(f"\n{'='*70}")
    print(f"INF MONITOR — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*70}")
    if SIGNALS_FILE.exists():
        with open(SIGNALS_FILE) as f:
            data = json.load(f)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["ts"])).total_seconds()
        print(f"Last scan: {data['ts'][:19]} ({age:.0f}s ago)")
        print(f"Symbols: {data['symbols_scanned']} | Extremes: {data['extremes']} | Bounces: {data['bounces']}")
        if data["signals"]:
            print(f"\nActive signals:")
            for s in data["signals"]:
                print(f"  {s['signal']:25s} {s['symbol']:15s} k_3m={s.get('k_3m',0):.0f} wt1={s.get('wt1_3m',0):.0f} → {s['action']}")
    # Show state (which symbols are currently at extremes)
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
        highs = [s for s, d in state.items() if d.get("extreme_high")]
        lows = [s for s, d in state.items() if d.get("extreme_low")]
        if highs:
            print(f"\nAt EXTREME HIGH (k>95): {', '.join(highs)}")
        if lows:
            print(f"At EXTREME LOW (k<5): {', '.join(lows)}")
    # Recent bounce signals from log
    if SIGNAL_LOG.exists():
        bounces = []
        with open(SIGNAL_LOG) as f:
            for line in f:
                try:
                    s = json.loads(line)
                    if "BOUNCE" in s.get("signal", ""):
                        bounces.append(s)
                except Exception:
                    pass
        if bounces:
            print(f"\nRecent bounces (last 10):")
            for s in bounces[-10:]:
                print(f"  {s['ts'][:19]} {s['signal']:15s} {s['symbol']:15s} k={s.get('k_3m',0):.0f} {s.get('strength','')}")
    print()


def daemon_loop():
    logger.info(f"Starting inf monitor — scanning every {CYCLE_SECONDS}s")
    running = True
    def handle_signal(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    while running:
        try:
            scan_cycle()
        except Exception as e:
            logger.error(f"Scan error: {e}")
        for _ in range(CYCLE_SECONDS):
            if not running:
                break
            time.sleep(1)
    logger.info("Monitor stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adaptive INF Monitor")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        show_status()
    elif args.daemon:
        daemon_loop()
    else:
        scan_cycle()
        show_status()
