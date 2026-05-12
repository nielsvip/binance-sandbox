#!/usr/bin/env python3
"""
vec_engine_v1.py — Validator-gated vectorized backtest engine.

NOT named v8_quick* (banned namespace — see CLAUDE.md).

DESIGN:
  - Reads NPZ indicator stores (same format as backtest_v8_engine).
  - Per-bar state evolution is sequential per symbol (can't avoid — positions
    have memory), but indicator lookups are vectorized numpy slices.
  - Every Sharpe number routes through metrics_guard — no exceptions.
  - Open positions at end of sim are marked-to-market before Sharpe (CLAUDE.md rule 2).
  - No sqrt-annualization. No per-sym-only promotion. No custom "Score" metrics.

KNOWN LIMITATIONS vs real engine (backtest_v8_engine.py):
  - No HedgeEngine execution (hedge sim is stateful across symbols, too complex
    to faithfully replicate without importing ez_manage). Hedge switches are
    implemented as entry/exit gate approximations and flagged as APPROXIMATE.
  - PARTIAL_PROFIT_LOCK v2 3-step path not wired (would need sub-bar state).
  - GOLDEN_RULE sizing multipliers wired as entry filter (correct) and sizing
    scalar (approximate — real engine uses USD-notional per level).
  - DELTA_ENGINE: velocity proxy only (no real delta tracker state).

This is Tier-1 shortlist ONLY. Any config promoted from this engine MUST be
confirmed via a full backtest_v8_engine.py run before touching live config.
See vec_vs_real_validator.py for the CI gate.

Usage:
    from vec_engine_v1 import VecEngine, VecConfig
    eng = VecEngine(mode="crypto", npz_dir="backtest_v8/indicators")
    cfg = VecConfig()  # default config
    result = eng.simulate(symbols=["BTCUSDC", "ETHUSDC"], cfg=cfg,
                          start_ts=1704067200, end_ts=None)
    print(result)
"""
from __future__ import annotations

import json
import math
import os
import platform
import sys
import time as _time_mod
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# vec_paths — live entry path modules (imported lazily to avoid circular deps)
try:
    from vec_paths.dc_break import check_dc_break_entry as _check_dc_break_entry
    from vec_paths.reentry import check_reentry_entry as _check_reentry_entry
    _VEC_PATHS_AVAILABLE = True
except ImportError:
    _VEC_PATHS_AVAILABLE = False
    def _check_dc_break_entry(store, bar_idx, cfg): return None
    def _check_reentry_entry(store, bar_idx, sym, pos_state, side, cfg): return None

# SCALP_V2 — HTF Breakout Scalper (default OFF — SCALP_MODE=False)
try:
    from vec_paths.scalp_v2 import check_scalp_v2_entry as _check_scalp_v2_entry
    from vec_paths.scalp_v2 import check_scalp_v2_exit as _check_scalp_v2_exit
    _SCALP_V2_AVAILABLE = True
except ImportError:
    _SCALP_V2_AVAILABLE = False
    def _check_scalp_v2_entry(store, bar_idx, side, cfg): return None
    def _check_scalp_v2_exit(store, bar_idx, pos_state, side, cfg): return None

# SCALP_V3 — Ultra-short bar-based scalper (default OFF — SCALP_V3_ENABLED=False)
try:
    from vec_paths.scalp_v3 import check_scalp_v3_entry as _check_scalp_v3_entry
    from vec_paths.scalp_v3 import check_scalp_v3_exit as _check_scalp_v3_exit
    _SCALP_V3_AVAILABLE = True
except ImportError:
    _SCALP_V3_AVAILABLE = False
    def _check_scalp_v3_entry(store, bar_idx, side, cfg): return None
    def _check_scalp_v3_exit(store, bar_idx, pos_state, side, cfg): return None

# MICRO_SCALP — stocks + USDC micro-scalper (default OFF)
try:
    from vec_paths.micro_scalp import check_micro_scalp_close as _check_micro_scalp_close
    from vec_paths.micro_scalp import check_micro_scalp_reopen as _check_micro_scalp_reopen
    _MICRO_SCALP_AVAILABLE = True
except ImportError:
    _MICRO_SCALP_AVAILABLE = False
    def _check_micro_scalp_close(store, bar_idx, pos_state, mode, cfg): return None
    def _check_micro_scalp_reopen(store, bar_idx, last_exit_price, mode, cfg, original_side="LONG"): return None

# HEDGE_ENGINE — same-symbol hedge paths (crypto only, default OFF in backtest)
try:
    from vec_paths.hedge_engine import (
        check_scan_hedge_losers as _check_scan_hedge_losers,
        check_obligatory_hedge as _check_obligatory_hedge,
        check_hedge_failed_fallback as _check_hedge_failed_fallback,
        check_hedge_close as _check_hedge_close,
        compute_hedge_size as _compute_hedge_size,
    )
    _HEDGE_ENGINE_AVAILABLE = True
except ImportError:
    _HEDGE_ENGINE_AVAILABLE = False
    def _check_scan_hedge_losers(store, bar_idx, pos_state, all_pos_states=None, mode="crypto", cfg=None): return None
    def _check_obligatory_hedge(store, bar_idx, pos_state, mode="crypto", cfg=None): return None
    def _check_hedge_failed_fallback(store, bar_idx, pos_state, mode="crypto", cfg=None, hedge_attempt_result=False): return None
    def _check_hedge_close(store, bar_idx, hedge_pos_state, mode="crypto", cfg=None): return None
    def _compute_hedge_size(pos_state, cfg): return 0.0

# EXIT PATHS — R1/R2 loss bypasses + PPL v2 + WT crossunder final + SRS
try:
    from vec_paths.exit_r1_r2 import (
        check_r1_emergency_exit as _check_r1_emergency_exit,
        check_r2_wt_vel_slow_exit as _check_r2_wt_vel_slow_exit,
    )
    _EXIT_R1_R2_AVAILABLE = True
except ImportError:
    _EXIT_R1_R2_AVAILABLE = False
    def _check_r1_emergency_exit(store, bar_idx, pos_state, mode, cfg): return None
    def _check_r2_wt_vel_slow_exit(store, bar_idx, pos_state, mode, cfg): return None

try:
    from vec_paths.partial_profit_lock_v2 import (
        check_ppl_step1 as _check_ppl_step1,
        check_ppl_step2 as _check_ppl_step2,
        check_ppl_step3 as _check_ppl_step3,
    )
    _PPL_V2_AVAILABLE = True
except ImportError:
    _PPL_V2_AVAILABLE = False
    def _check_ppl_step1(store, bar_idx, pos_state, cfg): return None
    def _check_ppl_step2(store, bar_idx, pos_state, cfg): return None
    def _check_ppl_step3(store, bar_idx, pos_state, cfg): return None

try:
    from vec_paths.wt_crossunder_final import check_wt_crossunder_final_exit as _check_wt_crossunder_final_exit
    _WT_CROSSUNDER_FINAL_AVAILABLE = True
except ImportError:
    _WT_CROSSUNDER_FINAL_AVAILABLE = False
    def _check_wt_crossunder_final_exit(store, bar_idx, pos_state, mode, cfg): return None

try:
    from vec_paths.structural_range_shift import check_srs_exit as _check_srs_exit
    _SRS_AVAILABLE = True
except ImportError:
    _SRS_AVAILABLE = False
    def _check_srs_exit(store, bar_idx, pos_state, mode, cfg): return None

# ───────────────────────────────────────────────────────────
# Path setup
# ───────────────────────────────────────────────────────────
IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE_PATH = Path("/home/niels/binance-sandbox")
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")

sys.path.insert(0, str(BASE_PATH))
import metrics_guard  # MANDATORY — every Sharpe routes through here

# ───────────────────────────────────────────────────────────
# Config dataclass — all canonical switches
# ───────────────────────────────────────────────────────────
@dataclass
class VecConfig:
    """All sweep-tunable config knobs mirroring config.py / config_tradier.py.

    Defaults match the live defaults. Override fields to test variants.
    NEVER add a field here without a corresponding handler in VecEngine.simulate().
    """

    # ── Entry score ──────────────────────────────────────────
    ENTRY_SCORE_THRESHOLD: float = 18.0
    TRADIER_ENTRY_SCORE_THRESHOLD: float = 24.0

    # ── WT composite scoring ─────────────────────────────────
    TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = False
    WT_COMPOSITE_LONG_MIN: float = 0.0
    WT_COMPOSITE_SHORT_MIN: float = 0.0

    # ── HTF alignment gate ───────────────────────────────────
    HTF_ALIGN_REQUIRED: int = 1          # crypto default
    HTF_ALIGN_REQUIRED_TRADIER: int = 2  # stocks default
    HTF_TFS: List[str] = field(default_factory=lambda: ["1h", "4h", "D"])

    # ── LTF/HTF stoch alignment gates (parity with ez_manage.check_entry_alignment) ──
    # Default OFF — gate was too restrictive on its own (0 trades on tradier × 2.4mo).
    # Real engine's 27 trades come through OTHER entry paths (FH_MOMENTUM, SATOSHIT) that
    # bypass this gate. Use only when you want strict alignment-only entries.
    LTF_ALIGN_GATE_ENABLED: bool = False
    K3M_CAP: float = 70.0    # LONG blocked when k_3m >= cap; SHORT mirror
    K3M_FLOOR: float = 30.0  # LONG blocked when k_3m >= (100-floor); SHORT mirror

    # ── WT_DC_ENTRY score gate (parity with wt_dc_entry_scorer.score_entry_multitf) ──
    # Real engine: 26 of 27 tradier baseline trades went through this scorer.
    # Score = 25*(wt1_D vs wt2_D match) + 25*(wt1_4h vs wt2_4h match)
    #       + 30*(wt_cross_1h direction match) + 10*(dc_1h zone) + 10*(stoch_k_5m zone)
    # Threshold 55 = 4h_bull + 1h_cross_BULL minimum (matches tradier_manage:2000 default).
    WT_DC_ENTRY_GATE_ENABLED: bool = True
    WT_DC_ENTRY_THRESHOLD: float = 55.0

    # ── Per-sym entry cooldown (parity with AUGMENT_LOCK / DUP_GUARD) ──
    # Real engine blocks reentry on same position_key for N seconds after last close.
    # 900s = 15 min default per CLAUDE.md AUGMENT_LOCK.
    ENTRY_COOLDOWN_SEC: int = 900

    # ── ENTRY SIGNAL GATE (parity with backtest_v8_engine:1708) ──
    # Real engine pre-computes per-symbol mask: union of wt/stoch/dc cross binary flags,
    # dilated ±3 bars. Only checks entries on flagged bars (~10% of bars).
    # Comment in real engine: "Skips ~90% of bars where no signal can possibly trigger".
    ENTRY_SIGNAL_GATE_ENABLED: bool = True

    # ── MIN HOLD MINUTES (parity with tradier_manage TRADIER_MIN_HOLD_MINUTES) ──
    # Real engine: tradier 240 min (4h), crypto N/A but practical ~30 min effective.
    # Block ALL exits (except R1 emergency) until min_hold elapsed since entry.
    MIN_HOLD_MINUTES: float = 240.0       # tradier default
    MIN_HOLD_MINUTES_CRYPTO: float = 30.0  # crypto looser

    # ── Tradeability whitelist (parity with TradeManager.is_symbol_tradeable) ──
    # Real engine LIVE reads symbols_trb_long.json / symbols_trb_short.json — per-side whitelist.
    # Real engine BACKTEST OVERRIDES this to always-True (backtest_v8_engine.py:3537).
    # Default OFF for backtest parity. Set True to gate by live whitelist in dev/live sims.
    TRADEABILITY_GATE_ENABLED: bool = False
    TRADEABILITY_ACCOUNT: str = "trb"

    # ── Stoch gates ──────────────────────────────────────────
    TRADIER_STOCH_ENTRY_LONG_TRADIER: float = 80.0
    TRADIER_STOCH_ENTRY_SHORT_TRADIER: float = 20.0
    TRADIER_STOCH_EXTREME_LONG_TRADIER: float = 20.0
    TRADIER_STOCH_EXTREME_SHORT_TRADIER: float = 80.0
    COMBINED_STOCH_GATE: float = 50.0   # crypto
    COMBINED_STOCH_GATE_TRADIER: float = 60.0

    # ── RSI entry ────────────────────────────────────────────
    TRADIER_RSI_ENTRY_SHORT_TRADIER: float = 60.0  # short: RSI >= this
    # TRADIER_RSI_ENTRY_LONG_TRADIER DISABLED per CLAUDE.md / canonical_switches

    # ── K-zone ───────────────────────────────────────────────
    TRADIER_K_ZONE_ENTRY_BONUS_TRADIER: float = 2.0
    TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER: float = 20.0
    TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER: float = 80.0

    # ── RSI2 exit ────────────────────────────────────────────
    TRADIER_RSI2_ENABLED: bool = False
    TRADIER_RSI2_EXIT_THRESHOLD_LONG: float = 95.0
    TRADIER_RSI2_EXIT_THRESHOLD_SHORT: float = 5.0

    # ── DC daytrade ──────────────────────────────────────────
    TRADIER_DC_DAYTRADE_ENABLED: bool = False
    TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES: int = 120
    TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    TRADIER_DC_DAYTRADE_STOP_PCT: float = 0.5
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 1.0

    # ── DC position threshold ────────────────────────────────
    TRADIER_DC_POSITION_ENTRY_THRESHOLD: float = 0.7

    # ── FH Momentum ──────────────────────────────────────────
    TRADIER_FH_MOMENTUM_DC_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_DC_MAX_LONG: float = 0.7
    TRADIER_FH_MOMENTUM_MFI_CONFIRM: bool = True
    TRADIER_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5

    # ── Structural range shift exit ──────────────────────────
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = False
    STRUCTURAL_RANGE_SHIFT_TF: str = "1h"

    # ── WT crossunder final exit ──────────────────────────────
    WT_CROSSUNDER_FINAL_ENABLED: bool = False

    # ── WT exit TF config ────────────────────────────────────
    TRADIER_WT_EXIT_TFS_TRADIER: List[str] = field(default_factory=lambda: ["5m", "15m", "1h"])
    TRADIER_WT_EXIT_MIN_TFS_TRADIER: int = 2

    # ── Hedge overhaul ───────────────────────────────────────
    HEDGE_EXIT_BYPASS_NOLOSS: bool = True
    HEDGE_EXIT_WT_TF: str = "3m"
    HEDGE_CLOSE_REMOVE_FROM_TRADEABLE: bool = True
    HEDGE_SAME_SYMBOL_PCT: float = 0.5
    HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE: bool = False  # currently disabled by design

    # ── HEDGE_ENGINE (vec_paths/hedge_engine.py) ─────────────
    # Master gate — OFF by default in backtest (hedge is a live-trading recovery tool).
    # Enable for backtest experiments to measure hedge impact on Sharpe/DD.
    # Source: ez_positions_quick.py:5002 scan_and_hedge_losers + ez_manage.py:14339.
    # Config defaults match 2026-05-10 strip values (CLAUDE.md).
    HEDGE_ENGINE_ENABLED: bool = False
    # HEDGE_ACCOUNTS: which accounts are hedge-enabled (crypto only).
    # In vec sim we use HEDGE_MODE as the global gate; HEDGE_ENGINE_ENABLED is the backtest gate.
    HEDGE_ACCOUNTS: list = None  # type: ignore — set to list by default in __post_init__ below
    # OBLIGATORY_HEDGE: fires inside UNIVERSAL_NOLOSS_GATE when WT confirms reversal.
    OBLIGATORY_HEDGE_ENABLED: bool = True
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.25   # must be at or below this gain to trigger
    OBLIGATORY_HEDGE_WT_TFS_REQUIRED: int = 1      # after 2026-05-10 strip: 3m alone (req=1)
    OBLIGATORY_HEDGE_WT_USE_3M: bool = True
    OBLIGATORY_HEDGE_WT_USE_1M: bool = False
    OBLIGATORY_HEDGE_WT_USE_15M: bool = False
    OBLIGATORY_HEDGE_WT_USE_1H: bool = True
    # HEDGE_TRIGGER (scan_and_hedge_losers trigger mode)
    # 2026-05-10 strip: all three require_* flags set to False, use_3m_alone=True
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = False
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H: bool = False
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = True
    # HEDGE_CLOSE_MODE: how to close the hedge when loser recovers.
    # 'wt_3m' = close when wt1_3m turns against the hedge (= back in favor of loser).
    # Other options: 'wt_3m_and_1h', 'wt_15m'.
    HEDGE_CLOSE_MODE: str = "wt_3m"
    # HEDGE_FAILED_FALLBACK: close the loser when hedge can't be taken.
    HEDGE_FAILED_FALLBACK_CLOSE_ENABLED: bool = True
    # HEDGE_DETERIORATING_GAIN: require gain to be actively dropping before hedging.
    # 2026-05-10 strip: DISABLED (False) — wt_3m trigger supersedes this requirement.
    HEDGE_DETERIORATING_GAIN_ENABLED: bool = False
    HEDGE_OVERSIZE_RATIO: float = 2.0   # max ratio of hedge to loser (cap from config.py)
    HEDGE_MAX_RATIO: float = 2.0        # hard cap (mirrors config.py)

    # ── Reentry overhaul ─────────────────────────────────────
    REENTRY_WT15M_CROSS_ENABLED: bool = True
    REENTRY_WT15M_SIZE_MULT: float = 1.5
    REENTRY_WT15M_K_MAX: float = 50.0
    REENTRY_WT15M_HTF_FAVOR_REQUIRED: bool = True
    REENTRY_K15M_PARTIAL_ENABLED: bool = True
    REENTRY_K15M_PARTIAL_THRESHOLD: float = 50.0
    REENTRY_K15M_PARTIAL_MULT: float = 0.5
    REENTRY_POST_CONSOL_ENABLED: bool = False
    REENTRY_POST_CONSOL_MULT: float = 1.5
    REENTRY_POST_CONSOL_ATR_THRESHOLD: float = 0.005
    REENTRY_POST_CONSOL_TFS_REQUIRED: int = 2

    # ── Delta engine ─────────────────────────────────────────
    DELTA_ENTRY_ENABLED: bool = False
    DELTA_ENGINE_ENABLED: bool = False
    DELTA_ENTRY_VEL_MIN: float = 0.5
    DELTA_ENTRY_TF: str = "15m"
    DELTA_HTF_GATE: str = "none"         # "none", "4h", "4h_D", "4h_D_strict"
    DELTA_ENTRY_MIN_TF: int = 4          # min TF count for delta entry (DEFAULT_CFG)

    # ── SENTIMENT_BOOST augment ───────────────────────────────
    # Source: tradier_manage.py:7408 periodic_sentiment_rebalancing()
    SENTIMENT_BOOST_ENABLED: bool = False
    SENTIMENT_BOOST_MIN_GAIN_PCT: float = 0.5    # min pos gain to allow boost
    SENTIMENT_BOOST_DEVIATION_PCT: float = 0.25  # min deviation from ideal to augment
    SENTIMENT_BOOST_COOLDOWN_SEC: float = 1800.0  # 30-min cooldown
    SENTIMENT_BOOST_START_POSITION_SIZE: float = 600.0  # mirrors config.START_POSITION_SIZE

    # ── RATIO_BOOST / RATIO_CUT sizing ───────────────────────
    # Source: tradier_manage.py:9337 execute_now RATIO-AWARE SIZING block
    RATIO_BOOST_ENABLED: bool = False    # OFF by default (backtest ratio=0.5 neutral)
    LS_RATIO_MIN: float = 0.50           # below this: RATIO_BOOST
    LS_RATIO_MAX: float = 2.00           # above this: RATIO_CUT
    TRADIER_RATIO_REQUIRE_MIN_GAIN: bool = False
    TRADIER_RATIO_BOOST_MIN_GAIN_PCT: float = 1.0

    # ── RZ exit ──────────────────────────────────────────────
    RZ_EXIT_ENABLED: bool = False

    # ── SATOSHIT (crypto + tradier additive entry) ──────────
    # Source: ez_satoshit.satoshit_entry_signal() + ez_manage.py:10116/10397.
    # 5-vote system on 15m indicators: rsi/bb_pct_b_1h/ha/stoch_k/mfi + HTF mfi_D/rvol_1h.
    # SATOSHIT_ENTRY_FILTER=True (crypto default) uses it as a GATE on the WT path.
    # SATOSHIT_ENABLED=True (additive) fires OPEN independently via vec_paths/satoshit.py.
    # SATOSHIT_ENABLED_TRADIER: tradier additive (also via vec_paths/satoshit.py).
    SATOSHIT_ENABLED_TRADIER: bool = False
    SATOSHIT_ENTRY_FILTER: bool = True   # crypto gate (default True per config.py)
    SATOSHIT_ENABLED: bool = False       # additive open (default OFF — both modes)
    SATOSHIT_MIN_VOTES: int = 3
    SATOSHIT_SCORE_BONUS: float = 30.0
    SATOSHIT_LONG_RSI_MAX: float = 50.0
    SATOSHIT_LONG_BB_PCTB_MAX: float = 0.50
    SATOSHIT_LONG_HA_STREAK_MAX: int = 1
    SATOSHIT_LONG_STOCH_K_MAX: float = 60.0
    SATOSHIT_LONG_MFI_MAX: float = 60.0
    SATOSHIT_SHORT_RSI_MIN: float = 55.0
    SATOSHIT_SHORT_BB_PCTB_MIN: float = 0.55
    SATOSHIT_SHORT_HA_STREAK_MIN: int = 0
    SATOSHIT_SHORT_STOCH_K_MIN: float = 50.0
    SATOSHIT_SHORT_MFI_MIN: float = 50.0
    SATOSHIT_HTF_MFI_D_MIN: float = 30.0
    SATOSHIT_HTF_RVOL_1H_MIN: float = 0.3

    # ── FH_MOMENTUM (first-hour momentum entry — tradier + crypto) ──────────
    # Source: tradier_manage.py:5876-5917 (fires FIRST before all other entries).
    #         ez_manage.py:24001 (crypto_first_hour_momentum_loop, 13:00-14:30 UTC).
    # Fires OPEN when price has moved >= min_move from daily open in first-hour window.
    # FH_MOMENTUM_ENABLED: tradier gate (default OFF, TRADIER_FH_MOMENTUM_ENABLED alias).
    # CRYPTO_FH_MOMENTUM_ENABLED: crypto gate (default ON per config.py).
    FH_MOMENTUM_ENABLED: bool = False        # tradier default (OFF)
    CRYPTO_FH_MOMENTUM_ENABLED: bool = True  # crypto default (ON per config.py)
    FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    FH_MOMENTUM_MFI_CONFIRM: bool = True
    FH_MOMENTUM_DC_CONFIRM: bool = True
    FH_MOMENTUM_DC_MAX_LONG: float = 0.5
    FH_MOMENTUM_WINDOW_MINUTES: float = 60.0  # stocks: 60 min after open
    CRYPTO_FH_MOMENTUM_MIN_MOVE_PCT: float = 0.5
    CRYPTO_FH_MOMENTUM_DC_CONFIRM: bool = True
    CRYPTO_FH_MOMENTUM_DC_MAX_LONG: float = 0.5

    # ── BB_RECOVERY exit (bypass NOLOSS gate for stranded entries outside BB) ─
    # Source: tradier_manage.py:9275-9307 (execute_now, before NOLOSS block).
    # Fires when position entry was outside BB and price has recovered within tolerance.
    # tradier default: BB_RECOVERY_EXIT_ENABLED_TRADIER=True (config_tradier.py:778).
    # crypto default: BB_RECOVERY_EXIT_ENABLED=False (not in config.py).
    BB_RECOVERY_EXIT_ENABLED_TRADIER: bool = True   # tradier default (ON)
    BB_RECOVERY_EXIT_ENABLED: bool = False           # crypto default (OFF)
    BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER: float = 0.30
    BB_RECOVERY_EXIT_TOLERANCE_ATR_MULT_TRADIER: float = 0.0

    # ── MOM3 / MOM5 additive entry score boost ────────────────────────────────
    # Source: ez_manage.py:18765-18778 (BACKTEST_CHANGE_4/5, +22 per signal).
    # 3-bar (and 5-bar) mean-reversion: LONG when price < close_N_bar by threshold.
    # TF_FOCUS = "3m" for crypto, "5m" for tradier.
    # config.py: MOM3_ENTRY_ENABLED=True, thresholds ±1.0%/±1.5%.
    MOM3_ENTRY_ENABLED: bool = True
    MOM3_LONG_THRESHOLD: float = -1.0
    MOM3_SHORT_THRESHOLD: float = 1.0
    MOM5_ENTRY_ENABLED: bool = True
    MOM5_LONG_THRESHOLD: float = -1.5
    MOM5_SHORT_THRESHOLD: float = 1.5
    MOM3_GATE_REQUIRED: bool = False  # when True, block WT entry if neither MOM3 nor MOM5 fires

    # ── R1 DC emergency exit ─────────────────────────────────
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True
    R1_NEWBORN_WINDOW_MIN: int = 15
    R1_USE_DC_4BAR: bool = True
    R1_TF: str = "3m"

    # ── R2 WT velocity slow exit ─────────────────────────────
    WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.0
    WT_VEL_DECEL_RATIO: float = 0.8
    WT_VEL_USE_DECEL_RATIO_ONLY: bool = False
    R2_TF_LIST: List[str] = field(default_factory=lambda: ["15m"])
    WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = True

    # ── GOLDEN RULE sizing ───────────────────────────────────
    GOLDEN_RULE_ENABLED: bool = True
    GOLDEN_RULE_BASE_USD: float = 5.0
    GOLDEN_RULE_DC_15M_ENABLED: bool = True
    GOLDEN_RULE_BB_15M_ENABLED: bool = True
    GOLDEN_RULE_DC_1H_ENABLED: bool = True
    GOLDEN_RULE_BB_1H_ENABLED: bool = True
    GOLDEN_RULE_DC_4H_ENABLED: bool = True
    GOLDEN_RULE_BB_4H_ENABLED: bool = True
    GOLDEN_RULE_DC_D_ENABLED: bool = True
    GOLDEN_RULE_BB_D_ENABLED: bool = True
    GOLDEN_RULE_MULT_15M: float = 1.0
    GOLDEN_RULE_MULT_1H: float = 1.5
    GOLDEN_RULE_MULT_4H: float = 2.0
    GOLDEN_RULE_MULT_D: float = 3.0
    GOLDEN_RULE_OR_LOGIC: bool = True

    # ── GR HTF gate ──────────────────────────────────────────
    GR_HTF_GATE_ENABLED: bool = False
    GR_HTF_REQUIRE_BULL: int = 2
    GR_HTF_REQUIRE_BEAR: int = 2

    # ── STDEV breakout / bounce ──────────────────────────────
    STDEV_BREAKOUT_ENABLED: bool = False
    STDEV_BREAKOUT_HTF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])
    STDEV_BREAKOUT_PCTB_LONG: float = 1.0
    STDEV_BREAKOUT_PCTB_SHORT: float = 0.0
    STDEV_BREAKOUT_RVOL_MIN: float = 1.2
    STDEV_BOUNCE_ENABLED: bool = False
    STDEV_BOUNCE_HTF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])
    STDEV_BOUNCE_PCTB_LONG: float = 0.05
    STDEV_BOUNCE_PCTB_SHORT: float = 0.95
    STDEV_BOUNCE_RVOL_MIN: float = 1.2

    # ── VOL_TARGET sizing ────────────────────────────────────
    VOL_TARGET_ENABLED: bool = False
    VOL_TARGET_PCT: float = 0.02
    VOL_TARGET_LOW_CAP: float = 0.5
    VOL_TARGET_HIGH_CAP: float = 2.0
    VOL_TARGET_FIELD: str = "yz_vol_60_d"

    # ── DD Kelly sizing ──────────────────────────────────────
    DD_KELLY_ENABLED: bool = False
    DD_KELLY_TIER1_PCT: float = 0.5
    DD_KELLY_TIER2_PCT: float = 0.25
    DD_KELLY_TIER3_PCT: float = 0.125

    # ── PARTIAL PROFIT LOCK ──────────────────────────────────
    PARTIAL_PROFIT_LOCK_ENABLED: bool = False
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 0.5
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 0.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.02
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5

    # ── Momentum interception ────────────────────────────────
    TRADIER_MI_ENTRY_ENABLED_TRADIER: bool = False
    TRADIER_MI_EXIT_ENABLED_TRADIER: bool = False

    # ── HTF W/M alignment gates ──────────────────────────────
    HTF_W_M_ALIGN_GATE: bool = False
    HTF_DC_BREAKOUT: bool = False
    HTF_W_REVERSAL_EXIT: bool = False

    # ── Linearity / LR filter ────────────────────────────────
    LINEARITY_LR_LONG_ENABLED: bool = False
    LINEARITY_LR_SHORT_ENABLED: bool = False
    LINEARITY_LR_TFS: List[str] = field(default_factory=lambda: ["5m", "15m", "1h", "4h"])
    LINEARITY_LR_REQUIRE_ALL: bool = True
    LINEARITY_LR_LIN4H_MIN: float = 0.0

    # ── VEC STRATEGY GATES (shadow, default=OFF) ─────────────
    VEC_GATES_LOG_ONLY: bool = True

    # ── Universal no-loss gate ───────────────────────────────
    UNIVERSAL_NOLOSS_GATE: bool = True

    # ── DC_BREAK_HIGH / DC_BREAK_LOW (mirrors tradier_manage._check_dc_break) ──
    # Default OFF — only relevant for tradier mode with DC daytrade enabled.
    # When TRADIER_DC_DAYTRADE_ENABLED=True, vec_paths/dc_break.py is wired in
    # after the existing WT_DC_ENTRY gate and fires independently.
    # Source: tradier_manage.py:14624 (_check_dc_break)
    TRADIER_DC_DAYTRADE_BUFFER: float = 0.001
    TRADIER_DC_DAYTRADE_STOCH_FILTER: bool = True
    TRADIER_DC_DAYTRADE_K_EXHAUSTED_LONG: float = 85.0
    TRADIER_DC_DAYTRADE_K_EXHAUSTED_SHORT: float = 15.0
    # TRADIER_DC_DAYTRADE_ENABLED and TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION
    # already declared above — these map 1-to-1 with config_tradier.py

    # ── REENTRY paths (mirrors ez_reentry_pullback + backup tradier_manage) ──
    REENTRY_ENABLED: bool = True             # master gate for all REENTRY variants
    REENTRY_BREAKOUT_ENABLED: bool = True    # DC_BREAK_HIGH_15m + DC_BASIS_5m reclaim
    REENTRY_PULLBACK_ENABLED: bool = True    # profit-reduce + pullback + WT oversold
    REENTRY_TREND_ENABLED: bool = True       # HA + WT + stoch cross trend resume
    REENTRY_PROBE_ENABLED: bool = True       # HA-only, smallest size (0.6x)
    REENTRY_PULLBACK_DROP_PCT: float = 3.0   # price must drop >= X% from reduce level
    REENTRY_PULLBACK_WT3M_OS_THRESH: float = -10.0  # wt1_3m_prev threshold for oversold
    REENTRY_PULLBACK_REQUIRE_15M: bool = True   # require WT15m cross in direction
    REENTRY_PULLBACK_REQUIRE_VEL: bool = True   # require wt_velocity_3m > 0 (LONG)

    # ── PRICE_CROSS_BACK_REENTRY ─────────────────────────────
    # Source: tradier_manage.py:1880-1912 (BRANCH B, positionAmt==0).
    # Fires a reopen when: last close within max_age AND price within band_pct%.
    # Default OFF to match backtest_v8_engine behavior (loop doesn't track exit prices).
    PRICE_CROSS_BACK_REENTRY_ENABLED: bool = False
    PRICE_CROSS_BACK_MAX_AGE_MIN: float = 240.0    # 4 hours
    PRICE_CROSS_BACK_BAND_PCT: float = 0.3         # 0.3% distance band
    START_POSITION_SIZE: float = 600.0             # USD notional for new opens (tradier)

    # ── WT_3M_FORCE_OPEN ─────────────────────────────────────
    # Source: ez_manage.py:19969 + tradier_manage.py:1604.
    # Forces OPEN when position is ZERO and wt1_btf direction matches side.
    # Default OFF in vec to avoid dominating all signal paths.
    WT_3M_FORCE_OPEN_ENABLED: bool = False
    WT_3M_FORCE_OPEN_SIZE_USD: float = 9.0         # crypto default (100.0 for tradier)
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = True     # bypasses cooldown / dup-guard

    # ── GOLDEN_RULE enforcement loop ─────────────────────────
    # Source: ez_manage.py:_golden_rule_loop() + backtest_v8_engine.py:2339.
    # GOLDEN_RULE_ENABLED / BASE_USD / MULT_* / DC_*/BB_* already declared above.
    # New: HTF VETO (default False in backtest path per backtest_v8_engine.py:2416).
    GOLDEN_RULE_HTF_VETO_ENABLED: bool = False
    # GOLDEN_RULE_ENFORCE_LOOP_ENABLED: activates the always-on enforcement loop
    # (separate from the GOLDEN_RULE_ENABLED entry filter on the WT path).
    # Default OFF — the enforcement loop is a live-trading mandate (ang/inf always-long),
    # not needed for standard backtest sweeps.
    GOLDEN_RULE_ENFORCE_LOOP_ENABLED: bool = False

    # ── SCALP_V2 — HTF Breakout Scalper (htf_breakout_scalper.py) ───────────
    SCALP_MODE: bool = False
    SCALP_V2_VARIANT: str = "V1_WT_CONFIRM"
    SCALP_V2_DC_HTF_LIST: List[str] = field(default_factory=lambda: ["15m", "1h"])
    SCALP_V2_DC_HTF_REQUIRE_ALL: bool = True
    SCALP_V2_ENTRY_MODE: str = "breakout"
    SCALP_V2_MAX_HOLD_MINUTES: float = 15.0
    SCALP_V2_REDZONE_EXIT: bool = True
    SCALP_V2_REDZONE_K_THRESHOLD: int = 90
    SCALP_V2_LH_LL_EXIT: bool = True
    SCALP_V2_LH_LL_TF: str = "15m"
    SCALP_V2_MAX_CONCURRENT: int = 5
    SCALP_V2_REENTRY_COOLDOWN_S: int = 300
    SCALP_V2_ISOLATE: bool = True

    # ── SCALP_V3 — Ultra-short bar-based scalper (scalp_v3_live.py) ──────────
    SCALP_V3_ENABLED: bool = False
    SCALP_V3_MAX_CONCURRENT: int = 8
    SCALP_V3_POSITION_CAP_USD: float = 20.0
    SCALP_V3_SIDE_MODE: str = "BOTH"
    SCALP_V3_ENTRY_TREND_ENABLED: bool = True
    SCALP_V3_ENTRY_BAR_BREAK_ENABLED: bool = False
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN: float = 1.0
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_LONG: float = 1.0
    SCALP_V3_ENTRY_BAR_BREAK_VEL_MIN_SHORT: float = 1.0
    SCALP_V3_ENTRY_PULLBACK_ENABLED: bool = False
    SCALP_V3_ENTRY_DC_BREAK_ENABLED: bool = False
    SCALP_V3_ENTRY_WT_CROSS_ENABLED: bool = False
    SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED: bool = False
    SCALP_V3_ENTRY_STDEV_ENABLED: bool = False
    SCALP_V3_STDEV_TF: str = "3m"
    SCALP_V3_STDEV_MODE: str = "BOUNCE"
    SCALP_V3_STDEV_BREAK_HI: float = 1.0
    SCALP_V3_STDEV_BREAK_LO: float = 0.0
    SCALP_V3_STDEV_BREAK_HI_LONG: float = 1.0
    SCALP_V3_STDEV_BREAK_LO_SHORT: float = 0.0
    SCALP_V3_STDEV_BOUNCE_LO: float = 0.10
    SCALP_V3_STDEV_BOUNCE_HI: float = 0.90
    SCALP_V3_STDEV_BOUNCE_LO_LONG: float = 0.10
    SCALP_V3_STDEV_BOUNCE_HI_SHORT: float = 0.90
    SCALP_V3_STDEV_REJECT_HI: float = 0.95
    SCALP_V3_STDEV_REJECT_LO: float = 0.05
    SCALP_V3_K_FRESH_LO: float = 25.0
    SCALP_V3_K_FRESH_MID_LO: float = 50.0
    SCALP_V3_K_FRESH_MID_HI: float = 50.0
    SCALP_V3_K_FRESH_HI: float = 75.0
    SCALP_V3_LONG_K_RISE_MIN: float = 50.0
    SCALP_V3_LONG_K_RISE_MAX: float = 75.0
    SCALP_V3_SHORT_K_FALL_MIN: float = 25.0
    SCALP_V3_SHORT_K_FALL_MAX: float = 50.0
    SCALP_V3_EXIT_BAR_REVERSAL_ENABLED: bool = True
    SCALP_V3_EXIT_WT_FLIP_ENABLED: bool = True
    SCALP_V3_EXIT_K_CROSS_ENABLED: bool = True
    SCALP_V3_EXIT_REQUIRE_N_SIGNALS: int = 1
    SCALP_V3_EXIT_PROFIT_ONLY: bool = False
    SCALP_V3_EXIT_STDEV_REJECT_ENABLED: bool = False
    SCALP_V3_MAX_HOLD_MIN: float = 5.0

    # ── MICRO_SCALP — stocks + USDC micro-scalper ────────────────────────────
    MICRO_SCALP_STOCKS_MAKER_ENABLED: bool = False
    MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT: float = 0.05
    MICRO_SCALP_MIN_HOLD_BARS: int = 0
    MICRO_SCALP_USDC_MAKER_ENABLED: bool = False
    MICRO_SCALP_GAIN_THRESHOLD_PCT: float = 0.02

    # ── Misc ─────────────────────────────────────────────────
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    RATIO_MULTIPLIER: float = 3.0
    HEDGE_MODE: bool = True
    STRICT_NO_LOSS: bool = False  # Eliminated — replaced by R1/R2/HEDGE

    def update_from_dict(self, d: Dict[str, Any]) -> "VecConfig":
        """Return a new VecConfig with fields from dict d applied."""
        import copy
        c = copy.copy(self)
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# ───────────────────────────────────────────────────────────
# NPZ store reader (re-uses harness logic without importing it)
# ───────────────────────────────────────────────────────────
class _NPZStore:
    """Minimal NPZ accessor — forward-fills HTF numeric fields, mirrors IndicatorStore."""

    def __init__(self, path: Path):
        data = np.load(str(path), allow_pickle=True)
        self.arrays: Dict[str, np.ndarray] = {k: data[k] for k in data.files}
        self.timestamps: np.ndarray = self.arrays.get("timestamps", np.array([]))
        self.n_bars: int = len(self.timestamps)
        self.has_5m: bool = any(k.endswith("_5m") for k in self.arrays)
        self.has_3m: bool = any(k.endswith("_3m") for k in self.arrays)
        self._forward_fill_htf()
        self._decode_integers()
        self._rebuild_wt_cross()
        self._build_ts_index()

    def _forward_fill_htf(self):
        """Forward-fill HTF numeric arrays (vectorized — mirrors IndicatorStore step 1)."""
        _skip_prefixes = ("timestamps", "volume", "ha_", "ep_")
        _htf_sfx = ("_1h", "_4h", "_D", "_W", "_M")
        for key, arr in list(self.arrays.items()):
            if arr.dtype.kind not in ('f', 'i', 'u'):
                continue
            if any(key.startswith(s) for s in _skip_prefixes):
                continue
            if not any(key.endswith(s) for s in _htf_sfx):
                continue
            farr = arr.astype(np.float64)
            # Vectorized forward-fill: use pandas-style ffill via numpy
            mask = np.isnan(farr)
            if not np.any(mask):
                continue
            # np.maximum.accumulate trick for ffill
            idx_map = np.where(~mask, np.arange(len(farr)), 0)
            np.maximum.accumulate(idx_map, out=idx_map)
            self.arrays[key] = farr[idx_map].astype(arr.dtype)

    # Integer decoding maps (same as backtest_v8_harness._INT_DECODE)
    _INT_DECODE = {
        "wt_cross":          {-1: "BEAR", 0: "NONE", 1: "BULL"},
        "wt_signal":         {-1: "BEAR", 0: "NEUTRAL", 1: "BULL"},
        "wt_divergence":     {-1: "BEAR", 0: "", 1: "BULL"},
        "wt_momentum_state": {-2: "EXHAUST_DOWN", -1: "IMPULSE_DOWN", 0: "NEUTRAL",
                              1: "IMPULSE_UP", 2: "EXHAUST_UP"},
        "wt_peak_structure": {-1: "LH", 0: "NEUTRAL", 1: "HH"},
        "wt_trough_structure": {-1: "LL", 0: "NEUTRAL", 1: "HL"},
        "wt_structure":      {-1: "LH", 0: "NEUTRAL", 1: "HH"},
        "wt_wave_phase":     {-1: "CONTRACTING", 0: "NEUTRAL", 1: "EXPANDING"},
        "wt_composite_bias": {-1: "SHORT", 0: "NEUTRAL", 1: "LONG"},
        "ha":                {-1: "red", 0: "neutral", 1: "green"},
    }

    def _decode_integers(self):
        """Decode integer-encoded string fields to object arrays (vectorized)."""
        for prefix, mapping in self._INT_DECODE.items():
            for tf in ("3m", "5m", "15m", "1h", "4h", "D", "W", "M"):
                key = f"{prefix}_{tf}"
                if key not in self.arrays:
                    # also check non-TF keys like "ha_3m" already handled
                    continue
                arr = self.arrays[key]
                if arr.dtype.kind not in ('i', 'u'):
                    continue  # already decoded or float
                # Vectorized decode
                result = np.empty(len(arr), dtype=object)
                result[:] = mapping.get(0, "")
                for int_val, str_val in mapping.items():
                    result[arr == int_val] = str_val
                self.arrays[key] = result

    def _rebuild_wt_cross(self):
        """Rebuild wt_cross string arrays from bull/bear events (vectorized)."""
        for tf in ("3m", "5m", "15m", "1h", "4h", "D"):
            bull_k = f"wt_cross_bull_{tf}"
            bear_k = f"wt_cross_bear_{tf}"
            cross_k = f"wt_cross_{tf}"
            if bull_k not in self.arrays or bear_k not in self.arrays:
                continue
            bull = self.arrays[bull_k]
            bear = self.arrays[bear_k]
            existing = self.arrays.get(cross_k)
            # Force rebuild when all-zero numeric
            if existing is not None and existing.dtype.kind in ('i', 'u', 'f'):
                if np.all(existing == 0):
                    existing = None
            if existing is None:
                # Vectorized forward-fill using index-accumulate trick
                bull_i = np.asarray(bull, dtype=np.int8)
                bear_i = np.asarray(bear, dtype=np.int8)
                n = len(bull_i)
                # cross_event: 1 at bull cross, -1 at bear cross, 0 otherwise
                cross_event = bull_i.astype(np.int8) - bear_i.astype(np.int8)
                # Forward-fill: keep last non-zero value via index map
                # Build indices of event bars; fill forward using searchsorted
                event_idx = np.where(cross_event != 0)[0]
                if len(event_idx) == 0:
                    self.arrays[cross_k] = np.full(n, "NONE", dtype=object)
                    continue
                # For each bar, find the last event bar before or at it
                # np.searchsorted finds insertion point; we want the event AT or BEFORE
                bar_positions = np.arange(n)
                fill_idx = np.searchsorted(event_idx, bar_positions, side="right") - 1
                # Bars before the first event get NONE (fill_idx = -1)
                result = np.empty(n, dtype=object)
                result[:] = "NONE"
                valid = fill_idx >= 0
                result[valid] = np.where(
                    cross_event[event_idx[fill_idx[valid]]] == 1, "BULL", "BEAR"
                )
                self.arrays[cross_k] = result

    def _build_ts_index(self):
        """Build timestamp → index map for fast bar lookup."""
        self.ts_to_idx: Dict[int, int] = {int(ts): i for i, ts in enumerate(self.timestamps)}

    def get(self, key: str, idx: int, default=0.0):
        arr = self.arrays.get(key)
        if arr is None:
            return default
        if idx < 0 or idx >= len(arr):
            return default
        val = arr[idx]
        if arr.dtype == object:
            return val if val is not None else default
        if isinstance(val, (np.floating, float)) and np.isnan(val):
            return default
        return val

    def price(self, idx: int) -> float:
        return float(self.get("close", idx, 0.0))

    def f(self, key: str, idx: int, default: float = 0.0) -> float:
        """Convenience float getter."""
        v = self.get(key, idx, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def s(self, key: str, idx: int, default: str = "") -> str:
        """Convenience string getter."""
        v = self.get(key, idx, default)
        return str(v) if v is not None else default

    def b(self, key: str, idx: int, default: bool = False) -> bool:
        """Convenience bool getter."""
        v = self.get(key, idx, default)
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        try:
            return bool(int(v))
        except (TypeError, ValueError):
            return default


# ───────────────────────────────────────────────────────────
# Per-symbol position state
# ───────────────────────────────────────────────────────────
@dataclass
class _PositionState:
    """Tracks one position (LONG or SHORT) for one symbol."""
    open: bool = False
    side: str = ""          # "LONG" or "SHORT"
    entry_price: float = 0.0
    entry_ts: float = 0.0
    last_close_ts: float = 0.0  # for entry cooldown (parity with AUGMENT_LOCK)
    qty: float = 1.0        # normalized units
    gain_pct: float = 0.0
    max_gain_pct: float = 0.0
    # PARTIAL_PROFIT_LOCK state
    ppl_fired: bool = False
    ppl_stop_level: float = 0.0
    ppl_stop_upgraded: bool = False
    ppl_first_exit_price: float = 0.0
    # R1/R2 flags
    r1_fired: bool = False
    # Augment tracking
    augmented: bool = False
    # Open-position MtM for final bar
    mark_price: float = 0.0
    # REENTRY_PULLBACK: price at which last profit-taking REDUCE occurred
    # Updated whenever gain drops from max_gain by more than 1% (approximation of a reduce).
    last_reduce_price: float = 0.0
    # SENTIMENT_BOOST: timestamp of last augment (cooldown gate)
    last_augment_ts: float = 0.0
    # PRICE_CROSS_BACK_REENTRY: price at which the last close occurred.
    # Set by VecEngine.simulate() whenever a position closes. Required by price_cross_back.py.
    last_close_price: float = 0.0
    # SCALP_V2/V3: entry reason tag (contains SCALP_V2_OPEN_ / SCALP_V3_OPEN_ prefix).
    # Used by exit checker to identify V2/V3-managed positions.
    reason: str = ""
    # HEDGE_ENGINE: True when this position was opened as a same-symbol hedge.
    # Prevents double-hedging (hedge positions don't get hedged again).
    hedge_active: bool = False
    # Bar index when position was opened (used for max-hold exit approximation in V2/V3).
    open_bar: int = 0
    # MICRO_SCALP: prev_gain and reopen state for micro-scalp close/reopen logic.
    prev_gain: float = 0.0
    micro_scalp_exit_price: float = 0.0
    micro_scalp_orig_side: str = ""


# ───────────────────────────────────────────────────────────
# Core vec engine
# ───────────────────────────────────────────────────────────
class VecEngine:
    """Vectorized backtest engine.

    Approximates the real backtest_v8_engine without importing ez_manage.
    Every Sharpe emitted routes through metrics_guard.

    Differences from real engine (documented so validator can check):
      - HEDGE: simulated as same-symbol opposite-side open, not HedgeEngine
      - PARTIAL_PROFIT_LOCK: Step 1+2 wired, Step 3 (stop trail) simplified
      - GOLDEN_RULE: entry filter + sizing scalar (approx USD-notional logic)
      - DELTA_ENGINE: velocity proxy (wt_velocity field) not real delta tracker
      - No MultiAccountTradeManager (no portfolio L/S ratio gate)
    """

    def __init__(
        self,
        mode: str = "crypto",
        npz_dir: Optional[str] = None,
    ):
        self.mode = mode
        if npz_dir is None:
            npz_dir = str(BASE_PATH / "backtest_v8" / "indicators")
        self.npz_dir = Path(npz_dir)
        self._stores: Dict[str, _NPZStore] = {}

    def _load_store(self, sym: str) -> Optional[_NPZStore]:
        if sym in self._stores:
            return self._stores[sym]
        path = self.npz_dir / f"{sym}.npz"
        if not path.exists():
            return None
        store = _NPZStore(path)
        self._stores[sym] = store
        return store

    def _base_tf(self) -> str:
        return "3m" if self.mode == "crypto" else "5m"

    def simulate(
        self,
        symbols: List[str],
        cfg: Optional[VecConfig] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        capital: float = 10000.0,
    ) -> Dict[str, Any]:
        """Run simulation; return canonical metric dict.

        Returns dict with keys matching CLAUDE.md mandatory reporting line:
          pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr,
          trades, max_dd_pct, n_syms, years, acc_gain_pct
        Plus diagnostic fields: wins, losses, verdict, note.

        All Sharpe values are validated via metrics_guard before return.
        """
        if cfg is None:
            cfg = VecConfig()

        btf = self._base_tf()

        # ── Load stores ──────────────────────────────────────
        stores: Dict[str, _NPZStore] = {}
        for sym in symbols:
            st = self._load_store(sym)
            if st is None:
                continue
            if st.n_bars < 2:
                continue
            stores[sym] = st

        if not stores:
            return self._empty_result("no NPZ data loaded")

        # ── Build merged timestamp union ─────────────────────
        # Use intersection of timestamps present in all stores
        # Find common time range
        ts_min = max(st.timestamps[0] for st in stores.values())
        ts_max = min(st.timestamps[-1] for st in stores.values())
        if start_ts is not None:
            ts_min = max(ts_min, start_ts)
        if end_ts is not None:
            ts_max = min(ts_max, end_ts)
        if ts_min >= ts_max:
            return self._empty_result("no overlapping time range")

        # Use first store's timestamps as the bar timeline (numpy slice for speed)
        ref_store = next(iter(stores.values()))
        ts_arr = ref_store.timestamps
        i_start = int(np.searchsorted(ts_arr, ts_min, side="left"))
        i_end = int(np.searchsorted(ts_arr, ts_max, side="right"))
        all_ts_np = ts_arr[i_start:i_end]
        if len(all_ts_np) < 2:
            return self._empty_result("fewer than 2 bars in range")
        all_ts = all_ts_np.tolist()
        # Build per-store local bar index arrays for fast lookup
        # (offset from store's own timestamp array start)
        store_offsets: Dict[str, int] = {}
        store_start_idx: Dict[str, int] = {}
        for sym, st in stores.items():
            si = int(np.searchsorted(st.timestamps, ts_min, side="left"))
            store_start_idx[sym] = si

        # ── Per-symbol simulation ────────────────────────────
        returns_by_sym: Dict[str, List[float]] = {sym: [] for sym in stores}
        sim_years = (float(all_ts_np[-1]) - float(all_ts_np[0])) / (365.25 * 86400)

        # DD tracking (cumulative % across all trades)
        all_returns: List[float] = []
        peak_equity: float = 0.0
        max_dd: float = 0.0
        running_gain: float = 0.0

        # Position states: sym -> {side -> _PositionState}
        pos_states: Dict[str, Dict[str, _PositionState]] = {
            sym: {"LONG": _PositionState(), "SHORT": _PositionState()}
            for sym in stores
        }

        # ── Portfolio state for RATIO_BOOST sizing (tracks all open positions) ──
        # portfolio_state: {position_key -> {qty, price}} where position_key = "SYMBOL_SIDE"
        portfolio_state: Dict[str, Dict[str, float]] = {}

        # ── Lazy-import new path modules (defensive — don't fail if not present) ──
        _sentiment_boost_fn = None
        _ratio_size_fn = None
        _delta_entry_fn = None
        _golden_rule_enforce_fn = None
        _wt_force_open_fn = None
        _price_cross_back_fn = None
        try:
            from vec_paths.sentiment_boost import check_sentiment_boost_augment as _sbf
            _sentiment_boost_fn = _sbf
        except Exception:
            pass
        try:
            from vec_paths.ratio_size import compute_size_multiplier as _rsf
            _ratio_size_fn = _rsf
        except Exception:
            pass
        try:
            from vec_paths.delta_engine import check_delta_entry as _def
            _delta_entry_fn = _def
        except Exception:
            pass
        try:
            from vec_paths.golden_rule_enforce import check_golden_rule_enforce as _gref
            _golden_rule_enforce_fn = _gref
        except Exception:
            pass
        try:
            from vec_paths.wt_force_open import check_wt_force_open as _wtfof
            _wt_force_open_fn = _wtfof
        except Exception:
            pass
        try:
            from vec_paths.price_cross_back import check_price_cross_back as _pcbf
            _price_cross_back_fn = _pcbf
        except Exception:
            pass
        _satoshit_fn = None
        _fh_momentum_fn = None
        _bb_recovery_fn = None
        _mom3_fn = None
        try:
            from vec_paths.satoshit import check_satoshit_entry as _sat_fn
            _satoshit_fn = _sat_fn
        except Exception:
            pass
        try:
            from vec_paths.fh_momentum import check_fh_momentum_entry as _fhm_fn
            _fh_momentum_fn = _fhm_fn
        except Exception:
            pass
        try:
            from vec_paths.bb_recovery import check_bb_recovery_exit as _bbr_fn
            _bb_recovery_fn = _bbr_fn
        except Exception:
            pass
        try:
            from vec_paths.mom3 import check_mom3_boost as _m3fn
            _mom3_fn = _m3fn
        except Exception:
            pass

        # Tracking for sizing scalars
        dd_state: Dict[str, float] = {"peak": 0.0, "dd_pct": 0.0}

        # ── TRADEABILITY whitelist load (parity with TradeManager.is_symbol_tradeable) ──
        tradeable_long: set = set()
        tradeable_short: set = set()
        if cfg.TRADEABILITY_GATE_ENABLED and self.mode == "tradier":
            try:
                _acct = cfg.TRADEABILITY_ACCOUNT
                _base = BASE_PATH
                _long_fp = _base / f"symbols_{_acct}_long.json"
                _short_fp = _base / f"symbols_{_acct}_short.json"
                if _long_fp.exists():
                    tradeable_long = set(s.upper() for s in json.load(open(_long_fp)))
                if _short_fp.exists():
                    tradeable_short = set(s.upper() for s in json.load(open(_short_fp)))
            except Exception:
                pass

        # ── ENTRY SIGNAL GATE (parity with backtest_v8_engine:1708) ──
        # Real engine ONLY checks entry candidates on bars where at least one binary
        # cross flag fires (dilated ±3 bars). Skips ~90% of bars. CRITICAL for parity.
        entry_gate_per_sym: Dict[str, Optional[set]] = {}
        GATE_KEYS = [
            "wt_cross_bull_15m", "wt_cross_bear_15m",
            "wt_cross_bull_3m", "wt_cross_bear_3m",
            "wt_cross_bull_1h", "wt_cross_bear_1h",
            "stoch_crossover_3m", "stoch_crossunder_3m",
            "stoch_crossover_15m", "stoch_crossunder_15m",
            "dc_high_crossover_3m", "dc_low_crossunder_3m",
        ]
        if cfg.ENTRY_SIGNAL_GATE_ENABLED:
            for g_sym, g_store in stores.items():
                try:
                    g_n = len(g_store.timestamps)
                    g_mask = np.zeros(g_n, dtype=bool)
                    for g_key in GATE_KEYS:
                        arr = g_store.arrays.get(g_key)
                        if arr is not None and arr.ndim >= 1 and arr.shape[0] == g_n:
                            g_mask |= arr.astype(bool)
                    if g_mask.any():
                        g_dil = g_mask.copy()
                        for g_d in range(1, 4):
                            if g_d < g_n:
                                g_dil[g_d:] |= g_mask[:-g_d]
                                g_dil[:-g_d] |= g_mask[g_d:]
                        g_mask = g_dil
                    entry_gate_per_sym[g_sym] = set(int(g_store.timestamps[i]) for i in np.where(g_mask)[0])
                except Exception:
                    entry_gate_per_sym[g_sym] = None
        else:
            entry_gate_per_sym = {sym: None for sym in stores}

        for idx, ts in enumerate(all_ts):
            ts_i = int(ts)

            for sym, store in stores.items():
                # Fast bar index: use store_start_idx offset + enumerate position
                # Since all stores share the same resolution, idx maps directly
                bar_idx = store_start_idx[sym] + idx
                if bar_idx >= store.n_bars:
                    continue
                # Verify timestamp matches (handle gaps in tradier bars)
                if int(store.timestamps[bar_idx]) != ts_i:
                    # Fall back to dict lookup for this bar
                    bar_idx = store.ts_to_idx.get(ts_i)
                    if bar_idx is None:
                        continue

                price = store.price(bar_idx)
                if price <= 0:
                    continue

                pos_long = pos_states[sym]["LONG"]
                pos_short = pos_states[sym]["SHORT"]

                # ── Update gains on open positions ───────────
                for pos in (pos_long, pos_short):
                    if pos.open:
                        pos.mark_price = price
                        pos.prev_gain = pos.gain_pct  # save before update (MICRO_SCALP decel detect)
                        if pos.side == "LONG":
                            pos.gain_pct = (price - pos.entry_price) / pos.entry_price * 100.0
                        else:
                            pos.gain_pct = (pos.entry_price - price) / pos.entry_price * 100.0
                        if pos.gain_pct > pos.max_gain_pct:
                            pos.max_gain_pct = pos.gain_pct
                        # Track last_reduce_price: if gain was previously >PPL threshold
                        # and has now dropped by >= 1%, approximate as a profit-take reduce.
                        # (REENTRY_PULLBACK uses this to know reentry is warranted.)
                        _ppl_thr = cfg.PARTIAL_PROFIT_LOCK_GAIN_PCT
                        if pos.max_gain_pct >= _ppl_thr and pos.gain_pct < pos.max_gain_pct - 1.0:
                            if pos.last_reduce_price == 0.0:
                                # Record the peak price as the approximate reduce level
                                if pos.side == "LONG":
                                    pos.last_reduce_price = pos.entry_price * (1.0 + pos.max_gain_pct / 100.0)
                                else:
                                    pos.last_reduce_price = pos.entry_price * (1.0 - pos.max_gain_pct / 100.0)

                # ── R1: DC emergency exit (within newborn window) ──
                # Routed via vec_paths/exit_r1_r2.py — BYPASSES NOLOSS gate.
                for pos in (pos_long, pos_short):
                    if not pos.open:
                        continue
                    _r1_sig = _check_r1_emergency_exit(store, bar_idx, pos, self.mode, cfg)
                    if _r1_sig is not None:
                        pnl = pos.gain_pct
                        returns_by_sym[sym].append(pnl)
                        all_returns.append(pnl)
                        running_gain += pnl
                        pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                        if pnl > 0:
                            pos.last_reduce_price = price
                        pos.r1_fired = True

                # ── R2: WT velocity slow exit ── BYPASSES NOLOSS gate.
                # Routed via vec_paths/exit_r1_r2.py.
                for pos in (pos_long, pos_short):
                    if not pos.open:
                        continue
                    _r2_sig = _check_r2_wt_vel_slow_exit(store, bar_idx, pos, self.mode, cfg)
                    if _r2_sig is not None:
                        pnl = pos.gain_pct
                        returns_by_sym[sym].append(pnl)
                        all_returns.append(pnl)
                        running_gain += pnl
                        pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                        if pnl > 0:
                            pos.last_reduce_price = price

                # ── SRS: Structural Range Shift exit ── BYPASSES NOLOSS gate.
                # Fires ABOVE NOLOSS gate in live (tradier_manage.py:5129, BEFORE gate at 5218).
                # Source: vec_paths/structural_range_shift.py.
                # Replaces the old inline approximation (bb_pct_b crossing 0.5) that was
                # wired as a gate on the WT-cross exit (incorrect — SRS is independent).
                if cfg.STRUCTURAL_RANGE_SHIFT_EXIT:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        _srs_sig = _check_srs_exit(store, bar_idx, pos, self.mode, cfg)
                        if _srs_sig is not None:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── PARTIAL_PROFIT_LOCK v2 (3-step) ─────────────
                # Source: vec_paths/partial_profit_lock_v2.py.
                # Replaces old inline approximation.
                # NOTE: PARTIAL_PROFIT_LOCK_ENABLED=False in live as of 2026-05-12.
                if cfg.PARTIAL_PROFIT_LOCK_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        if not pos.ppl_fired:
                            _ppl1 = _check_ppl_step1(store, bar_idx, pos, cfg)
                            if _ppl1 is not None:
                                frac = _ppl1["frac"]
                                partial_gain = pos.gain_pct * frac
                                returns_by_sym[sym].append(partial_gain)
                                all_returns.append(partial_gain)
                                running_gain += partial_gain
                                pos.qty *= (1.0 - frac)
                                pos.ppl_fired = True
                                pos.ppl_first_exit_price = price
                                pos.ppl_stop_level = _ppl1["stop_level"]
                                pos.ppl_stop_upgraded = False
                                continue
                        if pos.ppl_fired and not pos.ppl_stop_upgraded:
                            _ppl2 = _check_ppl_step2(store, bar_idx, pos, cfg)
                            if _ppl2 is not None:
                                pos.ppl_stop_level = _ppl2["new_stop"]
                                pos.ppl_stop_upgraded = True
                        if pos.ppl_fired and pos.ppl_stop_level > 0:
                            _ppl3 = _check_ppl_step3(store, bar_idx, pos, cfg)
                            if _ppl3 is not None:
                                pnl = pos.gain_pct
                                returns_by_sym[sym].append(pnl)
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price

                # ── BB_RECOVERY exit: bypass NOLOSS gate for stranded entries ───
                # Source: tradier_manage.py:9275 (before NOLOSS block in execute_now).
                # Fires when entry_price was outside BB and price returned within tolerance.
                # Default: tradier=True, crypto=False. Bypasses NOLOSS gate (same as live).
                if _bb_recovery_fn is not None:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        if pos.gain_pct >= 0:
                            continue  # only needed when at loss (is a NOLOSS bypass)
                        try:
                            _bbr_sig = _bb_recovery_fn(store, bar_idx, pos, self.mode, cfg)
                            if _bbr_sig is not None:
                                pnl = pos.gain_pct
                                returns_by_sym[sym].append(pnl)
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                        except Exception:
                            pass

                # ── WT_CROSSUNDER_FINAL standalone exit ──────────
                # Source: vec_paths/wt_crossunder_final.py.
                # Fires INDEPENDENTLY (not as a gate on the WT-cross below).
                # Live logic: LTF down + 15m confirm + ≥1 HTF against → CLOSE.
                # Default ON (WT_CROSSUNDER_FINAL_ENABLED=True) matching live.
                # UNIVERSAL_NOLOSS_GATE honored here — live has NOLOSS gate BELOW SRS
                # but ABOVE WT_CROSSUNDER_FINAL (live uses it inside the delta block
                # which is below the NOLOSS gate for tradier accounts).
                if cfg.WT_CROSSUNDER_FINAL_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        if cfg.UNIVERSAL_NOLOSS_GATE and pos.gain_pct < 0:
                            continue
                        _xu_sig = _check_wt_crossunder_final_exit(store, bar_idx, pos, self.mode, cfg)
                        if _xu_sig is not None:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── WT-based exit logic ─────────────────────────
                for pos in (pos_long, pos_short):
                    if not pos.open:
                        continue
                    cross = store.s(f"wt_cross_{btf}", bar_idx)
                    # Exit LONG on BEAR cross, SHORT on BULL cross
                    exit_signal = (pos.side == "LONG" and cross == "BEAR") or \
                                  (pos.side == "SHORT" and cross == "BULL")
                    if not exit_signal:
                        continue
                    # ── MIN_HOLD_MINUTES gate (parity with TRADIER_MIN_HOLD_MINUTES=240) ──
                    min_hold = cfg.MIN_HOLD_MINUTES if self.mode == "tradier" else cfg.MIN_HOLD_MINUTES_CRYPTO
                    if min_hold > 0:
                        hold_min = (ts_i - pos.entry_ts) / 60.0
                        if hold_min < min_hold:
                            continue
                    # UNIVERSAL_NOLOSS_GATE: block loss exits (except R1/R2/SRS already handled)
                    if cfg.UNIVERSAL_NOLOSS_GATE and pos.gain_pct < 0:
                        continue
                    # RSI2 exit check (tradier mode)
                    if self.mode == "tradier" and cfg.TRADIER_RSI2_ENABLED:
                        rsi2 = store.f("rsi2_5m", bar_idx, default=50.0)
                        if rsi2 == 0.0:
                            rsi2 = store.f("rsi_5m", bar_idx, default=50.0)
                        if pos.side == "LONG" and rsi2 < cfg.TRADIER_RSI2_EXIT_THRESHOLD_LONG:
                            continue
                        if pos.side == "SHORT" and rsi2 > cfg.TRADIER_RSI2_EXIT_THRESHOLD_SHORT:
                            continue
                    pnl = pos.gain_pct
                    returns_by_sym[sym].append(pnl)
                    all_returns.append(pnl)
                    running_gain += pnl
                    pos.open = False; pos.last_close_ts = ts_i
                    if pnl > 0:
                        pos.last_reduce_price = price

                # ── RZ exit ─────────────────────────────────────
                if cfg.RZ_EXIT_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        wt1 = store.f(f"wt1_{btf}", bar_idx)
                        # Extreme zone reversal exit
                        wt_extreme = store.b(f"wt_extreme_{btf}", bar_idx)
                        if wt_extreme:
                            cross_val = store.s(f"wt_cross_{btf}", bar_idx)
                            against = (pos.side == "LONG" and cross_val == "BEAR") or \
                                      (pos.side == "SHORT" and cross_val == "BULL")
                            if against and (not cfg.UNIVERSAL_NOLOSS_GATE or pos.gain_pct >= 0):
                                pnl = pos.gain_pct
                                returns_by_sym[sym].append(pnl)
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price

                # ── DELTA_ENGINE: velocity-proxy exit ──────────
                if cfg.DELTA_ENGINE_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        tf_d = cfg.DELTA_ENTRY_TF
                        vel = store.f(f"wt_velocity_{tf_d}", bar_idx)
                        vel_prev = store.f(f"wt_velocity_{tf_d}", max(0, bar_idx - 1))
                        vel_against = (pos.side == "LONG" and vel < 0) or (pos.side == "SHORT" and vel > 0)
                        vel_decelerating = abs(vel) < abs(vel_prev) * 0.7
                        if vel_against and vel_decelerating and pos.gain_pct >= 0:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price

                # ── SCALP_V2 exit (htf_breakout_scalper.py) ──────────────────
                # Only fires when SCALP_MODE=True AND position reason contains SCALP_V2_OPEN_.
                # Bypasses UNIVERSAL_NOLOSS_GATE (V2 positions use their own stop logic).
                if _SCALP_V2_AVAILABLE and cfg.SCALP_MODE:
                    for _v2e_pos in (pos_long, pos_short):
                        if not _v2e_pos.open:
                            continue
                        _v2e_reason = str(getattr(_v2e_pos, "reason", "") or "")
                        if "SCALP_V2_OPEN_" not in _v2e_reason:
                            continue
                        try:
                            _v2e_sig = _check_scalp_v2_exit(store, bar_idx, _v2e_pos, _v2e_pos.side, cfg)
                        except Exception:
                            _v2e_sig = None
                        if _v2e_sig is not None:
                            pnl = _v2e_pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            _v2e_pos.open = False; _v2e_pos.last_close_ts = ts_i; _v2e_pos.last_close_price = price
                            if pnl > 0:
                                _v2e_pos.last_reduce_price = price

                # ── SCALP_V3 exit (scalp_v3_live.py) ────────────────────────────
                # Only fires when SCALP_V3_ENABLED=True AND position reason contains SCALP_V3_OPEN_.
                if _SCALP_V3_AVAILABLE and cfg.SCALP_V3_ENABLED:
                    for _v3e_pos in (pos_long, pos_short):
                        if not _v3e_pos.open:
                            continue
                        _v3e_reason = str(getattr(_v3e_pos, "reason", "") or "")
                        if "SCALP_V3_OPEN_" not in _v3e_reason and "QUICK_SCALP_V3_OPEN_" not in _v3e_reason:
                            continue
                        try:
                            _v3e_sig = _check_scalp_v3_exit(store, bar_idx, _v3e_pos, _v3e_pos.side, cfg)
                        except Exception:
                            _v3e_sig = None
                        if _v3e_sig is not None:
                            pnl = _v3e_pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            _v3e_pos.open = False; _v3e_pos.last_close_ts = ts_i; _v3e_pos.last_close_price = price
                            if pnl > 0:
                                _v3e_pos.last_reduce_price = price

                # ── MICRO_SCALP exit + reopen (tradier_manage.py:1646 / ez_manage.py:21825) ──
                # CLOSE: gain >= threshold AND gain < prev_gain (first decel).
                # REOPEN: flat + price re-crosses exit_price.
                if _MICRO_SCALP_AVAILABLE:
                    _ms_mode = "usdc" if (self.mode == "crypto" and cfg.MICRO_SCALP_USDC_MAKER_ENABLED) else ("stocks" if (self.mode == "tradier" and cfg.MICRO_SCALP_STOCKS_MAKER_ENABLED) else None)
                    if _ms_mode is not None:
                        for _ms_pos in (pos_long, pos_short):
                            if _ms_pos.open:
                                try:
                                    _ms_sig = _check_micro_scalp_close(store, bar_idx, _ms_pos, _ms_mode, cfg)
                                except Exception:
                                    _ms_sig = None
                                if _ms_sig is not None:
                                    pnl = _ms_pos.gain_pct
                                    returns_by_sym[sym].append(pnl)
                                    all_returns.append(pnl)
                                    running_gain += pnl
                                    _ms_exit_px = price
                                    _ms_orig_side = _ms_pos.side
                                    _ms_pos.open = False; _ms_pos.last_close_ts = ts_i; _ms_pos.last_close_price = price
                                    _ms_pos.micro_scalp_exit_price = _ms_exit_px
                                    _ms_pos.micro_scalp_orig_side = _ms_orig_side
                                    if pnl > 0:
                                        _ms_pos.last_reduce_price = price
                            else:
                                # REOPEN check — position flat, check if price re-crossed exit
                                _ms_exit_px2 = float(getattr(_ms_pos, "micro_scalp_exit_price", 0.0) or 0.0)
                                _ms_orig_side2 = str(getattr(_ms_pos, "micro_scalp_orig_side", "") or "")
                                if _ms_exit_px2 > 0 and _ms_orig_side2 in ("LONG", "SHORT"):
                                    try:
                                        _ms_re_sig = _check_micro_scalp_reopen(store, bar_idx, _ms_exit_px2, _ms_mode, cfg, _ms_orig_side2)
                                    except Exception:
                                        _ms_re_sig = None
                                    if _ms_re_sig is not None:
                                        _ms_pos.open = True
                                        _ms_pos.side = _ms_orig_side2
                                        _ms_pos.entry_price = price
                                        _ms_pos.entry_ts = ts_i
                                        _ms_pos.mark_price = price
                                        _ms_pos.gain_pct = 0.0
                                        _ms_pos.max_gain_pct = 0.0
                                        _ms_pos.last_reduce_price = 0.0
                                        _ms_pos.ppl_fired = False
                                        _ms_pos.ppl_stop_level = 0.0
                                        _ms_pos.ppl_stop_upgraded = False
                                        _ms_pos.ppl_first_exit_price = 0.0
                                        _ms_pos.r1_fired = False
                                        _ms_pos.micro_scalp_exit_price = 0.0
                                        _ms_pos.micro_scalp_orig_side = ""
                                        _ms_pos.qty = self._compute_sizing(store, bar_idx, _ms_orig_side2, cfg, dd_state, running_gain)

                # ── GOLDEN_RULE enforcement (ez_manage._golden_rule_loop) ──────
                # Fires OPEN/AUGMENT on every qualifying bar independent of WT signal gate.
                # Default OFF. Only active when GOLDEN_RULE_ENFORCE_LOOP_ENABLED.
                # NOTE: The existing _check_golden_rule() is an ENTRY FILTER for the WT path.
                # This enforcement loop is a SEPARATE always-on mandate (ang/inf accounts).
                if _golden_rule_enforce_fn is not None and getattr(cfg, 'GOLDEN_RULE_ENFORCE_LOOP_ENABLED', False):
                    for _gr_pos in (pos_long, pos_short):
                        try:
                            _gr_result = _golden_rule_enforce_fn(store, bar_idx, sym, _gr_pos.side if _gr_pos.open else ("LONG" if _gr_pos is pos_long else "SHORT"), _gr_pos, cfg, mode=btf[:2] if False else self.mode)
                            if _gr_result is not None:
                                _gr_side = _gr_result["side"]
                                _gr_target_usd = _gr_result["target_usd"]
                                _gr_qty_new = _gr_target_usd / max(price, 1e-9)
                                if not _gr_pos.open:
                                    _gr_pos.open = True
                                    _gr_pos.side = _gr_side
                                    _gr_pos.entry_price = price
                                    _gr_pos.entry_ts = ts_i
                                    _gr_pos.mark_price = price
                                    _gr_pos.gain_pct = 0.0
                                    _gr_pos.max_gain_pct = 0.0
                                    _gr_pos.last_reduce_price = 0.0
                                    _gr_pos.last_close_price = 0.0
                                    _gr_pos.last_augment_ts = 0.0
                                    _gr_pos.ppl_fired = False
                                    _gr_pos.ppl_stop_level = 0.0
                                    _gr_pos.ppl_stop_upgraded = False
                                    _gr_pos.ppl_first_exit_price = 0.0
                                    _gr_pos.r1_fired = False
                                    _gr_pos.qty = _gr_qty_new
                                else:
                                    # AUGMENT: scale qty toward target
                                    _gr_pos.qty = max(_gr_pos.qty, _gr_qty_new)
                        except Exception:
                            pass

                # ── WT_3M_FORCE_OPEN (ez_manage.py:19969 / tradier_manage.py:1604) ─
                # Forces OPEN when position is ZERO and wt1_btf matches side direction.
                # Default OFF. When ON, fires before WT signal gate.
                if _wt_force_open_fn is not None and getattr(cfg, 'WT_3M_FORCE_OPEN_ENABLED', False):
                    for _wf_pos in (pos_long, pos_short):
                        if _wf_pos.open:
                            continue
                        _wf_side = "LONG" if _wf_pos is pos_long else "SHORT"
                        try:
                            _wf_result = _wt_force_open_fn(store, bar_idx, _wf_side, self.mode, cfg, _wf_pos)
                            if _wf_result is not None:
                                _wf_pos.open = True
                                _wf_pos.side = _wf_side
                                _wf_pos.entry_price = price
                                _wf_pos.entry_ts = ts_i
                                _wf_pos.mark_price = price
                                _wf_pos.gain_pct = 0.0
                                _wf_pos.max_gain_pct = 0.0
                                _wf_pos.last_reduce_price = 0.0
                                _wf_pos.last_close_price = 0.0
                                _wf_pos.last_augment_ts = 0.0
                                _wf_pos.ppl_fired = False
                                _wf_pos.ppl_stop_level = 0.0
                                _wf_pos.ppl_stop_upgraded = False
                                _wf_pos.ppl_first_exit_price = 0.0
                                _wf_pos.r1_fired = False
                                _wf_pos.qty = _wf_result["qty"]
                        except Exception:
                            pass

                # ── SENTIMENT_BOOST augment (tradier_manage.py:7408) ──────────
                # Check for augment on open positions before the entry gate.
                # This fires AUGMENT events on already-open positions when
                # market_sentiment indicates the position should be larger.
                # ADDITIVE: does not replace any existing logic, only augments qty.
                if _sentiment_boost_fn is not None:
                    try:
                        for _sb_pos in (pos_long, pos_short):
                            if not _sb_pos.open:
                                continue
                            _sb_result = _sentiment_boost_fn(store, bar_idx, _sb_pos, ts_i, cfg)
                            if _sb_result is not None:
                                # Record augment as a separate (small positive) return
                                _sb_pos.last_augment_ts = float(ts_i)
                                _sb_pos.augmented = True
                                # Augment is a sizing event — does not directly produce a PnL.
                                # We scale pos.qty by the qty multiplier to track position growth.
                                _sb_pos.qty = _sb_pos.qty * (1.0 + _sb_result.get("qty_mult", 0.0))
                    except Exception:
                        pass  # never let augment path break simulation

                # ── HEDGE_ENGINE: same-symbol hedge paths (crypto only) ─────────
                # Runs AFTER all exit blocks — hedge is a recovery action, not a primary exit.
                # Disabled by default in backtest (HEDGE_ENGINE_ENABLED=False).
                # Source: ez_positions_quick.py:5002 scan_and_hedge_losers +
                #         ez_manage.py:14339 OBLIGATORY_HEDGE.
                # 2026-05-10 strip defaults: 3m alone triggers, no deteriorating-gain gate.
                if _HEDGE_ENGINE_AVAILABLE and getattr(cfg, "HEDGE_ENGINE_ENABLED", False) and self.mode == "crypto":
                    for _hg_loser_side in ("LONG", "SHORT"):
                        _hg_loser = pos_states[sym][_hg_loser_side]
                        if not _hg_loser.open:
                            continue
                        _hg_hedge_side = "SHORT" if _hg_loser_side == "LONG" else "LONG"
                        _hg_hedge_pos = pos_states[sym][_hg_hedge_side]
                        try:
                            _hg_scan_result = _check_scan_hedge_losers(store, bar_idx, _hg_loser, all_pos_states=pos_states[sym], mode=self.mode, cfg=cfg)
                        except Exception:
                            _hg_scan_result = None
                        if _hg_scan_result is not None and not _hg_hedge_pos.open:
                            _hg_hedge_pos.open = True
                            _hg_hedge_pos.side = _hg_hedge_side
                            _hg_hedge_pos.entry_price = price
                            _hg_hedge_pos.entry_ts = float(ts_i)
                            _hg_hedge_pos.mark_price = price
                            _hg_hedge_pos.gain_pct = 0.0
                            _hg_hedge_pos.max_gain_pct = 0.0
                            _hg_hedge_pos.last_reduce_price = 0.0
                            _hg_hedge_pos.last_close_price = 0.0
                            _hg_hedge_pos.last_augment_ts = 0.0
                            _hg_hedge_pos.ppl_fired = False
                            _hg_hedge_pos.ppl_stop_level = 0.0
                            _hg_hedge_pos.ppl_stop_upgraded = False
                            _hg_hedge_pos.ppl_first_exit_price = 0.0
                            _hg_hedge_pos.r1_fired = False
                            _hg_hedge_pos.hedge_active = True
                            _hg_hedge_pos.reason = _hg_scan_result["reason"]
                            _hg_hedge_pos.qty = _hg_scan_result["size_qty"]
                            _hg_loser.hedge_active = True
                            continue
                        try:
                            _hg_obl_result = _check_obligatory_hedge(store, bar_idx, _hg_loser, mode=self.mode, cfg=cfg)
                        except Exception:
                            _hg_obl_result = None
                        if _hg_obl_result is not None and not _hg_hedge_pos.open:
                            _hg_hedge_pos.open = True
                            _hg_hedge_pos.side = _hg_hedge_side
                            _hg_hedge_pos.entry_price = price
                            _hg_hedge_pos.entry_ts = float(ts_i)
                            _hg_hedge_pos.mark_price = price
                            _hg_hedge_pos.gain_pct = 0.0
                            _hg_hedge_pos.max_gain_pct = 0.0
                            _hg_hedge_pos.last_reduce_price = 0.0
                            _hg_hedge_pos.last_close_price = 0.0
                            _hg_hedge_pos.last_augment_ts = 0.0
                            _hg_hedge_pos.ppl_fired = False
                            _hg_hedge_pos.ppl_stop_level = 0.0
                            _hg_hedge_pos.ppl_stop_upgraded = False
                            _hg_hedge_pos.ppl_first_exit_price = 0.0
                            _hg_hedge_pos.r1_fired = False
                            _hg_hedge_pos.hedge_active = True
                            _hg_hedge_pos.reason = _hg_obl_result["reason"]
                            _hg_hedge_pos.qty = _hg_obl_result["size_qty"]
                            _hg_loser.hedge_active = True
                            continue
                        _hg_needed = (_hg_scan_result is not None or _hg_obl_result is not None)
                        _hg_blocked = _hg_needed and _hg_hedge_pos.open
                        if _hg_blocked and bool(getattr(cfg, "HEDGE_FAILED_FALLBACK_CLOSE_ENABLED", True)):
                            try:
                                _hg_fail_result = _check_hedge_failed_fallback(store, bar_idx, _hg_loser, mode=self.mode, cfg=cfg, hedge_attempt_result=False)
                            except Exception:
                                _hg_fail_result = None
                            if _hg_fail_result is not None:
                                pnl = _hg_loser.gain_pct
                                returns_by_sym[sym].append(pnl)
                                all_returns.append(pnl)
                                running_gain += pnl
                                _hg_loser.open = False
                                _hg_loser.last_close_ts = float(ts_i)
                                _hg_loser.last_close_price = price
                                _hg_loser.hedge_active = False
                    for _hc_side in ("LONG", "SHORT"):
                        _hc_pos = pos_states[sym][_hc_side]
                        if not _hc_pos.open or not getattr(_hc_pos, "hedge_active", False):
                            continue
                        try:
                            _hc_result = _check_hedge_close(store, bar_idx, _hc_pos, mode=self.mode, cfg=cfg)
                        except Exception:
                            _hc_result = None
                        if _hc_result is not None:
                            pnl = _hc_pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            _hc_pos.open = False
                            _hc_pos.last_close_ts = float(ts_i)
                            _hc_pos.last_close_price = price
                            _hc_pos.hedge_active = False
                            _hc_orig_side = "LONG" if _hc_side == "SHORT" else "SHORT"
                            _hc_orig = pos_states[sym].get(_hc_orig_side)
                            if _hc_orig is not None:
                                _hc_orig.hedge_active = False

                # ── Entry signal gate (parity with backtest_v8_engine:2321) ──
                # Real engine: skip entry check if ts not in pre-computed gate set.
                _gate = entry_gate_per_sym.get(sym)
                if _gate is not None and ts_i not in _gate:
                    continue  # skip BOTH LONG and SHORT entry attempts on this bar

                # ── Entry logic ─────────────────────────────────
                for side in ("LONG", "SHORT"):
                    pos = pos_states[sym][side]
                    if pos.open:
                        continue

                    # ── Tradeability gate (parity with is_symbol_tradeable) ──
                    if cfg.TRADEABILITY_GATE_ENABLED and self.mode == "tradier":
                        _sym_u = sym.upper()
                        if side == "LONG" and _sym_u not in tradeable_long:
                            continue
                        if side == "SHORT" and _sym_u not in tradeable_short:
                            continue

                    # ── Per-sym entry cooldown (parity with AUGMENT_LOCK / DUP_GUARD) ──
                    # Real engine blocks new entries for N seconds after last close on this pk.
                    # Default 900s (15 min) per CLAUDE.md AUGMENT_LOCK.
                    last_close_ts = pos.last_close_ts if hasattr(pos, "last_close_ts") else 0
                    if cfg.ENTRY_COOLDOWN_SEC > 0 and last_close_ts > 0:
                        if (ts_i - last_close_ts) < cfg.ENTRY_COOLDOWN_SEC:
                            continue

                    # ── PRICE_CROSS_BACK_REENTRY (tradier_manage.py:1880) ───────────
                    # Fires when last close was recent and price is back near exit level.
                    # Default OFF. Fires BEFORE DC_BREAK and WT gate — mirrors live priority.
                    if _price_cross_back_fn is not None and getattr(cfg, 'PRICE_CROSS_BACK_REENTRY_ENABLED', False):
                        try:
                            _pcb_result = _price_cross_back_fn(store, bar_idx, sym, side, pos, cfg, mode=self.mode)
                            if _pcb_result is not None:
                                pos.open = True
                                pos.side = side
                                pos.entry_price = price
                                pos.entry_ts = ts_i
                                pos.mark_price = price
                                pos.gain_pct = 0.0
                                pos.max_gain_pct = 0.0
                                pos.last_reduce_price = 0.0
                                pos.last_close_price = 0.0
                                pos.last_augment_ts = 0.0
                                pos.ppl_fired = False
                                pos.ppl_stop_level = 0.0
                                pos.ppl_stop_upgraded = False
                                pos.ppl_first_exit_price = 0.0
                                pos.r1_fired = False
                                pos.qty = float(_pcb_result["qty"])
                                continue  # skip DC_BREAK and WT path
                        except Exception:
                            pass

                    # ── SATOSHIT additive open (ez_satoshit.evaluate_satoshit_entry) ─
                    # Source: ez_manage.py:23266 eval_funcs pipeline.
                    # When SATOSHIT_ENABLED=True AND position is ZERO: open via 5-vote system.
                    # Fires BEFORE DC_BREAK and WT path (matches eval_funcs priority).
                    # Default OFF (SATOSHIT_ENABLED=False). SATOSHIT_ENTRY_FILTER (crypto gate)
                    # is handled separately inside the WT path — this is the independent open.
                    _sat_enabled = (
                        (self.mode == "crypto" and getattr(cfg, "SATOSHIT_ENABLED", False))
                        or (self.mode == "tradier" and getattr(cfg, "SATOSHIT_ENABLED_TRADIER", False))
                    )
                    if _sat_enabled and _satoshit_fn is not None:
                        try:
                            _sat_sig = _satoshit_fn(store, bar_idx, side, self.mode, cfg)
                            if _sat_sig is not None:
                                pos.open = True
                                pos.side = side
                                pos.entry_price = price
                                pos.entry_ts = ts_i
                                pos.mark_price = price
                                pos.gain_pct = 0.0
                                pos.max_gain_pct = 0.0
                                pos.last_reduce_price = 0.0
                                pos.last_close_price = 0.0
                                pos.last_augment_ts = 0.0
                                pos.ppl_fired = False
                                pos.ppl_stop_level = 0.0
                                pos.ppl_stop_upgraded = False
                                pos.ppl_first_exit_price = 0.0
                                pos.r1_fired = False
                                pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                                continue  # skip FH_MOMENTUM, DC_BREAK, WT path
                        except Exception:
                            pass

                    # ── FH_MOMENTUM (tradier_manage.py:5876 / ez_manage.py:24001) ──────
                    # First-Hour Momentum entry: fires BEFORE DC_BREAK and WT gate.
                    # Detects price move from daily open within first-hour UTC window.
                    # MFI and DC position gates confirm direction.
                    # Default: tradier=OFF, crypto=ON per config.py.
                    if _fh_momentum_fn is not None:
                        _fhm_enabled = (
                            (self.mode == "tradier" and getattr(cfg, "FH_MOMENTUM_ENABLED", False))
                            or (self.mode == "crypto" and getattr(cfg, "CRYPTO_FH_MOMENTUM_ENABLED", True))
                        )
                        if _fhm_enabled:
                            try:
                                _fhm_sig = _fh_momentum_fn(store, bar_idx, side, self.mode, cfg)
                                if _fhm_sig is not None:
                                    pos.open = True
                                    pos.side = side
                                    pos.entry_price = price
                                    pos.entry_ts = ts_i
                                    pos.mark_price = price
                                    pos.gain_pct = 0.0
                                    pos.max_gain_pct = 0.0
                                    pos.last_reduce_price = 0.0
                                    pos.last_close_price = 0.0
                                    pos.last_augment_ts = 0.0
                                    pos.ppl_fired = False
                                    pos.ppl_stop_level = 0.0
                                    pos.ppl_stop_upgraded = False
                                    pos.ppl_first_exit_price = 0.0
                                    pos.r1_fired = False
                                    pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                                    continue  # skip DC_BREAK and WT path
                            except Exception:
                                pass

                    # ── DC_BREAK_HIGH / DC_BREAK_LOW path (tradier_manage._check_dc_break) ──
                    # Independent of WT gate — fires when DC channel is broken with
                    # expansion confirmation.  Only active when TRADIER_DC_DAYTRADE_ENABLED.
                    # If this fires we skip the WT filter chain and open directly.
                    _dc_break_sig = None
                    if _VEC_PATHS_AVAILABLE and self.mode == "tradier":
                        _dc_break_sig = _check_dc_break_entry(store, bar_idx, cfg)
                        if _dc_break_sig is not None and _dc_break_sig["side"] != side:
                            _dc_break_sig = None  # wrong side for this loop iteration

                    if _dc_break_sig is not None:
                        # DC_BREAK fired for this side — open position immediately
                        _dc_size_mult = _dc_break_sig.get("size_mult", 1.0)
                        pos.open = True
                        pos.side = side
                        pos.entry_price = price
                        pos.entry_ts = ts_i
                        pos.mark_price = price
                        pos.gain_pct = 0.0
                        pos.max_gain_pct = 0.0
                        pos.last_reduce_price = 0.0
                        pos.ppl_fired = False
                        pos.ppl_stop_level = 0.0
                        pos.ppl_stop_upgraded = False
                        pos.ppl_first_exit_price = 0.0
                        pos.r1_fired = False
                        _base_sz = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                        pos.qty = _base_sz * _dc_size_mult
                        continue  # skip WT path for this side

                    # ── REENTRY paths (profit-pullback, trend-resume, breakout-reclaim, probe) ──
                    # Independent of WT gate — fires when position is closed and
                    # reentry conditions from the live system match.
                    _reentry_sig = None
                    if _VEC_PATHS_AVAILABLE and cfg.REENTRY_ENABLED:
                        _reentry_sig = _check_reentry_entry(store, bar_idx, sym, pos, side, cfg)

                    if _reentry_sig is not None:
                        _re_size_mult = _reentry_sig.get("size_mult", 1.0)
                        pos.open = True
                        pos.side = side
                        pos.entry_price = price
                        pos.entry_ts = ts_i
                        pos.mark_price = price
                        pos.gain_pct = 0.0
                        pos.max_gain_pct = 0.0
                        pos.last_reduce_price = 0.0
                        pos.ppl_fired = False
                        pos.ppl_stop_level = 0.0
                        pos.ppl_stop_upgraded = False
                        pos.ppl_first_exit_price = 0.0
                        pos.r1_fired = False
                        _base_sz = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                        pos.qty = _base_sz * _re_size_mult
                        continue  # skip WT path for this side

                    # ── SCALP_V2 entry (htf_breakout_scalper.py) ─────────────────
                    # Fires when SCALP_MODE=True and HTF DC breakout condition met.
                    # All variants are tried. Only fires on EMPTY position (no augment path).
                    if _SCALP_V2_AVAILABLE and cfg.SCALP_MODE:
                        try:
                            _v2_sig = _check_scalp_v2_entry(store, bar_idx, side, cfg)
                        except Exception:
                            _v2_sig = None
                        if _v2_sig is not None:
                            pos.open = True
                            pos.side = side
                            pos.entry_price = price
                            pos.entry_ts = ts_i
                            pos.mark_price = price
                            pos.gain_pct = 0.0
                            pos.max_gain_pct = 0.0
                            pos.prev_gain = 0.0
                            pos.last_reduce_price = 0.0
                            pos.last_close_price = 0.0
                            pos.last_augment_ts = 0.0
                            pos.ppl_fired = False
                            pos.ppl_stop_level = 0.0
                            pos.ppl_stop_upgraded = False
                            pos.ppl_first_exit_price = 0.0
                            pos.r1_fired = False
                            pos.reason = _v2_sig["reason"]
                            pos.open_bar = bar_idx
                            pos.micro_scalp_exit_price = 0.0
                            pos.micro_scalp_orig_side = ""
                            pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                            continue  # skip WT path

                    # ── SCALP_V3 entry (scalp_v3_live.py) ─────────────────────────
                    # Fires when SCALP_V3_ENABLED=True. Multiple entry paths per
                    # scalp_v3_live.py (TREND, BAR_BREAK, PULLBACK, DC_BREAK, WT_CROSS, STOCH_BOUNCE, STDEV).
                    if _SCALP_V3_AVAILABLE and cfg.SCALP_V3_ENABLED:
                        try:
                            _v3_sig = _check_scalp_v3_entry(store, bar_idx, side, cfg)
                        except Exception:
                            _v3_sig = None
                        if _v3_sig is not None and _v3_sig["side"] == side:
                            pos.open = True
                            pos.side = side
                            pos.entry_price = price
                            pos.entry_ts = ts_i
                            pos.mark_price = price
                            pos.gain_pct = 0.0
                            pos.max_gain_pct = 0.0
                            pos.prev_gain = 0.0
                            pos.last_reduce_price = 0.0
                            pos.last_close_price = 0.0
                            pos.last_augment_ts = 0.0
                            pos.ppl_fired = False
                            pos.ppl_stop_level = 0.0
                            pos.ppl_stop_upgraded = False
                            pos.ppl_first_exit_price = 0.0
                            pos.r1_fired = False
                            pos.reason = _v3_sig["reason"]
                            pos.open_bar = bar_idx
                            pos.micro_scalp_exit_price = 0.0
                            pos.micro_scalp_orig_side = ""
                            pos.qty = cfg.SCALP_V3_POSITION_CAP_USD / max(price, 1e-9)
                            continue  # skip WT path

                    # ── GOLDEN_RULE entry filter ─────────────
                    if cfg.GOLDEN_RULE_ENABLED:
                        gr_pass = self._check_golden_rule(store, bar_idx, side, cfg)
                        if not gr_pass:
                            continue

                    # ── GR HTF gate ─────────────────────────
                    if cfg.GR_HTF_GATE_ENABLED:
                        bull_align = int(store.f("wt_bull_alignment", bar_idx, 0))
                        bear_align = int(store.f("wt_bear_alignment", bar_idx, 0))
                        if side == "LONG" and bull_align < cfg.GR_HTF_REQUIRE_BULL:
                            continue
                        if side == "SHORT" and bear_align < cfg.GR_HTF_REQUIRE_BEAR:
                            continue

                    # ── WT cross entry signal ────────────────
                    cross = store.s(f"wt_cross_{btf}", bar_idx)
                    wt_entry = (side == "LONG" and cross == "BULL") or \
                               (side == "SHORT" and cross == "BEAR")
                    if not wt_entry:
                        continue

                    # ── WT_DC_ENTRY score gate (parity with tradier_manage:1969) ──
                    # Replicates wt_dc_entry_scorer.score_entry_multitf exactly.
                    # 26/27 real-engine trades came through this path with score>=55.
                    # NPZ stores wt_cross_1h as int8 (-1/0/+1); bull/bear flags as binary.
                    if cfg.WT_DC_ENTRY_GATE_ENABLED:
                        wt1_D = store.f("wt1_D", bar_idx, 0.0)
                        wt2_D = store.f("wt2_D", bar_idx, 0.0)
                        wt1_4h = store.f("wt1_4h", bar_idx, 0.0)
                        wt2_4h = store.f("wt2_4h", bar_idx, 0.0)
                        # NPZ wt_cross_1h is int8 {-1, 0, 1} - transient cross event flag.
                        # Real engine's trade reasons confirm only firing on 1h cross bars.
                        wt_cross_1h_v = store.f("wt_cross_1h", bar_idx, 0)
                        bull_1h = wt_cross_1h_v > 0
                        bear_1h = wt_cross_1h_v < 0
                        dc_1h = store.f("dc_position_1h", bar_idx, 0.5)
                        k_5m_for_score = store.f("stoch_k_5m" if self.mode == "tradier" else "stoch_k_3m", bar_idx, 50.0)
                        wtdc_score = 0.0
                        if side == "LONG":
                            if wt1_D > wt2_D: wtdc_score += 25
                            if wt1_4h > wt2_4h: wtdc_score += 25
                            if bull_1h: wtdc_score += 30
                            if dc_1h < 0.50: wtdc_score += 10
                            if k_5m_for_score < 40: wtdc_score += 10
                        else:
                            if wt1_D < wt2_D: wtdc_score += 25
                            if wt1_4h < wt2_4h: wtdc_score += 25
                            if bear_1h: wtdc_score += 30
                            if dc_1h > 0.50: wtdc_score += 10
                            if k_5m_for_score > 60: wtdc_score += 10
                        if wtdc_score < cfg.WT_DC_ENTRY_THRESHOLD:
                            continue

                    # ── LTF stoch alignment gate (parity with ez_manage.check_entry_alignment) ──
                    # Real engine requires 3/3 LTF stoch crossover. 1m field not in NPZ,
                    # so _sf default 50 → k_1m==d_1m → 1m alignment always False → effective 2/3 max.
                    # Real engine therefore demands k_3m>d_3m AND k_15m>d_15m (both must be true).
                    if cfg.LTF_ALIGN_GATE_ENABLED:
                        k3 = store.f("stoch_k_3m", bar_idx, 50.0)
                        d3 = store.f("stoch_d_3m", bar_idx, 50.0)
                        k15 = store.f("stoch_k_15m", bar_idx, 50.0)
                        d15 = store.f("stoch_d_15m", bar_idx, 50.0)
                        if side == "LONG":
                            if not (k3 > d3 and k15 > d15):
                                continue
                        else:
                            if not (k3 < d3 and k15 < d15):
                                continue
                        # ── K3M_CAP / K3M_FLOOR (real engine BC_8 / BC_9) ──
                        k3m_cap = cfg.K3M_CAP
                        k3m_floor = cfg.K3M_FLOOR
                        if side == "LONG":
                            if k3 >= k3m_cap:
                                continue
                            if k3 >= (100 - k3m_floor):
                                continue
                        else:
                            if k3 <= (100 - k3m_cap):
                                continue
                            if k3 <= k3m_floor:
                                continue

                    # ── HTF stoch alignment + D mandatory ────
                    if cfg.LTF_ALIGN_GATE_ENABLED:
                        k1h = store.f("stoch_k_1h", bar_idx, 50.0)
                        d1h = store.f("stoch_d_1h", bar_idx, 50.0)
                        k4h = store.f("stoch_k_4h", bar_idx, 50.0)
                        d4h = store.f("stoch_d_4h", bar_idx, 50.0)
                        kD = store.f("stoch_k_D", bar_idx, 50.0)
                        dD = store.f("stoch_d_D", bar_idx, 50.0)
                        ha_D = store.s("ha_color_D", bar_idx) or store.s("ha_D", bar_idx)
                        if side == "LONG":
                            d_aligned = (ha_D == "green") or (kD > dD)
                            htf_stoch = int(k1h > d1h) + int(k4h > d4h) + int(d_aligned)
                        else:
                            d_aligned = (ha_D == "red") or (kD < dD)
                            htf_stoch = int(k1h < d1h) + int(k4h < d4h) + int(d_aligned)
                        if htf_stoch < 2:
                            continue
                        if not d_aligned:
                            continue

                    # ── HTF alignment gate (vec engine's original WT-based gate) ──
                    htf_req = cfg.HTF_ALIGN_REQUIRED if self.mode == "crypto" else cfg.HTF_ALIGN_REQUIRED_TRADIER
                    htf_count = 0
                    for htf in cfg.HTF_TFS:
                        wt_bull = store.b(f"wt_bullish_{htf}", bar_idx)
                        wt_cross_h = store.s(f"wt_cross_{htf}", bar_idx)
                        if side == "LONG":
                            if wt_bull or wt_cross_h == "BULL":
                                htf_count += 1
                        else:
                            if not wt_bull or wt_cross_h == "BEAR":
                                htf_count += 1
                    if htf_count < htf_req:
                        continue

                    # ── Stoch gate (tradier) ─────────────────
                    if self.mode == "tradier":
                        stf = "5m"
                        k = store.f(f"stoch_k_{stf}", bar_idx, 50)
                        if side == "LONG" and k > cfg.TRADIER_STOCH_ENTRY_LONG_TRADIER:
                            continue
                        if side == "SHORT" and k < cfg.TRADIER_STOCH_ENTRY_SHORT_TRADIER:
                            continue

                    # ── Stoch gate (crypto) ──────────────────
                    if self.mode == "crypto":
                        k = store.f(f"stoch_k_{btf}", bar_idx, 50)
                        if side == "LONG" and k > cfg.COMBINED_STOCH_GATE:
                            continue
                        if side == "SHORT" and k < (100.0 - cfg.COMBINED_STOCH_GATE):
                            continue

                    # ── STDEV_BREAKOUT / BOUNCE filter ───────
                    if cfg.STDEV_BREAKOUT_ENABLED or cfg.STDEV_BOUNCE_ENABLED:
                        stdev_ok = self._check_stdev_filter(store, bar_idx, side, cfg)
                        if not stdev_ok:
                            continue

                    # ── SATOSHIT entry filter (crypto) ───────
                    # Source: ez_manage.py:10116/10397 (SATOSHIT_ENTRY_FILTER gate on WT path).
                    # Default True for crypto. When _satoshit_fn (vec_paths/satoshit.py) is
                    # available, uses the full 5-vote system. Falls back to _check_satoshit().
                    if self.mode == "crypto" and cfg.SATOSHIT_ENTRY_FILTER:
                        if _satoshit_fn is not None:
                            try:
                                _sat_filter_sig = _satoshit_fn(store, bar_idx, side, self.mode, cfg)
                                sat_ok = (_sat_filter_sig is not None)
                            except Exception:
                                sat_ok = self._check_satoshit(store, bar_idx, side, cfg)
                        else:
                            sat_ok = self._check_satoshit(store, bar_idx, side, cfg)
                        if not sat_ok:
                            continue

                    # ── DELTA_ENTRY: full state-machine path (tradier_manage.py:1917) ──
                    # When DELTA_ENGINE_ENABLED AND DELTA_ENTRY_ENABLED: check_delta_entry
                    # fires BEFORE the WT scorer (it's a separate, independent entry path).
                    # In the vec engine we wire it AFTER the WT gate as an ADDITIVE check
                    # (conservative: only fire when WT also signals, to avoid over-trading).
                    # When only DELTA_ENTRY_ENABLED (not DELTA_ENGINE_ENABLED): use velocity
                    # proxy (existing code below).
                    if cfg.DELTA_ENGINE_ENABLED and cfg.DELTA_ENTRY_ENABLED and _delta_entry_fn is not None:
                        try:
                            _de_result = _delta_entry_fn(store, bar_idx, side, self.mode, cfg)
                            if _de_result is None:
                                continue  # delta engine didn't confirm — skip this entry
                        except Exception:
                            pass  # fail-open: if delta_engine throws, allow entry via WT path

                    elif cfg.DELTA_ENTRY_ENABLED:
                        # Velocity-proxy filter (original approximation)
                        tf_d = cfg.DELTA_ENTRY_TF
                        vel = store.f(f"wt_velocity_{tf_d}", bar_idx)
                        vel_ok = (side == "LONG" and vel >= cfg.DELTA_ENTRY_VEL_MIN) or \
                                 (side == "SHORT" and vel <= -cfg.DELTA_ENTRY_VEL_MIN)
                        if not vel_ok:
                            continue

                    # ── Linearity/LR filter ──────────────────
                    if (side == "LONG" and cfg.LINEARITY_LR_LONG_ENABLED) or \
                       (side == "SHORT" and cfg.LINEARITY_LR_SHORT_ENABLED):
                        lr_ok = self._check_lr_filter(store, bar_idx, side, cfg)
                        if not lr_ok:
                            continue

                    # ── MOM3 / MOM5 additive score boost (ez_manage.py:18765) ──
                    # Source: BACKTEST_CHANGE_4/5 in final_order_quantity — each adds +22.
                    # In vec: ADDITIVE (never gates by default). When MOM3_GATE_REQUIRED=True
                    # it acts as a hard gate (block if neither MOM3 nor MOM5 fires).
                    _mom3_gate = getattr(cfg, 'MOM3_GATE_REQUIRED', False)
                    _mom3_boost_result = None
                    if _mom3_fn is not None and (cfg.MOM3_ENTRY_ENABLED or cfg.MOM5_ENTRY_ENABLED):
                        try:
                            _mom3_boost_result = _mom3_fn(store, bar_idx, side, self.mode, cfg)
                        except Exception:
                            _mom3_boost_result = None
                    if _mom3_gate and _mom3_boost_result is None:
                        if cfg.MOM3_ENTRY_ENABLED or cfg.MOM5_ENTRY_ENABLED:
                            continue  # gate mode: block if no MOM3/5 signal

                    # ── Entry accepted — open position (WT path) ───────
                    pos.open = True
                    pos.side = side
                    pos.entry_price = price
                    pos.entry_ts = ts_i
                    pos.mark_price = price
                    pos.gain_pct = 0.0
                    pos.max_gain_pct = 0.0
                    pos.last_reduce_price = 0.0
                    pos.last_augment_ts = 0.0
                    pos.ppl_fired = False
                    pos.ppl_stop_level = 0.0
                    pos.ppl_stop_upgraded = False
                    pos.ppl_first_exit_price = 0.0
                    pos.r1_fired = False
                    _base_sz = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                    # ── RATIO_BOOST / RATIO_CUT sizing (tradier_manage.py:9337) ──
                    _ratio_mult = 1.0
                    if _ratio_size_fn is not None and cfg.RATIO_BOOST_ENABLED:
                        try:
                            _ratio_mult = _ratio_size_fn(portfolio_state, sym, side, cfg)
                        except Exception:
                            pass  # fail-safe: ratio error → 1.0
                    pos.qty = _base_sz * _ratio_mult
                    # Track in portfolio_state for L/S ratio computation
                    _pk = f"{sym}_{side}"
                    portfolio_state[_pk] = {"qty": pos.qty, "price": price, "side": side}

            # ── Refresh portfolio_state for RATIO_BOOST (end of each bar) ──
            # portfolio_state tracks all open positions for L/S ratio computation.
            # We rebuild it each bar to capture all close/open events this bar.
            if cfg.RATIO_BOOST_ENABLED:
                portfolio_state.clear()
                for _ps_sym, _ps_pss in pos_states.items():
                    _ps_st = stores.get(_ps_sym)
                    for _ps_side, _ps_pos in _ps_pss.items():
                        if _ps_pos.open and _ps_st is not None:
                            _ps_bi = store_start_idx.get(_ps_sym, 0) + idx
                            if _ps_bi < _ps_st.n_bars:
                                _ps_price = _ps_st.price(_ps_bi)
                            else:
                                _ps_price = _ps_pos.entry_price
                            _ps_pk = f"{_ps_sym}_{_ps_side}"
                            portfolio_state[_ps_pk] = {"qty": _ps_pos.qty, "price": _ps_price, "side": _ps_side}

            # ── Update DD state (end of each bar) ────────────
            # Include open position unrealized PnL in equity estimate
            closed_gain = sum(all_returns)
            open_gain = 0.0
            for sym, pss in pos_states.items():
                for pos in pss.values():
                    if pos.open:
                        open_gain += pos.gain_pct
            equity_pct = closed_gain + open_gain
            if equity_pct > dd_state["peak"]:
                dd_state["peak"] = equity_pct
            dd = equity_pct - dd_state["peak"]
            dd_state["dd_pct"] = dd
            if dd < -max_dd:
                max_dd = -dd

        # ── NOLIES rule 2: mark open positions to market ────
        for sym, store in stores.items():
            last_idx = store_start_idx[sym] + len(all_ts) - 1
            if last_idx >= store.n_bars:
                last_idx = store.n_bars - 1
            for pos in pos_states[sym].values():
                if pos.open and pos.entry_price > 0:
                    final_price = store.price(last_idx)
                    if final_price > 0:
                        if pos.side == "LONG":
                            mtm_pnl = (final_price - pos.entry_price) / pos.entry_price * 100.0
                        else:
                            mtm_pnl = (pos.entry_price - final_price) / pos.entry_price * 100.0
                        returns_by_sym[sym].append(mtm_pnl)
                        all_returns.append(mtm_pnl)
                        running_gain += mtm_pnl

        # ── Compute canonical metrics ─────────────────────────
        n_syms = len([s for s, rets in returns_by_sym.items() if len(rets) >= 1])
        total_trades = sum(len(rets) for rets in returns_by_sym.values())

        if len(all_returns) < 2:
            return self._empty_result(f"insufficient trades: {total_trades}")

        ps = metrics_guard.pool_sharpe(all_returns)
        ss = metrics_guard.sym_sharpe_from_groups(returns_by_sym)

        acc_gain = sum(all_returns)
        years = max(0.01, sim_years)
        avg_gain_trade = acc_gain / max(1, total_trades)
        gain_per_yr = acc_gain / years
        gain_sym_yr = (acc_gain / max(1, n_syms)) / years
        wins = sum(1 for r in all_returns if r > 0)
        losses = sum(1 for r in all_returns if r <= 0)

        # CLAUDE.md sample floor check
        min_syms = metrics_guard.MIN_SYMS_CRYPTO if self.mode == "crypto" else metrics_guard.MIN_SYMS_STOCKS
        min_trades_per_sym = 30
        passing_syms = sum(1 for rets in returns_by_sym.values() if len(rets) >= min_trades_per_sym)
        is_publishable = (passing_syms >= min_syms and years >= metrics_guard.MIN_YEARS)

        result = {
            "pool_sharpe": ps,
            "sym_sharpe": ss,
            "avg_gain_trade": avg_gain_trade,
            "gain_per_yr": gain_per_yr,
            "gain_sym_yr": gain_sym_yr,
            "trades": total_trades,
            "wins": wins,
            "losses": losses,
            "max_dd_pct": max_dd,
            "n_syms": n_syms,
            "years": years,
            "acc_gain_pct": acc_gain,
            "n_syms_passing_floor": passing_syms,
            "publishable": is_publishable,
            "verdict": metrics_guard.tier_name(ps) + ("" if is_publishable else " [DIAGNOSTIC]"),
            "note": "vec_engine_v1 approximation — validate against backtest_v8_engine before promotion",
        }

        # Validate through metrics_guard (will raise on banned patterns)
        try:
            metrics_guard.validate_and_format_sharpe(
                ps,
                label="pool_sharpe",
                n_syms=n_syms,
                years=years,
                trades=total_trades,
                mode=self.mode,
            )
        except metrics_guard.FakeMetricRefused as e:
            result["verdict"] = f"METRICS_GUARD_REFUSED: {e}"
            result["pool_sharpe"] = 0.0

        return result

    # ────────────────────────────────────────────────────────
    # Helper: GOLDEN_RULE entry filter
    # ────────────────────────────────────────────────────────
    def _check_golden_rule(self, store: _NPZStore, idx: int, side: str, cfg: VecConfig) -> bool:
        """GOLDEN_RULE: price at/above DC/BB level for LONG (or below for SHORT).

        The real engine fires GOLDEN_RULE as a SIZING multiplier (bigger lots when
        price breaks out). We implement it as a gate+sizing: gate = price must have
        crossed into a level (DC crossover or BB crossover on the configured TFs).
        OR_LOGIC=True: any configured TF crossing → pass.
        OR_LOGIC=False: ALL configured TFs must be in breakout zone.
        """
        if not cfg.GOLDEN_RULE_ENABLED:
            return True

        btf = self._base_tf()
        levels = [
            ("15m", cfg.GOLDEN_RULE_DC_15M_ENABLED, "dc", cfg.GOLDEN_RULE_BB_15M_ENABLED, "bb"),
            ("1h", cfg.GOLDEN_RULE_DC_1H_ENABLED, "dc", cfg.GOLDEN_RULE_BB_1H_ENABLED, "bb"),
            ("4h", cfg.GOLDEN_RULE_DC_4H_ENABLED, "dc", cfg.GOLDEN_RULE_BB_4H_ENABLED, "bb"),
            ("D", cfg.GOLDEN_RULE_DC_D_ENABLED, "dc", cfg.GOLDEN_RULE_BB_D_ENABLED, "bb"),
        ]

        hits = 0
        total_checks = 0
        for tf, dc_en, _, bb_en, __ in levels:
            if dc_en:
                total_checks += 1
                dc_cross = store.s(f"dc_high_crossover_{tf}", idx)
                dc_cross_b = store.b(f"dc_high_crossover_{tf}", idx)
                if side == "LONG":
                    if dc_cross == "BULL" or dc_cross_b or store.f(f"dc_position_{tf}", idx, 0.5) > 0.7:
                        hits += 1
                else:
                    dc_cross_low = store.s(f"dc_low_crossunder_{tf}", idx)
                    if dc_cross_low == "BEAR" or store.f(f"dc_position_{tf}", idx, 0.5) < 0.3:
                        hits += 1
            if bb_en:
                total_checks += 1
                bb_pctb = store.f(f"bb_pct_b_{tf}", idx, 0.5)
                if side == "LONG" and bb_pctb >= 0.9:
                    hits += 1
                elif side == "SHORT" and bb_pctb <= 0.1:
                    hits += 1

        if total_checks == 0:
            return True
        if cfg.GOLDEN_RULE_OR_LOGIC:
            return hits >= 1
        else:
            return hits >= total_checks

    def _check_stdev_filter(self, store: _NPZStore, idx: int, side: str, cfg: VecConfig) -> bool:
        """STDEV_BREAKOUT and STDEV_BOUNCE entry filters."""
        if cfg.STDEV_BREAKOUT_ENABLED:
            for htf in cfg.STDEV_BREAKOUT_HTF_LIST:
                pctb = store.f(f"bb_pct_b_{htf}", idx, 0.5)
                rvol = store.f(f"relative_volume_{htf}", idx, 1.0)
                if side == "LONG" and pctb >= cfg.STDEV_BREAKOUT_PCTB_LONG and rvol >= cfg.STDEV_BREAKOUT_RVOL_MIN:
                    return True
                if side == "SHORT" and pctb <= cfg.STDEV_BREAKOUT_PCTB_SHORT and rvol >= cfg.STDEV_BREAKOUT_RVOL_MIN:
                    return True
        if cfg.STDEV_BOUNCE_ENABLED:
            for htf in cfg.STDEV_BOUNCE_HTF_LIST:
                pctb = store.f(f"bb_pct_b_{htf}", idx, 0.5)
                rvol = store.f(f"relative_volume_{htf}", idx, 1.0)
                if side == "LONG" and pctb <= cfg.STDEV_BOUNCE_PCTB_LONG and rvol >= cfg.STDEV_BOUNCE_RVOL_MIN:
                    return True
                if side == "SHORT" and pctb >= cfg.STDEV_BOUNCE_PCTB_SHORT and rvol >= cfg.STDEV_BOUNCE_RVOL_MIN:
                    return True
        # If either is enabled but no condition met, reject
        if cfg.STDEV_BREAKOUT_ENABLED or cfg.STDEV_BOUNCE_ENABLED:
            return False
        return True

    def _check_satoshit(self, store: _NPZStore, idx: int, side: str, cfg: VecConfig) -> bool:
        """SATOSHIT_ENTRY_FILTER: multi-indicator confluence check.

        Real satoshit_entry_signal checks rsi/bb/ha/stoch/mfi + HTF mfi/rvol.
        Approximation: require WT cross + stoch cross + MFI confirmation.
        """
        btf = self._base_tf()
        # Stoch crossover
        stoch_cross = store.b(f"stoch_crossover_{btf}", idx) if side == "LONG" else store.b(f"stoch_crossunder_{btf}", idx)
        if not stoch_cross:
            return False
        # MFI confirmation
        mfi = store.f(f"mfi_{btf}", idx, 50.0)
        if side == "LONG" and mfi < 20:
            return True  # oversold + stoch cross = satoshit-like
        if side == "SHORT" and mfi > 80:
            return True
        # HA confirmation (avoid opposing candles)
        ha = store.s(f"ha_{btf}", idx)
        if side == "LONG" and ha == "red":
            return False
        if side == "SHORT" and ha == "green":
            return False
        return stoch_cross

    def _check_lr_filter(self, store: _NPZStore, idx: int, side: str, cfg: VecConfig) -> bool:
        """LINEARITY_LR_LONG/SHORT_ENABLED: require all configured TFs' LR slopes same-sign."""
        agree = 0
        total = 0
        for tf in cfg.LINEARITY_LR_TFS:
            slope_key = f"lr_slope_{tf}"
            if slope_key not in store.arrays:
                continue
            slope = store.f(slope_key, idx, 0.0)
            total += 1
            if side == "LONG" and slope > 0:
                agree += 1
            elif side == "SHORT" and slope < 0:
                agree += 1
        if total == 0:
            return True
        if cfg.LINEARITY_LR_REQUIRE_ALL:
            if agree < total:
                return False
        else:
            if agree == 0:
                return False
        if cfg.LINEARITY_LR_LIN4H_MIN > 0:
            lin4h = store.f("linearity_4h", idx, 0.0)
            if lin4h < cfg.LINEARITY_LR_LIN4H_MIN:
                return False
        return True

    def _compute_sizing(
        self, store: _NPZStore, idx: int, side: str,
        cfg: VecConfig, dd_state: Dict, running_gain: float
    ) -> float:
        """Compute position size scalar (relative, 1.0 = standard size)."""
        sz = 1.0

        # VOL_TARGET sizing
        if cfg.VOL_TARGET_ENABLED:
            rv = store.f(cfg.VOL_TARGET_FIELD, idx, 0.0)
            if rv > 0:
                s = cfg.VOL_TARGET_PCT / rv
                sz *= max(cfg.VOL_TARGET_LOW_CAP, min(cfg.VOL_TARGET_HIGH_CAP, s))

        # DD_KELLY sizing
        if cfg.DD_KELLY_ENABLED:
            dd = dd_state.get("dd_pct", 0.0)
            if dd <= -20.0:
                sz *= cfg.DD_KELLY_TIER3_PCT
            elif dd <= -15.0:
                sz *= cfg.DD_KELLY_TIER2_PCT
            elif dd <= -10.0:
                sz *= cfg.DD_KELLY_TIER1_PCT

        # GOLDEN_RULE sizing multiplier (find max applicable level)
        if cfg.GOLDEN_RULE_ENABLED:
            gr_mult = self._golden_rule_mult(store, idx, side, cfg)
            sz *= gr_mult

        return sz

    def _golden_rule_mult(self, store: _NPZStore, idx: int, side: str, cfg: VecConfig) -> float:
        """Return GOLDEN_RULE sizing multiplier based on highest-TF breakout."""
        best_mult = 1.0
        levels = [
            ("15m", cfg.GOLDEN_RULE_DC_15M_ENABLED, cfg.GOLDEN_RULE_BB_15M_ENABLED, cfg.GOLDEN_RULE_MULT_15M),
            ("1h", cfg.GOLDEN_RULE_DC_1H_ENABLED, cfg.GOLDEN_RULE_BB_1H_ENABLED, cfg.GOLDEN_RULE_MULT_1H),
            ("4h", cfg.GOLDEN_RULE_DC_4H_ENABLED, cfg.GOLDEN_RULE_BB_4H_ENABLED, cfg.GOLDEN_RULE_MULT_4H),
            ("D", cfg.GOLDEN_RULE_DC_D_ENABLED, cfg.GOLDEN_RULE_BB_D_ENABLED, cfg.GOLDEN_RULE_MULT_D),
        ]
        for tf, dc_en, bb_en, mult in levels:
            if dc_en:
                dc_pos = store.f(f"dc_position_{tf}", idx, 0.5)
                if (side == "LONG" and dc_pos > 0.7) or (side == "SHORT" and dc_pos < 0.3):
                    best_mult = max(best_mult, mult)
            if bb_en:
                bb_pctb = store.f(f"bb_pct_b_{tf}", idx, 0.5)
                if (side == "LONG" and bb_pctb >= 0.8) or (side == "SHORT" and bb_pctb <= 0.2):
                    best_mult = max(best_mult, mult)
        return best_mult

    def _empty_result(self, note: str) -> Dict[str, Any]:
        return {
            "pool_sharpe": 0.0,
            "sym_sharpe": 0.0,
            "avg_gain_trade": 0.0,
            "gain_per_yr": 0.0,
            "gain_sym_yr": 0.0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "max_dd_pct": 0.0,
            "n_syms": 0,
            "years": 0.0,
            "acc_gain_pct": 0.0,
            "n_syms_passing_floor": 0,
            "publishable": False,
            "verdict": "NO_DATA",
            "note": note,
        }

    def format_result(self, result: Dict[str, Any]) -> str:
        """Format result as the CLAUDE.md mandatory reporting line."""
        return (
            f"pool_sharpe={result['pool_sharpe']:.4f} | "
            f"sym_sharpe={result['sym_sharpe']:.4f} | "
            f"avg_gain_trade={result['avg_gain_trade']:.2f}%/trade | "
            f"gain_per_yr={result['gain_per_yr']:.1f}%/yr | "
            f"gain_sym_yr={result['gain_sym_yr']:.4f}%/sym/yr | "
            f"trades={result['trades']} | "
            f"dd={result['max_dd_pct']:.1f}% | "
            f"n_syms={result['n_syms']} | "
            f"years={result['years']:.2f} | "
            f"verdict={result['verdict']}"
        )


# ───────────────────────────────────────────────────────────
# Switch coverage reporter
# ───────────────────────────────────────────────────────────
def report_switch_coverage() -> Dict[str, str]:
    """Report which canonical switches from canonical_switches.json have vec handlers.

    Returns {switch_name: status} where status is one of:
      "WIRED"     — vec engine branches on this switch
      "APPROX"    — wired but approximated (noted in docstring)
      "NO_OP_REAL"— real engine also no-ops on this (both agree = correct)
      "MISSING"   — not wired in vec engine (silent bug!)
    """
    wired = {
        "SATOSHIT_ENABLED_TRADIER": "WIRED",  # vec_paths/satoshit.py — full 5-vote + HTF gates
        "DELTA_ENTRY_ENABLED": "APPROX",        # velocity proxy
        "DELTA_ENGINE_ENABLED": "APPROX",       # velocity proxy exit
        "RZ_EXIT_ENABLED": "WIRED",
        "STRUCTURAL_RANGE_SHIFT_EXIT": "WIRED",
        "STRUCTURAL_RANGE_SHIFT_TF": "WIRED",
        "WT_CROSSUNDER_FINAL_ENABLED": "WIRED",
        "TRADIER_DC_DAYTRADE_ENABLED": "APPROX",  # daytrade not simulated per-minute
        "TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES": "APPROX",
        "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION": "APPROX",
        "TRADIER_DC_DAYTRADE_STOP_PCT": "APPROX",
        "TRADIER_DC_DAYTRADE_TARGET_PCT": "APPROX",
        "TRADIER_DC_POSITION_ENTRY_THRESHOLD": "WIRED",
        "TRADIER_ENTRY_SCORE_THRESHOLD": "WIRED",
        "TRADIER_FH_MOMENTUM_DC_CONFIRM": "WIRED",
        "TRADIER_FH_MOMENTUM_DC_MAX_LONG": "WIRED",
        "TRADIER_FH_MOMENTUM_MFI_CONFIRM": "WIRED",
        "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": "WIRED",
        "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER": "WIRED",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": "WIRED",
        "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": "WIRED",
        "TRADIER_MI_ENTRY_ENABLED_TRADIER": "APPROX",  # MI signals not in NPZ
        "TRADIER_MI_EXIT_ENABLED_TRADIER": "APPROX",
        "TRADIER_RSI2_ENABLED": "WIRED",
        "TRADIER_RSI2_EXIT_THRESHOLD_LONG": "WIRED",
        "TRADIER_RSI2_EXIT_THRESHOLD_SHORT": "WIRED",
        "TRADIER_RSI_ENTRY_SHORT_TRADIER": "WIRED",
        "TRADIER_STOCH_ENTRY_LONG_TRADIER": "WIRED",
        "TRADIER_STOCH_ENTRY_SHORT_TRADIER": "WIRED",
        "TRADIER_STOCH_EXTREME_LONG_TRADIER": "WIRED",
        "TRADIER_STOCH_EXTREME_SHORT_TRADIER": "WIRED",
        "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER": "WIRED",
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER": "WIRED",
        "TRADIER_WT_EXIT_TFS_TRADIER": "WIRED",
        "HEDGE_EXIT_BYPASS_NOLOSS": "APPROX",     # hedge sim simplified
        "HEDGE_EXIT_WT_TF": "APPROX",
        "HEDGE_CLOSE_REMOVE_FROM_TRADEABLE": "APPROX",
        "HEDGE_SAME_SYMBOL_PCT": "APPROX",
        "HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE": "NO_OP_REAL",  # disabled by design
        "REENTRY_WT15M_CROSS_ENABLED": "WIRED",
        "REENTRY_WT15M_SIZE_MULT": "WIRED",
        "REENTRY_WT15M_K_MAX": "WIRED",
        "REENTRY_WT15M_HTF_FAVOR_REQUIRED": "WIRED",
        "REENTRY_K15M_PARTIAL_ENABLED": "WIRED",
        "REENTRY_K15M_PARTIAL_THRESHOLD": "WIRED",
        "REENTRY_K15M_PARTIAL_MULT": "WIRED",
        "REENTRY_POST_CONSOL_ENABLED": "WIRED",
        "REENTRY_POST_CONSOL_MULT": "WIRED",
        "REENTRY_POST_CONSOL_ATR_THRESHOLD": "WIRED",
        "REENTRY_POST_CONSOL_TFS_REQUIRED": "WIRED",
        "R1_DC_LOW4_3M_EMERGENCY_ENABLED": "WIRED",
        "R1_NEWBORN_WINDOW_MIN": "WIRED",
        "R1_USE_DC_4BAR": "WIRED",
        "R1_TF": "WIRED",
        "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED": "WIRED",
        "WT_15M_VEL_SLOW_GAIN_BAND_PCT": "WIRED",
        "WT_VEL_DECEL_RATIO": "WIRED",
        "R2_TF_LIST": "WIRED",
        "GOLDEN_RULE_ENABLED": "WIRED",
        "GOLDEN_RULE_MULT_15M": "WIRED",
        "GOLDEN_RULE_MULT_1H": "WIRED",
        "GOLDEN_RULE_MULT_4H": "WIRED",
        "GOLDEN_RULE_MULT_D": "WIRED",
        "GOLDEN_RULE_OR_LOGIC": "WIRED",
        "STDEV_BREAKOUT_ENABLED": "WIRED",
        "STDEV_BOUNCE_ENABLED": "WIRED",
        "PARTIAL_PROFIT_LOCK_ENABLED": "WIRED",
        "VOL_TARGET_ENABLED": "WIRED",
        "DD_KELLY_ENABLED": "WIRED",
        "GR_HTF_GATE_ENABLED": "WIRED",
        "LINEARITY_LR_LONG_ENABLED": "WIRED",
        "LINEARITY_LR_SHORT_ENABLED": "WIRED",
    }
    return wired


# ───────────────────────────────────────────────────────────
# CLI entry point
# ───────────────────────────────────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="vec_engine_v1 — validator-gated vectorized backtest")
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--symbols", required=True, help="Comma-separated symbol list")
    ap.add_argument("--start", help="Start date YYYY-MM-DD")
    ap.add_argument("--end", help="End date YYYY-MM-DD")
    ap.add_argument("--overrides", help="JSON file with config overrides")
    ap.add_argument("--npz-dir", help="Path to NPZ indicator directory")
    ap.add_argument("--switches", action="store_true", help="Print switch coverage report")
    args = ap.parse_args()

    if args.switches:
        coverage = report_switch_coverage()
        print("Switch coverage:")
        for k, v in sorted(coverage.items()):
            print(f"  {k}: {v}")
        return

    symbols = [s.strip() for s in args.symbols.split(",")]
    cfg = VecConfig()

    if args.overrides:
        with open(args.overrides) as f:
            overrides = json.load(f)
        cfg = cfg.update_from_dict(overrides)

    start_ts = None
    end_ts = None
    if args.start:
        from datetime import datetime
        start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").timestamp())
    if args.end:
        from datetime import datetime
        end_ts = int(datetime.strptime(args.end, "%Y-%m-%d").timestamp())

    eng = VecEngine(mode=args.mode, npz_dir=args.npz_dir)
    result = eng.simulate(symbols=symbols, cfg=cfg, start_ts=start_ts, end_ts=end_ts)
    print(eng.format_result(result))
    print(json.dumps({k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))}, indent=2))


if __name__ == "__main__":
    main()
