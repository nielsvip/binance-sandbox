#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
BACKTEST FACTORY — Comprehensive backtesting engine using ALL indicators × ALL TF combos × ALL symbols.
Replaces fragmented backtest scripts with a single, solid, continuously-running factory.

Features:
  - ALL indicators from ez_indicators: Stoch, RSI, ATR, DC, HA, MFI, WT, BB, LR, EMA, SMA, Hull, RelVol
  - Multi-TF alignment: tests entry on LTF with HTF confirmation (all combos)
  - Walk-forward validation: 70/30 in-sample/out-of-sample split
  - Combo testing: 2/3/4-signal combos + single-signal sweeps
  - Continuous operation: runs until killed, saves incrementally
  - Portfolio-level metrics: Sharpe, Sortino, Calmar, MaxDD, PF, WR
  - Deduplication: never re-tests a combo already tested
  - All CPUs used for maximum throughput

Usage:
  python3 backtest_factory.py                         # Run continuous (all symbols, all TFs)
  python3 backtest_factory.py --symbols 50 --rounds 5 # Limited run
  python3 backtest_factory.py --report                # Print current top results
  python3 backtest_factory.py --sweep-only             # Single-metric sweep (no combos)
"""
import os, sys, json, math, time, random, signal, hashlib, logging, gc, warnings
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count, Manager
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s [FACTORY] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# ═══ PATHS ═══════════════════════════════════════════════════════════════════
BASE_PATH = Path("/Users/niels/Documents/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
DATA_DIR = BASE_PATH / "data"
RESULTS_DIR = DATA_DIR / "backtest_factory"
RESULTS_JSONL = RESULTS_DIR / "factory_results.jsonl"
TOP_FILE = RESULTS_DIR / "factory_top.json"
SWEEP_FILE = RESULTS_DIR / "factory_sweep.json"
TESTED_FILE = RESULTS_DIR / "factory_tested_hashes.json"
PROGRESS_FILE = RESULTS_DIR / "factory_progress.json"
ANNUAL_BARS = {"1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365}
FEE_PCT = 0.08  # 0.08% round-trip
TFS = ["3m", "15m", "1h", "4h", "D"]
# Multi-TF combos: entry TF → required HTF confirmations
TF_COMBOS = {
    "3m": [[], ["15m"], ["1h"], ["15m", "1h"]],
    "15m": [[], ["1h"], ["4h"], ["1h", "4h"], ["1h", "4h", "D"]],
    "1h": [[], ["4h"], ["D"], ["4h", "D"]],
    "4h": [[], ["D"]],
    "D": [[]],
}
# HTF bar ratio for alignment
HTF_RATIO = {"3m": {"15m": 5, "1h": 20, "4h": 80, "D": 480}, "15m": {"1h": 4, "4h": 16, "D": 96}, "1h": {"4h": 4, "D": 24}, "4h": {"D": 6}}
N_WORKERS = max(1, cpu_count() - 2)
SAVE_INTERVAL = 120  # seconds
TOP_N = 2000
MIN_TRADES = 15
WALK_FORWARD_SPLIT = 0.7  # 70% train, 30% test
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    logger.info("Shutdown requested, finishing current batch...")

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══ VECTORIZED INDICATOR ENGINE ═════════════════════════════════════════════
# Replicates ALL indicators from ez_indicators.py in pure numpy for speed

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
    """Stochastic K/D — matches ez_indicators (K=14, SK=5, SD=5 tournament winner)."""
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = np.max(h[i - k_period + 1:i + 1])
        ll = np.min(lo[i - k_period + 1:i + 1])
        raw_k[i] = (c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
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


def ind_atr(h, lo, c, period=14):
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))))
    tr[0] = h[0] - lo[0]
    return _ema_np(tr, period)


def ind_atr_long(h, lo, c, period=100):
    return ind_atr(h, lo, c, period)


def ind_donchian(h, lo, period=20):
    n = len(h)
    dc_h = np.full(n, np.nan)
    dc_l = np.full(n, np.nan)
    for i in range(period - 1, n):
        dc_h[i] = np.max(h[i - period + 1:i + 1])
        dc_l[i] = np.min(lo[i - period + 1:i + 1])
    dc_mid = (dc_h + dc_l) / 2
    return dc_h, dc_l, dc_mid


def ind_bollinger(c, period=20, std_mult=2.0):
    mid = _sma_np(c, period)
    std = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)):
        std[i] = np.std(c[i - period + 1:i + 1])
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4
    ha_o = np.empty_like(o)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)


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


def ind_wavetrend(h, lo, c, n1=10, n2=21):
    hlc3 = (h + lo + c) / 3.0
    esa = _ema_np(hlc3, n1)
    d = _ema_np(np.abs(hlc3 - esa), n1)
    ci = np.where(d > 0, (hlc3 - esa) / (0.015 * d), 0.0)
    wt1 = _ema_np(ci, n2)
    wt2 = _sma_np(wt1, 4)
    return wt1, wt2


def ind_linreg(c, period=50):
    n = len(c)
    result = np.full(n, np.nan)
    x = np.arange(period, dtype=float)
    sx2 = float((x ** 2).sum())
    xm = x - x.mean()
    for i in range(period - 1, n):
        window = c[i - period + 1:i + 1]
        slope = np.sum(window * xm) / (np.sum(xm ** 2) + 1e-9)
        result[i] = slope / (c[i] + 1e-9) * 100  # normalized slope
    return result


def ind_hull_trend(c, short_p=9, long_p=21):
    """Hull-like trend: EMA(short) > EMA(long) → t_up=1."""
    ema_s = _ema_np(c, short_p)
    ema_l = _ema_np(c, long_p)
    t_up = np.where(ema_s > ema_l, 1, 0)
    tco = np.where((np.roll(t_up, 1) == 0) & (t_up == 1), 1, 0)
    tcu = np.where((np.roll(t_up, 1) == 1) & (t_up == 0), 1, 0)
    return t_up, tco, tcu


def ind_relative_volume(v, period=20):
    avg = _sma_np(v, period)
    return np.where(avg > 0, v / avg, 1.0)


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


# ═══ COMPREHENSIVE INDICATOR COMPUTATION ═════════════════════════════════════

def compute_all_indicators(data):
    """Compute ALL indicators matching ez_indicators.py output. Returns dict of arrays."""
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    I = {}
    # ── Stochastic (multiple parameter sets)
    for kp, sk, sd, suffix in [(14, 5, 5, ""), (9, 5, 5, "_9"), (21, 5, 5, "_21"), (14, 3, 3, "_fast")]:
        k, d = ind_stoch(h, lo, c, kp, sk, sd)
        I[f"k{suffix}"] = k
        I[f"d{suffix}"] = d
        kp_arr = np.roll(k, 1); kp_arr[0] = k[0]
        dp_arr = np.roll(d, 1); dp_arr[0] = d[0]
        I[f"stoch_co{suffix}"] = ((k > d) & (kp_arr <= dp_arr)).astype(np.int8)
        I[f"stoch_cu{suffix}"] = ((k < d) & (kp_arr >= dp_arr)).astype(np.int8)
        I[f"kp{suffix}"] = kp_arr
    # ── RSI
    rsi14 = ind_rsi(c, 14)
    rsi9 = ind_rsi(c, 9)
    I["rsi"] = rsi14
    I["rsi9"] = rsi9
    rsip = np.roll(rsi14, 1); rsip[0] = rsi14[0]
    I["rsip"] = rsip
    # ── ATR
    atr14 = ind_atr(h, lo, c, 14)
    atr_long = ind_atr_long(h, lo, c, 100)
    I["atr"] = atr14
    I["atr_long"] = atr_long
    atr_mean = _sma_np(atr14, 50)
    I["atr_ratio"] = np.where(atr_mean > 0, atr14 / atr_mean, 1.0)
    I["atr_pct"] = np.where(c > 0, atr14 / c * 100, 0)
    # ── Donchian Channel
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    I["dc_h"] = dc_h
    I["dc_l"] = dc_l
    I["dc_mid"] = dc_mid
    I["dc_pct"] = np.where((dc_h - dc_l) > 0, (c - dc_l) / (dc_h - dc_l), 0.5)
    dc_mid_prev = np.roll(dc_mid, 1)
    c_prev = np.roll(c, 1)
    I["dc_co"] = ((c > dc_mid) & (c_prev <= dc_mid_prev)).astype(np.int8)
    I["dc_cu"] = ((c < dc_mid) & (c_prev >= dc_mid_prev)).astype(np.int8)
    # Donchian 4-bar (dc4)
    dc_h4, dc_l4, _ = ind_donchian(h, lo, 4)
    I["dc_h4"] = dc_h4
    I["dc_l4"] = dc_l4
    # ── Bollinger Bands
    bb_u, bb_m, bb_l = ind_bollinger(c, 20, 2.0)
    I["bb_pct"] = np.where((bb_u - bb_l) > 0, (c - bb_l) / (bb_u - bb_l), 0.5)
    I["bb_squeeze"] = np.where(bb_m > 0, (bb_u - bb_l) / bb_m, 0)
    # ── Heikin-Ashi
    ha = ind_heikin_ashi(o, h, lo, c)
    I["ha"] = ha
    ha_prev = np.roll(ha, 1); ha_prev[0] = ha[0]
    I["hap"] = ha_prev
    ha_streak = np.zeros(n)
    for i in range(1, n):
        if ha[i] == ha[i - 1]:
            ha_streak[i] = ha_streak[i - 1] + ha[i]
        else:
            ha_streak[i] = ha[i]
    I["ha_streak"] = ha_streak
    # ── MFI
    I["mfi"] = ind_mfi(h, lo, c, v, 14)
    # ── WaveTrend
    wt1, wt2 = ind_wavetrend(h, lo, c, 10, 21)
    I["wt1"] = wt1
    I["wt2"] = wt2
    wt1p = np.roll(wt1, 1); wt1p[0] = wt1[0]
    wt2p = np.roll(wt2, 1); wt2p[0] = wt2[0]
    I["wt_co"] = ((wt1 > wt2) & (wt1p <= wt2p)).astype(np.int8)
    I["wt_cu"] = ((wt1 < wt2) & (wt1p >= wt2p)).astype(np.int8)
    # ── EMA / SMA
    ema20 = _ema_np(c, 20)
    sma200 = _sma_np(c, 200)
    sma500 = _sma_np(c, 500)
    I["ema20"] = ema20
    I["sma200"] = sma200
    I["p_vs_ema"] = np.where(ema20 > 0, (c / ema20 - 1.0) * 100, 0)
    I["p_vs_s200"] = np.where(sma200 > 0, (c / sma200 - 1.0) * 100, 0)
    ema3 = np.roll(ema20, 3); ema3[:3] = ema20[:3]
    I["ema_slope"] = np.where(ema3 > 0, (ema20 / ema3 - 1.0) * 100, 0)
    # ── Linear Regression slope
    I["lr_trend"] = ind_linreg(c, 50)
    # ── Hull Trend
    t_up, tco, tcu = ind_hull_trend(c, 9, 21)
    I["t_up"] = t_up
    I["tco"] = tco
    I["tcu"] = tcu
    # ── Relative Volume
    I["rel_vol"] = ind_relative_volume(v, 20)
    # ── Price action
    I["candle_body"] = np.where((h - lo) > 0, np.abs(c - o) / (h - lo), 0)
    I["upper_wick"] = np.where((h - lo) > 0, (h - np.maximum(o, c)) / (h - lo), 0)
    I["lower_wick"] = np.where((h - lo) > 0, (np.minimum(o, c) - lo) / (h - lo), 0)
    I["green"] = np.where(c > o, 1, 0).astype(np.int8)
    I["mom3"] = np.where(np.roll(c, 3) > 0, (c - np.roll(c, 3)) / np.roll(c, 3) * 100, 0)
    I["mom5"] = np.where(np.roll(c, 5) > 0, (c - np.roll(c, 5)) / np.roll(c, 5) * 100, 0)
    # ── Engulfing
    body_curr = c - o
    body_prev = np.roll(c, 1) - np.roll(o, 1)
    I["bull_engulf"] = ((body_curr > 0) & (body_prev < 0) & (np.abs(body_curr) > np.abs(body_prev) * 1.1)).astype(np.int8)
    I["bear_engulf"] = ((body_curr < 0) & (body_prev > 0) & (np.abs(body_curr) > np.abs(body_prev) * 1.1)).astype(np.int8)
    # ── Pin bars
    I["bull_pin"] = ((I["lower_wick"] > 0.6) & (I["candle_body"] < 0.25)).astype(np.int8)
    I["bear_pin"] = ((I["upper_wick"] > 0.6) & (I["candle_body"] < 0.25)).astype(np.int8)
    # ── Close
    I["close"] = c
    I["open"] = o
    return I


# ═══ SIGNAL GENERATION ═══════════════════════════════════════════════════════

def build_signals(I):
    """Build all named atomic boolean signals for LONG and SHORT. Returns L, S dicts."""
    k = I["k"]; d = I["d"]; kp = I["kp"]
    rsi = I["rsi"]; rsip = I["rsip"]; mfi = I["mfi"]
    wt1 = I["wt1"]; wt2 = I["wt2"]
    bb = I["bb_pct"]; dc = I["dc_pct"]
    atr_r = I["atr_ratio"]; rv = I["rel_vol"]
    ha = I["ha"]; hap = I["hap"]
    ema_d = I["p_vs_ema"]; s200 = I["p_vs_s200"]
    ems = I["ema_slope"]; lr = I["lr_trend"]
    L, S = {}, {}
    # ── Stochastic thresholds
    for t in [15, 20, 25, 30, 35, 40, 50]:
        L[f"k<{t}"] = k < t
    for t in [50, 60, 65, 70, 75, 80, 85]:
        S[f"k>{t}"] = k > t
    L["k>d"] = k > d; S["k<d"] = k < d
    L["stoch_co"] = I["stoch_co"].astype(bool)
    S["stoch_cu"] = I["stoch_cu"].astype(bool)
    L["k_rising"] = (k > kp) & (k < 50)
    S["k_falling"] = (k < kp) & (k > 50)
    # Cross + zone combos
    L["k<40+k>d"] = (k < 40) & (k > d)
    S["k>60+k<d"] = (k > 60) & (k < d)
    L["k<30+co"] = (k < 30) & I["stoch_co"].astype(bool)
    S["k>70+cu"] = (k > 70) & I["stoch_cu"].astype(bool)
    # Fast stoch
    k9 = I["k_9"]; d9 = I["d_9"]
    L["k9<30"] = k9 < 30; S["k9>70"] = k9 > 70
    L["k9>d9"] = k9 > d9; S["k9<d9"] = k9 < d9
    L["stoch9_co"] = I["stoch_co_9"].astype(bool)
    S["stoch9_cu"] = I["stoch_cu_9"].astype(bool)
    # ── RSI
    for t in [25, 30, 35, 40, 45, 50]:
        L[f"rsi<{t}"] = rsi < t
    for t in [50, 55, 60, 65, 70, 75]:
        S[f"rsi>{t}"] = rsi > t
    L["rsi_rising"] = (rsi > rsip) & (rsi < 50)
    S["rsi_falling"] = (rsi < rsip) & (rsi > 50)
    L["rsi<40+k>d"] = (rsi < 40) & (k > d)
    S["rsi>60+k<d"] = (rsi > 60) & (k < d)
    L["rsi<30+co"] = (rsi < 30) & I["stoch_co"].astype(bool)
    S["rsi>70+cu"] = (rsi > 70) & I["stoch_cu"].astype(bool)
    # ── MFI
    for t in [20, 30, 40, 50]:
        L[f"mfi<{t}"] = mfi < t
    for t in [50, 60, 70, 80]:
        S[f"mfi>{t}"] = mfi > t
    L["mfi<40+k>d"] = (mfi < 40) & (k > d)
    S["mfi>60+k<d"] = (mfi > 60) & (k < d)
    # ── WaveTrend
    for t in [-80, -60, -40, -20, 0]:
        L[f"wt1<{t}"] = wt1 < t
    for t in [0, 20, 40, 60, 80]:
        S[f"wt1>{t}"] = wt1 > t
    L["wt_co"] = I["wt_co"].astype(bool)
    S["wt_cu"] = I["wt_cu"].astype(bool)
    L["wt_co+<0"] = (wt1 < 0) & I["wt_co"].astype(bool)
    S["wt_cu+>0"] = (wt1 > 0) & I["wt_cu"].astype(bool)
    L["wt1<wt2+k>d"] = (wt1 < wt2) & (k > d)
    S["wt1>wt2+k<d"] = (wt1 > wt2) & (k < d)
    # ── Bollinger
    for t in [-0.2, -0.1, 0.0, 0.1, 0.2, 0.3]:
        L[f"bb<{t:.1f}"] = bb < t
    for t in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2]:
        S[f"bb>{t:.1f}"] = bb > t
    L["bb<0+k>d"] = (bb < 0) & (k > d)
    S["bb>1+k<d"] = (bb > 1.0) & (k < d)
    # ── Donchian
    for t in [0.10, 0.15, 0.20, 0.25, 0.30]:
        L[f"dc<{t}"] = dc < t
    for t in [0.70, 0.75, 0.80, 0.85, 0.90]:
        S[f"dc>{t}"] = dc > t
    L["dc_co"] = I["dc_co"].astype(bool)
    S["dc_cu"] = I["dc_cu"].astype(bool)
    L["dc<0.2+co"] = (dc < 0.2) & I["dc_co"].astype(bool)
    S["dc>0.8+cu"] = (dc > 0.8) & I["dc_cu"].astype(bool)
    # ── EMA/SMA structure
    L["above_ema20"] = ema_d > 0; S["below_ema20"] = ema_d < 0
    L["above_s200"] = s200 > 0; S["below_s200"] = s200 < 0
    L["ema_rising"] = ems > 0; S["ema_falling"] = ems < 0
    L["ema_rising+k>d"] = (ems > 0) & (k > d)
    S["ema_falling+k<d"] = (ems < 0) & (k < d)
    for pct in [-2, -1, -0.5]:
        L[f"ema_near{pct}"] = (ema_d >= pct) & (ema_d < 1.0)
    for pct in [0.5, 1, 2]:
        S[f"ema_near+{pct}"] = (ema_d <= pct) & (ema_d > -1.0)
    # ── Heikin-Ashi
    L["ha_green"] = (ha == 1)
    S["ha_red"] = (ha == -1)
    L["ha_2green"] = (ha == 1) & (hap == 1)
    S["ha_2red"] = (ha == -1) & (hap == -1)
    L["ha_flip_g"] = (ha == 1) & (hap == -1)
    S["ha_flip_r"] = (ha == -1) & (hap == 1)
    L["ha_green+k>d"] = (ha == 1) & (k > d)
    S["ha_red+k<d"] = (ha == -1) & (k < d)
    # ── ATR volatility
    L["atr_hi"] = atr_r > 1.5; S["atr_hi_s"] = atr_r > 1.5
    L["atr_lo"] = atr_r < 0.7; S["atr_lo_s"] = atr_r < 0.7
    L["atr_hi+k>d"] = (atr_r > 1.5) & (k > d)
    S["atr_hi+k<d"] = (atr_r > 1.5) & (k < d)
    # ── Relative volume
    for t in [1.2, 1.5, 2.0, 2.5]:
        L[f"rv>{t}"] = rv > t
        S[f"rv>{t}_s"] = rv > t
    L["rv>1.5+k>d"] = (rv > 1.5) & (k > d)
    S["rv>1.5+k<d"] = (rv > 1.5) & (k < d)
    # ── Hull Trend
    L["t_up"] = I["t_up"].astype(bool)
    S["t_dn"] = ~I["t_up"].astype(bool)
    L["tco"] = I["tco"].astype(bool)
    S["tcu"] = I["tcu"].astype(bool)
    L["t_up+k>d"] = I["t_up"].astype(bool) & (k > d)
    S["t_dn+k<d"] = (~I["t_up"].astype(bool)) & (k < d)
    # ── Linear regression
    L["lr_up"] = lr > 0; S["lr_dn"] = lr < 0
    L["lr_strong_up"] = lr > 0.1; S["lr_strong_dn"] = lr < -0.1
    # ── Price action patterns
    L["bull_engulf"] = I["bull_engulf"].astype(bool)
    S["bear_engulf"] = I["bear_engulf"].astype(bool)
    L["bull_pin"] = I["bull_pin"].astype(bool)
    S["bear_pin"] = I["bear_pin"].astype(bool)
    # ── Momentum
    L["mom3_up"] = I["mom3"] > 0; S["mom3_dn"] = I["mom3"] < 0
    L["mom5_up"] = I["mom5"] > 0; S["mom5_dn"] = I["mom5"] < 0
    return L, S


# ═══ METRICS COMPUTATION ═════════════════════════════════════════════════════

def compute_metrics(returns, tf):
    if len(returns) < MIN_TRADES:
        return None
    rets = np.array(returns)
    mean_r = rets.mean()
    std_r = rets.std()
    if std_r <= 0:
        return None
    annual = ANNUAL_BARS.get(tf, 8760)
    sharpe = mean_r / std_r * math.sqrt(annual)
    # Sortino
    downside = rets[rets < 0]
    down_std = downside.std() if len(downside) > 2 else std_r
    sortino = mean_r / (down_std + 1e-9) * math.sqrt(annual)
    # Win rate and profit factor
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    wr = len(wins) / len(rets)
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    # Max drawdown
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    # Calmar
    calmar = (cum[-1] / annual) / (max_dd + 1e-9) if max_dd > 0 else 0.0
    # Composite score (weighted blend for ranking)
    score = sharpe * 0.3 + sortino * 0.2 + pf * 0.15 + wr * 20 + calmar * 0.1 - max_dd * 0.5
    return {"sharpe": round(sharpe, 4), "sortino": round(sortino, 4), "calmar": round(calmar, 4), "win_rate": round(wr, 4), "pf": round(pf, 4), "max_dd": round(max_dd, 4), "n_trades": len(rets), "total_return": round(float(cum[-1]), 4), "mean_ret": round(float(mean_r), 6), "score": round(score, 4)}


# ═══ TRADE SIMULATION ════════════════════════════════════════════════════════

def simulate_trades(c, entries, direction, hold_bars, tp_pct=None, sl_pct=None):
    """Simulate trades from boolean entry array. Returns list of % returns."""
    n = len(c)
    warmup = 60
    entries[:warmup] = False
    entries[-(hold_bars + 1):] = False
    # Dedupe: keep only first in consecutive runs
    for i in range(1, n):
        if entries[i] and entries[i - 1]:
            entries[i] = False
    idxs = np.where(entries)[0]
    if len(idxs) < MIN_TRADES:
        return []
    returns = []
    last_exit = -1
    for idx in idxs:
        if idx <= last_exit:
            continue
        ep = c[idx]
        if ep <= 0:
            continue
        # Walk forward bar by bar for TP/SL
        exit_idx = min(idx + hold_bars, n - 1)
        for j in range(idx + 1, min(idx + hold_bars + 1, n)):
            if direction == "LONG":
                pnl = (c[j] - ep) / ep * 100
            else:
                pnl = (ep - c[j]) / ep * 100
            if tp_pct and pnl >= tp_pct:
                exit_idx = j
                break
            if sl_pct and pnl <= -sl_pct:
                exit_idx = j
                break
        else:
            exit_idx = min(idx + hold_bars, n - 1)
        if direction == "LONG":
            ret = (c[exit_idx] - ep) / ep * 100 - FEE_PCT
        else:
            ret = (ep - c[exit_idx]) / ep * 100 - FEE_PCT
        returns.append(ret)
        last_exit = exit_idx
    return returns


# ═══ MULTI-TF ALIGNMENT ══════════════════════════════════════════════════════

def build_htf_alignment(symbol, entry_tf, htf_list, n_primary):
    """Load HTF data and build alignment arrays mapped to primary TF bars."""
    alignments = {}
    for htf in htf_list:
        data = load_klines(symbol, htf)
        if data is None or len(data) < 50:
            return None  # missing required HTF
        ratio = HTF_RATIO.get(entry_tf, {}).get(htf, 1)
        htf_I = compute_all_indicators(data)
        # Build key alignment signals at HTF level
        htf_k = htf_I["k"]
        htf_d = htf_I["d"]
        htf_ha = htf_I["ha"]
        htf_rsi = htf_I["rsi"]
        htf_wt1 = htf_I["wt1"]
        # Map to primary bars via repeat
        for name, arr in [("k", htf_k), ("d", htf_d), ("ha", htf_ha), ("rsi", htf_rsi), ("wt1", htf_wt1)]:
            aligned = np.repeat(arr, ratio)
            if len(aligned) >= n_primary:
                aligned = aligned[-n_primary:]
            else:
                pad = np.full(n_primary - len(aligned), 50.0 if name in ("k", "d", "rsi") else 0.0)
                aligned = np.concatenate([pad, aligned])
            alignments[f"{htf}_{name}"] = aligned
    return alignments


def build_htf_filters(alignments, htf_list):
    """Build HTF confirmation filters from alignment arrays."""
    L_htf, S_htf = {}, {}
    for htf in htf_list:
        k = alignments.get(f"{htf}_k")
        d = alignments.get(f"{htf}_d")
        ha = alignments.get(f"{htf}_ha")
        if k is None:
            continue
        # Bull alignment: K>D on HTF
        L_htf[f"{htf}_bull"] = k > d
        S_htf[f"{htf}_bear"] = k < d
        # Strong bull: K>D + HA green
        if ha is not None:
            L_htf[f"{htf}_strong_bull"] = (k > d) & (ha == 1)
            S_htf[f"{htf}_strong_bear"] = (k < d) & (ha == -1)
        # Oversold/overbought HTF
        L_htf[f"{htf}_k<40"] = k < 40
        S_htf[f"{htf}_k>60"] = k > 60
        L_htf[f"{htf}_k<30"] = k < 30
        S_htf[f"{htf}_k>70"] = k > 70
    return L_htf, S_htf


# ═══ WORKER FUNCTION ═════════════════════════════════════════════════════════

def worker_task(args):
    """Process one (symbol, entry_tf, htf_list, mode) task. Returns list of results."""
    symbol, entry_tf, htf_list, mode, seed = args
    random.seed(seed)
    np.random.seed(seed % (2**32))
    data = load_klines(symbol, entry_tf)
    if data is None:
        return []
    n = len(data)
    c = data[:, 3]
    I = compute_all_indicators(data)
    L, S = build_signals(I)
    # Build HTF filters if needed
    if htf_list:
        alignments = build_htf_alignment(symbol, entry_tf, htf_list, n)
        if alignments is None:
            return []
        L_htf, S_htf = build_htf_filters(alignments, htf_list)
    else:
        L_htf, S_htf = {}, {}
    results = []
    hold_bars_options = [3, 5, 8, 13, 21, 34]
    tp_options = [None, 0.5, 1.0, 1.5, 2.0, 3.0]
    htf_tag = "+".join(htf_list) if htf_list else "none"
    if mode == "sweep":
        # Single-metric sweep: test every signal × hold × direction
        for direction, pool, pool_d in [("LONG", L, L_htf), ("SHORT", S, S_htf)]:
            for sig_name, sig_arr in pool.items():
                for hold in hold_bars_options:
                    entries = sig_arr.copy()
                    # Apply HTF filter if available
                    if pool_d:
                        htf_filter_name = list(pool_d.keys())[0]
                        entries = entries & pool_d[htf_filter_name]
                    rets = simulate_trades(c, entries, direction, hold)
                    m = compute_metrics(rets, entry_tf)
                    if m and m["sharpe"] >= 1.0:
                        m.update({"symbol": symbol, "tf": entry_tf, "htf": htf_tag, "direction": direction, "signals": [sig_name], "hold_bars": hold, "tp": None, "mode": "sweep"})
                        results.append(m)
    elif mode == "combo":
        # Random combo testing (2-4 signals)
        L_names = list(L.keys()); S_names = list(S.keys())
        L_htf_names = list(L_htf.keys()); S_htf_names = list(S_htf.keys())
        n_combos = 3000
        for _ in range(n_combos):
            direction = random.choice(("LONG", "SHORT"))
            pool_names = L_names if direction == "LONG" else S_names
            pool = L if direction == "LONG" else S
            htf_pool_names = L_htf_names if direction == "LONG" else S_htf_names
            htf_pool = L_htf if direction == "LONG" else S_htf
            sz = random.choices([2, 3, 4], weights=[0.3, 0.5, 0.2])[0]
            # Pick LTF signals
            n_ltf = min(sz, len(pool_names))
            if n_ltf < 1:
                continue
            ltf_chosen = random.sample(pool_names, n_ltf)
            mask = pool[ltf_chosen[0]].copy()
            for nm in ltf_chosen[1:]:
                mask = mask & pool[nm]
            # Add HTF filter
            htf_chosen = []
            if htf_pool_names and random.random() < 0.7:
                htf_sig = random.choice(htf_pool_names)
                mask = mask & htf_pool[htf_sig]
                htf_chosen = [htf_sig]
            hold = random.choice(hold_bars_options)
            tp = random.choice(tp_options)
            rets = simulate_trades(c, mask, direction, hold, tp_pct=tp)
            m = compute_metrics(rets, entry_tf)
            if m and m["score"] > 0:
                all_sigs = sorted(ltf_chosen + htf_chosen)
                m.update({"symbol": symbol, "tf": entry_tf, "htf": htf_tag, "direction": direction, "signals": all_sigs, "hold_bars": hold, "tp": tp, "mode": "combo"})
                results.append(m)
    elif mode == "walkforward":
        # Walk-forward validation of top combos
        L_names = list(L.keys()); S_names = list(S.keys())
        n_combos = 1000
        split = int(n * WALK_FORWARD_SPLIT)
        for _ in range(n_combos):
            direction = random.choice(("LONG", "SHORT"))
            pool_names = L_names if direction == "LONG" else S_names
            pool = L if direction == "LONG" else S
            sz = random.choices([2, 3], weights=[0.5, 0.5])[0]
            n_ltf = min(sz, len(pool_names))
            chosen = random.sample(pool_names, n_ltf)
            mask = pool[chosen[0]].copy()
            for nm in chosen[1:]:
                mask = mask & pool[nm]
            hold = random.choice([5, 8, 13])
            # In-sample
            mask_is = mask.copy(); mask_is[split:] = False
            rets_is = simulate_trades(c[:split], mask_is[:split], direction, hold)
            m_is = compute_metrics(rets_is, entry_tf)
            if not m_is or m_is["sharpe"] < 1.5:
                continue
            # Out-of-sample
            mask_oos = mask.copy(); mask_oos[:split] = False
            rets_oos = simulate_trades(c, mask_oos, direction, hold)
            m_oos = compute_metrics(rets_oos, entry_tf)
            if m_oos and m_oos["n_trades"] >= 5:
                # Report with both IS and OOS metrics
                m_oos.update({"symbol": symbol, "tf": entry_tf, "htf": htf_tag, "direction": direction, "signals": sorted(chosen), "hold_bars": hold, "tp": None, "mode": "walkforward", "is_sharpe": m_is["sharpe"], "is_wr": m_is["win_rate"], "oos_sharpe": m_oos["sharpe"], "oos_wr": m_oos["win_rate"]})
                results.append(m_oos)
    return results


# ═══ RESULT MANAGEMENT ═══════════════════════════════════════════════════════

def load_existing():
    best = {}
    if RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                key = (r["direction"], tuple(sorted(r["signals"])), r["tf"], r.get("htf", ""))
                if key not in best or r["score"] > best[key]["score"]:
                    best[key] = r
            except Exception:
                pass
    return best


def save_results(best, new_results):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if new_results:
        with RESULTS_JSONL.open("a") as f:
            for r in new_results:
                f.write(json.dumps(r) + "\n")
    top = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:TOP_N]
    TOP_FILE.write_text(json.dumps({"generated_at": datetime.utcnow().isoformat(), "total_unique": len(best), "top": top}, indent=2))


def print_report():
    if not TOP_FILE.exists():
        print("No results yet. Run the factory first.")
        return
    data = json.loads(TOP_FILE.read_text())
    top = data["top"]
    print(f"\n{'='*160}")
    print(f"BACKTEST FACTORY — TOP {min(50, len(top))} RESULTS (of {data['total_unique']} unique combos)")
    print(f"Generated: {data['generated_at']}")
    print(f"{'='*160}")
    print(f"{'#':>3} {'Score':>8} {'Sharpe':>8} {'Sortino':>8} {'WR':>6} {'PF':>6} {'MDD':>6} {'Trades':>7} {'TotRet':>8} {'Dir':>6} {'TF':>4} {'HTF':>12} {'Hold':>5} {'Mode':>10} {'Signals'}")
    print("-" * 160)
    for i, r in enumerate(top[:50]):
        sigs = ",".join(r["signals"][:4]) + ("..." if len(r["signals"]) > 4 else "")
        print(f"{i+1:>3} {r['score']:>8.2f} {r['sharpe']:>8.2f} {r.get('sortino',0):>8.2f} {r['win_rate']:>6.1%} {r['pf']:>6.1f} {r['max_dd']:>6.2f} {r['n_trades']:>7} {r['total_return']:>8.2f} {r['direction']:>6} {r['tf']:>4} {r.get('htf',''):>12} {r['hold_bars']:>5} {r.get('mode',''):>10} {sigs}")
    # Aggregate stats
    print(f"\n{'='*80}")
    print("SIGNAL FREQUENCY IN TOP 200")
    from collections import Counter
    sig_counts = Counter()
    for r in top[:200]:
        for s in r["signals"]:
            sig_counts[s] += 1
    for sig, cnt in sig_counts.most_common(30):
        print(f"  {sig:<35} {cnt:>4}x")
    print(f"\nTF DISTRIBUTION:")
    tf_counts = Counter(r["tf"] for r in top[:200])
    for tf, cnt in tf_counts.most_common():
        print(f"  {tf:<6} {cnt:>4}x")


# ═══ MAIN ════════════════════════════════════════════════════════════════════

def get_all_symbols():
    symbols = set()
    for f in KLINES_DIR.glob("*_1h.json"):
        sym = f.stem.replace("_1h", "")
        symbols.add(sym)
    priority = ["BTCUSDT", "BTCUSDC", "ETHUSDT", "ETHUSDC", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDC", "LINKUSDC", "AVAXUSDT", "DOTUSDT", "UNIUSDT", "AAVEUSDC"]
    ordered = [s for s in priority if s in symbols]
    remaining = sorted(symbols - set(ordered))
    return ordered + remaining


def run_factory(max_symbols=999, max_rounds=0, sweep_only=False):
    global shutdown_flag
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    symbols = get_all_symbols()[:max_symbols]
    logger.info(f"FACTORY START | {len(symbols)} symbols × {len(TFS)} TFs × {sum(len(v) for v in TF_COMBOS.values())} HTF combos | Workers: {N_WORKERS}")
    best = load_existing()
    logger.info(f"Loaded {len(best)} existing results")
    total_tasks = 0
    new_results = []
    last_save = time.time()
    round_num = 0
    modes = ["sweep"] if sweep_only else ["sweep", "combo", "walkforward"]
    with Pool(processes=N_WORKERS, maxtasksperchild=20) as pool:
        while not shutdown_flag:
            round_num += 1
            if max_rounds > 0 and round_num > max_rounds:
                break
            mode = modes[round_num % len(modes)] if len(modes) > 1 else modes[0]
            # Build task list
            batch_syms = random.sample(symbols, min(60, len(symbols)))
            tasks = []
            for sym in batch_syms:
                for entry_tf in TFS:
                    htf_combos = TF_COMBOS.get(entry_tf, [[]])
                    for htf_list in htf_combos:
                        tasks.append((sym, entry_tf, htf_list, mode, random.randint(0, 2**31)))
            random.shuffle(tasks)
            logger.info(f"[Round {round_num}] {len(tasks)} tasks | mode={mode} | unique={len(best)}")
            for batch_results in pool.imap_unordered(worker_task, tasks, chunksize=4):
                total_tasks += 1
                for r in batch_results:
                    key = (r["direction"], tuple(sorted(r["signals"])), r["tf"], r.get("htf", ""))
                    if key not in best or r["score"] > best[key]["score"]:
                        best[key] = r
                        new_results.append(r)
                if time.time() - last_save > SAVE_INTERVAL:
                    logger.info(f"  SAVE | tasks={total_tasks} unique={len(best)} new_this_batch={len(new_results)}")
                    save_results(best, new_results)
                    new_results = []
                    last_save = time.time()
                    # Print top 3
                    top3 = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:3]
                    for i, r in enumerate(top3, 1):
                        logger.info(f"  #{i} {r['direction']:>5s} {r['signals']} score={r['score']:.2f} sharpe={r['sharpe']:.2f} wr={r['win_rate']:.1%} n={r['n_trades']} tf={r['tf']}")
                if shutdown_flag:
                    break
            if new_results:
                save_results(best, new_results)
                new_results = []
                last_save = time.time()
            # Save progress
            PROGRESS_FILE.write_text(json.dumps({"round": round_num, "total_tasks": total_tasks, "unique_combos": len(best), "last_update": datetime.utcnow().isoformat()}))
    logger.info(f"FACTORY DONE | {total_tasks} tasks | {len(best)} unique combos")
    if new_results:
        save_results(best, new_results)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest Factory — comprehensive indicator × TF testing")
    parser.add_argument("--symbols", type=int, default=999, help="Max symbols to test")
    parser.add_argument("--rounds", type=int, default=0, help="Max rounds (0=infinite)")
    parser.add_argument("--report", action="store_true", help="Print current top results")
    parser.add_argument("--sweep-only", action="store_true", help="Single-metric sweep only")
    parser.add_argument("--workers", type=int, default=N_WORKERS, help="Parallel workers")
    args = parser.parse_args()
    if args.report:
        print_report()
        return
    run_factory(max_symbols=args.symbols, max_rounds=args.rounds, sweep_only=args.sweep_only)


if __name__ == "__main__":
    main()
