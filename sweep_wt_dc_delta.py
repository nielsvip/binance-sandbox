#!/usr/bin/env python3
"""
Sweep wt_dc_delta.py parameters on NPZ data.
Tests entry AND exit rules across parameter grid.
Runs on server NPZ files (crypto on S2, stocks on S1/S2).

Usage:
    python3 sweep_wt_dc_delta.py --crypto          # 48 symbols, 4yr, S2 NPZ
    python3 sweep_wt_dc_delta.py --stocks           # 121 symbols, 2yr, S1/S2 NPZ
    python3 sweep_wt_dc_delta.py --report           # Show results
"""
import argparse
import json
import logging
import os
import sys
import time
from itertools import product
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_dc_delta import compute_npz_signals, DEFAULT_CFG

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SWEEP] %(message)s")
logger = logging.getLogger("sweep")

BASE_PATH = Path(__file__).resolve().parent
RESULTS_DIR = BASE_PATH / "data" / "delta_sweep"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Detect environment
IS_SERVER = not (BASE_PATH / "tradeable_keys.json").exists() or os.path.exists("/home/niels/binance-sandbox")
if IS_SERVER:
    CRYPTO_NPZ = Path("/home/niels/binance-sandbox/backtest_v4/indicators")
    STOCK_NPZ = Path("/home/niels/binance-sandbox/backtest_v4_tradier/indicators")
else:
    CRYPTO_NPZ = BASE_PATH / "backtest_v4" / "indicators"
    STOCK_NPZ = BASE_PATH / "backtest_v4_tradier" / "indicators"

# ═══════════════════════════════════════════════════════════════════════
# PARAMETER GRID
# ═══════════════════════════════════════════════════════════════════════

PARAM_GRID = {
    # TF weights: which TFs matter most for delta speed
    "tf_weights": [
        {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},   # default: 3m dominant
        {"3m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},   # 1h dominant
        {"3m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},   # 15m dominant
        {"3m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0, "D": 1.0},   # equal
    ],
    # Entry: how many TFs must agree
    "entry_min_tf": [2, 3, 4],
    # Entry: z-score threshold for "significant speed"
    "entry_z_threshold": [1.0, 1.5, 2.0, 2.5],
    # Entry: acceleration threshold
    "entry_accel_threshold": [0.0, 0.3, 0.5, 1.0],
    # Speed smoothing
    "speed_smooth": [1, 3, 5],
    # TF z-score threshold for counting "active" TFs
    "tf_z_threshold": [0.5, 1.0, 1.5],
}

# FOCUSED GRID: realistic frequency + HTF alignment
# Crypto target: 1-2 entries/day/symbol → needs 4h gate + cooldown
# Stocks target: 2/week/symbol → needs 4h+D gate + long cooldown
CRYPTO_FOCUSED_GRID = {
    "tf_weights": [
        {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
        {"3m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
    ],
    "entry_min_tf": [3, 4],
    "entry_z_threshold": [1.5, 2.0, 2.5, 3.0],
    "entry_accel_threshold": [0.3, 0.5],
    "speed_smooth": [3, 5],
    "tf_z_threshold": [1.0, 1.5],
    "htf_gate": ["none", "4h", "4h_D"],
    "cooldown_bars": [0, 20, 60],  # 0, 1h, 3h at 3m resolution
}

STOCK_FOCUSED_GRID = {
    "tf_weights": [
        {"5m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
        {"5m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},
    ],
    "entry_min_tf": [3, 4],
    "entry_z_threshold": [2.0, 2.5, 3.0, 4.0],
    "entry_accel_threshold": [0.3, 0.5],
    "speed_smooth": [3, 5],
    "tf_z_threshold": [1.0, 1.5],
    "htf_gate": ["4h", "4h_D", "4h_D_strict"],
    "cooldown_bars": [60, 120, 240],  # 5h, 10h, 20h at 5m resolution
}

# LOCAL MEGA GRIDS: denser search, fast on small symbol sets
# ~5,184 configs × 2.5s = ~3.6h on 4 crypto symbols
LOCAL_CRYPTO_MEGA = {
    "tf_weights": [
        {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
        {"3m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
        {"3m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},
    ],
    "entry_min_tf": [2, 3],
    "entry_z_threshold": [1.5, 2.0, 2.5, 3.0],
    "entry_accel_threshold": [0.0, 0.3, 0.5],
    "speed_smooth": [3, 5],
    "tf_z_threshold": [0.5, 1.0, 1.5],
    "htf_gate": ["none", "4h", "4h_D"],
    "cooldown_bars": [0, 10, 20, 60],
}

# ~4,608 configs × 3.3s = ~4.2h on 24 stock symbols
LOCAL_STOCK_MEGA = {
    "tf_weights": [
        {"5m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
        {"5m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},
    ],
    "entry_min_tf": [2, 3],
    "entry_z_threshold": [1.5, 2.0, 2.5, 3.0],
    "entry_accel_threshold": [0.0, 0.3, 0.5],
    "speed_smooth": [3, 5],
    "tf_z_threshold": [0.5, 1.0, 1.5],
    "htf_gate": ["none", "4h", "4h_D", "4h_D_strict"],
    "cooldown_bars": [0, 60, 120, 240],
}

# Keep old grids for reference
PARAM_GRID_ORIGINAL = dict(PARAM_GRID)

# Stock-specific: use 5m instead of 3m (original broad sweep)
STOCK_PARAM_GRID = {
    **PARAM_GRID,
    "tf_weights": [
        {"5m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
        {"5m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
        {"5m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},
        {"5m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0, "D": 1.0},
    ],
}


def build_configs(grid):
    """Expand grid into list of config dicts."""
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    configs = []
    for combo in product(*values):
        cfg = {**DEFAULT_CFG}
        for k, v in zip(keys, combo):
            cfg[k] = v
        configs.append(cfg)
    return configs


# ═══════════════════════════════════════════════════════════════════════
# V2: REAL POSITION SIMULATION WITH EXIT LOGIC + ATR NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════

EXIT_TYPES = {
    "wt_cross": "WT cross against position on exit_tf",
    "speed_decay": "Delta speed drops to exit_speed_pct of peak",
    "dc_reversal": "DC position crosses 0.5 against direction on exit_tf",
    "stoch_cross": "Stoch cross against on exit_tf",
    "combined_wt_speed": "WT cross OR speed decay (whichever first)",
    "combined_wt_stoch": "WT cross AND stoch cross confirm",
    "giveback": "Gave back giveback_pct of max unrealized gain",
    "atr_trail": "Price trails by atr_trail_mult * ATR from peak",
}

# V2: ENTRY grid (small) × EXIT grid (smart — only relevant params per exit type)
# ═══════════════════════════════════════════════════════════════════════
# 6 STRATEGY VARIANTS: ST + LT for crypto, stocks, options
# ═══════════════════════════════════════════════════════════════════════

# Entry grids: SMALL — V2 is about EXIT testing, not re-sweeping entries
# Update these with mega sweep winners

# ── CRYPTO ST: scalp 9-90min, 3m dominant ──
V2_CRYPTO_ST_ENTRY = {
    "tf_weights": [
        {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
        {"3m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
    ],
    "entry_min_tf": [2],
    "entry_z_threshold": [1.5],
    "entry_accel_threshold": [0.0],
    "speed_smooth": [5],
    "tf_z_threshold": [1.5],
    "htf_gate": ["4h", "4h_D"],
    "cooldown_bars": [10, 20],
    "atr_entry_filter": [0, 1],
}

# ── CRYPTO LT: swing 3h-24h, 1h dominant ──
V2_CRYPTO_LT_ENTRY = {
    "tf_weights": [
        {"3m": 1.0, "15m": 1.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
        {"3m": 0.5, "15m": 1.0, "1h": 2.0, "4h": 3.0, "D": 2.0},
    ],
    "entry_min_tf": [2],
    "entry_z_threshold": [1.5],
    "entry_accel_threshold": [0.0],
    "speed_smooth": [5],
    "tf_z_threshold": [1.5],
    "htf_gate": ["4h_D"],
    "cooldown_bars": [60, 120],
    "atr_entry_filter": [0, 1],
}

# ── STOCKS ST: day trade 1-5h, 5m dominant ──
V2_STOCK_ST_ENTRY = {
    "tf_weights": [
        {"5m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},
        {"5m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
    ],
    "entry_min_tf": [3],
    "entry_z_threshold": [2.5],
    "entry_accel_threshold": [0.3],
    "speed_smooth": [5],
    "tf_z_threshold": [1.5],
    "htf_gate": ["4h"],
    "cooldown_bars": [20, 60],
    "atr_entry_filter": [0, 1],
}

# ── STOCKS LT: swing 1-5 days, 1h dominant ──
V2_STOCK_LT_ENTRY = {
    "tf_weights": [
        {"5m": 0.5, "15m": 1.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
        {"5m": 0.5, "15m": 1.0, "1h": 2.0, "4h": 3.0, "D": 2.0},
    ],
    "entry_min_tf": [2],
    "entry_z_threshold": [1.5, 2.0],
    "entry_accel_threshold": [0.0],
    "speed_smooth": [5],
    "tf_z_threshold": [1.5],
    "htf_gate": ["4h_D"],
    "cooldown_bars": [120, 240],
    "atr_entry_filter": [0, 1],
}

# ── OPTIONS ST: 1-3 day swing, bigger moves for spreads ──
V2_OPTIONS_ST_ENTRY = {
    "tf_weights": [
        {"5m": 0.5, "15m": 1.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
    ],
    "entry_min_tf": [3],
    "entry_z_threshold": [2.5, 3.0],
    "entry_accel_threshold": [0.3],
    "speed_smooth": [5],
    "tf_z_threshold": [1.5],
    "htf_gate": ["4h_D"],
    "cooldown_bars": [60, 120],
    "atr_entry_filter": [1],
}

# ── OPTIONS LT: 1-2 week position, D reversal exit ──
V2_OPTIONS_LT_ENTRY = {
    "tf_weights": [
        {"5m": 0.0, "15m": 0.5, "1h": 1.0, "4h": 3.0, "D": 2.0},
        {"5m": 0.0, "15m": 0.5, "1h": 2.0, "4h": 2.0, "D": 3.0},
    ],
    "entry_min_tf": [2],
    "entry_z_threshold": [1.5, 2.0],
    "entry_accel_threshold": [0.0],
    "speed_smooth": [5],
    "tf_z_threshold": [1.5],
    "htf_gate": ["4h_D", "4h_D_strict"],
    "cooldown_bars": [240, 480],
    "atr_entry_filter": [1],
}

# Exit configs per strategy type — only expand relevant params per exit_type

def _build_exit_configs(tfs, max_holds, speed_pcts=None, giveback_pcts=None, atr_mults=None):
    """Build exit configs where only relevant params vary per exit type."""
    if speed_pcts is None:
        speed_pcts = [20, 30, 50, 70]
    if giveback_pcts is None:
        giveback_pcts = [30, 40, 50, 60, 75]
    if atr_mults is None:
        atr_mults = [1.0, 1.5, 2.0, 2.5, 3.0]
    base = {"exit_speed_pct": 50, "giveback_pct": 50, "atr_trail_mult": 2.0}
    exits = []
    for etf in tfs:
        for mh in max_holds:
            b = {**base, "exit_tf": etf, "max_hold_bars": mh}
            exits.append({**b, "exit_type": "wt_cross"})
            exits.append({**b, "exit_type": "dc_reversal"})
            exits.append({**b, "exit_type": "stoch_cross"})
            exits.append({**b, "exit_type": "combined_wt_stoch"})
            for sp in speed_pcts:
                exits.append({**b, "exit_type": "speed_decay", "exit_speed_pct": sp})
            for sp in [30, 50]:
                exits.append({**b, "exit_type": "combined_wt_speed", "exit_speed_pct": sp})
            for gb in giveback_pcts:
                exits.append({**b, "exit_type": "giveback", "giveback_pct": gb})
            for at in atr_mults:
                exits.append({**b, "exit_type": "atr_trail", "atr_trail_mult": at})
    return exits

# ST: fast exits on low TFs, short max hold
EXITS_CRYPTO_ST = _build_exit_configs(["3m", "15m"], [30, 60])
EXITS_CRYPTO_LT = _build_exit_configs(["1h", "4h"], [180, 480], speed_pcts=[30, 50, 70], giveback_pcts=[40, 60, 75], atr_mults=[1.5, 2.5, 3.5])
EXITS_STOCK_ST = _build_exit_configs(["5m", "15m"], [30, 60])
EXITS_STOCK_LT = _build_exit_configs(["1h", "4h"], [240, 480], speed_pcts=[30, 50, 70], giveback_pcts=[40, 60, 75], atr_mults=[1.5, 2.5, 3.5])
EXITS_OPTIONS_ST = _build_exit_configs(["1h", "4h"], [120, 240], speed_pcts=[30, 50], giveback_pcts=[30, 50, 70], atr_mults=[2.0, 3.0, 4.0])
EXITS_OPTIONS_LT = _build_exit_configs(["4h", "D"], [480, 960], speed_pcts=[50, 70], giveback_pcts=[50, 60, 75], atr_mults=[2.5, 3.5, 5.0])

# Keep original combined lists for backward compat
V2_EXIT_CONFIGS_CRYPTO = EXITS_CRYPTO_ST
V2_EXIT_CONFIGS_STOCKS = EXITS_STOCK_ST


def build_v2_configs(entry_grid, exit_configs):
    """Cross entry grid × smart exit configs."""
    entry_combos = build_configs(entry_grid)
    configs = []
    for entry in entry_combos:
        for exit_cfg in exit_configs:
            cfg = {**DEFAULT_CFG, **entry, **exit_cfg}
            configs.append(cfg)
    return configs


def backtest_symbol_v2(npz_path, cfg):
    """V2: Simulate actual positions with entry+exit logic, ATR normalization."""
    try:
        d = dict(np.load(npz_path, allow_pickle=True))
    except Exception:
        return None
    if "close" not in d or "timestamps" not in d:
        return None
    close = d["close"].astype(np.float64)
    n = len(close)
    if n < 500:
        return None
    # Detect base TF from NPZ (stocks have 5m, crypto has 3m)
    has_5m = "close_5m" in d
    base_tf = "5m" if has_5m else "3m"
    exit_tf = cfg.get("exit_tf", base_tf)
    # Get ATR for normalization (use 1h as reference)
    atr_1h = d.get("atr_1h", np.ones(n)).astype(np.float64)
    atr_1h = np.where(atr_1h > 0, atr_1h, np.nanmedian(atr_1h[atr_1h > 0]) if np.any(atr_1h > 0) else 1.0)
    atr_ref = d.get(f"atr_{exit_tf}", atr_1h).astype(np.float64)
    atr_ref = np.where(atr_ref > 0, atr_ref, np.nanmedian(atr_ref[atr_ref > 0]) if np.any(atr_ref > 0) else 1.0)
    # Compute entry signals
    try:
        signals = compute_npz_signals(d, cfg)
    except Exception:
        return None
    entry_long = signals["entry_long"].copy()
    entry_short = signals["entry_short"].copy()
    # ── HTF GATE (same as v1) ──
    htf_gate = cfg.get("htf_gate", "none")
    if htf_gate != "none":
        wt1_4h = d.get("wt1_4h", np.zeros(n)).astype(np.float64)
        wt2_4h = d.get("wt2_4h", np.zeros(n)).astype(np.float64)
        wt1_D = d.get("wt1_D", np.zeros(n)).astype(np.float64)
        wt2_D = d.get("wt2_D", np.zeros(n)).astype(np.float64)
        ha_4h = d.get("ha_4h", np.zeros(n))
        ha_D = d.get("ha_D", np.zeros(n))
        k_4h = d.get("stoch_k_4h", np.full(n, 50.0)).astype(np.float64)
        k_D = d.get("stoch_k_D", np.full(n, 50.0)).astype(np.float64)
        if htf_gate == "4h":
            entry_long &= (wt1_4h > wt2_4h) | (ha_4h == 1)
            entry_short &= (wt1_4h < wt2_4h) | (ha_4h == -1)
        elif htf_gate == "4h_D":
            entry_long &= ((wt1_4h > wt2_4h) | (ha_4h == 1)) & ((wt1_D > wt2_D) | (ha_D == 1))
            entry_short &= ((wt1_4h < wt2_4h) | (ha_4h == -1)) & ((wt1_D < wt2_D) | (ha_D == -1))
        elif htf_gate == "4h_D_strict":
            entry_long &= (wt1_4h > wt2_4h) & (wt1_D > wt2_D) & (k_4h < 80) & (k_D < 80)
            entry_short &= (wt1_4h < wt2_4h) & (wt1_D < wt2_D) & (k_4h > 20) & (k_D > 20)
    # ── ATR ENTRY FILTER: skip if price action is within normal ATR range ──
    if cfg.get("atr_entry_filter", 0):
        atr_median = np.nanmedian(atr_1h)
        if atr_median > 0:
            bar_move = np.abs(close - np.roll(close, 1))
            bar_move[0] = 0
            entry_long &= bar_move > atr_median * 0.5
            entry_short &= bar_move > atr_median * 0.5
    # ── COOLDOWN ──
    cooldown = cfg.get("cooldown_bars", 0)
    if cooldown > 0:
        for arr in [entry_long, entry_short]:
            last_entry = -cooldown - 1
            for i in range(n):
                if arr[i]:
                    if i - last_entry <= cooldown:
                        arr[i] = False
                    else:
                        last_entry = i
    # ── LOAD EXIT SIGNAL ARRAYS ──
    exit_type = cfg.get("exit_type", "wt_cross")
    wt_cross_bear_tf = d.get(f"wt_cross_bear_{exit_tf}", np.zeros(n, dtype=np.int8))
    wt_cross_bull_tf = d.get(f"wt_cross_bull_{exit_tf}", np.zeros(n, dtype=np.int8))
    stoch_crossunder_tf = d.get(f"stoch_crossunder_{exit_tf}", np.zeros(n, dtype=np.int8))
    stoch_crossover_tf = d.get(f"stoch_crossover_{exit_tf}", np.zeros(n, dtype=np.int8))
    dc_pos_tf = d.get(f"dc_position_{exit_tf}", np.full(n, 0.5)).astype(np.float64)
    wt_vel_tf = d.get(f"wt_velocity_{exit_tf}", np.zeros(n)).astype(np.float64)
    exit_speed_pct = cfg.get("exit_speed_pct", 50) / 100.0
    giveback_pct = cfg.get("giveback_pct", 50) / 100.0
    atr_trail_mult = cfg.get("atr_trail_mult", 2.0)
    max_hold = cfg.get("max_hold_bars", 180)
    # ── SIMULATE POSITIONS ──
    trades = []
    entry_indices = np.where(entry_long | entry_short)[0]
    i_trade = 0
    bar = 0
    while i_trade < len(entry_indices):
        entry_bar = entry_indices[i_trade]
        if entry_bar < bar:
            i_trade += 1
            continue
        is_long = bool(entry_long[entry_bar])
        entry_price = close[entry_bar]
        entry_atr = float(atr_1h[entry_bar])
        max_gain_pct = 0.0
        peak_speed = abs(float(wt_vel_tf[entry_bar]))
        exit_bar = min(entry_bar + max_hold, n - 1)
        exit_reason = "max_hold"
        for j in range(entry_bar + 1, min(entry_bar + max_hold + 1, n)):
            pnl_pct = ((close[j] - entry_price) / entry_price * 100) if is_long else ((entry_price - close[j]) / entry_price * 100)
            max_gain_pct = max(max_gain_pct, pnl_pct)
            cur_speed = abs(float(wt_vel_tf[j]))
            peak_speed = max(peak_speed, cur_speed)
            exited = False
            if exit_type == "wt_cross":
                if is_long and wt_cross_bear_tf[j]:
                    exited = True; exit_reason = "wt_cross_bear"
                elif not is_long and wt_cross_bull_tf[j]:
                    exited = True; exit_reason = "wt_cross_bull"
            elif exit_type == "speed_decay":
                if peak_speed > 0 and cur_speed < peak_speed * exit_speed_pct:
                    exited = True; exit_reason = "speed_decay"
            elif exit_type == "dc_reversal":
                if is_long and dc_pos_tf[j] < 0.3:
                    exited = True; exit_reason = "dc_rev_low"
                elif not is_long and dc_pos_tf[j] > 0.7:
                    exited = True; exit_reason = "dc_rev_high"
            elif exit_type == "stoch_cross":
                if is_long and stoch_crossunder_tf[j]:
                    exited = True; exit_reason = "stoch_cross_under"
                elif not is_long and stoch_crossover_tf[j]:
                    exited = True; exit_reason = "stoch_cross_over"
            elif exit_type == "combined_wt_speed":
                wt_exit = (is_long and wt_cross_bear_tf[j]) or (not is_long and wt_cross_bull_tf[j])
                speed_exit = peak_speed > 0 and cur_speed < peak_speed * exit_speed_pct
                if wt_exit or speed_exit:
                    exited = True; exit_reason = "combined_wt_speed"
            elif exit_type == "combined_wt_stoch":
                wt_exit = (is_long and wt_cross_bear_tf[j]) or (not is_long and wt_cross_bull_tf[j])
                stoch_exit = (is_long and stoch_crossunder_tf[j]) or (not is_long and stoch_crossover_tf[j])
                if wt_exit and stoch_exit:
                    exited = True; exit_reason = "combined_wt_stoch"
            elif exit_type == "giveback":
                if max_gain_pct > 0.1 and pnl_pct < max_gain_pct * (1 - giveback_pct):
                    exited = True; exit_reason = "giveback"
            elif exit_type == "atr_trail":
                if entry_atr > 0 and max_gain_pct > 0.1:
                    trail_dist = atr_trail_mult * entry_atr / entry_price * 100
                    if pnl_pct < max_gain_pct - trail_dist:
                        exited = True; exit_reason = "atr_trail"
            if exited:
                exit_bar = j
                break
        final_pnl = ((close[exit_bar] - entry_price) / entry_price * 100) if is_long else ((entry_price - close[exit_bar]) / entry_price * 100)
        atr_normalized_pnl = final_pnl / (entry_atr / entry_price * 100) if entry_atr > 0 else final_pnl
        trades.append({
            "pnl_pct": final_pnl,
            "atr_pnl": atr_normalized_pnl,
            "max_gain": max_gain_pct,
            "giveback": max_gain_pct - final_pnl if max_gain_pct > 0 else 0,
            "hold_bars": exit_bar - entry_bar,
            "is_long": is_long,
            "exit_reason": exit_reason,
        })
        bar = exit_bar + 1
        i_trade += 1
    if len(trades) < 3:
        return {"trades": len(trades), "sharpe": 0, "wr": 0, "avg_ret": 0, "atr_sharpe": 0, "long_trades": 0, "short_trades": 0}
    pnls = np.array([t["pnl_pct"] for t in trades])
    atr_pnls = np.array([t["atr_pnl"] for t in trades])
    givebacks = np.array([t["giveback"] for t in trades])
    holds = np.array([t["hold_bars"] for t in trades])
    avg_ret = float(np.mean(pnls))
    std_ret = float(np.std(pnls))
    sharpe = avg_ret / std_ret if std_ret > 1e-10 else 0
    atr_sharpe = float(np.mean(atr_pnls)) / float(np.std(atr_pnls)) if float(np.std(atr_pnls)) > 1e-10 else 0
    wr = float(np.sum(pnls > 0) / len(pnls) * 100)
    n_long = sum(1 for t in trades if t["is_long"])
    exit_reasons = {}
    for t in trades:
        exit_reasons[t["exit_reason"]] = exit_reasons.get(t["exit_reason"], 0) + 1
    return {
        "trades": len(trades),
        "long_trades": n_long,
        "short_trades": len(trades) - n_long,
        "sharpe": round(sharpe, 4),
        "atr_sharpe": round(atr_sharpe, 4),
        "wr": round(wr, 1),
        "avg_ret": round(avg_ret, 4),
        "std_ret": round(std_ret, 4),
        "avg_hold": round(float(np.mean(holds)), 1),
        "avg_giveback": round(float(np.mean(givebacks)), 3),
        "max_ret": round(float(np.max(pnls)), 4),
        "min_ret": round(float(np.min(pnls)), 4),
        "exit_reasons": exit_reasons,
    }


# ═══════════════════════════════════════════════════════════════════════
# V1: ORIGINAL FORWARD-RETURN BACKTEST (kept for running sweeps)
# ═══════════════════════════════════════════════════════════════════════

def backtest_symbol(npz_path, cfg, forward_bars=20):
    """Run delta signals on one NPZ file, compute PnL statistics.
    Applies HTF alignment gate + cooldown to bring frequency to realistic levels."""
    try:
        d = dict(np.load(npz_path, allow_pickle=True))
    except Exception as e:
        return None
    if "close" not in d or "timestamps" not in d:
        return None
    close = d["close"].astype(np.float64)
    n = len(close)
    if n < 500:
        return None
    try:
        signals = compute_npz_signals(d, cfg)
    except Exception as e:
        return None
    entry_long = signals["entry_long"].copy()
    entry_short = signals["entry_short"].copy()
    # ── HTF ALIGNMENT GATE ──
    # Long: 4h WT bullish (wt1 > wt2) AND D stoch_k rising or HA green
    # Short: 4h WT bearish AND D stoch_k falling or HA red
    htf_gate = cfg.get("htf_gate", "none")
    if htf_gate != "none":
        wt1_4h = d.get("wt1_4h", np.zeros(n)).astype(np.float64)
        wt2_4h = d.get("wt2_4h", np.zeros(n)).astype(np.float64)
        wt1_D = d.get("wt1_D", np.zeros(n)).astype(np.float64)
        wt2_D = d.get("wt2_D", np.zeros(n)).astype(np.float64)
        ha_4h = d.get("ha_4h", np.zeros(n))
        ha_D = d.get("ha_D", np.zeros(n))
        k_4h = d.get("stoch_k_4h", np.full(n, 50.0)).astype(np.float64)
        k_D = d.get("stoch_k_D", np.full(n, 50.0)).astype(np.float64)
        if htf_gate == "4h":
            # 4h must confirm direction
            entry_long &= (wt1_4h > wt2_4h) | (ha_4h == 1)
            entry_short &= (wt1_4h < wt2_4h) | (ha_4h == -1)
        elif htf_gate == "4h_D":
            # 4h AND D must confirm
            bull_4h = (wt1_4h > wt2_4h) | (ha_4h == 1)
            bull_D = (wt1_D > wt2_D) | (ha_D == 1)
            bear_4h = (wt1_4h < wt2_4h) | (ha_4h == -1)
            bear_D = (wt1_D < wt2_D) | (ha_D == -1)
            entry_long &= bull_4h & bull_D
            entry_short &= bear_4h & bear_D
        elif htf_gate == "4h_D_strict":
            # 4h AND D WT bullish AND K not extreme
            entry_long &= (wt1_4h > wt2_4h) & (wt1_D > wt2_D) & (k_4h < 80) & (k_D < 80)
            entry_short &= (wt1_4h < wt2_4h) & (wt1_D < wt2_D) & (k_4h > 20) & (k_D > 20)
    # ── COOLDOWN: minimum bars between entries ──
    cooldown = cfg.get("cooldown_bars", 0)
    if cooldown > 0:
        for arr in [entry_long, entry_short]:
            last_entry = -cooldown - 1
            for i in range(n):
                if arr[i]:
                    if i - last_entry <= cooldown:
                        arr[i] = False
                    else:
                        last_entry = i
    # Forward returns
    fwd = np.zeros(n)
    if n > forward_bars:
        fwd[:-forward_bars] = (close[forward_bars:] - close[:-forward_bars]) / np.clip(close[:-forward_bars], 1e-10, None) * 100
    long_entries = entry_long.sum()
    long_returns = fwd[entry_long] if long_entries > 0 else np.array([])
    short_entries = entry_short.sum()
    short_returns = -fwd[entry_short] if short_entries > 0 else np.array([])
    all_returns = np.concatenate([long_returns, short_returns]) if (long_entries + short_entries) > 0 else np.array([])
    if len(all_returns) < 5:
        return {"trades": int(long_entries + short_entries), "sharpe": 0, "wr": 0, "avg_ret": 0, "long_trades": int(long_entries), "short_trades": int(short_entries)}
    avg_ret = float(np.mean(all_returns))
    std_ret = float(np.std(all_returns))
    sharpe = avg_ret / std_ret if std_ret > 1e-10 else 0
    wr = float(np.sum(all_returns > 0) / len(all_returns) * 100)
    return {
        "trades": int(long_entries + short_entries),
        "long_trades": int(long_entries),
        "short_trades": int(short_entries),
        "sharpe": round(sharpe, 4),
        "wr": round(wr, 1),
        "avg_ret": round(avg_ret, 4),
        "std_ret": round(std_ret, 4),
        "max_ret": round(float(np.max(all_returns)), 4) if len(all_returns) > 0 else 0,
        "min_ret": round(float(np.min(all_returns)), 4) if len(all_returns) > 0 else 0,
    }


def run_sweep_v2(npz_dir, configs, asset_type="crypto_v2", max_symbols=None, symbol_filter=None):
    """Run V2 sweep with pre-built configs (entry × exit)."""
    npz_files = sorted(npz_dir.glob("*.npz"))
    if symbol_filter:
        allowed = {s.upper() for s in symbol_filter}
        npz_files = [f for f in npz_files if f.stem.upper().replace("USDT", "").replace("USD", "") in allowed or f.stem.upper() in allowed]
    if max_symbols:
        npz_files = npz_files[:max_symbols]
    logger.info(f"V2 Sweep: {len(npz_files)} symbols × {len(configs)} configs = {len(npz_files) * len(configs)} backtests")
    results = []
    for ci, cfg in enumerate(configs):
        htf = cfg.get('htf_gate', 'none')
        cd = cfg.get('cooldown_bars', 0)
        exit_t = cfg.get('exit_type', '?')
        exit_tf = cfg.get('exit_tf', '?')
        cfg_name = f"mtf={cfg['entry_min_tf']}_ez={cfg['entry_z_threshold']}_tz={cfg['tf_z_threshold']}_htf={htf}_cd={cd}_EXIT={exit_t}_{exit_tf}_mh={cfg.get('max_hold_bars',0)}"
        if exit_t == "speed_decay" or exit_t == "combined_wt_speed":
            cfg_name += f"_sp={cfg.get('exit_speed_pct', 50)}"
        elif exit_t == "giveback":
            cfg_name += f"_gb={cfg.get('giveback_pct', 50)}"
        elif exit_t == "atr_trail":
            cfg_name += f"_at={cfg.get('atr_trail_mult', 2.0)}"
        all_stats = []
        t0 = time.time()
        for npz_path in npz_files:
            stat = backtest_symbol_v2(npz_path, cfg)
            if stat and stat["trades"] > 0:
                all_stats.append(stat)
        elapsed = time.time() - t0
        valid = [s for s in all_stats if s["trades"] >= 3]
        if not valid:
            continue
        total_trades = sum(s["trades"] for s in all_stats)
        total_long = sum(s["long_trades"] for s in all_stats)
        total_short = sum(s["short_trades"] for s in all_stats)
        avg_sharpe = float(np.mean([s["sharpe"] for s in valid]))
        avg_atr_sharpe = float(np.mean([s.get("atr_sharpe", 0) for s in valid]))
        avg_wr = float(np.mean([s["wr"] for s in valid]))
        avg_ret = float(np.mean([s["avg_ret"] for s in valid]))
        avg_hold = float(np.mean([s.get("avg_hold", 0) for s in valid]))
        avg_giveback = float(np.mean([s.get("avg_giveback", 0) for s in valid]))
        n_profitable = sum(1 for s in all_stats if s["sharpe"] > 0)
        result = {
            "name": cfg_name,
            "config": cfg,
            "symbols_tested": len(all_stats),
            "total_trades": int(total_trades),
            "long_trades": int(total_long),
            "short_trades": int(total_short),
            "avg_sharpe": round(avg_sharpe, 4),
            "avg_atr_sharpe": round(avg_atr_sharpe, 4),
            "avg_wr": round(avg_wr, 1),
            "avg_ret": round(avg_ret, 4),
            "avg_hold_bars": round(avg_hold, 1),
            "avg_giveback": round(avg_giveback, 3),
            "n_profitable_symbols": n_profitable,
            "pct_profitable": round(n_profitable / max(len(all_stats), 1) * 100, 1),
            "elapsed_s": round(elapsed, 1),
        }
        results.append(result)
        if (ci + 1) % 10 == 0 or ci == 0 or len(npz_files) <= 24:
            logger.info(f"[{ci+1}/{len(configs)}] {cfg_name[:90]} → S={avg_sharpe:.3f} atrS={avg_atr_sharpe:.3f} WR={avg_wr:.1f}% tr={total_trades} hold={avg_hold:.0f} gb={avg_giveback:.2f} ({elapsed:.1f}s)")
    results.sort(key=lambda x: -x["avg_sharpe"])
    out_file = RESULTS_DIR / f"sweep_{asset_type}_{int(time.time())}.json"
    with open(out_file, "w") as f:
        json.dump({"asset_type": asset_type, "timestamp": time.time(), "n_configs": len(configs), "n_symbols": len(npz_files), "results": results}, f, indent=2, default=str)
    logger.info(f"Results saved to {out_file}")
    logger.info(f"\n{'='*120}")
    logger.info(f"TOP 20 — {asset_type} ({len(npz_files)} symbols, {len(configs)} configs)")
    logger.info(f"{'#':>3} {'Sharpe':>7} {'atrS':>6} {'WR':>5} {'AvgRet':>7} {'Trades':>7} {'Hold':>5} {'GvBk':>5} {'Prof%':>6} {'Name'}")
    logger.info(f"{'-'*120}")
    for i, r in enumerate(results[:20], 1):
        logger.info(f"{i:>3} {r['avg_sharpe']:>+6.3f} {r['avg_atr_sharpe']:>+5.3f} {r['avg_wr']:>4.1f}% {r['avg_ret']:>+6.3f}% {r['total_trades']:>7} {r['avg_hold_bars']:>5.0f} {r['avg_giveback']:>5.2f} {r['pct_profitable']:>5.1f}% {r['name'][:60]}")
    logger.info(f"{'='*120}")
    if results:
        logger.info(f"\nWINNER: {results[0]['name']}")


def run_sweep(npz_dir, grid, asset_type="crypto", max_symbols=None, symbol_filter=None, use_v2=False):
    """Run full sweep across all symbols and configs."""
    npz_files = sorted(npz_dir.glob("*.npz"))
    if symbol_filter:
        allowed = {s.upper() for s in symbol_filter}
        npz_files = [f for f in npz_files if f.stem.upper().replace("USDT", "").replace("USD", "") in allowed or f.stem.upper() in allowed]
    if max_symbols:
        npz_files = npz_files[:max_symbols]
    configs = build_configs(grid)
    bt_func = backtest_symbol_v2 if use_v2 else backtest_symbol
    logger.info(f"Sweep: {len(npz_files)} symbols × {len(configs)} configs = {len(npz_files) * len(configs)} backtests")
    results = []
    for ci, cfg in enumerate(configs):
        htf = cfg.get('htf_gate', 'none')
        cd = cfg.get('cooldown_bars', 0)
        cfg_name = f"tw={list(cfg['tf_weights'].values())[:3]}_mtf={cfg['entry_min_tf']}_ez={cfg['entry_z_threshold']}_ea={cfg['entry_accel_threshold']}_sm={cfg['speed_smooth']}_tz={cfg['tf_z_threshold']}_htf={htf}_cd={cd}"
        if use_v2:
            cfg_name += f"_exit={cfg.get('exit_type','?')}_etf={cfg.get('exit_tf','?')}_mh={cfg.get('max_hold_bars',0)}"
        all_stats = []
        t0 = time.time()
        for npz_path in npz_files:
            stat = bt_func(npz_path, cfg)
            if stat and stat["trades"] > 0:
                all_stats.append(stat)
        elapsed = time.time() - t0
        if not all_stats:
            continue
        # Aggregate across symbols
        valid = [s for s in all_stats if s["trades"] >= 3]
        if not valid:
            continue
        total_trades = sum(s["trades"] for s in all_stats)
        total_long = sum(s["long_trades"] for s in all_stats)
        total_short = sum(s["short_trades"] for s in all_stats)
        avg_sharpe = np.mean([s["sharpe"] for s in valid])
        avg_wr = np.mean([s["wr"] for s in valid])
        avg_ret = np.mean([s["avg_ret"] for s in valid])
        n_profitable = sum(1 for s in all_stats if s["sharpe"] > 0)
        result = {
            "name": cfg_name,
            "config": {k: v for k, v in cfg.items() if k in grid},
            "symbols_tested": len(all_stats),
            "total_trades": int(total_trades),
            "long_trades": int(total_long),
            "short_trades": int(total_short),
            "avg_sharpe": round(float(avg_sharpe), 4) if valid else 0,
            "avg_wr": round(float(avg_wr), 1) if valid else 0,
            "avg_ret": round(float(avg_ret), 4) if valid else 0,
            "n_profitable_symbols": n_profitable,
            "pct_profitable": round(n_profitable / max(len(all_stats), 1) * 100, 1),
            "elapsed_s": round(elapsed, 1),
        }
        if use_v2 and valid:
            avg_atr_sharpe = np.mean([s.get("atr_sharpe", 0) for s in valid])
            avg_hold = np.mean([s.get("avg_hold", 0) for s in valid])
            avg_giveback = np.mean([s.get("avg_giveback", 0) for s in valid])
            result["avg_atr_sharpe"] = round(float(avg_atr_sharpe), 4)
            result["avg_hold_bars"] = round(float(avg_hold), 1)
            result["avg_giveback"] = round(float(avg_giveback), 3)
        results.append(result)
        if (ci + 1) % 10 == 0 or ci == 0 or len(npz_files) <= 24:
            extra = f" atrS={result.get('avg_atr_sharpe', 'N/A')} hold={result.get('avg_hold_bars', 'N/A')}" if use_v2 else ""
            logger.info(f"[{ci+1}/{len(configs)}] {cfg_name[:80]} → Sharpe={result['avg_sharpe']:.3f} WR={result['avg_wr']:.1f}% trades={total_trades}{extra} ({elapsed:.1f}s)")
    results.sort(key=lambda x: -x["avg_sharpe"])
    # Save
    out_file = RESULTS_DIR / f"sweep_{asset_type}_{int(time.time())}.json"
    with open(out_file, "w") as f:
        json.dump({"asset_type": asset_type, "timestamp": time.time(),
                    "n_configs": len(configs), "n_symbols": len(npz_files),
                    "results": results}, f, indent=2, default=str)
    logger.info(f"Results saved to {out_file}")
    # Print top 15
    logger.info(f"\n{'='*100}")
    logger.info(f"TOP 15 CONFIGS — {asset_type} ({len(npz_files)} symbols)")
    logger.info(f"{'#':>3} {'Sharpe':>7} {'WR':>5} {'AvgRet':>7} {'Trades':>7} {'L':>5} {'S':>5} {'Syms':>4} {'Prof%':>6} {'Name'}")
    logger.info(f"{'-'*100}")
    for i, r in enumerate(results[:15], 1):
        logger.info(f"{i:>3} {r['avg_sharpe']:>+6.3f} {r['avg_wr']:>4.1f}% {r['avg_ret']:>+6.3f}% {r['total_trades']:>7} {r['long_trades']:>5} {r['short_trades']:>5} {r['symbols_tested']:>4} {r['pct_profitable']:>5.1f}% {r['name'][:50]}")
    logger.info(f"{'='*100}")
    if results:
        best = results[0]
        logger.info(f"\nWINNER: {best['name']}")
        logger.info(f"  Sharpe={best['avg_sharpe']} WR={best['avg_wr']}% AvgRet={best['avg_ret']}%")
        logger.info(f"  Config: {json.dumps(best['config'], default=str)}")
    return results


def show_report():
    """Show latest results."""
    files = sorted(RESULTS_DIR.glob("sweep_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("No results yet.")
        return
    for f in files[:2]:
        data = json.load(open(f))
        results = data["results"]
        print(f"\n{'='*100}")
        print(f"{data['asset_type'].upper()} — {data['n_symbols']} symbols × {data['n_configs']} configs")
        print(f"{'#':>3} {'Sharpe':>7} {'WR':>5} {'AvgRet':>7} {'Trades':>7} {'Prof%':>6} {'Name'}")
        print(f"{'-'*100}")
        for i, r in enumerate(results[:20], 1):
            marker = " ★" if i <= 3 else ""
            print(f"{i:>3} {r['avg_sharpe']:>+6.3f} {r['avg_wr']:>4.1f}% {r['avg_ret']:>+6.3f}% {r['total_trades']:>7} {r['pct_profitable']:>5.1f}% {r['name'][:55]}{marker}")
        if results:
            print(f"\nWINNER: {results[0]['name']}")
            print(f"  {json.dumps(results[0]['config'], default=str)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crypto", action="store_true", help="Broad crypto sweep (1728 configs)")
    parser.add_argument("--stocks", action="store_true", help="Broad stock sweep (1728 configs)")
    parser.add_argument("--crypto-focused", action="store_true", help="Focused crypto: HTF gate + cooldown (1-2/day target)")
    parser.add_argument("--stocks-focused", action="store_true", help="Focused stocks: HTF gate + cooldown (2/week target)")
    parser.add_argument("--crypto-mega", action="store_true", help="Local mega crypto sweep (dense grid, use --symbols)")
    parser.add_argument("--stocks-mega", action="store_true", help="Local mega stock sweep (dense grid, use --symbols)")
    parser.add_argument("--crypto-v2", action="store_true", help="V2: crypto ST exits (backward compat)")
    parser.add_argument("--stocks-v2", action="store_true", help="V2: stock ST exits (backward compat)")
    parser.add_argument("--crypto-st", action="store_true", help="V2: crypto short-term (scalp 9-90min)")
    parser.add_argument("--crypto-lt", action="store_true", help="V2: crypto long-term (swing 3h-24h)")
    parser.add_argument("--stocks-st", action="store_true", help="V2: stocks short-term (day trade 1-5h)")
    parser.add_argument("--stocks-lt", action="store_true", help="V2: stocks long-term (swing 1-5 days)")
    parser.add_argument("--options-st", action="store_true", help="V2: options short-term (1-3 day swing)")
    parser.add_argument("--options-lt", action="store_true", help="V2: options long-term (1-2 week position)")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbol list (e.g. AAPL,MSFT,NVDA)")
    args = parser.parse_args()
    sym_filter = args.symbols.split(",") if args.symbols else None
    if args.report:
        show_report()
    elif args.crypto:
        if not CRYPTO_NPZ.exists():
            logger.error(f"Crypto NPZ dir not found: {CRYPTO_NPZ}")
            sys.exit(1)
        run_sweep(CRYPTO_NPZ, PARAM_GRID, "crypto", args.max_symbols, sym_filter)
    elif args.stocks:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        run_sweep(STOCK_NPZ, STOCK_PARAM_GRID, "stocks", args.max_symbols, sym_filter)
    elif args.crypto_focused:
        if not CRYPTO_NPZ.exists():
            logger.error(f"Crypto NPZ dir not found: {CRYPTO_NPZ}")
            sys.exit(1)
        run_sweep(CRYPTO_NPZ, CRYPTO_FOCUSED_GRID, "crypto_focused", args.max_symbols, sym_filter)
    elif args.stocks_focused:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        run_sweep(STOCK_NPZ, STOCK_FOCUSED_GRID, "stocks_focused", args.max_symbols, sym_filter)
    elif args.crypto_mega:
        if not CRYPTO_NPZ.exists():
            logger.error(f"Crypto NPZ dir not found: {CRYPTO_NPZ}")
            sys.exit(1)
        n = len(build_configs(LOCAL_CRYPTO_MEGA))
        logger.info(f"LOCAL MEGA crypto: {n} configs")
        run_sweep(CRYPTO_NPZ, LOCAL_CRYPTO_MEGA, "crypto_mega", args.max_symbols, sym_filter)
    elif args.stocks_mega:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        n = len(build_configs(LOCAL_STOCK_MEGA))
        logger.info(f"LOCAL MEGA stocks: {n} configs")
        run_sweep(STOCK_NPZ, LOCAL_STOCK_MEGA, "stocks_mega", args.max_symbols, sym_filter)
    elif args.crypto_v2 or args.crypto_st:
        if not CRYPTO_NPZ.exists():
            logger.error(f"Crypto NPZ dir not found: {CRYPTO_NPZ}")
            sys.exit(1)
        v2_configs = build_v2_configs(V2_CRYPTO_ST_ENTRY, EXITS_CRYPTO_ST)
        logger.info(f"CRYPTO ST: {len(v2_configs)} configs ({len(build_configs(V2_CRYPTO_ST_ENTRY))} entry × {len(EXITS_CRYPTO_ST)} exit)")
        run_sweep_v2(CRYPTO_NPZ, v2_configs, "crypto_st", args.max_symbols, sym_filter)
    elif args.crypto_lt:
        if not CRYPTO_NPZ.exists():
            logger.error(f"Crypto NPZ dir not found: {CRYPTO_NPZ}")
            sys.exit(1)
        v2_configs = build_v2_configs(V2_CRYPTO_LT_ENTRY, EXITS_CRYPTO_LT)
        logger.info(f"CRYPTO LT: {len(v2_configs)} configs ({len(build_configs(V2_CRYPTO_LT_ENTRY))} entry × {len(EXITS_CRYPTO_LT)} exit)")
        run_sweep_v2(CRYPTO_NPZ, v2_configs, "crypto_lt", args.max_symbols, sym_filter)
    elif args.stocks_v2 or args.stocks_st:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        v2_configs = build_v2_configs(V2_STOCK_ST_ENTRY, EXITS_STOCK_ST)
        logger.info(f"STOCKS ST: {len(v2_configs)} configs ({len(build_configs(V2_STOCK_ST_ENTRY))} entry × {len(EXITS_STOCK_ST)} exit)")
        run_sweep_v2(STOCK_NPZ, v2_configs, "stocks_st", args.max_symbols, sym_filter)
    elif args.stocks_lt:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        v2_configs = build_v2_configs(V2_STOCK_LT_ENTRY, EXITS_STOCK_LT)
        logger.info(f"STOCKS LT: {len(v2_configs)} configs ({len(build_configs(V2_STOCK_LT_ENTRY))} entry × {len(EXITS_STOCK_LT)} exit)")
        run_sweep_v2(STOCK_NPZ, v2_configs, "stocks_lt", args.max_symbols, sym_filter)
    elif args.options_st:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        v2_configs = build_v2_configs(V2_OPTIONS_ST_ENTRY, EXITS_OPTIONS_ST)
        logger.info(f"OPTIONS ST: {len(v2_configs)} configs ({len(build_configs(V2_OPTIONS_ST_ENTRY))} entry × {len(EXITS_OPTIONS_ST)} exit)")
        run_sweep_v2(STOCK_NPZ, v2_configs, "options_st", args.max_symbols, sym_filter)
    elif args.options_lt:
        if not STOCK_NPZ.exists():
            logger.error(f"Stock NPZ dir not found: {STOCK_NPZ}")
            sys.exit(1)
        v2_configs = build_v2_configs(V2_OPTIONS_LT_ENTRY, EXITS_OPTIONS_LT)
        logger.info(f"OPTIONS LT: {len(v2_configs)} configs ({len(build_configs(V2_OPTIONS_LT_ENTRY))} entry × {len(EXITS_OPTIONS_LT)} exit)")
        run_sweep_v2(STOCK_NPZ, v2_configs, "options_lt", args.max_symbols, sym_filter)
    else:
        print("Usage: --crypto | --stocks | --crypto-focused | --stocks-focused | --crypto-mega | --stocks-mega | --crypto-v2 | --stocks-v2 | --report")
