#!/usr/bin/env python3
"""
PAPER TRADING TOURNAMENT — 12 strategy variants on live Binance prices
=====================================================================
Runs until 10am ET, then prints comparison report.

Each variant has different exit/reentry aggressiveness settings.
All use the same live price + indicator data.

Usage:
    python3 paper_tournament.py                    # Run tournament
    python3 paper_tournament.py --report           # Print latest results
    python3 paper_tournament.py --duration 3600    # Run for 1 hour

Symbols: Top 20 most liquid USDT futures
"""

import asyncio
import json
import logging
import math
import os
import signal
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import numpy as np

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

BASE_PATH = Path(__file__).parent
DATA_DIR = BASE_PATH / "data" / "paper_tournament"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = DATA_DIR / "results.json"
TRADES_FILE = DATA_DIR / "trades.jsonl"
LOG_FILE = Path.home() / "logs" / "paper_tournament.log"

# Top 20 most liquid crypto futures
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT",
    "ARBUSDT", "OPUSDT", "INJUSDT", "SEIUSDT", "TAOUSDT",
]

FEE_PCT_DEFAULT = 0.04  # 0.04% per trade (taker) — overridden by variant fee_pct
START_CAPITAL = 1000.0
POSITION_SIZE_USD = 50.0  # $50 per position
SCAN_INTERVAL = 30  # seconds between scans
KLINE_INTERVALS = ["3m", "15m", "1h"]

# ═══════════════════════════════════════════════════════════════
# 12 STRATEGY VARIANTS
# ═══════════════════════════════════════════════════════════════

VARIANTS = {
    # === ROUND 1 WINNER: B3/C2 (medium exits + reentry) ===
    "W1_r1_winner": {
        "desc": "Round 1 winner: 5% TP + reentry, maker fees (0.02%)",
        "account_tp_pct": 5.0,
        "cycle_tp_pct": 8.0,
        "break_even_peak": 2.0,
        "break_even_floor": 0.5,
        "trailing_peak": 4.0,
        "trailing_dd": 1.5,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 1800,
        "stagnation_gain": -0.5,
        "fee_pct": 0.02,  # Maker fee
    },
    # === CURRENT CONFIG (our changes applied to server) ===
    "W2_current_cfg": {
        "desc": "Current server config: 8% TP, 10% cycle, no mitigator",
        "account_tp_pct": 8.0,
        "cycle_tp_pct": 10.0,
        "break_even_peak": 3.0,
        "break_even_floor": 1.0,
        "trailing_peak": 5.0,
        "trailing_dd": 2.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 3600,
        "stagnation_gain": -0.5,
        "fee_pct": 0.02,
    },
    # === BACKTEST WINNER: stoch_cross_15m exit + 3ATR stop ===
    "W3_bt_stoch15m": {
        "desc": "Backtest best crypto: exit on 15m stoch cross, 3ATR stop",
        "account_tp_pct": 999.0,
        "cycle_tp_pct": 999.0,
        "break_even_peak": 999.0,
        "break_even_floor": 999.0,
        "trailing_peak": 999.0,
        "trailing_dd": 999.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 99999,
        "stagnation_gain": -99.0,
        "exit_on_15m_stoch_cross": True,  # Exit when k_15m crosses d_15m against position
        "stop_loss_pct": -3.0,  # ~3ATR approximation
        "min_hold_bars": 3,
        "fee_pct": 0.02,
    },
    # === BACKTEST WINNER + WIDER: same but 1h stoch cross ===
    "W4_bt_stoch1h": {
        "desc": "Backtest variant: exit on 1h stoch cross (wider hold)",
        "account_tp_pct": 999.0,
        "cycle_tp_pct": 999.0,
        "break_even_peak": 999.0,
        "break_even_floor": 999.0,
        "trailing_peak": 999.0,
        "trailing_dd": 999.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 99999,
        "stagnation_gain": -99.0,
        "exit_on_1h_stoch_cross": True,  # Exit when k_1h crosses d_1h against position
        "stop_loss_pct": -5.0,
        "min_hold_bars": 6,
        "fee_pct": 0.02,
    },
    # === SYMMETRIC WITH STOP: 5% TP / 3% SL ===
    "W5_sym_5_3": {
        "desc": "Symmetric 5% TP / 3% SL + maker fees + reentry",
        "account_tp_pct": 5.0,
        "cycle_tp_pct": 8.0,
        "break_even_peak": 2.0,
        "break_even_floor": 0.5,
        "trailing_peak": 4.0,
        "trailing_dd": 1.5,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 1800,
        "stagnation_gain": -0.5,
        "stop_loss_pct": -3.0,
        "fee_pct": 0.02,
    },
    # === SYMMETRIC WITH STOP: 8% TP / 4% SL ===
    "W6_sym_8_4": {
        "desc": "Symmetric 8% TP / 4% SL + maker fees + reentry",
        "account_tp_pct": 8.0,
        "cycle_tp_pct": 10.0,
        "break_even_peak": 3.0,
        "break_even_floor": 1.0,
        "trailing_peak": 5.0,
        "trailing_dd": 2.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 3600,
        "stagnation_gain": -0.5,
        "stop_loss_pct": -4.0,
        "fee_pct": 0.02,
    },
    # === TRAILING ONLY (no fixed TP) ===
    "W7_trail_only": {
        "desc": "No TP — trailing 2% from 5%+ peak, 3% SL, reentry",
        "account_tp_pct": 999.0,
        "cycle_tp_pct": 999.0,
        "break_even_peak": 5.0,
        "break_even_floor": 2.0,
        "trailing_peak": 5.0,
        "trailing_dd": 2.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 7200,
        "stagnation_gain": -1.0,
        "stop_loss_pct": -3.0,
        "fee_pct": 0.02,
    },
    # === BASELINE: taker fees (no maker optimization) ===
    "W8_taker_base": {
        "desc": "Same as W2 but with TAKER fees (0.04%) — fee comparison baseline",
        "account_tp_pct": 8.0,
        "cycle_tp_pct": 10.0,
        "break_even_peak": 3.0,
        "break_even_floor": 1.0,
        "trailing_peak": 5.0,
        "trailing_dd": 2.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 3600,
        "stagnation_gain": -0.5,
        "fee_pct": 0.04,  # Taker fee — compare against W2
    },
    # === OLD CONFIG (pre-changes baseline) ===
    "W9_old_config": {
        "desc": "OLD config: 2% TP, 3% cycle, tight guards, no reentry",
        "account_tp_pct": 2.0,
        "cycle_tp_pct": 3.0,
        "break_even_peak": 0.5,
        "break_even_floor": 0.2,
        "trailing_peak": 2.0,
        "trailing_dd": 0.8,
        "reentry_enabled": False,
        "reentry_delay_sec": 999999,
        "stoch_profit_exit": True,
        "k1_scalp_exit": True,
        "stagnation_exit_sec": 300,
        "stagnation_gain": 0.1,
        "fee_pct": 0.04,
    },
    # === LONG ONLY (no shorts in bull market) ===
    "W10_long_only": {
        "desc": "LONG ONLY: 8% TP, wide guards, reentry, no shorts",
        "account_tp_pct": 8.0,
        "cycle_tp_pct": 10.0,
        "break_even_peak": 3.0,
        "break_even_floor": 1.0,
        "trailing_peak": 5.0,
        "trailing_dd": 2.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 3600,
        "stagnation_gain": -0.5,
        "fee_pct": 0.02,
        "long_only": True,
    },
    # === AGGRESSIVE SCALP + reentry (high freq, maker fees) ===
    "W11_scalp_maker": {
        "desc": "Scalp: exit on 3m stoch flip, instant reentry, MAKER fees",
        "account_tp_pct": 999.0,
        "cycle_tp_pct": 999.0,
        "break_even_peak": 999.0,
        "break_even_floor": 999.0,
        "trailing_peak": 999.0,
        "trailing_dd": 999.0,
        "reentry_enabled": True,
        "reentry_delay_sec": 5,
        "stoch_profit_exit": True,
        "k1_scalp_exit": True,
        "stagnation_exit_sec": 600,
        "stagnation_gain": 0.0,
        "exit_on_stoch_flip": True,
        "fee_pct": 0.02,  # Maker — can scalping work with lower fees?
    },
    # === HOLD FOREVER (pure entry signal test) ===
    "W12_hold_24h": {
        "desc": "HOLD 24h: no exits except 24h timeout — tests entry signal quality",
        "account_tp_pct": 999.0,
        "cycle_tp_pct": 999.0,
        "break_even_peak": 999.0,
        "break_even_floor": 999.0,
        "trailing_peak": 999.0,
        "trailing_dd": 999.0,
        "reentry_enabled": False,
        "reentry_delay_sec": 999999,
        "stoch_profit_exit": False,
        "k1_scalp_exit": False,
        "stagnation_exit_sec": 86400,  # 24h
        "stagnation_gain": -99.0,
        "fee_pct": 0.02,
    },
}


# ═══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════

@dataclass
class Position:
    symbol: str
    side: str  # LONG / SHORT
    entry_price: float
    qty: float
    entry_time: float
    max_gain_pct: float = 0.0
    exit_price: float = 0.0
    exit_time: float = 0.0
    exit_reason: str = ""


@dataclass
class PaperAccount:
    variant_id: str
    params: Dict[str, Any]
    capital: float = START_CAPITAL
    positions: Dict[str, Position] = field(default_factory=dict)  # symbol -> Position
    closed_trades: List[Dict] = field(default_factory=list)
    pending_reentries: Dict[str, Dict] = field(default_factory=dict)  # symbol -> {exit_price, exit_time, side}
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    max_drawdown: float = 0.0
    peak_capital: float = START_CAPITAL


# ═══════════════════════════════════════════════════════════════
# INDICATOR ENGINE (lightweight, self-contained)
# ═══════════════════════════════════════════════════════════════

def stoch_kd(highs, lows, closes, k_period=14, k_smooth=3, d_smooth=3):
    n = len(closes)
    if n < k_period:
        return 50.0, 50.0, 50.0, 50.0
    raw_k = []
    for i in range(n):
        if i < k_period - 1:
            raw_k.append(50.0)
        else:
            hh = max(highs[i - k_period + 1:i + 1])
            ll = min(lows[i - k_period + 1:i + 1])
            raw_k.append((closes[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0)
    # Simple moving average smoothing
    k_vals = []
    for i in range(len(raw_k)):
        if i < k_smooth - 1:
            k_vals.append(raw_k[i])
        else:
            k_vals.append(sum(raw_k[i - k_smooth + 1:i + 1]) / k_smooth)
    d_vals = []
    for i in range(len(k_vals)):
        if i < d_smooth - 1:
            d_vals.append(k_vals[i])
        else:
            d_vals.append(sum(k_vals[i - d_smooth + 1:i + 1]) / d_smooth)
    k_curr = k_vals[-1] if k_vals else 50.0
    d_curr = d_vals[-1] if d_vals else 50.0
    k_prev = k_vals[-2] if len(k_vals) > 1 else k_curr
    d_prev = d_vals[-2] if len(d_vals) > 1 else d_curr
    return k_curr, d_curr, k_prev, d_prev


def ha_color(opens, closes):
    if len(opens) < 2:
        return "neutral"
    ha_c = (opens[-1] + max(opens[-1], closes[-1]) + min(opens[-1], closes[-1]) + closes[-1]) / 4
    ha_o = (opens[-2] + closes[-2]) / 2
    return "green" if ha_c > ha_o else "red"


async def fetch_klines(session, symbol, interval, limit=100):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return [(float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in data]
    except Exception:
        pass
    return []


async def fetch_all_indicators(session, symbols):
    """Fetch 3m, 15m, 1h klines for all symbols and compute stochastics."""
    indicators = {}
    keys = []
    coros = []
    for sym in symbols:
        for tf in KLINE_INTERVALS:
            keys.append((sym, tf))
            coros.append(fetch_klines(session, sym, tf, 50))

    all_data = await asyncio.gather(*coros, return_exceptions=True)
    results = {}
    for (sym, tf), data in zip(keys, all_data):
        results[(sym, tf)] = data if isinstance(data, list) else []

    for sym in symbols:
        ind = {"current_price": 0.0}
        for tf in KLINE_INTERVALS:
            klines = results.get((sym, tf), [])
            if not klines:
                continue
            opens = [k[0] for k in klines]
            highs = [k[1] for k in klines]
            lows = [k[2] for k in klines]
            closes = [k[3] for k in klines]
            k, d, k_prev, d_prev = stoch_kd(highs, lows, closes)
            ha = ha_color(opens, closes)
            ind[f"k_{tf}"] = k
            ind[f"d_{tf}"] = d
            ind[f"k_{tf}_prev"] = k_prev
            ind[f"d_{tf}_prev"] = d_prev
            ind[f"ha_{tf}"] = ha
            ind["current_price"] = closes[-1]
        indicators[sym] = ind
    return indicators


# ═══════════════════════════════════════════════════════════════
# TRADING ENGINE
# ═══════════════════════════════════════════════════════════════

def should_enter(ind: Dict, side: str) -> bool:
    """Entry signal: 3m stoch crossover + 15m confirmation."""
    k_3m = ind.get("k_3m", 50)
    d_3m = ind.get("d_3m", 50)
    k_3m_prev = ind.get("k_3m_prev", 50)
    k_15m = ind.get("k_15m", 50)
    d_15m = ind.get("d_15m", 50)
    ha_15m = ind.get("ha_15m", "neutral")
    if side == "LONG":
        crossover = k_3m > d_3m and k_3m_prev <= d_3m + 0.5
        confirmation = k_15m > d_15m or k_15m < 30
        return crossover and confirmation and k_3m < 80
    else:
        crossover = k_3m < d_3m and k_3m_prev >= d_3m - 0.5
        confirmation = k_15m < d_15m or k_15m > 70
        return crossover and confirmation and k_3m > 20


def check_exit(pos: Position, ind: Dict, params: Dict, now_ts: float) -> Optional[str]:
    """Check all exit triggers. Returns reason or None."""
    price = ind.get("current_price", 0)
    if price <= 0:
        return None

    is_long = pos.side == "LONG"
    if is_long:
        gain_pct = ((price - pos.entry_price) / pos.entry_price) * 100
    else:
        gain_pct = ((pos.entry_price - price) / pos.entry_price) * 100

    pos.max_gain_pct = max(pos.max_gain_pct, gain_pct)
    age_sec = now_ts - pos.entry_time

    # Stop loss (only for C variants)
    sl = params.get("stop_loss_pct", -999)
    if gain_pct <= sl:
        return f"STOP_LOSS_{gain_pct:.2f}%"

    # ACCOUNT_TP
    if gain_pct >= params["account_tp_pct"]:
        return f"ACCOUNT_TP_{gain_pct:.2f}%"

    # CYCLE_TP (need 15m stoch turning)
    k_15m = ind.get("k_15m", 50)
    k_15m_prev = ind.get("k_15m_prev", 50)
    if gain_pct >= params["cycle_tp_pct"]:
        if is_long and k_15m < k_15m_prev:
            return f"CYCLE_TP_{gain_pct:.2f}%"
        elif not is_long and k_15m > k_15m_prev:
            return f"CYCLE_TP_{gain_pct:.2f}%"

    # BREAK_EVEN_GUARD
    if pos.max_gain_pct > params["break_even_peak"] and gain_pct <= params["break_even_floor"]:
        return f"BREAK_EVEN_GUARD_peak{pos.max_gain_pct:.1f}%_curr{gain_pct:.2f}%"

    # TRAILING_STOP
    if pos.max_gain_pct > params["trailing_peak"] and (pos.max_gain_pct - gain_pct) > params["trailing_dd"]:
        return f"TRAILING_STOP_peak{pos.max_gain_pct:.1f}%"

    # STOCH_PROFIT_EXIT (0.03-0.50%)
    if params.get("stoch_profit_exit") and 0.03 <= gain_pct < 0.50:
        k_3m = ind.get("k_3m", 50)
        d_3m = ind.get("d_3m", 50)
        if (is_long and k_3m < d_3m) or (not is_long and k_3m > d_3m):
            return f"STOCH_PROFIT_EXIT_{gain_pct:.3f}%"

    # k1_scalp_exit
    if params.get("k1_scalp_exit") and gain_pct >= 0.10:
        k_3m = ind.get("k_3m", 50)
        d_3m = ind.get("d_3m", 50)
        if (is_long and k_3m < d_3m) or (not is_long and k_3m > d_3m):
            return f"k1_scalp_HIT_{gain_pct:.2f}%"

    # Exit on stoch flip (D2 variant)
    if params.get("exit_on_stoch_flip") and gain_pct > 0.0:
        k_3m = ind.get("k_3m", 50)
        d_3m = ind.get("d_3m", 50)
        if (is_long and k_3m < d_3m) or (not is_long and k_3m > d_3m):
            return f"STOCH_FLIP_EXIT_{gain_pct:.2f}%"

    # Exit on 15m stoch cross (backtest winner)
    min_hold = params.get("min_hold_bars", 0) * 180  # 3m bars → seconds
    if params.get("exit_on_15m_stoch_cross") and age_sec > min_hold:
        k_15m = ind.get("k_15m", 50)
        d_15m = ind.get("d_15m", 50)
        if (is_long and k_15m < d_15m) or (not is_long and k_15m > d_15m):
            return f"STOCH_15M_CROSS_EXIT_{gain_pct:.2f}%"

    # Exit on 1h stoch cross
    if params.get("exit_on_1h_stoch_cross") and age_sec > min_hold:
        k_1h = ind.get("k_1h", 50)
        d_1h = ind.get("d_1h", 50)
        if (is_long and k_1h < d_1h) or (not is_long and k_1h > d_1h):
            return f"STOCH_1H_CROSS_EXIT_{gain_pct:.2f}%"

    # STAGNATION_EXIT
    stag_sec = params.get("stagnation_exit_sec", 300)
    stag_gain = params.get("stagnation_gain", 0.1)
    if age_sec > stag_sec and gain_pct < stag_gain:
        return f"STAGNATION_{gain_pct:.2f}%"

    return None


def check_reentry(acct: PaperAccount, symbol: str, ind: Dict, now_ts: float) -> Optional[str]:
    """Check if a pending reentry should fire."""
    if not acct.params.get("reentry_enabled"):
        return None

    pending = acct.pending_reentries.get(symbol)
    if not pending:
        return None

    delay = acct.params.get("reentry_delay_sec", 30)
    if now_ts - pending["exit_time"] < delay:
        return None

    side = pending["side"]
    k_3m = ind.get("k_3m", 50)
    d_3m = ind.get("d_3m", 50)

    # Reentry on any momentum tick in our favor
    if side == "LONG" and k_3m > d_3m:
        return side
    elif side == "SHORT" and k_3m < d_3m:
        return side

    return None


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), mode="a"),
    ],
)
logger = logging.getLogger("tournament")


def print_report(accounts: Dict[str, PaperAccount], elapsed_min: float):
    """Print comparison table."""
    rows = []
    for vid, acct in sorted(accounts.items()):
        win_rate = (acct.winning_trades / max(acct.total_trades, 1)) * 100
        net = acct.total_pnl - acct.total_fees
        roi = (net / START_CAPITAL) * 100
        rows.append((roi, vid, acct.params["desc"], acct.total_trades, acct.winning_trades, win_rate, acct.total_pnl, acct.total_fees, net, acct.max_drawdown, len(acct.positions)))

    rows.sort(key=lambda x: -x[0])
    print(f"\n{'='*120}")
    print(f"PAPER TOURNAMENT RESULTS — {elapsed_min:.0f} min elapsed — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*120}")
    print(f"{'Rank':<5} {'Variant':<24} {'Trades':>7} {'Wins':>5} {'WR%':>6} {'Gross$':>8} {'Fees$':>7} {'Net$':>8} {'ROI%':>7} {'MaxDD%':>7} {'Open':>5}")
    print(f"{'-'*120}")
    for rank, (roi, vid, desc, trades, wins, wr, gross, fees, net, mdd, open_pos) in enumerate(rows, 1):
        marker = " <<<" if rank == 1 else ""
        print(f"{rank:<5} {vid:<24} {trades:>7} {wins:>5} {wr:>5.1f}% {gross:>+7.2f} {fees:>6.2f} {net:>+7.2f} {roi:>+6.2f}% {mdd:>6.2f}% {open_pos:>5}{marker}")
    print(f"{'='*120}")
    # Print descriptions
    print(f"\nVariant descriptions:")
    for roi, vid, desc, *_ in rows:
        print(f"  {vid}: {desc}")
    print()


def save_results(accounts: Dict[str, PaperAccount], elapsed_min: float):
    """Save results to JSON."""
    results = {}
    for vid, acct in accounts.items():
        net = acct.total_pnl - acct.total_fees
        results[vid] = {
            "desc": acct.params["desc"],
            "total_trades": acct.total_trades,
            "winning_trades": acct.winning_trades,
            "win_rate": (acct.winning_trades / max(acct.total_trades, 1)) * 100,
            "gross_pnl": round(acct.total_pnl, 4),
            "fees": round(acct.total_fees, 4),
            "net_pnl": round(net, 4),
            "roi_pct": round((net / START_CAPITAL) * 100, 4),
            "max_drawdown_pct": round(acct.max_drawdown, 4),
            "open_positions": len(acct.positions),
            "elapsed_min": round(elapsed_min, 1),
            "closed_trades": acct.closed_trades[-50:],  # last 50
        }
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)


async def run_tournament(duration_sec: int = None):
    logger.info("Starting tournament...")
    # Calculate duration until 10am ET tomorrow if not specified
    if duration_sec is None:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        now_et = datetime.now(et)
        target = now_et.replace(hour=10, minute=0, second=0, microsecond=0)
        if target <= now_et:
            target += timedelta(days=1)
        duration_sec = int((target - now_et).total_seconds())
        logger.info(f"Running until 10:00 AM ET ({target.strftime('%Y-%m-%d %H:%M %Z')}), duration: {duration_sec/3600:.1f}h")

    # Initialize accounts
    accounts: Dict[str, PaperAccount] = {}
    for vid, params in VARIANTS.items():
        accounts[vid] = PaperAccount(variant_id=vid, params=params)

    start_time = time.time()
    cycle = 0
    last_report = 0

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= duration_sec:
                logger.info("Tournament time limit reached.")
                break

            cycle += 1
            now_ts = time.time()

            # Fetch indicators for all symbols
            try:
                indicators = await fetch_all_indicators(session, SYMBOLS)
            except Exception as e:
                logger.error(f"Indicator fetch error: {e}")
                await asyncio.sleep(10)
                continue

            if not indicators:
                logger.warning("No indicator data, retrying...")
                await asyncio.sleep(10)
                continue

            # Process each variant
            for vid, acct in accounts.items():
                params = acct.params

                for symbol in SYMBOLS:
                    ind = indicators.get(symbol)
                    if not ind or ind.get("current_price", 0) <= 0:
                        continue

                    price = ind["current_price"]
                    pos_key = symbol  # One position per symbol per variant

                    # === CHECK EXITS ===
                    if pos_key in acct.positions:
                        pos = acct.positions[pos_key]
                        exit_reason = check_exit(pos, ind, params, now_ts)
                        if exit_reason:
                            is_long = pos.side == "LONG"
                            if is_long:
                                gain_pct = ((price - pos.entry_price) / pos.entry_price) * 100
                                pnl_usd = (price - pos.entry_price) * pos.qty
                            else:
                                gain_pct = ((pos.entry_price - price) / pos.entry_price) * 100
                                pnl_usd = (pos.entry_price - price) * pos.qty
                            fee = abs(pos.qty * price * params.get("fee_pct", FEE_PCT_DEFAULT) / 100)
                            acct.total_pnl += pnl_usd
                            acct.total_fees += fee
                            acct.total_trades += 1
                            if pnl_usd > 0:
                                acct.winning_trades += 1
                            acct.capital += pnl_usd - fee
                            acct.peak_capital = max(acct.peak_capital, acct.capital)
                            dd = ((acct.peak_capital - acct.capital) / acct.peak_capital) * 100 if acct.peak_capital > 0 else 0
                            acct.max_drawdown = max(acct.max_drawdown, dd)
                            trade_record = {"symbol": symbol, "side": pos.side, "entry": pos.entry_price, "exit": price, "gain_pct": round(gain_pct, 4), "pnl_usd": round(pnl_usd, 4), "fee": round(fee, 4), "reason": exit_reason, "hold_sec": round(now_ts - pos.entry_time), "ts": datetime.now(timezone.utc).isoformat()[:19]}
                            acct.closed_trades.append(trade_record)
                            # Write to JSONL
                            with open(TRADES_FILE, "a") as tf:
                                tf.write(json.dumps({"variant": vid, **trade_record}) + "\n")
                            # Register for reentry
                            if params.get("reentry_enabled"):
                                acct.pending_reentries[symbol] = {"exit_price": price, "exit_time": now_ts, "side": pos.side}
                            del acct.positions[pos_key]
                            continue

                    # === CHECK REENTRY ===
                    if pos_key not in acct.positions:
                        reentry_side = check_reentry(acct, symbol, ind, now_ts)
                        if reentry_side:
                            qty = POSITION_SIZE_USD / price
                            fee = abs(qty * price * params.get("fee_pct", FEE_PCT_DEFAULT) / 100)
                            acct.total_fees += fee
                            acct.capital -= fee
                            acct.positions[pos_key] = Position(symbol=symbol, side=reentry_side, entry_price=price, qty=qty, entry_time=now_ts)
                            acct.pending_reentries.pop(symbol, None)
                            continue

                    # === CHECK NEW ENTRIES ===
                    if pos_key not in acct.positions and symbol not in acct.pending_reentries:
                        # Determine side based on indicators
                        _sides = ["LONG"] if params.get("long_only") else ["LONG", "SHORT"]
                        for side in _sides:
                            if should_enter(ind, side):
                                qty = POSITION_SIZE_USD / price
                                fee = abs(qty * price * params.get("fee_pct", FEE_PCT_DEFAULT) / 100)
                                acct.total_fees += fee
                                acct.capital -= fee
                                acct.positions[pos_key] = Position(symbol=symbol, side=side, entry_price=price, qty=qty, entry_time=now_ts)
                                break

            # Report every 2 minutes (first report after 60s)
            elapsed_min = elapsed / 60
            if elapsed - last_report >= 120 or (last_report == 0 and elapsed > 60):
                last_report = elapsed
                print_report(accounts, elapsed_min)
                save_results(accounts, elapsed_min)

            # Log cycle
            if cycle % 10 == 0:
                total_open = sum(len(a.positions) for a in accounts.values())
                total_trades = sum(a.total_trades for a in accounts.values())
                logger.info(f"Cycle {cycle}: {len(indicators)} symbols, {total_open} open positions, {total_trades} total trades, {elapsed_min:.0f}m elapsed")

            await asyncio.sleep(SCAN_INTERVAL)

    # Final report
    elapsed_min = (time.time() - start_time) / 60
    print_report(accounts, elapsed_min)
    save_results(accounts, elapsed_min)
    logger.info(f"Tournament complete. Results saved to {RESULTS_FILE}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        if RESULTS_FILE.exists():
            results = json.load(open(RESULTS_FILE))
            rows = []
            for vid, r in results.items():
                rows.append((r["roi_pct"], vid, r["desc"], r["total_trades"], r["winning_trades"], r["win_rate"], r["gross_pnl"], r["fees"], r["net_pnl"], r["max_drawdown_pct"], r["open_positions"]))
            rows.sort(key=lambda x: -x[0])
            print(f"\n{'='*120}")
            print(f"PAPER TOURNAMENT RESULTS — {results[list(results.keys())[0]].get('elapsed_min', 0):.0f} min elapsed")
            print(f"{'='*120}")
            print(f"{'Rank':<5} {'Variant':<24} {'Trades':>7} {'Wins':>5} {'WR%':>6} {'Gross$':>8} {'Fees$':>7} {'Net$':>8} {'ROI%':>7} {'MaxDD%':>7} {'Open':>5}")
            print(f"{'-'*120}")
            for rank, (roi, vid, desc, trades, wins, wr, gross, fees, net, mdd, open_pos) in enumerate(rows, 1):
                print(f"{rank:<5} {vid:<24} {trades:>7} {wins:>5} {wr:>5.1f}% {gross:>+7.2f} {fees:>6.2f} {net:>+7.2f} {roi:>+6.2f}% {mdd:>6.2f}% {open_pos:>5}")
            print(f"{'='*120}")
        else:
            print("No results yet. Run the tournament first.")
        return

    duration = None
    if "--duration" in sys.argv:
        idx = sys.argv.index("--duration")
        duration = int(sys.argv[idx + 1])

    asyncio.run(run_tournament(duration))


if __name__ == "__main__":
    main()
