#!/usr/bin/env python3
"""
ABLATION BACKTEST — Test Every BACKTEST_CHANGE Individually Against Baseline
=============================================================================
For each BACKTEST_CHANGE parameter:
  - Run the full backtest with BASELINE config (March 4, 2026)
  - Run with that ONE parameter changed to its current value
  - Compare: Sharpe, WR, PnL, PF

Uses the same backtest_master.py indicator engine.
Outputs: SQLite DB + Markdown report ranking every change by Sharpe delta.

Usage:
  python3 ablation_backtest.py [--workers 14] [--symbols-limit 50]
"""
import json, os, sys, time, math, sqlite3, argparse
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np

BASE = Path("/home/niels/binance-sandbox")
KLINES_DIR = BASE / "klines_cache"
RESULTS_DIR = BASE / "backtest_framework" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "ablation_backtest.db"
REPORT_PATH = RESULTS_DIR / "ABLATION_REPORT.md"

FEE = 0.0008
WARMUP = 200
MIN_TRADES = 5

# ═══════════════════════════════════════════════════════════════════════
# BASELINE vs CURRENT — Every BACKTEST_CHANGE parameter
# Each entry: (change_id, param_name, baseline_value, current_value, description)
# Baseline = March 4 2026 config (pre-all-changes)
# ═══════════════════════════════════════════════════════════════════════
CHANGES = [
    # --- TF Architecture ---
    ("BC_1", "TF_FOCUS", "15m", "3m", "Primary TF: 15m→3m"),
    ("BC_2", "TF_FOCUS_WEIGHT", 5.0, 8.0, "TF focus weight: 5→8"),
    ("BC_32", "TF_ALIGNMENT_MIN_TOTAL", 1, 3, "Alignment min total: none→3 (baseline had no alignment filter, used HTF_CONF instead)"),
    ("BC_33", "TF_ALIGNMENT_MIN_SHORT", 1, 1, "Alignment min short: none→1"),
    ("BC_34", "TF_ALIGNMENT_MIN_LONG", 1, 1, "Alignment min long: none→1"),
    ("BC_35", "HTF1_CONF", True, False, "1h confirmation: ON→OFF"),
    # --- Entry Signals (new) ---
    ("BC_3", "EMA_DIST_ENTRY_ENABLED", False, True, "EMA distance entry signal"),
    ("BC_4", "MOM3_ENTRY_ENABLED", False, True, "3-bar momentum entry"),
    ("BC_5", "MOM5_ENTRY_ENABLED", False, True, "5-bar momentum entry"),
    ("BC_6", "BB_ENTRY_LONG_THRESHOLD", -999.0, -0.2, "BB %B entry threshold"),
    ("BC_7", "SMA200_DIST_LONG_THRESHOLD", -2.0, -3.0, "SMA200 dist threshold: -2→-3"),
    # --- Exit Architecture ---
    ("BC_12", "CYCLE_TP_TIERED_ENABLED", False, True, "Aggressive tiered TP"),
    ("BC_14", "STOCH_CROSS_3M_EXIT_ENABLED", False, True, "3m stoch cross exit"),
    ("BC_15", "OPTIMAL_HOLD_BARS_3M", 999, 21, "Max hold 3m: unlimited→21 bars"),
    ("BC_16", "OPTIMAL_HOLD_BARS_15M", 999, 13, "Max hold 15m: unlimited→13 bars"),
    ("BC_101_TP", "ACCOUNT_TP_PCT", 0.02, 0.005, "TP: 2%→0.5%"),
    ("BC_112", "EXIT_GAIN_THRESHOLD_MIN", 0.15, 1.0, "Gain threshold: 0.15→1.0"),
    # --- Stoch/K-zone ---
    ("BC_105", "K3M_CAP", 80, 80, "K3M cap (unchanged, control)"),
    ("BC_9", "K3M_FLOOR", 0, 30, "K3M floor: none→30"),
    ("BC_109", "K_ZONE_ENTRY_ENABLED", False, True, "K-zone entry"),
    ("BC_101_KZ", "K_ZONE_LONG_THRESHOLD", 35, 90, "K-zone long: 35→90"),
    ("BC_101_KS", "K_ZONE_SHORT_THRESHOLD", 65, 10, "K-zone short: 65→10"),
    # --- Sizing ---
    ("BC_21", "START_POSITION_SIZE", 55.0, 18.0, "Start size: $55→$18"),
    ("BC_22", "MAX_ORDER_VALUE", 92, 120, "Max order: $92→$120"),
    ("BC_24", "EMA_DIST_SIZING_ENABLED", False, True, "EMA distance sizing"),
    ("BC_122", "DC_EDGE_SIZING_ENABLED", False, True, "DC edge sizing"),
    ("BC_25", "HIGH_GAIN_AUGMENTATION_MIN_SIZE", 200, 50, "Aug min size: $200→$50"),
    # --- Loss Handling ---
    ("BC_17", "STOP_LOSS_THRESHOLD", 0.2, 999.0, "Stop loss: 0.2%→disabled"),
    ("BC_18", "FAST_CUT_LOSS_THRESHOLD", -1.5, -999.0, "Fast cut loss→disabled"),
    ("BC_19", "REDUCE_HUGE_LOSS_THRESHOLD", -2.0, -999.0, "Huge loss reduce→disabled"),
    ("BC_20", "BREAKOUT_GUARD_LOSS_THRESHOLD", -0.5, -999.0, "Breakout guard→disabled"),
    ("BC_113", "STOP_MAJOR_LOSS_BLOCK_ENABLED", False, True, "Block STOP_MAJOR_LOSS path"),
    ("BC_114", "IMMEDIATE_WRONG_WAY_ENABLED", True, False, "Wrong-way exit→disabled"),
    ("BC_115", "FAST_RISER_DOUBLE_ENABLED", True, False, "Fast riser double→disabled"),
    ("BC_116", "AUGMENT_PYRAMID_ENABLED", True, False, "Pyramid augment→disabled"),
    # --- Hedge/Ratio ---
    ("BC_123", "HEDGE_MODE", True, False, "Hedge mode: ON→OFF"),
    ("BC_253", "RATIO_MULTIPLIER", 4.0, 3.0, "Ratio multiplier: 4→3"),
    # --- Mover Detection ---
    ("BC_111_MOVER", "MOVER_DETECTION_ENABLED", False, True, "Mover detection"),
    ("BC_111_RSI", "RSI_ENTRY_GATE_ENABLED", False, True, "RSI<37 entry gate"),
    ("BC_111_RSI_TH", "RSI_ENTRY_MAX_LONG", 100.0, 37.0, "RSI entry max: 100→37"),
    # --- Momentum Fade ---
    ("BC_113b", "MOMENTUM_FADE_ENABLED", False, True, "Momentum fade entry"),
    # --- Cooldowns ---
    ("BC_42", "REDUCTION_COOLDOWN_SECONDS", 90.0, 15.0, "Reduce cooldown: 90→15s"),
    ("BC_41", "AUGMENTATION_COOLDOWN_SECONDS", 480.0, 90.0, "Aug cooldown: 480→90s"),
    ("BC_43", "FORCE_REFRESH_SECONDS", 16, 10, "Force refresh: 16→10s"),
    ("BC_40", "CIRCUIT_BREAKER_COOLDOWN", 120, 60, "Circuit breaker: 120→60s"),
    # --- HA signal ---
    ("BC_31", "HA_3M_ENTRY_WEIGHT", 0.0, -0.5, "HA 3m entry: neutral→contrarian"),
    # --- Entry Filters (new) ---
    ("BC_100", "ENTRY_VOL_MIN_RATIO", 1.0, 1.3, "Min volume ratio: 1.0→1.3"),
    ("BC_103", "ENTRY_ATR_PCT_MIN", 0.0, 1.5, "Min ATR%: none→1.5%"),
    ("BC_110", "BOUNCE_REENTRY_ENABLED", False, True, "Bounce reentry"),
    # --- Trail/Gain ---
    ("BC_105_TRAIL", "WIN_TRAIL_EROSION_PCT", 1.0, 0.30, "Trail erosion: 100%→30%"),
    ("BC_106", "HTF_STRICT", False, True, "HTF strict mode"),
    # --- Volume filter ---
    ("BC_23", "DC_WIDTH_MAX_MULT", 8.0, 5.0, "DC width max: 8→5"),
    # --- NOLOSS TP ---
    ("BC_100_TP", "NOLOSS_MIN_PROFIT_PCT", 0.50, 0.50, "NOLOSS TP (unchanged, control)"),
    # --- Short RSI filter ---
    ("BC_101_RSI", "SHORT_RSI_MIN_1H", 0, 40, "Short RSI gate: none→40"),
    # --- Long stoch chase block ---
    ("BC_102", "LONG_STOCH_CHASE_BLOCK", False, True, "Block chasing overbought longs"),
    # --- Short above SMA20 ---
    ("BC_104", "SHORT_ABOVE_SMA20_BONUS", 0, 15, "Short above SMA20 bonus"),
]

# ═══════════════════════════════════════════════════════════════════════
# SYMBOLS
# ═══════════════════════════════════════════════════════════════════════
def get_symbols(limit=None):
    syms = json.load(open(BASE / "symbols.json"))
    if limit:
        syms = syms[:limit]
    return syms

# ═══════════════════════════════════════════════════════════════════════
# DATA + INDICATORS (reused from backtest_master)
# ═══════════════════════════════════════════════════════════════════════
def load_tf(sym, tf):
    p = KLINES_DIR / f"{sym}_{tf}.json"
    if not p.exists():
        return None
    with open(p) as f:
        raw = json.load(f)
    if len(raw) < 50:
        return None
    o = np.array([float(x["open"]) if isinstance(x["open"], str) else x["open"] for x in raw], dtype=np.float64)
    h = np.array([float(x["high"]) if isinstance(x["high"], str) else x["high"] for x in raw], dtype=np.float64)
    l = np.array([float(x["low"]) if isinstance(x["low"], str) else x["low"] for x in raw], dtype=np.float64)
    c = np.array([float(x["close"]) if isinstance(x["close"], str) else x["close"] for x in raw], dtype=np.float64)
    v = np.array([float(x["volume"]) if isinstance(x["volume"], str) else x["volume"] for x in raw], dtype=np.float64)
    return {"o": o, "h": h, "l": l, "c": c, "v": v}

def stoch_kd(h, l, c, k_period=14, smooth=3):
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hi = np.max(h[i - k_period + 1:i + 1])
        li = np.min(l[i - k_period + 1:i + 1])
        raw_k[i] = (c[i] - li) / (hi - li) * 100.0 if hi > li else 50.0
    k = np.full(n, 50.0)
    for i in range(smooth - 1, n):
        k[i] = raw_k[i - smooth + 1:i + 1].mean()
    d = np.full(n, 50.0)
    for i in range(smooth - 1, n):
        d[i] = k[i - smooth + 1:i + 1].mean()
    return k, d

def rsi_calc(c, period=14):
    n = len(c)
    out = np.full(n, 50.0)
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.zeros(n)
    avg_l = np.zeros(n)
    if period < n:
        avg_g[period] = gain[1:period + 1].mean()
        avg_l[period] = loss[1:period + 1].mean()
        for i in range(period + 1, n):
            avg_g[i] = (avg_g[i - 1] * (period - 1) + gain[i]) / period
            avg_l[i] = (avg_l[i - 1] * (period - 1) + loss[i]) / period
        for i in range(period, n):
            if avg_l[i] > 1e-10:
                rs = avg_g[i] / avg_l[i]
                out[i] = 100.0 - 100.0 / (1.0 + rs)
            elif avg_g[i] > 0:
                out[i] = 100.0
    return out

def ema(c, period):
    n = len(c)
    out = np.zeros(n)
    if n == 0:
        return out
    out[0] = c[0]
    k = 2.0 / (period + 1)
    for i in range(1, n):
        out[i] = c[i] * k + out[i - 1] * (1 - k)
    return out

def sma(c, period):
    n = len(c)
    out = np.zeros(n)
    cs = np.cumsum(c)
    out[period - 1:] = (cs[period - 1:] - np.concatenate([[0], cs[:-period]])) / period
    return out

def atr_calc(h, l, c, period=14):
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = np.zeros(n)
    if period < n:
        a[period] = tr[:period + 1].mean()
        for i in range(period + 1, n):
            a[i] = (a[i - 1] * (period - 1) + tr[i]) / period
    return a

def bb_pctb(c, period=20, std_mult=2.0):
    n = len(c)
    out = np.full(n, 0.5)
    s = sma(c, period)
    for i in range(period - 1, n):
        std = np.std(c[i - period + 1:i + 1])
        if std > 1e-10:
            upper = s[i] + std_mult * std
            lower = s[i] - std_mult * std
            out[i] = (c[i] - lower) / (upper - lower) if upper > lower else 0.5
    return out

def heikin_ashi(o, h, l, c):
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty(len(c))
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(c)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    return ha_o, ha_c

def donchian(h, l, period=20):
    n = len(h)
    dc_h = np.zeros(n)
    dc_l = np.zeros(n)
    for i in range(period, n):
        dc_h[i] = np.max(h[i - period:i])
        dc_l[i] = np.min(l[i - period:i])
    return dc_h, dc_l, (dc_h + dc_l) / 2.0

def make_idx_map(n_primary, n_tf):
    if n_tf <= 0:
        return np.zeros(n_primary, dtype=np.int32)
    ratio = n_tf / n_primary
    idx = (np.arange(n_primary) * ratio - 1).astype(np.int32)
    np.clip(idx, 0, n_tf - 1, out=idx)
    return idx

# ═══════════════════════════════════════════════════════════════════════
# PRECOMPUTE ALL INDICATORS FOR A SYMBOL
# ═══════════════════════════════════════════════════════════════════════
def precompute_symbol(sym):
    """Load all TFs and compute all indicators we need for any config."""
    tfs = ["1m", "3m", "15m", "1h", "4h"]
    data = {}
    for tf in tfs:
        d = load_tf(sym, tf)
        if d is not None:
            data[tf] = d
    if "3m" not in data and "15m" not in data:
        return None
    result = {"data": data, "indicators": {}}
    for tf in tfs:
        if tf not in data:
            continue
        d = data[tf]
        n = len(d["c"])
        k, dd = stoch_kd(d["h"], d["l"], d["c"])
        r = rsi_calc(d["c"], 14)
        ha_o, ha_c = heikin_ashi(d["o"], d["h"], d["l"], d["c"])
        dc_h, dc_l, dc_b = donchian(d["h"], d["l"], 20)
        atr_v = atr_calc(d["h"], d["l"], d["c"], 14)
        ema20 = ema(d["c"], 20)
        sma200 = sma(d["c"], 200)
        bb = bb_pctb(d["c"], 20)
        vol_sma = sma(d["v"], 20)
        result["indicators"][tf] = {"k": k, "d": dd, "rsi": r, "ha_o": ha_o, "ha_c": ha_c, "dc_h": dc_h, "dc_l": dc_l, "dc_b": dc_b, "atr": atr_v, "ema20": ema20, "sma200": sma200, "bb": bb, "vol_sma": vol_sma}
    return result

# ═══════════════════════════════════════════════════════════════════════
# SIMULATE ONE CONFIG ON ONE SYMBOL
# ═══════════════════════════════════════════════════════════════════════
def simulate_config(sym_data, params):
    """
    Run backtest on precomputed data with given parameter dict.
    Returns dict with sharpe, wr, pnl, pf, trades.
    """
    tf_focus = params.get("TF_FOCUS", "3m")
    if tf_focus not in sym_data["data"]:
        tf_focus = "15m" if "15m" in sym_data["data"] else list(sym_data["data"].keys())[0]
    d = sym_data["data"][tf_focus]
    ind = sym_data["indicators"].get(tf_focus)
    if ind is None:
        return None
    c, h, l, o, v = d["c"], d["h"], d["l"], d["o"], d["v"]
    n = len(c)
    if n < WARMUP + 50:
        return None
    k, dd = ind["k"], ind["d"]
    r = ind["rsi"]
    ha_o, ha_c = ind["ha_o"], ind["ha_c"]
    dc_h, dc_l, dc_b = ind["dc_h"], ind["dc_l"], ind["dc_b"]
    atr_v = ind["atr"]
    ema20 = ind["ema20"]
    sma200 = ind["sma200"]
    bb = ind["bb"]
    vol_sma = ind["vol_sma"]
    # Load HTF indicators if available
    htf_data = {}
    for htf in ["15m", "1h", "4h"]:
        if htf in sym_data["indicators"] and htf != tf_focus:
            htf_ind = sym_data["indicators"][htf]
            htf_n = len(sym_data["data"][htf]["c"])
            idx_map = make_idx_map(n, htf_n)
            htf_data[htf] = {key: val[idx_map] for key, val in htf_ind.items()}
    # --- Build entry signals ---
    # Base signal: stoch crossover on focus TF
    k_prev = np.roll(k, 1); k_prev[0] = k[0]
    d_prev = np.roll(dd, 1); d_prev[0] = dd[0]
    xover_long = (k > dd) & (k_prev <= d_prev)
    xover_short = (k < dd) & (k_prev >= d_prev)
    xover_long[:WARMUP] = False
    xover_short[:WARMUP] = False
    # K-zone entry
    if params.get("K_ZONE_ENTRY_ENABLED", False):
        k_zone_long_th = params.get("K_ZONE_LONG_THRESHOLD", 35)
        k_zone_short_th = params.get("K_ZONE_SHORT_THRESHOLD", 65)
        k_turn_up = (k > k_prev) & (k < k_zone_long_th)
        k_turn_down = (k < k_prev) & (k > k_zone_short_th)
        xover_long = xover_long | k_turn_up
        xover_short = xover_short | k_turn_down
    # RSI entry gate
    if params.get("RSI_ENTRY_GATE_ENABLED", False):
        rsi_max_long = params.get("RSI_ENTRY_MAX_LONG", 37.0)
        rsi_min_short = params.get("RSI_ENTRY_MIN_SHORT", 63.0)
        # Use 1h RSI if available, else focus TF
        rsi_gate = htf_data.get("1h", {}).get("rsi", r)
        xover_long = xover_long & (rsi_gate < rsi_max_long)
        xover_short = xover_short & (rsi_gate > rsi_min_short)
    # Volume filter
    vol_min = params.get("ENTRY_VOL_MIN_RATIO", 1.0)
    if vol_min > 1.0:
        vol_ok = np.zeros(n, dtype=bool)
        for i in range(20, n):
            if vol_sma[i] > 0:
                vol_ok[i] = v[i] / vol_sma[i] >= vol_min
        xover_long = xover_long & vol_ok
        xover_short = xover_short & vol_ok
    # ATR% filter
    atr_min = params.get("ENTRY_ATR_PCT_MIN", 0.0)
    if atr_min > 0:
        atr_pct = np.zeros(n)
        for i in range(14, n):
            if c[i] > 0:
                atr_pct[i] = atr_v[i] / c[i] * 100.0
        xover_long = xover_long & (atr_pct >= atr_min)
        xover_short = xover_short & (atr_pct >= atr_min)
    # BB entry filter
    bb_th = params.get("BB_ENTRY_LONG_THRESHOLD", -999.0)
    if bb_th > -100:
        xover_long = xover_long & (bb < (0.5 + bb_th))
        xover_short = xover_short & (bb > (0.5 - bb_th))
    # SMA200 distance filter
    sma200_th = params.get("SMA200_DIST_LONG_THRESHOLD", -2.0)
    if sma200_th > -100:
        sma200_dist = np.zeros(n)
        for i in range(200, n):
            if sma200[i] > 0:
                sma200_dist[i] = (c[i] - sma200[i]) / sma200[i] * 100.0
        xover_long = xover_long & (sma200_dist >= sma200_th)
    # Short RSI gate
    short_rsi_min = params.get("SHORT_RSI_MIN_1H", 0)
    if short_rsi_min > 0:
        rsi_1h = htf_data.get("1h", {}).get("rsi", r)
        xover_short = xover_short & (rsi_1h >= short_rsi_min)
    # Long stoch chase block
    if params.get("LONG_STOCH_CHASE_BLOCK", False):
        k_1h = htf_data.get("1h", {}).get("k", k)
        ha_1h_o = htf_data.get("1h", {}).get("ha_o", ha_o)
        ha_1h_c = htf_data.get("1h", {}).get("ha_c", ha_c)
        ha_streak = np.zeros(n)
        for i in range(1, n):
            if ha_1h_c[i] > ha_1h_o[i]:
                ha_streak[i] = ha_streak[i - 1] + 1
            else:
                ha_streak[i] = 0
        xover_long = xover_long & ~((k_1h > 70) & (ha_streak > 2))
    # K3M cap/floor
    k3m_cap = params.get("K3M_CAP", 80)
    k3m_floor = params.get("K3M_FLOOR", 0)
    if k3m_cap < 100:
        xover_long = xover_long & (k < k3m_cap)
    if k3m_floor > 0:
        xover_short = xover_short & (k > k3m_floor)
    # HTF confirmation (baseline had this ON)
    if params.get("HTF1_CONF", True):
        if "1h" in htf_data:
            k_1h = htf_data["1h"]["k"]
            d_1h = htf_data["1h"]["d"]
            xover_long = xover_long & (k_1h > d_1h)
            xover_short = xover_short & (k_1h < d_1h)
    # TF alignment
    align_min = params.get("TF_ALIGNMENT_MIN_TOTAL", 4)
    if align_min > 1:
        aligned = np.ones(n, dtype=np.int32)
        for htf in ["15m", "1h", "4h"]:
            if htf in htf_data:
                htf_k = htf_data[htf]["k"]
                htf_d = htf_data[htf]["d"]
                aligned_long_htf = (htf_k > htf_d).astype(np.int32)
                aligned = aligned + aligned_long_htf
        xover_long_aligned = xover_long & (aligned >= align_min)
        aligned_short = np.ones(n, dtype=np.int32)
        for htf in ["15m", "1h", "4h"]:
            if htf in htf_data:
                htf_k = htf_data[htf]["k"]
                htf_d = htf_data[htf]["d"]
                aligned_short += (htf_k < htf_d).astype(np.int32)
        xover_short_aligned = xover_short & (aligned_short >= align_min)
        xover_long = xover_long_aligned
        xover_short = xover_short_aligned
    # HA contrarian weight
    ha_weight = params.get("HA_3M_ENTRY_WEIGHT", 0.0)
    if ha_weight < 0:
        ha_green = ha_c > ha_o
        xover_long = xover_long & ~ha_green
        xover_short = xover_short & ha_green
    # Momentum fade entries
    if params.get("MOMENTUM_FADE_ENABLED", False):
        body_atr_min = params.get("MOMENTUM_FADE_BODY_ATR_MIN", 2.0)
        fade_vol_min = params.get("MOMENTUM_FADE_VOL_MIN", 2.0)
        for i in range(20, n):
            if atr_v[i] > 0 and vol_sma[i] > 0:
                body = abs(c[i] - o[i])
                if body >= body_atr_min * atr_v[i] and v[i] >= fade_vol_min * vol_sma[i]:
                    if c[i] > o[i] and k[i] > 60:
                        xover_short[i] = True
                    elif c[i] < o[i] and k[i] < 40:
                        xover_long[i] = True
    # --- SIMULATE TRADES ---
    tp_pct = params.get("ACCOUNT_TP_PCT", 0.02)
    noloss = params.get("NOLOSS_MIN_PROFIT_PCT", 0.50) / 100.0
    stop_loss_th = params.get("STOP_LOSS_THRESHOLD", 0.2) / 100.0
    gain_th_low = params.get("EXIT_GAIN_THRESHOLD_MIN", 0.15) / 100.0
    max_hold_bars = params.get("OPTIMAL_HOLD_BARS_3M", 999)
    if params.get("TF_FOCUS", "3m") == "15m":
        max_hold_bars = params.get("OPTIMAL_HOLD_BARS_15M", 999)
    trail_erosion = params.get("WIN_TRAIL_EROSION_PCT", 1.0)
    stop_major_block = params.get("STOP_MAJOR_LOSS_BLOCK_ENABLED", False)
    wrong_way_enabled = params.get("IMMEDIATE_WRONG_WAY_ENABLED", True)
    # Collect entry indices
    long_entries = np.nonzero(xover_long)[0]
    short_entries = np.nonzero(xover_short)[0]
    trades = []
    def run_trades(entries, is_long):
        last_exit = -1
        for ei in entries:
            if ei <= last_exit or ei >= n - 2:
                continue
            ep = c[ei]
            if ep <= 0:
                continue
            max_gain_pct = 0.0
            exit_bar = min(ei + max_hold_bars, n - 1)
            exit_price = c[exit_bar]
            exited = False
            for bi in range(ei + 1, min(ei + max_hold_bars + 1, n)):
                if is_long:
                    pnl_pct = (c[bi] - ep) / ep
                    high_pnl = (h[bi] - ep) / ep
                    low_pnl = (l[bi] - ep) / ep
                else:
                    pnl_pct = (ep - c[bi]) / ep
                    high_pnl = (ep - l[bi]) / ep
                    low_pnl = (ep - h[bi]) / ep
                max_gain_pct = max(max_gain_pct, high_pnl)
                # TP hit
                if high_pnl >= tp_pct:
                    exit_bar = bi
                    exit_price = ep * (1 + tp_pct) if is_long else ep * (1 - tp_pct)
                    exited = True
                    break
                # Trail erosion exit
                if trail_erosion < 1.0 and max_gain_pct > noloss:
                    if pnl_pct < max_gain_pct * (1 - trail_erosion):
                        exit_bar = bi
                        exit_price = c[bi]
                        exited = True
                        break
                # NOLOSS exit: gained enough then dropping back
                if max_gain_pct >= noloss and pnl_pct <= gain_th_low:
                    exit_bar = bi
                    exit_price = c[bi]
                    exited = True
                    break
                # Stop loss (if enabled)
                if not stop_major_block and stop_loss_th < 100:
                    if max_gain_pct >= stop_loss_th and pnl_pct <= 0:
                        exit_bar = bi
                        exit_price = c[bi]
                        exited = True
                        break
                # Wrong-way immediate exit
                if wrong_way_enabled and bi == ei + 1 and pnl_pct < -0.005:
                    exit_bar = bi
                    exit_price = c[bi]
                    exited = True
                    break
            if not exited:
                exit_bar = min(ei + max_hold_bars, n - 1)
                exit_price = c[exit_bar]
            if is_long:
                ret = (exit_price - ep) / ep - 2 * FEE
            else:
                ret = (ep - exit_price) / ep - 2 * FEE
            trades.append(ret)
            last_exit = exit_bar
    run_trades(long_entries, True)
    run_trades(short_entries, False)
    if len(trades) < MIN_TRADES:
        return None
    trades_arr = np.array(trades)
    wr = np.mean(trades_arr > 0) * 100
    avg_pnl = np.mean(trades_arr) * 100
    total_pnl = np.sum(trades_arr) * 100
    wins = trades_arr[trades_arr > 0]
    losses = trades_arr[trades_arr <= 0]
    pf = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    sharpe = np.mean(trades_arr) / np.std(trades_arr) * math.sqrt(len(trades_arr)) if np.std(trades_arr) > 1e-10 else 0.0
    return {"sharpe": round(sharpe, 4), "wr": round(wr, 2), "avg_pnl": round(avg_pnl, 4), "total_pnl": round(total_pnl, 2), "pf": round(pf, 3), "trades": len(trades)}

# ═══════════════════════════════════════════════════════════════════════
# BUILD BASELINE AND CHANGED CONFIGS
# ═══════════════════════════════════════════════════════════════════════
def build_baseline():
    """March 4 2026 config — before any BACKTEST_CHANGES."""
    return {c[1]: c[2] for c in CHANGES}

def build_changed(baseline, change):
    """Baseline + one change applied."""
    cfg = dict(baseline)
    cfg[change[1]] = change[3]
    return cfg

# ═══════════════════════════════════════════════════════════════════════
# WORKER: Test ALL changes on one symbol (precompute once)
# ═══════════════════════════════════════════════════════════════════════
def test_all_changes_on_symbol(sym):
    sym_data = precompute_symbol(sym)
    if sym_data is None:
        return []
    baseline = build_baseline()
    res_base = simulate_config(sym_data, baseline)
    results = []
    for change in CHANGES:
        change_id, param_name, baseline_val, current_val, description = change
        if baseline_val == current_val:
            continue
        changed = dict(baseline)
        changed[param_name] = current_val
        res_changed = simulate_config(sym_data, changed)
        if res_base is None or res_changed is None:
            continue
        results.append({
            "symbol": sym,
            "change_id": change_id,
            "param_name": param_name,
            "description": description,
            "baseline_val": str(baseline_val),
            "current_val": str(current_val),
            "base_sharpe": res_base["sharpe"],
            "base_wr": res_base["wr"],
            "base_pnl": res_base["total_pnl"],
            "base_pf": res_base["pf"],
            "base_trades": res_base["trades"],
            "changed_sharpe": res_changed["sharpe"],
            "changed_wr": res_changed["wr"],
            "changed_pnl": res_changed["total_pnl"],
            "changed_pf": res_changed["pf"],
            "changed_trades": res_changed["trades"],
            "sharpe_delta": round(res_changed["sharpe"] - res_base["sharpe"], 4),
            "wr_delta": round(res_changed["wr"] - res_base["wr"], 2),
            "pnl_delta": round(res_changed["total_pnl"] - res_base["total_pnl"], 2),
        })
    return results

# ═══════════════════════════════════════════════════════════════════════
# DB
# ═══════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DROP TABLE IF EXISTS ablation")
    conn.execute("""CREATE TABLE ablation (
        symbol TEXT, change_id TEXT, param_name TEXT, description TEXT,
        baseline_val TEXT, current_val TEXT,
        base_sharpe REAL, base_wr REAL, base_pnl REAL, base_pf REAL, base_trades INT,
        changed_sharpe REAL, changed_wr REAL, changed_pnl REAL, changed_pf REAL, changed_trades INT,
        sharpe_delta REAL, wr_delta REAL, pnl_delta REAL
    )""")
    conn.commit()
    return conn

# ═══════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════
def write_report(conn):
    rows = conn.execute("""
        SELECT change_id, param_name, description,
               baseline_val, current_val,
               AVG(sharpe_delta) as avg_sharpe_delta,
               AVG(wr_delta) as avg_wr_delta,
               AVG(pnl_delta) as avg_pnl_delta,
               SUM(CASE WHEN sharpe_delta > 0 THEN 1 ELSE 0 END) as symbols_improved,
               COUNT(*) as symbols_tested,
               AVG(base_sharpe) as avg_base_sharpe,
               AVG(changed_sharpe) as avg_changed_sharpe,
               AVG(base_wr) as avg_base_wr,
               AVG(changed_wr) as avg_changed_wr
        FROM ablation
        GROUP BY change_id
        ORDER BY avg_sharpe_delta DESC
    """).fetchall()
    lines = [
        f"# ABLATION BACKTEST REPORT",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Methodology:** Each BACKTEST_CHANGE tested individually against March 4 baseline.",
        f"**Positive Sharpe delta = change HELPS. Negative = change HURTS.**",
        "",
        "## Rankings (sorted by avg Sharpe delta across all symbols)",
        "",
        "| # | Change | Param | Baseline→Current | Avg Sharpe Δ | Avg WR Δ | Symbols ↑ | Symbols Tested | Verdict |",
        "|---|--------|-------|------------------|-------------|---------|-----------|----------------|---------|",
    ]
    for i, row in enumerate(rows):
        change_id, param, desc, base_v, curr_v, avg_sd, avg_wd, avg_pd, improved, tested, avg_bs, avg_cs, avg_bw, avg_cw = row
        pct_improved = improved / tested * 100 if tested > 0 else 0
        if avg_sd > 0.5 and pct_improved > 55:
            verdict = "**KEEP**"
        elif avg_sd < -0.5 and pct_improved < 45:
            verdict = "**REVERT**"
        elif abs(avg_sd) < 0.5:
            verdict = "NEUTRAL"
        else:
            verdict = "REVIEW"
        lines.append(f"| {i+1} | {change_id} | {param} | {base_v}→{curr_v} | {avg_sd:+.3f} | {avg_wd:+.2f}% | {improved}/{tested} ({pct_improved:.0f}%) | {tested} | {verdict} |")
    lines.extend(["", "## Summary", ""])
    keep = sum(1 for r in rows if r[5] > 0.5 and r[8] / r[9] > 0.55)
    revert = sum(1 for r in rows if r[5] < -0.5 and r[8] / r[9] < 0.45)
    neutral = len(rows) - keep - revert
    lines.append(f"- **KEEP:** {keep} changes improve performance")
    lines.append(f"- **REVERT:** {revert} changes hurt performance")
    lines.append(f"- **NEUTRAL:** {neutral} changes have negligible impact")
    lines.append("")
    lines.append("## Detailed: Changes that HURT (Sharpe Δ < -0.5)")
    lines.append("")
    for row in rows:
        if row[5] < -0.5:
            lines.append(f"- **{row[0]}** ({row[1]}): {row[2]} — Avg Sharpe Δ: {row[5]:+.3f}, {row[8]}/{row[9]} symbols improved")
    lines.append("")
    lines.append("## Detailed: Changes that HELP (Sharpe Δ > +0.5)")
    lines.append("")
    for row in rows:
        if row[5] > 0.5:
            lines.append(f"- **{row[0]}** ({row[1]}): {row[2]} — Avg Sharpe Δ: {row[5]:+.3f}, {row[8]}/{row[9]} symbols improved")
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport written to {REPORT_PATH}")

# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Ablation Backtest — test every BACKTEST_CHANGE")
    parser.add_argument("--workers", type=int, default=14)
    parser.add_argument("--symbols-limit", type=int, default=0, help="0=all symbols")
    args = parser.parse_args()
    symbols = get_symbols(args.symbols_limit if args.symbols_limit > 0 else None)
    active_changes = [c for c in CHANGES if c[2] != c[3]]
    print(f"Testing {len(active_changes)} changes × {len(symbols)} symbols")
    print(f"Using {args.workers} workers — 1 symbol per worker (precompute once, test all changes)")
    conn = init_db()
    done = 0
    total_results = 0
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(test_all_changes_on_symbol, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            done += 1
            sym = futures[fut]
            try:
                results = fut.result()
                for r in results:
                    conn.execute("INSERT INTO ablation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (r["symbol"], r["change_id"], r["param_name"], r["description"], r["baseline_val"], r["current_val"], r["base_sharpe"], r["base_wr"], r["base_pnl"], r["base_pf"], r["base_trades"], r["changed_sharpe"], r["changed_wr"], r["changed_pnl"], r["changed_pf"], r["changed_trades"], r["sharpe_delta"], r["wr_delta"], r["pnl_delta"]))
                total_results += len(results)
                conn.commit()
            except Exception as e:
                print(f"  ERROR {sym}: {e}")
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - done) / rate / 60 if rate > 0 else 0
            print(f"  [{done}/{len(symbols)}] {sym}: {total_results} results — {rate:.1f} sym/s — ETA {eta:.1f}m")
    elapsed = time.time() - t0
    print(f"\nDone! {total_results} results from {done} symbols in {elapsed/60:.1f} minutes")
    write_report(conn)
    conn.close()

if __name__ == "__main__":
    main()
