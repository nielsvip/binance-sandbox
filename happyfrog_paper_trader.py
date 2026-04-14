#!/usr/bin/env python3
"""
HappyFrog Paper Trader — Trailing-stop swing SHORT strategy (paper mode only)

Derived from reverse-engineering Finandy trader "HappyFrog".
The ENTRY doesn't matter much — the trailing stop IS the edge.

Strategy: SHORT on any 1h Heikin-Ashi RED candle, trailing stop exit.
- Initial SL: 1.0% above entry
- Trail activates at +1.5% unrealized profit
- Trail distance: 0.3% from peak profit
- Hard TP: 5.0%
- Max hold: 120 bars (5 days)
- Max concurrent: 5 (1 per symbol, ranked by ATR%)

Reads 1h klines from klines_cache/ (refreshed by ez_market_data.py).
Logs paper trades to data/happyfrog_paper/trades.jsonl.
Does NOT interact with execute_now or any real trading.

Usage:
    python3 happyfrog_paper_trader.py              # Single check (cron-friendly)
    python3 happyfrog_paper_trader.py --daemon     # Run continuously (hourly checks)
    python3 happyfrog_paper_trader.py --status     # Show open positions + stats
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
DATA_DIR = BASE_PATH / "data" / "happyfrog_paper"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.jsonl"
POSITIONS_FILE = DATA_DIR / "open_positions.json"
STATS_FILE = DATA_DIR / "stats.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [HAPPYFROG] %(message)s")
logger = logging.getLogger("happyfrog_paper")

# ═══════════════════════════════════════════════════════════════
# STRATEGY CONFIG — from backtest PF 2.16
# ═══════════════════════════════════════════════════════════════
ENABLED = True
SIDE = "SHORT"
INITIAL_SL_PCT = 0.01       # 1.0% fixed SL before trail activates
TRAIL_ACTIVATION_PCT = 0.015 # Trail activates at +1.5% unrealized profit
TRAIL_DISTANCE_PCT = 0.003   # 0.3% from peak profit
TP_PCT = 0.05                # 5.0% hard cap take profit
MAX_HOLD_BARS = 120          # 120 bars = 5 days at 1h
MAX_CONCURRENT = 5           # Max 5 positions, 1 per symbol
ATR_PCT_CAP = 20             # Skip extreme micro-caps
FEE_PCT = 0.0008             # 0.08% round-trip fee
SLIPPAGE_PCT = 0.0002        # 0.02% slippage
TOTAL_COST_PCT = FEE_PCT + SLIPPAGE_PCT  # 0.10% total
PAPER_SIZE_USD = 100         # Notional per position for tracking


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


def compute_ha_and_atr(df: pd.DataFrame) -> dict:
    """Compute Heikin-Ashi color and ATR% for the last bar.
    Entry filter is minimal: just HA red candle."""
    n = len(df)
    if n < 250:
        return None
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    opn = df["open"].values
    # Heikin-Ashi
    ha_c = (opn + high + low + close) / 4
    ha_o = np.zeros(n)
    ha_o[0] = opn[0]
    for i in range(1, n):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    ha_red = ha_c[-1] < ha_o[-1]
    if not ha_red:
        return None
    # ATR% for ranking
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    atr14 = pd.Series(tr).ewm(span=14, adjust=False).mean().iloc[-1]
    atr_pct = (atr14 / close[-1] * 100) if close[-1] > 0 else 0
    if atr_pct > ATR_PCT_CAP:
        return None
    return {
        "price": float(close[-1]),
        "atr_pct": float(atr_pct),
        "ha_red": True,
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
    """Check open positions for trailing stop / fixed SL / TP / max hold exits."""
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
        # Current unrealized at close
        current_pnl_pct = (entry_price - current_price) / entry_price
        # Best profit this bar (price went to low)
        best_pnl_this_bar = (entry_price - current_low) / entry_price
        # Worst this bar (price went to high)
        worst_pnl_this_bar = (entry_price - current_high) / entry_price
        # Update max_profit_pct tracking (peak unrealized profit ever seen)
        prev_max_profit = pos.get("max_profit_pct", 0)
        new_max_profit = max(prev_max_profit, best_pnl_this_bar)
        pos["max_profit_pct"] = float(new_max_profit)
        exit_reason = None
        exit_price = current_price
        pnl_pct = 0
        # 1. Hard TP at 5.0%
        if best_pnl_this_bar >= TP_PCT:
            exit_reason = "TP"
            exit_price = entry_price * (1 - TP_PCT)
            pnl_pct = (TP_PCT - TOTAL_COST_PCT) * 100
        # 2. Trailing stop logic
        elif new_max_profit >= TRAIL_ACTIVATION_PCT:
            # Trail is active: SL at (max_profit - trail_distance)
            trail_sl_level = new_max_profit - TRAIL_DISTANCE_PCT
            # If worst this bar breached the trail SL (price bounced up)
            if worst_pnl_this_bar <= trail_sl_level:
                exit_reason = "TRAIL_SL"
                # Exit at the trail SL level
                exit_price = entry_price * (1 - trail_sl_level)
                pnl_pct = (trail_sl_level - TOTAL_COST_PCT) * 100
        # 3. Fixed SL before trail activates (1.0% above entry)
        if exit_reason is None and new_max_profit < TRAIL_ACTIVATION_PCT:
            if worst_pnl_this_bar <= -INITIAL_SL_PCT:
                exit_reason = "FIXED_SL"
                exit_price = entry_price * (1 + INITIAL_SL_PCT)
                pnl_pct = -(INITIAL_SL_PCT + TOTAL_COST_PCT) * 100
        # 4. Max hold timeout
        if exit_reason is None and bars_held >= MAX_HOLD_BARS:
            exit_reason = "MAX_HOLD"
            exit_price = current_price
            raw = (entry_price - current_price) / entry_price
            pnl_pct = (raw - TOTAL_COST_PCT) * 100
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
                "max_profit_pct": float(new_max_profit * 100),
                "trail_active": new_max_profit >= TRAIL_ACTIVATION_PCT,
                "entry_signal": pos.get("signal", {}),
            }
            log_trade(trade)
            update_stats(trade)
            logger.info(f"EXIT {symbol} {exit_reason} pnl={pnl_pct:+.2f}% bars={bars_held} maxprofit={new_max_profit*100:.2f}% (entry={entry_price:.6f} exit={exit_price:.6f})")
        else:
            unrealized = current_pnl_pct * 100
            pos["unrealized_pnl"] = float(unrealized)
            pos["bars_held"] = bars_held
            trail_active = new_max_profit >= TRAIL_ACTIVATION_PCT
            pos["trail_active"] = trail_active
            if trail_active:
                trail_sl = new_max_profit - TRAIL_DISTANCE_PCT
                pos["trail_sl_pct"] = float(trail_sl * 100)
            remaining.append(pos)
    return remaining


def check_entries(positions: list) -> list:
    """Scan ALL symbols, rank candidates by ATR%, pick top N."""
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
        signal = compute_ha_and_atr(df)
        if signal:
            candidates.append((symbol, signal))
    # Rank by ATR% descending — highest volatility = most room for trailing profit
    candidates.sort(key=lambda x: -x[1].get("atr_pct", 0))
    slots = MAX_CONCURRENT - len(positions)
    if candidates:
        logger.info(f"Candidates this hour: {len(candidates)} HA-red signals, picking top {min(slots, len(candidates))} by ATR%")
    for symbol, signal in candidates[:slots]:
        pos = {
            "symbol": symbol,
            "side": SIDE,
            "entry_price": signal["price"],
            "entry_time": str(now),
            "max_profit_pct": 0.0,
            "trail_active": False,
            "signal": signal,
        }
        positions.append(pos)
        logger.info(f"ENTRY {symbol} SHORT @ {signal['price']:.6f} | ATR%={signal['atr_pct']:.2f}")
    if candidates and len(candidates) > slots:
        skipped = candidates[slots:slots + 3]
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
    print(f"HAPPYFROG PAPER TRADER — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"Enabled: {ENABLED}")
    print(f"Config: InitSL={INITIAL_SL_PCT*100}% TrailAct={TRAIL_ACTIVATION_PCT*100}% TrailDist={TRAIL_DISTANCE_PCT*100}% TP={TP_PCT*100}%")
    print(f"MaxHold={MAX_HOLD_BARS}bars MaxPos={MAX_CONCURRENT} ATR%cap={ATR_PCT_CAP} Fees={TOTAL_COST_PCT*100}%")
    positions = load_positions()
    print(f"\nOpen positions: {len(positions)}/{MAX_CONCURRENT}")
    for p in positions:
        unr = p.get("unrealized_pnl", 0)
        bars = p.get("bars_held", 0)
        maxp = p.get("max_profit_pct", 0)
        trail = p.get("trail_active", False)
        trail_sl = p.get("trail_sl_pct", None)
        trail_str = f"TRAILING sl={trail_sl:+.2f}%" if trail and trail_sl is not None else "fixed SL"
        print(f"  {p['symbol']:20s} entry={p['entry_price']:.6f} unr={unr:+.2f}% maxp={maxp*100:.2f}% bars={bars} [{trail_str}]")
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
                trail_tag = " [TRAIL]" if t.get("trail_active", False) else ""
                print(f"  {t['symbol']:20s} {t['exit_reason']:10s} pnl={t['pnl_pct']:+.2f}% bars={t.get('bars_held',0)} maxp={t.get('max_profit_pct',0):.1f}%{trail_tag}")
    print()


def daemon_loop():
    """Run continuously, checking at :09 past each hour."""
    logger.info("Starting daemon mode — checking every hour at :09")
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
        # Sleep until next hour + 9 minutes (stagger: Graal :05, CryptoMAK :07, HappyFrog :09)
        now = datetime.now(timezone.utc)
        next_check = now.replace(minute=9, second=0, microsecond=0)
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
    parser = argparse.ArgumentParser(description="HappyFrog Paper Trader — Trailing-stop swing SHORT")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--status", action="store_true", help="Show status")
    args = parser.parse_args()
    if args.status:
        show_status()
    elif args.daemon:
        daemon_loop()
    else:
        run_cycle()
