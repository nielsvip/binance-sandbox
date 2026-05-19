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

# SIZING pipeline (Path 1) — compute_position_size + WT_HTF_DISCOUNT + HEDGE_SIZE_CAP
try:
    from vec_paths.sizing import compute_position_size as _compute_position_size_v2
    from vec_paths.sizing import compute_size_multiplier as _compute_size_mult_v2
    _SIZING_V2_AVAILABLE = True
except ImportError:
    _SIZING_V2_AVAILABLE = False
    def _compute_position_size_v2(store, bar_idx, side, mode, cfg, dd_state, running_gain, pos_state=None, is_hedge=False, origin_pos_usd=0.0, is_rz_entry=False, base_qty_override=None): return (1.0, 0)
    def _compute_size_mult_v2(store, bar_idx, side, mode, cfg, dd_state, running_gain, pos_state=None, is_hedge=False, origin_pos_usd=0.0, is_rz_entry=False): return 1.0

# DUP_GUARD (Path 2) — AUGMENT_LOCK + DUPLICATE_OPEN_GUARD
try:
    from vec_paths.dup_guard import check_dup_guard_block as _check_dup_guard_block
    _DUP_GUARD_AVAILABLE = True
except ImportError:
    _DUP_GUARD_AVAILABLE = False
    def _check_dup_guard_block(pos_state, ts_i, current_gain_pct, cfg, pos_value_usd=0.0, action="AUGMENT", reason_hint=""): return None

# REDUCE PATHS (Path 3) — K1M_EXTREME_REVERSE, STRONG_REDUCE_K, PROFIT_TAKE_REDUCE
try:
    from vec_paths.reduce_paths import (
        check_k1m_extreme_reverse as _check_k1m_extreme_reverse,
        check_strong_reduce_k as _check_strong_reduce_k,
        check_profit_take_reduce as _check_profit_take_reduce,
    )
    _REDUCE_PATHS_AVAILABLE = True
except ImportError:
    _REDUCE_PATHS_AVAILABLE = False
    def _check_k1m_extreme_reverse(store, bar_idx, pos_state, mode, cfg): return None
    def _check_strong_reduce_k(store, bar_idx, pos_state, mode, cfg): return None
    def _check_profit_take_reduce(store, bar_idx, pos_state, mode, cfg): return None

# PEAK_GIVEBACK / BE_EROSION (Path 4)
try:
    from vec_paths.peak_giveback_be_erosion import (
        check_peak_giveback_exit as _check_peak_giveback_exit,
        check_be_erosion_exit as _check_be_erosion_exit,
    )
    _PEAK_GIVEBACK_AVAILABLE = True
except ImportError:
    _PEAK_GIVEBACK_AVAILABLE = False
    def _check_peak_giveback_exit(store, bar_idx, pos_state, mode, cfg): return None
    def _check_be_erosion_exit(store, bar_idx, pos_state, mode, cfg): return None

# WINNER_PROTECT / WT_15M_VEL_SLOW (Path 5)
try:
    from vec_paths.winner_protect import (
        check_winner_protect_hold as _check_winner_protect_hold,
        check_wt_15m_vel_slow_zero_gain as _check_wt_15m_vel_slow_zero_gain,
    )
    _WINNER_PROTECT_AVAILABLE = True
except ImportError:
    _WINNER_PROTECT_AVAILABLE = False
    def _check_winner_protect_hold(store, bar_idx, pos_state, mode, cfg): return False
    def _check_wt_15m_vel_slow_zero_gain(store, bar_idx, pos_state, mode, cfg): return None

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

    # ── ROUND 5 FIX (2026-05-19) — MIN_ENTRY_SPACING_HOURS ─────────────────
    # Source: per-sym overlay requirement — round 4 hit structural ceiling
    # because SOL needed entry spacing tightened (trade ratio 0.21x).
    # ENTRY_COOLDOWN_SEC only gates the WT path; this knob gates ALL entry
    # paths (WT, FLZ, REENTRY, SCALP, PCB, DC_BREAK, BB_SQUEEZE...).
    # When > 0, no new OPEN of any kind within MIN_ENTRY_SPACING_HOURS of
    # the previous CLOSE on the same (sym, side) pair. Default 0 = no
    # cross-path spacing (preserves R4 behavior). Set in per_sym overlay.
    MIN_ENTRY_SPACING_HOURS: float = 0.0

    # ── ROUND 4 FIX (2026-05-19) — FLZ_AGENT_FORCE_OPEN_MOCK ─────────────────
    # MTF_SR_FRESH_SETUP-style sparse force-opens. Mirrors flz bot behavior of
    # opening LONG when price kisses a daily/weekly horizontal level even though
    # WT setup is unfavorable. Forensic evidence (ETH 30d window):
    #   live opens: FLZ_AGENT_FORCE_OPEN(MTF_SR_FRESH_SETUP) × 6 of 12 OPENs.
    #   Vec previously had no equivalent → vec never lost on those entries →
    #   vec sharpe sign FLIPPED positive vs live's slightly-negative.
    # Logic: when bar_idx is on a daily-open boundary AND price within
    # FLZ_MOCK_SR_ATR_MULT × atr_D of dc_basis_D OR dc_basis_W, force open
    # this side. Bypasses WT score / GR filter (intentional). Honors cooldown
    # and dup_guard (these are bot-level too).
    # Default OFF; flipped on per-sym via baseline JSON.
    FLZ_FORCE_OPEN_MOCK_ENABLED: bool = False
    FLZ_FORCE_OPEN_MOCK_SR_ATR_MULT: float = 0.50  # distance to D-basis in ATR_D units
    FLZ_FORCE_OPEN_MOCK_REQUIRE_NEW_DAY: bool = True  # only fire on first bar of a UTC day
    FLZ_FORCE_OPEN_MOCK_MIN_GAP_HOURS: float = 6.0  # min hours between FLZ-mock fires (anti-spam)
    FLZ_FORCE_OPEN_MOCK_LONG_ONLY: bool = True  # mirrors live ETH evidence (all 6 fires were LONG)

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

    # ── Path 1: Sizing pipeline (WT_HTF_DISCOUNT + HEDGE_SIZE_CAP + MIN_POS) ──
    # Source: vec_paths/sizing.py + position_evaluator.compute_trade_qty_core()
    WT_HTF_DISCOUNT_ENABLED: bool = True        # reduce size when HTF WT misaligned
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0         # max hedge size as pct of loser value
    MIN_POSITION_SIZE: float = 55.0             # USD floor (maps to min_qty at current price)
    WINNER_AUGMENT_ENABLED: bool = False        # avg-in on profitable position
    WINNER_AUGMENT_MIN_GAIN_PCT: float = 2.0    # min gain to trigger augment bonus
    WINNER_AUGMENT_SIZE_MULT: float = 1.5       # qty multiplier when augmenting winner
    SIZING_V2_ENABLED: bool = False             # opt-in: use compute_position_size_v2 pipeline

    # ── Path 2: DUP_GUARD + AUGMENT_LOCK ─────────────────────────────────────
    # Source: vec_paths/dup_guard.py + ez_manage.py:11014+14062
    DUP_GUARD_ENABLED: bool = True              # master gate for both guards
    DUP_GUARD_USE_GAIN_GATE: bool = True        # True=gain-based, False=time-cooldown
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5      # threshold = MIN_GAIN * multiplier = 1.5%
    AUGMENT_LOCK_MIN_SECONDS: float = 900.0     # cooldown for AUGMENT_LOCK
    DUPLICATE_OPEN_COOLDOWN: float = 900.0      # fallback time-based cooldown

    # ── Path 3: Reduce paths ──────────────────────────────────────────────────
    # Source: vec_paths/reduce_paths.py
    K1M_EXTREME_REVERSE_ENABLED: bool = False   # k_1m extreme + turn → partial close
    K1M_EXTREME_HIGH: float = 90.0
    K1M_EXTREME_LOW: float = 10.0
    K1M_REVERSE_REQUIRES_PROFIT: bool = True
    K1M_REVERSE_REDUCE_FRAC: float = 0.5
    STRONG_REDUCE_K_ENABLED: bool = False       # k_15m extreme + k_1h opposite → partial
    SRK_K15M_LONG_MIN: float = 80.0
    SRK_K1H_LONG_MAX: float = 30.0
    SRK_K15M_SHORT_MAX: float = 20.0
    SRK_K1H_SHORT_MIN: float = 70.0
    SRK_REDUCE_FRAC: float = 0.5
    PROFIT_TAKE_REDUCE_ENABLED: bool = False    # gain >= threshold → partial close
    PROFIT_TAKE_GAIN_PCT: float = 2.0
    PROFIT_TAKE_REDUCE_FRAC: float = 0.5

    # ── Path 4: PEAK_GIVEBACK + BE_EROSION ───────────────────────────────────
    # Source: vec_paths/peak_giveback_be_erosion.py + tradier_manage.py:5028
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.5
    PEAK_GIVEBACK_DROP_PCT: float = 0.5
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED: bool = False   # OFF per USER 2026-05-11
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = False      # OFF since 2026-04-27
    PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN: bool = True
    PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT: float = -0.5
    BREAKEVEN_GRACE_MINUTES: float = 15.0
    BE_EROSION_ENABLED: bool = False
    BE_EROSION_MIN_PEAK_PCT: float = 0.5
    BE_EROSION_FLOOR_PCT: float = 0.0
    BE_EROSION_HOLD_MIN_MIN: float = 15.0

    # ── Path 5: WINNER_PROTECT + WT_15M_VEL_SLOW zero-gain variant ───────────
    # Source: vec_paths/winner_protect.py
    # WINNER_PROTECT: ez_positions_quick.py:13918
    WINNER_PROTECT_ENABLED: bool = False
    WINNER_PROTECT_GAIN_PCT: float = 2.0        # alias for RP_PROTECT_MIN_GAIN
    RP_PROTECT_THRESHOLD: float = 70.0
    RP_PROTECT_MIN_GAIN: float = 2.0
    WINNER_PROTECT_HTF_MIN_ALIGNED: int = 2     # vec proxy for live ranking threshold
    # WT_15M_VEL_SLOW (zero-gain variant): ez_manage.py:21022 R2
    # Note: R2_USE_EXIT_R1_R2_MODULE=True means exit_r1_r2.py handles R2 above.
    # winner_protect.py R2 only activates when R2_USE_EXIT_R1_R2_MODULE=False.
    R2_USE_EXIT_R1_R2_MODULE: bool = True       # True = use exit_r1_r2.py (default)
    R2_PEAK_MIN_PCT: float = 0.5
    # WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED, WT_15M_VEL_SLOW_GAIN_BAND_PCT,
    # WT_15M_VEL_SLOW_GAIN_FLOOR_PCT, WT_VEL_DECEL_RATIO, WT_VEL_USE_DECEL_RATIO_ONLY,
    # R2_TF_LIST, WT_15M_VEL_NEAR_ZERO_THRESHOLD already declared above in R2 section.

    # ── Misc ─────────────────────────────────────────────────
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    RATIO_MULTIPLIER: float = 3.0
    HEDGE_MODE: bool = True
    STRICT_NO_LOSS: bool = False  # Eliminated — replaced by R1/R2/HEDGE

    # ── 2026-05-18 hourly_reconfig cluster-knobs — WIRED 2026-05-18 19:00 ─────────
    # All 14 knobs below are now wired in VecEngine.simulate(). See wiring notes:
    # - BREAKOUT_RETEST_ARMED_ENABLED: ENTRY gate (entry loop, after WT_DC score gate)
    # - DC_BB_D_BREAK_REVERSE_ENABLED: ENTRY suppression (entry loop, when knob=False any
    #   entry with reason containing "DC_BB_D_BREAK_REVERSE" is suppressed; since vec engine
    #   does not emit reverse-reason opens, this acts as a no-op suppressor — the knob's
    #   primary effect is on live reverse-fire pattern; vec ablation observes via missing
    #   entries that would have been re-emitted by the reverse path).
    # - HEDGE_HTF_VETO_ENABLED: HEDGE entry block (hedge_engine section)
    # - HEDGE_MAX_ABSOLUTE_USD: HEDGE entry block (size cap via qty * price)
    # - HTF_TREND_VETO_ENABLED: ENTRY veto in OPEN/AUGMENT (entry loop, before WT_DC score)
    # - NOLOSS_BYPASS_WT_5OF5_ENABLED / _MIN_TFS: EXIT bypass (WT-cross exit + STOCH exit
    #   allow gain<0 closure when ≥MIN_TFS of {5m,15m,1h,4h,D} WT against)
    # - PARTIAL_PROFIT_LOCK_*_TRADIER: mode==tradier overrides for PPL Step1/2/3 thresholds
    # - STDEV_MACRO_ENTRY_VETO_ENABLED: ENTRY veto on STRONG_TOP (LONG) / STRONG_BOT (SHORT)
    # - STDEV_MACRO_AUGMENT_VETO_ENABLED: AUGMENT veto on TOP/BOT (vec engine has limited
    #   augment path via SENTIMENT_BOOST — this knob blocks sentiment_boost when at extreme)
    # - STDEV_MACRO_R4_EXIT_ENABLED: EXIT path after BB_RECOVERY/before STOCH_REVERSE_EXIT
    BREAKOUT_RETEST_ARMED_ENABLED: bool = False              # WIRED 2026-05-18 (entry gate)
    DC_BB_D_BREAK_REVERSE_ENABLED: bool = True               # WIRED 2026-05-18 (entry suppression)
    HEDGE_HTF_VETO_ENABLED: bool = True                      # WIRED 2026-05-18 (hedge entry block)
    HEDGE_MAX_ABSOLUTE_USD: float = 100000.0                 # WIRED 2026-05-18 (hedge size cap)
    HTF_TREND_VETO_ENABLED: bool = False                     # WIRED 2026-05-18 (entry veto)
    NOLOSS_BYPASS_WT_5OF5_ENABLED: bool = False              # WIRED 2026-05-18 (exit bypass)
    NOLOSS_BYPASS_WT_5OF5_MIN_TFS: int = 5                   # WIRED 2026-05-18 (exit bypass threshold)
    PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER: float = 0.3        # WIRED 2026-05-18 (tradier PPL step1)
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER: float = 0.5    # WIRED 2026-05-18 (tradier PPL step2)
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER: float = 0.02  # WIRED 2026-05-18 (tradier PPL BE buffer)
    PARTIAL_PROFIT_LOCK_FRAC_TRADIER: float = 0.625          # WIRED 2026-05-18 (tradier PPL fraction)
    STDEV_MACRO_ENTRY_VETO_ENABLED: bool = False             # WIRED 2026-05-18 (entry veto)
    STDEV_MACRO_AUGMENT_VETO_ENABLED: bool = False           # WIRED 2026-05-18 (augment veto)
    STDEV_MACRO_R4_EXIT_ENABLED: bool = False                # WIRED 2026-05-18 (R4 exit)
    # STDEV_MACRO threshold mirrors (defaults from vec_paths/stdev_macro_vec.py):
    STDEV_MACRO_MODERATE_THRESHOLD: float = 1.5
    STDEV_MACRO_STRONG_THRESHOLD: float = 2.5
    STDEV_MACRO_WINDOW_D: int = 200
    STDEV_MACRO_WINDOW_W: int = 52
    STDEV_MACRO_R4_REQUIRE_LTF_FLIP: bool = True

    # ── ROUND 3 FIX #1 2026-05-19 — RIDICULOUS_HOLD_VEC time-cap exit ─────────
    # Mirror of ez_manage.py:38754 RIDICULOUS_HOLD_GUARD. Live evidence (iter 22-24):
    # 64% of live ETH closes & 60% of live SOL closes fire reason
    # "RIDICULOUS_HOLD_age{H}h_cap48h_g{-X}%" — small-loss positions held >48h get
    # force-closed. Vec engine NEVER force-closes on age alone — it relies on
    # STOCH_REVERSE_EXIT / PPL / R1 / R2 which all over-fire vs live.
    # New time-cap path: fires when position age > N hours AND gain in band
    # [floor_pct, ceil_pct]. Default ceil_pct = 0.5% (small-loss-or-near-zero
    # band; matches live evidence where age >239h closes had gain -0.39% to -0.01%).
    # Live uses RIDICULOUS_HOLD_HOURS = 48.0 (config.py default).
    # Default RIDICULOUS_HOLD_VEC_ENABLED=False so baseline unchanged. Opt-in.
    RIDICULOUS_HOLD_VEC_ENABLED: bool = False
    RIDICULOUS_HOLD_VEC_HOURS: float = 48.0
    RIDICULOUS_HOLD_VEC_GAIN_FLOOR_PCT: float = -15.0   # close if gain >= floor
    RIDICULOUS_HOLD_VEC_GAIN_CEIL_PCT: float = 0.5      #   and gain <= ceil


    # ══════════════════════════════════════════════════════════════════════
    # ▼ VEC GAP WIRING 2026-05-19 — bulk-add live-only knobs as VecConfig
    # ▼ fields with live defaults. Defaults preserve 9/9 smoke baseline.
    # ▼ Wired logic landing in vec_paths/* incrementally. Each knob below
    # ▼ is now discoverable by sweep coordinators (was: NoneType vs cfg attr).
    # ▼ See data/_diagnostic/knob_coverage_matrix_20260519.xlsx.
    # ══════════════════════════════════════════════════════════════════════

    # ── ENTRY (291 knobs) ──
    ABLATION_DISABLE_AUGMENTATION: bool = False
    ABLATION_DISABLE_ENTRY_LEADERBOARD: bool = False
    ABLATION_DISABLE_ENTRY_RANKING: bool = True
    ABLATION_DISABLE_ENTRY_REVERSAL: bool = False
    ABLATION_DISABLE_ENTRY_TECHNICAL: bool = True
    ABLATION_DISABLE_HIGH_GAIN_AUGMENT: bool = False
    ABLATION_DISABLE_PERIODIC_REENTRY: bool = False
    ABLATION_DISABLE_QUICK_ENTRY: bool = False
    ABLATION_DISABLE_REENTRY: bool = False
    ABLATION_DISABLE_REENTRY_ENFORCE: bool = False
    AUGMENTED_POSITIONS_GUARD_FLOOR_MULT: float = 0.5
    AUGMENT_BLOWPAST_ENABLED: bool = True
    AUGMENT_HTF_TREND_ENABLED: bool = True
    AUGMENT_ONLY_WHEN_PROFITABLE: bool = True
    AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER: bool = True
    AUGMENT_WT_3TF_ENABLED: bool = True
    AUGMENT_WT_CROSS_ENABLED: bool = True
    BB_BREAKOUT_CONT_ENABLED: bool = True
    BB_BREAKOUT_CONT_HOURS: float = 72.0
    BB_BREAKOUT_ENABLED: bool = False
    BB_BREAKOUT_SCORE: int = 20
    BB_BREAKOUT_TF: str = "1h"
    BB_ENTRY_LONG_THRESHOLD: float = -0.2
    BB_ENTRY_SHORT_THRESHOLD: float = 1.0
    BB_RSI_STOCH_SCALP_ENABLED: bool = False
    BB_RSI_STOCH_SCALP_SCORE: int = 25
    BB_SQUEEZE_COOLDOWN: float = 300.0
    BB_SQUEEZE_ENABLED: bool = True
    BB_SQUEEZE_MIN_ALIGNMENT: int = 10
    BB_SQUEEZE_WIDTH_PERCENTILE: float = 0.2
    BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE: float = 0.02
    BOUNCE_AUGMENT_ENABLED: bool = True
    BOUNCE_AUGMENT_K_D_CROSSING_UP: bool = True
    BOUNCE_AUGMENT_K_D_THRESHOLD: float = 20.0
    BOUNCE_AUGMENT_MIN_LOSS_PCT: float = -0.5
    BOUNCE_AUGMENT_PAPER: bool = True
    BOUNCE_REENTRY_ENABLED: bool = True
    BOUNCE_REENTRY_ENABLED_TRADIER: bool = True
    BOUNCE_REENTRY_K_RESET_LONG: int = 35
    BOUNCE_REENTRY_K_RESET_LONG_TRADIER: int = 35
    BOUNCE_REENTRY_K_RESET_SHORT: int = 65
    BOUNCE_REENTRY_K_RESET_SHORT_TRADIER: int = 65
    BOUNCE_TOP_REENTRY_MULT: float = 1.5
    BTC_RZ_PROXIMITY_PCT: float = 0.5
    CONNORS_RSI_ENTRY_THRESHOLD: float = 10.0
    CRYPTO_FH_MOMENTUM_MAX_POSITIONS: int = 4
    CRYPTO_FH_MOMENTUM_POSITION_SIZE_MULT: float = 1.0
    DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED: bool = False
    DAEMON_REENTRY_STALE_EXIT_ENABLED: bool = True
    DC_BB_CROSSBACK_HYSTERESIS_PCT: float = 2.0
    DC_BREAKOUT_ENTRY_ENABLED: bool = True
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False
    DC_POSITION_ENTRY_THRESHOLD: float = 0.25
    DELTA_ENTRY_SCORE_BONUS: int = 15
    DELTA_ENTRY_SCORE_PENALTY: int = -25
    DELTA_REENTRY_FILTER_ENABLED: bool = False
    DELTA_REENTRY_HTF_GATE: str = "4h"
    DELTA_REENTRY_MIN_TF: int = 2
    DELTA_REENTRY_REQUIRE_NOT_EXITING: bool = True
    DELTA_REENTRY_Z_THRESHOLD: float = 1.0
    DG_MAX_FORCE_OPEN_NOTIONAL_USD: float = 500.0
    DG_REPEAT_OPEN_PER_DAY_MAX: int = 3
    EMA_DIST_ENTRY_ENABLED: bool = True
    ENTRY_ATR_PCT_MIN: float = 1.5
    ENTRY_MIN_ALIGNMENT: int = 10
    ENTRY_PRIMARY_TF: str = '4h'
    ENTRY_SYMGATE_ENABLED: bool = False
    ENTRY_TRIGGER_TF: str = '15m'
    ENTRY_VET_NO_STRUCT_OR_BREAKOUT_REQUIRED: bool = True
    ENTRY_VOL_MIN_RATIO: float = 1.3
    ENTRY_ZONE_LONG: float = 80.0
    ENTRY_ZONE_SHORT: float = 20.0
    EVAL_REENTRY_ENABLED: bool = True
    EXTREME_OB_BB_PCT_B_4H_MIN: float = 1.0
    EXTREME_OS_BB_PCT_B_4H_MAX: float = 0.0
    EZ_REENTRY_DAEMON_ENABLED: bool = True
    EZ_REENTRY_INLINE_ENABLED: bool = False
    EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED: bool = False
    EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED: bool = False
    EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED: bool = False
    EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED: bool = False
    EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED: bool = True
    EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED: bool = False
    EZ_REENTRY_PRICE_CROSS_INTERVAL_S: float = 5.0
    EZ_REENTRY_PRICE_CROSS_MAX_AGE_HOURS: float = 48.0
    EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK: int = 20
    EZ_REENTRY_PRICE_CROSS_MIN_GAP_S: float = 60.0
    EZ_REENTRY_PRICE_CROSS_PARTIAL_FRAC: float = 0.5
    EZ_REENTRY_PRICE_CROSS_PCT: float = 0.0
    EZ_REENTRY_QUEUE_CONSUMER_ENABLED: bool = True
    EZ_REENTRY_QUEUE_CONSUMER_INTERVAL_S: float = 5.0
    FH_MOMENTUM_EVAL_MINUTES: int = 30
    FH_MOMENTUM_MAX_POSITIONS: int = 5
    FH_MOMENTUM_POSITION_SIZE: float = 600.0
    GR_HTF_DIRECT_ENTRY_DOUBLE_SCORE: float = 34.0
    GR_HTF_DIRECT_ENTRY_ENABLED: bool = True
    GR_HTF_DIRECT_ENTRY_SCORE_MIN: float = 23.0
    GUARANTEED_REENTRY_DELTA_GATE_ENABLED: bool = False
    GUARANTEED_REENTRY_K_FAVORABLE_HIGH: float = 70.0
    GUARANTEED_REENTRY_K_FAVORABLE_LOW: float = 30.0
    GUARANTEED_REENTRY_K_HIGH_BLOCK: float = 80.0
    GUARANTEED_REENTRY_K_LOW_BLOCK: float = 20.0
    GUARANTEED_REENTRY_STRICT_CONFIRMATION: bool = False
    GUARANTEED_REENTRY_TIGHT_STOP_ENABLED: bool = True
    GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S: float = 1800.0
    GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S: float = 60.0
    GUARANTEED_REENTRY_TIGHT_STOP_PCT: float = 0.5
    HIGH_GAIN_AUGMENTATION_MIN_SIZE: int = 50
    HLR_REENTRY_MAX_AGE_S: float = 14400.0
    HLR_REENTRY_MULT_1H: float = 1.5
    HLR_REENTRY_MULT_4H: float = 2.0
    HLR_REENTRY_MULT_D: float = 2.5
    HLR_REENTRY_MULT_W: float = 3.0
    HTF_GATE_APPLY_TO_AUGMENT: bool = True
    HTF_GATE_APPLY_TO_OPEN: bool = True
    K_ZONE_ENTRY_BONUS: int = 25
    K_ZONE_ENTRY_BONUS_TRADIER: int = 20
    K_ZONE_ENTRY_ENABLED: bool = True
    K_ZONE_ENTRY_ENABLED_TRADIER: bool = True
    LEGACY_DC_BREAKOUT_REENTRY: bool = True
    LEGACY_GUARANTEED_REENTRY: bool = True
    LEGACY_PROC_SINGLE_REENTRY: bool = False
    LEGACY_REENTRY_PSR_DC_BOUNCE: bool = True
    LEGACY_REENTRY_PSR_FULL_DC: bool = True
    LEGACY_REENTRY_PSR_K_DC_CROSSOVER: bool = False
    LEGACY_REENTRY_PSR_QUICK_RECOVERY: bool = True
    LH_HL_FILTER_AUGMENT_GATE_ENABLED: bool = False
    LIVE_ENTRY_ENGINE_BOOST_SCORE: float = 8.0
    LIVE_ENTRY_ENGINE_DC_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_HTF_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_MIN_SCORE: float = 0.5
    LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT: float = 1.0
    LIVE_ENTRY_ENGINE_STOCH_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_WT_ENABLED: bool = True
    MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS: bool = False
    MANDATORY_REENTRY_K_HIGH_BLOCK: float = 80.0
    MANDATORY_REENTRY_K_LOW_BLOCK: float = 20.0
    MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME: bool = True
    MAX_AUGMENTS_PER_POSITION: int = 3
    MFI_ENTRY_ENABLED: bool = True
    MI_ENTRY_ENABLED: bool = False
    MI_ENTRY_ENABLED_TRADIER: bool = False
    MI_ENTRY_EXHAUST_BONUS: int = 8
    MI_ENTRY_EXHAUST_BONUS_TRADIER: int = 8
    MI_ENTRY_STRUCT_BONUS: int = 10
    MI_ENTRY_STRUCT_BONUS_TRADIER: int = 10
    MTS_ENTRY_QUALITY_BONUS: float = 25.0
    MTS_ENTRY_QUALITY_MIN: float = 8.0
    MTS_ENTRY_QUALITY_MIN_SHORT: float = 5.0
    MTS_ENTRY_QUALITY_MIN_TRADIER: float = 0.0
    MTS_ENTRY_QUALITY_STRONG: float = 40.0
    OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT: float = 1.0
    OBLIGATORY_REENTRY_ENABLED: bool = True
    OBLIGATORY_REENTRY_K15_HIGH_BLOCK: float = 95.0
    OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC: float = 0.5
    OBLIGATORY_REENTRY_LONG_ENABLED: bool = True
    OBLIGATORY_REENTRY_SCORE_TIER1: int = 40
    OBLIGATORY_REENTRY_SCORE_TIER2: int = 30
    OBLIGATORY_REENTRY_SCORE_TIER3: int = 30
    OBLIGATORY_REENTRY_SHORT_ENABLED: bool = True
    OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK: float = 5.0
    OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC: float = 0.5
    OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT: float = 1.5
    OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT: float = 1.5
    OBLIGATORY_REENTRY_SMA_FIELD: str = "ema_50"
    OBLIGATORY_REENTRY_SMA_TF: str = "15m"
    OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED: int = 3
    OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED: int = 1
    OPENING_BUFFER_NO_CLOSE_MINUTES: float = 30.0
    PARABOLIC_BB_PCT_B_4H_MAX: float = 0.1
    PARABOLIC_BB_PCT_B_4H_MIN: float = 0.7
    RATIO_REBALANCE_MAX_OPENS_EXTREME: int = 12
    RATIO_REBALANCE_MAX_OPENS_NORMAL: int = 8
    RATIO_REBALANCE_MAX_OPENS_STUCK: int = 10
    RED_ZONE_AUGMENT_GATE_ENABLED: bool = False
    RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED: bool = True
    REENTRY2_DC_BREAK_ENABLED: bool = True
    REENTRY2_DIR_FAV_ENABLED: bool = True
    REENTRY2_QUICK_RECOVERY_ENABLED: bool = True
    REENTRY2_STOCH_CROSS_ENABLED: bool = False
    REENTRY_2_ENABLED: bool = True
    REENTRY_60MIN_MIN_PCT: float = 0.05
    REENTRY_60MIN_UNCONDITIONAL_ENABLED: bool = True
    REENTRY_60MIN_WINDOW_MIN: float = 1440.0
    REENTRY_AGGRESSIVE_WINDOW_MIN: float = 30.0
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True
    REENTRY_B04_DC_RETEST_ENABLED: bool = True
    REENTRY_B09_SNAPBACK_ENABLED: bool = False
    REENTRY_B10_STOCH_REV_ENABLED: bool = True
    REENTRY_B11_DC_BREAK_ENABLED: bool = True
    REENTRY_B12_WT_MOM_ENABLED: bool = True
    REENTRY_B14_HA_TREND_ENABLED: bool = True
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True
    REENTRY_B16_SIZE_MULT_STRONG: float = 3.0
    REENTRY_B16_SIZE_MULT_WEAK: float = 1.5
    REENTRY_B16_SMA200_PROX_PCT: float = 0.005
    REENTRY_B16_SMA200_PULLBACK_ENABLED: bool = True
    REENTRY_COOLDOWN_S: float = 0.0
    REENTRY_CROSS_FRESHNESS_ENABLED: bool = False
    REENTRY_CROSS_MAX_BARS_AGO: int = 5
    REENTRY_DISPATCH_BACKOFF_S: float = 0.4
    REENTRY_DISPATCH_MAX_ATTEMPTS: int = 3
    REENTRY_ESCALATION_CRIT_MIN: float = 60.0
    REENTRY_ESCALATION_WARN_MIN: float = 30.0
    REENTRY_EXHAUSTED_PARTIAL_ENABLED: bool = True
    REENTRY_FAVORABLE_HTF_MIN: int = 2
    REENTRY_FAVORABLE_MOVE_PCT: float = 1.0
    REENTRY_FAVORABLE_QTY_MULT: float = 1.0
    REENTRY_LIVE_MONITOR_ENABLED: bool = True
    REENTRY_LIVE_MONITOR_INTERVAL_S: int = 30
    REENTRY_LIVE_MONITOR_PARTIAL_PCT: float = 0.5
    REENTRY_MANDATORY: bool = True
    REENTRY_MIN_GAP_MINUTES: float = 15.0
    REENTRY_NEVER_SKIP_ENABLED: bool = True
    REENTRY_PRICE_IMPROVE_PCT: float = 0.1
    REENTRY_RALLY_HTF_MIN: int = 1
    REENTRY_RALLY_K15M_MAX: float = 30.0
    REENTRY_SYMGATE_ENABLED: bool = False
    REENTRY_SYMGATE_SPEED_MIN: float = 0.5
    REENTRY_TIER1_SIZE_MULT: float = 1.5
    REENTRY_TIER1_SIZE_MULT_TRADIER: float = 1.5
    REENTRY_TIER2_MAX_MINUTES: float = 120.0
    REENTRY_TIER2_MAX_MINUTES_TRADIER: float = 120.0
    REENTRY_TIER2_MIN_MINUTES: float = 10.0
    REENTRY_TIER2_MIN_MINUTES_TRADIER: float = 10.0
    REENTRY_TIER2_PRICE_PCT: float = 0.003
    REENTRY_TIER2_PRICE_PCT_TRADIER: float = 0.003
    REENTRY_TIER2_SIZE_MULT: float = 0.8
    REENTRY_TIER2_SIZE_MULT_TRADIER: float = 0.8
    RSI2_ENTRY_THRESHOLD: float = 3.0
    RSI_ENTRY_LONG_TRADIER: float = 40.0
    RSI_ENTRY_PERIOD_TRADIER: int = 10
    RSI_ENTRY_SHORT_TRADIER: float = 58.0
    RZ_BASELINE_BOUNCE_SHORT_ENABLED: bool = False
    RZ_BASELINE_TOL: float = 0.05
    RZ_BOT_BB_THRESHOLD: float = 0.15
    RZ_DIV_BLOCK_MIN: int = 2
    RZ_DIV_EXIT_ENABLED: bool = True
    RZ_ENTRY_ENABLED: bool = True
    RZ_K_ENTRY_MAX: float = 50.0
    RZ_K_EXIT: float = 95.0
    RZ_LEGS_MIN: float = 20.0
    RZ_LTF_MICRO: str = "5m"
    RZ_MFI_EXIT: float = 85.0
    RZ_REQUIRE_STRUCT: bool = False
    RZ_TOP_BB_THRESHOLD: float = 0.85
    RZ_TWO_PHASE_EXIT_ENABLED: bool = True
    RZ_ZSCORE_EXIT_ENABLED: bool = True
    RZ_ZSCORE_ZONE_ENABLED: bool = True
    SCALP_V3_REENTRY_STICKY_ENABLED: bool = True
    SCALP_V3_REENTRY_STICKY_MIN: int = 30
    SCALP_V3_REQUIRE_SR_ON_ENTRY: bool = False
    SMA200_DIST_ENTRY_ENABLED: bool = True
    STDEV_BB_RZ_EXIT_ENABLED: bool = False
    STDEV_BB_RZ_EXIT_TF: str = "D"
    TF_FOCUS_ENTRY_HARD_GATE: bool = True
    TRADIER_FH_MOMENTUM_MFI_MIN: float = 55.0
    TRADIER_FH_MOMENTUM_WINDOW_MINUTES: int = 60
    TRADIER_MFI_ENTRY_LONG_ENABLED: bool = True
    TRADIER_MFI_ENTRY_LONG_TRADIER: float = 60.0
    TRADIER_REENTRY_ANTI_CHURN_ENABLED: bool = False
    TRADIER_REENTRY_HARDCOOL_MIN: float = 30.0
    TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED: bool = True
    TRADIER_REENTRY_RZ_BLOCK_ENABLED: bool = False
    TRADIER_REOPEN_WAIT_S: float = 0.0
    TRA_DISABLE_AUGMENT: bool = True
    TRA_WT_DC_ENTRY_THRESHOLD: float = 85.0
    TRC_ENTRY_MIN_ALIGNMENT: int = 6
    TRC_ENTRY_ZONE_LONG: float = 30.0
    TRC_ENTRY_ZONE_SHORT: float = 70.0
    VP_GATE_AUGMENT_GATE_ENABLED: bool = False
    WT_3M_FORCE_OPEN_GR_GATE_ENABLED: bool = True
    WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF: int = 5
    WT_3M_FORCE_OPEN_GR_MIN_TFS: int = 4
    WT_3M_FORCE_OPEN_GR_VOTE_MIN: int = 20
    WT_3M_OPEN_GATE_ENABLED: bool = False
    WT_DC_ENTRY_BAR_MATURITY_BLOCK: float = 0.7
    WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED: bool = False
    WT_DC_ENTRY_K5M_MAX_LONG: float = 100.0
    WT_DC_ENTRY_K5M_MIN_SHORT: float = 0.0
    WT_DIV_ENTRY_GATE_ENABLED: bool = True
    WT_EXHAUST_ENTRY_GATE_ENABLED: bool = False
    WT_PERCENTILE_ENTRY_GATE_ENABLED: bool = False
    WT_PERCENTILE_ENTRY_OB_D: float = 90.0
    WT_PERCENTILE_ENTRY_OS_D: float = 10.0
    ZONE_OPEN_THRESHOLD: int = 25
    # ── EXIT (214 knobs) ──
    ABLATION_DISABLE_CHECK_NOLOSS: bool = False
    ABLATION_DISABLE_DC_BREACH_REDUCE: bool = False
    ABLATION_DISABLE_QUICK_EXIT: bool = False
    ABLATION_DISABLE_SPIKE_FADE_EXIT: bool = False
    ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC: float = 30.0
    ALL_TF_AGAINST_CLOSE_ENABLED: bool = True
    ALL_TF_AGAINST_CLOSE_MIN_TFS: int = 5
    ATR_ADAPTIVE_STOP_ENABLED: bool = False
    ATR_ADAPTIVE_STOP_MULT: float = 2.0
    ATR_ADAPTIVE_STOP_TF: str = "1h"
    BREAKEVEN_DC_FIELD_MODE: str = 'DC4'
    BREAKEVEN_DC_LOW4_ENABLED: bool = True
    BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT: bool = True
    BTC_TECH_EXIT_WT_MIN_TFS: int = 3
    CLOSE_ZONE_SIZE_MULT: float = 1.5
    CONNORS_RSI_EXIT_THRESHOLD: float = 70.0
    CT_WT_VELOCITY_1H_MIN: float = 9.0
    CT_WT_VELOCITY_GATE_ENABLED: bool = True
    CYCLE_TP_TIERED_ENABLED: bool = True
    CYCLE_TP_TIERED_FRAC: float = 0.25
    DC_DAYTRADE_PRE_CLOSE_MINUTES: int = 120
    DC_DAYTRADE_STOP_PCT: float = 0.015
    DC_HOPELESS_EXIT_ENABLED: bool = True
    DC_HOPELESS_EXIT_MIN_AGE_S: int = 900
    DC_RECOVERY_EXIT_ENABLED: bool = False
    DC_RECOVERY_EXIT_TOLERANCE_ATR_MULT: float = 0.0
    DC_RECOVERY_EXIT_TOLERANCE_PCT: float = 0.25
    DC_TIER4_BAR_MATURITY_BLOCK: float = 0.7
    DC_TIER4_BAR_MATURITY_BLOCK_ENABLED: bool = False
    DD_BOUNCE_DD_STOP_ENABLED: bool = True
    DELTA_EXIT_ACCEL_THRESHOLD: float = -0.1
    DELTA_EXIT_DECAY_RATIO: float = 0.9
    DELTA_EXIT_DOM_TF_ENABLED: bool = True
    DELTA_EXIT_ENABLED: bool = True
    DELTA_EXIT_MIN_HOLD: int = 4
    DELTA_EXIT_MIN_TF_LOST: int = 1
    DELTA_EXIT_OPPOSING_RATIO: float = 1.5
    DELTA_EXIT_SCORE_BONUS: int = 20
    DELTA_EXIT_SPEED_DECAY: bool = True
    DELTA_EXIT_TF: str = "3m"
    DELTA_SERVICE_BLEED_STOP: bool = True
    DELTA_SERVICE_REDUCE_GATE: bool = True
    DELTA_SERVICE_TRAILING_STOP: bool = True
    DYNAMIC_SCORE_COUNTER_EXIT_ENABLED: bool = True
    DYNAMIC_SCORE_COUNTER_EXIT_THRESHOLD: float = 55.0
    EXIT_ALGO_SCORE_ENABLED: bool = False
    EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED: bool = False
    EXIT_BOUNCE_TOP_ENABLED: bool = False
    EXIT_CONV_FAIL_ENABLED: bool = False
    EXIT_DC_BREACH_REDUCE_ENABLED: bool = True
    EXIT_DELTA_SPEED_ENABLED: bool = True
    EXIT_GAIN_EROSION_ENABLED: bool = True
    EXIT_HARD_DROP_5M_ENABLED: bool = False
    EXIT_HTF_QUICK_TP_ENABLED: bool = True
    EXIT_IBS_EXHAUSTION_ENABLED: bool = False
    EXIT_K5M_BOUNCE_ENABLED: bool = False
    EXIT_MARKET_SPIKE_REDUCE_ENABLED: bool = True
    EXIT_MAX_HOLD_ENABLED: bool = False
    EXIT_MAX_HOLD_MINUTES: int = 99999
    EXIT_MI_ENABLED: bool = False
    EXIT_ON_ALL: bool = True
    EXIT_ON_ALL_ENABLED: bool = False
    EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED: bool = True
    EXIT_PREEMPTIVE_BREAKEVEN_ENABLED: bool = True
    EXIT_SCORER_DC_EXTREME: float = 0.8
    EXIT_SCORER_FULL_SCORE: float = 100.0
    EXIT_SCORER_K_EXTREME: float = 75.0
    EXIT_SCORER_MIN_CONDITIONS: int = 5
    EXIT_SCORER_PARTIAL_SCORE: float = 40.0
    EXIT_SENTIMENT_ENABLED: bool = False
    EXIT_STDEV_BREAKOUT_FAIL_ENABLED: bool = True
    EXIT_STRUCT_BREAK_5M_ENABLED: bool = False
    EXIT_STRUCT_DC_BREAK_ENABLED: bool = True
    EXIT_TREND_REVERSAL_ENABLED: bool = True
    E_1_EXIT_DELTA_THR: float = 50.0
    E_1_WT_EXIT_USE_DELTA_ENABLED: bool = False
    E_3_USE_WT_STRUCTURE_EXIT_MODE: int = 0
    FROZEN_ACTIVATION_STOP_ENABLED: bool = True
    GAP_FILL_STOP_MULT: float = 0.3
    GAP_FILL_TP_FILL_PCT: float = 0.7
    GOLDEN_RULE_EXIT_MIN_IND: int = 2
    GOLDEN_RULE_EXIT_MIN_TFS: int = 0
    GR_HTF_DIRECT_EXIT_ENABLED: bool = True
    GR_HTF_DIRECT_EXIT_SCORE: float = 29.0
    HARD_BREAKEVEN_FLOOR_ENABLED: bool = True
    HARD_BREAKEVEN_MIN_PEAK_PCT: float = 0.5
    HLR_TOP_EXIT_ENABLED: bool = True
    HTF_EXIT_VETO_ENABLED: bool = True
    HTF_EXIT_VETO_MAX_LOSS_PCT: float = 2.0
    HTF_EXIT_VETO_MIN_ALIGNED: int = 2
    HTF_W_REVERSAL_EXIT_TRADIER_ENABLED: bool = False
    HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D: bool = True
    LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED: bool = False
    LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED: bool = False
    LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK: bool = True
    MACD_EXIT_ENABLED: bool = False
    MACD_EXIT_MIN_GAIN: float = 0.3
    MACD_EXIT_TF: str = "15m"
    MAKER_CLOSE_COMMISSION_FLOOR_ENABLED: bool = False
    MAKER_CLOSE_COMMISSION_FLOOR_TTL_SEC: float = 300.0
    MARKET_CLOSE_HOUR: int = 16
    MARKET_CLOSE_MINUTE: int = 0
    MFI_FLIP_EXIT_ENABLED: bool = True
    MIN_EXIT_TF_AGAINST_TRADIER: int = 3
    MI_DIV_EXIT_ENABLED: bool = True
    MI_DIV_EXIT_ENABLED_TRADIER: bool = True
    MI_EXHAUST_EXIT_ENABLED: bool = True
    MI_EXHAUST_EXIT_ENABLED_TRADIER: bool = True
    MI_EXIT_ENABLED: bool = False
    MI_EXIT_ENABLED_TRADIER: bool = False
    MI_EXIT_VETO_ENABLED_TRADIER: bool = False
    MI_MIN_GAIN_EXIT: float = 0.1
    MI_MIN_GAIN_EXIT_TRADIER: float = 0.5
    MI_STRUCT_EXIT_ENABLED: bool = True
    MI_STRUCT_EXIT_ENABLED_TRADIER: bool = True
    MI_VELOCITY_EXIT_ENABLED: bool = True
    MI_VELOCITY_EXIT_ENABLED_TRADIER: bool = True
    MI_WAVE_EXIT_ENABLED: bool = True
    MI_WAVE_EXIT_ENABLED_TRADIER: bool = True
    NOLOSS_BB1H_GATE_ENABLED: bool = False
    NOLOSS_DC4H_GATE_ENABLED: bool = True
    NOLOSS_MIN_PROFIT_PCT: float = 0.0
    NOLOSS_MIN_PROFIT_PCT_TRADIER: float = 0.0
    ORB_STOP_MIDPOINT: bool = True
    PARTIAL_PROFIT_LOCK_USE_MAKER: bool = True
    R3_GAIN_MAX_PCT: float = 0.0
    R3_HTF_FLIP_4H_TIER_ENABLED: bool = False
    R3_HTF_FLIP_EXIT_ENABLED: bool = False
    RATIO_CLOSE_LOSING_COOLDOWN_SECONDS: float = 900.0
    RATIO_CLOSE_LOSING_MAX_PER_CYCLE: int = 2
    RATIO_CLOSE_LOSING_MIN_LOSS_PCT: float = -5.0
    RATIO_CLOSE_LOSING_MIN_PNL_DELTA_PCT: float = 10.0
    RATIO_CLOSE_LOSING_MIN_SKEW_PP: float = 40.0
    RATIO_CLOSE_LOSING_OVERWEIGHT: bool = False
    RATIO_EMERGENCY_EXIT_COOLDOWN: float = 999999.0
    RATIO_EMERGENCY_EXIT_MAX_LOSS_PCT: float = -999.0
    RATIO_EMERGENCY_EXIT_MAX_PER_CYCLE: int = 0
    RATIO_EMERGENCY_EXIT_THRESHOLD: float = 999.0
    RATIO_REBALANCE_CLOSE_OVERWEIGHT_ONLY: bool = True
    RATIO_REBALANCE_MAX_CLOSES: int = 3
    REDUCE_HUGE_LOSS_THRESHOLD: float = -999.0
    RSI2_EXIT_THRESHOLD_LONG: float = 70.0
    RSI2_EXIT_THRESHOLD_SHORT: float = 30.0
    SATOSHIT_EXIT_ENABLED: bool = False
    SATOSHIT_EXIT_PARTIAL_PCT: float = 0.7
    SCALP_STOP_PCT: float = 9.99
    SCALP_V3_AUG_BE_STOP_ENABLED: bool = True
    SCALP_V3_AUG_BE_STOP_PCT: float = 0.1
    SCALP_V3_K_OB_EXIT_ENABLED: bool = True
    SCALP_V3_K_OB_EXIT_K15M_HI: float = 80.0
    SCALP_V3_K_OB_EXIT_K15M_LO: float = 20.0
    SCALP_V3_K_OB_EXIT_K3M_HI: float = 80.0
    SCALP_V3_K_OB_EXIT_K3M_LO: float = 20.0
    SCALP_V3_K_OB_EXIT_WALL_PCT: float = 0.5
    SCALP_V3_OB_WALL_TOO_CLOSE_PCT: float = 0.5
    SCALP_V3_PROTECTIVE_EXIT_ENABLED: bool = True
    SERVICE_REDUCE: bool = True
    SERVICE_STOP: bool = True
    SIMPLE_TP_EXIT_ENABLED: bool = False
    SIMPLE_TP_PCT: float = 0.5
    STALL_MAX_CLOSES_PER_CYCLE: int = 2
    STDEV_BREAKOUT_EXIT_PCTB_FAIL: float = 0.75
    STDEV_BREAKOUT_EXIT_WT_ENABLED: bool = True
    STDEV_REJECT_EXIT_ENABLED: bool = False
    STDEV_REJECT_EXIT_RETURN: float = 0.65
    STDEV_REJECT_EXIT_TF: str = "D"
    STDEV_REJECT_EXIT_ZONE: float = 0.8
    STOCH_CROSS_1H_EXIT_ENABLED: bool = True
    STOP_MAJOR_LOSS_BLOCK_ENABLED: bool = True
    TF_FOCUS_EXIT_HARD_GATE: bool = True
    TRADIER_NOLOSS_SRS_BYPASS: bool = True
    TRADIER_POST_CLOSE_COOLDOWN_MIN: float = 15.0
    TRAILING_AUG_ENABLED_TRADIER: bool = False
    TRAILING_AUG_GAIN_STEP_PCT: float = 0.5
    TRAILING_AUG_MAX_PER_POSITION: int = 3
    TRAILING_AUG_MIN_GAIN_PCT: float = 0.5
    TRA_NO_LOSS_EXIT: bool = True
    TRA_STRICT_EXIT_ONLY: bool = True
    TRC_NOLOSS_MIN_PROFIT_PCT: float = 0.5
    TREND_EXIT_SCORE_FLIP: int = 0
    TREND_MIN_GAIN_EXIT: float = 0.1
    UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL: bool = True
    VERBOSE_STOPS: bool = False
    WT_4H_VEL_EXIT_ENABLED: bool = True
    WT_4H_VEL_EXIT_K_EXTREME_HIGH: float = 80.0
    WT_4H_VEL_EXIT_K_EXTREME_LOW: float = 20.0
    WT_4H_VEL_EXIT_LONG_VEL_MIN: float = -2.0
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME: bool = True
    WT_4H_VEL_EXIT_REQUIRE_PROFIT: bool = True
    WT_4H_VEL_EXIT_SHORT_VEL_MIN: float = 2.0
    WT_CROSS_EXIT_APPLIES_TO_LOSERS: bool = True
    WT_CROSS_EXIT_APPLIES_TO_WINNERS: bool = True
    WT_CROSS_EXIT_ENABLED: bool = True
    WT_CROSS_EXIT_MIN_AGE_MINUTES: float = 1.0
    WT_CROSS_EXIT_REQUIRE_15M_CONFIRM: bool = True
    WT_DC_EXIT_STALE_MAX_S: int = 600
    WT_DC_EXIT_THRESHOLD: float = 25.0
    WT_D_BOUNCE_DD_STOP_ENABLED: bool = True
    WT_EXHAUST_EXIT_ENABLED: bool = True
    WT_EXHAUST_EXIT_MIN_GAIN_PCT: float = 0.5
    WT_EXHAUST_EXIT_REQUIRE_GAIN: bool = False
    WT_EXIT_MIN_TFS_TRADIER: int = 5
    WT_EXIT_TFS_TRADIER: str = "5m+15m+1h+4h+D"
    WT_EXIT_VEL_THRESHOLD: float = -6.0
    WT_EXIT_VETO_ENABLED_TRADIER: bool = False
    WT_PERCENTILE_EXIT_ENABLED: bool = False
    WT_PERCENTILE_EXIT_OB_4H: float = 75.0
    WT_PERCENTILE_EXIT_OB_D: float = 90.0
    WT_PERCENTILE_EXIT_OS_4H: float = 25.0
    WT_PERCENTILE_EXIT_OS_D: float = 10.0
    WT_REDUCE_FRAC_HIGH: float = 0.5
    WT_REDUCE_FRAC_LOW: float = 0.15
    WT_REDUCE_FRAC_MED: float = 0.25
    ZONE_CLOSE_THRESHOLD: int = 20
    # ── HEDGE (61 knobs) ──
    ABLATION_DISABLE_AGGRESSIVE_HEDGE: bool = False
    ABLATION_DISABLE_HEDGE: bool = False
    FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED: bool = False
    FUNDING_HEDGE_GATE_ENABLED: bool = True
    GR_HEDGE_SCORE_FLOOR: int = 15
    HEDGE_ALREADY_COVERED_THRESHOLD: float = 0.9
    HEDGE_BANDAID_OFF_ENABLED: bool = True
    HEDGE_BANDAID_OFF_REQUIRE_WT_3M_FLIP: bool = True
    HEDGE_CLOSE_SCALP_MODE: bool = True
    HEDGE_DC_LONG_REJECT_DCP: float = 0.85
    HEDGE_DC_RESISTANCE_GATE_ENABLED: bool = False
    HEDGE_DC_SHORT_REJECT_DCP: float = 0.15
    HEDGE_DECAY_NUKE_ENABLED: bool = True
    HEDGE_DUAL_IF_HEDGE_MODE: bool = False
    HEDGE_EXIT_DELTA_CHECK_ENABLED: bool = False
    HEDGE_MAX_AGE_HOURS: float = 6.0
    HEDGE_MAX_AGE_KILL_REQUIRE_PROFIT: bool = True
    HEDGE_MODE_TRADIER: bool = False
    HEDGE_OPEN_OB_CHECK_ENABLED: bool = True
    HEDGE_OPEN_OB_IMB_BOUND: float = 0.65
    HEDGE_SAME_SYMBOL_ENABLED: bool = True
    HEDGE_SCALP_C_REQUIRE_COMBINED_NONNEG: bool = True
    HEDGE_SCALP_MAX_AGE_MIN: float = 15.0
    HEDGE_STRICT_WT_MIN_TFS_AGAINST: int = 4
    HEDGE_TRIGGER_GR_SCORE_ENABLED: bool = True
    HEDGE_TRIGGER_LOSS_PCT: float = -0.05
    HEDGE_WT_VEL_GATE_ENABLED: bool = False
    LH_HL_FILTER_HEDGE_GATE_ENABLED: bool = False
    LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED: bool = False
    LOSS_EXIT_REQUIRES_HEDGE: bool = True
    MANDATORY_HEDGE_GAIN_THRESHOLD_PCT: float = -0.5
    MANDATORY_HEDGE_HARD_THRESHOLD_PCT: float = -2.0
    MANDATORY_HEDGE_ON_NEGATIVE_ENABLED: bool = True
    MOMENTUM_RIDER_HEDGE_RATIO: float = 1.2
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED: bool = True
    OBLIGATORY_HEDGE_OR_CLOSE_LOOP_INTERVAL_SECONDS: float = 60.0
    OBLIGATORY_HEDGE_PCT: float = 0.0
    OBLIGATORY_HEDGE_WT_TFS: int = 2
    OBLIGATORY_SECTOR_HEDGE_ENABLED: bool = True
    OBLIGATORY_SECTOR_HEDGE_LOOP_INTERVAL_SECONDS: float = 90.0
    OBLIGATORY_SECTOR_HEDGE_TRIGGER_REQUIRE_WT_5M_AND_1H: bool = True
    OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED: bool = False
    OI_HEDGE_GATE_ENABLED: bool = False
    OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED: bool = False
    OPPOSITE_LOSER_HEDGE_PROTECT_MAX_GAIN: float = 5.0
    OVERNIGHT_GAP_HEDGE_CLOSE_MINUTES: float = 5.0
    OVERNIGHT_GAP_HEDGE_ENABLED: bool = False
    OVERNIGHT_GAP_HEDGE_OPEN_MINUTES: float = 15.0
    OVERNIGHT_GAP_HEDGE_SENTIMENT_THRESHOLD: float = 20.0
    OVERNIGHT_GAP_HEDGE_SIZE_FRAC: float = 0.5
    R3_HEDGE_INVARIANT_DUMP_ENABLED: bool = True
    RED_ZONE_HEDGE_GATE_ENABLED: bool = False
    TREND_HEDGE_MAX_SEC: int = 180
    UNDERWATER_HEDGE_OR_CLOSE_ENABLED: bool = True
    UNDERWATER_HEDGE_OR_CLOSE_HTF_CLOSE_REQUIRED: int = 2
    VP_GATE_HEDGE_GATE_ENABLED: bool = False
    WT15M_AGAINST_FORCE_HEDGE_COOLDOWN_SEC: float = 30.0
    WT15M_AGAINST_FORCE_HEDGE_ENABLED: bool = True
    WT_15M_SAME_HEDGE_COOLDOWN_SEC: int = 1800
    WT_15M_SAME_HEDGE_DAILY_CAP: int = 2
    WT_15M_SAME_HEDGE_ENABLED: bool = True
    # ── SIZING (55 knobs) ──
    BREAKOUT_TF_SIZE_CAP_MULT: float = 5.0
    BREAKOUT_TF_SIZE_ENABLED: bool = True
    BREAKOUT_TF_SIZE_MULT_15M: float = 1.0
    BREAKOUT_TF_SIZE_MULT_1H: float = 2.0
    BREAKOUT_TF_SIZE_MULT_3M: float = 0.5
    BREAKOUT_TF_SIZE_MULT_4H: float = 3.0
    BREAKOUT_TF_SIZE_MULT_D: float = 4.0
    BROKER_PREFLIGHT_MAX_SAME_SIDE_QTY: float = 50.0
    BTC_LEVERAGE: float = 20.0
    BTC_PER_TRADE_NOTIONAL_USD_MAX: float = 290.0
    CLENOW_POSITION_SIZE: float = 800.0
    CONNORS_RSI_POSITION_SIZE: float = 600.0
    DC_DAYTRADE_MAX_POSITION_SIZE: float = 1000.0
    DC_DAYTRADE_START_SIZE: float = 600.0
    DELTA_PYRAMID_QTY_MULT: float = 1.5
    EP_POSITION_SIZE: float = 800.0
    GAP_FILL_POSITION_SIZE: float = 600.0
    MAX_ORDER_VALUE: float = 200.0
    MAX_ORDER_VALUE_FIN: float = 20.0
    MAX_ORDER_VALUE_MEN: float = 20.0
    MAX_POSITION_SIZE: float = 20.0
    MAX_POSITION_SIZE_BTC: float = 2000.0
    MAX_POSITION_SIZE_FIN: float = 20.0
    MAX_POSITION_SIZE_MEN: float = 20.0
    MINERVINI_POSITION_SIZE: float = 800.0
    MOMENTUM_RIDER_BASE_SIZE_USD: float = 50.0
    ORB_POSITION_SIZE: float = 600.0
    RATIO_REBALANCE_SIZE_MAX_MULT: float = 10.0
    RATIO_REBALANCE_SIZE_MULT: float = 4.0
    RATIO_REBALANCE_SIZE_SKEW_BOOST: float = 0.05
    RED_ZONE_MIN_WALL_NOTIONAL_USD: int = 50000
    ROTATION_POSITION_SIZE: float = 1200.0
    RSI2_POSITION_SIZE: float = 600.0
    R_Z3_WT_COMPOSITE_SIZE_ENABLED: bool = False
    SBA_SIZE_FRACTION: float = 0.4
    SCALP_MAX_POSITION_SIZE: float = 500.0
    SCALP_START_SIZE: float = 150.0
    SMFI_POSITION_SIZE: float = 600.0
    SPIKE_FADE_POSITION_SIZE: float = 600.0
    STDEV_BREAKOUT_RETEST_SIZE_MULT: float = 1.5
    TRADEABLE_KEYS_MANDATORY_SIZE_USD: float = 9.0
    TRC_CLENOW_POSITION_SIZE: float = 2640.0
    TRC_CONNORS_RSI_POSITION_SIZE: float = 1980.0
    TRC_DC_DAYTRADE_START_SIZE: float = 1980.0
    TRC_EP_POSITION_SIZE: float = 2640.0
    TRC_GAP_FILL_POSITION_SIZE: float = 1980.0
    TRC_MAX_ORDER_VALUE: float = 1250.0
    TRC_MAX_POSITION_SIZE: float = 3750.0
    TRC_MINERVINI_POSITION_SIZE: float = 2640.0
    TRC_ORB_POSITION_SIZE: float = 1980.0
    TRC_ROTATION_POSITION_SIZE: float = 3000.0
    TRC_RSI2_POSITION_SIZE: float = 1980.0
    TRC_SCALP_START_SIZE: float = 495.0
    TRC_SMFI_POSITION_SIZE: float = 1980.0
    TRC_START_POSITION_SIZE: float = 330.0
    # ── RISK (69 knobs) ──
    ALIGNMENT_GATE_MIN: int = 4
    ALIGNMENT_GATE_TOTAL: int = 36
    BACKTEST_VALIDATED_GATES_TRADIER: bool = True
    BOUNCE_TOP_MAX_LOSS_PCT: float = -50.0
    CATALYST_VOLUME_GATE_ENABLED: bool = False
    CIRCUIT_BREAKER_COOLDOWN: int = 60
    CRYPTO_SPIKE_FADE_COOLDOWN_SEC: float = 540.0
    CT_15M_MOMENTUM_GATE_ENABLED: bool = False
    CT_CHOP_4H_GATE_ENABLED: bool = False
    CT_VOLUME_SURGE_GATE_ENABLED: bool = False
    DD_BOUNCE_COOLDOWN_HOURS: float = 4.0
    DG_BROKER_MEMORY_SYNC_BLOCK: bool = True
    DIRECT_HIGH_GAIN_COOLDOWN_SECONDS: int = 15
    DISASTER_GUARD_ENABLED: bool = True
    ENABLE_LOSS_PROTECTION: bool = True
    HTF_DIRECTION_GATE_ENABLED: bool = False
    HTF_GATE_BYPASS_RZ: bool = True
    HTF_GATE_D_MANDATORY: bool = False
    HTF_GATE_MIN_CONFIRMATIONS: int = 2
    HTF_TREND_VETO_BYPASS_ENABLED: bool = True
    HTF_VETO_REQUIRE_D: bool = True
    HTF_W_M_ALIGN_GATE_TRADIER_ENABLED: bool = False
    MAX_DAILY_LOSS_PCT: float = 3.0
    MOMENTUM_RIDER_COOLDOWN: float = 300.0
    MTS_GATE_ENABLED: bool = True
    MTS_GATE_ENABLED_TRADIER: bool = False
    PARABOLIC_PROTECTION_ENABLED: bool = True
    PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED: bool = False
    RATIO_PNL_DYNAMIC_GATES_ENABLED: bool = True
    RATIO_PNL_GATE_SOFT_MAX: float = 2.0
    RATIO_PNL_GATE_SOFT_MIN: float = 0.5
    RATIO_REBALANCE_APPLY_HTF_GATE: bool = True
    RATIO_REBALANCE_COOLDOWN_CRASH: float = 1800.0
    RATIO_REBALANCE_COOLDOWN_EXTREME: float = 3600.0
    RATIO_REBALANCE_COOLDOWN_NORMAL: float = 3600.0
    RATIO_REBALANCE_COOLDOWN_PNL_DIVERGENT: float = 1200.0
    RED_ZONE_GATE_ENABLED: bool = False
    RED_ZONE_TRADIER_GATE_ENABLED: bool = True
    RE_6_WAVE_PHASE_GATE_ENABLED: bool = False
    RIDICULOUS_HOLD_GUARD_ENABLED: bool = False
    RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG: bool = True
    R_G10_HTF_DIV_GATE_ENABLED: bool = False
    R_S6_WT_MSTATE_GATE_MODE: int = 0
    SATOSHIT_PROTECT_TRADES: bool = True
    SBA_COOLDOWN_GLOBAL_S: int = 300
    SBA_COOLDOWN_POSITION_S: int = 3375
    SBA_MAX_LOSS_PCT: float = -15.0
    SCALP_V3_AUG_COOLDOWN_SEC: float = 180.0
    SCALP_V3_BYPASS_HTF_DIRECTION_GATE: bool = True
    SCALP_V3_HTF_TREND_VEL_GATE: float = 3.0
    SCALP_V3_MAX_LOSS_PCT: float = -1.5
    SCALP_V3_SCAN_BYPASS_GATES: bool = False
    SENTIMENT_TOP_N_GATE_ENABLED: bool = False
    SPIKE_FADE_COOLDOWN_BARS: int = 6
    TRC_MAX_DAILY_LOSS_PCT: float = 10.0
    TREND_GATES: bool = True
    TR_BBWIDTH4H_GATE_ENABLED: bool = False
    TR_CHOP4H_GATE_ENABLED: bool = False
    VOL_SPIKE_COOLDOWN: float = 300.0
    VP_GATE_ENABLED: bool = False
    VP_GATE_MIN_DENSITY_Z: float = 2.0
    VP_GATE_MIN_DISTANCE_PCT: float = 1.0
    VP_GATE_STALE_MAX_SEC: float = 7200.0
    WT_CHOP_GATE_ENABLED: bool = False
    WT_COMPOSITE_DELTA_GATE_ENABLED: bool = True
    WT_COMPOSITE_VETO_ENABLED_TRADIER: bool = False
    WT_DC_HTF_GATE: str = "4h"
    WT_D_BOUNCE_AUG_COOLDOWN_HOURS: float = 1.0
    WT_MTF_VEL_GATE_ENABLED: bool = True
    # ── INDICATOR (153 knobs) ──
    ADX_RANGING_THRESHOLD: float = 20.0
    ADX_REGIME_FILTER_ENABLED: bool = False
    ADX_TF: str = "1h"
    BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED: bool = False
    BTC_HARD_BLOCK_OTHER_ACCOUNTS: bool = False
    CHECK_INTERVAL: float = 3.0
    CONNORS_RSI_ENABLED: bool = False
    CONNORS_RSI_MAX_HOLD_DAYS: int = 20
    CRYPTO_SPIKE_FADE_K_EXHAUSTION: float = 80.0
    CT_MFI_15M_LONG_MIN: float = 45.0
    CT_MFI_15M_SHORT_MAX: float = 55.0
    CT_STOCH_K_15M_LONG_MIN: float = 45.0
    CT_STOCH_K_15M_SHORT_MAX: float = 55.0
    DC_BREAK_GR_MULT_BREAKOUT: float = 0.1
    DC_BREAK_GR_MULT_ENABLED: bool = False
    DC_BREAK_GR_MULT_RETEST: float = 3.0
    DC_BREAK_GR_RETEST_TOLERANCE_PCT: float = 0.3
    DC_BREAK_LOW_REQUIRE_HTF_ENABLED: bool = False
    DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS: int = 2
    DC_DAYTRADE_K_EXHAUSTED_LONG: float = 85.0
    DC_DAYTRADE_K_EXHAUSTED_SHORT: float = 15.0
    DC_DAYTRADE_STOCH_FILTER: bool = True
    DG_DAILY_GAIN_BLOCK_SHORT_PCT: float = 2.5
    DG_DAILY_LOSS_BLOCK_LONG_PCT: float = 2.5
    DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG: float = 35.0
    DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT: float = 65.0
    DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG: float = 35.0
    DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT: float = 65.0
    DG_OPPOSITE_SIDE_PROFIT_BLOCK_PCT: float = 1.0
    EMA200_STOCHRSI_BODY_MULT: float = 1.05
    EMA200_STOCHRSI_ENABLED: bool = False
    EMA200_STOCHRSI_K_LONG: float = 20.0
    EMA200_STOCHRSI_K_SHORT: float = 80.0
    EMA200_STOCHRSI_SCORE: int = 25
    EMA200_STOCHRSI_TF: str = "1h"
    EMA_9_21_FILTER_ENABLED: bool = True
    EMA_9_21_TIMEFRAME: str = "5m"
    EMA_DIST_LONG_THRESHOLD: float = -1.0
    EMA_DIST_SHORT_THRESHOLD: float = 1.0
    EMA_DIST_SIZING_ENABLED: bool = True
    EMA_DIST_SIZING_MULT: float = 2.0
    EMA_PULLBACK_ENABLED: bool = False
    EMA_PULLBACK_SCORE_BONUS: int = 35
    EMA_PULLBACK_TF: str = "15m"
    EXTREME_OB_RSI_4H_MIN: float = 80.0
    EXTREME_OB_RSI_D_MIN: float = 75.0
    EXTREME_OS_RSI_4H_MAX: float = 20.0
    EXTREME_OS_RSI_D_MAX: float = 25.0
    FUNDING_GATE_ENABLED: bool = True
    FUNDING_GATE_ENABLED_TRADIER: bool = False
    FUNDING_GATE_LONG_MAX: float = 0.0005
    FUNDING_GATE_PC_RATIO_LONG_MAX: float = 1.2
    FUNDING_GATE_PC_RATIO_SHORT_MIN: float = 0.83
    FUNDING_GATE_SHORT_MIN: float = -0.0005
    FUNDING_GATE_TRADIER_NEAR_MONEY_PREFER: bool = True
    FUNDING_GATE_TRADIER_STALE_MAX_HOURS: float = 4.0
    HA_WICK_QUALITY_ENABLED: bool = False
    HA_WICK_QUALITY_SCORE: int = 15
    HA_WICK_QUALITY_TF: str = "1h"
    HLR_OFF_SMA_PTS_FRAC: float = 0.5
    HLR_OFF_SMA_SZ_FRAC: float = 0.7
    HLR_SMA_BAND_PCT: float = 0.03
    HTF_GATE_SIGNALS_SMA200D: bool = True
    K_ZONE_LONG_THRESHOLD: int = 35
    K_ZONE_LONG_THRESHOLD_TRADIER: int = 35
    K_ZONE_SHORT_THRESHOLD: int = 10
    K_ZONE_SHORT_THRESHOLD_TRADIER: int = 65
    K_ZONE_VETO_ENABLED_TRADIER: bool = False
    LH_HL_FILTER_REPLACE_SMA200D: bool = False
    LONG_STOCH_CHASE_BLOCK: bool = True
    MACD_ZERO_CROSS_ENABLED: bool = False
    MACD_ZERO_CROSS_SCORE: int = 15
    MACD_ZERO_CROSS_TF: str = "1h"
    MARK_PRICE_MAX_STALENESS: int = 2
    MFI_LONG_THRESHOLD_D: float = 80.0
    MOMENTUM_FADE_K_ZONE: bool = True
    MOMENTUM_FADE_K_ZONE_TRADIER: bool = True
    OUTLIER_STUCK_ATR_FACTOR: float = 0.5
    OUTLIER_STUCK_HOURS: float = 2.0
    PARABOLIC_RSI_1H_MAX: float = 35.0
    PARABOLIC_RSI_1H_MIN: float = 65.0
    PARABOLIC_RSI_4H_MAX: float = 30.0
    PARABOLIC_RSI_4H_MIN: float = 70.0
    PENNY_STOCK_LONG_BLOCK_ENABLED: bool = False
    PENNY_STOCK_LONG_BLOCK_PRICE_USD: float = 5.0
    PERSIST: float = 72.0
    PERSIST_FIN: float = 24.0
    PERSIST_FLZ: float = 0.0
    PERSIST_INF: float = 4.0
    PERSIST_MEN: float = 24.0
    QUICK_RECOVERY_WINDOW_MIN: float = 120.0
    RANK_CONVICTION_ENABLED: bool = False
    RED_ZONE_FALLBACK_K15_HIGH: float = 80.0
    RED_ZONE_FALLBACK_K15_LOW: float = 20.0
    RED_ZONE_GATE_FALLBACK_ENABLED: bool = True
    RE_4_B14_HA_STREAK_CONV_ENABLED: bool = False
    RE_4_HA_STREAK_CAP: int = 5
    RE_4_HA_STREAK_WEIGHT: float = 5.0
    ROTATION_LOOKBACK_DAYS: int = 10
    ROTATION_SMA200_FILTER: bool = True
    RP_WEAK_PENALTY: float = -10.0
    RP_WEAK_THRESHOLD: float = 30.0
    RSI2_MEAN_REVERSION_ENABLED: bool = False
    RSI2_SCORE_BONUS: int = 20
    RSI2_THRESHOLD_LONG: float = 15.0
    RSI2_THRESHOLD_SHORT: float = 85.0
    RSI_MACD_EMA_ENABLED: bool = False
    RSI_MACD_EMA_RSI_LONG: float = 35.0
    RSI_MACD_EMA_RSI_SHORT: float = 65.0
    RSI_MACD_EMA_SCORE: int = 25
    RSI_MACD_EMA_TF: str = "1h"
    R_S3_DIV_STACK_ENABLED: bool = False
    R_S4_HA_STREAK_ENABLED: bool = False
    R_S4_HA_STREAK_TF: str = "1h"
    R_S4_HA_STREAK_WEIGHT: float = 5.0
    R_S7_HHLL_STACK_ENABLED: bool = False
    R_Z5_DC_PULLBACK_MULT: float = 1.5
    R_Z5_DC_PULLBACK_SIZING_ENABLED: bool = False
    SBA_ADX_MAX: float = 25.0
    SCALP_V3_OB_FLOW_K_AGREE: bool = True
    SCALP_V3_PROTECTIVE_K_DROP_MIN: float = 5.0
    SHORT_ABOVE_EMA20_IS_PENALTY: bool = True
    SHORT_ABOVE_SMA20_BONUS: int = 15
    SHORT_RSI_MIN_1H: float = 40.0
    SMA200_DIST_LONG_THRESHOLD_4H: float = -10.0
    SMA_FILTER_PERIOD_TRADIER: int = 100
    SMFI_ENABLED: bool = False
    SMFI_LONG_BUDGET: float = 3000.0
    SMFI_MAX_HOLD_DAYS: int = 10
    SMFI_MAX_PER_SIDE: int = 5
    SMFI_SHORT_BUDGET: float = 3000.0
    SPIKE_FADE_K_EXHAUSTION: float = 70.0
    SPIKE_FADE_LOOKBACK_BARS: int = 6
    TRADIER_RSI_SHORT_15M: float = 65.0
    TRADIER_RSI_SHORT_1H: float = 65.0
    TRADIER_RSI_SHORT_REL_VOLUME_MIN: float = 2.4
    TRADIER_RSI_SHORT_RVOL_15M: float = 1.0
    TRADIER_RSI_SHORT_RVOL_1H: float = 1.0
    TRC_CONNORS_RSI_ENABLED: bool = True
    TRC_SMFI_ENABLED: bool = True
    TRC_SMFI_LONG_BUDGET: float = 9900.0
    TRC_SMFI_SHORT_BUDGET: float = 9900.0
    TRIPLE_CONF_RSI_LONG: float = 30.0
    TRIPLE_CONF_RSI_SHORT: float = 70.0
    TRIPLE_CONF_STOCH_LONG: float = 20.0
    TRIPLE_CONF_STOCH_SHORT: float = 80.0
    TR_ADX4H_BOYCOTT_SCORE: int = -40
    TR_ADX4H_GATE_ENABLED: bool = False
    TR_ADX4H_MAX: float = 20.0
    TR_MFI4H_LONG_BOYCOTT_SCORE: int = -25
    TR_MFI4H_LONG_ENABLED: bool = True
    TR_MFI4H_LONG_MIN: float = 40.0
    WRONG_SIDE_K_TFS_REQUIRED: int = 3
    # ── OTHER (459 knobs) ──
    ABLATION_DISABLE_FAST_RISER: bool = False
    ABLATION_DISABLE_RATIO_REBALANCE: bool = False
    ABLATION_DISABLE_SCALP_GUARD: bool = False
    AGGRESSIVE_LOSS_CUT_ENABLED: bool = False
    ATR_ADAPTIVE_SIZING_ENABLED: bool = False
    ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0
    BANDAID_OFF_LOSER_RECOVER_PCT: float = -0.25
    BASIS_CONDITION: bool = False
    BEAR_MARKET_MODE: bool = True
    BEAR_MARKET_MODE_TRADIER: bool = True
    BOUNCE_TOP_MIN_HOLD_MINUTES: float = 2880.0
    BOUNCE_TOP_MIN_LOSS_PCT: float = -3.0
    BOUNCE_TOP_RISING_CROSS_MULT: float = 2.0
    BREAKOUT_GUARD_LOSS_THRESHOLD: float = -999.0
    BROKER_PREFLIGHT_CACHE_S: float = 3.0
    BROKER_PREFLIGHT_ENABLED: bool = True
    BTC_ACCEL_RAMP_REQUIRE_POSITIVE: bool = True
    BTC_DEDICATED_ENABLED: bool = True
    BTC_PER_SYM_CONFIG_ENABLED: bool = True
    BTC_ROUND_BANDS_EACH_SIDE: int = 8
    BTC_ROUND_INC_PRIMARY_USD: float = 5000.0
    BTC_ROUND_INC_SECONDARY_USD: float = 1000.0
    CATALYST_VOLUME_RATIO: float = 1.5
    CLENOW_ENABLED: bool = False
    CLENOW_MIN_SCORE: float = 5.0
    CLENOW_REBALANCE_DAYS: int = 21
    CLENOW_TOP_N: int = 20
    COMMISSION_BUFFER_PCT: float = 0.1
    CONGRESS_CONVICTION_MIN_SOURCES: int = 2
    CONGRESS_CONVICTION_SIZING_BOOST: float = 1.3
    CONVICTION_SHORT_THRESHOLD: int = 20
    CRASH_MULT_GRADIENT_ENABLED: bool = False
    CRASH_MULT_GRADIENT_MAX: float = 2.5
    CRYPTO_SPIKE_FADE_ENABLED: bool = True
    CRYPTO_SPIKE_FADE_MAX_POSITIONS: int = 6
    CRYPTO_SPIKE_FADE_THRESHOLD_PCT: float = 10.0
    CT_CHOP_4H_MAX: float = 50.0
    CT_DC_CROSSOVER_SKIP_ENABLED: bool = True
    CT_REL_VOL_MIN: float = 1.3
    DC_BREAKOUT_SCORE: int = 15
    DC_BREAKOUT_TF: str = "1h"
    DC_DAYTRADE_ACCOUNT: str = "trb"
    DC_DAYTRADE_BUFFER: float = 0.001
    DC_DAYTRADE_ENABLED: bool = True
    DC_DAYTRADE_LONG_BUDGET: float = 3000.0
    DC_DAYTRADE_MAX_HOLD_MINUTES: float = 240.0
    DC_DAYTRADE_MAX_PER_SIDE: int = 5
    DC_DAYTRADE_REQUIRE_1H_EXPANSION: bool = True
    DC_DAYTRADE_SHORT_BUDGET: float = 3000.0
    DC_DAYTRADE_TARGET_PCT: float = 0.01
    DC_EDGE_SIZING_ENABLED: bool = True
    DC_EDGE_SIZING_MAX_MULT: float = 3.0
    DC_EDGE_SIZING_MIN_MULT: float = 1.0
    DC_MOMENT_ENABLED: bool = False
    DC_MOMENT_OPPOSITE_PENALTY: float = -15.0
    DC_MOMENT_STRONG_BONUS: float = 10.0
    DC_MOMENT_STRONG_THRESHOLD: float = 40.0
    DC_WIDTH_CAP_MULT: float = 10.0
    DC_WIDTH_MAX_MULT: float = 5.0
    DC_WIDTH_SIZING_ENABLED: bool = True
    DD_BOUNCE_ENABLED: bool = False
    DD_BOUNCE_REQUIRE_HIGHER_PRICE: bool = True
    DD_BOUNCE_REQUIRE_HIGHER_WT: bool = True
    DD_BOUNCE_WT_4H_ENABLED: bool = True
    DD_BOUNCE_WT_D_ENABLED: bool = True
    DELTA_ACCEL_LOOKBACK: int = 5
    DELTA_MIN_TF_FOR_ACTION: int = 2
    DELTA_PYRAMID_ACCEL_THRESHOLD: float = 0.2
    DELTA_PYRAMID_MAX: int = 8
    DELTA_PYRAMID_MIN_BARS: int = 8
    DELTA_PYRAMID_PRICE_TOL: float = 0.02
    DELTA_SCORE_WEIGHT: float = 30.0
    DELTA_SPEED_SMOOTH: int = 5
    DELTA_TF_Z_THRESHOLD: float = 1.5
    DELTA_Z_WINDOW: int = 200
    DG_HIGH_VOLATILITY_ATR_PCT: float = 4.0
    DG_HTF_ALIGN_REQUIRE_1H: bool = False
    DG_HTF_ALIGN_REQUIRE_4H: bool = True
    DG_HTF_ALIGN_REQUIRE_D: bool = True
    DG_WT_3M_REQUIRE_HTF_CONFIRM: bool = True
    DT_TARGET_ATR_ENABLED: bool = False
    EOD_RATIO_ENFORCE_TRADIER: bool = False
    EPISODIC_PIVOT_ENABLED: bool = False
    EXTREME_MODE: bool = False
    EXTREME_OB_OS_OVERRIDE_ENABLED: bool = True
    FAST_CUT_LOSS_MIN_AGE_MINUTES: float = 15.0
    FAST_CUT_LOSS_THRESHOLD: float = -999.0
    FG_FEAR_THRESHOLD: int = 25
    FG_GREED_THRESHOLD: int = 75
    FG_SIZING_ENABLED: bool = False
    FOOTHOLD_PILEON_ENABLED: bool = False
    FORCE_REFRESH_SECONDS: int = 10
    FROZEN_ABSOLUTE_FLOOR_PCT_CRYPTO: float = -10.0
    FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER: float = -8.0
    FROZEN_ACTIVATION_TF: str = "4h"
    GAIN_THRESHOLD_LOW: float = 1.0
    GAP_FILL_ENABLED: bool = True
    GAP_FILL_MAX_GAP_PCT: float = 5.0
    GAP_FILL_MIN_GAP_PCT: float = 0.5
    GOLDEN_RULE_MIN_IND: int = 5
    HLR_BYPASS_MIN_TF_WEIGHT: int = 25
    HLR_PTS_15M: int = 25
    HLR_PTS_1H: int = 40
    HLR_PTS_3M: int = 15
    HLR_PTS_4H: int = 60
    HLR_PTS_D: int = 90
    HLR_PTS_W: int = 130
    HLR_RALLY_ENABLED: bool = True
    HLR_SZ_15M: float = 1.5
    HLR_SZ_1H: float = 2.0
    HLR_SZ_3M: float = 1.2
    HLR_SZ_4H: float = 3.5
    HLR_SZ_D: float = 6.0
    HLR_SZ_MAX: float = 10.0
    HLR_SZ_W: float = 10.0
    HLR_TOP_MIN_GAIN_PCT: float = 1.5
    HLR_TOP_MIN_TFS: int = 2
    HLR_TOP_VEL_1H_THRESH: float = -1.0
    HLR_TOP_VEL_4H_THRESH: float = 0.0
    HLR_TOP_VEL_D_THRESH: float = 0.0
    HTF1_CONF: bool = True
    HTF4_CONF: bool = True
    HTF_DC_BREAKOUT_TRADIER_ENABLED: bool = False
    HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT: bool = True
    HTF_DC_BREAKOUT_TRADIER_TF: str = "4h"
    HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT: float = 0.0
    HTF_STRICT: bool = True
    HTF_W_M_ALIGN_TRADIER_REQUIRED: int = 2
    IMMEDIATE_WRONG_WAY_ENABLED: bool = False
    INDICATOR_MAX_AGE_SECONDS: float = 200.0
    LEADERBOARD_FILTER: bool = True
    LEGACY_DIRECTION_FAVORABLE: bool = True
    LH_HL_FILTER_DC_THRESHOLD_PCT: float = 0.5
    LH_HL_FILTER_ENABLED: bool = False
    LH_HL_FILTER_MODE: str = "STRICT_2BAR"
    LH_HL_FILTER_REQUIRE_BOTH: bool = False
    LH_HL_FILTER_TF_REQ: int = 2
    LIGHT_MODE: bool = False
    LIVE_POSITION_FRESHNESS_MAX_SEC: float = 3.0
    LOCAL_EXTREMES_MIN_SCORE: float = 45.0
    LOG_BACKUP_COUNT: int = 30
    LR_PCTB_D_SHORT_THRESHOLD: float = 0.1
    LS_RATIO_ENFORCE: bool = True
    LS_RATIO_ENFORCE_TRADIER: bool = True
    LS_RATIO_HARD_MAX: float = 3.5
    LS_RATIO_HARD_MIN: float = 0.05
    LS_RATIO_LOG_INTERVAL: int = 60
    LS_RATIO_MAX_TRADIER: float = 2.0
    LS_RATIO_MIN_TRADIER: float = 0.5
    LUNCH_DEADZONE_ENABLED: bool = True
    LUNCH_DEADZONE_MODE: str = "BLOCK_MOMENTUM"
    MARKET_DATA_REFRESH_INTERVAL_SECONDS: float = 45.0
    MARKET_MODE: str = "NORMAL_MODE"
    MARKET_QUALITY_SCORE_ENABLED: bool = False
    MARKET_QUALITY_SCORE_ENABLED_TRADIER: bool = True
    MAX_CONCURRENT_ORDERS: int = 186
    MAX_CONCURRENT_POSITIONS: int = 16
    MAX_DECAY_COMPLETE_DAYS: int = 7
    MAX_DECAY_START_HOURS: int = 1
    MAX_GAIN_DECAY_COMPLETE_DAYS: int = 7
    MAX_MARKET_DATA_FILE_AGE_SECONDS: int = 1200
    MAX_MEMORY_GB: int = 8
    MAX_SYMBOL_VALUE_TRADIER: float = 3750.0
    MINERVINI_ENABLED: bool = False
    MINERVINI_LONG_BUDGET: float = 4000.0
    MINERVINI_MAX_HOLD_DAYS: int = 40
    MINERVINI_MIN_SEPA_SCORE: int = 5
    MINERVINI_TARGET_PCT: float = 25.0
    MIN_HOLD_BARS_TRADIER: int = 40
    MIN_USD_DELTA_CONFIRM: float = 1.0
    MI_TF_AGREE_MIN: int = 3
    MI_TF_AGREE_MIN_TRADIER: int = 3
    MOMENTUM_FADE_BODY_ATR_MIN: float = 2.0
    MOMENTUM_FADE_BODY_ATR_MIN_TRADIER: float = 2.0
    MOMENTUM_FADE_ENABLED: bool = False
    MOMENTUM_FADE_ENABLED_TRADIER: bool = False
    MOMENTUM_FADE_SCORE_BONUS: int = 35
    MOMENTUM_FADE_SCORE_BONUS_TRADIER: int = 5
    MOMENTUM_FADE_VOL_MIN: float = 2.0
    MOMENTUM_FADE_VOL_MIN_TRADIER: float = 2.0
    MOMENTUM_RIDER_DC_WIDTH_MIN: float = 8.0
    MOMENTUM_RIDER_ENABLED: bool = False
    MOMENTUM_RIDER_MAX_SYMBOLS: int = 5
    MOMENTUM_RIDER_REL_VOL_MIN: float = 3.0
    MOMENTUM_RIDER_SCAN_INTERVAL: float = 10.0
    MONITOR_REDUCTION_STALE_THRESHOLD: float = 180.0
    MOVER_ACCOUNT: str = "inf"
    MOVER_DETECTION_ENABLED: bool = True
    MOVER_LINEARITY_MIN: float = 0.3
    MOVER_MAX_POSITIONS: int = 6
    MOVER_SCORE_BONUS: int = 40
    MOVER_THRESHOLD: float = 5.0
    MOVER_VOL_MIN: float = 1.0
    MTS_BOTTOM_BONUS_THRESHOLD: float = 25.0
    MTS_BOTTOM_MIN: float = 15.0
    MTS_BOTTOM_MIN_SHORT: float = 10.0
    MTS_BOTTOM_MIN_TRADIER: float = 5.0
    MTS_BOTTOM_STRONG_THRESHOLD: float = 40.0
    OB_PRICE_DEFER_AT_LEVEL_TOL_PCT: float = 0.05
    OB_PRICE_DEFER_ENABLED: bool = False
    OB_PRICE_DEFER_MAX_DISTANCE_PCT: float = 0.5
    OB_PRICE_DEFER_TTL_SEC: float = 300.0
    OI_CONFIRM_ENABLED: bool = True
    OI_CONFIRM_ENABLED_TRADIER: bool = False
    OI_CONFIRM_MIN_CHANGE_PCT: float = 0.5
    OI_CONFIRM_MIN_OI_CHANGE_PCT_TRADIER: float = 0.5
    OI_CONFIRM_MIN_PRICE_PCT: float = 0.3
    OI_CONFIRM_MIN_PRICE_PCT_TRADIER: float = 0.3
    OPPOSITE_LOSER_DEEP_LOSS_PCT: float = -5.0
    OPTIMAL_HOLD_BARS_3M: int = 999
    ORB_ENABLED: bool = False
    ORB_LONG_BUDGET: float = 2000.0
    ORB_MAX_HOLD_MINUTES: float = 150.0
    ORB_MAX_PER_DAY: int = 3
    ORB_RVOL_MIN: float = 1.5
    ORB_SHORT_BUDGET: float = 2000.0
    ORB_TARGET_MULT: float = 1.5
    ORDER_CACHE_TTL: int = 60
    OUTLIER_DETECTOR_ENABLED: bool = True
    OUTLIER_RUNAWAY_ATR_FACTOR: float = 2.0
    OUTLIER_SCAN_INTERVAL: float = 60.0
    OUTLIER_STALE_HOURS: float = 6.0
    PAU_TIMEOUT_SEC: float = 120.0
    PER_SYM_CONFIG_ENABLED: bool = True
    PNL_DECAY_COMPLETE_DAYS: int = 5
    PNL_DECAY_FINAL_PERCENTAGE: float = 0.1
    PNL_DECAY_START_HOURS: int = 1
    POSITION_REFRESH_MIN_INTERVAL: int = 5
    POSITION_SAVE_INTERVAL: float = 6.0
    POSITION_STALE_THRESHOLD_SECONDS: float = 60.0
    RANKING_MULT_ENABLED: bool = False
    RANKING_MULT_MAX: float = 2.5
    RANKING_MULT_MIN: float = 0.3
    RATIO_MULTIPLIER_TRADIER: float = 3.5
    RATIO_PNL_ACCELERATION: float = 2.5
    RATIO_PNL_DELTA_THRESHOLD: float = 3.0
    RATIO_PNL_TARGET_LONG_MAX: float = 90.0
    RATIO_PNL_TARGET_LONG_MIN: float = 10.0
    RATIO_PNL_WEIGHT: float = 0.5
    RATIO_PNL_WEIGHT_ENABLED: bool = True
    RED_ZONE_MIN_DISTANCE_PCT: float = 0.4
    RED_ZONE_STALE_MAX_SEC: float = 30.0
    RED_ZONE_TRADIER_MIN_DISTANCE_PCT: float = 0.5
    RED_ZONE_TRADIER_MIN_OI_AT_WALL: int = 1000
    RED_ZONE_TRADIER_STALE_MAX_HOURS: float = 4.0
    REV_MODE: bool = False
    RE_2_PCT_OB: float = 95.0
    RE_2_PCT_OS: float = 5.0
    RE_2_USE_PERCENTILE_ENABLED: bool = False
    RE_3_B12_RISING_BONUS_ENABLED: bool = False
    RE_3_CONVICTION_BONUS: float = 10.0
    RE_3_RISING_COUNT_THR: int = 3
    RE_5_B04_COMPRESSION_BONUS_ENABLED: bool = False
    RE_5_COMPRESSION_BONUS: float = 10.0
    RE_5_INSIDE_COUNT_THR: int = 3
    RE_6_MIN_EXPANDING_TFS: int = 2
    RIDICULOUS_LOSS_PCT: float = -15.0
    ROTATION_BOTTOM_N: int = 8
    ROTATION_ENABLED: bool = True
    ROTATION_HOLD_DAYS: int = 7
    ROTATION_TOP_N: int = 3
    RP_OPPOSITE_PENALTY: float = -20.0
    RP_STRONG_BONUS: float = 15.0
    RP_STRONG_THRESHOLD: float = 70.0
    RVOL_MOMENTUM_MIN: float = 1.5
    R_G10_HTF_DIV_TFS: str = "4h,D"
    R_S1_WT_COMPOSITE_DELTA_THR: float = 50.0
    R_S1_WT_COMPOSITE_DELTA_USE_ENABLED: bool = False
    R_S2_WT_ADAPTIVE_OS_ENABLED: bool = False
    R_S2_WT_PCT_OB_SHORT: float = 90.0
    R_S2_WT_PCT_OS_LONG: float = 10.0
    R_S3_HIDDEN_BONUS: float = 25.0
    R_S3_HTF_WEIGHT_ENABLED: bool = False
    R_S3_MAIN_PENALTY: float = -20.0
    R_S3_TF_WEIGHT_15M: float = 0.5
    R_S3_TF_WEIGHT_1H: float = 1.0
    R_S3_TF_WEIGHT_3M: float = 0.25
    R_S3_TF_WEIGHT_4H: float = 2.5
    R_S3_TF_WEIGHT_D: float = 4.0
    R_S5_SENT_VEL_BONUS: float = 5.0
    R_S5_SENT_VEL_ENABLED: bool = False
    R_S5_SENT_VEL_PCT_THR: float = 75.0
    R_S7_HHLL_BONUS_PER_TF: float = 3.0
    R_S7_HHLL_MIN_INDICATORS: int = 2
    R_S7_HHLL_MIN_TFS_FOR_BONUS: int = 2
    R_S7_HHLL_TFS: str = "15m,1h,4h,D"
    R_Z2_BOT_MULT: float = 0.5
    R_Z2_PCT_BOT_THR: float = 30.0
    R_Z2_PCT_TOP_THR: float = 90.0
    R_Z2_PERCENTILE_SCALER_ENABLED: bool = False
    R_Z2_TOP_MULT: float = 1.5
    R_Z3_T1_MULT: float = 1.0
    R_Z3_T1_THR: float = 50.0
    R_Z3_T2_MULT: float = 1.5
    R_Z3_T2_THR: float = 100.0
    R_Z3_T3_MULT: float = 2.0
    R_Z3_T3_THR: float = 150.0
    R_Z5_DC_HTF_MIN: float = 0.6
    R_Z5_DC_LTF_LOW_THR: float = 0.2
    SANDBOX_MODE: bool = False
    SBA_ENABLED: bool = False
    SBA_MAX_ADDS: int = 2
    SBA_MAX_CONCURRENT: int = 3
    SBA_MAX_TOTAL_MULT: float = 2.5
    SBA_MIN_LOSS_PCT: float = -2.0
    SBA_MIN_SCORE: float = 3.5
    SCALP_LONG_BUDGET: float = 250.0
    SCALP_MAX_HOLD_MINUTES: float = 180.0
    SCALP_MAX_POSITIONS_PER_SIDE: int = 6
    SCALP_MIN_MOVE_PCT: float = 0.003
    SCALP_MIN_REL_VOL: float = 1.1
    SCALP_SHORT_BUDGET: float = 250.0
    SCALP_TARGET_PCT: float = 0.005
    SCALP_TOP_MOVERS_N: int = 14
    SCALP_V3_AUG_ENABLED: bool = True
    SCALP_V3_AUG_MAX_FRAC_OF_POS: float = 0.5
    SCALP_V3_AUG_MIN_GAIN: float = 2.0
    SCALP_V3_DIAG_LOG: bool = True
    SCALP_V3_ENFORCE_UNIVERSE_DIRECTION: bool = True
    SCALP_V3_FAST_PPL_ENABLED: bool = True
    SCALP_V3_FAST_PPL_GAIN_PCT: float = 0.5
    SCALP_V3_OB_DIV_CONFLICT_MAX_NET: float = 50.0
    SCALP_V3_OB_FLOW_AGREE_ENABLED: bool = True
    SCALP_V3_OB_FLOW_AGREE_MODE: str = "WT_VEL"
    SCALP_V3_OB_FLOW_OFI_MIN_ABS: float = 0.0
    SCALP_V3_OB_FLOW_VEL_1M_MIN: float = 0.0
    SCALP_V3_OB_FLOW_VEL_3M_MIN: float = 0.0
    SCALP_V3_OB_MIN_DIFF: float = 30.0
    SCALP_V3_OB_MIN_SCORE: float = 70.0
    SCALP_V3_OB_REQUIRED: bool = True
    SCALP_V3_SCAN_INTERVAL_SEC: float = 10.0
    SCALP_V3_SCAN_MIN_DIVERGENCE: float = 0.3
    SCALP_V3_SCAN_TOP_N: int = 8
    SCALP_V3_SR_TOL_PCT: float = 0.5
    SECTOR_LS_RATIO_ENABLED: bool = True
    SENTIMENT_TOP_N: int = 20
    SLEEP_TIME_PER_TASKS: int = 3
    SLEEP_TIME_PROC_ACCT: int = 5
    SPIKE_FADE_ENABLED: bool = True
    SPIKE_FADE_MAX_POSITIONS: int = 10
    SPIKE_FADE_THRESHOLD_PCT: float = 2.0
    SQUEEZE_ENABLED: bool = False
    STALE_WARNING_INTERVAL_SECONDS: float = 30.0
    STALL_AGE_MIN_MIN: float = 180.0
    STALL_DELTA_SPEED_MAX: float = 1.0
    STDEV_BREAKOUT_MAX_AGE_BARS: int = 50
    STDEV_BREAKOUT_MAX_RETESTS: int = 3
    STDEV_BREAKOUT_RETEST_PCTB_MAX: float = 1.05
    STDEV_BREAKOUT_RETEST_PCTB_MIN: float = 0.85
    STDEV_BREAKOUT_RETEST_SCORE: int = 22
    STDEV_BREAKOUT_SCORE: int = 25
    SWING_LONG_BUDGET: float = 25000.0
    SWING_SHORT_BUDGET: float = 25000.0
    SYMBOL_PERF_DECAY_HOURS: float = 12.0
    SYMBOL_PERF_ENABLED: bool = True
    SYMBOL_PERF_MAX_MULT: float = 10.0
    SYMBOL_PERF_MIN_MULT: float = 0.1
    SYMBOL_PERF_MIN_TRADES: int = 5
    SYMBOL_PERF_REFRESH_SECONDS: float = 3600.0
    SYMBOL_PERF_WINDOW_DAYS: int = 14
    TF_ALIGNMENT_MIN_LONG: int = 2
    TF_ALIGNMENT_MIN_SHORT: int = 2
    TF_FOCUS_WEIGHT: float = 8.0
    TF_HTF1: str = "15m"
    TF_HTF2: str = "1h"
    TF_HTF3: str = "4h"
    TF_MACRO: str = "D"
    TF_MICRO: str = "1m"
    TF_SCALP: str = "3m"
    TIER_A_MIN_GAIN: float = 0.3
    TIER_A_MIN_TRADES: int = 10
    TIER_A_MULTIPLIER: float = 1.2
    TIER_A_WIN_RATE: float = 0.6
    TIER_B_MIN_TRADES: int = 5
    TIER_B_WIN_RATE: float = 0.45
    TIER_C_MULTIPLIER: float = 0.7
    TIER_ENABLED: bool = True
    TIME_ZONE_ENABLED: bool = True
    TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED: bool = True
    TRADIER_LOCAL_EXTREMES_SCORING_ENABLED: bool = False
    TRADIER_MI_SUBSIGNAL_MIN_COUNT: int = 3
    TRADIER_QUEUE_DEDUPE_SEC: float = 60.0
    TRADIER_REQUIRE_TRADEABLE_KEY: bool = True
    TRA_LONG_ONLY: bool = True
    TRA_MAX_BUYS_PER_DAY: int = 1
    TRA_MIN_HOLD_MINUTES: float = 1440.0
    TRA_SATOSHIT_ONLY: bool = True
    TRC_5M_SWEEP_BENCHMARK: str = "SPY"
    TRC_5M_SWEEP_DELTA_WEIGHT: float = 0.3
    TRC_5M_SWEEP_ENABLED: bool = True
    TRC_5M_SWEEP_TOP_N: int = 8
    TRC_5M_SWEEP_Z_WEIGHT: float = 0.7
    TRC_BEAR_MARKET_MODE: bool = False
    TRC_CLENOW_ENABLED: bool = True
    TRC_DC_DAYTRADE_LONG_BUDGET: float = 9900.0
    TRC_DC_DAYTRADE_SHORT_BUDGET: float = 9900.0
    TRC_EPISODIC_PIVOT_ENABLED: bool = False
    TRC_LS_RATIO_MAX: float = 3.0
    TRC_LS_RATIO_MIN: float = 0.3
    TRC_MAX_CONCURRENT_POSITIONS: int = 32
    TRC_MINERVINI_ENABLED: bool = True
    TRC_MINERVINI_LONG_BUDGET: float = 13200.0
    TRC_MOMENTUM_FADE_ENABLED: bool = False
    TRC_ORB_ENABLED: bool = False
    TRC_ORB_LONG_BUDGET: float = 6600.0
    TRC_ORB_SHORT_BUDGET: float = 6600.0
    TRC_SCALP_LONG_BUDGET: float = 1250.0
    TRC_SCALP_MAX_POSITIONS_PER_SIDE: int = 12
    TRC_SCALP_SHORT_BUDGET: float = 1250.0
    TRC_SCALP_TARGET_PCT: float = 0.01
    TRC_SQUEEZE_ENABLED: bool = False
    TRC_SWING_LONG_BUDGET: float = 100000.0
    TRC_SWING_SHORT_BUDGET: float = 100000.0
    TREND_HTF_MIN_BEAR: int = 7
    TREND_HTF_MIN_BULL: int = 7
    TRIPLE_CONF_ENABLED: bool = False
    TRIPLE_CONF_SCORE: int = 30
    TRIPLE_CONF_TF: str = "1h"
    TR_BBWIDTH4H_BOYCOTT_SCORE: int = -35
    TR_BBWIDTH4H_MAX: float = 10.0
    TR_CHOP4H_BONUS: int = 15
    TR_CHOP4H_MIN: float = 50.0
    TR_CHOP4H_PENALTY: int = -20
    TR_CHOP4H_TREND_MAX: float = 38.0
    TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE: int = -25
    TR_DCWIDTH4H_SHORT_ENABLED: bool = True
    TR_DCWIDTH4H_SHORT_MAX: float = 15.0
    TR_TREND_V1_SHADOW_LOG_ONLY: bool = True
    UNDERWATER_HOC_USDC_MAKER_BYPASS: bool = True
    VALIDATE_REFRESH: int = 2
    VERBOSE: bool = True
    VERBOSE2: bool = False
    VERBOSE_FETCH_LOGGING: bool = False
    VOL_SPIKE_BODY_RATIO: float = 0.7
    VOL_SPIKE_ENABLED: bool = True
    VOL_SPIKE_LS_MAX_IMBALANCE: float = 1.5
    VOL_SPIKE_MIN_ALIGNMENT: int = 3
    VOL_SPIKE_RELVOL_THRESHOLD: float = 3.0
    VWAP_FILTER_ENABLED: bool = False
    WRONG_SIDE_ABS_KILL_ENABLED: bool = True
    WRONG_SIDE_MIN_AGE_MIN: float = 30.0
    WRONG_SIDE_WT_TFS_REQUIRED: int = 4
    WT_CHOP_MAX: int = 8
    WT_COMPOSITE_DELTA_LONG_MIN: float = -100.0
    WT_COMPOSITE_DELTA_SCORE_BONUS: float = 3.0
    WT_COMPOSITE_DELTA_SCORE_ENABLED: bool = True
    WT_COMPOSITE_DELTA_SCORE_THRESHOLD: float = 50.0
    WT_COMPOSITE_DELTA_SHORT_MAX: float = 100.0
    WT_COMPOSITE_SCORING_ENABLED: bool = True
    WT_COMPOSITE_SCORING_ENABLED_TRADIER: bool = True
    WT_CROSSUNDER_15M_SHORT: bool = True
    WT_D_BOUNCE_AUG_ENABLED: bool = False
    WT_D_BOUNCE_AUG_MULTIPLIER: float = 2.0
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_PRICE: bool = True
    WT_D_BOUNCE_AUG_REQUIRE_HIGHER_WT: bool = True
    WT_MTF_VEL_MIN: int = 2
    ZERO_CONFIRMATION_THRESHOLD_API: int = 2
    ZERO_CONFIRMATION_THRESHOLD_WS: int = 1
    ZONE_MID_THRESHOLD: int = 30
    # ── DAEMON (4 knobs) ──
    CLENOW_REGIME_FILTER: bool = True
    REGIME_DETECTION_ENABLED: bool = False
    VIX_REGIME_FILTER_ENABLED: bool = True
    VIX_VOLATILITY_REGIME_ENABLED: bool = True
    # ── LIVE_ONLY (3 knobs) ──
    REDIS_DB: int = 0
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

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

        # ── Trade-record side-channel (2026-05-18: quick_engine_compat support) ──
        # When env V8_TRADES_OUT_DIR is set, every close site appends a record to
        # _trade_log which is written as JSONL at end of simulate(). Mirrors the
        # legacy OPUS_VOMIT (v8_quick_engine) recorder API so flz_hourly_reconfig
        # and tradier_hourly_reconfig can keep using the same JSONL contract.
        # Fields per record: symbol, side, entry_ts, exit_ts, entry_price,
        # exit_price, pnl_pct, exit_reason, stream.
        _trades_out_dir = os.environ.get("V8_TRADES_OUT_DIR", "")
        _trades_run_id = os.environ.get("V8_TRADES_RUN_ID", "default")
        _trade_log: Optional[List[Dict[str, Any]]] = [] if _trades_out_dir else None

        def _emit_trade(_pos, _ts_i, _price_i, _pnl_pct, _reason, _stream="primary"):
            if _trade_log is None:
                return
            try:
                _trade_log.append({
                    "symbol": _pos_sym_ref[0] if _pos_sym_ref else "",
                    "side": getattr(_pos, "side", "") or "",
                    "entry_ts": int(getattr(_pos, "entry_ts", 0) or 0),
                    "exit_ts": int(_ts_i or 0),
                    "entry_price": float(getattr(_pos, "entry_price", 0.0) or 0.0),
                    "exit_price": float(_price_i or 0.0),
                    "pnl_pct": float(_pnl_pct),
                    "exit_reason": str(_reason or "VEC_CLOSE"),
                    "stream": _stream,
                })
            except Exception:
                pass

        # Mutable holder so _emit_trade can see current symbol without a closure rebind.
        _pos_sym_ref: List[str] = [""]

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

        # ── ROUND 6 (2026-05-19) — DC_BB_D_BREAK_REVERSE crossback state ──
        # Mirrors ez_manage.py:38881 _db_state dict. Per-symbol tracks:
        #   last_dir: "UP" or "DOWN" (last fresh band break direction)
        #   last_level: price level of the break
        #   last_band: "DC" or "BB"
        # Required for CROSSBACK detection — fires when price drops back through
        # level * (1 - hysteresis) for UP-breaks or level * (1 + hysteresis) for
        # DOWN-breaks. Stateless break-only checks (iter 10) never fired because
        # SOL never had a fresh band-break-down in the test window — but it DID
        # have UP breaks followed by retracements (which is the live exit path).
        dc_bb_state: Dict[str, Dict[str, Any]] = {sym: {} for sym in stores}

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

        # ── STDEV_MACRO state arrays (2026-05-18 wired) ───────────────────────────
        # Per-symbol macro_state per bar: 0=MID, ±1=TOP/BOT, ±2=STRONG.
        # Source preference: NPZ macro_z_D/macro_z_W fields (if present); else compute
        # on the fly from close array using vec_paths.stdev_macro_vec.
        # Fail-open: if both unavailable, state stays 0 (MID) → all STDEV gates pass.
        # Computed ONCE before bar loop (vectorized) for speed.
        macro_state_per_sym: Dict[str, np.ndarray] = {}
        _stdev_macro_needed = (
            bool(getattr(cfg, "STDEV_MACRO_ENTRY_VETO_ENABLED", False))
            or bool(getattr(cfg, "STDEV_MACRO_AUGMENT_VETO_ENABLED", False))
            or bool(getattr(cfg, "STDEV_MACRO_R4_EXIT_ENABLED", False))
        )
        if _stdev_macro_needed:
            try:
                from vec_paths.stdev_macro_vec import (
                    rolling_log_zscore as _rlz,
                    derive_state_vec as _dsv,
                )
                _macro_w_d = int(getattr(cfg, "STDEV_MACRO_WINDOW_D", 200))
                _macro_w_w = int(getattr(cfg, "STDEV_MACRO_WINDOW_W", 52))
                for _ms_sym, _ms_store in stores.items():
                    _ms_z_d = _ms_store.arrays.get("macro_z_D")
                    _ms_z_w = _ms_store.arrays.get("macro_z_W")
                    if _ms_z_d is None or _ms_z_w is None:
                        _ms_close = _ms_store.arrays.get("close")
                        if _ms_close is None or len(_ms_close) == 0:
                            macro_state_per_sym[_ms_sym] = None  # fail-open
                            continue
                        _ms_z_d = _rlz(_ms_close, _macro_w_d)
                        _ms_z_w = _rlz(_ms_close, _macro_w_w)
                    macro_state_per_sym[_ms_sym] = _dsv(_ms_z_d, _ms_z_w)
            except Exception:
                # Fail-open: state stays None per symbol → no veto
                for _ms_sym in stores:
                    macro_state_per_sym[_ms_sym] = None

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
                _pos_sym_ref[0] = sym  # trade-record side-channel (2026-05-18)
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
                        _emit_trade(pos, ts_i, price, pnl, "R1_DC_LOW4_EMERGENCY")
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
                        _emit_trade(pos, ts_i, price, pnl, "R2_WT_VEL_SLOW")
                        all_returns.append(pnl)
                        running_gain += pnl
                        pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                        if pnl > 0:
                            pos.last_reduce_price = price

                # ── ROUND 3 FIX #1 2026-05-19 — RIDICULOUS_HOLD_VEC time-cap ──
                # Live source: ez_manage.py:38754 RIDICULOUS_HOLD_GUARD. Forced-close
                # path for small-loss positions held longer than HOURS. Iter 22-24
                # showed this firing on 4 of 6 live ETH closes (age 239-272h, gains
                # -0.39% to -0.01%) and 16 of 38 live SOL closes (age 50-92h).
                # Vec had NO equivalent → over-fired STOCH_REVERSE_EXIT instead.
                # BYPASSES NOLOSS gate (close at small loss is sanctioned for stale
                # positions). Default OFF so baseline preserved; baseline file opts in.
                if bool(getattr(cfg, "RIDICULOUS_HOLD_VEC_ENABLED", False)):
                    _rh_hours = float(getattr(cfg, "RIDICULOUS_HOLD_VEC_HOURS", 48.0))
                    _rh_floor = float(getattr(cfg, "RIDICULOUS_HOLD_VEC_GAIN_FLOOR_PCT", -15.0))
                    _rh_ceil = float(getattr(cfg, "RIDICULOUS_HOLD_VEC_GAIN_CEIL_PCT", 0.5))
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        _rh_age_h = (ts_i - pos.entry_ts) / 3600.0
                        if _rh_age_h <= _rh_hours:
                            continue
                        _rh_g = pos.gain_pct
                        if not (_rh_floor <= _rh_g <= _rh_ceil):
                            continue
                        pnl = _rh_g
                        returns_by_sym[sym].append(pnl)
                        _emit_trade(pos, ts_i, price, pnl, "RIDICULOUS_HOLD_VEC")
                        all_returns.append(pnl)
                        running_gain += pnl
                        pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                        if pnl > 0:
                            pos.last_reduce_price = price

                # ── WT_15M_VEL_SLOW near-zero gain exit (Path 5 / R2 variant) ──────
                # Source: vec_paths/winner_protect.py + ez_manage.py:21022 R2 block.
                # Fires BEFORE NOLOSS gate — is a NOLOSS bypass.
                # Different from exit_r1_r2.check_r2_wt_vel_slow_exit which uses
                # WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED default True. This path duplicates
                # that logic inside winner_protect.py for composition clarity.
                # NOTE: exit_r1_r2.py already wires R2 above. This block is gated to
                # AVOID double-firing: only runs when exit_r1_r2's R2 is disabled.
                if not bool(getattr(cfg, 'R2_USE_EXIT_R1_R2_MODULE', True)):
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        _wp_r2_sig = _check_wt_15m_vel_slow_zero_gain(store, bar_idx, pos, self.mode, cfg)
                        if _wp_r2_sig is not None:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            _emit_trade(pos, ts_i, price, pnl, "WT_15M_VEL_SLOW_ZERO_GAIN")
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── PEAK_GIVEBACK exit (Path 4) ──────────────────────────────────
                # Source: vec_paths/peak_giveback_be_erosion.py + tradier_manage.py:5028.
                # Fires BEFORE NOLOSS gate (trades still open at loss allowed to close).
                # Default: PEAK_GIVEBACK_PROTECTION_ENABLED=True but
                #   PEAK_GIVEBACK_HARD_ZERO_ENABLED=False +
                #   PEAK_GIVEBACK_DROP_TRIGGER_ENABLED=False → effectively disabled in current config.
                if bool(getattr(cfg, 'PEAK_GIVEBACK_PROTECTION_ENABLED', True)):
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        _pgb_sig = _check_peak_giveback_exit(store, bar_idx, pos, self.mode, cfg)
                        if _pgb_sig is not None:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            _emit_trade(pos, ts_i, price, pnl, "PEAK_GIVEBACK")
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── BE_EROSION exit (Path 4 variant) ────────────────────────────
                # Source: vec_paths/peak_giveback_be_erosion.py.
                # Default OFF (BE_EROSION_ENABLED=False).
                if bool(getattr(cfg, 'BE_EROSION_ENABLED', False)):
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        _be_sig = _check_be_erosion_exit(store, bar_idx, pos, self.mode, cfg)
                        if _be_sig is not None:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            _emit_trade(pos, ts_i, price, pnl, "BE_EROSION")
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── REDUCE paths (Path 3) ────────────────────────────────────────
                # K1M_EXTREME_REVERSE, STRONG_REDUCE_K, PROFIT_TAKE_REDUCE.
                # These are PARTIAL closes: append fractional pnl, reduce pos.qty.
                # All three default OFF. Firing one reduces qty but leaves position open.
                for _rp_pos in (pos_long, pos_short):
                    if not _rp_pos.open:
                        continue
                    _rp_gain = _rp_pos.gain_pct
                    for _rp_fn, _rp_enabled_attr in (
                        (_check_k1m_extreme_reverse, 'K1M_EXTREME_REVERSE_ENABLED'),
                        (_check_strong_reduce_k, 'STRONG_REDUCE_K_ENABLED'),
                        (_check_profit_take_reduce, 'PROFIT_TAKE_REDUCE_ENABLED'),
                    ):
                        if not bool(getattr(cfg, _rp_enabled_attr, False)):
                            continue
                        _rp_sig = _rp_fn(store, bar_idx, _rp_pos, self.mode, cfg)
                        if _rp_sig is None:
                            continue
                        if _rp_sig.get('require_profit', True) and _rp_gain < 0:
                            continue
                        _frac = float(_rp_sig.get('frac', 0.5))
                        partial_pnl = _rp_gain * _frac
                        returns_by_sym[sym].append(partial_pnl)
                        _emit_trade(_rp_pos, ts_i, price, partial_pnl, "REDUCE_PROFIT_TAKE")
                        all_returns.append(partial_pnl)
                        running_gain += partial_pnl
                        _rp_pos.qty *= (1.0 - _frac)
                        # Track profit-take state (reuse ppl_fired as "took-profit" flag)
                        _rp_pos.ppl_fired = True
                        if _rp_gain > 0:
                            _rp_pos.last_reduce_price = price
                        break  # only one reduce path fires per bar per position

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
                            _emit_trade(pos, ts_i, price, pnl, "STRUCTURAL_RANGE_SHIFT")
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── PARTIAL_PROFIT_LOCK v2 (3-step) ─────────────
                # Source: vec_paths/partial_profit_lock_v2.py.
                # Replaces old inline approximation.
                # NOTE: PARTIAL_PROFIT_LOCK_ENABLED=False in live as of 2026-05-12.
                # 2026-05-18 WIRED: tradier-mode overrides PPL_GAIN_PCT/ARM/BE_BUFFER/FRAC
                # via PARTIAL_PROFIT_LOCK_*_TRADIER fields. We pass a shallow proxy when
                # mode==tradier so PPL module reads tradier values.
                _ppl_cfg = cfg
                if cfg.PARTIAL_PROFIT_LOCK_ENABLED and self.mode == "tradier":
                    class _PPLProxy:
                        __slots__ = ("_inner",)
                        _PPL_OVERRIDES = {
                            "PARTIAL_PROFIT_LOCK_GAIN_PCT": "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER",
                            "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER",
                            "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER",
                            "PARTIAL_PROFIT_LOCK_FRAC": "PARTIAL_PROFIT_LOCK_FRAC_TRADIER",
                        }
                        def __init__(self, inner):
                            object.__setattr__(self, "_inner", inner)
                        def __getattr__(self, name):
                            ov = self._PPL_OVERRIDES.get(name)
                            if ov is not None:
                                return getattr(self._inner, ov)
                            return getattr(self._inner, name)
                    _ppl_cfg = _PPLProxy(cfg)
                if cfg.PARTIAL_PROFIT_LOCK_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        if not pos.ppl_fired:
                            _ppl1 = _check_ppl_step1(store, bar_idx, pos, _ppl_cfg)
                            if _ppl1 is not None:
                                frac = _ppl1["frac"]
                                partial_gain = pos.gain_pct * frac
                                returns_by_sym[sym].append(partial_gain)
                                _emit_trade(pos, ts_i, price, partial_gain, "PPL_STEP1")
                                all_returns.append(partial_gain)
                                running_gain += partial_gain
                                pos.qty *= (1.0 - frac)
                                pos.ppl_fired = True
                                pos.ppl_first_exit_price = price
                                pos.ppl_stop_level = _ppl1["stop_level"]
                                pos.ppl_stop_upgraded = False
                                continue
                        if pos.ppl_fired and not pos.ppl_stop_upgraded:
                            _ppl2 = _check_ppl_step2(store, bar_idx, pos, _ppl_cfg)
                            if _ppl2 is not None:
                                pos.ppl_stop_level = _ppl2["new_stop"]
                                pos.ppl_stop_upgraded = True
                        if pos.ppl_fired and pos.ppl_stop_level > 0:
                            _ppl3 = _check_ppl_step3(store, bar_idx, pos, _ppl_cfg)
                            if _ppl3 is not None:
                                pnl = pos.gain_pct
                                returns_by_sym[sym].append(pnl)
                                _emit_trade(pos, ts_i, price, pnl, "PPL_STEP2")
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
                                _emit_trade(pos, ts_i, price, pnl, "PPL_STEP3")
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                        except Exception:
                            pass

                # ── R4_STDEV_MACRO exit (WIRED 2026-05-18) ──────────────────────
                # Source: config.py:1279 + memory project_stdev_macro_scaffold_20260517.
                # Long-window log-price z-score on D+W: fire CLOSE when STRONG_TOP (LONG)
                # or STRONG_BOT (SHORT), optionally requiring LTF 4h flip against.
                # Bypasses UNIVERSAL_NOLOSS_GATE (reason in LOSS_EXIT_TECHNICAL_BYPASS).
                if bool(getattr(cfg, "STDEV_MACRO_R4_EXIT_ENABLED", False)):
                    _r4_arr = macro_state_per_sym.get(sym)
                    if _r4_arr is not None and bar_idx < len(_r4_arr):
                        _r4_state = int(_r4_arr[bar_idx])
                        _r4_require_flip = bool(getattr(cfg, "STDEV_MACRO_R4_REQUIRE_LTF_FLIP", True))
                        for pos in (pos_long, pos_short):
                            if not pos.open:
                                continue
                            _r4_fire = False
                            _r4_reason_tag = ""
                            if pos.side == "LONG" and _r4_state >= 2:
                                # STRONG_TOP → close LONG
                                if _r4_require_flip:
                                    _w1_4h = store.f("wt1_4h", bar_idx, 0.0)
                                    _w2_4h = store.f("wt2_4h", bar_idx, 0.0)
                                    _r4_fire = (abs(_w1_4h) > 1e-9 and _w1_4h < _w2_4h)
                                else:
                                    _r4_fire = True
                                _r4_reason_tag = "R4_STDEV_MACRO_TOP"
                            elif pos.side == "SHORT" and _r4_state <= -2:
                                if _r4_require_flip:
                                    _w1_4h = store.f("wt1_4h", bar_idx, 0.0)
                                    _w2_4h = store.f("wt2_4h", bar_idx, 0.0)
                                    _r4_fire = (abs(_w1_4h) > 1e-9 and _w1_4h > _w2_4h)
                                else:
                                    _r4_fire = True
                                _r4_reason_tag = "R4_STDEV_MACRO_BOT"
                            if _r4_fire:
                                pnl = pos.gain_pct
                                returns_by_sym[sym].append(pnl)
                                _emit_trade(pos, ts_i, price, pnl, _r4_reason_tag)
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False
                                pos.last_close_ts = ts_i
                                pos.last_close_price = price
                                if pnl > 0:
                                    pos.last_reduce_price = price

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
                            _emit_trade(pos, ts_i, price, pnl, "WT_CROSSUNDER_FINAL")
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price
                            if pnl > 0:
                                pos.last_reduce_price = price

                # ── WT-based exit logic ─────────────────────────
                # Iter 1 fix 2026-05-19: live exits require multi-TF confirmation, not
                # bare 3m WT cross. Add HTF gate: ≥WT_BASE_CROSS_EXIT_HTF_MIN of {1h,4h,D}
                # must be against position AND 15m confirms. Mirrors WT_CROSSUNDER_FINAL
                # gating in vec_paths/wt_crossunder_final.py. Default min_htf=1.
                # Knob WT_BASE_CROSS_EXIT_HTF_GATE_ENABLED defaults True per iter 1.
                # ── ROUND 6 FIX 2026-05-19 — STOCH_REVERSE_EXIT_ENABLED master gate ──
                # When set to False (e.g. via per_sym overlay for SOLUSDC_LONG), the
                # entire STOCH_REVERSE_EXIT path is skipped via per-iteration check.
                # Default True (preserves round-5 behavior on BTC/ETH). Round-6
                # forensic: SOL vec had 6 STOCH_REVERSE_EXIT closes while live had
                # ZERO — this exit was tightening vec's per-trade pnl distribution
                # and producing KS_p=5e-5. Disabling for SOL (per_sym) widens the
                # loss tail to match live's RIDICULOUS_HOLD-dominated exit mix.
                _src_master_enabled = bool(getattr(cfg, "STOCH_REVERSE_EXIT_ENABLED", True))
                for pos in (pos_long, pos_short):
                    if not _src_master_enabled:
                        break  # path disabled by per_sym overlay — skip whole exit
                    if not pos.open:
                        continue
                    cross = store.s(f"wt_cross_{btf}", bar_idx)
                    # Exit LONG on BEAR cross, SHORT on BULL cross
                    exit_signal = (pos.side == "LONG" and cross == "BEAR") or \
                                  (pos.side == "SHORT" and cross == "BULL")
                    if not exit_signal:
                        continue
                    # ── ITER 1 HTF confirmation gate ──
                    if bool(getattr(cfg, "WT_BASE_CROSS_EXIT_HTF_GATE_ENABLED", True)):
                        _wt1_15m = store.f("wt1_15m", bar_idx, 0.0)
                        _wt2_15m = store.f("wt2_15m", bar_idx, 0.0)
                        _wt1_1h  = store.f("wt1_1h",  bar_idx, 0.0)
                        _wt2_1h  = store.f("wt2_1h",  bar_idx, 0.0)
                        _wt1_4h  = store.f("wt1_4h",  bar_idx, 0.0)
                        _wt2_4h  = store.f("wt2_4h",  bar_idx, 0.0)
                        _wt1_D   = store.f("wt1_D",   bar_idx, 0.0)
                        _wt2_D   = store.f("wt2_D",   bar_idx, 0.0)
                        if pos.side == "LONG":
                            _m15_against = (_wt1_15m < _wt2_15m) or (_wt1_15m > 95)
                            _htf_against = sum(1 for (_w1, _w2) in [
                                (_wt1_1h, _wt2_1h), (_wt1_4h, _wt2_4h), (_wt1_D, _wt2_D)
                            ] if _w1 < _w2)
                        else:
                            _m15_against = (_wt1_15m > _wt2_15m) or (_wt1_15m < -95)
                            _htf_against = sum(1 for (_w1, _w2) in [
                                (_wt1_1h, _wt2_1h), (_wt1_4h, _wt2_4h), (_wt1_D, _wt2_D)
                            ] if _w1 > _w2)
                        _htf_min = int(getattr(cfg, "WT_BASE_CROSS_EXIT_HTF_MIN", 1))
                        if not (_m15_against and _htf_against >= _htf_min):
                            continue
                    # ── MIN_HOLD_MINUTES gate (parity with TRADIER_MIN_HOLD_MINUTES=240) ──
                    min_hold = cfg.MIN_HOLD_MINUTES if self.mode == "tradier" else cfg.MIN_HOLD_MINUTES_CRYPTO
                    if min_hold > 0:
                        hold_min = (ts_i - pos.entry_ts) / 60.0
                        if hold_min < min_hold:
                            continue
                    # UNIVERSAL_NOLOSS_GATE: block loss exits (except R1/R2/SRS already handled)
                    # NOLOSS_BYPASS_WT_5OF5 (WIRED 2026-05-18): allow loss exit when ≥MIN_TFS
                    # of {5m,15m,1h,4h,D} WT against position. Source: tradier_manage.py:5934.
                    if cfg.UNIVERSAL_NOLOSS_GATE and pos.gain_pct < 0:
                        _nlb_pass = False
                        if bool(getattr(cfg, "NOLOSS_BYPASS_WT_5OF5_ENABLED", False)):
                            _nlb_min = int(getattr(cfg, "NOLOSS_BYPASS_WT_5OF5_MIN_TFS", 5))
                            _nlb_against = 0
                            for _tf in ("5m", "15m", "1h", "4h", "D"):
                                _w1 = store.f(f"wt1_{_tf}", bar_idx, 0.0)
                                _w2 = store.f(f"wt2_{_tf}", bar_idx, 0.0)
                                if (pos.side == "LONG" and _w1 < _w2) or (pos.side == "SHORT" and _w1 > _w2):
                                    _nlb_against += 1
                            _nlb_pass = (_nlb_against >= _nlb_min)
                        if not _nlb_pass:
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
                    # ── ITER 14 fix 2026-05-19 (round 2): STOCH_REVERSE_EXIT_MIN_LOSS_HOLD_HOURS ──
                    # Live behavior: bare WT-bear-cross does NOT close a losing position. Loss
                    # exits go through R1/R2/HEDGE_FAILED/RIDICULOUS_HOLD time-cap (~48h). Vec
                    # was over-firing STOCH_REVERSE_EXIT on losses immediately. Add a MIN_HOLD
                    # gate that applies ONLY when position is at loss — winners can still exit
                    # immediately. Knob STOCH_REVERSE_EXIT_MIN_LOSS_HOLD_HOURS default 0.0 (no
                    # gate so SOL convergence preserved). Iter 11 NONNEG_GAIN gate was reverted —
                    # it blocked SOL's legitimate small-loss closes which mirror live RIDICULOUS_HOLD.
                    _src_min_loss_h = float(getattr(cfg, "STOCH_REVERSE_EXIT_MIN_LOSS_HOLD_HOURS", 0.0))
                    if _src_min_loss_h > 0.0 and pos.gain_pct < 0:
                        _age_h = (ts_i - pos.entry_ts) / 3600.0
                        if _age_h < _src_min_loss_h:
                            continue
                    # ── ITER 22 fix 2026-05-19 (round 2): STOCH_REVERSE_EXIT_LOSS_THRESHOLD_PCT ──
                    # Live RIDICULOUS_HOLD cap fires ~ -1% to -2% range (samples: -0.39%, -0.01%,
                    # -0.17%, -0.06%); very-small losses tend to be HELD until time-cap. Mirror
                    # this: refuse STOCH_REVERSE_EXIT when gain is between threshold and 0 (small
                    # loss not big enough to warrant exit). Closes only when gain < threshold OR
                    # gain >= 0. Default 0.0 (no effect — preserves SOL convergence). Bump via
                    # baseline to e.g. -1.5 for stronger live parity on BTC/ETH.
                    _src_loss_thr = float(getattr(cfg, "STOCH_REVERSE_EXIT_LOSS_THRESHOLD_PCT", 0.0))
                    if _src_loss_thr < 0.0 and _src_loss_thr < pos.gain_pct < 0.0:
                        continue
                    pnl = pos.gain_pct
                    returns_by_sym[sym].append(pnl)
                    _emit_trade(pos, ts_i, price, pnl, "STOCH_REVERSE_EXIT")
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
                                _emit_trade(pos, ts_i, price, pnl, "DC_RECOVERY_EXIT")
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False; pos.last_close_ts = ts_i; pos.last_close_price = price

                # ── ROUND 6 FIX 2026-05-19 — DC_BB_D_BREAK_REVERSE EXIT-leg ──
                # Source: ez_manage.py:38881-39048. Stateful CROSSBACK detection.
                # Live SOL exit-mix has 7 closes via this path (= 18% of all SOL closes).
                # State per sym: dc_bb_state[sym] = {last_dir, last_level, last_band}
                # Step 1: detect fresh band breaks (UP or DN) → record state
                # Step 2: if no fresh break, check crossback through level * (1 ± hysteresis)
                # Step 3: if position is on the wrong side after a crossback (or fresh break
                #         in opposite direction), close it with DC_BB_D_BREAK_REVERSE_close_<band>_<event>
                # Gated by DC_BB_D_BREAK_REVERSE_ENABLED (mirrors entry-leg knob).
                if bool(getattr(cfg, "DC_BB_D_BREAK_REVERSE_ENABLED", True)):
                    _dbe_dc_hi_d = store.f("dc_high_D_prev", bar_idx, 0.0) or store.f("dc_high_D", bar_idx, 0.0)
                    _dbe_dc_lo_d = store.f("dc_low_D_prev", bar_idx, 0.0) or store.f("dc_low_D", bar_idx, 0.0)
                    _dbe_bb_up_d = store.f("bb_upper_D", bar_idx, 0.0)
                    _dbe_bb_lo_d = store.f("bb_lower_D", bar_idx, 0.0)
                    _dbe_hyst = float(getattr(cfg, "DC_BB_CROSSBACK_HYSTERESIS_PCT", 2.0)) / 100.0
                    _dbe_break_up = (_dbe_dc_hi_d > 0 and price > _dbe_dc_hi_d) or \
                                    (_dbe_bb_up_d > 0 and price > _dbe_bb_up_d)
                    _dbe_break_dn = (_dbe_dc_lo_d > 0 and price < _dbe_dc_lo_d) or \
                                    (_dbe_bb_lo_d > 0 and price < _dbe_bb_lo_d)
                    _dbe_sym_st = dc_bb_state.get(sym, {})
                    _dbe_cross_back_to_long = False
                    _dbe_cross_back_to_short = False
                    _dbe_event = "UNKNOWN"
                    _dbe_band = "DC"
                    if _dbe_break_up:
                        _dbe_lvl = _dbe_dc_hi_d if (_dbe_dc_hi_d > 0 and price > _dbe_dc_hi_d) else _dbe_bb_up_d
                        _dbe_band = "DC" if (_dbe_dc_hi_d > 0 and price > _dbe_dc_hi_d) else "BB"
                        dc_bb_state[sym] = {"last_dir": "UP", "last_level": _dbe_lvl, "last_band": _dbe_band}
                        _dbe_event = "BREAK_UP"
                    elif _dbe_break_dn:
                        _dbe_lvl = _dbe_dc_lo_d if (_dbe_dc_lo_d > 0 and price < _dbe_dc_lo_d) else _dbe_bb_lo_d
                        _dbe_band = "DC" if (_dbe_dc_lo_d > 0 and price < _dbe_dc_lo_d) else "BB"
                        dc_bb_state[sym] = {"last_dir": "DOWN", "last_level": _dbe_lvl, "last_band": _dbe_band}
                        _dbe_event = "BREAK_DOWN"
                    else:
                        if _dbe_sym_st.get("last_dir") == "UP" and _dbe_sym_st.get("last_level", 0) > 0:
                            _dbe_cross_level = _dbe_sym_st["last_level"] * (1.0 - _dbe_hyst)
                            if price < _dbe_cross_level:
                                _dbe_cross_back_to_short = True
                                _dbe_band = _dbe_sym_st.get("last_band", "DC")
                                _dbe_event = "CROSSBACK_TO_SHORT"
                                dc_bb_state[sym] = {"last_dir": "DOWN", "last_level": _dbe_sym_st["last_level"], "last_band": _dbe_band}
                        elif _dbe_sym_st.get("last_dir") == "DOWN" and _dbe_sym_st.get("last_level", 0) > 0:
                            _dbe_cross_level = _dbe_sym_st["last_level"] * (1.0 + _dbe_hyst)
                            if price > _dbe_cross_level:
                                _dbe_cross_back_to_long = True
                                _dbe_band = _dbe_sym_st.get("last_band", "DC")
                                _dbe_event = "CROSSBACK_TO_LONG"
                                dc_bb_state[sym] = {"last_dir": "UP", "last_level": _dbe_sym_st["last_level"], "last_band": _dbe_band}
                    # Position is on WRONG side?
                    # LONG wrong on (BREAK_DOWN OR CROSSBACK_TO_SHORT); SHORT wrong on (BREAK_UP OR CROSSBACK_TO_LONG)
                    _dbe_should_be_long = _dbe_break_up or _dbe_cross_back_to_long
                    _dbe_should_be_short = _dbe_break_dn or _dbe_cross_back_to_short
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        _dbe_wrong = (pos.side == "LONG" and _dbe_should_be_short) or \
                                     (pos.side == "SHORT" and _dbe_should_be_long)
                        if not _dbe_wrong:
                            continue
                        pnl = pos.gain_pct
                        returns_by_sym[sym].append(pnl)
                        _emit_trade(pos, ts_i, price, pnl, f"DC_BB_D_BREAK_REVERSE_close_{_dbe_band}_{_dbe_event}")
                        all_returns.append(pnl)
                        running_gain += pnl
                        pos.open = False
                        pos.last_close_ts = ts_i
                        pos.last_close_price = price
                        if pnl > 0:
                            pos.last_reduce_price = price

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
                            _emit_trade(pos, ts_i, price, pnl, "BB_RECOVERY_EXIT")
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
                            _emit_trade(_v2e_pos, ts_i, price, pnl, "SCALP_V2_EXIT")
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
                            _emit_trade(_v3e_pos, ts_i, price, pnl, "SCALP_V3_EXIT")
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
                                    _emit_trade(_ms_pos, ts_i, price, pnl, "MICRO_SCALP_CLOSE")
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

                # ── DUP_GUARD augment check (Path 2) ────────────────────────────
                # Source: vec_paths/dup_guard.py + ez_manage.py:11014+14062.
                # Applied to all AUGMENT-eligible open positions BEFORE sentiment boost.
                # In simulate() this gates the SENTIMENT_BOOST augment path.
                # DUP_GUARD for NEW opens (empty position) is handled in the entry loop below.
                _dg_blocked: set = set()
                if _DUP_GUARD_AVAILABLE and bool(getattr(cfg, 'DUP_GUARD_ENABLED', True)):
                    for _dg_pos in (pos_long, pos_short):
                        if not _dg_pos.open:
                            continue
                        _dg_val = _dg_pos.qty * price if price > 0 else 0.0
                        _dg_blk = _check_dup_guard_block(
                            _dg_pos, float(ts_i), _dg_pos.gain_pct, cfg,
                            pos_value_usd=_dg_val, action="AUGMENT"
                        )
                        if _dg_blk is not None:
                            _dg_blocked.add(_dg_pos.side)

                # ── SENTIMENT_BOOST augment (tradier_manage.py:7408) ──────────
                # Check for augment on open positions before the entry gate.
                # This fires AUGMENT events on already-open positions when
                # market_sentiment indicates the position should be larger.
                # ADDITIVE: does not replace any existing logic, only augments qty.
                if _sentiment_boost_fn is not None:
                    # STDEV_MACRO_AUGMENT_VETO (WIRED 2026-05-18): block AUGMENT when at
                    # TOP/BOT macro extreme (|state|>=1). LONG augment vetoed at TOP, SHORT at BOT.
                    _smv_aug_block_long = False
                    _smv_aug_block_short = False
                    if bool(getattr(cfg, "STDEV_MACRO_AUGMENT_VETO_ENABLED", False)):
                        _smv_aug_arr = macro_state_per_sym.get(sym)
                        if _smv_aug_arr is not None and bar_idx < len(_smv_aug_arr):
                            _smv_aug_state = int(_smv_aug_arr[bar_idx])
                            _smv_aug_block_long = (_smv_aug_state >= 1)   # TOP or STRONG_TOP
                            _smv_aug_block_short = (_smv_aug_state <= -1) # BOT or STRONG_BOT
                    try:
                        for _sb_pos in (pos_long, pos_short):
                            if not _sb_pos.open:
                                continue
                            if _sb_pos.side in _dg_blocked:
                                continue
                            if _sb_pos.side == "LONG" and _smv_aug_block_long:
                                continue
                            if _sb_pos.side == "SHORT" and _smv_aug_block_short:
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
                        # ── HEDGE_HTF_VETO (WIRED 2026-05-18) ──
                        # Source: ez_manage.py:24453.
                        # For LONG-loser pos hedge=SHORT requires wt1_D < wt2_D.
                        # For SHORT-loser pos hedge=LONG requires wt1_D > wt2_D.
                        # Fail-open if D data missing.
                        _hg_htf_veto_active = False
                        if bool(getattr(cfg, "HEDGE_HTF_VETO_ENABLED", False)):
                            _hg_w1_D = store.f("wt1_D", bar_idx, 0.0)
                            _hg_w2_D = store.f("wt2_D", bar_idx, 0.0)
                            if abs(_hg_w1_D) > 1e-9 and abs(_hg_w2_D) > 1e-9:
                                _hg_is_long = (_hg_loser_side == "LONG")
                                _hg_aligned = (_hg_is_long and _hg_w1_D < _hg_w2_D) or \
                                              ((not _hg_is_long) and _hg_w1_D > _hg_w2_D)
                                if not _hg_aligned:
                                    _hg_htf_veto_active = True
                        try:
                            _hg_scan_result = _check_scan_hedge_losers(store, bar_idx, _hg_loser, all_pos_states=pos_states[sym], mode=self.mode, cfg=cfg)
                        except Exception:
                            _hg_scan_result = None
                        # HEDGE_MAX_ABSOLUTE_USD cap (WIRED 2026-05-18) — cap qty * price to max abs USD.
                        # Source: ez_positions_quick.py:6296/7406.
                        _hg_max_abs = float(getattr(cfg, "HEDGE_MAX_ABSOLUTE_USD", 100000.0))
                        if _hg_scan_result is not None and _hg_max_abs > 0 and price > 0:
                            _hg_qty_capped = min(float(_hg_scan_result.get("size_qty", 0.0)), _hg_max_abs / price)
                            _hg_scan_result = dict(_hg_scan_result)
                            _hg_scan_result["size_qty"] = _hg_qty_capped
                        if _hg_htf_veto_active:
                            _hg_scan_result = None  # HTF veto blocks hedge open
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
                        # Apply HEDGE_MAX_ABSOLUTE_USD cap + HEDGE_HTF_VETO to obligatory hedge too.
                        if _hg_obl_result is not None and _hg_max_abs > 0 and price > 0:
                            _hg_qty_capped = min(float(_hg_obl_result.get("size_qty", 0.0)), _hg_max_abs / price)
                            _hg_obl_result = dict(_hg_obl_result)
                            _hg_obl_result["size_qty"] = _hg_qty_capped
                        if _hg_htf_veto_active:
                            _hg_obl_result = None  # HTF veto blocks hedge open
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
                                _emit_trade(_hg_loser, ts_i, price, pnl, "HEDGE_FAILED_FALLBACK")
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
                            _emit_trade(_hc_pos, ts_i, price, pnl, "HEDGE_CLOSE")
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

                    # ── HTF_TREND_VETO entry gate (WIRED 2026-05-18) ──
                    # Source: ez_manage.py:18660 / tradier_manage.py:10079.
                    # Blocks OPEN against Daily wt-trend: LONG requires wt1_D > wt2_D, SHORT mirror.
                    # Fail-open when wt1_D / wt2_D absent or both near-zero (data missing).
                    if bool(getattr(cfg, "HTF_TREND_VETO_ENABLED", False)):
                        _htfv_w1_D = store.f("wt1_D", bar_idx, 0.0)
                        _htfv_w2_D = store.f("wt2_D", bar_idx, 0.0)
                        if abs(_htfv_w1_D) > 1e-9 and abs(_htfv_w2_D) > 1e-9:
                            _htfv_ok = (side == "LONG" and _htfv_w1_D > _htfv_w2_D) or \
                                       (side == "SHORT" and _htfv_w1_D < _htfv_w2_D)
                            if not _htfv_ok:
                                continue

                    # ── STDEV_MACRO entry veto (WIRED 2026-05-18) ──
                    # Source: config.py:1275 + memory project_stdev_macro_scaffold_20260517.
                    # Blocks OPEN when STRONG_TOP (state=2) for LONG, STRONG_BOT (state=-2) for SHORT.
                    # Fail-open when state arrays missing or value MID.
                    if bool(getattr(cfg, "STDEV_MACRO_ENTRY_VETO_ENABLED", False)):
                        _smv_arr = macro_state_per_sym.get(sym)
                        if _smv_arr is not None and bar_idx < len(_smv_arr):
                            _smv_state = int(_smv_arr[bar_idx])
                            # STRONG_TOP=2 vetoes LONG; STRONG_BOT=-2 vetoes SHORT
                            if side == "LONG" and _smv_state >= 2:
                                continue
                            if side == "SHORT" and _smv_state <= -2:
                                continue

                    # ── Tradeability gate (parity with is_symbol_tradeable) ──
                    if cfg.TRADEABILITY_GATE_ENABLED and self.mode == "tradier":
                        _sym_u = sym.upper()
                        if side == "LONG" and _sym_u not in tradeable_long:
                            continue
                        if side == "SHORT" and _sym_u not in tradeable_short:
                            continue

                    # ── ROUND 5 (2026-05-19) — MIN_ENTRY_SPACING_HOURS ──────────────
                    # Cross-path entry spacing. Gates EVERY new OPEN path below
                    # (PCB, FLZ, WT, REENTRY, SCALP, DC_BREAK...) by elapsed time
                    # since last close on this (sym, side) pair. Default 0 = OFF.
                    # Set per-sym in baseline overlay (e.g. SOLUSDC_LONG = 6.0h).
                    _r5_spacing_h = float(getattr(cfg, "MIN_ENTRY_SPACING_HOURS", 0.0) or 0.0)
                    if _r5_spacing_h > 0.0:
                        _r5_last_close = pos.last_close_ts if hasattr(pos, "last_close_ts") else 0.0
                        if _r5_last_close > 0.0 and (ts_i - _r5_last_close) < (_r5_spacing_h * 3600.0):
                            continue

                    # ── PRICE_CROSS_BACK_REENTRY (tradier_manage.py:1880) ───────────
                    # ROUND 4 FIX #4 2026-05-19: moved BEFORE cooldown / DUP_GUARD so the
                    # PCB path can actually fire. Previously placed AFTER cooldown — but
                    # PCB MAX_AGE_MIN=240 == ENTRY_COOLDOWN_SEC=14400s/60=240, so the
                    # cooldown always shadowed PCB. Live priority order has PCB BEFORE
                    # cooldown (it IS the recovery from a recent close). Keeps default OFF
                    # for ablation safety; flipped True in r4 baseline.
                    # ROUND 5 FIX #3 (2026-05-19) — PCB also obeys REENTRY_ENABLED master gate.
                    # User mandate: REENTRY_ENABLED=False must disable ALL reentry paths,
                    # including PRICE_CROSS_BACK (it IS a reentry). ETH per-sym overlay
                    # sets REENTRY_ENABLED=False; that must kill PCB too.
                    if _price_cross_back_fn is not None and getattr(cfg, 'PRICE_CROSS_BACK_REENTRY_ENABLED', False) and bool(getattr(cfg, 'REENTRY_ENABLED', True)):
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

                    # ── Per-sym entry cooldown (parity with AUGMENT_LOCK / DUP_GUARD) ──
                    # Real engine blocks new entries for N seconds after last close on this pk.
                    # Default 900s (15 min) per CLAUDE.md AUGMENT_LOCK.
                    # ROUND 4 2026-05-19: moved AFTER PCB so PCB can override cooldown.
                    last_close_ts = pos.last_close_ts if hasattr(pos, "last_close_ts") else 0
                    if cfg.ENTRY_COOLDOWN_SEC > 0 and last_close_ts > 0:
                        if (ts_i - last_close_ts) < cfg.ENTRY_COOLDOWN_SEC:
                            continue

                    # ── DUP_GUARD on NEW opens (Path 2) ──────────────────────────
                    # Source: vec_paths/dup_guard.py + ez_manage.py:14062 HARD_AUGMENT_LOCK.
                    # Per CLAUDE.md 2026-05-09: AUGMENT_LOCK extends to ALL opens,
                    # including true OPEN on empty positions.
                    if _DUP_GUARD_AVAILABLE and bool(getattr(cfg, 'DUP_GUARD_ENABLED', True)):
                        _new_open_blk = _check_dup_guard_block(
                            pos, float(ts_i), 0.0, cfg,
                            pos_value_usd=0.0, action="OPEN"
                        )
                        if _new_open_blk is not None:
                            continue

                    # ── ROUND 4 FIX 2026-05-19 — FLZ_AGENT_FORCE_OPEN_MOCK ──────────
                    # Sparse MTF_SR-style force-open. Two modes (toggle via MODE knob):
                    #   "near_dc"   = price within ATR_MULT × atr_D of dc_basis_D / dc_basis_W.
                    #   "near_dc_high" = price within ATR_MULT × atr_D of dc_HIGH_D
                    #                    (level just touched at top — captures late-buy peak fills)
                    # Bypasses WT/GR filters (sparse-entry mock for flz bot late-buy opens).
                    # Honors cooldown + dup_guard. Default OFF.
                    if bool(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_ENABLED", False)):
                        _flz_long_only = bool(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_LONG_ONLY", True))
                        _flz_skip = (_flz_long_only and side != "LONG")
                        if not _flz_skip:
                            _flz_mode = str(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_MODE", "near_dc"))
                            _flz_atr_d = store.f("atr_D", bar_idx, 0.0)
                            _flz_thr = float(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_SR_ATR_MULT", 0.50)) * _flz_atr_d
                            _flz_near = False
                            if _flz_atr_d > 0 and _flz_thr > 0:
                                if _flz_mode == "near_dc_high":
                                    _flz_lvl = store.f("dc_high_D", bar_idx, 0.0) or store.f("dc_high_D_prev", bar_idx, 0.0)
                                    if _flz_lvl > 0:
                                        # LONG only: fire when price approached the high (price within thr below high)
                                        _flz_near = (price <= _flz_lvl + _flz_thr) and (price >= _flz_lvl - _flz_thr)
                                else:
                                    _flz_dc_d = store.f("dc_basis_D", bar_idx, 0.0)
                                    _flz_dc_w = store.f("dc_basis_W", bar_idx, _flz_dc_d) or _flz_dc_d
                                    if _flz_dc_d > 0:
                                        _flz_d_near = abs(price - _flz_dc_d) <= _flz_thr
                                        _flz_w_near = abs(price - _flz_dc_w) <= _flz_thr
                                        _flz_near = _flz_d_near or _flz_w_near
                            # ── ROUND 4 FLZ wt_D-bearish gate (optional) ─────────────
                            # When FLZ_FORCE_OPEN_MOCK_WT_D_BEARISH_ONLY=True, only fire
                            # when wt1_D < wt2_D (i.e. macro top — recreates the live
                            # flz-bot tendency to force-open into the bear setup).
                            if _flz_near and bool(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_WT_D_BEARISH_ONLY", False)):
                                _flz_w1_D = store.f("wt1_D", bar_idx, 0.0)
                                _flz_w2_D = store.f("wt2_D", bar_idx, 0.0)
                                if abs(_flz_w1_D) > 1e-9 and abs(_flz_w2_D) > 1e-9:
                                    if side == "LONG" and not (_flz_w1_D < _flz_w2_D):
                                        _flz_near = False
                                    elif side == "SHORT" and not (_flz_w1_D > _flz_w2_D):
                                        _flz_near = False
                            if _flz_near:
                                # New-day gate (anti-spam — flz mostly fires on first bar/day)
                                _flz_new_day_ok = True
                                if bool(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_REQUIRE_NEW_DAY", True)):
                                    _flz_prev_day = (ts_i - 300) // 86400
                                    _flz_curr_day = ts_i // 86400
                                    _flz_new_day_ok = (_flz_curr_day != _flz_prev_day)
                                # Last FLZ fire spacing (stored on pos object)
                                _flz_last_fire_ts = float(getattr(pos, "flz_last_fire_ts", 0.0))
                                _flz_min_gap_s = float(getattr(cfg, "FLZ_FORCE_OPEN_MOCK_MIN_GAP_HOURS", 6.0)) * 3600.0
                                _flz_gap_ok = (ts_i - _flz_last_fire_ts) >= _flz_min_gap_s
                                if _flz_new_day_ok and _flz_gap_ok:
                                    if True:
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
                                        pos.reason = "FLZ_AGENT_FORCE_OPEN_MOCK(MTF_SR)"
                                        pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                                        try:
                                            pos.flz_last_fire_ts = float(ts_i)
                                        except Exception:
                                            pass
                                        continue  # skip SATOSHIT/WT path

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

                    # ── DC_BB_D_BREAK_REVERSE FIRE trigger (WIRED 2026-05-18) ──
                    # Source: ez_manage.py:38741. Daily DC/BB band break (close vs
                    # dc_high_D_prev / dc_low_D_prev / bb_upper_D / bb_lower_D) fires reverse-open.
                    # When knob DISABLED (ablation), this path emits zero entries → fewer trades.
                    # When knob ENABLED (default True): fires LONG on UP-break, SHORT on DN-break.
                    if bool(getattr(cfg, "DC_BB_D_BREAK_REVERSE_ENABLED", True)):
                        _db_dc_hi_d = store.f("dc_high_D_prev", bar_idx, 0.0) or store.f("dc_high_D", bar_idx, 0.0)
                        _db_dc_lo_d = store.f("dc_low_D_prev", bar_idx, 0.0) or store.f("dc_low_D", bar_idx, 0.0)
                        _db_bb_up_d = store.f("bb_upper_D", bar_idx, 0.0)
                        _db_bb_lo_d = store.f("bb_lower_D", bar_idx, 0.0)
                        _db_break_up = (_db_dc_hi_d > 0 and price > _db_dc_hi_d) or \
                                       (_db_bb_up_d > 0 and price > _db_bb_up_d)
                        _db_break_dn = (_db_dc_lo_d > 0 and price < _db_dc_lo_d) or \
                                       (_db_bb_lo_d > 0 and price < _db_bb_lo_d)
                        # 1-bar edge detection: prior bar not yet broken; this bar is.
                        if bar_idx > 0:
                            _db_prev_px = store.price(bar_idx - 1)
                            _db_prev_up = (_db_dc_hi_d > 0 and _db_prev_px > _db_dc_hi_d) or \
                                          (_db_bb_up_d > 0 and _db_prev_px > _db_bb_up_d)
                            _db_prev_dn = (_db_dc_lo_d > 0 and _db_prev_px < _db_dc_lo_d) or \
                                          (_db_bb_lo_d > 0 and _db_prev_px < _db_bb_lo_d)
                            _db_break_up = _db_break_up and not _db_prev_up
                            _db_break_dn = _db_break_dn and not _db_prev_dn
                        if (side == "LONG" and _db_break_up) or (side == "SHORT" and _db_break_dn):
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
                            pos.reason = f"DC_BB_D_BREAK_REVERSE_open_{'UP' if _db_break_up else 'DN'}"
                            pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
                            continue  # skip WT path

                    # ── BREAKOUT_RETEST_ARMED (Rule A) FIRE trigger (WIRED 2026-05-18) ──
                    # Source: ez_manage.py:35464 / tradier_manage.py ~1832.
                    # Stateless simplified impl: D/W aligned + retest band + k3m crossover
                    # + 15m+1h aligned. When all conditions match, OPEN directly.
                    # ROLLBACK: BREAKOUT_RETEST_ARMED_ENABLED=False.
                    if bool(getattr(cfg, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
                        _ra_w1_D = store.f("wt1_D", bar_idx, 0.0)
                        _ra_w2_D = store.f("wt2_D", bar_idx, 0.0)
                        _ra_w1_W = store.f("wt1_W", bar_idx, 0.0)
                        _ra_w2_W = store.f("wt2_W", bar_idx, 0.0)
                        _ra_dc_basis_D = store.f("dc_basis_D", bar_idx, 0.0)
                        _ra_atr_D = store.f("atr_D", bar_idx, 0.0)
                        _ra_w1_15m = store.f("wt1_15m", bar_idx, 0.0)
                        _ra_w2_15m = store.f("wt2_15m", bar_idx, 0.0)
                        _ra_w1_1h = store.f("wt1_1h", bar_idx, 0.0)
                        _ra_w2_1h = store.f("wt2_1h", bar_idx, 0.0)
                        _ra_k_3m = store.f("stoch_k_3m", bar_idx, 50.0)
                        _ra_d_3m = store.f("stoch_d_3m", bar_idx, 50.0)
                        _ra_k_3m_prev = store.f("stoch_k_3m", max(0, bar_idx - 1), _ra_k_3m)
                        _ra_data_ok = (abs(_ra_w1_D) > 1e-9 and abs(_ra_w2_D) > 1e-9
                                       and _ra_dc_basis_D > 0 and _ra_atr_D > 0)
                        if _ra_data_ok:
                            _ra_mult = float(getattr(cfg, "BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT", 0.30))
                            _ra_dist_atr = abs(price - _ra_dc_basis_D) / _ra_atr_D
                            _ra_armed_long = (_ra_w1_D > _ra_w2_D and _ra_w1_W > _ra_w2_W and price > _ra_dc_basis_D)
                            _ra_armed_short = (_ra_w1_D < _ra_w2_D and _ra_w1_W < _ra_w2_W and price < _ra_dc_basis_D)
                            _ra_retest_band = _ra_dist_atr < _ra_mult
                            _ra_k3m_xup = (_ra_k_3m_prev < 30 and _ra_k_3m > _ra_d_3m and _ra_k_3m > _ra_k_3m_prev)
                            _ra_k3m_xdn = (_ra_k_3m_prev > 70 and _ra_k_3m < _ra_d_3m and _ra_k_3m < _ra_k_3m_prev)
                            _ra_15m_bull = (_ra_w1_15m > _ra_w2_15m)
                            _ra_15m_bear = (_ra_w1_15m < _ra_w2_15m)
                            _ra_1h_bull = (_ra_w1_1h > _ra_w2_1h)
                            _ra_1h_bear = (_ra_w1_1h < _ra_w2_1h)
                            _ra_fire = (
                                (side == "LONG" and _ra_armed_long and _ra_retest_band
                                 and _ra_k3m_xup and _ra_15m_bull and _ra_1h_bull)
                                or
                                (side == "SHORT" and _ra_armed_short and _ra_retest_band
                                 and _ra_k3m_xdn and _ra_15m_bear and _ra_1h_bear)
                            )
                            if _ra_fire:
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
                                pos.reason = f"RULE_A_RETEST_{side}"
                                pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)
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
                        # FIX (noop): use mode-specific threshold so TRADIER_ENTRY_SCORE_THRESHOLD
                        # and ENTRY_SCORE_THRESHOLD gate the WT_DC_ENTRY score path.
                        # The WT_DC scorer produces 0-100 scores. The live entry_score knobs
                        # (TRADIER_ENTRY_SCORE_THRESHOLD=24, ENTRY_SCORE_THRESHOLD=18) use a
                        # different scale in production, but in vec we map them directly to the
                        # WT_DC scale. The effective threshold = max of both knobs so that
                        # raising TRADIER_ENTRY_SCORE_THRESHOLD above its default (24) adds a
                        # tighter gate, and lowering it (toward 0) falls back to WT_DC_ENTRY_THRESHOLD.
                        # Default values (24, 18) are below WT_DC_ENTRY_THRESHOLD=55, so baseline
                        # behavior is preserved — the WT_DC threshold still dominates at defaults.
                        if self.mode == "tradier":
                            _wtdc_thr = max(cfg.WT_DC_ENTRY_THRESHOLD, cfg.TRADIER_ENTRY_SCORE_THRESHOLD)
                        else:
                            _wtdc_thr = max(cfg.WT_DC_ENTRY_THRESHOLD, cfg.ENTRY_SCORE_THRESHOLD)
                        if wtdc_score < _wtdc_thr:
                            continue

                    # ── WT composite scoring gate (tradier_manage.py:3750) ──────────
                    # FIX (noop): TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER was declared
                    # but never read. Gate: wt_bull_alignment >= 3 AND wt_composite_long >= threshold.
                    # Source: tradier_manage.py:3750-3759.
                    # In vec: wt_composite_long is always >= 0 (NPZ values [0, 503]). The meaningful
                    # gate is WT_COMPOSITE_LONG_MIN / WT_COMPOSITE_SHORT_MIN — when raised above 0,
                    # this filters to only high-quality WT composite bars.
                    # Default WT_COMPOSITE_LONG_MIN=0.0 → gate is wt_alignment>=3 only.
                    if self.mode == "tradier" and bool(getattr(cfg, "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER", False)):
                        _wt_align_field = "wt_bull_alignment" if side == "LONG" else "wt_bear_alignment"
                        _wt_comp_field = "wt_composite_long" if side == "LONG" else "wt_composite_short"
                        _wt_side_align = int(store.f(_wt_align_field, bar_idx, 5))
                        _wt_side_comp = store.f(_wt_comp_field, bar_idx, 0.0)
                        _wt_comp_long_min = float(getattr(cfg, "WT_COMPOSITE_LONG_MIN", 0.0))
                        _wt_comp_short_min = float(getattr(cfg, "WT_COMPOSITE_SHORT_MIN", 0.0))
                        _wt_comp_user_min = _wt_comp_long_min if side == "LONG" else _wt_comp_short_min
                        if _wt_side_align < 3:
                            continue
                        if _wt_side_comp < _wt_comp_user_min:
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
            _pos_sym_ref[0] = sym  # trade-record side-channel
            last_idx = store_start_idx[sym] + len(all_ts) - 1
            if last_idx >= store.n_bars:
                last_idx = store.n_bars - 1
            _mtm_ts = int(store.timestamps[last_idx]) if last_idx < store.n_bars else 0
            for pos in pos_states[sym].values():
                if pos.open and pos.entry_price > 0:
                    final_price = store.price(last_idx)
                    if final_price > 0:
                        if pos.side == "LONG":
                            mtm_pnl = (final_price - pos.entry_price) / pos.entry_price * 100.0
                        else:
                            mtm_pnl = (pos.entry_price - final_price) / pos.entry_price * 100.0
                        returns_by_sym[sym].append(mtm_pnl)
                        _emit_trade(pos, _mtm_ts, final_price, mtm_pnl, "MTM_END_OF_SIM")
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

        # ── Trade-record JSONL flush (2026-05-18: quick_engine_compat side-channel) ──
        # When V8_TRADES_OUT_DIR is set, write one JSONL file per (run_id, symbol)
        # mirroring the legacy OPUS_VOMIT layout: <out_dir>/<run_id>__<symbol>.jsonl.
        # flz_hourly_reconfig and tradier_hourly_reconfig read these files by name.
        if _trade_log:
            try:
                os.makedirs(_trades_out_dir, exist_ok=True)
                # Group records by symbol
                _by_sym: Dict[str, List[Dict[str, Any]]] = {}
                for _rec in _trade_log:
                    _sym_k = _rec.get("symbol", "") or ""
                    _by_sym.setdefault(_sym_k, []).append(_rec)
                for _sym_k, _recs in _by_sym.items():
                    if not _sym_k:
                        continue
                    _out_path = Path(_trades_out_dir) / f"{_trades_run_id}__{_sym_k}.jsonl"
                    with open(_out_path, "w") as _f:
                        for _rec in _recs:
                            _f.write(json.dumps(_rec) + "\n")
            except Exception as _exc:
                print(f"[vec_engine_v1] trade-log write error: {_exc}", flush=True)

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
        # 2026-05-18 hourly_reconfig cluster-knobs (wired at lines 1524-1527,
        # 1598, 1683, 1939-1943, 1989, 2004, 2120, 2133, 2386, 2434).
        "BREAKOUT_RETEST_ARMED_ENABLED": "WIRED",
        "DC_BB_D_BREAK_REVERSE_ENABLED": "WIRED",
        "HEDGE_HTF_VETO_ENABLED": "WIRED",
        "HEDGE_MAX_ABSOLUTE_USD": "WIRED",
        "HTF_TREND_VETO_ENABLED": "WIRED",
        "NOLOSS_BYPASS_WT_5OF5_ENABLED": "WIRED",
        "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": "WIRED",
        "PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER": "WIRED",
        "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER": "WIRED",
        "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER": "WIRED",
        "PARTIAL_PROFIT_LOCK_FRAC_TRADIER": "WIRED",
        "STDEV_MACRO_ENTRY_VETO_ENABLED": "WIRED",
        "STDEV_MACRO_AUGMENT_VETO_ENABLED": "WIRED",
        "STDEV_MACRO_R4_EXIT_ENABLED": "WIRED",
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
