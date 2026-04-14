#!/usr/bin/env python3
"""
Dengi Paper Trader — "Деньги любят тишину" (Money Loves Silence)

SHORT-only momentum continuation scalper (paper mode only).
Enters SHORT when RSI(14) < 35 AND Stoch K < 25 (oversold continuation).
Additional filters: MFI < 36, volume ratio > 1.3x average.
Complementary to Graal: Graal shorts reversals (RSI high), Dengi shorts continuation (RSI low).

Reads 1h klines from klines_cache/ (refreshed by ez_market_data.py).
Logs paper trades to data/dengi_paper/trades.jsonl.
Does NOT interact with execute_now or any real trading.

Usage:
    python3 dengi_paper_trader.py              # Single check (cron-friendly)
    python3 dengi_paper_trader.py --daemon     # Run continuously (hourly checks)
    python3 dengi_paper_trader.py --status     # Show open positions + stats
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
DATA_DIR = BASE_PATH / "data" / "dengi_paper"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.jsonl"
POSITIONS_FILE = DATA_DIR / "open_positions.json"
STATS_FILE = DATA_DIR / "stats.json"

LOG_DIR = BASE_PATH / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

file_handler = logging.FileHandler(LOG_DIR / "dengi_paper.log")
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter("%(asctime)s [DENGI] %(message)s"))

error_handler = logging.FileHandler(LOG_DIR / "dengi_paper_error.log")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter("%(asctime)s [DENGI] %(message)s"))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s [DENGI] %(message)s"))

logger = logging.getLogger("dengi_paper")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(error_handler)
logger.addHandler(console_handler)

# ═══════════════════════════════════════════════════════════════
# STRATEGY CONFIG — from 1,172-trade analysis (strongest edge)
# ═══════════════════════════════════════════════════════════════
ENABLED = True
SIDE = "SHORT"
TP_PCT = 0.008         # 0.8% take profit (source median win)
SL_PCT = 0.002         # 0.2% stop loss (sweet spot for momentum continuation)
MAX_HOLD_BARS = 12     # 12 bars max hold
RSI_MAX = 50           # RSI < 50 (NPZ analysis: winners at RSI ~35-49, losers at RSI ~49+)
STOCH_K_MAX = 50       # Stoch K < 50 (NPZ: winners at stoch_k_4h=43.6, NOT <25)
MFI_MAX = 50           # MFI < 50 (NPZ: winners at MFI ~36-48)
VOL_RATIO_MIN = 1.3    # Volume ratio > 1.3x average
ATR_PCT_CAP = 20       # Skip symbols with ATR% > 20
MAX_CONCURRENT = 3     # Pick top 3 by ATR%
FEE_PCT = 0.001        # 0.10% round-trip fee
PAPER_SIZE_USD = 100   # Notional per position
LEVERAGE = 20          # 20x leverage equivalent (paper only)

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
    """Check if Dengi entry conditions are met at the LAST completed bar.
    Returns dict with signal info or None if no signal."""
    n = len(df)
    if n < 250:
        return None
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    opn = df["open"].values
    vol = df["volume"].values
    price = close[-1]
    # RSI 14
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
    # Stoch RSI K
    stoch = stoch_rsi(pd.Series(close), 14, 5, 5)
    if stoch is None:
        return None
    k_col = "k" if "k" in stoch.columns else "%K"
    k_val = float(stoch[k_col].iloc[-1])
    if np.isnan(k_val) or k_val >= STOCH_K_MAX:
        return None
    # MFI
    tp = (high + low + close) / 3
    raw_mf = tp * vol
    td = np.diff(tp, prepend=tp[0])
    pm = np.where(td > 0, raw_mf, 0)
    nm = np.where(td < 0, raw_mf, 0)
    ps = pd.Series(pm).rolling(14, min_periods=1).sum().iloc[-1]
    ns = pd.Series(nm).rolling(14, min_periods=1).sum().iloc[-1]
    mfi = 100 - 100 / (1 + ps / max(ns, 1e-10))
    if mfi >= MFI_MAX:
        return None
    # Volume ratio > 1.3x average
    vol_sma = pd.Series(vol).rolling(20, min_periods=1).mean().iloc[-1]
    vol_ratio = vol[-1] / vol_sma if vol_sma > 0 else 0
    if vol_ratio < VOL_RATIO_MIN:
        return None
    # ATR% for ranking (and cap)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    atr14 = pd.Series(tr).ewm(span=14, adjust=False).mean().iloc[-1]
    atr_pct = (atr14 / price * 100) if price > 0 else 0
    if atr_pct > ATR_PCT_CAP:
        return None
    return {
        "price": float(price),
        "rsi": float(rsi),
        "stoch_k": float(k_val),
        "mfi": float(mfi),
        "vol_ratio": float(vol_ratio),
        "atr_pct": float(atr_pct),
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
        # SHORT: profit when price drops
        best_pnl = (entry_price - current_low) / entry_price
        worst_pnl = (entry_price - current_high) / entry_price
        exit_reason = None
        exit_price = current_price
        pnl_pct = 0
        if best_pnl >= TP_PCT:
            exit_reason = "TP"
            exit_price = entry_price * (1 - TP_PCT)
            pnl_pct = (TP_PCT - FEE_PCT) * LEVERAGE * 100
        elif worst_pnl <= -SL_PCT:
            exit_reason = "SL"
            exit_price = entry_price * (1 + SL_PCT)
            pnl_pct = -(SL_PCT + FEE_PCT) * LEVERAGE * 100
        elif bars_held >= MAX_HOLD_BARS:
            exit_reason = "MAX_HOLD"
            exit_price = current_price
            raw = (entry_price - current_price) / entry_price
            pnl_pct = (raw - FEE_PCT) * LEVERAGE * 100
        if exit_reason:
            trade = {
                "symbol": symbol,
                "side": SIDE,
                "leverage": LEVERAGE,
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
            unrealized = (entry_price - current_price) / entry_price * LEVERAGE * 100
            pos["unrealized_pnl"] = float(unrealized)
            pos["bars_held"] = bars_held
            remaining.append(pos)
    return remaining


def check_entries(positions: list) -> list:
    """Scan ALL symbols, rank candidates by ATR% descending, pick top 3."""
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
        logger.info(f"ENTRY {symbol} SHORT @ {signal['price']:.6f} | ATR%={signal['atr_pct']:.2f} RSI={signal['rsi']:.1f} StochK={signal['stoch_k']:.1f} MFI={signal['mfi']:.1f} VolR={signal['vol_ratio']:.2f}")
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
    print(f"DENGI PAPER TRADER STATUS -- {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"Enabled: {ENABLED}")
    print(f"Config: TP={TP_PCT*100}% SL={SL_PCT*100}% RSI<{RSI_MAX} StochK<{STOCH_K_MAX} MFI<{MFI_MAX} VolR>{VOL_RATIO_MIN} Lev={LEVERAGE}x")
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
    """Run continuously, checking at :15 past each hour."""
    logger.info("Starting daemon mode -- checking every hour at :15")
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
        # Sleep until next hour + 15 minutes (stagger: Graal :05, ProTrend :11, ProProfit :13, Dengi :15)
        now = datetime.now(timezone.utc)
        next_check = now.replace(minute=15, second=0, microsecond=0)
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
    parser = argparse.ArgumentParser(description="Dengi Paper Trader -- oversold momentum continuation SHORT scalper")
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
