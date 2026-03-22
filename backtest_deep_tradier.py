#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
TRADIER DEEP VARIATION BACKTEST — Exhaustive parameter sweep based on ablation findings.

Key finding: RSI(2) mean reversion + SMA200 filter is the core edge.
Now test every variation: RSI periods, thresholds, SMA lengths, exit logic, hold times.

Runs for hours, saves incrementally.
"""
import os, sys, json, math, time, signal, logging, warnings, itertools
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from numpy.lib.stride_tricks import sliding_window_view

logging.basicConfig(level=logging.INFO, format='%(asctime)s [DEEP_T] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

import platform
if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache" / "tradier"
RESULTS_DIR = BASE_PATH / "data" / "backtest_deep_tradier"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "deep_results.json"
BEST_FILE = RESULTS_DIR / "deep_best.json"
FEE_PCT = 0.10
ANNUAL_BARS = {"5m": 19656, "15m": 6552, "1h": 1638, "4h": 410, "D": 252}
N_WORKERS = max(1, cpu_count() - 1)
MIN_TRADES = 10
WARMUP = 250
ENTRY_TF = "D"
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
    clean = np.where(np.isnan(arr), arr[~np.isnan(arr)].mean() if np.any(~np.isnan(arr)) else 50.0, arr)
    cum = np.cumsum(clean)
    cum[period:] = cum[period:] - cum[:-period]
    result = np.full_like(arr, np.nan)
    result[period - 1:] = cum[period - 1:] / period
    return result


def _ema_np(arr, period):
    result = np.empty_like(arr); result[:] = np.nan
    if len(arr) < period: return result
    mult = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = arr[i] * mult + result[i - 1] * (1 - mult)
    return result


def ind_rsi(c, period=14):
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema_np(gain, period)
    avg_loss = _ema_np(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - 100 / (1 + rs)


def ind_stoch(h, lo, c, k_period=14, sk=5, sd=5):
    n = len(c)
    if n >= k_period:
        hh = np.max(sliding_window_view(h, k_period), axis=1)
        ll = np.min(sliding_window_view(lo, k_period), axis=1)
        raw_k = np.full(n, 50.0)
        denom = hh - ll; valid = denom > 0
        raw_k[k_period - 1:] = np.where(valid, (c[k_period - 1:] - ll) / denom * 100, 50.0)
    else:
        raw_k = np.full(n, 50.0)
    return _sma_np(raw_k, sk), _sma_np(raw_k, sd)


def ind_donchian(h, lo, period=20):
    n = len(h)
    dc_h = np.full(n, np.nan); dc_l = np.full(n, np.nan)
    if n >= period:
        dc_h[period - 1:] = np.max(sliding_window_view(h, period), axis=1)
        dc_l[period - 1:] = np.min(sliding_window_view(lo, period), axis=1)
    return dc_h, dc_l, (dc_h + dc_l) / 2


def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4
    ha_o = np.empty_like(o); ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)


def ind_atr(h, lo, c, period=14):
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))))
    tr[0] = h[0] - lo[0]
    return _ema_np(tr, period)


def ind_mfi(h, lo, c, v, period=14):
    tp = (h + lo + c) / 3; mf = tp * v; n = len(c)
    result = np.full(n, np.nan)
    for i in range(period, n):
        pos = sum(mf[j] for j in range(i - period + 1, i + 1) if tp[j] > tp[j - 1])
        neg = sum(mf[j] for j in range(i - period + 1, i + 1) if tp[j] < tp[j - 1])
        result[i] = 100 - 100 / (1 + pos / neg) if neg > 0 else 100.0
    return result


def ind_bb(c, period=20, std_mult=2.0):
    mid = _sma_np(c, period)
    std = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)):
        std[i] = np.std(c[i - period + 1:i + 1])
    return mid + std_mult * std, mid, mid - std_mult * std


def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < WARMUP + 50: return None
        if isinstance(bars[0], dict):
            o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
            h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
            lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
            c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            o, h, lo, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
        return np.column_stack([o, h, lo, c, v])
    except Exception: return None


# ═══ PARAMETERIZED CONFIG ═════════════════════════════════════════════════

@dataclass
class DeepConfig:
    name: str = "DEFAULT"
    # Entry
    rsi_period: int = 2              # RSI period for mean-reversion entry
    rsi_entry_long: float = 5.0      # Buy when RSI < this
    rsi_entry_short: float = 95.0    # Short when RSI > this
    use_stoch_entry: bool = False     # Ablation says REMOVE
    use_dc_breakout: bool = True      # Ablation says KEEP
    use_ha_confirm: bool = False      # Ablation says REMOVE
    use_sma_filter: bool = True       # Ablation says KEEP
    sma_period: int = 200             # Trend filter SMA period
    use_mfi_filter: bool = True       # Ablation says KEEP
    mfi_threshold: float = 50.0       # MFI gate
    use_bb_filter: bool = False       # NEW: Bollinger band filter
    bb_entry_pct: float = 0.1        # Enter when BB% < this (long) or > 1-this (short)
    # Exit
    rsi_exit_long: float = 70.0      # Exit long when RSI > this
    rsi_exit_short: float = 30.0     # Exit short when RSI < this
    use_atr_trail: bool = False       # Ablation says REMOVE
    atr_trail_mult: float = 2.0
    use_dc_stop: bool = False         # Nearly neutral
    use_sma_cross_exit: bool = False  # Ablation says destructive
    use_profit_target: bool = False   # NEW: fixed % profit target
    profit_target_pct: float = 5.0
    use_time_stop: bool = False
    max_hold_days: int = 20
    # Walk-forward
    walk_forward_split: float = 0.7   # 70% in-sample, 30% out-of-sample


# ═══ BACKTEST ENGINE ═══════════════════════════════════════════════════════

def run_backtest(symbol, cfg, data, oos_only=False):
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    if n < WARMUP + 50: return None
    # Indicators
    rsi = ind_rsi(c, cfg.rsi_period)
    sma = _sma_np(c, cfg.sma_period)
    mfi = ind_mfi(h, lo, c, v, 14)
    atr = ind_atr(h, lo, c, 14)
    ha = ind_heikin_ashi(o, h, lo, c)
    k, d = ind_stoch(h, lo, c, 14, 5, 5)
    kp = np.roll(k, 1); kp[0] = k[0]
    stoch_co = ((k > d) & (kp <= np.roll(d, 1))).astype(bool)
    stoch_cu = ((k < d) & (kp >= np.roll(d, 1))).astype(bool)
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    dc_h4, dc_l4, _ = ind_donchian(h, lo, 4)
    c_prev = np.roll(c, 1); c_prev[0] = c[0]
    if cfg.use_bb_filter:
        bb_u, bb_m, bb_l = ind_bb(c, 20, 2.0)
        bb_pct = np.where((bb_u - bb_l) > 0, (c - bb_l) / (bb_u - bb_l), 0.5)
    else:
        bb_pct = np.full(n, 0.5)
    # Walk-forward: determine test range
    split_bar = int(n * cfg.walk_forward_split) if oos_only else WARMUP
    start_bar = max(split_bar, WARMUP)
    # Entry signals
    long_entry = (rsi < cfg.rsi_entry_long)
    short_entry = (rsi > cfg.rsi_entry_short)
    if cfg.use_stoch_entry:
        long_entry |= stoch_co
        short_entry |= stoch_cu
    if cfg.use_dc_breakout:
        long_entry |= ((c > dc_mid) & (c_prev <= np.roll(dc_mid, 1)))
        short_entry |= ((c < dc_mid) & (c_prev >= np.roll(dc_mid, 1)))
    if cfg.use_ha_confirm:
        long_entry &= (ha == 1)
        short_entry &= (ha == -1)
    if cfg.use_sma_filter:
        long_entry &= (c > sma)
        short_entry &= (c < sma)
    if cfg.use_mfi_filter:
        long_entry &= ((mfi < cfg.mfi_threshold) | np.isnan(mfi))
        short_entry &= ((mfi > (100 - cfg.mfi_threshold)) | np.isnan(mfi))
    if cfg.use_bb_filter:
        long_entry &= (bb_pct < cfg.bb_entry_pct)
        short_entry &= (bb_pct > (1.0 - cfg.bb_entry_pct))
    long_entry[:start_bar] = False
    short_entry[:start_bar] = False
    # Simulate
    trades = []
    position = None
    fee_mult = FEE_PCT / 100.0
    cooldown_until = 0
    for bar in range(start_bar, n):
        if shutdown_flag: break
        price = c[bar]
        if price <= 0: continue
        if position is not None:
            is_long = position.side == "LONG"
            gain_pct = ((price - position.cost_basis) / position.cost_basis * 100) if is_long else ((position.cost_basis - price) / position.cost_basis * 100) if position.cost_basis > 0 else 0.0
            position.max_gain = max(position.max_gain, gain_pct)
            bars_held = bar - position.entry_bar
            should_exit = False; exit_reason = ""
            # RSI mean reversion exit (THE star)
            if (is_long and rsi[bar] > cfg.rsi_exit_long) or (not is_long and rsi[bar] < cfg.rsi_exit_short):
                should_exit = True; exit_reason = "RSI_EXIT"
            # Profit target
            if not should_exit and cfg.use_profit_target and gain_pct >= cfg.profit_target_pct:
                should_exit = True; exit_reason = "PROFIT_TARGET"
            # ATR trail
            if not should_exit and cfg.use_atr_trail and position.atr_at_entry > 0 and bars_held >= 2:
                stop = cfg.atr_trail_mult * position.atr_at_entry
                if (is_long and price < position.entry_price - stop) or (not is_long and price > position.entry_price + stop):
                    should_exit = True; exit_reason = "ATR_TRAIL"
            # DC stop
            if not should_exit and cfg.use_dc_stop:
                lvl = dc_l4[bar] if (bars_held <= 5 and is_long) else (dc_h4[bar] if (bars_held <= 5 and not is_long) else (dc_l[bar] if is_long else dc_h[bar]))
                if not np.isnan(lvl):
                    if (is_long and price < lvl * 0.998) or (not is_long and price > lvl * 1.002):
                        should_exit = True; exit_reason = "DC_STOP"
            # SMA cross exit
            if not should_exit and cfg.use_sma_cross_exit and not np.isnan(sma[bar]):
                if (is_long and price < sma[bar] and gain_pct < -1.0) or (not is_long and price > sma[bar] and gain_pct < -1.0):
                    should_exit = True; exit_reason = "SMA_CROSS_EXIT"
            # Time stop
            if not should_exit and cfg.use_time_stop and bars_held >= cfg.max_hold_days:
                should_exit = True; exit_reason = "TIME_STOP"
            if should_exit:
                pnl = gain_pct - fee_mult * 100 * 2
                trades.append({"pnl_pct": pnl, "bars_held": bars_held, "reason": exit_reason, "max_gain": position.max_gain, "side": position.side})
                position = None; cooldown_until = bar + 1
                continue
        if position is None and bar > cooldown_until:
            atr_val = atr[bar] if not np.isnan(atr[bar]) else 0
            if long_entry[bar]:
                position = Position(side="LONG", entry_price=price, entry_bar=bar, cost_basis=price, atr_at_entry=atr_val)
            elif short_entry[bar]:
                position = Position(side="SHORT", entry_price=price, entry_bar=bar, cost_basis=price, atr_at_entry=atr_val)
    if position is not None:
        is_long = position.side == "LONG"
        gain_pct = ((c[-1] - position.cost_basis) / position.cost_basis * 100) if is_long else ((position.cost_basis - c[-1]) / position.cost_basis * 100)
        trades.append({"pnl_pct": gain_pct - FEE_PCT / 100 * 2 * 100, "bars_held": n - position.entry_bar, "reason": "END", "max_gain": position.max_gain, "side": position.side})
    if len(trades) < MIN_TRADES: return None
    rets = np.array([t["pnl_pct"] for t in trades])
    mean_r = rets.mean(); std_r = rets.std()
    if std_r <= 0: return None
    annual = ANNUAL_BARS.get(ENTRY_TF, 252)
    sharpe = mean_r / std_r * math.sqrt(annual)
    wins = np.sum(rets > 0)
    gross_p = rets[rets > 0].sum() if np.any(rets > 0) else 0
    gross_l = abs(rets[rets < 0].sum()) if np.any(rets < 0) else 1e-9
    cum = np.cumsum(rets); peak = np.maximum.accumulate(cum)
    reason_pnl = defaultdict(float)
    reason_cnt = defaultdict(int)
    for t in trades:
        reason_pnl[t["reason"]] += t["pnl_pct"]
        reason_cnt[t["reason"]] += 1
    return {"symbol": symbol, "n_trades": len(rets), "sharpe": round(sharpe, 3), "win_rate": round(wins / len(rets) * 100, 1), "profit_factor": round(gross_p / gross_l, 3) if gross_l > 0 else 999, "max_drawdown": round((cum - peak).min(), 2), "total_pnl": round(rets.sum(), 2), "mean_pnl": round(mean_r, 4), "avg_bars": round(np.mean([t["bars_held"] for t in trades]), 1), "reason_pnl": {k: round(v, 1) for k, v in reason_pnl.items()}, "reason_cnt": dict(reason_cnt)}


@dataclass
class Position:
    side: str; entry_price: float = 0.0; entry_bar: int = 0; cost_basis: float = 0.0; atr_at_entry: float = 0.0; max_gain: float = 0.0; size: float = 1.0


def _pool_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _worker(args):
    symbol, cfg_dict, oos = args
    cfg = DeepConfig(**cfg_dict)
    data = load_klines(symbol, ENTRY_TF)
    if data is None: return None
    try: return run_backtest(symbol, cfg, data, oos)
    except Exception: return None


def get_symbols(max_n=0):
    syms = sorted({f.name.replace(f"_{ENTRY_TF}.json", "") for f in KLINES_DIR.iterdir() if f.name.endswith(f"_{ENTRY_TF}.json")})
    return syms[:max_n] if max_n > 0 else syms


def aggregate(results):
    valid = [r for r in results if r is not None]
    if not valid: return {}
    sharpes = [r["sharpe"] for r in valid]
    return {"n_syms": len(valid), "n_trades": sum(r["n_trades"] for r in valid), "avg_sharpe": round(np.mean(sharpes), 3), "med_sharpe": round(np.median(sharpes), 3), "avg_wr": round(np.mean([r["win_rate"] for r in valid]), 1), "avg_pf": round(np.mean([r["profit_factor"] for r in valid if r["profit_factor"] < 100]), 3), "avg_dd": round(np.mean([r["max_drawdown"] for r in valid]), 2), "total_pnl": round(sum(r["total_pnl"] for r in valid), 1), "avg_bars": round(np.mean([r["avg_bars"] for r in valid]), 1)}


def run_config(pool, cfg, symbols, oos=False):
    tasks = [(s, asdict(cfg), oos) for s in symbols]
    results = pool.map(_worker, tasks, chunksize=max(1, len(symbols) // N_WORKERS))
    return aggregate(results)


# ═══ VARIATION GENERATOR ═════════════════════════════════════════════════

def generate_all_configs():
    """Generate exhaustive parameter grid. ~2000+ configs."""
    configs = []
    # ── PHASE A: RSI period × entry threshold × exit threshold ──
    for rsi_p in [2, 3, 4, 5, 7, 10, 14]:
        for entry_l, entry_s in [(3, 97), (5, 95), (7, 93), (10, 90), (15, 85), (20, 80), (25, 75), (30, 70)]:
            for exit_l, exit_s in [(50, 50), (55, 45), (60, 40), (65, 35), (70, 30), (75, 25), (80, 20), (90, 10)]:
                configs.append(DeepConfig(name=f"RSI{rsi_p}_E{entry_l}/{entry_s}_X{exit_l}/{exit_s}", rsi_period=rsi_p, rsi_entry_long=entry_l, rsi_entry_short=entry_s, rsi_exit_long=exit_l, rsi_exit_short=exit_s))
    # ── PHASE B: SMA period sweep ──
    for sma_p in [20, 50, 100, 150, 200, 300]:
        configs.append(DeepConfig(name=f"SMA{sma_p}", sma_period=sma_p))
        configs.append(DeepConfig(name=f"SMA{sma_p}_noMFI", sma_period=sma_p, use_mfi_filter=False))
    # ── PHASE C: No SMA filter (pure mean reversion) ──
    for rsi_p in [2, 3, 5]:
        for entry_l in [5, 10, 15]:
            for exit_l in [60, 70, 80]:
                configs.append(DeepConfig(name=f"PURE_RSI{rsi_p}_E{entry_l}_X{exit_l}", rsi_period=rsi_p, rsi_entry_long=entry_l, rsi_entry_short=100-entry_l, rsi_exit_long=exit_l, rsi_exit_short=100-exit_l, use_sma_filter=False))
    # ── PHASE D: Bollinger band filter ──
    for bb_pct in [0.0, 0.05, 0.1, 0.15, 0.2]:
        configs.append(DeepConfig(name=f"BB{bb_pct}", use_bb_filter=True, bb_entry_pct=bb_pct))
    # ── PHASE E: Profit target ──
    for pt in [1.0, 2.0, 3.0, 5.0, 7.0, 10.0]:
        configs.append(DeepConfig(name=f"PT{pt}", use_profit_target=True, profit_target_pct=pt))
    # ── PHASE F: Time stops ──
    for days in [3, 5, 7, 10, 15, 20, 30, 50]:
        configs.append(DeepConfig(name=f"TSTOP{days}d", use_time_stop=True, max_hold_days=days))
    # ── PHASE G: DC breakout ON/OFF with different combos ──
    configs.append(DeepConfig(name="NO_DC_NO_MFI", use_dc_breakout=False, use_mfi_filter=False))
    configs.append(DeepConfig(name="DC_ONLY_ENTRY", use_dc_breakout=True, rsi_entry_long=0, rsi_entry_short=100))
    # ── PHASE H: Stoch entry back (with RSI exit) ──
    configs.append(DeepConfig(name="STOCH+RSI2", use_stoch_entry=True))
    configs.append(DeepConfig(name="STOCH+RSI2+HA", use_stoch_entry=True, use_ha_confirm=True))
    # ── PHASE I: Best combo from ablation + variations ──
    # RSI(2) < 5, exit > 70, SMA200, MFI, DC breakout, no stoch, no HA, no ATR trail
    for rsi_p in [2, 3]:
        for entry in [3, 5, 7]:
            for exit_val in [60, 65, 70, 75, 80]:
                for sma in [100, 150, 200]:
                    configs.append(DeepConfig(name=f"GOLDEN_R{rsi_p}_E{entry}_X{exit_val}_S{sma}", rsi_period=rsi_p, rsi_entry_long=entry, rsi_entry_short=100-entry, rsi_exit_long=exit_val, rsi_exit_short=100-exit_val, sma_period=sma))
    return configs


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--symbols", type=int, default=0)
    parser.add_argument("--max-configs", type=int, default=0)
    args = parser.parse_args()
    if args.report:
        if not RESULTS_FILE.exists():
            logger.error("No results file"); return
        results = json.loads(RESULTS_FILE.read_text())
        # Sort by Sharpe and show top 30
        sorted_r = sorted(results.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)
        print("\n" + "═" * 100)
        print("TRADIER DEEP VARIATION RESULTS — Top 30 by Sharpe (Daily bars, ~4.8 years)")
        print("═" * 100)
        print(f"\n{'#':>3} {'Config':<45} {'Sharpe':>8} {'WR%':>7} {'PF':>7} {'DD%':>7} {'Trades':>7} {'AvgBars':>8} {'TotalPnL':>10}")
        print("-" * 105)
        for i, (name, m) in enumerate(sorted_r[:30]):
            print(f"{i+1:>3} {name:<45} {m.get('avg_sharpe', 0):>8.3f} {m.get('avg_wr', 0):>6.1f}% {m.get('avg_pf', 0):>7.3f} {m.get('avg_dd', 0):>6.2f}% {m.get('n_trades', 0):>7} {m.get('avg_bars', 0):>8.1f} {m.get('total_pnl', 0):>10.1f}")
        print(f"\nWorst 10:")
        for i, (name, m) in enumerate(sorted_r[-10:]):
            print(f"  {name:<45} Sharpe={m.get('avg_sharpe', 0):>8.3f}")
        print(f"\nTotal configs tested: {len(results)}")
        # Show best by category
        categories = {"RSI": [], "SMA": [], "PURE": [], "BB": [], "PT": [], "TSTOP": [], "GOLDEN": [], "STOCH": [], "DC": []}
        for name, m in results.items():
            for cat in categories:
                if name.startswith(cat) or cat in name:
                    categories[cat].append((name, m)); break
        print(f"\n{'═' * 60}")
        print("BEST PER CATEGORY")
        print("═" * 60)
        for cat, items in categories.items():
            if items:
                best = max(items, key=lambda x: x[1].get("avg_sharpe", -999))
                print(f"  {cat:<10} → {best[0]:<40} Sharpe={best[1].get('avg_sharpe', 0):.3f} WR={best[1].get('avg_wr', 0):.1f}% PF={best[1].get('avg_pf', 0):.3f}")
        return
    symbols = get_symbols(args.symbols)
    logger.info(f"Found {len(symbols)} tradier symbols with {ENTRY_TF} klines")
    configs = generate_all_configs()
    if args.max_configs > 0:
        configs = configs[:args.max_configs]
    logger.info(f"Generated {len(configs)} configs to test")
    # Load existing results
    all_results = {}
    if RESULTS_FILE.exists():
        try: all_results = json.loads(RESULTS_FILE.read_text())
        except Exception: pass
    tested = set(all_results.keys())
    remaining = [c for c in configs if c.name not in tested]
    logger.info(f"Already tested: {len(tested)}, remaining: {len(remaining)}")
    pool = Pool(N_WORKERS, initializer=_pool_init)
    save_interval = 60
    last_save = time.time()
    best_sharpe = max((v.get("avg_sharpe", -999) for v in all_results.values()), default=-999)
    try:
        for ci, cfg in enumerate(remaining):
            if shutdown_flag: break
            t0 = time.time()
            # In-sample
            agg = run_config(pool, cfg, symbols, oos=False)
            if not agg:
                continue
            agg["config"] = asdict(cfg)
            all_results[cfg.name] = agg
            elapsed = time.time() - t0
            marker = ""
            if agg.get("avg_sharpe", -999) > best_sharpe:
                best_sharpe = agg["avg_sharpe"]
                marker = " ★ NEW BEST"
                # Run OOS validation for new bests
                oos_agg = run_config(pool, cfg, symbols, oos=True)
                if oos_agg:
                    agg["oos_sharpe"] = oos_agg.get("avg_sharpe")
                    agg["oos_wr"] = oos_agg.get("avg_wr")
                    agg["oos_pf"] = oos_agg.get("avg_pf")
                    agg["oos_pnl"] = oos_agg.get("total_pnl")
                    marker += f" (OOS: Sharpe={oos_agg.get('avg_sharpe', 0):.3f} WR={oos_agg.get('avg_wr', 0):.1f}%)"
            logger.info(f"  [{ci+1}/{len(remaining)}] {cfg.name}: Sharpe={agg.get('avg_sharpe', 'N/A')} WR={agg.get('avg_wr', 'N/A')}% PF={agg.get('avg_pf', 'N/A')} ({agg.get('n_trades', 0)} trades, {elapsed:.1f}s){marker}")
            # Incremental save
            if time.time() - last_save > save_interval:
                RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
                last_save = time.time()
    finally:
        pool.close()
        pool.join()
    RESULTS_FILE.write_text(json.dumps(all_results, indent=2, default=str))
    # Save best configs
    sorted_best = sorted(all_results.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)[:20]
    BEST_FILE.write_text(json.dumps(dict(sorted_best), indent=2, default=str))
    logger.info(f"Done. {len(all_results)} configs tested. Best Sharpe: {best_sharpe:.3f}. Results: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
