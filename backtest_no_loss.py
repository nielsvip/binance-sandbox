#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
BACKTEST: NO-LOSS + NATURAL EXIT + REENTRY

Philosophy:
  - NEVER sell at a loss. Period. No cutoffs, no hard caps, no forced exits.
  - Wait for bar CLOSE to naturally be >= min_profit_pct above entry (no intrabar exits)
  - After a profitable exit, REENTER on next valid signal (pullback reentry)
  - Track: how long capital sits underwater, max drawdown during hold, reentry success

Reentry modes:
  - "immediate"  — reenter on very next entry signal after exit
  - "pullback"   — after exit, wait for stoch to pull back to zone before reentering
  - "continuation"— reenter only if trend still aligned (HA color + stoch direction)

Usage:
  python3 backtest_no_loss.py                              # All symbols, 15m
  python3 backtest_no_loss.py --symbols 50 --tf 15m,1h     # Multi-TF
  python3 backtest_no_loss.py --tp 0.5,1.0,2.0             # Custom TP sweep
  python3 backtest_no_loss.py --reentry pullback            # Reentry mode
  python3 backtest_no_loss.py --report                      # Print last results
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
KLINES_DIR = BASE_PATH / "klines_cache"
DATA_DIR = BASE_PATH / "data"
RESULTS_DIR = DATA_DIR / "backtest_noloss"
RESULTS_FILE = RESULTS_DIR / "results_v2.json"
ANNUAL_BARS = {"1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365}
FEE_PCT = 0.08  # 0.08% round-trip
MIN_TRADES = 10
WARMUP = 200
N_WORKERS = max(1, cpu_count() - 2)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [NOLOSS-v2] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)
shutdown_flag = False

def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    logger.info("Shutdown requested...")

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ═══ VECTORIZED INDICATORS ═══════════════════════════════════════════════════

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
    return _ema_np(tr, period)

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

def ind_wavetrend(h, lo, c, n1=10, n2=21):
    hlc3 = (h + lo + c) / 3.0
    esa = _ema_np(hlc3, n1)
    d = _ema_np(np.abs(hlc3 - esa), n1)
    ci = np.where(d > 0, (hlc3 - esa) / (0.015 * d), 0.0)
    wt1 = _ema_np(ci, n2)
    wt2 = _sma_np(wt1, 4)
    np.nan_to_num(wt1, copy=False, nan=0.0)
    np.nan_to_num(wt2, copy=False, nan=0.0)
    return wt1, wt2

def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < 60:
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
            elif arr.ndim == 2 and arr.shape[1] >= 4:
                o, h, lo, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
                v = np.ones(len(c))
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
    dp = np.roll(d, 1); dp[0] = d[0]
    I["stoch_co"] = ((k > d) & (kp <= dp)).astype(np.int8)
    I["stoch_cu"] = ((k < d) & (kp >= dp)).astype(np.int8)
    # K turning: k is rising (from prev bar)
    I["k_rising"] = (k > kp).astype(np.int8)
    I["k_falling"] = (k < kp).astype(np.int8)
    I["rsi"] = ind_rsi(c, 14)
    ha = ind_heikin_ashi(o, h, lo, c)
    I["ha"] = ha
    # HA flip detection
    ha_prev = np.roll(ha, 1); ha_prev[0] = ha[0]
    I["ha_flip_green"] = ((ha == 1) & (ha_prev == -1)).astype(np.int8)
    I["ha_flip_red"] = ((ha == -1) & (ha_prev == 1)).astype(np.int8)
    # HA streak
    ha_streak = np.zeros(n)
    for i in range(1, n):
        if ha[i] == ha[i - 1]:
            ha_streak[i] = ha_streak[i - 1] + 1
        else:
            ha_streak[i] = 1
    I["ha_streak"] = ha_streak
    atr = ind_atr(h, lo, c, 14)
    np.nan_to_num(atr, copy=False, nan=0.0)
    I["atr"] = atr
    I["atr_pct"] = np.where(c > 0, atr / c * 100, 0)
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    I["dc_pct"] = np.where((dc_h - dc_l) > 0, (c - dc_l) / (dc_h - dc_l), 0.5)
    bb_u, bb_m, bb_l = ind_bollinger(c, 20, 2.0)
    I["bb_pct"] = np.where((bb_u - bb_l) > 0, (c - bb_l) / (bb_u - bb_l), 0.5)
    wt1, wt2 = ind_wavetrend(h, lo, c, 10, 21)
    I["wt1"] = wt1; I["wt2"] = wt2
    sma200 = _sma_np(c, 200)
    I["sma200"] = sma200
    # Candle patterns
    body = np.abs(c - o)
    range_ = h - lo
    I["body_ratio"] = np.where(range_ > 0, body / range_, 0)
    I["lower_wick"] = np.where(range_ > 0, (np.minimum(o, c) - lo) / range_, 0)
    I["upper_wick"] = np.where(range_ > 0, (h - np.maximum(o, c)) / range_, 0)
    I["green"] = (c > o).astype(np.int8)
    # Hammer / shooting star (strong reversal candles)
    I["hammer"] = ((I["lower_wick"] > 0.6) & (I["body_ratio"] < 0.3) & (I["green"] == 1)).astype(np.int8)
    I["inv_hammer"] = ((I["upper_wick"] > 0.6) & (I["body_ratio"] < 0.3) & (I["green"] == 0)).astype(np.int8)
    # Bullish/bearish engulfing
    prev_body = np.roll(body, 1); prev_body[0] = 0
    prev_green = np.roll(I["green"], 1); prev_green[0] = 0
    I["bull_engulf"] = ((I["green"] == 1) & (prev_green == 0) & (body > prev_body * 1.1)).astype(np.int8)
    I["bear_engulf"] = ((I["green"] == 0) & (prev_green == 1) & (body > prev_body * 1.1)).astype(np.int8)
    # Price vs SMA200
    I["above_sma200"] = np.where(np.isnan(sma200), True, c > sma200)
    I["close"] = c; I["high"] = h; I["low"] = lo; I["open"] = o; I["vol"] = v
    return I

def load_htf_indicators(symbol, primary_tf, primary_n):
    """Load higher timeframe indicators and align to primary bars."""
    tf_hierarchy = {"3m": ["15m", "1h", "4h"], "5m": ["15m", "1h", "4h"], "15m": ["1h", "4h", "D"], "1h": ["4h", "D"], "4h": ["D"]}
    tf_ratios = {"3m": {"15m": 5, "1h": 20, "4h": 80, "D": 480}, "5m": {"15m": 3, "1h": 12, "4h": 48, "D": 288}, "15m": {"1h": 4, "4h": 16, "D": 96}, "1h": {"4h": 4, "D": 24}, "4h": {"D": 6}}
    htf_list = tf_hierarchy.get(primary_tf, [])
    htf_data = {}
    for htf in htf_list:
        data = load_klines(symbol, htf)
        if data is None or len(data) < 50:
            continue
        htf_I = compute_indicators(data)
        ratio = tf_ratios.get(primary_tf, {}).get(htf, 1)
        # Align to primary bars via repeat
        for key in ["k", "d", "ha", "k_rising", "k_falling", "ha_flip_green", "ha_flip_red", "rsi", "dc_pct", "bb_pct"]:
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

# ═══ ENTRY SIGNAL DETECTION ═════════════════════════════════════════════════
# KEY CHANGE: No crossover waiting. Entry on K value zones + candle formations.
# Reentry: price bounces back, K turns in our direction from a zone, candle confirms.

def build_entry_signals(I, htf, direction, entry_mode="k_zone_candle"):
    """
    Build entry signals based on stoch K value zones + candle formations.
    NO crossover required — that's too late. We enter when:
      1. K is in favorable zone (oversold for LONG, overbought for SHORT)
      2. K is turning (rising for LONG, falling for SHORT)
      3. Candle formation confirms (HA flip, hammer, engulfing)
      4. Optional HTF alignment
    """
    k, d = I["k"], I["d"]
    ha = I["ha"]
    rsi = I["rsi"]
    n = len(k)
    if entry_mode == "k_zone_candle":
        # K in zone + turning + bullish/bearish candle
        if direction == "LONG":
            k_zone = (k < 35)
            k_turn = I["k_rising"].astype(bool)
            candle = (I["ha_flip_green"].astype(bool)) | (I["hammer"].astype(bool)) | (I["bull_engulf"].astype(bool))
            sig = k_zone & k_turn & candle
        else:
            k_zone = (k > 65)
            k_turn = I["k_falling"].astype(bool)
            candle = (I["ha_flip_red"].astype(bool)) | (I["inv_hammer"].astype(bool)) | (I["bear_engulf"].astype(bool))
            sig = k_zone & k_turn & candle
    elif entry_mode == "k_zone_ha":
        # K in zone + K turning + HA color match (simpler)
        if direction == "LONG":
            sig = (k < 40) & I["k_rising"].astype(bool) & (ha == 1)
        else:
            sig = (k > 60) & I["k_falling"].astype(bool) & (ha == -1)
    elif entry_mode == "k_deep_zone":
        # Deep oversold/overbought + any turn signal
        if direction == "LONG":
            sig = (k < 20) & I["k_rising"].astype(bool)
        else:
            sig = (k > 80) & I["k_falling"].astype(bool)
    elif entry_mode == "k_zone_multi":
        # K zone + candle + RSI confirmation
        if direction == "LONG":
            sig = (k < 35) & I["k_rising"].astype(bool) & (ha == 1) & (rsi < 45)
        else:
            sig = (k > 65) & I["k_falling"].astype(bool) & (ha == -1) & (rsi > 55)
    elif entry_mode == "k_zone_dc":
        # K zone + candle + Donchian position
        dc = I["dc_pct"]
        if direction == "LONG":
            sig = (k < 35) & I["k_rising"].astype(bool) & (ha == 1) & (dc < 0.3)
        else:
            sig = (k > 65) & I["k_falling"].astype(bool) & (ha == -1) & (dc > 0.7)
    elif entry_mode == "k_zone_bb":
        # K zone + Bollinger band position
        bb = I["bb_pct"]
        if direction == "LONG":
            sig = (k < 35) & I["k_rising"].astype(bool) & (bb < 0.2)
        else:
            sig = (k > 65) & I["k_falling"].astype(bool) & (bb > 0.8)
    else:
        sig = np.zeros(n, dtype=bool)
    # HTF alignment filter: if HTF data available, require HTF K in favorable zone
    if htf:
        for htf_tf in ["1h", "4h", "D"]:
            htf_k = htf.get(f"{htf_tf}_k")
            htf_ha = htf.get(f"{htf_tf}_ha")
            if htf_k is not None and htf_ha is not None:
                if direction == "LONG":
                    # HTF K not overbought + HTF HA not against us
                    htf_ok = (htf_k < 75) & ((htf_ha == 1) | (htf_k < 30))
                else:
                    htf_ok = (htf_k > 25) & ((htf_ha == -1) | (htf_k > 70))
                sig = sig & htf_ok
    sig[:WARMUP] = False
    return sig

# ═══ REENTRY-AWARE SIMULATION ═══════════════════════════════════════════════

def simulate_natural_noloss(I, htf, direction, min_profit_pct, entry_mode, reentry_mode="bounce"):
    """
    Full simulation with natural no-loss exits and smart reentry.

    Exit: ONLY when bar CLOSE >= min_profit_pct. No cutoffs. No forced exits. Ever.
    Reentry: price pulls back, K returns to zone, candle confirms — no crossover needed.

    Reentry modes:
      - "bounce":  after exit, wait for K to pull back into zone + K turning + candle confirm
                   (same logic as initial entry — price bounced, we get back in)
      - "immediate": next entry signal fires, get in (aggressive — assumes trend continues)
      - "deep_bounce": after exit, require K to go deep into zone (<20 LONG / >80 SHORT)
                       before reentering (conservative — only on strong pullbacks)
    """
    closes = I["close"]
    highs = I["high"]
    lows = I["low"]
    k = I["k"]
    ha = I["ha"]
    n = len(closes)
    raw_signals = build_entry_signals(I, htf, direction, entry_mode)
    # Build deep reentry signals
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
            # Track K resetting to zone after exit
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
        annual = ANNUAL_BARS.get(tf, 8760)
        sharpe = mean_r / std_r * math.sqrt(annual)
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    wr = len(wins) / len(rets)
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    # Capital utilization: what % of total bars were we in a trade?
    bars_in_trade = int(holds.sum())
    capital_util = bars_in_trade / total_bars if total_bars > 0 else 0
    # Reentry stats
    n_reentries = sum(1 for r in reentries if r)
    reentry_rets = [t["pnl_pct"] for t in trades if t["is_reentry"]]
    reentry_wr = (sum(1 for r in reentry_rets if r > 0) / len(reentry_rets)) if reentry_rets else 0
    reentry_avg = np.mean(reentry_rets) if reentry_rets else 0
    # Natural TP vs data end
    natural_tp = sum(1 for r in reasons if r == "NATURAL_TP")
    data_end = sum(1 for r in reasons if r == "DATA_END")
    return {"sharpe": round(sharpe, 4), "win_rate": round(wr, 4), "pf": round(pf, 4), "max_dd": round(max_dd, 4), "n_trades": len(rets), "total_return": round(float(cum[-1]), 4), "mean_ret": round(float(mean_r), 6), "avg_hold_bars": round(float(holds.mean()), 1), "median_hold_bars": round(float(np.median(holds)), 1), "max_hold_bars": int(holds.max()), "avg_max_adverse": round(float(adverse.mean()), 4), "worst_adverse": round(float(adverse.min()), 4), "avg_max_favorable": round(float(favorable.mean()), 4), "capital_util": round(capital_util, 4), "natural_tp_exits": natural_tp, "data_end_exits": data_end, "natural_tp_rate": round(natural_tp / len(rets), 4), "n_reentries": n_reentries, "reentry_wr": round(reentry_wr, 4), "reentry_avg_ret": round(float(reentry_avg), 4)}

# ═══ WORKER ══════════════════════════════════════════════════════════════════

def _worker(args):
    """Process all combos for one symbol+tf+direction in a single worker call."""
    symbol, tf, direction, tp_list, entry_list, reentry_list = args
    data = load_klines(symbol, tf)
    if data is None or len(data) < WARMUP + 100:
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
                results.append(metrics)
    return results

# ═══ DISCOVER ════════════════════════════════════════════════════════════════

def discover_symbols(tf, max_symbols=0):
    symbols = set()
    for f in KLINES_DIR.glob(f"*_{tf}.json"):
        sym = f.name.replace(f"_{tf}.json", "")
        if sym.endswith("USDT") and not sym.startswith("."):
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
    print(f"\n{'='*140}")
    print(f"NO-LOSS v2 — NATURAL EXIT + REENTRY — {len(results)} combos")
    print(f"{'='*140}")
    # ── Summary by TP × direction × reentry
    print(f"\n{'MinTP':>6} {'Dir':>6} {'Reentry':>13} {'#Sym':>5} {'AvgWR':>7} {'AvgRet':>8} {'AvgShp':>8} {'AvgHold':>8} {'MedHold':>8} {'CapUtil':>8} {'AvgAdv':>8} {'TPRate':>7} {'ReWR':>6} {'ReAvg':>7}")
    print("-" * 140)
    keys = set()
    for r in results:
        keys.add((r["min_profit_pct"], r["direction"], r["reentry_mode"]))
    for tp, direction, reentry in sorted(keys):
        subset = [r for r in results if r["min_profit_pct"] == tp and r["direction"] == direction and r["reentry_mode"] == reentry]
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
        re_avg = np.mean([r["reentry_avg_ret"] for r in subset])
        print(f"{tp:>5.2f}% {direction:>6} {reentry:>13} {len(subset):>5} {avg_wr:>6.1%} {avg_ret:>8.2f} {avg_shp:>8.2f} {avg_hold:>8.1f} {med_hold:>8.1f} {cap_util:>7.1%} {avg_adv:>8.2f} {tp_rate:>6.1%} {re_wr:>5.1%} {re_avg:>7.3f}")
    # ── Top 30 by total return
    top = sorted(results, key=lambda x: x["total_return"], reverse=True)
    print(f"\n{'='*140}")
    print("TOP 30 BY TOTAL RETURN")
    print(f"{'='*140}")
    print(f"{'Symbol':<16} {'TF':>4} {'Dir':>6} {'TP%':>5} {'Entry':>14} {'Reentry':>13} {'WR':>6} {'Return':>8} {'Sharpe':>8} {'#Tr':>5} {'AvgHd':>7} {'MaxHd':>7} {'WrstAd':>8} {'CapUt':>6} {'TPR':>5} {'#Re':>4}")
    print("-" * 140)
    for r in top[:30]:
        print(f"{r['symbol']:<16} {r['tf']:>4} {r['direction']:>6} {r['min_profit_pct']:>4.1f}% {r['entry_mode']:>14} {r['reentry_mode']:>13} {r['win_rate']:>5.1%} {r['total_return']:>8.2f} {r['sharpe']:>8.2f} {r['n_trades']:>5} {r['avg_hold_bars']:>7.1f} {r['max_hold_bars']:>7} {r['worst_adverse']:>8.2f} {r['capital_util']:>5.1%} {r['natural_tp_rate']:>4.1%} {r['n_reentries']:>4}")
    # ── Best capital efficiency (return / capital_util)
    efficient = [r for r in results if r["capital_util"] > 0.01]
    if efficient:
        for r in efficient:
            r["_efficiency"] = r["total_return"] / r["capital_util"]
        efficient.sort(key=lambda x: x["_efficiency"], reverse=True)
        print(f"\n{'='*140}")
        print("TOP 20 BY CAPITAL EFFICIENCY (return per unit of capital utilization)")
        print(f"{'='*140}")
        print(f"{'Symbol':<16} {'TF':>4} {'Dir':>6} {'TP%':>5} {'Reentry':>13} {'Return':>8} {'CapUtil':>8} {'Effic':>9} {'WR':>6} {'#Tr':>5} {'AvgHd':>7}")
        print("-" * 110)
        for r in efficient[:20]:
            print(f"{r['symbol']:<16} {r['tf']:>4} {r['direction']:>6} {r['min_profit_pct']:>4.1f}% {r['reentry_mode']:>13} {r['total_return']:>8.2f} {r['capital_util']:>7.1%} {r['_efficiency']:>9.1f} {r['win_rate']:>5.1%} {r['n_trades']:>5} {r['avg_hold_bars']:>7.1f}")
    # ── Reentry effectiveness
    re_results = [r for r in results if r["n_reentries"] > 0]
    if re_results:
        print(f"\n{'='*140}")
        print("REENTRY EFFECTIVENESS — Top 20 by reentry win rate (min 3 reentries)")
        print(f"{'='*140}")
        re_results = [r for r in re_results if r["n_reentries"] >= 3]
        re_results.sort(key=lambda x: x["reentry_wr"], reverse=True)
        print(f"{'Symbol':<16} {'Dir':>6} {'TP%':>5} {'Reentry':>13} {'#Re':>5} {'ReWR':>6} {'ReAvg':>8} {'TotalWR':>8} {'TotRet':>8}")
        print("-" * 100)
        for r in re_results[:20]:
            print(f"{r['symbol']:<16} {r['direction']:>6} {r['min_profit_pct']:>4.1f}% {r['reentry_mode']:>13} {r['n_reentries']:>5} {r['reentry_wr']:>5.1%} {r['reentry_avg_ret']:>8.3f} {r['win_rate']:>7.1%} {r['total_return']:>8.2f}")

# ═══ MAIN ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="No-Loss v2 — Natural Exit + Reentry Backtest")
    parser.add_argument("--symbols", type=int, default=0, help="Max symbols (0=all)")
    parser.add_argument("--tf", type=str, default="15m", help="Timeframe(s), comma-separated")
    parser.add_argument("--tp", type=str, default="0.25,0.5,1.0,1.5,2.0,3.0,5.0", help="Min TP percentages, comma-separated")
    parser.add_argument("--entry", type=str, default="k_zone_candle,k_zone_ha,k_zone_dc,k_deep_zone,k_zone_multi", help="Entry modes (K zone based, no crossover)")
    parser.add_argument("--reentry", type=str, default="bounce,immediate,deep_bounce", help="Reentry modes")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--workers", type=int, default=N_WORKERS)
    args = parser.parse_args()
    if args.report:
        print_report()
        return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tps = [float(x) for x in args.tp.split(",")]
    entry_modes = [x.strip() for x in args.entry.split(",")]
    reentry_modes = [x.strip() for x in args.reentry.split(",")]
    tfs = [x.strip() for x in args.tf.split(",")]
    # Build tasks batched by symbol+tf+direction (compute indicators once, run all combos)
    all_tasks = []
    total_combos = 0
    for tf in tfs:
        symbols = discover_symbols(tf, args.symbols)
        logger.info(f"Found {len(symbols)} symbols for {tf}")
        for sym in symbols:
            for direction in ["LONG", "SHORT"]:
                all_tasks.append((sym, tf, direction, tps, entry_modes, reentry_modes))
                total_combos += len(tps) * len(entry_modes) * len(reentry_modes)
    logger.info(f"Worker tasks: {len(all_tasks)} (each runs {len(tps)*len(entry_modes)*len(reentry_modes)} combos = {total_combos} total)")
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
        combos_done = done * len(tps) * len(entry_modes) * len(reentry_modes)
        logger.info(f"Progress: {done}/{len(all_tasks)} symbols ({combos_done}/{total_combos} combos) — {len(results)} valid — {rate:.1f} sym/s — ETA {eta:.0f}s")
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved {len(results)} results to {RESULTS_FILE}")
    try:
        import pandas as pd
        df = pd.DataFrame(results)
        xlsx_path = RESULTS_DIR / "results_v2.xlsx"
        df.to_excel(xlsx_path, index=False)
        logger.info(f"Saved XLSX to {xlsx_path}")
    except Exception as e:
        logger.warning(f"Could not save XLSX: {e}")
    print_report()
    logger.info(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
