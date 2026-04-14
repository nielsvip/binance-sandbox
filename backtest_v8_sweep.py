#!/usr/bin/env python3
"""
V8 Sweep — DC/WT all-TF parameter sweep, NO_LOSS abolished.
Priority: Donchian Channel + WaveTrend entries across all timeframes.

Runs V8 engine as subprocess per config. Writes results CSV + hourly reports.

Usage:
    python3 backtest_v8_sweep.py --mode crypto --account ang --start 2022-01-01 --workers 4
    python3 backtest_v8_sweep.py --mode tradier --account trb --start 2022-01-01 --workers 4
    python3 backtest_v8_sweep.py --mode tradier --start 2022-01-01 --workers 6 --tier 2
"""
import argparse
import csv
import hashlib
import itertools
import json
import logging
import os
import platform
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("v8_sweep")

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE_PATH = Path("/home/niels/binance-sandbox")
    PYTHON = str(next(
        (p for p in [
            Path("/home/niels/.conda/envs/binance_env/bin/python3"),
            Path("/home/niels/miniconda3/envs/binance_env/bin/python"),
        ] if p.exists()),
        "python3"
    ))
    SCRIPTS_DIR = Path("/home/niels/binance-sandbox")
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
    SCRIPTS_DIR = BASE_PATH

SWEEP_DIR = BASE_PATH / "backtest_v8" / "sweeps"
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

ENGINE = SCRIPTS_DIR / "backtest_v8_engine.py"
TIMEOUT = 7200  # 2h per config max (12 syms × ~9.5 min = ~115 min)

# ═══════════════════════════════════════════════════════════════
# SWEEP CONFIG DEFINITIONS
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# ALL SWEEPS: NO_LOSS = OFF (discarded). Focus: DC settings vs WT exit TF combos.
# Goal: Find best leading TFs (4h/D for "end is near") and lagging TFs (15m/5m/3m for best exit price).
# ═══════════════════════════════════════════════════════════════

# CRYPTO Tier 1 — DC position/breakout × WT exit TF × WT velocity
CRYPTO_TIER1 = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "DC_BREAKOUT_ENTRY_ENABLED": [True, False],
    "DC_BREAKOUT_SCORE": [5, 15, 25],
    "DC_BREAKOUT_TF": ["15m", "1h", "4h"],
    "WT_EXIT_VEL_THRESHOLD": [-4.0, -8.0, -12.0, -20.0],
    "WT_REDUCE_FRAC_LOW": [0.10, 0.25, 0.50],
    "ENTRY_SCORE_MIN": [18, 22],
    "MI_EXIT_ENABLED": [True, False],  # MI: momentum interception exit A/B
    "MI_ENTRY_ENABLED": [True, False],  # MI: momentum interception entry A/B
}

# CRYPTO Tier 2 — DC sizing + edge detection deep-dive
CRYPTO_TIER2 = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "DC_BREAKOUT_ENTRY_ENABLED": [True],
    "DC_BREAKOUT_SCORE": [5, 10, 15, 20],
    "DC_BREAKOUT_TF": ["15m", "1h", "4h"],
    "DC_WIDTH_SIZING_ENABLED": [True, False],
    "DC_EDGE_SIZING_ENABLED": [True, False],
    "WT_EXIT_VEL_THRESHOLD": [-6.0, -12.0],
    "WT_REDUCE_FRAC_LOW": [0.10, 0.25],
    "WT_REDUCE_FRAC_MED": [0.15, 0.25, 0.40],
    "ENTRY_SCORE_MIN": [18, 22],
    "MI_EXIT_ENABLED": [True, False],  # MI: momentum interception exit A/B
    "MI_ENTRY_ENABLED": [True, False],  # MI: momentum interception entry A/B
}

# Representative 12-symbol fast-sweep set (diverse sectors, ~5-7 min per config)
FAST_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD"
FAST_SYMBOLS_CRYPTO = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,SKYUSDT,LTCUSDT,UNIUSDT"
# CORE: 4 symbols for fast crypto cycles (completes in ~5-8 min vs 30 min for 12 symbols)
CORE_SYMBOLS_CRYPTO = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"

# TRADIER Tier 1 — WT exit TF combos × DC daytrade settings (NO_LOSS always off)
# Which TFs should LEAD exit decisions (4h/D = "getting close to end")
# Which TFs should CONFIRM exit (15m/1h = best price timing)
# Tier 1: WT exit TF breadth — how many TFs must turn against before exit?
# Key insight: 3of3 fires too fast. Test 4/5 and 5/5 to let trades mature.
# W and M not in NPZ yet — will add after precompute update.
TRADIER_TIER1 = {
    "TRADIER_DC_DAYTRADE_ENABLED": [True, False],
    "TRADIER_WT_EXIT_TFS_TRADIER": [
        "1h+4h+D",                    # Current: 3 TFs
        "5m+15m+1h+4h+D",             # ALL 5 TFs — broad consensus exit
        "15m+1h+4h+D",                # 4 TFs — skip 5m noise
        "1h+4h",                       # 2 HTFs only — fastest exits
        "4h+D",                        # 2 slowest — most patient
    ],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [2, 3, 4, 5],  # Require 2-5 TFs against
    "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER": [True, False],
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True, False],  # MI: momentum interception exit A/B
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],  # MI: momentum interception entry A/B
}

# TRADIER Tier 3 — GATE LOOSENING (from Part 16 trader intelligence)
# T1-T6: Entry gates kill 97% of winning trades. Loosen to match profitable traders.
# Baseline: PF 0.96, 731 trades, WR 49.7%, PnL -$66
TRADIER_TIER3 = {
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": [35, 50, 80],     # T2/T3: Widen stoch zone (35=current, 80=almost no filter)
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": [65, 50, 20],    # Mirror for shorts
    "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER": [25, 15, 5],         # Lower bonus = less score dependence
    "TRADIER_WT_EXIT_TFS_TRADIER": ["5m+15m+1h+4h+D", "15m+1h+4h+D", "1h+4h+D"],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [3, 4, 5],              # Require majority TF agreement
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True, False],  # MI: momentum interception exit A/B
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],  # MI: momentum interception entry A/B
}

# TRADIER Tier 4 — FULL GRID: DC position × WT patience × gate loosening
# Combines the winners from T1/T3 with DC position entry/exit values
# DC_POSITION is THE decisive factor: where is price in the channel?
TRADIER_TIER4 = {
    # DC position entry: how low in channel before entering long?
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": [0.15, 0.25, 0.35, 0.50],
    # Gate loosening: widen stoch zone to let more winners in
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": [35, 50, 80],
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": [65, 50, 20],
    # WT exit: patience (proven winner = MIN 4-5)
    "TRADIER_WT_EXIT_TFS_TRADIER": ["5m+15m+1h+4h+D", "1h+4h+D"],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [4, 5],
    # WT composite (uses cross-TF alignment for entry scoring)
    "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER": [True, False],
    # DC daytrade (marginal positive effect)
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True, False],  # MI: momentum interception exit A/B
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],  # MI: momentum interception entry A/B
}

# TRADIER Tier 5 — CONFIGURABLE ENTRY GATES (was hardcoded, now sweepable)
# Paired L/S to avoid cartesian explosion: 4 stoch × 3 extreme × 3 score × 3 mfi = 108 configs
TRADIER_TIER5 = None  # Uses TRADIER_TIER5_CONFIGS (fixed list, not grid)
_T5_STOCH = [(35, 65), (50, 50), (65, 35), (80, 20)]
_T5_EXTREME = [(20, 80), (30, 70), (40, 60)]
_T5_SCORE = [12, 8, 4]
_T5_MFI = [(42, 58), (50, 50), (60, 40)]
def _build_tier5():
    import itertools
    cfgs = []
    for (sl, ss), (el, es), sc, (ml, ms) in itertools.product(_T5_STOCH, _T5_EXTREME, _T5_SCORE, _T5_MFI):
        for mi_exit, mi_entry in [(False, False), (True, False), (True, True)]:
            cfgs.append({
                "TRADIER_STOCH_ENTRY_LONG_TRADIER": sl, "TRADIER_STOCH_ENTRY_SHORT_TRADIER": ss,
                "TRADIER_STOCH_EXTREME_LONG_TRADIER": el, "TRADIER_STOCH_EXTREME_SHORT_TRADIER": es,
                "TRADIER_ENTRY_SCORE_THRESHOLD": sc,
                "TRADIER_RSI_ENTRY_LONG_TRADIER": ml, "TRADIER_RSI_ENTRY_SHORT_TRADIER": ms,
                "TRADIER_DC_DAYTRADE_ENABLED": True,
                "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
                "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 5,
                "TRADIER_MI_EXIT_ENABLED_TRADIER": mi_exit,
                "TRADIER_MI_ENTRY_ENABLED_TRADIER": mi_entry,
            })
    return cfgs
TRADIER_TIER5_CONFIGS = _build_tier5()
# = 324 configs (4 × 3 × 3 × 3 × 3 MI modes: off/exit/exit+entry)

# TRADIER Tier 6 — MFI2 + GAP_FILL TUNING (RSI replaced by MFI, BACKTEST_CHANGE_MT2)
# MFI(2) = volume-weighted mean reversion. GAP_FILL = opening gap fade.
# Exit thresholds proven irrelevant (WT patience MIN=5 controls exits).
# Focused grid: MFI2 entry × GAP_FILL size. Lock exit at 65/35 (default).
TRADIER_TIER6 = {
    # MFI(2) entry threshold — how oversold before entering (lower = stricter)
    "TRADIER_RSI2_ENTRY_THRESHOLD": [3.0, 5.0, 10.0, 15.0, 25.0],
    # GAP_FILL minimum gap — RSI2 sweep said 0.5% best, MFI2 early data says 2.0% best
    "TRADIER_GAP_FILL_MIN_GAP_PCT": [0.5, 1.0, 1.5, 2.0],
    # Lock in proven winners
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [5],  # MIN=5 proven best (sweep report)
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True, False],  # MI: momentum interception exit A/B
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],  # MI: momentum interception entry A/B
}

# TRADIER Tier 10 — FIRST-HOUR MOMENTUM (BACKTEST_CHANGE_MT3)
# Research: First 30min >±0.5% predicts day direction 82% of time (3,560 days)
# Momentum WITH trend (FH + MFI + DC confirm) = 56-60% WR
# Test: threshold size × MFI confirm × DC confirm
TRADIER_TIER10 = {
    "TRADIER_FH_MOMENTUM_ENABLED": [True],
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": [0.3, 0.5, 0.75, 1.0, 1.5],
    "TRADIER_FH_MOMENTUM_DC_CONFIRM": [True],
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG": [0.25, 0.33, 0.5, 0.75, 1.0],  # THE question: buy bottom quarter or anywhere?
    "TRADIER_FH_MOMENTUM_MFI_CONFIRM": [False],  # MFI barely matters (1.314 vs 1.313)
    # DISABLE competing strategies to isolate FH
    "TRADIER_GAP_FILL_ENABLED": [False],
    "TRADIER_RSI2_ENABLED": [False],
    "TRADIER_SPIKE_FADE_ENABLED": [False],
    "TRADIER_DC_DAYTRADE_ENABLED": [False],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [5],
}
# = 5 × 5 × 1 × 1 × 1 × 1 × 1 = 25 configs — FH move threshold × DC entry zone

# TRADIER Tier 7 — MASTER TRADER INTELLIGENCE CONFIGS (from v8_test_queue.json)
# Internet sweep discovered our gates kill 96.6% of winning trader entries.
# 6 test configs: individual gate relaxation + combined + pattern rule.
# Each sub-dict is ONE config (no cartesian product — each runs independently).
_TRADIER_TIER7_BASE = [
    # MT_T1: Entry score 12→6 (captures 72% more winners, median winner score=6)
    {
        "TRADIER_ENTRY_SCORE_THRESHOLD": 6,
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
        "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 80,
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 20,
    },
    # MT_T2: K3M exhaustion zone 30-70 → 20-80 (kills 47.3% of profitable trades)
    {
        "TRADIER_K3M_EXHAUSTION_LOW": 20,
        "TRADIER_K3M_EXHAUSTION_HIGH": 80,
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
        "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 80,
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 20,
    },
    # MT_T3: LTF alignment 3/3 → 2/3 (kills 70.8% of profitable trades)
    {
        "TRADIER_LTF_ALIGNMENT_MIN": 2,
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
        "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 80,
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 20,
    },
    # MT_T4: Daily stoch alignment optional (kills 45% of winners)
    {
        "TRADIER_DAILY_ALIGNMENT_MANDATORY": False,
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
        "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 80,
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 20,
    },
    # MT_T5: 87.5% WR SHORT pattern (atr_pct > 1.3 + choppiness <= 52 + bb_pct_b <= 0.81)
    {
        "TRADIER_PATTERN_RULE": "atr_pct > 1.224 AND atr_pct > 1.3013 AND choppiness <= 52.252 AND bb_pct_b <= 0.8132",
        "TRADIER_PATTERN_SIDE": "SHORT",
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
        "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 80,
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 20,
    },
    # MT_T_COMBO: ALL gate killers combined — CRITICAL test
    {
        "TRADIER_ENTRY_SCORE_THRESHOLD": 6,
        "TRADIER_K3M_EXHAUSTION_LOW": 20,
        "TRADIER_K3M_EXHAUSTION_HIGH": 80,
        "TRADIER_LTF_ALIGNMENT_MIN": 2,
        "TRADIER_DAILY_ALIGNMENT_MANDATORY": False,
        "TRADIER_DC_DAYTRADE_ENABLED": True,
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 4,
        "TRADIER_WT_EXIT_TFS_TRADIER": "5m+15m+1h+4h+D",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 80,
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 20,
    },
]
# Expand each with 3 MI modes: off / exit-only / exit+entry → 18 configs
TRADIER_TIER7_CONFIGS = []
for _cfg7 in _TRADIER_TIER7_BASE:
    for _mi_exit7, _mi_entry7 in [(False, False), (True, False), (True, True)]:
        _c7 = dict(_cfg7)
        _c7["TRADIER_MI_EXIT_ENABLED_TRADIER"] = _mi_exit7
        _c7["TRADIER_MI_ENTRY_ENABLED_TRADIER"] = _mi_entry7
        TRADIER_TIER7_CONFIGS.append(_c7)
# = 18 fixed configs (6 base × 3 MI modes)

# TRADIER Tier 8 — CONGRESS CONVICTION SIZING BOOST validation
# Tests whether giving 1.2-1.5x sizing to multi-source conviction symbols improves results.
# Uses the best proven params from T4/T5 as baseline, varies only the sizing boost.
TRADIER_TIER8 = {
    "TRADIER_CONGRESS_CONVICTION_SIZING_BOOST": [1.0, 1.2, 1.3, 1.5],  # 1.0 = no boost (baseline)
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [4],
    "TRADIER_WT_EXIT_TFS_TRADIER": ["5m+15m+1h+4h+D"],
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": [80],
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": [20],
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": [0.50],
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True, False],  # MI: momentum interception exit A/B
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],  # MI: momentum interception entry A/B
}
# = 4 configs (fast — tests sizing boost in isolation)

# TRADIER Tier 2 — DC daytrade parameter deep-dive (DC ON, vary all DC params)
TRADIER_TIER2 = {
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
    "TRADIER_DC_DAYTRADE_TARGET_PCT": [0.005, 0.01, 0.015, 0.02],
    "TRADIER_DC_DAYTRADE_STOP_PCT": [0.005, 0.01, 0.015],
    "TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES": [60.0, 120.0, 240.0],
    "TRADIER_DC_DAYTRADE_BUFFER": [0.001, 0.003, 0.005],
    "TRADIER_WT_EXIT_TFS_TRADIER": ["1h+4h", "4h+D", "1h+4h+D"],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [1, 2],
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True, False],  # MI: momentum interception exit A/B
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],  # MI: momentum interception entry A/B
}


# ═══════════════════════════════════════════════════════════════
# MOMENTUM INTERCEPTION (MI) SWEEP TIERS
# Detect slowing deltas, LH/LL structure, divergence before D/W flip.
# 5 sub-signals vote; MI_TF_AGREE_MIN decides threshold.
# ═══════════════════════════════════════════════════════════════

# CRYPTO Tier 3 — MI exit/entry parameter sweep
CRYPTO_TIER3 = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "MI_EXIT_ENABLED": [True],
    "MI_ENTRY_ENABLED": [True, False],
    "MI_TF_AGREE_MIN": [2, 3, 4],
    "MI_MIN_GAIN_EXIT": [0.05, 0.10, 0.20],
    "MI_STRUCT_EXIT_ENABLED": [True, False],
    "MI_EXHAUST_EXIT_ENABLED": [True, False],
    "MI_DIV_EXIT_ENABLED": [True, False],
    "MI_VELOCITY_EXIT_ENABLED": [True],
    "MI_WAVE_EXIT_ENABLED": [True, False],
    "MI_ENTRY_STRUCT_BONUS": [5, 10, 15],
    "MI_ENTRY_EXHAUST_BONUS": [5, 8, 12],
}
# Grid note: 1 × 1 × 2 × 3 × 3 × 2 × 2 × 2 × 1 × 2 × 3 × 3 = 7,776 configs (too many)
# Will be pruned by build_configs_mi() below — only test sensible combos

# CRYPTO Tier 3F — MI FOCUSED (pruned grid: key dimensions only)
CRYPTO_TIER3F = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "MI_EXIT_ENABLED": [True],
    "MI_ENTRY_ENABLED": [True, False],
    "MI_TF_AGREE_MIN": [2, 3, 4],
    "MI_MIN_GAIN_EXIT": [0.05, 0.10, 0.20],
    "MI_VELOCITY_EXIT_ENABLED": [True],
    "MI_WAVE_EXIT_ENABLED": [True, False],
}
# = 1 × 1 × 1 × 2 × 3 × 3 × 1 × 2 = 36 configs (fast)

# TRADIER Tier 9 — MI for stocks (all sub-signals × threshold × min gain)
TRADIER_TIER9 = {
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True],
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],
    "TRADIER_MI_TF_AGREE_MIN_TRADIER": [2, 3, 4],
    "TRADIER_MI_MIN_GAIN_EXIT_TRADIER": [0.30, 0.50, 1.00],
    "TRADIER_MI_STRUCT_EXIT_ENABLED_TRADIER": [True, False],
    "TRADIER_MI_EXHAUST_EXIT_ENABLED_TRADIER": [True, False],
    "TRADIER_MI_DIV_EXIT_ENABLED_TRADIER": [True, False],
    "TRADIER_MI_VELOCITY_EXIT_ENABLED_TRADIER": [True],
    "TRADIER_MI_WAVE_EXIT_ENABLED_TRADIER": [True, False],
    "TRADIER_WT_EXIT_TFS_TRADIER": ["5m+15m+1h+4h+D"],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [5],
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
}
# Grid: 1 × 2 × 3 × 3 × 2 × 2 × 2 × 1 × 2 × 1 × 1 × 1 = 288 configs

# TRADIER Tier 9F — MI FOCUSED (pruned: key dimensions only)
TRADIER_TIER9F = {
    "TRADIER_MI_EXIT_ENABLED_TRADIER": [True],
    "TRADIER_MI_ENTRY_ENABLED_TRADIER": [True, False],
    "TRADIER_MI_TF_AGREE_MIN_TRADIER": [2, 3, 4],
    "TRADIER_MI_MIN_GAIN_EXIT_TRADIER": [0.30, 0.50, 1.00],
    "TRADIER_MI_VELOCITY_EXIT_ENABLED_TRADIER": [True],
    "TRADIER_MI_WAVE_EXIT_ENABLED_TRADIER": [True, False],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [5],
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
}
# = 1 × 2 × 3 × 3 × 1 × 2 × 1 × 1 = 36 configs (fast)

# ═══════════════════════════════════════════════════════════════
# 2.5σ STDEV BREAKOUT — HTF breakout + LTF retest scaling
# Replaces all_red/all_green. When price breaks 2.5σ BB on D/4h,
# enter immediately. Scale in on 1h/15m retests with larger size.
# Technical exit: price retreats inside bands = breakout failed.
# ═══════════════════════════════════════════════════════════════

# CRYPTO Tier 11 — STDEV BREAKOUT parameter sweep
CRYPTO_TIER11 = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "STDEV_BREAKOUT_ENABLED": [True],
    # Breakout threshold: 2.5σ = 1.125 pctb, 2.0σ = 1.0, 3.0σ = 1.25
    "STDEV_BREAKOUT_PCTB_LONG": [1.0, 1.125, 1.25],
    "STDEV_BREAKOUT_PCTB_SHORT": [0.0, -0.125, -0.25],
    # HTF combinations for breakout detection
    "STDEV_BREAKOUT_HTF_LIST": [["D", "4h"], ["D"], ["4h"]],
    # Retest TFs
    "STDEV_BREAKOUT_RETEST_TF_LIST": [["1h", "15m"], ["1h"], ["15m"]],
    # Volume filter: require elevated volume for real breakouts
    "STDEV_BREAKOUT_RVOL_MIN": [1.0, 1.2, 1.5],
    # Retest zone: how close to band must pullback be?
    "STDEV_BREAKOUT_RETEST_PCTB_MIN": [0.75, 0.85, 0.95],
    # Exit: how far must pctb fall to declare breakout failed?
    "STDEV_BREAKOUT_EXIT_PCTB_FAIL": [0.50, 0.75, 0.875],
    # Max retests before breakout cycle ends
    "STDEV_BREAKOUT_MAX_RETESTS": [2, 3, 5],
}
# Full grid = 3×3×3×3×3×3×3×3 = 6561 — too many. Use focused below.

# CRYPTO Tier 11F — STDEV BREAKOUT FOCUSED (key dimensions only)
CRYPTO_TIER11F = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "STDEV_BREAKOUT_ENABLED": [True],
    "STDEV_BREAKOUT_PCTB_LONG": [1.0, 1.125, 1.25],
    "STDEV_BREAKOUT_PCTB_SHORT": [0.0, -0.125, -0.25],
    "STDEV_BREAKOUT_HTF_LIST": [["D", "4h"], ["4h"]],
    "STDEV_BREAKOUT_RVOL_MIN": [1.0, 1.5],
    "STDEV_BREAKOUT_EXIT_PCTB_FAIL": [0.50, 0.75],
}
# = 3×3×2×2×2 = 72 configs (manageable)

# CRYPTO Tier 12 — TECHNICAL EXIT + REENTRY VARIANTS
# USER DIRECTIVE 2026-04-10: test the "exit + reenter > sit out" hypothesis vs "hold through".
# REENTRY_MANDATORY=True/False is the A/B switch for the core hypothesis.
# 2026-04-10 02:05 RETRIM: ~750s/config × 4 workers = need ≤72 configs to finish by 06:00 UTC.
# DOM_TF_ENABLED locked to True (we know it's needed); BOUNCE locked to True (winner from S1).
CRYPTO_TIER12 = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    # Core A/B: hold-through vs exit-and-reenter (the user's primary hypothesis)
    "REENTRY_MANDATORY": [True, False],
    # Reentry sizing — does coming back with MORE size win?
    "REENTRY_TIER1_SIZE_MULT": [1.0, 1.5, 2.0],
    # Delta exit dominant TF — uses the flag the user wired in tonight
    "DELTA_EXIT_DOM_TF_ENABLED": [True],
    "DELTA_EXIT_TF": ["3m", "15m"],
    # Exit aggressiveness
    "WT_EXIT_VEL_THRESHOLD": [-4.0, -8.0, -12.0],
    "DELTA_EXIT_DECAY_RATIO": [0.5, 0.9],
}
# = 2×3×1×2×3×2 = 72 configs × ~750s ÷ 4 workers = ~3.75h, finishes ~06:00 UTC

# CRYPTO Tier 13 — ENTRY SELECTIVITY (push WR ≥ 90% target)
# USER DIRECTIVE 2026-04-10 03:30: 90%+ WR + 3+ trades/day. Tier 12 maxed at 67% WR.
# To push WR up: tighten ENTRY criteria. The user's existing baseline at config.py is
# CRYPTO Tier 15 — STRUCTURAL RANGE SHIFT TF COMPARISON
# USER 2026-04-10: test which channel/TF is the best "structural shift" indicator.
# 6 alternatives: dc_1h (tight), dc_4h (current winner), dc_D (wide), bb_1h/4h/D (Bollinger).
CRYPTO_TIER15 = {
    "NOLOSS_MIN_PROFIT_PCT": [0.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
    "STRUCTURAL_RANGE_SHIFT_TF": ["dc_1h", "dc_4h", "dc_D", "bb_1h", "bb_4h", "bb_D"],
    "DELTA_EXIT_DOM_TF_ENABLED": [True],
    "DELTA_EXIT_TF": ["3m"],
    "DELTA_EXIT_DECAY_RATIO": [0.90],
    "DELTA_EXIT_MIN_TF_LOST": [1],
}
# = 6 configs — one per TF/indicator. Direct A/B/C/D/E/F comparison.

# CRYPTO Tier 15b — SRS CASCADE KNOBS (2026-04-14)
# Tests the cascade thresholds added for the new rejection-detector logic.
# K_HIGH: overbought floor for LONG exit (stoch_k_1h & 15m both turning down from >=K_HIGH)
# K_LOW: oversold ceiling for SHORT exit (stoch_k_1h & 15m both turning up from <=K_LOW)
# PROXIMITY_BPS: how close to dc_high_4h/dc_low_4h the current_price must be before cascade fires
CRYPTO_TIER15B = {
    "NOLOSS_MIN_PROFIT_PCT": [0.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    "STRUCTURAL_RANGE_SHIFT_EXIT": [True],
    "STRUCTURAL_RANGE_SHIFT_TF": ["dc_4h"],
    "STRUCTURAL_RANGE_SHIFT_K_HIGH": [70.0, 75.0, 80.0],
    "STRUCTURAL_RANGE_SHIFT_K_LOW": [20.0, 25.0, 30.0],
    "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS": [50.0, 100.0, 200.0],
    "DELTA_EXIT_DOM_TF_ENABLED": [True],
    "DELTA_EXIT_TF": ["3m"],
    "DELTA_EXIT_DECAY_RATIO": [0.90],
    "DELTA_EXIT_MIN_TF_LOST": [1],
}
# = 3×3×3 = 27 configs — cascade knob grid.

# CRYPTO Tier 14 — STRUCTURAL RANGE SHIFT EXIT (hold losers, cut at DC boundary)
# USER 2026-04-10: test "hold until profit OR 4h DC range shift" vs standard exits.
# A/B: STRUCTURAL_RANGE_SHIFT_EXIT True (new patience rule) vs False (baseline).
# When True: losers held until profitable. If entry drifts outside dc_low_4h — dc_high_4h,
# accept loss at DC boundary (dc_high_4h for longs, dc_low_4h for shorts).
CRYPTO_TIER14 = {
    "NOLOSS_MIN_PROFIT_PCT": [0.0, 0.15],    # 0.0 = breakeven ok, 0.15 = above commission floor
    "STRICT_NO_LOSS_ACCOUNTS": [[]],           # no-loss OFF (structural shift handles loss exits)
    "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],  # A/B: new rule vs baseline
    # Lock exit params at winner values
    "DELTA_EXIT_DOM_TF_ENABLED": [True],
    "DELTA_EXIT_TF": ["3m"],
    "DELTA_EXIT_DECAY_RATIO": [0.90],
    "DELTA_EXIT_MIN_TF_LOST": [1],
    # Vary exit aggressiveness to see interaction with patience
    "WT_EXIT_VEL_THRESHOLD": [-4.0, -8.0, -12.0],
}
# = 2×1×2×1×1×1×1×3 = 12 configs — fast sweep, ~2h on 4 workers

# DELTA_EXIT_DOM_TF_ENABLED=True, DELTA_EXIT_TF="3m", DELTA_EXIT_DECAY_RATIO=0.90,
# DELTA_EXIT_MIN_TF_LOST=1 → WR 89.0%, PF 26.35, Sharpe +3.45. Goal: cross 90%.
CRYPTO_TIER13 = {
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
    # Entry strictness — higher z = fewer but higher-conviction entries (drives WR up)
    "DELTA_ENTRY_Z_THRESHOLD": [1.5, 2.0, 2.5],
    "DELTA_ENTRY_MIN_TF": [2, 3],
    # HTF veto — gate entries against the bigger trend
    "DELTA_HTF_GATE": ["none", "4h", "4h_D"],
    # TF z floor — per-TF strength minimum
    "DELTA_TF_Z_THRESHOLD": [1.0, 1.5],
    # Hold these at the user's current winners (lock-in from prior sweep)
    "DELTA_EXIT_DOM_TF_ENABLED": [True],
    "DELTA_EXIT_TF": ["3m"],
    "DELTA_EXIT_DECAY_RATIO": [0.90],
    "DELTA_EXIT_MIN_TF_LOST": [1],
}
# = 3×2×3×2×1×1×1×1 = 36 configs × ~6 min ÷ 4 workers (50-day window) = ~54 min, done well before market open

# ═══════════════════════════════════════════════════════════════════════════════
# CRYPTO Tier 17 — EXIT ABLATION: Each exit path True/False
# 2026-04-10: User needs to safely remove Finandy NO_LOSS.
# Test every exit independently ON/OFF. One-at-a-time ablation (disable one, keep rest).
# Then combo sweep on the top harmful exits.
# UNIVERSAL_NOLOSS_GATE=True throughout — losers wait for 0% or structural shift.
# ═══════════════════════════════════════════════════════════════════════════════
def _build_exit_ablation_configs():
    """Focused exit ablation: 12 suspect flags + continuous param sweeps.
    Priority: delta/wt_dc exits stay, test everything else that might hurt.
    KEEP: DELTA_EXIT, STRUCTURAL_RANGE_SHIFT, RZ_EXIT (proven).
    TEST: the 12 flags user flagged as 'probably wrong'."""
    base = {
        "UNIVERSAL_NOLOSS_GATE": True,
        "STRICT_NO_LOSS_ACCOUNTS": [],
        "NOLOSS_MIN_PROFIT_PCT": 0.0,
        "STRUCTURAL_RANGE_SHIFT_EXIT": True,
        "STRUCTURAL_RANGE_SHIFT_TF": "dc_4h",
        "DELTA_EXIT_ENABLED": True,
        "DELTA_EXIT_DOM_TF_ENABLED": True,
        "DELTA_EXIT_TF": "3m",
        "DELTA_EXIT_DECAY_RATIO": 0.90,
        "DELTA_EXIT_MIN_TF_LOST": 1,
        "RZ_EXIT_ENABLED": True,
        # --- 12 suspect flags at current values ---
        "EXIT_MARKET_SPIKE_REDUCE_ENABLED": True,
        "EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED": True,
        "EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED": True,
        "EXIT_HARD_MAX_LOSS_CAP_ENABLED": True,
        "EXIT_PREEMPTIVE_BREAKEVEN_ENABLED": True,
        "HARD_MAX_LOSS_PCT": -5.0,
        "MI_EXIT_ENABLED": False,
        "WT_EXIT_VEL_THRESHOLD": -6.0,
        "AGGRESSIVE_LOSS_CUT_ENABLED": False,
        "RZ_K_EXIT": 90.0,  # Now: k_15m > 90 + in red zone + slowdown (not flat k_1h dump)
        "RZ_MFI_EXIT": 85.0,  # Now: MFI confirms red zone exhaustion (not standalone)
        "EXIT_GAIN_EROSION_ENABLED": True,
    }
    configs = []
    # Config 0: BASELINE — current live settings
    configs.append(dict(base))
    # --- PHASE 1: Each suspect bool True/False (one at a time) ---
    bool_toggles = [
        ("EXIT_MARKET_SPIKE_REDUCE_ENABLED", False),
        ("EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED", False),
        ("EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED", False),
        ("EXIT_HARD_MAX_LOSS_CAP_ENABLED", False),
        ("EXIT_PREEMPTIVE_BREAKEVEN_ENABLED", False),
        ("MI_EXIT_ENABLED", True),  # Currently OFF — test if ON helps
        ("AGGRESSIVE_LOSS_CUT_ENABLED", True),  # Currently OFF — test ON
        ("EXIT_GAIN_EROSION_ENABLED", False),
    ]
    for key, test_val in bool_toggles:
        cfg = dict(base); cfg[key] = test_val
        configs.append(cfg)
    # --- PHASE 2: Continuous param sweeps ---
    # WT_EXIT_VEL_THRESHOLD: calmer (-2) vs current (-6) vs aggressive (-12)
    for vel in [-2.0, -4.0, -12.0]:
        cfg = dict(base); cfg["WT_EXIT_VEL_THRESHOLD"] = vel
        configs.append(cfg)
    # HARD_MAX_LOSS_PCT: -3% (tight) vs -5% (current) vs -10% (loose) vs disabled
    for pct in [-3.0, -10.0, -999.0]:
        cfg = dict(base); cfg["HARD_MAX_LOSS_PCT"] = pct
        configs.append(cfg)
    # RZ_K_EXIT: 80 (aggressive) vs 90 (current) vs 95 (conservative)
    for k in [80.0, 95.0]:
        cfg = dict(base); cfg["RZ_K_EXIT"] = k
        configs.append(cfg)
    # RZ_MFI_EXIT: 75 (aggressive) vs 85 (current) vs 95 (conservative)
    for m in [75.0, 95.0]:
        cfg = dict(base); cfg["RZ_MFI_EXIT"] = m
        configs.append(cfg)
    # --- PHASE 3: Combos ---
    # AGGRESSIVE: everything that might help ON, tight thresholds
    agg = dict(base)
    agg["MI_EXIT_ENABLED"] = True
    agg["WT_EXIT_VEL_THRESHOLD"] = -2.0
    agg["RZ_K_EXIT"] = 80.0
    agg["RZ_MFI_EXIT"] = 75.0
    agg["HARD_MAX_LOSS_PCT"] = -3.0
    configs.append(agg)
    # CONSERVATIVE: all suspect exits OFF, let delta+SRS handle everything
    cons = dict(base)
    cons["EXIT_MARKET_SPIKE_REDUCE_ENABLED"] = False
    cons["EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED"] = False
    cons["EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED"] = False
    cons["EXIT_HARD_MAX_LOSS_CAP_ENABLED"] = False
    cons["EXIT_PREEMPTIVE_BREAKEVEN_ENABLED"] = False
    cons["EXIT_GAIN_EROSION_ENABLED"] = False
    cons["AGGRESSIVE_LOSS_CUT_ENABLED"] = False
    cons["WT_EXIT_VEL_THRESHOLD"] = -12.0
    cons["RZ_K_EXIT"] = 95.0
    cons["RZ_MFI_EXIT"] = 95.0
    configs.append(cons)
    # NOLOSS OFF (danger test — what happens without the gate?)
    no_gate = dict(base)
    no_gate["UNIVERSAL_NOLOSS_GATE"] = False
    configs.append(no_gate)
    return configs

CRYPTO_TIER17_CONFIGS = _build_exit_ablation_configs()
# = 22 configs: baseline + 8 bool toggles + 10 param sweeps + 3 combos

# ═══════════════════════════════════════════════════════════════════════════════
# TRADIER Tier 17 — same exit ablation for stocks
# Same logic, adapted for stock-specific exits
# ═══════════════════════════════════════════════════════════════════════════════
def _build_tradier_exit_ablation_configs():
    """Build stock exit ablation configs that ACTUALLY vary results.

    The tradier exit path is dominated by wt_dc_exit_scorer.py which was
    fully hardcoded. Now it reads config params. The REAL knobs are:
    - EXIT_SCORER_MIN_CONDITIONS: 3/4/5 (how many multi-TF conditions needed)
    - EXIT_SCORER_K_EXTREME: 65/75/85 (stoch threshold)
    - EXIT_SCORER_DC_EXTREME: 0.70/0.80/0.90 (DC position threshold)
    - WT_DC_EXIT_THRESHOLD: 20/30/40 (score cutoff)
    - DELTA_EXIT_ENABLED: True/False (delta engine exit)
    - STRUCTURAL_RANGE_SHIFT_EXIT: True/False (structural shift exit)
    - UNIVERSAL_NOLOSS_GATE: True/False (no-loss gate)
    """
    base = {
        "UNIVERSAL_NOLOSS_GATE": True,
        "STRUCTURAL_RANGE_SHIFT_EXIT": True,
        "STRUCTURAL_RANGE_SHIFT_TF": "bb_4h",
        "DELTA_EXIT_ENABLED": True,
        "EXIT_SCORER_MIN_CONDITIONS": 5,
        "EXIT_SCORER_K_EXTREME": 75,
        "EXIT_SCORER_DC_EXTREME": 0.80,
        "WT_DC_EXIT_THRESHOLD": 30,
        "HARD_MAX_LOSS_PCT": -5.0,
        "EXIT_HARD_MAX_LOSS_CAP_ENABLED": True,
    }
    configs = []
    # Config 0: BASELINE (strict 5/5, current settings)
    configs.append(dict(base))
    # Config 1-3: Vary MIN_CONDITIONS (most impactful knob — loosens/tightens exit)
    for mc in [3, 4]:
        cfg = dict(base); cfg["EXIT_SCORER_MIN_CONDITIONS"] = mc
        configs.append(cfg)
    # Config 3-4: Vary K_EXTREME threshold
    for ke in [65, 85]:
        cfg = dict(base); cfg["EXIT_SCORER_K_EXTREME"] = ke
        configs.append(cfg)
    # Config 5-6: Vary DC_EXTREME threshold
    for de in [0.70, 0.90]:
        cfg = dict(base); cfg["EXIT_SCORER_DC_EXTREME"] = de
        configs.append(cfg)
    # Config 7-8: Vary WT_DC_EXIT_THRESHOLD
    for th in [20, 40]:
        cfg = dict(base); cfg["WT_DC_EXIT_THRESHOLD"] = th
        configs.append(cfg)
    # Config 9: DELTA_EXIT off
    cfg = dict(base); cfg["DELTA_EXIT_ENABLED"] = False
    configs.append(cfg)
    # Config 10: SRS off
    cfg = dict(base); cfg["STRUCTURAL_RANGE_SHIFT_EXIT"] = False
    configs.append(cfg)
    # Config 11: NOLOSS gate off (danger test)
    cfg = dict(base); cfg["UNIVERSAL_NOLOSS_GATE"] = False
    configs.append(cfg)
    # Config 12: LOOSE combo (3/5, K=65, DC=0.70, threshold=20) — max exits
    cfg = dict(base)
    cfg["EXIT_SCORER_MIN_CONDITIONS"] = 3
    cfg["EXIT_SCORER_K_EXTREME"] = 65
    cfg["EXIT_SCORER_DC_EXTREME"] = 0.70
    cfg["WT_DC_EXIT_THRESHOLD"] = 20
    configs.append(cfg)
    # Config 13: TIGHT combo (5/5, K=85, DC=0.90, threshold=40) — min exits
    cfg = dict(base)
    cfg["EXIT_SCORER_MIN_CONDITIONS"] = 5
    cfg["EXIT_SCORER_K_EXTREME"] = 85
    cfg["EXIT_SCORER_DC_EXTREME"] = 0.90
    cfg["WT_DC_EXIT_THRESHOLD"] = 40
    configs.append(cfg)
    # Config 14: loss cap -3%
    cfg = dict(base); cfg["HARD_MAX_LOSS_PCT"] = -3.0
    configs.append(cfg)
    # Config 15: loss cap -10%
    cfg = dict(base); cfg["HARD_MAX_LOSS_PCT"] = -10.0
    configs.append(cfg)
    return configs

TRADIER_TIER17_CONFIGS = _build_tradier_exit_ablation_configs()
# = 16 configs: baseline + scorer param variations + combo extremes + loss caps

# TRADIER Tier 11 — STDEV BREAKOUT for stocks
# BOTH prefixed (tradier_manage) and unprefixed (ez_positions_quick) needed.
TRADIER_TIER11 = None  # Uses TRADIER_TIER11_CONFIGS (paired keys)
def _build_tradier_tier11():
    import itertools
    pctb_pairs = [(1.0, 0.0), (1.125, -0.125), (1.25, -0.25)]
    htf_opts = [["D", "4h"], ["D"], ["4h"]]
    rvol_opts = [1.0, 1.2, 1.5]
    exit_opts = [0.50, 0.75, 0.875]
    retest_opts = [2, 3, 5]
    cfgs = []
    for (pl, ps), htf, rv, ex, rt in itertools.product(pctb_pairs, htf_opts, rvol_opts, exit_opts, retest_opts):
        c = {
            "STDEV_BREAKOUT_ENABLED": True, "TRADIER_STDEV_BREAKOUT_ENABLED": True,
            "STDEV_BREAKOUT_PCTB_LONG": pl, "TRADIER_STDEV_BREAKOUT_PCTB_LONG": pl,
            "STDEV_BREAKOUT_PCTB_SHORT": ps, "TRADIER_STDEV_BREAKOUT_PCTB_SHORT": ps,
            "STDEV_BREAKOUT_HTF_LIST": htf, "TRADIER_STDEV_BREAKOUT_HTF_LIST": htf,
            "STDEV_BREAKOUT_RVOL_MIN": rv, "TRADIER_STDEV_BREAKOUT_RVOL_MIN": rv,
            "STDEV_BREAKOUT_EXIT_PCTB_FAIL": ex, "TRADIER_STDEV_BREAKOUT_EXIT_PCTB_FAIL": ex,
            "STDEV_BREAKOUT_MAX_RETESTS": rt, "TRADIER_STDEV_BREAKOUT_MAX_RETESTS": rt,
            "TRADIER_DC_DAYTRADE_ENABLED": True, "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 5,
        }
        cfgs.append(c)
    return cfgs
TRADIER_TIER11_CONFIGS = _build_tradier_tier11()
# = 3×3×3×3×3 = 729 configs

# TRADIER Tier 11F — STDEV BREAKOUT FOCUSED for stocks
TRADIER_TIER11F = None
def _build_tradier_tier11f():
    import itertools
    pctb_pairs = [(1.0, 0.0), (1.125, -0.125), (1.25, -0.25)]
    htf_opts = [["D", "4h"], ["4h"]]
    rvol_opts = [1.0, 1.5]
    exit_opts = [0.50, 0.75]
    cfgs = []
    for (pl, ps), htf, rv, ex in itertools.product(pctb_pairs, htf_opts, rvol_opts, exit_opts):
        c = {
            "STDEV_BREAKOUT_ENABLED": True, "TRADIER_STDEV_BREAKOUT_ENABLED": True,
            "STDEV_BREAKOUT_PCTB_LONG": pl, "TRADIER_STDEV_BREAKOUT_PCTB_LONG": pl,
            "STDEV_BREAKOUT_PCTB_SHORT": ps, "TRADIER_STDEV_BREAKOUT_PCTB_SHORT": ps,
            "STDEV_BREAKOUT_HTF_LIST": htf, "TRADIER_STDEV_BREAKOUT_HTF_LIST": htf,
            "STDEV_BREAKOUT_RVOL_MIN": rv, "TRADIER_STDEV_BREAKOUT_RVOL_MIN": rv,
            "STDEV_BREAKOUT_EXIT_PCTB_FAIL": ex, "TRADIER_STDEV_BREAKOUT_EXIT_PCTB_FAIL": ex,
            "TRADIER_DC_DAYTRADE_ENABLED": True, "TRADIER_WT_EXIT_MIN_TFS_TRADIER": 5,
        }
        cfgs.append(c)
    return cfgs
TRADIER_TIER11F_CONFIGS = _build_tradier_tier11f()
# = 3×2×2×2 = 24 configs (fast)
# = 3×3×2×2×2 = 72 configs (manageable)


# ═══════════════════════════════════════════════════════════════
# TRADIER Tier 20 — REAL MONEY SWEEP ($70k capital, $180k daytrade BP)
# Position sizing scaled to actual account: half-Kelly ~6% per trade.
# Tests the #1 bottleneck (sizing) × proven best WT/DC params.
# Run with: --tier 20 --capital 70000 --start 2024-01-01
# ═══════════════════════════════════════════════════════════════
_SIZING_70K = {
    "TRADIER_START_POSITION_SIZE": 3500.0,
    "TRADIER_MAX_POSITION_SIZE": 15000.0,
    "TRADIER_SWING_START_SIZE": 5000.0,
    "TRADIER_SWING_LONG_BUDGET": 35000.0,
    "TRADIER_SWING_SHORT_BUDGET": 35000.0,
    "TRADIER_SWING_MAX_POSITION_SIZE": 12000.0,
    "TRADIER_SCALP_START_SIZE": 3000.0,
    "TRADIER_SCALP_LONG_BUDGET": 15000.0,
    "TRADIER_SCALP_SHORT_BUDGET": 15000.0,
    "TRADIER_SCALP_MAX_POSITION_SIZE": 8000.0,
    "TRADIER_ROTATION_POSITION_SIZE": 7000.0,
    "TRADIER_RSI2_POSITION_SIZE": 3500.0,
    "TRADIER_GAP_FILL_POSITION_SIZE": 3500.0,
    "TRADIER_DC_DAYTRADE_START_SIZE": 4000.0,
    "TRADIER_DC_DAYTRADE_LONG_BUDGET": 45000.0,
    "TRADIER_DC_DAYTRADE_SHORT_BUDGET": 45000.0,
    "TRADIER_DC_DAYTRADE_MAX_POSITION_SIZE": 12000.0,
}
TRADIER_TIER20 = {
    # Sizing: $70k account, half-Kelly ~6% = $4,200/trade
    **{k: [v] for k, v in _SIZING_70K.items()},
    # PROVEN WINNER: First-Hour Momentum (Tier 10: ALL 25 configs profitable, Sharpe 1.36-1.54)
    "TRADIER_FH_MOMENTUM_ENABLED": [True],
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": [0.3],       # Best: 0.3% (0.5-1.0 also fine, 1.5% drops)
    "TRADIER_FH_MOMENTUM_DC_CONFIRM": [True],
    "TRADIER_FH_MOMENTUM_MFI_CONFIRM": [False],       # MFI barely matters (1.314 vs 1.313)
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG": [0.25, 1.0],   # Bottom quarter vs anywhere (barely matters but test both)
    # WT exit patience: 5/5 = let trades mature (proven best)
    "TRADIER_WT_EXIT_TFS_TRADIER": ["5m+15m+1h+4h+D"],
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [5],
    # DC daytrade: on (confirmed profitable in Tier 10 combo)
    "TRADIER_DC_DAYTRADE_ENABLED": [True],
    # Gate loosening: score 6 vs 12 (Tier 7 showed 96.6% of winners killed by strict gates)
    "TRADIER_ENTRY_SCORE_THRESHOLD": [6, 12],
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": [80],
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": [20],
    # Competing strategies: GAP_FILL + RSI2/MFI2 on/off to isolate FH edge
    "TRADIER_GAP_FILL_ENABLED": [True, False],
    "TRADIER_RSI2_ENABLED": [True, False],
}
# = 2 × 2 × 2 × 2 = 16 configs (DC_MAX × SCORE × GAP_FILL × RSI2)
# Each config runs 121 symbols × 2 years. At ~2 min/symbol = ~4 hours/config.
# 16 configs × 4 workers = ~16 hours total.


# ═══════════════════════════════════════════════════════════════
# TRADIER Tier 25 — V8-LIVE PARAMETERS (exits + RZ + delta + hold)
# These are the only parameters that actually affect V8 results because
# execute_trade_action is patched out (entry gates are dead).
# Tests: delta decay, min hold, RZ features, WT exit patience, structural exit.
# 3 × 3 × 2 × 2 × 2 × 2 × 2 × 2 = 576 configs
# ═══════════════════════════════════════════════════════════════
TRADIER_TIER25 = {
    # ═══ PROVEN WORKING (isolation-tested 2026-04-12) ═══
    # WT_DC ENTRY — the REAL entry gate (wt_dc_score_entry threshold)
    # A/B: threshold=1 vs 99 → 0.309 vs 0.371 Sharpe
    "WT_DC_ENTRY_THRESHOLD": [35, 43, 55, 75],
    # STRUCTURAL RANGE SHIFT — cascade rewrite (2026-04-14): TF bb_1h + k/wt cascade
    # Isolation: ON=0.278 vs OFF=0.259 (21 more trades) — pre-cascade numbers
    "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
    "STRUCTURAL_RANGE_SHIFT_TF": ["bb_1h"],
    "STRUCTURAL_RANGE_SHIFT_K_HIGH": [75.0],
    "STRUCTURAL_RANGE_SHIFT_K_LOW": [25.0],
    "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS": [100.0],
    # WT CROSSUNDER FINAL — dominant exit (WT1<WT2 on 1h+4h)
    # Disabling forces delta/RZ to be sole exit path — completely different regime
    "WT_CROSSUNDER_FINAL_ENABLED": [True, False],
    # DELTA ENGINE — master on/off for entire delta entry+exit system
    "DELTA_ENGINE_ENABLED": [True, False],
    # DELTA ENTRY — delta entry independent of delta exit
    "DELTA_ENTRY_ENABLED": [True, False],
    # RZ EXIT — zone-based exits (TOP_FAILED_BREAKOUT, TOP_EXIT_LONG, BOTTOM_*)
    # Isolation: ON=507 trades vs OFF=486 trades
    "RZ_EXIT_ENABLED": [True, False],
    # SATOSHIT — the should_enter pre-filter (live uses this as sole entry)
    "SATOSHIT_ENTRY_FILTER": [True, False],
    # SHOULD_ENTER_FALLBACK_ENABLED — activate traditional WT/stochastic entry logic AFTER satoshit check.
    # MUST be True or entries are fully blocked when SATOSHIT returns False.
    # Without this, SATOSHIT=False configs produce 0 trades (should_enter_long returns False).
    "SHOULD_ENTER_FALLBACK_ENABLED": [True],
    # RULE 2026-04-14: NEVER prune knobs that appear dead. If a toggle produces no variance,
    # the OTHER settings around it are wrong and need to change until True/False matters.
    # Dead knob = signal that some upstream gate is blocking the feature from firing.
    # ═══ REMOVED 2026-04-12 (proven zero effect in isolation tests) ═══
    # DELTA_EXIT_DECAY_RATIO: standard weakness exit overshadowed by RZ/WT_CROSS exits
    # DELTA_EXIT_MIN_TF_LOST: same — standard weakness never fires as deciding path
    # TRADIER_MIN_HOLD_MINUTES: all positions held 30-50h, even 480m gate never blocks
    # RZ_DIV_EXIT_ENABLED: structurally unreachable (standard weakness fires first)
    # RZ_TWO_PHASE_EXIT_ENABLED: structurally unreachable (standard weakness fires first)
}
# 4 × 2 × 2 × 2 × 2 × 2 × 2 × 1 = 256 configs (SHOULD_ENTER_FALLBACK_ENABLED=True fixed — required for entries)

# ═══════════════════════════════════════════════════════════════
# CRYPTO Tier 25 — V8-LIVE PARAMETERS ABLATION (crypto equivalent of TRADIER_TIER25)
# Tests the production on/off switches that actually drive crypto V8 results.
# Mirrors tradier_t25 design: each boolean tested independently + entry threshold grid.
# STRUCTURAL_RANGE_SHIFT_TF locked to dc_4h (ABSOLUTE RULE — never change for crypto).
# Run with: --tier 25 --mode crypto --start 2024-01-01 --workers 2 --symbols fast
# ═══════════════════════════════════════════════════════════════
CRYPTO_TIER25 = {
    # WT_DC_ENTRY_THRESHOLD: DEAD FOR CRYPTO — ez_positions_quick never reads it.
    # Only used in tradier_manage.py. Removed to halve config count (256→64).
    # "WT_DC_ENTRY_THRESHOLD": [35, 43, 55, 75],
    # Structural range shift — crypto uses dc_4h (ABSOLUTE: never change TF for crypto)
    "STRUCTURAL_RANGE_SHIFT_EXIT": [True, False],
    "STRUCTURAL_RANGE_SHIFT_TF": ["dc_4h"],  # LOCKED — dc_4h is the only valid value for crypto
    "STRUCTURAL_RANGE_SHIFT_K_HIGH": [75.0],
    "STRUCTURAL_RANGE_SHIFT_K_LOW": [25.0],
    # WT crossunder final — dominant exit signal (WT1<WT2 on 1h+4h)
    "WT_CROSSUNDER_FINAL_ENABLED": [True, False],
    # Delta engine — master on/off for entire delta entry+exit system
    "DELTA_ENGINE_ENABLED": [True, False],
    # Delta entry FILTER — requires delta engine approval to open a position
    # True = entries blocked unless delta engine fires (use as strict entry gate)
    # False = entries proceed normally, delta engine only affects exits
    "DELTA_ENTRY_ENABLED": [True, False],
    # RZ exit — zone-based exits (TOP/BOTTOM exhaustion signals)
    "RZ_EXIT_ENABLED": [True, False],
    # SATOSHIT — copy trader entry filter (SATOSHIT_ENABLED in config.py)
    "SATOSHIT_ENABLED": [True, False],
    # NOLOSS: always OFF for crypto (no stop loss paths)
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
}
# 2^6 = 64 configs (WT_DC_ENTRY_THRESHOLD removed — dead for crypto)

# ═══════════════════════════════════════════════════════════════
# TRADIER Tier 26 — Reentry Rally Gate Sweep
# Tests REENTRY_RALLY_K15M_MAX (k15m level cap on WT_2of3 reentry) and
# REENTRY_RALLY_HTF_MIN (min HTF TFs aligned: 1h/4h/D) added to evaluate_reentry.
# 100=off means original stoch gate only; 40/20 add k15m level cap.
# HTF_MIN=1: at least 1 of (1h/4h/D) — lenient; 3: all 3 required — strict.
# 3 × 3 = 9 configs
# Run with: --tier 26 --mode tradier --account trb --start 2024-06-01 --workers 4
# ═══════════════════════════════════════════════════════════════
TRADIER_TIER26 = {
    "REENTRY_RALLY_K15M_MAX": [100.0, 40.0, 20.0],
    "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
}
# 3 × 3 = 9 configs

# ═══════════════════════════════════════════════════════════════
# CRYPTO Tier 26 — Reentry Rally Gate Sweep (mirrors TRADIER_TIER26)
# Same knobs applied to crypto evaluate_reentry WT_2of3 block.
# Crypto uses 3m/15m/1h for _wt_fav, then 1h/4h/D for HTF count.
# Run with: --tier 26 --mode crypto --start 2024-01-01 --workers 2 --symbols fast
# ═══════════════════════════════════════════════════════════════
CRYPTO_TIER26 = {
    "REENTRY_RALLY_K15M_MAX": [100.0, 40.0, 20.0],
    "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
    "NOLOSS_MIN_PROFIT_PCT": [-999.0],
    "STRICT_NO_LOSS_ACCOUNTS": [[]],
}
# 3 × 3 = 9 configs

# ═══════════════════════════════════════════════════════════════
# CRYPTO Tier 30 — Copy Trader Entry Gate Sweep (BC_170–BC_174)
# Tests 5 new entry gates individually, combined, and top-3 combo.
# All gates default to False in config.py; sweep enables them one at a time.
# Run with: --tier 30 --mode crypto --start 2024-01-01 --workers 2 --symbols fast
# ═══════════════════════════════════════════════════════════════
_CT_GATE_BASE = {
    "NOLOSS_MIN_PROFIT_PCT": -999.0,
    "STRICT_NO_LOSS_ACCOUNTS": [],
    "CT_WT_VELOCITY_GATE_ENABLED": False,
    "CT_15M_MOMENTUM_GATE_ENABLED": False,
    "CT_DC_CROSSOVER_SKIP_ENABLED": False,
    "CT_CHOP_4H_GATE_ENABLED": False,
    "CT_CHOP_4H_MAX": 50.0,
    "CT_VOLUME_SURGE_GATE_ENABLED": False,
}

def _build_ct_gate_configs():
    configs = []
    # 0: BASELINE — all CT gates OFF
    baseline = dict(_CT_GATE_BASE)
    configs.append(baseline)
    # 1: BC_170 only — WT velocity 1h
    c170 = dict(_CT_GATE_BASE)
    c170["CT_WT_VELOCITY_GATE_ENABLED"] = True
    configs.append(c170)
    # 2: BC_171 only — 15m momentum (stoch/MFI)
    c171 = dict(_CT_GATE_BASE)
    c171["CT_15M_MOMENTUM_GATE_ENABLED"] = True
    configs.append(c171)
    # 3: BC_172 only — DC crossover skip (SHORT only)
    c172 = dict(_CT_GATE_BASE)
    c172["CT_DC_CROSSOVER_SKIP_ENABLED"] = True
    configs.append(c172)
    # 4: BC_173 only — chop 4h gate (threshold 50)
    c173 = dict(_CT_GATE_BASE)
    c173["CT_CHOP_4H_GATE_ENABLED"] = True
    c173["CT_CHOP_4H_MAX"] = 50.0
    configs.append(c173)
    # 5: BC_174 only — volume surge gate
    c174 = dict(_CT_GATE_BASE)
    c174["CT_VOLUME_SURGE_GATE_ENABLED"] = True
    configs.append(c174)
    # 6: ALL 5 gates together
    c_all = dict(_CT_GATE_BASE)
    c_all["CT_WT_VELOCITY_GATE_ENABLED"] = True
    c_all["CT_15M_MOMENTUM_GATE_ENABLED"] = True
    c_all["CT_DC_CROSSOVER_SKIP_ENABLED"] = True
    c_all["CT_CHOP_4H_GATE_ENABLED"] = True
    c_all["CT_CHOP_4H_MAX"] = 50.0
    c_all["CT_VOLUME_SURGE_GATE_ENABLED"] = True
    configs.append(c_all)
    # 7: Top 3 by Cohen's d — BC_170 + BC_171 + BC_173
    c_top3 = dict(_CT_GATE_BASE)
    c_top3["CT_WT_VELOCITY_GATE_ENABLED"] = True
    c_top3["CT_15M_MOMENTUM_GATE_ENABLED"] = True
    c_top3["CT_CHOP_4H_GATE_ENABLED"] = True
    c_top3["CT_CHOP_4H_MAX"] = 50.0
    configs.append(c_top3)
    return configs

CRYPTO_TIER30_CONFIGS = _build_ct_gate_configs()
# = 8 configs (baseline + 5 individual + all-5 + top-3)


def build_configs(param_grid: Dict) -> List[Dict]:
    keys = list(param_grid.keys())
    values = [param_grid[k] for k in keys]
    configs = []
    for combo in itertools.product(*values):
        cfg = dict(zip(keys, combo))
        configs.append(cfg)
    return configs


def config_name(cfg: Dict) -> str:
    parts = []
    for k, v in sorted(cfg.items()):
        short_k = k.replace("TRADIER_", "T_").replace("DC_BREAKOUT_", "DC_").replace("WT_EXIT_", "WT_").replace("NOLOSS_MIN_PROFIT_PCT", "NOLOSS").replace("ENTRY_SCORE_MIN", "SCORE")
        if isinstance(v, list):
            short_v = "[]" if not v else "_".join(str(x) for x in v)
        elif isinstance(v, float) and v == -999.0:
            short_v = "OFF"
        elif isinstance(v, bool):
            short_v = "1" if v else "0"
        else:
            short_v = str(v).replace(".", "p").replace("+", "P").replace("-", "N")
        parts.append(f"{short_k[:8]}={short_v[:6]}")
    name = "_".join(parts)
    return name[:120]


# ═══════════════════════════════════════════════════════════════
# RUN ONE CONFIG
# ═══════════════════════════════════════════════════════════════
def run_config(args_tuple) -> Dict:
    cfg, mode, account, start, capital, run_id, npz_dir, symbols_filter = args_tuple
    name = config_name(cfg)
    override_path = SWEEP_DIR / f"override_{run_id}.json"
    with open(override_path, "w") as f:
        json.dump(cfg, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    env["V8_REAL_ETA"] = os.environ.get("V8_REAL_ETA", "1")
    env["V8_BYPASS_SHOULD_ENTER"] = os.environ.get("V8_BYPASS_SHOULD_ENTER", "0")
    env["V8_SWEEP_MODE"] = "1"  # suppress verbose logs → 10-50x faster simulation
    cmd = [PYTHON, str(ENGINE), "--mode", mode, "--account", account, "--start", start, "--capital", str(capital)]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]
    if symbols_filter:
        cmd += ["--symbols", symbols_filter]
    t0 = time.time()
    # Kill thresholds:
    # HEARTBEAT_TIMEOUT: if no V8_HEARTBEAT or V8_RESULT_LIVE after N secs → OOM/crash → kill
    # ZERO_TRADES_TIMEOUT: if V8_RESULT_LIVE shows closes=0 for N secs → filters too strict → kill
    HEARTBEAT_TIMEOUT = 90   # seconds with no heartbeat/result → kill (OOM or crash)
    ZERO_TRADES_TIMEOUT = 30  # seconds closes=0 persists after first V8_RESULT_LIVE → kill
    kill_reason = [None]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=str(SCRIPTS_DIR))
        output_lines = []
        first_live_t = [None]       # wall-clock time when first V8_RESULT_LIVE seen
        last_live_closes = [None]   # closes= value from most recent V8_RESULT_LIVE
        last_alive_t = [time.time()]  # tracks last heartbeat or result line
        def _reader():
            for line in iter(proc.stdout.readline, ''):
                output_lines.append(line)
                if 'V8_HEARTBEAT:' in line or 'V8_RESULT_LIVE:' in line:
                    last_alive_t[0] = time.time()
                if 'V8_RESULT_LIVE:' in line:
                    mc = re.search(r'closes=(\d+)', line)
                    if mc:
                        n = int(mc.group(1))
                        if first_live_t[0] is None:
                            first_live_t[0] = time.time()
                        last_live_closes[0] = n
        t_reader = threading.Thread(target=_reader, daemon=True)
        t_reader.start()
        while proc.poll() is None:
            now = time.time()
            elapsed_now = now - t0
            # Kill if no heartbeat/result in HEARTBEAT_TIMEOUT seconds (OOM or crash)
            if now - last_alive_t[0] > HEARTBEAT_TIMEOUT and first_live_t[0] is None:
                proc.kill()
                kill_reason[0] = f"no_heartbeat_after_{HEARTBEAT_TIMEOUT}s"
                break
            # Kill if zero closes persist after first report
            if first_live_t[0] is not None and last_live_closes[0] == 0:
                if now - first_live_t[0] > ZERO_TRADES_TIMEOUT:
                    proc.kill()
                    kill_reason[0] = f"zero_trades_for_{ZERO_TRADES_TIMEOUT}s"
                    break
            time.sleep(0.5)
        proc.wait()
        t_reader.join(timeout=5)
        elapsed = time.time() - t0
        output = ''.join(output_lines)
        m = re.search(r"V8_RESULT:\s+sharpe=([-\d.]+)\s+pnl=([-\d.]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)(?:\s+total_pnl_dollars=([-\d.]+))?(?:\s+avg_pnl=([-\d.]+))?(?:\s+avg_pos_value=([-\d.]+))?", output)
        if m and int(m.group(3)) > 0:
            result = {
                "run_id": run_id, "name": name, "config": cfg,
                "sharpe": float(m.group(1)), "pnl": float(m.group(2)),
                "trades": int(m.group(3)), "wins": int(m.group(4)), "losses": int(m.group(5)),
                "total_pnl_dollars": float(m.group(6) or 0), "avg_pnl": float(m.group(7) or 0),
                "avg_pos_value": float(m.group(8) or 0),
                "elapsed": round(elapsed, 1), "status": "ok",
            }
        else:
            status = "killed_" + kill_reason[0] if kill_reason[0] else "no_result"
            result = {"run_id": run_id, "name": name, "config": cfg, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "total_pnl_dollars": 0.0, "avg_pnl": 0.0, "avg_pos_value": 0.0, "elapsed": round(elapsed, 1), "status": status, "error": output[-500:]}
    except Exception as e:
        elapsed = time.time() - t0
        result = {"run_id": run_id, "name": name, "config": cfg, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "elapsed": round(elapsed, 1), "status": "error", "error": str(e)}
    override_path.unlink(missing_ok=True)
    return result


# ═══════════════════════════════════════════════════════════════
# RESULTS + REPORTING
# ═══════════════════════════════════════════════════════════════
def write_results(results: List[Dict], csv_path: Path):
    if not results:
        return
    fieldnames = ["run_id", "name", "sharpe", "pnl", "trades", "wins", "losses", "elapsed", "status"] + [f"cfg_{k}" for k in results[0]["config"].keys()]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in sorted(results, key=lambda x: -x.get("sharpe", 0)):
            row = {k: v for k, v in r.items() if k != "config"}
            for k, v in r.get("config", {}).items():
                row[f"cfg_{k}"] = json.dumps(v) if isinstance(v, list) else v
            w.writerow(row)


def print_report(results: List[Dict], total: int, t_start: float, mode: str):
    done = len(results)
    ok = [r for r in results if r.get("status") == "ok" and r.get("trades", 0) > 0]
    elapsed = time.time() - t_start
    eta = (elapsed / done * (total - done)) if done > 0 else 0
    print(f"\n{'='*70}")
    print(f"  V8 SWEEP REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Mode: {mode} | Progress: {done}/{total} ({done*100//total}%) | ETA: {eta/3600:.1f}h")
    print(f"  Configs with trades: {len(ok)}/{done}")
    if ok:
        best = sorted(ok, key=lambda x: -x["sharpe"])[:10]
        print(f"\n  TOP 10 BY SHARPE:")
        print(f"  {'#':<3} {'Sharpe':>8} {'PnL%':>8} {'Trades':>7} {'WR%':>6}  Config")
        for i, r in enumerate(best):
            wr = r["wins"] / max(1, r["wins"] + r["losses"]) * 100
            print(f"  {i+1:<3} {r['sharpe']:>8.3f} {r['pnl']:>8.2f} {r['trades']:>7} {wr:>5.1f}%  {r['name'][:60]}")
        worst = sorted(ok, key=lambda x: x["sharpe"])[:3]
        print(f"\n  BOTTOM 3:")
        for r in worst:
            wr = r["wins"] / max(1, r["wins"] + r["losses"]) * 100
            print(f"  {r['sharpe']:>8.3f} {r['pnl']:>8.2f} {r['trades']:>7} {wr:>5.1f}%  {r['name'][:60]}")
    print(f"{'='*70}\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="V8 Sweep — DC/WT all-TF, NO_LOSS abolished")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="tradier")
    parser.add_argument("--account", type=str, default="")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--tier", type=int, default=1, help="1=core, 2=DC deep, 3=MI full, 3f=MI focused, 4=full grid, 5=master traders, 6=MFI2+gap, 7=trader intel, 8=congress sizing, 9=MI stocks, 9f=MI stocks focused, 11=stdev breakout, 11f=stdev focused")
    parser.add_argument("--tier-name", type=str, default="", help="Named tier override (e.g. '3f' for focused MI)")
    parser.add_argument("--resume", action="store_true", help="Skip already-completed run_ids")
    parser.add_argument("--npz-dir", type=str, default="", help="Explicit NPZ directory (passed to engine)")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbol filter (empty=all, 'fast'=12 representative symbols)")
    parser.add_argument("--report-interval", type=int, default=3600, help="Report every N seconds (default 3600=1h)")
    args = parser.parse_args()

    account = args.account or ("ang" if args.mode == "crypto" else "trb")
    _tier_name = args.tier_name or str(args.tier)

    if args.mode == "crypto":
        if args.tier == 30: param_grid = None  # Tier 30 uses fixed configs
        elif args.tier == 25: param_grid = CRYPTO_TIER25  # production ablation sweep (mirrors tradier t25)
        elif args.tier == 26: param_grid = CRYPTO_TIER26  # reentry rally gate sweep (k15m cap + HTF min)
        elif _tier_name == "15b": param_grid = CRYPTO_TIER15B  # SRS cascade knob sweep
        elif _tier_name == "11f": param_grid = CRYPTO_TIER11F
        elif _tier_name == "3f": param_grid = CRYPTO_TIER3F
        elif args.tier == 17: param_grid = None  # Tier 17 uses fixed configs (exit ablation)
        elif args.tier == 15: param_grid = CRYPTO_TIER15  # structural range shift TF comparison (dc_1h/4h/D, bb_1h/4h/D)
        elif args.tier == 14: param_grid = CRYPTO_TIER14  # structural range shift exit (hold losers, cut at DC boundary)
        elif args.tier == 13: param_grid = CRYPTO_TIER13  # entry selectivity sweep (push WR ≥ 90%)
        elif args.tier == 12: param_grid = CRYPTO_TIER12  # techexit + reentry sweep
        elif args.tier == 11: param_grid = CRYPTO_TIER11
        elif args.tier == 3: param_grid = CRYPTO_TIER3
        elif args.tier == 2: param_grid = CRYPTO_TIER2
        else: param_grid = CRYPTO_TIER1
    else:
        if _tier_name == "11f": param_grid = TRADIER_TIER11F
        elif _tier_name == "9f": param_grid = TRADIER_TIER9F
        elif args.tier == 11: param_grid = TRADIER_TIER11
        elif args.tier == 9: param_grid = TRADIER_TIER9
        elif args.tier == 1: param_grid = TRADIER_TIER1
        elif args.tier == 2: param_grid = TRADIER_TIER2
        elif args.tier == 3: param_grid = TRADIER_TIER3
        elif args.tier == 4: param_grid = TRADIER_TIER4
        elif args.tier == 5: param_grid = TRADIER_TIER5
        elif args.tier == 6: param_grid = TRADIER_TIER6
        elif args.tier == 7: param_grid = None  # Tier 7 uses fixed configs
        elif args.tier == 8: param_grid = TRADIER_TIER8
        elif args.tier == 9: param_grid = TRADIER_TIER9
        elif args.tier == 10: param_grid = TRADIER_TIER10
        elif args.tier == 16: param_grid = CRYPTO_TIER15  # stocks SRS TF comparison — same 6-way grid, tradier mode
        elif args.tier == 17: param_grid = None  # Tier 17 uses fixed configs (exit ablation stocks)
        elif args.tier == 20: param_grid = TRADIER_TIER20
        elif args.tier == 25: param_grid = TRADIER_TIER25
        elif args.tier == 26: param_grid = TRADIER_TIER26  # reentry rally gate sweep (k15m cap + HTF min)
        else: param_grid = TRADIER_TIER1

    if args.tier == 17 and args.mode == "crypto":
        configs = CRYPTO_TIER17_CONFIGS
    elif args.tier == 17 and args.mode == "tradier":
        configs = TRADIER_TIER17_CONFIGS
    elif args.tier == 30 and args.mode == "crypto":
        configs = CRYPTO_TIER30_CONFIGS
    elif args.tier == 11 and args.mode == "tradier":
        configs = TRADIER_TIER11_CONFIGS
    elif _tier_name == "11f" and args.mode == "tradier":
        configs = TRADIER_TIER11F_CONFIGS
    elif args.tier == 7 and args.mode == "tradier":
        configs = TRADIER_TIER7_CONFIGS
    elif args.tier == 5 and args.mode == "tradier":
        configs = TRADIER_TIER5_CONFIGS
    else:
        configs = build_configs(param_grid)
    total = len(configs)

    # Symbols filter: 'fast' → use curated 12-symbol set, 'core' → 4-symbol fast crypto, otherwise pass through
    symbols_filter = args.symbols
    if symbols_filter == "fast":
        symbols_filter = FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO
    elif symbols_filter == "core":
        symbols_filter = CORE_SYMBOLS_CRYPTO
    syms_tag = f"_fast" if args.symbols == "fast" else (f"_{len(symbols_filter.split(','))}sym_{hashlib.md5(symbols_filter.encode()).hexdigest()[:6]}" if symbols_filter else "")

    ts_tag = datetime.utcnow().strftime("%Y%m%d_%H%M")
    csv_path = SWEEP_DIR / f"v8_sweep_{args.mode}_t{args.tier}{syms_tag}_{ts_tag}.csv"
    progress_path = SWEEP_DIR / f"v8_sweep_{args.mode}_t{args.tier}{syms_tag}_progress.json"

    # Resume: load already-done run_ids
    done_ids: set = set()
    if args.resume and progress_path.exists():
        with open(progress_path) as f:
            prev = json.load(f)
        done_ids = {r["run_id"] for r in prev}
        logger.info(f"Resume: {len(done_ids)} already done")

    results: List[Dict] = []
    t_start = time.time()
    last_report = t_start
    report_interval = args.report_interval

    sym_desc = f" symbols={symbols_filter}" if symbols_filter else " symbols=ALL"
    logger.info(f"V8 Sweep: mode={args.mode} tier={args.tier} start={args.start} account={account}{sym_desc}")
    logger.info(f"Total configs: {total} | Workers: {args.workers} | CSV: {csv_path}")

    run_args = []
    for i, cfg in enumerate(configs):
        run_id = f"{args.mode}_t{args.tier}_{i:05d}"
        if run_id in done_ids:
            continue
        run_args.append((cfg, args.mode, account, args.start, args.capital, run_id, args.npz_dir, symbols_filter))

    logger.info(f"Configs to run: {len(run_args)} (skipping {len(done_ids)} done)")
    print_report(results, total, t_start, args.mode)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_config, a): a for a in run_args}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:
                r = {"run_id": "unknown", "name": "ERROR", "config": {}, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "elapsed": 0, "status": "error", "error": str(e)}
            results.append(r)
            # Save progress
            with open(progress_path, "w") as f:
                json.dump(results, f)
            write_results(results, csv_path)
            now = time.time()
            done = len(results)
            status = r.get("status", "?")
            sharpe = r.get("sharpe", 0.0)
            trades = r.get("trades", 0)
            logger.info(f"[{done}/{len(run_args)}] {r['name'][:60]} | sharpe={sharpe:.3f} trades={trades} status={status} {r['elapsed']:.0f}s")
            if now - last_report >= report_interval:
                print_report(results, total, t_start, args.mode)
                last_report = now

    print_report(results, total, t_start, args.mode)
    logger.info(f"Sweep complete. Results: {csv_path}")
    # Print top 20 final summary
    ok = [r for r in results if r.get("status") == "ok" and r.get("trades", 0) > 0]
    if ok:
        print(f"\nFINAL TOP 20 ({args.mode} tier-{args.tier}):")
        for i, r in enumerate(sorted(ok, key=lambda x: -x["sharpe"])[:20]):
            wr = r["wins"] / max(1, r["wins"] + r["losses"]) * 100
            print(f"  {i+1:>3}. sharpe={r['sharpe']:>7.3f}  pnl={r['pnl']:>8.2f}%  trades={r['trades']:>5}  wr={wr:.1f}%")
            print(f"       {r['name'][:100]}")


if __name__ == "__main__":
    main()
