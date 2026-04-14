#!/usr/bin/env python3
"""
ProTrend Paper Trader — HA-flip LONG dip-buy scalper (paper mode only)

Derived from 380-trade analysis (+$3,918 profit, PF 1.73).
Strategy: LONG on 1h Heikin-Ashi red->green flip with oversold indicator filters.

Reads 1h klines from klines_cache/ (refreshed by ez_market_data.py).
Logs paper trades to data/protrend_paper/trades.jsonl.
Does NOT interact with execute_now or any real trading.

Usage:
    python3 protrend_paper_trader.py              # Single check (cron-friendly)
    python3 protrend_paper_trader.py --daemon     # Run continuously (hourly checks)
    python3 protrend_paper_trader.py --status     # Show open positions + stats
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

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config
from ez_indicators import stoch_rsi

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = config.KLINES_CACHE_DIR
DATA_DIR = BASE_PATH / "data" / "protrend_paper"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.jsonl"
POSITIONS_FILE = DATA_DIR / "open_positions.json"
STATS_FILE = DATA_DIR / "stats.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PROTREND] %(message)s")
logger = logging.getLogger("protrend_paper")

# ===================================================================
# STRATEGY CONFIG — from 380-trade analysis, +$3,918 profit, PF 1.73
# ===================================================================
ENABLED = True
SIDE = "LONG"
TP_PCT = 0.020         # 2.0% take profit (source median win 1.96%)
SL_PCT = 0.0015        # 0.15% stop loss (tight-SL discipline)
MAX_HOLD_BARS = 24     # 24h max hold
STOCH_K_MAX = 25       # Stoch RSI K < 25 (oversold = dip-buy setup)
RSI_MAX = 50           # RSI < 50 (bearish = contrarian long)
BB_PCTB_MAX = 0.4      # BB %B < 0.4 (lower band = oversold)
ATR_PCT_CAP = 20       # Skip symbols with ATR% > 20
MAX_CONCURRENT = 2     # Pick top 2 by ATR%
FEE_PCT = 0.001        # 0.10% round-trip fee
PAPER_SIZE_USD = 100   # Notional per position

TRADEABLE_SYMBOLS = None


def load_tradeable_symbols():
    global TRADEABLE_SYMBOLS
    path = BASE_PATH / "tradeable_keys.json"
    if not path.exists():
        logger.warning(f"No tradeable_keys.json, using all USDT symbols")
        TRADEABLE_SYMBOLS = None
        return
    with open(path) as f:
        keys = json.load(f)
    symbols = set()
    for k in keys:
        parts = k.split(":")
        pk = parts[1] if len(parts) == 2 else parts[0]
        if pk.endswith("_LONG"):
            pk = pk[:-5]
        elif pk.endswith("_SHORT"):
            pk = pk[:-6]
        symbols.add(pk)
    TRADEABLE_SYMBOLS = symbols
    logger.info(f"Loaded {len(TRADEABLE_SYMBOLS)} tradeable symbols")


def load_klines_1h(symbol: str) -> pd.DataFrame:
    path = KLINES_DIR / f"{symbol}_1h.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        if len(df) < 250:
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"Failed loading {symbol}: {e}")
        return None


def compute_entry_signal(df: pd.DataFrame) -> dict:
    """Check if LONG entry conditions are met at the LAST completed bar."""
    n = len(df)
    if n < 250:
        return None
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    opn = df["open"].values
    vol = df["volume"].values
    price = close[-1]
    # Heikin-Ashi
    ha_c = (opn + high + low + close) / 4
    ha_o = np.zeros(n)
    ha_o[0] = opn[0]
    for i in range(1, n):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    ha_green = ha_c > ha_o
    # Check HA flip at last bar: prev red, current green (dip-buy)
    if not (not ha_green[-2] and ha_green[-1]):
        return None
    # Stoch RSI K < 25 (oversold)
    stoch = stoch_rsi(pd.Series(close), 14, 5, 5)
    if stoch is None:
        return None
    k_val = stoch["k"].iloc[-1] if "k" in stoch.columns else stoch["%K"].iloc[-1]
    if np.isnan(k_val) or k_val >= STOCH_K_MAX:
        return None
    # RSI < 50 (bearish = contrarian long)
    delta = np.diff(close, prepend=close[0])
    g = np.where(delta > 0, delta, 0).astype(float)
    l = np.where(delta < 0, -delta, 0).astype(float)
    alpha = 2 / 15
    ag = np.zeros(n)
    al = np.zeros(n)
    ag[0] = g[0]
    al[0] = l[0]
    for i in range(1, n):
        ag[i] = alpha * g[i] + (1 - alpha) * ag[i - 1]
        al[i] = alpha * l[i] + (1 - alpha) * al[i - 1]
    rsi = 100 - 100 / (1 + ag[-1] / max(al[-1], 1e-10))
    if rsi >= RSI_MAX:
        return None
    # BB %B < 0.4 (lower band = oversold)
    cs = pd.Series(close)
    sma20 = cs.rolling(20, min_periods=1).mean().iloc[-1]
    std20 = cs.rolling(20, min_periods=1).std().iloc[-1]
    bb_up = sma20 + 2 * std20
    bb_lo = sma20 - 2 * std20
    bb_range = bb_up - bb_lo
    bb_pctb = (price - bb_lo) / bb_range if bb_range > 0 else 0.5
    if bb_pctb >= BB_PCTB_MAX:
        return None
    # ATR% for ranking + cap
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    atr14 = pd.Series(tr).ewm(span=14, adjust=False).mean().iloc[-1]
    atr_pct = (atr14 / price * 100) if price > 0 else 0
    if atr_pct > ATR_PCT_CAP:
        return None
    return {
        "price": float(price),
        "stoch_k": float(k_val),
        "rsi": float(rsi),
        "bb_pctb": float(bb_pctb),
        "atr_pct": float(atr_pct),
        "ha_flip": "red_to_green",
        "timestamp": str(df["timestamp"].iloc[-1]),
    }


def load_positions() -> list:
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return []


def save_positions(positions: list):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)


def log_trade(trade: dict):
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(trade, default=str) + "\n")


def update_stats(trade: dict):
    stats = {}
    if STATS_FILE.exists():
        with open(STATS_FILE) as f:
            stats = json.load(f)
    stats["total_trades"] = stats.get("total_trades", 0) + 1
    if trade.get("pnl_pct", 0) > 0:
        stats["wins"] = stats.get("wins", 0) + 1
    stats["total_pnl_pct"] = stats.get("total_pnl_pct", 0) + trade.get("pnl_pct", 0)
    stats["last_trade"] = str(datetime.now(timezone.utc))
    total = stats["total_trades"]
    wins = stats.get("wins", 0)
    stats["win_rate"] = wins / total * 100 if total > 0 else 0
    stats["avg_pnl"] = stats["total_pnl_pct"] / total if total > 0 else 0
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def check_exits(positions: list) -> list:
    """Check open positions for TP/SL/max hold exits."""
    remaining = []
    now = datetime.now(timezone.utc)
    for pos in positions:
        symbol = pos["symbol"]
        df = load_klines_1h(symbol)
        if df is None:
            remaining.append(pos)
            continue
        current_price = float(df["close"].iloc[-1])
        current_high = float(df["high"].iloc[-1])
        current_low = float(df["low"].iloc[-1])
        entry_price = pos["entry_price"]
        entry_time = pd.to_datetime(pos["entry_time"])
        bars_held = len(df[df["timestamp"] > entry_time])
        # LONG: profit when price rises
        best_pnl = (current_high - entry_price) / entry_price
        worst_pnl = (current_low - entry_price) / entry_price
        exit_reason = None
        exit_price = current_price
        pnl_pct = 0
        if best_pnl >= TP_PCT:
            exit_reason = "TP"
            exit_price = entry_price * (1 + TP_PCT)
            pnl_pct = (TP_PCT - FEE_PCT) * 100
        elif worst_pnl <= -SL_PCT:
            exit_reason = "SL"
            exit_price = entry_price * (1 - SL_PCT)
            pnl_pct = -(SL_PCT + FEE_PCT) * 100
        elif bars_held >= MAX_HOLD_BARS:
            exit_reason = "MAX_HOLD"
            exit_price = current_price
            raw = (current_price - entry_price) / entry_price
            pnl_pct = (raw - FEE_PCT) * 100
        if exit_reason:
            trade = {
                "symbol": symbol,
                "side": SIDE,
                "entry_price": entry_price,
                "exit_price": float(exit_price),
                "entry_time": pos["entry_time"],
                "exit_time": str(now),
                "bars_held": bars_held,
                "pnl_pct": float(pnl_pct),
                "exit_reason": exit_reason,
                "entry_signal": pos.get("signal", {}),
            }
            log_trade(trade)
            update_stats(trade)
            logger.info(f"EXIT {symbol} {exit_reason} pnl={pnl_pct:.2f}% bars={bars_held} (entry={entry_price:.6f} exit={exit_price:.6f})")
        else:
            unrealized = (current_price - entry_price) / entry_price * 100
            pos["unrealized_pnl"] = float(unrealized)
            pos["bars_held"] = bars_held
            remaining.append(pos)
    return remaining


def check_entries(positions: list) -> list:
    """Scan ALL symbols, rank candidates by ATR% descending, pick top N."""
    if len(positions) >= MAX_CONCURRENT:
        return positions
    open_symbols = set(p["symbol"] for p in positions)
    now = datetime.now(timezone.utc)
    import glob
    files = glob.glob(str(KLINES_DIR / "*USDT_1h.json")) + glob.glob(str(KLINES_DIR / "*USDC_1h.json"))
    symbols_to_scan = set(Path(f).stem.replace("_1h", "") for f in files)
    candidates = []
    for symbol in symbols_to_scan:
        if symbol in open_symbols:
            continue
        df = load_klines_1h(symbol)
        if df is None:
            continue
        signal = compute_entry_signal(df)
        if signal:
            candidates.append((symbol, signal))
    # Rank by ATR% descending
    candidates.sort(key=lambda x: -x[1].get("atr_pct", 0))
    slots = MAX_CONCURRENT - len(positions)
    if candidates:
        logger.info(f"Candidates this hour: {len(candidates)} signals, picking top {min(slots, len(candidates))} by ATR%")
    for symbol, signal in candidates[:slots]:
        pos = {
            "symbol": symbol,
            "side": SIDE,
            "entry_price": signal["price"],
            "entry_time": str(now),
            "signal": signal,
        }
        positions.append(pos)
        logger.info(f"ENTRY {symbol} LONG @ {signal['price']:.6f} | ATR%={signal['atr_pct']:.2f} StochK={signal['stoch_k']:.1f} RSI={signal['rsi']:.1f} BB={signal['bb_pctb']:.2f}")
    if candidates and len(candidates) > slots:
        skipped = candidates[slots:slots + 3]
        logger.info(f"Skipped (lower ATR%): {', '.join(s[0] for s in skipped)}...")
    return positions


def run_cycle():
    """Run one check cycle: check exits, then entries."""
    if not ENABLED:
        logger.info("Strategy DISABLED -- skipping")
        return
    positions = load_positions()
    logger.info(f"Cycle start: {len(positions)} open positions")
    positions = check_exits(positions)
    positions = check_entries(positions)
    save_positions(positions)
    logger.info(f"Cycle end: {len(positions)} open positions")


def show_status():
    """Print current status."""
    print(f"\n{'='*60}")
    print(f"PROTREND PAPER TRADER STATUS -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"Enabled: {ENABLED}")
    print(f"Side: {SIDE}")
    print(f"Config: TP={TP_PCT*100}% SL={SL_PCT*100}% StochK<{STOCH_K_MAX} RSI<{RSI_MAX} BB<{BB_PCTB_MAX}")
    positions = load_positions()
    print(f"\nOpen positions: {len(positions)}/{MAX_CONCURRENT}")
    for p in positions:
        unr = p.get("unrealized_pnl", 0)
        bars = p.get("bars_held", 0)
        print(f"  {p['symbol']:20s} entry={p['entry_price']:.6f} unrealized={unr:+.2f}% bars={bars}")
    if STATS_FILE.exists():
        with open(STATS_FILE) as f:
            stats = json.load(f)
        print(f"\nLifetime stats:")
        print(f"  Trades: {stats.get('total_trades', 0)}")
        print(f"  Win Rate: {stats.get('win_rate', 0):.1f}%")
        print(f"  Total PnL: {stats.get('total_pnl_pct', 0):.2f}%")
        print(f"  Avg PnL: {stats.get('avg_pnl', 0):.4f}%")
        print(f"  Last trade: {stats.get('last_trade', 'never')}")
    if TRADES_FILE.exists():
        trades = []
        with open(TRADES_FILE) as f:
            for line in f:
                trades.append(json.loads(line))
        if trades:
            recent = trades[-5:]
            print(f"\nLast {len(recent)} trades:")
            for t in recent:
                print(f"  {t['symbol']:20s} {t['exit_reason']:10s} pnl={t['pnl_pct']:+.2f}% bars={t.get('bars_held',0)}")
    print()


def daemon_loop():
    """Run continuously, checking at :11 past each hour."""
    logger.info("Starting daemon mode -- checking every hour at :11")
    running = True
    def handle_signal(sig, frame):
        nonlocal running
        running = False
        logger.info("Shutdown signal received")
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    while running:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Cycle error: {e}")
        # Sleep until next hour + 11 minutes
        now = datetime.now(timezone.utc)
        next_check = now.replace(minute=11, second=0, microsecond=0)
        if next_check <= now:
            next_check = next_check + pd.Timedelta(hours=1)
        wait = (next_check - now).total_seconds()
        logger.info(f"Next check at {next_check.strftime('%H:%M')} UTC ({wait:.0f}s)")
        for _ in range(int(wait)):
            if not running:
                break
            time.sleep(1)
    logger.info("Daemon stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ProTrend Paper Trader -- HA-flip LONG dip-buy scalper")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--status", action="store_true", help="Show status")
    args = parser.parse_args()
    load_tradeable_symbols()
    if args.status:
        show_status()
    elif args.daemon:
        daemon_loop()
    else:
        run_cycle()
