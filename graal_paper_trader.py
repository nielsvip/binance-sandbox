#!/usr/bin/env python3
"""
Graal Paper Trader — HA-flip SHORT scalper (paper mode only)

Derived from reverse-engineering Finandy trader "Graal.tut" (97.9% WR, 47 trades).
Strategy: SHORT on 1h Heikin-Ashi green→red flip with indicator filters.

Reads 1h klines from klines_cache/ (refreshed by ez_market_data.py).
Logs paper trades to data/graal_paper/trades.jsonl.
Does NOT interact with execute_now or any real trading.

Usage:
    python3 graal_paper_trader.py              # Single check (cron-friendly)
    python3 graal_paper_trader.py --daemon     # Run continuously (hourly checks)
    python3 graal_paper_trader.py --status     # Show open positions + stats
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

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = config.KLINES_CACHE_DIR
DATA_DIR = BASE_PATH / "data" / "graal_paper"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.jsonl"
POSITIONS_FILE = DATA_DIR / "open_positions.json"
STATS_FILE = DATA_DIR / "stats.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [GRAAL] %(message)s")
logger = logging.getLogger("graal_paper")

# ═══════════════════════════════════════════════════════════════
# STRATEGY CONFIG — from 6-month backtest with fees
# ═══════════════════════════════════════════════════════════════
ENABLED = True
SIDE = "SHORT"
TP_PCT = 0.005         # 0.5% take profit (honest backtest: TP=SL=0.5%, PF 5.05)
SL_PCT = 0.005         # 0.5% stop loss (equal TP/SL — HA flip predicts direction 88%)
MAX_HOLD_BARS = 12     # 12h max hold
RSI_MIN = 50           # RSI > 50 (not oversold = room to fall)
RSI_MAX = 75           # RSI < 75 (from Cohen's d: losers enter at RSI 71+)
BB_PCTB_MIN = 0.5      # BB %B > 0.5 (upper half of bands)
BB_PCTB_MAX = 1.0      # BB %B < 1.0 (not extreme breakout above bands)
MFI_MIN = 50           # MFI > 50
ADX_MIN = 20           # ADX > 20 (trending)
CHOP_MAX = 60          # Choppiness < 60 (not choppy)
MAX_CONCURRENT = 2     # Pick top 2 by ATR%
FEE_PCT = 0.0008       # 0.08% round-trip fee
PAPER_SIZE_USD = 100   # Notional per position
# === 7 EXTRA FILTERS from 560-trade Cohen's d analysis ===
# These separate Graal's winners from his losers (d > 0.6 effect size)
CANDLE_BODY_MAX_PCT = 1.0   # d=2.13: skip if last candle body > 1% (pumping)
MOMENTUM_3BAR_MAX_PCT = 3.0 # d=1.75: skip if 3-bar price change > 3%
EMA9_DIST_MAX_PCT = 4.0     # d=1.68: skip if price > 4% above EMA9
VOLUME_RATIO_MAX = 2.0      # d=1.24: skip if volume > 2x average (breakout volume)
EMA20_DIST_MAX_PCT = 6.0    # d=1.26: skip if price > 6% above EMA20

# Tradeable symbols — from tradeable_keys.json
TRADEABLE_SYMBOLS = None  # Loaded at startup


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
    """Check if entry conditions are met at the LAST completed bar.
    Returns dict with signal info or None if no signal."""
    n = len(df)
    if n < 250:
        return None
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    opn = df["open"].values
    vol = df["volume"].values
    # Heikin-Ashi
    ha_c = (opn + high + low + close) / 4
    ha_o = np.zeros(n)
    ha_o[0] = opn[0]
    for i in range(1, n):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    ha_green = ha_c > ha_o
    # Check HA flip at last bar: prev green, current red
    if not (ha_green[-2] and not ha_green[-1]):
        return None
    price = close[-1]
    # === FILTER 1: Candle body (d=2.13 — strongest discriminator) ===
    candle_body_pct = abs(close[-1] - opn[-1]) / opn[-1] * 100 if opn[-1] > 0 else 0
    if candle_body_pct > CANDLE_BODY_MAX_PCT:
        return None
    # === FILTER 2: 3-bar momentum (d=1.75) ===
    if n >= 4:
        mom3 = (close[-1] - close[-4]) / close[-4] * 100 if close[-4] > 0 else 0
        if mom3 > MOMENTUM_3BAR_MAX_PCT:
            return None
    # === FILTER 3 + 5: EMA distances (d=1.68, d=1.26) ===
    ema9 = pd.Series(close).ewm(span=9, adjust=False).mean().iloc[-1]
    ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().iloc[-1]
    ema9_dist = (price - ema9) / ema9 * 100 if ema9 > 0 else 0
    ema20_dist = (price - ema20) / ema20 * 100 if ema20 > 0 else 0
    if ema9_dist > EMA9_DIST_MAX_PCT:
        return None
    if ema20_dist > EMA20_DIST_MAX_PCT:
        return None
    # === FILTER 4: Volume ratio (d=1.24) ===
    vol_sma = pd.Series(vol).rolling(20, min_periods=1).mean().iloc[-1]
    vol_ratio = vol[-1] / vol_sma if vol_sma > 0 else 1
    if vol_ratio > VOLUME_RATIO_MAX:
        return None
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
    if rsi < RSI_MIN or rsi > RSI_MAX:
        return None
    # BB %B
    cs = pd.Series(close)
    sma20 = cs.rolling(20, min_periods=1).mean().iloc[-1]
    std20 = cs.rolling(20, min_periods=1).std().iloc[-1]
    bb_up = sma20 + 2 * std20
    bb_lo = sma20 - 2 * std20
    bb_range = bb_up - bb_lo
    bb_pctb = (price - bb_lo) / bb_range if bb_range > 0 else 0.5
    if bb_pctb < BB_PCTB_MIN or bb_pctb > BB_PCTB_MAX:
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
    if mfi < MFI_MIN:
        return None
    # ADX (simplified vectorized)
    pdm = np.diff(high, prepend=high[0])
    mdm = -np.diff(low, prepend=low[0])
    pdm_c = np.where((pdm > 0) & (pdm > mdm), pdm, 0).astype(float)
    mdm_c = np.where((mdm > 0) & (mdm > pdm), mdm, 0).astype(float)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    atr14_s = pd.Series(tr).ewm(span=14, adjust=False).mean()
    pdi_s = 100 * pd.Series(pdm_c).ewm(span=14, adjust=False).mean() / atr14_s.replace(0, 1e-10)
    mdi_s = 100 * pd.Series(mdm_c).ewm(span=14, adjust=False).mean() / atr14_s.replace(0, 1e-10)
    dx_s = 100 * (pdi_s - mdi_s).abs() / (pdi_s + mdi_s).replace(0, 1e-10)
    adx = dx_s.ewm(span=14, adjust=False).mean().iloc[-1]
    if adx < ADX_MIN:
        return None
    # Choppiness
    atr1 = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    as14 = pd.Series(atr1).rolling(14, min_periods=1).sum().iloc[-1]
    hh14 = pd.Series(high).rolling(14, min_periods=1).max().iloc[-1]
    ll14 = pd.Series(low).rolling(14, min_periods=1).min().iloc[-1]
    cd = hh14 - ll14
    chop = 100 * np.log10(as14 / max(cd, 1e-10)) / np.log10(14)
    if chop > CHOP_MAX:
        return None
    # ATR% for ranking
    atr_pct = (atr14_s.iloc[-1] / price * 100) if price > 0 else 0
    return {
        "price": float(price),
        "rsi": float(rsi),
        "bb_pctb": float(bb_pctb),
        "mfi": float(mfi),
        "adx": float(adx),
        "chop": float(chop),
        "atr_pct": float(atr_pct),
        "candle_body_pct": float(candle_body_pct),
        "ema9_dist": float(ema9_dist),
        "ema20_dist": float(ema20_dist),
        "vol_ratio": float(vol_ratio),
        "ha_flip": True,
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
            pnl_pct = (TP_PCT - FEE_PCT) * 100
        elif worst_pnl <= -SL_PCT:
            exit_reason = "SL"
            exit_price = entry_price * (1 + SL_PCT)
            pnl_pct = -(SL_PCT + FEE_PCT) * 100
        elif bars_held >= MAX_HOLD_BARS:
            exit_reason = "MAX_HOLD"
            exit_price = current_price
            raw = (entry_price - current_price) / entry_price
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
            unrealized = (entry_price - current_price) / entry_price * 100
            pos["unrealized_pnl"] = float(unrealized)
            pos["bars_held"] = bars_held
            remaining.append(pos)
    return remaining


def check_entries(positions: list) -> list:
    """Scan ALL symbols, rank candidates by ATR%, pick top N (Graal's secret sauce)."""
    if len(positions) >= MAX_CONCURRENT:
        return positions
    open_symbols = set(p["symbol"] for p in positions)
    now = datetime.now(timezone.utc)
    # Paper mode: scan ALL crypto symbols with 1h klines
    import glob
    files = glob.glob(str(KLINES_DIR / "*USDT_1h.json")) + glob.glob(str(KLINES_DIR / "*USDC_1h.json"))
    symbols_to_scan = set(Path(f).stem.replace("_1h", "") for f in files)
    # Collect ALL candidates first
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
    # Rank by ATR% descending — highest volatility = most likely to hit 0.8% TP within the hour
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
        logger.info(f"ENTRY {symbol} SHORT @ {signal['price']:.6f} | ATR%={signal['atr_pct']:.2f} RSI={signal['rsi']:.1f} BB={signal['bb_pctb']:.2f} MFI={signal['mfi']:.1f}")
    if candidates and len(candidates) > slots:
        skipped = candidates[slots:slots+3]
        logger.info(f"Skipped (lower ATR%): {', '.join(s[0] for s in skipped)}...")
    return positions


def run_cycle():
    """Run one check cycle: check exits, then entries."""
    if not ENABLED:
        logger.info("Strategy DISABLED — skipping")
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
    print(f"GRAAL PAPER TRADER STATUS — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"Enabled: {ENABLED}")
    print(f"Config: TP={TP_PCT*100}% SL={SL_PCT*100}% RSI>{RSI_MIN} BB>{BB_PCTB_MIN} MFI>{MFI_MIN} ADX>{ADX_MIN} Chop<{CHOP_MAX}")
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
    """Run continuously, checking at the top of each hour."""
    logger.info("Starting daemon mode — checking every hour")
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
        # Sleep until next hour + 5 minutes (give klines time to refresh)
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=5, second=0, microsecond=0)
        if next_hour <= now:
            next_hour = next_hour + pd.Timedelta(hours=1)
        wait = (next_hour - now).total_seconds()
        logger.info(f"Next check at {next_hour.strftime('%H:%M')} UTC ({wait:.0f}s)")
        for _ in range(int(wait)):
            if not running:
                break
            time.sleep(1)
    logger.info("Daemon stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Graal Paper Trader — HA-flip SHORT scalper")
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
