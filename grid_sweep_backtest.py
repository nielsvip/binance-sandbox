#!/usr/bin/env python3
"""
GRID SWEEP BACKTEST — Comprehensive entry + exit parameter optimization
========================================================================
Tests permissive vs restrictive entries AND strict vs loose exits across
ALL tunable parameters using REAL indicators from ez_indicators.py.

3-Phase approach:
  Phase 1: Entry grid (sweep RSI, MTS, K-caps, alignment, score thresholds)
           with default exits — find top 20 entry combos
  Phase 2: Exit grid (sweep TP%, trail%, max hold, structure exit, tiered TP)
           with default entries — find top 20 exit combos
  Phase 3: Cross top-20 entry x top-20 exit = 400 combined combos

Runs on server with 48 symbols (4+ years of 15m data), 16 CPUs.

Usage:
  python3 grid_sweep_backtest.py --phase 1 --workers 16 --limit 48
  python3 grid_sweep_backtest.py --phase 2 --workers 16 --limit 48
  python3 grid_sweep_backtest.py --phase 3 --workers 16 --limit 48
  python3 grid_sweep_backtest.py --phase all --workers 16 --limit 48
"""
import argparse
import itertools
import json
import math
import os
import sys
import time
import signal
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Detect environment
if Path("/home/niels/binance-sandbox").exists():
    KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
    CACHE_DIR = Path("/home/niels/binance-sandbox/indicator_cache")
    SYMBOLS_FILE = Path("/home/niels/binance-sandbox/backtest_48_symbols.json")
elif (SCRIPT_DIR / "klines_cache_backtest").exists():
    KLINES_DIR = SCRIPT_DIR / "klines_cache_backtest"
    CACHE_DIR = SCRIPT_DIR / "indicator_cache"
    SYMBOLS_FILE = SCRIPT_DIR / "symbols.json"
else:
    KLINES_DIR = SCRIPT_DIR / "klines_cache"
    CACHE_DIR = SCRIPT_DIR / "indicator_cache"
    SYMBOLS_FILE = SCRIPT_DIR / "symbols.json"

RESULTS_DIR = SCRIPT_DIR / "data" / "grid_sweep"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

FEE = 0.0008  # 0.08% per side (taker)
WARMUP = 300
MIN_TRADES = 5

# Graceful shutdown
_SHUTDOWN = False
def _handle_signal(sig, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    print("\n[SHUTDOWN] Finishing current workers...")
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══════════════════════════════════════════════════════════════
# DEFAULT PARAMETERS (current production config)
# ═══════════════════════════════════════════════════════════════

DEFAULT_ENTRY = {
    "RSI_MAX_LONG": 37.0,         # RSI must be < this for LONG
    "RSI_MIN_SHORT": 63.0,        # RSI must be > this for SHORT
    "MTS_BOTTOM_MIN": 10.0,       # Multi-TF state bottom score min (LONG)
    "MTS_BOTTOM_MIN_SHORT": 5.0,  # MTS bottom score min (SHORT)
    "MTS_ENTRY_QUALITY_MIN": 5.0, # MTS entry quality min (LONG)
    "MTS_EQ_MIN_SHORT": 0.0,      # MTS entry quality min (SHORT)
    "K3M_CAP": 80,                # Block LONG when k_3m > this
    "K3M_FLOOR": 30,              # Block SHORT when k_3m < this (mirror)
    "SCORE_MIN": 18,              # Minimum total score to enter
    "TF_ALIGN_MIN": 2,            # Minimum TF alignment count
    "EMA_DIST_LONG": -1.0,        # Enter LONG when ema_dist < this
    "EMA_DIST_SHORT": 1.0,        # Enter SHORT when ema_dist > this
    "WT_CROSS_REQUIRED": True,    # Require WT cross for entry
    "VOL_MIN_RATIO": 1.3,         # Min relative volume
    "HTF_STRICT": True,           # Require all HTFs aligned
}

DEFAULT_EXIT = {
    "TP_PCT": 0.5,               # Take profit %
    "TRAIL_PCT": 0.50,           # Trail stop as fraction of max gain
    "TRAIL_ACTIVATE": 0.5,       # Min gain to activate trail
    "MAX_HOLD": 200,             # Max bars to hold
    "STRUCTURE_EXIT": True,      # Exit on structure break (K cross against)
    "WT_EXIT": True,             # Exit on WT cross against
    "K_EXHAUSTION_EXIT": True,   # Exit when K > 90 (LONG) or K < 10 (SHORT)
    "K_EXHAUST_LEVEL": 90,       # K level for exhaustion
    "TIERED_TP": False,          # Use tiered TP levels
    "TIERED_LEVELS": [0.15, 0.30, 0.50, 0.70, 1.0, 1.5, 2.0, 3.0],
    "TIERED_FRAC": 0.25,         # Fraction to close at each tier
    "NOLOSS_MIN": 0.0,           # Minimum gain to allow exit (0 = breakeven)
    "GAIN_THRESHOLD_MIN": 1.0,   # Min gain for non-TP exits
}


# ═══════════════════════════════════════════════════════════════
# PARAMETER GRIDS
# ═══════════════════════════════════════════════════════════════

ENTRY_GRID = {
    "RSI_MAX_LONG":       [25, 30, 37, 45, 55, 99],    # 99 = disabled
    "MTS_BOTTOM_MIN":     [0, 5, 10, 15, 20, 30],
    "MTS_ENTRY_QUALITY_MIN": [0, 5, 10, 15],
    "K3M_CAP":            [60, 70, 80, 90, 100],
    "SCORE_MIN":          [10, 14, 18, 22, 26],
    "WT_CROSS_REQUIRED":  [True, False],
}
# 6 x 6 x 4 x 5 x 5 x 2 = 7,200 entry combos

EXIT_GRID = {
    "TP_PCT":             [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0],
    "TRAIL_PCT":          [0.0, 0.30, 0.50, 0.70],     # 0 = no trail
    "TRAIL_ACTIVATE":     [0.3, 0.5, 1.0],
    "MAX_HOLD":           [50, 100, 200, 500, 999],
    "STRUCTURE_EXIT":     [True, False],
    "K_EXHAUSTION_EXIT":  [True, False],
    "GAIN_THRESHOLD_MIN": [0.3, 0.5, 1.0, 1.5],
}
# 8 x 4 x 3 x 5 x 2 x 2 x 4 = 7,680 exit combos

# For Phase 3: we'll cross top-N from each phase
PHASE3_TOP_N = 30


# ═══════════════════════════════════════════════════════════════
# INDICATOR LOADING (from precomputed .npz cache)
# ═══════════════════════════════════════════════════════════════

def load_indicators(symbol: str, primary_tf: str = "15m") -> Optional[Dict]:
    """Load precomputed indicators from .npz cache.
    Falls back to live computation if cache doesn't exist."""
    cache_path = CACHE_DIR / f"{symbol}_{primary_tf}.npz"
    if cache_path.exists():
        try:
            data = dict(np.load(str(cache_path), allow_pickle=True))
            data["n"] = int(data["_n"][0])
            return data
        except Exception:
            pass
    # Fallback: compute on the fly (slow)
    try:
        from precompute_indicators import precompute_symbol, load_cached
        precompute_symbol(symbol, primary_tf, force=True)
        return load_cached(symbol, primary_tf)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# ENTRY SCORING — replicates rate() logic with configurable thresholds
# ═══════════════════════════════════════════════════════════════

def compute_entry_score(ind: Dict, i: int, is_long: bool, ep: Dict) -> Tuple[int, str]:
    """Compute entry score at bar i with entry params ep.
    Returns (score, reason). Score >= ep['SCORE_MIN'] means entry."""
    score = 0
    reasons = []
    price = ind["close"][i]
    if price <= 0:
        return 0, "NO_PRICE"

    # --- RSI Gate (THE #1 LEVER) ---
    rsi_1h = ind.get("rsi_1h", np.full(ind["n"], 50))[i]
    rsi_15m = ind.get("rsi_15m", np.full(ind["n"], 50))[i]
    rsi_max_long = ep.get("RSI_MAX_LONG", 37)
    rsi_min_short = ep.get("RSI_MIN_SHORT", 100 - rsi_max_long)
    if is_long and rsi_1h > rsi_max_long and rsi_max_long < 99:
        return 0, "RSI_GATE"
    if not is_long and rsi_1h < rsi_min_short and rsi_max_long < 99:
        return 0, "RSI_GATE"

    # --- K3M Cap/Floor ---
    k_3m = ind.get("k_3m", np.full(ind["n"], 50))[i]
    k3m_cap = ep.get("K3M_CAP", 80)
    k3m_floor = 100 - k3m_cap
    if is_long and k_3m > k3m_cap:
        return 0, "K3M_CAP"
    if not is_long and k_3m < k3m_floor:
        return 0, "K3M_FLOOR"

    # --- Multi-TF State (MTS) Gate ---
    mts_bottom = 0
    mts_eq = 0
    for tf in ["15m", "1h", "4h"]:
        k_val = ind.get(f"k_{tf}", np.full(ind["n"], 50))[i]
        wt1 = ind.get(f"wt1_{tf}", np.full(ind["n"], 0))[i]
        wt_bull = ind.get(f"wt_bullish_{tf}", np.full(ind["n"], 0))[i]
        tf_weight = {"15m": 8, "1h": 12, "4h": 4}.get(tf, 1)
        if is_long:
            if k_val < 30:
                mts_bottom += tf_weight * (30 - k_val) / 30
            if wt1 < -30:
                mts_bottom += tf_weight * 0.5
            if wt_bull > 0:
                mts_eq += tf_weight * 0.3
        else:
            if k_val > 70:
                mts_bottom += tf_weight * (k_val - 70) / 30
            if wt1 > 30:
                mts_bottom += tf_weight * 0.5
            if wt_bull < 1:
                mts_eq += tf_weight * 0.3
    mts_bottom_min = ep.get("MTS_BOTTOM_MIN", 10) if is_long else ep.get("MTS_BOTTOM_MIN_SHORT", 5)
    mts_eq_min = ep.get("MTS_ENTRY_QUALITY_MIN", 5) if is_long else ep.get("MTS_EQ_MIN_SHORT", 0)
    if mts_bottom < mts_bottom_min:
        return 0, "MTS_BOTTOM"
    if mts_eq < mts_eq_min:
        return 0, "MTS_EQ"

    # --- TF Alignment ---
    tf_align = 0
    for tf in ["15m", "1h", "4h", "D"]:
        k_val = ind.get(f"k_{tf}", np.full(ind["n"], 50))[i]
        d_val = ind.get(f"d_{tf}", np.full(ind["n"], 50))[i]
        if is_long and k_val > d_val:
            tf_align += 1
        elif not is_long and k_val < d_val:
            tf_align += 1
    if tf_align < ep.get("TF_ALIGN_MIN", 2):
        return 0, "TF_ALIGN"

    # --- WT Cross Signal ---
    wt_cross_required = ep.get("WT_CROSS_REQUIRED", True)
    has_wt_cross = False
    for tf in ["15m", "1h"]:
        if is_long:
            if ind.get(f"wt_cross_bull_{tf}", np.zeros(ind["n"]))[i] > 0:
                has_wt_cross = True; score += 5; reasons.append(f"WT_BULL_{tf}")
        else:
            if ind.get(f"wt_cross_bear_{tf}", np.zeros(ind["n"]))[i] > 0:
                has_wt_cross = True; score += 5; reasons.append(f"WT_BEAR_{tf}")
    if wt_cross_required and not has_wt_cross:
        return 0, "NO_WT_CROSS"

    # --- K Crossover Signal ---
    for tf in ["15m", "1h"]:
        if is_long and ind.get(f"k_cross_up_{tf}", np.zeros(ind["n"]))[i] > 0:
            score += 4; reasons.append(f"K_UP_{tf}")
        elif not is_long and ind.get(f"k_cross_dn_{tf}", np.zeros(ind["n"]))[i] > 0:
            score += 4; reasons.append(f"K_DN_{tf}")

    # --- EMA Distance (mean reversion) ---
    ema_dist = ind.get("ema_dist_15m", np.zeros(ind["n"]))[i]
    if is_long and ema_dist < ep.get("EMA_DIST_LONG", -1.0):
        score += 4; reasons.append("EMA_DIST")
    elif not is_long and ema_dist > ep.get("EMA_DIST_SHORT", 1.0):
        score += 4; reasons.append("EMA_DIST")

    # --- Heikin-Ashi Confirmation ---
    ha = ind.get("ha_green_15m", np.full(ind["n"], 0.5))[i]
    ha_prev = ind.get("ha_green_15m_prev", np.full(ind["n"], 0.5))[i]
    if is_long and ha > 0.5 and ha_prev < 0.5:
        score += 3; reasons.append("HA_FLIP_GREEN")
    elif not is_long and ha < 0.5 and ha_prev > 0.5:
        score += 3; reasons.append("HA_FLIP_RED")

    # --- DC Position ---
    dc_pos = ind.get("dc_pos_15m", np.full(ind["n"], 0.5))[i]
    if is_long and dc_pos < 0.2:
        score += 3; reasons.append("DC_LOW")
    elif not is_long and dc_pos > 0.8:
        score += 3; reasons.append("DC_HIGH")

    # --- Momentum (3-bar) ---
    mom3 = ind.get("mom3_15m", np.zeros(ind["n"]))[i]
    if is_long and mom3 < -1.0:
        score += 2; reasons.append("MOM3_DOWN")
    elif not is_long and mom3 > 1.0:
        score += 2; reasons.append("MOM3_UP")

    # --- BB %B ---
    bb_pctb = ind.get("bb_pctb_15m", np.full(ind["n"], 0.5))[i]
    if is_long and bb_pctb < -0.2:
        score += 2; reasons.append("BB_OVERSOLD")
    elif not is_long and bb_pctb > 1.0:
        score += 2; reasons.append("BB_OVERBOUGHT")

    # --- Volume Confirmation ---
    rvol = ind.get("rvol_15m", np.ones(ind["n"]))[i]
    if rvol >= ep.get("VOL_MIN_RATIO", 1.3):
        score += 2; reasons.append("VOL")

    # --- HTF Trend (1h HA + 4h HA) ---
    if ep.get("HTF_STRICT", True):
        ha_1h = ind.get("ha_green_1h", np.full(ind["n"], 0.5))[i]
        ha_4h = ind.get("ha_green_4h", np.full(ind["n"], 0.5))[i]
        if is_long:
            if ha_1h > 0.5:
                score += 2
            if ha_4h > 0.5:
                score += 1
        else:
            if ha_1h < 0.5:
                score += 2
            if ha_4h < 0.5:
                score += 1

    # --- SMA200 Distance Bonus ---
    sma200_d = ind.get("sma200_dist_1h", np.zeros(ind["n"]))[i]
    if is_long and sma200_d < -3.0:
        score += 2; reasons.append("SMA200_FAR")
    elif not is_long and sma200_d > 3.0:
        score += 2; reasons.append("SMA200_FAR")

    # --- MTS Score Bonus ---
    if mts_bottom > 25:
        score += 4
    elif mts_bottom > 15:
        score += 2
    if mts_eq > 10:
        score += 2

    return score, "+".join(reasons[:3]) if reasons else "SCORE"


# ═══════════════════════════════════════════════════════════════
# SIMULATION ENGINE — replay bars with configurable entry/exit
# ═══════════════════════════════════════════════════════════════

def simulate(ind: Dict, entry_params: Dict, exit_params: Dict) -> Optional[Dict]:
    """Simulate trading on precomputed indicators with given params.
    Returns metrics dict or None if insufficient trades."""
    n = ind["n"]
    close = ind["close"]
    high = ind["high"]
    low = ind["low"]
    opn = ind["open"]

    tp_pct = exit_params.get("TP_PCT", 0.5)
    trail_pct = exit_params.get("TRAIL_PCT", 0.5)
    trail_activate = exit_params.get("TRAIL_ACTIVATE", 0.5)
    max_hold = exit_params.get("MAX_HOLD", 200)
    structure_exit = exit_params.get("STRUCTURE_EXIT", True)
    wt_exit = exit_params.get("WT_EXIT", True)
    k_exhaust_exit = exit_params.get("K_EXHAUSTION_EXIT", True)
    k_exhaust_level = exit_params.get("K_EXHAUST_LEVEL", 90)
    tiered_tp = exit_params.get("TIERED_TP", False)
    tiered_levels = exit_params.get("TIERED_LEVELS", [0.15, 0.30, 0.50, 0.70, 1.0, 1.5, 2.0, 3.0])
    tiered_frac = exit_params.get("TIERED_FRAC", 0.25)
    noloss_min = exit_params.get("NOLOSS_MIN", 0.0)
    gain_thresh_min = exit_params.get("GAIN_THRESHOLD_MIN", 1.0)
    score_min = entry_params.get("SCORE_MIN", 18)

    # Pre-extract arrays for fast exit checks
    k_15m = ind.get("k_15m", np.full(n, 50.0))
    d_15m = ind.get("d_15m", np.full(n, 50.0))
    wt_cross_bull_15m = ind.get("wt_cross_bull_15m", np.zeros(n))
    wt_cross_bear_15m = ind.get("wt_cross_bear_15m", np.zeros(n))

    trades = []
    position = None  # {side, entry_price, entry_bar, max_gain, remaining_frac, next_tier}
    cooldown = 0

    for i in range(WARMUP, n - 1):
        price = close[i]
        if price <= 0:
            continue
        if cooldown > 0:
            cooldown -= 1

        # --- EXIT CHECK ---
        if position is not None:
            is_long = position["side"] == "LONG"
            ep = position["entry_price"]
            bars_held = i - position["entry_bar"]
            if is_long:
                pnl_pct = (price - ep) / ep * 100
                high_pnl = (high[i] - ep) / ep * 100
            else:
                pnl_pct = (ep - price) / ep * 100
                high_pnl = (ep - low[i]) / ep * 100
            position["max_gain"] = max(position["max_gain"], high_pnl)
            mg = position["max_gain"]
            exit_signal = False
            reason = ""

            # TIERED TP
            if tiered_tp and pnl_pct > 0:
                next_tier = position.get("next_tier", 0)
                if next_tier < len(tiered_levels):
                    tier_target = tiered_levels[next_tier] * 100  # Convert to %
                    if pnl_pct >= tier_target:
                        frac = position.get("remaining_frac", 1.0)
                        closed_frac = frac * tiered_frac
                        position["remaining_frac"] = frac - closed_frac
                        position["next_tier"] = next_tier + 1
                        if position["remaining_frac"] < 0.1:
                            exit_signal = True; reason = f"TIERED_TP_{next_tier}"

            # SIMPLE TP
            if not tiered_tp and high_pnl >= tp_pct:
                exit_signal = True; reason = f"TP_{tp_pct}%"

            # TRAIL STOP
            if trail_pct > 0 and mg >= trail_activate and pnl_pct < mg * trail_pct:
                exit_signal = True; reason = f"TRAIL_{trail_pct*100:.0f}%"

            # MAX HOLD
            if bars_held >= max_hold:
                exit_signal = True; reason = f"MAX_HOLD_{max_hold}"

            # STRUCTURE BREAK (K crossunder on 15m for LONG, crossover for SHORT)
            if structure_exit and bars_held >= 3 and pnl_pct >= gain_thresh_min:
                k_val = k_15m[i]; d_val = d_15m[i]
                if i > 0:
                    k_prev = k_15m[i - 1]; d_prev = d_15m[i - 1]
                    was_fav = (k_prev > d_prev) if is_long else (k_prev < d_prev)
                    now_against = (k_val < d_val) if is_long else (k_val > d_val)
                    if was_fav and now_against:
                        exit_signal = True; reason = "STRUCT_BREAK"

            # WT CROSS AGAINST
            if wt_exit and bars_held >= 3 and pnl_pct >= gain_thresh_min:
                if is_long and wt_cross_bear_15m[i] > 0:
                    exit_signal = True; reason = "WT_AGAINST"
                elif not is_long and wt_cross_bull_15m[i] > 0:
                    exit_signal = True; reason = "WT_AGAINST"

            # K EXHAUSTION
            if k_exhaust_exit and pnl_pct >= 0.3:
                k_val = k_15m[i]
                if is_long and k_val > k_exhaust_level:
                    # Check if falling
                    if i > 0 and k_15m[i] < k_15m[i - 1]:
                        exit_signal = True; reason = f"K_EXHAUST_{k_exhaust_level}"
                elif not is_long and k_val < (100 - k_exhaust_level):
                    if i > 0 and k_15m[i] > k_15m[i - 1]:
                        exit_signal = True; reason = f"K_EXHAUST_{100-k_exhaust_level}"

            # STRICT NO LOSS: never exit at a loss
            if exit_signal and pnl_pct < noloss_min:
                if bars_held < max_hold:  # Only honor no-loss if not max hold
                    exit_signal = False

            if exit_signal:
                frac = position.get("remaining_frac", 1.0)
                ret = (pnl_pct / 100.0 - 2 * FEE) * frac
                trades.append({"ret": ret, "side": position["side"], "bars": bars_held, "reason": reason, "pnl_pct": pnl_pct})
                position = None
                cooldown = 2  # Don't re-enter immediately
            continue

        # --- ENTRY CHECK ---
        if cooldown > 0:
            continue

        # Fast filter: only check when there's a signal change
        has_signal = False
        if i < n:
            if wt_cross_bull_15m[i] > 0 or wt_cross_bear_15m[i] > 0:
                has_signal = True
            elif ind.get("wt_cross_bull_1h", np.zeros(n))[i] > 0 or ind.get("wt_cross_bear_1h", np.zeros(n))[i] > 0:
                has_signal = True
            elif ind.get("k_cross_up_15m", np.zeros(n))[i] > 0 or ind.get("k_cross_dn_15m", np.zeros(n))[i] > 0:
                has_signal = True
            elif i % 4 == 0:  # Every 4th bar = 1h boundary
                has_signal = True
        if not has_signal:
            continue

        best_score = 0
        best_side = None
        best_reason = ""
        for side_long in [True, False]:
            sc, reason = compute_entry_score(ind, i, side_long, entry_params)
            if sc >= score_min and sc > best_score:
                best_score = sc
                best_side = "LONG" if side_long else "SHORT"
                best_reason = reason

        if best_side is not None and i + 1 < n:
            next_open = opn[i + 1]
            if next_open > 0:
                position = {"side": best_side, "entry_price": next_open, "entry_bar": i + 1, "max_gain": 0.0, "remaining_frac": 1.0, "next_tier": 0}

    # Close remaining position at last bar
    if position is not None:
        ep_val = position["entry_price"]
        last_price = close[-1]
        is_long = position["side"] == "LONG"
        pnl_pct = (last_price - ep_val) / ep_val * 100 if is_long else (ep_val - last_price) / ep_val * 100
        frac = position.get("remaining_frac", 1.0)
        ret = (pnl_pct / 100.0 - 2 * FEE) * frac
        trades.append({"ret": ret, "side": position["side"], "bars": ind["n"] - position["entry_bar"], "reason": "END", "pnl_pct": pnl_pct})

    if len(trades) < MIN_TRADES:
        return None

    rets = np.array([t["ret"] for t in trades])
    wr = np.mean(rets > 0) * 100
    sharpe = np.mean(rets) / np.std(rets) * math.sqrt(len(rets)) if np.std(rets) > 1e-10 else 0.0
    total_pnl = np.sum(rets) * 100
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    pf = abs(wins.sum() / losses.sum()) if len(losses) > 0 and abs(losses.sum()) > 1e-10 else 99.0
    avg_hold = np.mean([t["bars"] for t in trades])
    long_trades = sum(1 for t in trades if t["side"] == "LONG")
    short_trades = sum(1 for t in trades if t["side"] == "SHORT")

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        r = t["reason"]
        if r not in exit_reasons:
            exit_reasons[r] = {"count": 0, "avg_ret": []}
        exit_reasons[r]["count"] += 1
        exit_reasons[r]["avg_ret"].append(t["ret"])
    for r in exit_reasons:
        exit_reasons[r]["avg_ret"] = round(np.mean(exit_reasons[r]["avg_ret"]) * 100, 3)

    return {
        "trades": len(trades), "sharpe": round(sharpe, 3), "wr": round(wr, 1),
        "pnl": round(total_pnl, 2), "pf": round(pf, 2), "avg_hold": round(avg_hold, 1),
        "long_trades": long_trades, "short_trades": short_trades,
        "exit_reasons": exit_reasons,
    }


# ═══════════════════════════════════════════════════════════════
# GRID GENERATION
# ═══════════════════════════════════════════════════════════════

def make_entry_combos() -> List[Tuple[str, Dict]]:
    """Generate all entry parameter combos. Returns [(name, params), ...]."""
    combos = []
    keys = list(ENTRY_GRID.keys())
    vals = list(ENTRY_GRID.values())
    for combo_vals in itertools.product(*vals):
        params = dict(zip(keys, combo_vals))
        # Mirror RSI
        params["RSI_MIN_SHORT"] = 100 - params["RSI_MAX_LONG"] if params["RSI_MAX_LONG"] < 99 else 1
        params["MTS_BOTTOM_MIN_SHORT"] = max(0, params["MTS_BOTTOM_MIN"] - 5)
        params["MTS_EQ_MIN_SHORT"] = 0
        name = f"RSI{params['RSI_MAX_LONG']}_MTS{params['MTS_BOTTOM_MIN']}_EQ{params['MTS_ENTRY_QUALITY_MIN']}_K{params['K3M_CAP']}_SC{params['SCORE_MIN']}_WT{'Y' if params['WT_CROSS_REQUIRED'] else 'N'}"
        # Merge with defaults
        full = {**DEFAULT_ENTRY, **params}
        combos.append((name, full))
    return combos


def make_exit_combos() -> List[Tuple[str, Dict]]:
    """Generate all exit parameter combos."""
    combos = []
    keys = list(EXIT_GRID.keys())
    vals = list(EXIT_GRID.values())
    for combo_vals in itertools.product(*vals):
        params = dict(zip(keys, combo_vals))
        # Skip invalid: trail requires activation below TP
        if params["TRAIL_PCT"] > 0 and params["TRAIL_ACTIVATE"] >= params["TP_PCT"]:
            continue
        # Skip: gain threshold must be <= TP
        if params["GAIN_THRESHOLD_MIN"] > params["TP_PCT"]:
            continue
        name = f"TP{params['TP_PCT']}_TR{params['TRAIL_PCT']}_TA{params['TRAIL_ACTIVATE']}_MH{params['MAX_HOLD']}_SE{'Y' if params['STRUCTURE_EXIT'] else 'N'}_KE{'Y' if params['K_EXHAUSTION_EXIT'] else 'N'}_GT{params['GAIN_THRESHOLD_MIN']}"
        full = {**DEFAULT_EXIT, **params}
        combos.append((name, full))
    return combos


# ═══════════════════════════════════════════════════════════════
# WORKER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def worker_entry(args):
    """Worker for Phase 1: sweep entry combos for one symbol."""
    sym, entry_combos = args
    try:
        ind = load_indicators(sym)
        if ind is None:
            return sym, {}
        results = {}
        for name, ep in entry_combos:
            r = simulate(ind, ep, DEFAULT_EXIT)
            if r is not None:
                results[name] = r
        return sym, results
    except Exception as e:
        print(f"  ERROR {sym}: {e}")
        return sym, {}


def worker_exit(args):
    """Worker for Phase 2: sweep exit combos for one symbol."""
    sym, exit_combos = args
    try:
        ind = load_indicators(sym)
        if ind is None:
            return sym, {}
        results = {}
        for name, xp in exit_combos:
            r = simulate(ind, DEFAULT_ENTRY, xp)
            if r is not None:
                results[name] = r
        return sym, results
    except Exception as e:
        print(f"  ERROR {sym}: {e}")
        return sym, {}


def worker_combined(args):
    """Worker for Phase 3: sweep combined entry+exit combos for one symbol."""
    sym, combos = args
    try:
        ind = load_indicators(sym)
        if ind is None:
            return sym, {}
        results = {}
        for name, ep, xp in combos:
            r = simulate(ind, ep, xp)
            if r is not None:
                results[name] = r
        return sym, results
    except Exception as e:
        print(f"  ERROR {sym}: {e}")
        return sym, {}


# ═══════════════════════════════════════════════════════════════
# AGGREGATION + RANKING
# ═══════════════════════════════════════════════════════════════

def aggregate_and_rank(all_results: Dict[str, Dict[str, Dict]], min_symbols: int = 5) -> List[Dict]:
    """Aggregate results across symbols and rank by avg Sharpe.
    Returns sorted list of {name, avg_sharpe, avg_wr, avg_pnl, avg_pf, n_symbols, avg_trades, avg_hold}."""
    agg = {}
    for sym, results in all_results.items():
        for combo_name, metrics in results.items():
            if combo_name not in agg:
                agg[combo_name] = []
            agg[combo_name].append(metrics)

    ranked = []
    for combo_name, metrics_list in agg.items():
        if len(metrics_list) < min_symbols:
            continue
        sharpes = [m["sharpe"] for m in metrics_list]
        wrs = [m["wr"] for m in metrics_list]
        pnls = [m["pnl"] for m in metrics_list]
        pfs = [m["pf"] for m in metrics_list]
        trades = [m["trades"] for m in metrics_list]
        holds = [m["avg_hold"] for m in metrics_list]
        ranked.append({
            "name": combo_name,
            "avg_sharpe": round(np.mean(sharpes), 3),
            "med_sharpe": round(np.median(sharpes), 3),
            "std_sharpe": round(np.std(sharpes), 3),
            "avg_wr": round(np.mean(wrs), 1),
            "avg_pnl": round(np.mean(pnls), 2),
            "total_pnl": round(np.sum(pnls), 2),
            "avg_pf": round(np.mean(pfs), 2),
            "n_symbols": len(metrics_list),
            "avg_trades": round(np.mean(trades), 1),
            "avg_hold": round(np.mean(holds), 1),
            "pct_positive": round(sum(1 for s in sharpes if s > 0) / len(sharpes) * 100, 1),
        })

    ranked.sort(key=lambda x: x["avg_sharpe"], reverse=True)
    return ranked


def print_top(ranked: List[Dict], label: str, top_n: int = 30):
    print(f"\n{'='*120}")
    print(f"  {label} — Top {min(top_n, len(ranked))} of {len(ranked)} combos")
    print(f"{'='*120}")
    print(f"  {'Rank':>4}  {'Combo Name':<85}  {'Sharpe':>7}  {'Med':>6}  {'WR%':>5}  {'PF':>5}  {'PnL':>8}  {'Sym':>3}  {'Trd':>5}  {'Hold':>5}  {'%+':>4}")
    print(f"  {'─'*4}  {'─'*85}  {'─'*7}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*8}  {'─'*3}  {'─'*5}  {'─'*5}  {'─'*4}")
    for rank, r in enumerate(ranked[:top_n], 1):
        print(f"  {rank:>4}  {r['name']:<85}  {r['avg_sharpe']:>+7.3f}  {r['med_sharpe']:>+6.3f}  {r['avg_wr']:>5.1f}  {r['avg_pf']:>5.2f}  {r['total_pnl']:>+8.1f}  {r['n_symbols']:>3}  {r['avg_trades']:>5.1f}  {r['avg_hold']:>5.1f}  {r['pct_positive']:>4.0f}")
    print(f"\n  Bottom 10:")
    for r in ranked[-10:]:
        print(f"        {r['name']:<85}  {r['avg_sharpe']:>+7.3f}  {r['med_sharpe']:>+6.3f}  {r['avg_wr']:>5.1f}  {r['avg_pf']:>5.2f}  {r['total_pnl']:>+8.1f}  {r['n_symbols']:>3}  {r['avg_trades']:>5.1f}  {r['avg_hold']:>5.1f}  {r['pct_positive']:>4.0f}")


# ═══════════════════════════════════════════════════════════════
# PHASE RUNNERS
# ═══════════════════════════════════════════════════════════════

def get_symbols(limit: Optional[int] = None) -> List[str]:
    syms = json.loads(SYMBOLS_FILE.read_text())
    if limit:
        # Sort by 15m kline file size (proxy for data depth)
        sized = []
        for s in syms:
            p = KLINES_DIR / f"{s}_15m.json"
            if p.exists():
                sized.append((s, p.stat().st_size))
        sized.sort(key=lambda x: x[1], reverse=True)
        syms = [s for s, _ in sized[:limit]]
    return syms


def run_phase(phase: int, syms: List[str], workers: int, batch_size: int = 0) -> List[Dict]:
    """Run a single phase. Returns ranked list."""
    if phase == 1:
        combos = make_entry_combos()
        print(f"\n[PHASE 1] ENTRY GRID — {len(combos)} combos x {len(syms)} symbols = {len(combos)*len(syms):,} tests")
        worker_fn = worker_entry
        tasks = [(sym, combos) for sym in syms]
        label = "PHASE 1: ENTRY PARAMETER SWEEP"
    elif phase == 2:
        combos = make_exit_combos()
        print(f"\n[PHASE 2] EXIT GRID — {len(combos)} combos x {len(syms)} symbols = {len(combos)*len(syms):,} tests")
        worker_fn = worker_exit
        tasks = [(sym, combos) for sym in syms]
        label = "PHASE 2: EXIT PARAMETER SWEEP"
    elif phase == 3:
        # Load Phase 1 + 2 winners
        p1_file = sorted(RESULTS_DIR.glob("phase1_*.json"))
        p2_file = sorted(RESULTS_DIR.glob("phase2_*.json"))
        if not p1_file or not p2_file:
            print("[PHASE 3] ERROR: Need Phase 1 and Phase 2 results first!")
            return []
        p1_ranked = json.loads(p1_file[-1].read_text())["ranked"]
        p2_ranked = json.loads(p2_file[-1].read_text())["ranked"]
        # Get top entry and exit combos
        entry_combos_all = {name: params for name, params in make_entry_combos()}
        exit_combos_all = {name: params for name, params in make_exit_combos()}
        top_entries = [(r["name"], entry_combos_all[r["name"]]) for r in p1_ranked[:PHASE3_TOP_N] if r["name"] in entry_combos_all]
        top_exits = [(r["name"], exit_combos_all[r["name"]]) for r in p2_ranked[:PHASE3_TOP_N] if r["name"] in exit_combos_all]
        # Cross them
        combined = []
        for ename, ep in top_entries:
            for xname, xp in top_exits:
                cname = f"{ename}__X__{xname}"
                combined.append((cname, ep, xp))
        print(f"\n[PHASE 3] COMBINED — {len(top_entries)} entries x {len(top_exits)} exits = {len(combined)} combos x {len(syms)} symbols = {len(combined)*len(syms):,} tests")
        worker_fn = worker_combined
        tasks = [(sym, combined) for sym in syms]
        label = "PHASE 3: COMBINED ENTRY x EXIT SWEEP"
    else:
        return []

    start = time.time()
    all_results = {}
    done = 0

    # Batch processing for large grids
    if batch_size > 0 and phase in (1, 2):
        if phase == 1:
            all_combos = make_entry_combos()
        else:
            all_combos = make_exit_combos()
        n_batches = math.ceil(len(all_combos) / batch_size)
        print(f"  Splitting into {n_batches} batches of {batch_size} combos each")
        for batch_idx in range(n_batches):
            if _SHUTDOWN:
                break
            batch_combos = all_combos[batch_idx * batch_size:(batch_idx + 1) * batch_size]
            print(f"  Batch {batch_idx+1}/{n_batches} ({len(batch_combos)} combos)...")
            if phase == 1:
                batch_tasks = [(sym, batch_combos) for sym in syms]
                batch_worker = worker_entry
            else:
                batch_tasks = [(sym, batch_combos) for sym in syms]
                batch_worker = worker_exit
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(batch_worker, t): t[0] for t in batch_tasks}
                for f in as_completed(futures):
                    if _SHUTDOWN:
                        break
                    sym = futures[f]
                    try:
                        s, results = f.result()
                        if s not in all_results:
                            all_results[s] = {}
                        all_results[s].update(results)
                    except Exception as e:
                        print(f"  Worker error {sym}: {e}")
                    done += 1
            elapsed = time.time() - start
            print(f"  Batch {batch_idx+1} done. Total: {done} symbol-batches in {elapsed:.0f}s")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker_fn, t): t[0] for t in tasks}
            for f in as_completed(futures):
                if _SHUTDOWN:
                    break
                sym = futures[f]
                try:
                    s, results = f.result()
                    all_results[s] = results
                except Exception as e:
                    print(f"  Worker error {sym}: {e}")
                done += 1
                if done % 5 == 0:
                    elapsed = time.time() - start
                    combos_done = sum(len(v) for v in all_results.values())
                    print(f"  {done}/{len(tasks)} symbols done ({elapsed:.0f}s, {combos_done:,} combo-results)")

    elapsed = time.time() - start
    total_combos = sum(len(v) for v in all_results.values())
    print(f"\n  Completed in {elapsed:.0f}s — {len(all_results)} symbols, {total_combos:,} combo-results")

    ranked = aggregate_and_rank(all_results, min_symbols=max(3, len(syms) // 4))
    print_top(ranked, label)

    # Save results
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report = {
        "phase": phase, "timestamp": ts, "symbols": list(all_results.keys()),
        "n_combos": len(ranked), "elapsed_seconds": round(elapsed, 1),
        "ranked": ranked[:200],  # Save top 200
    }
    path = RESULTS_DIR / f"phase{phase}_{ts}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"\n  Results saved: {path}")
    return ranked


# ═══════════════════════════════════════════════════════════════
# PARAMETER ANALYSIS — what matters most
# ═══════════════════════════════════════════════════════════════

def analyze_parameters(ranked: List[Dict], phase: int):
    """Analyze which parameters have the most impact on Sharpe."""
    if not ranked:
        return
    print(f"\n{'='*80}")
    print(f"  PARAMETER IMPACT ANALYSIS (Phase {phase})")
    print(f"{'='*80}")

    if phase == 1:
        grid = ENTRY_GRID
    elif phase == 2:
        grid = EXIT_GRID
    else:
        return

    for param, values in grid.items():
        print(f"\n  {param}:")
        for val in values:
            # Find combos matching this value
            matching = []
            for r in ranked:
                name = r["name"]
                # Parse param value from combo name
                if param == "RSI_MAX_LONG" and f"RSI{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "MTS_BOTTOM_MIN" and f"_MTS{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "MTS_ENTRY_QUALITY_MIN" and f"_EQ{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "K3M_CAP" and f"_K{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "SCORE_MIN" and f"_SC{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "WT_CROSS_REQUIRED":
                    tag = "_WTY" if val else "_WTN"
                    if name.endswith(tag):
                        matching.append(r["avg_sharpe"])
                elif param == "TP_PCT" and f"TP{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "TRAIL_PCT" and f"_TR{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "TRAIL_ACTIVATE" and f"_TA{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "MAX_HOLD" and f"_MH{val}_" in name:
                    matching.append(r["avg_sharpe"])
                elif param == "STRUCTURE_EXIT":
                    tag = "_SEY_" if val else "_SEN_"
                    if tag in name:
                        matching.append(r["avg_sharpe"])
                elif param == "K_EXHAUSTION_EXIT":
                    tag = "_KEY_" if val else "_KEN_"
                    if tag in name:
                        matching.append(r["avg_sharpe"])
                elif param == "GAIN_THRESHOLD_MIN" and f"_GT{val}" in name:
                    matching.append(r["avg_sharpe"])
            if matching:
                avg = np.mean(matching)
                med = np.median(matching)
                best = max(matching)
                print(f"    {str(val):>8s}: avg={avg:+.3f}  med={med:+.3f}  best={best:+.3f}  (n={len(matching)})")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Grid Sweep Backtest — comprehensive entry+exit optimization")
    parser.add_argument("--phase", type=str, default="all", help="Phase to run: 1, 2, 3, or all")
    parser.add_argument("--workers", type=int, default=16, help="Parallel workers")
    parser.add_argument("--limit", type=int, default=48, help="Number of symbols")
    parser.add_argument("--batch", type=int, default=500, help="Batch size for large grids (0=no batching)")
    args = parser.parse_args()

    syms = get_symbols(args.limit)
    print(f"Grid Sweep Backtest — {len(syms)} symbols, {args.workers} workers")
    print(f"Symbols: {', '.join(syms[:10])}... ({len(syms)} total)")
    print(f"Klines dir: {KLINES_DIR}")

    entry_combos = make_entry_combos()
    exit_combos = make_exit_combos()
    print(f"Entry combos: {len(entry_combos)}")
    print(f"Exit combos: {len(exit_combos)}")

    phases = [1, 2, 3] if args.phase == "all" else [int(args.phase)]

    for phase in phases:
        if _SHUTDOWN:
            break
        ranked = run_phase(phase, syms, args.workers, batch_size=args.batch)
        if ranked:
            analyze_parameters(ranked, phase)

    print(f"\n{'='*80}")
    print(f"  ALL PHASES COMPLETE")
    print(f"  Results in: {RESULTS_DIR}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
