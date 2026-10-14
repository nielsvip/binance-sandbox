#!/usr/bin/env python3
"""ez_crosses — backup stochastic RSI crossover detector (ez_indicators fallback).

Batch execution: runs full computation every 15 minutes max. No continuous loops.
Sleeps between intervals to avoid CPU burn.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from config import Config
from utils import setup_ez_script_logger, get_simple_redis_manager

config = Config()
logger = setup_ez_script_logger('ez_crosses')
BASE_PATH = config.BASE_PATH
SIGNALS_FILE = config.DATA_DIR / "crosses_signals.json"
BATCH_INTERVAL = 900  # 15 minutes between full scans
IDLE_SLEEP = 60  # Sleep 60s between idle checks

def stochastic_rsi(closes, k=14, d=3, smooth=3):
    """Minimal stochastic RSI calculation."""
    if len(closes) < k + d + smooth:
        return None, None

    closes = closes[-k - d - smooth:]
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]

    gains = sum(d for d in deltas if d > 0 for _ in range(1))
    losses = sum(-d for d in deltas if d < 0 for _ in range(1))

    rs = (gains / losses) if losses > 0 else (100 if gains > 0 else 0)
    rsi = 100 - (100 / (1 + rs))

    rsi_vals = []
    for i in range(len(closes)):
        lo = min(closes[max(0, i-k):i+1])
        hi = max(closes[max(0, i-k):i+1])
        rsi_val = ((rsi - lo) / (hi - lo) * 100) if (hi - lo) > 0 else 50
        rsi_vals.append(rsi_val)

    k_vals = [sum(rsi_vals[max(0,i-smooth):i+1]) / min(i+1, smooth) for i in range(len(rsi_vals))]
    d_vals = [sum(k_vals[max(0,i-d):i+1]) / min(i+1, d) for i in range(len(k_vals))]

    return k_vals[-1] if k_vals else 50, d_vals[-1] if d_vals else 50

def load_signal_cache():
    """Load cached last signals."""
    if SIGNALS_FILE.exists():
        try:
            with open(SIGNALS_FILE) as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_signals(signals):
    """Save signals atomically."""
    SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SIGNALS_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(signals, f)
    tmp.replace(SIGNALS_FILE)

async def scan_crosses():
    """Scan all symbols for stochastic crosses."""
    try:
        rm = await get_simple_redis_manager()
        if not rm:
            logger.warning("No Redis connection")
            return

        signals = load_signal_cache()

        # Read klines from Redis (fast)
        for symbol in config.SYMBOLS_ACTIVE:
            try:
                key = f"klines:{symbol}:15m"
                klines_raw = await rm.read('gateway', key)
                if not klines_raw:
                    continue

                klines = json.loads(klines_raw)[-20:]  # Last 20 bars only
                closes = [float(k['close']) for k in klines]

                if len(closes) < 5:
                    continue

                k, d = stochastic_rsi(closes)
                if k is None:
                    continue

                if symbol not in signals:
                    signals[symbol] = {'k': k, 'd': d, 'crossed': False}
                    continue

                prev_k = signals[symbol].get('k', 50)
                prev_d = signals[symbol].get('d', 50)

                # Cross detection
                crossed = (prev_k <= prev_d and k > d) or (prev_k >= prev_d and k < d)

                signals[symbol] = {
                    'k': round(k, 2),
                    'd': round(d, 2),
                    'crossed': crossed,
                    'at': datetime.now(timezone.utc).isoformat()
                }

            except Exception as e:
                logger.debug(f"{symbol}: {e}")

        save_signals(signals)
        logger.info(f"Scanned {len(signals)} symbols")

    except Exception as e:
        logger.error(f"Scan error: {e}")

async def main():
    """Main loop — runs scan every 15 minutes, sleeps between."""
    logger.info("ez_crosses started (batch every 15min, backup for ez_indicators)")
    last_scan = 0
    while True:
        try:
            now = time.time()
            if now - last_scan >= BATCH_INTERVAL:
                await scan_crosses()
                last_scan = now
            await asyncio.sleep(IDLE_SLEEP)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            await asyncio.sleep(IDLE_SLEEP)

if __name__ == '__main__':
    asyncio.run(main())
