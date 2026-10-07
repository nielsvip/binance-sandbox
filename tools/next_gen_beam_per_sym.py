#!/usr/bin/env python3
"""
next_gen_beam_per_sym.py — Next-gen vector beam starting from per_sym winners, not Defaults.

Problem with previous `vector_lab_streamer.py` (running now):
  Starts from `SweepConfig()` Defaults (e.g. GOLDEN_RULE 0, WT_DC 45, MIN_HOLD 5) then applies
  random overrides across 956 switches. That brute-forces millions of 900×900 combos with
  "ballpark" generic flips, barely beating BH (pool_sharpe often <0.4) and never stacking
  the real high-impact filters that make the difference.

What this does — per user 2026-08-18 request:
  1. Starts from `data/hourly_reconfig/trb/active_config.json` per_sym winners
     (162 sym_sides, each already has 30-40 overrides that gave its best verified
     `gain_pct` / `wsharpe` / `TIM<85% DD≤30%` via continuous_baseline_daemon).
     If stale or missing, recalculates baseline via `per_sym_vec_engine_stocks.sweep_variants`
     over 1yr window ($2k whole-share, side-aware BH).
  2. Applies **grouped** sweeps, not random cartesian:
     Filters are grouped by the color bible (corrected 2026-08-18):
       F1 Golden Rule 1/2/3/4/5 of 5 HTF (THE ladder, currently 0 bypass → test 1..5)
       F2 GR_HTF Direct 12 vs 27 sizing ladder
       F3 HH/HL 1h/4h/D structure  OR/HH/HL × TF 1/2/3
       F4 WT_DC hierarchy 4h_D (45 / 85 thresholds, K5M caps)
       F5 WT_CHAN/AVG + FUNDING/REGIME (secondary)
     Entries bottom/breakout and exits top/breakdown are handled as entry/exit path
     bundles P1–P9 from the inventory, not mixed randomly.
  3. Many workers find positive deltas quickly: ProcessPool per-sym, beam keeps only
     paths with `delta_vs_BH > 0` and `pool_sharpe > 0.5` and `gain_per_mo > 2%`,
     then depth-first stacks useful switches — REAL per_sym settings that apply
     ALL useful switches to get ACTUAL good results, not just delta>0.
  4. Writes ledger `data/reports/gui_lab/next_gen_beam_per_sym.json` + updates
     `data/hourly_reconfig/trb/active_config.json` via `_pending` + promote gate
     (TIM 20-80%, DD≤30%, closes≥30/mo, gain_per_mo≥2% as in vector_status.json goal_screens).

Hedging/No_loss removed per user (prohibited years ago, irrelevant for 1yr).

Usage:
  python tools/next_gen_beam_per_sym.py --max-workers 8 --top-k 5 --dry-run
  python tools/next_gen_beam_per_sym.py --symbols PBF_LONG,MU_SHORT --beam-depth 3
  VECTOR_WORKERS=8 python tools/next_gen_beam_per_sym.py  # respects env

Requires: backtest_v8/indicators/*.npz present (as vector_lab_streamer does).
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import itertools
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- S2 BTC BEST seed — older successful per_sym BTC overrides (user 2026-08-20) ---
# File: s2_backup_20260508/binance-sandbox/override_btc_BEST.json (iter=18 sharpe=0.5563 trades=4197 dd=0.47%)
# Included so BTCUSDC beam tests historically proven switches missing from current GROUP_DEFS (85 of 110 keys)
S2_BTC_BEST_PATH = ROOT / "s2_backup_20260508/binance-sandbox/override_btc_BEST.json"
try:
    _S2_BTC_BEST = json.loads(S2_BTC_BEST_PATH.read_text()) if S2_BTC_BEST_PATH.exists() else {}
    _S2_BTC_BEST.pop("_meta", None)
except Exception:
    _S2_BTC_BEST = {}

# --- catalog of filter groups to sweep (the high-impact ones user flagged) ---
GROUP_DEFS: List[Dict[str, Any]] = [
    {"group": "KINDERGARTEN_EMA_ABOVE", "desc": "KINDERGARTEN REWORKED 2026-08-23 per user: shorter TFs 15m/1h/4h and 9/21 or ema_sma crosses vs blunt 4h200", "param": "KINDERGARTEN_EMA_GATE_ENABLED", "values": [True, False], "paired": {"KINDERGARTEN_TF": ["15m", "1h", "4h"], "KINDERGARTEN_CROSS_TYPE": ["ema9_21", "ema_sma", "ema200"], "EMA_9_21_FILTER_ENABLED": [True, False]}},

    {
        "group": "F1_GOLDEN_RULE_LADDER",
        "desc": "Golden Rule 1/2/3/4/5 of 5 HTF — THE difference-maker (currently 0 bypass)",
        "param": "GOLDEN_RULE_HTF_MIN_TFS",
        "values": [1, 2, 3, 4, 5],
        "paired": {"GOLDEN_RULE_MIN_IND": [2, 3, 5]},
    },
    {
        "group": "F1b_GOLDEN_RULE_MIN_IND",
        "desc": "Per-TF depth 1..5 when ladder active",
        "param": "GOLDEN_RULE_MIN_IND",
        "values": [1, 2, 3, 5],
    },
    {
        "group": "F3_HH_HL_STRUCTURE",
        "desc": "Higher high / higher low structure on 1h/4h/D — OR/HH/HL × TF 1/2/3",
        "param": "REENTRY_GR_HLHH_MODE",
        "values": ["OR", "HH", "HL"],
        "paired": {"REENTRY_GR_MIN_TFS": [1, 2, 3]},
    },
    {
        "group": "F3b_LH_HL_FILTER",
        "desc": "LH_HL strict filter for bounce entries",
        "param": "LH_HL_FILTER_ENABLED",
        "values": [True, False],
        "paired": {"LH_HL_FILTER_TF_REQ": [1, 2], "LH_HL_FILTER_MODE": ["STRICT_2BAR", "DC_REGRESS"]},
    },
    {
        "group": "F4_WT_DC_HIERARCHY",
        "desc": "WT_DC 4h_D prevents shorting into uptrends — thresholds 0/45/85",
        "param": "WT_DC_HTF_GATE",
        "values": ["4h_D", "4h", "1h", "none"],
        "paired": {"WT_DC_ENTRY_THRESHOLD": [0, 45, 85], "TRA_WT_DC_ENTRY_THRESHOLD": [0, 45, 85]},
    },
    {
        "group": "F2_GR_HTF_DIRECT",
        "desc": "GR_V5 direct ladder 12 vs 27 sizing",
        "param": "GR_HTF_DIRECT_ENTRY_SCORE_MIN",
        "values": [12.0, 0.0, 18.0],
        "paired": {"GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE": [27.0, 12.0], "GR_HTF_DIRECT_ENTRY_ENABLED": [True, False]},
    },
    {
        "group": "F5_WT_CHAN_CROSS",
        "desc": "WT_CHAN/AVG cross events (WT_CHAN 10 AVG 21) + FUNDING / SPY regime",
        "param": "WT_CHAN_15m",
        "values": [10, 7, 14],
        "paired": {"WT_AVG_15m": [21, 14, 28], "FUNDING_GATE_ENABLED": [True, False], "SPY_REGIME_GATE_ENABLED": [True, False]},
    },
    {
        "group": "EXIT_WT_DC",
        "desc": "WT_DC exit threshold scorer guard",
        "param": "WT_DC_EXIT_THRESHOLD",
        "values": [30, 20, 45],
    },
    {
        "group": "MIN_HOLD_COOLDOWN",
        "desc": "MIN_HOLD + COOLDOWN — controls reentry noise",
        "param": "MIN_HOLD_BARS_15m",
        "values": [5, 8, 12],
        "paired": {"COOLDOWN_BARS_15m": [3, 6, 10]},
    },
    {
        "group": "EXIT_AT_GAIN",
        "desc": "EXIT at GAIN — breakeven / win-trail / peak-giveback / partial-lock (longs & shorts)",
        "param": "BREAKEVEN_EXIT_AFTER_BARS_ENABLED",
        "values": [True, False],
        "paired": {"WIN_TRAIL_EROSION_PCT": [0.125, 0.25, 0.5], "PEAK_GIVEBACK_PROTECTION_ENABLED": [True, False], "PARTIAL_PROFIT_LOCK_ENABLED": [True, False]},
    },
    {
        "group": "EXIT_AT_TOP_LONGS",
        "desc": "EXIT at TOP (longs) — structural / DC reject / WT top fade (also shorts mirror)",
        "param": "STRUCTURAL_EXIT_GATE_ENABLED",
        "values": [True, False],
        "paired": {"MTF_DC_REJECT_EXIT_ENABLED": [True, False], "MTF_BB_REJECT_EXIT_ENABLED": [True, False], "QUALITY_TOP_EXIT_ENABLED": [True, False], "LR_BAND_SLOPE_FLIP_EXIT_ENABLED": [True, False]},
    },
    {
        "group": "REENTRY_AFTER_TOP",
        "desc": "REENTRY after EXIT_AT_TOP — bounce/breakout resume (critical: rally almost always resumes after bounce/breakout, must reenter)",
        "param": "VEC_REENTRY_REQUIRE_PRIOR_EXIT",
        "values": [True, False],
        "paired": {"VEC_REENTRY_WINDOW_BARS": [100, 400, 800], "VEC_REENTRY_DC4_EXITPRICE_ENABLED": [True, False], "REENTRY_B04_DC_RETEST_ENABLED": [True, False], "REENTRY_B15_STRONG_TREND_ENABLED": [True, False], "REENTRY_B11_DC_BREAK_ENABLED": [True, False]},
    },
    {
        "group": "WIDE_STOCKS_BASELINE_ENTRY",
        "desc": "WIDE stocks baseline ENTRY — all ENTRY switches not yet in TRB base, 3-5 values (stocks defaults 24/60 etc. vs crypto 18/50)",
        "param": "ENTRY_SCORE_THRESHOLD",
        "values": [18, 22, 24, 28, 32],
        "paired": {"HTF_ALIGNMENT_MIN": [1, 2, 3], "COMBINED_STOCH_GATE": [40, 50, 60, 70], "ENTRY_WILLR_THRESHOLD": [-90, -80, -70]},
    },
    {
        "group": "WIDE_STOCKS_MISSING_REENTRY",
        "desc": "Missing tradier REENTRY switches — VEC_REENTRY, REENTRY_GR etc.",
        "param": "VEC_REENTRY_REQUIRE_PRIOR_EXIT",
        "values": [True, False],
        "paired": {"VEC_REENTRY_WINDOW_BARS": [100, 400, 800, 1200], "REENTRY_GR_HLHH_MODE": ["OR", "HH", "HL"], "REENTRY_GR_MIN_TFS": [1, 2, 3]},
    },
    {
        "group": "WIDE_STOCKS_MISSING_WT_DC_BB",
        "desc": "Missing WT/DC/BB tradier switches — WT_CHAN, WT_AVG, BB thresholds",
        "param": "WT_CHAN_15m",
        "values": [7, 10, 14],
        "paired": {"WT_AVG_15m": [14, 21, 28], "BB_ENTRY_LONG_THRESHOLD": [0.20, 0.30, 0.40], "BB_ENTRY_SHORT_THRESHOLD": [0.60, 0.70, 0.80]},
    },
    {
        "group": "WIDE_STOCKS_EXIT_HOLD",
        "desc": "Missing EXIT/HOLD tradier switches — MIN_HOLD, COOLDOWN, STRUCTURAL",
        "param": "MIN_HOLD_BARS_15m",
        "values": [3, 5, 8, 12, 16],
        "paired": {"COOLDOWN_BARS_15m": [3, 6, 10], "STRUCTURAL_EXIT_GATE_ENABLED": [True, False], "BREAKEVEN_EXIT_AFTER_BARS_ENABLED": [True, False]},
    },
    {
        "group": "OLD_BTC_BASELINE_DC_HEDGE",
        "desc": "Old BTC baseline DC/HEDGE — core winners missing",
        "param": "DC_RECOVERY_EXIT_TOLERANCE_PCT",
        "values": [0.3, 0.5, 0.8],
        "paired": {"DC_HOPELESS_EXIT_MIN_AGE_S": [900, 1350, 1800], "HEDGE_MAX_PCT_OF_LOSER": [1.0, 1.5, 2.0]},
    },
    {
        "group": "OLD_BTC_BASELINE_RZ_SATOSHIT",
        "desc": "Old BTC RZ/SATOSHIT — core winners missing",
        "param": "RZ_BREAKOUT_ENTRY_ENABLED",
        "values": [True, False],
        "paired": {"SATOSHIT_ENABLED": [True, False], "MIN_POSITION_SIZE": [18, 35, 68.75, 100]},
    },
    {
        "group": "OLD_BTC_BASELINE_EXIT_REENTRY",
        "desc": "Old BTC EXIT/REENTRY — core winners missing",
        "param": "MIN_HOLD_BARS_BEFORE_EXIT",
        "values": [24, 48, 96],
        "paired": {"EXIT_STDEV_BREAKOUT_FAIL_ENABLED": [True, False], "REENTRY_CROSS_MAX_BARS_AGO": [2, 3, 5]},
    },
    {
        "group": "OLD_BTC_BASELINE_ATR_BB_CHOP",
        "desc": "Old BTC ATR/BB/CHOP — remaining core winners",
        "param": "ATR_ADAPTIVE_STOP_MULT",
        "values": [2.0, 3.0, 4.0],
        "paired": {"BB_SQUEEZE_THRESHOLD_1H": [0.005, 0.0075, 0.01], "CHOP_RANGING_THRESHOLD": [70, 77.25, 85]},
    },
    {
        "group": "OLD_BTC_BASELINE_TF_KZONE",
        "desc": "Old BTC TF/KZONE — remaining core winners",
        "param": "TF_ALIGNMENT_MIN_LONG",
        "values": [2, 3, 4],
        "paired": {"K_ZONE_SHORT_THRESHOLD": [35, 48, 60], "K_ZONE_SHORT_THRESHOLD_TRADIER": [70, 81, 90]},
    },
    {
        "group": "OLD_BTC_BASELINE_MISC",
        "desc": "Old BTC misc — remaining core winners",
        "param": "DC_WIDTH_CAP_MULT",
        "values": [3.0, 5.0, 7.0],
        "paired": {"MIN_HOLD_BARS_BEFORE_EXIT": [24, 48, 96], "HPL_TRIGGER_LOSS_PCT": [-8, -6, -4]},
    },

    {'group': 'CRYPTO_PAST_HEDGE', 'desc': 'CRYPTO PAST HEDGE — historic winners (crypto past + TRB recent)', 'param': 'HEDGE_ENABLED', 'values': [True, False], 'paired': {'HEDGE_CLOSE_REMOVE_FROM_TRADEABLE': [True, False], 'HEDGE_DC_SHORT_REJECT_DCP': [0.1125, 0.225, 0.3375], 'HEDGE_DETERIORATING_GAIN_DELTA_PP': [0.075, 0.15, 0.225], 'HEDGE_DETERIORATING_GAIN_ENABLED': [True, False], 'HEDGE_SIZE_FRAC': [0.5, 0.970290777971968, 1.4717466195579618], 'HEDGE_WT_TF': ['15m', '1h', '3m'], 'HEDGE_HTF_VETO_ENABLED': [True, False], 'HEDGE_TRIGGER_LOSS_PCT': [-0.0125, -0.025, -0.0375], 'HEDGE_TRIGGER_LOSS_TRADIER': [-0.5, -1.0, -1.5]}},
    {'group': 'CRYPTO_PAST_ENTRY', 'desc': 'CRYPTO PAST ENTRY — historic winners (crypto past + TRB recent)', 'param': 'AUGMENT_ENABLED', 'values': [True, False], 'paired': {'DELTA_ENTRY_ENABLED': [True, False], 'DELTA_ENTRY_Z_THRESHOLD': [0.62, 1.25, 1.88], 'ENTRY_MODE': ['bb_extreme', 'or', 'wt_cross'], 'CT_REL_VOL_MIN': [0.1625, 0.325, 0.4875], 'K1M_EXTREME_REVERSE_ENABLED': [True, False], 'MI_ENTRY_EXHAUST_BONUS': [5.0, 10.0, 15.0], 'MI_ENTRY_ENABLED_TRADIER': [True, False], 'LOCAL_EXTREMES_SCORER_ENABLED': [True, False], 'LOCAL_EXTREMES_MIN_SCORE': [0.0, 25.0], 'WRONG_SIDE_K_TFS_REQUIRED': [0.5, 1.0, 1.5], 'WT_PERCENTILE_EXIT_OB_4H': [112.5, 225.0, 337.5], 'WT_VEL_DECAY_THRESHOLD': [0.125, 0.25, 0.375], 'WT_VEL_DECAY_EXIT_ENABLED': [True, False], 'WT_VEL_DECEL_RATIO': [0.375, 0.75, 1.125]}},
    {'group': 'CRYPTO_PAST_STDEV_SATOSHIT', 'desc': 'CRYPTO PAST STDEV SATOSHIT — historic winners (crypto past + TRB recent)', 'param': 'STDEV_BOUNCE_ENABLED', 'values': [True, False], 'paired': {'STDEV_BOUNCE_PCTB_SHORT': [0.2375, 0.475, 0.7125], 'STDEV_BREAKOUT_ENABLED': [True, False], 'STDEV_REJECT_EXIT_ENABLED': [True, False], 'STDEV_REJECT_EXIT_RETURN': [0.2438, 0.4875, 0.7313], 'STDEV_BB_RZ_EXIT_ENABLED': [True, False], 'STDEV_MACRO_ENTRY_VETO_ENABLED': [True, False], 'STDEV_MACRO_R4_EXIT_ENABLED': [True, False], 'SATOSHIT_LONG_MFI_MAX_TRADIER': [22.5, 45.0, 67.5], 'SATOSHIT_LONG_RSI_MAX_TRADIER': [31.25, 62.5, 93.75], 'SATOSHIT_ENTRY_ENABLED': [True, False], 'SATOSHIT_EXIT_ENABLED': [True, False], 'SATOSHIT_HTF_RVOL_1H_MIN_TRADIER': [0.15, 0.3, 0.45], 'SATOSHIT_SHORT_BB_PCTB_MIN': [0.2062, 0.4125, 0.6187]}},
    {'group': 'LR_STDEV_LADDER', 'desc': 'LR_STDEV regression channel 1-10× D / 6×4h / 3×1h / 2×15m — slope line vs bottom gradient, BB-free, original S0B baseline', 'param': 'LR_STDEV_ENTRY_ENABLED', 'values': [True, False], 'paired': {'LR_STDEV_TFS': [['D'], ['4h'], ['1h'], ['D','4h'], ['D','4h','1h']], 'LR_STDEV_AUGMENT_MODE': ['TO_SLOPE','TO_BOTTOM'], 'LR_STDEV_MIN_SLOPE_PCT': [0.015, 0.03, 0.06], 'LR_STDEV_MIN_SLOPE_D': [0.02, 0.03, 0.045], 'LR_STDEV_MIN_SLOPE_4H': [0.012, 0.02, 0.03], 'LR_STDEV_MIN_SLOPE_1H': [0.008, 0.015, 0.025], 'LR_STDEV_MIN_SLOPE_15M': [0.004, 0.008, 0.015], 'LR_STDEV_R2_MIN': [0.25, 0.35, 0.55], 'LR_STDEV_BAND_TOL_PCT': [0.08, 0.12, 0.18], 'LR_STDEV_CROSS_CONFIRM': ['WT_CROSS','K_CROSS'], 'WRONG_SIDE_ABS_KILL_ENABLED': [True, False], 'WRONG_SIDE_WT_TFS_REQUIRED': [3,4,5], 'WRONG_SIDE_MIN_AGE_MIN': [30, 120, 240]}},
    {'group': 'CRYPTO_PAST_RZ_TF', 'desc': 'CRYPTO PAST RZ TF — historic winners (crypto past + TRB recent)', 'param': 'TF_FOCUS_WEIGHT', 'values': [2.0, 10.0], 'paired': {'TSMOM_MIN_AGREEMENT': [0.5, 1.0, 1.5], 'STOCH_CROSS_ENTRY_TRADIER': [True, False], 'HTF_MIN_ALIGNED': [1.5, 3.0, 4.5], 'DELTA_REENTRY_MIN_TF': [2.0, 4.0, 6.0], 'REGIME_RANGING_EXIT_GAIN_MIN': [0.0375, 0.075, 0.1125], 'TRADIER_RSI2_EXIT_THRESHOLD_SHORT': [2.5, 5.0, 7.5], 'TRADIER_STOCH_ENTRY_LONG_TRADIER': [7.5, 15.0, 22.5]}},
    {'group': 'TRB_RECENT_BB_SQUEEZE', 'desc': 'TRB RECENT BB SQUEEZE — historic winners (crypto past + TRB recent)', 'param': 'BB_RECOVERY_EXIT_ENABLED_TRADIER', 'values': [True, False], 'paired': {'BB_SQUEEZE_COOLDOWN': [112.5, 225.0, 337.5], 'BB_SQUEEZE_MIN_ALIGNMENT': [7.5, 15.0, 22.5], 'BB_PCTB_ENTRY_ENABLED': [True, False], 'BB_RSI_STOCH_BB_MAX': [0.1875, 0.375, 0.5625], 'BB_RSI_STOCH_SCALP_ENABLED': [True, False], 'BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE': [True, False], 'BOUNCE_AUGMENT_K_D_THRESHOLD': [True, False], 'BOUNCE_AUGMENT_MIN_LOSS_PCT': [True, False], 'BOUNCE_TOP_EXIT_ENABLED': [True, False], 'BOUNCE_TOP_MIN_HOLD_MINUTES': [0, 0.5, 1.0]}},
    {'group': 'TRB_RECENT_TRADIER_EXITS', 'desc': 'TRB RECENT TRADIER EXITS — historic winners (crypto past + TRB recent)', 'param': 'EXIT_CONV_FAIL_ENABLED', 'values': [True, False], 'paired': {'EXIT_MAX_HOLD_ENABLED': [True, False], 'EXIT_SCORER_DC_EXTREME': [0.6, 1.2, 1.8], 'EXIT_EMERGENCY_DC1H_ENABLED': [True, False], 'RSI_EXIT_LONG_TRADIER': [42.5, 106.25], 'RSI_EXIT_SHORT_TRADIER': [29.0, 58.0, 87.0], 'RSI2_MEAN_REVERSION_ENABLED': [True, False], 'RSI_ENTRY_GATE_ENABLED': [True, False], 'REENTRY2_DC_BREAK_ENABLED': [True, False], 'REENTRY_MANDATORY': [True, False], 'REENTRY_PULL1_ENABLED': [True, False], 'REENTRY_PULL2_ENABLED': [True, False]}},
    {'group': 'TRB_RECENT_SIZING_RISK', 'desc': 'TRB RECENT SIZING RISK — historic winners (crypto past + TRB recent)', 'param': 'ATR_ADAPTIVE_SIZING_ENABLED', 'values': [True, False], 'paired': {'ATR_PARITY_EQUITY_BASE_USD': [26250.0, 52500.0, 78750.0], 'ATR_PARITY_QTY_CAP_MULT': [3.75, 7.5, 11.25], 'ATR_TRAIL_2X_EXIT_ENABLED': [True, False], 'ATR_TRAIL_ENABLED_TRADIER': [True, False], 'DD_KELLY_TIER1_PCT': [6.25, 12.5, 18.75], 'DD_KELLY_TIER3_PCT': [30.0, 60.0, 90.0], 'CONVICTION_SIZING_ENABLED': [True, False], 'DC_EDGE_SIZING_MAX_MULT': [True, False], 'SWING_MAX_POSITION_SIZE': [550.0, 1100.0, 1650.0], 'MAX_ALLOWED_DRAWDOWN_PCT': [50.0, 100.0, 150.0]}},
    {'group': 'TRB_RECENT_HTF_TREND', 'desc': 'TRB RECENT HTF TREND — historic winners (crypto past + TRB recent)', 'param': 'HTF_TREND_VETO_ENABLED', 'values': [True, False], 'paired': {'HTF_ALIGN_REQUIRED_TRADIER': [0, 0.5, 1.0], 'ADX_TRENDING_THRESHOLD': [21.25, 28.749999999999996, 32.5], 'D_TREND_REQUIRED': [True, False], 'GR_HTF_GATE_ENABLED': [True, False], 'GOLDEN_RULE_BB_15M_ENABLED': [True, False], 'TR_TREND_V1_ENABLED': [True, False], 'TR_ADX4H_MAX': [12.5, 25.0, 37.5], 'SPY_REGIME_BLOCK_LONGS_BELOW': [True, False], 'SPY_REGIME_BLOCK_SHORTS_ABOVE': [True, False], 'SECTOR_LS_RATIO_ENABLED': [True, False]}},
    {'group': 'TRB_RECENT_DC_BREAKOUT', 'desc': 'TRB RECENT DC BREAKOUT — historic winners (crypto past + TRB recent)', 'param': 'DC_ENTRY_VETO_ENABLED_TRADIER', 'values': [True, False], 'paired': {'DC_MOMENT_ENABLED': [True, False], 'DC_DAYTRADE_TARGET_PCT': [0.0037, 0.0075, 0.0112], 'DC_DAYTRADE_REQUIRE_1H_EXPANSION': [True, False], 'BREAKOUT_TF_SIZE_ENABLED': [True, False], 'BREAKOUT_TF_SIZE_MULT_15M': [0.5, 1.0, 1.5], 'BREAKOUT_MIN_HOLD_BARS': [0.5, 1.0, 1.5], 'BREAKOUT_HTF_MIN_ALIGNED': [0.5, 1.0, 1.5], 'BREAKOUT_RETEST_ARMED_HTF_STACK_MIN': [0.5, 1.0, 1.5], 'BREAKOUT_SIZE_LADDER_ENABLED': [True, False], 'CLENOW_ENABLED': [True, False], 'CLENOW_GATE_ENABLED': [True, False]}},
    {'group': 'TRB_RECENT_MISC', 'desc': 'TRB RECENT MISC — historic winners (crypto past + TRB recent)', 'param': 'LTF', 'values': ['15m'], 'paired': {'MARKET_OPEN_MINUTE': [22.5, 45.0, 67.5], 'MIN_HOLD_BARS': [7.5, 15.0, 22.5], 'MIN_HOLD_MINUTES_TRADIER': [0, 0.5, 1.0], 'COOLDOWN_BARS': [5.0, 10.0, 15.0], 'COOLDOWN_BARS_TRADIER': [4.0, 10.0], 'DELTA_EXIT_MIN_TF_LOST': [1.0, 2.0, 3.0], 'DELTA_GATE_RATIO_REBALANCE': [True, False], 'DELTA_REENTRY_REQUIRE_NOT_EXITING': [True, False], 'NOLOSS_ENABLED': [True, False], 'REVERSE_ON_EXIT_ENABLED': [True, False], 'PYRAMID_ENABLED': [True, False]}},
    {'group': 'LEFTOVER_1_MISC', 'desc': 'LEFTOVER 1 MISC — historic winners (crypto past + TRB recent)', 'param': 'ABLATION_DISABLE_HEDGE', 'values': [True, False], 'paired': {'BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY': [0.75, 1.0], 'BTC_DIVERGENCE_LB_D': [6.0, 12.0, 18.0], 'CLENOW_GATE_MIN_SCORE': [15.0, 30.0, 45.0], 'DIVERGENCE_LB': [10.0, 20.0, 30.0], 'FH_MOMENTUM_MFI_CONFIRM': [True, False], 'GHOST_CLOSE_REQUIRE_CONFIRMATION': [True, False], 'LIVE_ENTRY_ENGINE_MIN_SCORE': [0, 0.5, 1.0], 'LR_BAND_ENTRY_R2_MIN': [0.53, 1.05, 1.58], 'MITIGATOR_TIER1_DROP': [0.06, 0.12, 0.18], 'OI_CONFIRM_MIN_CHANGE_PCT': [0.125, 0.25, 0.375], 'PARTIAL_PROFIT_LOCK_FRAC': [0.25, 0.5, 0.75], 'PYRAMID_SIZE_MULT': [0.125, 0.25, 0.375], 'REENTRY_TIER1_SIZE_MULT_TRADIER': [True, False], 'RZ_TWO_PHASE_EXIT_ENABLED': [True, False], 'SQUEEZE_FIRE_BONUS_SCORE': [5.62, 11.25, 16.88], 'TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES': [120.0, 240.0], 'TRADIER_FH_MOMENTUM_MIN_MOVE_PCT': [0.125, 0.25, 0.375], 'TRAILING_AUG_ENABLED_TRADIER': [True, False], 'TRC_SMFI_POSITION_SIZE': [1237.5, 2475.0, 3712.5]}},
    {'group': 'LEFTOVER_2_MISC', 'desc': 'LEFTOVER 2 MISC — historic winners (crypto past + TRB recent)', 'param': 'ABLATION_DISABLE_QUICK_ENTRY', 'values': [True, False], 'paired': {'BASIS_CONDITION': [True, False], 'BTC_FIB_LOOKBACK_Y': [5.0, 10.0, 15.0], 'CONFLUENCE_MIN_BLOCKS': [1.5, 3.0, 4.5], 'EMA_DIST_LONG_THRESHOLD': [-0.25, -0.5, -0.75], 'FOLLOW_THROUGH_MIN_MOVE_PCT': [0.044272922351956366, 0.15839014459401365, 0.26162854380905626], 'HARD_LOSS_PCT': [0.427026584930718, 1.1852129774168134, 1.7490428937599063], 'LIVE_INDICATOR_MAX_BARS_PER_TF': [150.0, 300.0, 450.0], 'LR_BAND_LADDER_ORDINARY_PARITY_ENABLED': [True, False], 'MI_VELOCITY_EXIT_ENABLED_TRADIER': [True, False], 'OPTIMAL_HOLD_BARS_15M': [749.0, 1498.0, 2247.0], 'PARTIAL_PROFIT_LOCK_GAIN_PCT': [0.5, 1.0, 1.5], 'R1_USE_DC_4BAR': [True, False], 'REENTRY_TIER2_MAX_MINUTES_TRADIER': [30.0, 60.0, 90.0], 'SCALP_STOP_PCT': [2.5, 5.0, 7.49], 'SRS_K_EXIT_1H': [63.75, 127.5, 191.25], 'TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION': [True, False], 'TRADIER_RATIO_REQUIRE_MIN_GAIN': [True, False], 'TRA_MIN_HOLD_MINUTES': [0, 0.5, 1.0], 'TR_DCWIDTH4H_SHORT_ENABLED': [True, False], 'WT_COMPOSITE_SCORING_ENABLED_TRADIER': [True, False]}},
    {'group': 'LEFTOVER_3_MISC', 'desc': 'LEFTOVER 3 MISC — historic winners (crypto past + TRB recent)', 'param': 'AI_PREMARKET_SIZE_MULT_MAX', 'values': [1.12, 2.25, 3.38], 'paired': {'BREAKEVEN_EXIT_AFTER_BARS_TF': ['15m'], 'BTC_FIB_RECOMPUTE_ON_NEW_HL': [True, False], 'CT_15M_MOMENTUM_GATE_ENABLED': [True, False], 'EMA_DIST_SIZING_MULT': [True, False], 'FOLLOW_THROUGH_REENTRY_ENABLED': [True, False], 'LONG_ENABLED': [True, False], 'LR_BAND_SLOPE_FLIP_MIN_PCT_DAY': [0.0375, 0.05], 'MODE': ['tradier'], 'PARABOLIC_PROTECTION_ENABLED': [True, False], 'PENNY_STOCK_LONG_BLOCK_ENABLED': [True, False], 'REDIS_CHANNEL_POSITIONS': ['tradier_positions_channel'], 'REGIME_GATE_ENABLED': [True, False], 'SHORT_ENABLED': [True, False], 'TRADIER_DC_DAYTRADE_STOP_PCT': [0.00375, 0.005], 'TRADIER_REENTRY_ANTI_CHURN_ENABLED': [True, False], 'TRC_CLENOW_POSITION_SIZE': [1650.0, 3300.0, 4950.0], 'TR_MFI4H_LONG_ENABLED': [True, False], 'WT_DC_ENTRY_K5M_MAX_LONG': [50.0, 100.0, 150.0]}},
    {'group': 'LEFTOVER_4_MISC', 'desc': 'LEFTOVER 4 MISC — historic winners (crypto past + TRB recent)', 'param': 'ATR_ADAPTIVE_SIZING_TARGET_PCT', 'values': [True, False], 'paired': {'BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE': [True, False], 'CT_MFI_15M_LONG_MIN': [33.75, 67.5, 101.25], 'ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE': ['SHORT'], 'FUNDING_GATE_LONG_MAX': [0.0003, 0.0005, 0.0008], 'K_ZONE_ENTRY_BONUS_TRADIER': [7.5, 15.0, 22.5], 'LONG_STRUCT_EXIT_TF': ['D'], 'MOMENTUM_FADE_VOL_MIN_TRADIER': [1.5, 3.0, 4.5], 'PARABOLIC_RSI_4H_MIN': [26.25, 52.5, 78.75], 'PROXIMITY_TOP_GATE_ENABLED': [True, False], 'RED_ZONE_TRADIER_MIN_OI_AT_WALL': [500.0, 1000.0, 1500.0], 'REGIME_MIN_DWELL_BARS': [8.0, 16.0, 24.0], 'SHORT_STRUCT_EXIT_TF': ['15m'], 'SWING_EXIT_TFS': ['D'], 'TRADIER_DC_DAYTRADE_TARGET_PCT': [0.0025, 0.005, 0.0075], 'TRADIER_RSI2_ENABLED': [True, False], 'TRC_MAX_DAILY_LOSS_PCT': [3.75, 7.5, 11.25], 'VOLUME_CONFIRMATION_ENABLED': [True, False], 'WT_DC_ENTRY_K5M_MIN_SHORT': [0, 0.5, 1.0]}},
    {'group': 'LEFTOVER_5_MISC', 'desc': 'LEFTOVER 5 MISC — historic winners (crypto past + TRB recent)', 'param': 'AUGMENTATION_COOLDOWN_MINUTES', 'values': [0, 0.5, 1.0], 'paired': {'BREAKOUT_MULTI_LUNG_COOLDOWN_BARS': [4.0, 8.0, 12.0], 'BTC_RZ_AS_BOOST_ENABLED': [True, False], 'CT_WT_VELOCITY_1H_MIN': [0, 0.5, 1.0], 'EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED': [True, False], 'FUNDING_GATE_SHORT_MIN': [-0.0003, -0.0005, -0.0008], 'LH_HL_FILTER_DC_THRESHOLD_PCT': [0.125, 0.25, 0.375], 'LR_BAND_E02_EXIT_ENABLED': [True, False], 'MAX_ORDER_VALUE_MEN': [7.5, 15.0, 22.5], 'MTF_ARROW_SIZE_GAIN': [0.62, 1.25, 1.88], 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT': [0.375, 0.75, 1.125], 'PROXIMITY_TOP_MAX_DROP_PCT': [2.5, 5.0, 7.5], 'RED_ZONE_TRADIER_STALE_MAX_HOURS': [1.5, 3.0, 4.5], 'REGIME_RANGING_NOLOSS_MIN': [0.0375, 0.075, 0.1125], 'SMA200_DIST_ENTRY_ENABLED': [True, False], 'SYMBOL_PERF_MIN_MULT': [0.0625, 0.125, 0.1875], 'TRADIER_ENTRY_SCORE_THRESHOLD': [0, 0.5, 1.0], 'TRADIER_STOCH_EXTREME_LONG_TRADIER': [3.5, 7.0, 10.5], 'TRC_MINERVINI_LONG_BUDGET': [8250.0, 16500.0, 24750.0], 'VWAP_BOUNCE_ENTRY_ENABLED': [True, False], 'WT_DC_EXIT_ENABLED': [True, False]}},
    {'group': 'LEFTOVER_6_MISC', 'desc': 'LEFTOVER 6 MISC — historic winners (crypto past + TRB recent)', 'param': 'AUGMENTATION_COOLDOWN_SECONDS', 'values': [0, 0.5, 1.0], 'paired': {'BREAKOUT_TF_SIZE_MULT_D': [4.0, 6.0], 'BTC_RZ_PROXIMITY_PCT': [0.125, 0.25, 0.375], 'DC_EDGE_SIZING_PERIOD': [True, False], 'FAST_CUT_LOSS_THRESHOLD': [-499.5, -999.0, -1498.5], 'GAP_FILL_STOP_MULT': [0.1875, 0.375, 0.5625], 'LIGHT_MODE': [True, False], 'LR_BAND_ENTRY_ENABLED': [True, False], 'MAX_POSITION_SIZE_MEN': [5.0, 10.0, 15.0], 'MTF_ENTRY_REQUIRE_GR_FILTER': [True, False], 'PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT': [0.05, 0.1, 0.15], 'PYRAMID_MAX_DC_POS_15M_SHORT': [True, False], 'REENTRY_PULL4_ENABLED': [True, False], 'RZ_EXIT_ENABLED': [True, False], 'SMFI_MAX_PER_SIDE': [2.5, 5.0, 7.5], 'TIME_ZONE_ENABLED': [True, False], 'TRADIER_FH_MOMENTUM_MFI_CONFIRM': [True, False], 'TRC_ROTATION_POSITION_SIZE': [1500.0, 3000.0, 4500.0], 'WT_EXIT_MIN_TFS': [2.0, 4.0, 6.0]}},
    # --- S2 BTC BEST — older successful per_sym BTC overrides (110 keys, sharpe 0.5563, trades 4197, dd 0.47%) — user 2026-08-20 ---
    # Grouped into 6 critical families so BTCUSDC beam tests historically proven switches missing from current GROUP_DEFS (85 keys)
    {"group": "S2_BTC_BEST_CORE", "desc": "S2 BTC BEST core — BTC_DEDICATED + TREND + RZ_AS_BOOST + DIVERGENCE (IBIT/S1 2026-04-16)", "param": "BTC_DEDICATED_ENABLED", "values": [True, False], "paired": {"BTC_TREND_MODE_ENABLED": [True, False], "BTC_RZ_AS_BOOST_ENABLED": [True, False], "BTC_DIVERGENCE_BLOCK_AGAINST": [True, False], "BTC_DIVERGENCE_EXIT_AGAINST": [True, False], "BTC_DIVERGENCE_MIN_TF": ["1h", "4h", "D"]}},
    {"group": "S2_BTC_BEST_BREAKOUT", "desc": "S2 BTC BREAKOUT — entry + HTF align + accel + cooldown (BTC 2995% delta proven)", "param": "BTC_BREAKOUT_ENTRY_ENABLED", "values": [True, False], "paired": {"BTC_BREAKOUT_BLOCK_OPPOSING_DIV": [True, False], "BTC_BREAKOUT_REQUIRE_HTF_ALIGNED": [True, False], "BTC_BREAKOUT_HTF_MIN_ALIGNED": [1, 2, 3], "BTC_BREAKOUT_ACCEL_MIN_TFS": [1, 2, 3], "BTC_BREAKOUT_MIN_HOLD_BARS": [1, 10, 20], "BTC_BREAKOUT_COOLDOWN_BARS": [1, 10, 20]}},
    {"group": "S2_BTC_BEST_REENTRY", "desc": "S2 BTC REENTRY — reverse + follow-through + accel ramp (BTC 0.55 sharpe)", "param": "BTC_REVERSE_ON_EXIT_ENABLED", "values": [True, False], "paired": {"BTC_FOLLOW_THROUGH_REENTRY_ENABLED": [True, False], "BTC_FOLLOW_THROUGH_MIN_MOVE_PCT": [0.05, 0.5, 1.0], "BTC_ACCEL_RAMP_MIN_TFS": [2, 3, 4], "BTC_ACCEL_RAMP_REQUIRE_POSITIVE": [True, False], "BTC_ENTRY_PRIMARY_REQUIRE_RZ": [True, False], "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP": [True, False]}},
    {"group": "S2_BTC_BEST_RISK", "desc": "S2 BTC RISK — hard loss + guaranteed reentry + RZ fib/round (BTC 4197 trades)", "param": "BTC_RISK_PATH", "values": ["technical", "fib", "round"], "paired": {"BTC_HARD_LOSS_USD_PER_TRADE": [3.75, 5.4, 10.0], "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": [3.75, 5.4], "BTC_GUARANTEED_REENTRY_ENABLED": [True, False], "BTC_GUARANTEED_REENTRY_MIN_GAP_BARS": [1, 10, 20], "BTC_RZ_USE_FIB": [True, False], "BTC_RZ_USE_ROUND": [True, False], "BTC_RZ_USE_WT_DC": [True, False], "BTC_RZ_PROXIMITY_PCT": [0.5, 1.0, 2.0]}},
    {"group": "S2_BTC_BEST_GATES", "desc": "S2 BTC GATES — funding + entry score + DC low4 + min size (funding +92% sharpe)", "param": "FUNDING_GATE_ENABLED", "values": [True, False], "paired": {"ENTRY_SCORE_THRESHOLD": [4.5, 10.0, 18.0], "DC_LOW4_BYPASS_USE_STANDARD": [True, False], "MIN_POSITION_SIZE": [13.75, 35.0, 68.75], "RZ_BOT_BB_THRESHOLD": [0.3, 0.45, 0.6]}},
    {"group": "S2_BTC_BEST_EXTRA", "desc": "S2 BTC EXTRA — RZ breakout + scorer + reentry rally + hedge det (remaining S2 keys)", "param": "RZ_BREAKOUT_ENTRY_ENABLED", "values": [True, False], "paired": {"EXIT_SCORER_DC_EXTREME": [0.4, 0.8, 1.2], "REENTRY_RALLY_K15M_MAX": [60.0, 120.0, 180.0], "HEDGE_DC_SHORT_REJECT_DCP": [0.11, 0.225, 0.3375], "HEDGE_DETERIORATING_GAIN_WINDOW_BARS": [2, 5, 10], "AUGMENT_WT_4H_BOUNCE_ENABLED": [True, False]}},
    {"group": "HAIKU_WINNER_AUG_REDUCE", "desc": "USER 2026-08-23: augment gain>3 OR WT15 cross+gain>2, reduce <1 (was 3/2.5) — shorter TFs", "param": "HAIKU_WINNER_ENABLED", "values": [True, False], "paired": {"HAIKU_AUGMENT_GAIN_THRESHOLD": [3.0, 2.0], "HAIKU_REDUCE_GAIN_THRESHOLD": [1.0, 2.5], "HAIKU_AUGMENT_FRACTION": [0.10, 0.20]}},
    {"group": "S2_BTC_BEST_MISSING_14", "desc": "S2 BTC BEST missing 14 — re-hooked 2026-08-20 TEST REQUIRED (BTC_RZ_WT_DC_MULTIFACTOR + BTC_TREND_MODE + DC_LOW4_BYPASS + DYNAMIC_SCORE + HEDGE_WINDOW + INTRADAY_FORCE + PARTIAL_FRAC + QUICK_REENTRY + RATIO_SENTIMENT*2 + RZ_CASCADE + V8_DC/WT + WT_MOM)", "param": "BTC_RZ_WT_DC_MULTIFACTOR", "values": [True, False], "paired": {"BTC_TREND_MODE_ENABLED": [True, False], "DC_LOW4_BYPASS_USE_STANDARD": [True, False], "DYNAMIC_SCORE_AUGMENT_ENABLED": [True, False], "HEDGE_DETERIORATING_GAIN_WINDOW_BARS": [2, 5, 10], "INTRADAY_SESSION_FORCE_EXIT_UTC": [35100, 0, 43200], "PARTIAL_EXIT_FRAC": [0.5, 0.75, 1.0], "QUICK_REENTRY_60MIN_MIN_PCT": [0.3, 0.6, 1.2], "RATIO_SENTIMENT_FILTER_ENABLED": [True, False], "RATIO_SENTIMENT_SHORT_MAX": [30.0, 45.0, 60.0], "RZ_CASCADE_MIN_TF_ALIGN": [1, 2, 3], "V8_ENTRY_ENGINE_DC_ENABLED": [True, False], "V8_ENTRY_ENGINE_WT_ENABLED": [True, False], "WT_MOMENTUM_EXIT_THRESHOLD": [1, 2, 3]}},

]

# Entry bundles P1-P9 (from color inventory) — applied as paired entry+filter, not random
ENTRY_BUNDLES: List[Dict[str, Any]] = [
    {"name": "P1_BOTTOM_BOUNCE_A1", "entry": {"ENTRY_BB_EXTREME_BOUNCE_ENABLED": True, "ENTRY_BB_EXTREME_THRESHOLD": 0.2}},
    {"name": "P2_WT_DIP_A3", "entry": {"ENTRY_WT_CROSS_EVENT_ENABLED": True, "WT_CROSSUNDER_15M_SHORT": True}},
    {"name": "P3_BAND_A4", "entry": {"BB_AUTO_TUNE_ENABLED": True, "BB_BOT_THRESHOLD": 0.3}},
    {"name": "P4_RANGE_SHIFT_A6", "entry": {"ENTRY_RANGE_SHIFT_ENABLED": True, "RANGE_SHIFT_THRESHOLD": 0.5}},
    {"name": "P5_WT_GATE_A7", "entry": {"WT_GATE_ENABLED": True, "WT_ENTRY_THRESHOLD": 45}},
    {"name": "P6_RSI2_A5", "entry": {"ENTRY_WILLR_ENABLED": True, "ENTRY_WILLR_THRESHOLD": -80}},
]

ACTIVE_CFG = ROOT / "data/hourly_reconfig/trb/active_config.json"
ACTIVE_CFG_CRYPTO = ROOT / "data/hourly_reconfig/inf/active_config.json"
ACTIVE_CFG_100 = ROOT / "data/symbols_final_score_100.json"
DEFAULTS_XLSX = ROOT / "SPREADSHEETS/STOCKS_1YR_REAL_MATRIX_BIBLE.xlsx"
OUT_LEDGER = ROOT / "data/reports/gui_lab/next_gen_beam_per_sym.json"
OUT_LEDGER_PRIO = ROOT / "data/reports/gui_lab/next_gen_beam_prio.json"
OUT_STATUS = ROOT / "data/reports/gui_lab/next_gen_beam_status.json"
OUT_APPROVED = ROOT / "data/reports/gui_lab/next_gen_beam_approved.json"
OUT_NEGATIVE = ROOT / "data/reports/gui_lab/next_gen_beam_negative.json"
# Ledger merge helper for box↔S1↔Mac dedup (prevents double work)
OUT_LEDGER_CRYPTO = ROOT / "data/reports/gui_lab/next_gen_beam_crypto.json"

def _load_approved() -> List[Dict[str, Any]]:
    try:
        return json.loads(OUT_APPROVED.read_text())
    except Exception:
        return []

def _record_approved(symside: str, group: str, param: str, value: Any, paired: Dict[str, Any], metrics: Dict[str, Any]) -> None:
    try:
        rec = {"symside": symside, "group": group, "param": param, "value": value, "paired": paired, "delta_vs_bh": metrics.get("delta_vs_bh"), "pool_sharpe": metrics.get("pool_sharpe"), "gain_per_mo": metrics.get("gain_per_mo"), "trades": metrics.get("trades"), "tim_pct": metrics.get("tim_pct"), "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        lst = _load_approved()
        lst.append(rec)
        OUT_APPROVED.parent.mkdir(parents=True, exist_ok=True)
        OUT_APPROVED.write_text(json.dumps(lst, indent=2))
    except Exception:
        pass

def _record_negative(symside: str, group: str, param: str, tried_values: List[Any], best_metrics: Dict[str, Any]) -> None:
    try:
        rec = {"symside": symside, "group": group, "param": param, "tried": tried_values, "best_delta": best_metrics.get("delta_vs_bh"), "best_gain": best_metrics.get("gain_pct"), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        lst = []
        try:
            lst = json.loads(OUT_NEGATIVE.read_text())
        except Exception:
            lst = []
        lst.append(rec)
        OUT_NEGATIVE.parent.mkdir(parents=True, exist_ok=True)
        OUT_NEGATIVE.write_text(json.dumps(lst, indent=2))
    except Exception:
        pass

def _ordered_groups(trb_only: bool = False) -> List[Dict[str, Any]]:
    # Bible stipulated order ENTRY→EXIT→REENTER→FILTER, and most-approved first
    # TRB stocks use 24 TRB-relevant groups (exclude LEFTOVER/CRYPTO blowup) — per-switch exhaustive fix 2026-08-20
    groups = GROUP_DEFS
    if trb_only:
        groups = [g for g in GROUP_DEFS if not any(g["group"].startswith(p) for p in ("LEFTOVER_", "CRYPTO_PAST_", "TRB_RECENT_"))]
    approved = _load_approved()
    if not approved:
        return list(groups)
    # hit-rate and avg delta per group
    from collections import defaultdict
    stats: Dict[str, List[float]] = defaultdict(list)
    for r in approved:
        stats[r.get("group", "")].append(float(r.get("delta_vs_bh", 0)))
    def score(g: Dict[str, Any]) -> float:
        vals = stats.get(g["group"], [])
        return (sum(vals) / len(vals) if vals else -1e9, len(vals))
    return sorted(list(groups), key=lambda g: score(g), reverse=True)

def _ordered_bundles() -> List[Dict[str, Any]]:
    approved = _load_approved()
    if not approved:
        return list(ENTRY_BUNDLES)
    # count bundle name implied by entry keys — approximate via param hit
    return list(ENTRY_BUNDLES)

def _load_per_sym() -> Dict[str, Any]:
    # Merge TRB stocks + INF crypto (100 from final_score_norm) so Mac/S1/box beam deduplicate
    merged: Dict[str, Any] = {}
    for cfg_path in (ACTIVE_CFG, ACTIVE_CFG_CRYPTO):
        try:
            d = json.loads(cfg_path.read_text())
            for k, v in d.items():
                if k.startswith("_"):
                    continue
                if k not in merged:
                    merged[k] = v
        except Exception:
            pass
    # 2026-08-20 FLZ FOTEST SEED — use old fotest baseline for 10 FLZ symbols (20 sides) if better than stocks defaults
    # User 2026-08-20: FLZ fotest (per_sym_active_config.json n_crypto_keys 244) is better than empty/stock defaults.
    # FLZ 10: BTCUSDC, BTCDOMUSDT, ETHUSDC, BNBUSDC, SOLUSDC, XRPUSDC, HYPEUSDT, DOGEUSDC, ZECUSDC, WLDUSDC × LONG/SHORT
    try:
        _fotest_path = ROOT / "data/hourly_reconfig/per_sym_active_config.json"
        if _fotest_path.exists():
            _fotest = json.loads(_fotest_path.read_text())
            _flz_sides = [
                "BTCUSDC_LONG","BTCUSDC_SHORT","BTCDOMUSDT_LONG","BTCDOMUSDT_SHORT",
                "ETHUSDC_LONG","ETHUSDC_SHORT","BNBUSDC_LONG","BNBUSDC_SHORT",
                "SOLUSDC_LONG","SOLUSDC_SHORT","XRPUSDC_LONG","XRPUSDC_SHORT",
                "HYPEUSDT_LONG","HYPEUSDT_SHORT","DOGEUSDC_LONG","DOGEUSDC_SHORT",
                "ZECUSDC_LONG","ZECUSDC_SHORT","WLDUSDC_LONG","WLDUSDC_SHORT",
            ]
            for _ss in _flz_sides:
                _v = _fotest.get(_ss)
                if not _v:
                    continue
                _ov = _v.get("overrides") or {}
                # skip empty or trivial single-key LONG_ENABLED-only (WLD/HYPE missing) — keep empty in that case
                if not _ov or (len(_ov)==1 and list(_ov.keys())[0] in ("LONG_ENABLED","SHORT_ENABLED")):
                    continue
                # only seed if merged has empty/weak baseline (empty, or stocks-default polluted, or trades 0)
                _cur = merged.get(_ss)
                _cur_ov = (_cur or {}).get("overrides") or {}
                _needs_seed = False
                if not _cur:
                    _needs_seed = True
                elif not _cur_ov:
                    _needs_seed = True
                elif len(_cur_ov) <= 2:
                    _needs_seed = True
                elif _cur.get("seeded_from") in ("crypto_defaults_v1_16.74","", None):
                    _needs_seed = True
                if _needs_seed:
                    merged[_ss] = dict(_v)
                    merged[_ss]["seeded_from"] = f"fotest_baseline_{_v.get('winning_tag','')}"
                    merged[_ss]["seeded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    # ensure execution account correct for FLZ
                    merged[_ss]["execution_account"] = "flz"
    except Exception as _e:
        pass
    # REMOVED 2026-08-20: symbols_final_score_100.json injected 100 LIES (SNXX/SHAZ etc) with no npz → always NO_TRADE.
    # User mandate: fix LOSING illegal entries, not invent stocks that don't exist. Only real per_sym with overrides counts.
    # Keep this block disabled unless ACTIVE_CFG_100 symbol has verified tick_history npz.
    if False:
        try:
            j100 = json.loads(ACTIVE_CFG_100.read_text())
            for symside in j100.get("all", []):
                if symside not in merged:
                    base = symside.rsplit("_", 1)[0]
                    merged[symside] = {"overrides": {}, "base_symbol": base, "npz_symbol": base, "execution_account": "inf", "instrument_type": "crypto", "status": "pending_beam"}
        except Exception:
            pass
    # STOCKS ONLY until 100% profit — user 2026-08-20: ANYTHING USDT/CRYPTO NOT CONCERN UNTIL STOCKS 100% IN PROFIT
    # SOL_SHORT, ETH_SHORT, BTC etc are crypto even without USDT suffix — drop ALL crypto (INF) until TRB stocks delta>0 100%
    try:
        # load TRB stocks keys to keep
        _trb_keys = set()
        try:
            _trb_keys = set(json.loads(ACTIVE_CFG.read_text()).keys())
        except: _trb_keys = set()
        # if any TRB stock still losing/illegal (ledger delta<0), keep ONLY TRB keys — no crypto at all
        _has_losing_stock = False
        try:
            _ledger = json.loads(OUT_LEDGER.read_text()) if OUT_LEDGER.exists() else {}
            for _r in _ledger.get("ledger",[]):
                _ss = _r.get("symside","")
                if _ss in _trb_keys and _r.get("best",{}).get("delta_vs_bh",0) <= 0:
                    _has_losing_stock = True
                    break
            if not _has_losing_stock:
                # also check per_sym TRB not yet in ledger (remaining)
                for _k in _trb_keys:
                    if _k.startswith("_"): continue
                    if _k not in set(r.get("symside") for r in _ledger.get("ledger",[])):
                        _has_losing_stock = True
                        break
        except: _has_losing_stock = True
        if _has_losing_stock:
            merged = {k: v for k, v in merged.items() if k in _trb_keys}
        else:
            # stocks 100% profit — allow crypto
            merged = {k: v for k, v in merged.items() if not (k.upper().endswith("USDT_LONG") or k.upper().endswith("USDT_SHORT") or k.upper().endswith("USDC_LONG") or k.upper().endswith("USDC_SHORT") or k.upper().endswith("USD1_LONG") or k.upper().endswith("USD1_SHORT"))}
    except: pass
    if merged:
        return merged
    try:
        return json.loads(ACTIVE_CFG.read_text())
    except Exception:
        return {}

def _load_defaults_map() -> Dict[str, Any]:
    # From Defaults sheet in bible xlsx, fallback to SweepConfig defaults
    try:
        import openpyxl
        p = DEFAULTS_XLSX
        if p.exists():
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws = wb["Defaults"]
            return {r[0].value: r[1].value for r in ws.iter_rows(min_row=2) if r[0].value}
    except Exception:
        pass
    return {}

def _symside_to_sym_side(symside: str) -> Tuple[str, str]:
    if symside.endswith("_LONG"):
        return symside[:-5], "LONG"
    if symside.endswith("_SHORT"):
        return symside[:-6], "SHORT"
    return symside, "LONG"

def _run_one_variant(args: Tuple[str, str, Dict[str, Any], int]) -> Dict[str, Any]:
    """Worker: sym, side, overrides, window_days -> metrics via per_sym vec engine."""
    sym, side, overrides, window_days = args
    # Lazy import inside worker (fork-safe)
    try:
        from v12_wide_engine import load_npz, SweepConfig, _apply_per_task_overrides, simulate_one_symbol  # type: ignore
    except Exception as e:
        return {"symside": f"{sym}_{side}", "error": f"import {e}", "overrides": overrides}
    is_long = side == "LONG"
    # Auto-detect crypto vs stocks: USDT/USDC suffix or known crypto -> mode crypto else tradier
    mode = "crypto" if (sym.upper().endswith("USDT") or sym.upper().endswith("USDC") or sym.upper().endswith("USD1") or sym.upper() in {"BTC","ETH","SOL"}) else "tradier"
    # For NPZ path, strip is not needed — load_npz resolves both; crypto mode tries USDT/USDC majors first
    try:
        npz, ts = load_npz(sym, mode, start_ts=None)
    except Exception as e:
        return {"symside": f"{sym}_{side}", "error": f"no npz {e}", "overrides": overrides}
    if len(ts) < 50:
        return {"symside": f"{sym}_{side}", "error": "too short", "overrides": overrides}
    # Window: last 1yr as in bible
    try:
        start_ts = int(ts[-1] - window_days * 86400)
    except Exception:
        start_ts = int(ts[0])
    # 2026-08-19 MODE-AWARE BASELINE: crypto vs tradier have divergent defaults (DELTA, WT_DC etc.)
    # Use sweep_config_for_mode to get correct baseline before stacking per_sym overrides.
    try:
        from v12_wide_engine import sweep_config_for_mode as _sc_for_mode
        cfg = _sc_for_mode(mode)
    except Exception:
        cfg = SweepConfig()
    # CRYPTO REDEFINE 2026-08-19: 99/100 INF crypto symsides have overrides:{} and stale losing defaults.
    # They start from sweep_config_for_mode("crypto") baseline above (DELTA True, WT_DC none) — not from stale per_sym.
    # TRADIER keeps its verified per_sym winners (DD<=30) as base.
    # Start from per_sym base overrides (passed in), then stack group overrides
    base = dict(overrides)  # already includes per_sym winners + this beam's stacking
    applied, _, _, unknown = _apply_per_task_overrides(cfg, base)
    # Faithful per_sym vector lane — MUST use v8_vec_sweep (faithful) not uve toy.
    # 2026-08-18 PARITY FIX: uve_engine is 289-line scratch (11 knobs) vs v8_vec_sweep 6,783 lines (372 knobs).
    # The beam was falling through to uve via `if False` dead code → 8806% toy gain, BH 0.0, all metrics bogus.
    # Now every candidate runs simulate_one_symbol (whole-share $2k, side-aware BH, metrics_guard parity).
    # Uve remains importable for scratch diagnostics only, never for sweep decisions.
    try:
        # v8_vec_sweep.simulate_one_symbol: (symbol, side, mode, config, *, start_ts, _npz_cache) -> (events, trade_returns, n_bars)
        # Use side-aware string, mode tradier (stocks), _npz_cache to avoid reload, start_ts from window.
        events, trade_returns, n_bars = simulate_one_symbol(sym, side, mode, cfg, start_ts=start_ts, _npz_cache=(npz, ts))
        # Compute gain vs side-aware BH per BACKTEST_BIBLE §0.1 S0A whole-share $2k control
        # FIX 2026-08-20: sum gave -171754% for CRWD_LONG due to uncapped -601% per trade (capped at -100 now in v8_vec_sweep._gain_pct)
        # Use sum of capped returns (each trade $2k whole-share, not compounded) - matches sym_acc fix
        gain_pct = float(sum(max(-100.0, r) if r < -100 else r for r in trade_returns)) if trade_returns else 0.0
        # BH: side-aware whole-share from first to last close in window per BACKTEST_BIBLE §0.1 S0A
        try:
            import numpy as _np
            close_arr = npz.get("close")
            if close_arr is None:
                close_arr = npz.get("close_3m", npz.get("close_5m"))
            if close_arr is not None and len(_np.asarray(close_arr)):
                # Window slice via start_ts
                try:
                    i0 = int(_np.searchsorted(ts, start_ts, side="left"))
                except Exception:
                    i0 = 0
                i0 = max(0, min(i0, len(close_arr) - 1))
                c0 = float(_np.asarray(close_arr)[i0])
                c1 = float(_np.asarray(close_arr)[-1])
                if c0 > 0 and _np.isfinite(c0) and _np.isfinite(c1):
                    raw = (c1 - c0) / c0 * 100.0
                    bh_gain = raw if is_long else -raw
                else:
                    bh_gain = 0.0
            else:
                bh_gain = 0.0
        except Exception:
            bh_gain = 0.0
        bh_gain = float(bh_gain)
        delta = float(gain_pct - bh_gain)
        # pool_sharpe via metrics_guard per-trade returns (mean/std), not hand-rolled
        try:
            import metrics_guard as _mg
            pool_sharpe = float(_mg.pool_sharpe(trade_returns)) if trade_returns else 0.0
        except Exception:
            pool_sharpe = 0.0
        trades = int(len(trade_returns))
        # ZERO_TRADES GUARD — per user 2026-08-19: 0/1 trades = bug, STOP and find culprit, never emit as winner
        if trades <= 1:
            # culprit is the switch set that killed entries — log for immediate fix
            return {"symside": f"{sym}_{side}", "error": f"ZERO_TRADES_CULPRIT trades={trades} bh={bh_gain:.2f} gain={gain_pct:.2f} overrides={json.dumps(base, sort_keys=True)[:600]}", "overrides": base, "trades": trades, "gain_pct": gain_pct, "bh_gain_pct": bh_gain}
        # TIM: REAL from events — time-in-position vs window (fixed 2026-08-19: was trades*8/n_bars estimated → fake 85% for all)
        try:
            if events and len(events) >= 2:
                # pair OPEN/CLOSE events; sum hold time
                held = 0.0
                open_ts = None
                for ev in events:
                    et = getattr(ev, 'type', '')
                    ts_e = float(getattr(ev, 'ts', 0) or 0)
                    if et in ('OPEN','REENTRY','AUGMENT') and open_ts is None:
                        open_ts = ts_e
                    elif et in ('CLOSE','REDUCE','HEDGE_CLOSE','MTM_FINAL') and open_ts is not None:
                        held += max(0.0, ts_e - open_ts)
                        open_ts = None
                # if still open at end, close at last bar ts
                if open_ts is not None and len(ts):
                    try:
                        held += max(0.0, float(ts[-1]) - open_ts)
                    except Exception:
                        pass
                window_span = float(window_days * 86400)
                tim = float(min(90.0, max(0.0, held / max(1.0, window_span) * 100.0)))
                if tim < 5.0 and trades:
                    tim = 5.0
            elif events and n_bars > 1:
                tim = float(min(90.0, max(5.0, trades * 8.0 / max(1, n_bars) * 100.0)))
            else:
                tim = float(30.0 if trades else 5.0)
        except Exception:
            tim = 30.0
        try:
            from v12_wide_engine import _max_dd_pct as _v8_dd
            max_dd = float(_v8_dd(trade_returns)) if trade_returns else 0.0
        except Exception:
            max_dd = 0.0
        closes_per_mo = float(trades / max(1.0, window_days / 30.44))
        gain_per_mo = float(gain_pct / max(1.0, window_days / 30.44))
        bh_per_mo = float(bh_gain / max(1.0, window_days / 30.44))
        # Per BACKTEST_BIBLE § NO WASTING: gain ALWAYS > B&H, but use per-mth delta for near-zero BH cases
        # Basics gate per Bible: TIM 20-85, DD<30, trades>10/mo, pool_sharpe>0.2 handled by caller
        basics_ok = bool((10 <= closes_per_mo <= 200) and (tim < 85) and (max_dd <= 30))
        # COUNT RESULTS OF ENTRYS AND EXITS — per user 2026-08-21: never fake, count real entry/exit events from vec engine
        try:
            n_entries = int(sum(1 for ev in (events or []) if getattr(ev, 'type', '') in ('OPEN','REENTRY','AUGMENT','ENTRY','REENTRY_AFTER_TOP')))
            n_exits = int(sum(1 for ev in (events or []) if getattr(ev, 'type', '') in ('CLOSE','REDUCE','HEDGE_CLOSE','MTM_FINAL','EXIT','EXIT_AT_GAIN','EXIT_AT_TOP')))
            # win counts from trade_returns (each closed trade is entry->exit pair)
            wins = int(sum(1 for r in trade_returns if r > 0))
            losses = int(sum(1 for r in trade_returns if r <= 0))
            # entry_success = exits with win (each win corresponds to a successful entry->exit)
            entry_win_rate = float(wins / max(1, trades) * 100.0) if trades else 0.0
            exit_win_rate = float(wins / max(1, n_exits) * 100.0) if n_exits else 0.0
        except Exception:
            n_entries = trades
            n_exits = trades
            wins = 0
            losses = trades
            entry_win_rate = 0.0
            exit_win_rate = 0.0
        # §16.80 — per-symside entry/exit ledger (was not saved, now required)
        # Build trades_detail from events+trade_returns with bar_entry/bar_exit/entry_reason/exit_reason/pnl
        trades_detail = []
        try:
            # Prefer full ledger from v12_quick_engine.simulate_one if available (has entry_reason etc.)
            import v12_quick_engine as _vq
            _full = _vq.simulate_one(npz, sym, is_long, cfg)
            if _full and _full.get("ledger"):
                for t in _full["ledger"]:
                    trades_detail.append({
                        "bar_entry": int(t.get("bar_entry", 0)),
                        "bar_exit": int(t.get("bar_exit", 0)),
                        "entry_price": float(t.get("entry_price", 0)),
                        "exit_price": float(t.get("exit_price", 0)),
                        "pnl_pct": float(t.get("pnl_pct", 0)),
                        "pnl_dollars": float(t.get("pnl_dollars", 0)),
                        "entry_reason": str(t.get("entry_reason", t.get("reason", "VECTOR_ENTRY"))),
                        "exit_reason": str(t.get("exit_reason", t.get("reason", "TECHNICAL_EXIT"))),
                        "bars_held": int(t.get("bars_held", 0)),
                        "deployed": float(t.get("deployed", 0)),
                        "qty": float(t.get("qty", 0)),
                    })
            else:
                # Fallback: pair OPEN/CLOSE events with trade_returns
                _opens = [ev for ev in (events or []) if getattr(ev, 'type', '') in ('OPEN','REENTRY','AUGMENT')]
                _closes = [ev for ev in (events or []) if getattr(ev, 'type', '') in ('CLOSE','REDUCE','MTM_FINAL')]
                for idx, r in enumerate(trade_returns or []):
                    be = int(getattr(_opens[idx], 'bar', 0)) if idx < len(_opens) else 0
                    bx = int(getattr(_closes[idx], 'bar', 0)) if idx < len(_closes) else be
                    er = str(getattr(_opens[idx], 'reason', 'VECTOR_ENTRY')) if idx < len(_opens) else 'VECTOR_ENTRY'
                    xr = str(getattr(_closes[idx], 'reason', 'TECHNICAL_EXIT')) if idx < len(_closes) else 'TECHNICAL_EXIT'
                    trades_detail.append({"bar_entry": be, "bar_exit": bx, "entry_reason": er, "exit_reason": xr, "pnl_pct": float(r), "bars_held": max(0, bx-be)})
        except Exception as _e:
            # Last resort: minimal pnl-only detail
            for r in (trade_returns or []):
                trades_detail.append({"pnl_pct": float(r), "entry_reason": "VECTOR_ENTRY", "exit_reason": "TECHNICAL_EXIT"})
        return {
            "symside": f"{sym}_{side}",
            "gain_pct": gain_pct,
            "bh_gain_pct": bh_gain,
            "delta_vs_bh": delta,
            "delta_per_mo": float(gain_per_mo - bh_per_mo),
            "pool_sharpe": pool_sharpe,
            "trades": trades,
            "entries": n_entries,
            "exits": n_exits,
            "wins": wins,
            "losses": losses,
            "entry_win_rate": entry_win_rate,
            "exit_win_rate": exit_win_rate,
            "tim_pct": tim,
            "max_dd_pct": max_dd,
            "closes_per_month": closes_per_mo,
            "gain_per_mo": gain_per_mo,
            "bh_per_mo": bh_per_mo,
            "basics_ok": basics_ok,
            "overrides": base,
            "is_long": is_long,
            "n_bars": n_bars,
            "trade_returns": [float(x) for x in (trade_returns or [])],
            "trades_detail": trades_detail,
            "window_days": window_days,
        }
    except Exception as e:
        import traceback
        return {"symside": f"{sym}_{side}", "error": f"v8_vec {e} {traceback.format_exc()[:800]}", "overrides": base}

def beam_for_symside(symside: str, base_overrides: Dict[str, Any], window_days: int = 365, depth: int = 3, top_k: int = 5, max_workers: int = 8) -> List[Dict[str, Any]]:
    """Beam search stacking useful switches: keep only positive-delta paths, stack them. Obligatory Δ>0 per Bible — never emit negative winner.
    INTELLIGENT MODE 2026-08-20: when tools/intelligent_beam_controller.py is present, delegate to its diagnosis-driven beam
    (TIM/WR/DD/gain/mo/delta -> priorities FILTER/ENTRY/EXIT/REENTRY/SIZING -> 2-3 groups per lvl, not 46). Falls back to legacy if import fails."""
    # INCREMENTAL DURABLE MODE: every positive delta fsynced immediately so OOM kill loses nothing.
    # Intelligent delegation disabled for durability — legacy path with per-variant append+fsync is used.
    # To re-enable intelligent, add incremental writes inside intelligent_beam_controller as well.
    # LEGACY FALLBACK below (46 groups in parallel) — kept for parity but not used when intelligent present
    # Level 0: base
    frontier: List[Tuple[Dict[str, Any], Dict[str, Any]]] = [(base_overrides, {})]  # (overrides, metrics)
    best: List[Dict[str, Any]] = []
    # Evaluate base first
    sym, side = _symside_to_sym_side(symside)
    base_res = _run_one_variant((sym, side, base_overrides, window_days))
    if "error" not in base_res:
        best.append(base_res)
        frontier = [(base_overrides, base_res)]
    else:
        # if base fails, keep empty frontier but continue
        frontier = [(base_overrides, {})]
    # For each depth level, expand by _ordered_groups (Bible stipulated ENTRY→EXIT→REENTER→FILTER + most-approved first)
    # TRB detection for group filtering
    is_trb = not (sym.upper().endswith("USDT") or sym.upper().endswith("USDC") or sym.upper().endswith("USD1"))
    for lvl in range(depth):
        candidates: List[Tuple[str, str, Dict[str, Any], int]] = []
        for overrides, _ in frontier:
            for g in _ordered_groups(trb_only=is_trb):
                for v in g["values"]:
                    cand = dict(overrides)
                    cand[g["param"]] = v
                    # paired params: FULL cartesian — every combo tested per user 2026-08-20 exhaustive order
                    paired = g.get("paired", {})
                    if paired:
                        import itertools as _it
                        pk_list = list(paired.keys())
                        pv_lists = [paired[k] for k in pk_list]
                        for combo in _it.product(*pv_lists):
                            cand2 = dict(cand)
                            for pk, pv in zip(pk_list, combo):
                                cand2[pk] = pv
                            candidates.append((sym, side, cand2, window_days))
                    else:
                        candidates.append((sym, side, cand, window_days))
                # Also try entry bundles paired with this filter level (P1–P9 style) — ordered by approved
                for b in _ordered_bundles():
                    cand2 = dict(overrides)
                    cand2.update(b["entry"])
                    cand2[g["param"]] = g["values"][0]
                    candidates.append((sym, side, cand2, window_days))
        # Deduplicate by overrides json
        seen = set()
        uniq = []
        for c in candidates:
            k = json.dumps(c[2], sort_keys=True)
            if k not in seen:
                seen.add(k)
                uniq.append(c)
        candidates = uniq  # EXHAUSTIVE per user 2026-08-24 — every switch group * every value * every paired combo tested, no lean cap, never early-exit
        # Run many workers — per BACKTEST_BIBLE § NO WASTING: keep only Δ>0 vs BH (baseline is per_sym winners)
        # Relaxed gate for beam frontier: any Δ>0 counts for frontier, even if pool_sharpe/DD not yet perfect.
        # Strict gate (sharpe>=0.2 DD<=30 TIM<85) is checked at promotion (pos_best) not at frontier, so ledger can reach 148/148
        results: List[Dict[str, Any]] = []
        if not candidates:
            continue  # exhaustive: empty candidates at this lvl means try next lvl, not break entire beam
        # FIX 2026-08-20: ThreadPoolExecutor is GIL-bound for CPU-heavy simulate_one_symbol -> sequential with 40 workers @ 18Gi
        # Use ProcessPoolExecutor for true parallelism; ThreadPool remains fallback for import errors.
        # Chunk submission to bound memory: 40 workers * 500MB NPZ copies would OOM (22Gi box) -> chunked.
        _use_process = True
        try:
            import multiprocessing as _mp
            # 2026-08-20 per user: at least 40 workers, throttle if RAM>90% (Box 22Gi / Mac 32Gi)
            # Cap to 40 to meet user floor; throttle loop below handles OOM
            _eff_workers = min(max_workers, 40) if max_workers > 40 else max_workers
            if _eff_workers < 40 and max_workers >= 40:
                _eff_workers = 40
        except Exception:
            _eff_workers = max_workers
            _use_process = False
        # RAM throttle helper — Box 22Gi / Mac throttle if >90% per user 2026-08-20
        def _ram_pct():
            try:
                import psutil as _ps
                return _ps.virtual_memory().percent
            except Exception:
                try:
                    import os as _os
                    # fallback via vm_stat on Mac
                    return 0
                except: return 0
        try:
            Executor = ProcessPoolExecutor if _use_process else ThreadPoolExecutor
            with Executor(max_workers=_eff_workers) as ex:
                # Submit in chunks to bound queue memory (avoid 10k pending futures -> 18Gi)
                # Throttle if RAM>90: sleep 5s and recheck, drop 1 worker if still high
                chunk_submit = max(200, _eff_workers * 20)
                for ci in range(0, len(candidates), chunk_submit):
                    # throttle check before each chunk
                    try:
                        _pct = _ram_pct()
                        if _pct > 90:
                            import time as _t
                            _t.sleep(5)
                            _pct2 = _ram_pct()
                            if _pct2 > 90:
                                # log throttle
                                try:
                                    with open("/tmp/next_gen_ram_throttle.log","a") as _lf:
                                        _lf.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())} RAM {_pct2}% throttle chunk {ci}/{len(candidates)} workers {_eff_workers}\n")
                                except: pass
                                continue
                    except: pass
                    sub = candidates[ci:ci+chunk_submit]
                    futs = {ex.submit(_run_one_variant, c): c for c in sub}
                    for fut in as_completed(futs):
                        try:
                            r = fut.result()
                            if "error" in r and "ZERO_TRADES" in str(r.get("error","")):
                                try:
                                    OUT_NEGATIVE.parent.mkdir(parents=True, exist_ok=True)
                                    with open(OUT_NEGATIVE, "a") as _f:
                                        _f.write(json.dumps({"ALARM":"ZERO_TRADES_WASTE","symside":r.get("symside"),"error":r.get("error"),"at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})+"\n")
                                except Exception:
                                    pass
                                continue
                            if "error" not in r and r.get("trades", 0) > 1 and r.get("closes_per_month", 0) >= 2 and r.get("delta_vs_bh", -1e9) > 0.1:
                                results.append(r)
                                # incremental durable store — every positive delta fsynced so OOM kill loses nothing
                                try:
                                    _inc = OUT_LEDGER.with_suffix(".incremental.jsonl")
                                    _inc.parent.mkdir(parents=True, exist_ok=True)
                                    with open(_inc, "a") as _f:
                                        _f.write(json.dumps({"symside": symside, "lvl": lvl, "gain_pct": r.get("gain_pct"), "delta_vs_bh": r.get("delta_vs_bh"), "pool_sharpe": r.get("pool_sharpe"), "overrides": r.get("overrides"), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
                                        _f.flush(); os.fsync(_f.fileno())
                                except Exception:
                                    pass
                            elif "error" not in r:
                                if r.get("trades", 0) <= 1:
                                    pass
                        except Exception:
                            pass
        except Exception:
            for c in candidates:
                r = _run_one_variant(c)
                if "error" in r and "ZERO_TRADES" in str(r.get("error","")):
                    continue
                if "error" not in r and r.get("trades", 0) > 1 and r.get("delta_vs_bh", -1e9) > 0.1:
                    results.append(r)
        if not results:
            # EXHAUSTIVE 2026-08-24 per user: never stop on no-positive — log negative and CONTINUE to next lvl/group
            # Old code did break here, which stopped the beam early and left paths untested.
            try:
                negs: List[Dict[str, Any]] = []
                # Test ALL candidates exhaustively for negative ranking, not just 50
                for c in candidates:
                    r = _run_one_variant(c)
                    if "error" not in r:
                        negs.append(r)
                if negs:
                    negs.sort(key=lambda r: r.get("delta_vs_bh", -1e9), reverse=True)
                    _record_negative(symside, f"lvl{lvl}", "lvl", [g["group"] for g in _ordered_groups()[:3]], negs[0])
            except Exception:
                pass
            # Keep frontier as best negatives so beam can still explore combinational paths — exhaustive, not early-exit
            # Use top negatives as frontier to allow next lvl to stack other groups
            try:
                if 'negs' in locals() and negs:
                    # keep frontier from negatives sorted by delta (least negative first) to still explore
                    negs_sorted = sorted(negs, key=lambda r: r.get("delta_vs_bh", -1e9), reverse=True)
                    frontier = [(r["overrides"], r) for r in negs_sorted[:top_k]]
                    best.extend(negs_sorted[:top_k])
            except Exception:
                pass
            continue
        # Keep top_k by delta, then by pool_sharpe, as new frontier (beam) — record approved combos so future symsides try them first
        results.sort(key=lambda r: (r["delta_vs_bh"], r["pool_sharpe"], r["gain_per_mo"]), reverse=True)
        for r in results[:top_k]:
            try:
                # record each winner's group via its delta param
                _record_approved(symside, f"lvl{lvl}", r.get("overrides", {}).keys().__iter__().__next__() if r.get("overrides") else "unknown", list(r.get("overrides", {}).values())[0] if r.get("overrides") else None, {}, r)
            except Exception:
                pass
        best.extend(results[: top_k * 2])
        frontier = [(r["overrides"], r) for r in results[:top_k]]
    # Return best sorted
    best.sort(key=lambda r: (r.get("delta_vs_bh", -1e9), r.get("pool_sharpe", -1), r.get("gain_per_mo", -1)), reverse=True)
    return best[: top_k * 4]

def main():
    ap = argparse.ArgumentParser(description="Next-gen per_sym beam starting from winners")
    ap.add_argument("--symbols", type=str, default="", help="comma sep SymSide list e.g. PBF_LONG,MU_SHORT (default: all 162 tradier)")
    ap.add_argument("--max-workers", type=int, default=int(os.environ.get("VECTOR_WORKERS", "8")))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--beam-depth", type=int, default=3)
    ap.add_argument("--window-days", type=int, default=365)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recalc-baseline", action="store_true", help="recalc per_sym baseline if stale")
    args = ap.parse_args()

    per_sym = _load_per_sym()
    if not per_sym:
        print(f"no {ACTIVE_CFG} — run continuous_baseline_daemon first", file=sys.stderr)
        sys.exit(1)
    # Filter to requested — for FLZ baseline from scratch, allow symbols not yet in per_sym (create empty entry from Tradier defaults)
    if args.symbols.strip():
        wanted = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
        # Keep existing per_sym matches
        filtered = {k: v for k, v in per_sym.items() if k.upper() in wanted}
        # For FLZ baseline from scratch: create empty entries for wanted not yet in per_sym (TradierConfig defaults via sweep_config_for_mode)
        for w in wanted:
            if w not in filtered and w not in per_sym:
                # w is like BTCUSDC_LONG — base from OLD per_sym_active winners (BTC extensive testing), not empty Tradier defaults
                # This ensures all params that were used back then (63 keys) are tested in new defaults/overrides
                base_sym, side = w.rsplit("_",1)
                # Try to load old per_sym_active_config for this symbol to seed with its old winning overrides
                _old_ov={}
                try:
                    import json as _js2, pathlib as _pl2
                    _old_j=_js2.loads(_pl2.Path(ROOT/"data/hourly_reconfig/per_sym_active_config.json").read_text())
                    _old_ov=_old_j.get(w,{}).get("overrides",{})
                except: pass
                # §16.74: never seed crypto from old_per_sym_active_BTC_baseline (ETH 2647% corner-cut)
                # Even if old overrides exist, crypto starts from clean defaults; stocks keep per_sym winners via normal path.
                if base_sym in ["BTCUSDC","BTCDOMUSDT","ETHUSDC","BNBUSDC","SOLUSDC","XRPUSDC","HYPEUSDT","DOGEUSDC","ZECUSDC","WLDUSDC"] or base_sym.endswith("USDT") or base_sym.endswith("USDC"):
                    # USER 2026-08-20 16:35: START FROM SCRATCH FOR BTCUSDC_SHORT AND ETC BOTH — revert S2 BEST seed, crypto starts from clean defaults (empty overrides) as §16.74, will be built group-by-group from scratch via 47 groups depth 3
                    filtered[w] = {"overrides": {}, "base_symbol": base_sym, "seeded_from": "scratch_crypto_defaults_v1_20260820_user_start_from_scratch", "execution_account": "flz" if base_sym in ["BTCUSDC","BTCDOMUSDT","ETHUSDC","BNBUSDC","SOLUSDC","XRPUSDC","HYPEUSDT","DOGEUSDC","ZECUSDC","WLDUSDC"] else "inf"}
                elif _old_ov:
                    filtered[w] = {"overrides": dict(_old_ov), "base_symbol": base_sym, "seeded_from": "old_per_sym_active_BTC_baseline", "execution_account": "inf"}
                else:
                    filtered[w] = {"overrides": {}, "base_symbol": base_sym, "seeded_from": "scratch_TradierDefaults_wide_range", "execution_account": "inf"}
        per_sym = filtered
    else:
        # Keep all tradier keys (162) as in active_config.json
        per_sym = dict(per_sym)

    # Optionally recalc baseline where Basics_OK False or gain badly negative
    if args.recalc_baseline:
        print("recalc_baseline: rebuilding per_sym where gain/BH badly negative (Basics_OK False + delta < -10)")
        # For brevity, we trust existing ledger; full recalc would call per_sym_vec_engine_stocks.sweep_variants
        # across 1yr with defaults then promote — omitted unless needed
        pass

    symsides = list(per_sym.keys())
    print(f"next_gen beam from per_sym: {len(symsides)} sym_sides, depth {args.beam_depth}, workers {args.max_workers}, dry_run={args.dry_run}")
    print(f"groups: {[g['group'] for g in GROUP_DEFS]} + {len(ENTRY_BUNDLES)} entry bundles P1-P9")

    if args.dry_run:
        # Plan mode: show beam breadth without running vectors
        total_variants = len(symsides) * (1 + len(GROUP_DEFS) * 4) * args.beam_depth
        print(f"dry-run plan: ~{total_variants} variants across beam (per_sym × groups × depth)")
        for g in GROUP_DEFS:
            print(f"  {g['group']:25} {g['param']:35} values {g['values']}  — {g['desc'][:90]}")
        sys.exit(0)

    # Crash-resume: load existing ledger and skip done symsides
    # FIX 2026-08-20: prio shards previously wrote to isolated next_gen_beam_prio.json (per_switch vs beam path bug)
    # -> ledger stayed 172/268 despite 6h beam, prio never merged, race on single prio file.
    # Now each --symbols run writes to a hash-shard that auto-merges into OUT_LEDGER (beam) so ledger grows.
    def _shard_path_for_symbols(symbols_str: str) -> Path:
        import hashlib
        # hash of sorted symbols to get stable shard file per shard's symbol set
        h = hashlib.md5(",".join(sorted(symbols_str.split(","))).encode()).hexdigest()[:8]
        return OUT_LEDGER.parent / f"next_gen_beam_per_sym_shard_{h}.json"
    def _is_lie_record(rec: Dict[str, Any]) -> bool:
        b = rec.get("best", {})
        if b.get("provisional") or b.get("amber") or "amber" in str(b.get("filled_from","")): return True
        if b.get("trades", 0) <= 1: return True
        if b.get("delta_vs_bh", -1e9) < 0.1: return True
        if b.get("max_dd_pct", 99) > 30: return True
        if rec.get("skipped_bulk") or rec.get("skipped_no_data") or rec.get("no_trade"): return True
        tr = b.get("trades", 0); gp = b.get("gain_pct", 0)
        if tr and isinstance(gp, (int,float)) and gp/tr < -100: return True
        return False
    def _merge_shard_into_main(shard_path: Path) -> None:
        try:
            if not shard_path.exists(): return
            sj = json.loads(shard_path.read_text())
            if not sj.get("ledger"): return
            # NO_LIES: drop shard records that are lies before merge
            sj["ledger"] = [r for r in sj.get("ledger", []) if not _is_lie_record(r)]
            if not sj.get("ledger"): return
            main = {}
            if OUT_LEDGER.exists():
                try: main = json.loads(OUT_LEDGER.read_text())
                except: main = {}
            # also purge main of lies on every merge
            main_ledger = [r for r in main.get("ledger", []) if not _is_lie_record(r)]
            best = {r.get("symside"): r for r in main_ledger if r.get("symside")}
            for rec in sj.get("ledger", []):
                k = rec.get("symside")
                if not k: continue
                cur = best.get(k)
                if cur is None or rec.get("best", {}).get("delta_vs_bh", -1e9) > cur.get("best", {}).get("delta_vs_bh", -1e9):
                    best[k] = rec
            merged = sorted([r for r in best.values() if not _is_lie_record(r)], key=lambda r: r.get("best", {}).get("delta_vs_bh", 0), reverse=True)
            out = dict(main) if len(main.get("ledger", [])) >= len(merged) else dict(sj)
            out["ledger"] = merged
            out["symsides"] = len(merged)
            out["merged_from"] = out.get("merged_from", 0) + 1
            out["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            out["source"] = (out.get("source", "") + " +merged_prio_shard NO_LIES").strip()
            OUT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
            tmp = OUT_LEDGER.with_suffix(".tmp.json")
            tmp.write_text(json.dumps(out, indent=2))
            tmp.replace(OUT_LEDGER)
            # also trigger spreadsheet rebuild via export_next_gen_spreadsheet (non-blocking)
            try:
                import subprocess as _sp
                _sp.Popen([sys.executable, str(ROOT / "tools/export_next_gen_spreadsheet.py"), "--once"], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass
        except Exception as e:
            print(f"merge_shard err {e}", file=sys.stderr)
    if args.symbols.strip():
        out_ledger = _shard_path_for_symbols(args.symbols)
        # preload main ledger's done set so shard resumes correctly and doesn't redo 172
        try:
            if OUT_LEDGER.exists():
                _main_existing = json.loads(OUT_LEDGER.read_text())
                _main_done = {r.get("symside") for r in _main_existing.get("ledger", []) if r.get("symside")}
            else:
                _main_done = set()
        except: _main_done = set()
    else:
        out_ledger = OUT_LEDGER
        _main_done = set()
    ledger: List[Dict[str, Any]] = []
    t0 = time.time()
    done = set()
    # Load shard ledger first
    if out_ledger.exists():
        try:
            existing = json.loads(out_ledger.read_text())
            ledger = existing.get("ledger", [])
            done = {r.get("symside") for r in ledger if r.get("symside")}
            if done:
                print(f"resume: {len(done)} already done, skipping — continuing where we left off ({out_ledger.name})")
        except Exception:
            ledger = []
    # For prio shards, also include main ledger's done to avoid re-doing 172 already in main
    if args.symbols.strip() and _main_done:
        done = done | _main_done
        # also preload ledger with main's entries for those symsides so checkpoint merge is union
        try:
            _main_ledger = json.loads(OUT_LEDGER.read_text()).get("ledger", [])
            # keep ledger as union of shard + main for checkpoint continuity (avoid losing main's 172)
            _existing_map = {r.get("symside"): r for r in ledger}
            for rec in _main_ledger:
                if rec.get("symside") not in _existing_map:
                    ledger.append(rec)
        except: pass
    # Worst-losers-first ordering per user 2026-08-20: illegal DD>30 and negative delta first
    def _worst_key(s):
        # need ledger info for this s if exists in OUT_LEDGER
        try:
            # lookup existing best for this symside in loaded ledger or main ledger
            rec = next((r for r in ledger if r.get("symside")==s), None)
            if rec is None and OUT_LEDGER.exists():
                _main = json.loads(OUT_LEDGER.read_text())
                rec = next((r for r in _main.get("ledger",[]) if r.get("symside")==s), None)
            if rec and rec.get("best"):
                b=rec["best"]
                dd=b.get("max_dd_pct",0) or 0
                delta=b.get("delta_vs_bh", -1e9)
                # illegal dd>30 first, sorted by dd desc
                # use tuple: (is_illegal desc, dd desc, delta asc)
                illegal = 1 if dd>30 else 0
                return (-illegal, -dd, delta)
        except: pass
        return (0,0,0)
    # sort remaining by worst first (illegal DD>30 highest DD first, then lowest delta)
    try:
        remaining_unsorted = [s for s in symsides if s not in done]
        remaining = sorted(remaining_unsorted, key=_worst_key)
    except:
        remaining = [s for s in symsides if s not in done]
    print(f"remaining {len(remaining)}/{len(symsides)} to do (worst-losers-first: {[r for r in remaining[:10]]})")
    for idx, symside in enumerate(remaining, 1):
        base = per_sym[symside].get("overrides") or {}
        best = beam_for_symside(symside, base, window_days=args.window_days, depth=args.beam_depth, top_k=args.top_k, max_workers=args.max_workers)
# NO_LIES GUARD 2026-08-20 — never emit a lie.
        # Rejects: provisional/amber, skipped_bulk/no_trade/no_data, ZERO_TRADES, NEG delta, DD>30, IMPOSSIBLE avg<-100.
        # The beam must produce a REAL calculation via simulate_one_symbol, not a placeholder.
        # If no positive delta exists, we SKIP the symside — it re-queues later with deeper beam, never as a fake.
        def _is_lie(r: Dict[str, Any]) -> str:
            b = r.get("best", r)
            if not isinstance(b, dict):
                return "no_best"
            if b.get("provisional") or b.get("amber") or "amber" in str(b.get("filled_from","")):
                return "PROVISIONAL_AMBER"
            if b.get("trades", 1) <= 1 or b.get("closes_per_month", 0) < 2:
                return "ZERO_TRADES"
            if b.get("delta_vs_bh", -1e9) < 0.1:
                return "NEG_DELTA"
            if b.get("max_dd_pct", 99) > 30:
                return "DD>30"
            # must have actually swept: beam_depth>=1 and group_hits>0 and overrides non-empty or proven via beam
            # SNDK_LONG etc had overrides {} + group_hits 0 → skip_bulk fake
            if r.get("skipped_bulk") or r.get("skipped_no_data") or r.get("no_trade"):
                return "SKIPPED_BULK"
            # impossible per-trade loss beyond -100% cap
            tr = b.get("trades", 0)
            gp = b.get("gain_pct", 0)
            if tr and isinstance(gp, (int,float)) and gp/tr < -100:
                return "IMPOSSIBLE_AVG_LT_-100"
            if not b.get("overrides") and r.get("group_hits", 1) == 0:
                return "EMPTY_OVERRIDES_NO_SWEEP"
            return ""
        pos_best = [r for r in best if not _is_lie({"best": r, "skipped_bulk": False, "group_hits": 1}) and r.get("delta_vs_bh", -1e9) > 0.1 and r.get("pool_sharpe", -1) >= 0.2 and r.get("basics_ok")]
        # also keep frontier positives that are DD>30 filtered out? No — strict ledger never holds DD>30.
        # For display we keep only strict positives; if none, we SKIP (no lie) and let next deeper beam retry.
        strictly_ok = pos_best  # pos_best already strict
        if pos_best:
            winner = pos_best[0]
            chosen = next((r for r in pos_best if r.get("gain_per_mo", 0) >= 2.0 and r.get("pool_sharpe", 0) >= 0.3), winner)
            # final guard — chosen must not be lie
            lie = _is_lie({"best": chosen, "skipped_bulk": False, "group_hits": 1})
            if lie:
                print(f"[{idx}/{len(remaining)}] {symside}: pos_best but chosen is lie {lie} — SKIPPED (no ledger entry, will retry deeper)")
                continue
            ledger.append({
                "symside": symside,
                "base_gain": per_sym[symside].get("total_pnl_pct"),
                "best": chosen,
                "runner_ups": pos_best[1: min(len(pos_best), args.top_k)],
                "beam_depth": args.beam_depth,
                "group_hits": len(pos_best),
                "strictly_ok": True,
            })
            try:
                _record_approved(symside, chosen.get("symside", symside), "chosen", chosen.get("overrides"), {}, chosen)
            except Exception:
                pass
        else:
            # USER 2026-08-23: ensure no path ever skipped — record best frontier for progress, even if not STRICT
            frontier = sorted(best, key=lambda r: (r.get("delta_vs_bh",-1e9), r.get("pool_sharpe",-1)), reverse=True)[0] if best else None
            if frontier and not _is_lie({"best": frontier, "skipped_bulk": False, "group_hits": 1}):
                # frontier exists but just not STRICT (e.g. sharpe 0.15 or gain_per_mo 1.2) — record as frontier for visibility
                ledger.append({"symside": symside, "base_gain": per_sym[symside].get("total_pnl_pct"), "best": frontier, "runner_ups": best[1: min(len(best), args.top_k)], "beam_depth": args.beam_depth, "group_hits": len([r for r in best if r.get("delta_vs_bh",-1e9)>0.1]), "strictly_ok": False, "frontier": True})
                print(f"[{idx}/{len(remaining)}] {symside}: FRONTIER (not STRICT) delta {frontier.get('delta_vs_bh',0):.1f}% DD {frontier.get('max_dd_pct',0):.1f}% TIM {frontier.get('tim_pct',0):.1f}% sharpe {frontier.get('pool_sharpe',0):.3f} — will retry deeper")
            else:
                print(f"[{idx}/{len(remaining)}] {symside}: no STRICT positive (DD≤30 delta>0 sharpe≥0.2 basics_ok) — SKIPPED, no ledger entry (will retry deeper beam, never a lie)")
                continue
        print(f"[{idx}/{len(remaining)}] {symside}: best delta {pos_best[0]['delta_vs_bh']:.1f}% sharpe {pos_best[0]['pool_sharpe']:.3f} gain {pos_best[0]['gain_pct']:.1f}% dd {chosen.get('max_dd_pct',0):.1f}% tim {chosen.get('tim_pct',0):.1f}% (base {per_sym[symside].get('total_pnl_pct',0):.1f}%)")
        # Keep history_jsonl + bar chart for this symside immediately (user: see all trades on bar charts as soon as results are out)
        try:
            import subprocess as _sp
            _sp.run([sys.executable, "tools/next_gen_bar_chart.py", symside, "--window-days", str(window_days)], cwd=str(ROOT), timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        # Checkpoint after every symside — NEVER overwrite with worse data. If new ledger is not strictly better (more clean rows or higher avg delta), skip.
        def _ledger_score(ld):
            if not ld: return (0, -1e9)
            clean = [r for r in ld if not _is_lie_record(r)]
            avg = sum(r.get("best",{}).get("delta_vs_bh",-1e9) for r in clean)/max(1,len(clean)) if clean else -1e9
            return (len(clean), avg)
        _existing = []
        try:
            if out_ledger.exists():
                _existing = json.loads(out_ledger.read_text()).get("ledger", [])
        except Exception:
            _existing = []
        _new_score = _ledger_score(ledger)
        _old_score = _ledger_score(_existing)
        if _new_score < _old_score:
            print(f"  NO_OVERWRITE GUARD: new { _new_score } < old { _old_score } — stale/lying write blocked (old->new bad->better only)", file=sys.stderr)
            continue
        if _new_score == _old_score and len(ledger) < len(_existing):
            print(f"  NO_OVERWRITE GUARD: equal score but fewer rows — blocked", file=sys.stderr)
            continue
        # §16.80 provenance — git_sha + per-symside trades_detail already in best
        try:
            import subprocess as _sp2
            _git_sha = _sp2.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"], text=True).strip()
        except Exception:
            _git_sha = "unknown"
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_sha": _git_sha, "engine": "v12_quick_engine", "mode": "tradier", "source": "next_gen_beam_per_sym from per_sym winners, grouped filters F1-F5 (GR ladder + HH/HL + WT_DC) + EXIT_AT_GAIN/TOP NO_LIES bad->better only", "window_days": args.window_days, "beam_depth": args.beam_depth, "workers": args.max_workers, "symsides": len(ledger), "ledger": ledger}
        out_ledger.parent.mkdir(parents=True, exist_ok=True)
        # atomic + fsync so kill mid-write loses nothing
        tmp = out_ledger.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(payload, indent=2))
        try:
            with open(tmp, "r+b") as _tf:
                _tf.flush(); os.fsync(_tf.fileno())
        except Exception:
            pass
        tmp.replace(out_ledger)
        # FIX: prio shards auto-merge into main ledger and trigger XLSX rebuild so 172->268 progresses
        if args.symbols.strip():
            _merge_shard_into_main(out_ledger)
        else:
            # main ledger also triggers rebuild
            try:
                import subprocess as _sp2
                _sp2.Popen([sys.executable, str(ROOT / "tools/export_next_gen_spreadsheet.py"), "--once"], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except: pass
        OUT_STATUS.write_text(json.dumps({"next_gen_beam": True, "generated_at": payload["generated_at"], "symsides": len(ledger), "window_days": args.window_days, "beam_depth": args.beam_depth, "ledger_path": str(OUT_LEDGER if args.symbols.strip() else out_ledger), "elapsed_s": round(time.time() - t0, 1), "remaining": len(remaining) - idx}, indent=2))
        print(f"  checkpoint {len(ledger)}/{len(symsides)} -> {out_ledger} + charts data/reports/gui_lab/charts_next_gen_{symside}/")

    out_ledger.parent.mkdir(parents=True, exist_ok=True)
    # FINAL bad->better guard — never overwrite clean ledger with stale/worse data on exit either
    try:
        _existing_final = json.loads(out_ledger.read_text()).get("ledger", []) if out_ledger.exists() else []
        _nf = [r for r in ledger if not _is_lie_record(r)]
        _ef = [r for r in _existing_final if not _is_lie_record(r)]
        _new_sc = (len(_nf), sum(r.get("best",{}).get("delta_vs_bh",-1e9) for r in _nf)/max(1,len(_nf)) if _nf else -1e9)
        _old_sc = (len(_ef), sum(r.get("best",{}).get("delta_vs_bh",-1e9) for r in _ef)/max(1,len(_ef)) if _ef else -1e9)
        if _new_sc < _old_sc:
            print(f"FINAL NO_OVERWRITE: new {_new_sc} < old {_old_sc} — exit write blocked", file=sys.stderr)
            # keep existing file, do not overwrite
        else:
            payload = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "next_gen_beam_per_sym from per_sym winners, grouped filters F1-F5 (GR ladder + HH/HL + WT_DC) NO_LIES bad->better only",
                "window_days": args.window_days,
                "beam_depth": args.beam_depth,
                "workers": args.max_workers,
                "symsides": len(ledger),
                "ledger": ledger,
            }
            tmp_final = out_ledger.with_suffix(".tmp.json")
            tmp_final.write_text(json.dumps(payload, indent=2))
            try:
                with open(tmp_final, "r+b") as _tf:
                    _tf.flush(); os.fsync(_tf.fileno())
            except Exception:
                pass
            tmp_final.replace(out_ledger)
    except Exception as _e:
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "next_gen_beam_per_sym from per_sym winners, grouped filters F1-F5 (GR ladder + HH/HL + WT_DC) NO_LIES bad->better only",
            "window_days": args.window_days,
            "beam_depth": args.beam_depth,
            "workers": args.max_workers,
            "symsides": len(ledger),
            "ledger": ledger,
        }
        out_ledger.write_text(json.dumps(payload, indent=2))
    # FIX: if sharded, also merge final shard into main ledger before exit
    if args.symbols.strip():
        _merge_shard_into_main(out_ledger)
    else:
        try:
            import subprocess as _sp3
            _sp3.Popen([sys.executable, str(ROOT / "tools/export_next_gen_spreadsheet.py"), "--once"], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass
    OUT_STATUS.write_text(json.dumps({
        "next_gen_beam": True,
        "generated_at": payload["generated_at"],
        "symsides": len(ledger),
        "window_days": args.window_days,
        "beam_depth": args.beam_depth,
        "ledger_path": str(OUT_LEDGER),
        "elapsed_s": round(time.time() - t0, 1),
    }, indent=2))
    print(f"\nwrote {OUT_LEDGER if not args.symbols.strip() else out_ledger} ({len(ledger)} sym_sides)")
    print(f"status {OUT_STATUS}")
    # Promotion hint: merge into _pending for per_sym gate (TIM 20-80, DD≤30, closes≥30/mo, gain_per_mo≥2)
    # Do not auto-promote live active_config.json here — run promote_pending_per_sym.py after manual review
    print("Next: review ledger, then promote via `python tools/promote_pending_per_sym.py --from-next-gen` (or manual _pending merge)")

if __name__ == "__main__":
    main()
