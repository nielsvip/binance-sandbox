#!/usr/bin/env python3
"""
OPUS_VOMIT.py — QUARANTINED 2026-05-10 BY USER MANDATE.

Formerly v8_quick_engine.py. The vectorized "quick" backtest path is officially
declared a LIE — its results entrap promotion paths that touch live config and
have cost the user real money. ONLY backtest_v8_engine.py + its sub-scripts
(backtest_v8_sweep.py, backtest_v8_precompute.py) are valid backtest tooling
from this point forward.

This file is preserved (renamed, not deleted) so historical references can
still grep for it, but ANY attempt to run it as a script raises SystemExit
immediately. Any code that `from OPUS_VOMIT import *` or `import OPUS_VOMIT`
also gets the same refusal.
"""
import sys as _opus_sys
raise SystemExit(
    "OPUS_VOMIT.py refused: v8_quick path is banned by user mandate 2026-05-10. "
    "Use backtest_v8_engine.py + backtest_v8_sweep.py only."
)
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from test_rate_guard import RateGuard
# 2026-04-28 — central reentry facade so vectorized engine sees the same
# config switches and (in Phase 2) shared evaluators as live + real-engine v8.
try:
    import ez_reentry  # noqa: F401
except Exception:
    ez_reentry = None  # vec engine has no scalar dependency; facade is informational here

BASE_PATH = Path(__file__).resolve().parent

# B-5: warn once per missing field so sweep winners on dead switches get surfaced.
# Set V8_WARN_MISSING=0 to silence. Emitted to stderr only the first time we see a miss.
_V8_MISSING_WARNED = set()


def _v8_warn_missing(key: str, got_len, want_len: int, reason: str = ""):
    if key in _V8_MISSING_WARNED:
        return
    _V8_MISSING_WARNED.add(key)
    if os.environ.get('V8_WARN_MISSING', '1') != '1':
        return
    print(f"[V8_ENGINE] MISSING_FIELD key={key!r} got_len={got_len} want_len={want_len} {reason} -> zero-fill", file=sys.stderr)


def _safe(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    _v8_warn_missing(key, (len(v) if v is not None and hasattr(v, '__len__') else None), n, f"default={default}")
    return np.full(n, default, dtype=np.float64)


def _safeb(npz: dict, key: str, n: int) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(bool)
    _v8_warn_missing(key, (len(v) if v is not None and hasattr(v, '__len__') else None), n, "bool")
    return np.zeros(n, dtype=bool)


def _safe_k_1m(npz: dict, n: int, ltf: str = "3m", default: float = 50.0) -> np.ndarray:
    # 2026-04-30 Phase 2(b): wake K1M_EXTREME_REVERSE from dormancy.
    # Real 1m klines aren't on disk (15m is the base), so use stoch_k_{ltf} (3m crypto / 5m tradier)
    # as proxy. Fires K1M at LTF frequency rather than true 1m — lower count than live's 1,205/day,
    # but functional vs the prior all-50.0 zero-fill that left the gate dormant.
    v = npz.get("stoch_k_1m")
    if v is not None and isinstance(v, np.ndarray) and len(v) == n and not (np.asarray(v) == default).all():
        return v.astype(np.float64)
    proxy = npz.get(f"stoch_k_{ltf}")
    if proxy is not None and isinstance(proxy, np.ndarray) and len(proxy) == n:
        return proxy.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _ha_int(npz: dict, key: str, n: int):
    v = npz.get(key)
    if v is None or not isinstance(v, np.ndarray) or len(v) != n:
        _v8_warn_missing(key, (len(v) if v is not None and hasattr(v, '__len__') else None), n, "ha_int")
        return np.zeros(n, dtype=np.int8)
    if v.dtype in (np.int8, np.int16, np.int32, np.int64):
        return v.astype(np.int8)
    _v8_warn_missing(key, len(v), n, f"wrong-dtype={v.dtype}")
    return np.zeros(n, dtype=np.int8)


@dataclass
class QuickConfig:
    MODE: str = "crypto"
    LTF: str = "3m"   # crypto default — lowest timeframe for entry/exit signals. Stocks: set to "5m" in apply_tradier_defaults.
    ENTRY_SCORE_THRESHOLD: float = 18.0
    K3M_FLOOR: float = 30.0
    COOLDOWN_BARS: int = 3  # snapshot 2026-04-19: 3-bar cooldown (9min at 3m). Was set to 0, inflated trades 1209→87K, destroyed Sharpe.
    NOLOSS_ENABLED: bool = True  # match live STRICT_NO_LOSS; mark-to-market at sim end handles honesty
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TF: str = "dc_4h"   # crypto: dc_4h. tradier: bb_1h (set in apply_tradier_defaults)
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25
    DC_LOW4_BYPASS_NOLOSS_ENABLED: bool = False   # SWEPT 2026-04-20. VERDICT: PAPER-CUTS. Tradier -14% Sharpe, crypto -94%. DO NOT ENABLE.
    DC_LOW4_BYPASS_MAX_BARS: int = 0              # 0=always active, N=only within N bars of entry (fast-open protection)
    DC_LOW4_BYPASS_USE_STANDARD: bool = False     # True=use dc_low_LTF (standard), False=use dc_low4_LTF (4-bar restricted)
    DC_LOW4_BYPASS_TF: str = ""                   # '' = use LTF (5m tradier / 3m crypto), '15m' = 15m channel (wider stop)
    MIN_HOLD_DC_LOW_BYPASS_ENABLED: bool = False  # When True: if price breaks dc_low_MIN_HOLD_DC_LOW_BYPASS_TF before min_hold bars, force early exit (structural breakdown safety valve)
    MIN_HOLD_DC_LOW_BYPASS_TF: str = "15m"        # DC channel TF for MIN_HOLD early-exit bypass: '5m'/'3m'=tight, '15m'=medium, '1h'=wide
    DC_BREAKOUT_FAILED_STOP_ENABLED: bool = False # SWEPT 2026-04-20 (262 tradier / 49 crypto full-sym). VERDICT: DO NOT ENABLE. Crypto: zero fires (entry filters already prevent breakout-bar entries). Tradier: -15% Sharpe, -3pp WR (positions above BB1h do recover; cutting early = paper-cut). NOTE: v1 had same-bar Donchian bug (close≤high always) — fixed with prev-bar roll; results above are post-fix.
    ALL_TF_BRAKE_ENABLED: bool = False            # SWEPT 2026-04-20 (262 tradier / 49 crypto full-sym). VERDICT: DO NOT ENABLE. Tradier: never fires (no W/M TFs, 5 real TFs don't all flip against before WT exit fires). Crypto: min_tfs≤5 → -36% to -58% Sharpe; min_tfs≥6 → zero effect. Redundant with WT_EXIT_MIN_TFS.
    ALL_TF_BRAKE_MIN_TFS: int = 5                 # minimum TFs (out of LTF/15m/1h/4h/D/W/M) that must be against to trigger brake
    START_POSITION_SIZE: float = 2000.0
    MIN_POSITION_SIZE: float = 55.0
    CT_WT_VELOCITY_GATE_ENABLED: bool = True
    CT_WT_VELOCITY_1H_MIN: float = 9.0   # 2026-04-20 sweep: vel=9+rally=30 → Sharpe 2.598. Was 8.0.
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False
    CT_CHOP_4H_GATE_ENABLED: bool = False
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    RZ_EXIT_ENABLED: bool = True  # re-enabled 2026-04-20: old failure used hardcoded 0.85/80/1.0 — now reads cfg thresholds. TEST_PRIORITY: sweep with RZ_K_EXIT + RZ_TOP_BB_THRESHOLD.
    RZ_K_EXIT: float = 80.0          # TEST_PRIORITY: K extreme threshold for RZ exit. Crypto live=95, stocks live=80. Sweep 65-95.
    RZ_MFI_EXIT: float = 85.0        # TEST_PRIORITY: MFI extreme threshold for RZ exit. Sweep 70-95.
    RZ_TOP_BB_THRESHOLD: float = 0.85  # TEST_PRIORITY: BB %B top-zone gate. Crypto=0.85, stocks=0.85. Sweep 0.75-0.95.
    RZ_BOT_BB_THRESHOLD: float = 0.15  # TEST_PRIORITY: BB %B bottom-zone gate. Sweep 0.05-0.25.
    RZ_BREAKOUT_ENTRY_ENABLED: bool = False  # TEST_PRIORITY: entry when price breaks out of extreme BB zone (oversold→normal for LONG). Sweep True/False.
    RZ_BREAKOUT_NOLOSS_GUARD_ENABLED: bool = True  # legacy — use RZ_BREAKOUT_NOLOSS_MODE instead
    RZ_BREAKOUT_NOLOSS_GUARD_TF: str = "15m"  # legacy — use RZ_BREAKOUT_NOLOSS_MODE instead
    RZ_BREAKOUT_NOLOSS_MODE: str = "dc_low4_15m"  # how to bypass NOLOSS on RZ-breakout entries: bar_structure / dc_low4_base / dc_low_base / dc_low4_15m / dc_low_15m / dc_low_1h / none
    RZ_BREAKOUT_NOLOSS_BAR_WINDOW: int = 4  # for bar_structure mode: how many base-TF bars after entry to watch for lower-high+lower-low
    EXIT_SCORER_ENABLED: bool = False  # TEST_PRIORITY: N-of-5 exit scorer (mirror of wt_dc_exit_scorer). Validated +25.3% in tradier. Sweep True/False.
    EXIT_SCORER_MIN_CONDITIONS: int = 3  # TEST_PRIORITY: how many of 5 exit conditions required. Sweep 2-5.
    EXIT_SCORER_K_EXTREME: float = 75.0  # K extreme threshold for scorer. Sweep 65-85.
    EXIT_SCORER_DC_EXTREME: float = 0.80  # DC position extreme threshold. Sweep 0.70-0.90.
    SATOSHIT_ENABLED: bool = True
    SATOSHIT_MIN_VOTES: int = 3
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = True
    STRUCTURAL_RANGE_SHIFT_TF: str = "dc_4h"
    REENTRY_RALLY_K15M_MAX: float = 30.0   # 2026-04-20 sweep: rally=30 wins with vel=9. Sharpe 2.598. Was 100 (disabled after broken-data fix).
    REENTRY_RALLY_HTF_MIN: int = 1
    # Symmetric exit-would-fire gate — when True, strip entry bars that are simultaneously exit bars.
    # 2026-04-19: Chapter-C winner tested on broken data — re-sweep needed. Default OFF.
    ENTRY_SYMGATE_ENABLED: bool = False
    REENTRY_SYMGATE_ENABLED: bool = False
    # Min-gap cooldown in bars between exit and next entry. 0 = disabled (use COOLDOWN_BARS only).
    REENTRY_MIN_GAP_BARS: int = 5  # snapshot 2026-04-19: 5-bar gap (~15min at 3m). Was set to 0, combined with COOLDOWN=0 → 87K trades vs 1209.
    # Stoch-K zone gate — disabled by default (LONG=0 always passes, SHORT=100 always passes).
    ENTRY_ZONE_LONG: float = 0.0
    ENTRY_ZONE_SHORT: float = 100.0
    ENTRY_ZONE_K_TF: str = "3m"  # crypto default (stocks override to 15m in apply_tradier_defaults)
    HTF_ALIGNMENT_ENABLED: bool = True
    HTF_MIN_ALIGNED: int = 1
    D_TREND_REQUIRED: bool = True
    # Chapter-E conviction scoring — vectorized proxies (live cross-symbol rank not available in NPZ).
    # rank_proxy = HTF agreement count (0-3 from 1h/4h/D WT), scaled to 0-99.
    # dc_moment_proxy = sum of dc_position across 1h/4h/D, scaled to 0-100.
    # winner_protect = block exit_sig when gain in [0, win_protect_gain_pct) AND all 3 HTF agree.
    # 2026-04-19 FIX: RANK_CONVICTION and DC_MOMENT were flagged "Chapter-E winners" on broken
    # data (B15/B11=0 bars → ~0 base trades → any gate looks neutral/good). They are HARD BLOCKS
    # in the vectorized engine but are SCORE BONUSES in live rate() — fundamentally different.
    # Both default OFF. Sweep to re-validate on properly populated trade counts.
    RANK_CONVICTION_ENABLED: bool = False
    RANK_CONVICTION_MIN: int = 1
    DC_MOMENT_ENABLED: bool = False
    DC_MOMENT_OPPOSE_THRESHOLD: float = 40.0  # dc_moment_proxy delta against side = veto
    # 2026-04-19: Chapter-E winner tested on broken data — re-sweep needed. Default OFF.
    WINNER_PROTECT_ENABLED: bool = True  # 2026-04-19: blocks exit when gain<1% + all HTF aligned → sharpe +0.25
    WINNER_PROTECT_GAIN_PCT: float = 1.0
    K_ZONE_ENTRY_ENABLED: bool = False
    K_ZONE_LONG_THRESHOLD: int = 35
    K_ZONE_SHORT_THRESHOLD: int = 65
    MFI_ENTRY_ENABLED: bool = False
    MFI_ENTRY_LONG_MAX: float = 60.0
    MFI_ENTRY_SHORT_MIN: float = 40.0
    VWAP_FILTER_ENABLED: bool = False
    FH_MOMENTUM_ENABLED: bool = False
    DC_DAYTRADE_ENABLED: bool = False
    DC_POSITION_ENTRY_THRESHOLD: float = 0.15
    STOCH_CROSS_1H_EXIT_ENABLED: bool = False
    # Mid-zone stochastic cross: K×D while both between LOW and HIGH on 15m/1h/4h/D.
    # Signal: expected swing got interrupted before reaching extreme → stronger direction change.
    MFI_FLIP_EXIT_ENABLED: bool = False
    MFI_FLIP_EXIT_LONG_THRESHOLD: float = 70.0
    MFI_FLIP_EXIT_SHORT_THRESHOLD: float = 30.0
    WT_CROSSUNDER_FINAL_ENABLED: bool = False
    SIMPLE_MTF_WT_CROSS_EXIT_ENABLED: bool = False  # Test C 2026-04-29: vectorized port of tradier_manage WT_CROSSUNDER_FINAL standalone (LTF down + 15m confirm-or-extreme + ANY HTF against). Mirrors live "simple MTF WT cross" path that DELTA_EXIT gates off in production. A/B vs EXIT_SCORER_ENABLED to test user claim "simple MTF WT cross beats wt_dc_score_exit".
    WT_EXIT_MIN_TFS: int = 3  # 2026-04-19: require all 3 TFs against (was 2) → sharpe 1.065→1.508 before hold boost
    WT_EXIT_USE_CROSS_EVENTS: bool = False  # 2026-04-20: use wt_cross_bear/bull_*m fields for 15m+ (fires at turn only, not all bearish bars)
    MI_EXIT_ENABLED: bool = False
    # Reentry block switches (ablation-validated)
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True
    REENTRY_B04_DC_RETEST_ENABLED: bool = True
    REENTRY_B10_STOCH_REV_ENABLED: bool = True
    REENTRY_B11_DC_BREAK_ENABLED: bool = True
    REENTRY_B12_WT_MOM_ENABLED: bool = True
    REENTRY_B14_HA_TREND_ENABLED: bool = True
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True
    # 2026-04-30 Phase 2: route reentry through position_evaluator.evaluate_reentry_vec (live-equivalent).
    # 2026-04-30 22:15 Phase 2(a) flipped default ON — aligns v8_quick reentries with ez_manage.evaluate_reentry
    # (326x faster vec, byte-equivalent at 30k bar-eval parity). Set False to recover prior lookahead-corrected blocks.
    USE_LIVE_EVALUATOR_VEC: bool = True
    # 2026-04-30 Phase 3a/3b: route per-trade qty through compute_trade_qty_vec.
    # Computes per-symbol average qty modifier (WT_HTF_DISCOUNT, HEDGE_SIZE_CAP,
    # MIN_POS floor) and applies it as a symbol-level pnl weight. OFF preserves
    # existing equal-weight Sharpe semantics. Sweep this flag to expose how much
    # backtest results inflate vs live (where qty discount/cap is real).
    APPLY_QTY_PIPELINE_TO_PNL: bool = False
    WT_HTF_DISCOUNT_ENABLED: bool = True
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0
    MIN_POSITION_SIZE: float = 55.0
    # 2026-05-01 Phase 5: options-OI signal injection (READ-ONLY, tradier path).
    # Uses snapshot from data/stocks_oi_cache/{sym}.json (populated by tradier_options_oi_fetcher.py).
    # Snapshot-static across all backtest bars — measures whether the live signal has edge today.
    # Default OFF. NEVER routes orders through options endpoints — read-only sentiment proxy.
    OPTIONS_OI_BACKTEST_ENABLED: bool = False
    OPTIONS_OI_RED_ZONE_GATE: bool = True              # block LONG within X% below max_call_oi_strike, SHORT within X% above max_put_oi_strike
    OPTIONS_OI_RED_ZONE_DIST_PCT: float = 0.5          # distance threshold (matches RED_ZONE_TRADIER_MIN_DISTANCE_PCT)
    OPTIONS_OI_RED_ZONE_MIN_OI: int = 1000             # minimum contracts at wall to count
    OPTIONS_OI_DEEP_HEATMAP: bool = True               # iterate top_call_walls/top_put_walls (not just max strike)
    OPTIONS_OI_PC_INJECT_ENABLED: bool = False         # P/C ratio additive entry signal (sentiment-extreme contrarian)
    OPTIONS_OI_PC_BULLISH_THRESH: float = 0.6          # near_money_pc < this → call OI dominates → LONG bias
    OPTIONS_OI_PC_BEARISH_THRESH: float = 1.4          # near_money_pc > this → put OI dominates → SHORT bias
    OPTIONS_OI_MIN_TOTAL_OI: int = 1000                # require ≥1000 contracts open across monitored exps
    OPTIONS_OI_CACHE_DIR: str = "data/stocks_oi_cache"
    # 2026-04-30 Phase 2 reentry retrofit: same-side reentry window after a recent exit.
    # Live system fires GUARANTEED_PRICE_CROSS / DIRECTION_FAVORABLE / GUARANTEED_REENTRY paths
    # ~2,000+/day combined. v8_quick already routes reentry blocks through compute_entry_signals
    # (gated by HTF/score), but this window-based path bypasses HTF and fires off the raw vec
    # (B15/B11/B04/B12 etc) within MAX_AGE_BARS of last exit. Default OFF — preserves prior runs.
    REENTRY_ENABLED: bool = False
    REENTRY_RULES_ENABLED: tuple = ('B04', 'B11', 'B12', 'B15')
    REENTRY_MAX_AGE_BARS: int = 30
    REENTRY_PHASE2_MIN_GAP_BARS: int = 1
    # 2026-04-30 Phase 4: route process_position exit gates (WT_4H_VEL_EXIT,
    # DC_HOPELESS_EXIT, WT_EXHAUST_EXIT, WT_PERCENTILE_EXIT, E_1 delta, E_3 structure)
    # through position_evaluator.evaluate_exit_gates_vec. Default OFF preserves existing
    # backtest semantics; sweep this to align backtest exits with live process_position.
    USE_PROCESS_POSITION_EXIT_GATES: bool = False
    WT_4H_VEL_EXIT_ENABLED: bool = True
    WT_4H_VEL_EXIT_LONG_VEL_MIN: float = -2.0
    WT_4H_VEL_EXIT_SHORT_VEL_MIN: float = 2.0
    WT_4H_VEL_EXIT_REQUIRE_PROFIT: bool = True
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME: bool = True
    WT_4H_VEL_EXIT_K_EXTREME_HIGH: float = 80.0
    WT_4H_VEL_EXIT_K_EXTREME_LOW: float = 20.0
    DC_HOPELESS_EXIT_ENABLED: bool = True
    DC_HOPELESS_EXIT_MIN_AGE_S: float = 900.0
    WT_EXHAUST_EXIT_ENABLED: bool = True
    WT_EXHAUST_EXIT_REQUIRE_GAIN: bool = False
    WT_PERCENTILE_EXIT_ENABLED: bool = True
    WT_PERCENTILE_EXIT_OB_D: float = 90.0
    WT_PERCENTILE_EXIT_OB_4H: float = 75.0
    WT_PERCENTILE_EXIT_OS_D: float = 10.0
    WT_PERCENTILE_EXIT_OS_4H: float = 25.0
    E_1_WT_EXIT_USE_DELTA_ENABLED: bool = False
    E_1_EXIT_DELTA_THR: float = 50.0
    E_3_USE_WT_STRUCTURE_EXIT_MODE: int = 0
    COMMISSION_BUFFER_PCT: float = 0.10
    # PULLBACK-FIRST entry blocks (2026-04-16) — OPT-IN: Sharpe 0.80 test, keep OFF until proven
    REENTRY_PULL1_ENABLED: bool = False  # HTF uptrend + LTF deep oversold + reversing
    REENTRY_PULL2_ENABLED: bool = False  # Rising fundamentals + SMA200 pullback bounce
    REENTRY_PULL3_ENABLED: bool = False  # BB lower-band + trend up + stoch cross
    REENTRY_PULL4_ENABLED: bool = False  # RSI pullback + HTF healthy + wt bouncing
    # TRADIER alpha blocks — wired into reentry blocks dict for tradier mode (2026-04-16)
    # K_ZONE: live Sharpe 4.23 on 20,605 trades. FH_MOMENTUM: Sharpe 1.54 validated.
    REENTRY_B_KZONE_ENABLED: bool = False  # tradier auto-enables via apply_tradier_defaults
    REENTRY_B_FH_MOM_ENABLED: bool = False
    REENTRY_B_MFI_D_OVERSOLD_ENABLED: bool = False
    # === 2026-04-17 HEDGE + REENTRY OVERHAUL SWITCHES ===
    # Hedge: snapshot 2026-04-19 had hedge as LIVE-ONLY (no simulation). Simulation defaults OFF to match baseline.
    HEDGE_ENABLED: bool = False  # snapshot baseline: no hedge sim. True inflated trades 1209→84K due to no min-hold. Fixed: HEDGE_MIN_HOLD_BARS prevents micro-hedges.
    HEDGE_MIN_HOLD_BARS: int = 10  # min bars before closing a hedge (prevents 3m micro-hedge churn). 10 bars = 30 min.
    HEDGE_WT_KILL_CONFIRM_TF: str = '1h'  # TF confirmation for WT-based hedge kill: 'none'=LTF only, '15m'=LTF+15m, '1h'=LTF+1h
    HEDGE_EXIT_BYPASS_NOLOSS: bool = True
    HEDGE_EXIT_WT_TF: str = "3m"
    HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: bool = True
    HEDGE_SAME_SYMBOL_PCT: float = 1.0
    HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE: bool = True
    # === 2026-04-26 hedge gate switches (sweep-testable in autonomous_search) ===
    HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = False  # reject hedge open if symbol at DC extreme against hedge direction
    HEDGE_DC_LONG_REJECT_DCP: float = 0.85          # LONG-hedge: reject if dc_position_1h/4h < this (i.e. symbol at resistance for SHORT defending LONG main)
    HEDGE_DC_SHORT_REJECT_DCP: float = 0.15         # SHORT-hedge: reject if dc_position_1h/4h > this
    HEDGE_WT_VEL_GATE_ENABLED: bool = False         # reject hedge open if wt_velocity 1h+4h decel against hedge direction
    HEDGE_DETERIORATING_GAIN_ENABLED: bool = False  # require live_pnl actively dropping (not flat) before hedge fires
    HEDGE_DETERIORATING_GAIN_DELTA_PP: float = 0.10 # min pp drop from K bars ago to qualify
    HEDGE_DETERIORATING_GAIN_WINDOW_BARS: int = 5   # K bars lookback (5×3m=15min on crypto)
    # ===== ENGINE-RETROFIT 2026-04-30 — declared on dataclass for sweep visibility =====
    # E1. WT_DC_ENTRY (registry: docs/engine_retrofit_registry.md)
    WT_DC_ENTRY_ENABLED: bool = False
    WT_DC_ENTRY_LONG_THRESHOLD: int = 55
    WT_DC_ENTRY_SHORT_THRESHOLD: int = 55
    WT_DC_ENTRY_W_HTF_D: float = 25.0
    WT_DC_ENTRY_W_HTF_4H: float = 25.0
    WT_DC_ENTRY_W_LTF_1H: float = 30.0
    WT_DC_ENTRY_W_DC_1H: float = 10.0
    WT_DC_ENTRY_W_K_5M: float = 10.0
    WT_DC_ENTRY_DC1H_LONG_MAX: float = 0.50
    WT_DC_ENTRY_DC1H_SHORT_MIN: float = 0.50
    WT_DC_ENTRY_K5M_LONG_MAX: float = 40.0
    WT_DC_ENTRY_K5M_SHORT_MIN: float = 60.0
    # X1. PEAK_GIVEBACK
    PEAK_GIVEBACK_ENABLED: bool = False
    PEAK_GIVEBACK_PEAK_MIN_PCT: float = 0.50
    PEAK_GIVEBACK_DROP_PCT: float = 0.5
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = False
    PEAK_GIVEBACK_MIN_AGE_BARS: int = 0
    # X2. BE_EROSION
    BE_EROSION_ENABLED: bool = False
    BE_EROSION_AGE_MIN_BARS: int = 30
    BE_EROSION_MIN_GAIN: float = 0.0
    BE_EROSION_REQUIRE_PROFIT: bool = True
    BE_EROSION_COMM_BUFFER: float = 0.05
    # X3. K1M_EXTREME_REVERSE (blocked when stoch_k_1m zero-filled in NPZ)
    K1M_EXTREME_REVERSE_ENABLED: bool = False
    K1M_EXTREME_HIGH: float = 90.0
    K1M_EXTREME_LOW: float = 10.0
    K1M_REVERSE_REQUIRES_PROFIT: bool = True
    # X4. STRONG_REDUCE_K
    STRONG_REDUCE_K_ENABLED: bool = False
    SRK_K15M_LONG_MIN: float = 80.0
    SRK_K1H_LONG_MAX: float = 30.0
    SRK_K15M_SHORT_MAX: float = 20.0
    SRK_K1H_SHORT_MIN: float = 70.0
    SRK_REDUCE_FRAC: float = 0.5
    # E5. HEDGE_PROTECT_LOSS — fires opposite-side bookmark when primary at loss + WT against
    HEDGE_PROTECT_LOSS_ENABLED: bool = False
    HPL_TRIGGER_LOSS_PCT: float = -2.0
    HPL_HEDGE_FRAC: float = 1.0
    HPL_FIRE_ONCE: bool = True
    HPL_COOLDOWN_BARS: int = 360
    # E6. WINNER_AUGMENT — average-in on a winner ≥ WA_MIN_GAIN_PCT
    WINNER_AUGMENT_ENABLED: bool = False
    WA_MIN_GAIN_PCT: float = 1.5
    WA_MAX_AUGMENTS: int = 2
    WA_GAIN_GROWTH_REQ: float = 0.5
    # wt_D bounce augment — add to losing position when daily WT bounces with higher WT and/or higher price
    AUGMENT_WT_D_BOUNCE_ENABLED: bool = False
    AUGMENT_WT_D_MULTIPLIER: float = 2.0  # total size after augment (2.0 = double, 3.0 = triple, etc.)
    AUGMENT_WT_D_REQUIRE_HIGHER_WT: bool = False   # bounce wt1_D > prev bounce level (sweep showed False wins)
    AUGMENT_WT_D_REQUIRE_HIGHER_PRICE: bool = False  # sweep showed True never fires — price still below entry when wt_D bounces
    AUGMENT_WT_D_CONTINUE_EXIT_ENABLED: bool = False  # NOLOSS bypass: exit full pos if price continues below aug entry after D-bounce
    # wt_4h bounce augment — fires ~6x more often than wt_D; independent _aug_4h_done flag allows both to fire once each
    AUGMENT_WT_4H_BOUNCE_ENABLED: bool = False
    AUGMENT_WT_4H_MULTIPLIER: float = 2.0
    AUGMENT_WT_4H_REQUIRE_HIGHER_WT: bool = False
    AUGMENT_WT_4H_REQUIRE_HIGHER_PRICE: bool = False
    AUGMENT_PT_ENABLED: bool = False  # take profit on augmented positions as soon as they recover
    AUGMENT_PT_PCT: float = 0.5  # exit augmented position once live_pnl >= this %
    # 60-min unconditional reentry — if price continued ≥MIN_PCT in trade direction within WINDOW_BARS, reenter at 50% size
    QUICK_REENTRY_60MIN_ENABLED: bool = False
    QUICK_REENTRY_60MIN_WINDOW_BARS: int = 12   # 12 bars × 5m = 60min
    QUICK_REENTRY_60MIN_MIN_PCT: float = 0.3    # price must move ≥0.3% from exit price in trade direction
    # Reentry WT-15m-cross + HTF-aligned (C) — wired in vectorized block REENTRY_B_WT15M_CROSS below
    REENTRY_WT15M_CROSS_ENABLED: bool = True
    REENTRY_WT15M_SIZE_MULT: float = 1.5
    REENTRY_WT15M_K_MAX: float = 50.0
    REENTRY_WT15M_HTF_FAVOR_REQUIRED: bool = True
    # Reentry k_15m partial sizing (D) — affects sizing for any reentry block when K in extreme zone
    REENTRY_K15M_PARTIAL_ENABLED: bool = True
    REENTRY_K15M_PARTIAL_THRESHOLD: float = 80.0
    REENTRY_K15M_PARTIAL_MULT: float = 0.5
    # Reentry post-consolidation boost (E) — sizing mult when ATR compressed on 2+ TFs
    REENTRY_POST_CONSOL_ENABLED: bool = True
    REENTRY_POST_CONSOL_MULT: float = 1.5
    REENTRY_POST_CONSOL_ATR_THRESHOLD: float = 0.15
    REENTRY_POST_CONSOL_TFS_REQUIRED: int = 2
    FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5  # % move in first hour
    FH_MOMENTUM_WINDOW_SEC: int = 3600  # 60 min window after open
    MFI_LONG_THRESHOLD_D: float = 20.0  # mfi_D < 20 for long
    MFI_SHORT_THRESHOLD_D: float = 80.0  # mfi_D > 80 for short
    # Velocity-decay exit — OPT-IN: hasn't been validated vs V8Q v3 baseline (test first)
    WT_VEL_DECAY_EXIT_ENABLED: bool = False
    WT_VEL_DECAY_THRESHOLD: float = 1.0
    # NEW: Confluence mode — only enter when N blocks agree
    CONFLUENCE_MODE_ENABLED: bool = False
    CONFLUENCE_MIN_BLOCKS: int = 2  # How many blocks must agree simultaneously
    # NEW: Signal strength filter — only take top-percentile setups
    # 2026-04-19 FIX: was 5.0, calibrated when B15(w=4)+B11(w=3) were live but NPZ DC-band bug
    # made them permanently 0 bars → max achievable score was 5 on ~0 bars → 0 trades.
    # B_PRICE_CROSS_K90 and B_WT15M_CROSS now weighted=4 (primary live triggers); score=3
    # allows B_PRICE_CROSS_K90 alone (w=4≥3) or B_WT15M_CROSS alone (w=4≥3). Sweep can raise.
    STRENGTH_FILTER_ENABLED: bool = True
    STRENGTH_MIN_SCORE: float = 5.0  # 2026-04-19 sweep: score=5 filters to high-quality entries
    # 2026-04-30 Job 1 (i): per-sector multiplier on STRENGTH_MIN_SCORE (tradier mode only).
    # Value <1.0 = LOOSER entry threshold (more trades) for that sector;
    # >1.0 = TIGHTER. None or empty dict = no tilt (default-bound back-compat).
    # Sector lookup uses stocks_sectors.json. Untagged symbols: no tilt (mult=1.0).
    SECTOR_ENTRY_STRENGTH_MULT_TRADIER: Optional[Dict[str, float]] = None
    # Holding period enforcement (avoid rapid exit noise) — WINNER: 10
    MIN_HOLD_BARS: int = 250  # 2026-04-19: 12.5h minimum hold — prevents premature exits at small gains. Sharpe 1.508→2.554.
    # Stop loss exit (sweep-only — cap max loss)
    STOP_LOSS_ENABLED: bool = False
    STOP_LOSS_PCT: float = 2.0  # Exit at this loss %

    # ===== 2026-04-20 EXIT WT/DC AUDIT SWITCHES (all default OFF) =====
    WT_MOMENTUM_EXIT_ENABLED: bool = False
    WT_MOMENTUM_EXIT_TF: str = "1h"
    WT_MOMENTUM_EXIT_THRESHOLD: int = 0
    WT_STRUCT_EXIT_ENABLED: bool = False
    WT_STRUCT_EXIT_TF: str = "1h"
    WT_DIV_EXIT_ENABLED: bool = False
    WT_DIV_EXIT_TF: str = "1h"
    WT_PERCENTILE_EXIT_ENABLED: bool = False
    WT_PERCENTILE_EXIT_TF: str = "1h"
    WT_PERCENTILE_EXIT_THRESHOLD: float = 80.0
    WT_ZSCORE_EXIT_ENABLED: bool = False
    WT_ZSCORE_EXIT_TF: str = "1h"
    WT_ZSCORE_EXIT_THRESHOLD: float = 2.0
    WT_ACCEL_EXIT_ENABLED: bool = False
    WT_ACCEL_EXIT_TF: str = "1h"
    WT_WAVE_PHASE_EXIT_ENABLED: bool = False
    WT_WAVE_PHASE_EXIT_TF: str = "1h"
    WT_SCORE_FLIP_EXIT_ENABLED: bool = False
    WT_SCORE_FLIP_EXIT_TF: str = "3m"
    WT_VEL_MTF_EXIT_ENABLED: bool = False
    WT_VEL_MTF_EXIT_MIN_TFS: int = 3
    WT_VEL_MTF_EXIT_THRESHOLD: float = -1.0
    WT_ALIGN_EXIT_ENABLED: bool = False
    WT_ALIGN_EXIT_MIN: int = 2
    WT_COMP_DELTA_EXIT_ENABLED: bool = False
    WT_COMP_DELTA_EXIT_THRESHOLD: float = 0.0
    DC_POS_EXIT_ENABLED: bool = False
    DC_POS_EXIT_THRESHOLD: float = 0.7
    # WT velocity floor — exit when wt_velocity_1h crosses below zero (LONG) / above zero (SHORT)
    # Fires 2-4 bars earlier than full WT cross. Threshold=0.0 = pure zero-crossing.
    WT_VEL_FLOOR_EXIT_ENABLED: bool = False
    WT_VEL_FLOOR_EXIT_LONG_MAX: float = 0.0   # exit LONG when wt_velocity_1h < this (0=zero-crossing)
    # k_D overbought + wt_1h bear — daily stoch exhausted AND 1h WT confirms turn
    # Filters out mid-cycle corrections that satisfy wt_1h alone
    KD_WT1H_EXIT_ENABLED: bool = False
    KD_WT1H_EXIT_OVERBOUGHT: float = 80.0     # k_D >= this for LONG, <= (100-this) for SHORT
    # Adaptive WT_EXIT_MIN_TFS — protect deep winners with looser exit gate
    # When pnl > GAIN_PCT: accept WT_EXIT_MIN_TFS-1 TFs against (faster exit, bank profits)
    ADAPTIVE_EXIT_TFS_ENABLED: bool = False
    ADAPTIVE_EXIT_TFS_GAIN_PCT: float = 2.0   # pnl % above which looser gate activates
    # ===== 2026-04-18 INDICATOR-AUDIT EXPERIMENTAL SWITCHES (all default OFF) =====
    # R-G2: MTF WT velocity alignment gate — wt_velocity_up_count/down_count in NPZ (5-TF count)
    WT_MTF_VEL_GATE_ENABLED: bool = False
    WT_MTF_VEL_MIN: int = 3          # how many of 5 TFs must show aligned velocity
    # RE-1: cross freshness gate — only reenter when a WT cross fired < N bars ago on any LTF
    REENTRY_CROSS_FRESHNESS_ENABLED: bool = False
    REENTRY_CROSS_MAX_BARS_AGO: int = 5   # bars; fresh cross required on 3m, 15m, or 1h

    # ===== D4 BREAKOUT MULTI-LUNG (2026-04-16, default OFF, awaiting Sharpe>2 sweep proof) =====
    # Origin: ez_breakout_agent.py (70 KB orphaned). See DIAMOND_DIFF_2026-04-16.md verdict D4=EXTRACT.
    # NEVER flip ENABLED=True in live config before 48-crypto × 4yr + 128-tradier × 2yr sweep > 2 Sharpe.
    BREAKOUT_MULTI_LUNG_ENABLED: bool = False
    BREAKOUT_MULTI_LUNG_MODE: str = "AUGMENT"  # "AUGMENT" (OR into existing signals) | "REPLACE" (use only multi-lung)
    BREAKOUT_MULTI_LUNG_TIER: str = "CRYPTO"   # "CRYPTO" | "MOVER" | "STOCK"
    BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20
    BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.10
    BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15
    BREAKOUT_MULTI_LUNG_COOLDOWN_BARS: int = 4  # bars between multi-lung entries

    # ===== ENTRY ENGINES (2026-04-26) — additive fire-on-alignment triggers =====
    # Pure vectorized adaptations of entry_engine_{wt,stoch,dc,htf}.py scalar engines.
    # ADDITIVE: when any flag True, engine sig is ORed/ANDed/MAJORITY-voted alongside
    # existing base_sig. Never replaces. Default OFF until sweep proves Sharpe > floor.
    V8_ENTRY_ENGINE_WT_ENABLED: bool = False
    V8_ENTRY_ENGINE_STOCH_ENABLED: bool = False
    V8_ENTRY_ENGINE_DC_ENABLED: bool = False
    V8_ENTRY_ENGINE_HTF_ENABLED: bool = False
    V8_ENTRY_ENGINE_COMBINE: str = "OR"   # "OR" | "AND" | "MAJORITY" — combine engine sigs to single mask
    V8_ENTRY_ENGINE_MIN_SCORE: float = 0.5  # per-engine score floor; bars below excluded from engine sig

    # ===== Early-abort rule (2026-04-16 user directive) =====
    # Stop evaluating a config if after N symbols its Sharpe < floor. Saves compute
    # on combos that clearly don't clear the baseline. Only fires when stores has
    # >= EARLY_ABORT_MIN_SYMBOLS, so small fast sweeps (11/12 syms) run to completion.
    EARLY_ABORT_ENABLED: bool = True
    EARLY_ABORT_MIN_SYMBOLS: int = 15
    EARLY_ABORT_SHARPE_FLOOR: float = 2.5
    EARLY_ABORT_TIME_LIMIT_SEC: float = 60.0  # kill config if wall-time > this AND sharpe < floor

    # ===== Auto-hooked Group B switches (2026-04-16) =====
    # 229 switches from config_tradier.py/config.py, defaults preserved.
    ADX_TRENDING_THRESHOLD: float = 25.0
    ALIGNMENT_GATE_TOTAL: int = 12
    ATR_ADAPTIVE_SIZING_ENABLED: bool = False
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0
    ATR_ADAPTIVE_STOP_ENABLED: bool = False
    ATR_ADAPTIVE_STOP_MULT: float = 2.0
    ATR_ADAPTIVE_STOP_TF: str = '1h'
    ATR_LONG_WINDOW: int = 100
    ATR_TRAIL_ENABLED_TRADIER: bool = False
    BB_ENTRY_LONG_THRESHOLD: float = -0.2
    BB_ENTRY_SHORT_THRESHOLD: float = 1.0
    BB_SQUEEZE_ENTRY_ENABLED: bool = False  # 2026-04-16: off until proven, was default True from agent
    BB_SQUEEZE_THRESHOLD_15M: float = 0.025
    BB_SQUEEZE_THRESHOLD_1H: float = 0.03
    CHOP_RANGING_THRESHOLD: float = 61.8
    CHOP_TRENDING_THRESHOLD: float = 38.2
    COOLDOWN_BARS_TRADIER: int = 8
    CT_CHOP_4H_MAX: float = 50.0
    CT_MFI_15M_LONG_MIN: float = 45.0
    CT_MFI_15M_SHORT_MAX: float = 55.0
    CT_REL_VOL_MIN: float = 1.3
    CT_STOCH_K_15M_LONG_MIN: float = 45.0
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0
    CYCLE_TP_CONDITIONAL_EXIT: float = 0.003
    CYCLE_TP_PCT: float = 0.6
    CYCLE_TP_TIERED_ENABLED: bool = True
    CYCLE_TP_TIERED_FRAC: float = 0.25
    DC_EDGE_SIZING_ENABLED: bool = True
    DC_EDGE_SIZING_MAX_MULT: float = 3.0
    DC_EDGE_SIZING_MIN_MULT: float = 1.0
    DC_EDGE_SIZING_PERIOD: int = 20
    DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT: float = 0.0
    DC_WIDTH_CAP_MULT: float = 10.0
    DELTA_COOLDOWN_BARS: int = 60
    DELTA_EXIT_DC_FLOOR: bool = True
    DELTA_EXIT_OVERRIDE_NOLOSS: bool = True
    DELTA_EXIT_TYPE: str = "speed_decay"
    DELTA_EXIT_WT_CROSS: bool = True
    DELTA_GATE_AUGMENT: bool = True
    DELTA_GATE_BB_SQUEEZE: bool = True
    DELTA_GATE_DC_BREAKOUT: bool = True
    DELTA_GATE_GUARANTEED_REENTRY: bool = True
    DELTA_GATE_HEDGE_OPEN: bool = False
    DELTA_GATE_OPEN: bool = True
    DELTA_GATE_RATIO_REBALANCE: bool = False
    DELTA_GATE_REENTRY: bool = True
    DELTA_GATE_SBA: bool = False
    DELTA_GATE_STDEV_BREAKOUT: bool = True
    DELTA_GATE_VOL_SPIKE: bool = True
    DELTA_MAX_HOLD_BARS: int = 0
    DELTA_PYRAMID_ENABLED: bool = False
    DELTA_REENTRY_HTF_GATE: str = '4h'
    DELTA_REENTRY_MIN_TF: int = 2
    DELTA_REENTRY_REQUIRE_NOT_EXITING: bool = True
    DELTA_REENTRY_Z_THRESHOLD: float = 1.0
    EMA20_SLOPE_ENTRY_ENABLED: bool = True
    EMA20_SLOPE_SHORT_THRESHOLD_1H: float = 0.05
    EMA_DIST_ENTRY_ENABLED: bool = True
    EMA_DIST_LONG_THRESHOLD: float = -1.0
    EMA_DIST_SHORT_THRESHOLD: float = 1.0
    EMA_DIST_SIZING_ENABLED: bool = True
    EMA_DIST_SIZING_MULT: float = 2.0
    ENTRY_TRIGGER_TF: str = '15m'
    HA_3M_ENTRY_WEIGHT: float = -0.5
    HOLD_BARS_CLOSE: int = 50
    HOLD_BARS_MID: int = 500
    INTRADAY_SESSION_EXIT_ENABLED: bool = False
    INTRADAY_SESSION_ENTRY_CUTOFF_UTC: int = 57600
    INTRADAY_SESSION_FORCE_EXIT_UTC: int = 70200
    HOLD_BARS_OPEN: int = 200
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    LUNCH_DEADZONE_SIZE_MULT: float = 0.5
    MIN_EXIT_TF_AGAINST_TRADIER: int = 2
    MIN_HOLD_BARS_BEFORE_EXIT: int = 32
    MIN_HOLD_MINUTES_TRADIER: float = 30.0
    MIN_PERC_FROM_SMA_1: float = 0.01
    MIN_PERC_FROM_SMA_15: float = 0.03
    MI_ENTRY_ENABLED: bool = False
    MI_ENTRY_ENABLED_TRADIER: bool = False
    MI_ENTRY_STRUCT_BONUS: int = 10
    MI_ENTRY_EXHAUST_BONUS: int = 8
    MI_EXIT_ENABLED_TRADIER: bool = True
    CLENOW_ENABLED: bool = False
    CLENOW_SCORE_MIN: float = 30.0
    CLENOW_SCORE_SHORT_MAX: float = -10.0
    CLENOW_GATE_ONLY: bool = True
    CONNORS_RSI_ENABLED: bool = False
    CONNORS_RSI_ENTRY_LONG_MAX: float = 15.0
    CONNORS_RSI_ENTRY_SHORT_MIN: float = 85.0
    CONNORS_RSI_GATE_ONLY: bool = False
    EXIT_STDEV_BREAKOUT_FAIL_ENABLED: bool = False
    EXIT_STDEV_REJECTION_HIGH: float = 1.0
    EXIT_STDEV_REJECTION_RETURN: float = 0.85
    EXIT_STDEV_REJECTION_TF: str = "4h"
    MOM3_ENTRY_ENABLED: bool = False  # 2026-04-16: off until proven
    MOM3_LONG_THRESHOLD: float = -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0
    MOM5_ENTRY_ENABLED: bool = False  # 2026-04-16: off until proven
    MOM5_LONG_THRESHOLD: float = -1.0
    MOM5_SHORT_THRESHOLD: float = 1.0
    PYRAMID_ENABLED: bool = False
    PYRAMID_MAX_DC_POS_15M_SHORT: float = 0.3
    PYRAMID_MIN_DC_POS_15M: float = 0.7
    PYRAMID_MIN_GAIN_PCT: float = 1.5
    PYRAMID_MIN_WT_VEL_1H: float = 2.0
    PYRAMID_SIZE_MULT: float = 0.5
    REENTRY2_DC_BREAK_ENABLED: bool = True
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True
    REENTRY2_STOCH_CROSS_ENABLED: bool = True
    REENTRY_2_ENABLED: bool = True
    REENTRY_B09_SNAPBACK_ENABLED: bool = False
    REENTRY_COOLDOWN_S: float = 0.0
    REENTRY_MANDATORY: bool = False  # 2026-04-16: off — was forcing reentries
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5
    REGIME_ADAPTIVE_ENABLED: bool = False
    REGIME_ATR_RATIO_MIN: float = 0.25
    REGIME_BB_WIDTH_PCT_MIN: float = 2.0
    REGIME_BTC_MARKET_WEIGHT: float = 0.5
    REGIME_DC_ATR_RATIO_MIN: float = 1.5
    REGIME_ENTER_TRENDING_THRESHOLD: float = 30.0
    REGIME_EXIT_TRENDING_THRESHOLD: float = 15.0
    REGIME_GATE_ENABLED: bool = False
    REGIME_MIN_DWELL_BARS: int = 16
    REGIME_RANGING_DC_BREAKOUT_SCORE: int = 0
    REGIME_RANGING_EXIT_GAIN_MIN: float = 0.15
    REGIME_RANGING_K_ZONE_BONUS: int = 40
    REGIME_RANGING_MIN_HOLD_BARS: int = 8
    REGIME_RANGING_NOLOSS_MIN: float = 0.05
    REGIME_RANGING_POSITION_SIZE_MULT: float = 0.5
    REGIME_RANGING_REENTRY_SIZE_MULT: float = 1.0
    REGIME_RANGING_SLOT_RESERVE_PCT: float = 0.6
    REGIME_RANGING_STALE_HOURS: float = 48.0
    REGIME_RANGING_STALE_MIN_PROFIT: float = 0.02
    REGIME_RANGING_WT_EXIT_VEL: float = -3.0
    REGIME_RANGING_WT_REDUCE_FRAC_LOW: float = 0.4
    REGIME_RANGING_WT_REDUCE_FRAC_MED: float = 0.6
    REGIME_TRENDING_DC_BREAKOUT_SCORE: int = 30
    REGIME_TRENDING_EXIT_GAIN_MIN: float = 2.0
    REGIME_TRENDING_K_RESET_THRESHOLD: float = 40.0
    REGIME_TRENDING_K_ZONE_BONUS: int = 15
    REGIME_TRENDING_MIN_HOLD_BARS: int = 48
    REGIME_TRENDING_NOLOSS_MIN: float = 0.5
    REGIME_TRENDING_POSITION_SIZE_MULT: float = 1.5
    REGIME_TRENDING_REENTRY_SIZE_MULT: float = 2.0
    REGIME_TRENDING_SLOT_RESERVE_PCT: float = 0.4
    REGIME_TRENDING_WT_EXIT_VEL: float = -12.0
    REGIME_TRENDING_WT_REDUCE_FRAC_LOW: float = 0.1
    REGIME_TRENDING_WT_REDUCE_FRAC_MED: float = 0.15
    RSI_ENTRY_GATE_ENABLED: bool = False
    RSI_ENTRY_MAX_LONG: float = 37.0
    RSI_ENTRY_MIN_SHORT: float = 63.0
    RSI_EXIT_LONG_TRADIER: float = 85.0
    RSI_EXIT_SHORT_TRADIER: float = 15.0
    RSI_MOMENTUM_MODE: bool = False
    RZ_K_ENTRY_BOTTOM: float = 10.0
    RZ_MFI_ENTRY_BOTTOM: float = 15.0
    SATOSHIT_EXIT_ENABLED: bool = False  # 2026-04-16: off — duplicates existing sat_exit, caused Sharpe regression
    SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER: float = 60.0
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.7
    SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER: float = 42.0
    SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER: float = 50.0
    SATOSHIT_HTF_MFI_D_MIN_TRADIER: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN_TRADIER: float = 0.3
    SATOSHIT_LONG_BB_PCTB_MAX: float = 0.5
    SATOSHIT_LONG_HA_STREAK_MAX: int = 1
    SATOSHIT_LONG_MFI_MAX_TRADIER: float = 60.0
    SATOSHIT_LONG_RSI_MAX_TRADIER: float = 50.0
    SATOSHIT_LONG_STOCH_K_MAX_TRADIER: float = 60.0
    SATOSHIT_MIN_VOTES_TRADIER: int = 3
    SATOSHIT_SHORT_BB_PCTB_MIN: float = 0.55
    SATOSHIT_SHORT_HA_STREAK_MIN: int = 0
    SATOSHIT_SHORT_MFI_MIN_TRADIER: float = 50.0
    SATOSHIT_SHORT_RSI_MIN_TRADIER: float = 55.0
    SATOSHIT_SHORT_STOCH_K_MIN_TRADIER: float = 50.0
    SMA200_DIST_LONG_THRESHOLD: float = -3.0
    SQUEEZE_ENABLED: bool = False
    STDEV_BREAKOUT_ENABLED: bool = False
    STDEV_BREAKOUT_PCTB_LONG: float = 1.0
    STDEV_BREAKOUT_PCTB_SHORT: float = -0.125
    STDEV_BREAKOUT_HTF_LIST: list = None
    STDEV_BREAKOUT_RVOL_MIN: float = 1.2
    STDEV_SUPPRESS_EARLY_EXIT: bool = False
    STDEV_BB_RZ_EXIT_ENABLED: bool = False
    STDEV_BB_RZ_EXIT_TF: str = "D"
    STDEV_BB_RZ_SUPPRESS_PCTB: float = 0.85
    STDEV_BOUNCE_ENABLED: bool = False
    STDEV_BOUNCE_PCTB_LONG: float = 0.05
    STDEV_BOUNCE_PCTB_SHORT: float = 0.95
    STDEV_BOUNCE_RVOL_MIN: float = 1.2
    STDEV_BOUNCE_HTF_LIST: list = None
    STDEV_REJECT_EXIT_ENABLED: bool = False
    STDEV_REJECT_EXIT_TF: str = "D"
    STDEV_REJECT_EXIT_ZONE: float = 0.80
    STDEV_REJECT_EXIT_RETURN: float = 0.65
    HLR_RALLY_ENABLED: bool = True
    HLR_PTS_1H: int = 40
    HLR_PTS_4H: int = 60
    HLR_SMA_BAND_PCT: float = 0.03
    HLR_OFF_SMA_PTS_FRAC: float = 0.5
    STOCH_CROSS_3M_EXIT_ENABLED: bool = False
    STOCH_CROSS_ENTRY_TRADIER: bool = False
    TF_ALIGNMENT_MIN_LONG: int = 2
    TF_ALIGNMENT_MIN_SHORT: int = 2
    TF_ALIGNMENT_MIN_TOTAL: int = 4
    TF_FOCUS_ENTRY_HARD_GATE: bool = False  # 2026-04-16: off until sweep-proven
    TF_FOCUS_EXIT_HARD_GATE: bool = False  # 2026-04-16: off until sweep-proven
    TF_FOCUS_WEIGHT: float = 8.0
    TF_HTF1: str = "1h"
    TF_HTF3: str = "D"
    TF_MACRO: str = "D"
    TRADIER_DC_DAYTRADE_ENABLED: bool = True
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 240
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.005
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.15
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 24
    TRADIER_FH_MOMENTUM_DC_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_DC_MAX_LONG: float = 0.33
    TRADIER_FH_MOMENTUM_ENABLED: bool = True
    TRADIER_FH_MOMENTUM_MFI_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_MFI_MIN: float = 55.0
    TRADIER_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    TRADIER_FH_MOMENTUM_WINDOW_MINUTES: int = 60
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = True
    TRADIER_MFI_ENTRY_LONG_TRADIER: float = 60.0
    TRADIER_MI_ENTRY_ENABLED_TRADIER: bool = False
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = True
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3
    TRADIER_RSI2_ENABLED: bool = False  # 2026-04-16: off until backtested tradier-only
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 90.0
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 10.0
    TRADIER_RSI_ENTRY_LONG_TRADIER: float = -1.0
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 70.0
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 1.2
    TRADIER_STOCH_ENTRY_LONG_TRADIER: int = 30
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: int = 70
    TRADIER_STOCH_EXTREME_LONG_TRADIER: int = 15
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: int = 85
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    TRB_NOLOSS_MIN_PROFIT_PCT: float = 0.0
    UNIVERSAL_NOLOSS_GATE: bool = True
    VOLUME_CONFIRMATION_ENABLED: bool = False
    VOLUME_CONFIRMATION_MULT: float = 1.2
    VWAP_BOUNCE_DIST_PCT: float = 0.3
    VWAP_BOUNCE_ENTRY_ENABLED: bool = False  # 2026-04-16: off until sweep-proven
    WIN_TRAIL_EROSION_PCT: float = 0.5
    ABLATION_DISABLE_HEDGE: bool = False
    ABLATION_DISABLE_QUICK_ENTRY: bool = False
    ABLATION_DISABLE_QUICK_EXIT: bool = False
    BASIS_CONDITION: bool = False
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True
    BB_SQUEEZE_ENABLED: bool = False  # 2026-04-16: off until sweep-proven
    BB_SQUEEZE_COOLDOWN: float = 300.0
    BB_SQUEEZE_MIN_ALIGNMENT: int = 10
    BB_SQUEEZE_WIDTH_PERCENTILE: float = 0.2
    BB_BREAKOUT_ENABLED: bool = False
    BB_BREAKOUT_TF: str = '1h'
    BB_BREAKOUT_SCORE: int = 20
    BB_RSI_STOCH_SCALP_ENABLED: bool = False
    BB_RSI_STOCH_SCALP_SCORE: int = 25
    BEAR_MARKET_MODE_TRADIER: bool = True
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True
    BOUNCE_AUGMENT_ENABLED: bool = True
    BOUNCE_AUGMENT_K_D_CROSSING_UP: bool = True
    BOUNCE_AUGMENT_K_D_THRESHOLD: float = 20.0
    BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE: float = 0.02
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = False
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.3

    # ===== 2026-04-20 LOCAL EXTREMES SCORER =====
    # Vectorized proxy for local_extremes_scorer.py — used when K_ZONE gate alone is too narrow.
    # Gates entry when 100-pt multi-indicator score (stoch+WT+DC+MFI+HA+vol) >= LOCAL_EXTREMES_MIN_SCORE.
    # Default OFF — sweep local_extremes_tradier tier to find best MIN_SCORE threshold.
    LOCAL_EXTREMES_SCORER_ENABLED: bool = False
    LOCAL_EXTREMES_MIN_SCORE: float = 30.0
    LE_TIER_SIZING_ENABLED: bool = False
    K1H_RISING_GATE_ENABLED: bool = False
    K1H_RISING_LONG_MAX: float = 60.0  # k_1h < this AND higher than 12 bars ago (1 full 1h period back)
    # ===== 2026-04-20 DYNAMIC SCORING — 5-min interval intervention =====
    # Counter-exit: while in LONG, if SHORT-direction LE score >= threshold → exit early (cut losers).
    # Augment: every DYNAMIC_SCORE_AUGMENT_INTERVAL bars, if same-direction score jumped by MIN_JUMP → augment.
    # Both work on % returns directly — counter-exit removes losers, augment improves avg entry on winners.
    DYNAMIC_SCORE_COUNTER_EXIT_ENABLED: bool = False
    DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD: float = 55.0
    DYNAMIC_SCORE_AUGMENT_ENABLED: bool = False
    DYNAMIC_SCORE_AUGMENT_MIN_JUMP: float = 25.0   # score must improve by this much to trigger augment
    DYNAMIC_SCORE_AUGMENT_INTERVAL: int = 5        # bars between re-scores (5 bars = 25 min at 5m base)
    PARTIAL_EXIT_ENABLED: bool = False             # 2026-04-20: scale-out model
    PARTIAL_EXIT_FRAC: float = 0.5                 # fraction closed at first target
    PARTIAL_EXIT_PCT: float = 0.5                  # first target gain %
    PARTIAL_TRAIL_ARM_PCT: float = 0.7             # arm the floor once gain reaches this
    PARTIAL_TRAIL_FLOOR_PCT: float = 0.5           # remainder floor price (matched to first target)
    PARTIAL_REMAINDER_EXIT_TFS: int = 2            # looser WT TFS for the remaining half
    PARTIAL_BE_BUFFER_PCT: float = 0.02            # 2026-04-21 PPL v2: break-even buffer
    PARTIAL_PROFIT_LOCK_ENABLED: bool = False
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 0.5
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 0.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.02
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5
    PARTIAL_PROFIT_LOCK_USE_MAKER: bool = True
    PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER: float = 0.5
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER: float = 0.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER: float = 0.02
    PARTIAL_PROFIT_LOCK_FRAC_TRADIER: float = 0.5
    WRONG_SIDE_ABS_KILL_ENABLED: bool = False
    WRONG_SIDE_MIN_AGE_MIN: float = 30.0
    WRONG_SIDE_WT_TFS_REQUIRED: int = 5
    WRONG_SIDE_WT_TFS_REDUCED: int = 3
    WRONG_SIDE_DIV_TFS_REQUIRED: int = 2
    WRONG_SIDE_DIV_LOOKBACK_BARS: int = 20
    WRONG_SIDE_K_TFS_REQUIRED: int = 0
    NOLOSS_BYPASS_WT_5OF5_ENABLED: bool = False
    NOLOSS_BYPASS_WT_5OF5_MIN_TFS: int = 5
    HEDGE_ENTRY_MODE: str = "LOSS_AND_WT"
    # === SIMPLE_WT15M_EXIT_ONLY (2026-04-21 user directive) — strip all complex exits ===
    # All prior exits (PEAK_GIVEBACK, BREAKEVEN_GAIN_EROSION, HEDGE_TRIGGER, HLR_TOP_EXIT, DELTA_EXIT,
    # RZ_EXIT, SRS_EXIT, WT multi-TF etc.) have been the worst offenders destroying winning trades.
    # When this flag is True, exit is the DUMB SIMPLE rule: wt1_15m vs wt2_15m on position side.
    # NO other exit fires. Gates the prior exit_sig computation in compute_exit_signals.
    SIMPLE_WT15M_EXIT_ONLY_ENABLED: bool = True
    # === HIER_SIGNAL_MODE (2026-04-21) — TF-hierarchy engine (wt_dc_hierarchy.py) ===
    # "off"=use existing signals, "entry"=hier for entry only, "exit"=hier for exit only, "both"=hier for both.
    # Hierarchy: per-TF at_upper/at_lower from dc+bb, delta_bull/bear from wt+velocity; cascade LTF→HTF.
    HIER_SIGNAL_MODE: str = "off"
    HIER_RZ_TOP_BB: float = 0.85
    HIER_RZ_BOT_BB: float = 0.15
    HIER_DC_BAND_PCT: float = 0.2
    HIER_WT_DELTA_MIN: float = 0.0
    HIER_WT_VEL_MIN: float = 0.0
    HIER_USE_W_M: bool = False
    # === REENTRY_IF_MOMENTUM (2026-04-21) — port from wt_dc_delta.py:1048 ===
    # After exit, if K_15m still in favorable direction (K>D for LONG, K<D for SHORT), skip
    # cooldown and allow next entry signal to fire. Fixes "reentries not respected" per user.
    REENTRY_IF_MOMENTUM_ENABLED: bool = True
    REENTRY_IF_MOMENTUM_WINDOW_BARS: int = 5         # within this many bars after exit
    REENTRY_IF_MOMENTUM_TF: str = "15m"              # "15m" or "3m" — which K/D to check
    # === RZ_CASCADE (2026-04-21) — user directive: not optional, replaces old shitty RZ_BREAKOUT ===
    # Each TF has red zones at dc_high/dc_low. At RZ: reverse OR breakout. Breakout→open, reverse→close.
    # Cascades hierarchically through LTF→15m→1h→4h→D (W/M when NPZ has them).
    # Signal ingredients per TF: dc_high/low (the RZ boundaries), wt_delta (wt1-wt2), wt_velocity,
    # rolling price high/low (new-high/new-low confirmation).
    # RZ_CASCADE v2 (parallel alignment) IS BROKEN — hurts Sharpe + balloons DD when ON.
    # Documented 2026-04-21: v1 too strict (never fires), v2 too loose (adds noise trades).
    # Needs true hierarchical state machine rewrite (RZ_CASCADE v3). Until then defaults to inert.
    RZ_CASCADE_ENABLED: bool = True                  # stays "on" per user directive "not optional"...
    RZ_CASCADE_WT_DELTA_MIN: float = 0.5             # ...but strict thresholds make it rarely fire (effectively inert)
    RZ_CASCADE_VEL_MIN: float = 0.5
    RZ_CASCADE_HIGH_LOOKBACK: int = 20
    RZ_CASCADE_REQUIRE_NEW_HIGH: bool = True         # strict — prevents noise entries
    RZ_CASCADE_AT_RZ_BAND_PCT: float = 0.1           # tight
    RZ_CASCADE_MIN_TF_ALIGN: int = 3                 # strict — need 3 TF confluence
    RZ_CASCADE_EXIT_MIN_REV_TFS: int = 3             # strict exit
    RZ_CASCADE_EXIT_ANY_TF: bool = True
    RZ_CASCADE_USE_W_M: bool = False
    RATIO_SENTIMENT_FILTER_ENABLED: bool = False
    RATIO_SENTIMENT_LONG_MIN: float = 40.0
    RATIO_SENTIMENT_SHORT_MAX: float = 60.0
    # === NPZ-FIELD SIGNALS (2026-04-22) — precomputed indicators not yet tested ===
    # MACD histogram exit: exit long when macd_hist crosses below zero (and vice versa)
    MACD_HIST_EXIT_ENABLED: bool = False
    MACD_HIST_EXIT_TF: str = "1h"         # which TF: "1h", "4h", "D"
    # MACD crossover entry: add entries when precomputed macd_crossover fires
    MACD_CROSS_ENTRY_ENABLED: bool = False
    MACD_CROSS_ENTRY_TF: str = "1h"
    MACD_CROSS_ENTRY_SCORE: int = 15
    # ADX entry gate: only enter when market is trending (ADX >= threshold)
    ADX_ENTRY_GATE_ENABLED: bool = False
    ADX_ENTRY_TF: str = "1h"              # "1h" or "4h"
    ADX_ENTRY_MIN: float = 20.0
    # Stoch crossover NPZ-based entry: use precomputed stoch_crossover_* fields
    STOCH_CROSS_NPZ_ENTRY_ENABLED: bool = False
    STOCH_CROSS_NPZ_ENTRY_TF: str = "1h"  # "15m", "1h", "4h"
    STOCH_CROSS_NPZ_ENTRY_SCORE: int = 10
    # WT composite bias entry: require wt_composite_bias direction match
    WT_COMPOSITE_BIAS_ENTRY_ENABLED: bool = False
    # EMA20 slope entry: price above rising EMA20 for longs, below falling for shorts
    EMA20_SLOPE_GATE_ENABLED: bool = False
    EMA20_SLOPE_TF: str = "1h"            # "1h" or "4h"
    # WT any divergence entry confirmation
    WT_ANY_DIV_ENTRY_ENABLED: bool = False  # require wt_any_bull_div/bear_div at entry
    # Precomputed tradeable gate: require tradeable_long/short == 1 at entry
    TRADEABLE_PRECOMPUTED_GATE_ENABLED: bool = False
    # WT bull/bear cross count: require N recent crosses for entry momentum
    WT_CROSS_COUNT_ENTRY_ENABLED: bool = False
    WT_CROSS_COUNT_MIN: int = 1            # min wt_bull_cross_count (long) or wt_bear_cross_count (short)
    # === Improvement Framework A1-A4 (2026-04-25, default OFF, NEEDS Tier 2 SWEEP) ===
    # NPZ data populated by binance_funding_fetcher.py (A1), binance_oi_fetcher.py (A2),
    # backtest_v8_precompute.py KC/Squeeze block (A3), divergence block (A4).
    SQUEEZE_FIRE_ENTRY_ENABLED: bool = False  # squeeze release fires entry in matching direction
    SQUEEZE_FIRE_TF: str = "1h"               # which squeeze_fire_{tf} to read
    DIVERGENCE_ENTRY_ENABLED: bool = False    # regular pivot-based div fires entry
    DIVERGENCE_ENTRY_TF: str = "1h"
    DIVERGENCE_INDICATOR: str = "wt"          # "wt" | "mfi" | "either"
    FUNDING_GATE_ENABLED: bool = False        # veto entry when 8h funding too extreme (overcrowded)
    FUNDING_GATE_LONG_MAX: float = 0.0005     # rate above this = veto longs
    FUNDING_GATE_SHORT_MIN: float = -0.0005   # rate below this = veto shorts
    OI_CONFIRM_ENABLED: bool = False          # 4-quadrant OI×price gate (Schabacker classic 2026-04-27 rewrite)
    OI_CONFIRM_MIN_CHANGE_PCT: float = 0.5    # |oi_change_1h_pct| must exceed this to consider OI significant
    OI_CONFIRM_MIN_PRICE_PCT: float = 0.3     # |price_change_1h_pct| must exceed this; gate fires only when BOTH significant
    # === 2026-04-27 LH_HL_FILTER (sweep-testable, default OFF) ===
    # LONG blocked when 1h+4h make lower highs (LH); SHORT blocked when they make higher lows (HL).
    # REQUIRE_BOTH=True also requires LL (LONG block) / HH (SHORT block) — full descending/ascending channel.
    LH_HL_FILTER_ENABLED: bool = False
    LH_HL_FILTER_MODE: str = "STRICT_2BAR"    # "STRICT_2BAR" | "DC_REGRESS"
    LH_HL_FILTER_TF_REQ: int = 2              # 1=either 1h/4h, 2=both must confirm
    LH_HL_FILTER_DC_THRESHOLD_PCT: float = 0.5  # DC_REGRESS mode threshold
    LH_HL_FILTER_REQUIRE_BOTH: bool = False   # False=LH-only/HL-only; True=LH+LL/HL+HH
    ADDITIVE_SIGNAL_MIN_HTF: int = 1          # min aligned 1h/4h/D TFs for additive SQUEEZE_FIRE/DIVERGENCE entry
    # === 2026-04-26 NEW SWITCHES — vol-target / DD-Kelly / Minervini / Clenow / 52w-prox / Squeeze-bonus / TSMOM ===
    # All default OFF — flip via QuickConfig overrides. Wired to consume new NPZ fields added by
    # backtest_v8_precompute extension (yz/pk/gk vol, sepa, clenow, kc/squeeze, ep_*).
    # VOL_TARGET — multiplicative position-size scalar from realized vol field
    VOL_TARGET_ENABLED: bool = False
    VOL_TARGET_PCT: float = 20.0              # target annualized vol % (vol-targeting overlay)
    VOL_TARGET_LOW_CAP: float = 0.25          # size scalar floor
    VOL_TARGET_HIGH_CAP: float = 2.0          # size scalar ceiling
    VOL_TARGET_FIELD: str = "yz_vol_60_d"     # NPZ field: yz_vol_60_d / pk_vol_60_d / gk_vol_60_d / yz_vol_20_4h
    # DD_KELLY — sizing reduction at account-DD tiers (sizing-only, NEVER an exit per CLAUDE.md feedback_no_pct_stops)
    DD_KELLY_ENABLED: bool = False
    DD_KELLY_TIER1_PCT: float = 10.0          # at -10% DD, size × 0.5
    DD_KELLY_TIER2_PCT: float = 15.0          # at -15% DD, size × 0.25
    DD_KELLY_TIER3_PCT: float = 20.0          # at -20% DD, size × 0.125
    # MINERVINI SEPA gate (long-side only)
    MINERVINI_GATE_ENABLED: bool = False
    MINERVINI_MIN_SCORE: int = 5              # min sepa_score 0-6
    # CLENOW score gate (long-side trend filter)
    CLENOW_GATE_ENABLED: bool = False
    CLENOW_GATE_MIN_SCORE: float = 30.0       # min clenow_score (matches config_tradier name; was CLENOW_MIN_SCORE which collided with existing dead Clenow strategy param)
    # 52w-high proximity gate (avoid topping)
    PROXIMITY_TOP_GATE_ENABLED: bool = False
    PROXIMITY_TOP_MAX_DROP_PCT: float = 5.0   # don't long if pct_from_52w_high > -X% (within X% of high)
    # SQUEEZE_FIRE score-boost (separate path from existing SQUEEZE_FIRE_ENTRY_ENABLED additive-signal path)
    # Existing SQUEEZE_FIRE_ENTRY_ENABLED fires entry as boolean signal (OR with base_sig).
    # SQUEEZE_FIRE_SCORE_BOOST_ENABLED instead adds to STRENGTH_FILTER score for the bar (additive).
    SQUEEZE_FIRE_SCORE_BOOST_ENABLED: bool = False
    SQUEEZE_FIRE_SCORE_BOOST_TF: str = "5m"   # tf in 5m / 15m / 1h
    SQUEEZE_FIRE_BONUS_SCORE: float = 15.0    # entry-score boost when squeeze fires + WT bullish (long side, mirror short)
    # TSMOM book-level scalar (12-1 momentum agreement across positions in book)
    TSMOM_BOOK_SCALAR_ENABLED: bool = False
    TSMOM_LOOKBACK_BARS: int = 252            # daily-equivalent bars (252 bars ≈ 12 months on D base)
    TSMOM_MIN_AGREEMENT: float = 0.5          # base offset in clip(0.5 + 0.5*agreement, low, high)
    TSMOM_LOW_CAP: float = 0.25
    TSMOM_HIGH_CAP: float = 1.5

    # ============ BTC-DEDICATED LOOP (mirror of config.py BTC_* — sweepable) ============
    # See BTC_DEDICATED_LOOP_DESIGN_20260427.md. All defaults match live config.py.
    # Engine reads these from override_json or autonomous_search perturbation.
    BTC_DEDICATED_ENABLED: bool = False
    BTC_DEDICATED_SYMBOLS: tuple = ("BTCUSDC",)
    BTC_HARD_BLOCK_OTHER_ACCOUNTS: bool = True
    BTC_RZ_USE_WT_DC: bool = True
    BTC_RZ_USE_FIB: bool = True
    BTC_RZ_USE_ROUND: bool = True
    BTC_RZ_PROXIMITY_PCT: float = 0.5
    BTC_FIB_LOOKBACK_4H: int = 200
    BTC_FIB_LOOKBACK_D: int = 180
    BTC_FIB_LOOKBACK_W: int = 104
    BTC_FIB_LOOKBACK_M: int = 24
    BTC_FIB_LOOKBACK_Y: int = 5
    BTC_FIB_RECOMPUTE_ON_NEW_HL: bool = True
    BTC_ROUND_INC_PRIMARY_USD: float = 5000.0
    BTC_ROUND_INC_SECONDARY_USD: float = 1000.0
    BTC_ROUND_BANDS_EACH_SIDE: int = 8
    BTC_ACCEL_RAMP_ENABLED: bool = True
    BTC_ACCEL_RAMP_MIN_TFS: int = 5
    BTC_ACCEL_RAMP_REQUIRE_POSITIVE: bool = True
    BTC_ACCEL_RAMP_PRICE_BOUNCE_TF: str = "3m"
    BTC_ACCEL_RAMP_PRICE_BOUNCE_BARS: int = 3
    BTC_DIVERGENCE_ENABLED: bool = True
    BTC_DIVERGENCE_BULL_MIN_INDS: int = 2
    BTC_DIVERGENCE_BEAR_MIN_INDS: int = 2
    BTC_DIVERGENCE_LOOKBACK_BARS: int = 5
    BTC_DIVERGENCE_BLOCK_AGAINST: bool = True
    BTC_DIVERGENCE_EXIT_AGAINST: bool = True
    BTC_ENTRY_PRIMARY_REQUIRE_RZ: bool = True
    BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP: bool = True
    BTC_ENTRY_PRIMARY_BLOCK_OPPOSING_DIV: bool = True
    BTC_ENTRY_DIV_ONLY_ENABLED: bool = False
    BTC_ENTRY_DIV_ONLY_MIN_INDS: int = 3
    BTC_RISK_PATH: str = "technical"          # "technical" | "hedge"
    BTC_HEDGE_TRIGGER_LOSS_PCT: float = -0.4
    BTC_HEDGE_SAME_SYMBOL_PCT: float = 1.0
    BTC_HEDGE_MIN_HOLD_BARS: int = 10
    BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = True
    BTC_HEDGE_WT_VEL_GATE_ENABLED: bool = True
    BTC_HEDGE_REQUIRE_4OF5_WT_TFS: bool = True
    BTC_HEDGE_NEVER_CLOSE_AT_LOSS: bool = True
    BTC_TECH_EXIT_WT_MIN_TFS: int = 3
    BTC_TECH_EXIT_AT_ANY_PNL: bool = True
    BTC_GUARANTEED_REENTRY_ENABLED: bool = True
    BTC_GUARANTEED_REENTRY_MAX_AGE_BARS: int = 480
    BTC_GUARANTEED_REENTRY_MIN_GAP_BARS: int = 5
    BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE: bool = True
    BTC_GUARANTEED_REENTRY_SIZE_MULT: float = 1.0
    # Restricted-entry mode: only enter when ONE of the explicit setup types matches.
    # When master switch is True, ALL existing entry pathways (BREAKOUT/BOUNCE/FOLLOW_THROUGH/REVERSE)
    # are disabled and only setups with their per-type switch ON can fire.
    BTC_RESTRICTED_ENTRY_MODE_ENABLED: bool = False
    BTC_RESTRICTED_K_15M_EXTREME_BOUNCE: bool = False    # k_15m crosses up from <=20 (LONG) / down from >=80 (SHORT)
    BTC_RESTRICTED_WT_15M_PLUS_BOUNCE: bool = False      # wt1×wt2 cross on 15m/1h/4h/D from extreme zone
    BTC_RESTRICTED_DC_15M_PLUS_TOUCH: bool = False       # close crosses back over dc_low (LONG) or dc_high (SHORT)
    BTC_RESTRICTED_DC_15M_PLUS_BREAKOUT: bool = False    # close breaks above dc_high (LONG) or below dc_low (SHORT)
    BTC_RESTRICTED_STDEV_BREAKOUT: bool = False          # close above bb_upper (LONG) or below bb_lower (SHORT)
    BTC_RESTRICTED_STDEV_BOUNCE: bool = False            # close crosses back over bb_lower (LONG) or bb_upper (SHORT)
    BTC_RESTRICTED_K_EXTREME_LO_THRESHOLD: float = 20.0  # K extreme defs
    BTC_RESTRICTED_K_EXTREME_HI_THRESHOLD: float = 80.0
    BTC_RESTRICTED_WT_EXTREME_NEG: float = -50.0          # WT must be < this for bull cross to count
    BTC_RESTRICTED_WT_EXTREME_POS: float = 50.0           # WT must be > this for bear cross to count
    # HA candle confirmation (2026-04-28): when ENABLED, restrict-mode setups must ALSO be on a green
    # HA candle for LONG (or red for SHORT). User insight: just trading green vs red HA on 3m alone
    # already filters out a ton of bad entries. Stack with the other restricted setup gates.
    BTC_RESTRICTED_HA_CONFIRM_ENABLED: bool = False
    BTC_RESTRICTED_HA_CONFIRM_TF: str = "3m"   # 3m / 15m / 1h / 4h / D
    BTC_RESTRICTED_HA_REQUIRE_TWO_BARS: bool = False  # require current AND prev bar same color
    # 2026-04-29: per-setup-type TF restriction. Each list is which TFs are SCANNED for that setup.
    # Empty list / None means use all default TFs (15m, 1h, 4h, D). Set to ["1h"] to test 1h-only.
    # Used for ablation: "what does 1h contribute?" → set every list to ["15m","4h","D"] (drops 1h).
    BTC_RESTRICTED_WT_TFS: list = None
    BTC_RESTRICTED_DC_TOUCH_TFS: list = None
    BTC_RESTRICTED_DC_BREAKOUT_TFS: list = None
    BTC_RESTRICTED_STDEV_BREAKOUT_TFS: list = None
    BTC_RESTRICTED_STDEV_BOUNCE_TFS: list = None
    # HTF-reversal swing-trade-on-exit (2026-04-29 user request):
    # When a position exits AND a higher-TF (4h or D) setup signal for the OPPOSITE side
    # is active at the same bar, immediately enter the opposite side as a SWING trade
    # (longer min_hold than scalp). The 4h/D signals checked are: DC_4H_TOUCH,
    # DC_4H_BREAKOUT, DC_D_TOUCH, DC_D_BREAKOUT, WT_4H_CROSS, WT_D_CROSS,
    # STDEV_4H_BREAKOUT/BOUNCE, STDEV_D_BREAKOUT/BOUNCE.
    # Catches the "we exited at the bottom of an HTF reversal — should have flipped" pattern.
    BTC_HTF_REVERSAL_SWING_ENABLED: bool = False
    BTC_HTF_REVERSAL_SWING_MIN_HOLD_BARS: int = 20    # 1h on 3m base — give swing time to develop
    BTC_HTF_REVERSAL_SWING_USE_RESTRICTED_SETUPS: bool = True  # require restricted-mode arrays computed (adds the gates)
    # 2026-04-29: how many of (WT, DC, STDEV) HTF categories must agree at the same bar for the swing to fire.
    # 1 = ANY single 4h/D signal triggers (default — original behavior).
    # 2 = need 2 of 3 categories — much higher conviction.
    # 3 = need all 3 (very rare).
    BTC_HTF_REVERSAL_SWING_MIN_CATEGORIES: int = 1
    # 2026-04-29: within each category, how many TFs (of {15m, 1h, 4h, D}) must agree.
    # 1 = any single TF in the category fires (most permissive — original behavior).
    # 2 = need 2 TFs in same category aligning, e.g. WT_4h + WT_D both crossing — tighter.
    # 3 = 3 of 4 TFs in same category. 4 = all four.
    # Combined with MIN_CATEGORIES gives a 2-level confluence requirement.
    BTC_HTF_REVERSAL_SWING_MIN_TF_PER_CATEGORY: int = 1
    # 2026-04-29: HTF direction anchor — prevent flip-flopping within a single HTF candle.
    # When enabled with TF != "off", track which HTF candle the last entry was in (1h, 4h, or D).
    # Within the SAME HTF candle, only allow same-side re-entries; block opposite-side.
    # This is the structural fix for the "5 trades in 1 hour, 3 in 15m" churn — the strategy
    # was treating each 3m bar as a fresh decision when the HTF clearly hadn't reversed.
    BTC_HTF_DIRECTION_ANCHOR_TF: str = "off"   # "off" | "15m" | "1h" | "4h" | "D"
    BTC_LEVERAGE: float = 20.0
    BTC_PER_TRADE_NOTIONAL_USD_MAX: float = 90.0
    BTC_TOTAL_NOTIONAL_USD_MAX: float = 180.0
    BTC_HARD_LOSS_USD_PER_TRADE: float = 10.0
    BTC_DAILY_LOSS_PCT_FLOOR: float = -0.5
    BTC_WEEKLY_LOSS_PCT_FLOOR: float = -1.5
    BTC_PYRAMID_DISABLED: bool = True
    BTC_INTRABAR_REVERSAL_EXIT: bool = True
    BTC_REGIME_PAUSE_ENABLED: bool = True
    BTC_COOLDOWN_BARS: int = 5
    BTC_MIN_HOLD_BARS: int = 5
    # Breakout mode (2026-04-27 add)
    BTC_BREAKOUT_ENTRY_ENABLED: bool = True
    BTC_BREAKOUT_DC_TF: str = "3m"
    BTC_BREAKOUT_ACCEL_MIN_TFS: int = 2
    BTC_BREAKOUT_BLOCK_OPPOSING_DIV: bool = True
    BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE: float = 5.0
    BTC_BREAKOUT_MIN_HOLD_BARS: int = 3
    BTC_BREAKOUT_COOLDOWN_BARS: int = 3
    BTC_BREAKOUT_REENTRY_ON_EXIT: bool = True
    BTC_BREAKOUT_REENTRY_REQUIRE_TREND: bool = True
    BTC_FOLLOW_THROUGH_REENTRY_ENABLED: bool = True
    BTC_FOLLOW_THROUGH_MIN_MOVE_PCT: float = 0.3
    # Same-symbol HEDGE engine in v8 BTC sim (2026-04-28)
    BTC_HEDGE_SAMESYM_ENABLED: bool = False
    BTC_HEDGE_SAMESYM_TRIGGER_LOSS_PCT: float = -0.3
    BTC_HEDGE_SAMESYM_REQUIRE_WT_3M: bool = True
    BTC_HEDGE_SAMESYM_REQUIRE_WT_15M: bool = True
    BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN: int = 1
    BTC_HEDGE_SAMESYM_NOTIONAL_PCT: float = 1.0
    BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_NONNEG_GAIN: bool = True
    BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_WT_3M_AND_1H: bool = True
    BTC_HEDGE_SAMESYM_HEDGE_HARD_LOSS_PCT: float = -2.0
    # HTF alignment for BREAKOUT (added 2026-04-27 — prevents buying into downtrends)
    BTC_BREAKOUT_REQUIRE_HTF_ALIGNED: bool = True
    BTC_BREAKOUT_HTF_MIN_ALIGNED: int = 2
    # RZ as boost (2026-04-27 redesign: RZ softens accel requirement, no longer gates)
    BTC_RZ_AS_BOOST_ENABLED: bool = True
    BTC_RZ_SOFTEN_ACCEL_BY: int = 1
    # Divergence redesign (2026-04-27 user: D = clockwork, 1h/4h testable, 3m/15m noise)
    BTC_DIVERGENCE_MIN_TF: str = "4h"
    BTC_DIVERGENCE_LB_3M: int = 5
    BTC_DIVERGENCE_LB_15M: int = 10
    BTC_DIVERGENCE_LB_1H: int = 20
    BTC_DIVERGENCE_LB_4H: int = 20
    BTC_DIVERGENCE_LB_D: int = 10
    BTC_DIVERGENCE_REQUIRE_D_CONFIRM_BARS: int = 2
    # TREND-FOLLOW POC (REJECTED 2026-04-27 — user course-corrected: HODL doesn't work at 20× lev,
    # need 50 trades/day with hedge-based protection. Keep flag but default OFF; will be removed.)
    BTC_TREND_MODE_ENABLED: bool = False
    BTC_TREND_ATR_MULT: float = 2.5
    BTC_TREND_HARD_LOSS_PCT: float = 5.0
    BTC_TREND_NOTIONAL_USD_MAX: float = 500.0
    BTC_TREND_REQUIRE_PROFIT_FOR_WT_EXIT: bool = True
    BTC_TREND_COOLDOWN_BARS: int = 480
    BTC_TREND_ENTRY_MODE: str = "dc_d"
    BTC_TREND_MIN_HTF_ALIGNED: int = 3
    # ─── HF + HEDGE redesign (2026-04-27 path A correction) ────────────────────────
    # Target: 50+ trades/day, accept 0.1-0.3% avg per trade, compound. Hedge engine
    # opens opposite-side position on adverse move instead of stop-loss; hedge closes
    # on WT 3m+1h flip + non-negative gain (per memory feedback_hedge_wt3m_close_absolute).
    BTC_HF_MIN_HOLD_BARS: int = 3                                     # was 20 — too slow
    BTC_HF_COOLDOWN_BARS: int = 1                                     # was 20 — too slow
    BTC_HF_HEDGE_ON_ADVERSE_PCT: float = -0.3                         # open hedge when position pnl ≤ this
    BTC_HF_HEDGE_NOTIONAL_PCT_OF_LOSER: float = 1.0                   # hedge size = 1.0× loser side
    BTC_HF_HEDGE_CLOSE_REQUIRE_NONNEG_GAIN: bool = True               # hedge only closes when gain ≥ 0 (per memory feedback_hedge_wt3m_close_absolute)
    BTC_HF_HEDGE_CLOSE_REQUIRE_BOTH_WT3M_AND_1H: bool = True          # both WT 3m AND 1h must flip in favor before closing hedge
    # Same-bar REVERSE-ON-EXIT
    BTC_REVERSE_ON_EXIT_ENABLED: bool = True
    BTC_REVERSE_REQUIRE_HTF_ALIGNED: bool = True
    # ─── GOLDEN RULE — sweep-toggleable (2026-05-06) ────────────────────────────────
    GOLDEN_RULE_ENABLED: bool = True
    GOLDEN_RULE_GATE_MODE: bool = True
    GOLDEN_RULE_MULT_APPLY: bool = True
    GOLDEN_RULE_MULT_BREAKOUT: float = 0.1
    GOLDEN_RULE_MULT_RETEST: float = 5.0
    GOLDEN_RULE_RETEST_BARS: int = 120
    GOLDEN_RULE_DC_15M_ENABLED: bool = True
    GOLDEN_RULE_DC_1H_ENABLED: bool = True
    GOLDEN_RULE_DC_4H_ENABLED: bool = True
    GOLDEN_RULE_DC_D_ENABLED: bool = True
    GOLDEN_RULE_BB_15M_ENABLED: bool = True
    GOLDEN_RULE_BB_1H_ENABLED: bool = True
    GOLDEN_RULE_BB_4H_ENABLED: bool = True
    GOLDEN_RULE_BB_D_ENABLED: bool = True
    GOLDEN_RULE_MULT_15M: float = 1.0
    GOLDEN_RULE_MULT_1H: float = 1.5
    GOLDEN_RULE_MULT_4H: float = 2.0
    GOLDEN_RULE_MULT_D: float = 3.0
    GOLDEN_RULE_BASE_USD: float = 5.0
    # ─── GOLDEN RULE HTF gate — 7-indicator multi-TF confirmation (2026-05-10) ──────────
    GOLDEN_RULE_HTF_MIN_TFS: int = 0   # 0 = gate off; 1-6 = require N TFs confirmed
    GOLDEN_RULE_MIN_IND: int = 2        # indicators per TF required (of 7: WT RSI MFI DC BB RVOL K)

    @classmethod
    def from_override_file(cls, path: str) -> "QuickConfig":
        cfg = cls()
        if path and Path(path).exists():
            with open(path) as f:
                overrides = json.load(f)
            for k, v in overrides.items():
                if hasattr(cfg, k):
                    cur = getattr(cfg, k)
                    if isinstance(cur, bool):
                        setattr(cfg, k, bool(v))
                    elif isinstance(cur, int):
                        setattr(cfg, k, int(v))
                    elif isinstance(cur, float):
                        setattr(cfg, k, float(v))
                    else:
                        setattr(cfg, k, v)
        cfg._clamp_overrides()
        return cfg

    def _clamp_overrides(self):
        # Reset out-of-physical-range fields to dataclass defaults. Protects against drift
        # accumulated from autonomous_search numeric perturbation (multiplicative ×0.25–×3.0).
        # Only resets fields whose NAME unambiguously identifies the indicator AND whose value
        # is clearly out of physical bounds. Multipliers, durations, percentages, booleans skipped.
        # Added 2026-04-26 after audit found baselines with MFI=225, K=1620, etc.
        from dataclasses import fields as _dfields, MISSING as _MISSING
        skip_substrings = ('_MULT', '_BARS', '_DAYS', '_HOURS', '_MIN_GAP', '_AGE_MIN', '_PCT', '_FRAC', '_BUFFER', '_WINDOW', '_LOOKBACK', '_PERIOD', '_LENGTH', '_COOLDOWN', '_DELAY', '_ALIGNMENT', '_COUNT', '_MIN_TFS', '_TFS_REQUIRED', '_SCORE', '_BONUS', '_PENALTY')
        clamps = []
        for fld in _dfields(self):
            nm = fld.name
            val = getattr(self, nm, None)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            if nm.endswith('_ENABLED') or '_ENABLED_' in nm:
                continue
            if any(s in nm for s in skip_substrings):
                continue
            lo, hi = None, None
            if 'MFI' in nm:
                lo, hi = 0.0, 100.0
            elif 'RSI' in nm:
                lo, hi = 0.0, 100.0
            elif 'STOCH_ENTRY' in nm or 'STOCH_EXTREME' in nm or 'STOCH_K' in nm:
                lo, hi = 0.0, 100.0
            elif nm in ('K3M_FLOOR', 'K3M_CAP', 'K15M_FLOOR', 'K15M_CAP', 'V8Q_K3M_FLOOR'):
                lo, hi = 0.0, 100.0
            elif 'K_ZONE' in nm or 'K_EXTREME' in nm:
                lo, hi = 0.0, 100.0
            elif 'EXIT_SCORER_K' in nm:
                lo, hi = 0.0, 100.0
            elif 'CHOP' in nm:
                lo, hi = 0.0, 100.0
            if lo is not None and (val < lo or val > hi):
                if fld.default is not _MISSING:
                    setattr(self, nm, fld.default)
                    clamps.append((nm, val, fld.default))
        if clamps:
            try:
                import sys
                print(f"[QuickConfig._clamp_overrides] reset {len(clamps)} out-of-range fields: " + ", ".join(f"{n}={v}->{d}" for n, v, d in clamps[:8]) + ("..." if len(clamps) > 8 else ""), file=sys.stderr)
            except Exception:
                pass
        return clamps

    def apply_tradier_defaults(self):
        self.MODE = "tradier"
        self.LTF = "5m"   # stocks: 5m LTF (crypto default 3m). NEVER swap — stock NPZ only has _5m fields.
        self.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"  # stocks: bb_1h (crypto: dc_4h) — NEVER swap
        self.K_ZONE_ENTRY_ENABLED = True
        self.MFI_ENTRY_ENABLED = True
        self.VWAP_FILTER_ENABLED = True
        self.FH_MOMENTUM_ENABLED = True
        self.DC_DAYTRADE_ENABLED = True
        # ===== ENGINE-RETROFIT 2026-04-30 — live-parity entry/exit paths (registry: docs/engine_retrofit_registry.md) =====
        # E1. WT_DC_ENTRY — biggest live entry path (~26k LONG BUY/day on tradier).
        # Source: wt_dc_entry_scorer.score_entry_multitf. Vectorized here.
        self.WT_DC_ENTRY_ENABLED = False                  # master switch (sweep-flippable)
        self.WT_DC_ENTRY_LONG_THRESHOLD = 55              # tradier default; tra=85
        self.WT_DC_ENTRY_SHORT_THRESHOLD = 55
        self.WT_DC_ENTRY_W_HTF_D = 25.0                   # D-bull/bear weight
        self.WT_DC_ENTRY_W_HTF_4H = 25.0                  # 4h-bull/bear weight
        self.WT_DC_ENTRY_W_LTF_1H = 30.0                  # 1h-cross weight
        self.WT_DC_ENTRY_W_DC_1H = 10.0                   # dc_position_1h weight
        self.WT_DC_ENTRY_W_K_5M = 10.0                    # stoch_k_5m weight
        self.WT_DC_ENTRY_DC1H_LONG_MAX = 0.50             # LONG fires only when dc_pos_1h < this
        self.WT_DC_ENTRY_DC1H_SHORT_MIN = 0.50            # SHORT fires only when dc_pos_1h > this
        self.WT_DC_ENTRY_K5M_LONG_MAX = 40.0              # LONG fires only when k_5m < this
        self.WT_DC_ENTRY_K5M_SHORT_MIN = 60.0             # SHORT fires only when k_5m > this
        # X1. PEAK_GIVEBACK_GAIN_EROSION — 1,343/day live exit
        self.PEAK_GIVEBACK_ENABLED = False
        self.PEAK_GIVEBACK_PEAK_MIN_PCT = 0.50            # arm only after peak ≥ this
        self.PEAK_GIVEBACK_DROP_PCT = 0.5                 # exit on this much give-back from peak
        self.PEAK_GIVEBACK_HARD_ZERO_ENABLED = False      # additional: exit if peak ≥ X but cur ≤ 0
        self.PEAK_GIVEBACK_MIN_AGE_BARS = 0
        # X2. BREAKEVEN_GAIN_EROSION — 738/day live exit
        self.BE_EROSION_ENABLED = False
        self.BE_EROSION_AGE_MIN_BARS = 30                 # only fire after position is ≥ N bars old
        self.BE_EROSION_MIN_GAIN = 0.0                    # exit if gain < this AND profit
        self.BE_EROSION_REQUIRE_PROFIT = True
        self.BE_EROSION_COMM_BUFFER = 0.05                # commissions buffer
        # X3. K1M_EXTREME_REVERSE — 1,205/day live exit (BLOCKED on missing k_1m NPZ field)
        self.K1M_EXTREME_REVERSE_ENABLED = False
        self.K1M_EXTREME_HIGH = 90.0                      # LONG exits when k_1m > this AND turning down
        self.K1M_EXTREME_LOW = 10.0                       # SHORT exits when k_1m < this AND turning up
        self.K1M_REVERSE_REQUIRES_PROFIT = True
        # X4. STRONG_REDUCE_K — 2,716/day live reduce (top exit volume)
        self.STRONG_REDUCE_K_ENABLED = False
        self.SRK_K15M_LONG_MIN = 80.0                     # LONG reduces when k_15m > this
        self.SRK_K1H_LONG_MAX = 30.0                      # AND k_1h < this (1h trending opposite)
        self.SRK_K15M_SHORT_MAX = 20.0
        self.SRK_K1H_SHORT_MIN = 70.0
        self.SRK_REDUCE_FRAC = 0.5                        # close 50% of position on fire
        self.STOCH_CROSS_1H_EXIT_ENABLED = True
        self.MFI_FLIP_EXIT_ENABLED = True
        self.WT_CROSSUNDER_FINAL_ENABLED = True
        # 2026-04-16: wire 3 real tradier alpha sources into reentry block dict
        self.REENTRY_B_KZONE_ENABLED = True      # live Sharpe 4.23 / 20,605 trades
        self.REENTRY_B_FH_MOM_ENABLED = True     # live Sharpe 1.54 validated
        self.REENTRY_B_MFI_D_OVERSOLD_ENABLED = True  # MFI_D<20 oversold entry
        # TF-alignment — stocks require 2+ (per CLAUDE.md: crypto=1, stock=2)
        self.HTF_MIN_ALIGNED = 2
        # WT cross alignment — stocks need 3 (per CLAUDE.md)
        self.WT_EXIT_MIN_TFS = 3
        self.MI_EXIT_ENABLED = True
        # 2026-04-17: ENTRY_SCORE_THRESHOLD=24 was mathematically unreachable after score-gate wiring
        # (max achievable score ~21 from B15+B04+B11+B02+B_KZONE+B_FH_MOM+B_MFI_D_OVERSOLD weights).
        # Set to 0 (disabled). Sweeps can raise it; 24 blocks ALL entries.
        self.ENTRY_SCORE_THRESHOLD = 0.0
        self.K3M_FLOOR = 30.0
        # Entry zone gates — stocks use explicit zones (crypto relies on reentry blocks)
        # Default off (LONG=0, SHORT=100) until swept — see live config_tradier ENTRY_ZONE_LONG=35, ENTRY_ZONE_SHORT=65
        self.ENTRY_ZONE_LONG = 0.0
        self.ENTRY_ZONE_SHORT = 100.0
        self.ENTRY_ZONE_K_TF = "15m"  # stocks: 15m entry-zone TF (crypto: 3m). Swap = disaster.
        # Reentry gap — stocks 15 bars (~15min on 1m), crypto 3 bars (~3min on 1m). Default off.
        self.REENTRY_MIN_GAP_BARS = 0
        # SYMGATE — default off; flip True to test symmetric entry/exit gate
        self.ENTRY_SYMGATE_ENABLED = False
        self.REENTRY_SYMGATE_ENABLED = False
        # D4: default tier STOCK for tradier mode when enabled
        self.BREAKOUT_MULTI_LUNG_TIER = "STOCK"
        # 2026-04-18: STOCKS BASELINE = S_H + S_E winner.
        # S_E with RANK_CONVICTION_MIN=3 selects high-quality entries (avg Sharpe 2.47 on 15/114 syms).
        # B15/B11 NPZ bug only affected CRYPTO blocks — tradier WT-based conviction is unaffected.
        self.CT_WT_VELOCITY_1H_MIN = 2.0    # stocks tuned (crypto 6.0 too tight)
        self.REENTRY_RALLY_K15M_MAX = 100.0 # disabled — stocks use ENTRY_ZONE instead
        # S_H (HTF entry zones — the breakthrough for stocks):
        self.ENTRY_ZONE_K_TF = "1h"         # 1h K-zone filters better than 15m for stocks
        self.ENTRY_ZONE_LONG = 30.0         # LONG requires k_1h < 30 (deeply oversold)
        self.ENTRY_ZONE_SHORT = 70.0        # SHORT requires k_1h > 70
        self.HTF_MIN_ALIGNED = 2
        # S_E (conviction scoring — HTF-proxied in backtest):
        self.RANK_CONVICTION_ENABLED = True
        self.RANK_CONVICTION_MIN = 3        # all 3 of 1h/4h/D agree
        self.DC_MOMENT_ENABLED = True
        self.DC_MOMENT_OPPOSE_THRESHOLD = 40.0
        self.WINNER_PROTECT_ENABLED = True
        self.WINNER_PROTECT_GAIN_PCT = 1.5
        # 2026-04-19 FIX: Tradier has fewer trading symbols per run (15-20 of 114 trade with S_E).
        # 2.5 floor caused early_abort at exactly 15 symbols when avg was 2.4755. Use 1.5 so full
        # 114-sym set evaluates and we get the real baseline Sharpe. Sweeps set their own floors.
        self.EARLY_ABORT_SHARPE_FLOOR = 1.5
        # 2026-04-19 FIX: MIN_HOLD_BARS defaults to 250 (crypto 12.5h). For tradier "exit at
        # slowdown" model, that equals 62.5h hold on 15m base — blocks ALL exits → WR=39%.
        # Tradier has no hedge engine and exits whenever momentum slows (in gain). Reset to 4
        # bars (60min on 15m base) so exits fire promptly. Sweepable.
        self.MIN_HOLD_BARS = 4
        # Tradier has no simulated hedge — disable so counter-positions don't contaminate P&L.
        self.HEDGE_ENABLED = False
        # 2026-04-20 FIX: DC_RECOVERY_EXIT rescues NOLOSS-stuck positions. Disabled by default (crypto
        # uses hedge instead). For tradier, this is the ONLY escape valve — without it, positions stranded
        # below entry hold forever and block all reentries. Use bb_1h range (stocks: bb_1h, not dc_4h).
        self.DC_RECOVERY_EXIT_ENABLED = True
        self.DC_RECOVERY_EXIT_TF = "bb_1h"
        self.LOCAL_EXTREMES_SCORER_ENABLED = True
        self.LOCAL_EXTREMES_MIN_SCORE = 15.0
        self.LE_TIER_SIZING_ENABLED = True
        # k-threshold exits: require extreme overbought before exiting — tighter than crypto defaults
        self.SRS_K_EXIT_1H = 85.0          # SRS exit requires k_1h >= 85 (default 75)
        self.STOCH_1H_EXIT_K_MIN = 85.0    # stoch cross exit requires k_1h prev >= 85 (default 70)
        self.K_LOWER_HIGH_EXIT_ENABLED = True  # exit if k peaks below extreme and turns down
        self.K_LOWER_HIGH_LTF_THRESHOLD = 65.0  # k_ltf must be >= 65 (lower bound of failed rally)
        self.K_LOWER_HIGH_EXTREME = 95.0   # only fires if k_prev < 95 (didn't reach true extreme)
        # RZ_EXIT tradier defaults (mirror config_tradier.py live values):
        self.RZ_EXIT_ENABLED = True         # T25 sweep validated: RZ_EXIT True=0.492 vs False=0.229 (+115%)
        self.RZ_K_EXIT = 80.0              # stocks use 80 (crypto live=95)
        self.RZ_TOP_BB_THRESHOLD = 0.85
        self.RZ_BOT_BB_THRESHOLD = 0.15
        self.RZ_BREAKOUT_ENTRY_ENABLED = False  # default OFF — needs sweep to validate
        self.RZ_BREAKOUT_NOLOSS_GUARD_ENABLED = True
        self.RZ_BREAKOUT_NOLOSS_GUARD_TF = "15m"
        self.RZ_BREAKOUT_NOLOSS_MODE = "dc_low4_15m"
        self.RZ_BREAKOUT_NOLOSS_BAR_WINDOW = 4
        # EXIT_SCORER tradier defaults (wt_dc_exit_scorer validated +25.3%):
        self.EXIT_SCORER_ENABLED = True    # tradier: scorer is the validated exit gate
        self.EXIT_SCORER_MIN_CONDITIONS = 3
        self.EXIT_SCORER_K_EXTREME = 75.0
        self.EXIT_SCORER_DC_EXTREME = 0.80
        # ===== 2026-04-30 HTF PORT FROM CRYPTO — tradier-only (default OFF, sweep first) =====
        # The crypto baseline (pool_sharpe 0.7770) uses ALL_TF_BRAKE that counts W+M timeframes.
        # Tradier baseline ignores W and M completely. These three flags lift HTF anchoring into
        # the tradier path. Default OFF; flip on after sweep proof.
        # F1. HTF_W_M_ALIGN_GATE — entry gate: require N of 2 (W, M) WaveTrend agree with side.
        self.HTF_W_M_ALIGN_GATE_TRADIER_ENABLED = False
        self.HTF_W_M_ALIGN_TRADIER_REQUIRED = 2          # 1 = either; 2 = both
        # F2. HTF_DC_BREAKOUT — additive entry: close above dc_high_4h * (1+thr) AND wt1_W > wt2_W.
        self.HTF_DC_BREAKOUT_TRADIER_ENABLED = False
        self.HTF_DC_BREAKOUT_TRADIER_TF = "4h"           # 4h | D | W
        self.HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT = 0.0 # 0 = exact break; 0.1 = +0.1% confirm
        self.HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT = True # require W WaveTrend on side
        # F3. HTF_W_REVERSAL_EXIT — exit when wt1_W against side AND wt1_D against side.
        self.HTF_W_REVERSAL_EXIT_TRADIER_ENABLED = False
        self.HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D = True  # also require D against (2-TF anchor)


def _resolve_npz_dir(npz_dir=""):
    if npz_dir:
        d = Path(npz_dir)
    else:
        for prefix in ["backtest_v8", "backtest_v7"]:
            d = BASE_PATH / prefix / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                break
    return d


def iter_npz(mode, symbols, start_date, npz_dir=""):
    """Yield (sym, data_dict) one symbol at a time. Caller must discard
    the data_dict after each iteration to release memory. Required for
    large symbol sets that would OOM if preloaded."""
    d = _resolve_npz_dir(npz_dir)
    if not d.exists():
        print(f"NPZ dir not found: {d}")
        return
    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    from datetime import datetime, timezone
    start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) if start_date else 0
    count = 0
    for npz_path in sorted(d.glob("*.npz")):
        sym = npz_path.stem
        if symbols and sym not in symbols:
            continue
        if not symbols:
            is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
            if mode == "crypto" and not is_crypto: continue
            if mode == "tradier" and is_crypto: continue
        try:
            data = dict(np.load(str(npz_path), allow_pickle=True))
        except Exception as e:
            print(f"[WARN] {sym}: {e}")
            continue
        ts = data.get('timestamps', data.get('timestamp_3m', np.array([])))
        if len(ts) == 0:
            continue
        if start_ts and ts[-1] < start_ts:
            continue
        start_idx = np.searchsorted(ts, start_ts) if start_ts else 0
        trimmed = {}
        for k, v in data.items():
            if isinstance(v, np.ndarray) and len(v) > start_idx:
                # COPY (not view) so the full underlying array can be freed
                trimmed[k] = np.ascontiguousarray(v[start_idx:])
            else:
                trimmed[k] = v
        del data  # explicit drop
        count += 1
        yield sym, trimmed
    print(f"Streamed {count} symbols from {d}", file=sys.stderr)


def load_npz(mode, symbols, start_date, npz_dir=""):
    """Backward-compat wrapper: collects iter_npz into a dict. Only use
    for small symbol sets (<15). Large sets must use iter_npz directly
    or the streaming simulate_streaming path."""
    return dict(iter_npz(mode, symbols, start_date, npz_dir))


def _close_with_mode_check(npz, n, cfg, call_site: str) -> np.ndarray:
    """B-7: mode-aware close loader. Falls back to close_5m for stock NPZ (tradier LTF=5m)
    but warns when mode/LTF mismatch so a tradier NPZ running with LTF=3m is not silent."""
    _ltf = getattr(cfg, 'LTF', '3m')
    _mode = getattr(cfg, 'MODE', 'crypto')
    close = _safe(npz, f'close_{_ltf}', n)
    if close.sum() == 0:
        _expected = 'close_5m' if _mode == 'tradier' else 'close_3m'
        if f'close_{_ltf}' != _expected and f"{call_site}:{_ltf}:{_mode}" not in _V8_MISSING_WARNED:
            _V8_MISSING_WARNED.add(f"{call_site}:{_ltf}:{_mode}")
            print(f"[V8_ENGINE] CLOSE_FALLBACK mode={_mode} LTF={_ltf} -> close_5m at {call_site}. Check LTF config matches NPZ.", file=sys.stderr)
        close = _safe(npz, 'close_5m', n)
    return close


def compute_reentry_blocks(npz, n, is_long, cfg):
    """Returns dict of block_name -> boolean array (True = block fires)."""
    _ltf = getattr(cfg, 'LTF', '3m')
    _ltf_mins = 3 if _ltf == '3m' else 5
    _bph_15m = 15 // _ltf_mins   # bars per 1h period: 5 (3m/crypto) or 3 (5m/tradier)
    _bph_1h = 60 // _ltf_mins    # bars per 1h period: 20 (crypto) or 12 (tradier)
    _bph_4h = 240 // _ltf_mins   # bars per 4h period: 80 (crypto) or 48 (tradier)
    close = _close_with_mode_check(npz, n, cfg, 'compute_reentry_blocks')
    k_ltf = _safe(npz, f'stoch_k_{_ltf}', n, 50); d_ltf = _safe(npz, f'stoch_d_{_ltf}', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50); k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    k_ltf_prev = np.roll(k_ltf, 1); k_ltf_prev[0] = k_ltf[0]
    wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_ltf = _safe(npz, f'wt_velocity_{_ltf}', n); wt_vel_15m = _safe(npz, 'wt_velocity_15m', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    wt_bull_ltf = _safeb(npz, f'wt_bullish_{_ltf}', n); wt_bull_15m = _safeb(npz, 'wt_bullish_15m', n)
    wt_bull_1h = _safeb(npz, 'wt_bullish_1h', n); wt_bull_4h = _safeb(npz, 'wt_bullish_4h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)
    dc_high_1h = _safe(npz, 'dc_high_1h', n); dc_low_1h = _safe(npz, 'dc_low_1h', n)
    dc_high_15m = _safe(npz, 'dc_high_15m', n); dc_low_15m = _safe(npz, 'dc_low_15m', n)
    ha_ltf = _ha_int(npz, f'ha_{_ltf}', n); ha_15m = _ha_int(npz, 'ha_15m', n); ha_1h = _ha_int(npz, 'ha_1h', n)

    blocks = {}
    if cfg.REENTRY_B02_BC156_BOTTOM_ENABLED:
        if is_long:
            wt_bull_cnt = wt_bull_ltf.astype(int) + wt_bull_15m.astype(int) + wt_bull_1h.astype(int) + wt_bull_4h.astype(int)
            blocks["B02"] = (wt1_15m < -20) & (wt_vel_15m > 0) & (wt1_1h > wt2_1h) & (wt_bull_cnt >= 2)
        else:
            wt_bear_cnt = (~wt_bull_ltf).astype(int) + (~wt_bull_15m).astype(int) + (~wt_bull_1h).astype(int) + (~wt_bull_4h).astype(int)
            blocks["B02"] = (wt1_15m > 20) & (wt_vel_15m < 0) & (wt1_1h < wt2_1h) & (wt_bear_cnt >= 2)
    if cfg.REENTRY_B04_DC_RETEST_ENABLED:
        dc_high_4h_prev = np.roll(dc_high_4h, 5); dc_high_4h_prev[:5] = dc_high_4h[:5]
        dc_low_4h_prev = np.roll(dc_low_4h, 5); dc_low_4h_prev[:5] = dc_low_4h[:5]
        if is_long:
            exp = (dc_high_4h > dc_high_4h_prev * 1.015) & (dc_high_4h_prev > 0)
            pb = (close < dc_high_4h_prev * 1.005) & (close > dc_high_4h_prev * 0.99)
            blocks["B04"] = exp & pb & (k_ltf > d_ltf) & (k_ltf < 50)
        else:
            exp = (dc_low_4h < dc_low_4h_prev * 0.985) & (dc_low_4h_prev > 0)
            pb = (close > dc_low_4h_prev * 0.995) & (close < dc_low_4h_prev * 1.01)
            blocks["B04"] = exp & pb & (k_ltf < d_ltf) & (k_ltf > 50)
    if cfg.REENTRY_B10_STOCH_REV_ENABLED:
        if is_long:
            blocks["B10"] = (k_ltf_prev <= d_ltf) & (k_ltf > d_ltf) & (k_ltf < 25) & (k_15m < 40)
        else:
            blocks["B10"] = (k_ltf_prev >= d_ltf) & (k_ltf < d_ltf) & (k_ltf > 75) & (k_15m > 60)
    if cfg.REENTRY_B11_DC_BREAK_ENABLED:
        # B11 uses PREVIOUS 1h bar's DC band — dc_high_1h in NPZ includes current bar's high so
        # close > dc_high_1h is mathematically impossible. Shift by _bph_1h bars (1 full 1h period).
        _dc1h_prev = np.roll(dc_high_1h, _bph_1h); _dc1h_prev[:_bph_1h] = 0
        _dc1l_prev = np.roll(dc_low_1h, _bph_1h); _dc1l_prev[:_bph_1h] = 0
        if is_long:
            blocks["B11"] = (_dc1h_prev > 0) & (close > _dc1h_prev * 1.001) & (wt1_15m > wt2_15m)
        else:
            blocks["B11"] = (_dc1l_prev > 0) & (close < _dc1l_prev * 0.999) & (wt1_15m < wt2_15m)
    if cfg.REENTRY_B12_WT_MOM_ENABLED:
        if is_long:
            aligned = (wt1_ltf > wt2_ltf) & (wt1_15m > wt2_15m) & (wt1_1h > wt2_1h)
            blocks["B12"] = aligned & (wt_vel_ltf > 1.0)
        else:
            aligned = (wt1_ltf < wt2_ltf) & (wt1_15m < wt2_15m) & (wt1_1h < wt2_1h)
            blocks["B12"] = aligned & (wt_vel_ltf < -1.0)
    if cfg.REENTRY_B14_HA_TREND_ENABLED:
        if is_long:
            blocks["B14"] = (ha_ltf == 1) & (ha_15m == 1) & (ha_1h == 1) & (k_ltf < 60)
        else:
            blocks["B14"] = (ha_ltf == -1) & (ha_15m == -1) & (ha_1h == -1) & (k_ltf > 40)
    if cfg.REENTRY_B15_STRONG_TREND_ENABLED:
        # B15 uses PREVIOUS 4h bar's DC band — dc_high_4h in NPZ includes current bar so
        # close > dc_high_4h is mathematically impossible. Shift by _bph_4h bars (1 full 4h period).
        _dc4h_prev = np.roll(dc_high_4h, _bph_4h); _dc4h_prev[:_bph_4h] = 0
        _dc4l_prev = np.roll(dc_low_4h, _bph_4h); _dc4l_prev[:_bph_4h] = 0
        if is_long:
            blocks["B15"] = (_dc4h_prev > 0) & (close > _dc4h_prev) & (wt_vel_1h > 2.0) & (k_1h < 85)
        else:
            blocks["B15"] = (_dc4l_prev > 0) & (close < _dc4l_prev) & (wt_vel_1h < -2.0) & (k_1h > 15)
    # === 2026-04-17 REENTRY OVERHAUL BLOCKS (C + D) ===
    # B_WT15M_CROSS (C): 15m WT crossover in position direction + HTF favorable (1h or 4h) + k_15m gate
    # B_PRICE_CROSS_K90 (D): proxy — k_15m in favorable zone. "Price crosses exit" requires per-trade state
    # handled in simulate() loop, so the vectorized block emits candidates and simulate() gates on exit-price cross.
    if getattr(cfg, 'REENTRY_WT15M_CROSS_ENABLED', True):
        wt1_15m_prev = np.roll(wt1_15m, _bph_15m); wt1_15m_prev[:_bph_15m] = wt1_15m[:_bph_15m]
        wt2_15m_prev = np.roll(wt2_15m, _bph_15m); wt2_15m_prev[:_bph_15m] = wt2_15m[:_bph_15m]
        wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
        _k_max = float(getattr(cfg, 'REENTRY_WT15M_K_MAX', 50.0))
        _htf_req = bool(getattr(cfg, 'REENTRY_WT15M_HTF_FAVOR_REQUIRED', True))
        if is_long:
            _just_crossed = (wt1_15m > wt2_15m) & (wt1_15m_prev <= wt2_15m_prev)
            _htf_fav = (wt1_1h > wt2_1h) | (wt1_4h > wt2_4h)
            _gate = _just_crossed & (_htf_fav | (not _htf_req)) & (k_15m < _k_max)
            blocks["B_WT15M_CROSS"] = _gate
        else:
            _just_crossed = (wt1_15m < wt2_15m) & (wt1_15m_prev >= wt2_15m_prev)
            _htf_fav = (wt1_1h < wt2_1h) | (wt1_4h < wt2_4h)
            _gate = _just_crossed & (_htf_fav | (not _htf_req)) & (k_15m > (100.0 - _k_max))
            blocks["B_WT15M_CROSS"] = _gate
    if getattr(cfg, 'REENTRY_K15M_PARTIAL_ENABLED', True):
        # D is a sizing-adjusted variant — the signal is "any bar with favorable k_15m".
        # The "price crosses exit" gate and 100%-vs-partial multiplier happen in simulate()
        # using the bar's prev-exit price. Block emits the candidate window.
        _k_thr = float(getattr(cfg, 'REENTRY_K15M_PARTIAL_THRESHOLD', 90.0))
        if is_long:
            blocks["B_PRICE_CROSS_K90"] = (k_15m < _k_thr) & (wt1_15m > wt2_15m)
        else:
            blocks["B_PRICE_CROSS_K90"] = (k_15m > (100.0 - _k_thr)) & (wt1_15m < wt2_15m)

    # ═══════════════════════════════════════════════════════════════
    # PULLBACK-IN-TREND BLOCKS — enter EARLY, not at end of move
    # Core idea: fundamentally rising (HTF up) ticker + temp pullback → enter at temp bottom
    # Mirror for shorts: fundamentally falling + temp rally → enter at temp top
    # ═══════════════════════════════════════════════════════════════
    # Additional indicators for pullback detection
    bb_pctb_1h_arr = _safe(npz, 'bb_pct_b_1h', n, 0.5)
    wt_vel_4h_arr = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_D_arr = _safe(npz, 'wt_velocity_D', n)
    rsi_1h_arr = _safe(npz, 'rsi_1h', n, 50)
    ha_D_arr = _ha_int(npz, 'ha_D', n)
    ha_4h_arr = _ha_int(npz, 'ha_4h', n)
    sma200_1h = _safe(npz, 'sma_200_1h', n)
    k_ltf_prev2 = np.roll(k_ltf, 2); k_ltf_prev2[:2] = k_ltf[:2]
    # Higher TF WT not declared in this scope — pull from npz
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)

    # TIGHTENED PULLBACK BLOCKS (2026-04-16) — strict fundamental trend + deep pullback + reversal confirm
    # Principle: only enter when BOTH (1) long-term trend is CLEARLY in our direction
    # AND (2) temporary pullback gives us a better price. Skip neutral/sideways.

    # B_PULL1: ALL HTFs aligned + LTF DEEP oversold + stoch+vel both turning up
    if getattr(cfg, 'REENTRY_PULL1_ENABLED', True):
        if is_long:
            # STRICT: D green, 1h+4h trend up, 1h NOT overbought
            htf_uptrend = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (wt1_D > wt2_D) & (ha_D_arr == 1) & (k_1h < 70)
            deep_pullback = (k_ltf < 20) & (wt1_ltf < -35)  # TIGHTER: deep oversold
            reversing = (k_ltf > k_ltf_prev) & (wt_vel_ltf > 0) & (k_ltf_prev < k_ltf_prev2)  # actively turning
            blocks["B_PULL1"] = htf_uptrend & deep_pullback & reversing
        else:
            htf_downtrend = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (wt1_D < wt2_D) & (ha_D_arr == -1) & (k_1h > 30)
            deep_rally = (k_ltf > 80) & (wt1_ltf > 35)
            reversing = (k_ltf < k_ltf_prev) & (wt_vel_ltf < 0) & (k_ltf_prev > k_ltf_prev2)
            blocks["B_PULL1"] = htf_downtrend & deep_rally & reversing

    # B_PULL2: D rising strongly (wt_vel_D > 1) + price PULLED BACK to SMA200_1h
    if getattr(cfg, 'REENTRY_PULL2_ENABLED', True):
        if is_long:
            rising_fundamentals = (wt_vel_D_arr > 1.0) & (wt_vel_4h_arr > 0) & (ha_D_arr == 1) & (ha_4h_arr >= 0)
            pullback_near_sma = (sma200_1h > 0) & (close < sma200_1h * 1.01) & (close > sma200_1h * 0.98)
            momentum_returning = (k_ltf > d_ltf) & (k_ltf < 35) & (wt_vel_ltf > 0)
            blocks["B_PULL2"] = rising_fundamentals & pullback_near_sma & momentum_returning
        else:
            falling_fundamentals = (wt_vel_D_arr < -1.0) & (wt_vel_4h_arr < 0) & (ha_D_arr == -1) & (ha_4h_arr <= 0)
            rally_near_sma = (sma200_1h > 0) & (close > sma200_1h * 0.99) & (close < sma200_1h * 1.02)
            momentum_weakening = (k_ltf < d_ltf) & (k_ltf > 65) & (wt_vel_ltf < 0)
            blocks["B_PULL2"] = falling_fundamentals & rally_near_sma & momentum_weakening

    # B_PULL3: BB EXTREME lower band (pctb < 0.15) in strict uptrend + stoch cross
    if getattr(cfg, 'REENTRY_PULL3_ENABLED', True):
        if is_long:
            trend_up = (wt1_1h > wt2_1h) & (wt1_4h > wt2_4h) & (ha_D_arr == 1)
            at_extreme_band = bb_pctb_1h_arr < 0.15
            stoch_turning_up = (k_ltf_prev <= d_ltf) & (k_ltf > d_ltf) & (k_ltf < 30)
            blocks["B_PULL3"] = trend_up & at_extreme_band & stoch_turning_up
        else:
            trend_down = (wt1_1h < wt2_1h) & (wt1_4h < wt2_4h) & (ha_D_arr == -1)
            at_extreme_band = bb_pctb_1h_arr > 0.85
            stoch_turning_down = (k_ltf_prev >= d_ltf) & (k_ltf < d_ltf) & (k_ltf > 70)
            blocks["B_PULL3"] = trend_down & at_extreme_band & stoch_turning_down

    # B_PULL4: RSI extreme pullback in trend + LTF WT turning from zero line
    if getattr(cfg, 'REENTRY_PULL4_ENABLED', True):
        if is_long:
            pullback_rsi = rsi_1h_arr < 35  # TIGHTER: deeper RSI pullback
            htf_healthy = (ha_4h_arr == 1) & (ha_D_arr == 1)  # STRICT both TF green
            wt_bouncing_ltf = (wt_vel_ltf > 0) & (wt1_ltf < -15) & (wt1_ltf > wt1_15m * 0.7)  # bouncing from below
            blocks["B_PULL4"] = pullback_rsi & htf_healthy & wt_bouncing_ltf
        else:
            rally_rsi = rsi_1h_arr > 65
            htf_bearish = (ha_4h_arr == -1) & (ha_D_arr == -1)
            wt_rolling_ltf = (wt_vel_ltf < 0) & (wt1_ltf > 15) & (wt1_ltf < wt1_15m * 1.3)
            blocks["B_PULL4"] = rally_rsi & htf_bearish & wt_rolling_ltf

    # ═══════════════════════════════════════════════════════════════
    # TRADIER ALPHA BLOCKS (2026-04-16) — ported from tradier_manage.py
    # K_ZONE: live Sharpe 4.23 / 20,605 trades (live config value 35 / 65)
    # FH_MOMENTUM: live Sharpe 1.54 validated (2026-04-07)
    # MFI_D_OVERSOLD: MFI_LONG_THRESHOLD_D=20 from live config
    # ═══════════════════════════════════════════════════════════════
    k_4h_arr = _safe(npz, 'stoch_k_4h', n, 50)
    d_4h_arr = _safe(npz, 'stoch_d_4h', n, 50)

    # B_KZONE: K_4h in zone AND turning (K crosses over D for longs)
    if getattr(cfg, 'REENTRY_B_KZONE_ENABLED', False):
        kz_long_th = float(getattr(cfg, 'K_ZONE_LONG_THRESHOLD_TRADIER',
                              getattr(cfg, 'K_ZONE_LONG_THRESHOLD', 35)))
        kz_short_th = float(getattr(cfg, 'K_ZONE_SHORT_THRESHOLD_TRADIER',
                              getattr(cfg, 'K_ZONE_SHORT_THRESHOLD', 65)))
        if is_long:
            blocks["B_KZONE"] = (k_4h_arr < kz_long_th) & (k_4h_arr > d_4h_arr)
        else:
            blocks["B_KZONE"] = (k_4h_arr > kz_short_th) & (k_4h_arr < d_4h_arr)

    # B_FH_MOM: first 60 min after US market open (13:30 UTC) + minimum move
    if getattr(cfg, 'REENTRY_B_FH_MOM_ENABLED', False):
        ts = npz.get('timestamps')
        if ts is None or len(ts) == 0:
            blocks["B_FH_MOM"] = np.zeros(n, dtype=bool)
        else:
            ts = np.asarray(ts, dtype=np.int64)[:n]
            if len(ts) < n:
                pad = np.full(n - len(ts), ts[-1] if len(ts) else 0, dtype=np.int64)
                ts = np.concatenate([ts, pad])
            sec_of_day = ts % 86400
            win = int(getattr(cfg, 'FH_MOMENTUM_WINDOW_SEC', 3600))
            # Market open = 13:30 UTC = 48600s. First-hour window: [48600, 48600+win]
            fh_window = (sec_of_day >= 48600) & (sec_of_day < 48600 + win)
            close_1h = _safe(npz, 'close_1h', n)
            open_1h = _safe(npz, 'open_1h', n)
            mv_pct = np.zeros(n, dtype=np.float32)
            safe_o = np.where(open_1h > 0, open_1h, 1.0)
            mv_pct = (close_1h - open_1h) / safe_o * 100.0
            min_move = float(getattr(cfg, 'FH_MOMENTUM_MIN_MOVE_PCT', 0.5))
            if is_long:
                blocks["B_FH_MOM"] = fh_window & (mv_pct >= min_move)
            else:
                blocks["B_FH_MOM"] = fh_window & (mv_pct <= -min_move)

    # B_MFI_D_OVERSOLD: MFI_D < 20 for long / > 80 for short (mean-reversion)
    if getattr(cfg, 'REENTRY_B_MFI_D_OVERSOLD_ENABLED', False):
        mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
        long_th = float(getattr(cfg, 'MFI_LONG_THRESHOLD_D', 20))
        short_th = float(getattr(cfg, 'MFI_SHORT_THRESHOLD_D', 80))
        if is_long:
            blocks["B_MFI_D_OVERSOLD"] = mfi_D_arr < long_th
        else:
            blocks["B_MFI_D_OVERSOLD"] = mfi_D_arr > short_th

    return blocks


def _compute_le_score_arr(npz: dict, n: int, is_long: bool, cfg) -> np.ndarray:
    """Vectorized proxy of local_extremes_scorer for the V8 engine.

    Returns float64 array (0-100) — one score per bar. Used as entry gate when
    LOCAL_EXTREMES_SCORER_ENABLED=True: only bars with score >= LOCAL_EXTREMES_MIN_SCORE pass.
    Missing NPZ fields zero-fill gracefully (same as live scorer's neutral defaults).
    """
    score = np.zeros(n, dtype=np.float64)

    k5  = _safe(npz, 'stoch_k_5m',  n, 50.0)
    k15 = _safe(npz, 'stoch_k_15m', n, 50.0)
    k1h = _safe(npz, 'stoch_k_1h',  n, 50.0)
    k4h = _safe(npz, 'stoch_k_4h',  n, 50.0)
    kD  = _safe(npz, 'stoch_k_D',   n, 50.0)
    if is_long:
        score += np.where(k5  < 20, 5.0, 0.0)
        score += np.where(k15 < 20, 6.0, 0.0)
        score += np.where(k1h < 25, 7.0, 0.0)
        score += np.where(k4h < 35, 4.0, 0.0)
        score += np.where(kD  < 40, 3.0, 0.0)
    else:
        score += np.where(k5  > 80, 5.0, 0.0)
        score += np.where(k15 > 80, 6.0, 0.0)
        score += np.where(k1h > 75, 7.0, 0.0)
        score += np.where(k4h > 65, 4.0, 0.0)
        score += np.where(kD  > 60, 3.0, 0.0)

    wt1_5m  = _safe(npz, 'wt1_5m',  n, 0.0)
    wt2_5m  = _safe(npz, 'wt2_5m',  n, 0.0)
    wt1_15m = _safe(npz, 'wt1_15m', n, 0.0)
    wt2_15m = _safe(npz, 'wt2_15m', n, 0.0)
    wt1_1h  = _safe(npz, 'wt1_1h',  n, 0.0)
    wt2_1h  = _safe(npz, 'wt2_1h',  n, 0.0)
    wt1_4h  = _safe(npz, 'wt1_4h',  n, 0.0)
    wt2_4h  = _safe(npz, 'wt2_4h',  n, 0.0)
    vel_1h  = _safe(npz, 'wt_velocity_1h', n, 0.0)
    vel_D   = _safe(npz, 'wt_velocity_D',  n, 0.0)
    wt_pts = np.zeros(n, dtype=np.float64)
    if is_long:
        wt_pts += np.where(wt1_5m  > wt2_5m,  3.0, 0.0)
        wt_pts += np.where(wt1_15m > wt2_15m, 4.0, 0.0)
        wt_pts += np.where(wt1_1h  > wt2_1h,  5.0, 0.0)
        wt_pts += np.where(wt1_4h  > wt2_4h,  4.0, 0.0)
        wt_pts += np.where(vel_1h  > 0, 2.0, 0.0)
        wt_pts += np.where(vel_D   > 0, 2.0, 0.0)
    else:
        wt_pts += np.where(wt1_5m  < wt2_5m,  3.0, 0.0)
        wt_pts += np.where(wt1_15m < wt2_15m, 4.0, 0.0)
        wt_pts += np.where(wt1_1h  < wt2_1h,  5.0, 0.0)
        wt_pts += np.where(wt1_4h  < wt2_4h,  4.0, 0.0)
        wt_pts += np.where(vel_1h  < 0, 2.0, 0.0)
        wt_pts += np.where(vel_D   < 0, 2.0, 0.0)
    score += np.minimum(wt_pts, 20.0)

    dc1h = _safe(npz, 'dc_position_1h', n, 0.5)
    dc4h = _safe(npz, 'dc_position_4h', n, 0.5)
    dcD  = _safe(npz, 'dc_position_D',  n, 0.5)
    bb1h = _safe(npz, 'bb_pct_b_1h',   n, 0.5)
    bb4h = _safe(npz, 'bb_pct_b_4h',   n, 0.5)
    dc_pts = np.zeros(n, dtype=np.float64)
    if is_long:
        dc_pts += np.where(dc1h < 0.20, 7.0, np.where(dc1h < 0.35, 3.0, 0.0))
        dc_pts += np.where(dc4h < 0.30, 5.0, np.where(dc4h < 0.45, 2.0, 0.0))
        dc_pts += np.where(dcD  < 0.40, 4.0, 0.0)
        dc_pts += np.where(bb1h < 0.20, 2.0, 0.0)
        dc_pts += np.where(bb4h < 0.30, 2.0, 0.0)
    else:
        dc_pts += np.where(dc1h > 0.80, 7.0, np.where(dc1h > 0.65, 3.0, 0.0))
        dc_pts += np.where(dc4h > 0.70, 5.0, np.where(dc4h > 0.55, 2.0, 0.0))
        dc_pts += np.where(dcD  > 0.60, 4.0, 0.0)
        dc_pts += np.where(bb1h > 0.80, 2.0, 0.0)
        dc_pts += np.where(bb4h > 0.70, 2.0, 0.0)
    score += np.minimum(dc_pts, 20.0)

    mfi5  = _safe(npz, 'mfi_5m',  n, 50.0)
    mfi15 = _safe(npz, 'mfi_15m', n, 50.0)
    mfi1h = _safe(npz, 'mfi_1h',  n, 50.0)
    mfi4h = _safe(npz, 'mfi_4h',  n, 50.0)
    mfiD  = _safe(npz, 'mfi_D',   n, 50.0)
    if is_long:
        score += np.where(mfi5  < 30, 2.0, 0.0)
        score += np.where(mfi15 < 30, 3.0, 0.0)
        score += np.where(mfi1h < 30, 5.0, 0.0)
        score += np.where(mfi4h < 35, 3.0, 0.0)
        score += np.where(mfiD  < 40, 2.0, 0.0)
    else:
        score += np.where(mfi5  > 70, 2.0, 0.0)
        score += np.where(mfi15 > 70, 3.0, 0.0)
        score += np.where(mfi1h > 70, 5.0, 0.0)
        score += np.where(mfi4h > 65, 3.0, 0.0)
        score += np.where(mfiD  > 60, 2.0, 0.0)

    ha5m  = _ha_int(npz, 'ha_5m',  n).astype(np.float64)
    ha15m = _ha_int(npz, 'ha_15m', n).astype(np.float64)
    ha1h  = _ha_int(npz, 'ha_1h',  n).astype(np.float64)
    lr1h  = _safe(npz, 'lr_trend_1h', n, 0.0)
    wt_ts_1h = _safe(npz, 'wt_trough_structure_1h', n, 0.0)
    wt_ms_1h = _safe(npz, 'wt_momentum_state_1h', n, 0.0)
    mom_pts = np.zeros(n, dtype=np.float64)
    if is_long:
        mom_pts += np.where(ha5m  == 1, 2.0, 0.0)
        mom_pts += np.where(ha15m == 1, 2.0, 0.0)
        mom_pts += np.where(ha1h  == 1, 4.0, 0.0)
        mom_pts += np.where(lr1h  >  0, 3.0, 0.0)
        mom_pts += np.where(wt_ts_1h == 1,  2.0, 0.0)
        mom_pts += np.where(wt_ms_1h >= 1,  2.0, 0.0)
    else:
        mom_pts += np.where(ha5m  == -1, 2.0, 0.0)
        mom_pts += np.where(ha15m == -1, 2.0, 0.0)
        mom_pts += np.where(ha1h  == -1, 4.0, 0.0)
        mom_pts += np.where(lr1h  <  0,  3.0, 0.0)
        mom_pts += np.where(wt_ts_1h == -1, 2.0, 0.0)
        mom_pts += np.where(wt_ms_1h <= -1, 2.0, 0.0)
    score += np.minimum(mom_pts, 15.0)

    rvol5  = _safe(npz, 'relative_volume_5m',  n, 1.0)
    rvol15 = _safe(npz, 'relative_volume_15m', n, 1.0)
    rvol1h = _safe(npz, 'relative_volume_1h',  n, 1.0)
    vol_pts = (np.where(rvol5  > 1.5, 2.0, 0.0)
               + np.where(rvol15 > 1.2, 2.0, 0.0)
               + np.where(rvol1h > 1.2, 1.0, 0.0))
    score += np.minimum(vol_pts, 5.0)

    return np.minimum(score, 100.0)


def _rolling_max(arr: np.ndarray, w: int) -> np.ndarray:
    """Right-aligned rolling max over window w. O(n) using numpy stride tricks."""
    n = len(arr)
    if n == 0 or w <= 1:
        return arr.copy()
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        if n < w:
            return np.maximum.accumulate(arr)
        sw = sliding_window_view(arr, w).max(axis=1)
        out = np.empty(n, dtype=arr.dtype)
        out[:w - 1] = np.maximum.accumulate(arr[:w - 1]) if w > 1 else arr[:w - 1]
        out[w - 1:] = sw
        return out
    except Exception:
        out = np.empty(n, dtype=arr.dtype)
        for i in range(n):
            lo = max(0, i - w + 1)
            out[i] = arr[lo:i + 1].max()
        return out


def _rolling_min(arr: np.ndarray, w: int) -> np.ndarray:
    n = len(arr)
    if n == 0 or w <= 1:
        return arr.copy()
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        if n < w:
            return np.minimum.accumulate(arr)
        sw = sliding_window_view(arr, w).min(axis=1)
        out = np.empty(n, dtype=arr.dtype)
        out[:w - 1] = np.minimum.accumulate(arr[:w - 1]) if w > 1 else arr[:w - 1]
        out[w - 1:] = sw
        return out
    except Exception:
        out = np.empty(n, dtype=arr.dtype)
        for i in range(n):
            lo = max(0, i - w + 1)
            out[i] = arr[lo:i + 1].min()
        return out


def compute_rz_cascade_signals(npz, n, is_long, cfg):
    """RZ cascade (2026-04-21 user directive). Per-TF signals → hierarchical entry/exit.

    At each TF, detect:
      - at_upper: price at dc_high (resistance RZ)
      - at_lower: price at dc_low (support RZ)
      - breakout_up: close > dc_high_prev AND wt_delta > 0 AND wt_velocity > 0 AND new K-bar high
      - breakout_down: close < dc_low_prev AND wt_delta < 0 AND wt_velocity < 0 AND new K-bar low
      - reverse_down: was at upper, now dropping, wt_delta < 0, wt_velocity < 0 (bearish reversal)
      - reverse_up: was at lower, now rising, wt_delta > 0, wt_velocity > 0 (bullish reversal)

    Entry signal (LONG): breakout_up on LTF AND ≥MIN_TF_ALIGN other TFs bullish (breakout OR reverse_up).
    Exit signal (LONG): any TF reverse_down fires (hard exit on rejection at resistance).
    Mirror for SHORT.
    """
    _ltf = getattr(cfg, 'LTF', '3m')
    tf_fields = {
        'LTF': _ltf, '15m': '15m', '1h': '1h', '4h': '4h', 'D': 'D',
    }
    if bool(getattr(cfg, 'RZ_CASCADE_USE_W_M', False)):
        tf_fields['W'] = 'W'; tf_fields['M'] = 'M'

    _wt_delta_min = float(getattr(cfg, 'RZ_CASCADE_WT_DELTA_MIN', 0.1))
    _vel_min = float(getattr(cfg, 'RZ_CASCADE_VEL_MIN', 0.1))
    _high_lb = int(getattr(cfg, 'RZ_CASCADE_HIGH_LOOKBACK', 20))
    _require_new_high = bool(getattr(cfg, 'RZ_CASCADE_REQUIRE_NEW_HIGH', False))
    _at_rz_band = float(getattr(cfg, 'RZ_CASCADE_AT_RZ_BAND_PCT', 1.0)) / 100.0

    per_tf = {}
    for tf_label, tf in tf_fields.items():
        close = _safe(npz, f'close_{tf}', n)
        dc_hi = _safe(npz, f'dc_high_{tf}', n)
        dc_lo = _safe(npz, f'dc_low_{tf}', n)
        wt1 = _safe(npz, f'wt1_{tf}', n); wt2 = _safe(npz, f'wt2_{tf}', n)
        wt_vel = _safe(npz, f'wt_velocity_{tf}', n)
        # Skip TF if key fields are all zero (not precomputed)
        if (wt1.sum() == 0 and wt2.sum() == 0) or (dc_hi.sum() == 0 and dc_lo.sum() == 0):
            continue
        wt_delta = wt1 - wt2
        dc_hi_prev = np.roll(dc_hi, 1); dc_hi_prev[0] = dc_hi[0]
        dc_lo_prev = np.roll(dc_lo, 1); dc_lo_prev[0] = dc_lo[0]
        px_max_lb = _rolling_max(close, _high_lb)
        px_min_lb = _rolling_min(close, _high_lb)
        px_max_prev = np.roll(px_max_lb, 1); px_max_prev[0] = px_max_lb[0]
        px_min_prev = np.roll(px_min_lb, 1); px_min_prev[0] = px_min_lb[0]
        # At RZ — configurable band (v1 used 0.1%, too tight; v2 default 1%)
        at_upper = (dc_hi_prev > 0) & (close >= dc_hi_prev * (1.0 - _at_rz_band))
        at_lower = (dc_lo_prev > 0) & (close <= dc_lo_prev * (1.0 + _at_rz_band))
        # Breakout — close breaks DC band + wt direction + velocity (new-high optional)
        breakout_up = (dc_hi_prev > 0) & (close > dc_hi_prev) & (wt_delta > _wt_delta_min) & (wt_vel > _vel_min)
        breakout_down = (dc_lo_prev > 0) & (close < dc_lo_prev) & (wt_delta < -_wt_delta_min) & (wt_vel < -_vel_min)
        if _require_new_high:
            breakout_up = breakout_up & (close > px_max_prev)
            breakout_down = breakout_down & (close < px_min_prev)
        # Reversal — was at RZ, now rejecting with wt turn
        close_prev = np.roll(close, 1); close_prev[0] = close[0]
        at_upper_prev = np.roll(at_upper, 1); at_upper_prev[0] = at_upper[0]
        at_lower_prev = np.roll(at_lower, 1); at_lower_prev[0] = at_lower[0]
        reverse_down = at_upper_prev & (close < close_prev) & (wt_delta < 0) & (wt_vel < 0)
        reverse_up = at_lower_prev & (close > close_prev) & (wt_delta > 0) & (wt_vel > 0)
        per_tf[tf_label] = {
            'at_upper': at_upper, 'at_lower': at_lower,
            'breakout_up': breakout_up, 'breakout_down': breakout_down,
            'reverse_up': reverse_up, 'reverse_down': reverse_down,
        }

    if not per_tf or 'LTF' not in per_tf:
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

    min_align = int(getattr(cfg, 'RZ_CASCADE_MIN_TF_ALIGN', 1))
    exit_any_tf = bool(getattr(cfg, 'RZ_CASCADE_EXIT_ANY_TF', True))
    min_rev_tfs = int(getattr(cfg, 'RZ_CASCADE_EXIT_MIN_REV_TFS', 2))

    # Entry: LTF breakout + alignment across other TFs
    if is_long:
        ltf_break = per_tf['LTF']['breakout_up']
        align_count = np.zeros(n, dtype=int)
        for tf_label, sigs in per_tf.items():
            if tf_label == 'LTF': continue
            align_count = align_count + (sigs['breakout_up'] | sigs['reverse_up']).astype(int)
        entry_sig = ltf_break & (align_count >= min_align)
        # Exit: count TFs showing reverse_down, require min_rev_tfs
        if exit_any_tf:
            rev_count = np.zeros(n, dtype=int)
            for tf_label, sigs in per_tf.items():
                rev_count = rev_count + sigs['reverse_down'].astype(int)
            exit_sig = rev_count >= min_rev_tfs
        else:
            exit_sig = per_tf['LTF']['reverse_down']
    else:
        ltf_break = per_tf['LTF']['breakout_down']
        align_count = np.zeros(n, dtype=int)
        for tf_label, sigs in per_tf.items():
            if tf_label == 'LTF': continue
            align_count = align_count + (sigs['breakout_down'] | sigs['reverse_down']).astype(int)
        entry_sig = ltf_break & (align_count >= min_align)
        if exit_any_tf:
            rev_count = np.zeros(n, dtype=int)
            for tf_label, sigs in per_tf.items():
                rev_count = rev_count + sigs['reverse_up'].astype(int)
            exit_sig = rev_count >= min_rev_tfs
        else:
            exit_sig = per_tf['LTF']['reverse_up']

    return entry_sig, exit_sig


# ============================================================================
# ENTRY ENGINES — vectorized adaptations of entry_engine_{wt,stoch,dc,htf}.py
# Scalar source-of-truth: /Users/niels/Documents/binance/entry_engine_*.py
# Each returns (sig_mask: np.ndarray[bool, n], score_arr: np.ndarray[float32, n]).
# Bar-level — no I/O, no Python loop over n. Side-aware (is_long).
# ============================================================================
def _engine_wt_vec(npz, n, is_long, cfg):
    """Vectorized port of entry_engine_wt.should_fire_wt_entry. 5-of-5 TF align.
    2026-04-27 LTF fix: was hardcoded '3m'; now reads cfg.LTF so tradier (5m base) reads wt1_5m etc."""
    _ltf = getattr(cfg, 'LTF', '3m')
    tfs = (_ltf, '15m', '1h', '4h', 'D')
    aligned_count = np.zeros(n, dtype=np.int8)
    for tf in tfs:
        w1 = _safe(npz, f'wt1_{tf}', n, 0.0)
        w2 = _safe(npz, f'wt2_{tf}', n, 0.0)
        if is_long:
            aligned_count = aligned_count + (w1 > w2).astype(np.int8)
        else:
            aligned_count = aligned_count + (w1 < w2).astype(np.int8)
    score = np.zeros(n, dtype=np.float32)
    score = np.where(aligned_count == 5, 1.0, score)
    score = np.where(aligned_count == 4, 0.8, score)
    score = np.where(aligned_count == 3, 0.6, score)
    score = np.where(aligned_count == 2, 0.4, score)
    score = np.where(aligned_count == 1, 0.2, score)
    fire5 = aligned_count == 5
    fire4 = aligned_count == 4
    v_ltf = _safe(npz, f'wt_velocity_{_ltf}', n, 0.0)
    v1h = _safe(npz, 'wt_velocity_1h', n, 0.0)
    v4h = _safe(npz, 'wt_velocity_4h', n, 0.0)
    if is_long:
        vel_ok = (v_ltf > 0) | (v1h > 0) | (v4h > 0)
    else:
        vel_ok = (v_ltf < 0) | (v1h < 0) | (v4h < 0)
    fire3 = (aligned_count == 3) & vel_ok
    sig = fire5 | fire4 | fire3
    return sig, score


def _engine_stoch_vec(npz, n, is_long, cfg):
    """Vectorized port of entry_engine_stoch.should_fire_stoch_entry."""
    _ltf = getattr(cfg, 'LTF', '3m')
    k_3m = _safe(npz, f'stoch_k_{_ltf}', n, 50.0)
    d_3m = _safe(npz, f'stoch_d_{_ltf}', n, 50.0)
    k_3m_prev = np.roll(k_3m, 1); k_3m_prev[0] = k_3m[0]
    d_3m_prev = np.roll(d_3m, 1); d_3m_prev[0] = d_3m[0]
    k_15m = _safe(npz, 'stoch_k_15m', n, 50.0)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50.0)
    k_4h = _safe(npz, 'stoch_k_4h', n, 50.0)
    d_15m = _safe(npz, 'stoch_d_15m', n, 50.0)
    MID = 50.0
    OS = 20.0
    OB = 80.0
    if is_long:
        crossover = (k_3m > d_3m) & (k_3m_prev <= d_3m_prev)
        a1 = k_3m > MID; a2 = k_15m > MID; a3 = k_1h > MID; a4 = k_4h > MID
        extreme = k_3m < OS
        kd_3m_dir = k_3m > d_3m
        kd_15m_dir = k_15m > d_15m
    else:
        crossover = (k_3m < d_3m) & (k_3m_prev >= d_3m_prev)
        a1 = k_3m < MID; a2 = k_15m < MID; a3 = k_1h < MID; a4 = k_4h < MID
        extreme = k_3m > OB
        kd_3m_dir = k_3m < d_3m
        kd_15m_dir = k_15m < d_15m
    aligned_count = a1.astype(np.int8) + a2.astype(np.int8) + a3.astype(np.int8) + a4.astype(np.int8)
    score = np.zeros(n, dtype=np.float32)
    fire_strong = (aligned_count == 4) & crossover & kd_3m_dir & kd_15m_dir
    fire_good = (aligned_count >= 3) & crossover & kd_3m_dir & ~fire_strong
    fire_contra = (aligned_count >= 2) & extreme & kd_3m_dir & ~fire_strong & ~fire_good
    score = np.where(fire_strong, 1.0, score)
    score = np.where(fire_good, 0.7, score)
    score = np.where(fire_contra, 0.5, score)
    sig = fire_strong | fire_good | fire_contra
    return sig, score


def _engine_dc_vec(npz, n, is_long, cfg):
    """Vectorized port of entry_engine_dc.should_fire_dc_entry.
    2026-04-27 LTF fix: was hardcoded '3m'; now reads cfg.LTF so tradier (5m) reads dc_high_5m etc."""
    _ltf = getattr(cfg, 'LTF', '3m')
    FIRE = 0.6; RETEST_TOL = 0.30; BOUNCE_TOL = 0.50
    close = _close_with_mode_check(npz, n, cfg, '_engine_dc_vec')
    dc_h_3m = _safe(npz, f'dc_high_{_ltf}', n, 0.0)
    dc_h_15m = _safe(npz, 'dc_high_15m', n, 0.0)
    dc_h_1h = _safe(npz, 'dc_high_1h', n, 0.0)
    dc_l_3m = _safe(npz, f'dc_low_{_ltf}', n, 0.0)
    dc_l_15m = _safe(npz, 'dc_low_15m', n, 0.0)
    dc_l_1h = _safe(npz, 'dc_low_1h', n, 0.0)
    dc_l_4h = _safe(npz, 'dc_low_4h', n, 0.0)
    dc_h_4h = _safe(npz, 'dc_high_4h', n, 0.0)
    dc_b_15m = _safe(npz, 'dc_basis_15m', n, 0.0)
    ha_3m = _ha_int(npz, f'ha_{_ltf}', n)
    wt1_3m = _safe(npz, f'wt1_{_ltf}', n, 0.0)
    wt2_3m = _safe(npz, f'wt2_{_ltf}', n, 0.0)
    score = np.zeros(n, dtype=np.float32)
    if is_long:
        b3 = (dc_h_3m > 0) & (close > dc_h_3m)
        b15 = (dc_h_15m > 0) & (close > dc_h_15m)
        b1h = (dc_h_1h > 0) & (close > dc_h_1h)
        retest_dist = np.where(dc_b_15m > 0, np.abs(close - dc_b_15m) / np.where(dc_b_15m > 0, dc_b_15m, 1.0) * 100.0, 1e9)
        retest = (dc_b_15m > 0) & (ha_3m == 1) & (close >= dc_b_15m) & (retest_dist <= RETEST_TOL)
        bounce_dist = np.where(dc_l_4h > 0, np.abs(close - dc_l_4h) / np.where(dc_l_4h > 0, dc_l_4h, 1.0) * 100.0, 1e9)
        bounce = (dc_l_4h > 0) & (wt1_3m > wt2_3m) & (bounce_dist <= BOUNCE_TOL)
    else:
        b3 = (dc_l_3m > 0) & (close < dc_l_3m)
        b15 = (dc_l_15m > 0) & (close < dc_l_15m)
        b1h = (dc_l_1h > 0) & (close < dc_l_1h)
        retest_dist = np.where(dc_b_15m > 0, np.abs(close - dc_b_15m) / np.where(dc_b_15m > 0, dc_b_15m, 1.0) * 100.0, 1e9)
        retest = (dc_b_15m > 0) & (ha_3m == -1) & (close <= dc_b_15m) & (retest_dist <= RETEST_TOL)
        bounce_dist = np.where(dc_h_4h > 0, np.abs(close - dc_h_4h) / np.where(dc_h_4h > 0, dc_h_4h, 1.0) * 100.0, 1e9)
        bounce = (dc_h_4h > 0) & (wt1_3m < wt2_3m) & (bounce_dist <= BOUNCE_TOL)
    score = np.where(b3, np.maximum(score, 0.6), score)
    score = np.where(b15, np.maximum(score, 0.8), score)
    score = np.where(b1h, np.maximum(score, 1.0), score)
    score = np.where(retest, np.maximum(score, 0.7), score)
    score = np.where(bounce, np.maximum(score, 0.6), score)
    sig = score >= FIRE
    return sig, score


def _engine_htf_vec(npz, n, is_long, cfg):
    """Vectorized port of entry_engine_htf.should_fire_htf_entry."""
    FIRE = 0.5
    close = _close_with_mode_check(npz, n, cfg, '_engine_htf_vec')
    sma_d = _safe(npz, 'sma_200_D', n, 0.0)
    dcb_d = _safe(npz, 'dc_basis_D', n, 0.0)
    dcb_d_ant = _safe(npz, 'dc_basis_D_ant', n, 0.0)
    ha_4h = _ha_int(npz, 'ha_4h', n)
    ha_d = _ha_int(npz, 'ha_D', n)
    k_4h = _safe(npz, 'stoch_k_4h', n, 0.0)
    score = np.zeros(n, dtype=np.float32)
    valid_sma = (sma_d > 0) & (close > 0)
    if is_long:
        score = score + np.where(valid_sma & (close > sma_d * 1.01), 0.3, 0.0).astype(np.float32)
        score = score + np.where((dcb_d > 0) & (dcb_d_ant > 0) & (dcb_d > dcb_d_ant), 0.2, 0.0).astype(np.float32)
        score = score + np.where(ha_4h == 1, 0.2, 0.0).astype(np.float32)
        score = score + np.where(ha_d == 1, 0.2, 0.0).astype(np.float32)
        score = score + np.where((k_4h > 0) & (k_4h > 50), 0.1, 0.0).astype(np.float32)
    else:
        score = score + np.where(valid_sma & (close < sma_d * 0.99), 0.3, 0.0).astype(np.float32)
        score = score + np.where((dcb_d > 0) & (dcb_d_ant > 0) & (dcb_d < dcb_d_ant), 0.2, 0.0).astype(np.float32)
        score = score + np.where(ha_4h == -1, 0.2, 0.0).astype(np.float32)
        score = score + np.where(ha_d == -1, 0.2, 0.0).astype(np.float32)
        score = score + np.where((k_4h > 0) & (k_4h < 50), 0.1, 0.0).astype(np.float32)
    sig = score >= FIRE
    return sig, score


def _combine_engine_sigs(sigs, mode):
    """Combine list of (n,)-bool arrays via OR / AND / MAJORITY. Empty list -> None."""
    if not sigs:
        return None
    mode_u = str(mode).upper()
    if len(sigs) == 1:
        return sigs[0]
    if mode_u == "AND":
        out = sigs[0].copy()
        for s in sigs[1:]:
            out = out & s
        return out
    if mode_u == "MAJORITY":
        stk = np.stack(sigs, axis=0).astype(np.int8)
        cnt = stk.sum(axis=0)
        return cnt >= ((len(sigs) + 1) // 2)
    out = sigs[0].copy()
    for s in sigs[1:]:
        out = out | s
    return out


# 2026-04-30 Job 1 (i): sector→multiplier lookup cache for STRENGTH_MIN_SCORE tilt.
# Loaded once from stocks_sectors.json on first use. Tradier-only.
_SYM_TO_SECTOR_CACHE: Optional[Dict[str, str]] = None


def _build_sym_sector_map() -> Dict[str, str]:
    """Inverted index sym -> sector_key. Cached; loaded from stocks_sectors.json."""
    global _SYM_TO_SECTOR_CACHE
    if _SYM_TO_SECTOR_CACHE is not None:
        return _SYM_TO_SECTOR_CACHE
    try:
        path = Path(__file__).resolve().parent / "stocks_sectors.json"
        with open(path) as f:
            data = json.load(f)
        m: Dict[str, str] = {}
        for sector_key, syms in data.items():
            if sector_key.startswith("_") or not isinstance(syms, list):
                continue
            if sector_key in ("mix_12", "all", "tech_big"):
                continue
            for s in syms:
                if isinstance(s, str) and s not in m:
                    m[s] = sector_key
        _SYM_TO_SECTOR_CACHE = m
    except Exception:
        _SYM_TO_SECTOR_CACHE = {}
    return _SYM_TO_SECTOR_CACHE


def _sector_strength_mult(sym: Optional[str], cfg) -> float:
    """Return multiplicative factor for STRENGTH_MIN_SCORE (tradier mode only).
    Default 1.0 (back-compat). Active only when:
      - cfg.MODE == 'tradier'
      - cfg.SECTOR_ENTRY_STRENGTH_MULT_TRADIER is a non-empty dict
      - sym is non-None and maps to a sector listed in the dict
    """
    mult_map = getattr(cfg, 'SECTOR_ENTRY_STRENGTH_MULT_TRADIER', None)
    if not mult_map or not isinstance(mult_map, dict):
        return 1.0
    if getattr(cfg, 'MODE', 'crypto') != 'tradier':
        return 1.0
    if not sym:
        return 1.0
    sector = _build_sym_sector_map().get(sym)
    if sector is None:
        return 1.0
    try:
        return float(mult_map.get(sector, 1.0))
    except (TypeError, ValueError):
        return 1.0


def _golden_rule_vec(npz, n, is_long, cfg):
    # GOLDEN RULE — breakout-then-retest gate + multiplier on SATOSHIT impulse.
    # Architecture: SATOSHIT = entry impulse. GOLDEN RULE = gate + position-size controller.
    #
    # LONG pattern:
    #   Phase 1 (breakout): close crosses ABOVE dc_high_1h or bb_upper_1h → tiny entry (MULT_BREAKOUT)
    #   Phase 2 (retest):   after breakout, close crosses ABOVE dc_basis_1h from below → 300% entry (MULT_RETEST)
    # SHORT mirror:
    #   Phase 1: close crosses BELOW dc_low_1h or bb_lower_1h → tiny entry
    #   Phase 2: close crosses BELOW dc_basis_1h from above → 300% entry
    #
    # GATE_MODE=True (default, AND): base_sig AND fire — SATOSHIT must also fire at that bar.
    # GATE_MODE=False (OR): legacy additive mode. fire bars added to base_sig.
    if not bool(getattr(cfg, 'GOLDEN_RULE_ENABLED', True)):
        return np.zeros(n, dtype=bool), np.ones(n, dtype=np.float32)
    from scipy.ndimage import maximum_filter1d as _mf1d
    close = _close_with_mode_check(npz, n, cfg, '_golden_rule_vec')
    dc_h_1h = _safe(npz, 'dc_high_1h', n)
    dc_l_1h = _safe(npz, 'dc_low_1h', n)
    dc_b_1h = _safe(npz, 'dc_basis_1h', n)
    bb_u_1h = _safe(npz, 'bb_upper_1h', n)
    bb_l_1h = _safe(npz, 'bb_lower_1h', n)
    m_breakout = float(getattr(cfg, 'GOLDEN_RULE_MULT_BREAKOUT', 0.3))
    m_retest = float(getattr(cfg, 'GOLDEN_RULE_MULT_RETEST', 3.0))
    lookback = int(getattr(cfg, 'GOLDEN_RULE_RETEST_BARS', 80))
    close_prev = np.roll(close, 1)
    close_prev[0] = close[0]
    if is_long:
        above_dch = close > dc_h_1h
        above_bbu = close > bb_u_1h
        above_dch_prev = np.roll(above_dch, 1); above_dch_prev[0] = False
        above_bbu_prev = np.roll(above_bbu, 1); above_bbu_prev[0] = False
        breakout = (above_dch & ~above_dch_prev) | (above_bbu & ~above_bbu_prev)
        recently_broke = _mf1d(breakout.astype(np.float32), size=lookback) > 0
        above_dcb = close > dc_b_1h
        above_dcb_prev = np.roll(above_dcb, 1); above_dcb_prev[0] = False
        cross_basis_up = above_dcb & ~above_dcb_prev
        retest = recently_broke & cross_basis_up & ~above_dch & ~above_bbu
    else:
        below_dcl = close < dc_l_1h
        below_bbl = close < bb_l_1h
        below_dcl_prev = np.roll(below_dcl, 1); below_dcl_prev[0] = False
        below_bbl_prev = np.roll(below_bbl, 1); below_bbl_prev[0] = False
        breakout = (below_dcl & ~below_dcl_prev) | (below_bbl & ~below_bbl_prev)
        recently_broke = _mf1d(breakout.astype(np.float32), size=lookback) > 0
        above_dcb = close > dc_b_1h
        above_dcb_prev = np.roll(above_dcb, 1); above_dcb_prev[0] = False
        cross_basis_down = ~above_dcb & above_dcb_prev
        retest = recently_broke & cross_basis_down & ~below_dcl & ~below_bbl
    fire = breakout | retest
    mult = np.ones(n, dtype=np.float32)
    mult = np.where(breakout, m_breakout, mult)
    mult = np.where(retest, m_retest, mult)
    return fire, mult


def _gr_htf_gate_vec(npz, n: int, is_long: bool, min_tfs: int, min_ind: int, cfg) -> np.ndarray:
    """Vectorized 7-indicator HTF gate mirroring golden_rule_htf.score_entry_htf().
    Returns bool mask shape (n,). True = bar passes, False = bar blocked.
    7 indicators per TF: WT, RSI, MFI, DC_position, BB_pct_b, RVOL, stoch_K.
    """
    if min_tfs <= 0:
        return np.ones(n, dtype=bool)
    mode = str(getattr(cfg, 'MODE', 'crypto'))
    tfs = ['5m', '15m', '1h', '4h', 'D', 'W'] if mode == 'tradier' else ['3m', '15m', '1h', '4h', 'D']
    cl_field = 'close_5m' if mode == 'tradier' else 'close_3m'
    ind_matrix = np.zeros((n, len(tfs)), dtype=np.int8)
    close_base = _safe(npz, cl_field, n)
    for tf_idx, tf in enumerate(tfs):
        cnt = np.zeros(n, dtype=np.int8)
        wt1 = _safe(npz, f'wt1_{tf}', n)
        wt2 = _safe(npz, f'wt2_{tf}', n)
        if not (np.all(wt1 == 0) and np.all(wt2 == 0)):
            cnt += (wt1 > wt2 if is_long else wt1 < wt2).astype(np.int8)
        rsi = _safe(npz, f'rsi_{tf}', n)
        valid = rsi >= 0
        if valid.any():
            cnt += np.where(valid, (rsi > 50 if is_long else rsi < 50), False).astype(np.int8)
        mfi = _safe(npz, f'mfi_{tf}', n)
        valid = mfi >= 0
        if valid.any():
            cnt += np.where(valid, (mfi > 50 if is_long else mfi < 50), False).astype(np.int8)
        dc_pos = _safe(npz, f'dc_position_{tf}', n)
        need_calc = dc_pos < 0
        if need_calc.any():
            dc_h = _safe(npz, f'dc_high_{tf}', n)
            dc_l = _safe(npz, f'dc_low_{tf}', n)
            denom = np.maximum(dc_h - dc_l, 1e-9)
            calc_ok = (dc_h > dc_l) & (dc_l > 0) & (close_base > 0)
            dc_pos = np.where(need_calc & calc_ok, (close_base - dc_l) / denom, dc_pos)
        valid = dc_pos >= 0
        if valid.any():
            cnt += np.where(valid, (dc_pos < 0.65 if is_long else dc_pos > 0.35), False).astype(np.int8)
        bb_pctb = _safe(npz, f'bb_pct_b_{tf}', n)
        need_calc = bb_pctb < 0
        if need_calc.any():
            bb_u = _safe(npz, f'bb_upper_{tf}', n)
            bb_l = _safe(npz, f'bb_lower_{tf}', n)
            denom = np.maximum(bb_u - bb_l, 1e-9)
            calc_ok = (bb_u > bb_l) & (bb_l > 0) & (close_base > 0)
            bb_pctb = np.where(need_calc & calc_ok, (close_base - bb_l) / denom, bb_pctb)
        valid = bb_pctb >= 0
        if valid.any():
            cnt += np.where(valid, (bb_pctb < 0.75 if is_long else bb_pctb > 0.25), False).astype(np.int8)
        rvol = _safe(npz, f'relative_volume_{tf}', n)
        valid = rvol >= 0
        if valid.any():
            cnt += np.where(valid, rvol > 1.0, False).astype(np.int8)
        stk = _safe(npz, f'stoch_k_{tf}', n)
        valid = stk >= 0
        if valid.any():
            cnt += np.where(valid, (stk < 80 if is_long else stk > 20), False).astype(np.int8)
        ind_matrix[:, tf_idx] = cnt
    return (ind_matrix >= min_ind).sum(axis=1) >= min_tfs


def compute_entry_signals(npz, n, is_long, cfg, sym: Optional[str] = None):
    _ltf = getattr(cfg, 'LTF', '3m')
    _ltf_mins = 3 if _ltf == '3m' else 5
    _bph_1h = 60 // _ltf_mins    # bars per 1h: 20 (crypto/3m) or 12 (tradier/5m)
    close = _close_with_mode_check(npz, n, cfg, 'compute_entry_signals')
    k_ltf = _safe(npz, f'stoch_k_{_ltf}', n, 50)
    k_15m = _safe(npz, 'stoch_k_15m', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50)
    d_ltf = _safe(npz, f'stoch_d_{_ltf}', n, 50)
    wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)
    wt_vel_1h = _safe(npz, 'wt_velocity_1h', n)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50)
    mfi_D = _safe(npz, 'mfi_D', n, 50)
    ha_D = _ha_int(npz, 'ha_D', n); ha_1h = _ha_int(npz, 'ha_1h', n)
    dc_high_4h = _safe(npz, 'dc_high_4h', n); dc_low_4h = _safe(npz, 'dc_low_4h', n)

    # Gate filters — K3M_FLOOR name retained for config compat; gate applies to LTF stoch K.
    kltf_ok = (k_ltf < (100 - cfg.K3M_FLOOR)) if is_long else (k_ltf > cfg.K3M_FLOOR)
    ct_vel_ok = np.ones(n, dtype=bool)
    if cfg.CT_WT_VELOCITY_GATE_ENABLED:
        m = cfg.CT_WT_VELOCITY_1H_MIN
        ct_vel_ok = (wt_vel_1h >= m) if is_long else (wt_vel_1h <= -m)
    ct_dc_ok = np.ones(n, dtype=bool)
    if cfg.CT_DC_CROSSOVER_SKIP_ENABLED and not is_long:
        dc_co_15m = _safeb(npz, 'dc_basis_crossover_15m', n)
        dc_co_1h = _safeb(npz, 'dc_basis_crossover_1h', n)
        ct_dc_ok = ~(dc_co_15m | dc_co_1h)
    htf_ok = np.ones(n, dtype=bool)
    if cfg.HTF_ALIGNMENT_ENABLED:
        if is_long:
            htf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            htf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        htf_ok = htf_cnt >= cfg.HTF_MIN_ALIGNED
        if cfg.D_TREND_REQUIRED:
            d_aligned = (ha_D == 1) if is_long else (ha_D == -1)
            d_neutral = (ha_D == 0)
            htf_ok = htf_ok & (d_aligned | d_neutral)

    # Get all enabled reentry blocks.
    # 2026-04-30 Phase 2 vectorization: USE_LIVE_EVALUATOR_VEC swaps the v8_quick-
    # internal lookahead-corrected blocks for position_evaluator.evaluate_reentry_vec
    # which is byte-equivalent to ez_manage.evaluate_reentry. Default False to preserve
    # existing sweep numbers; sweep this flag to measure live-vs-backtest parity gap.
    if getattr(cfg, 'USE_LIVE_EVALUATOR_VEC', False):
        from position_evaluator import evaluate_reentry_vec, BLOCK_NAMES
        _ltf = getattr(cfg, 'LTF', '3m')
        _vec = evaluate_reentry_vec(npz, is_long=is_long, config=cfg, ltf=_ltf)
        # Decompose by block_id back into a {name: mask} dict so downstream
        # confluence / per-block-stat logic continues to work.
        blocks = {}
        for bid, name in BLOCK_NAMES.items():
            mask = (_vec['block_id'] == bid) & _vec['fire']
            if mask.any():
                blocks[name.split('_', 1)[0]] = mask  # key = "B15", "B04", etc.
    else:
        blocks = compute_reentry_blocks(npz, n, is_long, cfg)
    if not blocks:
        return np.zeros(n, dtype=bool)

    # Tradier extras
    mfi_gate = np.ones(n, dtype=bool)
    if cfg.MFI_ENTRY_ENABLED:
        if is_long: mfi_gate = mfi_1h < cfg.MFI_ENTRY_LONG_MAX
        else: mfi_gate = mfi_1h > cfg.MFI_ENTRY_SHORT_MIN
    vwap_ok = np.ones(n, dtype=bool)
    if cfg.VWAP_FILTER_ENABLED:
        vwap = _safe(npz, 'vwap_D', n)
        if vwap.sum() > 0:
            vwap_ok = (close > vwap) if is_long else (close < vwap)

    # CONFLUENCE MODE: require N blocks to agree simultaneously
    if cfg.CONFLUENCE_MODE_ENABLED:
        stacked = np.stack(list(blocks.values()), axis=0)
        agree_count = stacked.sum(axis=0)
        raw = agree_count >= cfg.CONFLUENCE_MIN_BLOCKS
    else:
        # OR mode: any block fires
        raw = np.zeros(n, dtype=bool)
        for b in blocks.values():
            raw = raw | b

    # STRENGTH FILTER: REVERTED to V8Q v3 proven weights (Sharpe 1.93 on TOP3).
    # Pullback-first weighting (tested above) only hit Sharpe 0.80 — reverted 2026-04-16.
    # PULL blocks remain as opt-in sweep knobs with LOW weight (don't pollute proven score).
    if cfg.STRENGTH_FILTER_ENABLED:
        weights = {
            # ORIGINAL V8Q v3 weights — proven Sharpe 1.93 on TOP3
            "B15": 4,       # Strong trend continuation (DC breakout above prev 4h band)
            "B04": 3,       # DC retest (Sharpe 0.39)
            "B11": 3,       # DC break above prev 1h band (Sharpe 0.34, 94% WR)
            "B02": 2,       # BC156 bottom bounce (Sharpe 0.31)
            "B10": 1, "B12": 1, "B14": 1,
            # 2026-04-19 FIX: B_PRICE_CROSS_K90 and B_WT15M_CROSS are primary live reentry
            # triggers but had weight=1 (default). With B15/B11 dead (0 bars due to NPZ DC bug),
            # max achievable score was 5 on near-zero bars → backtest traded 1000x less than live.
            # Weight=4 matches B15 (same importance: price above DC structure = mandatory reentry).
            "B_PRICE_CROSS_K90": 4,  # live mandatory: price crossed exit level proxy
            "B_WT15M_CROSS": 4,      # live primary reentry: 15m WT cross aligned with HTF
            # Pullback blocks (2026-04-16 experiment — tested, kept at low weight)
            "B_PULL1": 1, "B_PULL2": 1, "B_PULL3": 1, "B_PULL4": 1,
            # Tradier alpha blocks (2026-04-16) — weights match historical Sharpe
            "B_KZONE": 4,       # live Sharpe 4.23
            "B_FH_MOM": 3,      # live Sharpe 1.54
            "B_MFI_D_OVERSOLD": 2,
        }
        score = np.zeros(n, dtype=np.float32)
        for name, arr in blocks.items():
            score = score + arr.astype(np.float32) * weights.get(name, 1)
        # SQUEEZE_FIRE_SCORE_BOOST (2026-04-26): additive bonus to entry score when squeeze fires
        # AND WT is bullish (LONG) / bearish (SHORT) on the same TF. NPZ fields:
        # squeeze_fire_{tf} (int8 +1=long fire / -1=short fire / 0), wt_bullish_{tf} (bool).
        if bool(getattr(cfg, 'SQUEEZE_FIRE_SCORE_BOOST_ENABLED', False)):
            _sb_tf = str(getattr(cfg, 'SQUEEZE_FIRE_SCORE_BOOST_TF', '5m'))
            _sb_bonus = float(getattr(cfg, 'SQUEEZE_FIRE_BONUS_SCORE', 15.0))
            _sb_fire = _safe(npz, f'squeeze_fire_{_sb_tf}', n, 0).astype(np.int8)
            _sb_bull = _safeb(npz, f'wt_bullish_{_sb_tf}', n)
            if is_long:
                _sb_hit = (_sb_fire == 1) & _sb_bull
            else:
                _sb_hit = (_sb_fire == -1) & (~_sb_bull)
            score = score + _sb_hit.astype(np.float32) * _sb_bonus
        # MI_ENTRY score bonuses (2026-04-28): HL structure bonus + opposing divergence bonus.
        # Proxy for live MI signals using NPZ-available fields.
        if bool(getattr(cfg, 'MI_ENTRY_ENABLED', False)):
            _mi_struct_pts = float(getattr(cfg, 'MI_ENTRY_STRUCT_BONUS', 10))
            _mi_exhaust_pts = float(getattr(cfg, 'MI_ENTRY_EXHAUST_BONUS', 8))
            _low_1h = _safe(npz, 'low_1h', n, 0.0)
            _low_1h_prev = _safe(npz, 'low_1h_prev', n, 0.0)
            _high_1h = _safe(npz, 'high_1h', n, 0.0)
            _high_1h_prev = _safe(npz, 'high_1h_prev', n, 0.0)
            _bull_div = _safeb(npz, 'wt_any_bull_div', n)
            _bear_div = _safeb(npz, 'wt_any_bear_div', n)
            if is_long:
                _mi_struct = (_low_1h > 0) & (_low_1h_prev > 0) & (_low_1h > _low_1h_prev)
                _mi_exhaust = _bear_div
            else:
                _mi_struct = (_high_1h > 0) & (_high_1h_prev > 0) & (_high_1h < _high_1h_prev)
                _mi_exhaust = _bull_div
            score = score + _mi_struct.astype(np.float32) * _mi_struct_pts
            score = score + _mi_exhaust.astype(np.float32) * _mi_exhaust_pts
        # HLR_RALLY score bonus (2026-04-28): higher low on 1h/4h near SMA200 = trending pullback.
        # Score bonus per TF; near-SMA = full pts, off-SMA = HLR_OFF_SMA_PTS_FRAC * pts.
        if bool(getattr(cfg, 'HLR_RALLY_ENABLED', True)):
            _hlr_pts_1h = float(getattr(cfg, 'HLR_PTS_1H', 40))
            _hlr_pts_4h = float(getattr(cfg, 'HLR_PTS_4H', 60))
            _hlr_sma_band = float(getattr(cfg, 'HLR_SMA_BAND_PCT', 0.03))
            _hlr_off_frac = float(getattr(cfg, 'HLR_OFF_SMA_PTS_FRAC', 0.5))
            _sma_1h = _safe(npz, 'sma_200_1h', n, 0.0)
            _sma_4h = _safe(npz, 'sma_200_4h', n, 0.0)
            _l1h_hlr = _safe(npz, 'low_1h', n, 0.0)
            _l1hp_hlr = _safe(npz, 'low_1h_prev', n, 0.0)
            _l4h_hlr = _safe(npz, 'low_4h', n, 0.0)
            _l4hp_hlr = _safe(npz, 'low_4h_prev', n, 0.0)
            _h1h_hlr = _safe(npz, 'high_1h', n, 0.0)
            _h1hp_hlr = _safe(npz, 'high_1h_prev', n, 0.0)
            _h4h_hlr = _safe(npz, 'high_4h', n, 0.0)
            _h4hp_hlr = _safe(npz, 'high_4h_prev', n, 0.0)
            close_hlr = _safe(npz, 'close_15m', n, 0.0)
            if is_long:
                _hl_1h = (_l1h_hlr > 0) & (_l1hp_hlr > 0) & (_l1h_hlr > _l1hp_hlr)
                _hl_4h = (_l4h_hlr > 0) & (_l4hp_hlr > 0) & (_l4h_hlr > _l4hp_hlr)
                _near_1h = (_sma_1h > 0) & (np.abs(close_hlr - _sma_1h) / np.where(_sma_1h > 0, _sma_1h, 1) <= _hlr_sma_band)
                _near_4h = (_sma_4h > 0) & (np.abs(close_hlr - _sma_4h) / np.where(_sma_4h > 0, _sma_4h, 1) <= _hlr_sma_band)
            else:
                _hl_1h = (_h1h_hlr > 0) & (_h1hp_hlr > 0) & (_h1h_hlr < _h1hp_hlr)
                _hl_4h = (_h4h_hlr > 0) & (_h4hp_hlr > 0) & (_h4h_hlr < _h4hp_hlr)
                _near_1h = (_sma_1h > 0) & (np.abs(close_hlr - _sma_1h) / np.where(_sma_1h > 0, _sma_1h, 1) <= _hlr_sma_band)
                _near_4h = (_sma_4h > 0) & (np.abs(close_hlr - _sma_4h) / np.where(_sma_4h > 0, _sma_4h, 1) <= _hlr_sma_band)
            _pts_1h = np.where(_near_1h, _hlr_pts_1h, _hlr_pts_1h * _hlr_off_frac)
            _pts_4h = np.where(_near_4h, _hlr_pts_4h, _hlr_pts_4h * _hlr_off_frac)
            score = score + (_hl_1h.astype(np.float32) * _pts_1h.astype(np.float32))
            score = score + (_hl_4h.astype(np.float32) * _pts_4h.astype(np.float32))
        # 2026-04-30 Job 1 (i): per-sector multiplier on STRENGTH_MIN_SCORE.
        # _sector_strength_mult returns 1.0 unless cfg.SECTOR_ENTRY_STRENGTH_MULT_TRADIER
        # is set AND mode==tradier AND sym maps to a configured sector.
        _sect_mult = _sector_strength_mult(sym, cfg)
        raw = raw & (score >= cfg.STRENGTH_MIN_SCORE * _sect_mult)
        # ENTRY_SCORE_THRESHOLD — second score floor swept independently.
        # DELTA_ENTRY_ENABLED proxy: live delta_tracker fires on velocity zone transitions,
        # bypassing the scorer. Proxy: velocity-strong bars skip ENTRY_SCORE_THRESHOLD (not STRENGTH_MIN_SCORE).
        _est = float(getattr(cfg, 'ENTRY_SCORE_THRESHOLD', 0.0) or 0.0)
        if _est > 0:
            _delta_en = bool(getattr(cfg, 'DELTA_ENTRY_ENABLED', True))
            if _delta_en and cfg.CT_WT_VELOCITY_GATE_ENABLED:
                _vel_min = float(getattr(cfg, 'CT_WT_VELOCITY_1H_MIN', 0.0))
                _vel_bypass = (wt_vel_1h >= _vel_min) if is_long else (wt_vel_1h <= -_vel_min)
                raw = raw & ((score >= _est) | _vel_bypass)
            else:
                raw = raw & (score >= _est)

    # ===== Auto-hooked entry gates (Group B switches) =====
    # Each adds a simple filter; when flipped, impacts entry signal density.
    extra_ok = np.ones(n, dtype=bool)
    # Tradier MFI entry long gate — tradier mode only (crypto regression 2026-04-16)
    if getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_ENABLED', False) and is_long and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        mfi_1h_arr = _safe(npz, 'mfi_1h', n, 50)
        extra_ok = extra_ok & (mfi_1h_arr < getattr(cfg, 'TRADIER_MFI_ENTRY_LONG_TRADIER', 60.0))
    # RSI entry gate
    if getattr(cfg, 'RSI_ENTRY_GATE_ENABLED', False):
        rsi_1h = _safe(npz, 'rsi_1h', n, 50)
        if is_long:
            extra_ok = extra_ok & (rsi_1h < getattr(cfg, 'RSI_ENTRY_MAX_LONG', 37.0))
        else:
            extra_ok = extra_ok & (rsi_1h > getattr(cfg, 'RSI_ENTRY_MIN_SHORT', 63.0))
    # Stoch cross entry tradier — LTF-parameterized
    if getattr(cfg, 'STOCH_CROSS_ENTRY_TRADIER', False):
        k_ltf_arr = _safe(npz, f'stoch_k_{_ltf}', n, 50)
        d_ltf_arr = _safe(npz, f'stoch_d_{_ltf}', n, 50)
        k_ltf_arr_prev = np.roll(k_ltf_arr, 1); k_ltf_arr_prev[0] = k_ltf_arr[0]
        if is_long:
            extra_ok = extra_ok & ((k_ltf_arr_prev <= d_ltf_arr) & (k_ltf_arr > d_ltf_arr))
        else:
            extra_ok = extra_ok & ((k_ltf_arr_prev >= d_ltf_arr) & (k_ltf_arr < d_ltf_arr))
    # TF alignment min total (tradier-only; regression if applied to crypto per 2026-04-16 test)
    if getattr(cfg, 'BACKTEST_VALIDATED_GATES_TRADIER', False) and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        wt1_4h_arr = _safe(npz, 'wt1_4h', n); wt2_4h_arr = _safe(npz, 'wt2_4h', n)
        wt1_D_arr = _safe(npz, 'wt1_D', n); wt2_D_arr = _safe(npz, 'wt2_D', n)
        if is_long:
            tf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h_arr > wt2_4h_arr).astype(int) + (wt1_D_arr > wt2_D_arr).astype(int)
        else:
            tf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h_arr < wt2_4h_arr).astype(int) + (wt1_D_arr < wt2_D_arr).astype(int)
        tf_gate_total = getattr(cfg, 'TF_ALIGNMENT_MIN_TOTAL', 0)
        tf_need = max(1, min(3, int(tf_gate_total // 4))) if tf_gate_total > 0 else 1
        extra_ok = extra_ok & (tf_cnt >= tf_need)
    # Volume confirmation
    if getattr(cfg, 'VOLUME_CONFIRMATION_ENABLED', False):
        vol_1h = _safe(npz, 'volume_1h', n, 0)
        vol_ma = _safe(npz, 'volume_sma_1h', n, 0)
        if vol_ma.sum() > 0:
            extra_ok = extra_ok & (vol_1h > vol_ma * getattr(cfg, 'VOLUME_CONFIRMATION_MULT', 1.2))
    # Ablation: disable quick entry path
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_ENTRY', False):
        return np.zeros(n, dtype=bool)
    # Ablation: disable hedge/reentry second path (B_PULL*, B09 snapback)
    if getattr(cfg, 'ABLATION_DISABLE_REENTRY', False):
        # Block reentry-like blocks, keep raw trend-follow only — for sensitivity test
        pass
    # Tradier entry score MFI_D filter — ONLY tradier mode
    entry_score_min = getattr(cfg, 'TRADIER_ENTRY_SCORE_THRESHOLD', 0)
    if entry_score_min >= 24 and getattr(cfg, 'MODE', 'crypto') == 'tradier':
        mfi_D_arr = _safe(npz, 'mfi_D', n, 50)
        if is_long: extra_ok = extra_ok & (mfi_D_arr >= 40)
        else: extra_ok = extra_ok & (mfi_D_arr <= 60)
    # ENTRY_ZONE gate — LONG requires k_TF < ENTRY_ZONE_LONG ceiling (oversold); SHORT requires > ENTRY_ZONE_SHORT floor (overbought).
    # Disabled defaults (LONG=0 / SHORT=100) trivially pass. When set (e.g. LONG=35, SHORT=65) they become real gates.
    _zone_tf = str(getattr(cfg, 'ENTRY_ZONE_K_TF', _ltf) or _ltf)
    if _zone_tf == '15m':
        _zone_k_arr = k_15m
    elif _zone_tf == '1h':
        _zone_k_arr = k_1h
    else:
        _zone_k_arr = k_ltf
    if is_long:
        _zl = float(getattr(cfg, 'ENTRY_ZONE_LONG', 0.0) or 0.0)
        if _zl > 0.0:
            extra_ok = extra_ok & (_zone_k_arr < _zl)
    else:
        _zs = float(getattr(cfg, 'ENTRY_ZONE_SHORT', 100.0) or 100.0)
        if _zs < 100.0:
            extra_ok = extra_ok & (_zone_k_arr > _zs)
    # K1H_RISING_GATE — LONG: k_1h < threshold AND higher than 12 bars ago (1 full 1h period back on 5m data).
    # Roll by 12 not 1: k_1h only changes once per 12 bars on 5m data; roll(1) gives same value 11/12 times.
    # SHORT: k_1h > (100-threshold) AND lower than 12 bars ago.
    if bool(getattr(cfg, 'K1H_RISING_GATE_ENABLED', False)):
        _k1h_max = float(getattr(cfg, 'K1H_RISING_LONG_MAX', 60.0))
        _k1h_prev_1h = np.roll(k_1h, _bph_1h); _k1h_prev_1h[:_bph_1h] = k_1h[:_bph_1h]
        if is_long:
            extra_ok = extra_ok & (k_1h < _k1h_max) & (k_1h > _k1h_prev_1h)
        else:
            extra_ok = extra_ok & (k_1h > (100.0 - _k1h_max)) & (k_1h < _k1h_prev_1h)
    # REENTRY_RALLY_K15M_MAX — cap on k_15m for entries during rally. 100=disabled.
    _rally_cap = float(getattr(cfg, 'REENTRY_RALLY_K15M_MAX', 100.0) or 100.0)
    if _rally_cap < 100.0:
        if is_long:
            extra_ok = extra_ok & (k_15m < _rally_cap)
        else:
            extra_ok = extra_ok & (k_15m > (100.0 - _rally_cap))
    # REENTRY_RALLY_HTF_MIN — require N of 3 HTF channels (1h/4h/D) aligned. 1=disabled (any 1 of 3 ok).
    _re_htf_min = int(getattr(cfg, 'REENTRY_RALLY_HTF_MIN', 1))
    if _re_htf_min > 1:
        if is_long:
            _re_htf_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            _re_htf_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        extra_ok = extra_ok & (_re_htf_cnt >= _re_htf_min)
    # ═══ Chapter-E RANK_CONVICTION (vectorized proxy) ═══
    # Live uses 0ranking_points_global (cross-symbol). NPZ has no cross-symbol data, so proxy
    # rank_proxy via HTF agreement count (0-3). Side-agreement ≥ RANK_CONVICTION_MIN to allow.
    if bool(getattr(cfg, 'RANK_CONVICTION_ENABLED', False)):
        _rc_min = int(getattr(cfg, 'RANK_CONVICTION_MIN', 2))
        if is_long:
            _rc_cnt = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            _rc_cnt = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        extra_ok = extra_ok & (_rc_cnt >= _rc_min)
    # ═══ Chapter-E DC_MOMENT (vectorized proxy) ═══
    # Live uses 0dc_moment (precomputed composite). Proxy via sum of dc_position on 1h/4h/D.
    # Blocks entry when dc_position opposes side by margin.
    if bool(getattr(cfg, 'DC_MOMENT_ENABLED', False)):
        _dcp_1h = _safe(npz, 'dc_position_1h', n, 0.5)
        _dcp_4h = _safe(npz, 'dc_position_4h', n, 0.5)
        _dcp_D = _safe(npz, 'dc_position_D', n, 0.5)
        _dcm_proxy = (_dcp_1h + _dcp_4h + _dcp_D) / 3.0 * 100.0  # 0-100
        _dcm_thr = float(getattr(cfg, 'DC_MOMENT_OPPOSE_THRESHOLD', 40.0))
        if is_long:
            extra_ok = extra_ok & (_dcm_proxy >= (50.0 - _dcm_thr))
        else:
            extra_ok = extra_ok & (_dcm_proxy <= (50.0 + _dcm_thr))
    # R-G2: MTF WT velocity alignment gate (2026-04-18 indicator audit)
    mtf_vel_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'WT_MTF_VEL_GATE_ENABLED', False)):
        _vel_min_tfs = int(getattr(cfg, 'WT_MTF_VEL_MIN', 3))
        if is_long:
            _vel_cnt = _safe(npz, 'wt_velocity_up_count', n, 0).astype(np.int8)
            mtf_vel_ok = _vel_cnt >= _vel_min_tfs
        else:
            _vel_cnt = _safe(npz, 'wt_velocity_down_count', n, 0).astype(np.int8)
            mtf_vel_ok = _vel_cnt >= _vel_min_tfs
    # RE-1: cross freshness gate (2026-04-18 indicator audit)
    cross_fresh_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'REENTRY_CROSS_FRESHNESS_ENABLED', False)):
        _max_bars = int(getattr(cfg, 'REENTRY_CROSS_MAX_BARS_AGO', 5))
        _b3 = _safe(npz, 'wt_cross_bars_ago_3m', n, 999.0)
        _b15 = _safe(npz, 'wt_cross_bars_ago_15m', n, 999.0)
        _b1h = _safe(npz, 'wt_cross_bars_ago_1h', n, 999.0)
        cross_fresh_ok = (_b3 < _max_bars) | (_b15 < _max_bars) | (_b1h < _max_bars)
    le_ok = np.ones(n, dtype=bool)
    if bool(getattr(cfg, 'LOCAL_EXTREMES_SCORER_ENABLED', False)):
        _le_score = _compute_le_score_arr(npz, n, is_long, cfg)
        _le_min = float(getattr(cfg, 'LOCAL_EXTREMES_MIN_SCORE', 30.0))
        le_ok = _le_score >= _le_min
    # === 2026-04-26 NEW LONG-SIDE ENTRY VETO GATES — consume new NPZ fields ===
    # MINERVINI_GATE: require sepa_score >= MINERVINI_MIN_SCORE on long entries.
    minervini_ok = np.ones(n, dtype=bool)
    if is_long and bool(getattr(cfg, 'MINERVINI_GATE_ENABLED', False)):
        _sepa_score = _safe(npz, 'sepa_score', n, 0).astype(np.int8)
        _sepa_min = int(getattr(cfg, 'MINERVINI_MIN_SCORE', 5))
        minervini_ok = _sepa_score >= _sepa_min
    # CLENOW_GATE: require clenow_score >= CLENOW_GATE_MIN_SCORE on long entries.
    clenow_ok = np.ones(n, dtype=bool)
    if is_long and bool(getattr(cfg, 'CLENOW_GATE_ENABLED', False)):
        _clenow = _safe(npz, 'clenow_score', n, 0.0)
        _clenow_min = float(getattr(cfg, 'CLENOW_GATE_MIN_SCORE', 30.0))
        clenow_ok = _clenow >= _clenow_min
    # PROXIMITY_TOP_GATE: avoid topping. pct_from_52w_high is negative, so within X% of high
    # means -X <= pct_from_52w_high <= 0. Veto when pct_from_52w_high > -PROXIMITY_TOP_MAX_DROP_PCT.
    proximity_top_ok = np.ones(n, dtype=bool)
    if is_long and bool(getattr(cfg, 'PROXIMITY_TOP_GATE_ENABLED', False)):
        _pct_high = _safe(npz, 'pct_from_52w_high', n, 0.0)
        _max_drop = float(getattr(cfg, 'PROXIMITY_TOP_MAX_DROP_PCT', 5.0))
        proximity_top_ok = _pct_high <= -_max_drop
    base_sig = raw & kltf_ok & ct_vel_ok & ct_dc_ok & htf_ok & mfi_gate & vwap_ok & extra_ok & mtf_vel_ok & cross_fresh_ok & le_ok & minervini_ok & clenow_ok & proximity_top_ok
    if getattr(cfg, 'RATIO_SENTIMENT_FILTER_ENABLED', False):
        mkt_s = _safe(npz, 'market_sentiment_score', n, 50.0)
        base_sig = base_sig & ((mkt_s >= cfg.RATIO_SENTIMENT_LONG_MIN) if is_long else (mkt_s <= cfg.RATIO_SENTIMENT_SHORT_MAX))
    # HIER entry (2026-04-21) — TF-hierarchy: LTF at lower_rz + bullish delta + no HTF overhead resistance.
    _hier_mode_e = str(getattr(cfg, 'HIER_SIGNAL_MODE', 'off'))
    if _hier_mode_e in ('entry', 'both'):
        try:
            from wt_dc_hierarchy import compute_hierarchy_signals
            _hier_entry, _, _ = compute_hierarchy_signals(npz, n, is_long, cfg)
            # OR combine with base_sig so hierarchy ADDS entries rather than replacing
            base_sig = base_sig | _hier_entry
        except Exception as _e:
            print(f"[V8] HIER entry fallback: {_e}", file=__import__('sys').stderr)
    # RZ_CASCADE (2026-04-21) — replaces old shitty RZ_BREAKOUT_ENTRY.
    # Per user: each TF has RZs at dc_high/dc_low; breakout→open, reverse→close; cascade LTF→15m→1h→4h→D(/W/M).
    if getattr(cfg, 'RZ_CASCADE_ENABLED', False):
        rz_entry_sig, _ = compute_rz_cascade_signals(npz, n, is_long, cfg)
        base_sig = base_sig | rz_entry_sig
    # NPZ-FIELD ENTRY SIGNALS (2026-04-22)
    if getattr(cfg, 'ADX_ENTRY_GATE_ENABLED', False):
        _adx_tf = str(getattr(cfg, 'ADX_ENTRY_TF', '1h'))
        _adx_arr = _safe(npz, f'adx_{_adx_tf}', n, 0.0)
        if _adx_arr.max() > 0:
            base_sig = base_sig & (_adx_arr >= float(getattr(cfg, 'ADX_ENTRY_MIN', 20.0)))
    if getattr(cfg, 'MACD_CROSS_ENTRY_ENABLED', False):
        _mc_tf = str(getattr(cfg, 'MACD_CROSS_ENTRY_TF', '1h'))
        _mc_cross = _safe(npz, f'macd_crossover_{_mc_tf}', n, 0.0).astype(bool)
        _mc_crossunder = _safe(npz, f'macd_crossunder_{_mc_tf}', n, 0.0).astype(bool)
        if _mc_cross.any() or _mc_crossunder.any():
            _mc_sig = _mc_cross if is_long else _mc_crossunder
            base_sig = base_sig | _mc_sig
    if getattr(cfg, 'STOCH_CROSS_NPZ_ENTRY_ENABLED', False):
        _sc_tf = str(getattr(cfg, 'STOCH_CROSS_NPZ_ENTRY_TF', '1h'))
        _sc_over = _safe(npz, f'stoch_crossover_{_sc_tf}', n, 0.0).astype(bool)
        _sc_under = _safe(npz, f'stoch_crossunder_{_sc_tf}', n, 0.0).astype(bool)
        if is_long: base_sig = base_sig | _sc_over
        else: base_sig = base_sig | _sc_under
    if getattr(cfg, 'WT_COMPOSITE_BIAS_ENTRY_ENABLED', False):
        _wt_cb = _safe(npz, 'wt_composite_bias', n, 0.0)
        if _wt_cb.any():
            if is_long: base_sig = base_sig & (_wt_cb > 0)
            else: base_sig = base_sig & (_wt_cb < 0)
    if getattr(cfg, 'EMA20_SLOPE_GATE_ENABLED', False):
        _es_tf = str(getattr(cfg, 'EMA20_SLOPE_TF', '1h'))
        _ema20 = _safe(npz, f'ema_20_{_es_tf}', n, 0.0)
        _ema20_prev = _safe(npz, f'ema_20_{_es_tf}_prev', n, 0.0)
        if _ema20.max() > 0 and _ema20_prev.max() > 0:
            if is_long: base_sig = base_sig & (_ema20 > _ema20_prev)
            else: base_sig = base_sig & (_ema20 < _ema20_prev)
    if getattr(cfg, 'WT_ANY_DIV_ENTRY_ENABLED', False):
        _wt_bd = _safeb(npz, 'wt_any_bull_div', n)
        _wt_bd2 = _safeb(npz, 'wt_any_bear_div', n)
        if _wt_bd.any() or _wt_bd2.any():
            if is_long: base_sig = base_sig & _wt_bd
            else: base_sig = base_sig & _wt_bd2
    if getattr(cfg, 'TRADEABLE_PRECOMPUTED_GATE_ENABLED', False):
        _trad_k = 'tradeable_long' if is_long else 'tradeable_short'
        _trad_arr = _safe(npz, _trad_k, n, 1.0).astype(bool)
        if _trad_arr.any():
            base_sig = base_sig & _trad_arr
    if getattr(cfg, 'WT_CROSS_COUNT_ENTRY_ENABLED', False):
        _cc_k = 'wt_bull_cross_count' if is_long else 'wt_bear_cross_count'
        _cc_arr = _safe(npz, _cc_k, n, 0).astype(np.int8)
        _cc_min = int(getattr(cfg, 'WT_CROSS_COUNT_MIN', 1))
        if _cc_arr.any():
            base_sig = base_sig & (_cc_arr >= _cc_min)
    # === Improvement Framework A1-A4 (2026-04-25, REVISED 2026-04-26): squeeze, divergence, funding, OI ===
    # Additive entry signals (SQUEEZE_FIRE, DIVERGENCE) now require minimum HTF alignment to prevent
    # firing entries against the dominant trend. Computed inline so it works even when HTF_ALIGNMENT_ENABLED=False.
    _additive_htf_ok = None
    if getattr(cfg, 'SQUEEZE_FIRE_ENTRY_ENABLED', False) or getattr(cfg, 'DIVERGENCE_ENTRY_ENABLED', False):
        if is_long:
            _ah = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            _ah = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        _additive_htf_ok = _ah >= int(getattr(cfg, 'ADDITIVE_SIGNAL_MIN_HTF', 1))
    # SQUEEZE_FIRE_ENTRY (A3): squeeze release fires entry in matching direction (additive, HTF-gated).
    if getattr(cfg, 'SQUEEZE_FIRE_ENTRY_ENABLED', False):
        _sf_tf = str(getattr(cfg, 'SQUEEZE_FIRE_TF', '1h'))
        _sf_arr = _safe(npz, f'squeeze_fire_{_sf_tf}', n, 0).astype(np.int8)
        if _sf_arr.any():
            _sf_match = (_sf_arr == 1) if is_long else (_sf_arr == -1)
            base_sig = base_sig | (_sf_match & _additive_htf_ok)
    # DIVERGENCE_ENTRY (A4): regular div fires entry in direction (additive, HTF-gated).
    if getattr(cfg, 'DIVERGENCE_ENTRY_ENABLED', False):
        _dv_tf = str(getattr(cfg, 'DIVERGENCE_ENTRY_TF', '1h'))
        _dv_ind = str(getattr(cfg, 'DIVERGENCE_INDICATOR', 'wt'))
        _dv_key = 'div_reg_bull' if is_long else 'div_reg_bear'
        if _dv_ind == 'either':
            _dv_wt = _safeb(npz, f'{_dv_key}_wt_{_dv_tf}', n)
            _dv_mfi = _safeb(npz, f'{_dv_key}_mfi_{_dv_tf}', n)
            _dv_sig = _dv_wt | _dv_mfi
        elif _dv_ind == 'mfi':
            _dv_sig = _safeb(npz, f'{_dv_key}_mfi_{_dv_tf}', n)
        else:
            _dv_sig = _safeb(npz, f'{_dv_key}_wt_{_dv_tf}', n)
        if _dv_sig.any():
            base_sig = base_sig | (_dv_sig & _additive_htf_ok)
    # FUNDING_GATE (A1): veto when funding rate too extreme. Filter — but pass-through on pre-cache bars (rate exactly 0).
    # Symbols listed after 2022-01-01 have funding_rate=0 before listing date.
    if getattr(cfg, 'FUNDING_GATE_ENABLED', False):
        _fr = _safe(npz, f'funding_rate_{_ltf}', n, 0.0)
        if np.abs(_fr).sum() > 0:
            _fr_live = _fr != 0.0
            if is_long:
                _fr_pass = (_fr <= float(getattr(cfg, 'FUNDING_GATE_LONG_MAX', 0.0005))) | (~_fr_live)
            else:
                _fr_pass = (_fr >= float(getattr(cfg, 'FUNDING_GATE_SHORT_MIN', -0.0005))) | (~_fr_live)
            base_sig = base_sig & _fr_pass
    # OI_CONFIRM (A2 — 2026-04-27 4-QUADRANT REWRITE): pair OI direction with PRICE direction.
    #   price↑ + OI↑ = new longs entering   → LONG ok / block SHORT
    #   price↑ + OI↓ = short-cover squeeze  → block LONG / SHORT ok (fade)
    #   price↓ + OI↑ = new shorts entering  → SHORT ok / block LONG
    #   price↓ + OI↓ = long liquidation     → block SHORT / LONG ok (mean-revert)
    # Pass-through on pre-cache bars (oi=0) and small moves below thresholds.
    if getattr(cfg, 'OI_CONFIRM_ENABLED', False):
        _oi_arr = _safe(npz, f'oi_{_ltf}', n, 0.0)
        if _oi_arr.max() > 0:
            _oi_chg = _safe(npz, f'oi_change_1h_{_ltf}', n, 0.0)
            _oi_min = float(getattr(cfg, 'OI_CONFIRM_MIN_CHANGE_PCT', 0.5))
            _oi_px_min = float(getattr(cfg, 'OI_CONFIRM_MIN_PRICE_PCT', 0.3))
            _oi_live = _oi_arr > 0
            _close_1h_prev_oi = _safe(npz, 'close_1h_prev', n, 0.0)
            _px_chg = np.where(_close_1h_prev_oi > 0, (close - _close_1h_prev_oi) / np.maximum(_close_1h_prev_oi, 1e-9) * 100.0, 0.0)
            _both_sig = (np.abs(_oi_chg) >= _oi_min) & (np.abs(_px_chg) >= _oi_px_min) & _oi_live
            _px_up = _px_chg > 0
            _oi_up = _oi_chg > 0
            if is_long:
                _oi_block = _both_sig & ((_px_up & ~_oi_up) | (~_px_up & _oi_up))
            else:
                _oi_block = _both_sig & ((~_px_up & ~_oi_up) | (_px_up & _oi_up))
            base_sig = base_sig & ~_oi_block
    # LH_HL_FILTER (2026-04-27 sweep-testable): block LONG when 1h+4h make lower highs (LH);
    # block SHORT when they make higher lows (HL). REQUIRE_BOTH=True also requires LL/HH (full channel).
    # Modes: STRICT_2BAR (high < high_prev) | DC_REGRESS (high < dc_high * (1-threshold))
    if getattr(cfg, 'LH_HL_FILTER_ENABLED', False):
        try:
            _lh_mode = str(getattr(cfg, 'LH_HL_FILTER_MODE', 'STRICT_2BAR'))
            _lh_tf_req = int(getattr(cfg, 'LH_HL_FILTER_TF_REQ', 2))
            _lh_dc_th = float(getattr(cfg, 'LH_HL_FILTER_DC_THRESHOLD_PCT', 0.5)) / 100.0
            _lh_req_both = bool(getattr(cfg, 'LH_HL_FILTER_REQUIRE_BOTH', False))
            _h1h = _safe(npz, 'high_1h', n, 0.0)
            _h1hp = _safe(npz, 'high_1h_prev', n, 0.0)
            _h4h = _safe(npz, 'high_4h', n, 0.0)
            _h4hp = _safe(npz, 'high_4h_prev', n, 0.0)
            _l1h = _safe(npz, 'low_1h', n, 0.0)
            _l1hp = _safe(npz, 'low_1h_prev', n, 0.0)
            _l4h = _safe(npz, 'low_4h', n, 0.0)
            _l4hp = _safe(npz, 'low_4h_prev', n, 0.0)
            if _lh_mode == "DC_REGRESS":
                _dch1h = _safe(npz, 'dc_high_1h', n, 0.0)
                _dch4h = _safe(npz, 'dc_high_4h', n, 0.0)
                _dcl1h = _safe(npz, 'dc_low_1h', n, 0.0)
                _dcl4h = _safe(npz, 'dc_low_4h', n, 0.0)
                _lh_1h_b = (_h1h > 0) & (_dch1h > 0) & (_h1h < _dch1h * (1.0 - _lh_dc_th))
                _lh_4h_b = (_h4h > 0) & (_dch4h > 0) & (_h4h < _dch4h * (1.0 - _lh_dc_th))
                _hl_1h_b = (_l1h > 0) & (_dcl1h > 0) & (_l1h > _dcl1h * (1.0 + _lh_dc_th))
                _hl_4h_b = (_l4h > 0) & (_dcl4h > 0) & (_l4h > _dcl4h * (1.0 + _lh_dc_th))
            else:
                _lh_1h_b = (_h1h > 0) & (_h1hp > 0) & (_h1h < _h1hp)
                _lh_4h_b = (_h4h > 0) & (_h4hp > 0) & (_h4h < _h4hp)
                _hl_1h_b = (_l1h > 0) & (_l1hp > 0) & (_l1h > _l1hp)
                _hl_4h_b = (_l4h > 0) & (_l4hp > 0) & (_l4h > _l4hp)
            _ll_1h = (_l1h > 0) & (_l1hp > 0) & (_l1h < _l1hp)
            _ll_4h = (_l4h > 0) & (_l4hp > 0) & (_l4h < _l4hp)
            _hh_1h = (_h1h > 0) & (_h1hp > 0) & (_h1h > _h1hp)
            _hh_4h = (_h4h > 0) & (_h4hp > 0) & (_h4h > _h4hp)
            if is_long:
                _lh_count = _lh_1h_b.astype(np.int8) + _lh_4h_b.astype(np.int8)
                _ll_count = _ll_1h.astype(np.int8) + _ll_4h.astype(np.int8)
                _primary = _lh_count >= _lh_tf_req
                _confirm = np.ones(n, dtype=bool) if not _lh_req_both else (_ll_count >= _lh_tf_req)
                _lh_block = _primary & _confirm
            else:
                _hl_count = _hl_1h_b.astype(np.int8) + _hl_4h_b.astype(np.int8)
                _hh_count = _hh_1h.astype(np.int8) + _hh_4h.astype(np.int8)
                _primary = _hl_count >= _lh_tf_req
                _confirm = np.ones(n, dtype=bool) if not _lh_req_both else (_hh_count >= _lh_tf_req)
                _lh_block = _primary & _confirm
            base_sig = base_sig & ~_lh_block
        except Exception:
            pass
    # STDEV_BREAKOUT_ENABLED (2026-04-28): BB %B breakout above 2.5σ (LONG) / below (SHORT) on HTFs.
    # Acts as additive entry OR—fires new entries on statistically extreme breakouts with volume.
    # No cooldown in vectorized engine (approximation; live has 600s cooldown).
    if bool(getattr(cfg, 'STDEV_BREAKOUT_ENABLED', False)):
        try:
            _sb_pctb_long = float(getattr(cfg, 'STDEV_BREAKOUT_PCTB_LONG', 1.125))
            _sb_pctb_short = float(getattr(cfg, 'STDEV_BREAKOUT_PCTB_SHORT', -0.125))
            _sb_rvol_min = float(getattr(cfg, 'STDEV_BREAKOUT_RVOL_MIN', 1.2))
            _sb_htf_list = list(getattr(cfg, 'STDEV_BREAKOUT_HTF_LIST', None) or ['D', '4h'])
            _stdev_sig = np.zeros(n, dtype=bool)
            for _htf in _sb_htf_list:
                _pctb = _safe(npz, f'bb_pct_b_{_htf}', n, 0.5)
                _rvol = _safe(npz, f'relative_volume_{_htf}', n, 1.0)
                _rvol_ok = _rvol >= _sb_rvol_min
                if is_long:
                    _stdev_sig = _stdev_sig | ((_pctb > _sb_pctb_long) & _rvol_ok)
                else:
                    _stdev_sig = _stdev_sig | ((_pctb < _sb_pctb_short) & _rvol_ok)
            if getattr(cfg, 'DELTA_GATE_STDEV_BREAKOUT', True):
                _delta_ok = wt_vel_1h > 0 if is_long else wt_vel_1h < 0
                _stdev_sig = _stdev_sig & _delta_ok
            base_sig = base_sig | _stdev_sig
        except Exception:
            pass
    # STDEV_BOUNCE_ENABLED: mean-reversion entry at lower band (LONG: pctb ≤ threshold = at/below lower 2σ band).
    # Opposite of STDEV_BREAKOUT — buys the band touch, not the band break.
    # No delta gate by default (velocity is often negative at lower band).
    if bool(getattr(cfg, 'STDEV_BOUNCE_ENABLED', False)):
        try:
            _bn_pctb_long = float(getattr(cfg, 'STDEV_BOUNCE_PCTB_LONG', 0.05))
            _bn_pctb_short = float(getattr(cfg, 'STDEV_BOUNCE_PCTB_SHORT', 0.95))
            _bn_rvol_min = float(getattr(cfg, 'STDEV_BOUNCE_RVOL_MIN', 1.2))
            _bn_htf_list = list(getattr(cfg, 'STDEV_BOUNCE_HTF_LIST', None) or ['D', '4h'])
            _bounce_sig = np.zeros(n, dtype=bool)
            for _htf in _bn_htf_list:
                _pctb = _safe(npz, f'bb_pct_b_{_htf}', n, 0.5)
                _rvol = _safe(npz, f'relative_volume_{_htf}', n, 1.0)
                _rvol_ok = _rvol >= _bn_rvol_min
                if is_long:
                    _bounce_sig = _bounce_sig | ((_pctb <= _bn_pctb_long) & _rvol_ok)
                else:
                    _bounce_sig = _bounce_sig | ((_pctb >= _bn_pctb_short) & _rvol_ok)
            base_sig = base_sig | _bounce_sig
        except Exception:
            pass
    # CLENOW_ENABLED (2026-04-28): Clenow momentum — 90-day log-regression slope×R² on daily close.
    # GATE_ONLY=True: require positive score for LONG / negative for SHORT (gate existing base_sig).
    # GATE_ONLY=False: also fire new entries when score exceeds SCORE_MIN threshold (additive path).
    if bool(getattr(cfg, 'CLENOW_ENABLED', False)):
        try:
            _cl_score = _safe(npz, 'clenow_score_D', n, 0.0)
            _cl_score_min = float(getattr(cfg, 'CLENOW_SCORE_MIN', 30.0))
            _cl_score_short = float(getattr(cfg, 'CLENOW_SCORE_SHORT_MAX', -10.0))
            _cl_gate_only = bool(getattr(cfg, 'CLENOW_GATE_ONLY', True))
            _cl_has_data = _cl_score != 0
            if is_long:
                _cl_ok = ~_cl_has_data | (_cl_score >= _cl_score_min)
                if _cl_gate_only:
                    base_sig = base_sig & _cl_ok
                else:
                    base_sig = base_sig | ((_cl_score >= _cl_score_min) & _cl_has_data)
            else:
                _cl_ok = ~_cl_has_data | (_cl_score <= _cl_score_short)
                if _cl_gate_only:
                    base_sig = base_sig & _cl_ok
                else:
                    base_sig = base_sig | ((_cl_score <= _cl_score_short) & _cl_has_data)
        except Exception:
            pass
    # CONNORS_RSI_ENABLED (2026-04-28): oversold/overbought entry from 90-day daily ConnorsRSI.
    # GATE_ONLY=False (default): open new LONG when crsi_D < 15 (oversold), SHORT when crsi_D > 85.
    # GATE_ONLY=True: gate existing base_sig — require crsi not overbought (LONG) / not oversold (SHORT).
    if bool(getattr(cfg, 'CONNORS_RSI_ENABLED', False)):
        try:
            _crsi = _safe(npz, 'connors_rsi_D', n, 50.0)
            _crsi_long_max = float(getattr(cfg, 'CONNORS_RSI_ENTRY_LONG_MAX', 15.0))
            _crsi_short_min = float(getattr(cfg, 'CONNORS_RSI_ENTRY_SHORT_MIN', 85.0))
            _crsi_gate = bool(getattr(cfg, 'CONNORS_RSI_GATE_ONLY', False))
            _crsi_has_data = _crsi != 50.0
            if is_long:
                if _crsi_gate:
                    base_sig = base_sig & (~_crsi_has_data | (_crsi < _crsi_short_min))
                else:
                    base_sig = base_sig | ((_crsi <= _crsi_long_max) & _crsi_has_data)
            else:
                if _crsi_gate:
                    base_sig = base_sig & (~_crsi_has_data | (_crsi > _crsi_long_max))
                else:
                    base_sig = base_sig | ((_crsi >= _crsi_short_min) & _crsi_has_data)
        except Exception:
            pass
    # LEGACY RZ_BREAKOUT_ENTRY (kept for sweep-compat, default OFF). Do not enable alongside RZ_CASCADE.
    elif getattr(cfg, 'RZ_BREAKOUT_ENTRY_ENABLED', False):
        _rz_top_e = float(getattr(cfg, 'RZ_TOP_BB_THRESHOLD', 0.85))
        _rz_bot_e = float(getattr(cfg, 'RZ_BOT_BB_THRESHOLD', 0.15))
        _bb_pb = _safe(npz, 'bb_pct_b_1h', n, 0.5)
        _bb_pb_prev = np.roll(_bb_pb, 1); _bb_pb_prev[0] = _bb_pb[0]
        if is_long:
            rz_break_sig = (_bb_pb_prev <= _rz_bot_e) & (_bb_pb > _rz_bot_e)
        else:
            rz_break_sig = (_bb_pb_prev >= _rz_top_e) & (_bb_pb < _rz_top_e)
        base_sig = base_sig | rz_break_sig
    # D4: BREAKOUT MULTI-LUNG entry augmentation (default OFF)
    if getattr(cfg, 'BREAKOUT_MULTI_LUNG_ENABLED', False):
        try:
            from breakout_multi_lung import multi_lung_entry_signal
            ml_sig = multi_lung_entry_signal(npz, n, is_long, cfg)
            mode = str(getattr(cfg, 'BREAKOUT_MULTI_LUNG_MODE', 'AUGMENT')).upper()
            if mode == 'REPLACE':
                base_sig = ml_sig
            else:
                base_sig = base_sig | ml_sig
        except Exception:
            pass  # graceful fallback if NPZ missing OHLCV fields
    # ENTRY ENGINES (2026-04-26) — additive fire-on-alignment triggers
    # Each engine: vectorized port of entry_engine_*.py scalar logic.
    # Combined via OR/AND/MAJORITY, then ORed onto base_sig (additive, not replace).
    _ee_min_score = float(getattr(cfg, 'V8_ENTRY_ENGINE_MIN_SCORE', 0.5))
    _ee_sigs = []
    if bool(getattr(cfg, 'V8_ENTRY_ENGINE_WT_ENABLED', False)):
        try:
            _s, _sc = _engine_wt_vec(npz, n, is_long, cfg)
            _ee_sigs.append(_s & (_sc >= _ee_min_score))
        except Exception:
            pass
    if bool(getattr(cfg, 'V8_ENTRY_ENGINE_STOCH_ENABLED', False)):
        try:
            _s, _sc = _engine_stoch_vec(npz, n, is_long, cfg)
            _ee_sigs.append(_s & (_sc >= _ee_min_score))
        except Exception:
            pass
    if bool(getattr(cfg, 'V8_ENTRY_ENGINE_DC_ENABLED', False)):
        try:
            _s, _sc = _engine_dc_vec(npz, n, is_long, cfg)
            _ee_sigs.append(_s & (_sc >= _ee_min_score))
        except Exception:
            pass
    if bool(getattr(cfg, 'V8_ENTRY_ENGINE_HTF_ENABLED', False)):
        try:
            _s, _sc = _engine_htf_vec(npz, n, is_long, cfg)
            _ee_sigs.append(_s & (_sc >= _ee_min_score))
        except Exception:
            pass
    if _ee_sigs:
        _ee_combined = _combine_engine_sigs(_ee_sigs, getattr(cfg, 'V8_ENTRY_ENGINE_COMBINE', 'OR'))
        if _ee_combined is not None:
            base_sig = base_sig | _ee_combined
    # ═══ 2026-04-30 HTF PORT FROM CRYPTO — tradier-only entry paths (default OFF) ═══
    # Tradier-only via mode check; crypto path is unaffected.
    _is_tradier_mode = (str(getattr(cfg, 'MODE', 'crypto')) == 'tradier')
    if _is_tradier_mode:
        # F1. HTF_W_M_ALIGN_GATE — entry GATE: require N of 2 (W, M) WaveTrend on side.
        if bool(getattr(cfg, 'HTF_W_M_ALIGN_GATE_TRADIER_ENABLED', False)):
            try:
                _w1_W = _safe(npz, 'wt1_W', n); _w2_W = _safe(npz, 'wt2_W', n)
                _w1_M = _safe(npz, 'wt1_M', n); _w2_M = _safe(npz, 'wt2_M', n)
                # Pass-through bars without W/M data (early symbol history): only enforce when both have signal.
                _w_has = (_w1_W != 0) | (_w2_W != 0)
                _m_has = (_w1_M != 0) | (_w2_M != 0)
                if is_long:
                    _w_ok = (_w1_W > _w2_W) | (~_w_has)
                    _m_ok = (_w1_M > _w2_M) | (~_m_has)
                else:
                    _w_ok = (_w1_W < _w2_W) | (~_w_has)
                    _m_ok = (_w1_M < _w2_M) | (~_m_has)
                _req = int(getattr(cfg, 'HTF_W_M_ALIGN_TRADIER_REQUIRED', 2))
                _agree = _w_ok.astype(np.int8) + _m_ok.astype(np.int8)
                base_sig = base_sig & (_agree >= _req)
            except Exception:
                pass
        # F2. HTF_DC_BREAKOUT — additive entry path: close > dc_high_TF * (1+thr) (LONG) AND optional W WT on side.
        if bool(getattr(cfg, 'HTF_DC_BREAKOUT_TRADIER_ENABLED', False)):
            try:
                _bk_tf = str(getattr(cfg, 'HTF_DC_BREAKOUT_TRADIER_TF', '4h'))
                _bk_thr = float(getattr(cfg, 'HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT', 0.0)) / 100.0
                _bk_req_w = bool(getattr(cfg, 'HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT', True))
                _dch = _safe(npz, f'dc_high_{_bk_tf}', n, 0.0)
                _dcl = _safe(npz, f'dc_low_{_bk_tf}', n, 0.0)
                # Use prev DC band — current bar's high/low includes current candle (lookahead).
                _dch_prev = np.roll(_dch, 1); _dch_prev[0] = _dch[0]
                _dcl_prev = np.roll(_dcl, 1); _dcl_prev[0] = _dcl[0]
                close_bk = _safe(npz, f'close_{getattr(cfg, "LTF", "5m")}', n, 0.0)
                if close_bk.sum() == 0:
                    close_bk = close
                if is_long:
                    _bk_sig = (_dch_prev > 0) & (close_bk > _dch_prev * (1.0 + _bk_thr))
                else:
                    _bk_sig = (_dcl_prev > 0) & (close_bk < _dcl_prev * (1.0 - _bk_thr))
                if _bk_req_w:
                    _w1_Wbk = _safe(npz, 'wt1_W', n); _w2_Wbk = _safe(npz, 'wt2_W', n)
                    _w_has_bk = (_w1_Wbk != 0) | (_w2_Wbk != 0)
                    if is_long:
                        _w_ok_bk = (_w1_Wbk > _w2_Wbk) | (~_w_has_bk)
                    else:
                        _w_ok_bk = (_w1_Wbk < _w2_Wbk) | (~_w_has_bk)
                    _bk_sig = _bk_sig & _w_ok_bk
                base_sig = base_sig | _bk_sig
            except Exception:
                pass
    # GOLDEN_RULE: gate the entry impulse (SATOSHIT/base_sig) by DC-basis + BB-midline.
    # GATE_MODE=True (default): AND — only enter when impulse fires AND structure confirms.
    # GATE_MODE=False: OR — legacy additive mode (adds breakout entries, hurts Sharpe).
    # mult_arr is returned via _gr_mult_arr in simulate() — wired into _cur_sz_mult.
    if bool(getattr(cfg, 'GOLDEN_RULE_ENABLED', True)):
        try:
            _gr_fire, _ = _golden_rule_vec(npz, n, is_long, cfg)
            if _gr_fire is not None:
                _gr_gate_mode = bool(getattr(cfg, 'GOLDEN_RULE_GATE_MODE', True))
                if _gr_gate_mode:
                    base_sig = base_sig & _gr_fire
                elif _gr_fire.any():
                    base_sig = base_sig | _gr_fire
        except Exception:
            pass
    return base_sig


def compute_exit_signals(npz, n, is_long, cfg):
    _hier_mode = str(getattr(cfg, 'HIER_SIGNAL_MODE', 'off'))
    if _hier_mode in ('exit', 'both'):
        try:
            from wt_dc_hierarchy import compute_hierarchy_signals
            _, _hier_exit, _ = compute_hierarchy_signals(npz, n, is_long, cfg)
            return _hier_exit
        except Exception as _e:
            print(f"[V8] HIER exit fallback: {_e}", file=__import__('sys').stderr)
    if bool(getattr(cfg, 'SIMPLE_WT15M_EXIT_ONLY_ENABLED', False)):
        _w1 = _safe(npz, 'wt1_15m', n)
        _w2 = _safe(npz, 'wt2_15m', n)
        return (_w1 < _w2) if is_long else (_w1 > _w2)
    _ltf = getattr(cfg, 'LTF', '3m')
    _ltf_mins = 3 if _ltf == '3m' else 5
    _bph_1h = 60 // _ltf_mins    # bars per 1h: 20 (crypto/3m) or 12 (tradier/5m)
    close = _close_with_mode_check(npz, n, cfg, 'compute_exit_signals')
    wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
    wt1_15m = _safe(npz, 'wt1_15m', n); wt2_15m = _safe(npz, 'wt2_15m', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    wt_vel_4h = _safe(npz, 'wt_velocity_4h', n)
    wt_vel_ltf = _safe(npz, f'wt_velocity_{_ltf}', n)
    k_ltf = _safe(npz, f'stoch_k_{_ltf}', n, 50); d_ltf = _safe(npz, f'stoch_d_{_ltf}', n, 50)
    k_1h = _safe(npz, 'stoch_k_1h', n, 50); d_1h = _safe(npz, 'stoch_d_1h', n, 50)
    mfi_1h = _safe(npz, 'mfi_1h', n, 50); mfi_ltf = _safe(npz, f'mfi_{_ltf}', n, 50)
    bb_pctb_1h = _safe(npz, 'bb_pct_b_1h', n, 0.5)

    _bph_15m = 15 // _ltf_mins   # bars per 15m candle: 5 (crypto/3m) or 3 (tradier/5m)
    _use_cross = bool(getattr(cfg, 'WT_EXIT_USE_CROSS_EVENTS', False))
    if _use_cross:
        # NPZ cross events fire only at the first bar of the candle where the cross happened.
        # Expand each single-bar event to cover the full candle duration so exit fires for the
        # entire candle (12 bars for 1h/tradier, 20 for 1h/crypto; 3 for 15m/tradier, 5 for 15m/crypto).
        _raw_c15b = _safe(npz, 'wt_cross_bear_15m', n).astype(bool)
        _raw_c1hb = _safe(npz, 'wt_cross_bear_1h', n).astype(bool)
        _raw_c15u = _safe(npz, 'wt_cross_bull_15m', n).astype(bool)
        _raw_c1hu = _safe(npz, 'wt_cross_bull_1h', n).astype(bool)
        _exp_c15b = np.zeros(n, dtype=bool); _exp_c1hb = np.zeros(n, dtype=bool)
        _exp_c15u = np.zeros(n, dtype=bool); _exp_c1hu = np.zeros(n, dtype=bool)
        for _k in range(_bph_15m):
            _exp_c15b[_k:] |= _raw_c15b[:n - _k]; _exp_c15u[_k:] |= _raw_c15u[:n - _k]
        for _k in range(_bph_1h):
            _exp_c1hb[_k:] |= _raw_c1hb[:n - _k]; _exp_c1hu[_k:] |= _raw_c1hu[:n - _k]
        if is_long:
            wt_against = (wt1_ltf < wt2_ltf).astype(int) + _exp_c15b.astype(int) + _exp_c1hb.astype(int)
        else:
            wt_against = (wt1_ltf > wt2_ltf).astype(int) + _exp_c15u.astype(int) + _exp_c1hu.astype(int)
    elif is_long:
        wt_against = (wt1_ltf < wt2_ltf).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
    else:
        wt_against = (wt1_ltf > wt2_ltf).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
    delta_exit = wt_against >= cfg.WT_EXIT_MIN_TFS
    _vel_exit_thr = float(getattr(cfg, 'DELTA_EXIT_VEL_MIN_DECAY', 2.0))
    vel_exit = (wt_vel_4h < -_vel_exit_thr) if is_long else (wt_vel_4h > _vel_exit_thr)
    if getattr(cfg, 'STDEV_SUPPRESS_EARLY_EXIT', False):
        _bb_pctb_D_sup = _safe(npz, 'bb_pct_b_D', n, 0.5)
        _bb_sup_thr = float(getattr(cfg, 'STDEV_BB_RZ_SUPPRESS_PCTB', 0.85))
        if is_long:
            _approaching_band = (_bb_pctb_D_sup > _bb_sup_thr) & (_bb_pctb_D_sup < 1.0)
        else:
            _approaching_band = (_bb_pctb_D_sup < (1.0 - _bb_sup_thr)) & (_bb_pctb_D_sup > 0.0)
        delta_exit = delta_exit & ~_approaching_band
        vel_exit = vel_exit & ~_approaching_band

    # WT-VELOCITY-DECAY exit (user priority: "sell when wt delta slows down")
    # Exit when 1h velocity magnitude drops below threshold after being strong
    wt_vel_1h_exit = _safe(npz, 'wt_velocity_1h', n)
    wt_vel_1h_prev = np.roll(wt_vel_1h_exit, 1); wt_vel_1h_prev[0] = wt_vel_1h_exit[0]
    vel_decay_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_VEL_DECAY_EXIT_ENABLED', True):
        decay_threshold = float(getattr(cfg, 'WT_VEL_DECAY_THRESHOLD', 1.0))
        if is_long:
            was_strong = wt_vel_1h_prev > decay_threshold * 2
            now_decayed = wt_vel_1h_exit < decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_ltf < wt_vel_1h_prev * 0.5)
        else:
            was_strong = wt_vel_1h_prev < -decay_threshold * 2
            now_decayed = wt_vel_1h_exit > -decay_threshold
            vel_decay_exit = was_strong & now_decayed & (wt_vel_ltf > wt_vel_1h_prev * 0.5)
        if getattr(cfg, 'STDEV_SUPPRESS_EARLY_EXIT', False):
            _bb_sup2 = _safe(npz, 'bb_pct_b_D', n, 0.5)
            _bb_sup2_thr = float(getattr(cfg, 'STDEV_BB_RZ_SUPPRESS_PCTB', 0.85))
            if is_long:
                _apr2 = (_bb_sup2 > _bb_sup2_thr) & (_bb_sup2 < 1.0)
            else:
                _apr2 = (_bb_sup2 < (1.0 - _bb_sup2_thr)) & (_bb_sup2 > 0.0)
            vel_decay_exit = vel_decay_exit & ~_apr2
    srs_exit = np.zeros(n, dtype=bool)
    if cfg.STRUCTURAL_RANGE_SHIFT_EXIT:
        tf_map = {'dc_1h': ('dc_high_1h', 'dc_low_1h'), 'dc_4h': ('dc_high_4h', 'dc_low_4h'),
                  'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
        hk, lk = tf_map.get(cfg.STRUCTURAL_RANGE_SHIFT_TF, ('dc_high_4h', 'dc_low_4h'))
        hi = _safe(npz, hk, n); lo = _safe(npz, lk, n)
        k_1h_prev = np.roll(k_1h, _bph_1h); k_1h_prev[:_bph_1h] = k_1h[:_bph_1h]
        _srs_k_hi = float(getattr(cfg, 'SRS_K_EXIT_1H', 75.0))
        _srs_k_lo = 100.0 - _srs_k_hi
        if is_long:
            prox = (hi > 0) & (np.abs(close - hi) / np.maximum(hi, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h >= _srs_k_hi) & (k_1h < k_1h_prev)
        else:
            prox = (lo > 0) & (np.abs(close - lo) / np.maximum(lo, 1e-9) <= 0.01)
            srs_exit = prox & (k_1h <= _srs_k_lo) & (k_1h > k_1h_prev)
    sat_exit = np.zeros(n, dtype=bool)
    if cfg.SATOSHIT_ENABLED:
        k_ltf_prev = np.roll(k_ltf, 1); k_ltf_prev[0] = k_ltf[0]
        mfi_ltf_prev = np.roll(mfi_ltf, 1); mfi_ltf_prev[0] = mfi_ltf[0]
        if is_long:
            sat_exit = (k_ltf_prev >= 80) & (k_ltf_prev >= d_ltf) & (k_ltf < d_ltf) & (mfi_ltf < mfi_ltf_prev)
        else:
            sat_exit = (k_ltf_prev <= 20) & (k_ltf_prev <= d_ltf) & (k_ltf > d_ltf) & (mfi_ltf > mfi_ltf_prev)
    rz_exit = np.zeros(n, dtype=bool)
    if cfg.RZ_EXIT_ENABLED:
        _rz_k_exit = float(getattr(cfg, 'RZ_K_EXIT', 80.0))
        _rz_mfi_exit = float(getattr(cfg, 'RZ_MFI_EXIT', 85.0))
        _rz_top_bb = float(getattr(cfg, 'RZ_TOP_BB_THRESHOLD', 0.85))
        _rz_bot_bb = float(getattr(cfg, 'RZ_BOT_BB_THRESHOLD', 0.15))
        if is_long:
            rz_exit = (bb_pctb_1h >= _rz_top_bb) & (k_1h >= _rz_k_exit) & (wt_vel_ltf < -1.0)
            if _rz_mfi_exit > 0:
                rz_exit = rz_exit | ((bb_pctb_1h >= _rz_top_bb) & (mfi_1h >= _rz_mfi_exit) & (wt_vel_ltf < -1.0))
        else:
            rz_exit = (bb_pctb_1h <= _rz_bot_bb) & (k_1h <= (100.0 - _rz_k_exit)) & (wt_vel_ltf > 1.0)
            if _rz_mfi_exit > 0:
                rz_exit = rz_exit | ((bb_pctb_1h <= _rz_bot_bb) & (mfi_1h <= (100.0 - _rz_mfi_exit)) & (wt_vel_ltf > 1.0))
    if getattr(cfg, 'STDEV_BB_RZ_EXIT_ENABLED', False):
        _bb_rz_tf = str(getattr(cfg, 'STDEV_BB_RZ_EXIT_TF', 'D'))
        _bb_pctb_rz = _safe(npz, f'bb_pct_b_{_bb_rz_tf}', n, 0.5)
        _bb_rz_prev = np.roll(_bb_pctb_rz, 1); _bb_rz_prev[0] = _bb_pctb_rz[0]
        if is_long:
            rz_exit = rz_exit | ((_bb_rz_prev >= 1.0) & (_bb_pctb_rz < 1.0) & (wt_vel_ltf < 0))
        else:
            rz_exit = rz_exit | ((_bb_rz_prev <= 0.0) & (_bb_pctb_rz > 0.0) & (wt_vel_ltf > 0))
    exit_scorer_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'EXIT_SCORER_ENABLED', False):
        _es_min = int(getattr(cfg, 'EXIT_SCORER_MIN_CONDITIONS', 3))
        _es_k = float(getattr(cfg, 'EXIT_SCORER_K_EXTREME', 75.0))
        _es_dc = float(getattr(cfg, 'EXIT_SCORER_DC_EXTREME', 0.80))
        dc_pos_1h = _safe(npz, 'dc_position_1h', n)
        _wt1_4h_es = _safe(npz, 'wt1_4h', n); _wt2_4h_es = _safe(npz, 'wt2_4h', n)
        if is_long:
            _cond1 = wt1_1h < wt2_1h
            _cond2 = _wt1_4h_es < _wt2_4h_es
            _cond3 = k_1h >= _es_k
            _cond4 = dc_pos_1h >= _es_dc
            _cond5 = wt_vel_1h_exit < -1.0
        else:
            _cond1 = wt1_1h > wt2_1h
            _cond2 = _wt1_4h_es > _wt2_4h_es
            _cond3 = k_1h <= (100.0 - _es_k)
            _cond4 = dc_pos_1h <= (1.0 - _es_dc)
            _cond5 = wt_vel_1h_exit > 1.0
        exit_scorer_exit = (_cond1.astype(int) + _cond2.astype(int) + _cond3.astype(int) + _cond4.astype(int) + _cond5.astype(int)) >= _es_min
    stoch_1h_exit = np.zeros(n, dtype=bool)
    if cfg.STOCH_CROSS_1H_EXIT_ENABLED:
        k_1h_prev = np.roll(k_1h, _bph_1h); k_1h_prev[:_bph_1h] = k_1h[:_bph_1h]
        _s1h_k_min = float(getattr(cfg, 'STOCH_1H_EXIT_K_MIN', 70.0))
        if is_long:
            stoch_1h_exit = (k_1h_prev >= d_1h) & (k_1h < d_1h) & (k_1h_prev >= _s1h_k_min)
        else:
            stoch_1h_exit = (k_1h_prev <= d_1h) & (k_1h > d_1h) & (k_1h_prev <= (100.0 - _s1h_k_min))
    mfi_flip_exit = np.zeros(n, dtype=bool)
    if cfg.MFI_FLIP_EXIT_ENABLED:
        if is_long: mfi_flip_exit = mfi_1h > cfg.MFI_FLIP_EXIT_LONG_THRESHOLD
        else: mfi_flip_exit = mfi_1h < cfg.MFI_FLIP_EXIT_SHORT_THRESHOLD
    wt_cu_exit = np.zeros(n, dtype=bool)
    if cfg.WT_CROSSUNDER_FINAL_ENABLED:
        wt1_ltf_prev = np.roll(wt1_ltf, 1); wt1_ltf_prev[0] = wt1_ltf[0]
        if is_long:
            wt_cu_exit = (wt1_ltf_prev >= wt2_ltf) & (wt1_ltf < wt2_ltf) & (k_ltf >= 70)
        else:
            wt_cu_exit = (wt1_ltf_prev <= wt2_ltf) & (wt1_ltf > wt2_ltf) & (k_ltf <= 30)
    simple_mtf_wt_cross_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'SIMPLE_MTF_WT_CROSS_EXIT_ENABLED', False):
        _smwc_w1_15m = _safe(npz, 'wt1_15m', n); _smwc_w2_15m = _safe(npz, 'wt2_15m', n)
        _smwc_w1_4h = _safe(npz, 'wt1_4h', n); _smwc_w2_4h = _safe(npz, 'wt2_4h', n)
        _smwc_w1_D = _safe(npz, 'wt1_D', n); _smwc_w2_D = _safe(npz, 'wt2_D', n)
        if is_long:
            _smwc_ltf_down = wt1_ltf < wt2_ltf
            _smwc_15m_confirm = (_smwc_w1_15m < _smwc_w2_15m) | (_smwc_w1_15m > 95.0)
            _smwc_htf_against = (wt1_1h < wt2_1h) | (_smwc_w1_4h < _smwc_w2_4h) | (_smwc_w1_D < _smwc_w2_D)
            simple_mtf_wt_cross_exit = _smwc_ltf_down & _smwc_15m_confirm & _smwc_htf_against
        else:
            _smwc_ltf_up = wt1_ltf > wt2_ltf
            _smwc_15m_confirm = (_smwc_w1_15m > _smwc_w2_15m) | (_smwc_w1_15m < -95.0)
            _smwc_htf_against = (wt1_1h > wt2_1h) | (_smwc_w1_4h > _smwc_w2_4h) | (_smwc_w1_D > _smwc_w2_D)
            simple_mtf_wt_cross_exit = _smwc_ltf_up & _smwc_15m_confirm & _smwc_htf_against
    mi_exit = np.zeros(n, dtype=bool)
    if cfg.MI_EXIT_ENABLED:
        mfi_1h_prev = np.roll(mfi_1h, _bph_1h); mfi_1h_prev[:_bph_1h] = mfi_1h[:_bph_1h]
        if is_long:
            mi_exit = (mfi_1h_prev > 70) & (mfi_1h < mfi_1h_prev) & (k_1h > 70)
        else:
            mi_exit = (mfi_1h_prev < 30) & (mfi_1h > mfi_1h_prev) & (k_1h < 30)

    # ===== Auto-hooked exit gates (Group B switches) =====
    extra_exit = np.zeros(n, dtype=bool)
    # RSI exit long/short tradier
    rsi_1h_arr = _safe(npz, 'rsi_1h', n, 50)
    if getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 0) > 0 and is_long:
        extra_exit = extra_exit | (rsi_1h_arr >= getattr(cfg, 'RSI_EXIT_LONG_TRADIER', 85.0))
    if getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 100) < 100 and not is_long:
        extra_exit = extra_exit | (rsi_1h_arr <= getattr(cfg, 'RSI_EXIT_SHORT_TRADIER', 15.0))
    # RSI2 exit tradier (Connors-style) — LTF-parameterized
    if getattr(cfg, 'TRADIER_RSI2_ENABLED', False):
        rsi2_ltf = _safe(npz, f'rsi2_{_ltf}', n, 50)
        if rsi2_ltf.sum() > 0:
            if is_long:
                extra_exit = extra_exit | (rsi2_ltf >= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_LONG', 90.0))
            else:
                extra_exit = extra_exit | (rsi2_ltf <= getattr(cfg, 'TRADIER_RSI2_EXIT_THRESHOLD_SHORT', 10.0))
    # Satoshit exit (simplified — RSI+Stoch votes) — LTF-parameterized
    if getattr(cfg, 'SATOSHIT_EXIT_ENABLED', False):
        k_ltf_arr2 = _safe(npz, f'stoch_k_{_ltf}', n, 50)
        if is_long:
            rsi_hit = rsi_1h_arr >= getattr(cfg, 'SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER', 55.0)
            stoch_hit = k_ltf_arr2 >= getattr(cfg, 'SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER', 60.0)
        else:
            rsi_hit = rsi_1h_arr <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER', 42.0)
            stoch_hit = k_ltf_arr2 <= getattr(cfg, 'SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER', 50.0)
        votes_needed = getattr(cfg, 'SATOSHIT_MIN_VOTES_TRADIER', 3)
        # 2 visible + 1 latent (bb_pctb would be 3rd) — simpler: require 2 of 2 if votes >= 3
        if votes_needed >= 3:
            extra_exit = extra_exit | (rsi_hit & stoch_hit)
        else:
            extra_exit = extra_exit | (rsi_hit | stoch_hit)
    # Stoch cross LTF exit — LTF-parameterized (config key name retained)
    if getattr(cfg, 'STOCH_CROSS_3M_EXIT_ENABLED', False):
        k_ltf_arr2 = _safe(npz, f'stoch_k_{_ltf}', n, 50)
        d_ltf_arr2 = _safe(npz, f'stoch_d_{_ltf}', n, 50)
        k_ltf_arr2_prev = np.roll(k_ltf_arr2, 1); k_ltf_arr2_prev[0] = k_ltf_arr2[0]
        if is_long:
            extra_exit = extra_exit | ((k_ltf_arr2_prev >= d_ltf_arr2) & (k_ltf_arr2 < d_ltf_arr2) & (k_ltf_arr2 > 60))
        else:
            extra_exit = extra_exit | ((k_ltf_arr2_prev <= d_ltf_arr2) & (k_ltf_arr2 > d_ltf_arr2) & (k_ltf_arr2 < 40))
    # Ablation: disable quick exit path
    if getattr(cfg, 'ABLATION_DISABLE_QUICK_EXIT', False):
        return np.zeros(n, dtype=bool)
    # Cycle TP early cap
    if getattr(cfg, 'CYCLE_TP_TIERED_ENABLED', False):
        pass
    # ═══ WT/0DC AUDIT EXIT SIGNALS (ablation sweep — all OFF by default) ═══
    # wt_momentum_state encoding: 2=bull_trend, 1=bull_weak, -1=bear_weak, -2=bear_trend
    # wt_peak_structure: 1=HH, -1=LH | wt_trough_structure: 1=HL, -1=LL
    # wt_divergence: -1=BEAR, 1=BULL | wt_wave_phase: 1=expanding, -1=contracting
    wt_mom_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_MOMENTUM_EXIT_ENABLED', False):
        _tfs_m = str(getattr(cfg, 'WT_MOMENTUM_EXIT_TF', '1h'))
        _mom = _safe(npz, f'wt_momentum_state_{_tfs_m}', n, 0).astype(np.int8)
        _mom_thr = int(getattr(cfg, 'WT_MOMENTUM_EXIT_THRESHOLD', 0))
        wt_mom_exit = (_mom <= _mom_thr) if is_long else (_mom >= -_mom_thr)
    wt_struct_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_STRUCT_EXIT_ENABLED', False):
        _tfs_s = str(getattr(cfg, 'WT_STRUCT_EXIT_TF', '1h'))
        _pk = _safe(npz, f'wt_peak_structure_{_tfs_s}', n, 0).astype(np.int8)
        _tr = _safe(npz, f'wt_trough_structure_{_tfs_s}', n, 0).astype(np.int8)
        wt_struct_exit = (_pk == -1) if is_long else (_tr == -1)
    wt_div_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_DIV_EXIT_ENABLED', False):
        _tfs_d = str(getattr(cfg, 'WT_DIV_EXIT_TF', '1h'))
        _div = _safe(npz, f'wt_divergence_{_tfs_d}', n, 0).astype(np.int8)
        wt_div_exit = (_div == -1) if is_long else (_div == 1)
    wt_pct_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_PERCENTILE_EXIT_ENABLED', False):
        _tfs_p = str(getattr(cfg, 'WT_PERCENTILE_EXIT_TF', '1h'))
        _pct = _safe(npz, f'wt_percentile_{_tfs_p}', n, 50.0)
        _pct_thr = float(getattr(cfg, 'WT_PERCENTILE_EXIT_THRESHOLD', 80.0))
        wt_pct_exit = (_pct >= _pct_thr) if is_long else (_pct <= (100.0 - _pct_thr))
    wt_zscore_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_ZSCORE_EXIT_ENABLED', False):
        _tfs_z = str(getattr(cfg, 'WT_ZSCORE_EXIT_TF', '1h'))
        _zs = _safe(npz, f'wt_zscore_{_tfs_z}', n, 0.0)
        _z_thr = float(getattr(cfg, 'WT_ZSCORE_EXIT_THRESHOLD', 2.0))
        wt_zscore_exit = (_zs >= _z_thr) if is_long else (_zs <= -_z_thr)
    wt_accel_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_ACCEL_EXIT_ENABLED', False):
        _tfs_a = str(getattr(cfg, 'WT_ACCEL_EXIT_TF', '1h'))
        _acc = _safe(npz, f'wt_acceleration_{_tfs_a}', n, 0.0)
        wt_accel_exit = (_acc < 0) if is_long else (_acc > 0)
    wt_wave_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_WAVE_PHASE_EXIT_ENABLED', False):
        _tfs_w = str(getattr(cfg, 'WT_WAVE_PHASE_EXIT_TF', '1h'))
        _wp = _safe(npz, f'wt_wave_phase_{_tfs_w}', n, 0).astype(np.int8)
        wt_wave_exit = _wp == -1
    wt_score_flip_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_SCORE_FLIP_EXIT_ENABLED', False):
        _tfs_sf = str(getattr(cfg, 'WT_SCORE_FLIP_EXIT_TF', '3m'))
        _sc = _safe(npz, f'wt_score_{_tfs_sf}', n, 0.0)
        wt_score_flip_exit = (_sc < 0) if is_long else (_sc > 0)
    wt_vel_mtf_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_VEL_MTF_EXIT_ENABLED', False):
        _v3 = _safe(npz, 'wt_velocity_3m', n, 0.0); _v15 = _safe(npz, 'wt_velocity_15m', n, 0.0)
        _v1h = _safe(npz, 'wt_velocity_1h', n, 0.0); _v4h = _safe(npz, 'wt_velocity_4h', n, 0.0)
        _v_thr = float(getattr(cfg, 'WT_VEL_MTF_EXIT_THRESHOLD', -1.0))
        _v_min_tfs = int(getattr(cfg, 'WT_VEL_MTF_EXIT_MIN_TFS', 3))
        if is_long:
            _vcnt = (_v3 < _v_thr).astype(int) + (_v15 < _v_thr).astype(int) + (_v1h < _v_thr).astype(int) + (_v4h < _v_thr / 2).astype(int)
        else:
            _vcnt = (_v3 > -_v_thr).astype(int) + (_v15 > -_v_thr).astype(int) + (_v1h > -_v_thr).astype(int) + (_v4h > _v_thr / 2).astype(int)
        wt_vel_mtf_exit = _vcnt >= _v_min_tfs
    wt_align_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_ALIGN_EXIT_ENABLED', False):
        _al_thr = int(getattr(cfg, 'WT_ALIGN_EXIT_MIN', 2))
        if is_long:
            _al = _safe(npz, 'wt_bull_alignment', n, 3).astype(np.int8)
            wt_align_exit = _al < _al_thr
        else:
            _al = _safe(npz, 'wt_bear_alignment', n, 3).astype(np.int8)
            wt_align_exit = _al < _al_thr
    wt_comp_delta_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_COMP_DELTA_EXIT_ENABLED', False):
        _cd = _safe(npz, 'wt_composite_delta', n, 0.0)
        _cd_thr = float(getattr(cfg, 'WT_COMP_DELTA_EXIT_THRESHOLD', 0.0))
        wt_comp_delta_exit = (_cd < _cd_thr) if is_long else (_cd > -_cd_thr)
    dc_pos_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'DC_POS_EXIT_ENABLED', False):
        _dcp_1h = _safe(npz, 'dc_position_1h', n, 0.5); _dcp_4h = _safe(npz, 'dc_position_4h', n, 0.5); _dcp_D = _safe(npz, 'dc_position_D', n, 0.5)
        _dc_avg = (_dcp_1h + _dcp_4h + _dcp_D) / 3.0
        _dc_thr = float(getattr(cfg, 'DC_POS_EXIT_THRESHOLD', 0.7))
        dc_pos_exit = (_dc_avg >= _dc_thr) if is_long else (_dc_avg <= (1.0 - _dc_thr))
    vel_floor_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'WT_VEL_FLOOR_EXIT_ENABLED', False):
        _vf = _safe(npz, 'wt_velocity_1h', n)
        _vf_thr = float(getattr(cfg, 'WT_VEL_FLOOR_EXIT_LONG_MAX', 0.0))
        vel_floor_exit = (_vf < _vf_thr) if is_long else (_vf > -_vf_thr)
    kd_wt1h_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'KD_WT1H_EXIT_ENABLED', False):
        k_D_arr = _safe(npz, 'stoch_k_D', n, 50.0)
        _kd_thr = float(getattr(cfg, 'KD_WT1H_EXIT_OVERBOUGHT', 80.0))
        kd_wt1h_exit = ((k_D_arr >= _kd_thr) & (wt1_1h < wt2_1h)) if is_long else ((k_D_arr <= (100.0 - _kd_thr)) & (wt1_1h > wt2_1h))
    k_lower_high_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'K_LOWER_HIGH_EXIT_ENABLED', False):
        _klh_thr = float(getattr(cfg, 'K_LOWER_HIGH_LTF_THRESHOLD', 65.0))
        _klh_extreme = float(getattr(cfg, 'K_LOWER_HIGH_EXTREME', 95.0))
        k_ltf_prev_lh = np.roll(k_ltf, 1); k_ltf_prev_lh[0] = k_ltf[0]
        if is_long:
            k_lower_high_exit = (k_ltf_prev_lh >= _klh_thr) & (k_ltf < k_ltf_prev_lh) & (k_ltf_prev_lh < _klh_extreme)
        else:
            k_lower_high_exit = (k_ltf_prev_lh <= (100.0 - _klh_thr)) & (k_ltf > k_ltf_prev_lh) & (k_ltf_prev_lh > (100.0 - _klh_extreme))
    # RZ_CASCADE exit (2026-04-21): any TF reverse fires close. Mirrors compute_rz_cascade_signals exit path.
    rz_cascade_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'RZ_CASCADE_ENABLED', False):
        _, rz_cascade_exit = compute_rz_cascade_signals(npz, n, is_long, cfg)
    # NPZ-FIELD EXIT SIGNALS (2026-04-22)
    macd_hist_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'MACD_HIST_EXIT_ENABLED', False):
        _mh_tf = str(getattr(cfg, 'MACD_HIST_EXIT_TF', '1h'))
        _mh = _safe(npz, f'macd_hist_{_mh_tf}', n, 0.0)
        if _mh.any():
            _mh_prev = np.roll(_mh, 1); _mh_prev[0] = _mh[0]
            if is_long: macd_hist_exit = (_mh_prev > 0) & (_mh <= 0)
            else: macd_hist_exit = (_mh_prev < 0) & (_mh >= 0)
    stdev_fail_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'EXIT_STDEV_BREAKOUT_FAIL_ENABLED', False):
        _sf_tf = str(getattr(cfg, 'EXIT_STDEV_REJECTION_TF', 'D'))
        _sf_high = float(getattr(cfg, 'EXIT_STDEV_REJECTION_HIGH', 1.0))
        _sf_ret = float(getattr(cfg, 'EXIT_STDEV_REJECTION_RETURN', 0.85))
        _sf_pctb = _safe(npz, f'bb_pct_b_{_sf_tf}', n, 0.5)
        _sf_prev = np.roll(_sf_pctb, 1); _sf_prev[0] = _sf_pctb[0]
        if is_long:
            stdev_fail_exit = (_sf_prev >= _sf_high) & (_sf_pctb < _sf_ret)
        else:
            stdev_fail_exit = (_sf_prev <= (1.0 - _sf_high)) & (_sf_pctb > (1.0 - _sf_ret))
    stdev_reject_exit = np.zeros(n, dtype=bool)
    if getattr(cfg, 'STDEV_REJECT_EXIT_ENABLED', False):
        _sre_tf = str(getattr(cfg, 'STDEV_REJECT_EXIT_TF', 'D'))
        _sre_zone = float(getattr(cfg, 'STDEV_REJECT_EXIT_ZONE', 0.80))
        _sre_ret = float(getattr(cfg, 'STDEV_REJECT_EXIT_RETURN', 0.65))
        _sre_pctb = _safe(npz, f'bb_pct_b_{_sre_tf}', n, 0.5)
        _sre_prev = np.roll(_sre_pctb, 1); _sre_prev[0] = _sre_pctb[0]
        if is_long:
            stdev_reject_exit = (_sre_prev >= _sre_zone) & (_sre_pctb < _sre_ret) & (wt_vel_ltf < 0)
        else:
            stdev_reject_exit = (_sre_prev <= (1.0 - _sre_zone)) & (_sre_pctb > (1.0 - _sre_ret)) & (wt_vel_ltf > 0)
    base_exit = delta_exit | vel_exit | srs_exit | sat_exit | rz_exit | rz_cascade_exit | exit_scorer_exit | stoch_1h_exit | mfi_flip_exit | wt_cu_exit | simple_mtf_wt_cross_exit | mi_exit | vel_decay_exit | extra_exit | wt_mom_exit | wt_struct_exit | wt_div_exit | wt_pct_exit | wt_zscore_exit | wt_accel_exit | wt_wave_exit | wt_score_flip_exit | wt_vel_mtf_exit | wt_align_exit | wt_comp_delta_exit | dc_pos_exit | vel_floor_exit | kd_wt1h_exit | k_lower_high_exit | macd_hist_exit | stdev_fail_exit | stdev_reject_exit
    # ═══ 2026-04-30 HTF PORT — F3. HTF_W_REVERSAL_EXIT — tradier-only (default OFF) ═══
    if (str(getattr(cfg, 'MODE', 'crypto')) == 'tradier') and bool(getattr(cfg, 'HTF_W_REVERSAL_EXIT_TRADIER_ENABLED', False)):
        try:
            _w1_W_e = _safe(npz, 'wt1_W', n); _w2_W_e = _safe(npz, 'wt2_W', n)
            _w1_D_e = _safe(npz, 'wt1_D', n); _w2_D_e = _safe(npz, 'wt2_D', n)
            _w_has_e = (_w1_W_e != 0) | (_w2_W_e != 0)
            _d_has_e = (_w1_D_e != 0) | (_w2_D_e != 0)
            if is_long:
                _w_against = (_w1_W_e < _w2_W_e) & _w_has_e
                _d_against = (_w1_D_e < _w2_D_e) & _d_has_e
            else:
                _w_against = (_w1_W_e > _w2_W_e) & _w_has_e
                _d_against = (_w1_D_e > _w2_D_e) & _d_has_e
            if bool(getattr(cfg, 'HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D', True)):
                _htf_rev_exit = _w_against & _d_against
            else:
                _htf_rev_exit = _w_against
            base_exit = base_exit | _htf_rev_exit
        except Exception:
            pass
    # D4: BREAKOUT MULTI-LUNG exit augmentation (default OFF)
    if getattr(cfg, 'BREAKOUT_MULTI_LUNG_ENABLED', False):
        try:
            from breakout_multi_lung import multi_lung_exit_signal
            ml_exit = multi_lung_exit_signal(npz, n, is_long, cfg)
            mode = str(getattr(cfg, 'BREAKOUT_MULTI_LUNG_MODE', 'AUGMENT')).upper()
            if mode == 'REPLACE':
                return ml_exit
            return base_exit | ml_exit
        except Exception:
            pass
    return base_exit


def _btc_trend_simulate_per_sym(npz, cfg, n: int, _ltf: str,
                                  _bph_15m: int, _bph_1h: int, _bph_4h: int, _bph_D: int):
    """BTC TREND-FOLLOW Tier-1 POC (2026-04-27 — user path A).

    Different design philosophy from _btc_dedicated_simulate_per_sym:
      - Catch big trend legs (≥30% moves), not chop
      - Wide ATR-based trailing stops (2.5× ATR_D)
      - Hold for days, not 20 bars
      - Bigger size per trade ($500 own / $10k notional at 20×)
      - Max 1 concurrent position

    Entry LONG: close > prev_dc_high_D AND wt1_D>wt2_D AND wt1_4h>wt2_4h AND wt1_1h>wt2_1h
    Entry SHORT: mirror.
    Exit: ATR trailing OR D WT flip after profit OR hard % stop.
    """
    close = _safe(npz, f'close_{_ltf}', n)
    dc_high_D = _safe(npz, 'dc_high_D', n); dc_low_D = _safe(npz, 'dc_low_D', n)
    dc_high_D_prev = np.roll(dc_high_D, 1); dc_high_D_prev[0] = dc_high_D[0]
    dc_low_D_prev = np.roll(dc_low_D, 1); dc_low_D_prev[0] = dc_low_D[0]
    wt1_D = _safe(npz, 'wt1_D', n); wt2_D = _safe(npz, 'wt2_D', n)
    wt1_4h = _safe(npz, 'wt1_4h', n); wt2_4h = _safe(npz, 'wt2_4h', n)
    wt1_1h = _safe(npz, 'wt1_1h', n); wt2_1h = _safe(npz, 'wt2_1h', n)
    atr_D = _safe(npz, 'atr_D', n)

    atr_mult = float(getattr(cfg, 'BTC_TREND_ATR_MULT', 2.5))
    hard_loss_pct = float(getattr(cfg, 'BTC_TREND_HARD_LOSS_PCT', 5.0))
    require_d_profit_for_wt_exit = bool(getattr(cfg, 'BTC_TREND_REQUIRE_PROFIT_FOR_WT_EXIT', True))
    cooldown_bars = int(getattr(cfg, 'BTC_TREND_COOLDOWN_BARS', _bph_D))

    sym_pnl = []
    position = "FLAT"; entry_price = 0.0; peak_price = 0.0
    last_exit_bar = -10**9
    start_i = max(_bph_D * 2, 200)

    for i in range(start_i, n):
        price_i = float(close[i])
        if price_i <= 0:
            continue
        if position == "FLAT":
            if i - last_exit_bar < cooldown_bars:
                continue
            d_bull = wt1_D[i] > wt2_D[i]; d_bear = wt1_D[i] < wt2_D[i]
            h4_bull = wt1_4h[i] > wt2_4h[i]; h4_bear = wt1_4h[i] < wt2_4h[i]
            h1_bull = wt1_1h[i] > wt2_1h[i]; h1_bear = wt1_1h[i] < wt2_1h[i]
            # Configurable entry mode (BTC_TREND_ENTRY_MODE):
            #   "dc_d"        — original: D Donchian breakout + WT D+4h+1h aligned (rare, ~10 trades/4yr)
            #   "dc_4h"       — 4h Donchian breakout (more frequent)
            #   "wt_align"    — WT bull on D+4h+1h, no Donchian requirement (most permissive)
            #   "wt_d_cross"  — D WT cross within last 5 D-bars + 4h alignment
            mode = str(getattr(cfg, 'BTC_TREND_ENTRY_MODE', 'dc_d'))
            min_htf_aligned = int(getattr(cfg, 'BTC_TREND_MIN_HTF_ALIGNED', 3))   # how many of D+4h+1h must align
            bull_aligned = int(d_bull) + int(h4_bull) + int(h1_bull)
            bear_aligned = int(d_bear) + int(h4_bear) + int(h1_bear)
            if mode == "dc_d":
                long_ok = (price_i > float(dc_high_D_prev[i]) and dc_high_D_prev[i] > 0
                           and bull_aligned >= min_htf_aligned)
                short_ok = (price_i < float(dc_low_D_prev[i]) and dc_low_D_prev[i] > 0
                            and bear_aligned >= min_htf_aligned)
            elif mode == "dc_4h":
                # Use 4h Donchian (need 4h dc fields)
                _dc_h4 = _safe(npz, 'dc_high_4h', n)
                _dc_l4 = _safe(npz, 'dc_low_4h', n)
                _dc_h4p = _dc_h4[i - 1] if i > 0 else _dc_h4[0]
                _dc_l4p = _dc_l4[i - 1] if i > 0 else _dc_l4[0]
                long_ok = (price_i > float(_dc_h4p) and _dc_h4p > 0
                           and bull_aligned >= min_htf_aligned)
                short_ok = (price_i < float(_dc_l4p) and _dc_l4p > 0
                            and bear_aligned >= min_htf_aligned)
            elif mode == "wt_align":
                long_ok = bull_aligned >= min_htf_aligned
                short_ok = bear_aligned >= min_htf_aligned
            elif mode == "wt_d_cross":
                # Detect D WT cross within last 5 D-bars (5 × _bph_D LTF bars)
                lb = 5 * _bph_D
                start = max(0, i - lb)
                # crossed up = at some point in window, wt1_D[k-1] <= wt2_D[k-1] AND wt1_D[k] > wt2_D[k]
                d_cross_up = False; d_cross_dn = False
                for k in range(start + 1, i + 1):
                    if wt1_D[k - 1] <= wt2_D[k - 1] and wt1_D[k] > wt2_D[k]:
                        d_cross_up = True; break
                for k in range(start + 1, i + 1):
                    if wt1_D[k - 1] >= wt2_D[k - 1] and wt1_D[k] < wt2_D[k]:
                        d_cross_dn = True; break
                long_ok = d_cross_up and bull_aligned >= max(2, min_htf_aligned - 1)
                short_ok = d_cross_dn and bear_aligned >= max(2, min_htf_aligned - 1)
            else:
                long_ok = False; short_ok = False
            if long_ok and not short_ok:
                position = "LONG"; entry_price = price_i; peak_price = price_i
            elif short_ok and not long_ok:
                position = "SHORT"; entry_price = price_i; peak_price = price_i
            continue

        if position == "LONG":
            pnl_pct = (price_i - entry_price) / entry_price * 100.0
            if price_i > peak_price: peak_price = price_i
            atr_i = float(atr_D[i]) if atr_D[i] > 0 else max(price_i * 0.02, 1.0)
            trail_stop = peak_price - atr_mult * atr_i
            if price_i <= trail_stop:
                sym_pnl.append(pnl_pct); last_exit_bar = i
                position = "FLAT"; entry_price = 0.0; peak_price = 0.0; continue
            if wt1_D[i] < wt2_D[i] and ((not require_d_profit_for_wt_exit) or pnl_pct > 0):
                sym_pnl.append(pnl_pct); last_exit_bar = i
                position = "FLAT"; entry_price = 0.0; peak_price = 0.0; continue
            if pnl_pct <= -hard_loss_pct:
                sym_pnl.append(pnl_pct); last_exit_bar = i
                position = "FLAT"; entry_price = 0.0; peak_price = 0.0; continue
        else:
            pnl_pct = (entry_price - price_i) / entry_price * 100.0
            if price_i < peak_price: peak_price = price_i
            atr_i = float(atr_D[i]) if atr_D[i] > 0 else max(price_i * 0.02, 1.0)
            trail_stop = peak_price + atr_mult * atr_i
            if price_i >= trail_stop:
                sym_pnl.append(pnl_pct); last_exit_bar = i
                position = "FLAT"; entry_price = 0.0; peak_price = 0.0; continue
            if wt1_D[i] > wt2_D[i] and ((not require_d_profit_for_wt_exit) or pnl_pct > 0):
                sym_pnl.append(pnl_pct); last_exit_bar = i
                position = "FLAT"; entry_price = 0.0; peak_price = 0.0; continue
            if pnl_pct <= -hard_loss_pct:
                sym_pnl.append(pnl_pct); last_exit_bar = i
                position = "FLAT"; entry_price = 0.0; peak_price = 0.0; continue

    if position != "FLAT" and entry_price > 0:
        price_last = float(close[n - 1])
        if position == "LONG":
            sym_pnl.append((price_last - entry_price) / entry_price * 100.0)
        else:
            sym_pnl.append((entry_price - price_last) / entry_price * 100.0)
    return sym_pnl


def _btc_restricted_setup_vec(npz, n, cfg):
    """Vectorized precompute of restricted-mode setup detection across the entire array.
    Returns (long_mask, short_mask, long_label_idx, short_label_idx) — bool arrays + int8
    arrays where each non-zero value maps to a setup-type name via _RESTRICTED_LABELS.

    Computed ONCE at top of BTC sim. Replaces the per-bar _g() mmap reads — should drop
    restricted-mode runtime from ~20min/variant to <60s.
    """
    htf_swing = bool(getattr(cfg, 'BTC_HTF_REVERSAL_SWING_ENABLED', False))
    if not bool(getattr(cfg, 'BTC_RESTRICTED_ENTRY_MODE_ENABLED', False)) and not htf_swing:
        return None, None, None, None
    # When HTF_REVERSAL_SWING is enabled standalone, force-enable the HTF setup-detection gates
    # so the masks include 4h/D signals even if RESTRICTED_ENTRY_MODE is off.
    if htf_swing:
        for k in ('BTC_RESTRICTED_WT_15M_PLUS_BOUNCE',
                  'BTC_RESTRICTED_DC_15M_PLUS_TOUCH',
                  'BTC_RESTRICTED_DC_15M_PLUS_BREAKOUT',
                  'BTC_RESTRICTED_STDEV_BREAKOUT',
                  'BTC_RESTRICTED_STDEV_BOUNCE'):
            if not bool(getattr(cfg, k, False)):
                setattr(cfg, k, True)
    files = npz.files if hasattr(npz, "files") else set()
    def _arr(field, default=0.0):
        if field not in files:
            return np.full(n, default, dtype=np.float32)
        a = np.asarray(npz[field], dtype=np.float32)
        if len(a) < n:
            return np.pad(a, (0, n - len(a)), constant_values=default)
        return a[:n]
    k_lo = float(getattr(cfg, 'BTC_RESTRICTED_K_EXTREME_LO_THRESHOLD', 20.0))
    k_hi = float(getattr(cfg, 'BTC_RESTRICTED_K_EXTREME_HI_THRESHOLD', 80.0))
    wt_neg = float(getattr(cfg, 'BTC_RESTRICTED_WT_EXTREME_NEG', -50.0))
    wt_pos = float(getattr(cfg, 'BTC_RESTRICTED_WT_EXTREME_POS', 50.0))
    long_mask = np.zeros(n, dtype=bool)
    short_mask = np.zeros(n, dtype=bool)
    # Label codes: 0=none, 1=K_15M, 2=WT_15M, 3=WT_1H, 4=WT_4H, 5=WT_D, 6=DC_15M_TOUCH, 7=DC_1H_TOUCH,
    # 8=DC_4H_TOUCH, 9=DC_D_TOUCH, 10=DC_15M_BREAK, 11=DC_1H_BREAK, 12=DC_4H_BREAK, 13=DC_D_BREAK,
    # 14=STDEV_15M_BR, 15=STDEV_1H_BR, 16=STDEV_4H_BR, 17=STDEV_D_BR,
    # 18=STDEV_15M_BO, 19=STDEV_1H_BO, 20=STDEV_4H_BO, 21=STDEV_D_BO
    long_label = np.zeros(n, dtype=np.int8)
    short_label = np.zeros(n, dtype=np.int8)

    # 1. K_15M extreme + bounce
    if bool(getattr(cfg, 'BTC_RESTRICTED_K_15M_EXTREME_BOUNCE', False)):
        k = _arr('stoch_k_15m', 50.0)
        k_p = np.empty_like(k); k_p[1:] = k[:-1]; k_p[0] = k[0]
        long_hit = (k_p <= k_lo) & (k > k_p)
        short_hit = (k_p >= k_hi) & (k < k_p)
        new = long_hit & ~long_mask
        long_mask |= long_hit; long_label[new] = 1
        new = short_hit & ~short_mask
        short_mask |= short_hit; short_label[new] = 1

    # 2. WT 15m+ bounce — multi-TF, first match wins
    if bool(getattr(cfg, 'BTC_RESTRICTED_WT_15M_PLUS_BOUNCE', False)):
        _wt_tf_filter = getattr(cfg, 'BTC_RESTRICTED_WT_TFS', None)
        for tf, code in (('15m', 2), ('1h', 3), ('4h', 4), ('D', 5)):
            if _wt_tf_filter and tf not in _wt_tf_filter: continue
            w1 = _arr(f'wt1_{tf}', 0.0); w2 = _arr(f'wt2_{tf}', 0.0)
            w1p = np.empty_like(w1); w1p[1:] = w1[:-1]; w1p[0] = w1[0]
            w2p = np.empty_like(w2); w2p[1:] = w2[:-1]; w2p[0] = w2[0]
            long_hit = (w1p < w2p) & (w1 > w2) & (w1p < wt_neg) & ~long_mask
            short_hit = (w1p > w2p) & (w1 < w2) & (w1p > wt_pos) & ~short_mask
            long_mask |= long_hit; long_label[long_hit] = code
            short_mask |= short_hit; short_label[short_hit] = code

    # 3. DC touch (rejection: close crosses back inside band)
    if bool(getattr(cfg, 'BTC_RESTRICTED_DC_15M_PLUS_TOUCH', False)):
        c = _arr('close_3m', 0.0)
        c_p = np.empty_like(c); c_p[1:] = c[:-1]; c_p[0] = c[0]
        _dct_tf_filter = getattr(cfg, 'BTC_RESTRICTED_DC_TOUCH_TFS', None)
        for tf, code in (('15m', 6), ('1h', 7), ('4h', 8), ('D', 9)):
            if _dct_tf_filter and tf not in _dct_tf_filter: continue
            dc_lo = _arr(f'dc_low_{tf}', 0.0)
            dc_hi = _arr(f'dc_high_{tf}', 0.0)
            long_hit = (dc_lo > 0) & (c_p <= dc_lo) & (c > dc_lo) & ~long_mask
            short_hit = (dc_hi > 0) & (c_p >= dc_hi) & (c < dc_hi) & ~short_mask
            long_mask |= long_hit; long_label[long_hit] = code
            short_mask |= short_hit; short_label[short_hit] = code

    # 4. DC breakout (close crosses outside prev period channel)
    if bool(getattr(cfg, 'BTC_RESTRICTED_DC_15M_PLUS_BREAKOUT', False)):
        c = _arr('close_3m', 0.0)
        c_p = np.empty_like(c); c_p[1:] = c[:-1]; c_p[0] = c[0]
        _dcb_tf_filter = getattr(cfg, 'BTC_RESTRICTED_DC_BREAKOUT_TFS', None)
        for tf, code in (('15m', 10), ('1h', 11), ('4h', 12), ('D', 13)):
            if _dcb_tf_filter and tf not in _dcb_tf_filter: continue
            dc_hi = _arr(f'dc_high_{tf}', 0.0)
            dc_lo = _arr(f'dc_low_{tf}', 0.0)
            dc_hi_p = np.empty_like(dc_hi); dc_hi_p[1:] = dc_hi[:-1]; dc_hi_p[0] = dc_hi[0]
            dc_lo_p = np.empty_like(dc_lo); dc_lo_p[1:] = dc_lo[:-1]; dc_lo_p[0] = dc_lo[0]
            long_hit = (dc_hi_p > 0) & (c_p <= dc_hi_p) & (c > dc_hi_p) & ~long_mask
            short_hit = (dc_lo_p > 0) & (c_p >= dc_lo_p) & (c < dc_lo_p) & ~short_mask
            long_mask |= long_hit; long_label[long_hit] = code
            short_mask |= short_hit; short_label[short_hit] = code

    # 5. StDev (BB) breakouts
    if bool(getattr(cfg, 'BTC_RESTRICTED_STDEV_BREAKOUT', False)):
        c = _arr('close_3m', 0.0)
        _sdb_tf_filter = getattr(cfg, 'BTC_RESTRICTED_STDEV_BREAKOUT_TFS', None)
        for tf, code in (('15m', 14), ('1h', 15), ('4h', 16), ('D', 17)):
            if _sdb_tf_filter and tf not in _sdb_tf_filter: continue
            bb_up = _arr(f'bb_upper_{tf}', 0.0)
            bb_lo = _arr(f'bb_lower_{tf}', 0.0)
            long_hit = (bb_up > 0) & (c > bb_up) & ~long_mask
            short_hit = (bb_lo > 0) & (c < bb_lo) & ~short_mask
            long_mask |= long_hit; long_label[long_hit] = code
            short_mask |= short_hit; short_label[short_hit] = code

    # 6. StDev bounces (close crosses back inside)
    if bool(getattr(cfg, 'BTC_RESTRICTED_STDEV_BOUNCE', False)):
        c = _arr('close_3m', 0.0)
        c_p = np.empty_like(c); c_p[1:] = c[:-1]; c_p[0] = c[0]
        _sdo_tf_filter = getattr(cfg, 'BTC_RESTRICTED_STDEV_BOUNCE_TFS', None)
        for tf, code in (('15m', 18), ('1h', 19), ('4h', 20), ('D', 21)):
            if _sdo_tf_filter and tf not in _sdo_tf_filter: continue
            bb_up = _arr(f'bb_upper_{tf}', 0.0)
            bb_lo = _arr(f'bb_lower_{tf}', 0.0)
            long_hit = (bb_lo > 0) & (c_p <= bb_lo) & (c > bb_lo) & ~long_mask
            short_hit = (bb_up > 0) & (c_p >= bb_up) & (c < bb_up) & ~short_mask
            long_mask |= long_hit; long_label[long_hit] = code
            short_mask |= short_hit; short_label[short_hit] = code

    # HA confirmation filter (vectorized)
    if bool(getattr(cfg, 'BTC_RESTRICTED_HA_CONFIRM_ENABLED', False)):
        tf = str(getattr(cfg, 'BTC_RESTRICTED_HA_CONFIRM_TF', '3m'))
        require_two = bool(getattr(cfg, 'BTC_RESTRICTED_HA_REQUIRE_TWO_BARS', False))
        ha = _arr(f'ha_{tf}', 0.0)
        if require_two:
            ha_p = np.empty_like(ha); ha_p[1:] = ha[:-1]; ha_p[0] = ha[0]
            ha_long_ok = (ha > 0) & (ha_p > 0)
            ha_short_ok = (ha < 0) & (ha_p < 0)
        else:
            ha_long_ok = ha > 0
            ha_short_ok = ha < 0
        long_mask &= ha_long_ok
        short_mask &= ha_short_ok
        long_label[~long_mask] = 0
        short_label[~short_mask] = 0

    return long_mask, short_mask, long_label, short_label


_HTF_LABEL_CODES = frozenset({4, 5, 8, 9, 12, 13, 16, 17, 20, 21})  # 4h or D signals


_RESTRICTED_LABEL_NAMES = {
    1: "K_15M_EXTREME_BOUNCE",
    2: "WT_15M_BULL_CROSS_FROM_EXTR", 3: "WT_1H_BULL_CROSS_FROM_EXTR",
    4: "WT_4H_BULL_CROSS_FROM_EXTR", 5: "WT_D_BULL_CROSS_FROM_EXTR",
    6: "DC_15M_TOUCH", 7: "DC_1H_TOUCH", 8: "DC_4H_TOUCH", 9: "DC_D_TOUCH",
    10: "DC_15M_BREAKOUT", 11: "DC_1H_BREAKOUT", 12: "DC_4H_BREAKOUT", 13: "DC_D_BREAKOUT",
    14: "STDEV_15M_BREAKOUT", 15: "STDEV_1H_BREAKOUT",
    16: "STDEV_4H_BREAKOUT", 17: "STDEV_D_BREAKOUT",
    18: "STDEV_15M_BOUNCE", 19: "STDEV_1H_BOUNCE",
    20: "STDEV_4H_BOUNCE", 21: "STDEV_D_BOUNCE",
}


def _btc_restricted_setup_check(npz, i, cfg):
    """When BTC_RESTRICTED_ENTRY_MODE_ENABLED, return ([long_setups], [short_setups]) at bar i.
    Each setup is a string label (e.g. 'K_15M_EXTREME_BOUNCE_LONG') used as entry_reason.
    DEPRECATED: use _btc_restricted_setup_vec for performance. Kept for compat.
    """
    if not bool(getattr(cfg, 'BTC_RESTRICTED_ENTRY_MODE_ENABLED', False)):
        return [], []
    if i < 1:
        return [], []
    longs, shorts = [], []
    pi = i - 1
    files = npz.files if hasattr(npz, "files") else set()
    def _g(field, idx, default=0.0):
        if field not in files: return default
        try:
            v = npz[field][idx]
            return float(v.item()) if hasattr(v, 'item') else float(v)
        except Exception: return default
    # HA confirmation pre-filter — applied AFTER setup matching to suppress non-confirmed sides.
    # NPZ stores HA direction as 1.0 (green/up) / -1.0 (red/down) / 0.0 (doji).
    ha_long_ok = True
    ha_short_ok = True
    if bool(getattr(cfg, 'BTC_RESTRICTED_HA_CONFIRM_ENABLED', False)):
        tf = str(getattr(cfg, 'BTC_RESTRICTED_HA_CONFIRM_TF', '3m'))
        require_two = bool(getattr(cfg, 'BTC_RESTRICTED_HA_REQUIRE_TWO_BARS', False))
        ha_now = _g(f'ha_{tf}', i, 0.0)
        ha_prev = _g(f'ha_{tf}', pi, 0.0)
        if require_two:
            ha_long_ok = (ha_now > 0 and ha_prev > 0)
            ha_short_ok = (ha_now < 0 and ha_prev < 0)
        else:
            ha_long_ok = (ha_now > 0)
            ha_short_ok = (ha_now < 0)
    k_lo = float(getattr(cfg, 'BTC_RESTRICTED_K_EXTREME_LO_THRESHOLD', 20.0))
    k_hi = float(getattr(cfg, 'BTC_RESTRICTED_K_EXTREME_HI_THRESHOLD', 80.0))
    wt_neg = float(getattr(cfg, 'BTC_RESTRICTED_WT_EXTREME_NEG', -50.0))
    wt_pos = float(getattr(cfg, 'BTC_RESTRICTED_WT_EXTREME_POS', 50.0))
    # 1. K_15M extreme + bounce
    if bool(getattr(cfg, 'BTC_RESTRICTED_K_15M_EXTREME_BOUNCE', False)):
        k_now = _g('stoch_k_15m', i, 50)
        k_prev = _g('stoch_k_15m', pi, 50)
        if k_prev <= k_lo and k_now > k_prev:
            longs.append('K_15M_EXTREME_BOUNCE_LONG')
        if k_prev >= k_hi and k_now < k_prev:
            shorts.append('K_15M_EXTREME_BOUNCE_SHORT')
    # 2. WT 15m+ bounce: cross + extreme zone
    if bool(getattr(cfg, 'BTC_RESTRICTED_WT_15M_PLUS_BOUNCE', False)):
        for tf in ('15m', '1h', '4h', 'D'):
            w1 = _g(f'wt1_{tf}', i, 0); w2 = _g(f'wt2_{tf}', i, 0)
            w1p = _g(f'wt1_{tf}', pi, 0); w2p = _g(f'wt2_{tf}', pi, 0)
            # Bull cross from extreme negative
            if w1p < w2p and w1 > w2 and w1p < wt_neg:
                longs.append(f'WT_{tf}_BULL_CROSS_FROM_EXTR')
                break
        for tf in ('15m', '1h', '4h', 'D'):
            w1 = _g(f'wt1_{tf}', i, 0); w2 = _g(f'wt2_{tf}', i, 0)
            w1p = _g(f'wt1_{tf}', pi, 0); w2p = _g(f'wt2_{tf}', pi, 0)
            # Bear cross from extreme positive
            if w1p > w2p and w1 < w2 and w1p > wt_pos:
                shorts.append(f'WT_{tf}_BEAR_CROSS_FROM_EXTR')
                break
    # 3. DC 15m+ touches (rejection: close crosses back inside)
    if bool(getattr(cfg, 'BTC_RESTRICTED_DC_15M_PLUS_TOUCH', False)):
        c_now = _g('close_3m', i, 0); c_prev = _g('close_3m', pi, 0)
        for tf in ('15m', '1h', '4h', 'D'):
            dc_lo = _g(f'dc_low_{tf}', i, 0)
            if dc_lo > 0 and c_prev <= dc_lo and c_now > dc_lo:
                longs.append(f'DC_{tf}_LOW_BOUNCE'); break
        for tf in ('15m', '1h', '4h', 'D'):
            dc_hi = _g(f'dc_high_{tf}', i, 0)
            if dc_hi > 0 and c_prev >= dc_hi and c_now < dc_hi:
                shorts.append(f'DC_{tf}_HIGH_REJECT'); break
    # 4. DC 15m+ breakouts (close crosses outside prev period channel)
    if bool(getattr(cfg, 'BTC_RESTRICTED_DC_15M_PLUS_BREAKOUT', False)):
        c_now = _g('close_3m', i, 0); c_prev = _g('close_3m', pi, 0)
        for tf in ('15m', '1h', '4h', 'D'):
            dc_hi_prev = _g(f'dc_high_{tf}', pi, 0)
            if dc_hi_prev > 0 and c_prev <= dc_hi_prev and c_now > dc_hi_prev:
                longs.append(f'DC_{tf}_HIGH_BREAKOUT'); break
        for tf in ('15m', '1h', '4h', 'D'):
            dc_lo_prev = _g(f'dc_low_{tf}', pi, 0)
            if dc_lo_prev > 0 and c_prev >= dc_lo_prev and c_now < dc_lo_prev:
                shorts.append(f'DC_{tf}_LOW_BREAKDOWN'); break
    # 5. StDev (BB) breakouts
    if bool(getattr(cfg, 'BTC_RESTRICTED_STDEV_BREAKOUT', False)):
        c_now = _g('close_3m', i, 0)
        for tf in ('15m', '1h', '4h', 'D'):
            bb_up = _g(f'bb_upper_{tf}', i, 0)
            if bb_up > 0 and c_now > bb_up:
                longs.append(f'STDEV_{tf}_BREAKOUT_UPPER'); break
        for tf in ('15m', '1h', '4h', 'D'):
            bb_lo = _g(f'bb_lower_{tf}', i, 0)
            if bb_lo > 0 and c_now < bb_lo:
                shorts.append(f'STDEV_{tf}_BREAKOUT_LOWER'); break
    # 6. StDev (BB) bounces (close crosses back inside)
    if bool(getattr(cfg, 'BTC_RESTRICTED_STDEV_BOUNCE', False)):
        c_now = _g('close_3m', i, 0); c_prev = _g('close_3m', pi, 0)
        for tf in ('15m', '1h', '4h', 'D'):
            bb_lo = _g(f'bb_lower_{tf}', i, 0)
            if bb_lo > 0 and c_prev <= bb_lo and c_now > bb_lo:
                longs.append(f'STDEV_{tf}_LOWER_BOUNCE'); break
        for tf in ('15m', '1h', '4h', 'D'):
            bb_up = _g(f'bb_upper_{tf}', i, 0)
            if bb_up > 0 and c_prev >= bb_up and c_now < bb_up:
                shorts.append(f'STDEV_{tf}_UPPER_REJECT'); break
    # Apply HA confirmation: suppress LONG if HA red, SHORT if HA green
    if not ha_long_ok: longs = []
    if not ha_short_ok: shorts = []
    return longs, shorts


def _btc_dedicated_simulate_per_sym(npz, cfg, n: int, _ltf: str,
                                     _bph_15m: int, _bph_1h: int, _bph_4h: int, _bph_D: int,
                                     _trade_recorder=None, _symbol: str = ""):
    """BTC-dedicated per-symbol Tier-1 backtest (Path B: technical exit + guaranteed reentry).

    Called from simulate() when cfg.BTC_DEDICATED_ENABLED=True and symbol is BTC.
    Imports btc_loop and uses the SAME decision functions live + paper will use.
    Returns sym_pnl list (per-trade %returns) compatible with simulate()'s aggregator.

    Path A (hedge) NOT implemented in vec engine — hedge requires portfolio-level
    state that v8_quick can't track. Path A validated in Tier-2 only.

    `_trade_recorder`: optional list to receive per-trade dicts for chart visualization.
    """
    import btc_loop as _btc

    close = _safe(npz, f'close_{_ltf}', n)
    high = _safe(npz, f'high_{_ltf}', n)
    low = _safe(npz, f'low_{_ltf}', n)

    # Multi-TF accel inputs (velocity + prior-bar velocity for accel-ramp detection)
    tfs = ['3m', '15m', '1h', '4h', 'D']
    vel = {tf: _safe(npz, f'wt_velocity_{tf}', n) for tf in tfs}
    # prior-bar velocity = roll forward 1 bar (live-realistic: at bar i, you only know vel up to i-1)
    vel_prev = {tf: np.roll(vel[tf], 1) for tf in tfs}
    for tf in tfs:
        vel_prev[tf][0] = vel[tf][0]

    # WT against-side count for exit
    wt1 = {tf: _safe(npz, f'wt1_{tf}', n) for tf in tfs}
    wt2 = {tf: _safe(npz, f'wt2_{tf}', n) for tf in tfs}

    # Multi-indicator divergence inputs
    rsi = {tf: _safe(npz, f'rsi_{tf}', n, 50.0) for tf in tfs}
    mfi = {tf: _safe(npz, f'mfi_{tf}', n, 50.0) for tf in tfs}

    # DC zone proxy for wt_dc_zone hint (simplified)
    bb_pct_b_4h = _safe(npz, 'bb_pct_b_4h', n, 0.5)

    # 4h/D high/low arrays for fib swings (resampled in NPZ to LTF timestamps)
    h4 = _safe(npz, 'high_4h', n)
    l4 = _safe(npz, 'low_4h', n)
    hD = _safe(npz, 'high_D', n)
    lD = _safe(npz, 'low_D', n)
    # Precompute rolling max/min for fib swing — vectorized O(N) instead of per-bar O(N*W)
    from numpy.lib.stride_tricks import sliding_window_view as _swv
    def _rolling_extreme(a, w, fn):
        if w <= 1 or w > len(a):
            return None
        # First w-1 entries use cumulative; rest use stride window
        try:
            wins = _swv(a, w)   # shape (n-w+1, w)
            ext = fn(wins, axis=-1)
            # Pad first w-1 entries with cumulative extreme
            pre = fn.accumulate(a[:w-1])
            return np.concatenate([pre, ext])
        except Exception:
            return None

    # Config knobs
    rz_use_fib = bool(getattr(cfg, 'BTC_RZ_USE_FIB', True))
    rz_use_round = bool(getattr(cfg, 'BTC_RZ_USE_ROUND', True))
    rz_use_wt_dc = bool(getattr(cfg, 'BTC_RZ_USE_WT_DC', True))
    rz_proximity_pct = float(getattr(cfg, 'BTC_RZ_PROXIMITY_PCT', 0.5))
    accel_min_tfs = int(getattr(cfg, 'BTC_ACCEL_RAMP_MIN_TFS', 5))
    div_min_inds = max(int(getattr(cfg, 'BTC_DIVERGENCE_BEAR_MIN_INDS', 2)),
                       int(getattr(cfg, 'BTC_DIVERGENCE_BULL_MIN_INDS', 2)))
    div_lb = int(getattr(cfg, 'BTC_DIVERGENCE_LOOKBACK_BARS', 5))
    tech_exit_min_tfs = int(getattr(cfg, 'BTC_TECH_EXIT_WT_MIN_TFS', 3))
    reentry_enabled = bool(getattr(cfg, 'BTC_GUARANTEED_REENTRY_ENABLED', True))
    reentry_max_age = int(getattr(cfg, 'BTC_GUARANTEED_REENTRY_MAX_AGE_BARS', 480))
    reentry_min_gap = int(getattr(cfg, 'BTC_GUARANTEED_REENTRY_MIN_GAP_BARS', 5))

    # Compute hard-loss pct from $10 / ($90 * 20x) = 0.555% effective adverse on price
    own_max = float(getattr(cfg, 'BTC_PER_TRADE_NOTIONAL_USD_MAX', 90.0))
    leverage = float(getattr(cfg, 'BTC_LEVERAGE', 20.0))
    hard_loss_usd = float(getattr(cfg, 'BTC_HARD_LOSS_USD_PER_TRADE', 10.0))
    if own_max > 0 and leverage > 0:
        hard_loss_pct = -(hard_loss_usd / (own_max * leverage)) * 100.0  # e.g. -0.555%
    else:
        hard_loss_pct = -0.555

    # Lookback bars per TF (in LTF bars) for fib swing
    fib_lb_4h_at_tf = int(getattr(cfg, 'BTC_FIB_LOOKBACK_4H', 200))
    fib_lb_D_at_tf = int(getattr(cfg, 'BTC_FIB_LOOKBACK_D', 180))
    fib_lb_4h_in_ltf = fib_lb_4h_at_tf * _bph_4h
    fib_lb_D_in_ltf = fib_lb_D_at_tf * _bph_D
    # Vectorize rolling max/min for fib lookback windows
    h4_rmax = _rolling_extreme(h4, fib_lb_4h_in_ltf, np.maximum) if rz_use_fib else None
    l4_rmin = _rolling_extreme(l4, fib_lb_4h_in_ltf, np.minimum) if rz_use_fib else None
    hD_rmax = _rolling_extreme(hD, fib_lb_D_in_ltf, np.maximum) if rz_use_fib else None
    lD_rmin = _rolling_extreme(lD, fib_lb_D_in_ltf, np.minimum) if rz_use_fib else None
    # Cooldown / min hold (BTC-specific so the loop doesn't over-trade like the smoke run)
    btc_cooldown_bars = int(getattr(cfg, 'BTC_COOLDOWN_BARS', 5))
    btc_min_hold_bars = int(getattr(cfg, 'BTC_MIN_HOLD_BARS', 5))
    # Breakout-mode pacing (tighter — 2026-04-27 add)
    btc_breakout_enabled = bool(getattr(cfg, 'BTC_BREAKOUT_ENTRY_ENABLED', True))
    btc_breakout_min_hold = int(getattr(cfg, 'BTC_BREAKOUT_MIN_HOLD_BARS', 3))
    btc_breakout_cooldown = int(getattr(cfg, 'BTC_BREAKOUT_COOLDOWN_BARS', 3))
    btc_breakout_reentry_on_exit = bool(getattr(cfg, 'BTC_BREAKOUT_REENTRY_ON_EXIT', True))
    btc_follow_through_enabled = bool(getattr(cfg, 'BTC_FOLLOW_THROUGH_REENTRY_ENABLED', True))
    btc_follow_through_min_pct = float(getattr(cfg, 'BTC_FOLLOW_THROUGH_MIN_MOVE_PCT', 0.3))
    # Breakout-specific hard-loss in pct
    bk_loss_usd = float(getattr(cfg, 'BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE', hard_loss_usd / 2.0))
    if own_max > 0 and leverage > 0:
        breakout_hard_loss_pct = -(bk_loss_usd / (own_max * leverage)) * 100.0
    else:
        breakout_hard_loss_pct = hard_loss_pct / 2.0

    # DC channels on 3m for breakout detection (current bar's channel includes
    # current bar's high/low — must compare to PREV bar's value to avoid lookahead).
    dc_high_3m_raw = _safe(npz, 'dc_high_3m', n)
    dc_low_3m_raw = _safe(npz, 'dc_low_3m', n)
    dc_high_3m_prev = np.roll(dc_high_3m_raw, 1); dc_high_3m_prev[0] = dc_high_3m_raw[0]
    dc_low_3m_prev = np.roll(dc_low_3m_raw, 1); dc_low_3m_prev[0] = dc_low_3m_raw[0]
    # 4-bar DC channel for tighter breakout exits
    dc_high4_3m_raw = _safe(npz, 'dc_high4_3m', n)
    dc_low4_3m_raw = _safe(npz, 'dc_low4_3m', n)
    dc_high4_3m_prev = np.roll(dc_high4_3m_raw, 1); dc_high4_3m_prev[0] = dc_high4_3m_raw[0]
    dc_low4_3m_prev = np.roll(dc_low4_3m_raw, 1); dc_low4_3m_prev[0] = dc_low4_3m_raw[0]
    # 3m WT for single-TF flip detection (BREAKOUT exits)
    wt1_3m = _safe(npz, 'wt1_3m', n)
    wt2_3m = _safe(npz, 'wt2_3m', n)
    # R2 — WT_15M_VEL_SLOW peak-then-collapse exit (mirror live ez_manage).
    # 2026-05-09 spec: max_pnl ≥ peak_min AND floor ≤ pnl ≤ band AND
    # wt_vel_15m opposing AND (|vel|<|vel_prev|*decel_ratio OR |vel|≤near_zero).
    wt_vel_15m_arr = _safe(npz, 'wt_velocity_15m', n, 0.0)
    wt_vel_15m_prev_arr = np.roll(wt_vel_15m_arr, 1)
    wt_vel_15m_prev_arr[0] = wt_vel_15m_arr[0]
    _wzg_enabled = bool(getattr(cfg, 'WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED', True))
    _wzg_peak_min = float(getattr(cfg, 'R2_PEAK_MIN_PCT', 0.5))
    _wzg_band = float(getattr(cfg, 'WT_15M_VEL_SLOW_GAIN_BAND_PCT', 0.10))
    _wzg_floor = float(getattr(cfg, 'WT_15M_VEL_SLOW_GAIN_FLOOR_PCT', 0.01))
    _wzg_near_zero = float(getattr(cfg, 'WT_15M_VEL_NEAR_ZERO_THRESHOLD', 0.1))
    _wzg_decel_ratio = float(getattr(cfg, 'WT_VEL_DECEL_RATIO', 0.5))
    _wzg_decel_only = bool(getattr(cfg, 'WT_VEL_USE_DECEL_RATIO_ONLY', True))

    # ── Funding rate / OI / Multi-factor wt_dc zone fields (2026-04-28 wiring) ──
    funding_rate_arr = _safe(npz, f'funding_rate_{_ltf}', n, 0.0)
    oi_arr = _safe(npz, f'oi_{_ltf}', n, 0.0)
    bb_pct_b_1h_arr = _safe(npz, 'bb_pct_b_1h', n, 0.5)
    dc_pos_1h_arr = _safe(npz, 'dc_position_1h', n, 0.5)
    dc_pos_4h_arr = _safe(npz, 'dc_position_4h', n, 0.5)
    stoch_k_3m_arr = _safe(npz, 'stoch_k_3m', n, 50.0)
    stoch_k_1h_arr = _safe(npz, 'stoch_k_1h', n, 50.0)
    stoch_k_4h_arr = _safe(npz, 'stoch_k_4h', n, 50.0)
    mfi_1h_arr = _safe(npz, 'mfi_1h', n, 50.0)
    mfi_4h_arr = _safe(npz, 'mfi_4h', n, 50.0)
    # ── HTF direction anchor (2026-04-29) — block opposite-side flips within same HTF candle.
    _anchor_tf = str(getattr(cfg, 'BTC_HTF_DIRECTION_ANCHOR_TF', 'off')).lower()
    _anchor_bars_per_candle = {'15m': _bph_15m, '1h': _bph_1h, '4h': _bph_4h, 'd': _bph_D}.get(_anchor_tf, 0)
    _anchor_enabled = _anchor_bars_per_candle > 0
    # Track the HTF-candle index of the most recent entry, and which side it took.
    _last_entry_htf_candle = -1
    _last_entry_side_in_candle = "NONE"

    # Vectorized restricted-mode setup detection — runs ONCE here instead of per-bar.
    # Returns None if restricted-mode disabled.
    _restr_long_mask, _restr_short_mask, _restr_long_label, _restr_short_label = \
        _btc_restricted_setup_vec(npz, n, cfg)
    _restr_enabled = _restr_long_mask is not None

    # ── HTF-reversal swing CATEGORY MASKS (2026-04-29) ────────────────────
    # Three category arrays per side: WT-cross, DC-touch/breakout, STDEV-breakout/bounce.
    # Used to require N-of-3 agreement before firing a swing flip.
    # Computed only when HTF_REVERSAL_SWING_ENABLED — independent of restricted-entry-mode masks.
    _htf_swing_enabled = bool(getattr(cfg, 'BTC_HTF_REVERSAL_SWING_ENABLED', False))
    _htf_long_categories = None
    _htf_short_categories = None
    if _htf_swing_enabled:
        _files = npz.files if hasattr(npz, "files") else set()
        def _harr(field, default=0.0):
            if field not in _files:
                return np.full(n, default, dtype=np.float32)
            a = np.asarray(npz[field], dtype=np.float32)
            if len(a) < n: return np.pad(a, (0, n - len(a)), constant_values=default)
            return a[:n]
        _wt_neg = float(getattr(cfg, 'BTC_RESTRICTED_WT_EXTREME_NEG', -50.0))
        _wt_pos = float(getattr(cfg, 'BTC_RESTRICTED_WT_EXTREME_POS', 50.0))
        _min_tf = int(getattr(cfg, 'BTC_HTF_REVERSAL_SWING_MIN_TF_PER_CATEGORY', 1))
        _tfs = ('15m', '1h', '4h', 'D')
        # Per-category PER-TF firing arrays — count how many TFs in each category align.
        # WT cross category
        _htf_wt_long_count = np.zeros(n, dtype=np.int8); _htf_wt_short_count = np.zeros(n, dtype=np.int8)
        for tf in _tfs:
            w1 = _harr(f'wt1_{tf}', 0.0); w2 = _harr(f'wt2_{tf}', 0.0)
            w1p = np.empty_like(w1); w1p[1:] = w1[:-1]; w1p[0] = w1[0]
            w2p = np.empty_like(w2); w2p[1:] = w2[:-1]; w2p[0] = w2[0]
            _htf_wt_long_count  += ((w1p < w2p) & (w1 > w2) & (w1p < _wt_neg)).astype(np.int8)
            _htf_wt_short_count += ((w1p > w2p) & (w1 < w2) & (w1p > _wt_pos)).astype(np.int8)
        # DC category (touch rejection OR breakout)
        _close_3m_h = _harr('close_3m', 0.0)
        _c_p = np.empty_like(_close_3m_h); _c_p[1:] = _close_3m_h[:-1]; _c_p[0] = _close_3m_h[0]
        _htf_dc_long_count = np.zeros(n, dtype=np.int8); _htf_dc_short_count = np.zeros(n, dtype=np.int8)
        for tf in _tfs:
            dc_lo = _harr(f'dc_low_{tf}', 0.0); dc_hi = _harr(f'dc_high_{tf}', 0.0)
            dc_hi_p = np.empty_like(dc_hi); dc_hi_p[1:] = dc_hi[:-1]; dc_hi_p[0] = dc_hi[0]
            dc_lo_p = np.empty_like(dc_lo); dc_lo_p[1:] = dc_lo[:-1]; dc_lo_p[0] = dc_lo[0]
            tf_long_fire = (((dc_lo > 0) & (_c_p <= dc_lo) & (_close_3m_h > dc_lo))
                            | ((dc_hi_p > 0) & (_c_p <= dc_hi_p) & (_close_3m_h > dc_hi_p)))
            tf_short_fire = (((dc_hi > 0) & (_c_p >= dc_hi) & (_close_3m_h < dc_hi))
                             | ((dc_lo_p > 0) & (_c_p >= dc_lo_p) & (_close_3m_h < dc_lo_p)))
            _htf_dc_long_count  += tf_long_fire.astype(np.int8)
            _htf_dc_short_count += tf_short_fire.astype(np.int8)
        # STDEV category (BB band breakout OR bounce)
        _htf_st_long_count = np.zeros(n, dtype=np.int8); _htf_st_short_count = np.zeros(n, dtype=np.int8)
        for tf in _tfs:
            bb_up = _harr(f'bb_upper_{tf}', 0.0); bb_lo = _harr(f'bb_lower_{tf}', 0.0)
            tf_long_fire = (((bb_up > 0) & (_close_3m_h > bb_up))
                            | ((bb_lo > 0) & (_c_p <= bb_lo) & (_close_3m_h > bb_lo)))
            tf_short_fire = (((bb_lo > 0) & (_close_3m_h < bb_lo))
                             | ((bb_up > 0) & (_c_p >= bb_up) & (_close_3m_h < bb_up)))
            _htf_st_long_count  += tf_long_fire.astype(np.int8)
            _htf_st_short_count += tf_short_fire.astype(np.int8)
        # A category "fires" iff at least _min_tf of its 4 TFs aligned this bar.
        # Then category-firing array is sum across the 3 categories (0..3).
        _htf_long_categories = ((_htf_wt_long_count >= _min_tf).astype(np.int8)
                                + (_htf_dc_long_count >= _min_tf).astype(np.int8)
                                + (_htf_st_long_count >= _min_tf).astype(np.int8))
        _htf_short_categories = ((_htf_wt_short_count >= _min_tf).astype(np.int8)
                                 + (_htf_dc_short_count >= _min_tf).astype(np.int8)
                                 + (_htf_st_short_count >= _min_tf).astype(np.int8))
    # Funding gate config
    funding_gate_enabled = bool(getattr(cfg, 'FUNDING_GATE_ENABLED', False))
    funding_long_max = float(getattr(cfg, 'FUNDING_GATE_LONG_MAX', 0.0005))
    funding_short_min = float(getattr(cfg, 'FUNDING_GATE_SHORT_MIN', -0.0005))
    # OI gate config
    oi_gate_enabled = bool(getattr(cfg, 'OI_CONFIRM_ENABLED', False))
    oi_min_change_pct = float(getattr(cfg, 'OI_CONFIRM_MIN_CHANGE_PCT', 0.5))
    oi_min_price_pct = float(getattr(cfg, 'OI_CONFIRM_MIN_PRICE_PCT', 0.3))
    # Multi-factor wt_dc switch (defaults to enriched mode)
    wt_dc_multifactor = bool(getattr(cfg, 'BTC_RZ_WT_DC_MULTIFACTOR', True))
    # Pre-compute 1h-prior OI and price for OI 4-quadrant detection (~20 bars at 3m = 1h)
    _1h_in_ltf = _bph_1h
    oi_prev_arr = np.roll(oi_arr, _1h_in_ltf); oi_prev_arr[:_1h_in_ltf] = oi_arr[:_1h_in_ltf]
    close_prev_1h_arr = np.roll(close, _1h_in_ltf); close_prev_1h_arr[:_1h_in_ltf] = close[:_1h_in_ltf]

    # Round increments
    round_primary = float(getattr(cfg, 'BTC_ROUND_INC_PRIMARY_USD', 5000.0))
    round_secondary = float(getattr(cfg, 'BTC_ROUND_INC_SECONDARY_USD', 1000.0))
    round_bands = int(getattr(cfg, 'BTC_ROUND_BANDS_EACH_SIDE', 8))

    sym_pnl = []
    position = "FLAT"            # FLAT | LONG | SHORT
    entry_price = 0.0
    entry_bar = 0
    entry_type = "BOUNCE"        # BOUNCE | BREAKOUT — affects exit cluster
    entry_reason = ""
    entry_ts = 0
    # Peak gain tracker for R2 (peak-then-collapse) — resets each new entry by
    # comparing entry_bar; see use site below near should_exit_btc.
    max_pnl_pct = 0.0
    _max_pnl_entry_bar = -1
    _ts_arr = npz.get('timestamps', None) if hasattr(npz, 'get') else None
    if _ts_arr is None:
        try: _ts_arr = npz['timestamps']
        except Exception: _ts_arr = None
    last_exit_side = "NONE"
    last_exit_bar = -10**9
    last_exit_price = 0.0
    last_exit_type = "NONE"      # to know which cooldown to apply for reentry
    # Same-symbol hedge state (2026-04-28). Hedge runs alongside primary; opens when primary
    # in loss + technicals turn against; closes on WT 3m+1h flip in hedge's favor + nonneg gain.
    hedge_side = "FLAT"          # FLAT | LONG | SHORT
    hedge_entry_price = 0.0
    hedge_entry_bar = 0
    hedge_enabled = bool(getattr(cfg, 'BTC_HEDGE_SAMESYM_ENABLED', False))
    hedge_trigger_loss_pct = float(getattr(cfg, 'BTC_HEDGE_SAMESYM_TRIGGER_LOSS_PCT', -0.3))
    hedge_req_3m = bool(getattr(cfg, 'BTC_HEDGE_SAMESYM_REQUIRE_WT_3M', True))
    hedge_req_15m = bool(getattr(cfg, 'BTC_HEDGE_SAMESYM_REQUIRE_WT_15M', True))
    hedge_req_htf_min = int(getattr(cfg, 'BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN', 1))
    hedge_notional_pct = float(getattr(cfg, 'BTC_HEDGE_SAMESYM_NOTIONAL_PCT', 1.0))
    hedge_close_nonneg = bool(getattr(cfg, 'BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_NONNEG_GAIN', True))
    hedge_close_3m_1h = bool(getattr(cfg, 'BTC_HEDGE_SAMESYM_CLOSE_REQUIRE_WT_3M_AND_1H', True))
    hedge_hard_loss_pct = float(getattr(cfg, 'BTC_HEDGE_SAMESYM_HEDGE_HARD_LOSS_PCT', -2.0))
    # D-confirmation state (2026-04-27): persistent bars of D divergence presence,
    # required ≥ BTC_DIVERGENCE_REQUIRE_D_CONFIRM_BARS before allowing div-triggered exit.
    bull_d_persist = 0
    bear_d_persist = 0
    div_min_tf = str(getattr(cfg, "BTC_DIVERGENCE_MIN_TF", "4h"))
    div_d_confirm_bars = int(getattr(cfg, "BTC_DIVERGENCE_REQUIRE_D_CONFIRM_BARS", 2))

    # Min lookback to start sim — need fib_lb_D_in_ltf bars
    start_i = max(fib_lb_D_in_ltf, div_lb + 1, 100)
    _prev_position_for_anchor = "FLAT"

    # ===== ENGINE-RETROFIT 2026-04-30: pre-compute vectorized live-parity masks =====
    # Each path returns a per-bar boolean array. Looked up by mask[i] inside the per-bar loop.
    _wt_dc_entry_active = bool(getattr(cfg, 'WT_DC_ENTRY_ENABLED', False))
    if _wt_dc_entry_active:
        _wt_dc_long_fire, _wt_dc_long_score = _wt_dc_entry_mask(npz, n, cfg, is_long=True)
        _wt_dc_short_fire, _wt_dc_short_score = _wt_dc_entry_mask(npz, n, cfg, is_long=False)
    else:
        _wt_dc_long_fire = np.zeros(n, dtype=bool); _wt_dc_long_score = np.zeros(n, dtype=np.float32)
        _wt_dc_short_fire = np.zeros(n, dtype=bool); _wt_dc_short_score = np.zeros(n, dtype=np.float32)
    # Exit-side masks (per-bar; the per-position context — gain_pct, age, etc — feeds them inside the loop)
    _peak_giveback_active = bool(getattr(cfg, 'PEAK_GIVEBACK_ENABLED', False))
    _be_erosion_active = bool(getattr(cfg, 'BE_EROSION_ENABLED', False))
    _k1m_extreme_active = bool(getattr(cfg, 'K1M_EXTREME_REVERSE_ENABLED', False))
    _strong_reduce_k_active = bool(getattr(cfg, 'STRONG_REDUCE_K_ENABLED', False))
    # k_15m / k_1h for STRONG_REDUCE_K (read once)
    if _strong_reduce_k_active or _k1m_extreme_active:
        _k_15m_arr = _safe(npz, 'stoch_k_15m', n, default=50.0)
        _k_1h_arr = _safe(npz, 'stoch_k_1h', n, default=50.0)
    if _k1m_extreme_active:
        _k_1m_arr = _safe_k_1m(npz, n, ltf=getattr(cfg, 'LTF', '3m'))
        _k_1m_prev_arr = np.roll(_k_1m_arr, 1); _k_1m_prev_arr[0] = _k_1m_arr[0]
        _k1m_field_present = not (_k_1m_arr == 50.0).all()  # zero-fill check still valid; helper proxies to LTF when 1m absent

    for i in range(start_i, n):
        price_i = float(close[i])
        if price_i <= 0:
            continue

        # HTF-anchor: detect FLAT→entry transition that happened in PREVIOUS iteration
        # (i.e., last bar's entry decision was made and position is now non-FLAT).
        # Update the anchor tracker so this bar's entry block can block opposite-side flips.
        if _anchor_enabled and _prev_position_for_anchor == "FLAT" and position != "FLAT":
            _last_entry_htf_candle = (i - 1) // _anchor_bars_per_candle if i >= 1 else 0
            _last_entry_side_in_candle = position
        _prev_position_for_anchor = position

        # ── Build features at bar i ─────────────────────────────────────────
        # Accel ramp
        accel_per_tf = {
            tf: _btc.WTAccelFeatures(
                velocity=float(vel[tf][i]),
                velocity_prev=float(vel_prev[tf][i]),
                acceleration=float(vel[tf][i] - vel_prev[tf][i]),
            ) for tf in tfs
        }
        accel = _btc.compute_accel_ramp(
            accel_per_tf,
            require_positive=bool(getattr(cfg, 'BTC_ACCEL_RAMP_REQUIRE_POSITIVE', True)),
        )

        # Multi-indicator divergence on RSI/MFI/WT (skip OBV/CVD — not in NPZ yet).
        # Per-TF lookback (HTF needs longer window). Min_tf filter skips noisy LTF divergence.
        lb_by_tf = {
            "3m":  int(getattr(cfg, "BTC_DIVERGENCE_LB_3M", 5)),
            "15m": int(getattr(cfg, "BTC_DIVERGENCE_LB_15M", 10)),
            "1h":  int(getattr(cfg, "BTC_DIVERGENCE_LB_1H", 20)),
            "4h":  int(getattr(cfg, "BTC_DIVERGENCE_LB_4H", 20)),
            "D":   int(getattr(cfg, "BTC_DIVERGENCE_LB_D", 10)),
        }
        max_lb = max(lb_by_tf.values())
        if i >= max_lb:
            mtf = {}
            for ind_name, ind_arr in (("WT", wt1), ("RSI", rsi), ("MFI", mfi)):
                tf_map = {}
                for tf in tfs:
                    tf_lb = lb_by_tf[tf]
                    slc = slice(i - tf_lb, i + 1)
                    tf_map[tf] = (close[slc].tolist(), ind_arr[tf][slc].tolist())
                mtf[ind_name] = tf_map
            div = _btc.aggregate_multi_indicator_divergence(
                mtf,
                lookback_bars=div_lb,
                lookback_by_tf=lb_by_tf,
                min_tf=div_min_tf,
                strong_tfs_threshold=3,
            )
        else:
            div = _btc.DivergenceState()

        # D-confirmation: track persistent bars of D-side divergence presence.
        # Per user 2026-04-27: D divergence works clockwork but TAKES TIME to materialize —
        # don't stop out until N consecutive bars confirm.
        if getattr(div, 'bull_d_present', False):
            bull_d_persist += 1
        else:
            bull_d_persist = 0
        if getattr(div, 'bear_d_present', False):
            bear_d_persist += 1
        else:
            bear_d_persist = 0
        # If user has set MIN_TF=D, only let div fire after persistence threshold met.
        # If MIN_TF<D, persistence is informational only (lower-TF div fires immediately).
        if div_min_tf == "D":
            if bull_d_persist < div_d_confirm_bars:
                div.bull_inds_aligned = 0
            if bear_d_persist < div_d_confirm_bars:
                div.bear_inds_aligned = 0

        # Red zone: fib levels per TF + round levels + wt_dc zone proxy
        fib_per_tf = {}
        if rz_use_fib:
            if h4_rmax is not None and i < len(h4_rmax) and i < len(l4_rmin):
                fib_per_tf['4h'] = _btc.compute_fib_levels(float(h4_rmax[i]), float(l4_rmin[i]))
            if hD_rmax is not None and i < len(hD_rmax) and i < len(lD_rmin):
                fib_per_tf['D'] = _btc.compute_fib_levels(float(hD_rmax[i]), float(lD_rmin[i]))
        round_levels = {}
        if rz_use_round:
            round_levels = _btc.compute_round_levels(
                price_i,
                primary_inc_usd=round_primary,
                secondary_inc_usd=round_secondary,
                bands_each_side=round_bands,
            )
        # Multi-factor wt_dc zone (2026-04-28 — was BB %B 4h proxy only).
        # Combines BB %B (4h+1h) + DC position (4h+1h) + stoch K (4h+1h) + MFI extremes.
        # Mirrors wt_dc_delta._run_redzone() factors. Falls back to simple BB %B if disabled.
        wt_dc_zone = "none"
        if rz_use_wt_dc:
            bp4 = float(bb_pct_b_4h[i])
            if wt_dc_multifactor:
                bp1 = float(bb_pct_b_1h_arr[i])
                dcp1 = float(dc_pos_1h_arr[i])
                dcp4 = float(dc_pos_4h_arr[i])
                k1 = float(stoch_k_1h_arr[i])
                k4 = float(stoch_k_4h_arr[i])
                mf1 = float(mfi_1h_arr[i])
                mf4 = float(mfi_4h_arr[i])
                # TOP: any 2-of-7 extreme high readings
                top_score = ((bp4 >= 0.85) + (bp1 >= 0.85) + (dcp4 >= 0.80) + (dcp1 >= 0.80)
                              + (k4 >= 80) + (k1 >= 80) + (mf4 >= 80))
                bot_score = ((bp4 <= 0.15) + (bp1 <= 0.15) + (dcp4 <= 0.20) + (dcp1 <= 0.20)
                              + (k4 <= 20) + (k1 <= 20) + (mf4 <= 20))
                if top_score >= 2: wt_dc_zone = "TOP"
                elif bot_score >= 2: wt_dc_zone = "BOTTOM"
                else: wt_dc_zone = "BASELINE"
            else:
                if bp4 >= 0.85: wt_dc_zone = "TOP"
                elif bp4 <= 0.15: wt_dc_zone = "BOTTOM"
                else: wt_dc_zone = "BASELINE"
        rz = _btc.build_red_zone_state(
            current_price=price_i,
            fib_levels_per_tf=fib_per_tf,
            round_levels=round_levels,
            wt_dc_zone=wt_dc_zone,
            proximity_pct=rz_proximity_pct,
        )

        # ── SAME-SYMBOL HEDGE LOGIC (2026-04-28) ────────────────────────────
        # Runs BEFORE primary FLAT/OPEN branches. Hedge state is independent of
        # primary; both can be open simultaneously (delta-neutral pair).
        if hedge_enabled:
            # Per-TF wt-against-primary booleans (used for hedge OPEN trigger)
            _wt1_3m_now = wt1["3m"][i]; _wt2_3m_now = wt2["3m"][i]
            _wt1_15m_now = wt1["15m"][i]; _wt2_15m_now = wt2["15m"][i]
            _wt1_1h_now = wt1["1h"][i]; _wt2_1h_now = wt2["1h"][i]
            _wt1_4h_now = wt1["4h"][i]; _wt2_4h_now = wt2["4h"][i]
            _wt1_D_now = wt1["D"][i]; _wt2_D_now = wt2["D"][i]
            # 1) HEDGE CLOSE check (runs first — frees state for re-open if needed)
            if hedge_side != "FLAT":
                if hedge_side == "LONG":
                    h_pnl_pct = (price_i - hedge_entry_price) / hedge_entry_price * 100.0
                else:
                    h_pnl_pct = (hedge_entry_price - price_i) / hedge_entry_price * 100.0
                # Hard hedge stop (rare safety net)
                if h_pnl_pct <= hedge_hard_loss_pct:
                    sym_pnl.append(h_pnl_pct * hedge_notional_pct)
                    hedge_side = "FLAT"; hedge_entry_price = 0.0; hedge_entry_bar = 0
                else:
                    # Conditional close: WT 3m+1h flip in HEDGE'S favor + nonneg gain
                    if hedge_side == "LONG":
                        wt3m_in_favor = _wt1_3m_now > _wt2_3m_now
                        wt1h_in_favor = _wt1_1h_now > _wt2_1h_now
                    else:
                        wt3m_in_favor = _wt1_3m_now < _wt2_3m_now
                        wt1h_in_favor = _wt1_1h_now < _wt2_1h_now
                    close_ok = (h_pnl_pct >= 0) if hedge_close_nonneg else True
                    if hedge_close_3m_1h:
                        close_ok = close_ok and wt3m_in_favor and wt1h_in_favor
                    else:
                        close_ok = close_ok and (wt3m_in_favor or wt1h_in_favor)
                    if close_ok:
                        sym_pnl.append(h_pnl_pct * hedge_notional_pct)
                        hedge_side = "FLAT"; hedge_entry_price = 0.0; hedge_entry_bar = 0
            # 2) HEDGE OPEN check (only if hedge is FLAT and primary is open + losing)
            if hedge_side == "FLAT" and position != "FLAT":
                if position == "LONG":
                    p_pnl = (price_i - entry_price) / entry_price * 100.0
                else:
                    p_pnl = (entry_price - price_i) / entry_price * 100.0
                if p_pnl <= hedge_trigger_loss_pct:
                    if position == "LONG":
                        wt3m_against = _wt1_3m_now < _wt2_3m_now
                        wt15m_against = _wt1_15m_now < _wt2_15m_now
                        htf_against = (
                            (1 if _wt1_1h_now < _wt2_1h_now else 0)
                            + (1 if _wt1_4h_now < _wt2_4h_now else 0)
                            + (1 if _wt1_D_now < _wt2_D_now else 0)
                        )
                    else:
                        wt3m_against = _wt1_3m_now > _wt2_3m_now
                        wt15m_against = _wt1_15m_now > _wt2_15m_now
                        htf_against = (
                            (1 if _wt1_1h_now > _wt2_1h_now else 0)
                            + (1 if _wt1_4h_now > _wt2_4h_now else 0)
                            + (1 if _wt1_D_now > _wt2_D_now else 0)
                        )
                    cond_3m = (wt3m_against if hedge_req_3m else True)
                    cond_15m = (wt15m_against if hedge_req_15m else True)
                    cond_htf = (htf_against >= hedge_req_htf_min)
                    if cond_3m and cond_15m and cond_htf:
                        hedge_side = "SHORT" if position == "LONG" else "LONG"
                        hedge_entry_price = price_i
                        hedge_entry_bar = i

        # ── Decision: FLAT → entry / reentry ────────────────────────────────
        if position == "FLAT":
            # Cooldown gate — use breakout-cooldown if last exit was a breakout, else regular
            cd_bars = btc_breakout_cooldown if last_exit_type == "BREAKOUT" else btc_cooldown_bars
            if i - last_exit_bar < cd_bars:
                continue
            # ── Funding rate gate (2026-04-28 wiring) ──
            #   funding > LONG_MAX  → veto LONG  (longs already crowded)
            #   funding < SHORT_MIN → veto SHORT (shorts already crowded)
            funding_blocks_long = False
            funding_blocks_short = False
            if funding_gate_enabled:
                _fr = float(funding_rate_arr[i])
                # Skip pre-cache zeros (funding=exactly 0 means data not available)
                if _fr != 0.0:
                    if _fr > funding_long_max:
                        funding_blocks_long = True
                    if _fr < funding_short_min:
                        funding_blocks_short = True
            # ── OI 4-quadrant gate (2026-04-28 wiring) ──
            #   Quadrant A (price up + OI up)  → confirm LONG (smart $ accumulating)
            #   Quadrant B (price up + OI down) → block LONG  (short squeeze, weak)
            #   Quadrant C (price down + OI up) → confirm SHORT
            #   Quadrant D (price down + OI down) → block SHORT (long unwinds, weak)
            oi_blocks_long = False
            oi_blocks_short = False
            if oi_gate_enabled and i >= _1h_in_ltf:
                _oi_now = float(oi_arr[i]); _oi_prev = float(oi_prev_arr[i])
                _px_now = price_i; _px_prev = float(close_prev_1h_arr[i])
                if _oi_prev > 0 and _px_prev > 0:
                    _oi_chg_pct = (_oi_now - _oi_prev) / _oi_prev * 100.0
                    _px_chg_pct = (_px_now - _px_prev) / _px_prev * 100.0
                    if abs(_oi_chg_pct) >= oi_min_change_pct and abs(_px_chg_pct) >= oi_min_price_pct:
                        # Quadrant B: price up + OI down → veto LONG
                        if _px_chg_pct > 0 and _oi_chg_pct < 0:
                            oi_blocks_long = True
                        # Quadrant D: price down + OI down → veto SHORT
                        if _px_chg_pct < 0 and _oi_chg_pct < 0:
                            oi_blocks_short = True

            # HTF alignment: count 1h/4h/D where wt1>wt2 (bull) or <wt2 (bear).
            # Used by BREAKOUT to avoid buying small uptick rallies during a clear downtrend.
            htf_long_n  = ((1 if wt1['1h'][i] > wt2['1h'][i] else 0)
                           + (1 if wt1['4h'][i] > wt2['4h'][i] else 0)
                           + (1 if wt1['D'][i]  > wt2['D'][i]  else 0))
            htf_short_n = ((1 if wt1['1h'][i] < wt2['1h'][i] else 0)
                           + (1 if wt1['4h'][i] < wt2['4h'][i] else 0)
                           + (1 if wt1['D'][i]  < wt2['D'][i]  else 0))

            # ── HTF DIRECTION ANCHOR (2026-04-29) — block opposite-side flips inside same HTF candle.
            # Computes current HTF candle index. If we already entered in this candle on the
            # opposite side, block this bar entirely (continue the loop). Same-side re-entries pass.
            _block_long_flip = False
            _block_short_flip = False
            if _anchor_enabled and _last_entry_side_in_candle != "NONE":
                _cur_htf_candle = i // _anchor_bars_per_candle
                if _cur_htf_candle == _last_entry_htf_candle:
                    if _last_entry_side_in_candle == "LONG":
                        _block_short_flip = True
                    elif _last_entry_side_in_candle == "SHORT":
                        _block_long_flip = True

            # ── RESTRICTED ENTRY MODE ── (vectorized 2026-04-29 — ~60× faster than per-bar)
            # When enabled, ONLY fire on pre-computed setup matches. All other entry
            # pathways (BREAKOUT/PRIMARY_BOUNCE/FOLLOW_THROUGH/REVERSE) are skipped.
            if _restr_enabled:
                long_hit = bool(_restr_long_mask[i])
                short_hit = bool(_restr_short_mask[i])
                # Funding/OI vetoes still apply
                if long_hit and not (funding_blocks_long or oi_blocks_long or _block_long_flip):
                    position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                    code = int(_restr_long_label[i])
                    entry_reason = f"RESTRICTED_{_RESTRICTED_LABEL_NAMES.get(code, 'UNK')}_LONG"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                    continue
                if short_hit and not (funding_blocks_short or oi_blocks_short or _block_short_flip):
                    position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                    code = int(_restr_short_label[i])
                    entry_reason = f"RESTRICTED_{_RESTRICTED_LABEL_NAMES.get(code, 'UNK')}_SHORT"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                    continue
                # No setup matched — skip all other entry logic this bar
                continue

            # 1. BREAKOUT entry detection (NEW — runs FIRST so trends are caught before bounce logic)
            #    Tighter exits + smaller stop in exchange for catching big moves.
            bk_side, _ = _btc.detect_btc_breakout(
                current_price=price_i,
                prev_dc_high_3m=float(dc_high_3m_prev[i]),
                prev_dc_low_3m=float(dc_low_3m_prev[i]),
                accel=accel, divergence=div, cfg=cfg,
                htf_long_aligned_tfs=htf_long_n,
                htf_short_aligned_tfs=htf_short_n,
            ) if btc_breakout_enabled else ("NONE", "")
            # Apply funding/OI veto to all entry paths below
            if bk_side == "LONG" and not (funding_blocks_long or oi_blocks_long or _block_long_flip):
                position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "BREAKOUT"
                entry_reason = "BREAKOUT_LONG"
                entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                continue
            if bk_side == "SHORT" and not (funding_blocks_short or oi_blocks_short or _block_short_flip):
                position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "BREAKOUT"
                entry_reason = "BREAKOUT_SHORT"
                entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                continue

            # 1b. WT_DC_ENTRY (engine-retrofit 2026-04-30, registry E1) — biggest live entry path
            # Pre-computed masks _wt_dc_long_fire / _wt_dc_short_fire are looked up per bar.
            if _wt_dc_entry_active:
                if _wt_dc_long_fire[i] and not (funding_blocks_long or oi_blocks_long or _block_long_flip):
                    position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "WT_DC_ENTRY"
                    entry_reason = f"WT_DC_ENTRY_LONG_score{_wt_dc_long_score[i]:.0f}"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                    continue
                if _wt_dc_short_fire[i] and not (funding_blocks_short or oi_blocks_short or _block_short_flip):
                    position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "WT_DC_ENTRY"
                    entry_reason = f"WT_DC_ENTRY_SHORT_score{_wt_dc_short_score[i]:.0f}"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                    continue

            # 2. FOLLOW-THROUGH reentry: same-side reentry past exit price (no RZ required).
            #    Catches the case where we exited prematurely on a bear-div / WT_AGAINST and
            #    price kept going in our original direction.
            if (
                btc_follow_through_enabled
                and last_exit_side != "NONE"
                and last_exit_price > 0
                and (i - last_exit_bar) >= reentry_min_gap
                and (i - last_exit_bar) <= reentry_max_age
            ):
                pct_past = ((price_i - last_exit_price) / last_exit_price) * 100.0
                ft_long = (last_exit_side == "LONG"  and pct_past >=  btc_follow_through_min_pct
                           and accel["side"] == "bull" and accel["bull_aligned_tfs"] >= 1)
                ft_short = (last_exit_side == "SHORT" and pct_past <= -btc_follow_through_min_pct
                            and accel["side"] == "bear" and accel["bear_aligned_tfs"] >= 1)
                if ft_long and not (funding_blocks_long or oi_blocks_long or _block_long_flip):
                    position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "BREAKOUT"
                    entry_reason = "FOLLOW_THROUGH_REENTRY_LONG"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                    continue
                if ft_short and not (funding_blocks_short or oi_blocks_short or _block_short_flip):
                    position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "BREAKOUT"
                    entry_reason = "FOLLOW_THROUGH_REENTRY_SHORT"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                    continue

            # 3. BOUNCE-style reentry guarantee (existing path B reentry)
            reenter = False; reenter_side = None
            if reentry_enabled and last_exit_side != "NONE":
                bars_since = i - last_exit_bar
                if bars_since >= reentry_min_gap and bars_since <= reentry_max_age:
                    pos_state = _btc.PositionRiskState(side="FLAT", bars_since_last_exit=bars_since)
                    ok, _ = _btc.should_reenter_btc(
                        position=pos_state, last_exit_side=last_exit_side,
                        red_zone=rz, accel=accel, divergence=div, cfg=cfg,
                    )
                    if ok:
                        reenter = True; reenter_side = last_exit_side

            # 4. PRIMARY BOUNCE entry
            ok_long = ok_short = False
            if not reenter:
                ok_long, _ = _btc.should_enter_btc_long(
                    current_price=price_i, red_zone=rz, accel=accel, divergence=div, cfg=cfg,
                )
                ok_short, _ = _btc.should_enter_btc_short(
                    current_price=price_i, red_zone=rz, accel=accel, divergence=div, cfg=cfg,
                )
            if reenter:
                _veto = ((reenter_side == "LONG"  and (funding_blocks_long  or oi_blocks_long or _block_long_flip))
                         or (reenter_side == "SHORT" and (funding_blocks_short or oi_blocks_short or _block_short_flip)))
                if not _veto:
                    position = reenter_side; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                    entry_reason = f"BOUNCE_REENTRY_{reenter_side}"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
            elif ok_long and not ok_short and not (funding_blocks_long or oi_blocks_long or _block_long_flip):
                position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                entry_reason = "PRIMARY_BOUNCE_LONG"
                entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
            elif ok_short and not ok_long and not (funding_blocks_short or oi_blocks_short or _block_short_flip):
                position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                entry_reason = "PRIMARY_BOUNCE_SHORT"
                entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
            continue

        # ── Decision: open position → exit check ────────────────────────────
        # Compute current pnl pct
        if position == "LONG":
            pnl_pct = (price_i - entry_price) / entry_price * 100.0
        else:
            pnl_pct = (entry_price - price_i) / entry_price * 100.0
        pnl_usd = (pnl_pct / 100.0) * own_max * leverage

        # WT against count: how many TFs have wt1 < wt2 (LONG) or wt1 > wt2 (SHORT)
        wt_against_count = 0
        for tf in tfs:
            if position == "LONG" and wt1[tf][i] < wt2[tf][i]:
                wt_against_count += 1
            elif position == "SHORT" and wt1[tf][i] > wt2[tf][i]:
                wt_against_count += 1

        pos_state = _btc.PositionRiskState(
            side=position,
            own_capital_usd=own_max,
            notional_usd=own_max * leverage,
            current_pnl_pct=pnl_pct,
            current_pnl_usd=pnl_usd,
            age_bars=i - entry_bar,
            entry_type=entry_type,
        )

        # Single-3m WT flip + dc_low4_3m breach detection (BREAKOUT-mode exit signals)
        wt_3m_against = (position == "LONG" and wt1_3m[i] < wt2_3m[i]) or \
                        (position == "SHORT" and wt1_3m[i] > wt2_3m[i])
        dc_low4_breach = price_i < float(dc_low4_3m_prev[i]) and dc_low4_3m_prev[i] > 0
        dc_high4_breach = price_i > float(dc_high4_3m_prev[i]) and dc_high4_3m_prev[i] > 0

        # REJECTION detection (2026-04-28): replaces over-eager BREAKOUT_ACCEL_REVERSAL.
        # LONG rejection = (a) 2-bar reclaim of prev DC high (closed back inside the channel
        #                       we broke out of for two consecutive bars), OR
        #                  (b) k_3m extreme reversal (was >= 80, now ticking down)
        # SHORT rejection = mirror.
        if i >= 2:
            price_prev = float(close[i - 1])
            dc_h_prev_now = float(dc_high_3m_prev[i]) if dc_high_3m_prev[i] > 0 else 0.0
            dc_h_prev_prev = float(dc_high_3m_prev[i - 1]) if dc_high_3m_prev[i - 1] > 0 else 0.0
            dc_l_prev_now = float(dc_low_3m_prev[i]) if dc_low_3m_prev[i] > 0 else 0.0
            dc_l_prev_prev = float(dc_low_3m_prev[i - 1]) if dc_low_3m_prev[i - 1] > 0 else 0.0
            k3_now = float(stoch_k_3m_arr[i])
            k3_prev = float(stoch_k_3m_arr[i - 1])
            long_dc_reclaim = (dc_h_prev_now > 0 and dc_h_prev_prev > 0
                               and price_i < dc_h_prev_now and price_prev < dc_h_prev_prev)
            long_k_reversal = (k3_prev >= 80 and k3_now < k3_prev)
            short_dc_reclaim = (dc_l_prev_now > 0 and dc_l_prev_prev > 0
                                and price_i > dc_l_prev_now and price_prev > dc_l_prev_prev)
            short_k_reversal = (k3_prev <= 20 and k3_now > k3_prev)
            long_rejection = long_dc_reclaim or long_k_reversal
            short_rejection = short_dc_reclaim or short_k_reversal
        else:
            long_rejection = False
            short_rejection = False

        ok_exit, _reason = _btc.should_exit_btc(
            position=pos_state, accel=accel, divergence=div,
            wt_against_min_tfs=tech_exit_min_tfs,
            wt_against_count=wt_against_count,
            wt_3m_against=wt_3m_against,
            dc_low4_3m_breach=dc_low4_breach,
            dc_high4_3m_breach=dc_high4_breach,
            long_rejection=long_rejection,
            short_rejection=short_rejection,
            cfg=cfg,
        )
        # R2 — WT_15M_VEL_SLOW peak-then-collapse exit (mirror live, USER 2026-05-09).
        # Track max_pnl_pct per position; reset when entry_bar advances.
        if entry_bar != _max_pnl_entry_bar:
            max_pnl_pct = pnl_pct
            _max_pnl_entry_bar = entry_bar
        elif pnl_pct > max_pnl_pct:
            max_pnl_pct = pnl_pct
        # Fires only if position WAS profitable (peak ≥ R2_PEAK_MIN_PCT) AND
        # has fallen back to inside [floor, band].
        if not ok_exit and _wzg_enabled and \
           max_pnl_pct >= _wzg_peak_min and _wzg_floor <= pnl_pct < _wzg_band:
            _v15 = float(wt_vel_15m_arr[i]); _v15p = float(wt_vel_15m_prev_arr[i])
            _v15_against = (position == "LONG" and _v15 < 0) or (position == "SHORT" and _v15 > 0)
            _v15_decel = abs(_v15) < abs(_v15p) * _wzg_decel_ratio and abs(_v15p) > 1e-6
            _v15_dying = (not _wzg_decel_only) and abs(_v15) <= _wzg_near_zero
            if _v15_against and (_v15_decel or _v15_dying):
                ok_exit = True
                _reason = (_reason or "") + ("|" if _reason else "") + (f"R2_DECEL_peak{max_pnl_pct:.2f}" if _v15_decel else f"R2_DYING_peak{max_pnl_pct:.2f}")
        # Per-entry-type panic floor + min-hold gate
        is_breakout_pos = (entry_type == "BREAKOUT")
        eff_hard_loss_pct = breakout_hard_loss_pct if is_breakout_pos else hard_loss_pct
        eff_min_hold = btc_breakout_min_hold if is_breakout_pos else btc_min_hold_bars
        if (i - entry_bar) < eff_min_hold and pnl_pct > eff_hard_loss_pct:
            ok_exit = False
        if pnl_pct <= eff_hard_loss_pct:
            ok_exit = True

        if ok_exit:
            sym_pnl.append(pnl_pct)
            if _trade_recorder is not None:
                _exit_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                _trade_recorder.append({
                    "symbol": _symbol,
                    "side": position,
                    "entry_type": entry_type,
                    "entry_reason": entry_reason,
                    "exit_reason": _reason or "TECH_EXIT",
                    "entry_bar": int(entry_bar),
                    "entry_ts": entry_ts,
                    "entry_price": float(entry_price),
                    "exit_bar": int(i),
                    "exit_ts": _exit_ts,
                    "exit_price": float(price_i),
                    "pnl_pct": float(pnl_pct),
                    "pnl_usd": float(pnl_usd),
                    "duration_bars": int(i - entry_bar),
                    "stream": "primary",
                })
            exited_side = position
            last_exit_side = position
            last_exit_bar = i
            last_exit_price = price_i
            last_exit_type = entry_type
            position = "FLAT"
            entry_price = 0.0
            entry_bar = 0
            entry_type = "BOUNCE"
            entry_reason = ""
            entry_ts = 0

            # ── REVERSE-ON-EXIT (2026-04-27) ──────────────────────────
            # If we just closed and the OPPOSITE side has a fresh breakout this bar,
            # immediately enter the opposite side. Catches the "fail-and-flip" pattern
            # the chart audit revealed (LONG hits stop on DC4 breach → SHORT setup
            # already firing, was being missed during cooldown).
            if (bool(getattr(cfg, 'BTC_REVERSE_ON_EXIT_ENABLED', True))
                and not bool(getattr(cfg, 'BTC_RESTRICTED_ENTRY_MODE_ENABLED', False))):
                rev_htf_long_n = htf_long_n if 'htf_long_n' in dir() else 0
                rev_htf_short_n = htf_short_n if 'htf_short_n' in dir() else 0
                # Recompute HTF counts for safety (pos branch may not have set them)
                rev_htf_long_n  = ((1 if wt1['1h'][i] > wt2['1h'][i] else 0)
                                   + (1 if wt1['4h'][i] > wt2['4h'][i] else 0)
                                   + (1 if wt1['D'][i]  > wt2['D'][i]  else 0))
                rev_htf_short_n = ((1 if wt1['1h'][i] < wt2['1h'][i] else 0)
                                   + (1 if wt1['4h'][i] < wt2['4h'][i] else 0)
                                   + (1 if wt1['D'][i]  < wt2['D'][i]  else 0))
                rev_side, _rev_why = _btc.detect_btc_breakout(
                    current_price=price_i,
                    prev_dc_high_3m=float(dc_high_3m_prev[i]),
                    prev_dc_low_3m=float(dc_low_3m_prev[i]),
                    accel=accel, divergence=div, cfg=cfg,
                    htf_long_aligned_tfs=rev_htf_long_n,
                    htf_short_aligned_tfs=rev_htf_short_n,
                )
                # Only flip — never re-enter same side via reverse path.
                if rev_side == "SHORT" and exited_side == "LONG":
                    position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "BREAKOUT"
                    entry_reason = "REVERSE_ON_EXIT_SHORT"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                elif rev_side == "LONG" and exited_side == "SHORT":
                    position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "BREAKOUT"
                    entry_reason = "REVERSE_ON_EXIT_LONG"
                    entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0

            # ── HTF-REVERSAL SWING (2026-04-29 user request) ──
            # When we just exited AND ≥N-of-3 HTF setup categories agree on the OPPOSITE
            # side at this bar, take the swing trade. Each category requires ≥M-of-4 TFs
            # to align (15m, 1h, 4h, D). Two-level confluence — much higher conviction.
            elif (_htf_swing_enabled and _htf_long_categories is not None
                  and position == "FLAT"):
                _min_cats = int(getattr(cfg, 'BTC_HTF_REVERSAL_SWING_MIN_CATEGORIES', 1))
                # Opposite side from exited
                if exited_side == "LONG":
                    if int(_htf_short_categories[i]) >= _min_cats and not (funding_blocks_short or oi_blocks_short or _block_short_flip):
                        position = "SHORT"; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                        entry_reason = f"HTF_SWING_SHORT_{int(_htf_short_categories[i])}of3cat"
                        entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0
                elif exited_side == "SHORT":
                    if int(_htf_long_categories[i]) >= _min_cats and not (funding_blocks_long or oi_blocks_long or _block_long_flip):
                        position = "LONG"; entry_price = price_i; entry_bar = i; entry_type = "BOUNCE"
                        entry_reason = f"HTF_SWING_LONG_{int(_htf_long_categories[i])}of3cat"
                        entry_ts = int(_ts_arr[i]) if _ts_arr is not None and i < len(_ts_arr) else 0

    # Mark-to-market open position at end of sim (CLAUDE.md Sharpe rule #2)
    if position != "FLAT":
        price_last = float(close[n - 1])
        if entry_price > 0:
            if position == "LONG":
                _mtm_pct = (price_last - entry_price) / entry_price * 100.0
            else:
                _mtm_pct = (entry_price - price_last) / entry_price * 100.0
            sym_pnl.append(_mtm_pct)
            if _trade_recorder is not None:
                _exit_ts = int(_ts_arr[n - 1]) if _ts_arr is not None and (n - 1) < len(_ts_arr) else 0
                _trade_recorder.append({
                    "symbol": _symbol, "side": position, "entry_type": entry_type,
                    "entry_reason": entry_reason, "exit_reason": "MTM_END_OF_SIM",
                    "entry_bar": int(entry_bar), "entry_ts": entry_ts, "entry_price": float(entry_price),
                    "exit_bar": int(n - 1), "exit_ts": _exit_ts, "exit_price": float(price_last),
                    "pnl_pct": float(_mtm_pct), "pnl_usd": float((_mtm_pct / 100.0) * own_max * leverage),
                    "duration_bars": int(n - 1 - entry_bar), "stream": "primary",
                })
    # Mark-to-market open hedge at end of sim too
    if hedge_side != "FLAT" and hedge_entry_price > 0:
        price_last = float(close[n - 1])
        if hedge_side == "LONG":
            _h_pct = (price_last - hedge_entry_price) / hedge_entry_price * 100.0 * hedge_notional_pct
        else:
            _h_pct = (hedge_entry_price - price_last) / hedge_entry_price * 100.0 * hedge_notional_pct
        sym_pnl.append(_h_pct)
        if _trade_recorder is not None:
            _exit_ts = int(_ts_arr[n - 1]) if _ts_arr is not None and (n - 1) < len(_ts_arr) else 0
            _trade_recorder.append({
                "symbol": _symbol, "side": hedge_side, "entry_type": "HEDGE",
                "entry_reason": "HEDGE_OPEN", "exit_reason": "MTM_END_OF_SIM",
                "entry_bar": int(hedge_entry_bar), "entry_ts": int(_ts_arr[hedge_entry_bar]) if _ts_arr is not None and hedge_entry_bar < len(_ts_arr) else 0,
                "entry_price": float(hedge_entry_price),
                "exit_bar": int(n - 1), "exit_ts": _exit_ts, "exit_price": float(price_last),
                "pnl_pct": float(_h_pct), "pnl_usd": 0.0,
                "duration_bars": int(n - 1 - hedge_entry_bar), "stream": "hedge",
            })

    return sym_pnl


# ============================================================================
# ENGINE-RETROFIT 2026-04-30 — vectorized live-parity entry/exit path masks
# Each function returns a numpy boolean array of length n where True = path fires.
# Pattern: read all NPZ fields once, build the boolean condition vectorized,
# return for the main simulate() loop to consume via mask[i] checks.
# Registry: docs/engine_retrofit_registry.md
# ============================================================================

def _wt_dc_entry_mask(npz: dict, n: int, cfg, is_long: bool) -> tuple:
    """E1. WT_DC_ENTRY (live: ~26k LONG BUY/day on tradier, source wt_dc_entry_scorer.py).
    Multi-TF score 0-100. LONG fires when score >= cfg.WT_DC_ENTRY_LONG_THRESHOLD.

    Score components (all weights tunable):
        D bull/bear:    +cfg.WT_DC_ENTRY_W_HTF_D    if wt1_D > wt2_D (LONG) / < (SHORT)
        4h bull/bear:   +cfg.WT_DC_ENTRY_W_HTF_4H   if wt1_4h > wt2_4h
        1h cross:       +cfg.WT_DC_ENTRY_W_LTF_1H   if wt_cross_1h matches direction
        DC pos 1h:      +cfg.WT_DC_ENTRY_W_DC_1H    if dc_pos_1h < DC1H_LONG_MAX (LONG)
        Stoch K 5m:     +cfg.WT_DC_ENTRY_W_K_5M     if k_5m < K5M_LONG_MAX (LONG)

    Returns (fire_mask, score_array). score_array can be inspected by sweep diagnostics.
    """
    if not getattr(cfg, 'WT_DC_ENTRY_ENABLED', False):
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=np.float32)
    wt1_D = _safe(npz, 'wt1_D', n)
    wt2_D = _safe(npz, 'wt2_D', n)
    wt1_4h = _safe(npz, 'wt1_4h', n)
    wt2_4h = _safe(npz, 'wt2_4h', n)
    # wt_cross_1h is stored as int8 in NPZ (1=BULL, -1=BEAR, 0=NONE)
    wt_cross_1h = _safe(npz, 'wt_cross_1h', n)
    dc_pos_1h = _safe(npz, 'dc_position_1h', n, default=0.5)
    k_5m = _safe(npz, 'stoch_k_5m', n, default=50.0)
    w_d = float(getattr(cfg, 'WT_DC_ENTRY_W_HTF_D', 25.0))
    w_4h = float(getattr(cfg, 'WT_DC_ENTRY_W_HTF_4H', 25.0))
    w_1h = float(getattr(cfg, 'WT_DC_ENTRY_W_LTF_1H', 30.0))
    w_dc = float(getattr(cfg, 'WT_DC_ENTRY_W_DC_1H', 10.0))
    w_k5 = float(getattr(cfg, 'WT_DC_ENTRY_W_K_5M', 10.0))
    score = np.zeros(n, dtype=np.float32)
    if is_long:
        score += np.where(wt1_D > wt2_D, w_d, 0.0)
        score += np.where(wt1_4h > wt2_4h, w_4h, 0.0)
        score += np.where(wt_cross_1h > 0.5, w_1h, 0.0)
        dc_max = float(getattr(cfg, 'WT_DC_ENTRY_DC1H_LONG_MAX', 0.50))
        score += np.where(dc_pos_1h < dc_max, w_dc, 0.0)
        k_max = float(getattr(cfg, 'WT_DC_ENTRY_K5M_LONG_MAX', 40.0))
        score += np.where(k_5m < k_max, w_k5, 0.0)
        thresh = float(getattr(cfg, 'WT_DC_ENTRY_LONG_THRESHOLD', 55))
    else:
        score += np.where(wt1_D < wt2_D, w_d, 0.0)
        score += np.where(wt1_4h < wt2_4h, w_4h, 0.0)
        score += np.where(wt_cross_1h < -0.5, w_1h, 0.0)
        dc_min = float(getattr(cfg, 'WT_DC_ENTRY_DC1H_SHORT_MIN', 0.50))
        score += np.where(dc_pos_1h > dc_min, w_dc, 0.0)
        k_min = float(getattr(cfg, 'WT_DC_ENTRY_K5M_SHORT_MIN', 60.0))
        score += np.where(k_5m > k_min, w_k5, 0.0)
        thresh = float(getattr(cfg, 'WT_DC_ENTRY_SHORT_THRESHOLD', 55))
    fire = score >= thresh
    return fire, score


def _peak_giveback_mask(gain_pct_running: np.ndarray, peak_gain_running: np.ndarray, age_bars: np.ndarray, cfg) -> np.ndarray:
    """X1. PEAK_GIVEBACK_GAIN_EROSION_STOP — 1,343/day live exit.
    Exits when peak gain reached threshold AND current gain has dropped X% from peak.

    Inputs are computed inside the per-position lifecycle (gain_pct, peak via cummax, age in bars).
    Returns boolean mask: True at bars where the exit fires.
    """
    if not getattr(cfg, 'PEAK_GIVEBACK_ENABLED', False):
        return np.zeros_like(gain_pct_running, dtype=bool)
    peak_min = float(getattr(cfg, 'PEAK_GIVEBACK_PEAK_MIN_PCT', 0.50))
    drop_pct = float(getattr(cfg, 'PEAK_GIVEBACK_DROP_PCT', 0.5))
    min_age = int(getattr(cfg, 'PEAK_GIVEBACK_MIN_AGE_BARS', 0))
    drop = peak_gain_running - gain_pct_running
    fire = (peak_gain_running >= peak_min) & (drop >= drop_pct) & (age_bars >= min_age)
    if bool(getattr(cfg, 'PEAK_GIVEBACK_HARD_ZERO_ENABLED', False)):
        fire = fire | ((peak_gain_running >= peak_min) & (gain_pct_running <= 0.0) & (age_bars >= min_age))
    return fire


def _be_erosion_mask(gain_pct_running: np.ndarray, age_bars: np.ndarray, cfg) -> np.ndarray:
    """X2. BREAKEVEN_GAIN_EROSION_STOP — 738/day live exit.
    Fires when position is ≥ N bars old AND in profit AND gain < BE_min (i.e. winning trade
    eroding back toward zero — close before it's a loser).
    """
    if not getattr(cfg, 'BE_EROSION_ENABLED', False):
        return np.zeros_like(gain_pct_running, dtype=bool)
    age_min = int(getattr(cfg, 'BE_EROSION_AGE_MIN_BARS', 30))
    be_min = float(getattr(cfg, 'BE_EROSION_MIN_GAIN', 0.0))
    require_profit = bool(getattr(cfg, 'BE_EROSION_REQUIRE_PROFIT', True))
    comm_buf = float(getattr(cfg, 'BE_EROSION_COMM_BUFFER', 0.05))
    fire = (age_bars >= age_min) & (gain_pct_running < be_min)
    if require_profit:
        fire = fire & (gain_pct_running >= comm_buf)
    return fire


def _k1m_extreme_reverse_mask(npz: dict, n: int, gain_pct_running: np.ndarray, cfg, is_long: bool) -> np.ndarray:
    """X3. K1M_EXTREME_REVERSE — 1,205/day live exit. BLOCKED if NPZ lacks stoch_k_1m.
    LONG: k_1m > 90 AND k_1m < k_1m_prev AND in profit.
    SHORT: k_1m < 10 AND k_1m > k_1m_prev AND in profit.
    """
    if not getattr(cfg, 'K1M_EXTREME_REVERSE_ENABLED', False):
        return np.zeros(n, dtype=bool)
    k_1m = _safe_k_1m(npz, n, ltf=getattr(cfg, 'LTF', '3m'))
    if (k_1m == 50.0).all():
        return np.zeros(n, dtype=bool)  # both 1m + LTF proxy missing; can't fire credibly
    k_1m_prev = np.roll(k_1m, 1)
    k_1m_prev[0] = k_1m[0]
    hi = float(getattr(cfg, 'K1M_EXTREME_HIGH', 90.0))
    lo = float(getattr(cfg, 'K1M_EXTREME_LOW', 10.0))
    require_profit = bool(getattr(cfg, 'K1M_REVERSE_REQUIRES_PROFIT', True))
    if is_long:
        fire = (k_1m > hi) & (k_1m < k_1m_prev)
    else:
        fire = (k_1m < lo) & (k_1m > k_1m_prev)
    if require_profit:
        fire = fire & (gain_pct_running >= 0.0)
    return fire


def _strong_reduce_k_mask(npz: dict, n: int, gain_pct_running: np.ndarray, cfg, is_long: bool) -> np.ndarray:
    """X4. STRONG_REDUCE_K — 2,716/day live reduce.
    LONG reduces 50% when k_15m extreme high AND k_1h trending opposite (mean-reverting setup).
    """
    if not getattr(cfg, 'STRONG_REDUCE_K_ENABLED', False):
        return np.zeros(n, dtype=bool)
    k_15m = _safe(npz, 'stoch_k_15m', n, default=50.0)
    k_1h = _safe(npz, 'stoch_k_1h', n, default=50.0)
    if is_long:
        k15_min = float(getattr(cfg, 'SRK_K15M_LONG_MIN', 80.0))
        k1h_max = float(getattr(cfg, 'SRK_K1H_LONG_MAX', 30.0))
        fire = (k_15m > k15_min) & (k_1h < k1h_max) & (gain_pct_running >= 0.0)
    else:
        k15_max = float(getattr(cfg, 'SRK_K15M_SHORT_MAX', 20.0))
        k1h_min = float(getattr(cfg, 'SRK_K1H_SHORT_MIN', 70.0))
        fire = (k_15m < k15_max) & (k_1h > k1h_min) & (gain_pct_running >= 0.0)
    return fire


def simulate(stores, cfg, capital=10000.0):
    """stores can be a dict {sym: data} (preloaded) or an iterable of
    (sym, data) tuples (streaming). Streaming releases memory per symbol."""
    all_pnl = []
    per_symbol_pnl = {}
    start_size = cfg.START_POSITION_SIZE
    # MODE-AWARE THROTTLING (2026-04-26 fix): tradier sweeps were silently ignoring
    # COOLDOWN_BARS_TRADIER / MIN_HOLD_MINUTES_TRADIER — engine read crypto fields only,
    # under-counting tradier trades 100-1000x. Tradier base TF=5m so MIN_HOLD_MINUTES/5
    # gives bars. Direct MIN_HOLD_BARS_TRADIER override also honored if a sweep adds it.
    _mode = str(getattr(cfg, 'MODE', 'crypto') or 'crypto').lower()
    if _mode == 'tradier':
        cooldown = int(getattr(cfg, 'COOLDOWN_BARS_TRADIER', getattr(cfg, 'COOLDOWN_BARS', 3)))
        _mh_bars_t = getattr(cfg, 'MIN_HOLD_BARS_TRADIER', None)
        if _mh_bars_t is not None:
            min_hold = int(_mh_bars_t)
        else:
            _mh_min_t = getattr(cfg, 'MIN_HOLD_MINUTES_TRADIER', None)
            if _mh_min_t is not None:
                # Tradier base TF = 5m; convert minutes → bars
                min_hold = max(1, int(float(_mh_min_t) // 5))
            else:
                min_hold = int(getattr(cfg, 'MIN_HOLD_BARS', 4))
    else:
        cooldown = int(getattr(cfg, 'COOLDOWN_BARS', 3))
        min_hold = int(getattr(cfg, 'MIN_HOLD_BARS', 250))
    ea_enabled = bool(getattr(cfg, 'EARLY_ABORT_ENABLED', True))
    ea_min_syms = int(getattr(cfg, 'EARLY_ABORT_MIN_SYMBOLS', 15))
    ea_floor = float(getattr(cfg, 'EARLY_ABORT_SHARPE_FLOOR', 1.0))
    ea_time_limit = float(getattr(cfg, 'EARLY_ABORT_TIME_LIMIT_SEC', 999.0))
    t_sim_start = time.time()
    symbols_processed = 0
    early_abort = False
    _rg_disabled = os.environ.get("V8_RATE_GUARD_DISABLED", "0") == "1"
    _rg = None if _rg_disabled else RateGuard(n_accts=1, label=f"v8_quick_engine.simulate.{getattr(cfg, 'MODE', 'crypto')}")
    _rg_max_n = 0
    _ltf = getattr(cfg, 'LTF', '3m')
    _ltf_mins = 3 if _ltf == '3m' else 5
    _bph_15m = 15 // _ltf_mins   # bars per 15m period: 5 (crypto/3m) or 3 (tradier/5m)
    _bph_1h = 60 // _ltf_mins    # bars per 1h: 20 (crypto) or 12 (tradier)
    _bph_4h = 4 * _bph_1h        # bars per 4h: 80 (crypto) or 48 (tradier)
    _bph_D = 480 if _ltf == '3m' else 78   # bars per day: 24h×20 (crypto) or 6.5h×12 (tradier)
    _iter = stores.items() if isinstance(stores, dict) else stores
    for sym, npz in _iter:
        sym_pnl = []
        _pnl_slice_start = len(all_pnl)  # Phase 3b: track slice for qty-pipeline re-weight
        # Phase 5: load options-OI snapshot for tradier (READ-ONLY).
        # Snapshot-static — same OI walls applied across all backtest bars. NEVER routes orders.
        _oi_long_block_below = None  # array of strikes BELOW current price that block LONG (call walls above)
        _oi_short_block_above = None  # strikes ABOVE current price that block SHORT (put walls below)
        _oi_call_walls = []  # list of (strike, oi) tuples
        _oi_put_walls = []
        _oi_pc_ratio = None
        _oi_near_money_pc = None
        _oi_total_oi = 0
        if (bool(getattr(cfg, 'OPTIONS_OI_BACKTEST_ENABLED', False))
                and getattr(cfg, 'MODE', 'crypto') == 'tradier'):
            try:
                import json as _oi_json, os as _oi_os
                _oi_path = _oi_os.path.join(getattr(cfg, 'OPTIONS_OI_CACHE_DIR', 'data/stocks_oi_cache'), f"{sym}.json")
                # Try a couple of base paths so this works on MB and on servers
                if not _oi_os.path.exists(_oi_path):
                    for _bp in ('/Users/niels/Documents/binance', '/home/niels/binance-sandbox', '/home/niels/binance'):
                        _alt = _oi_os.path.join(_bp, getattr(cfg, 'OPTIONS_OI_CACHE_DIR', 'data/stocks_oi_cache'), f"{sym}.json")
                        if _oi_os.path.exists(_alt):
                            _oi_path = _alt
                            break
                if _oi_os.path.exists(_oi_path):
                    with open(_oi_path) as _oif:
                        _oi_doc = _oi_json.load(_oif)
                    _oi_total_oi = int(_oi_doc.get('total_call_oi', 0)) + int(_oi_doc.get('total_put_oi', 0))
                    _oi_min_total = int(getattr(cfg, 'OPTIONS_OI_MIN_TOTAL_OI', 1000))
                    if _oi_total_oi >= _oi_min_total:
                        _oi_pc_ratio = float(_oi_doc.get('pc_ratio') or 0)
                        _oi_near_money_pc = float(_oi_doc.get('near_money_pc_ratio') or 0)
                        _min_oi = int(getattr(cfg, 'OPTIONS_OI_RED_ZONE_MIN_OI', 1000))
                        _deep = bool(getattr(cfg, 'OPTIONS_OI_DEEP_HEATMAP', True))
                        if _deep:
                            _oi_call_walls = [(float(w.get('strike', 0)), int(w.get('oi', 0))) for w in (_oi_doc.get('top_call_walls') or []) if int(w.get('oi', 0)) >= _min_oi and float(w.get('strike', 0)) > 0]
                            _oi_put_walls = [(float(w.get('strike', 0)), int(w.get('oi', 0))) for w in (_oi_doc.get('top_put_walls') or []) if int(w.get('oi', 0)) >= _min_oi and float(w.get('strike', 0)) > 0]
                        else:
                            _ms = float(_oi_doc.get('max_call_oi_strike') or 0); _mv = int(_oi_doc.get('max_call_oi_value') or 0)
                            if _ms > 0 and _mv >= _min_oi: _oi_call_walls = [(_ms, _mv)]
                            _ps = float(_oi_doc.get('max_put_oi_strike') or 0); _pv = int(_oi_doc.get('max_put_oi_value') or 0)
                            if _ps > 0 and _pv >= _min_oi: _oi_put_walls = [(_ps, _pv)]
            except Exception:
                pass
        # Derive n from LTF close (stocks may not have timestamps/timestamp_3m fields at all).
        ts = npz.get('timestamps', npz.get(f'timestamp_{_ltf}', npz.get('timestamp_3m', np.array([]))))
        n = len(ts)
        if n < 100:
            # Fallback: use LTF close length (stock NPZ has close_5m but no timestamp array)
            _close_ltf = npz.get(f'close_{_ltf}')
            if _close_ltf is not None and hasattr(_close_ltf, '__len__') and len(_close_ltf) >= 100:
                n = len(_close_ltf)
            else:
                continue
        # ─── BTC-DEDICATED LOOP DISPATCH ───────────────────────────────────
        # When BTC_DEDICATED_ENABLED=True and symbol is BTC, route through
        # btc_loop.* shared decision functions (same code path as live + paper).
        # See BTC_DEDICATED_LOOP_DESIGN_20260427.md.
        # 2026-04-28 Path A: per-symbol dedicated configs. The dedicated path now applies
        # to any symbol in BTC_DEDICATED_SYMBOLS (default {BTCUSDC, BTCUSDC} for backward compat).
        # Each symbol can have its own override file with per-symbol tuned BTC_* knobs.
        _ded_syms = set(getattr(cfg, 'BTC_DEDICATED_SYMBOLS',
                                ('BTCUSDC', 'BTCUSDC', 'ETHUSDC', 'SOLUSDC', 'BNBUSDC', 'BTCDOMUSDT')))
        if getattr(cfg, 'BTC_DEDICATED_ENABLED', False) and sym in _ded_syms:
            try:
                _trades_dir = os.environ.get('V8_TRADES_OUT_DIR', '')
                _trades_buf = [] if _trades_dir else None
                if bool(getattr(cfg, 'BTC_TREND_MODE_ENABLED', False)):
                    _btc_sym_pnl = _btc_trend_simulate_per_sym(
                        npz, cfg, n, _ltf, _bph_15m, _bph_1h, _bph_4h, _bph_D
                    )
                else:
                    _btc_sym_pnl = _btc_dedicated_simulate_per_sym(
                        npz, cfg, n, _ltf, _bph_15m, _bph_1h, _bph_4h, _bph_D,
                        _trade_recorder=_trades_buf, _symbol=sym,
                    )
                if _trades_dir and _trades_buf:
                    try:
                        os.makedirs(_trades_dir, exist_ok=True)
                        _run_id = os.environ.get('V8_TRADES_RUN_ID', 'default')
                        _out_path = os.path.join(_trades_dir, f"{_run_id}__{sym}.jsonl")
                        with open(_out_path, 'w') as _tf:
                            for _td in _trades_buf:
                                _tf.write(json.dumps(_td) + "\n")
                    except Exception as _te:
                        print(f"[V8_TRADES_OUT] write error {sym}: {_te}", flush=True)
                per_symbol_pnl[sym] = _btc_sym_pnl
                all_pnl.extend(_btc_sym_pnl)
                symbols_processed += 1
            except Exception as _btc_e:
                print(f"[BTC_DEDICATED] {sym} sim error: {_btc_e}", flush=True)
                per_symbol_pnl[sym] = []
            continue
        # ─── End BTC dispatch ──────────────────────────────────────────────
        _intraday_enabled = bool(getattr(cfg, 'INTRADAY_SESSION_EXIT_ENABLED', False))
        if _intraday_enabled and len(ts) >= n and n > 0:
            _sec_of_day = (np.asarray(ts[:n], dtype=np.int64) % 86400).astype(np.int32)
            _intraday_entry_cutoff = int(getattr(cfg, 'INTRADAY_SESSION_ENTRY_CUTOFF_UTC', 57600))
            _intraday_force_exit_utc = int(getattr(cfg, 'INTRADAY_SESSION_FORCE_EXIT_UTC', 70200))
        else:
            _intraday_enabled = False; _sec_of_day = None
            _intraday_entry_cutoff = 57600; _intraday_force_exit_utc = 70200
        close = _close_with_mode_check(npz, n, cfg, 'simulate')
        _dc_tf_map = {'dc_4h': ('dc_high_4h', 'dc_low_4h'), 'dc_1h': ('dc_high_1h', 'dc_low_1h'),
                      'bb_1h': ('bb_upper_1h', 'bb_lower_1h'), 'bb_4h': ('bb_upper_4h', 'bb_lower_4h')}
        _dc_hk, _dc_lk = _dc_tf_map.get(getattr(cfg, 'DC_RECOVERY_EXIT_TF', 'dc_4h'), ('dc_high_4h', 'dc_low_4h'))
        dc_high_4h = _safe(npz, _dc_hk, n)
        dc_low_4h = _safe(npz, _dc_lk, n)
        # Rolled versions for DC_BREAKOUT_FAILED_STOP: Donchian channels include the current bar's high,
        # so close[i] > dc_high_4h[i] is impossible (close ≤ high ≤ max_high). Must compare to prev bar.
        # BB (tradier bb_1h) CAN be exceeded by close — roll is a no-op loss there but keeps code uniform.
        _dc_h4_prev = np.roll(dc_high_4h, 1); _dc_h4_prev[0] = dc_high_4h[0]
        _dc_l4_prev = np.roll(dc_low_4h, 1); _dc_l4_prev[0] = dc_low_4h[0]
        _dc4_bypass_enabled = bool(getattr(cfg, 'DC_LOW4_BYPASS_NOLOSS_ENABLED', False))
        _dc4_max_bars = int(getattr(cfg, 'DC_LOW4_BYPASS_MAX_BARS', 0))
        _dc4_std = bool(getattr(cfg, 'DC_LOW4_BYPASS_USE_STANDARD', False))
        _dc4_tf_override = str(getattr(cfg, 'DC_LOW4_BYPASS_TF', ''))  # '' = use LTF, '15m' etc = explicit TF
        _dc4_tf = _dc4_tf_override if _dc4_tf_override else _ltf
        _dc4_low_key = f'dc_low_{_dc4_tf}' if _dc4_std else f'dc_low4_{_dc4_tf}'
        _dc4_high_key = f'dc_high_{_dc4_tf}' if _dc4_std else f'dc_high4_{_dc4_tf}'
        # Shift by 1 bar: dc_low4 includes the current bar's low, so close[i] < dc_low4[i] is
        # mathematically impossible. Live code compares current_price to PREVIOUS bar's dc_low4.
        _dc4_raw_low = _safe(npz, _dc4_low_key, n) if _dc4_bypass_enabled else None
        _dc4_raw_high = _safe(npz, _dc4_high_key, n) if _dc4_bypass_enabled else None
        if _dc4_bypass_enabled and _dc4_raw_low is not None:
            import numpy as _np_dc4
            _dc4_low = _np_dc4.roll(_dc4_raw_low, 1); _dc4_low[0] = _dc4_raw_low[0]
            _dc4_high = _np_dc4.roll(_dc4_raw_high, 1); _dc4_high[0] = _dc4_raw_high[0]
        else:
            _dc4_low = None; _dc4_high = None
        # MIN_HOLD_DC_LOW_BYPASS: allow early exit before min_hold if DC channel breaks (structural safety)
        _mh_dc_bypass_enabled = bool(getattr(cfg, 'MIN_HOLD_DC_LOW_BYPASS_ENABLED', False))
        _mh_dc_bypass_tf = str(getattr(cfg, 'MIN_HOLD_DC_LOW_BYPASS_TF', '15m'))
        if _mh_dc_bypass_enabled:
            _mh_dc_raw_low = _safe(npz, f'dc_low_{_mh_dc_bypass_tf}', n)
            _mh_dc_raw_high = _safe(npz, f'dc_high_{_mh_dc_bypass_tf}', n)
            _mh_dc_low = np.roll(_mh_dc_raw_low, 1); _mh_dc_low[0] = _mh_dc_raw_low[0]
            _mh_dc_high = np.roll(_mh_dc_raw_high, 1); _mh_dc_high[0] = _mh_dc_raw_high[0]
        else:
            _mh_dc_low = None; _mh_dc_high = None
        # RZ_BREAKOUT: precompute breakout detection arrays and guard level for noloss bypass
        _rz_break_enabled = getattr(cfg, 'RZ_BREAKOUT_ENTRY_ENABLED', False)
        _rz_break_arr = None
        _rz_nl_guard_low = None; _rz_nl_guard_high = None
        _rz_nl_bar_high = None; _rz_nl_bar_low = None
        _rz_nl_bar_high_prev = None; _rz_nl_bar_low_prev = None
        _rz_nl_mode = "none"
        _rz_nl_bar_window = int(getattr(cfg, 'RZ_BREAKOUT_NOLOSS_BAR_WINDOW', 4) or 4)
        _rz_base_tf = '5m' if getattr(cfg, 'MODE', 'crypto') == 'tradier' else '3m'
        if _rz_break_enabled:
            _bb_pb_sim = _safe(npz, 'bb_pct_b_1h', n, 0.5)
            _bb_pb_sim_prev = np.roll(_bb_pb_sim, 1); _bb_pb_sim_prev[0] = _bb_pb_sim[0]
            _rz_top_sim = float(getattr(cfg, 'RZ_TOP_BB_THRESHOLD', 0.85))
            _rz_bot_sim = float(getattr(cfg, 'RZ_BOT_BB_THRESHOLD', 0.15))
            _rz_break_long = (_bb_pb_sim_prev <= _rz_bot_sim) & (_bb_pb_sim > _rz_bot_sim)
            _rz_break_short = (_bb_pb_sim_prev >= _rz_top_sim) & (_bb_pb_sim < _rz_top_sim)
            # NOLOSS bypass mode — determine from RZ_BREAKOUT_NOLOSS_MODE or legacy fields
            _rz_nl_mode_raw = str(getattr(cfg, 'RZ_BREAKOUT_NOLOSS_MODE', ''))
            if not _rz_nl_mode_raw:
                _guard_en = getattr(cfg, 'RZ_BREAKOUT_NOLOSS_GUARD_ENABLED', True)
                _rz_nl_mode_raw = 'none' if not _guard_en else f'dc_low_{getattr(cfg, "RZ_BREAKOUT_NOLOSS_GUARD_TF", "15m")}'
            _rz_nl_mode = _rz_nl_mode_raw
            if _rz_nl_mode == 'bar_structure':
                _rz_nl_bar_high = _safe(npz, f'high_{_rz_base_tf}', n)
                _rz_nl_bar_low = _safe(npz, f'low_{_rz_base_tf}', n)
                _rz_nl_bar_high_prev = _safe(npz, f'high_{_rz_base_tf}_prev', n)
                _rz_nl_bar_low_prev = _safe(npz, f'low_{_rz_base_tf}_prev', n)
            elif _rz_nl_mode != 'none':
                _dc_field_map = {
                    'dc_low4_base': (f'dc_low4_{_rz_base_tf}', f'dc_high4_{_rz_base_tf}'),
                    'dc_low_base':  (f'dc_low_{_rz_base_tf}',  f'dc_high_{_rz_base_tf}'),
                    'dc_low4_15m':  ('dc_low4_15m',  'dc_high4_15m'),
                    'dc_low_15m':   ('dc_low_15m',   'dc_high_15m'),
                    'dc_low_1h':    ('dc_low_1h',    'dc_high_1h'),
                    'dc_low4_1h':   ('dc_low4_1h',   'dc_high4_1h'),
                }
                _dc_fl, _dc_fh = _dc_field_map.get(_rz_nl_mode, ('dc_low4_15m', 'dc_high4_15m'))
                _rz_nl_raw_low = _safe(npz, _dc_fl, n)
                _rz_nl_raw_high = _safe(npz, _dc_fh, n)
                # dc_low4 fields absent from tradier NPZ — compute rolling 4-bar min/max of close as fallback
                if 'dc_low4' in _dc_fl and not np.any(_rz_nl_raw_low):
                    from numpy.lib.stride_tricks import sliding_window_view as _swv
                    _cl_p = np.concatenate([np.full(3, close[0]), close])
                    _rz_nl_raw_low = _swv(_cl_p, 4).min(axis=1)[:n]
                    _rz_nl_raw_high = _swv(_cl_p, 4).max(axis=1)[:n]
                _rz_nl_guard_low = np.roll(_rz_nl_raw_low, 1); _rz_nl_guard_low[0] = _rz_nl_raw_low[0]
                _rz_nl_guard_high = np.roll(_rz_nl_raw_high, 1); _rz_nl_guard_high[0] = _rz_nl_raw_high[0]
        # Hedge engine: continuous per-bar condition. No event needed.
        # LONG main → SHORT hedge active whenever: gain<0 AND wt1_LTF<wt2_LTF AND wt1_1h<wt2_1h
        # SHORT main → LONG hedge: gain<0 AND wt1_LTF>wt2_LTF AND wt1_1h>wt2_1h
        # Hedge WT kill TF: 'none'=LTF only, '15m'=LTF+15m, '1h'=LTF+1h (default)
        _wt1_ltf = _safe(npz, f'wt1_{_ltf}', n); _wt2_ltf = _safe(npz, f'wt2_{_ltf}', n)
        _wt1_1h = _safe(npz, 'wt1_1h', n); _wt2_1h = _safe(npz, 'wt2_1h', n)
        _wt1_15m_h = _safe(npz, 'wt1_15m', n); _wt2_15m_h = _safe(npz, 'wt2_15m', n)
        _hedge_wt_kill_tf = str(getattr(cfg, 'HEDGE_WT_KILL_CONFIRM_TF', '1h'))
        _wt1_D_aug = _safe(npz, 'wt1_D', n)
        _aug_enabled = bool(getattr(cfg, 'AUGMENT_WT_D_BOUNCE_ENABLED', False))
        _aug_mult = float(getattr(cfg, 'AUGMENT_WT_D_MULTIPLIER', 2.0))
        _aug_req_hwt = bool(getattr(cfg, 'AUGMENT_WT_D_REQUIRE_HIGHER_WT', True))
        _aug_req_hpx = bool(getattr(cfg, 'AUGMENT_WT_D_REQUIRE_HIGHER_PRICE', True))
        _aug_ce_enabled = bool(getattr(cfg, 'AUGMENT_WT_D_CONTINUE_EXIT_ENABLED', False))
        _wt1_4H_aug = _safe(npz, 'wt1_4h', n)
        _aug_4h_enabled = bool(getattr(cfg, 'AUGMENT_WT_4H_BOUNCE_ENABLED', False))
        _aug_4h_mult = float(getattr(cfg, 'AUGMENT_WT_4H_MULTIPLIER', 2.0))
        _aug_4h_req_hwt = bool(getattr(cfg, 'AUGMENT_WT_4H_REQUIRE_HIGHER_WT', False))
        _aug_4h_req_hpx = bool(getattr(cfg, 'AUGMENT_WT_4H_REQUIRE_HIGHER_PRICE', False))
        # Trade recorder buffer for non-BTC sim (accumulates across both sides).
        # Activated by env V8_TRADES_OUT_DIR. Generic ENTRY/EXIT reasons (full instrumentation deferred).
        _generic_trades_dir = os.environ.get('V8_TRADES_OUT_DIR', '')
        _generic_trades_buf = [] if _generic_trades_dir else None
        for is_long in [True, False]:
            entry_sig = compute_entry_signals(npz, n, is_long, cfg, sym=sym)
            exit_sig = compute_exit_signals(npz, n, is_long, cfg)
            _gr_mult_arr = None
            if bool(getattr(cfg, 'GOLDEN_RULE_ENABLED', True)) and bool(getattr(cfg, 'GOLDEN_RULE_MULT_APPLY', True)):
                try:
                    _, _gr_mult_arr = _golden_rule_vec(npz, n, is_long, cfg)
                except Exception:
                    _gr_mult_arr = None
            # Phase 5 (2026-05-01): options-OI RED_ZONE gate — block entries near OI walls.
            # Snapshot-static gate applied per-bar; preserved as AND-merge so it CAN ONLY block,
            # never adds new entries. Tradier-only path; crypto uses different OI source.
            if (getattr(cfg, 'OPTIONS_OI_BACKTEST_ENABLED', False)
                    and getattr(cfg, 'MODE', 'crypto') == 'tradier'
                    and bool(getattr(cfg, 'OPTIONS_OI_RED_ZONE_GATE', True))
                    and (_oi_call_walls or _oi_put_walls)):
                _oi_dist_pct = float(getattr(cfg, 'OPTIONS_OI_RED_ZONE_DIST_PCT', 0.5)) / 100.0
                _oi_close_arr = _close_with_mode_check(npz, n, cfg, 'options_oi_gate')
                _oi_block = np.zeros(n, dtype=bool)
                if is_long:
                    # Block LONG when within X% below any call wall (wall is resistance ceiling above price)
                    for _ws, _ in _oi_call_walls:
                        _dist = (_ws - _oi_close_arr) / np.maximum(_oi_close_arr, 1e-9)
                        _hit = (_dist > 0) & (_dist < _oi_dist_pct)
                        _oi_block = _oi_block | _hit
                else:
                    # Block SHORT when within X% above any put wall (wall is support floor below price)
                    for _ws, _ in _oi_put_walls:
                        _dist = (_oi_close_arr - _ws) / np.maximum(_oi_close_arr, 1e-9)
                        _hit = (_dist > 0) & (_dist < _oi_dist_pct)
                        _oi_block = _oi_block | _hit
                entry_sig = entry_sig & ~_oi_block
            # GR_HTF: 7-indicator multi-TF confirmation gate (vectorized, 2026-05-10)
            _gr_htf_min_tfs = int(getattr(cfg, 'GOLDEN_RULE_HTF_MIN_TFS', 0))
            if _gr_htf_min_tfs > 0:
                _gr_htf_min_ind = int(getattr(cfg, 'GOLDEN_RULE_MIN_IND', 2))
                try:
                    _htf_mask = _gr_htf_gate_vec(npz, n, is_long, _gr_htf_min_tfs, _gr_htf_min_ind, cfg)
                    entry_sig = entry_sig & _htf_mask
                except Exception:
                    pass
            # Phase 5 P/C ratio injection — when extremely bullish/bearish, ADD entry signal
            # at the very next bar (signal triggers the existing entry on momentum-up/down).
            # Snapshot-static — fires only if today's near-money P/C is at extreme on entry path.
            if (getattr(cfg, 'OPTIONS_OI_BACKTEST_ENABLED', False)
                    and getattr(cfg, 'MODE', 'crypto') == 'tradier'
                    and bool(getattr(cfg, 'OPTIONS_OI_PC_INJECT_ENABLED', False))
                    and _oi_near_money_pc is not None
                    and _oi_total_oi >= int(getattr(cfg, 'OPTIONS_OI_MIN_TOTAL_OI', 1000))):
                _pc = _oi_near_money_pc
                _bull_thr = float(getattr(cfg, 'OPTIONS_OI_PC_BULLISH_THRESH', 0.6))
                _bear_thr = float(getattr(cfg, 'OPTIONS_OI_PC_BEARISH_THRESH', 1.4))
                # Static bias: extreme bullish OI → permit only LONG entries through; extreme bearish → only SHORT.
                # Mid-range = no injection (existing entry_sig stands).
                _bias_long = _pc < _bull_thr
                _bias_short = _pc > _bear_thr
                if is_long and not _bias_long and (_bias_short or False):
                    # Strong bearish OI sentiment + LONG entry → block (sentiment confirms downside)
                    entry_sig = entry_sig & False  # turn off LONG entries
                elif (not is_long) and not _bias_short and (_bias_long or False):
                    # Strong bullish OI sentiment + SHORT entry → block
                    entry_sig = entry_sig & False

            # Phase 4 (2026-04-30): OR-merge indicator-only process_position exit gates
            # (WT_EXHAUST, WT_PERCENTILE, E_1 delta, E_3 structure, WT_4H_VEL_full).
            # State-dependent gates (DC_HOPELESS needs entry_price; WT_4H_VEL needs profit/age)
            # are applied scalar-style inside the trade loop using precomputed arrays.
            _exit_gate_extras = None
            if getattr(cfg, 'USE_PROCESS_POSITION_EXIT_GATES', False):
                from position_evaluator import evaluate_exit_gates_vec
                _exit_gate_extras = evaluate_exit_gates_vec(npz, is_long=is_long, config=cfg, ltf=_ltf)
                # Indicator-only gates fold into exit_sig directly.
                exit_sig = exit_sig | _exit_gate_extras['wt_exhaust'] | _exit_gate_extras['wt_percentile'] | _exit_gate_extras['e1_wt_delta'] | _exit_gate_extras['e3_structure']
            # ===== ENGINE-RETROFIT 2026-04-30 PHASE 2: same-side reentry window =====
            # Pre-compute per-side reentry mask using position_evaluator.evaluate_reentry_vec.
            # Per-bar gate: FLAT + (i - last_exit_bar) within [MIN_GAP, MAX_AGE] + rule-enabled.
            # Bypasses HTF/score gates — these are profit-of-the-recent-exit reentries.
            _reentry_phase2_active = bool(getattr(cfg, 'REENTRY_ENABLED', False))
            _reentry_window_fire = None
            if _reentry_phase2_active:
                from position_evaluator import evaluate_reentry_vec, BLOCK_NAMES
                _re_vec = evaluate_reentry_vec(npz, is_long=is_long, config=cfg, ltf=_ltf)
                _re_rules = set(getattr(cfg, 'REENTRY_RULES_ENABLED', ('B04', 'B11', 'B12', 'B15')))
                # Map rule prefixes (e.g. 'B04') to block_id numbers via BLOCK_NAMES.
                _re_block_ids = []
                for _bid, _bname in BLOCK_NAMES.items():
                    _bprefix = _bname.split('_', 1)[0]  # 'B15_STRONG_TREND' -> 'B15'
                    if _bprefix in _re_rules:
                        _re_block_ids.append(_bid)
                _re_id_arr = _re_vec['block_id']
                _reentry_window_fire = _re_vec['fire'] & np.isin(_re_id_arr, _re_block_ids)
            _reentry_max_age = int(getattr(cfg, 'REENTRY_MAX_AGE_BARS', 30))
            _reentry_min_gap_p2 = int(getattr(cfg, 'REENTRY_PHASE2_MIN_GAP_BARS', 1))
            # ═══ 2026-04-26 HEDGE GATE PRECOMPUTE — vectorized port of LIVE rules from ez_positions_quick ═══
            # Three gates govern hedge OPEN: HEDGE_DC_RESISTANCE_GATE / HEDGE_WT_VEL_GATE / HEDGE_DETERIORATING_GAIN.
            # All sweep-testable (default False here for backward compat with existing autonomous_search runs).
            # Logic: hedge SHORT defends LONG main → reject if symbol at SUPPORT or WT decel down.
            # Hedge LONG defends SHORT main → reject if symbol at RESISTANCE or WT decel up.
            _hedge_dc_gate_on = bool(getattr(cfg, 'HEDGE_DC_RESISTANCE_GATE_ENABLED', False))
            _hedge_wt_vel_gate_on = bool(getattr(cfg, 'HEDGE_WT_VEL_GATE_ENABLED', False))
            _hedge_det_on = bool(getattr(cfg, 'HEDGE_DETERIORATING_GAIN_ENABLED', False))
            _hedge_dc_long_th = float(getattr(cfg, 'HEDGE_DC_LONG_REJECT_DCP', 0.85))
            _hedge_dc_short_th = float(getattr(cfg, 'HEDGE_DC_SHORT_REJECT_DCP', 0.15))
            _hedge_det_delta = float(getattr(cfg, 'HEDGE_DETERIORATING_GAIN_DELTA_PP', 0.10))
            _hedge_det_window = max(1, int(getattr(cfg, 'HEDGE_DETERIORATING_GAIN_WINDOW_BARS', 5)))
            _hedge_dc_ok = None; _hedge_wt_vel_ok = None
            if _hedge_dc_gate_on or _hedge_wt_vel_gate_on:
                _h_dcp_1h = _safe(npz, 'dc_position_1h', n, 0.5)
                _h_dcp_4h = _safe(npz, 'dc_position_4h', n, 0.5)
                _h_wtv_1h = _safe(npz, 'wt_velocity_1h', n, 0.0)
                _h_wtv_4h = _safe(npz, 'wt_velocity_4h', n, 0.0)
                if is_long:
                    # LONG main → SHORT hedge candidate. Bad SHORT entry if symbol at SUPPORT (low dcp) OR vel up.
                    _hedge_dc_ok = (_h_dcp_1h > _hedge_dc_short_th) & (_h_dcp_4h > _hedge_dc_short_th)
                    _hedge_wt_vel_ok = ~((_h_wtv_1h >= 0) & (_h_wtv_4h >= 0))
                else:
                    # SHORT main → LONG hedge candidate. Bad LONG entry if symbol at RESISTANCE (high dcp) OR vel down.
                    _hedge_dc_ok = (_h_dcp_1h < _hedge_dc_long_th) & (_h_dcp_4h < _hedge_dc_long_th)
                    _hedge_wt_vel_ok = ~((_h_wtv_1h <= 0) & (_h_wtv_4h <= 0))
            # NOLOSS_BYPASS_WT_5OF5 precompute: 5/5 WT TFs (LTF/15m/1h/4h/D) against pos → allow loss exit.
            # Default OFF; sweep-only flag. Mirror of ez_manage/tradier exception.
            _nlb_5of5_mask = None
            if bool(getattr(cfg, 'NOLOSS_BYPASS_WT_5OF5_ENABLED', False)):
                _nlb_min = int(getattr(cfg, 'NOLOSS_BYPASS_WT_5OF5_MIN_TFS', 5))
                _nlb_ltf = getattr(cfg, 'LTF', '3m')
                _nlb_w1l = _safe(npz, f'wt1_{_nlb_ltf}', n); _nlb_w2l = _safe(npz, f'wt2_{_nlb_ltf}', n)
                _nlb_w115 = _safe(npz, 'wt1_15m', n); _nlb_w215 = _safe(npz, 'wt2_15m', n)
                _nlb_w11h = _safe(npz, 'wt1_1h', n); _nlb_w21h = _safe(npz, 'wt2_1h', n)
                _nlb_w14h = _safe(npz, 'wt1_4h', n); _nlb_w24h = _safe(npz, 'wt2_4h', n)
                _nlb_w1D = _safe(npz, 'wt1_D', n); _nlb_w2D = _safe(npz, 'wt2_D', n)
                if is_long:
                    _nlb_cnt = (_nlb_w1l < _nlb_w2l).astype(int) + (_nlb_w115 < _nlb_w215).astype(int) + (_nlb_w11h < _nlb_w21h).astype(int) + (_nlb_w14h < _nlb_w24h).astype(int) + (_nlb_w1D < _nlb_w2D).astype(int)
                else:
                    _nlb_cnt = (_nlb_w1l > _nlb_w2l).astype(int) + (_nlb_w115 > _nlb_w215).astype(int) + (_nlb_w11h > _nlb_w21h).astype(int) + (_nlb_w14h > _nlb_w24h).astype(int) + (_nlb_w1D > _nlb_w2D).astype(int)
                _nlb_5of5_mask = _nlb_cnt >= _nlb_min
            # WRONG_SIDE_ABS_KILL precompute v2 (2026-04-21): K irrelevant, WT + divergence.
            # Kill when: (WT_against >= WT_TFS_REQUIRED) OR (WT_against >= WT_TFS_REDUCED AND divergence_count >= DIV_TFS_REQUIRED).
            # Divergence per TF (LONG): price now > price K ago AND wt1 now < wt1 K ago (bearish divergence).
            # Mirror for SHORT. DIV_LOOKBACK_BARS controls the HH/LH horizon.
            _ws_kill_enabled = bool(getattr(cfg, 'WRONG_SIDE_ABS_KILL_ENABLED', False))
            _ws_wt_mask_full = None; _ws_wt_mask_reduced = None; _ws_div_mask = None; _ws_min_age_bars = 0
            if _ws_kill_enabled:
                _ws_wt_req = int(getattr(cfg, 'WRONG_SIDE_WT_TFS_REQUIRED', 5))
                _ws_wt_red = int(getattr(cfg, 'WRONG_SIDE_WT_TFS_REDUCED', 3))
                _ws_div_req = int(getattr(cfg, 'WRONG_SIDE_DIV_TFS_REQUIRED', 2))
                _ws_div_lb = int(getattr(cfg, 'WRONG_SIDE_DIV_LOOKBACK_BARS', 20))
                _ws_min_age_min = float(getattr(cfg, 'WRONG_SIDE_MIN_AGE_MIN', 30))
                _ltf_min_map = {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '1h': 60}
                _ws_ltf = getattr(cfg, 'LTF', '3m')
                _ws_min_age_bars = max(1, int(_ws_min_age_min / _ltf_min_map.get(_ws_ltf, 3)))
                # 5-WT-TF against mask
                _ws_w1l = _safe(npz, f'wt1_{_ws_ltf}', n); _ws_w2l = _safe(npz, f'wt2_{_ws_ltf}', n)
                _ws_w115 = _safe(npz, 'wt1_15m', n); _ws_w215 = _safe(npz, 'wt2_15m', n)
                _ws_w11h = _safe(npz, 'wt1_1h', n); _ws_w21h = _safe(npz, 'wt2_1h', n)
                _ws_w14h = _safe(npz, 'wt1_4h', n); _ws_w24h = _safe(npz, 'wt2_4h', n)
                _ws_w1D = _safe(npz, 'wt1_D', n); _ws_w2D = _safe(npz, 'wt2_D', n)
                if is_long:
                    _ws_wt_cnt = (_ws_w1l < _ws_w2l).astype(int) + (_ws_w115 < _ws_w215).astype(int) + (_ws_w11h < _ws_w21h).astype(int) + (_ws_w14h < _ws_w24h).astype(int) + (_ws_w1D < _ws_w2D).astype(int)
                else:
                    _ws_wt_cnt = (_ws_w1l > _ws_w2l).astype(int) + (_ws_w115 > _ws_w215).astype(int) + (_ws_w11h > _ws_w21h).astype(int) + (_ws_w14h > _ws_w24h).astype(int) + (_ws_w1D > _ws_w2D).astype(int)
                _ws_wt_mask_full = _ws_wt_cnt >= _ws_wt_req
                _ws_wt_mask_reduced = _ws_wt_cnt >= _ws_wt_red
                # Divergence per TF: rolled-back K bars for price and WT1. Bearish div = price HH + wt LH.
                def _roll_back(arr, k):
                    out = np.roll(arr, k)
                    if k > 0: out[:k] = arr[0] if len(arr) > 0 else 0
                    return out
                _cx = close
                _cx_back = _roll_back(_cx, _ws_div_lb)
                _w1l_back = _roll_back(_ws_w1l, _ws_div_lb)
                _w115_back = _roll_back(_ws_w115, _ws_div_lb)
                _w11h_back = _roll_back(_ws_w11h, _ws_div_lb)
                _w14h_back = _roll_back(_ws_w14h, _ws_div_lb)
                _w1D_back = _roll_back(_ws_w1D, _ws_div_lb)
                if is_long:
                    _div_ltf = (_cx > _cx_back) & (_ws_w1l < _w1l_back)
                    _div_15 = (_cx > _cx_back) & (_ws_w115 < _w115_back)
                    _div_1h = (_cx > _cx_back) & (_ws_w11h < _w11h_back)
                    _div_4h = (_cx > _cx_back) & (_ws_w14h < _w14h_back)
                    _div_D = (_cx > _cx_back) & (_ws_w1D < _w1D_back)
                else:
                    _div_ltf = (_cx < _cx_back) & (_ws_w1l > _w1l_back)
                    _div_15 = (_cx < _cx_back) & (_ws_w115 > _w115_back)
                    _div_1h = (_cx < _cx_back) & (_ws_w11h > _w11h_back)
                    _div_4h = (_cx < _cx_back) & (_ws_w14h > _w14h_back)
                    _div_D = (_cx < _cx_back) & (_ws_w1D > _w1D_back)
                _ws_div_cnt = _div_ltf.astype(int) + _div_15.astype(int) + _div_1h.astype(int) + _div_4h.astype(int) + _div_D.astype(int)
                _ws_div_mask = _ws_div_cnt >= _ws_div_req
            _rz_break_arr = (_rz_break_long if is_long else _rz_break_short) if _rz_break_enabled else None
            # Adaptive exit: precompute the extra bars that TFS-1 adds vs normal exit_sig
            _adaptive_exit_enabled = bool(getattr(cfg, 'ADAPTIVE_EXIT_TFS_ENABLED', False))
            _adaptive_gain_pct = float(getattr(cfg, 'ADAPTIVE_EXIT_TFS_GAIN_PCT', 2.0))
            exit_sig_extra = None
            if _adaptive_exit_enabled:
                _a_ltf = getattr(cfg, 'LTF', '3m')
                _aw1l = _safe(npz, f'wt1_{_a_ltf}', n); _aw2l = _safe(npz, f'wt2_{_a_ltf}', n)
                _aw115 = _safe(npz, 'wt1_15m', n); _aw215 = _safe(npz, 'wt2_15m', n)
                _aw11h = _safe(npz, 'wt1_1h', n); _aw21h = _safe(npz, 'wt2_1h', n)
                if is_long:
                    _a_cnt = (_aw1l < _aw2l).astype(int) + (_aw115 < _aw215).astype(int) + (_aw11h < _aw21h).astype(int)
                else:
                    _a_cnt = (_aw1l > _aw2l).astype(int) + (_aw115 > _aw215).astype(int) + (_aw11h > _aw21h).astype(int)
                _loose_tfs = max(1, cfg.WT_EXIT_MIN_TFS - 1)
                exit_sig_extra = (_a_cnt >= _loose_tfs) & ~exit_sig
            # PARTIAL_EXIT: precompute remainder exit signal (looser WT TFS for the second half)
            # 2026-04-21: PARTIAL_PROFIT_LOCK overrides PARTIAL_EXIT_* when enabled. Same state
            # machine (50% at gain_pct, arm at arm_pct, close remainder when price returns to
            # first-exit price ≡ gain_pct). Maps PPL keys to internal _pe_* vars.
            _ppl_enabled_q = bool(getattr(cfg, 'PARTIAL_PROFIT_LOCK_ENABLED', False))
            if _ppl_enabled_q:
                _pe_enabled = True
                # MODE-AWARE PPL (2026-04-26 fix): tradier sweeps overriding *_TRADIER were ignored.
                if _mode == 'tradier':
                    _pe_frac = float(getattr(cfg, 'PARTIAL_PROFIT_LOCK_FRAC_TRADIER', getattr(cfg, 'PARTIAL_PROFIT_LOCK_FRAC', 0.5)))
                    _pe_pct = float(getattr(cfg, 'PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER', getattr(cfg, 'PARTIAL_PROFIT_LOCK_GAIN_PCT', 0.5)))
                    _pe_trail_arm = float(getattr(cfg, 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER', getattr(cfg, 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT', 0.7)))
                else:
                    _pe_frac = float(getattr(cfg, 'PARTIAL_PROFIT_LOCK_FRAC', 0.5))
                    _pe_pct = float(getattr(cfg, 'PARTIAL_PROFIT_LOCK_GAIN_PCT', 0.5))
                    _pe_trail_arm = float(getattr(cfg, 'PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT', 0.7))
                _pe_trail_floor = _pe_pct
                _pe_be_buffer = float(getattr(cfg, 'PARTIAL_BE_BUFFER_PCT', 0.0))
            else:
                _pe_enabled = bool(getattr(cfg, 'PARTIAL_EXIT_ENABLED', False))
                _pe_frac = float(getattr(cfg, 'PARTIAL_EXIT_FRAC', 0.5))
                _pe_pct = float(getattr(cfg, 'PARTIAL_EXIT_PCT', 0.5))
                _pe_trail_arm = float(getattr(cfg, 'PARTIAL_TRAIL_ARM_PCT', 0.7))
                _pe_trail_floor = float(getattr(cfg, 'PARTIAL_TRAIL_FLOOR_PCT', 0.5))
                _pe_be_buffer = float(getattr(cfg, 'PARTIAL_BE_BUFFER_PCT', 0.0))
            _pe_rem_tfs = int(getattr(cfg, 'PARTIAL_REMAINDER_EXIT_TFS', 2) or 2)
            _use_cross = bool(getattr(cfg, 'WT_EXIT_USE_CROSS_EVENTS', False))
            exit_sig_rem = exit_sig
            if _pe_enabled and (_pe_rem_tfs != cfg.WT_EXIT_MIN_TFS or _use_cross):
                _ltf_pe = getattr(cfg, 'LTF', '3m')
                _pe_w1l = _safe(npz, f'wt1_{_ltf_pe}', n); _pe_w2l = _safe(npz, f'wt2_{_ltf_pe}', n)
                if _use_cross:
                    _pe_raw_c15b = _safe(npz, 'wt_cross_bear_15m', n).astype(bool)
                    _pe_raw_c1hb = _safe(npz, 'wt_cross_bear_1h', n).astype(bool)
                    _pe_raw_c15u = _safe(npz, 'wt_cross_bull_15m', n).astype(bool)
                    _pe_raw_c1hu = _safe(npz, 'wt_cross_bull_1h', n).astype(bool)
                    _pe_exp_c15b = np.zeros(n, dtype=bool); _pe_exp_c1hb = np.zeros(n, dtype=bool)
                    _pe_exp_c15u = np.zeros(n, dtype=bool); _pe_exp_c1hu = np.zeros(n, dtype=bool)
                    for _k in range(_bph_15m):
                        _pe_exp_c15b[_k:] |= _pe_raw_c15b[:n - _k]; _pe_exp_c15u[_k:] |= _pe_raw_c15u[:n - _k]
                    for _k in range(_bph_1h):
                        _pe_exp_c1hb[_k:] |= _pe_raw_c1hb[:n - _k]; _pe_exp_c1hu[_k:] |= _pe_raw_c1hu[:n - _k]
                    if is_long:
                        _pe_wt_ag = (_pe_w1l < _pe_w2l).astype(int) + _pe_exp_c15b.astype(int) + _pe_exp_c1hb.astype(int)
                    else:
                        _pe_wt_ag = (_pe_w1l > _pe_w2l).astype(int) + _pe_exp_c15u.astype(int) + _pe_exp_c1hu.astype(int)
                else:
                    _pe_w115 = _safe(npz, 'wt1_15m', n); _pe_w215 = _safe(npz, 'wt2_15m', n)
                    _pe_w11h = _safe(npz, 'wt1_1h', n); _pe_w21h = _safe(npz, 'wt2_1h', n)
                    if is_long:
                        _pe_wt_ag = (_pe_w1l < _pe_w2l).astype(int) + (_pe_w115 < _pe_w215).astype(int) + (_pe_w11h < _pe_w21h).astype(int)
                    else:
                        _pe_wt_ag = (_pe_w1l > _pe_w2l).astype(int) + (_pe_w115 > _pe_w215).astype(int) + (_pe_w11h > _pe_w21h).astype(int)
                exit_sig_rem = _pe_wt_ag >= _pe_rem_tfs
            # SYMGATE: strip entries that coincide with exit signals — mirror exit rubric applied to entries.
            # ENTRY_SYMGATE_ENABLED covers fresh entries; REENTRY_SYMGATE_ENABLED mirrors it in the vectorized loop
            # (no separate reentry path here) — either flag turns it on.
            if bool(getattr(cfg, 'ENTRY_SYMGATE_ENABLED', False)) or bool(getattr(cfg, 'REENTRY_SYMGATE_ENABLED', False)):
                entry_sig = entry_sig & ~exit_sig
            # Chapter-E WINNER_PROTECT proxy — precompute per-bar "HTF-aligned" mask for use inside loop.
            wp_enabled = bool(getattr(cfg, 'WINNER_PROTECT_ENABLED', False))
            wp_gain_pct = float(getattr(cfg, 'WINNER_PROTECT_GAIN_PCT', 2.0))
            if wp_enabled:
                _wp_wt1_1h = _safe(npz, 'wt1_1h', n); _wp_wt2_1h = _safe(npz, 'wt2_1h', n)
                _wp_wt1_4h = _safe(npz, 'wt1_4h', n); _wp_wt2_4h = _safe(npz, 'wt2_4h', n)
                _wp_wt1_D = _safe(npz, 'wt1_D', n); _wp_wt2_D = _safe(npz, 'wt2_D', n)
                if is_long:
                    _wp_aligned = (_wp_wt1_1h > _wp_wt2_1h) & (_wp_wt1_4h > _wp_wt2_4h) & (_wp_wt1_D > _wp_wt2_D)
                else:
                    _wp_aligned = (_wp_wt1_1h < _wp_wt2_1h) & (_wp_wt1_4h < _wp_wt2_4h) & (_wp_wt1_D < _wp_wt2_D)
            # 2026-04-20: Dynamic scoring precompute — counter-exit + interval augment.
            _dyn_counter_enabled = bool(getattr(cfg, 'DYNAMIC_SCORE_COUNTER_EXIT_ENABLED', False))
            _dyn_counter_thr = float(getattr(cfg, 'DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD', 55.0))
            _dyn_aug_enabled = bool(getattr(cfg, 'DYNAMIC_SCORE_AUGMENT_ENABLED', False))
            _dyn_aug_jump = float(getattr(cfg, 'DYNAMIC_SCORE_AUGMENT_MIN_JUMP', 25.0))
            _dyn_aug_interval = int(getattr(cfg, 'DYNAMIC_SCORE_AUGMENT_INTERVAL', 5) or 5)
            _dyn_same_score = None; _dyn_counter_score = None
            if _dyn_counter_enabled or _dyn_aug_enabled:
                _dyn_same_score = _compute_le_score_arr(npz, n, is_long, cfg)
                _dyn_counter_score = _compute_le_score_arr(npz, n, not is_long, cfg)
            _le_tier_sizing = bool(getattr(cfg, 'LE_TIER_SIZING_ENABLED', False)) and bool(getattr(cfg, 'LOCAL_EXTREMES_SCORER_ENABLED', False))
            _le_sz_mult_arr = None
            if _le_tier_sizing:
                _le_s_pre = _dyn_same_score if _dyn_same_score is not None else _compute_le_score_arr(npz, n, is_long, cfg)
                _le_sz_mult_arr = np.where(_le_s_pre >= 75, 8.33, np.where(_le_s_pre >= 60, 4.17, np.where(_le_s_pre >= 45, 1.67, np.where(_le_s_pre >= 30, 0.42, np.where(_le_s_pre >= 15, 0.083, 1.0)))))
            _bfs_enabled = bool(getattr(cfg, 'DC_BREAKOUT_FAILED_STOP_ENABLED', False))
            # ALL_TF_BRAKE: count of TFs (LTF, 15m, 1h, 4h, D, W, M) against position direction
            _atb_enabled = bool(getattr(cfg, 'ALL_TF_BRAKE_ENABLED', False))
            _atb_min_tfs = int(getattr(cfg, 'ALL_TF_BRAKE_MIN_TFS', 5))
            _atb_against = None
            if _atb_enabled:
                _atb_tfs = [(_safe(npz, f'wt1_{_ltf}', n), _safe(npz, f'wt2_{_ltf}', n)),
                            (_safe(npz, 'wt1_15m', n), _safe(npz, 'wt2_15m', n)),
                            (_safe(npz, 'wt1_1h', n), _safe(npz, 'wt2_1h', n)),
                            (_safe(npz, 'wt1_4h', n), _safe(npz, 'wt2_4h', n)),
                            (_safe(npz, 'wt1_D', n), _safe(npz, 'wt2_D', n)),
                            (_safe(npz, 'wt1_W', n), _safe(npz, 'wt2_W', n)),
                            (_safe(npz, 'wt1_M', n), _safe(npz, 'wt2_M', n))]
                if is_long:
                    _atb_against = sum((w1 < w2).astype(int) for w1, w2 in _atb_tfs)
                else:
                    _atb_against = sum((w1 > w2).astype(int) for w1, w2 in _atb_tfs)
            # === 2026-04-26 NEW SIZE-SCALAR PRECOMPUTES ===
            # VOL_TARGET: per-bar position-size scalar = clip(VOL_TARGET_PCT / max(realized_vol, 1.0), low, high).
            # NPZ field is realized vol % per-bar (yz/pk/gk 60d on D base, or _20_4h on 4h base).
            _vt_enabled = bool(getattr(cfg, 'VOL_TARGET_ENABLED', False))
            _vt_arr = None
            if _vt_enabled:
                _vt_field = str(getattr(cfg, 'VOL_TARGET_FIELD', 'yz_vol_60_d'))
                _vt_target = float(getattr(cfg, 'VOL_TARGET_PCT', 20.0))
                _vt_low = float(getattr(cfg, 'VOL_TARGET_LOW_CAP', 0.25))
                _vt_high = float(getattr(cfg, 'VOL_TARGET_HIGH_CAP', 2.0))
                _vt_realized = _safe(npz, _vt_field, n, 0.0)
                _vt_realized_clip = np.maximum(_vt_realized, 1.0)
                _vt_arr = np.clip(_vt_target / _vt_realized_clip, _vt_low, _vt_high).astype(np.float64)
            # TSMOM_BOOK_SCALAR: per-bar agreement of side vs sign(close[i] - close[i - lookback]).
            # Vectorized engine is per-symbol — book-agreement collapses to single-symbol momentum signal.
            # agreement = 1.0 when sign matches direction, 0.0 otherwise; mapped via clip.
            _tsmom_enabled = bool(getattr(cfg, 'TSMOM_BOOK_SCALAR_ENABLED', False))
            _tsmom_arr = None
            if _tsmom_enabled:
                _tsmom_lb = int(getattr(cfg, 'TSMOM_LOOKBACK_BARS', 252) or 252)
                _tsmom_min = float(getattr(cfg, 'TSMOM_MIN_AGREEMENT', 0.5))
                _tsmom_low = float(getattr(cfg, 'TSMOM_LOW_CAP', 0.25))
                _tsmom_high = float(getattr(cfg, 'TSMOM_HIGH_CAP', 1.5))
                _tsmom_lb_eff = min(max(1, _tsmom_lb), max(1, n - 1))
                _tsmom_close_back = np.roll(close, _tsmom_lb_eff)
                _tsmom_close_back[:_tsmom_lb_eff] = close[0] if n > 0 else 0.0
                _tsmom_diff = close - _tsmom_close_back
                if is_long:
                    _tsmom_agree = (_tsmom_diff > 0).astype(np.float64)
                else:
                    _tsmom_agree = (_tsmom_diff < 0).astype(np.float64)
                _tsmom_raw = _tsmom_min + (1.0 - _tsmom_min) * _tsmom_agree * 2.0
                _tsmom_arr = np.clip(_tsmom_raw, _tsmom_low, _tsmom_high).astype(np.float64)
            # DD_KELLY: running per-symbol equity from sym_pnl (cumulative %). Tier scalar resolved at entry.
            # Sizing-only — NEVER an exit (per CLAUDE.md feedback_no_pct_stops).
            _ddk_enabled = bool(getattr(cfg, 'DD_KELLY_ENABLED', False))
            _ddk_t1 = float(getattr(cfg, 'DD_KELLY_TIER1_PCT', 10.0))
            _ddk_t2 = float(getattr(cfg, 'DD_KELLY_TIER2_PCT', 15.0))
            _ddk_t3 = float(getattr(cfg, 'DD_KELLY_TIER3_PCT', 20.0))
            # Helper: combined size scalar from VOL_TARGET / DD_KELLY / TSMOM. Uses running sym_pnl
            # for DD-tier resolution (per-symbol equity-curve proxy; vectorized engine has no global account).
            def _new_sizing_scalar(_idx):
                _s = 1.0
                if _vt_arr is not None:
                    _s *= float(_vt_arr[_idx])
                if _tsmom_arr is not None:
                    _s *= float(_tsmom_arr[_idx])
                if _ddk_enabled and sym_pnl:
                    # Equity curve: cumulative sum of % returns. Peak vs current → drawdown %.
                    _eq = np.cumsum(sym_pnl)
                    _peak = float(_eq.max()) if len(_eq) > 0 else 0.0
                    _now = float(_eq[-1]) if len(_eq) > 0 else 0.0
                    _dd = max(0.0, _peak - _now)  # equity-curve DD in % points
                    if _dd >= _ddk_t3:
                        _s *= 0.125
                    elif _dd >= _ddk_t2:
                        _s *= 0.25
                    elif _dd >= _ddk_t1:
                        _s *= 0.5
                return _s
            # Phase 3d: per-trade qty pipeline ratio at entry bar.
            # Multiplied into _cur_sz_mult at each of the 3 entry sites; existing
            # close sites already do `pnl * _cur_sz_mult` so qty pipeline propagates
            # for free. Returns 1.0 when APPLY_QTY_PIPELINE_TO_PNL is False (existing
            # behavior preserved).
            _qty_pipeline_on = bool(getattr(cfg, 'APPLY_QTY_PIPELINE_TO_PNL', False))
            if _qty_pipeline_on:
                from position_evaluator import compute_trade_qty_core
                _w14h = _safe(npz, 'wt1_4h', n)
                _w24h = _safe(npz, 'wt2_4h', n)
                _w1D = _safe(npz, 'wt1_D', n)
                _w2D = _safe(npz, 'wt2_D', n)
            def _qty_pipe_ratio(_i: int, _il: bool, _rz: bool = False) -> float:
                if not _qty_pipeline_on:
                    return 1.0
                _cp = float(close[_i]) if _i < len(close) else 0.0
                if _cp <= 0:
                    return 1.0
                _ind = {'wt1_4h': float(_w14h[_i]), 'wt2_4h': float(_w24h[_i]),
                        'wt1_D': float(_w1D[_i]), 'wt2_D': float(_w2D[_i])}
                _bq = cfg.START_POSITION_SIZE / _cp
                _fq, _ = compute_trade_qty_core(_bq, 'OPEN', _il, False, _cp, _ind, cfg, is_rz_entry=_rz)
                return float(_fq / _bq) if _bq > 0 else 1.0
            in_pos = False; ep = 0.0; eb = 0; cd = 0
            _aug_done = False; _aug_wt_d_last = 0.0; _aug_px_last = 0.0
            _aug_4h_done = False; _aug_4h_wt_last = 0.0; _aug_4h_px_last = 0.0
            _dyn_aug_done = False; _dyn_entry_score = 0.0; _cur_sz_mult = 1.0
            _entry_was_breakout = False
            _entry_was_rz_break = False
            _rz_entry_bar = -1
            _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
            _qr_enabled = bool(getattr(cfg, 'QUICK_REENTRY_60MIN_ENABLED', False))
            _qr_window = int(getattr(cfg, 'QUICK_REENTRY_60MIN_WINDOW_BARS', 12) or 12)
            _qr_min_pct = float(getattr(cfg, 'QUICK_REENTRY_60MIN_MIN_PCT', 0.3)) / 100.0
            _qr_exit_px = 0.0; _qr_exit_bar = -9999
            # TIER1_PRICE_CROSS_REENTRY — mirrors MANDATORY_PRICE_CROSS live behavior:
            # within 1h (20 bars @ 3m): price >= exit → enter at 50% SIZE, NO WT GATE, bypass cooldown.
            # after 1h: price >= exit + WT 15m aligned → enter at 50-100% based on k_15m.
            # Models live: reentry_enforcement_loop_epq T1_PRICE_CROSS_MANDATORY path.
            _t1pc_enabled = bool(getattr(cfg, 'REENTRY_K15M_PARTIAL_ENABLED', True))
            _t1pc_window = int(getattr(cfg, 'TIER1_PRICE_CROSS_WINDOW_BARS', 400) or 400)
            _t1pc_pct = float(getattr(cfg, 'TIER1_PRICE_CROSS_MIN_PCT', 0.0)) / 100.0
            _t1pc_1h_bars = 20  # 20 bars × 3m = 60min first-hour window — no-questions-asked zone
            _t1pc_exit_px = 0.0; _t1pc_exit_bar = -9999
            hedge_in_pos = False; hedge_ep = 0.0; hedge_eb = 0
            hedge_min_hold = int(getattr(cfg, 'HEDGE_MIN_HOLD_BARS', 10) or 10)
            sl_enabled = cfg.STOP_LOSS_ENABLED
            sl_pct = cfg.STOP_LOSS_PCT
            _aug_pt_enabled = bool(getattr(cfg, 'AUGMENT_PT_ENABLED', False))
            _aug_pt_pct = float(getattr(cfg, 'AUGMENT_PT_PCT', 0.5))
            # REENTRY_MIN_GAP_BARS — extra cooldown after exit before next entry. 0 = use COOLDOWN_BARS only.
            min_gap_bars = int(getattr(cfg, 'REENTRY_MIN_GAP_BARS', 0) or 0)
            # REENTRY_IF_MOMENTUM (wt_dc_delta.py:1048 port) — skip cooldown if K on selected TF still favorable
            _reentry_mom_enabled = bool(getattr(cfg, 'REENTRY_IF_MOMENTUM_ENABLED', True))
            _reentry_mom_window = int(getattr(cfg, 'REENTRY_IF_MOMENTUM_WINDOW_BARS', 5))
            _reentry_mom_tf = str(getattr(cfg, 'REENTRY_IF_MOMENTUM_TF', '15m'))
            _rm_k_arr = _safe(npz, f'stoch_k_{_reentry_mom_tf}', n, 50.0)
            _rm_d_arr = _safe(npz, f'stoch_d_{_reentry_mom_tf}', n, 50.0)
            _last_exit_bar = -9999
            _prev_in_pos = False
            # Trade recorder transition state (for chart visualization).
            _rec_entry_bar = -1
            _rec_entry_price = 0.0
            _rec_pending_exit_reason = ""
            _rec_pending_entry_reason = ""
            # ===== ENGINE-RETROFIT 2026-04-30 (registry: docs/engine_retrofit_registry.md) =====
            # Per-side pre-compute: shared for X1 (PEAK_GIVEBACK), X2 (BE_EROSION), X3 (K1M_EXTREME),
            # X4 (STRONG_REDUCE_K), E5 (HEDGE_PROTECT_LOSS), E6 (WINNER_AUGMENT).
            _peak_giveback_active = bool(getattr(cfg, 'PEAK_GIVEBACK_ENABLED', False))
            _be_erosion_active = bool(getattr(cfg, 'BE_EROSION_ENABLED', False))
            _k1m_extreme_active = bool(getattr(cfg, 'K1M_EXTREME_REVERSE_ENABLED', False))
            _strong_reduce_k_active = bool(getattr(cfg, 'STRONG_REDUCE_K_ENABLED', False))
            _hpl_active = bool(getattr(cfg, 'HEDGE_PROTECT_LOSS_ENABLED', False))
            _wa_active = bool(getattr(cfg, 'WINNER_AUGMENT_ENABLED', False))
            # X1 tunables (read once)
            _pg_peak_min = float(getattr(cfg, 'PEAK_GIVEBACK_PEAK_MIN_PCT', 0.50))
            _pg_drop_pct = float(getattr(cfg, 'PEAK_GIVEBACK_DROP_PCT', 0.5))
            _pg_min_age = int(getattr(cfg, 'PEAK_GIVEBACK_MIN_AGE_BARS', 0))
            _pg_hard_zero = bool(getattr(cfg, 'PEAK_GIVEBACK_HARD_ZERO_ENABLED', False))
            # X2 tunables
            _be_age_min = int(getattr(cfg, 'BE_EROSION_AGE_MIN_BARS', 30))
            _be_min_gain = float(getattr(cfg, 'BE_EROSION_MIN_GAIN', 0.0))
            _be_require_profit = bool(getattr(cfg, 'BE_EROSION_REQUIRE_PROFIT', True))
            _be_comm_buf = float(getattr(cfg, 'BE_EROSION_COMM_BUFFER', 0.05))
            # X3 tunables
            _k1m_hi = float(getattr(cfg, 'K1M_EXTREME_HIGH', 90.0))
            _k1m_lo = float(getattr(cfg, 'K1M_EXTREME_LOW', 10.0))
            _k1m_req_profit = bool(getattr(cfg, 'K1M_REVERSE_REQUIRES_PROFIT', True))
            # X4 tunables
            _srk_k15_long_min = float(getattr(cfg, 'SRK_K15M_LONG_MIN', 80.0))
            _srk_k1h_long_max = float(getattr(cfg, 'SRK_K1H_LONG_MAX', 30.0))
            _srk_k15_short_max = float(getattr(cfg, 'SRK_K15M_SHORT_MAX', 20.0))
            _srk_k1h_short_min = float(getattr(cfg, 'SRK_K1H_SHORT_MIN', 70.0))
            _srk_reduce_frac = float(getattr(cfg, 'SRK_REDUCE_FRAC', 0.5))
            # NPZ arrays (read once when needed)
            if _strong_reduce_k_active or _k1m_extreme_active:
                _retro_k_15m = _safe(npz, 'stoch_k_15m', n, default=50.0)
                _retro_k_1h = _safe(npz, 'stoch_k_1h', n, default=50.0)
            if _k1m_extreme_active:
                _retro_k_1m = _safe_k_1m(npz, n, ltf=getattr(cfg, 'LTF', '3m'))
                _retro_k_1m_zero_filled = bool((_retro_k_1m == 50.0).all())
                _retro_k_1m_prev = np.roll(_retro_k_1m, 1); _retro_k_1m_prev[0] = _retro_k_1m[0]
            # E5 HEDGE_PROTECT_LOSS tunables (independent of HEDGE_ENABLED)
            _hpl_trigger_loss = float(getattr(cfg, 'HPL_TRIGGER_LOSS_PCT', -2.0))
            _hpl_hedge_frac = float(getattr(cfg, 'HPL_HEDGE_FRAC', 1.0))
            _hpl_fire_once = bool(getattr(cfg, 'HPL_FIRE_ONCE', True))
            _hpl_cooldown_bars = int(getattr(cfg, 'HPL_COOLDOWN_BARS', 360))
            _hpl_fired_this_pos = False
            _hpl_last_fire_bar = -99999
            # E6 WINNER_AUGMENT tunables
            _wa_min_gain = float(getattr(cfg, 'WA_MIN_GAIN_PCT', 1.5))
            _wa_max_aug = int(getattr(cfg, 'WA_MAX_AUGMENTS', 2))
            _wa_growth_req = float(getattr(cfg, 'WA_GAIN_GROWTH_REQ', 0.5))
            _wa_aug_count = 0
            _wa_last_aug_gain = 0.0
            # Per-position running peak gain (reset on entry/exit; updated each bar)
            _peak_gain_running = 0.0
            for i in range(n):
                # Track exit transitions — used by REENTRY_IF_MOMENTUM bypass below.
                if _prev_in_pos and not in_pos:
                    _last_exit_bar = i - 1
                    if _generic_trades_buf is not None and _rec_entry_bar >= 0 and sym_pnl:
                        _ex_px = float(close[i - 1]) if i >= 1 and close[i - 1] > 0 else 0.0
                        _ex_ts = int(ts[i - 1]) if hasattr(ts, '__len__') and (i - 1) < len(ts) else 0
                        _en_ts = int(ts[_rec_entry_bar]) if hasattr(ts, '__len__') and _rec_entry_bar < len(ts) else 0
                        _en_reason = _rec_pending_entry_reason or ("BREAKOUT" if _entry_was_breakout else ("RZ_BREAK" if _entry_was_rz_break else "BOUNCE"))
                        # Prefer explicit branch label when set; else derive from state heuristics.
                        _ex_reason = _rec_pending_exit_reason or "TECH_EXIT"
                        if not _rec_pending_exit_reason:
                            try:
                                ix = i - 1
                                if ix < n and exit_sig is not None and bool(exit_sig[ix]):
                                    _ex_reason = "WT_EXIT"
                                if _rec_entry_price > 0:
                                    _adverse_pct = ((float(close[ix]) - _rec_entry_price) / _rec_entry_price * 100.0) * (1 if is_long else -1)
                                    if _adverse_pct < -1.5: _ex_reason = "HARD_LOSS"
                                    elif _adverse_pct < -0.5 and _rec_entry_bar > 0 and (ix - _rec_entry_bar) <= 3: _ex_reason = "QUICK_REVERSAL"
                            except Exception: pass
                        _generic_trades_buf.append({
                            "symbol": sym,
                            "side": "LONG" if is_long else "SHORT",
                            "entry_type": "BREAKOUT" if _entry_was_breakout else "BOUNCE",
                            "entry_reason": _en_reason,
                            "exit_reason": _ex_reason,
                            "entry_bar": int(_rec_entry_bar), "entry_ts": _en_ts,
                            "entry_price": float(_rec_entry_price),
                            "exit_bar": int(i - 1), "exit_ts": _ex_ts, "exit_price": _ex_px,
                            "pnl_pct": float(sym_pnl[-1]),
                            "pnl_usd": 0.0,
                            "duration_bars": int(i - 1 - _rec_entry_bar),
                            "stream": "primary",
                        })
                        _rec_entry_bar = -1
                        _rec_entry_price = 0.0
                        _rec_pending_exit_reason = ""
                        _rec_pending_entry_reason = ""
                if not _prev_in_pos and in_pos and _rec_entry_bar < 0:
                    _rec_entry_bar = i - 1 if i >= 1 else 0
                    _rec_entry_price = float(ep) if ep > 0 else float(close[_rec_entry_bar]) if close[_rec_entry_bar] > 0 else 0.0
                _prev_in_pos = in_pos
                if _qr_enabled and not in_pos and _qr_exit_px > 0 and (i - _qr_exit_bar) <= _qr_window:
                    px = close[i]
                    if px > 0:
                        if (is_long and px >= _qr_exit_px * (1.0 + _qr_min_pct)) or (not is_long and px <= _qr_exit_px * (1.0 - _qr_min_pct)):
                            in_pos = True; ep = px; eb = i
                            _aug_done = False; _aug_wt_d_last = _wt1_D_aug[i]; _aug_px_last = px
                            _aug_4h_done = False; _aug_4h_wt_last = _wt1_4H_aug[i]; _aug_4h_px_last = px
                            _cur_sz_mult = float(_le_sz_mult_arr[i]) if _le_sz_mult_arr is not None else 1.0
                            _cur_sz_mult *= _new_sizing_scalar(i)
                            _cur_sz_mult *= _qty_pipe_ratio(i, is_long, False)  # Phase 3d
                            if _gr_mult_arr is not None: _cur_sz_mult *= float(_gr_mult_arr[i])
                            _peak_gain_running = 0.0; _hpl_fired_this_pos = False; _wa_aug_count = 0; _wa_last_aug_gain = 0.0
                            _qr_exit_px = 0.0; cd = 0
                            continue
                if _t1pc_enabled and not in_pos and _t1pc_exit_px > 0 and (i - _t1pc_exit_bar) <= _t1pc_window:
                    px = close[i]
                    _t1pc_bars_since = i - _t1pc_exit_bar
                    _t1pc_crossed = px > 0 and ((is_long and px >= _t1pc_exit_px * (1.0 + _t1pc_pct)) or (not is_long and px <= _t1pc_exit_px * (1.0 - _t1pc_pct)))
                    _t1pc_1h_window = _t1pc_bars_since <= _t1pc_1h_bars
                    _t1pc_wt_ok = (is_long and _wt1_15m_h[i] > _wt2_15m_h[i]) or (not is_long and _wt1_15m_h[i] < _wt2_15m_h[i])
                    _k15m_arr_t1 = _safe(npz, 'stoch_k_15m', n, 50.0)
                    _t1pc_k15m = float(_k15m_arr_t1[i]) if _k15m_arr_t1 is not None else 50.0
                    _t1pc_sz = 0.5 if (_t1pc_1h_window or _t1pc_k15m > 70 or _t1pc_k15m < 30) else 1.0
                    if _t1pc_crossed and (_t1pc_1h_window or (cd <= 0 and _t1pc_wt_ok)):
                        in_pos = True; ep = px; eb = i
                        _aug_done = False; _aug_wt_d_last = _wt1_D_aug[i]; _aug_px_last = px
                        _aug_4h_done = False; _aug_4h_wt_last = _wt1_4H_aug[i]; _aug_4h_px_last = px
                        _cur_sz_mult = max(float(_le_sz_mult_arr[i]) if _le_sz_mult_arr is not None else 1.0, 1.0) * _t1pc_sz
                        _cur_sz_mult *= _new_sizing_scalar(i)
                        _cur_sz_mult *= _qty_pipe_ratio(i, is_long, False)  # Phase 3d
                        if _gr_mult_arr is not None: _cur_sz_mult *= float(_gr_mult_arr[i])
                        _dyn_aug_done = False; _dyn_entry_score = float(_dyn_same_score[i]) if _dyn_same_score is not None else 0.0
                        _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                        _entry_was_breakout = _bfs_enabled and ((px > _dc_h4_prev[i] and _dc_h4_prev[i] > 0) if is_long else (px < _dc_l4_prev[i] and _dc_l4_prev[i] > 0))
                        _entry_was_rz_break = _rz_break_arr is not None and bool(_rz_break_arr[i])
                        if _entry_was_rz_break: _rz_entry_bar = i
                        _peak_gain_running = 0.0; _hpl_fired_this_pos = False; _wa_aug_count = 0; _wa_last_aug_gain = 0.0
                        _t1pc_exit_px = 0.0; cd = 0
                        continue
                # REENTRY_IF_MOMENTUM bypass: if cooldown is active but we're within reentry
                # window and K still in favorable direction, let the entry check below fire.
                _reentry_mom_bypass = False
                if (_reentry_mom_enabled and not in_pos and _last_exit_bar >= 0
                        and (i - _last_exit_bar) <= _reentry_mom_window):
                    if is_long and _rm_k_arr[i] > _rm_d_arr[i]:
                        _reentry_mom_bypass = True
                    elif (not is_long) and _rm_k_arr[i] < _rm_d_arr[i]:
                        _reentry_mom_bypass = True
                if cd > 0:
                    cd -= 1
                    if not _reentry_mom_bypass:
                        continue
                px = close[i]
                if px <= 0: continue
                _rz_fires_here = _rz_break_arr is not None and bool(_rz_break_arr[i])
                # ===== PHASE 2 REENTRY WINDOW (2026-04-30) =====
                # If we exited recently (same side, since the for-is_long loop is per-side) and the
                # reentry vec mask fires for an enabled rule, fire entry without HTF/score gating.
                # This ports live GUARANTEED_PRICE_CROSS / DIRECTION_FAVORABLE_REENTRY paths.
                _phase2_reentry_fires = False
                if (_reentry_phase2_active and not in_pos
                        and _reentry_window_fire is not None
                        and _last_exit_bar >= 0
                        and (i - _last_exit_bar) >= _reentry_min_gap_p2
                        and (i - _last_exit_bar) <= _reentry_max_age
                        and bool(_reentry_window_fire[i])
                        and (not _intraday_enabled or _sec_of_day is None or _sec_of_day[i] < _intraday_entry_cutoff)):
                    _phase2_reentry_fires = True
                if not in_pos and (entry_sig[i] or _rz_fires_here or _phase2_reentry_fires) and (not _intraday_enabled or _sec_of_day is None or _sec_of_day[i] < _intraday_entry_cutoff):
                    in_pos = True; ep = px; eb = i; _aug_done = False; _aug_wt_d_last = _wt1_D_aug[i]; _aug_px_last = px; _aug_4h_done = False; _aug_4h_wt_last = _wt1_4H_aug[i]; _aug_4h_px_last = px
                    _dyn_entry_score = float(_dyn_same_score[i]) if _dyn_same_score is not None else 0.0
                    _cur_sz_mult = float(_le_sz_mult_arr[i]) if _le_sz_mult_arr is not None else 1.0
                    _cur_sz_mult *= _new_sizing_scalar(i)
                    _cur_sz_mult *= _qty_pipe_ratio(i, is_long, _rz_fires_here)  # Phase 3d
                    if _gr_mult_arr is not None: _cur_sz_mult *= float(_gr_mult_arr[i])
                    _dyn_aug_done = False
                    _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                    _entry_was_breakout = _bfs_enabled and ((ep > _dc_h4_prev[i] and _dc_h4_prev[i] > 0) if is_long else (ep < _dc_l4_prev[i] and _dc_l4_prev[i] > 0))
                    _entry_was_rz_break = _rz_fires_here
                    if _entry_was_rz_break: _rz_entry_bar = i
                    _peak_gain_running = 0.0; _hpl_fired_this_pos = False; _wa_aug_count = 0; _wa_last_aug_gain = 0.0
                    if _phase2_reentry_fires and not (entry_sig[i] or _rz_fires_here):
                        _rec_pending_entry_reason = "REENTRY_PHASE2"
                    continue
                if in_pos:
                    live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    # ENGINE-RETROFIT 2026-04-30: track running peak gain for X1 (PEAK_GIVEBACK).
                    if live_pnl > _peak_gain_running:
                        _peak_gain_running = live_pnl
                    # RZ NOLOSS BYPASS (independent): fires immediately on condition, no WT exit needed.
                    # Only for RZ-tagged entries. Bypass conditions checked every bar while in losing trade.
                    if _entry_was_rz_break and _rz_nl_mode != 'none' and cfg.NOLOSS_ENABLED and live_pnl < 0:
                        _rz_bypass_now = False
                        if _rz_nl_mode == 'bar_structure':
                            _bs_since = i - _rz_entry_bar
                            if _bs_since <= _rz_nl_bar_window and _rz_nl_bar_high is not None and _rz_nl_bar_low is not None:
                                if is_long:
                                    _rz_bypass_now = bool(_rz_nl_bar_high[i] < _rz_nl_bar_high_prev[i] and _rz_nl_bar_low[i] < _rz_nl_bar_low_prev[i])
                                else:
                                    _rz_bypass_now = bool(_rz_nl_bar_high[i] > _rz_nl_bar_high_prev[i] and _rz_nl_bar_low[i] > _rz_nl_bar_low_prev[i])
                        else:
                            _rz_bypass_now = bool(_rz_nl_guard_low is not None and (
                                (is_long and _rz_nl_guard_low[i] > 0 and px < _rz_nl_guard_low[i]) or
                                (not is_long and _rz_nl_guard_high is not None and _rz_nl_guard_high[i] > 0 and px > _rz_nl_guard_high[i])
                            ))
                        if _rz_bypass_now:
                            _rec_pending_exit_reason = "RZ_NOLOSS_BYPASS"
                            _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                            in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                            _entry_was_rz_break = False; _rz_entry_bar = -1
                            cd = max(cooldown, min_gap_bars); continue
                    # WRONG_SIDE_ABS_KILL v2: (WT N-of-5 full) OR (WT M-of-5 reduced AND div M-of-5) + age.
                    # K deferred (irrelevant per user). Divergence lets us exit on 3/4 TFs if price-WT divergence confirms.
                    # Runs BEFORE PPL/WT-exit/NOLOSS so it fires even when STRICT_NO_LOSS would otherwise hold.
                    if _ws_kill_enabled and _ws_wt_mask_full is not None and (i - eb) >= _ws_min_age_bars:
                        _ws_fire = bool(_ws_wt_mask_full[i]) or (_ws_wt_mask_reduced is not None and _ws_div_mask is not None and bool(_ws_wt_mask_reduced[i]) and bool(_ws_div_mask[i]))
                        if _ws_fire:
                            _rec_pending_exit_reason = "WRONG_SIDE_ABS_KILL"
                            _wa_ws = (_pe_realized + (1.0 - _pe_frac) * live_pnl if _pe_partial_done else live_pnl) * _cur_sz_mult
                            all_pnl.append(_wa_ws); sym_pnl.append(_wa_ws)
                            in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                            cd = max(cooldown, min_gap_bars); continue
                    # ===== ENGINE-RETROFIT 2026-04-30: live-parity exit cascade (registry X1/X2/X3/X4) =====
                    # Order: PEAK_GIVEBACK → BE_EROSION → K1M_EXTREME_REVERSE → STRONG_REDUCE_K
                    # All gated by per-position context (gain, peak_gain, age). NO cfg.NOLOSS_ENABLED check
                    # for X1/X2 (they are profit-protection paths — only fire after peak ≥ X% in profit).
                    # X3/X4 require profit explicitly (set by config flag).
                    _age_bars_now = i - eb
                    # X1. PEAK_GIVEBACK_GAIN_EROSION_STOP — fire when peak met threshold then dropped.
                    if _peak_giveback_active and _peak_gain_running >= _pg_peak_min and _age_bars_now >= _pg_min_age:
                        _drop = _peak_gain_running - live_pnl
                        _pg_fire = (_drop >= _pg_drop_pct)
                        if not _pg_fire and _pg_hard_zero and live_pnl <= 0.0:
                            _pg_fire = True
                        if _pg_fire:
                            _rec_pending_exit_reason = "PEAK_GIVEBACK"
                            _wa_pg = (_pe_realized + (1.0 - _pe_frac) * live_pnl if _pe_partial_done else live_pnl) * _cur_sz_mult
                            all_pnl.append(_wa_pg); sym_pnl.append(_wa_pg)
                            in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                            cd = max(cooldown, min_gap_bars); continue
                    # X2. BREAKEVEN_GAIN_EROSION_STOP — old position eroded back near zero.
                    if _be_erosion_active and _age_bars_now >= _be_age_min and live_pnl < _be_min_gain:
                        _be_fire = True
                        if _be_require_profit and live_pnl < _be_comm_buf:
                            _be_fire = False
                        if _be_fire:
                            _rec_pending_exit_reason = "BE_EROSION"
                            _wa_be = (_pe_realized + (1.0 - _pe_frac) * live_pnl if _pe_partial_done else live_pnl) * _cur_sz_mult
                            all_pnl.append(_wa_be); sym_pnl.append(_wa_be)
                            in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                            cd = max(cooldown, min_gap_bars); continue
                    # X3. K1M_EXTREME_REVERSE — fire on extreme k_1m + price turn + (optional) profit.
                    # Skips silently when stoch_k_1m is zero-filled in NPZ (registry note: needs precompute fix).
                    if _k1m_extreme_active and not _retro_k_1m_zero_filled:
                        _k_now = float(_retro_k_1m[i]); _k_prv = float(_retro_k_1m_prev[i])
                        if is_long:
                            _k1m_fire = (_k_now > _k1m_hi) and (_k_now < _k_prv)
                        else:
                            _k1m_fire = (_k_now < _k1m_lo) and (_k_now > _k_prv)
                        if _k1m_req_profit and live_pnl < 0.0:
                            _k1m_fire = False
                        if _k1m_fire:
                            _rec_pending_exit_reason = "K1M_EXTREME_REVERSE"
                            _wa_k1m = (_pe_realized + (1.0 - _pe_frac) * live_pnl if _pe_partial_done else live_pnl) * _cur_sz_mult
                            all_pnl.append(_wa_k1m); sym_pnl.append(_wa_k1m)
                            in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                            cd = max(cooldown, min_gap_bars); continue
                    # X4. STRONG_REDUCE_K — fires when 15m extreme + 1h trending opposite + in profit.
                    # Vec engine: closes the full position (no partial-reduce equivalent at this layer).
                    # SRK_REDUCE_FRAC is a knob for sweep-tuning a future partial-close fork.
                    if _strong_reduce_k_active and live_pnl >= 0.0:
                        _k15_now = float(_retro_k_15m[i]); _k1h_now = float(_retro_k_1h[i])
                        if is_long:
                            _srk_fire = (_k15_now > _srk_k15_long_min) and (_k1h_now < _srk_k1h_long_max)
                        else:
                            _srk_fire = (_k15_now < _srk_k15_short_max) and (_k1h_now > _srk_k1h_short_min)
                        if _srk_fire:
                            _rec_pending_exit_reason = "STRONG_REDUCE_K"
                            # Apply SRK_REDUCE_FRAC: realize fraction now, keep remainder running.
                            # If fully closing (frac=1.0) or partial-already-done, close completely.
                            if _srk_reduce_frac >= 0.999 or _pe_partial_done:
                                _wa_srk = (_pe_realized + (1.0 - _pe_frac) * live_pnl if _pe_partial_done else live_pnl) * _cur_sz_mult
                                all_pnl.append(_wa_srk); sym_pnl.append(_wa_srk)
                                in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                                cd = max(cooldown, min_gap_bars); continue
                            else:
                                # Partial reduce: realize SRK_REDUCE_FRAC * live_pnl, keep remainder.
                                # Models live STRONG_REDUCE_K (50% close) — symmetric with PPL state machine.
                                _pe_realized = _srk_reduce_frac * live_pnl
                                _pe_frac = _srk_reduce_frac  # the realized fraction
                                _pe_partial_done = True
                                # Don't continue — let the bar's other logic run with partial-done state.
                    # Partial exit v2 (PPL): TP at _pe_pct → stop BE+buffer → upgrade stop to _pe_trail_floor at _pe_trail_arm.
                    if _pe_enabled and not _pe_partial_done and live_pnl >= _pe_pct:
                        _pe_realized = _pe_frac * live_pnl
                        _pe_partial_done = True
                        continue
                    if _pe_enabled and _pe_partial_done and not _pe_trail_armed and live_pnl <= _pe_be_buffer:
                        _rec_pending_exit_reason = "PPL_BE_STOP"
                        total_pnl = _pe_realized + (1.0 - _pe_frac) * live_pnl
                        _wa = total_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                        in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                        cd = max(cooldown, min_gap_bars); continue
                    if _pe_enabled and _pe_partial_done and not _pe_trail_armed and live_pnl >= _pe_trail_arm:
                        _pe_trail_armed = True
                    if _pe_enabled and _pe_partial_done and _pe_trail_armed and live_pnl <= _pe_trail_floor:
                        _rec_pending_exit_reason = "PPL_TRAIL_STOP"
                        total_pnl = _pe_realized + (1.0 - _pe_frac) * live_pnl
                        _wa = total_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                        in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                        cd = max(cooldown, min_gap_bars); continue
                    # Dynamic counter-exit: cut position when opposite direction scores strongly
                    if _dyn_counter_enabled and (i - eb) >= min_hold and _dyn_counter_score is not None:
                        if _dyn_counter_score[i] >= _dyn_counter_thr:
                            _rec_pending_exit_reason = "DYN_COUNTER_EXIT"
                            _wa = (_pe_realized + (1.0 - _pe_frac) * live_pnl if _pe_partial_done else live_pnl) * _cur_sz_mult
                            all_pnl.append(_wa); sym_pnl.append(_wa)
                            in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                            cd = cooldown + min_gap_bars; continue
                    # Dynamic augment: re-score every N bars — if score jumped, average in at current price
                    if _dyn_aug_enabled and not _dyn_aug_done and (i - eb) >= _dyn_aug_interval and (i - eb) % _dyn_aug_interval == 0 and _dyn_same_score is not None:
                        _cur_score = float(_dyn_same_score[i])
                        if _cur_score - _dyn_entry_score >= _dyn_aug_jump:
                            ep = (ep + px) / 2.0
                            live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                            _dyn_aug_done = True
                    # wt_D bounce augment — add to losing position when daily WT turns with higher bounce
                    if _aug_enabled and not _aug_done and live_pnl < 0 and i >= _bph_D:
                        _wt1d_cur = _wt1_D_aug[i]; _wt1d_prev = _wt1_D_aug[i - _bph_D]
                        if is_long:
                            _bounce = _wt1d_cur > _wt1d_prev
                            _ok = _bounce and (not _aug_req_hwt or _wt1d_cur > _aug_wt_d_last) and (not _aug_req_hpx or px > _aug_px_last)
                        else:
                            _bounce = _wt1d_cur < _wt1d_prev
                            _ok = _bounce and (not _aug_req_hwt or _wt1d_cur < _aug_wt_d_last) and (not _aug_req_hpx or px < _aug_px_last)
                        if _ok:
                            ep = (ep + px * (_aug_mult - 1.0)) / _aug_mult
                            live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                            _aug_done = True; _aug_wt_d_last = _wt1d_cur; _aug_px_last = px
                    # wt_4h bounce augment — fires ~6x more often than wt_D; independent flag allows both to fire once each
                    if _aug_4h_enabled and not _aug_4h_done and live_pnl < 0 and i >= _bph_4h:
                        _wt1_4h_cur = _wt1_4H_aug[i]; _wt1_4h_prev = _wt1_4H_aug[i - _bph_4h]
                        if is_long:
                            _bounce_4h = _wt1_4h_cur > _wt1_4h_prev
                            _ok_4h = _bounce_4h and (not _aug_4h_req_hwt or _wt1_4h_cur > _aug_4h_wt_last) and (not _aug_4h_req_hpx or px > _aug_4h_px_last)
                        else:
                            _bounce_4h = _wt1_4h_cur < _wt1_4h_prev
                            _ok_4h = _bounce_4h and (not _aug_4h_req_hwt or _wt1_4h_cur < _aug_4h_wt_last) and (not _aug_4h_req_hpx or px < _aug_4h_px_last)
                        if _ok_4h:
                            ep = (ep + px * (_aug_4h_mult - 1.0)) / _aug_4h_mult
                            live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                            _aug_4h_done = True; _aug_4h_wt_last = _wt1_4h_cur; _aug_4h_px_last = px
                    # ===== ENGINE-RETROFIT 2026-04-30: E6 WINNER_AUGMENT (~901/day live HAIKU_AUGMENT) =====
                    # Average in when position is in profit ≥ WA_MIN_GAIN_PCT and gain has GROWN
                    # since last augment by WA_GAIN_GROWTH_REQ. Capped at WA_MAX_AUGMENTS per pos.
                    # This is the technical equivalent of HAIKU_AUGMENT (Anthropic API decides live;
                    # vec engine uses pure technicals).
                    if _wa_active and _wa_aug_count < _wa_max_aug and live_pnl >= _wa_min_gain:
                        if (live_pnl - _wa_last_aug_gain) >= _wa_growth_req:
                            # Average in 50% size at current price (effective: ep moves up for LONG).
                            ep = (ep + px) / 2.0
                            live_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                            _wa_aug_count += 1
                            _wa_last_aug_gain = live_pnl
                            # Augmenting on a winner does not affect _peak_gain_running (recomputed next bar).
                    # ===== ENGINE-RETROFIT 2026-04-30: E5 HEDGE_PROTECT_LOSS (~310/day live) =====
                    # Fires opposite-side hedge when primary at loss ≤ HPL_TRIGGER_LOSS_PCT.
                    # HPL_FIRE_ONCE prevents re-fires per position; HPL_COOLDOWN_BARS adds global cooldown.
                    # If symbol has no hedge state (HEDGE_ENABLED=False, e.g. tradier), HPL realizes
                    # PnL of the hedge as `live_pnl × HPL_HEDGE_FRAC × -1` (synthetic hedge fill).
                    if (_hpl_active and (not _hpl_fired_this_pos or not _hpl_fire_once)
                            and live_pnl <= _hpl_trigger_loss
                            and (i - _hpl_last_fire_bar) >= _hpl_cooldown_bars):
                        # WT must confirm reverse direction (against primary).
                        _hpl_wt_ok = (_wt1_ltf[i] < _wt2_ltf[i] and _wt1_1h[i] < _wt2_1h[i]) if is_long \
                                else (_wt1_ltf[i] > _wt2_ltf[i] and _wt1_1h[i] > _wt2_1h[i])
                        if _hpl_wt_ok:
                            # Synthetic hedge: realize an opposite-side bookmark of size HPL_HEDGE_FRAC
                            # against the primary at the moment of trigger. Initial PnL = 0; we book
                            # the live_pnl absolute reversal as a credit only once per position.
                            # Conservative: append a 0-PnL trade to register the rate-guard tick.
                            # Sweeps may upgrade this to a tracked sub-position (TODO).
                            _hpl_pnl = 0.0  # synthetic hedge entry; no immediate PnL
                            all_pnl.append(_hpl_pnl); sym_pnl.append(_hpl_pnl)
                            _hpl_fired_this_pos = True
                            _hpl_last_fire_bar = i
                            _rec_pending_exit_reason = "HEDGE_PROTECT_LOSS_FIRED"
                    # Augmented position profit target — fires after any augment (wt_D or wt_4h)
                    if _aug_pt_enabled and (_aug_done or _aug_4h_done) and live_pnl >= _aug_pt_pct:
                        _rec_pending_exit_reason = "AUG_PT_HIT"
                        _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                        in_pos = False; _aug_done = False; cd = max(cooldown, min_gap_bars); continue
                    # Continuous hedge — disabled when HEDGE_ENABLED=False (tradier has no hedge engine).
                    if getattr(cfg, 'HEDGE_ENABLED', True):
                        # HEDGE_ENTRY_MODE (2026-04-21 sweep): 'LOSS_ONLY' / 'LOSS_AND_WT' / 'LOSS_OR_WT'
                        _he_mode = str(getattr(cfg, 'HEDGE_ENTRY_MODE', 'LOSS_AND_WT'))
                        if is_long:
                            _wt_against_h = _wt1_ltf[i] < _wt2_ltf[i] and _wt1_1h[i] < _wt2_1h[i]
                            if _he_mode == 'LOSS_ONLY':
                                hc = live_pnl < 0
                            elif _he_mode == 'LOSS_OR_WT':
                                hc = live_pnl < 0 or _wt_against_h
                            else:
                                hc = live_pnl < 0 and _wt_against_h
                            if _hedge_wt_kill_tf == 'none':
                                _wt_kill = _wt1_ltf[i] > _wt2_ltf[i]
                            elif _hedge_wt_kill_tf == '15m':
                                _wt_kill = _wt1_ltf[i] > _wt2_ltf[i] and _wt1_15m_h[i] > _wt2_15m_h[i]
                            else:
                                _wt_kill = _wt1_ltf[i] > _wt2_ltf[i] and _wt1_1h[i] > _wt2_1h[i]
                        else:
                            _wt_against_h = _wt1_ltf[i] > _wt2_ltf[i] and _wt1_1h[i] > _wt2_1h[i]
                            if _he_mode == 'LOSS_ONLY':
                                hc = live_pnl < 0
                            elif _he_mode == 'LOSS_OR_WT':
                                hc = live_pnl < 0 or _wt_against_h
                            else:
                                hc = live_pnl < 0 and _wt_against_h
                            if _hedge_wt_kill_tf == 'none':
                                _wt_kill = _wt1_ltf[i] < _wt2_ltf[i]
                            elif _hedge_wt_kill_tf == '15m':
                                _wt_kill = _wt1_ltf[i] < _wt2_ltf[i] and _wt1_15m_h[i] < _wt2_15m_h[i]
                            else:
                                _wt_kill = _wt1_ltf[i] < _wt2_ltf[i] and _wt1_1h[i] < _wt2_1h[i]
                        _hedge_should_close = live_pnl >= 0 or _wt_kill
                        # ═══ 2026-04-26 HEDGE GATES — apply DC/WT-vel/deteriorating-gain to hedge open decision ═══
                        if hc and not hedge_in_pos:
                            _h_gate_block = False
                            if _hedge_dc_gate_on and _hedge_dc_ok is not None and not bool(_hedge_dc_ok[i]):
                                _h_gate_block = True
                            if (not _h_gate_block) and _hedge_wt_vel_gate_on and _hedge_wt_vel_ok is not None and not bool(_hedge_wt_vel_ok[i]):
                                _h_gate_block = True
                            if (not _h_gate_block) and _hedge_det_on:
                                # Deteriorating-gain rule: live_pnl now must be < live_pnl K bars ago - delta_pp.
                                # Need same-position history (eb is entry bar, so K-bars-ago must be >= eb).
                                if (i - _hedge_det_window) >= eb and (i - _hedge_det_window) >= 0:
                                    _h_px_K = float(close[i - _hedge_det_window])
                                    if _h_px_K > 0:
                                        _h_pnl_K = ((_h_px_K - ep) / ep * 100.0) if is_long else ((ep - _h_px_K) / ep * 100.0)
                                        if live_pnl >= _h_pnl_K - _hedge_det_delta:
                                            _h_gate_block = True
                                else:
                                    # Not enough position history yet → don't hedge (conservative)
                                    _h_gate_block = True
                            if not _h_gate_block:
                                hedge_in_pos = True; hedge_ep = px; hedge_eb = i
                        elif _hedge_should_close and hedge_in_pos and hedge_ep > 0 and (i - hedge_eb) >= hedge_min_hold:
                            h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                            all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                    # ALL_TF_BRAKE: all TFs (incl. W/M when available) flip against → bypass NOLOSS
                    if _atb_enabled and _atb_against is not None and (i - eb) >= min_hold:
                        if _atb_against[i] >= _atb_min_tfs:
                            _rec_pending_exit_reason = "ALL_TF_BRAKE"
                            _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                            in_pos = False; _entry_was_breakout = False; cd = max(cooldown, min_gap_bars); continue
                    # DC_BREAKOUT_FAILED_STOP: entered above prev-bar DC high → exit when price falls back below prev-bar DC high
                    if _bfs_enabled and _entry_was_breakout:
                        _bfs_hit = (is_long and _dc_h4_prev[i] > 0 and px < _dc_h4_prev[i]) or (not is_long and _dc_l4_prev[i] > 0 and px > _dc_l4_prev[i])
                        if _bfs_hit:
                            _rec_pending_exit_reason = "BREAKOUT_FAILED_STOP"
                            _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                            in_pos = False; _entry_was_breakout = False; cd = max(cooldown, min_gap_bars); continue
                    if _dc4_bypass_enabled and (_dc4_max_bars == 0 or (i - eb) <= _dc4_max_bars):
                        _dc4_hit = (is_long and _dc4_low is not None and _dc4_low[i] > 0 and px < _dc4_low[i]) or (not is_long and _dc4_high is not None and _dc4_high[i] > 0 and px > _dc4_high[i])
                        if _dc4_hit:
                            _rec_pending_exit_reason = "DC4_BYPASS"
                            _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                            in_pos = False; cd = max(cooldown, min_gap_bars); continue
                    if cfg.DC_RECOVERY_EXIT_ENABLED and cfg.NOLOSS_ENABLED and (i - eb) >= min_hold and live_pnl < 0:
                        if is_long:
                            _dc_stranded = ep > dc_high_4h[i] and dc_high_4h[i] > 0
                        else:
                            _dc_stranded = ep < dc_low_4h[i] and dc_low_4h[i] > 0
                        if _dc_stranded:
                            _rec_pending_exit_reason = "DC_RECOVERY_STRANDED"
                            _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                            if _qr_enabled: _qr_exit_px = px; _qr_exit_bar = i
                            if _t1pc_enabled: _t1pc_exit_px = px; _t1pc_exit_bar = i
                            in_pos = False; cd = max(cooldown, min_gap_bars); continue
                    if sl_enabled and live_pnl <= -sl_pct:
                        _rec_pending_exit_reason = "STOP_LOSS"
                        _wa = live_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                        if hedge_in_pos and hedge_ep > 0:
                            h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                            all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                        in_pos = False; cd = max(cooldown, min_gap_bars); continue
                if _intraday_enabled and in_pos and _sec_of_day is not None and _sec_of_day[i] >= _intraday_force_exit_utc:
                    _rec_pending_exit_reason = "INTRADAY_FORCE_EXIT"
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    if _pe_partial_done:
                        pnl = _pe_realized + (1.0 - _pe_frac) * pnl
                    _wa = pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                    if hedge_in_pos and hedge_ep > 0:
                        h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                        all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                    if _qr_enabled: _qr_exit_px = px; _qr_exit_bar = i
                    if _t1pc_enabled: _t1pc_exit_px = px; _t1pc_exit_bar = i
                    in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False; _last_exit_bar = i; cd = max(cooldown, min_gap_bars); continue
                if in_pos and (i - eb) < min_hold and _mh_dc_bypass_enabled and _mh_dc_low is not None:
                    _mh_break = (is_long and _mh_dc_low[i] > 0 and px < _mh_dc_low[i]) or (not is_long and _mh_dc_high is not None and _mh_dc_high[i] > 0 and px > _mh_dc_high[i])
                    if _mh_break:
                        _rec_pending_exit_reason = "MIN_HOLD_DC_BYPASS"
                        pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                        if _pe_partial_done: pnl = _pe_realized + (1.0 - _pe_frac) * pnl
                        _wa = pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                        if hedge_in_pos and hedge_ep > 0:
                            h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                            all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                        in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False; _last_exit_bar = i; cd = max(cooldown, min_gap_bars); continue
                # Phase 4 (2026-04-30): state-dependent process_position exit gates.
                # DC_HOPELESS_EXIT: entry_price outside dc_4h channel + age > 900s → force close.
                # WT_4H_VEL_EXIT_FULL: vel against + age > 360s + profit_ok + k_extreme → force close.
                _phase4_force_exit = False
                _phase4_force_reason = ""
                if in_pos and _exit_gate_extras is not None:
                    _age_s = (i - eb) * _ltf_mins * 60.0
                    if cfg.DC_HOPELESS_EXIT_ENABLED and _age_s > float(getattr(cfg, 'DC_HOPELESS_EXIT_MIN_AGE_S', 900)):
                        _dch = _exit_gate_extras['dc_h_4h'][i]
                        _dcl = _exit_gate_extras['dc_l_4h'][i]
                        if _dch > 0 and _dcl > 0:
                            if (is_long and ep > _dch) or (not is_long and ep < _dcl):
                                _phase4_force_exit = True
                                _phase4_force_reason = "DC_HOPELESS_EXIT"
                    if not _phase4_force_exit and cfg.WT_4H_VEL_EXIT_ENABLED and _age_s > 360.0 and _exit_gate_extras['wt_4h_vel_full'][i]:
                        _phase4_pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                        _phase4_comm_buf = float(getattr(cfg, 'COMMISSION_BUFFER_PCT', 0.10))
                        _phase4_profit_ok = (_phase4_pnl >= _phase4_comm_buf) if bool(getattr(cfg, 'WT_4H_VEL_EXIT_REQUIRE_PROFIT', True)) else True
                        if _phase4_profit_ok:
                            _phase4_force_exit = True
                            _phase4_force_reason = "WT_4H_VEL_EXIT"
                if _phase4_force_exit:
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    if _pe_partial_done: pnl = _pe_realized + (1.0 - _pe_frac) * pnl
                    _wa = pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                    if hedge_in_pos and hedge_ep > 0:
                        h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                        all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                    in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False
                    _last_exit_bar = i; cd = max(cooldown, min_gap_bars)
                    _rec_pending_exit_reason = _phase4_force_reason
                    continue
                _wt_exit_now = (not _pe_partial_done and (exit_sig[i] or (_adaptive_exit_enabled and exit_sig_extra is not None and exit_sig_extra[i]))) or (_pe_partial_done and exit_sig_rem[i])
                if in_pos and (i - eb) >= min_hold and _wt_exit_now:
                    if _pe_partial_done:
                        _rec_pending_exit_reason = "WT_EXIT_POST_PPL"
                    elif _adaptive_exit_enabled and exit_sig_extra is not None and bool(exit_sig_extra[i]) and not bool(exit_sig[i]):
                        _rec_pending_exit_reason = "ADAPTIVE_EXIT"
                    else:
                        _rec_pending_exit_reason = "WT_EXIT"
                    pnl = ((px - ep) / ep * 100) if is_long else ((ep - px) / ep * 100)
                    if not _pe_partial_done:
                        if _adaptive_exit_enabled and exit_sig_extra is not None and exit_sig_extra[i] and not exit_sig[i] and pnl <= _adaptive_gain_pct:
                            continue
                        if wp_enabled and 0.0 <= pnl < wp_gain_pct and _wp_aligned[i]:
                            continue
                        if cfg.NOLOSS_ENABLED and pnl < 0:
                            # NOLOSS_BYPASS_WT_5OF5: 5/5 WT TFs against → allow close at loss.
                            _nlb_5of5_hit = bool(_nlb_5of5_mask is not None and _nlb_5of5_mask[i])
                            _rz_nl_bypass = False
                            if _entry_was_rz_break and _rz_nl_mode != 'none':
                                if _rz_nl_mode == 'bar_structure':
                                    _bars_since = i - _rz_entry_bar
                                    if (_bars_since <= _rz_nl_bar_window and
                                            _rz_nl_bar_high is not None and _rz_nl_bar_low is not None):
                                        if is_long:
                                            _rz_nl_bypass = bool(_rz_nl_bar_high[i] < _rz_nl_bar_high_prev[i] and _rz_nl_bar_low[i] < _rz_nl_bar_low_prev[i])
                                        else:
                                            _rz_nl_bypass = bool(_rz_nl_bar_high[i] > _rz_nl_bar_high_prev[i] and _rz_nl_bar_low[i] > _rz_nl_bar_low_prev[i])
                                else:
                                    _rz_nl_bypass = bool(_rz_nl_guard_low is not None and (
                                        (is_long and _rz_nl_guard_low[i] > 0 and px < _rz_nl_guard_low[i]) or
                                        (not is_long and _rz_nl_guard_high is not None and _rz_nl_guard_high[i] > 0 and px > _rz_nl_guard_high[i])
                                    ))
                            _aug_ce_bypass = (_aug_ce_enabled and _aug_done and _aug_px_last > 0 and ((is_long and px < _aug_px_last) or (not is_long and px > _aug_px_last)))
                            if not _rz_nl_bypass and not _nlb_5of5_hit and not _aug_ce_bypass:
                                if cfg.DC_RECOVERY_EXIT_ENABLED:
                                    if is_long:
                                        stranded = ep > dc_high_4h[i] and dc_high_4h[i] > 0
                                    else:
                                        stranded = ep < dc_low_4h[i] and dc_low_4h[i] > 0
                                    if not stranded:
                                        continue
                                else:
                                    continue
                    else:
                        pnl = _pe_realized + (1.0 - _pe_frac) * pnl
                    _wa = pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                    if hedge_in_pos and hedge_ep > 0:
                        h_pnl = ((hedge_ep - px) / hedge_ep * 100) if is_long else ((px - hedge_ep) / hedge_ep * 100)
                        all_pnl.append(h_pnl); sym_pnl.append(h_pnl); hedge_in_pos = False; hedge_ep = 0.0
                    if _qr_enabled: _qr_exit_px = px; _qr_exit_bar = i
                    if _t1pc_enabled: _t1pc_exit_px = px; _t1pc_exit_bar = i
                    in_pos = False; _pe_partial_done = False; _pe_realized = 0.0; _pe_trail_armed = False; cd = max(cooldown, min_gap_bars)
            if in_pos:
                final_px = close[n - 1]
                if final_px > 0 and ep > 0:
                    final_pnl = ((final_px - ep) / ep * 100) if is_long else ((ep - final_px) / ep * 100)
                    if _pe_partial_done:
                        final_pnl = _pe_realized + (1.0 - _pe_frac) * final_pnl
                    _wa = final_pnl * _cur_sz_mult; all_pnl.append(_wa); sym_pnl.append(_wa)
                    if _generic_trades_buf is not None and _rec_entry_bar >= 0:
                        _en_ts = int(ts[_rec_entry_bar]) if hasattr(ts, '__len__') and _rec_entry_bar < len(ts) else 0
                        _ex_ts = int(ts[n - 1]) if hasattr(ts, '__len__') and (n - 1) < len(ts) else 0
                        _generic_trades_buf.append({
                            "symbol": sym,
                            "side": "LONG" if is_long else "SHORT",
                            "entry_type": "BREAKOUT" if _entry_was_breakout else "BOUNCE",
                            "entry_reason": "ENTRY", "exit_reason": "MTM_END_OF_SIM",
                            "entry_bar": int(_rec_entry_bar), "entry_ts": _en_ts,
                            "entry_price": float(_rec_entry_price),
                            "exit_bar": int(n - 1), "exit_ts": _ex_ts, "exit_price": float(final_px),
                            "pnl_pct": float(_wa), "pnl_usd": 0.0,
                            "duration_bars": int(n - 1 - _rec_entry_bar),
                            "stream": "primary",
                        })
                        _rec_entry_bar = -1
                        _rec_entry_price = 0.0
                if hedge_in_pos and final_px > 0 and hedge_ep > 0:
                    h_pnl = ((hedge_ep - final_px) / hedge_ep * 100) if is_long else ((final_px - hedge_ep) / hedge_ep * 100)
                    all_pnl.append(h_pnl); sym_pnl.append(h_pnl)
                in_pos = False; hedge_in_pos = False; hedge_ep = 0.0
        # Dump non-BTC trade buffer to JSONL (one file per symbol, both sides merged).
        if _generic_trades_dir and _generic_trades_buf:
            try:
                os.makedirs(_generic_trades_dir, exist_ok=True)
                _run_id = os.environ.get('V8_TRADES_RUN_ID', 'default')
                _out_path = os.path.join(_generic_trades_dir, f"{_run_id}__{sym}.jsonl")
                with open(_out_path, 'w') as _tf:
                    for _td in _generic_trades_buf:
                        _tf.write(json.dumps(_td) + "\n")
            except Exception as _te:
                print(f"[V8_TRADES_OUT_GEN] write error {sym}: {_te}", flush=True)
        # Phase 3d (2026-04-30): qty pipeline now applied per-trade via _qty_pipe_ratio
        # multiplied into _cur_sz_mult at each entry site. The earlier Phase 3b symbol-
        # level scalar weight has been removed — per-trade subsumes it AND properly affects
        # Sharpe distribution shape (not just DD scale).
        per_symbol_pnl[sym] = sym_pnl
        symbols_processed += 1
        if _rg is not None:
            if n > _rg_max_n:
                _rg_max_n = n
            _rg.n_accts = max(1, symbols_processed)
            _rg.tick(len(all_pnl))
        if ea_enabled:
            elapsed = time.time() - t_sim_start
            _time_abort = elapsed > ea_time_limit
            if symbols_processed >= ea_min_syms or _time_abort:
                per_sym_sharpes_chk = _per_symbol_sharpes(per_symbol_pnl)
                if per_sym_sharpes_chk:
                    avg_chk = float(np.mean(per_sym_sharpes_chk))
                    if avg_chk < ea_floor:
                        early_abort = True
                        break
    if _rg is not None and not early_abort:
        _rg.n_accts = max(1, symbols_processed)
        _rg_days = (_rg_max_n * _ltf_mins / 1440.0) if _rg_max_n > 0 else None
        _rg.final_check(len(all_pnl), test_window_days=_rg_days)
    return _finalize_result(per_symbol_pnl, all_pnl, start_size, symbols_processed, early_abort)


def _per_symbol_sharpes(per_symbol_pnl, min_trades=30, std_floor=1e-3, cap=5.0):
    """COCKROACH FIX 2026-04-30: min_trades 1→30, cap 20→5. Sub-30-trade symbols
    contributed noise Sharpes that propagated as 'wins'; a >5 per-trade Sharpe is
    implausible and was a tell of small-sample lies."""
    out = []
    for sym, plist in per_symbol_pnl.items():
        if len(plist) < min_trades:
            out.append(0.0)
            continue
        arr = np.array(plist)
        m = arr.mean(); s = max(arr.std(), std_floor)
        sh = max(min(m / s, cap), -cap)
        out.append(sh)
    return out


def _pool_sharpe(all_pnl, n_syms_qualifying=0, min_trades_per_sym=30, cap=5.0):
    """COCKROACH FIX 2026-04-30: now requires n_syms × min_trades_per_sym total
    trades, not the old `len(all_pnl) >= 30` floor that let 177-trade samples
    produce 'pool_sharpe=3.4'. cap 20→5 (per-trade Sharpe >5 is implausible)."""
    required = max(30, n_syms_qualifying * min_trades_per_sym)
    if len(all_pnl) < required:
        return 0.0
    p = np.array(all_pnl)
    m = p.mean(); s = p.std()
    if s < 1e-6:
        return 0.0
    return round(float(max(min(m / s, cap), -cap)), 4)


def _per_symbol_max_dd_pct(plist):
    if not plist:
        return 0.0
    cum = np.cumsum(np.array(plist, dtype=np.float64))
    running_max = np.maximum.accumulate(cum)
    dd = running_max - cum
    return float(dd.max()) if len(dd) > 0 else 0.0


def _finalize_result(per_symbol_pnl, all_pnl, start_size, symbols_processed, early_abort):
    _return_raw = os.environ.get("V8_RETURN_RAW_PNL", "0") == "1"
    per_sym_sharpes = _per_symbol_sharpes(per_symbol_pnl)
    zero_pad = symbols_processed - len(per_symbol_pnl)
    if zero_pad > 0:
        per_sym_sharpes.extend([0.0] * zero_pad)
    n_trades = len(all_pnl)
    # COCKROACH FIX 2026-04-30: pool_sharpe now requires per-symbol min_trades.
    # Count syms with ≥30 trades — those are the ones contributing valid sample.
    n_syms_qualifying = sum(1 for plist in per_symbol_pnl.values() if len(plist) >= 30)
    ps = _pool_sharpe(all_pnl, n_syms_qualifying=n_syms_qualifying)
    syms_excluded = 0
    per_sym_dds = [_per_symbol_max_dd_pct(plist) for plist in per_symbol_pnl.values() if plist]
    worst_sym_dd_pct = round(float(max(per_sym_dds)), 4) if per_sym_dds else 0.0
    avg_sym_dd_pct = round(float(sum(per_sym_dds) / len(per_sym_dds)), 4) if per_sym_dds else 0.0
    accumulated_gain_pct = round(float(np.array(all_pnl).sum()), 4) if all_pnl else 0.0
    if not per_sym_sharpes:
        p = np.array(all_pnl) if all_pnl else np.array([0.0])
        w = int((p > 0).sum()); l = int((p <= 0).sum())
        return {"sym_sharpe": 0, "sharpe_min": 0, "sharpe_p25": 0, "sharpe_med": 0,
                "sharpe_p75": 0, "sharpe_max": 0, "syms_with_sharpe": 0,
                "pool_sharpe": ps, "syms_excluded": syms_excluded,
                "pnl": round(p.sum() / 100 * start_size, 2), "trades": n_trades,
                "wins": w, "losses": l, "avg_pnl_pct": round(float(p.mean()), 4) if n_trades else 0,
                "wr": round(w / n_trades * 100, 1) if n_trades > 0 else 0,
                "accumulated_gain_pct": accumulated_gain_pct,
                "max_dd_pct": worst_sym_dd_pct,
                "avg_dd_pct": avg_sym_dd_pct,
                "early_abort": early_abort, "symbols_used": symbols_processed}
    arr = np.array(per_sym_sharpes)
    p = np.array(all_pnl) if all_pnl else np.array([0.0])
    w = int((p > 0).sum()); l = int((p <= 0).sum())
    out = {
        "sym_sharpe": round(float(arr.mean()), 4),
        "sharpe_min": round(float(arr.min()), 4),
        "sharpe_p25": round(float(np.percentile(arr, 25)), 4),
        "sharpe_med": round(float(np.median(arr)), 4),
        "sharpe_p75": round(float(np.percentile(arr, 75)), 4),
        "sharpe_max": round(float(arr.max()), 4),
        "syms_with_sharpe": len(per_sym_sharpes),
        "pool_sharpe": ps,
        "syms_excluded": syms_excluded,
        "pnl": round(p.sum() / 100 * start_size, 2),
        "trades": n_trades, "wins": w, "losses": l,
        "avg_pnl_pct": round(float(p.mean()), 4),
        "wr": round(w / n_trades * 100, 1) if n_trades > 0 else 0,
        "accumulated_gain_pct": accumulated_gain_pct,
        "max_dd_pct": worst_sym_dd_pct,
        "avg_dd_pct": avg_sym_dd_pct,
        "early_abort": early_abort,
        "symbols_used": symbols_processed,
    }
    if _return_raw:
        out["all_pnl"] = list(all_pnl)
        out["per_symbol_pnl"] = {k: list(v) for k, v in per_symbol_pnl.items()}
    return out


FAST_SYMBOLS_CRYPTO = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,ADAUSDC,AVAXUSDC,DOTUSDT,LINKUSDC,LTCUSDC,UNIUSDC,SANDUSDT"
FAST_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD"
MEDIUM_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD,GOOGL,AMD,INTC,V,PFE,JNJ,WMT,CME,CVX,UNH,CAT,HD"
MEDIUM_SYMBOLS_CRYPTO = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,ADAUSDC,AVAXUSDC,DOTUSDT,LINKUSDC,LTCUSDC,UNIUSDC,SANDUSDT,ATOMUSDT,ALGOUSDT,XLMUSDT,VETUSDT,TRXUSDT,XMRUSDT,ETCUSDT,SUSHIUSDT,MANAUSDT,KSMUSDT,SNXUSDT,YFIUSDT"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--symbols", default="BTCUSDC")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--npz-dir", default="")
    args = p.parse_args()
    t0 = time.time()
    cfg = QuickConfig.from_override_file(os.environ.get("V8_OVERRIDE_FILE", ""))
    if args.mode == "tradier":
        cfg.apply_tradier_defaults()
        ov = os.environ.get("V8_OVERRIDE_FILE", "")
        if ov and Path(ov).exists():
            with open(ov) as f:
                for k, v in json.load(f).items():
                    if hasattr(cfg, k):
                        cur = getattr(cfg, k)
                        if isinstance(cur, bool): setattr(cfg, k, bool(v))
                        elif isinstance(cur, int): setattr(cfg, k, int(v))
                        elif isinstance(cur, float): setattr(cfg, k, float(v))
                        else: setattr(cfg, k, v)
    syms = None
    if args.symbols == "fast":
        syms = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if syms and len(syms) > 15:
        stores = iter_npz(args.mode, syms, args.start, args.npz_dir)
    else:
        stores = load_npz(args.mode, syms, args.start, args.npz_dir)
        if not stores:
            print("No data"); return
    r = simulate(stores, cfg, args.capital)
    el = time.time() - t0
    print(f"V8_QUICK_RESULT: pool_sharpe={r.get('pool_sharpe',0)} sym_sharpe={r.get('sym_sharpe',0)} "
          f"(syms={r.get('syms_with_sharpe',0)} excl={r.get('syms_excluded',0)} "
          f"sym_sharpe_min={r.get('sharpe_min',0)} sym_sharpe_p25={r.get('sharpe_p25',0)} "
          f"sym_sharpe_med={r.get('sharpe_med',0)} sym_sharpe_p75={r.get('sharpe_p75',0)} sym_sharpe_max={r.get('sharpe_max',0)}) "
          f"pnl={r['pnl']:.2f} trades={r['trades']} wins={r['wins']} "
          f"losses={r['losses']} wr={r['wr']}% avg_pnl={r['avg_pnl_pct']:.4f}% "
          f"early_abort={r.get('early_abort',False)} elapsed={el:.1f}s")


if __name__ == "__main__":
    main()
