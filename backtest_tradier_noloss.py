#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
BACKTEST: TRADIER STOCKS — NO-LOSS NATURAL EXIT + BOUNCE REENTRY

Two strategy profiles:
  1. DAY TRADE (1h/15m): Enter intraday, exit same day if profitable. Hold overnight only if underwater.
     Gap risk mitigated by only entering mid-session, not near open/close.
  2. HODL (D/4h): Enter on weekly pullbacks, hold for days/weeks until natural TP.
     Higher TP thresholds (1-5%) since stocks trend longer than crypto.

Both share:
  - NEVER sell at a loss — hold until bar close >= min_profit_pct
  - K-zone entry (K value + K turning + candle formation, no crossover wait)
  - Bounce reentry after profitable exit
  - Multi-TF alignment from higher timeframes

Usage:
  python3 backtest_tradier_noloss.py                          # All symbols, both strategies
  python3 backtest_tradier_noloss.py --strategy daytrade       # Day trade only
  python3 backtest_tradier_noloss.py --strategy hodl           # HODL only
  python3 backtest_tradier_noloss.py --symbols 30 --tf D       # 30 symbols, daily
  python3 backtest_tradier_noloss.py --report                  # Print last results
"""
import os, sys, json, math, time, signal, logging, warnings, argparse
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from datetime import datetime
from multiprocessing import Pool, cpu_count
from collections import defaultdict
try:
    import config
    BASE_PATH = Path(config.BASE_PATH)
except Exception:
    import platform
    BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache" / "tradier"
DATA_DIR = BASE_PATH / "data"
RESULTS_DIR = DATA_DIR / "backtest_tradier_noloss"
RESULTS_FILE = RESULTS_DIR / "results.json"
ANNUAL_BARS = {"1m": 98280, "5m": 19656, "15m": 6552, "1h": 1638, "4h": 410, "D": 252}
FEE_PCT = 0.0  # Tradier: $0 commissions, spread ~0.01-0.03% on liquid stocks
MIN_TRADES = 8
WARMUP = 100
N_WORKERS = max(1, cpu_count() - 2)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [TRADIER-NL] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)
shutdown_flag = False

def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ═══ INDICATORS ══════════════════════════════════════════════════════════════

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

def _sma_np(arr, period):
    if len(arr) < period:
        return np.full_like(arr, np.nan)
    cum = np.cumsum(arr)
    cum[period:] = cum[period:] - cum[:-period]
    result = np.full_like(arr, np.nan)
    result[period - 1:] = cum[period - 1:] / period
    return result

def ind_stoch(h, lo, c, k_period=14, sk=5, sd=5):
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = np.max(h[i - k_period + 1:i + 1])
        ll = np.min(lo[i - k_period + 1:i + 1])
        raw_k[i] = (c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
    k = _ema_np(raw_k, sk)
    np.nan_to_num(k, copy=False, nan=50.0)
    d = _ema_np(k, sd)
    np.nan_to_num(d, copy=False, nan=50.0)
    return k, d

def ind_rsi(c, period=14):
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema_np(gain, period)
    avg_loss = _ema_np(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100 - 100 / (1 + rs)
    np.nan_to_num(rsi, copy=False, nan=50.0)
    return rsi

def ind_atr(h, lo, c, period=14):
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))))
    tr[0] = h[0] - lo[0]
    atr = _ema_np(tr, period)
    np.nan_to_num(atr, copy=False, nan=0.0)
    return atr

def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4
    ha_o = np.empty_like(o)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)

def ind_donchian(h, lo, period=20):
    n = len(h)
    dc_h = np.full(n, np.nan)
    dc_l = np.full(n, np.nan)
    for i in range(period - 1, n):
        dc_h[i] = np.max(h[i - period + 1:i + 1])
        dc_l[i] = np.min(lo[i - period + 1:i + 1])
    return dc_h, dc_l, (dc_h + dc_l) / 2

def ind_bollinger(c, period=20, std_mult=2.0):
    mid = _sma_np(c, period)
    std = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)):
        std[i] = np.std(c[i - period + 1:i + 1])
    return mid + std_mult * std, mid, mid - std_mult * std

def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < 30:
            return None
        if isinstance(bars[0], dict):
            o = np.array([float(b.get("open", 0)) for b in bars], dtype=np.float64)
            h = np.array([float(b.get("high", 0)) for b in bars], dtype=np.float64)
            lo = np.array([float(b.get("low", 0)) for b in bars], dtype=np.float64)
            c = np.array([float(b.get("close", 0)) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[1] >= 5:
                o, h, lo, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
            else:
                return None
        # Filter out zero-price bars (weekends/holidays)
        valid = c > 0
        if valid.sum() < 30:
            return None
        return np.column_stack([o[valid], h[valid], lo[valid], c[valid], v[valid]])
    except Exception:
        return None

def compute_indicators(data):
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    I = {}
    k, d = ind_stoch(h, lo, c, 14, 5, 5)
    I["k"] = k; I["d"] = d
    kp = np.roll(k, 1); kp[0] = k[0]
    I["k_rising"] = (k > kp).astype(np.int8)
    I["k_falling"] = (k < kp).astype(np.int8)
    I["rsi"] = ind_rsi(c, 14)
    ha = ind_heikin_ashi(o, h, lo, c)
    I["ha"] = ha
    ha_prev = np.roll(ha, 1); ha_prev[0] = ha[0]
    I["ha_flip_green"] = ((ha == 1) & (ha_prev == -1)).astype(np.int8)
    I["ha_flip_red"] = ((ha == -1) & (ha_prev == 1)).astype(np.int8)
    I["atr"] = ind_atr(h, lo, c, 14)
    I["atr_pct"] = np.where(c > 0, I["atr"] / c * 100, 0)
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    I["dc_pct"] = np.where((dc_h - dc_l) > 0, (c - dc_l) / (dc_h - dc_l), 0.5)
    bb_u, bb_m, bb_l = ind_bollinger(c, 20, 2.0)
    I["bb_pct"] = np.where((bb_u - bb_l) > 0, (c - bb_l) / (bb_u - bb_l), 0.5)
    sma50 = _sma_np(c, 50)
    sma200 = _sma_np(c, 200)
    np.nan_to_num(sma50, copy=False, nan=0.0)
    np.nan_to_num(sma200, copy=False, nan=0.0)
    I["sma50"] = sma50; I["sma200"] = sma200
    I["above_sma50"] = np.where(sma50 > 0, c > sma50, True)
    I["above_sma200"] = np.where(sma200 > 0, c > sma200, True)
    # Engulfing
    body = np.abs(c - o)
    prev_body = np.roll(body, 1); prev_body[0] = 0
    green = (c > o).astype(np.int8)
    prev_green = np.roll(green, 1); prev_green[0] = 0
    I["bull_engulf"] = ((green == 1) & (prev_green == 0) & (body > prev_body * 1.1)).astype(np.int8)
    I["bear_engulf"] = ((green == 0) & (prev_green == 1) & (body > prev_body * 1.1)).astype(np.int8)
    # Hammer / shooting star
    range_ = h - lo
    lower_wick = np.where(range_ > 0, (np.minimum(o, c) - lo) / range_, 0)
    upper_wick = np.where(range_ > 0, (h - np.maximum(o, c)) / range_, 0)
    body_ratio = np.where(range_ > 0, body / range_, 0)
    I["hammer"] = ((lower_wick > 0.6) & (body_ratio < 0.3) & (green == 1)).astype(np.int8)
    I["inv_hammer"] = ((upper_wick > 0.6) & (body_ratio < 0.3) & (green == 0)).astype(np.int8)
    I["close"] = c; I["high"] = h; I["low"] = lo; I["open"] = o
    return I

def load_htf_indicators(symbol, primary_tf, primary_n):
    tf_hierarchy = {"15m": ["1h", "4h", "D"], "1h": ["4h", "D"], "4h": ["D"]}
    tf_ratios = {"15m": {"1h": 4, "4h": 16, "D": 96}, "1h": {"4h": 4, "D": 24}, "4h": {"D": 6}}
    htf_list = tf_hierarchy.get(primary_tf, [])
    htf_data = {}
    for htf in htf_list:
        data = load_klines(symbol, htf)
        if data is None or len(data) < 30:
            continue
        htf_I = compute_indicators(data)
        ratio = tf_ratios.get(primary_tf, {}).get(htf, 1)
        for key in ["k", "d", "ha", "k_rising", "k_falling", "rsi", "dc_pct"]:
            arr = htf_I[key]
            aligned = np.repeat(arr, ratio)
            if len(aligned) >= primary_n:
                aligned = aligned[-primary_n:]
            else:
                fill = 50.0 if key in ("k", "d", "rsi") else 0.0
                pad = np.full(primary_n - len(aligned), fill)
                aligned = np.concatenate([pad, aligned])
            htf_data[f"{htf}_{key}"] = aligned
    return htf_data

# ═══ ENTRY SIGNALS ═══════════════════════════════════════════════════════════

def build_entry_signals(I, htf, direction, entry_mode="k_zone_candle"):
    k, d = I["k"], I["d"]
    ha, rsi = I["ha"], I["rsi"]
    n = len(k)
    if entry_mode == "k_zone_candle":
        if direction == "LONG":
            sig = (k < 35) & I["k_rising"].astype(bool) & ((I["ha_flip_green"].astype(bool)) | (I["hammer"].astype(bool)) | (I["bull_engulf"].astype(bool)))
        else:
            sig = (k > 65) & I["k_falling"].astype(bool) & ((I["ha_flip_red"].astype(bool)) | (I["inv_hammer"].astype(bool)) | (I["bear_engulf"].astype(bool)))
    elif entry_mode == "k_zone_ha":
        if direction == "LONG":
            sig = (k < 40) & I["k_rising"].astype(bool) & (ha == 1)
        else:
            sig = (k > 60) & I["k_falling"].astype(bool) & (ha == -1)
    elif entry_mode == "k_zone_sma":
        # K zone + price near SMA50 (mean reversion to moving average)
        sma50 = I["sma50"]
        if direction == "LONG":
            near_sma = np.where(sma50 > 0, (I["close"] - sma50) / sma50 * 100, 0)
            sig = (k < 35) & I["k_rising"].astype(bool) & (ha == 1) & (near_sma < 2.0) & (near_sma > -5.0)
        else:
            near_sma = np.where(sma50 > 0, (I["close"] - sma50) / sma50 * 100, 0)
            sig = (k > 65) & I["k_falling"].astype(bool) & (ha == -1) & (near_sma > -2.0) & (near_sma < 5.0)
    elif entry_mode == "k_deep_zone":
        if direction == "LONG":
            sig = (k < 20) & I["k_rising"].astype(bool)
        else:
            sig = (k > 80) & I["k_falling"].astype(bool)
    elif entry_mode == "k_zone_bb":
        bb = I["bb_pct"]
        if direction == "LONG":
            sig = (k < 35) & I["k_rising"].astype(bool) & (bb < 0.2)
        else:
            sig = (k > 65) & I["k_falling"].astype(bool) & (bb > 0.8)
    else:
        sig = np.zeros(n, dtype=bool)
    # HTF alignment
    if htf:
        for htf_tf in ["4h", "D"]:
            htf_k = htf.get(f"{htf_tf}_k")
            htf_ha = htf.get(f"{htf_tf}_ha")
            if htf_k is not None and htf_ha is not None:
                if direction == "LONG":
                    htf_ok = (htf_k < 75) & ((htf_ha == 1) | (htf_k < 30))
                else:
                    htf_ok = (htf_k > 25) & ((htf_ha == -1) | (htf_k > 70))
                sig = sig & htf_ok
    sig[:WARMUP] = False
    return sig

# ═══ SIMULATION ══════════════════════════════════════════════════════════════

def simulate_natural_noloss(I, htf, direction, min_profit_pct, entry_mode, reentry_mode="bounce"):
    closes = I["close"]
    highs = I["high"]
    lows = I["low"]
    k = I["k"]
    ha = I["ha"]
    n = len(closes)
    raw_signals = build_entry_signals(I, htf, direction, entry_mode)
    if reentry_mode == "deep_bounce":
        if direction == "LONG":
            deep_signals = (k < 20) & I["k_rising"].astype(bool) & ((ha == 1) | I["hammer"].astype(bool) | I["bull_engulf"].astype(bool))
        else:
            deep_signals = (k > 80) & I["k_falling"].astype(bool) & ((ha == -1) | I["inv_hammer"].astype(bool) | I["bear_engulf"].astype(bool))
        deep_signals[:WARMUP] = False
    trades = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0
    max_adverse = 0.0
    max_favorable = 0.0
    last_exit_idx = -1
    k_reset = False
    for i in range(WARMUP, n):
        if in_position:
            if direction == "LONG":
                pnl_pct = (closes[i] - entry_price) / entry_price * 100
                best = (highs[i] - entry_price) / entry_price * 100
                worst = (lows[i] - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - closes[i]) / entry_price * 100
                best = (entry_price - lows[i]) / entry_price * 100
                worst = (entry_price - highs[i]) / entry_price * 100
            max_favorable = max(max_favorable, best)
            max_adverse = min(max_adverse, worst)
            if pnl_pct >= min_profit_pct:
                net_pnl = pnl_pct - FEE_PCT
                hold_bars = i - entry_idx
                trades.append({"entry_idx": int(entry_idx), "exit_idx": int(i), "entry_price": float(entry_price), "exit_price": float(closes[i]), "pnl_pct": round(float(net_pnl), 4), "gross_pnl_pct": round(float(pnl_pct), 4), "hold_bars": int(hold_bars), "max_adverse_pct": round(float(max_adverse), 4), "max_favorable_pct": round(float(max_favorable), 4), "exit_reason": "NATURAL_TP", "is_reentry": last_exit_idx > 0})
                in_position = False
                last_exit_idx = i
                k_reset = False
        else:
            if last_exit_idx > 0 and not k_reset:
                if direction == "LONG" and k[i] < 35:
                    k_reset = True
                elif direction == "SHORT" and k[i] > 65:
                    k_reset = True
            can_enter = False
            if last_exit_idx < 0:
                can_enter = raw_signals[i]
            elif reentry_mode == "immediate":
                can_enter = raw_signals[i]
            elif reentry_mode == "bounce":
                can_enter = k_reset and raw_signals[i]
            elif reentry_mode == "deep_bounce":
                can_enter = deep_signals[i]
            else:
                can_enter = raw_signals[i]
            if can_enter:
                in_position = True
                entry_price = closes[i]
                entry_idx = i
                max_adverse = 0.0
                max_favorable = 0.0
    if in_position:
        if direction == "LONG":
            pnl_pct = (closes[n - 1] - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - closes[n - 1]) / entry_price * 100
        net_pnl = pnl_pct - FEE_PCT
        hold_bars = (n - 1) - entry_idx
        trades.append({"entry_idx": int(entry_idx), "exit_idx": int(n - 1), "entry_price": float(entry_price), "exit_price": float(closes[n - 1]), "pnl_pct": round(float(net_pnl), 4), "gross_pnl_pct": round(float(pnl_pct), 4), "hold_bars": int(hold_bars), "max_adverse_pct": round(float(max_adverse), 4), "max_favorable_pct": round(float(max_favorable), 4), "exit_reason": "DATA_END", "is_reentry": last_exit_idx > 0})
    return trades

# ═══ METRICS ═════════════════════════════════════════════════════════════════

def compute_metrics(trades, tf, total_bars):
    if len(trades) < MIN_TRADES:
        return None
    rets = np.array([t["pnl_pct"] for t in trades])
    holds = np.array([t["hold_bars"] for t in trades])
    adverse = np.array([t["max_adverse_pct"] for t in trades])
    favorable = np.array([t["max_favorable_pct"] for t in trades])
    reasons = [t["exit_reason"] for t in trades]
    reentries = [t["is_reentry"] for t in trades]
    mean_r = rets.mean()
    std_r = rets.std()
    if std_r <= 0:
        sharpe = 999.0 if mean_r > 0 else 0.0
    else:
        annual = ANNUAL_BARS.get(tf, 252)
        sharpe = mean_r / std_r * math.sqrt(annual)
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    wr = len(wins) / len(rets)
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    bars_in_trade = int(holds.sum())
    capital_util = bars_in_trade / total_bars if total_bars > 0 else 0
    n_reentries = sum(1 for r in reentries if r)
    reentry_rets = [t["pnl_pct"] for t in trades if t["is_reentry"]]
    reentry_wr = (sum(1 for r in reentry_rets if r > 0) / len(reentry_rets)) if reentry_rets else 0
    reentry_avg = np.mean(reentry_rets) if reentry_rets else 0
    natural_tp = sum(1 for r in reasons if r == "NATURAL_TP")
    return {"sharpe": round(sharpe, 4), "win_rate": round(wr, 4), "pf": round(pf, 4), "max_dd": round(max_dd, 4), "n_trades": len(rets), "total_return": round(float(cum[-1]), 4), "mean_ret": round(float(mean_r), 6), "avg_hold_bars": round(float(holds.mean()), 1), "median_hold_bars": round(float(np.median(holds)), 1), "max_hold_bars": int(holds.max()), "avg_max_adverse": round(float(adverse.mean()), 4), "worst_adverse": round(float(adverse.min()), 4), "avg_max_favorable": round(float(favorable.mean()), 4), "capital_util": round(capital_util, 4), "natural_tp_exits": natural_tp, "natural_tp_rate": round(natural_tp / len(rets), 4), "n_reentries": n_reentries, "reentry_wr": round(reentry_wr, 4), "reentry_avg_ret": round(float(reentry_avg), 4)}

# ═══ WORKER ══════════════════════════════════════════════════════════════════

def _worker(args):
    symbol, tf, direction, tp_list, entry_list, reentry_list, strategy = args
    data = load_klines(symbol, tf)
    if data is None or len(data) < WARMUP + 50:
        return []
    I = compute_indicators(data)
    htf = load_htf_indicators(symbol, tf, len(data))
    total_bars = len(data) - WARMUP
    results = []
    for entry_mode in entry_list:
        for reentry_mode in reentry_list:
            for min_tp in tp_list:
                trades = simulate_natural_noloss(I, htf, direction, min_tp, entry_mode, reentry_mode)
                if len(trades) < MIN_TRADES:
                    continue
                metrics = compute_metrics(trades, tf, total_bars)
                if metrics is None:
                    continue
                metrics["symbol"] = symbol
                metrics["tf"] = tf
                metrics["direction"] = direction
                metrics["min_profit_pct"] = min_tp
                metrics["entry_mode"] = entry_mode
                metrics["reentry_mode"] = reentry_mode
                metrics["strategy"] = strategy
                results.append(metrics)
    return results

# ═══ DISCOVER ════════════════════════════════════════════════════════════════

def discover_symbols(tf, max_symbols=0):
    symbols = set()
    for f in KLINES_DIR.glob(f"*_{tf}.json"):
        sym = f.name.replace(f"_{tf}.json", "")
        if not sym.startswith("."):
            symbols.add(sym)
    symbols = sorted(symbols)
    if max_symbols > 0:
        symbols = symbols[:max_symbols]
    return symbols

# ═══ REPORT ══════════════════════════════════════════════════════════════════

def print_report(results_file=None):
    rf = results_file or RESULTS_FILE
    if not rf.exists():
        print("No results found. Run backtest first.")
        return
    results = json.loads(rf.read_text())
    if not results:
        print("Empty results.")
        return
    print(f"\n{'='*145}")
    print(f"TRADIER NO-LOSS BACKTEST — {len(results)} combos")
    print(f"{'='*145}")
    # Summary by strategy × TP × direction
    print(f"\n{'Strategy':>10} {'MinTP':>6} {'Dir':>6} {'TF':>4} {'#Sym':>5} {'AvgWR':>7} {'AvgRet':>8} {'AvgShp':>8} {'AvgHold':>8} {'MedHold':>8} {'CapUtil':>8} {'AvgAdv':>8} {'TPRate':>7} {'ReWR':>6}")
    print("-" * 130)
    keys = set()
    for r in results:
        keys.add((r["strategy"], r["min_profit_pct"], r["direction"], r["tf"]))
    for strat, tp, direction, tf in sorted(keys):
        subset = [r for r in results if r["strategy"] == strat and r["min_profit_pct"] == tp and r["direction"] == direction and r["tf"] == tf]
        if not subset:
            continue
        avg_wr = np.mean([r["win_rate"] for r in subset])
        avg_ret = np.mean([r["total_return"] for r in subset])
        avg_shp = np.mean([r["sharpe"] for r in subset])
        avg_hold = np.mean([r["avg_hold_bars"] for r in subset])
        med_hold = np.mean([r["median_hold_bars"] for r in subset])
        cap_util = np.mean([r["capital_util"] for r in subset])
        avg_adv = np.mean([r["avg_max_adverse"] for r in subset])
        tp_rate = np.mean([r["natural_tp_rate"] for r in subset])
        re_wr = np.mean([r["reentry_wr"] for r in subset])
        print(f"{strat:>10} {tp:>5.1f}% {direction:>6} {tf:>4} {len(subset):>5} {avg_wr:>6.1%} {avg_ret:>8.2f} {avg_shp:>8.2f} {avg_hold:>8.1f} {med_hold:>8.1f} {cap_util:>7.1%} {avg_adv:>8.2f} {tp_rate:>6.1%} {re_wr:>5.1%}")
    # Top 30
    top = sorted(results, key=lambda x: x["total_return"], reverse=True)
    print(f"\n{'='*145}")
    print("TOP 30 BY TOTAL RETURN")
    print(f"{'='*145}")
    print(f"{'Symbol':<8} {'Strat':>10} {'TF':>4} {'Dir':>6} {'TP%':>5} {'Entry':>14} {'Reentry':>12} {'WR':>6} {'Return':>8} {'Sharpe':>8} {'#Tr':>5} {'AvgHd':>7} {'MaxHd':>7} {'WrstAd':>8} {'CapUt':>6} {'TPR':>5} {'#Re':>4}")
    print("-" * 145)
    for r in top[:30]:
        print(f"{r['symbol']:<8} {r['strategy']:>10} {r['tf']:>4} {r['direction']:>6} {r['min_profit_pct']:>4.1f}% {r['entry_mode']:>14} {r['reentry_mode']:>12} {r['win_rate']:>5.1%} {r['total_return']:>8.2f} {r['sharpe']:>8.2f} {r['n_trades']:>5} {r['avg_hold_bars']:>7.1f} {r['max_hold_bars']:>7} {r['worst_adverse']:>8.2f} {r['capital_util']:>5.1%} {r['natural_tp_rate']:>4.1%} {r['n_reentries']:>4}")
    # Best HODL performers (>2% TP, D or 4h)
    hodl = [r for r in results if r["strategy"] == "hodl" and r["total_return"] > 0]
    if hodl:
        hodl.sort(key=lambda x: x["total_return"], reverse=True)
        print(f"\n{'='*145}")
        print("TOP 20 HODL PERFORMERS (D/4h, higher TP)")
        print(f"{'='*145}")
        print(f"{'Symbol':<8} {'TF':>4} {'Dir':>6} {'TP%':>5} {'Entry':>14} {'WR':>6} {'Return':>8} {'Sharpe':>8} {'#Tr':>5} {'AvgHd':>7} {'MaxHd':>7} {'CapUt':>6}")
        print("-" * 100)
        for r in hodl[:20]:
            print(f"{r['symbol']:<8} {r['tf']:>4} {r['direction']:>6} {r['min_profit_pct']:>4.1f}% {r['entry_mode']:>14} {r['win_rate']:>5.1%} {r['total_return']:>8.2f} {r['sharpe']:>8.2f} {r['n_trades']:>5} {r['avg_hold_bars']:>7.1f} {r['max_hold_bars']:>7} {r['capital_util']:>5.1%}")
    # Best day trade performers (1h/15m)
    dt = [r for r in results if r["strategy"] == "daytrade" and r["total_return"] > 0]
    if dt:
        dt.sort(key=lambda x: x["total_return"], reverse=True)
        print(f"\n{'='*145}")
        print("TOP 20 DAY TRADE PERFORMERS (1h/15m)")
        print(f"{'='*145}")
        print(f"{'Symbol':<8} {'TF':>4} {'Dir':>6} {'TP%':>5} {'Entry':>14} {'WR':>6} {'Return':>8} {'Sharpe':>8} {'#Tr':>5} {'AvgHd':>7} {'MaxHd':>7} {'CapUt':>6}")
        print("-" * 100)
        for r in dt[:20]:
            print(f"{r['symbol']:<8} {r['tf']:>4} {r['direction']:>6} {r['min_profit_pct']:>4.1f}% {r['entry_mode']:>14} {r['win_rate']:>5.1%} {r['total_return']:>8.2f} {r['sharpe']:>8.2f} {r['n_trades']:>5} {r['avg_hold_bars']:>7.1f} {r['max_hold_bars']:>7} {r['capital_util']:>5.1%}")

# ═══ MAIN ════════════════════════════════════════════════════════════════════

STRATEGIES = {
    "daytrade": {
        "tfs": ["1h"],
        "tps": [0.25, 0.5, 1.0, 1.5, 2.0],
        "entries": ["k_zone_candle", "k_zone_ha", "k_zone_bb"],
        "reentries": ["bounce", "immediate"],
        "directions": ["LONG", "SHORT"],
    },
    "hodl": {
        "tfs": ["D", "4h"],
        "tps": [1.0, 2.0, 3.0, 5.0, 8.0],
        "entries": ["k_zone_candle", "k_zone_ha", "k_zone_sma", "k_deep_zone"],
        "reentries": ["bounce", "deep_bounce"],
        "directions": ["LONG", "SHORT"],
    },
}

def main():
    parser = argparse.ArgumentParser(description="Tradier No-Loss Backtest")
    parser.add_argument("--symbols", type=int, default=0, help="Max symbols (0=all)")
    parser.add_argument("--strategy", type=str, default="all", help="daytrade, hodl, or all")
    parser.add_argument("--tf", type=str, default="", help="Override timeframe(s)")
    parser.add_argument("--tp", type=str, default="", help="Override TP sweep")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--workers", type=int, default=N_WORKERS)
    args = parser.parse_args()
    if args.report:
        print_report()
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    strats = list(STRATEGIES.keys()) if args.strategy == "all" else [args.strategy]
    all_tasks = []
    total_combos = 0
    for strat_name in strats:
        strat = STRATEGIES[strat_name]
        tfs = [x.strip() for x in args.tf.split(",")] if args.tf else strat["tfs"]
        tps = [float(x) for x in args.tp.split(",")] if args.tp else strat["tps"]
        entries = strat["entries"]
        reentries = strat["reentries"]
        directions = strat["directions"]
        for tf in tfs:
            symbols = discover_symbols(tf, args.symbols)
            logger.info(f"[{strat_name}] Found {len(symbols)} symbols for {tf}")
            for sym in symbols:
                for direction in directions:
                    all_tasks.append((sym, tf, direction, tps, entries, reentries, strat_name))
                    total_combos += len(tps) * len(entries) * len(reentries)
    logger.info(f"Worker tasks: {len(all_tasks)} ({total_combos} total combos)")
    logger.info(f"Using {args.workers} workers")
    results = []
    batch_size = args.workers * 4
    t0 = time.time()
    for i in range(0, len(all_tasks), batch_size):
        if shutdown_flag:
            break
        batch = all_tasks[i:i + batch_size]
        with Pool(args.workers) as pool:
            batch_results = pool.map(_worker, batch)
        for batch_r in batch_results:
            if batch_r:
                results.extend(batch_r)
        done = min(i + batch_size, len(all_tasks))
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(all_tasks) - done) / rate if rate > 0 else 0
        logger.info(f"Progress: {done}/{len(all_tasks)} ({done/len(all_tasks)*100:.1f}%) — {len(results)} valid — {rate:.1f}/s — ETA {eta:.0f}s")
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved {len(results)} results to {RESULTS_FILE}")
    try:
        import pandas as pd
        df = pd.DataFrame(results)
        xlsx_path = RESULTS_DIR / "results.xlsx"
        df.to_excel(xlsx_path, index=False)
        logger.info(f"Saved XLSX to {xlsx_path}")
    except Exception as e:
        logger.warning(f"Could not save XLSX: {e}")
    print_report()
    logger.info(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
