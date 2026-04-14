#!/usr/bin/env python3
"""
CryptoMAK Paper Trader — HA trend-continuation scalper (paper mode only)

Derived from Finandy trader "MAIN - CryptoMAK" (100% WR, 38 trades, both L+S).
Strategy: LONG when HA has been green 3+ bars, SHORT when HA has been red 3+ bars.
Rank candidates by ATR%, pick top 1. TP=2.5%, SL=1.5%.

Reads 1h klines from klines_cache/ (refreshed by ez_market_data.py).
Logs paper trades to data/cryptomak_paper/trades.jsonl.
Does NOT interact with execute_now or any real trading.

Usage:
    python3 cryptomak_paper_trader.py              # Single check
    python3 cryptomak_paper_trader.py --daemon      # Run continuously
    python3 cryptomak_paper_trader.py --status      # Show status
"""
import argparse
import glob
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
DATA_DIR = BASE_PATH / "data" / "cryptomak_paper"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.jsonl"
POSITIONS_FILE = DATA_DIR / "open_positions.json"
STATS_FILE = DATA_DIR / "stats.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MAK] %(message)s")
logger = logging.getLogger("cryptomak_paper")

# ═══════════════════════════════════════════════════════════════
# STRATEGY CONFIG — from backtest: TP=2.5% SL=1.5% pick top 1
# ═══════════════════════════════════════════════════════════════
ENABLED = True
TP_PCT = 0.025         # 2.5% take profit
SL_PCT = 0.015         # 1.5% stop loss
MAX_HOLD_BARS = 24     # 24h max hold
MAX_CONCURRENT = 1     # Pick only the single best candidate
FEE_PCT = 0.0008       # 0.08% round-trip fee

# LONG filters (from CryptoMAK fingerprint: momentum continuation)
LONG_RSI_MIN = 55      # RSI must be bullish
LONG_BB_MIN = 0.6      # Price in upper BB
LONG_MFI_MIN = 40      # Flow positive
LONG_HA_STREAK_MIN = 3 # HA green for 3+ bars (trend established)

# SHORT filters (from CryptoMAK fingerprint: trend continuation)
SHORT_RSI_MAX = 50     # RSI must be bearish
SHORT_BB_MAX = 0.4     # Price in lower BB
SHORT_MFI_MAX = 50     # Flow negative/neutral
SHORT_HA_STREAK_MAX = -3  # HA red for 3+ bars (downtrend established)


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


def compute_ha_and_indicators(df: pd.DataFrame):
    """Compute HA streak, RSI, BB%B, MFI, ATR% for the last bar."""
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
    # HA streak at last bar
    streak = 0
    if ha_green[-1]:
        for j in range(n - 1, -1, -1):
            if ha_green[j]:
                streak += 1
            else:
                break
    else:
        for j in range(n - 1, -1, -1):
            if not ha_green[j]:
                streak -= 1
            else:
                break
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
    # BB %B
    cs = pd.Series(close)
    sma20 = cs.rolling(20, min_periods=1).mean().iloc[-1]
    std20 = cs.rolling(20, min_periods=1).std().iloc[-1]
    bb_up = sma20 + 2 * std20
    bb_lo = sma20 - 2 * std20
    bb_range = bb_up - bb_lo
    bb_pctb = (close[-1] - bb_lo) / bb_range if bb_range > 0 else 0.5
    # MFI
    tp = (high + low + close) / 3
    raw_mf = tp * vol
    td = np.diff(tp, prepend=tp[0])
    pm = np.where(td > 0, raw_mf, 0)
    nm = np.where(td < 0, raw_mf, 0)
    ps = pd.Series(pm).rolling(14, min_periods=1).sum().iloc[-1]
    ns = pd.Series(nm).rolling(14, min_periods=1).sum().iloc[-1]
    mfi = 100 - 100 / (1 + ps / max(ns, 1e-10))
    # ATR%
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    atr14 = pd.Series(tr).ewm(span=14, adjust=False).mean().iloc[-1]
    atr_pct = (atr14 / close[-1] * 100) if close[-1] > 0 else 0
    return {
        "price": float(close[-1]),
        "ha_streak": streak,
        "ha_green": bool(ha_green[-1]),
        "rsi": float(rsi),
        "bb_pctb": float(bb_pctb),
        "mfi": float(mfi),
        "atr_pct": float(atr_pct),
        "timestamp": str(df["timestamp"].iloc[-1]),
    }


def check_long_signal(ind: dict) -> bool:
    return (ind["ha_streak"] >= LONG_HA_STREAK_MIN
            and ind["rsi"] >= LONG_RSI_MIN
            and ind["bb_pctb"] >= LONG_BB_MIN
            and ind["mfi"] >= LONG_MFI_MIN)


def check_short_signal(ind: dict) -> bool:
    return (ind["ha_streak"] <= SHORT_HA_STREAK_MAX
            and ind["rsi"] <= SHORT_RSI_MAX
            and ind["bb_pctb"] <= SHORT_BB_MAX
            and ind["mfi"] <= SHORT_MFI_MAX)


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
    remaining = []
    now = datetime.now(timezone.utc)
    for pos in positions:
        symbol = pos["symbol"]
        side = pos["side"]
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
        if side == "SHORT":
            best_pnl = (entry_price - current_low) / entry_price
            worst_pnl = (entry_price - current_high) / entry_price
        else:
            best_pnl = (current_high - entry_price) / entry_price
            worst_pnl = (current_low - entry_price) / entry_price
        exit_reason = None
        exit_price = current_price
        pnl_pct = 0
        if best_pnl >= TP_PCT:
            exit_reason = "TP"
            if side == "SHORT":
                exit_price = entry_price * (1 - TP_PCT)
            else:
                exit_price = entry_price * (1 + TP_PCT)
            pnl_pct = (TP_PCT - FEE_PCT) * 100
        elif worst_pnl <= -SL_PCT:
            exit_reason = "SL"
            if side == "SHORT":
                exit_price = entry_price * (1 + SL_PCT)
            else:
                exit_price = entry_price * (1 - SL_PCT)
            pnl_pct = -(SL_PCT + FEE_PCT) * 100
        elif bars_held >= MAX_HOLD_BARS:
            exit_reason = "MAX_HOLD"
            exit_price = current_price
            if side == "SHORT":
                raw = (entry_price - current_price) / entry_price
            else:
                raw = (current_price - entry_price) / entry_price
            pnl_pct = (raw - FEE_PCT) * 100
        if exit_reason:
            trade = {
                "symbol": symbol,
                "side": side,
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
            logger.info(f"EXIT {symbol} {side} {exit_reason} pnl={pnl_pct:+.2f}% bars={bars_held}")
        else:
            if side == "SHORT":
                unrealized = (entry_price - current_price) / entry_price * 100
            else:
                unrealized = (current_price - entry_price) / entry_price * 100
            pos["unrealized_pnl"] = float(unrealized)
            pos["bars_held"] = bars_held
            remaining.append(pos)
    return remaining


def check_entries(positions: list) -> list:
    if len(positions) >= MAX_CONCURRENT:
        return positions
    open_symbols = set(p["symbol"] for p in positions)
    now = datetime.now(timezone.utc)
    files = glob.glob(str(KLINES_DIR / "*USDT_1h.json")) + glob.glob(str(KLINES_DIR / "*USDC_1h.json"))
    symbols_to_scan = set(Path(f).stem.replace("_1h", "") for f in files)
    candidates = []
    for symbol in symbols_to_scan:
        if symbol in open_symbols:
            continue
        df = load_klines_1h(symbol)
        if df is None:
            continue
        ind = compute_ha_and_indicators(df)
        if ind is None:
            continue
        # Cap ATR% at 20 — ultra-volatile micro-caps are noise, not signal
        if ind["atr_pct"] > 20:
            continue
        if check_long_signal(ind):
            candidates.append((symbol, ind, "LONG"))
        elif check_short_signal(ind):
            candidates.append((symbol, ind, "SHORT"))
    # Rank by ATR% descending
    candidates.sort(key=lambda x: -x[1].get("atr_pct", 0))
    slots = MAX_CONCURRENT - len(positions)
    if candidates:
        logger.info(f"Candidates: {len(candidates)} signals ({sum(1 for c in candidates if c[2]=='LONG')}L/{sum(1 for c in candidates if c[2]=='SHORT')}S), picking top {min(slots, len(candidates))} by ATR%")
    for symbol, ind, side in candidates[:slots]:
        pos = {
            "symbol": symbol,
            "side": side,
            "entry_price": ind["price"],
            "entry_time": str(now),
            "signal": ind,
        }
        positions.append(pos)
        logger.info(f"ENTRY {symbol} {side} @ {ind['price']:.6f} | ATR%={ind['atr_pct']:.2f} HA_streak={ind['ha_streak']} RSI={ind['rsi']:.1f} BB={ind['bb_pctb']:.2f} MFI={ind['mfi']:.1f}")
    return positions


def run_cycle():
    if not ENABLED:
        logger.info("Strategy DISABLED")
        return
    positions = load_positions()
    logger.info(f"Cycle start: {len(positions)} open")
    positions = check_exits(positions)
    positions = check_entries(positions)
    save_positions(positions)
    logger.info(f"Cycle end: {len(positions)} open")


def show_status():
    print(f"\n{'='*60}")
    print(f"CRYPTOMAK PAPER TRADER — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"Enabled: {ENABLED}")
    print(f"Config: TP={TP_PCT*100}% SL={SL_PCT*100}% MaxHold={MAX_HOLD_BARS}h MaxPos={MAX_CONCURRENT}")
    print(f"LONG: RSI>{LONG_RSI_MIN} BB>{LONG_BB_MIN} MFI>{LONG_MFI_MIN} HA_streak>={LONG_HA_STREAK_MIN}")
    print(f"SHORT: RSI<{SHORT_RSI_MAX} BB<{SHORT_BB_MAX} MFI<{SHORT_MFI_MAX} HA_streak<={SHORT_HA_STREAK_MAX}")
    positions = load_positions()
    print(f"\nOpen: {len(positions)}/{MAX_CONCURRENT}")
    for p in positions:
        unr = p.get("unrealized_pnl", 0)
        bars = p.get("bars_held", 0)
        print(f"  {p['symbol']:20s} {p['side']:5s} entry={p['entry_price']:.6f} unr={unr:+.2f}% bars={bars}")
    if STATS_FILE.exists():
        with open(STATS_FILE) as f:
            stats = json.load(f)
        print(f"\nLifetime:")
        print(f"  Trades: {stats.get('total_trades', 0)} | WR: {stats.get('win_rate', 0):.1f}% | PnL: {stats.get('total_pnl_pct', 0):.2f}% | Avg: {stats.get('avg_pnl', 0):.4f}%")
    if TRADES_FILE.exists():
        trades = []
        with open(TRADES_FILE) as f:
            for line in f:
                trades.append(json.loads(line))
        if trades:
            print(f"\nLast {min(5, len(trades))} trades:")
            for t in trades[-5:]:
                print(f"  {t['symbol']:20s} {t['side']:5s} {t['exit_reason']:10s} pnl={t['pnl_pct']:+.2f}% bars={t.get('bars_held',0)}")
    print()


def daemon_loop():
    logger.info("Starting daemon — checking every hour")
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
        now = datetime.now(timezone.utc)
        next_check = now.replace(minute=7, second=0, microsecond=0)
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
    parser = argparse.ArgumentParser(description="CryptoMAK Paper Trader")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        show_status()
    elif args.daemon:
        daemon_loop()
    else:
        run_cycle()
