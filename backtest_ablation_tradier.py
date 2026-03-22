#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
TRADIER ABLATION BACKTEST — Systematically test every entry/exit/augment component for STOCKS.

Adapts the crypto ablation framework to Tradier stock system (tradier_manage.py).
Uses Daily bars (1214 bars = ~4.8 years) as primary TF.

Usage:
  python3 backtest_ablation_tradier.py                    # Full run
  python3 backtest_ablation_tradier.py --report           # Print results
  python3 backtest_ablation_tradier.py --symbols 30       # Limit symbols
"""
import os, sys, json, math, time, signal, logging, warnings, argparse
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from copy import deepcopy

logging.basicConfig(level=logging.INFO, format='%(asctime)s [TRADIER_ABL] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

import platform
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache" / "tradier"
RESULTS_DIR = BASE_PATH / "data" / "backtest_ablation_tradier"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "tradier_ablation_results.json"
FEE_PCT = 0.10  # stocks: ~$0 commission but 0.10% slippage estimate
ANNUAL_BARS = {"5m": 19656, "15m": 6552, "1h": 1638, "4h": 410, "D": 252}
N_WORKERS = max(1, cpu_count() - 1)
MIN_TRADES = 10
WARMUP = 200
ENTRY_TF = "D"  # Daily bars have 4.8 years of data
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══ INDICATORS ══════════════════════════════════════════════════════════════

def _sma_np(arr, period):
    if len(arr) < period:
        return np.full_like(arr, np.nan)
    clean = np.where(np.isnan(arr), 50.0, arr)
    cum = np.cumsum(clean)
    cum[period:] = cum[period:] - cum[:-period]
    result = np.full_like(arr, np.nan)
    result[period - 1:] = cum[period - 1:] / period
    return result


def _ema_np(arr, period):
    result = np.empty_like(arr)
    result[:] = np.nan
    if len(arr) < period:
        return result
    mult = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = arr[i] * mult + result[i - 1] * (1 - mult)
    return result


def ind_stoch(h, lo, c, k_period=14, sk=5, sd=5):
    n = len(c)
    from numpy.lib.stride_tricks import sliding_window_view
    if n >= k_period:
        hh = np.max(sliding_window_view(h, k_period), axis=1)
        ll = np.min(sliding_window_view(lo, k_period), axis=1)
        raw_k = np.full(n, 50.0)
        denom = hh - ll
        valid = denom > 0
        raw_k[k_period - 1:] = np.where(valid, (c[k_period - 1:] - ll) / denom * 100, 50.0)
    else:
        raw_k = np.full(n, 50.0)
    k = _sma_np(raw_k, sk)
    d = _sma_np(k, sd)
    return k, d


def ind_rsi(c, period=14):
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema_np(gain, period)
    avg_loss = _ema_np(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - 100 / (1 + rs)


def ind_rsi2(c):
    return ind_rsi(c, 2)


def ind_donchian(h, lo, period=20):
    n = len(h)
    from numpy.lib.stride_tricks import sliding_window_view
    dc_h = np.full(n, np.nan)
    dc_l = np.full(n, np.nan)
    if n >= period:
        dc_h[period - 1:] = np.max(sliding_window_view(h, period), axis=1)
        dc_l[period - 1:] = np.min(sliding_window_view(lo, period), axis=1)
    dc_mid = (dc_h + dc_l) / 2
    return dc_h, dc_l, dc_mid


def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4
    ha_o = np.empty_like(o)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)


def ind_atr(h, lo, c, period=14):
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))))
    tr[0] = h[0] - lo[0]
    return _ema_np(tr, period)


def ind_mfi(h, lo, c, v, period=14):
    tp = (h + lo + c) / 3
    mf = tp * v
    n = len(c)
    result = np.full(n, np.nan)
    for i in range(period, n):
        pos = sum(mf[j] for j in range(i - period + 1, i + 1) if tp[j] > tp[j - 1])
        neg = sum(mf[j] for j in range(i - period + 1, i + 1) if tp[j] < tp[j - 1])
        result[i] = 100 - 100 / (1 + pos / neg) if neg > 0 else 100.0
    return result


def ind_linreg_slope(c, period=50):
    n = len(c)
    result = np.full(n, np.nan)
    xm = np.arange(period, dtype=float) - (period - 1) / 2.0
    for i in range(period - 1, n):
        window = c[i - period + 1:i + 1]
        slope = np.sum(window * xm) / (np.sum(xm ** 2) + 1e-9)
        result[i] = slope / (c[i] + 1e-9) * 100
    return result


def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < WARMUP + 50:
            return None
        if isinstance(bars[0], dict):
            o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
            h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
            lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
            c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[1] >= 5:
                o, h, lo, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
            else:
                return None
        return np.column_stack([o, h, lo, c, v])
    except Exception:
        return None


def compute_indicators(data):
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    I = {}
    k, d = ind_stoch(h, lo, c, 14, 5, 5)
    I["k"] = k; I["d"] = d
    kp = np.roll(k, 1); kp[0] = k[0]
    I["kp"] = kp
    I["stoch_co"] = ((k > d) & (kp <= np.roll(d, 1))).astype(np.int8)
    I["stoch_cu"] = ((k < d) & (kp >= np.roll(d, 1))).astype(np.int8)
    I["rsi"] = ind_rsi(c, 14)
    I["rsi2"] = ind_rsi2(c)
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    I["dc_h"] = dc_h; I["dc_l"] = dc_l; I["dc_mid"] = dc_mid
    dc_h4, dc_l4, _ = ind_donchian(h, lo, 4)
    I["dc_h4"] = dc_h4; I["dc_l4"] = dc_l4
    I["ha"] = ind_heikin_ashi(o, h, lo, c)
    hap = np.roll(I["ha"], 1); hap[0] = I["ha"][0]
    I["hap"] = hap
    I["atr"] = ind_atr(h, lo, c, 14)
    I["sma200"] = _sma_np(c, 200)
    I["sma50"] = _sma_np(c, 50)
    I["ema20"] = _ema_np(c, 20)
    I["mfi"] = ind_mfi(h, lo, c, v, 14)
    I["lr_slope"] = ind_linreg_slope(c, 50)
    # Momentum: 10-day return (for rotation strategy)
    I["ret10"] = np.where(np.roll(c, 10) > 0, (c / np.roll(c, 10) - 1) * 100, 0)
    I["ret10"][0:10] = 0
    I["c"] = c; I["o"] = o; I["h"] = h; I["lo"] = lo; I["v"] = v
    I["c_prev"] = np.roll(c, 1); I["c_prev"][0] = c[0]
    return I


# ═══ STRATEGY CONFIG ═══════════════════════════════════════════════════════

@dataclass
class TradierConfig:
    name: str = "BASELINE"
    # ── ENTRY STRATEGIES ──
    entry_stoch_cross: bool = True          # Stoch K/D crossover
    entry_rsi2_mean_revert: bool = True     # RSI(2) < 3 long, > 97 short
    entry_dc_breakout: bool = True          # Donchian breakout
    entry_ha_flip: bool = True              # HA color flip confirmation
    entry_sma200_filter: bool = True        # Price vs SMA200 trend filter
    entry_mfi_filter: bool = True           # MFI oversold/overbought filter
    # ── EXIT CONDITIONS ──
    exit_stoch_cross: bool = True           # Exit on stoch cross against
    exit_dc_stop: bool = True               # DC-based stops (time-tiered)
    exit_atr_trail: bool = True             # ATR trailing stop (2x ATR)
    exit_rsi2_mean_revert: bool = True      # RSI(2) exit (>70 long, <30 short)
    exit_time_stop: bool = True             # Max hold days
    exit_break_even_guard: bool = True      # Close if peak gain drops to 1%
    exit_sma200_cross: bool = True          # Exit if price crosses SMA200 against
    # ── AUGMENTATION ──
    augment_enabled: bool = True
    augment_dc_tiers: bool = True           # DC tier-based augmentation
    # ── PARAMETERS ──
    min_gain_to_augment: float = 5.0        # From crypto ablation findings
    max_augments: int = 3
    max_hold_days: int = 20                 # Max days to hold
    atr_trail_mult: float = 2.0             # ATR multiplier for trailing stop


# ═══ POSITION TRACKING ═════════════════════════════════════════════════════

@dataclass
class Position:
    side: str
    entry_price: float = 0.0
    entry_bar: int = 0
    size: float = 1.0
    augments: int = 0
    max_gain: float = 0.0
    cost_basis: float = 0.0
    atr_at_entry: float = 0.0


# ═══ BACKTEST ENGINE ═══════════════════════════════════════════════════════

def run_backtest(symbol: str, cfg: TradierConfig, data) -> Optional[dict]:
    I = compute_indicators(data)
    c = I["c"]
    n = len(c)
    if n < WARMUP + 50:
        return None
    rsi2 = I["rsi2"]
    rsi = I["rsi"]
    sma200 = I["sma200"]
    mfi = I["mfi"]
    atr = I["atr"]
    fee_mult = FEE_PCT / 100.0
    # ── Entry signals ──
    long_entry = np.zeros(n, dtype=bool)
    short_entry = np.zeros(n, dtype=bool)
    if cfg.entry_stoch_cross:
        long_entry |= I["stoch_co"].astype(bool)
        short_entry |= I["stoch_cu"].astype(bool)
    if cfg.entry_rsi2_mean_revert:
        long_entry |= (rsi2 < 5)
        short_entry |= (rsi2 > 95)
    if cfg.entry_dc_breakout:
        dc_co = (c > I["dc_mid"]) & (I["c_prev"] <= np.roll(I["dc_mid"], 1))
        dc_cu = (c < I["dc_mid"]) & (I["c_prev"] >= np.roll(I["dc_mid"], 1))
        long_entry |= dc_co
        short_entry |= dc_cu
    if cfg.entry_ha_flip:
        long_entry &= (I["ha"] == 1)
        short_entry &= (I["ha"] == -1)
    if cfg.entry_sma200_filter:
        above_sma = c > sma200
        below_sma = c < sma200
        long_entry &= above_sma
        short_entry &= below_sma
    if cfg.entry_mfi_filter:
        long_entry &= (mfi < 50) | np.isnan(mfi)
        short_entry &= (mfi > 50) | np.isnan(mfi)
    long_entry &= (I["k"] < 65) & (I["k"] > 5)
    short_entry &= (I["k"] > 35) & (I["k"] < 95)
    long_entry[:WARMUP] = False
    short_entry[:WARMUP] = False
    # ── Simulate ──
    trades = []
    position = None
    cooldown_until = 0
    for bar in range(WARMUP, n):
        if shutdown_flag:
            break
        price = c[bar]
        if price <= 0:
            continue
        if position is not None:
            is_long = position.side == "LONG"
            gain_pct = ((price - position.cost_basis) / position.cost_basis * 100) if is_long else ((position.cost_basis - price) / position.cost_basis * 100) if position.cost_basis > 0 else 0.0
            position.max_gain = max(position.max_gain, gain_pct)
            bars_held = bar - position.entry_bar
            should_exit = False
            exit_reason = ""
            # ── EXIT: Break even guard ──
            if cfg.exit_break_even_guard and position.max_gain > 5.0 and gain_pct <= 1.0:
                should_exit = True
                exit_reason = "BREAK_EVEN_GUARD"
            # ── EXIT: ATR trailing stop ──
            if not should_exit and cfg.exit_atr_trail and position.atr_at_entry > 0 and bars_held >= 2:
                atr_stop = cfg.atr_trail_mult * position.atr_at_entry
                if is_long and price < position.entry_price - atr_stop:
                    should_exit = True
                    exit_reason = "ATR_TRAIL_STOP"
                elif not is_long and price > position.entry_price + atr_stop:
                    should_exit = True
                    exit_reason = "ATR_TRAIL_STOP"
            # ── EXIT: DC stop (time-tiered) ──
            if not should_exit and cfg.exit_dc_stop:
                if bars_held <= 5:
                    dc_stop_level = I["dc_l4"][bar] if is_long else I["dc_h4"][bar]
                else:
                    dc_stop_level = I["dc_l"][bar] if is_long else I["dc_h"][bar]
                if not np.isnan(dc_stop_level):
                    if (is_long and price < dc_stop_level * 0.998) or (not is_long and price > dc_stop_level * 1.002):
                        should_exit = True
                        exit_reason = "DC_STOP"
            # ── EXIT: Stoch cross against ──
            if not should_exit and cfg.exit_stoch_cross and gain_pct > 0.3:
                if (is_long and I["stoch_cu"][bar]) or (not is_long and I["stoch_co"][bar]):
                    should_exit = True
                    exit_reason = "STOCH_CROSS_EXIT"
            # ── EXIT: RSI2 mean reversion exit ──
            if not should_exit and cfg.exit_rsi2_mean_revert:
                if (is_long and rsi2[bar] > 70) or (not is_long and rsi2[bar] < 30):
                    should_exit = True
                    exit_reason = "RSI2_EXIT"
            # ── EXIT: Time stop ──
            if not should_exit and cfg.exit_time_stop and bars_held >= cfg.max_hold_days and gain_pct > 0:
                should_exit = True
                exit_reason = "TIME_STOP"
            # ── EXIT: SMA200 cross against ──
            if not should_exit and cfg.exit_sma200_cross and not np.isnan(sma200[bar]):
                if (is_long and price < sma200[bar] and gain_pct < -1.0) or (not is_long and price > sma200[bar] and gain_pct < -1.0):
                    should_exit = True
                    exit_reason = "SMA200_CROSS_EXIT"
            # ── AUGMENT ──
            if not should_exit and cfg.augment_enabled and position.augments < cfg.max_augments and gain_pct >= cfg.min_gain_to_augment:
                aug_ok = False
                if cfg.augment_dc_tiers:
                    if is_long and I["k"][bar] > I["kp"][bar] and I["ha"][bar] == 1:
                        aug_ok = True
                    elif not is_long and I["k"][bar] < I["kp"][bar] and I["ha"][bar] == -1:
                        aug_ok = True
                else:
                    aug_ok = (is_long and I["k"][bar] > I["kp"][bar]) or (not is_long and I["k"][bar] < I["kp"][bar])
                if aug_ok:
                    aug_size = position.size
                    old_cost = position.cost_basis * position.size
                    position.size += aug_size
                    position.cost_basis = (old_cost + price * aug_size) / position.size
                    position.augments += 1
            # ── Execute exit ──
            if should_exit:
                pnl_pct = ((price - position.cost_basis) / position.cost_basis * 100 if is_long else (position.cost_basis - price) / position.cost_basis * 100) - fee_mult * 100 * 2
                trades.append({"side": position.side, "entry": position.cost_basis, "exit": price, "pnl_pct": pnl_pct, "pnl_sized": pnl_pct * position.size, "bars_held": bars_held, "augments": position.augments, "max_gain": position.max_gain, "reason": exit_reason})
                position = None
                cooldown_until = bar + 1
                continue
        # ── Open new position ──
        if position is None and bar > cooldown_until:
            atr_val = atr[bar] if not np.isnan(atr[bar]) else 0.0
            if long_entry[bar]:
                position = Position(side="LONG", entry_price=price, entry_bar=bar, cost_basis=price, atr_at_entry=atr_val)
            elif short_entry[bar]:
                position = Position(side="SHORT", entry_price=price, entry_bar=bar, cost_basis=price, atr_at_entry=atr_val)
    if position is not None:
        price = c[-1]
        is_long = position.side == "LONG"
        pnl_pct = ((price - position.cost_basis) / position.cost_basis * 100 if is_long else (position.cost_basis - price) / position.cost_basis * 100) - fee_mult * 100 * 2
        trades.append({"side": position.side, "entry": position.cost_basis, "exit": price, "pnl_pct": pnl_pct, "pnl_sized": pnl_pct * position.size, "bars_held": len(c) - position.entry_bar, "augments": position.augments, "max_gain": position.max_gain, "reason": "END_OF_DATA"})
    if len(trades) < MIN_TRADES:
        return None
    return _compute_metrics(trades, symbol, cfg)


def _compute_metrics(trades, symbol, cfg):
    rets = np.array([t["pnl_pct"] for t in trades])
    sized_rets = np.array([t["pnl_sized"] for t in trades])
    n_trades = len(rets)
    mean_r = rets.mean()
    std_r = rets.std()
    if std_r <= 0:
        return None
    annual = ANNUAL_BARS.get(ENTRY_TF, 252)
    sharpe = mean_r / std_r * math.sqrt(annual)
    downside = rets[rets < 0]
    down_std = downside.std() if len(downside) > 2 else std_r
    sortino = mean_r / (down_std + 1e-9) * math.sqrt(annual)
    wins = np.sum(rets > 0)
    win_rate = wins / n_trades * 100
    gross_profit = rets[rets > 0].sum() if np.any(rets > 0) else 0
    gross_loss = abs(rets[rets < 0].sum()) if np.any(rets < 0) else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    max_dd = (cum - peak).min()
    avg_bars = np.mean([t["bars_held"] for t in trades])
    augmented = [t for t in trades if t["augments"] > 0]
    aug_count = len(augmented)
    aug_wr = (sum(1 for t in augmented if t["pnl_pct"] > 0) / aug_count * 100) if aug_count > 0 else 0
    reason_counts = defaultdict(int)
    reason_pnl = defaultdict(float)
    for t in trades:
        reason_counts[t["reason"]] += 1
        reason_pnl[t["reason"]] += t["pnl_pct"]
    return {"symbol": symbol, "config": cfg.name, "n_trades": n_trades, "sharpe": round(sharpe, 3), "sortino": round(sortino, 3), "win_rate": round(win_rate, 1), "profit_factor": round(profit_factor, 3), "max_drawdown": round(max_dd, 2), "total_pnl": round(sized_rets.sum(), 2), "mean_pnl": round(mean_r, 4), "avg_bars_held": round(avg_bars, 1), "aug_trades": aug_count, "aug_win_rate": round(aug_wr, 1), "min_gain_augment": cfg.min_gain_to_augment, "reason_counts": dict(reason_counts), "reason_pnl": {k: round(v, 2) for k, v in reason_pnl.items()}}


def _pool_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _worker(args):
    symbol, cfg_dict = args
    cfg = TradierConfig(**cfg_dict)
    data = load_klines(symbol, ENTRY_TF)
    if data is None:
        return None
    try:
        return run_backtest(symbol, cfg, data)
    except Exception:
        return None


def build_ablation_configs() -> List[TradierConfig]:
    configs = [TradierConfig(name="BASELINE")]
    for field_name, label in [
        ("entry_stoch_cross", "NO_STOCH_CROSS_ENTRY"),
        ("entry_rsi2_mean_revert", "NO_RSI2_ENTRY"),
        ("entry_dc_breakout", "NO_DC_BREAKOUT_ENTRY"),
        ("entry_ha_flip", "NO_HA_FLIP_ENTRY"),
        ("entry_sma200_filter", "NO_SMA200_FILTER"),
        ("entry_mfi_filter", "NO_MFI_FILTER"),
        ("exit_stoch_cross", "NO_STOCH_CROSS_EXIT"),
        ("exit_dc_stop", "NO_DC_STOP"),
        ("exit_atr_trail", "NO_ATR_TRAIL"),
        ("exit_rsi2_mean_revert", "NO_RSI2_EXIT"),
        ("exit_time_stop", "NO_TIME_STOP"),
        ("exit_break_even_guard", "NO_BREAK_EVEN_GUARD"),
        ("exit_sma200_cross", "NO_SMA200_CROSS_EXIT"),
    ]:
        cfg = TradierConfig(name=label)
        setattr(cfg, field_name, False)
        configs.append(cfg)
    configs.append(TradierConfig(name="NO_AUGMENT", augment_enabled=False))
    configs.append(TradierConfig(name="NO_DC_TIER_AUG", augment_dc_tiers=False))
    return configs


def build_augment_sweep_configs() -> List[TradierConfig]:
    configs = []
    for pct in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0]:
        configs.append(TradierConfig(name=f"AUG_MIN_GAIN_{pct}", min_gain_to_augment=pct))
    return configs


def get_symbols(max_symbols=0):
    symbols = set()
    for f in KLINES_DIR.iterdir():
        if f.name.endswith(f"_{ENTRY_TF}.json"):
            symbols.add(f.name.replace(f"_{ENTRY_TF}.json", ""))
    symbols = sorted(symbols)
    if max_symbols > 0:
        symbols = symbols[:max_symbols]
    return symbols


def aggregate_results(per_symbol_results):
    valid = [r for r in per_symbol_results if r is not None]
    if not valid:
        return {}
    avg_sharpe = np.mean([r["sharpe"] for r in valid])
    med_sharpe = np.median([r["sharpe"] for r in valid])
    avg_wr = np.mean([r["win_rate"] for r in valid])
    avg_pf = np.mean([r["profit_factor"] for r in valid if r["profit_factor"] < 100])
    avg_dd = np.mean([r["max_drawdown"] for r in valid])
    total_pnl = sum(r["total_pnl"] for r in valid)
    aug_trades = sum(r["aug_trades"] for r in valid)
    aug_wr = np.mean([r["aug_win_rate"] for r in valid if r["aug_trades"] > 0]) if any(r["aug_trades"] > 0 for r in valid) else 0
    reason_counts = defaultdict(int)
    reason_pnl = defaultdict(float)
    for r in valid:
        for k, v in r.get("reason_counts", {}).items():
            reason_counts[k] += v
        for k, v in r.get("reason_pnl", {}).items():
            reason_pnl[k] += v
    return {"n_symbols": len(valid), "n_trades": sum(r["n_trades"] for r in valid), "avg_sharpe": round(avg_sharpe, 3), "median_sharpe": round(med_sharpe, 3), "avg_win_rate": round(avg_wr, 1), "avg_profit_factor": round(avg_pf, 3) if valid else 0, "avg_max_drawdown": round(avg_dd, 2), "total_pnl": round(total_pnl, 2), "aug_trades": aug_trades, "aug_win_rate": round(aug_wr, 1), "reason_counts": dict(reason_counts), "reason_pnl": {k: round(v, 2) for k, v in sorted(reason_pnl.items(), key=lambda x: x[1])}}


def run_phase(configs, symbols, phase_name):
    results = {}
    logger.info(f"═══ {phase_name} ═══ {len(configs)} configs × {len(symbols)} symbols = {len(configs) * len(symbols)} backtests on {N_WORKERS} workers")
    pool = Pool(N_WORKERS, initializer=_pool_init)
    try:
        for ci, cfg in enumerate(configs):
            if shutdown_flag:
                break
            t0 = time.time()
            cfg_dict = asdict(cfg)
            tasks = [(sym, cfg_dict) for sym in symbols]
            symbol_results = pool.map(_worker, tasks, chunksize=max(1, len(symbols) // N_WORKERS))
            agg = aggregate_results(symbol_results)
            agg["config_name"] = cfg.name
            agg["config"] = cfg_dict
            results[cfg.name] = agg
            elapsed = time.time() - t0
            logger.info(f"  [{ci+1}/{len(configs)}] {cfg.name}: Sharpe={agg.get('avg_sharpe', 'N/A')} WR={agg.get('avg_win_rate', 'N/A')}% PF={agg.get('avg_profit_factor', 'N/A')} ({agg.get('n_trades', 0)} trades, {agg.get('n_symbols', 0)} syms, {elapsed:.1f}s)")
    finally:
        pool.close()
        pool.join()
    return results


def print_report(results_file=RESULTS_FILE):
    if not results_file.exists():
        logger.error(f"No results at {results_file}")
        return
    all_results = json.loads(results_file.read_text())
    print("\n" + "═" * 100)
    print("TRADIER ABLATION BACKTEST — Component Impact on Sharpe Ratio (Daily bars, ~4.8 years)")
    print("═" * 100)
    ablation = all_results.get("ablation", {})
    if ablation:
        baseline = ablation.get("BASELINE", {})
        bl_sharpe = baseline.get("avg_sharpe", 0)
        print(f"\n{'Config':<30} {'Sharpe':>8} {'Delta':>8} {'WR%':>7} {'PF':>7} {'DD%':>7} {'Trades':>7} {'Verdict':>10}")
        print("-" * 95)
        for name, m in sorted(ablation.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True):
            delta = m.get("avg_sharpe", 0) - bl_sharpe
            delta_str = f"{delta:+.3f}" if name != "BASELINE" else "---"
            verdict = "REMOVE" if delta > 0.05 and name != "BASELINE" else ("KEEP" if delta < -0.05 and name != "BASELINE" else ("NEUTRAL" if name != "BASELINE" else ""))
            print(f"{name:<30} {m.get('avg_sharpe', 0):>8.3f} {delta_str:>8} {m.get('avg_win_rate', 0):>6.1f}% {m.get('avg_profit_factor', 0):>7.3f} {m.get('avg_max_drawdown', 0):>6.2f}% {m.get('n_trades', 0):>7} {verdict:>10}")
    sweep = all_results.get("augment_sweep", {})
    if sweep:
        print(f"\n{'═' * 70}")
        print("AUGMENT SWEEP — MIN_GAIN_TO_BUY_AGGRESSIVELY")
        print("═" * 70)
        print(f"\n{'Min Gain%':<12} {'Sharpe':>8} {'WR%':>7} {'PF':>7} {'AugTrades':>10} {'TotalPnL':>10}")
        print("-" * 60)
        for name, m in sorted(sweep.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True):
            gain = m.get("config", {}).get("min_gain_to_augment", "?")
            print(f"{gain:<12} {m.get('avg_sharpe', 0):>8.3f} {m.get('avg_win_rate', 0):>6.1f}% {m.get('avg_profit_factor', 0):>7.3f} {m.get('aug_trades', 0):>10} {m.get('total_pnl', 0):>10.1f}")
    ablation = all_results.get("ablation", {})
    if ablation and "BASELINE" in ablation:
        reason_pnl = ablation["BASELINE"].get("reason_pnl", {})
        if reason_pnl:
            print(f"\n{'═' * 60}")
            print("EXIT REASON PnL BREAKDOWN (Baseline)")
            print("═" * 60)
            for reason, pnl in sorted(reason_pnl.items(), key=lambda x: x[1]):
                count = ablation["BASELINE"].get("reason_counts", {}).get(reason, 0)
                avg = pnl / count if count > 0 else 0
                print(f"  {reason:<30} PnL={pnl:>8.1f}% Count={count:>5} Avg={avg:>6.2f}%")
    print()


def main():
    parser = argparse.ArgumentParser(description="Tradier Ablation Backtest")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--symbols", type=int, default=0)
    args = parser.parse_args()
    if args.report:
        print_report()
        return
    symbols = get_symbols(args.symbols)
    logger.info(f"Found {len(symbols)} tradier symbols with {ENTRY_TF} klines")
    all_results = {}
    ablation_configs = build_ablation_configs()
    ablation_results = run_phase(ablation_configs, symbols, "PHASE 1+2: ABLATION")
    all_results["ablation"] = ablation_results
    RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
    sweep_configs = build_augment_sweep_configs()
    sweep_results = run_phase(sweep_configs, symbols, "PHASE 3: AUGMENT SWEEP")
    all_results["augment_sweep"] = sweep_results
    RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
    print_report()


if __name__ == "__main__":
    main()
