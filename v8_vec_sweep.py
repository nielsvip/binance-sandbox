"""v8_vec_sweep.py — fast pure-vectorized sweep engine.

Standalone replacement for backtest_v8_engine.py when running millions of
parameter variations. Uses ONLY the parity-tested vec_paths modules and
position_evaluator vec functions — no imports from ez_manage / ez_positions_quick
/ tradier_manage / live code at all.

DESIGN
======
- Per-symbol loop, fully vectorized across the symbol's entire bar history.
- Numpy hot loop: pull NPZ once, build per-bar gates via vec functions,
  then iterate bars in Python ONLY to mutate the single-symbol position
  state (entry_price, qty, gain etc.) — every gate decision was already
  computed in bulk.
- Trade events appended to JSONL in /history/<acct>/<SYM>_<SIDE>.jsonl-compatible
  format so existing tooling (dashboards :5057 / :5077) reads them.
- Every Sharpe number written goes through metrics_guard — NO bare sharpe
  emission anywhere. Sub-floor samples get the [DIAGNOSTIC] tag.

CONSTRAINTS HONORED
===================
- NO LIES MANDATE: all sharpe via metrics_guard.format_standard_set
- IMPOSTER BLOCK: no single-symbol "BEST" — multi-sym pool only at summary
- Per-trade returns dataset is written to a paired _trades.jsonl

USAGE
=====
    python v8_vec_sweep.py --mode crypto --account flz --symbols BTC,ETH,SOL \\
        --start 2026-01-01 --workers 1

    # smoke test (1 sym × 1 week)
    python tools/test_v8_vec_sweep_smoke.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Import the vec functions
sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_guard  # NO-LIES MANDATE — every sharpe writer must import this

from position_evaluator import (
    evaluate_reentry_vec,
    compute_trade_qty_vec,
    evaluate_exit_gates_vec,
    evaluate_noloss_gate_vec,
    evaluate_augment_eligibility_vec,
    BLOCK_NAMES,
    EXIT_NAMES,
    EXIT_NONE,
    EXIT_DC_HOPELESS,
    EXIT_WT_4H_VEL,
    EXIT_WT_EXHAUST,
    EXIT_WT_PERCENTILE,
    EXIT_E1_WT_DELTA,
    EXIT_E3_STRUCTURE,
    _SUB_GATE_TO_INT,
    _INT_TO_SUB_GATE,
)

# Optional vec modules — wrap import so a stripped repo still runs
try:
    from vec_paths.hedge_scan_gates import evaluate_hedge_scan_gates_vec
except ImportError:
    evaluate_hedge_scan_gates_vec = None
try:
    from vec_paths.newborn_protect import evaluate_newborn_protect_vec
except ImportError:
    evaluate_newborn_protect_vec = None
try:
    from vec_paths.cooldown_locks import evaluate_cooldown_locks_vec
except ImportError:
    evaluate_cooldown_locks_vec = None

# 2026-05-17 VEC_OVERTRADE_FIX — live-parity HARD_AUGMENT_LOCK + DUP_GUARD gate.
# vec_paths/dup_guard.py:check_dup_guard_block() mirrors ez_manage.py:10970-10988
# + 14062-14081 and is already used by backtest_v8_engine via _v8_vec_short_circuit.
# vec_sweep was importing nothing from there → OPEN path fired on every wt_3m-aligned
# bar with no post-CLOSE cooldown (root cause of 156k-trades-vs-slow-77 over-trade).
try:
    from vec_paths.dup_guard import check_dup_guard_block as _vec_check_dup_guard
except ImportError:
    _vec_check_dup_guard = None

try:
    from vec_paths.protect_balance_overtrade import evaluate_protect_balance_overtrade_vec
except ImportError:
    evaluate_protect_balance_overtrade_vec = None
try:
    from vec_paths.circuit_sharpe_gates import evaluate_circuit_sharpe_gates_vec
except ImportError:
    evaluate_circuit_sharpe_gates_vec = None
try:
    from vec_paths.tradeable_state_gates import evaluate_tradeable_state_gates_vec
except ImportError:
    evaluate_tradeable_state_gates_vec = None
try:
    from vec_paths.open_intent_size_gates import evaluate_open_intent_size_gates_vec
except ImportError:
    evaluate_open_intent_size_gates_vec = None
try:
    from vec_paths.quarantine_strategy_validation import evaluate_quarantine_vec
except ImportError:
    evaluate_quarantine_vec = None
try:
    from vec_paths.stale_mark_price import evaluate_stale_mark_block_vec
except ImportError:
    evaluate_stale_mark_block_vec = None
try:
    from vec_paths.emergency_brake import BrakeLookup
except ImportError:
    BrakeLookup = None
try:
    from vec_paths.golden_rule_htf_vote import evaluate_gr_htf_vec
except ImportError:
    evaluate_gr_htf_vec = None
try:
    from vec_paths.exit_r1_r2 import (
        check_r1_emergency_exit,
        check_r2_wt_vel_slow_exit,
    )
except ImportError:
    check_r1_emergency_exit = None
    check_r2_wt_vel_slow_exit = None
try:
    from vec_paths.wt_crossunder_final import check_wt_crossunder_final_exit
except ImportError:
    check_wt_crossunder_final_exit = None
try:
    from vec_paths.partial_profit_lock_v2 import check_ppl_step1, check_ppl_step2, check_ppl_step3
except ImportError:
    check_ppl_step1 = None
    check_ppl_step2 = None
    check_ppl_step3 = None
try:
    from vec_paths.golden_rule_enforce import check_golden_rule_enforce
except ImportError:
    check_golden_rule_enforce = None
try:
    from vec_paths.peak_giveback_be_erosion import check_peak_giveback_exit, check_be_erosion_exit
except ImportError:
    check_peak_giveback_exit = None
    check_be_erosion_exit = None
try:
    from vec_paths.reduce_paths import check_profit_take_reduce, check_strong_reduce_k
except ImportError:
    check_profit_take_reduce = None
    check_strong_reduce_k = None
try:
    from vec_paths.delta_engine import check_delta_entry
except ImportError:
    check_delta_entry = None
# TR_TREND_v1 — Daily-decision breakout-retest stock strategy (spec: data/research_20260516/strategy_plan.md §4)
# Default-OFF per CLAUDE.md NEW STRATEGY PROHIBITION; sweep-validate before any live enable.
try:
    from vec_paths.tr_trend_v1 import (
        build_tr_trend_v1_arrays,
        evaluate_tr_trend_v1_entry,
        evaluate_tr_trend_v1_exit,
        compute_tr_trend_v1_full_unit_qty,
    )
except ImportError:
    build_tr_trend_v1_arrays = None
    evaluate_tr_trend_v1_entry = None
    evaluate_tr_trend_v1_exit = None
    compute_tr_trend_v1_full_unit_qty = None


# ════════════════════════════════════════════════════════════════════════════════
# Adapters: bridge v8_vec_sweep's npz dict / SymState to vec_paths store/pos_state API
# ════════════════════════════════════════════════════════════════════════════════

class _NPZStoreAdapter:
    """Wraps npz dict so vec_paths check_* functions can call store.f(key, idx)."""
    __slots__ = ("_npz", "_close", "_ts")
    def __init__(self, npz: Dict[str, Any], close: np.ndarray, ts: np.ndarray):
        self._npz = npz; self._close = close; self._ts = ts
    def f(self, key: str, idx: int, default: float = 0.0) -> float:
        arr = self._npz.get(key)
        if arr is None or idx >= len(arr): return default
        try: return float(arr[idx])
        except Exception: return default
    def price(self, idx: int) -> float:
        return float(self._close[idx])
    @property
    def timestamps(self) -> np.ndarray: return self._ts


class _PosStateAdapter:
    """Wraps SymState so vec_paths check_* functions see pos_state.side/gain_pct/etc.
    PPL mutable fields (ppl_fired etc.) live HERE, not on SymState.
    gain_pct MUST be updated each bar: pos_adapter.gain_pct = gain."""
    __slots__ = ("_state", "gain_pct", "ppl_fired", "ppl_first_exit_price",
                 "ppl_stop_level", "ppl_stop_upgraded")
    def __init__(self, state: "SymState"):
        self._state = state
        self.gain_pct: float = 0.0
        self.ppl_fired: bool = False
        self.ppl_first_exit_price: float = 0.0
        self.ppl_stop_level: float = 0.0
        self.ppl_stop_upgraded: bool = False
    @property
    def open(self) -> bool: return self._state.qty > 0.0001
    @property
    def side(self) -> str: return "LONG" if self._state.is_long else "SHORT"
    @property
    def max_gain_pct(self) -> float: return self._state.max_gain
    @property
    def entry_price(self) -> float: return self._state.entry_price
    @property
    def entry_ts(self) -> float: return self._state.opened_at
    @property
    def qty(self) -> float:
        return self._state.qty if self._state.is_long else -self._state.qty
    def reset_ppl(self):
        self.ppl_fired = False
        self.ppl_first_exit_price = 0.0
        self.ppl_stop_level = 0.0
        self.ppl_stop_upgraded = False


REPO_ROOT = Path(__file__).resolve().parent
NPZ_DIR = REPO_ROOT / "backtest_v8" / "indicators"
KLINES_CRYPTO_DIR = REPO_ROOT / "klines_cache_backtest"
KLINES_STOCK_DIR = REPO_ROOT / "klines_cache_backtest" / "tradier"
SWEEP_RESULTS_DIR = REPO_ROOT / "data" / "sweep_results"
HISTORY_DIR = REPO_ROOT / "data" / "history"

# Session-scoped abort + fingerprint files — used to stop sibling processes on
# duplicate/invalid results. Set VEC_SESSION env var to share across a sweep batch.
_VEC_SESSION = os.getenv("VEC_SESSION", str(os.getpid()))
_SESSION_ABORT_PATH = Path(f"/tmp/vec_sweep_abort_{_VEC_SESSION}")
_SESSION_FP_PATH = Path(f"/tmp/vec_sweep_fingerprints_{_VEC_SESSION}.json")


# ════════════════════════════════════════════════════════════════════════════════
# SweepConfig — sane defaults for every knob the vec functions read via getattr
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class SweepConfig:
    """All knobs needed by the vec modules. Defaults mirror live config.py
    where they exist. Override via CLI --override KEY=VAL pairs."""

    # ── position sizing ───────────────────────────────────────────────────
    START_POSITION_SIZE: float = 55.0
    MIN_POSITION_SIZE: float = 25.0
    MIN_GAIN: float = 3.0
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    COMMISSION_BUFFER_PCT: float = 0.10
    ROUND_TRIP_COST_PCT: float = 0.10  # bid-ask spread + slippage; deducted from every trade return
    WT_HTF_DISCOUNT_ENABLED: bool = True
    # ── reentry blocks ────────────────────────────────────────────────────
    REENTRY_B15_STRONG_TREND_ENABLED: bool = True
    REENTRY_B04_DC_RETEST_ENABLED: bool = True
    REENTRY_B11_DC_BREAK_ENABLED: bool = True
    REENTRY_B02_BC156_BOTTOM_ENABLED: bool = True
    REENTRY_B12_WT_MOM_ENABLED: bool = True
    REENTRY_B14_HA_TREND_ENABLED: bool = True
    REENTRY_B10_STOCH_REV_ENABLED: bool = True
    REENTRY_B01_WT_2of3_ENABLED: bool = False
    REENTRY_B09_SNAPBACK_ENABLED: bool = False
    # ── exit gates ────────────────────────────────────────────────────────
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
    WT_PERCENTILE_EXIT_ENABLED: bool = False
    WT_PERCENTILE_EXIT_OB_D: float = 90.0
    WT_PERCENTILE_EXIT_OB_4H: float = 75.0
    WT_PERCENTILE_EXIT_OS_D: float = 10.0
    WT_PERCENTILE_EXIT_OS_4H: float = 25.0
    E_1_WT_EXIT_USE_DELTA_ENABLED: bool = False
    E_1_EXIT_DELTA_THR: float = 50.0
    E_3_USE_WT_STRUCTURE_EXIT_MODE: int = 0
    # ── noloss + hedge ────────────────────────────────────────────────────
    UNIVERSAL_NOLOSS_GATE: bool = True
    UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL: bool = True
    UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS: tuple = (
        "R1_DC_LOW4_3M_EMERGENCY", "R2_WT_VEL_SLOW", "WT_15M_VEL_SLOW",
        "HEDGE_FAILED", "STRUCTURAL_RANGE_SHIFT", "WT_3M_FORCE_OPEN",
    )
    OBLIGATORY_HEDGE_ENABLED: bool = True
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.5
    OBLIGATORY_HEDGE_PCT: float = 1.0
    OBLIGATORY_HEDGE_WT_USE_1M: bool = False
    OBLIGATORY_HEDGE_WT_USE_3M: bool = True
    OBLIGATORY_HEDGE_WT_USE_15M: bool = False
    OBLIGATORY_HEDGE_WT_USE_1H: bool = True
    OBLIGATORY_HEDGE_WT_TFS_REQUIRED: int = 0
    HEDGE_MODE: bool = True
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H: bool = True
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = False
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = True
    HEDGE_FAILED_FALLBACK_CLOSE_ENABLED: bool = True
    HEDGE_COMPLETED_LOCKOUT_SECONDS: float = 60.0
    # ── augment / dup guard ───────────────────────────────────────────────
    DUP_GUARD_USE_GAIN_GATE: bool = True   # 2026-05-17 VEC_OVERTRADE_FIX: match live default
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5
    PULLBACK_AUGMENT_ENABLED: bool = True
    PULLBACK_AUGMENT_REVERSAL_MIN: float = 1.0
    HARD_AUGMENT_LOCK_SECONDS: float = 900.0
    HARD_REDUCE_LOCK_SECONDS: float = 60.0
    AUGMENTATION_COOLDOWN_SECONDS: float = 540.0
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = True
    EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED: bool = True
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5
    # ── 2026-05-17 VEC_OVERTRADE_FIX (master kill flag) ──────────────────
    # Vec engine was producing 156,601 trades on crypto BTC+ETH × 4.37yr while
    # slow engine (backtest_v8_sweep.py) produces 77. Root cause: vec OPEN path
    # (simulate_one_symbol L809-902) fires on every bar where `wt_3m_aligned[i]`
    # is True, with NO post-CLOSE cooldown / HARD_AUGMENT_LOCK / DUP_GUARD_GAIN.
    # Slow engine enforces all of these in backtest_v8_engine.py:2473-2486 +
    # 2456-2467. When this flag is True (default), vec mirrors slow-engine
    # semantics via vec_paths.dup_guard.check_dup_guard_block(). Set False ONLY
    # to reproduce pre-fix over-trading behaviour for A/B comparison.
    VEC_OVERTRADE_FIX_ENABLED: bool = True
    # AUGMENT_LOCK_MIN_SECONDS — alias read by vec_paths/dup_guard.py:66
    # (check_dup_guard_block reads AUGMENT_LOCK_MIN_SECONDS, slow engine reads
    # HARD_AUGMENT_LOCK_SECONDS). Mirror live config.py default (900s = 15min).
    AUGMENT_LOCK_MIN_SECONDS: float = 900.0
    # ── 2026-05-17 VEC_NOLOSS_GATE (master kill flag) ─────────────────────
    # First-honest-tradier sweep showed mtm_n=63 open losers (avg -22%) caught
    # only at simulation end by MTM_FINAL_BAR_NOLIES_RULE2 — a CLAUDE.md sacred-rule
    # violation ("WE HEDGE OR WE CLOSE — WE NEVER HOLD"). evaluate_noloss_gate_vec
    # was IMPORTED at L55 but only consulted INSIDE `if exit_id != EXIT_NONE:`
    # (L1254-1276); positions sitting in -22% with no exit trigger sailed past it.
    # When True (default), every bar where state.qty>0 AND gain<comm_buf calls
    # the noloss gate inline; HEDGE_FAILED_FALLBACK_CLOSE returns from gate
    # close the position immediately, OBLIGATORY_HEDGE returns open a vec hedge.
    # Set False to reproduce pre-fix MtM-only behaviour.
    VEC_NOLOSS_GATE_ENABLED: bool = True
    # ── newborn protect ───────────────────────────────────────────────────
    NEWBORN_PROTECT_ENABLED: bool = True
    NEWBORN_PROTECT_GRACE_SECONDS: float = 900.0
    # ── intent / size ─────────────────────────────────────────────────────
    ABSOLUTE_OPEN_LOCK_SECONDS: float = 30.0
    PREFLIGHT_INTENT_LOCK_SECONDS: float = 15.0
    HARD_SIZE_GATE_ENABLED: bool = True
    HARD_SIZE_GAIN_FLOOR: float = 3.0
    # ── circuit / sharpe gates (default OFF; engine treats absent as 0) ───
    CIRCUIT_BREAKER_ENABLED: bool = False
    SHARPE_HOUR_FLOOR_ENABLED: bool = False
    REGIME_FLOOR_ENABLED: bool = False
    VOLUME_FLOOR_ENABLED: bool = False
    # ── quarantine ────────────────────────────────────────────────────────
    QUARANTINE_ENFORCE_ENABLED: bool = False  # backtest = no quarantine list
    QUARANTINE_BYPASS_HEDGE: bool = True
    QUARANTINE_BYPASS_REENTRY_ZERO_POS: bool = True
    # ── stale mark ────────────────────────────────────────────────────────
    EXECUTE_NOW_MAX_MARK_AGE_S: float = 60.0
    # ── ratio sizing (kept as knob; backtest reads as multiplier) ─────────
    RATIO_MULTIPLIER: float = 4.0
    # ── DC stop loss sweep flags ──────────────────────────────────────────
    DC_LOW4_STOP_ENABLED: bool = False   # stop at dc_low4_<TF> recorded at entry
    DC_LOW_STOP_ENABLED: bool = False    # stop at dc_low_<TF> (1-bar)
    DC_STOP_TF: str = ""                 # override TF (empty = auto: "3m" crypto / "5m" tradier)
    # ── GR multiplier exit sweep flags ────────────────────────────────────────
    GR_EXIT_ENABLED: bool = False        # exit when GR votes ≥ MIN_TFS TFs × MIN_IND each AND wt1_3m against
    GR_EXIT_MIN_TFS: int = 3             # TFs that must each reach GR_EXIT_MIN_IND (default 3×3=9)
    GR_EXIT_MIN_IND: int = 3             # indicators per TF that must agree against trade
    # ── R1 DC emergency exit ──────────────────────────────────────────────────
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True
    R1_NEWBORN_WINDOW_MIN: float = 15.0
    R1_USE_DC_4BAR: bool = True
    R1_TF: str = ""          # auto: "3m" crypto / "5m" tradier
    # ── R2 WT velocity slow exit ──────────────────────────────────────────────
    WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = True
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01
    WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10
    WT_VEL_DECEL_RATIO: float = 0.5
    WT_VEL_USE_DECEL_RATIO_ONLY: bool = True
    WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1
    R2_PEAK_MIN_PCT: float = 0.5
    R2_TF_LIST: Optional[List[str]] = None   # None = auto per mode
    # ── WT crossunder final exit ──────────────────────────────────────────────
    WT_CROSSUNDER_FINAL_ENABLED: bool = True
    WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED: bool = False
    WT_CROSSUNDER_FINAL_NOLOSS_BYPASS: bool = False
    # ── GOLDEN_RULE entries ───────────────────────────────────────────────────
    GOLDEN_RULE_ENABLED: bool = True
    GOLDEN_RULE_BASE_USD: float = 5.0
    GOLDEN_RULE_COOLDOWN_S: float = 600.0
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
    GOLDEN_RULE_HTF_VETO_ENABLED: bool = False
    GOLDEN_RULE_MIN_IND: int = 1
    GOLDEN_RULE_HTF_MIN_TFS: int = 0
    GR_DC_EXTENDED_LONG: float = 0.65  # DC extension threshold for HTF confirmation (0=use module default)
    GR_BB_EXTENDED_LONG: float = 0.75  # BB pct-b threshold for HTF confirmation (0=use module default)
    # ── Partial Profit Lock (PPL) ────────────────────────────────────────────
    PARTIAL_PROFIT_LOCK_ENABLED: bool = True
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 0.5
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 0.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.10
    # ── peak giveback / BE erosion exits ─────────────────────────────────────
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED: bool = False   # default OFF (user disabled 2026-05-11)
    PEAK_GIVEBACK_DROP_PCT: float = 0.5
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.5
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = False      # OFF since 2026-04-27
    PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN: bool = True
    PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT: float = -0.5
    MIN_HOLD_MINUTES_CRYPTO: float = 30.0
    TRADIER_MIN_HOLD_MINUTES: float = 240.0
    BREAKEVEN_GRACE_MINUTES: float = 5.0
    BE_EROSION_ENABLED: bool = False                   # default OFF
    BE_EROSION_MIN_PEAK_PCT: float = 0.5
    BE_EROSION_FLOOR_PCT: float = 0.0
    BE_EROSION_HOLD_MIN_MIN: float = 15.0
    # ── profit-take reduce paths ──────────────────────────────────────────────
    PROFIT_TAKE_REDUCE_ENABLED: bool = False           # default OFF
    PROFIT_TAKE_GAIN_PCT: float = 2.0
    PROFIT_TAKE_REDUCE_FRAC: float = 0.5
    STRONG_REDUCE_K_ENABLED: bool = False              # default OFF
    SRK_K15M_LONG_MIN: float = 80.0
    SRK_K1H_LONG_MAX: float = 30.0
    SRK_K15M_SHORT_MAX: float = 20.0
    SRK_K1H_SHORT_MIN: float = 70.0
    SRK_REDUCE_FRAC: float = 0.5
    K1M_EXTREME_REVERSE_ENABLED: bool = False          # default OFF
    # ── Hedge engine (sweep model) ───────────────────────────────────────────
    HEDGE_SCAN_ENABLED: bool = True         # master switch for sweep hedge model
    HEDGE_MIN_LOSS_PCT: float = -0.5        # matches OBLIGATORY_HEDGE_MIN_LOSS_PCT
    HEDGE_QTY_PCT: float = 1.0              # hedge qty as fraction of main qty
    HEDGE_CLOSE_ON_WT3M_FLIP: bool = True   # close hedge when wt1_3m turns back
    GR_HEDGE_SCORE_FLOOR: int = 6           # GR against-score floor for hedge trigger (0=disabled)
    GR_HEDGE_REQUIRE_WT3M: bool = True      # always require wt1_3m against for hedge
    HEDGE_TRIGGER_REQUIRE_15M_OR_1H: bool = True  # require 15m or 1h WT alignment (can be overridden by GR)
    # ── IN_GAIN_TREND exit ────────────────────────────────────────────────────
    IN_GAIN_TREND_EXIT_ENABLED: bool = True
    IN_GAIN_TREND_BIG_WINNER_PCT: float = 10.0    # gain threshold for HTF tier
    IN_GAIN_TREND_MED_WINNER_PCT: float = 5.0     # gain threshold for 15m tier
    IN_GAIN_TREND_MIN_GAIN: float = 2.0           # minimum gain to even check
    # ── DELTA_ENGINE entries ──────────────────────────────────────────────────
    DELTA_ENGINE_ENABLED: bool = False          # default OFF (matches live default)
    DELTA_ENTRY_ENABLED: bool = False
    DELTA_ENTRY_MIN_TF: int = 2
    DELTA_ENTRY_Z_THRESHOLD: float = 1.5
    DELTA_SPEED_SMOOTH: int = 5
    DELTA_TF_Z_THRESHOLD: float = 1.5
    DELTA_HTF_GATE: str = "none"
    # ── WT_15M_BOUNCE_OPEN — fresh 15m cross within BB + 4h/1h HTF in favor ─────
    WT_15M_BOUNCE_OPEN_ENABLED: bool = False
    WT_15M_BOUNCE_MAX_BARS_AGO: int = 2       # bars since 15m cross (2 bars = 30min)
    WT_15M_BOUNCE_BB_MIN: float = 0.05        # bb_pct_b lower bound (within BB)
    WT_15M_BOUNCE_BB_MAX: float = 0.95        # bb_pct_b upper bound (within BB)
    WT_15M_BOUNCE_REQUIRE_BOTH_HTF: bool = False  # False=OR(4h,1h), True=AND(4h,1h)
    # ── STRUCTURAL-PATTERN GATES (2026-05-15) — default OFF, sweep-A/B before live ──
    # A1: SPY > 200SMA top-level regime gate (Faber/Antonacci/Clenow/Connors universal)
    SPY_REGIME_GATE_ENABLED: bool = False
    SPY_REGIME_BLOCK_LONGS_BELOW: bool = True   # block longs when SPY_close_D < SPY_sma200_D
    SPY_REGIME_BLOCK_SHORTS_ABOVE: bool = False  # block shorts when SPY > 200SMA (opt-in)
    # A3: ATR-parity sizing (Clenow/Dunn/Mulvaney/AQR universal). Replaces base_qty sizing.
    SIZING_MODE: str = "DEFAULT"                # DEFAULT | ATR_PARITY
    TARGET_RISK_PER_TRADE_PCT: float = 0.20     # % of equity risked per trade (0.20% = aggressive)
    ATR_PARITY_EQUITY_BASE_USD: float = 35000.0 # nominal sleeve capital (50% of $70k trb+trc)
    ATR_PARITY_USE_DAILY: bool = True           # True=atr_D (audited-winner standard), False=atr_base_tf
    ATR_PARITY_QTY_CAP_MULT: float = 5.0        # cap qty at 5× DEFAULT (prevents runaway low-vol sizes)
    # A4: Daily-decision TF gate (Minervini/Clenow/Faber/Antonacci universal).
    # When DAILY, entry/exit decisions fire only on the LAST base-TF bar of each trading day.
    DECISION_TF_MODE: str = "BASE"              # BASE | DAILY
    # B2: Connors mean-reversion overlay — when stock > 200SMA AND connors_rsi_D < threshold,
    # take long with 5-day SMA exit. Standalone entry trigger (6th OPEN trigger when flat).
    CONNORS_RSI2_OVERLAY_ENABLED: bool = False
    CONNORS_RSI2_THRESHOLD: float = 10.0        # connors_rsi composite (NPZ field connors_rsi_D)
    CONNORS_RSI2_REQUIRE_ABOVE_200SMA: bool = True
    CONNORS_RSI2_EXIT_BARS: int = 5             # time-based exit (5 trading days)
    # SIDE-ASYMMETRIC SIZING (2026-05-16) — preserve short-side detection but cap downside in bull regimes.
    # Default 1.0/1.0 → no behavior change. Bull-bias example: LONG=1.5 / SHORT=0.25
    #   → longs get 1.5× capital, shorts get 0.25× (busted short loses 1/4 the dollars).
    # Applied to OPEN base_qty BEFORE compute_trade_qty_vec; AUGMENTs inherit proportionally.
    LONG_SIZE_MULT: float = 1.0
    SHORT_SIZE_MULT: float = 1.0
    # ── TR_TREND_v1 (2026-05-17 build, spec §4) — DEFAULT-OFF NEW STRATEGY ────────
    # When True, simulate_one_symbol uses ONLY the TR_TREND_v1 D-decision breakout-
    # retest paths (legacy wt_3m / reentry / GR / DELTA / connors triggers disabled
    # to test the pure strategy). Mandatory sweep proof before any live enable
    # per CLAUDE.md NEW STRATEGY PROHIBITION.
    TR_TREND_V1_ENABLED: bool = False
    TR_TREND_V1_DC_LOOKBACK_D: int = 20
    TR_TREND_V1_VOL_MULT: float = 1.5
    TR_TREND_V1_VOL_SMA_LEN_D: int = 50
    TR_TREND_V1_TT_NEAR_HIGH_PCT: float = 25.0
    TR_TREND_V1_TT_MIN_PASS: int = 4
    TR_TREND_V1_SMA50_LEN_D: int = 50
    TR_TREND_V1_SMA200_LEN_D: int = 200
    TR_TREND_V1_SMA200_SLOPE_LOOKBACK_D: int = 10
    TR_TREND_V1_W52_BARS_D: int = 252
    TR_TREND_V1_ATR_STOP_MULT: float = 2.0
    TR_TREND_V1_RETEST_TOL_PCT: float = 0.5
    TR_TREND_V1_RETEST_VOL_MAX_MULT: float = 0.7
    TR_TREND_V1_RETEST_MAX_BARS_D: int = 5
    TR_TREND_V1_TIME_STOP_BARS_D: int = 60
    TR_TREND_V1_TIME_STOP_NO_HIGH_BARS_D: int = 30
    TR_TREND_V1_SPY_REGIME_ENABLED: bool = True
    TR_TREND_V1_SPY_SLOPE_LOOKBACK_D: int = 10
    TR_TREND_V1_RISK_PCT: float = 0.5
    TR_TREND_V1_ACCOUNT_USD: float = 35000.0


# ════════════════════════════════════════════════════════════════════════════════
# NPZ + klines loader
# ════════════════════════════════════════════════════════════════════════════════

def _resolve_npz_path(symbol: str, mode: str) -> Path:
    """Resolve symbol → NPZ filename. Crypto adds USDC/USDT suffix; stocks bare."""
    s = symbol.upper()
    if mode == "crypto":
        # User mandate: USDC-OVER-USDT. Try USDC first for the 10 majors.
        usdc_majors = {"ETH", "BTC", "SOL", "ADA", "BNB", "AVAX", "XRP", "LINK", "LTC", "UNI"}
        if s in usdc_majors:
            p = NPZ_DIR / f"{s}USDC.npz"
            if p.exists():
                return p
        # Try USDT
        p = NPZ_DIR / f"{s}USDT.npz"
        if p.exists():
            return p
        # Try as-given
        p = NPZ_DIR / f"{s}.npz"
        if p.exists():
            return p
        raise FileNotFoundError(f"No NPZ for crypto symbol {symbol} in {NPZ_DIR}")
    else:
        p = NPZ_DIR / f"{s}.npz"
        if p.exists():
            return p
        raise FileNotFoundError(f"No NPZ for stock symbol {symbol} in {NPZ_DIR}")


def load_npz(symbol: str, mode: str, start_ts: Optional[int] = None) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    """Load NPZ into dict. Slices to bars >= start_ts if given. Returns (npz_dict, timestamps).

    NPZ files in this repo contain some object arrays (e.g. wt_momentum_state_*).
    allow_pickle=True is required; these files are repo-internal, never untrusted.
    """
    p = _resolve_npz_path(symbol, mode)
    with np.load(p, allow_pickle=True) as z:
        ts = np.asarray(z["timestamps"])
        if start_ts is not None:
            i0 = int(np.searchsorted(ts, start_ts, side="left"))
        else:
            i0 = 0
        npz: Dict[str, np.ndarray] = {}
        for k in z.files:
            arr = z[k]
            try:
                arr_full = np.asarray(arr)
            except Exception:
                continue
            # Skip 0-d / scalar entries
            if arr_full.ndim == 0:
                continue
            npz[k] = arr_full[i0:]
        ts = ts[i0:]
    return npz, ts


# ════════════════════════════════════════════════════════════════════════════════
# Per-symbol simulator
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeEvent:
    ts: float            # unix seconds
    type: str            # OPEN/AUGMENT/REDUCE/CLOSE/HEDGE_OPEN/HEDGE_CLOSE
    qty: float
    price: float
    value: float         # qty * price
    reason: str
    pnl_pct: float = 0.0  # only on REDUCE/CLOSE


@dataclass
class SymState:
    is_long: bool
    qty: float = 0.0
    entry_price: float = 0.0
    initial_qty: float = 0.0
    opened_at: float = 0.0
    last_augment_ts: float = 0.0
    last_reduce_ts: float = 0.0
    last_fire_ts: float = 0.0
    augmented_count: int = 0
    max_gain: float = 0.0
    hedge_active: bool = False
    hedge_completed_ts: float = 0.0
    hedge_qty: float = 0.0            # qty of hedge position
    hedge_entry_price: float = 0.0    # price hedge opened at
    hedge_opened_at: float = 0.0      # timestamp hedge opened
    hedge_gain_at_open: float = 0.0   # main position gain when hedge opened
    ppl_fired: bool = False
    intent_lock_stamp: float = 0.0
    last_open_attempt_ts: float = 0.0
    r1_stop_price: float = 0.0
    gr_last_fire_ts: float = 0.0    # GOLDEN_RULE cooldown tracking


def _gain_pct(entry: float, mark: float, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (mark - entry) / entry * 100.0
    return (entry - mark) / entry * 100.0


def _get_ltf_for_mode(mode: str) -> str:
    """Per CLAUDE.md: crypto base TF = 3m, stocks base TF = 5m.
    Tradier NPZs expose _5m fields (not _3m). Hardcoding ltf='3m' for tradier
    causes position_evaluator npz.get(f'wt1_{ltf}') to miss → zero arrays →
    silent 0-trade tradier vec sweeps. Always route ltf through this helper.
    """
    return "5m" if str(mode).lower() == "tradier" else "3m"


def simulate_one_symbol(
    symbol: str,
    side: str,            # 'LONG' or 'SHORT'
    mode: str,
    config: SweepConfig,
    *,
    start_ts: Optional[int] = None,
    max_bars: Optional[int] = None,
    _npz_cache: Optional[Tuple] = None,
    _gr_htf_entry_mask: Optional[np.ndarray] = None,
) -> Tuple[List[TradeEvent], List[float], int]:
    """Run the vec engine on one symbol+side. Returns (events, trade_returns, n_bars).

    _npz_cache: pre-loaded (npz_dict, ts_array) tuple — skips disk read. Pass when
                running multiple variants on the same symbol (gr_dcbb_threshold sweep).
    _gr_htf_entry_mask: optional shape (N,) bool array. When provided and
                GOLDEN_RULE_HTF_MIN_TFS > 0, GR entries (open + augment) are additionally
                gated by this mask. Pre-computed by run_gr_dcbb_sweep() via
                evaluate_gr_htf_vec with the threshold combo for this variant.
    """
    if _SESSION_ABORT_PATH.exists():
        sys.stderr.write(f"SKIP {symbol}/{side}: abort signaled by sibling process\n")
        return [], [], 0
    is_long = side.upper() == "LONG"
    if _npz_cache is not None:
        npz, ts = _npz_cache
    else:
        npz, ts = load_npz(symbol, mode, start_ts=start_ts)
    n = len(ts)
    if max_bars is not None and max_bars < n:
        # Slice the dict
        sliced = {k: v[:max_bars] for k, v in npz.items()}
        npz = sliced
        ts = ts[:max_bars]
        n = max_bars
    if n < 50:
        return [], [], n

    close = npz.get("close")
    if close is None:
        close = npz.get("close_3m", np.zeros(n, dtype=np.float32))
    close = np.asarray(close, dtype=np.float32)

    # ─── PRECOMPUTE GATES IN BULK ────────────────────────────────────────────
    # LTF must match mode: crypto=3m, tradier=5m (CLAUDE.md base-TF rule).
    # Tradier NPZs do NOT have _3m fields — hardcoding 'ltf="3m"' here was the
    # root cause of silent 0-trade tradier vec sweeps (see vec_sweep_tradier_audit.md).
    _ltf = _get_ltf_for_mode(mode)
    # 1. Reentry blocks
    reentry = evaluate_reentry_vec(npz, is_long, config, ltf=_ltf)
    # 2. Exit gates (indicator-only)
    exit_gates = evaluate_exit_gates_vec(npz, is_long, config, ltf=_ltf)
    # 3. Pre-compute hedge cascade WT-against arrays (we'll call noloss vec
    #    per-need, but precompute reuses these too).
    wt1_3m = np.nan_to_num(npz.get("wt1_3m", np.zeros(n)).astype(np.float32))
    wt2_3m = np.nan_to_num(npz.get("wt2_3m", np.zeros(n)).astype(np.float32))
    wt1_15m = np.nan_to_num(npz.get("wt1_15m", np.zeros(n)).astype(np.float32))
    wt2_15m = np.nan_to_num(npz.get("wt2_15m", np.zeros(n)).astype(np.float32))
    wt1_1h = np.nan_to_num(npz.get("wt1_1h", np.zeros(n)).astype(np.float32))
    wt2_1h = np.nan_to_num(npz.get("wt2_1h", np.zeros(n)).astype(np.float32))
    dc_h_4h = exit_gates["dc_h_4h"]
    dc_l_4h = exit_gates["dc_l_4h"]
    # DC stop loss arrays (mode-aware TF, overridable via DC_STOP_TF)
    _dc_stop_tf = str(config.DC_STOP_TF).strip() if str(config.DC_STOP_TF).strip() else ("5m" if mode == "tradier" else "3m")
    _dc4_stop_long  = np.nan_to_num(npz.get(f"dc_low4_{_dc_stop_tf}",  np.zeros(n)).astype(np.float32))
    _dc4_stop_short = np.nan_to_num(npz.get(f"dc_high4_{_dc_stop_tf}", np.zeros(n)).astype(np.float32))
    _dc1_stop_long  = np.nan_to_num(npz.get(f"dc_low_{_dc_stop_tf}",   np.zeros(n)).astype(np.float32))
    _dc1_stop_short = np.nan_to_num(npz.get(f"dc_high_{_dc_stop_tf}",  np.zeros(n)).astype(np.float32))
    # WT force-open: 3m PLUS (15m OR 1h) aligned — matches live spec
    wt_15m_aligned = (wt1_15m > wt2_15m) if is_long else (wt1_15m < wt2_15m)
    wt_1h_aligned  = (wt1_1h  > wt2_1h)  if is_long else (wt1_1h  < wt2_1h)
    wt_3m_aligned  = ((wt1_3m > wt2_3m) if is_long else (wt1_3m < wt2_3m)) & (wt_15m_aligned | wt_1h_aligned)
    # wt1_3m against the trade (required by GR exit and hedge trigger)
    _wt3m_against = (wt1_3m < wt2_3m) if is_long else (wt1_3m > wt2_3m)
    # WT against for hedge trigger (15m and 1h)
    wt_15m_against = (wt1_15m < wt2_15m) if is_long else (wt1_15m > wt2_15m)
    wt_1h_against  = (wt1_1h  < wt2_1h)  if is_long else (wt1_1h  > wt2_1h)
    # GR against-score (vote_min=total-count) — used by GR_HEDGE_SCORE_FLOOR only
    _gr_against_count: Optional[np.ndarray] = None
    if int(config.GR_HEDGE_SCORE_FLOOR) > 0 and evaluate_gr_htf_vec is not None:
        _, _gr_against_count = evaluate_gr_htf_vec(
            npz, is_long=(not is_long), mode=mode, vote_min=1, n=n
        )
    # GR exit gate (legacy min_tfs × min_ind mode) — fires when ≥ MIN_TFS TFs each
    # have ≥ MIN_IND indicators agreeing AGAINST the trade AND wt1_3m is also against.
    # This is the CORRECT interpretation of "#TF × #ind threshold": threshold 3×3=9
    # means 3 TFs each showing 3 indicators in opposition — a genuinely meaningful filter.
    _gr_exit_passes: Optional[np.ndarray] = None
    if config.GR_EXIT_ENABLED and evaluate_gr_htf_vec is not None:
        _gr_exit_passes, _gr_exit_tfs = evaluate_gr_htf_vec(
            npz, is_long=(not is_long), mode=mode,
            min_tfs=int(config.GR_EXIT_MIN_TFS),
            min_ind=int(config.GR_EXIT_MIN_IND),
            vote_min=0, n=n,
        )
        # Validity check: degenerate threshold → exit THIS variant only (no shared abort).
        # Shared abort (_SESSION_ABORT_PATH) is reserved for IDENTICAL RESULT detection only.
        _fire_rate = float(np.mean(_gr_exit_passes))
        if _fire_rate > 0.90:
            sys.stderr.write(
                f"GR_EXIT_ABORT {symbol}/{side}: gate fires on {_fire_rate:.1%} of bars "
                f"(GR_EXIT_MIN_TFS={config.GR_EXIT_MIN_TFS} GR_EXIT_MIN_IND={config.GR_EXIT_MIN_IND}) "
                f"— threshold too loose, would produce identical results to any lower setting. "
                f"Raise MIN_TFS or MIN_IND. Stopping this variant only.\n"
            )
            return [], [], n
        elif _fire_rate > 0.70:
            sys.stderr.write(
                f"GR_EXIT_WARN {symbol}/{side}: gate fires on {_fire_rate:.1%} of bars "
                f"(MIN_TFS={config.GR_EXIT_MIN_TFS} MIN_IND={config.GR_EXIT_MIN_IND}) — "
                f"threshold may be too loose for meaningful differentiation\n"
            )
        elif _fire_rate < 0.02:
            sys.stderr.write(
                f"GR_EXIT_WARN {symbol}/{side}: gate fires on only {_fire_rate:.1%} of bars "
                f"(MIN_TFS={config.GR_EXIT_MIN_TFS} MIN_IND={config.GR_EXIT_MIN_IND}) — "
                f"threshold very strict, gate barely fires\n"
            )

    # WT_15M_BOUNCE_OPEN — vectorized precompute (2026-05-14 root-cause fix).
    # 15m bounce was REENTRY-only in live code; this adds it as an OPEN trigger.
    # All conditions evaluated across the full bar array here → one bool lookup per bar.
    _b15_open_mask = np.zeros(n, dtype=bool)
    if config.WT_15M_BOUNCE_OPEN_ENABLED:
        _b15_bars_ago = np.nan_to_num(
            npz.get('wt_cross_bars_ago_15m', np.full(n, 999, dtype=np.float32))
        ).astype(np.float32)
        _b15_rising_15m = npz.get('wt_cross_rising_15m', np.zeros(n, dtype=np.int8)).astype(bool)
        _b15_bb = np.nan_to_num(
            npz.get('bb_pct_b_15m', np.full(n, 0.5, dtype=np.float32))
        ).astype(np.float32)
        _b15_rising_4h = npz.get('wt_cross_rising_4h', np.zeros(n, dtype=np.int8)).astype(bool)
        _b15_rising_1h = npz.get('wt_cross_rising_1h', np.zeros(n, dtype=np.int8)).astype(bool)
        _b15_fresh = (_b15_bars_ago <= config.WT_15M_BOUNCE_MAX_BARS_AGO) & (_b15_bars_ago > 0)
        _b15_bb_ok = (_b15_bb >= config.WT_15M_BOUNCE_BB_MIN) & (_b15_bb <= config.WT_15M_BOUNCE_BB_MAX)
        if is_long:
            _b15_dir_ok = _b15_rising_15m
            _b15_htf_ok = (_b15_rising_4h & _b15_rising_1h) if config.WT_15M_BOUNCE_REQUIRE_BOTH_HTF \
                else (_b15_rising_4h | _b15_rising_1h)
        else:
            _b15_dir_ok = ~_b15_rising_15m
            _b15_htf_ok = (~_b15_rising_4h & ~_b15_rising_1h) if config.WT_15M_BOUNCE_REQUIRE_BOTH_HTF \
                else (~_b15_rising_4h | ~_b15_rising_1h)
        _b15_open_mask = _b15_fresh & _b15_bb_ok & _b15_dir_ok & _b15_htf_ok

    # ─── STRUCTURAL GATES PRECOMPUTE (2026-05-15) ─────────────────────────────
    # A1: SPY > 200SMA regime mask (aligned to this symbol's ts).
    _spy_long_ok = np.ones(n, dtype=bool)
    _spy_short_ok = np.ones(n, dtype=bool)
    if config.SPY_REGIME_GATE_ENABLED and mode == "tradier":
        try:
            _spy_npz, _spy_ts = load_npz("SPY", mode, start_ts=int(ts[0]))
            _spy_close_d = _spy_npz.get("close_D")
            _spy_sma200_d = _spy_npz.get("sma_200_D")
            if _spy_close_d is not None and _spy_sma200_d is not None:
                _spy_above = np.nan_to_num(_spy_close_d, nan=0.0) > np.nan_to_num(_spy_sma200_d, nan=1e9)
                if len(_spy_above) == n:
                    _aligned = _spy_above
                else:
                    _idx = np.searchsorted(_spy_ts, ts, side="right") - 1
                    _idx = np.clip(_idx, 0, len(_spy_above) - 1)
                    _aligned = _spy_above[_idx]
                if config.SPY_REGIME_BLOCK_LONGS_BELOW:
                    _spy_long_ok = _aligned
                if config.SPY_REGIME_BLOCK_SHORTS_ABOVE:
                    _spy_short_ok = ~_aligned
        except Exception as _e:
            sys.stderr.write(f"SPY_REGIME_GATE: could not load SPY NPZ ({_e}); gate disabled this run\n")

    # A4: Daily-decision-TF mask — fire entry/exit only at last base-TF bar of trading day.
    _daily_decision_mask = np.ones(n, dtype=bool)
    if str(config.DECISION_TF_MODE).upper() == "DAILY":
        # Compute UTC day-of-year for each bar; mark transitions (last bar of each day).
        _doy = (ts // 86400).astype(np.int64)
        _is_last_of_day = np.zeros(n, dtype=bool)
        if n > 1:
            _is_last_of_day[:-1] = _doy[:-1] != _doy[1:]
            _is_last_of_day[-1] = True  # treat final bar as last of its day
        _daily_decision_mask = _is_last_of_day

    # B2: Connors RSI-2 overlay — long when close > 200SMA AND connors_rsi_D < threshold.
    # Currently long-only (per Connors literature). Short side passes through unchanged.
    _connors_open_mask = np.zeros(n, dtype=bool)
    _connors_exit_mask = np.zeros(n, dtype=bool)
    if config.CONNORS_RSI2_OVERLAY_ENABLED and is_long and mode == "tradier":
        _crsi_d = np.nan_to_num(npz.get("connors_rsi_D", np.full(n, 50.0, dtype=np.float32)), nan=50.0).astype(np.float32)
        _sma200_d = np.nan_to_num(npz.get("sma_200_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _close_d = np.nan_to_num(npz.get("close_D", close), nan=0.0).astype(np.float32)
        _above_200 = (_close_d > _sma200_d) if config.CONNORS_RSI2_REQUIRE_ABOVE_200SMA else np.ones(n, dtype=bool)
        _oversold = _crsi_d < float(config.CONNORS_RSI2_THRESHOLD)
        _connors_open_mask = _above_200 & _oversold
        # Exit signal: close > 5-bar SMA on daily (use shifted close_D mean as proxy).
        _bars5 = max(1, int(config.CONNORS_RSI2_EXIT_BARS))
        try:
            _close_5d_avg = np.convolve(_close_d, np.ones(_bars5, dtype=np.float32) / _bars5, mode="same")
            _connors_exit_mask = _close_d > _close_5d_avg
        except Exception:
            _connors_exit_mask = np.zeros(n, dtype=bool)

    # A3: ATR-parity sizing array — precompute qty per bar (overrides default at OPEN).
    _atr_parity_qty = np.zeros(n, dtype=np.float32)
    if str(config.SIZING_MODE).upper() == "ATR_PARITY":
        _atr_field = "atr_D" if config.ATR_PARITY_USE_DAILY else (f"atr_{('5m' if mode == 'tradier' else '3m')}")
        _atr_series = np.nan_to_num(npz.get(_atr_field, np.full(n, 0.01, dtype=np.float32)), nan=0.0).astype(np.float32)
        _target_dollar_risk = (float(config.TARGET_RISK_PER_TRADE_PCT) / 100.0) * float(config.ATR_PARITY_EQUITY_BASE_USD)
        # qty = $-risk / $-per-share-risk-where-$-per-share = ATR_$
        # Avoid divide-by-zero: clip ATR floor to 0.5% of price.
        _atr_safe = np.maximum(_atr_series, np.maximum(close * 0.005, 1e-6))
        _atr_parity_qty = _target_dollar_risk / _atr_safe

    # ─── TR_TREND_v1 precompute (2026-05-17 NEW STRATEGY, default-OFF) ──────────
    # When TR_TREND_V1_ENABLED is True we build the per-bar boolean gates ONCE
    # then short-circuit the legacy OPEN / EXIT pipeline below. Hot loop only
    # consults these arrays + per-symbol tr_state dict.
    _tr_trend_arrays: Dict[str, Any] = {"enabled": False}
    _tr_state: Dict[str, Any] = {}
    if (
        bool(getattr(config, "TR_TREND_V1_ENABLED", False))
        and build_tr_trend_v1_arrays is not None
    ):
        _spy_npz_for_tr = None
        _spy_ts_for_tr = None
        if mode == "tradier" and bool(getattr(config, "TR_TREND_V1_SPY_REGIME_ENABLED", True)):
            try:
                _spy_npz_for_tr, _spy_ts_for_tr = load_npz("SPY", mode, start_ts=int(ts[0]))
            except Exception as _e:
                sys.stderr.write(f"TR_TREND_V1: could not load SPY NPZ ({_e}); regime gate disabled\n")
        try:
            _tr_trend_arrays = build_tr_trend_v1_arrays(
                npz, mode, is_long, config,
                spy_npz=_spy_npz_for_tr, spy_ts=_spy_ts_for_tr, base_ts=ts,
            )
        except Exception as _e:
            sys.stderr.write(f"TR_TREND_V1 precompute failed {symbol}/{side}: {_e}\n")
            _tr_trend_arrays = {"enabled": False}

    # ─── ITERATE BARS (hot loop — pure Python state mutation) ────────────────
    state = SymState(is_long=is_long)
    _store = _NPZStoreAdapter(npz, close, ts)
    _pos = _PosStateAdapter(state)
    events: List[TradeEvent] = []
    trade_returns: List[float] = []  # per-trade % gain (for pool_sharpe)

    # Pre-compute commission buffer for gain checks
    comm_buf = float(config.COMMISSION_BUFFER_PCT)
    min_gain = float(config.MIN_GAIN)
    grace_s = float(config.NEWBORN_PROTECT_GRACE_SECONDS)
    # Side-asymmetric sizing — applied as RETURN MULTIPLIER (treats SIZE_MULT as leverage).
    # Without this, pool_sharpe is computed from per-trade % which is qty-independent —
    # changing LONG_SIZE_MULT 1.0→3.0 has zero effect on metrics (bug repro 2026-05-17).
    # By applying mult to pnl_pct at close-time, a 3x position produces 3x the equity return
    # per trade — economically equivalent to "deploy 3x dollars at the same setup."
    _side_return_mult = float(getattr(config, "LONG_SIZE_MULT", 1.0)) if is_long else \
                        float(getattr(config, "SHORT_SIZE_MULT", 1.0))

    _tr_v1_requested = bool(getattr(config, "TR_TREND_V1_ENABLED", False))
    _tr_v1_active = _tr_v1_requested and bool(_tr_trend_arrays.get("enabled", False))
    # If TR_TREND_v1 was requested but the symbol has insufficient D-bar history
    # (build_tr_trend_v1_arrays returns enabled=False when n_d < 60), SKIP the
    # symbol entirely rather than silently fall through to legacy entry triggers.
    # This prevents thinly-traded names with <60 days of NPZ history from
    # producing meaningless 700+ trade counts when the user wanted a pure
    # daily-decision strategy test.
    if _tr_v1_requested and not _tr_v1_active:
        sys.stderr.write(
            f"TR_TREND_V1 SKIP {symbol}/{side}: insufficient D-bar history "
            f"(need >=60 D bars; got {len(_tr_trend_arrays.get('boundary_idx_in_base', []))})\n"
        )
        return [], [], n

    for i in range(n):
        bar_ts = float(ts[i])
        mark = float(close[i])
        if mark <= 0 or not np.isfinite(mark):
            continue

        # ─── TR_TREND_v1 short-circuit (2026-05-17 NEW STRATEGY) ─────────────
        # When the daily-decision breakout-retest strategy is enabled, ALL legacy
        # entry triggers (wt_3m / reentry / GR / DELTA / connors) are disabled.
        # Only Path A (initial breakout, half unit) + Path B (retest add, second
        # half) open positions; only TR_TREND_v1 structural exits close them
        # (STOP_HIT / 50SMA_BREAK / DC_REVERSE / TIME_STOP). No micro-gain exits,
        # no NO_LOSS gates. The 3% MIN_GAIN rule still applies via stop sizing
        # (2 ATR = typically much wider than 3%, so positions get real room).
        if _tr_v1_active and evaluate_tr_trend_v1_entry is not None:
            # EXIT first — let stop/structural exits fire on the same bar as a new
            # signal would (prevents one bar of double-position).
            if state.qty > 0.0001 and evaluate_tr_trend_v1_exit is not None:
                _trx = evaluate_tr_trend_v1_exit(
                    _tr_trend_arrays, i, mark, _tr_state, is_long, config,
                )
                if _trx is not None:
                    pnl_pct = _gain_pct(state.entry_price, mark, is_long)
                    ev = TradeEvent(
                        ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_trx["reason"], pnl_pct=pnl_pct,
                    )
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts
                    _tr_state = {}  # reset per-position state
                    continue
            # OPEN / ADD
            _trxe = evaluate_tr_trend_v1_entry(
                _tr_trend_arrays, i, _tr_state, is_long, config,
                pos_open=(state.qty > 0.0001), mark=mark,
            )
            if _trxe is not None:
                _atr_d_here = float(_tr_trend_arrays["atr_d_base"][i])
                full_unit = compute_tr_trend_v1_full_unit_qty(config, _atr_d_here, mark) if compute_tr_trend_v1_full_unit_qty else 0.0
                qty_to_add = full_unit * float(_trxe.get("size_unit", 0.5))
                if qty_to_add > 0:
                    if _trxe["action"] == "ENTRY":
                        ev = TradeEvent(
                            ts=bar_ts, type="OPEN", qty=qty_to_add, price=mark,
                            value=qty_to_add * mark, reason=_trxe["reason"],
                        )
                        events.append(ev)
                        state.qty = qty_to_add
                        state.entry_price = mark
                        state.initial_qty = qty_to_add
                        state.opened_at = bar_ts
                        state.augmented_count = 0
                        state.max_gain = 0.0
                        state.last_augment_ts = bar_ts
                    elif _trxe["action"] == "ADD":
                        denom = state.qty + qty_to_add
                        new_entry = (state.qty * state.entry_price + qty_to_add * mark) / denom if denom > 0 else mark
                        ev = TradeEvent(
                            ts=bar_ts, type="AUGMENT", qty=qty_to_add, price=mark,
                            value=qty_to_add * mark, reason=_trxe["reason"],
                        )
                        events.append(ev)
                        state.qty = denom
                        state.entry_price = new_entry
                        state.augmented_count += 1
                        state.last_augment_ts = bar_ts
            continue  # TR_TREND_v1 handles this bar fully — skip all legacy logic

        # ─── 2026-05-17 VEC_OVERTRADE_FIX — POST-CLOSE COOLDOWN ──────────────
        # Mirrors slow engine backtest_v8_engine.py:2473-2486 which gates every
        # is_aug_action (including OPEN on empty) by HARD_AUGMENT_LOCK_SECONDS
        # since the last position-increase event. Without this, vec re-opens on
        # every wt_3m-aligned bar (~33% of bars on a typical 4-yr NPZ →
        # 156k trades vs slow engine's 77).
        #
        # WT_3M_FORCE_OPEN bypass: when reason matches AND
        # WT_3M_FORCE_OPEN_BYPASS_GATES is True (default), check_dup_guard_block
        # returns None (no block). Slow engine has the same bypass in
        # cooldown_locks.py:200-207. So we only block here when the bypass is
        # explicitly disabled in the sweep config.
        # When VEC_OVERTRADE_FIX_ENABLED is False, this block is a no-op.
        if (
            getattr(config, "VEC_OVERTRADE_FIX_ENABLED", True)
            and _vec_check_dup_guard is not None
            and state.qty <= 0.0001                   # only for OPEN-on-empty candidates
            and state.last_augment_ts > 0             # need a prior fill as anchor
        ):
            # We don't know the OPEN reason yet (decided in the OPEN block below)
            # so we use the default vec reason "WT_3M_FORCE_OPEN" — most permissive
            # (bypass active when WT_3M_FORCE_OPEN_BYPASS_GATES=True).
            _vof_block = _vec_check_dup_guard(
                pos_state=state, ts_i=bar_ts, current_gain_pct=0.0, cfg=config,
                pos_value_usd=0.0, action="OPEN", reason_hint="WT_3M_FORCE_OPEN",
            )
            if _vof_block is not None:
                # Lock engaged → skip this bar entirely. No OPEN attempt,
                # no exit checks (irrelevant — position is empty).
                continue

        # ─── FLAT: consider OPEN ──────────────────────────────────────────
        if state.qty <= 0.0001:
            # A1 SPY-regime gate: block side per gate config (default OFF for both sides)
            if config.SPY_REGIME_GATE_ENABLED:
                if is_long and not bool(_spy_long_ok[i]):
                    continue
                if (not is_long) and not bool(_spy_short_ok[i]):
                    continue
            # A4 Daily-decision-TF gate: only evaluate OPEN on last bar of trading day
            if str(config.DECISION_TF_MODE).upper() == "DAILY" and not bool(_daily_decision_mask[i]):
                continue
            # OPEN gate: WT_3M direction + reentry-fire OR force-open OR GOLDEN_RULE
            fire_block = bool(reentry["fire"][i])
            wt_open_ok = bool(wt_3m_aligned[i])
            # GOLDEN_RULE entry check (third trigger when flat)
            _gr_result = None
            if check_golden_rule_enforce is not None and config.GOLDEN_RULE_ENABLED:
                _gr_cooldown_ok = (bar_ts - state.gr_last_fire_ts) >= float(config.GOLDEN_RULE_COOLDOWN_S)
                _gr_htf_ok = (_gr_htf_entry_mask is None) or bool(_gr_htf_entry_mask[i])
                if _gr_cooldown_ok and _gr_htf_ok:
                    _gr_result = check_golden_rule_enforce(_store, i, symbol, side, _pos, config, mode)
            # DELTA_ENGINE entry check (fourth trigger when flat)
            _delta_result = None
            if check_delta_entry is not None and config.DELTA_ENGINE_ENABLED and config.DELTA_ENTRY_ENABLED:
                _delta_result = check_delta_entry(_store, i, side, mode, config)
            # WT_15M_BOUNCE_OPEN — fifth trigger (precomputed mask, O(1) per bar)
            _b15_ok = bool(_b15_open_mask[i])
            # B2 Connors RSI-2 overlay — sixth trigger (long-only)
            _connors_ok = bool(_connors_open_mask[i])
            if not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok or _connors_ok):
                continue
            # STDEV_MACRO_ENTRY_VETO — block OPEN at macro extreme on same side.
            # Additive to existing entry triggers (BB-breakout etc.) — never silently
            # overrides them; refuses the entry with an explicit reason. Default OFF.
            if bool(getattr(config, "STDEV_MACRO_ENTRY_VETO_ENABLED", False)):
                try:
                    import stdev_macro as _sm_open
                    _sm_ind = {
                        "macro_z_D": float(npz["macro_z_D"][i]) if "macro_z_D" in npz else 0.0,
                        "macro_z_W": float(npz["macro_z_W"][i]) if "macro_z_W" in npz else 0.0,
                        "macro_z_M": float(npz["macro_z_M"][i]) if "macro_z_M" in npz else 0.0,
                    }
                    _sm_state_open = _sm_open.compute_stdev_macro_state(_sm_ind, config_obj=config)
                    _sm_side_open = "LONG" if is_long else "SHORT"
                    _sm_block, _sm_why = _sm_open.entry_veto(_sm_side_open, _sm_state_open, config)
                    if _sm_block:
                        continue
                except Exception:
                    pass
            # Compute size via qty pipeline (single-bar call into vec for parity)
            base_qty_arr = np.array([config.START_POSITION_SIZE / mark], dtype=np.float32)
            # A3 ATR-parity sizing override: replace base qty with ATR-parity qty
            if str(config.SIZING_MODE).upper() == "ATR_PARITY":
                _ap_qty = float(_atr_parity_qty[i])
                if _ap_qty > 0:
                    _cap = float(config.ATR_PARITY_QTY_CAP_MULT) * float(config.START_POSITION_SIZE) / mark
                    _ap_qty = min(_ap_qty, _cap)
                    base_qty_arr = np.array([_ap_qty], dtype=np.float32)
            # Side-asymmetric sizing — apply LONG/SHORT multiplier to base qty
            _side_mult = float(getattr(config, "LONG_SIZE_MULT", 1.0)) if is_long else float(getattr(config, "SHORT_SIZE_MULT", 1.0))
            if _side_mult != 1.0:
                base_qty_arr = base_qty_arr * _side_mult
            qty_dict = compute_trade_qty_vec(
                {k: v[i:i+1] for k, v in npz.items()},
                base_qty_arr,
                is_long=is_long,
                config=config,
                is_hedge=False,
            )
            new_qty = float(qty_dict["qty"][0])
            if new_qty <= 0:
                continue
            if _connors_ok and not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok):
                _crsi_val = float(_crsi_d[i]) if config.CONNORS_RSI2_OVERLAY_ENABLED else 0.0
                reason = f"CONNORS_RSI2_OVERLAY_crsi={_crsi_val:.1f}"
            elif _delta_result is not None and not fire_block and not wt_open_ok and _gr_result is None and not _b15_ok:
                reason = _delta_result["reason"]
            elif _gr_result is not None and not fire_block and not wt_open_ok and not _b15_ok:
                reason = _gr_result["reason"]
            elif _b15_ok and not fire_block and not wt_open_ok and _gr_result is None and _delta_result is None:
                _b15_bars_val = int(_b15_bars_ago[i]) if config.WT_15M_BOUNCE_OPEN_ENABLED else 0
                _b15_bb_val = float(_b15_bb[i]) if config.WT_15M_BOUNCE_OPEN_ENABLED else 0.0
                reason = f"WT_15M_BOUNCE_OPEN_bars={_b15_bars_val}_bb={_b15_bb_val:.2f}"
            elif fire_block:
                block_id = int(reentry["block_id"][i])
                reason = BLOCK_NAMES.get(block_id, "WT_3M_FORCE_OPEN")
            else:
                reason = "WT_3M_FORCE_OPEN"
            ev = TradeEvent(
                ts=bar_ts, type="OPEN", qty=new_qty, price=mark,
                value=new_qty * mark, reason=reason,
            )
            events.append(ev)
            state.qty = new_qty
            state.entry_price = mark
            state.initial_qty = new_qty
            state.opened_at = bar_ts
            state.augmented_count = 0
            state.max_gain = 0.0
            state.last_open_attempt_ts = bar_ts
            # 2026-05-17 VEC_OVERTRADE_FIX — stamp last_augment_ts on OPEN as
            # well as on AUGMENT (L1271 below). Slow engine sets
            # `_bt_augment_lock[pk]` in execute_trade_action for every
            # non-reduce non-hedge action (backtest_v8_engine.py:2486 post-pass,
            # 2308 V8_DECISION_ONLY path). Without this stamp, the post-CLOSE
            # cooldown above never engages — state.last_augment_ts stays 0.
            if getattr(config, "VEC_OVERTRADE_FIX_ENABLED", True):
                state.last_augment_ts = bar_ts
            _pos.reset_ppl()
            if _gr_result is not None:
                state.gr_last_fire_ts = bar_ts
            # Record DC stop price at entry time
            if config.DC_LOW4_STOP_ENABLED:
                _s = float(_dc4_stop_long[i] if is_long else _dc4_stop_short[i])
                state.r1_stop_price = _s if _s > 0 else 0.0
            elif config.DC_LOW_STOP_ENABLED:
                _s = float(_dc1_stop_long[i] if is_long else _dc1_stop_short[i])
                state.r1_stop_price = _s if _s > 0 else 0.0
            else:
                state.r1_stop_price = 0.0
            continue

        # ─── HOLDING: compute gain + age ────────────────────────────────
        gain = _gain_pct(state.entry_price, mark, is_long)
        if gain > state.max_gain:
            state.max_gain = gain
        _pos.gain_pct = gain
        age_s = bar_ts - state.opened_at

        # ─── PARTIAL PROFIT LOCK (PPL) — fires as REDUCE, then protects remainder ─
        if check_ppl_step1 is not None and config.PARTIAL_PROFIT_LOCK_ENABLED:
            _ppl1 = check_ppl_step1(_store, i, _pos, config)
            if _ppl1 is not None:
                reduce_qty = state.qty * float(_ppl1.get("frac", config.PARTIAL_PROFIT_LOCK_FRAC))
                if reduce_qty > 0:
                    ev = TradeEvent(ts=bar_ts, type="REDUCE", qty=reduce_qty, price=mark,
                        value=reduce_qty * mark, reason=_ppl1["reason"], pnl_pct=gain)
                    events.append(ev)
                    trade_returns.append(gain * float(_ppl1.get("frac", config.PARTIAL_PROFIT_LOCK_FRAC)))
                    state.qty -= reduce_qty
                    _pos.ppl_fired = True
                    _pos.ppl_first_exit_price = mark
                    _pos.ppl_stop_level = float(_ppl1.get("stop_level", state.entry_price))
                    state.last_reduce_ts = bar_ts
            elif check_ppl_step2 is not None and _pos.ppl_fired and not _pos.ppl_stop_upgraded:
                _ppl2 = check_ppl_step2(_store, i, _pos, config)
                if _ppl2 is not None:
                    _pos.ppl_stop_level = float(_ppl2.get("new_stop", _pos.ppl_stop_level))
                    _pos.ppl_stop_upgraded = True
            if check_ppl_step3 is not None and _pos.ppl_fired and state.qty > 0.0001:
                _ppl3 = check_ppl_step3(_store, i, _pos, config)
                if _ppl3 is not None:
                    pnl_pct = gain
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_ppl3["reason"], pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    _pos.reset_ppl(); _pos.gain_pct = 0.0
                    continue

        # ─── PROFIT_TAKE REDUCE (partial close at profit target) ────────
        if check_profit_take_reduce is not None and config.PROFIT_TAKE_REDUCE_ENABLED and state.qty > 0.0001:
            _ptr = check_profit_take_reduce(_store, i, _pos, mode, config)
            if _ptr is not None:
                frac = float(_ptr.get("frac", 0.5))
                reduce_qty = state.qty * frac
                if reduce_qty > 0:
                    ev = TradeEvent(ts=bar_ts, type="REDUCE", qty=reduce_qty, price=mark,
                        value=reduce_qty * mark, reason=_ptr["reason"], pnl_pct=gain)
                    events.append(ev)
                    trade_returns.append(gain * frac)
                    state.qty -= reduce_qty
                    state.last_reduce_ts = bar_ts
                    _pos.ppl_fired = True  # prevent repeat fire within same position

        # ─── STRONG REDUCE K (stoch-based partial close) ─────────────────
        if check_strong_reduce_k is not None and config.STRONG_REDUCE_K_ENABLED and state.qty > 0.0001:
            _srk = check_strong_reduce_k(_store, i, _pos, mode, config)
            if _srk is not None:
                if not _srk.get("require_profit", False) or gain >= 0:
                    frac = float(_srk.get("frac", 0.5))
                    reduce_qty = state.qty * frac
                    if reduce_qty > 0:
                        ev = TradeEvent(ts=bar_ts, type="REDUCE", qty=reduce_qty, price=mark,
                            value=reduce_qty * mark, reason=_srk["reason"], pnl_pct=gain)
                        events.append(ev)
                        trade_returns.append(gain * frac)
                        state.qty -= reduce_qty
                        state.last_reduce_ts = bar_ts

        # ─── HEDGE OPEN ────────────────────────────────────────────────────
        if (config.HEDGE_SCAN_ENABLED and state.qty > 0.0001 and not state.hedge_active
                and (bar_ts - state.hedge_completed_ts) >= float(config.HEDGE_COMPLETED_LOCKOUT_SECONDS)
                and gain < float(config.HEDGE_MIN_LOSS_PCT)
                and bool(_wt3m_against[i])):
            # USER 2026-05-13: hedge requires wt1_3m against + GR total-vote-score >= floor.
            # 15m/1h WT alignment removed — GR score alone is the HTF confirmation gate.
            _gr_score = int(_gr_against_count[i]) if _gr_against_count is not None else 0
            _gr_ok = (_gr_against_count is not None
                      and int(config.GR_HEDGE_SCORE_FLOOR) > 0
                      and _gr_score >= int(config.GR_HEDGE_SCORE_FLOOR))
            if _gr_ok:
                hedge_qty = state.qty * float(config.HEDGE_QTY_PCT)
                reason = (f"HEDGE_PROTECT_{'SHORT' if is_long else 'LONG'}_LOSS_g{gain:.2f}"
                         f"_GR{_gr_score}")
                ev = TradeEvent(ts=bar_ts, type="HEDGE_OPEN", qty=hedge_qty, price=mark,
                    value=hedge_qty * mark, reason=reason)
                events.append(ev)
                state.hedge_active = True
                state.hedge_qty = hedge_qty
                state.hedge_entry_price = mark
                state.hedge_opened_at = bar_ts
                state.hedge_gain_at_open = gain

        # ─── HEDGE CLOSE ───────────────────────────────────────────────────
        if state.hedge_active and state.hedge_qty > 0 and state.hedge_entry_price > 0:
            wt3m_back_in_favor = not bool(_wt3m_against[i])
            if wt3m_back_in_favor or gain >= 0.0:
                if is_long:
                    hedge_pnl = (state.hedge_entry_price - mark) / state.hedge_entry_price * 100.0
                else:
                    hedge_pnl = (mark - state.hedge_entry_price) / state.hedge_entry_price * 100.0
                close_reason = f"HEDGE_CLOSE_WT{'_RECOVERED' if gain >= 0.0 else ''}_pnl{hedge_pnl:.2f}"
                ev = TradeEvent(ts=bar_ts, type="HEDGE_CLOSE", qty=state.hedge_qty, price=mark,
                    value=state.hedge_qty * mark, reason=close_reason, pnl_pct=hedge_pnl)
                events.append(ev)
                trade_returns.append(hedge_pnl)
                state.hedge_active = False
                state.hedge_qty = 0.0
                state.hedge_entry_price = 0.0
                state.hedge_opened_at = 0.0
                state.hedge_completed_ts = bar_ts

        # ─── EXIT GATES (indicator-only first, then state-aware) ────────
        exit_id = EXIT_NONE
        exit_reason = ""

        # ─── R1 EMERGENCY EXIT (monitoring-based — matches live R1_DC_LOW4_3M_EMERGENCY) ─
        if check_r1_emergency_exit is not None and state.qty > 0.0001:
            _r1 = check_r1_emergency_exit(_store, i, _pos, mode, config)
            if _r1 is not None:
                pnl_pct = gain
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_r1["reason"], pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                _pos.gain_pct = 0.0
                continue

        # DC_STOP: fixed stop price recorded at entry — bypasses noloss (R1 spec)
        # NOTE: This is a SWEEP-ONLY approximation (DC_FIXED_STOP). Not in live.
        # Use R1_DC_LOW4_3M_EMERGENCY_ENABLED above for live-parity.
        if (config.DC_LOW4_STOP_ENABLED or config.DC_LOW_STOP_ENABLED) and state.r1_stop_price > 0:
            _dc_breached = (is_long and mark <= state.r1_stop_price) or ((not is_long) and mark >= state.r1_stop_price)
            if _dc_breached:
                pnl_pct = gain
                ev = TradeEvent(
                    ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark,
                    reason=f"DC_STOP_px{mark:.4f}_stop{state.r1_stop_price:.4f}",
                    pnl_pct=pnl_pct,
                )
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                continue

        # GR multiplier exit: against-score >= threshold AND wt1_3m against
        # 2026-05-17 MIN_GAIN_EXIT_GATE: non-emergency, requires gain >= MIN_GAIN.
        if exit_id == EXIT_NONE and config.GR_EXIT_ENABLED and _gr_exit_passes is not None and gain >= min_gain:
            if bool(_wt3m_against[i]) and bool(_gr_exit_passes[i]):
                exit_id = 99
                exit_reason = f"GR_EXIT_{config.GR_EXIT_MIN_TFS}tf_x_{config.GR_EXIT_MIN_IND}ind"

        # WT_4H_VEL_EXIT (needs profit + age; vec gave us full mask)
        # 2026-05-17 MIN_GAIN_EXIT_GATE: was `gain >= comm_buf` (0.10%) which closed
        # at micro-gains, dragging avg_gain_trade to 0.37%. Now requires real gain
        # (>= config.MIN_GAIN = 3.0%) before this non-emergency exit can fire.
        if exit_id == EXIT_NONE and exit_gates["wt_4h_vel_full"][i] and age_s > 360 and gain >= min_gain:
            exit_id = EXIT_WT_4H_VEL
            exit_reason = f"WT_4H_VEL_g={gain:.2f}%"

        # DC_HOPELESS_EXIT (state-aware: entry outside dc_4h channel)
        # 2026-05-17 MIN_GAIN_EXIT_GATE: in profit-zone (0 < gain < MIN_GAIN) skip;
        # at real loss (gain < comm_buf) the common close block at L1326 routes
        # through noloss_gate which handles loss decisions; at gain >= MIN_GAIN
        # allow the harvest.
        if exit_id == EXIT_NONE and float(config.DC_HOPELESS_EXIT_ENABLED):
            dh = float(dc_h_4h[i]); dl = float(dc_l_4h[i])
            if dh > 0 and dl > 0 and age_s > float(config.DC_HOPELESS_EXIT_MIN_AGE_S):
                if (is_long and state.entry_price > dh) or ((not is_long) and state.entry_price < dl):
                    if gain >= min_gain or gain < comm_buf:
                        exit_id = EXIT_DC_HOPELESS
                        exit_reason = f"DC_HOPELESS_entry={state.entry_price:.4f}"

        # 2026-05-17 MIN_GAIN_EXIT_GATE: was `gain > 0` (allowed close at +0.1%);
        # now requires gain >= MIN_GAIN to avoid micro-gain closes.
        if exit_id == EXIT_NONE and config.WT_EXHAUST_EXIT_ENABLED and exit_gates["wt_exhaust"][i] and age_s > grace_s:
            if gain >= min_gain:
                exit_id = EXIT_WT_EXHAUST
                exit_reason = "WT_EXHAUST"

        # 2026-05-17 MIN_GAIN_EXIT_GATE: WT_PERCENTILE / E_1_WT_DELTA / E_3_STRUCTURE
        # are non-emergency exits — gate behind gain >= MIN_GAIN to prevent
        # micro-gain closes (R1/R2/HEDGE_FAILED handle the real loss paths).
        if exit_id == EXIT_NONE and exit_gates["wt_percentile"][i] and age_s > grace_s and gain >= min_gain:
            exit_id = EXIT_WT_PERCENTILE
            exit_reason = "WT_PERCENTILE"

        if exit_id == EXIT_NONE and exit_gates["e1_wt_delta"][i] and age_s > grace_s and gain >= min_gain:
            exit_id = EXIT_E1_WT_DELTA
            exit_reason = "E_1_WT_DELTA"

        if exit_id == EXIT_NONE and exit_gates["e3_structure"][i] and age_s > grace_s and gain >= min_gain:
            exit_id = EXIT_E3_STRUCTURE
            exit_reason = "E_3_STRUCTURE"

        # PEAK_GIVEBACK exit (gain decaying from peak — full close, bypasses noloss)
        if exit_id == EXIT_NONE and check_peak_giveback_exit is not None and state.qty > 0.0001:
            _pgb = check_peak_giveback_exit(_store, i, _pos, mode, config)
            if _pgb is not None:
                pnl_pct = gain
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_pgb, pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts
                _pos.gain_pct = 0.0; _pos.reset_ppl()
                continue

        # BE_EROSION exit (gain eroded to loss after being profitable — bypasses noloss)
        if exit_id == EXIT_NONE and check_be_erosion_exit is not None and state.qty > 0.0001:
            _beg = check_be_erosion_exit(_store, i, _pos, mode, config)
            if _beg is not None:
                pnl_pct = gain
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_beg, pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts
                _pos.gain_pct = 0.0; _pos.reset_ppl()
                continue

        # IN_GAIN_TREND_EXIT (profit harvesting on strong winners — mirrors live ez_manage logic)
        # ha_1h/ha_15m in NPZ are int8: 1=green, -1=red (NOT strings)
        # 2026-05-17 MIN_GAIN_EXIT_GATE: floor at max(IN_GAIN_TREND_MIN_GAIN, MIN_GAIN)
        # so this profit-harvest never closes below 3% real gain.
        if exit_id == EXIT_NONE and config.IN_GAIN_TREND_EXIT_ENABLED and gain >= max(float(config.IN_GAIN_TREND_MIN_GAIN), min_gain):
            _igt_fired = False
            _igt_reason = ""
            if gain >= float(config.IN_GAIN_TREND_BIG_WINNER_PCT):
                # BIG_WINNER_HTF: stoch_k_1h < stoch_d_1h AND ha_1h == -1/red (LONG)
                #                 stoch_k_1h > stoch_d_1h AND ha_1h == 1/green (SHORT)
                _k1h = float(_store.f("stoch_k_1h", i, 50.0))
                _d1h = float(_store.f("stoch_d_1h", i, 50.0))
                _ha1h = int(_store.f("ha_1h", i, 0))
                if is_long and _k1h < _d1h and _ha1h == -1:
                    _igt_fired = True
                    _igt_reason = f"IN_GAIN_TREND_EXIT_BIG_WINNER_HTF_g{gain:.1f}"
                elif (not is_long) and _k1h > _d1h and _ha1h == 1:
                    _igt_fired = True
                    _igt_reason = f"IN_GAIN_TREND_EXIT_BIG_WINNER_HTF_g{gain:.1f}"
            elif gain >= float(config.IN_GAIN_TREND_MED_WINNER_PCT):
                # MED_WINNER_15M: ha_15m == -1/red AND stoch_k_15m < stoch_d_15m (LONG)
                #                 ha_15m == 1/green AND stoch_k_15m > stoch_d_15m (SHORT)
                _k15 = float(_store.f("stoch_k_15m", i, 50.0))
                _d15 = float(_store.f("stoch_d_15m", i, 50.0))
                _ha15 = int(_store.f("ha_15m", i, 0))
                if is_long and _ha15 == -1 and _k15 < _d15:
                    _igt_fired = True
                    _igt_reason = f"IN_GAIN_TREND_EXIT_MED_WINNER_15M_g{gain:.1f}"
                elif (not is_long) and _ha15 == 1 and _k15 > _d15:
                    _igt_fired = True
                    _igt_reason = f"IN_GAIN_TREND_EXIT_MED_WINNER_15M_g{gain:.1f}"
            if _igt_fired:
                exit_id = 94
                exit_reason = _igt_reason

        # WT_CROSSUNDER_FINAL (stateless indicator check)
        # 2026-05-17 MIN_GAIN_EXIT_GATE: non-emergency — only fire when
        # gain >= MIN_GAIN (profit harvest) or at real loss (noloss_gate routes).
        if exit_id == EXIT_NONE and check_wt_crossunder_final_exit is not None and state.qty > 0.0001:
            if gain >= min_gain or gain < comm_buf:
                _wtcf = check_wt_crossunder_final_exit(_store, i, _pos, mode, config)
                if _wtcf is not None:
                    exit_id = 91
                    exit_reason = _wtcf["reason"]

        # R2 WT VELOCITY SLOW (near-breakeven slowdown — matches live R2_WT_VEL_SLOW)
        if exit_id == EXIT_NONE and check_r2_wt_vel_slow_exit is not None and state.qty > 0.0001:
            _r2 = check_r2_wt_vel_slow_exit(_store, i, _pos, mode, config)
            if _r2 is not None:
                # R2 bypasses noloss gate — execute close directly
                pnl_pct = gain
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_r2["reason"], pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts
                _pos.gain_pct = 0.0
                continue

        # R4 STDEV_MACRO — long-window log-price z-score on D AND W extreme + LTF
        # flip. Additive to R1/R2/R3 — runs AFTER R2. Default OFF behind
        # STDEV_MACRO_R4_EXIT_ENABLED. Reason joins UNIVERSAL_NOLOSS_GATE_BYPASS.
        if exit_id == EXIT_NONE and bool(getattr(config, "STDEV_MACRO_R4_EXIT_ENABLED", False)) and state.qty > 0.0001:
            try:
                import stdev_macro as _sm_r4
                _r4_ind_vec = {
                    "macro_z_D": float(npz["macro_z_D"][i]) if "macro_z_D" in npz else 0.0,
                    "macro_z_W": float(npz["macro_z_W"][i]) if "macro_z_W" in npz else 0.0,
                    "macro_z_M": float(npz["macro_z_M"][i]) if "macro_z_M" in npz else 0.0,
                    "wt1_4h": float(npz["wt1_4h"][i]) if "wt1_4h" in npz else 0.0,
                    "wt2_4h": float(npz["wt2_4h"][i]) if "wt2_4h" in npz else 0.0,
                }
                _r4_state_vec = _sm_r4.compute_stdev_macro_state(_r4_ind_vec, config_obj=config)
                _r4_side_vec = "LONG" if is_long else "SHORT"
                _r4_close_vec, _r4_reason_vec = _sm_r4.r4_exit(_r4_side_vec, _r4_state_vec, _r4_ind_vec, config)
                if _r4_close_vec:
                    pnl_pct = gain
                    _r4_full_reason = f"{_r4_reason_vec}_zD={_r4_state_vec.get('macro_z_D', 0.0):.2f}_zW={_r4_state_vec.get('macro_z_W', 0.0):.2f}"
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_r4_full_reason, pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts
                    _pos.gain_pct = 0.0
                    continue
            except Exception:
                pass

        # ─── 2026-05-17 VEC_NOLOSS_GATE — per-bar HEDGE-OR-CLOSE for open losers ───
        # CLAUDE.md sacred rule: "WE HEDGE OR WE CLOSE — WE NEVER HOLD."
        # First-honest-tradier sweep had mtm_n=63 because positions sitting in
        # -22% with NO exit-gate trigger waited for MTM_FINAL_BAR_NOLIES_RULE2.
        # This block consults evaluate_noloss_gate_vec on EVERY bar where the
        # position is open AND in real loss (gain < comm_buf) AND no exit_id
        # is set (so it doesn't double-fire with the `if exit_id != EXIT_NONE:`
        # block below). Honors OBLIGATORY_HEDGE (opens vec hedge) and
        # HEDGE_FAILED_FALLBACK_CLOSE (closes position) per live semantics.
        if (
            getattr(config, "VEC_NOLOSS_GATE_ENABLED", True)
            and exit_id == EXIT_NONE
            and state.qty > 0.0001
            and gain < comm_buf
        ):
            _vng = evaluate_noloss_gate_vec(
                {k: v[i:i+1] for k, v in npz.items()},
                is_long=is_long,
                real_gain_pct=np.array([gain], dtype=np.float32),
                positionAmt=np.array([state.qty if is_long else -state.qty], dtype=np.float32),
                mark_price=np.array([mark], dtype=np.float32),
                reason="VEC_BAR_EVAL",
                config=config,
                is_hedge=False,
                is_reduce=True,
                hedge_already_active=np.array([state.hedge_active], dtype=bool),
                hedge_attempt_succeeded=np.array([False], dtype=bool),  # vec has no broker; assume hedge fails → fallback close
            )
            _vng_action = int(_vng["action_id"][0])
            _vng_hedge_fire = bool(_vng["hedge_fire"][0])
            _vng_hedge_qty = float(_vng["hedge_qty"][0])
            # OBLIGATORY_HEDGE: open vec same-symbol hedge before considering close
            if _vng_hedge_fire and not state.hedge_active and _vng_hedge_qty > 0.0:
                _vh_reason = f"OBLIGATORY_HEDGE_VEC_g{gain:.2f}"
                ev = TradeEvent(ts=bar_ts, type="HEDGE_OPEN", qty=_vng_hedge_qty, price=mark,
                    value=_vng_hedge_qty * mark, reason=_vh_reason)
                events.append(ev)
                state.hedge_active = True
                state.hedge_qty = _vng_hedge_qty
                state.hedge_entry_price = mark
                state.hedge_opened_at = bar_ts
                state.hedge_gain_at_open = gain
                # Hedge now active → gate would have returned HOLD anyway; defer close.
                continue
            # HEDGE_FAILED_FALLBACK_CLOSE: close at loss (sacred rule — never hold open loser)
            if _vng_action == 2:
                pnl_pct = gain
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=f"HEDGE_FAILED_FALLBACK_CLOSE_g{gain:.2f}",
                    pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts
                _pos.gain_pct = 0.0
                continue
            # action_id 0 (HOLD) or 1 (ALLOW_REDUCE) → fall through to normal exit/augment flow

        if exit_id != EXIT_NONE:
            # CLOSE — gate through noloss if at a loss. We call the vec noloss
            # on a SINGLE-BAR slice to leverage parity-tested logic.
            reason_str = EXIT_NAMES.get(exit_id, exit_reason)
            if gain < comm_buf:
                # Loss exit → noloss gate decides HOLD vs ALLOW_REDUCE vs HEDGE_FAIL
                ngd = evaluate_noloss_gate_vec(
                    {k: v[i:i+1] for k, v in npz.items()},
                    is_long=is_long,
                    real_gain_pct=np.array([gain], dtype=np.float32),
                    positionAmt=np.array([state.qty if is_long else -state.qty], dtype=np.float32),
                    mark_price=np.array([mark], dtype=np.float32),
                    reason=reason_str,
                    config=config,
                    is_hedge=False,
                    is_reduce=True,
                    hedge_already_active=np.array([state.hedge_active], dtype=bool),
                    hedge_attempt_succeeded=np.array([True], dtype=bool),
                )
                action_id = int(ngd["action_id"][0])
                if action_id == 0:  # HOLD
                    continue
                # 1 = ALLOW_REDUCE, 2 = HEDGE_FAIL fallback close — both close
            # Execute close
            pnl_pct = gain
            ev = TradeEvent(
                ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                value=state.qty * mark, reason=reason_str, pnl_pct=pnl_pct,
            )
            events.append(ev)
            trade_returns.append(pnl_pct)
            state.qty = 0.0
            state.entry_price = 0.0
            state.initial_qty = 0.0
            state.opened_at = 0.0
            state.augmented_count = 0
            state.max_gain = 0.0
            state.last_reduce_ts = bar_ts
            state.hedge_active = False
            state.hedge_qty = 0.0
            state.hedge_entry_price = 0.0
            state.hedge_completed_ts = bar_ts
            continue

        # ─── AUGMENT path (reentry fires while holding) ────────────────
        if reentry["fire"][i]:
            block_id = int(reentry["block_id"][i])
            qty_mult = float(reentry["qty_mult"][i])
            proposed = (config.START_POSITION_SIZE * qty_mult) / mark
            # Augment eligibility
            elig = evaluate_augment_eligibility_vec(
                action="AUGMENT",
                position_amt_arr=np.array([state.qty if is_long else -state.qty], dtype=np.float32),
                real_gain_arr=np.array([gain], dtype=np.float32),
                entry_price_arr=np.array([state.entry_price], dtype=np.float32),
                mark_price_arr=np.array([mark], dtype=np.float32),
                proposed_qty_arr=np.array([proposed], dtype=np.float32),
                config=config,
                last_augmentation_time_arr=np.array([state.last_augment_ts], dtype=np.float64),
                initial_quantity_arr=np.array([state.initial_qty], dtype=np.float32),
                max_gain_arr=np.array([state.max_gain], dtype=np.float32),
                now_ts_arr=np.array([bar_ts], dtype=np.float64),
                is_long=is_long,
                reason_arr=np.array([BLOCK_NAMES.get(block_id, "AUGMENT")], dtype=object),
            )
            if not bool(elig["allowed"][0]):
                continue
            aug_qty = float(elig["augment_qty"][0])
            if aug_qty <= 0:
                continue
            # New blended entry
            denom = state.qty + aug_qty
            new_entry = (state.qty * state.entry_price + aug_qty * mark) / denom if denom > 0 else mark
            ev = TradeEvent(
                ts=bar_ts, type="AUGMENT", qty=aug_qty, price=mark,
                value=aug_qty * mark, reason=BLOCK_NAMES.get(block_id, "AUGMENT"),
            )
            events.append(ev)
            state.qty = denom
            state.entry_price = new_entry
            state.augmented_count += 1
            state.last_augment_ts = bar_ts

        # ─── 2026-05-17 VEC_OVERTRADE_FIX — GR-AUGMENT HARD_AUGMENT_LOCK ─────
        # Mirrors slow engine backtest_v8_engine.py:2473-2486. The reentry
        # AUGMENT path immediately above already goes through
        # evaluate_augment_eligibility_vec() (L1240) which has its own
        # HARD_AUGMENT_LOCK check, but the GR-augment path BELOW does NOT —
        # it would otherwise fire on every GR-cooldown-elapsed bar where GR
        # votes pass, independent of the 900s aug lock.
        if (
            getattr(config, "VEC_OVERTRADE_FIX_ENABLED", True)
            and _vec_check_dup_guard is not None
            and check_golden_rule_enforce is not None
            and config.GOLDEN_RULE_ENABLED
            and state.qty > 0.0001
        ):
            _vof_gr_block = _vec_check_dup_guard(
                pos_state=state, ts_i=bar_ts, current_gain_pct=gain, cfg=config,
                pos_value_usd=state.qty * mark, action="AUGMENT",
                reason_hint="GOLDEN_RULE_AUGMENT",
            )
            if _vof_gr_block is not None:
                continue

        # GOLDEN_RULE augment while holding
        if check_golden_rule_enforce is not None and config.GOLDEN_RULE_ENABLED and state.qty > 0.0001:
            _gr_cooldown_ok = (bar_ts - state.gr_last_fire_ts) >= float(config.GOLDEN_RULE_COOLDOWN_S)
            _gr_htf_ok = (_gr_htf_entry_mask is None) or bool(_gr_htf_entry_mask[i])
            if _gr_cooldown_ok and _gr_htf_ok:
                _gr_aug = check_golden_rule_enforce(_store, i, symbol, side, _pos, config, mode)
                if _gr_aug is not None and _gr_aug.get("action") == "AUGMENT":
                    aug_mult = float(_gr_aug.get("mult", 1.0))
                    aug_qty = (config.START_POSITION_SIZE * aug_mult) / mark
                    if aug_qty > 0:
                        denom = state.qty + aug_qty
                        new_entry = (state.qty * state.entry_price + aug_qty * mark) / denom if denom > 0 else mark
                        ev = TradeEvent(ts=bar_ts, type="AUGMENT", qty=aug_qty, price=mark,
                            value=aug_qty * mark, reason=_gr_aug["reason"])
                        events.append(ev)
                        state.qty = denom
                        state.entry_price = new_entry
                        state.augmented_count += 1
                        state.last_augment_ts = bar_ts
                        state.gr_last_fire_ts = bar_ts

    # ─── MtM-FINAL-BAR (NO-LIES RULE 2: open losers MUST be appended) ──────
    if state.qty > 0.0001:
        final_mark = float(close[-1])
        pnl_pct = _gain_pct(state.entry_price, final_mark, is_long)
        ev = TradeEvent(
            ts=float(ts[-1]), type="CLOSE", qty=state.qty, price=final_mark,
            value=state.qty * final_mark, reason="MTM_FINAL_BAR_NOLIES_RULE2",
            pnl_pct=pnl_pct,
        )
        events.append(ev)
        trade_returns.append(pnl_pct)

    # ─── MTM HEDGE — open hedge at simulation end MUST be MtM'd (NO-LIES) ──
    if state.hedge_active and state.hedge_qty > 0 and state.hedge_entry_price > 0:
        final_mark = float(close[-1])
        if is_long:
            hedge_pnl = (state.hedge_entry_price - final_mark) / state.hedge_entry_price * 100.0
        else:
            hedge_pnl = (final_mark - state.hedge_entry_price) / state.hedge_entry_price * 100.0
        ev = TradeEvent(ts=float(ts[-1]), type="HEDGE_CLOSE", qty=state.hedge_qty, price=final_mark,
            value=state.hedge_qty * final_mark, reason="MTM_FINAL_HEDGE_NOLIES", pnl_pct=hedge_pnl)
        events.append(ev)
        trade_returns.append(hedge_pnl)

    # Side-asymmetric sizing — scale ALL per-trade returns by side_return_mult so
    # LONG_SIZE_MULT/SHORT_SIZE_MULT actually affect pool_sharpe/gain (otherwise the
    # multipliers are no-ops on % metrics). Applied BEFORE round-trip cost so cost
    # is still 1× per trade (spread doesn't scale with position size).
    if _side_return_mult != 1.0:
        trade_returns = [r * _side_return_mult for r in trade_returns]

    # Deduct round-trip spread/slippage from every trade return (NO-LIES: gross ≠ net)
    _rt_cost = float(getattr(config, "ROUND_TRIP_COST_PCT", 0.10))
    if _rt_cost != 0.0:
        trade_returns = [r - _rt_cost for r in trade_returns]

    return events, trade_returns, n


# ════════════════════════════════════════════════════════════════════════════════
# Trade event writer → /history/<acct>/<SYM>_<SIDE>.jsonl compatible
# ════════════════════════════════════════════════════════════════════════════════

def write_history_jsonl(
    account: str,
    symbol: str,
    side: str,
    events: List[TradeEvent],
    out_root: Path,
):
    """Append events to <out_root>/<account>/<SYMBOL>_<SIDE>.jsonl in the
    schema used by /history/<acct>/*.jsonl (used by /:5057 dashboard etc.)."""
    acct_dir = out_root / account
    acct_dir.mkdir(parents=True, exist_ok=True)
    path = acct_dir / f"{symbol}_{side}.jsonl"
    with path.open("w") as fh:
        for ev in events:
            iso = datetime.fromtimestamp(ev.ts, tz=timezone.utc).isoformat()
            row = {
                "ts": iso,
                "type": ev.type,
                "qty": round(ev.qty, 8),
                "price": round(ev.price, 8),
                "value": round(ev.value, 4),
                "reason": ev.reason,
                "indicators": {},
            }
            if ev.pnl_pct:
                row["pnl_pct"] = round(ev.pnl_pct, 6)
            fh.write(json.dumps(row) + "\n")
    return path


# ════════════════════════════════════════════════════════════════════════════════
# Drawdown helper
# ════════════════════════════════════════════════════════════════════════════════

def _max_dd_pct(trade_returns: List[float]) -> float:
    """Compute max equity drawdown from per-trade returns. Returns positive %, ∈ [0, 100].

    Uses COMPOUND equity (cumprod of 1+r/100) — matches how a real account drawdown
    is measured. Capped at 100% (full account wipe).

    Previous bug (2026-05-17): used additive cumsum then (peak-cum) which produces
    "summed percentage points" not equity drawdown. With 3,008 -0.4%-avg trades you'd
    see DD=862% which is nonsensical (real DD ∈ [0,100%]).
    """
    if not trade_returns:
        return 0.0
    # Equity curve as multiplicative chain. Treat each trade as a discrete bet on capital.
    r = np.asarray(trade_returns, dtype=np.float64) / 100.0
    # Clip extreme single-trade returns to avoid one outlier killing equity
    r = np.clip(r, -0.99, 10.0)  # max -99% / +1000% per trade for stability
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    # DD as fraction below peak
    dd = (peak - equity) / np.maximum(peak, 1e-9)
    return float(min(100.0, dd.max() * 100.0)) if len(dd) else 0.0


# ════════════════════════════════════════════════════════════════════════════════
# Multi-symbol sweep entry
# ════════════════════════════════════════════════════════════════════════════════

def run_sweep(
    *,
    mode: str,
    account: str,
    symbols: List[str],
    sides: Optional[List[str]] = None,
    start: str = "2024-01-01",
    config: Optional[SweepConfig] = None,
    workers: int = 1,
    write_history: bool = True,
    out_jsonl: Optional[Path] = None,
    max_bars: Optional[int] = None,
) -> Dict[str, Any]:
    """Run a sweep across symbols. Returns the canonical 9-field metric dict.

    Always writes:
        - data/sweep_results/v8_vec_sweep_<TS>.jsonl (per-sym summary rows)
        - data/sweep_results/v8_vec_sweep_<TS>_trades.jsonl (every fill, source-of-truth)
        - /history/<account>/*.jsonl if write_history=True
    """
    config = config or SweepConfig()
    sides = sides or ["LONG", "SHORT"]
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if "T" in start else \
               datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_dt.timestamp())

    ts_run = int(time.time())
    SWEEP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = out_jsonl or (SWEEP_RESULTS_DIR / f"v8_vec_sweep_{ts_run}.jsonl")
    trades_path = SWEEP_RESULTS_DIR / f"v8_vec_sweep_{ts_run}_trades.jsonl"

    returns_by_sym: Dict[str, List[float]] = {}
    total_bars = 0
    elapsed_per_sym = []

    with summary_path.open("w") as smry, trades_path.open("w") as trd:
        for sym in symbols:
            for side in sides:
                t0 = time.perf_counter()
                try:
                    events, returns, n_bars = simulate_one_symbol(
                        sym, side, mode, config,
                        start_ts=start_ts, max_bars=max_bars,
                    )
                except FileNotFoundError as e:
                    sys.stderr.write(f"SKIP {sym}_{side}: {e}\n")
                    continue
                except Exception as e:
                    sys.stderr.write(f"FAIL {sym}_{side}: {type(e).__name__}: {e}\n")
                    continue
                t1 = time.perf_counter()
                elapsed = t1 - t0
                elapsed_per_sym.append((sym, side, n_bars, elapsed))
                total_bars += n_bars
                key = f"{sym}_{side}"
                if returns:
                    returns_by_sym[key] = returns

                # Write each event to the trades JSONL (source of truth)
                for ev in events:
                    iso = datetime.fromtimestamp(ev.ts, tz=timezone.utc).isoformat()
                    trd.write(json.dumps({
                        "ts": iso, "symbol": sym, "side": side, "type": ev.type,
                        "qty": ev.qty, "price": ev.price, "value": ev.value,
                        "reason": ev.reason, "pnl_pct": ev.pnl_pct,
                    }) + "\n")

                # Per-sym summary row
                n_trades = len(returns)
                if n_trades > 0:
                    sym_ps = metrics_guard.pool_sharpe(returns)
                    sym_dd = _max_dd_pct(returns)
                    sym_acc = sum(returns)
                else:
                    sym_ps = 0.0
                    sym_dd = 0.0
                    sym_acc = 0.0
                smry.write(json.dumps({
                    "symbol": sym, "side": side, "n_bars": n_bars,
                    "n_trades": n_trades, "pool_sharpe": sym_ps,
                    "max_dd_pct": sym_dd, "acc_gain_pct": sym_acc,
                    "elapsed_s": round(elapsed, 3),
                }) + "\n")

                # Write history JSONL (for live dashboards)
                if write_history and events:
                    write_history_jsonl(account, sym, side, events,
                                        out_root=HISTORY_DIR / f"_v8_vec_sweep_{ts_run}")

    # ─── Aggregate across symbols using metrics_guard (NO LIES) ──────────────
    if returns_by_sym:
        # Determine years from total_bars on the chosen base TF (3m → 3min/bar)
        # We approximate via timestamps from last sym; OK for diagnostic display.
        max_ts = 0; min_ts = 1 << 62
        for sym in symbols:
            try:
                _, ts_arr = load_npz(sym, mode, start_ts=start_ts)
                if len(ts_arr):
                    if int(ts_arr[0]) < min_ts: min_ts = int(ts_arr[0])
                    if int(ts_arr[-1]) > max_ts: max_ts = int(ts_arr[-1])
            except FileNotFoundError:
                continue
        if max_bars is not None and min_ts < max_ts:
            # Estimate from bar count instead (3m bars)
            years = (max_bars * 180) / (365.25 * 86400)
        elif max_ts > min_ts:
            years = (max_ts - min_ts) / (365.25 * 86400)
        else:
            years = 0.01
        std = metrics_guard.standard_metric_set(returns_by_sym, years=years)
        # Add max_dd computed across pooled-cum trades (conservative: per-sym max)
        worst_dd = 0.0
        for rets in returns_by_sym.values():
            d = _max_dd_pct(rets)
            if d > worst_dd:
                worst_dd = d
        std["max_dd_pct"] = worst_dd
    else:
        std = {
            "pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0,
            "gain_per_yr": 0.0, "gain_sym_yr": 0.0,
            "trades": 0, "n_syms": 0, "years": 0.0, "max_dd_pct": 0.0,
        }

    # Final-line: canonical 9-field via metrics_guard
    try:
        canonical_line = metrics_guard.format_standard_set(std, mode=mode)
    except metrics_guard.FakeMetricRefused as e:
        canonical_line = f"[METRICS_REFUSED] {e}"

    # ─── Identical-result guard — detect broken thresholds ASAP ─────────────────
    if config.GR_EXIT_ENABLED and std["trades"] > 0:
        _fp = {"trades": int(std["trades"]), "sharpe_3dp": round(std["pool_sharpe"], 3)}
        _variant_key = f"tfs{config.GR_EXIT_MIN_TFS}_ind{config.GR_EXIT_MIN_IND}"
        if _SESSION_FP_PATH.exists():
            try:
                _fps = json.loads(_SESSION_FP_PATH.read_text())
                for _prev_key, _prev_fp in _fps.items():
                    if _prev_fp == _fp and _prev_key != _variant_key:
                        _dup_msg = (
                            f"[IDENTICAL_RESULT_DETECTED] {_variant_key} matches {_prev_key}: "
                            f"trades={_fp['trades']} sharpe={_fp['sharpe_3dp']} — "
                            f"GR thresholds are in a degenerate range (both fire at same rate). "
                            f"Stop and widen the threshold gap."
                        )
                        print(_dup_msg, flush=True)
                        _SESSION_ABORT_PATH.write_text(_dup_msg)
                        break
            except Exception:
                pass
        try:
            _fps = json.loads(_SESSION_FP_PATH.read_text()) if _SESSION_FP_PATH.exists() else {}
            _fps[_variant_key] = _fp
            _SESSION_FP_PATH.write_text(json.dumps(_fps))
        except Exception:
            pass

    summary = {
        "ts_run": ts_run,
        "mode": mode,
        "account": account,
        "symbols": symbols,
        "sides": sides,
        "start": start,
        "n_syms": std["n_syms"],
        "n_trades": std["trades"],
        "years": std["years"],
        "pool_sharpe": std["pool_sharpe"],
        "sym_sharpe": std["sym_sharpe"],
        "avg_gain_trade": std["avg_gain_trade"],
        "gain_per_yr": std["gain_per_yr"],
        "gain_sym_yr": std["gain_sym_yr"],
        "max_dd_pct": std["max_dd_pct"],
        "total_bars": total_bars,
        "canonical_line": canonical_line,
        "summary_path": str(summary_path),
        "trades_path": str(trades_path),
        "elapsed_per_sym": elapsed_per_sym,
    }
    return summary


# ════════════════════════════════════════════════════════════════════════════════
# Fast multi-variant DC/BB threshold sweep
# ════════════════════════════════════════════════════════════════════════════════

_DCBB_GRID = [
    ("baseline_dcbb_defaults", 0.65, 0.75),
    ("DC0.35_BB0.45", 0.35, 0.45), ("DC0.35_BB0.60", 0.35, 0.60),
    ("DC0.35_BB0.75", 0.35, 0.75), ("DC0.35_BB0.90", 0.35, 0.90),
    ("DC0.50_BB0.45", 0.50, 0.45), ("DC0.50_BB0.60", 0.50, 0.60),
    ("DC0.50_BB0.75", 0.50, 0.75), ("DC0.50_BB0.90", 0.50, 0.90),
    ("DC0.65_BB0.45", 0.65, 0.45), ("DC0.65_BB0.60", 0.65, 0.60),
    ("DC0.65_BB0.90", 0.65, 0.90),
    ("DC0.80_BB0.45", 0.80, 0.45), ("DC0.80_BB0.60", 0.80, 0.60),
    ("DC0.80_BB0.75", 0.80, 0.75), ("DC0.80_BB0.90", 0.80, 0.90),
]  # 16 variants (baseline_dcbb_defaults = DC0.65_BB0.75, deduplicated)


def _dcbb_worker(args_tuple):
    """Worker function for parallel gr_dcbb sweep — processes one (sym, side) pair.
    Returns {variant_label: [trade_returns]} dict, or {} on skip/error.
    Runs in a subprocess so imports must be self-contained (evaluate_gr_htf_vec + simulate_one_symbol
    are module-level imports that survive pickling of this function reference).
    """
    sym, side, mode, base_config, start_ts, min_tfs, min_ind = args_tuple
    result: Dict[str, List[float]] = {}
    try:
        npz, ts = load_npz(sym, mode, start_ts=start_ts)
    except Exception as e:
        sys.stderr.write(f"[dcbb_worker] SKIP {sym}/{side}: {type(e).__name__}: {e}\n")
        return result
    n = len(ts)
    if n < 50:
        return result
    is_long = side.upper() == "LONG"
    if evaluate_gr_htf_vec is None:
        sys.stderr.write(f"[dcbb_worker] evaluate_gr_htf_vec not available\n")
        return result
    # Precompute all 16 HTF confirmation masks
    htf_masks: List[np.ndarray] = []
    for _label, dc_thr, bb_thr in _DCBB_GRID:
        mask, _ = evaluate_gr_htf_vec(
            npz, is_long, mode,
            min_tfs=min_tfs, min_ind=min_ind,
            invert_dc_bb=True,
            dc_threshold=dc_thr, bb_threshold=bb_thr,
            n=n,
        )
        htf_masks.append(mask)
    # Run 16 simulations using cached NPZ
    for v_idx, (label, _dc, _bb) in enumerate(_DCBB_GRID):
        try:
            _events, returns, _n = simulate_one_symbol(
                sym, side, mode, base_config,
                _npz_cache=(npz, ts),
                _gr_htf_entry_mask=htf_masks[v_idx],
            )
        except Exception as e:
            sys.stderr.write(f"[dcbb_worker] FAIL {sym}/{side} {label}: {type(e).__name__}: {e}\n")
            continue
        result[label] = returns
    t_elapsed = time.perf_counter()
    sys.stderr.write(f"[dcbb_worker] done {sym}/{side}: n={n} variants=16\n")
    return result


def run_gr_dcbb_sweep(
    mode: str,
    account: str,
    symbols: List[str],
    sides: Optional[List[str]] = None,
    start: str = "2025-01-01",
    base_config: Optional[SweepConfig] = None,
    write_history: bool = False,
    workers: int = 4,
) -> None:
    """Fast DC/BB threshold sweep for the GR HTF confirmation gate.

    Loads each symbol's NPZ ONCE, precomputes all 16 HTF confirmation masks via
    evaluate_gr_htf_vec, then runs simulate_one_symbol 16× with the cached NPZ
    and pre-computed mask. Symbols processed in parallel (workers= controls pool size).

    Requires GOLDEN_RULE_HTF_MIN_TFS > 0 for the masks to have any effect.
    With MIN_TFS=0 all variants are identical (gate is off) — sweep warns and exits.
    """
    if evaluate_gr_htf_vec is None:
        print("[gr_dcbb_sweep] ERROR: evaluate_gr_htf_vec not importable — cannot sweep", flush=True)
        return
    sides = sides or ["LONG", "SHORT"]
    base_config = base_config or SweepConfig()
    min_tfs = int(getattr(base_config, "GOLDEN_RULE_HTF_MIN_TFS", 0))
    min_ind = int(getattr(base_config, "GOLDEN_RULE_MIN_IND", 1))
    if min_tfs <= 0:
        print(
            "[gr_dcbb_sweep] WARNING: GOLDEN_RULE_HTF_MIN_TFS=0 — HTF confirmation gate is OFF."
            " All DC/BB threshold variants will be identical. Set min_tfs >= 1 to sweep."
            " Aborting.", flush=True,
        )
        return

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_dt.timestamp())
    n_years = max((datetime.now(timezone.utc) - start_dt).total_seconds() / 86400.0 / 365.25, 0.01)
    n_variants = len(_DCBB_GRID)
    n_tasks = len(symbols) * len(sides)

    # Accumulate per-variant trade returns across all symbols
    variant_returns: Dict[str, List[float]] = {label: [] for label, _, _ in _DCBB_GRID}

    print(
        f"[gr_dcbb_sweep] mode={mode} symbols={len(symbols)} sides={sides} start={start}"
        f" min_tfs={min_tfs} min_ind={min_ind} variants={n_variants} workers={workers}"
        f" tasks={n_tasks}",
        flush=True,
    )
    t_run_start = time.perf_counter()

    # Build task list: (sym, side, mode, base_config, start_ts, min_tfs, min_ind)
    tasks = [
        (sym, side, mode, base_config, start_ts, min_tfs, min_ind)
        for sym in symbols
        for side in sides
    ]
    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_dcbb_worker, t): (t[0], t[1]) for t in tasks}
        for fut in as_completed(futures):
            sym_key, side_key = futures[fut]
            completed += 1
            try:
                sym_result = fut.result()
            except Exception as e:
                print(f"[gr_dcbb_sweep] [{completed}/{n_tasks}] {sym_key}/{side_key} WORKER_ERROR: {e}", flush=True)
                continue
            for label, ret_list in sym_result.items():
                variant_returns[label].extend(ret_list)
            n_trades_here = sum(len(v) for v in sym_result.values())
            print(f"[gr_dcbb_sweep] [{completed}/{n_tasks}] {sym_key}/{side_key} done — variants={len(sym_result)} trades_all_variants={n_trades_here}", flush=True)

    elapsed_total = time.perf_counter() - t_run_start
    print(f"\n[gr_dcbb_sweep] all {n_tasks} tasks done in {elapsed_total:.1f}s — reporting {n_variants} variants\n", flush=True)

    # Report canonical 9-field metrics for every variant
    results_dir = Path(__file__).resolve().parent / "data" / "sweep_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_csv = results_dir / f"gr_dcbb_sweep_{mode}_{ts_str}.csv"
    import csv as _csv
    header_written = False
    seen_fps: Dict[tuple, str] = {}
    mg_mode = "stocks" if mode == "tradier" else "crypto"
    with out_csv.open("w", newline="") as f:
        writer = None
        for label, dc_thr, bb_thr in _DCBB_GRID:
            rets = variant_returns[label]
            trades = len(rets)
            if trades >= 2:
                ps = metrics_guard.pool_sharpe(rets)
                sym_s = ps  # single-pool, no per-sym breakdown here
                acc_gain = float(sum(rets))
            else:
                ps = 0.0; sym_s = 0.0; acc_gain = 0.0
            n_syms = len(symbols)
            row = {
                "label": label, "dc_threshold": dc_thr, "bb_threshold": bb_thr,
                "pool_sharpe": round(ps, 4), "sym_sharpe": round(sym_s, 4),
                "avg_gain_trade": round(acc_gain / trades, 4) if trades > 0 else 0.0,
                "gain_per_yr": round(acc_gain / n_years, 2),
                "gain_sym_yr": round(acc_gain / max(1, n_syms) / n_years, 4),
                "trades": trades, "max_dd_pct": round(_max_dd_pct(rets), 4) if rets else 0.0,
                "n_syms": n_syms, "years": round(n_years, 3),
            }
            if writer is None:
                writer = _csv.DictWriter(f, fieldnames=list(row.keys()))
                writer.writeheader()
            writer.writerow(row)
            # Identical-score detection
            if trades >= 5:
                fp = (round(ps, 4), trades)
                if fp in seen_fps:
                    print(
                        f"[IDENTICAL_SCORE_WARNING] '{label}' (DC={dc_thr} BB={bb_thr}) == '{seen_fps[fp]}':"
                        f" pool_sharpe={ps:.4f} trades={trades} — knob not differentiating!", flush=True,
                    )
                else:
                    seen_fps[fp] = label
            print(
                f"  {label:35s}  DC={dc_thr:.2f}  BB={bb_thr:.2f}  "
                f"pool_sharpe={ps:+.4f}  trades={trades:5d}  gain/yr={acc_gain/n_years:+.1f}%",
                flush=True,
            )
    print(f"\n[gr_dcbb_sweep] results -> {out_csv}", flush=True)


# ════════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════════

def _parse_overrides(items: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for it in items or []:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        # Try parse as int / float / bool / str
        vs = v.strip()
        if vs.lower() in ("true", "false"):
            out[k.strip()] = (vs.lower() == "true")
        else:
            try:
                out[k.strip()] = int(vs)
            except ValueError:
                try:
                    out[k.strip()] = float(vs)
                except ValueError:
                    out[k.strip()] = vs
    return out


def main():
    ap = argparse.ArgumentParser(description="v8_vec_sweep — fast pure-vec backtest sweep engine")
    ap.add_argument("--mode", choices=("crypto", "tradier"), default="crypto")
    ap.add_argument("--account", default="flz")
    ap.add_argument("--symbols", required=True, help="comma-separated, e.g. BTC,ETH,SOL")
    ap.add_argument("--sides", default="LONG,SHORT", help="LONG,SHORT,LONG_SHORT")
    ap.add_argument("--start", default="2024-01-01", help="YYYY-MM-DD")
    ap.add_argument("--workers", type=int, default=1, help="(reserved for multiproc)")
    ap.add_argument("--max-bars", type=int, default=None, help="Cap bars per symbol (smoke testing)")
    ap.add_argument("--no-history", action="store_true", help="Skip /history/<acct>/ JSONL writes")
    ap.add_argument("--override", action="append", default=[], help="KEY=VAL config overrides (repeatable)")
    ap.add_argument("--tier", default="", help="Sweep tier: 'gr_dcbb_threshold' for fast DC/BB threshold sweep")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    sides = [s.strip().upper() for s in args.sides.split(",") if s.strip()]
    cfg = SweepConfig()
    for k, v in _parse_overrides(args.override).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            sys.stderr.write(f"WARN: unknown SweepConfig knob {k} — ignored\n")

    if args.tier == "gr_dcbb_threshold":
        run_gr_dcbb_sweep(
            mode=args.mode, account=args.account,
            symbols=syms, sides=sides,
            start=args.start, base_config=cfg,
            write_history=not args.no_history,
            workers=args.workers,
        )
        return 0

    t0 = time.perf_counter()
    s = run_sweep(
        mode=args.mode, account=args.account, symbols=syms, sides=sides,
        start=args.start, config=cfg, workers=args.workers,
        write_history=not args.no_history, max_bars=args.max_bars,
    )
    elapsed = time.perf_counter() - t0
    print(f"v8_vec_sweep done in {elapsed:.2f}s | bars={s['total_bars']:,}")
    print(f"  summary={s['summary_path']}")
    print(f"  trades ={s['trades_path']}")
    print(s["canonical_line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
