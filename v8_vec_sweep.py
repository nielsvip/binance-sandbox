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
from dataclasses import dataclass, field, asdict, replace as _dc_replace
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

# 2026-05-26 — Vec live-parity adapter trio. Default OFF preserves behaviour.
try:
    from vec_paths.exit_to_reduce_adapter import exit_to_reduce as _vec_exit_to_reduce
except Exception:
    _vec_exit_to_reduce = None
try:
    from vec_paths.ratio_reduce_sym_proxy import check_ratio_reduce_proxy as _vec_check_ratio_proxy
except Exception:
    _vec_check_ratio_proxy = None
try:
    from vec_paths.first_open_throttle import is_first_open_throttled as _vec_first_open_throttled
except Exception:
    _vec_first_open_throttled = None

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
    from vec_paths.breakout_retest import evaluate_breakout_retest_vec
except ImportError:
    evaluate_breakout_retest_vec = None
try:
    # 2026-05-22 USER MANDATE: real bottom/top detector — replaces scattergun micro-entries
    from vec_paths.quality_bottom_entry import (
        precompute_quality_bottom_long_mask,
        precompute_quality_top_short_mask,
    )
except ImportError:
    precompute_quality_bottom_long_mask = None
    precompute_quality_top_short_mask = None
try:
    # 2026-05-20 PORT — live 4-engine entry vote (WT/Stoch/DC/HTF/STDEV_MACRO)
    from vec_paths.live_entry_engine import live_entry_engine_passes_vec
except ImportError:
    live_entry_engine_passes_vec = None
try:
    from vec_paths.exit_r1_r2 import (
        check_r1_emergency_exit,
        check_r2_wt_vel_slow_exit,
    )
except ImportError:
    check_r1_emergency_exit = None
    check_r2_wt_vel_slow_exit = None
try:
    from vec_paths.newborn_loss_kill import check_newborn_loss_kill_exit
except ImportError:
    check_newborn_loss_kill_exit = None
try:
    from vec_paths.top_of_range_block import build_top_of_range_block_masks, build_breakout_masks
except ImportError:
    build_top_of_range_block_masks = None
    build_breakout_masks = None
try:
    from vec_paths.wt_dc_htf_gate import build_wt_dc_htf_gate_mask
except ImportError:
    build_wt_dc_htf_gate_mask = None
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
    from vec_paths.btc_dedicated import (
        build_btc_long_entry_mask,
        build_btc_short_entry_mask,
        build_btc_breakout_mask,
        build_btc_exit_mask,
        build_btc_divergence_block_mask,
        is_btc_symbol,
    )
except ImportError:
    build_btc_long_entry_mask = None
    build_btc_short_entry_mask = None
    build_btc_breakout_mask = None
    build_btc_exit_mask = None
    build_btc_divergence_block_mask = None
    is_btc_symbol = None
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
# WT exit family (2026-05-26) — WT_DIV / WT_ACCEL / WT_MOMENTUM / WT_EXHAUST cfg.
# Source: ez_positions_quick.py:3461-3464 (WT_EXHAUST+DIV_EXIT), 3523-3542
# (MI_DIV+MI_VELOCITY), and the cluster of WT_*_EXIT_ENABLED knobs the
# autonomous search has been writing into per-sym overrides for months.
# Pulled out of unknown-knob limbo so v8_vec_sweep stops silently ignoring them.
try:
    from vec_paths.wt_exits import build_wt_exit_masks as _build_wt_exit_masks
except ImportError:
    _build_wt_exit_masks = None
try:
    from vec_paths.delta_engine import check_delta_entry
except ImportError:
    check_delta_entry = None
# REGIME engine (live ez_regime.py vectorised) — adapts noloss/exit_gain_min per bar
# based on RANGING vs TRENDING classification. Reads 7 REGIME_* knobs from
# config.py:2423-2455 (REGIME_BTC_MARKET_WEIGHT, REGIME_RANGING_EXIT_GAIN_MIN,
# REGIME_RANGING_NOLOSS_MIN, REGIME_RANGING_WT_REDUCE_FRAC_LOW,
# REGIME_TRENDING_EXIT_GAIN_MIN, REGIME_TRENDING_K_RESET_THRESHOLD,
# REGIME_TRENDING_SLOT_RESERVE_PCT) plus master switch REGIME_DETECTION_ENABLED.
try:
    from vec_paths.regime_engine import build_regime_arrays as build_regime_arrays_vec
    from vec_paths.regime_engine import regime_adapted_min_gain as _regime_adapted_min_gain
except ImportError:
    build_regime_arrays_vec = None
    _regime_adapted_min_gain = None
# RZ cascade (Reverse Zone) — RZ_BREAKOUT entry + EXIT_SCORER N-of-5 exit + RZ_CASCADE
# multi-TF breakout/reverse signals. Source: ez_manage.py:32735+,
# wt_dc_exit_scorer.py:39+, old/v8_quick_engine.py:1802+. Defaults OFF except where
# the source's live default differs (RZ_CASCADE_ENABLED defaults False so existing
# sweeps unchanged until explicitly toggled).
try:
    from vec_paths.rz_cascade import (
        check_rz_breakout_entry_vec,
        check_rz_exit_vec,
        compute_rz_cascade_signals_vec,
    )
except ImportError:
    check_rz_breakout_entry_vec = None
    check_rz_exit_vec = None
    compute_rz_cascade_signals_vec = None
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
    def b(self, key: str, idx: int, default: bool = False) -> bool:
        # 2026-05-22 B4: added for vec_paths/delta_engine which reads bool fields
        # (dc_basis_crossover_1h etc.) via store.b(). Previously delta_engine crashed
        # with AttributeError when DELTA_ENGINE_ENABLED was True.
        arr = self._npz.get(key)
        if arr is None or idx >= len(arr): return default
        try: return bool(arr[idx])
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
    def breakout_entry(self) -> bool: return bool(getattr(self._state, "breakout_entry", False))
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
    # 2026-05-22 PARITY: live config.py has START_POSITION_SIZE=18.0,
    # MIN_POSITION_SIZE=1.0. Vec was using 55/25 — sizing doesn't affect %
    # returns but does shape min-qty rounding behavior.
    START_POSITION_SIZE: float = 18.0
    MIN_POSITION_SIZE: float = 1.0
    MIN_GAIN: float = 3.0
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    COMMISSION_BUFFER_PCT: float = 0.10
    # 2026-05-22 USER MANDATE: keep 0.08% round-trip cost for crypto. Earlier I
    # called this "phantom" — incorrect framing. The 0.08% was the intentional
    # conservative cost figure. The actual bug was that the override path was
    # silently broken (NameError on undefined module-level `config` swallowed by
    # try/except → hardcoded 0.08 literal returned regardless of any SweepConfig
    # override). NameError fixed (function now threads SweepConfig instance), but
    # the 0.08% value itself stays. Per-suffix knobs for future override:
    #   USDC: 0.08% (per user mandate — covers webhook-taker fallback worst case)
    #   USDT: 0.08% (same — keep conservative)
    #   Stocks: handled by config_tradier (~0.05%)
    ROUND_TRIP_COST_PCT: float = 0.08
    ROUND_TRIP_COST_USDC_PCT: float = 0.08
    ROUND_TRIP_COST_USDT_PCT: float = 0.08
    # 2026-05-22 USER MANDATE: NO EXIT AT LOW GAINS. /history audit: 48% of all
    # closes fired below +0.02% gain, 92.8% below +1.0%. System cannot capture
    # upside. Global floor: non-emergency exits must clear MIN_EXIT_GAIN_PCT.
    # Emergencies (R1/R3/R4/HEDGE_FAILED/RIDICULOUS_*/FROZEN_*/SUPERVISOR) bypass.
    # 0.0 = disabled. Default 1.0% well above 0.08% cost.
    MIN_EXIT_GAIN_PCT: float = 1.0
    # 2026-05-22 B3 PARITY: live config.py:66 MAX_AUGMENTS_PER_POSITION=20 (USER
    # 2026-05-21). Vec was uncapped — winners that should compound past 20× were
    # also stacking augments on mean-reversion losers, biasing pool_sharpe negative.
    MAX_AUGMENTS_PER_POSITION: int = 20
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
    REENTRY_B16_MIDRANGE_ENABLED: bool = True
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
    # WT exit family (2026-05-26) — vec_paths/wt_exits.py.
    # All default OFF: existing sweeps unchanged until the per-task override flips them.
    WT_DIV_EXIT_ENABLED: bool = False
    WT_DIV_EXIT_TF: str = "1h"
    WT_DIV_EXIT_REQUIRE_EXHAUST: bool = True
    WT_DIV_EXIT_MOM_TF: str = "1h"
    WT_ACCEL_EXIT_ENABLED: bool = False
    WT_ACCEL_EXIT_MIN_TFS: int = 2
    WT_ACCEL_EXIT_LONG_THR: float = -0.5
    WT_ACCEL_EXIT_SHORT_THR: float = 0.5
    WT_MOMENTUM_EXIT_ENABLED: bool = False
    WT_MOMENTUM_EXIT_THRESHOLD: int = 2
    WT_EXHAUST_EXIT_MIN_TFS: int = 0  # 0 = use live-equivalent (4h AND (1h OR 15m)) mask
    BTC_TECH_EXIT_WT_MIN_TFS: int = 3
    # ── noloss + hedge ────────────────────────────────────────────────────
    # 2026-05-26 USER MANDATE: NO_LOSS dead, hedge dead — REENTRY is the only protection.
    # Earlier 2026-05-22 partial fix set HEDGE_MODE/OBLIGATORY_HEDGE_ENABLED False but
    # missed HEDGE_SCAN_ENABLED (line ~548, a separate sweep-model bypass) and
    # UNIVERSAL_NOLOSS_GATE here — they kept hedge + no-loss alive in vec, producing
    # 6104 phantom HEDGE_OPEN events on flz BTCUSDC LONG proof (2026-05-26 02:56).
    # Both now False to match live config.py 2026-05-20 mandate.
    UNIVERSAL_NOLOSS_GATE: bool = False
    UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL: bool = True
    UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS: tuple = (
        "R1_DC_LOW4_3M_EMERGENCY", "R2_WT_VEL_SLOW", "WT_15M_VEL_SLOW",
        "HEDGE_FAILED", "STRUCTURAL_RANGE_SHIFT", "WT_3M_FORCE_OPEN",
    )
    # 2026-05-22 PARITY FIX: live config has HEDGE_MODE=False, OBLIGATORY_HEDGE_ENABLED=False
    # for most accounts. Vec defaulted True, hedging positions live wouldn't have hedged,
    # creating phantom HEDGE_CLOSE events that distort baseline. Match live defaults.
    OBLIGATORY_HEDGE_ENABLED: bool = False
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.5
    OBLIGATORY_HEDGE_PCT: float = 1.0
    OBLIGATORY_HEDGE_WT_USE_1M: bool = False
    OBLIGATORY_HEDGE_WT_USE_3M: bool = True
    OBLIGATORY_HEDGE_WT_USE_15M: bool = False
    OBLIGATORY_HEDGE_WT_USE_1H: bool = True
    OBLIGATORY_HEDGE_WT_TFS_REQUIRED: int = 0
    HEDGE_MODE: bool = False
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H: bool = True
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = False
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = False  # 2026-05-22 parity: live False
    HEDGE_FAILED_FALLBACK_CLOSE_ENABLED: bool = True
    HEDGE_COMPLETED_LOCKOUT_SECONDS: float = 60.0
    # ── augment / dup guard ───────────────────────────────────────────────
    DUP_GUARD_USE_GAIN_GATE: bool = False  # 2026-05-22 parity: live False — time-cooldown is fallback
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5
    PULLBACK_AUGMENT_ENABLED: bool = True
    PULLBACK_AUGMENT_REVERSAL_MIN: float = 1.0
    HARD_AUGMENT_LOCK_SECONDS: float = 900.0
    HARD_REDUCE_LOCK_SECONDS: float = 60.0
    AUGMENTATION_COOLDOWN_SECONDS: float = 540.0
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = False  # 2026-05-22 parity: live False
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
    # 2026-05-26 USER MANDATE: NO_LOSS dead, hedging dead — REENTRY is the only
    # protection. The 2026-05-17 every-bar noloss gate was the SOURCE of 6,104
    # OBLIGATORY_HEDGE_VEC events on flz BTCUSDC LONG (line 2674) — it called
    # evaluate_noloss_gate_vec which returned hedge_fire=True regardless of
    # HEDGE_SCAN_ENABLED / HEDGE_MODE / OBLIGATORY_HEDGE_ENABLED defaults. KILLED.
    # MtM losses now caught by R1/R2 emergency + organic exits + REENTRY.
    VEC_NOLOSS_GATE_ENABLED: bool = False
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
    # ── 2026-05-26 MARKET REGIME DETECTION (vec_paths/regime_engine.py) ──
    # Mirrors live ez_regime.py defaults (config.py:2423-2455). 7 knobs
    # promoted to SweepConfig so they no longer appear in UNKNOWN telemetry
    # when a per-task override flips them on. RANGING vs TRENDING per-bar
    # exit floor is wired into v8_vec_sweep simulate loop (_min_gain_bar).
    REGIME_DETECTION_ENABLED: bool = False
    REGIME_ENTER_TRENDING_THRESHOLD: float = 30.0
    REGIME_EXIT_TRENDING_THRESHOLD: float = 15.0
    REGIME_MIN_DWELL_BARS: int = 16
    REGIME_BTC_MARKET_WEIGHT: float = 0.5
    REGIME_RANGING_NOLOSS_MIN: float = 0.05
    REGIME_RANGING_EXIT_GAIN_MIN: float = 0.15
    REGIME_RANGING_WT_REDUCE_FRAC_LOW: float = 0.40
    REGIME_RANGING_SLOT_RESERVE_PCT: float = 0.60
    REGIME_TRENDING_NOLOSS_MIN: float = 0.50
    REGIME_TRENDING_EXIT_GAIN_MIN: float = 2.0
    REGIME_TRENDING_WT_REDUCE_FRAC_LOW: float = 0.10
    REGIME_TRENDING_SLOT_RESERVE_PCT: float = 0.40
    REGIME_TRENDING_K_RESET_THRESHOLD: float = 40.0
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
    # ── NEWBORN_LOSS_KILL (2026-05-21 USER post-ORDI mandate) ─────────────────
    # Closes any newborn position whose gain crosses below threshold. Tighter than R1.
    # 2026-05-22 V2 — added WT velocity confirmation after V1 (-0.5%/30min unguarded)
    # scored ΔSharpe -0.0139 (closed wicks → re-entered worse). REQUIRE_VEL_AGAINST=True
    # means we only close when momentum confirms the loss is real, not a wick.
    NEWBORN_LOSS_KILL_ENABLED: bool = False
    NEWBORN_LOSS_KILL_MIN_AGE_MIN: float = 15.0  # V4 — give position time to breathe
    NEWBORN_LOSS_KILL_WINDOW_MIN: float = 30.0
    NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT: float = 0.0    # V3 breakeven
    NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST: bool = True
    NEWBORN_LOSS_KILL_VEL_TF: str = ""           # auto: "3m" crypto / "5m" tradier
    NEWBORN_LOSS_KILL_SURGICAL_ONLY: bool = True # V4 — only fire on breakout_entry positions
    # ── TOP_OF_RANGE_BLOCK (2026-05-22 USER post-ORDI prevention mandate) ────
    # Block OPEN/AUGMENT when dc_position is extreme on ALL listed TFs.
    # ORDI was bought at dc_h1h — this prevents repeat. Default OFF until A/B proves +ΔSharpe.
    TOP_OF_RANGE_BLOCK_ENABLED: bool = False
    TOP_OF_RANGE_BLOCK_THRESHOLD: float = 0.95
    TOP_OF_RANGE_BLOCK_TF_LIST: str = "1h,4h,D"
    TOP_OF_RANGE_BLOCK_REQUIRE_ALL: bool = True
    # ── R2 WT velocity slow exit ──────────────────────────────────────────────
    WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED: bool = True
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT: float = 0.01
    WT_15M_VEL_SLOW_GAIN_BAND_PCT: float = 0.10
    WT_VEL_DECEL_RATIO: float = 0.5
    WT_VEL_USE_DECEL_RATIO_ONLY: bool = True
    WT_15M_VEL_NEAR_ZERO_THRESHOLD: float = 0.1
    R2_PEAK_MIN_PCT: float = 0.5
    R2_TF_LIST: tuple = ('15m',)   # 2026-05-22 parity: live ('15m',). Was None.
    # ── WT crossunder final exit ──────────────────────────────────────────────
    WT_CROSSUNDER_FINAL_ENABLED: bool = True
    WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED: bool = False
    WT_CROSSUNDER_FINAL_NOLOSS_BYPASS: bool = False
    # R10 Target 3 (2026-05-19): min-hold gate to suppress adjacent-bar scalp cascade.
    # R9 forensic: 211/3344 closes were adjacent-bar exits via WT_CROSSUNDER_FINAL.
    # Gate skips the exit when (bar_ts - state.opened_at) / base_tf_seconds < this.
    # 0 = no-op (legacy). Base TF: 180s crypto (3m) / 300s tradier (5m).
    WT_CROSSUNDER_FINAL_MIN_HOLD_BARS: int = 0
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
    GOLDEN_RULE_MIN_IND: int = 2  # 2026-05-22 parity: live 2
    GOLDEN_RULE_HTF_MIN_TFS: int = 1  # 2026-05-22 parity: live 1
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
    # 2026-05-26 USER MANDATE: hedging declared dead weeks ago. Master switch off.
    HEDGE_SCAN_ENABLED: bool = False        # master switch for sweep hedge model
    HEDGE_MIN_LOSS_PCT: float = -0.5        # matches OBLIGATORY_HEDGE_MIN_LOSS_PCT
    HEDGE_QTY_PCT: float = 1.0              # hedge qty as fraction of main qty
    HEDGE_CLOSE_ON_WT3M_FLIP: bool = True   # close hedge when wt1_3m turns back
    GR_HEDGE_SCORE_FLOOR: int = 15           # 2026-05-22 parity: live 15 (was vec 6 — too aggressive hedging)
    GR_HEDGE_REQUIRE_WT3M: bool = True      # always require wt1_3m against for hedge
    HEDGE_TRIGGER_REQUIRE_15M_OR_1H: bool = True  # require 15m or 1h WT alignment (can be overridden by GR)
    # ── IN_GAIN_TREND exit ────────────────────────────────────────────────────
    IN_GAIN_TREND_EXIT_ENABLED: bool = True
    IN_GAIN_TREND_BIG_WINNER_PCT: float = 10.0    # gain threshold for HTF tier
    IN_GAIN_TREND_MED_WINNER_PCT: float = 5.0     # gain threshold for 15m tier
    IN_GAIN_TREND_MIN_GAIN: float = 2.0           # minimum gain to even check
    # ── DELTA_ENGINE entries ──────────────────────────────────────────────────
    # 2026-05-22 B4: adapter .b() method added — DELTA_ENGINE can now run. Re-flipped to True.
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    DELTA_ENTRY_MIN_TF: int = 3
    DELTA_ENTRY_Z_THRESHOLD: float = 2.5
    DELTA_SPEED_SMOOTH: int = 5
    DELTA_TF_Z_THRESHOLD: float = 1.5
    DELTA_HTF_GATE: str = "none"
    # ── WT_15M_BOUNCE_OPEN — fresh 15m cross within BB + 4h/1h HTF in favor ─────
    WT_15M_BOUNCE_OPEN_ENABLED: bool = False
    WT_15M_BOUNCE_MAX_BARS_AGO: int = 2       # bars since 15m cross (2 bars = 30min)
    WT_15M_BOUNCE_BB_MIN: float = 0.05        # bb_pct_b lower bound (within BB)
    WT_15M_BOUNCE_BB_MAX: float = 0.95        # bb_pct_b upper bound (within BB)
    WT_15M_BOUNCE_REQUIRE_BOTH_HTF: bool = False  # False=OR(4h,1h), True=AND(4h,1h)
    # ── BB_BREAKOUT + BB_RSI_STOCH SCALP (Phase 9 — WIRED 2026-05-22) ─────
    BB_BREAKOUT_ENABLED: bool = False             # price outside BB on TF → entry trigger
    BB_BREAKOUT_TF: str = '15m'                  # which TF bb_pct_b to check
    BB_ENTRY_LONG_THRESHOLD: float = 0.30        # LONG trigger fires when bb_pct_b < this (pullback)
    BB_ENTRY_SHORT_THRESHOLD: float = 0.70       # SHORT trigger fires when bb_pct_b > this (pullback)
    BB_RSI_STOCH_SCALP_ENABLED: bool = False     # triple confirmation: BB + RSI + Stoch
    BB_RSI_STOCH_SCALP_TF: str = '15m'           # TF for triple confirmation (was hardcoded 5m)
    BB_RSI_STOCH_BB_MAX: float = 0.30            # bb_pct_b threshold (was 0.2)
    BB_RSI_STOCH_RSI_MAX: float = 40.0           # rsi threshold (was 30)
    BB_RSI_STOCH_K_MAX: float = 30.0             # stoch_k threshold (was 20)
    # ── BB PULLBACK GATE — pre-entry filter for ALL triggers (2026-05-23) ────
    BB_PULLBACK_GATE_ENABLED: bool = False       # gate ALL entries on BB position
    BB_PULLBACK_GATE_TF: str = '15m'             # timeframe for BB %B check
    BB_PULLBACK_GATE_LONG_MAX: float = 0.30      # LONG blocked when bb_pct_b > this
    BB_PULLBACK_GATE_SHORT_MIN: float = 0.70     # SHORT blocked when bb_pct_b < this
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
    # 2026-05-22 B1: per-(account, symbol, side) sizing multiplier.
    # Mirrors live ez_manage.execute_trade_action ZEC_FLZ_LONG_SIZE_MULT and
    # any future per-sym scalars. Format:
    #   {"flz:ZECUSDC_LONG": 5.0, "trb:NVDA_LONG": 2.0, ...}
    # Applied to per-trade returns on top of LONG_SIZE_MULT/SHORT_SIZE_MULT.
    # Single-sym sweeps pass via SIM_ACCOUNT_KEY env var or via SweepConfig.
    PER_SYM_SIZE_MULTS: dict = field(default_factory=lambda: {"flz:ZECUSDC_LONG": 5.0})
    SIM_ACCOUNT_KEY: str = "flz"
    # 2026-05-22 USER MANDATE: REAL bottom/top entry, ~1-2 per sym per day.
    # Replaces scattergun WT_3M/RZ_BASELINE/GR micro-fires when active.
    # See vec_paths/quality_bottom_entry.py for full rationale + factor list.
    QUALITY_BOTTOM_ENTRY_ENABLED: bool = True
    QUALITY_BOTTOM_PULLBACK_PCT: float = 3.0
    QUALITY_BOTTOM_K_15M_MAX: float = 25.0
    QUALITY_BOTTOM_K_1H_MAX: float = 35.0
    QUALITY_BOTTOM_PULLBACK_WINDOW_BARS: int = 96  # 24h on 15m basis (15m × 96 = 24h)
    QUALITY_BOTTOM_BB_PCTB_MAX: float = 0.20
    QUALITY_BOTTOM_DC_LOW_PROX_PCT: float = 0.5
    QUALITY_BOTTOM_VOL_MULT: float = 1.3
    QUALITY_BOTTOM_VOL_WINDOW_BARS: int = 20
    QUALITY_BOTTOM_HA_FLIP_LOOKBACK: int = 3
    QUALITY_BOTTOM_MAX_PER_DAY: int = 3
    QUALITY_BOTTOM_DISABLE_SCATTERGUN: bool = False
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
    # ── RZ CASCADE (Reverse Zone) — vectorized 2026-05-26 ──────────────────────
    # Vec ports of: ez_manage.py:32735+ RZ_BREAKOUT entry; wt_dc_exit_scorer.py
    # score_exit() (EXIT_SCORER_*); old/v8_quick_engine.py:1802 cascade signals.
    # Defaults match live config.py / config_tradier.py / flz active_config.json.
    RZ_BREAKOUT_ENTRY_ENABLED: bool = False         # ez_manage.py:32738 default OFF
    RZ_TOP_BB_THRESHOLD: float = 0.85               # live default
    RZ_BOT_BB_THRESHOLD: float = 0.15               # live default
    RZ_BREAKOUT_BAND: float = 0.05                  # ez_manage.py:32742 default
    EXIT_SCORER_ENABLED: bool = False               # wt_dc_exit_scorer used only when True
    EXIT_SCORER_MIN_CONDITIONS: int = 5             # 5=strict (default), 3=loose
    EXIT_SCORER_K_EXTREME: float = 75.0             # stoch K extreme floor (LONG) / ceiling (SHORT)
    EXIT_SCORER_DC_EXTREME: float = 0.80            # dc_position extreme floor / ceiling
    RZ_CASCADE_ENABLED: bool = False                # gate for multi-TF cascade
    RZ_CASCADE_MIN_TF_ALIGN: int = 1                # number of non-LTF TFs aligned for entry
    RZ_CASCADE_EXIT_MIN_REV_TFS: int = 2            # reverse-TF count for exit
    RZ_CASCADE_EXIT_ANY_TF: bool = True             # True=count any-TF reverse; False=LTF-only
    RZ_CASCADE_WT_DELTA_MIN: float = 0.1            # min |wt1-wt2| to count breakout
    RZ_CASCADE_VEL_MIN: float = 0.1                 # min |wt_velocity| to count breakout
    RZ_CASCADE_AT_RZ_BAND_PCT: float = 1.0          # % band around dc_high/low for at_upper/at_lower
    RZ_CASCADE_HIGH_LOOKBACK: int = 20              # lookback bars for new-high requirement
    RZ_CASCADE_REQUIRE_NEW_HIGH: bool = False       # require new close-high to count breakout
    RZ_CASCADE_USE_W_M: bool = False                # add W/M TFs to alignment count
    # ── DEAD-KNOB REWIRE (2026-05-18 18:00 UTC mandate) ─────────────────────────
    # Flags previously honoured only in backtest_v8_engine.py / tradier_manage.py
    # but ignored by the vec path → every V8_USE_VEC_ALL=1 sweep that toggled
    # these returned baseline-identical results (DEAD KNOB / DUPLICATE_OF).
    # GR_HTF_GATE — block OPEN by wt_bull_alignment / wt_bear_alignment count.
    # MFI_ENTRY   — block LONG OPEN when mfi_D > threshold (oversold proxy).
    GR_HTF_GATE_ENABLED: bool = False        # mirrors backtest_v8_engine.py:6062
    GR_HTF_REQUIRE_BULL: int = 1             # min wt_bull_alignment for LONG OPEN
    GR_HTF_REQUIRE_BEAR: int = 1             # min wt_bear_alignment for SHORT OPEN
    GOLDEN_RULE_HTF_GATE_MODE: str = "ALIGN" # ALIGN | OFF (reserved)
    MFI_ENTRY_ENABLED: bool = False          # mirrors backtest_v8_engine.py:6077
    MFI_LONG_THRESHOLD_D: float = 80.0       # block LONG when mfi_D > this (overbought zone, fixed 2026-05-18)
    MFI_SHORT_THRESHOLD_D: float = 20.0      # block SHORT when mfi_D < this (oversold zone)
    # ── CATALYST_VOLUME_GATE (2026-05-17) — block OPEN unless vol > N×50d-avg + DC-D break ──
    CATALYST_VOLUME_GATE_ENABLED: bool = False
    CATALYST_VOLUME_RATIO: float = 1.5
    # ── PENNY_STOCK_LONG_BLOCK (2026-05-17) — block LONG on stocks < $N ──
    PENNY_STOCK_LONG_BLOCK_ENABLED: bool = False
    PENNY_STOCK_LONG_BLOCK_PRICE_USD: float = 5.0
    # ── DC_BREAK_LOW_REQUIRE_HTF (2026-05-17) — bare short needs ≥N HTF bear ──
    DC_BREAK_LOW_REQUIRE_HTF_ENABLED: bool = False
    DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS: int = 2
    # ── BAR_MATURITY guards (2026-05-16) — block entry when bar incomplete ──
    WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED: bool = False
    WT_DC_ENTRY_BAR_MATURITY_BLOCK: float = 0.7
    DC_TIER4_BAR_MATURITY_BLOCK_ENABLED: bool = False
    DC_TIER4_BAR_MATURITY_BLOCK: float = 0.7
    # ── DT_TARGET_ATR (2026-05-17) — ATR-driven reduce target instead of flat % ──
    DT_TARGET_ATR_ENABLED: bool = False
    TRADIER_DC_DAYTRADE_TARGET_PCT: float = 0.005
    # ── GHOST_CLOSE_REQUIRE_CONFIRMATION (2026-05-16) — require N API zero-confirms before ghost close ──
    GHOST_CLOSE_REQUIRE_CONFIRMATION: bool = True
    # ── HTF_DIRECTION_GATE (2026-05-16) — block OPEN when D-WT against intended side ──
    HTF_DIRECTION_GATE_ENABLED: bool = False
    # ── TRADIER_ENTRY_SCORE_THRESHOLD (2026-05-16) — min entry score for OPEN ──
    ENTRY_SCORE_THRESHOLD: int = 0
    TRADIER_ENTRY_SCORE_THRESHOLD: int = 0
    # ── LIVE_ENTRY_ENGINE vote (2026-05-20 PORT) — 4-engine score aggregator ──
    # Mirrors ez_manage.py:32543 / tradier_manage.py:2657 etc. Defaults OFF
    # so existing sweeps are unchanged. When LIVE_ENTRY_ENGINE_ENABLED=True,
    # an OPEN must additionally satisfy:
    #   final_score = base_score + BOOST_SCORE*score_max >= threshold
    # where score_max = max(score among fired engines with score >= MIN_SCORE)
    # and threshold = WT_DC_ENTRY_THRESHOLD (tradier) | ENTRY_SCORE_THRESHOLD (crypto).
    # When LIVE_ENTRY_ENGINE_ENABLED=False the vec module is a pass-through.
    # 2026-05-22 PARITY: live has all 4 entry-engine flags True (WT/Stoch/DC/HTF).
    # If the vec port at v8_vec_sweep:623+ is wired, this enables the same gate.
    LIVE_ENTRY_ENGINE_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_WT_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_STOCH_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_DC_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_HTF_ENABLED: bool = True
    LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED: bool = False
    LIVE_ENTRY_ENGINE_MIN_SCORE: float = 0.5
    LIVE_ENTRY_ENGINE_BOOST_SCORE: float = 8.0
    WT_DC_ENTRY_THRESHOLD: float = 0.0  # tradier final-score threshold for vec gate
    WT_DC_HTF_GATE: str = "none"  # 'none'|'1h'|'4h'|'4h_D' — mirrors tradier_manage:2751; blocks wt_open_ok entry when HTF WT against
    # ── DC_LOW / BB FROZEN STOP (2026-05-18) — freeze DC/BB at entry as stop ──
    DC_LOW_FROZEN_STOP_ENABLED: bool = False
    DC_LOW_FROZEN_STOP_TF: str = '4h'
    DC_LOW_FROZEN_STOP_USE_4BAR: bool = False
    DC_LOW_FROZEN_STOP_FLOOR_PCT: float = -999.0
    BB_FROZEN_STOP_ENABLED: bool = False
    BB_FROZEN_STOP_TF: str = '1h'
    BB_FROZEN_STOP_FIELD: str = 'lower'
    # PHASE B 2026-05-19 candle-pattern stops (no-hedge regime)
    LH_STOP_ENABLED: bool = False
    LH_STOP_TF: str = '15m'
    LL_STOP_ENABLED: bool = False
    LL_STOP_TF: str = '15m'
    IB_STOP_ENABLED: bool = False
    IB_STOP_TF: str = '1h'
    IB_STOP_LOOKBACK: int = 20
    PULLBACK_STOP_ENABLED: bool = False
    PULLBACK_STOP_TF: str = '15m'
    PULLBACK_STOP_MODE: str = 'red2'      # red1 / red2 / red3
    BB_TAG_FAIL_STOP_ENABLED: bool = False
    BB_TAG_FAIL_STOP_TF: str = '1h'
    BB_TAG_FAIL_STOP_LOOKBACK: int = 5
    PATTERN_STOPS_LOSS_ONLY: bool = True  # only fire when gain<0; False = fire any time
    # 2026-05-19 USER MANDATE tight breakout stops
    NEVER_GO_RED_STOP_ENABLED: bool = False
    NEVER_GO_RED_MIN_PEAK_PCT: float = 0.3
    NEVER_GO_RED_BUFFER_PCT: float = 0.0
    CHANNEL_REENTRY_STOP_ENABLED: bool = False
    CHANNEL_REENTRY_STOP_TF: str = '1h'
    CHANNEL_REENTRY_STOP_FIELD: str = 'dc_high'  # 'dc_high' or 'bb_upper'
    # 2026-05-19 USER MANDATE Phase D — smart exhaustion exit
    EXH_EXIT_ENABLED: bool = False
    EXH_LTF_FIELD: str = 'bb_upper'         # 'bb_upper' or 'dc_high' (3m base; auto-flips for SHORT)
    EXH_USE_BASIS_CROSS: bool = True
    EXH_BASIS_FIELD: str = 'dc_basis'       # 'bb_basis' or 'dc_basis'
    EXH_K_15M_LONG_THRESHOLD: float = 80.0  # k_15m >= this required to fire LONG exit
    EXH_K_15M_SHORT_THRESHOLD: float = 20.0
    EXH_HTF_GATE_ENABLED: bool = False
    EXH_HTF_TF: str = '1h'
    EXH_HTF_FIELD: str = 'bb_upper'
    EXH_HTF_NEAR_PCT: float = 0.5
    # 2026-05-19 Phase E MTF protocol — proven entry rewrite
    MTF_ARMED_ENTRY_ENABLED: bool = False         # master — short-circuits legacy entries
    MTF_ARMED_HTF_LIST: str = '1h,4h,D,W'
    MTF_ARMED_BANDTYPES: str = 'dc,bb,wt'
    MTF_TRIGGER_WT_IN_BB_ENABLED: bool = True
    MTF_TRIGGER_WT_TF: str = '15m'
    MTF_TRIGGER_1H_DIRECT_ENABLED: bool = True
    MTF_TRIGGER_1H_DIRECT_BANDTYPE: str = 'dc'
    MTF_TRIGGER_15M_DIRECT_ENABLED: bool = False  # test variant
    MTF_TRIGGER_15M_DIRECT_BANDTYPE: str = 'dc'
    MTF_REQUIRE_ARMED_ANY: bool = True
    MTF_BIG_ADD_TF: str = '3m'                    # test {3m, 15m, 1h}
    MTF_REQUIRE_HH_BOUNCE: bool = True
    MTF_BIG_ADD_SIZE_MULT: float = 4.0            # BIG = 4× SMALL = 1× normal
    MTF_SMALL_SIZE_FRAC: float = 0.25
    MTF_SLOWDOWN_STALL_BARS: int = 5
    MTF_SLOWDOWN_STALL_PCT: float = 0.1
    MTF_SLOWDOWN_REQUIRE_BIG_ADD: bool = True
    # 2026-05-19 Phase F slowdown loosening
    MTF_SLOWDOWN_MASTER_ENABLED: bool = True
    MTF_SLOWDOWN_REQUIRE_PEAK_PCT: float = 0.0      # only fire after gain peaked >= this (0 = always)
    MTF_SLOWDOWN_MIN_AGE_S: float = 0.0             # don't fire for first N seconds
    MTF_SLOWDOWN_DISABLE_RED3M: bool = False
    MTF_SLOWDOWN_DISABLE_WTFLIP: bool = False
    MTF_SLOWDOWN_DISABLE_KDROP: bool = False
    MTF_SLOWDOWN_DISABLE_STALL: bool = False
    # 2026-05-19 Phase G — GR filter + WT-direction suspension
    MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED: bool = True
    MTF_ENTRY_REQUIRE_GR_FILTER: bool = True
    MTF_GR_FILTER_ENABLED: bool = True
    MTF_GR_MIN_TFS: int = 3
    MTF_GR_MIN_IND: int = 5
    MTF_GR_INVERT_DC_BB: bool = False
    # 2026-05-19 Phase H2 — slowdown TF selector (3m noisy, 15m steadier)
    MTF_SLOWDOWN_TF: str = '3m'
    # 2026-05-19 Phase I — compound exit (ATR trail + GR exit + WT cross + DC/BB reject)
    MTF_EXIT_USE_COMPOUND: bool = True   # 2026-05-22 parity: live True (Phase I MTF compound exit)
    MTF_ATR_TRAIL_ENABLED: bool = True
    MTF_ATR_TRAIL_MULT: float = 2.0
    MTF_ATR_TRAIL_TF: str = '15m'                # '15m', '1h', 'D'
    MTF_GR_EXIT_GATE_ENABLED: bool = True
    MTF_GR_EXIT_MIN_TFS: int = 3
    MTF_GR_EXIT_MIN_IND: int = 5
    MTF_WT_CROSS_EXIT_ENABLED: bool = True
    MTF_WT_CROSS_EXIT_TF: str = '15m'            # '15m', '1h', 'either'
    MTF_DC_REJECT_EXIT_ENABLED: bool = True
    MTF_DC_REJECT_EXIT_TF: str = '1h'  # 2026-05-22 parity: live 1h
    MTF_BB_REJECT_EXIT_ENABLED: bool = True
    MTF_BB_REJECT_EXIT_TF: str = '1h'  # 2026-05-22 parity: live 1h
    MTF_BB_REJECT_EXIT_LOOKBACK: int = 5
    MTF_REENTRY_COOLDOWN_BARS_HARD: int = 1
    # ── 4-FLAG REWIRE (2026-05-18 21:00 UTC mandate) ────────────────────────────
    # Mirrors ez_manage.py:18650+ (HTF_TREND_VETO), :38247+ (R3_HTF_FLIP/_4H),
    # :35454+ (BREAKOUT_RETEST_ARMED). Previously all 4 were reverted by A3 and
    # produced baseline-identical sweep results when re-enabled because vec path
    # had no implementation. Default OFF per current config.py/config_tradier.py.
    HTF_TREND_VETO_ENABLED: bool = False                  # mirrors ez_manage.py:18660
    HTF_TREND_VETO_SCORE_MIN_ABS: float = 5.0             # 2026-05-21 wired knob (engine-only — vec uses binary alignment)
    R3_HTF_FLIP_EXIT_ENABLED: bool = False                # mirrors ez_manage.py:38260
    R3_HTF_FLIP_4H_TIER_ENABLED: bool = False             # mirrors ez_manage.py:38290
    BREAKOUT_RETEST_ARMED_ENABLED: bool = False           # mirrors ez_manage.py:35464
    BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT: float = 0.30   # |px - dc_basis_D| / atr_D < this
    BREAKOUT_RETEST_ARMED_WINDOW_DAYS: int = 7            # window of D-bars for retest arm
    BREAKOUT_RETEST_ARMED_VOLUME_MULT: float = 1.25       # relative_volume_D >= MULT to arm (0 = off)
    BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX: int = 30         # LONG fires when stoch_k_3m_prev < this
    BREAKOUT_RETEST_ARMED_HTF_STACK_MIN: int = 2          # 2 = AND (live); 1 = OR over {15m, 1h}

    # 2026-05-26 Vec-engine live-parity knobs (mirror of config.py additions).
    # Default OFF/0 preserves bit-exact vec behaviour. Flip via --override for A/B sweeps.
    # 2026-05-26 22:00 BATCH 2 — user-clarified architecture (see
    # data/_diagnostic/REDUCE_VS_CLOSE_ARCHITECTURE.md). Default frac=1.0 = REDUCE
    # label + full close. PPL step 1 routes to STEP1_FRAC=0.5.
    VEC_LIVE_REDUCE_PARITY_ENABLED: bool = False
    VEC_LIVE_REDUCE_DEFAULT_FRAC: float = 1.0       # non-PPL exits → full close
    VEC_LIVE_REDUCE_PPL_STEP1_FRAC: float = 0.5     # PPL step 1 → genuine 50% partial
    VEC_LIVE_REDUCE_PPL_REASONS: tuple = ("PARTIAL_PROFIT_LOCK_STEP1", "PPL_STEP1")
    VEC_REDUCE_CASCADE_COOLDOWN_S: float = 0.0      # 0 = OFF; mirrors live _recent_reduces
    VEC_LIVE_REDUCE_PARITY_FRAC: float = 0.0        # legacy override (still respected if >0)
    VEC_LIVE_REDUCE_PARITY_KEEP_DUST: bool = False
    VEC_RATIO_REDUCE_PROXY_ENABLED: bool = False
    RATIO_TRIM_MIN_INTERVAL_S: float = 14400.0      # 4h between proxy trims
    RATIO_TRIM_MIN_AGE_S: float = 7200.0            # 2h min age before first trim
    RATIO_TRIM_GAIN_FLOOR: float = 0.5              # trim when gain <= 0.5%
    VEC_FIRST_OPEN_THROTTLE_BARS: int = 0           # 0 = OFF
    # 2026-05-26 BATCH 3 — UNIVERSAL_AUGMENT_GAIN_GATE mirror of config.py:744.
    # Live default is True (ez_manage.py:23956 gate active). VEC default kept
    # False to preserve Arm A switch_hunt baseline bit-exactly (the live default
    # diverges intentionally — flip via override in A/B arms). Block any AUGMENT
    # when (mark vs last_augmentation_price) gain <
    # MIN_GAIN_TO_BUY_AGGRESSIVELY (3.0% default).
    UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED: bool = False
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    # 2026-05-26 BATCH 3 — WT_CROSSUNDER_FINAL per-sym-side cooldown.
    # Live `_recent_reduces` Redis floor effectively prevents this exit from
    # firing >1×/3600s per sym-side (live = 0 fires; vec = 41,597 fires across
    # 11 syms in 1yr). VEC default kept 0.0 to preserve Arm A baseline.
    # Flip to 3600.0 to mirror live floor.
    WT_CROSSUNDER_FINAL_COOLDOWN_S: float = 0.0
    # 2026-05-26 BATCH 3 — PPL fire cooldown per sym-side (mirrors live's
    # _recent_ppl_fires Redis key, ~24h). After full close, the next OPEN
    # creates a new _pos which has ppl_fired=False, so STEP1 fires again
    # immediately on the next position lifetime. Live blocks via Redis key
    # for ~24h. VEC default kept 0.0 to preserve baseline.
    PPL_FIRE_COOLDOWN_S: float = 0.0
    # 2026-05-26 BATCH 4 — GR OPEN-on-empty cooldown per sym-side.
    # Mirrors live `_recent_opens` Redis floor (default 900s,
    # ez_manage.py:395) which blocks OPEN re-fires within the window even
    # when GR_COOLDOWN_S (600s) has elapsed. Vec was missing this gate, so
    # GR fired 7,975× on 11-sym ang 1yr vs live's 22. Default 0.0 = OFF
    # (preserves Arm A bit-exact baseline). Recommend 3600.0 for full
    # parity (catches live's 4× safety floor on top of the 900s Redis).
    GR_OPEN_COOLDOWN_S: float = 0.0
    # 2026-05-26 BATCH 4 — AUGMENT time cooldown per sym-side.
    # Mirrors live `_recent_augments` Redis floor (60-300s) which gates
    # AUGMENT independent of gain. UAG (gain gate) runs in parallel; this
    # is the time-axis sibling. Default 0.0 = OFF. Recommend 300.0.
    AUG_COOLDOWN_S: float = 0.0
    # 2026-05-26 BATCH 4 — refined WT_CROSSUNDER cooldown semantics.
    # When True, the cooldown is bypassed if state.last_reduce_was_loss is
    # True (urgent loss-cut — must not be blocked) OR the WT bias has
    # flipped opposite since the last fire (state.wt_state_last_fire).
    # Without this, Batch 3's cooldown blocked valid loss-cut exits and
    # degraded Sharpe (-0.0535 in Arm C). Default False keeps Batch 3
    # semantics (time-only) for backward-compat A/B comparison.
    WT_CROSSUNDER_REFINED_BYPASS_ENABLED: bool = False


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
    _dc_fstop_entry: float = 0.0
    _bb_fstop_entry: float = 0.0
    _ever_outside_channel: bool = False  # Phase C CHANNEL_REENTRY state
    gr_last_fire_ts: float = 0.0    # GOLDEN_RULE cooldown tracking
    # Phase E 2026-05-19 MTF protocol state
    _mtf_prior_bounce_price: float = 0.0
    _mtf_stall_count: int = 0
    _mtf_max_k_seen: float = 0.0
    _mtf_big_added: bool = False
    # Phase I 2026-05-19 compound exit state
    _mtf_atr_trail: float = 0.0
    _mtf_ever_outside_dc: bool = False
    # 2026-05-22 USER: MICRO_SCALP_USDC_CLOSE wire-in. Per-bar prev_gain so the
    # decel comparison (gain < prev_gain) works in vec just like ez_manage:40000+.
    prev_gain: float = 0.0
    # 2026-05-22 QUALITY_BOTTOM_ENTRY daily-cap state (USER ~1-2/day mandate)
    quality_entries_today: int = 0
    quality_last_day_utc: int = 0
    # 2026-05-22 SURGICAL NLK: tag entry as breakout when it bypassed TOR_BLOCK
    # via raw_dc_pos>1.0 exception. Surgical NLK only fires on these positions.
    breakout_entry: bool = False
    # 2026-05-26 BATCH 3 UAG: last augmentation price for UNIVERSAL_AUGMENT_GAIN_GATE.
    # Mirrors live position.last_augmentation_price (ez_manage.py:23971). Set on
    # OPEN + every AUGMENT. UAG gate blocks augment when
    # gain_since_last_add < MIN_GAIN_TO_BUY_AGGRESSIVELY (default 3.0%).
    last_augmentation_price: float = 0.0
    # 2026-05-26 BATCH 3 — WT_CROSSUNDER_FINAL per-sym-side cooldown tracking.
    # Live `_recent_reduces` Redis floor blocks this exit re-firing within
    # WT_CROSSUNDER_FINAL_COOLDOWN_S seconds. Default 0.0 = inert.
    wt_crossunder_last_fire_ts: float = 0.0
    # 2026-05-26 BATCH 3 — PPL fire cooldown tracking (sym-side persistent).
    # Mirrors live's _recent_ppl_fires Redis key ~24h. Survives full close
    # → reopen cycles (lives on SymState, not on _pos).
    ppl_last_fire_ts: float = 0.0
    # 2026-05-26 BATCH 4 — GR OPEN-on-empty cooldown anchor (sym-side
    # persistent). Mirrors live `_recent_opens` Redis floor 900s. Survives
    # close/reopen cycles — set on every GR OPEN, never reset in
    # position-close blocks. Independent from gr_last_fire_ts (which is
    # the GR-only cooldown for AUGMENT path at 600s default).
    gr_last_open_ts: float = 0.0
    # 2026-05-26 BATCH 4 — refined WT_CROSSUNDER cooldown state.
    # last_reduce_was_loss: set on every REDUCE/CLOSE event;
    # urgent loss-cuts may bypass the cooldown to avoid compounding losses.
    # wt_state_last_fire: signed WT bias at last fire (+1=LONG aligned,
    # -1=SHORT aligned, 0=unset). Bypass triggers when current bias flips
    # opposite vs the recorded value.
    last_reduce_was_loss: bool = False
    wt_state_last_fire: int = 0


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
    # 2b. WT exit family (DIV/ACCEL/MOMENTUM/EXHAUST_cfg) — 2026-05-26.
    # Auto-loaded into exit_gates so the simulate loop can check via mask[i].
    if _build_wt_exit_masks is not None:
        _wtex = _build_wt_exit_masks(npz, n, is_long, config)
        exit_gates['wt_div_exit'] = _wtex['div']['mask']
        exit_gates['wt_accel_exit'] = _wtex['accel']['mask']
        exit_gates['wt_momentum_exit'] = _wtex['momentum']['mask']
        # exhaust_cfg gives caller two masks: live-equivalent (mask) and BTC N-TF (mask_btc)
        exit_gates['wt_exhaust_cfg'] = _wtex['exhaust_cfg']['mask']
        exit_gates['wt_exhaust_btc'] = _wtex['exhaust_cfg']['mask_btc']
    else:
        _zero = np.zeros(n, dtype=bool)
        exit_gates['wt_div_exit'] = _zero
        exit_gates['wt_accel_exit'] = _zero
        exit_gates['wt_momentum_exit'] = _zero
        exit_gates['wt_exhaust_cfg'] = _zero
        exit_gates['wt_exhaust_btc'] = _zero
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
    # GR activation params (2026-05-18 2-stage gate — mirror live golden_rule_htf).
    _gr_require_act = bool(getattr(config, "GOLDEN_RULE_REQUIRE_ACTIVATION", False))
    _gr_act_tfs = list(getattr(config, "GOLDEN_RULE_ACTIVATION_TF_LIST", None) or [])
    _gr_entry_tfs = list(getattr(config, "GOLDEN_RULE_ENTRY_TF_LIST", None) or [])
    _gr_dc_thr = float(getattr(config, "GR_DC_EXTENDED_LONG", 0.0) or 0.0)
    _gr_bb_thr = float(getattr(config, "GR_BB_EXTENDED_LONG", 0.0) or 0.0)
    # GR against-score (vote_min=total-count) — used by GR_HEDGE_SCORE_FLOOR only
    _gr_against_count: Optional[np.ndarray] = None
    if int(config.GR_HEDGE_SCORE_FLOOR) > 0 and evaluate_gr_htf_vec is not None:
        _, _gr_against_count = evaluate_gr_htf_vec(
            npz, is_long=(not is_long), mode=mode, vote_min=1, n=n,
            dc_threshold=_gr_dc_thr, bb_threshold=_gr_bb_thr,
            require_activation=_gr_require_act,
            activation_tfs=_gr_act_tfs,
            entry_tfs=_gr_entry_tfs,
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
            dc_threshold=_gr_dc_thr, bb_threshold=_gr_bb_thr,
            require_activation=_gr_require_act,
            activation_tfs=_gr_act_tfs,
            entry_tfs=_gr_entry_tfs,
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

    # BB_BREAKOUT — pullback into BB on configured TF → entry trigger (2026-05-23 FIXED)
    # OLD logic (HARMFUL): fire when bb_pct_b > 1.0 (overbought momentum chase)
    # NEW logic (PROVEN): fire when bb_pct_b < threshold (oversold pullback)
    _bb_break_mask = np.zeros(n, dtype=bool)
    if config.BB_BREAKOUT_ENABLED:
        _bb_tf = config.BB_BREAKOUT_TF
        _bb_pct = np.nan_to_num(npz.get(f'bb_pct_b_{_bb_tf}', np.full(n, 0.5, dtype=np.float32))).astype(np.float32)
        if is_long:
            _bb_break_mask = _bb_pct < float(getattr(config, 'BB_ENTRY_LONG_THRESHOLD', 0.30))
        else:
            _bb_break_mask = _bb_pct > float(getattr(config, 'BB_ENTRY_SHORT_THRESHOLD', 0.70))

    # BB_RSI_STOCH_SCALP — triple confirmation at extremes → entry trigger (2026-05-23 FIXED)
    # NOW configurable TF + thresholds (was hardcoded 5m/0.2/30/20)
    _brs_mask = np.zeros(n, dtype=bool)
    if config.BB_RSI_STOCH_SCALP_ENABLED:
        _brs_tf = str(getattr(config, 'BB_RSI_STOCH_SCALP_TF', '15m'))
        _brs_bb = np.nan_to_num(npz.get(f'bb_pct_b_{_brs_tf}', np.full(n, 0.5, dtype=np.float32))).astype(np.float32)
        _brs_rsi = np.nan_to_num(npz.get(f'rsi_{_brs_tf}', np.full(n, 50.0, dtype=np.float32))).astype(np.float32)
        _brs_k = np.nan_to_num(npz.get(f'stoch_k_{_brs_tf}', np.full(n, 50.0, dtype=np.float32))).astype(np.float32)
        _brs_bb_max = float(getattr(config, 'BB_RSI_STOCH_BB_MAX', 0.30))
        _brs_rsi_max = float(getattr(config, 'BB_RSI_STOCH_RSI_MAX', 40.0))
        _brs_k_max = float(getattr(config, 'BB_RSI_STOCH_K_MAX', 30.0))
        if is_long:
            _brs_mask = (_brs_bb < _brs_bb_max) & (_brs_rsi < _brs_rsi_max) & (_brs_k < _brs_k_max)
        else:
            _brs_mask = (_brs_bb > (1.0 - _brs_bb_max)) & (_brs_rsi > (100.0 - _brs_rsi_max)) & (_brs_k > (100.0 - _brs_k_max))

    # ─── RZ CASCADE PRECOMPUTE (2026-05-26) ───────────────────────────────────
    _rz_break_mask = np.zeros(n, dtype=bool)
    _rz_cascade_entry = np.zeros(n, dtype=bool)
    _rz_cascade_exit = np.zeros(n, dtype=bool)
    _rz_scorer_exit = np.zeros(n, dtype=bool)
    if check_rz_breakout_entry_vec is not None:
        try:
            _rz_break_mask = check_rz_breakout_entry_vec(npz, n, is_long, config)
        except Exception as _e_rz1:
            sys.stderr.write(f"RZ_BREAKOUT vec precompute failed: {_e_rz1}\n")
            _rz_break_mask = np.zeros(n, dtype=bool)
    if compute_rz_cascade_signals_vec is not None:
        try:
            _rz_ltf = "3m" if mode == "crypto" else "5m"
            _rz_cascade_entry, _rz_cascade_exit = compute_rz_cascade_signals_vec(
                npz, n, is_long, config, ltf=_rz_ltf,
            )
        except Exception as _e_rz2:
            sys.stderr.write(f"RZ_CASCADE vec precompute failed: {_e_rz2}\n")
            _rz_cascade_entry = np.zeros(n, dtype=bool)
            _rz_cascade_exit = np.zeros(n, dtype=bool)
    if check_rz_exit_vec is not None:
        try:
            _rz_scorer_exit = check_rz_exit_vec(npz, n, is_long, config)
        except Exception as _e_rz3:
            sys.stderr.write(f"RZ EXIT_SCORER vec precompute failed: {_e_rz3}\n")
            _rz_scorer_exit = np.zeros(n, dtype=bool)

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

    # ─── DEAD-KNOB REWIRE precompute (2026-05-18) ───────────────────────────────
    # GR_HTF_GATE: wt_bull_alignment / wt_bear_alignment in NPZ (integer counts).
    # Block OPEN when LONG and wt_bull_alignment < GR_HTF_REQUIRE_BULL (mirror SHORT).
    _gr_htf_gate_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "GR_HTF_GATE_ENABLED", False)):
        _gh_bull_arr = np.nan_to_num(
            npz.get("wt_bull_alignment", np.zeros(n, dtype=np.float32)), nan=0.0
        ).astype(np.int32)
        _gh_bear_arr = np.nan_to_num(
            npz.get("wt_bear_alignment", np.zeros(n, dtype=np.float32)), nan=0.0
        ).astype(np.int32)
        if is_long:
            _gh_req = int(getattr(config, "GR_HTF_REQUIRE_BULL", 1))
            _gr_htf_gate_open_ok = _gh_bull_arr >= _gh_req
        else:
            _gh_req = int(getattr(config, "GR_HTF_REQUIRE_BEAR", 1))
            _gr_htf_gate_open_ok = _gh_bear_arr >= _gh_req
    # MFI_ENTRY: OVERBOUGHT FILTER (fixed 2026-05-18; prior semantics inverted/dead-gate).
    # Blocks LONG when mfi_D > MFI_LONG_THRESHOLD_D (80 = overbought zone, reversal expected).
    # Blocks SHORT when mfi_D < MFI_SHORT_THRESHOLD_D (20 = oversold zone).
    _mfi_entry_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "MFI_ENTRY_ENABLED", False)):
        _mfi_d_arr = np.nan_to_num(
            npz.get("mfi_D", np.full(n, 50.0, dtype=np.float32)), nan=50.0
        ).astype(np.float32)
        if is_long:
            _mfi_thr = float(getattr(config, "MFI_LONG_THRESHOLD_D", 80.0))
            _mfi_entry_open_ok = _mfi_d_arr <= _mfi_thr
        else:
            _mfi_short_thr = float(getattr(config, "MFI_SHORT_THRESHOLD_D", 20.0))
            _mfi_entry_open_ok = _mfi_d_arr >= _mfi_short_thr

    # ─── CATALYST_VOLUME_GATE precompute (2026-05-18 knob wiring) ──────────────
    # Derive volume_D_50_sma on-the-fly from existing volume_D field in NPZ.
    _catalyst_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "CATALYST_VOLUME_GATE_ENABLED", False)):
        _vol_d = np.nan_to_num(npz.get("volume_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float64)
        _vol_d_50_sma = npz.get("volume_D_50_sma", None)
        if _vol_d_50_sma is None:
            _ts_d = npz.get("timestamp_D", ts)
            _day_idx = np.concatenate([[0], np.where(np.diff(_ts_d) != 0)[0] + 1])
            _day_vols = _vol_d[_day_idx]
            _sma50 = np.full(len(_day_vols), np.nan, dtype=np.float64)
            for _j in range(len(_day_vols)):
                _s = max(0, _j - 49)
                _sma50[_j] = np.mean(_day_vols[_s:_j+1])
            _vol_d_50_sma = np.zeros(n, dtype=np.float64)
            for _j in range(len(_day_idx)):
                _start = _day_idx[_j]
                _end = _day_idx[_j+1] if _j+1 < len(_day_idx) else n
                _vol_d_50_sma[_start:_end] = _sma50[_j]
        else:
            _vol_d_50_sma = np.nan_to_num(np.asarray(_vol_d_50_sma, dtype=np.float64), nan=0.0)
        _ratio = float(getattr(config, "CATALYST_VOLUME_RATIO", 1.5))
        _vol_ok = _vol_d > (_vol_d_50_sma * _ratio)
        _dc_high_d = np.nan_to_num(npz.get("dc_high_D", np.zeros(n, dtype=np.float32)), nan=0.0)
        _dc_low_d = np.nan_to_num(npz.get("dc_low_D", np.zeros(n, dtype=np.float32)), nan=0.0)
        if is_long:
            _dc_break = close >= _dc_high_d
        else:
            _dc_break = close <= _dc_low_d
        _catalyst_open_ok = _vol_ok & _dc_break

    # ─── PENNY_STOCK_LONG_BLOCK precompute ──────────────────────────────────────
    _penny_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "PENNY_STOCK_LONG_BLOCK_ENABLED", False)) and is_long and mode == "tradier":
        _penny_floor = float(getattr(config, "PENNY_STOCK_LONG_BLOCK_PRICE_USD", 5.0))
        _penny_open_ok = close >= _penny_floor

    # ─── DC_BREAK_LOW_REQUIRE_HTF precompute ────────────────────────────────────
    _dc_break_htf_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "DC_BREAK_LOW_REQUIRE_HTF_ENABLED", False)) and not is_long:
        _htf_min = int(getattr(config, "DC_BREAK_LOW_REQUIRE_HTF_MIN_TFS", 2))
        _bear_count = np.zeros(n, dtype=np.int32)
        for _tf in ("15m", "1h", "4h", "D"):
            _w1 = np.nan_to_num(npz.get(f"wt1_{_tf}", np.zeros(n, dtype=np.float32)), nan=0.0)
            _w2 = np.nan_to_num(npz.get(f"wt2_{_tf}", np.zeros(n, dtype=np.float32)), nan=0.0)
            _bear_count += (_w1 < _w2).astype(np.int32)
        _dc_break_htf_ok = _bear_count >= _htf_min

    # ─── HTF_DIRECTION_GATE precompute ──────────────────────────────────────────
    _htf_dir_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "HTF_DIRECTION_GATE_ENABLED", False)):
        _wt1_d = np.nan_to_num(npz.get("wt1_D", np.zeros(n, dtype=np.float32)), nan=0.0)
        _wt2_d = np.nan_to_num(npz.get("wt2_D", np.zeros(n, dtype=np.float32)), nan=0.0)
        if is_long:
            _htf_dir_open_ok = _wt1_d > _wt2_d
        else:
            _htf_dir_open_ok = _wt1_d < _wt2_d

    # ─── HTF_TREND_VETO precompute (2026-05-18 4-FLAG REWIRE) ───────────────────
    # Mirrors ez_manage.py:18660+. Blocks OPEN/AUGMENT when wt1_D vs wt2_D against.
    # NOTE: ez_manage version also fires for AUGMENT and skips HEDGE_*/RULE_C
    # reasons. In vec there's no per-bar reason string, so we apply identically
    # to OPEN. (AUGMENT in vec follows OPEN gates upstream of size scaling.)
    _htf_trend_veto_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "HTF_TREND_VETO_ENABLED", False)):
        _htfv_w1_D = np.nan_to_num(npz.get("wt1_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _htfv_w2_D = np.nan_to_num(npz.get("wt2_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _htfv_data_ok = (np.abs(_htfv_w1_D) > 1e-9) & (np.abs(_htfv_w2_D) > 1e-9)
        if is_long:
            _htfv_aligned = _htfv_w1_D > _htfv_w2_D
        else:
            _htfv_aligned = _htfv_w1_D < _htfv_w2_D
        # Fail-open when data missing (mirrors live behaviour: _htfv_data_ok=False → no block)
        _htf_trend_veto_ok = (~_htfv_data_ok) | _htfv_aligned

    # ─── BB_PULLBACK_GATE precompute (2026-05-23) ───────────────────────────────
    from vec_paths.bb_pullback_gate import precompute_bb_pullback_gate
    _bb_pullback_ok = precompute_bb_pullback_gate(npz, config, is_long, n, mode)

    # ─── R3_HTF_FLIP precompute (2026-05-18 4-FLAG REWIRE) ──────────────────────
    # Mirrors ez_manage.py:38260+. EXIT when (a) Daily tier: px breaks dc_basis_D
    # AGAINST side OR (wt1_D/wt2_D AND wt1_W/wt2_W flip against), or (b) 4H tier:
    # px crosses ema_20_4h ± atr_4h against side AND wt1_4h/wt2_4h flip against.
    _r3hf_daily_fire = np.zeros(n, dtype=bool)
    _r3hf_4h_fire = np.zeros(n, dtype=bool)
    if bool(getattr(config, "R3_HTF_FLIP_EXIT_ENABLED", False)):
        _r3hf_dcD = np.nan_to_num(npz.get("dc_basis_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _r3hf_w1D = np.nan_to_num(npz.get("wt1_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _r3hf_w2D = np.nan_to_num(npz.get("wt2_D", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _r3hf_w1W = np.nan_to_num(npz.get("wt1_W", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _r3hf_w2W = np.nan_to_num(npz.get("wt2_W", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _r3hf_dc_present = _r3hf_dcD > 0
        if is_long:
            _r3hf_dc_break = (close < _r3hf_dcD) & _r3hf_dc_present
            _r3hf_wt_flip = (_r3hf_w1D < _r3hf_w2D) & (_r3hf_w1W < _r3hf_w2W)
        else:
            _r3hf_dc_break = (close > _r3hf_dcD) & _r3hf_dc_present
            _r3hf_wt_flip = (_r3hf_w1D > _r3hf_w2D) & (_r3hf_w1W > _r3hf_w2W)
        _r3hf_daily_fire = _r3hf_dc_break | _r3hf_wt_flip
        if bool(getattr(config, "R3_HTF_FLIP_4H_TIER_ENABLED", False)):
            _r3hf_ema4h = np.nan_to_num(npz.get("ema_20_4h", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
            _r3hf_atr4h = np.nan_to_num(npz.get("atr_4h", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
            _r3hf_w14h = np.nan_to_num(npz.get("wt1_4h", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
            _r3hf_w24h = np.nan_to_num(npz.get("wt2_4h", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
            _r3hf_4h_data_ok = (_r3hf_ema4h > 0) & (_r3hf_atr4h > 0)
            if is_long:
                _r3hf_4h_fire = _r3hf_4h_data_ok & (close < (_r3hf_ema4h - _r3hf_atr4h)) & (_r3hf_w14h < _r3hf_w24h)
            else:
                _r3hf_4h_fire = _r3hf_4h_data_ok & (close > (_r3hf_ema4h + _r3hf_atr4h)) & (_r3hf_w14h > _r3hf_w24h)

    # ─── BREAKOUT_RETEST_ARMED precompute (2026-05-18 — delegated to vec_paths) ──
    # Mirrors ez_manage.py:35464+. SIMPLIFIED stateless. Delegates to
    # vec_paths.breakout_retest.evaluate_breakout_retest_vec so the four
    # extra knobs (WINDOW_DAYS, VOLUME_MULT, K_3M_PREV_MAX, HTF_STACK_MIN)
    # actually affect the fire mask — previous inline impl honored only
    # ENABLED + RETEST_ATR_MULT (the other arms were DROPPED_PHANTOM_KNOB).
    _ra_fire_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "BREAKOUT_RETEST_ARMED_ENABLED", False)) and evaluate_breakout_retest_vec is not None:
        try:
            _ra_features = {
                "close": close,
                "dc_basis_D": npz.get("dc_basis_D"),
                "atr_D": npz.get("atr_D"),
                "wt1_D": npz.get("wt1_D"),
                "wt2_D": npz.get("wt2_D"),
                "wt1_W": npz.get("wt1_W"),
                "wt2_W": npz.get("wt2_W"),
                "wt1_15m": npz.get("wt1_15m"),
                "wt2_15m": npz.get("wt2_15m"),
                "wt1_1h": npz.get("wt1_1h"),
                "wt2_1h": npz.get("wt2_1h"),
                "stoch_k_3m": npz.get("stoch_k_3m"),
                "stoch_k_3m_prev": npz.get("stoch_k_3m_prev"),
                "k_3m": npz.get("k_3m"),
                "volume_D": npz.get("volume_D"),
                "relative_volume_D": npz.get("relative_volume_D"),
                "timestamp_D": npz.get("timestamp_D"),
            }
            _ra_long, _ra_short = evaluate_breakout_retest_vec(_ra_features, config, mode=mode)
            _ra_fire_mask = (_ra_long if is_long else _ra_short).astype(bool)
        except Exception:
            _ra_fire_mask = np.zeros(n, dtype=bool)

    # ─── 2026-05-22 QUALITY_BOTTOM_ENTRY precompute (USER MANDATE) ──────────────
    # Multi-factor AND-gate: HTF bullish + ≥3% pullback + multi-TF oversold +
    # HA reversal + lower-band proximity + volume confirm.
    # See vec_paths/quality_bottom_entry.py.
    _qb_fire_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "QUALITY_BOTTOM_ENTRY_ENABLED", True)) and (
        precompute_quality_bottom_long_mask is not None
        and precompute_quality_top_short_mask is not None
    ):
        try:
            if is_long:
                _qb_fire_mask = precompute_quality_bottom_long_mask(npz, config).astype(bool)
            else:
                _qb_fire_mask = precompute_quality_top_short_mask(npz, config).astype(bool)
        except Exception as _qb_exc:
            _qb_fire_mask = np.zeros(n, dtype=bool)

    # ─── 2026-05-26 BTC_DEDICATED precompute (USER MANDATE: vectorize live btc_loop) ──
    # Mirrors btc_loop.should_enter_btc_long/short + detect_btc_breakout + should_exit_btc.
    # Only fires for BTC symbols (BTCUSDC/BTCUSDT) per live `_btc_dedicated_disabled`.
    # See vec_paths/btc_dedicated.py.
    _btc_entry_mask = np.zeros(n, dtype=bool)
    _btc_breakout_mask = np.zeros(n, dtype=bool)
    _btc_exit_mask = np.zeros(n, dtype=bool)
    if (
        bool(getattr(config, "BTC_DEDICATED_ENABLED", False))
        and build_btc_long_entry_mask is not None
        and is_btc_symbol is not None
        and is_btc_symbol(symbol)
    ):
        try:
            if is_long:
                _btc_entry_mask = build_btc_long_entry_mask(npz, n, config).astype(bool)
            else:
                _btc_entry_mask = build_btc_short_entry_mask(npz, n, config).astype(bool)
        except Exception:
            _btc_entry_mask = np.zeros(n, dtype=bool)
        try:
            _btc_breakout_mask = build_btc_breakout_mask(npz, n, is_long, config).astype(bool)
        except Exception:
            _btc_breakout_mask = np.zeros(n, dtype=bool)
        try:
            _btc_exit_mask = build_btc_exit_mask(npz, n, is_long, config).astype(bool)
        except Exception:
            _btc_exit_mask = np.zeros(n, dtype=bool)

    # ─── 2026-05-26 MOMENTUM_BREAKOUT precompute (USER MANDATE: fire entry the bar of
    # the breakout, not 4-16hr after — diagnostic showed 30 sustained 5%+ moves missed
    # by 250-963 min on BTCUSDC). Vec_paths/momentum_breakout.py: close > prior 1h DC
    # high (LONG) / < prior 1h DC low (SHORT) confirmed by 3m WT direction. Default OFF.
    _mom_break_long_mask = np.zeros(n, dtype=bool)
    _mom_break_short_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "MOMENTUM_BREAKOUT_ENABLED", False)):
        try:
            from vec_paths.momentum_breakout import build_momentum_breakout_masks
            _mb_long, _mb_short = build_momentum_breakout_masks(npz, config)
            if is_long:
                _mom_break_long_mask = _mb_long.astype(bool)
            else:
                _mom_break_short_mask = _mb_short.astype(bool)
        except Exception:
            pass

    # ─── QUALITY_TOP_EXIT precompute (2026-05-23 USER MANDATE "in at bottom, out at top") ─
    # Symmetric exit partner for quality_bottom_entry. LONG exit fires on top-short mask;
    # SHORT exit fires on bottom-long mask. Default OFF.
    _qt_exit_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "QUALITY_TOP_EXIT_ENABLED", False)):
        try:
            from vec_paths.quality_top_exit import precompute_quality_top_exit_mask as _qte
            _qt_exit_mask = _qte(npz, config, is_long).astype(bool)
        except Exception:
            _qt_exit_mask = np.zeros(n, dtype=bool)

    # ─── DC_LOW / BB FROZEN STOP precompute ─────────────────────────────────────
    _dc_fstop_arr = np.zeros(n, dtype=np.float32)
    _bb_fstop_arr = np.zeros(n, dtype=np.float32)
    if bool(getattr(config, "DC_LOW_FROZEN_STOP_ENABLED", False)):
        _fs_tf = str(getattr(config, "DC_LOW_FROZEN_STOP_TF", "4h"))
        _fs_4bar = bool(getattr(config, "DC_LOW_FROZEN_STOP_USE_4BAR", False))
        if is_long:
            _fld = f"dc_low4_{_fs_tf}" if _fs_4bar else f"dc_low_{_fs_tf}"
        else:
            _fld = f"dc_high4_{_fs_tf}" if _fs_4bar else f"dc_high_{_fs_tf}"
        _dc_fstop_arr = np.nan_to_num(npz.get(_fld, np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
    if bool(getattr(config, "BB_FROZEN_STOP_ENABLED", False)):
        _bb_tf = str(getattr(config, "BB_FROZEN_STOP_TF", "1h"))
        _bb_field = str(getattr(config, "BB_FROZEN_STOP_FIELD", "lower"))
        if _bb_field == "lower":
            _bb_fstop_arr = np.nan_to_num(npz.get(f"bb_lower_{_bb_tf}", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        elif _bb_field == "upper":
            _bb_fstop_arr = np.nan_to_num(npz.get(f"bb_upper_{_bb_tf}", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        else:  # basis = midline = (upper + lower) / 2
            _bbu = np.nan_to_num(npz.get(f"bb_upper_{_bb_tf}", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
            _bbl = np.nan_to_num(npz.get(f"bb_lower_{_bb_tf}", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
            _bb_fstop_arr = np.where((_bbu > 0) & (_bbl > 0), (_bbu + _bbl) / 2.0, 0.0).astype(np.float32)
    _fstop_floor = float(getattr(config, "DC_LOW_FROZEN_STOP_FLOOR_PCT", -999.0))

    # ─── PHASE B 2026-05-19 candle-pattern stops precompute ─────────────────────
    try:
        from vec_paths.candle_pattern_stops import (
            build_all_pattern_stop_arrays, check_pattern_stops_at_bar,
        )
        _pattern_stop_arrays = build_all_pattern_stop_arrays(npz, n, is_long, config)
        _pattern_stop_active = any(v is not None for v in _pattern_stop_arrays.values())
    except Exception as _e:
        sys.stderr.write(f"candle_pattern_stops precompute failed {symbol}/{side}: {_e}\n")
        _pattern_stop_arrays = {}
        _pattern_stop_active = False
        check_pattern_stops_at_bar = None
    _pattern_stops_loss_only = bool(getattr(config, "PATTERN_STOPS_LOSS_ONLY", True))

    # ─── PHASE C 2026-05-19 tight breakout stops precompute ─────────────────────
    try:
        from vec_paths.tight_breakout_stops import (
            build_channel_arrays, check_never_go_red, check_channel_reentry,
        )
        _chre_upper_arr, _chre_lower_arr = build_channel_arrays(npz, n, is_long, config)
        _ngr_active = bool(getattr(config, "NEVER_GO_RED_STOP_ENABLED", False))
        _chre_active = bool(getattr(config, "CHANNEL_REENTRY_STOP_ENABLED", False))
    except Exception as _e:
        sys.stderr.write(f"tight_breakout_stops precompute failed {symbol}/{side}: {_e}\n")
        _ngr_active = False
        _chre_active = False
        _chre_upper_arr = np.zeros(n, dtype=np.float32)
        _chre_lower_arr = np.zeros(n, dtype=np.float32)
        check_never_go_red = None
        check_channel_reentry = None

    # ─── PHASE D 2026-05-19 smart exhaustion exit precompute ────────────────────
    try:
        from vec_paths.exhaustion_exit import (
            build_exh_arrays, check_exh_exit_at_bar,
        )
        _exh_arrays = build_exh_arrays(npz, n, is_long, config)
        _exh_active = bool(_exh_arrays.get("enabled", False))
    except Exception as _e:
        sys.stderr.write(f"exhaustion_exit precompute failed {symbol}/{side}: {_e}\n")
        _exh_arrays = {"enabled": False}
        _exh_active = False
        check_exh_exit_at_bar = None

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
    # 2026-05-26 REGIME engine vectorised — when REGIME_DETECTION_ENABLED=True,
    # the per-bar exit_gain_min replaces the scalar `min_gain` for non-emergency
    # exits. Mirrors live ez_regime.get_regime_params() noloss/exit_gain_min selection.
    # See vec_paths/regime_engine.py. Reads 7 user-mandated REGIME_* knobs.
    if build_regime_arrays_vec is not None:
        try:
            _regime_arrays = build_regime_arrays_vec(npz, n, config)
        except Exception as _re:
            sys.stderr.write(f"regime_engine precompute failed {symbol}/{side}: {_re}\n")
            _regime_arrays = {"enabled": False}
    else:
        _regime_arrays = {"enabled": False}
    _regime_active = bool(_regime_arrays.get("enabled", False))
    _regime_exit_gain = _regime_arrays.get("exit_gain_min")
    # Side-asymmetric sizing — applied as RETURN MULTIPLIER (treats SIZE_MULT as leverage).
    # Without this, pool_sharpe is computed from per-trade % which is qty-independent —
    # changing LONG_SIZE_MULT 1.0→3.0 has zero effect on metrics (bug repro 2026-05-17).
    # By applying mult to pnl_pct at close-time, a 3x position produces 3x the equity return
    # per trade — economically equivalent to "deploy 3x dollars at the same setup."
    _side_return_mult = float(getattr(config, "LONG_SIZE_MULT", 1.0)) if is_long else \
                        float(getattr(config, "SHORT_SIZE_MULT", 1.0))
    # 2026-05-22 B1: per-(account, symbol, side) sizing multiplier — multiplied on top
    # of side_return_mult. Mirrors live ZEC_FLZ_LONG_SIZE_MULT=5.0 etc.
    _psm_dict = getattr(config, "PER_SYM_SIZE_MULTS", {}) or {}
    _psm_account = str(getattr(config, "SIM_ACCOUNT_KEY", "flz") or "flz")
    _psm_key = f"{_psm_account}:{symbol}_{'LONG' if is_long else 'SHORT'}"
    _psm_mult = float(_psm_dict.get(_psm_key, 1.0))
    if _psm_mult != 1.0:
        _side_return_mult = _side_return_mult * _psm_mult

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

    # ─── PHASE E 2026-05-19 MTF protocol precompute ────────────────────────────
    try:
        from vec_paths.mtf_armed_entries import (
            build_mtf_arrays, check_mtf_small_entry,
            check_mtf_big_add, check_mtf_slowdown,
        )
        _mtf_arrays = build_mtf_arrays(npz, n, is_long, config)
        _mtf_active = bool(_mtf_arrays.get("enabled", False))
    except Exception as _e:
        sys.stderr.write(f"mtf_armed_entries precompute failed {symbol}/{side}: {_e}\n")
        _mtf_arrays = {"enabled": False}
        _mtf_active = False
        check_mtf_small_entry = check_mtf_big_add = check_mtf_slowdown = None
    _mtf_small_frac = float(getattr(config, "MTF_SMALL_SIZE_FRAC", 0.25))
    _mtf_big_mult = float(getattr(config, "MTF_BIG_ADD_SIZE_MULT", 4.0))

    # ─── PHASE G 2026-05-19 GR filter pass mask precompute ─────────────────────
    try:
        from vec_paths.gr_filter_vec import build_gr_filter_mask
        if _mtf_active and bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True)):
            _gr_filter_mask = build_gr_filter_mask(npz, n, is_long, mode, config)
        else:
            _gr_filter_mask = np.ones(n, dtype=bool)
    except Exception as _e:
        sys.stderr.write(f"gr_filter_vec precompute failed {symbol}/{side}: {_e}\n")
        _gr_filter_mask = np.ones(n, dtype=bool)
    _mtf_require_gr = bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True))

    # ─── 2026-05-22 TOP_OF_RANGE_BLOCK precompute (post-ORDI prevention) ────
    if build_top_of_range_block_masks is not None:
        try:
            class _StoreShim:
                def __init__(self, prices, npz):
                    self.prices = prices
                    self.arrays = npz
                def f(self, key, idx, default=0.0):
                    arr = self.arrays.get(key)
                    if arr is None or idx >= len(arr):
                        return default
                    return float(arr[idx])
            _tor_shim = _StoreShim(prices=npz.get("close"), npz=npz)
            _tor_block_long, _tor_block_short = build_top_of_range_block_masks(_tor_shim, n, config)
        except Exception as _e:
            sys.stderr.write(f"top_of_range_block precompute failed {symbol}/{side}: {_e}\n")
            _tor_block_long = np.zeros(n, dtype=bool)
            _tor_block_short = np.zeros(n, dtype=bool)
    else:
        _tor_block_long = np.zeros(n, dtype=bool)
        _tor_block_short = np.zeros(n, dtype=bool)
    # 2026-05-22 SURGICAL NLK: precompute breakout masks regardless of TOR_BLOCK_ENABLED.
    # Used to tag pos_state.breakout_entry on OPEN events so NLK can fire surgically.
    if build_breakout_masks is not None:
        try:
            class _BkShim:
                def __init__(self, prices, npz):
                    self.prices = prices
                    self.arrays = npz
                def f(self, key, idx, default=0.0):
                    arr = self.arrays.get(key)
                    if arr is None or idx >= len(arr):
                        return default
                    return float(arr[idx])
            _bk_shim = _BkShim(prices=npz.get("close"), npz=npz)
            _breakout_long_any, _breakout_short_any = build_breakout_masks(_bk_shim, n, config)
        except Exception as _e:
            sys.stderr.write(f"build_breakout_masks failed {symbol}/{side}: {_e}\n")
            _breakout_long_any = np.zeros(n, dtype=bool)
            _breakout_short_any = np.zeros(n, dtype=bool)
    else:
        _breakout_long_any = np.zeros(n, dtype=bool)
        _breakout_short_any = np.zeros(n, dtype=bool)

    # ─── WT_DC_HTF_GATE precompute (mirrors tradier_manage:2751-2762) ──────────
    # Blocks wt_open_ok (WT force-open) entries when the configured HTF WT is
    # against the trade direction. Gate: 'none'|'1h'|'4h'|'4h_D'.
    if build_wt_dc_htf_gate_mask is not None:
        try:
            _wt_dc_htf_gate_block = build_wt_dc_htf_gate_mask(npz, n, is_long, config)
        except Exception as _e:
            sys.stderr.write(f"wt_dc_htf_gate precompute failed {symbol}/{side}: {_e}\n")
            _wt_dc_htf_gate_block = np.zeros(n, dtype=bool)
    else:
        _wt_dc_htf_gate_block = np.zeros(n, dtype=bool)

    # ─── PHASE I 2026-05-19 compound exit precompute ──────────────────────────
    try:
        from vec_paths.mtf_armed_entries import build_compound_exit_arrays, check_mtf_compound_exit
        from vec_paths.gr_filter_vec import build_gr_filter_mask as _build_gr_mask
        _mtf_compound_active = bool(getattr(config, "MTF_EXIT_USE_COMPOUND", False)) and _mtf_active
        if _mtf_compound_active:
            _compound_arrays = build_compound_exit_arrays(npz, n, is_long, mode, config)
            # GR exit mask = GR filter pass for OPPOSITE side
            _gr_exit_min_tfs = int(getattr(config, "MTF_GR_EXIT_MIN_TFS", 3))
            _gr_exit_min_ind = int(getattr(config, "MTF_GR_EXIT_MIN_IND", 5))
            # Temporarily build a config wrapper for opposite-side scoring
            class _GRExitCfg:
                MTF_GR_FILTER_ENABLED = True
                MTF_GR_MIN_TFS = _gr_exit_min_tfs
                MTF_GR_MIN_IND = _gr_exit_min_ind
                MTF_GR_INVERT_DC_BB = False
            _compound_arrays["gr_exit_mask"] = _build_gr_mask(npz, n, not is_long, mode, _GRExitCfg())
        else:
            _compound_arrays = {}
    except Exception as _e:
        sys.stderr.write(f"compound_exit precompute failed {symbol}/{side}: {_e}\n")
        _compound_arrays = {}
        _mtf_compound_active = False
        check_mtf_compound_exit = None

    for i in range(n):
        bar_ts = float(ts[i])
        mark = float(close[i])
        if mark <= 0 or not np.isfinite(mark):
            continue
        # 2026-05-26 REGIME-ADAPTED per-bar exit floor — replaces scalar `min_gain`
        # in non-emergency exit gates when REGIME_DETECTION_ENABLED=True. In
        # RANGING regime the floor drops to REGIME_RANGING_EXIT_GAIN_MIN (~0.15%)
        # so vec captures fast mean-reversion exits; in TRENDING regime it stays
        # at REGIME_TRENDING_EXIT_GAIN_MIN (~2.0%) to let winners run. When the
        # master switch is False, _min_gain_bar == min_gain (no behaviour change).
        if _regime_active and _regime_exit_gain is not None and i < _regime_exit_gain.shape[0]:
            _min_gain_bar = float(_regime_exit_gain[i])
        else:
            _min_gain_bar = min_gain

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
                    if _vec_exit_to_reduce is not None:
                        _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                            trade_returns=trade_returns, ts=bar_ts, mark=mark,
                            gain=pnl_pct, reason=_trx["reason"], is_long=is_long,
                            cfg=config, TradeEvent=TradeEvent)
                        if _closed:
                            _tr_state = {}  # reset per-position state on full close
                            continue
                        # REDUCE: position still open — fall through to next exit/augment block
                    else:
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
                        state.last_augmentation_price = mark  # UAG anchor
                        state.breakout_entry = bool(_breakout_long_any[i] if is_long else _breakout_short_any[i])
                    elif _trxe["action"] == "ADD":
                        # 2026-05-22 B3: MAX_AUGMENTS cap (live=20). Mandate 2026-05-21:
                        # winners compound; losers don't pile up.
                        _max_aug = int(getattr(config, "MAX_AUGMENTS_PER_POSITION", 20))
                        # 2026-05-26 BATCH 3 UAG: mirror live ez_manage.py:23956
                        _uag_block_trx = False
                        if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", False))
                                and state.qty > 0.0001):
                            _uag_last_px = (state.last_augmentation_price
                                if state.last_augmentation_price > 0 else state.entry_price)
                            if _uag_last_px > 0:
                                _uag_gain_since = (
                                    (mark - _uag_last_px) / _uag_last_px * 100.0
                                    if is_long
                                    else (_uag_last_px - mark) / _uag_last_px * 100.0
                                )
                                _uag_min_gain = float(getattr(config, "MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0))
                                if _uag_gain_since < _uag_min_gain:
                                    _uag_block_trx = True
                        # 2026-05-26 BATCH 4 — AUG_COOLDOWN_S time-axis sibling.
                        # Mirrors live _recent_augments Redis floor 60-300s.
                        # Independent of UAG (gain gate); both must pass.
                        _aug_cd_s_trx = float(getattr(config, "AUG_COOLDOWN_S", 0.0))
                        _aug_cd_block_trx = (
                            _aug_cd_s_trx > 0.0
                            and state.last_augment_ts > 0.0
                            and (bar_ts - state.last_augment_ts) < _aug_cd_s_trx
                        )
                        if state.augmented_count >= _max_aug or _uag_block_trx or _aug_cd_block_trx:
                            pass  # skip augment — cap reached or UAG/AUG_CD-blocked
                        else:
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
                            state.last_augmentation_price = mark
            continue  # TR_TREND_v1 handles this bar fully — skip all legacy logic

        # ─── PHASE E 2026-05-19 MTF protocol short-circuit ───────────────────
        # When MTF_ARMED_ENTRY_ENABLED=True, ALL legacy entries are disabled.
        # Only MTF triggers open positions:
        #   - SMALL on (ARMED+WT-in-BB) or 1h/15m direct breakout
        #   - BIG ADD on HH-validated bounce on configured TF
        # SLOWDOWN immediate-sell fires after SMALL if no BIG ADD yet.
        # Standard frozen-stop / pattern exits still apply after position open.
        if _mtf_active and check_mtf_small_entry is not None:
            # If position open: check slowdown + big-add
            if state.qty > 0.0001:
                # Compute gain inline (legacy block does it later)
                _mtf_gain = _gain_pct(state.entry_price, mark, is_long)
                if _mtf_gain > state.max_gain:
                    state.max_gain = _mtf_gain
                # ─── PHASE I compound exit (takes precedence over slowdown) ─
                if _mtf_compound_active and check_mtf_compound_exit is not None:
                    _ce_fire, _ce_reason, _new_trail, _new_ever_dc = check_mtf_compound_exit(
                        _compound_arrays, i, mark, _mtf_gain, state.entry_price,
                        is_long, state._mtf_atr_trail, state._mtf_ever_outside_dc, config,
                    )
                    state._mtf_atr_trail = _new_trail
                    state._mtf_ever_outside_dc = _new_ever_dc
                    if _ce_fire:
                        pnl_pct = _mtf_gain
                        if _vec_exit_to_reduce is not None:
                            _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                                trade_returns=trade_returns, ts=bar_ts, mark=mark,
                                gain=pnl_pct, reason=_ce_reason, is_long=is_long,
                                cfg=config, TradeEvent=TradeEvent)
                            if _closed:
                                state._mtf_prior_bounce_price = 0.0; state._mtf_stall_count = 0
                                state._mtf_max_k_seen = 0.0; state._mtf_big_added = False
                                state._mtf_atr_trail = 0.0; state._mtf_ever_outside_dc = False
                                continue
                            # REDUCE: position still open — fall through
                        else:
                            ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                                value=state.qty * mark, reason=_ce_reason, pnl_pct=pnl_pct)
                            events.append(ev); trade_returns.append(pnl_pct)
                            state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                            state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                            state.last_reduce_ts = bar_ts; state.hedge_active = False
                            state._mtf_prior_bounce_price = 0.0; state._mtf_stall_count = 0
                            state._mtf_max_k_seen = 0.0; state._mtf_big_added = False
                            state._mtf_atr_trail = 0.0; state._mtf_ever_outside_dc = False
                            continue
                # Phase H2 — TF-selectable slowdown trigger inputs
                _slow_tf = str(getattr(config, "MTF_SLOWDOWN_TF", "3m"))
                _k3 = float(npz.get(f"k_{_slow_tf}", np.zeros(n))[i]) if f"k_{_slow_tf}" in npz else 0.0
                _wt1_3 = float(npz.get(f"wt1_{_slow_tf}", np.zeros(n))[i]) if f"wt1_{_slow_tf}" in npz else 0.0
                _wt2_3 = float(npz.get(f"wt2_{_slow_tf}", np.zeros(n))[i]) if f"wt2_{_slow_tf}" in npz else 0.0
                _o3 = float(npz.get(f"open_{_slow_tf}", np.zeros(n))[i]) if f"open_{_slow_tf}" in npz else 0.0
                _c3 = float(npz.get(f"close_{_slow_tf}", np.zeros(n))[i]) if f"close_{_slow_tf}" in npz else mark
                _slow_fire, _slow_reason, _new_stall, _new_max_k = check_mtf_slowdown(
                    gain=_mtf_gain, gain_prev=0.0, stall_count=state._mtf_stall_count,
                    max_k_seen=state._mtf_max_k_seen, k_3m=_k3, close=_c3, open_=_o3,
                    wt1_3m=_wt1_3, wt2_3m=_wt2_3, big_added=state._mtf_big_added, config=config,
                    max_gain=state.max_gain, age_s=(bar_ts - state.opened_at),
                )
                state._mtf_stall_count = _new_stall
                state._mtf_max_k_seen = _new_max_k
                if _slow_fire:
                    pnl_pct = _mtf_gain
                    if _vec_exit_to_reduce is not None:
                        _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                            trade_returns=trade_returns, ts=bar_ts, mark=mark,
                            gain=pnl_pct, reason=_slow_reason, is_long=is_long,
                            cfg=config, TradeEvent=TradeEvent)
                        if _closed:
                            state._mtf_prior_bounce_price = 0.0; state._mtf_stall_count = 0
                            state._mtf_max_k_seen = 0.0; state._mtf_big_added = False
                            continue
                        # REDUCE: position still open — fall through
                    else:
                        ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                            value=state.qty * mark, reason=_slow_reason, pnl_pct=pnl_pct)
                        events.append(ev); trade_returns.append(pnl_pct)
                        state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                        state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                        state.last_reduce_ts = bar_ts; state.hedge_active = False
                        state._mtf_prior_bounce_price = 0.0; state._mtf_stall_count = 0
                        state._mtf_max_k_seen = 0.0; state._mtf_big_added = False
                        continue
                # BIG ADD check (only once per position)
                if not state._mtf_big_added and check_mtf_big_add is not None:
                    _big_fire, _big_reason, _new_prior = check_mtf_big_add(
                        _mtf_arrays, i, mark, is_long, state._mtf_prior_bounce_price, config,
                    )
                    state._mtf_prior_bounce_price = _new_prior
                    if _big_fire:
                        # 2026-05-22 B3: MAX_AUGMENTS cap
                        _max_aug = int(getattr(config, "MAX_AUGMENTS_PER_POSITION", 20))
                        # 2026-05-26 BATCH 3 UAG: mirror live ez_manage.py:23956
                        _uag_block_mtf = False
                        if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", False))
                                and state.qty > 0.0001):
                            _uag_last_px = (state.last_augmentation_price
                                if state.last_augmentation_price > 0 else state.entry_price)
                            if _uag_last_px > 0:
                                _uag_gain_since = (
                                    (mark - _uag_last_px) / _uag_last_px * 100.0
                                    if is_long
                                    else (_uag_last_px - mark) / _uag_last_px * 100.0
                                )
                                _uag_min_gain = float(getattr(config, "MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0))
                                if _uag_gain_since < _uag_min_gain:
                                    _uag_block_mtf = True
                        # 2026-05-26 BATCH 4 — AUG_COOLDOWN_S time-axis sibling.
                        _aug_cd_s_mtf = float(getattr(config, "AUG_COOLDOWN_S", 0.0))
                        _aug_cd_block_mtf = (
                            _aug_cd_s_mtf > 0.0
                            and state.last_augment_ts > 0.0
                            and (bar_ts - state.last_augment_ts) < _aug_cd_s_mtf
                        )
                        if state.augmented_count >= _max_aug or _uag_block_mtf or _aug_cd_block_mtf:
                            pass  # cap reached or UAG/AUG_CD-blocked
                        else:
                            add_qty = state.qty * (_mtf_big_mult - 1.0)
                            if add_qty > 0:
                                denom = state.qty + add_qty
                                new_entry = (state.qty * state.entry_price + add_qty * mark) / denom if denom > 0 else mark
                                ev = TradeEvent(ts=bar_ts, type="AUGMENT", qty=add_qty, price=mark,
                                    value=add_qty * mark, reason=_big_reason)
                                events.append(ev)
                                state.qty = denom; state.entry_price = new_entry
                                state.augmented_count += 1; state.last_augment_ts = bar_ts
                                state.last_augmentation_price = mark
                                state._mtf_big_added = True
                continue  # MTF holds position — skip legacy logic
            # No position: check for SMALL entry (gated by GR filter)
            _small_fire, _small_reason = check_mtf_small_entry(_mtf_arrays, i, config)
            if _small_fire and _mtf_require_gr and not _gr_filter_mask[i]:
                _small_fire = False  # blocked by GR filter
            if _small_fire:
                new_qty = float(config.START_POSITION_SIZE) * _mtf_small_frac / max(mark, 1e-9)
                ev = TradeEvent(ts=bar_ts, type="OPEN", qty=new_qty, price=mark,
                    value=new_qty * mark, reason=_small_reason)
                events.append(ev)
                state.qty = new_qty; state.entry_price = mark; state.initial_qty = new_qty
                state.opened_at = bar_ts; state.augmented_count = 0; state.max_gain = 0.0
                state.last_open_attempt_ts = bar_ts; state.last_augment_ts = bar_ts
                state.last_augmentation_price = mark  # UAG anchor
                state._mtf_prior_bounce_price = 0.0; state._mtf_stall_count = 0
                state._mtf_max_k_seen = 0.0; state._mtf_big_added = False
                state.breakout_entry = bool(_breakout_long_any[i] if is_long else _breakout_short_any[i])
            continue  # MTF active — skip all legacy entry logic

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
            # 2026-05-26 first-OPEN throttle (VEC_FIRST_OPEN_THROTTLE_BARS=0 → no-op)
            if _vec_first_open_throttled is not None and _vec_first_open_throttled(i, state, config):
                continue
            # A1 SPY-regime gate: block side per gate config (default OFF for both sides)
            if config.SPY_REGIME_GATE_ENABLED:
                if is_long and not bool(_spy_long_ok[i]):
                    continue
                if (not is_long) and not bool(_spy_short_ok[i]):
                    continue
            # A4 Daily-decision-TF gate: only evaluate OPEN on last bar of trading day
            if str(config.DECISION_TF_MODE).upper() == "DAILY" and not bool(_daily_decision_mask[i]):
                continue
            # DEAD-KNOB REWIRE 2026-05-18: GR_HTF_GATE — block when alignment short.
            # Mirrors backtest_v8_engine.py:6062 ("BLOCKED_GR_HTF_BULL/BEAR_…").
            if not bool(_gr_htf_gate_open_ok[i]):
                continue
            # DEAD-KNOB REWIRE 2026-05-18: MFI_ENTRY — block LONG/SHORT by mfi_D.
            # Mirrors backtest_v8_engine.py:6077 ("BLOCKED_MFI_ENTRY_D_…gt…").
            if not bool(_mfi_entry_open_ok[i]):
                continue
            # CATALYST_VOLUME_GATE — block OPEN unless vol breakout + DC-D break.
            if not bool(_catalyst_open_ok[i]):
                continue
            # PENNY_STOCK_LONG_BLOCK — block LONG on stocks < $N.
            if not bool(_penny_open_ok[i]):
                continue
            # DC_BREAK_LOW_REQUIRE_HTF — bare short needs ≥N HTF bear.
            if not bool(_dc_break_htf_ok[i]):
                continue
            # HTF_DIRECTION_GATE — block OPEN when D-WT against intended side.
            if not bool(_htf_dir_open_ok[i]):
                continue
            # HTF_TREND_VETO (2026-05-18 REWIRE) — daily WT trend gate.
            if not bool(_htf_trend_veto_ok[i]):
                continue
            # BB_PULLBACK_GATE (2026-05-23) — block entries when BB %B unfavorable.
            if not bool(_bb_pullback_ok[i]):
                continue
            # OPEN gate: WT_3M direction + reentry-fire OR force-open OR GOLDEN_RULE
            fire_block = bool(reentry["fire"][i])
            wt_open_ok = bool(wt_3m_aligned[i])
            # WT_DC_HTF_GATE — block wt_open_ok when HTF WT is against the trade.
            # Mirrors tradier_manage:2751-2762. Does NOT block GR/DELTA/B15/etc.
            if wt_open_ok and _wt_dc_htf_gate_block[i]:
                wt_open_ok = False
            # GOLDEN_RULE entry check (third trigger when flat)
            _gr_result = None
            if check_golden_rule_enforce is not None and config.GOLDEN_RULE_ENABLED:
                _gr_cooldown_ok = (bar_ts - state.gr_last_fire_ts) >= float(config.GOLDEN_RULE_COOLDOWN_S)
                _gr_htf_ok = (_gr_htf_entry_mask is None) or bool(_gr_htf_entry_mask[i])
                # 2026-05-26 BATCH 4 — GR_OPEN_COOLDOWN_S gate. Mirrors live's
                # _recent_opens Redis floor (ez_manage.py:395, 900s default).
                # Default 0.0 = inert (Arm A bit-exact preserved).
                _gr_open_cd_s = float(getattr(config, "GR_OPEN_COOLDOWN_S", 0.0))
                _gr_open_cd_ok = True
                if _gr_open_cd_s > 0.0 and state.gr_last_open_ts > 0.0:
                    _gr_open_cd_ok = (bar_ts - state.gr_last_open_ts) >= _gr_open_cd_s
                if _gr_cooldown_ok and _gr_htf_ok and _gr_open_cd_ok:
                    _gr_result = check_golden_rule_enforce(_store, i, symbol, side, _pos, config, mode)
            # DELTA_ENGINE entry check (fourth trigger when flat)
            _delta_result = None
            if check_delta_entry is not None and config.DELTA_ENGINE_ENABLED and config.DELTA_ENTRY_ENABLED:
                _delta_result = check_delta_entry(_store, i, side, mode, config)
            # WT_15M_BOUNCE_OPEN — fifth trigger (precomputed mask, O(1) per bar)
            _b15_ok = bool(_b15_open_mask[i])
            # B2 Connors RSI-2 overlay — sixth trigger (long-only)
            _connors_ok = bool(_connors_open_mask[i])
            # BREAKOUT_RETEST_ARMED (2026-05-18 REWIRE) — seventh trigger (Rule A).
            # Mirrors ez_manage.py:35464+. Stateless simplified form.
            _ra_ok = bool(_ra_fire_mask[i])
            # BB_BREAKOUT + BB_RSI_STOCH — eighth + ninth triggers (2026-05-22)
            _bb_break_ok = bool(_bb_break_mask[i])
            _brs_ok = bool(_brs_mask[i])
            # 2026-05-26 BTC_DEDICATED — vectorised btc_loop.should_enter_btc_long/short
            # + detect_btc_breakout. Only fires for BTC syms; mask is all-False otherwise.
            _btc_ok = bool(_btc_entry_mask[i]) or bool(_btc_breakout_mask[i])
            # 2026-05-26 RZ cluster — RZ_BREAKOUT + RZ_CASCADE entry triggers
            _rz_break_ok = bool(_rz_break_mask[i])
            _rz_cascade_ok = bool(_rz_cascade_entry[i])
            # 2026-05-26 MOMENTUM_BREAKOUT — fires the bar of the breakout (USER MANDATE).
            _mom_break_ok = bool(_mom_break_long_mask[i]) if is_long else bool(_mom_break_short_mask[i])
            # 2026-05-22 QUALITY_BOTTOM_ENTRY — REAL bottom/top detector (USER mandate)
            _qb_ok = bool(_qb_fire_mask[i])
            # Daily-cap on quality entries (~1-2/day target)
            _qb_max_per_day = int(getattr(config, "QUALITY_BOTTOM_MAX_PER_DAY", 3))
            _today_utc = int(bar_ts // 86400)
            if state.quality_last_day_utc != _today_utc:
                state.quality_last_day_utc = _today_utc
                state.quality_entries_today = 0
            if _qb_ok and state.quality_entries_today >= _qb_max_per_day:
                _qb_ok = False  # daily cap reached
            # When DISABLE_SCATTERGUN: quality gate is the ONLY trigger; suppress all others.
            if bool(getattr(config, "QUALITY_BOTTOM_DISABLE_SCATTERGUN", True)) and bool(getattr(config, "QUALITY_BOTTOM_ENTRY_ENABLED", True)):
                if not _qb_ok:
                    continue
                # quality entry fires — count it
                state.quality_entries_today += 1
            else:
                if not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok or _connors_ok or _ra_ok or _qb_ok or _bb_break_ok or _brs_ok or _btc_ok or _rz_break_ok or _rz_cascade_ok or _mom_break_ok):
                    continue
                if _qb_ok:
                    state.quality_entries_today += 1
            # STDEV_MACRO_ENTRY_VETO — block OPEN at macro extreme on same side.
            # Additive to existing entry triggers (BB-breakout etc.) — never silently
            # overrides them; refuses the entry with an explicit reason. Default OFF.
            if bool(getattr(config, "STDEV_MACRO_ENTRY_VETO_ENABLED", False)):
                try:
                    import stdev_macro as _sm_open
                    _sm_ind = {
                        "bb_pct_b_D": float(npz["bb_pct_b_D"][i]) if "bb_pct_b_D" in npz else 0.5,
                        "bb_pct_b_4h": float(npz["bb_pct_b_4h"][i]) if "bb_pct_b_4h" in npz else 0.5,
                        "bb_pct_b_1h": float(npz["bb_pct_b_1h"][i]) if "bb_pct_b_1h" in npz else 0.5,
                    }
                    _sm_state_open = _sm_open.compute_stdev_macro_state(_sm_ind, config_obj=config)
                    _sm_side_open = "LONG" if is_long else "SHORT"
                    _sm_block, _sm_why = _sm_open.entry_veto(_sm_side_open, _sm_state_open, config)
                    if _sm_block:
                        # Reason recorded indirectly via the gate (returns reason string); to surface in
                        # ledger debug, we can stash a counter on _pos if needed. For now: skip cleanly.
                        continue
                except Exception:
                    pass
            # LIVE_ENTRY_ENGINE vote (2026-05-20 PORT) — 4-engine score aggregator
            # mirroring ez_manage.py:32543 / tradier_manage.py:2657. Default OFF.
            # When master flag is True: an OPEN that passed the trigger cascade above
            # must additionally clear `final_score = boost*score_max + base_score`
            # against the configured threshold. base_score is 0 in vec (full
            # wt_dc_entry_scorer port out of scope); see vec_paths/live_entry_engine.py.
            if live_entry_engine_passes_vec is not None and bool(getattr(config, "LIVE_ENTRY_ENGINE_ENABLED", False)):
                _ee_passes, _ee_final, _ee_reasons = live_entry_engine_passes_vec(
                    npz, i, "LONG" if is_long else "SHORT",
                    base_score=0.0, cfg=config, symbol=symbol, mode=mode,
                )
                if not _ee_passes:
                    continue
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
            # 2026-05-22 TOP_OF_RANGE_BLOCK — skip entries when price is at extreme
            # range position on all listed TFs (USER ORDI-prevention mandate).
            if (is_long and _tor_block_long[i]) or ((not is_long) and _tor_block_short[i]):
                continue
            if _ra_ok and not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok or _connors_ok):
                reason = f"RULE_A_RETEST_{'LONG' if is_long else 'SHORT'}_px{mark:.6f}"
            elif _connors_ok and not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok):
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
            elif _btc_ok and not (wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok or _connors_ok or _ra_ok or _bb_break_ok or _brs_ok or _qb_ok):
                # 2026-05-26 BTC_DEDICATED — primary entry or breakout (matches live btc_loop reasons)
                if bool(_btc_breakout_mask[i]):
                    reason = f"BTC_LOOP_ENTRY_{'LONG' if is_long else 'SHORT'}_BREAKOUT"
                else:
                    reason = f"BTC_LOOP_ENTRY_{'LONG' if is_long else 'SHORT'}_PRIMARY"
            elif _mom_break_ok:
                # 2026-05-26 MOMENTUM_BREAKOUT — at-the-breakout entry (USER mandate: fix 4-16hr lag)
                _tf = str(getattr(config, "MOMENTUM_BREAKOUT_TF", "1h"))
                _prev_key = f"dc_high_{_tf}" if is_long else f"dc_low_{_tf}"
                _prev_arr = npz.get(_prev_key)
                _prev_v = float(_prev_arr[i-1]) if (_prev_arr is not None and i > 0) else 0.0
                reason = f"MOMENTUM_BREAKOUT_{'LONG' if is_long else 'SHORT'}_{_tf}_px{mark:.4f}_dc{_prev_v:.4f}"
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
            state.last_augmentation_price = mark  # UAG anchor
            # 2026-05-22 SURGICAL NLK — tag entry as breakout-bypassed if either
            # breakout mask fires at this bar for this side. Used by surgical NLK
            # to only close newborn losers that were breakout entries.
            state.breakout_entry = bool(_breakout_long_any[i] if is_long else _breakout_short_any[i])
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
                # 2026-05-26 BATCH 4 — stamp GR_OPEN_COOLDOWN anchor (sym-side
                # persistent; survives close/reopen). Mirrors live _recent_opens
                # Redis key set on GR open at ez_manage.py:22313 / 23670.
                state.gr_last_open_ts = bar_ts
            # Record DC stop price at entry time
            if config.DC_LOW4_STOP_ENABLED:
                _s = float(_dc4_stop_long[i] if is_long else _dc4_stop_short[i])
                state.r1_stop_price = _s if _s > 0 else 0.0
            elif config.DC_LOW_STOP_ENABLED:
                _s = float(_dc1_stop_long[i] if is_long else _dc1_stop_short[i])
                state.r1_stop_price = _s if _s > 0 else 0.0
            else:
                state.r1_stop_price = 0.0
            # Record frozen stop prices at entry time
            if bool(getattr(config, "DC_LOW_FROZEN_STOP_ENABLED", False)):
                state._dc_fstop_entry = float(_dc_fstop_arr[i]) if float(_dc_fstop_arr[i]) > 0 else 0.0
            else:
                state._dc_fstop_entry = 0.0
            if bool(getattr(config, "BB_FROZEN_STOP_ENABLED", False)):
                state._bb_fstop_entry = float(_bb_fstop_arr[i]) if float(_bb_fstop_arr[i]) > 0 else 0.0
            else:
                state._bb_fstop_entry = 0.0
            state._ever_outside_channel = False
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
            # 2026-05-26 BATCH 3 — PPL_FIRE_COOLDOWN_S gate (mirrors live's
            # _recent_ppl_fires Redis key ~24h). state.ppl_last_fire_ts lives
            # on SymState so it survives full-close + reopen cycles (where
            # _pos.ppl_fired would reset). Default 0.0 = inert.
            _ppl_cd_s = float(getattr(config, "PPL_FIRE_COOLDOWN_S", 0.0))
            _ppl_cd_ok = True
            if _ppl_cd_s > 0.0 and state.ppl_last_fire_ts > 0.0:
                _ppl_cd_ok = (bar_ts - state.ppl_last_fire_ts) >= _ppl_cd_s
            if _ppl1 is not None and _ppl_cd_ok:
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
                    state.ppl_last_fire_ts = bar_ts
            elif check_ppl_step2 is not None and _pos.ppl_fired and not _pos.ppl_stop_upgraded:
                _ppl2 = check_ppl_step2(_store, i, _pos, config)
                if _ppl2 is not None:
                    _pos.ppl_stop_level = float(_ppl2.get("new_stop", _pos.ppl_stop_level))
                    _pos.ppl_stop_upgraded = True
            if check_ppl_step3 is not None and _pos.ppl_fired and state.qty > 0.0001:
                _ppl3 = check_ppl_step3(_store, i, _pos, config)
                if _ppl3 is not None:
                    pnl_pct = gain
                    if _vec_exit_to_reduce is not None:
                        _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                            trade_returns=trade_returns, ts=bar_ts, mark=mark,
                            gain=pnl_pct, reason=_ppl3["reason"], is_long=is_long,
                            cfg=config, TradeEvent=TradeEvent)
                        if _closed:
                            continue
                        # REDUCE: position still open — fall through
                    else:
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

        # ─── QUALITY_TOP_EXIT (2026-05-23 USER MANDATE "in at bottom, out at top") ────
        # Symmetric partner to QUALITY_BOTTOM_ENTRY. Closes full position when the
        # quality-top mask fires (LONG: top-short criteria; SHORT: bottom-long). Optional
        # gain floor via QUALITY_TOP_EXIT_MIN_GAIN_PCT suppresses early closes on noise.
        # Default OFF; opt-in via QUALITY_TOP_EXIT_ENABLED.
        if state.qty > 0.0001 and _qt_exit_mask[i]:
            _qt_min_gain = float(getattr(config, "QUALITY_TOP_EXIT_MIN_GAIN_PCT", 0.0))
            if gain >= _qt_min_gain:
                pnl_pct = gain
                _reason = f"QUALITY_TOP_EXIT_g{gain:.2f}%"
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_reason, pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                    _pos.gain_pct = 0.0
                    continue

        # ─── BTC_DEDICATED exit gate (2026-05-26 vectorised btc_loop.should_exit_btc) ──
        # Fires on WT-against count >= BTC_TECH_EXIT_WT_MIN_TFS, OR accel-reversal,
        # OR divergence-against. Only active when BTC_DEDICATED_ENABLED for BTC syms.
        if state.qty > 0.0001 and bool(_btc_exit_mask[i]):
            pnl_pct = gain
            _reason = f"BTC_LOOP_EXIT_{'LONG' if is_long else 'SHORT'}_TECH"
            if _vec_exit_to_reduce is not None:
                _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                    trade_returns=trade_returns, ts=bar_ts, mark=mark,
                    gain=pnl_pct, reason=_reason, is_long=is_long,
                    cfg=config, TradeEvent=TradeEvent)
                if _closed:
                    continue
                # REDUCE: position still open — fall through
            else:
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_reason,
                    pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                _pos.gain_pct = 0.0
                continue

        # ─── RZ EXIT gates (2026-05-26) ─────────────────────────────────────
        # EXIT_SCORER N-of-5 (wt_dc_exit_scorer.py) AND/OR RZ_CASCADE multi-TF
        # reverse. Both gated by their own master flags; mask is all-False
        # otherwise, so this block is a no-op for existing sweeps.
        if state.qty > 0.0001 and (bool(_rz_scorer_exit[i]) or bool(_rz_cascade_exit[i])):
            pnl_pct = gain
            _rz_reason_parts = []
            if bool(_rz_scorer_exit[i]):
                _rz_reason_parts.append("EXIT_SCORER_STRICT")
            if bool(_rz_cascade_exit[i]):
                _rz_reason_parts.append("RZ_CASCADE_REV")
            _rz_reason = "_".join(_rz_reason_parts) + f"_g{gain:.2f}%"
            if _vec_exit_to_reduce is not None:
                _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                    trade_returns=trade_returns, ts=bar_ts, mark=mark,
                    gain=pnl_pct, reason=_rz_reason, is_long=is_long,
                    cfg=config, TradeEvent=TradeEvent)
                if _closed:
                    continue
                # REDUCE: position still open — fall through
            else:
                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_rz_reason, pnl_pct=pnl_pct)
                events.append(ev)
                trade_returns.append(pnl_pct)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                _pos.gain_pct = 0.0
                continue

        # ─── R1 EMERGENCY EXIT (monitoring-based — matches live R1_DC_LOW4_3M_EMERGENCY) ─
        if check_r1_emergency_exit is not None and state.qty > 0.0001:
            _r1 = check_r1_emergency_exit(_store, i, _pos, mode, config)
            if _r1 is not None:
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_r1["reason"], is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
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

        # ─── MICRO_SCALP_USDC_CLOSE (USER 2026-05-22 wire-in — matches ez_manage:40000+) ───
        # Fires when gain >= threshold AND gain < prev_gain (first decel past threshold).
        # USDC syms only (crypto mode + symbol.endswith("USDC")); stocks use the parallel
        # MICRO_SCALP_STOCKS_MAKER_ENABLED knob (not wired here yet). Bypasses noloss gates
        # in live (only fires on positive gain), so close direct.
        # ROLLBACK: set MICRO_SCALP_USDC_MAKER_ENABLED=False (config.py:606) — the gate goes idle.
        if (
            exit_id == EXIT_NONE
            and state.qty > 0.0001
            and mode == "crypto"
            and symbol.endswith("USDC")
            and bool(getattr(config, "MICRO_SCALP_USDC_MAKER_ENABLED", False))
        ):
            _ms_thr = float(getattr(config, "MICRO_SCALP_GAIN_THRESHOLD_PCT", 0.02))
            if gain >= _ms_thr and gain < state.prev_gain:
                pnl_pct = gain
                _ms_reason = f"MICRO_SCALP_USDC_CLOSE_g{gain:.3f}%_prev{state.prev_gain:.3f}%"
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_ms_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        state.prev_gain = 0.0
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_ms_reason, pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                    state.prev_gain = 0.0
                    _pos.gain_pct = 0.0
                    continue

        # ─── NEWBORN_LOSS_KILL (USER 2026-05-21 post-ORDI mandate) ───────────────
        # Closes newborn position the moment gain drops below threshold.
        # Tighter than R1; doesn't require DC4 breach. Bypasses NO_LOSS.
        if check_newborn_loss_kill_exit is not None and state.qty > 0.0001:
            _nlk = check_newborn_loss_kill_exit(_store, i, _pos, mode, config)
            if _nlk is not None:
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_nlk["reason"], is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_nlk["reason"], pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                    _pos.gain_pct = 0.0
                    continue

        # R3_HTF_FLIP (2026-05-18 REWIRE) — Daily + 4h structural close.
        # Mirrors ez_manage.py:38260+. Reason in LOSS_EXIT_TECHNICAL_BYPASS so
        # close fires even at a loss. Daily tier always evaluated when ENABLED;
        # 4h tier evaluated only when _4H_TIER_ENABLED. continue → new bar.
        if state.qty > 0.0001 and bool(getattr(config, "R3_HTF_FLIP_EXIT_ENABLED", False)):
            _r3_fire = False
            _r3_tier = ""
            if bool(_r3hf_daily_fire[i]):
                _r3_fire = True
                _r3_tier = "DAILY"
            elif bool(getattr(config, "R3_HTF_FLIP_4H_TIER_ENABLED", False)) and bool(_r3hf_4h_fire[i]):
                _r3_fire = True
                _r3_tier = "4H"
            if _r3_fire:
                pnl_pct = gain
                _r3_reason = f"R3_HTF_FLIP{'_4H' if _r3_tier == '4H' else ''}_{_r3_tier}_px{mark:.6f}"
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_r3_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(
                        ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_r3_reason, pnl_pct=pnl_pct,
                    )
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
                _reason = f"DC_STOP_px{mark:.4f}_stop{state.r1_stop_price:.4f}"
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(
                        ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark,
                        reason=_reason,
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

        # DC_LOW / BB FROZEN STOP — price recorded at entry, exit when breached
        if state.qty > 0.0001:
            _fs_hit = False
            _fs_reason = ""
            if bool(getattr(config, "DC_LOW_FROZEN_STOP_ENABLED", False)) and hasattr(state, '_dc_fstop_entry') and state._dc_fstop_entry > 0:
                if (is_long and mark <= state._dc_fstop_entry) or (not is_long and mark >= state._dc_fstop_entry):
                    _fs_loss = _gain_pct(state.entry_price, mark, is_long)
                    if _fstop_floor <= -999.0 or _fs_loss >= _fstop_floor:
                        _fs_hit = True
                        _fs_reason = f"DC_FROZEN_STOP_{config.DC_LOW_FROZEN_STOP_TF}_px{mark:.4f}_stop{state._dc_fstop_entry:.4f}"
            if not _fs_hit and bool(getattr(config, "BB_FROZEN_STOP_ENABLED", False)) and hasattr(state, '_bb_fstop_entry') and state._bb_fstop_entry > 0:
                if (is_long and mark <= state._bb_fstop_entry) or (not is_long and mark >= state._bb_fstop_entry):
                    _fs_loss = _gain_pct(state.entry_price, mark, is_long)
                    if _fstop_floor <= -999.0 or _fs_loss >= _fstop_floor:
                        _fs_hit = True
                        _fs_reason = f"BB_FROZEN_STOP_{config.BB_FROZEN_STOP_TF}_px{mark:.4f}_stop{state._bb_fstop_entry:.4f}"
            if _fs_hit:
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_fs_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_fs_reason, pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                    continue

        # ─── PHASE B 2026-05-19 candle-pattern stops (PSTOP_*) ─────────────────
        # LH / LL / IB / PULLBACK / BB_TAG_FAIL — all configurable per TF.
        # By default fires only when underwater (PATTERN_STOPS_LOSS_ONLY=True).
        if _pattern_stop_active and check_pattern_stops_at_bar is not None and state.qty > 0.0001:
            _ps_eligible = (gain < 0) if _pattern_stops_loss_only else True
            if _ps_eligible:
                _ps_fire, _ps_reason = check_pattern_stops_at_bar(_pattern_stop_arrays, i, config)
                if _ps_fire:
                    pnl_pct = gain
                    if _vec_exit_to_reduce is not None:
                        _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                            trade_returns=trade_returns, ts=bar_ts, mark=mark,
                            gain=pnl_pct, reason=_ps_reason, is_long=is_long,
                            cfg=config, TradeEvent=TradeEvent)
                        if _closed:
                            state._ever_outside_channel = False
                            continue
                        # REDUCE: position still open — fall through
                    else:
                        ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                            value=state.qty * mark, reason=_ps_reason, pnl_pct=pnl_pct)
                        events.append(ev)
                        trade_returns.append(pnl_pct)
                        state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                        state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                        state.last_reduce_ts = bar_ts; state.hedge_active = False
                        state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                        state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                        state._ever_outside_channel = False
                        continue

        # ─── PHASE C 2026-05-19 USER MANDATE tight breakout stops ──────────────
        # NEVER_GO_RED: closes if gain crosses below 0 after max_gain >= peak threshold.
        # CHANNEL_REENTRY: closes if price was outside channel and re-entered.
        # ─── PHASE D 2026-05-19 USER MANDATE smart exhaustion exit ─────────────
        # EXH: (3m LTF rejection OR basis cross) AND k_15m extreme [AND HTF near]
        if _exh_active and check_exh_exit_at_bar is not None and state.qty > 0.0001:
            _prev_mark = float(close[i-1]) if i > 0 else mark
            _exh_fire, _exh_reason, _new_ever_exh = check_exh_exit_at_bar(
                _exh_arrays, i, mark, _prev_mark, is_long, state._ever_outside_channel, config,
            )
            state._ever_outside_channel = _new_ever_exh
            if _exh_fire:
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_exh_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        state._ever_outside_channel = False
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_exh_reason, pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                    state._ever_outside_channel = False
                    continue
        if (_ngr_active or _chre_active) and state.qty > 0.0001:
            _tb_fire = False
            _tb_reason = ""
            if _ngr_active and check_never_go_red is not None:
                _tb_fire, _tb_reason = check_never_go_red(gain, state.max_gain, config)
            if not _tb_fire and _chre_active and check_channel_reentry is not None:
                _ch_u = float(_chre_upper_arr[i])
                _ch_l = float(_chre_lower_arr[i])
                _tb_fire, _tb_reason, _new_ever = check_channel_reentry(
                    mark, is_long, state._ever_outside_channel, _ch_u, _ch_l, config,
                )
                state._ever_outside_channel = _new_ever
            if _tb_fire:
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_tb_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        state._ever_outside_channel = False
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_tb_reason, pnl_pct=pnl_pct)
                    events.append(ev)
                    trade_returns.append(pnl_pct)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts; state.hedge_active = False
                    state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                    state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                    state._ever_outside_channel = False
                    continue

        # GR multiplier exit: against-score >= threshold AND wt1_3m against
        # 2026-05-17 MIN_GAIN_EXIT_GATE: non-emergency, requires gain >= MIN_GAIN.
        if exit_id == EXIT_NONE and config.GR_EXIT_ENABLED and _gr_exit_passes is not None and gain >= _min_gain_bar:
            if bool(_wt3m_against[i]) and bool(_gr_exit_passes[i]):
                exit_id = 99
                exit_reason = f"GR_EXIT_{config.GR_EXIT_MIN_TFS}tf_x_{config.GR_EXIT_MIN_IND}ind"

        # WT_4H_VEL_EXIT (needs profit + age; vec gave us full mask)
        # 2026-05-17 MIN_GAIN_EXIT_GATE: was `gain >= comm_buf` (0.10%) which closed
        # at micro-gains, dragging avg_gain_trade to 0.37%. Now requires real gain
        # (>= config.MIN_GAIN = 3.0%) before this non-emergency exit can fire.
        if exit_id == EXIT_NONE and exit_gates["wt_4h_vel_full"][i] and age_s > 360 and gain >= _min_gain_bar:
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
                    if gain >= _min_gain_bar or gain < comm_buf:
                        exit_id = EXIT_DC_HOPELESS
                        exit_reason = f"DC_HOPELESS_entry={state.entry_price:.4f}"

        # 2026-05-17 MIN_GAIN_EXIT_GATE: was `gain > 0` (allowed close at +0.1%);
        # now requires gain >= MIN_GAIN to avoid micro-gain closes.
        if exit_id == EXIT_NONE and config.WT_EXHAUST_EXIT_ENABLED and exit_gates["wt_exhaust"][i] and age_s > grace_s:
            if gain >= _min_gain_bar:
                exit_id = EXIT_WT_EXHAUST
                exit_reason = "WT_EXHAUST"

        # 2026-05-17 MIN_GAIN_EXIT_GATE: WT_PERCENTILE / E_1_WT_DELTA / E_3_STRUCTURE
        # are non-emergency exits — gate behind gain >= MIN_GAIN to prevent
        # micro-gain closes (R1/R2/HEDGE_FAILED handle the real loss paths).
        if exit_id == EXIT_NONE and exit_gates["wt_percentile"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = EXIT_WT_PERCENTILE
            exit_reason = "WT_PERCENTILE"

        if exit_id == EXIT_NONE and exit_gates["e1_wt_delta"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = EXIT_E1_WT_DELTA
            exit_reason = "E_1_WT_DELTA"

        if exit_id == EXIT_NONE and exit_gates["e3_structure"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = EXIT_E3_STRUCTURE
            exit_reason = "E_3_STRUCTURE"

        # WT exit family (vec_paths/wt_exits.py — 2026-05-26). Sentinel exit_ids
        # mirror the GR_EXIT=99 convention. All four are non-emergency exits gated
        # behind gain >= MIN_GAIN and grace age, same as the surrounding cohort.
        if exit_id == EXIT_NONE and exit_gates["wt_div_exit"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = 110
            exit_reason = "WT_DIV_EXIT"
        if exit_id == EXIT_NONE and exit_gates["wt_accel_exit"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = 111
            exit_reason = "WT_ACCEL_EXIT"
        if exit_id == EXIT_NONE and exit_gates["wt_momentum_exit"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = 112
            exit_reason = "WT_MOMENTUM_EXIT"
        # Configurable N-TFs variant of WT_EXHAUST (used by BTC_TECH_EXIT_WT_MIN_TFS).
        # Only fires when caller explicitly sets WT_EXHAUST_EXIT_MIN_TFS > 0 (else the
        # default live-equivalent mask above at exit_gates["wt_exhaust"] handles it).
        if exit_id == EXIT_NONE and int(getattr(config, 'WT_EXHAUST_EXIT_MIN_TFS', 0)) > 0 \
                and exit_gates["wt_exhaust_cfg"][i] and age_s > grace_s and gain >= _min_gain_bar:
            exit_id = 113
            exit_reason = f"WT_EXHAUST_CFG_minTFs={int(getattr(config, 'WT_EXHAUST_EXIT_MIN_TFS', 0))}"

        # PEAK_GIVEBACK exit (gain decaying from peak — full close, bypasses noloss)
        if exit_id == EXIT_NONE and check_peak_giveback_exit is not None and state.qty > 0.0001:
            _pgb = check_peak_giveback_exit(_store, i, _pos, mode, config)
            if _pgb is not None:
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_pgb, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
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
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_beg, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
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
        if exit_id == EXIT_NONE and config.IN_GAIN_TREND_EXIT_ENABLED and gain >= max(float(config.IN_GAIN_TREND_MIN_GAIN), _min_gain_bar):
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
        # 2026-05-19 R10 Target 3: WT_CROSSUNDER_FINAL_MIN_HOLD_BARS gate suppresses
        # adjacent-bar scalp cascade (211/3344 R9 closes were single-bar exits).
        if exit_id == EXIT_NONE and check_wt_crossunder_final_exit is not None and state.qty > 0.0001:
            _wtcf_min_hold = int(getattr(config, "WT_CROSSUNDER_FINAL_MIN_HOLD_BARS", 0))
            _wtcf_hold_ok = True
            if _wtcf_min_hold > 0 and state.opened_at > 0.0:
                _wtcf_base_tf_s = 300.0 if mode == "tradier" else 180.0
                _wtcf_bars_held = (bar_ts - state.opened_at) / _wtcf_base_tf_s
                _wtcf_hold_ok = _wtcf_bars_held >= _wtcf_min_hold
            # 2026-05-26 BATCH 3 — WT_CROSSUNDER_FINAL_COOLDOWN_S gate (mirrors
            # live `_recent_reduces` Redis floor — sym-side persistent across
            # close/reopen). Default 0.0 = inert (baseline preservation).
            _wtcf_cooldown_s = float(getattr(config, "WT_CROSSUNDER_FINAL_COOLDOWN_S", 0.0))
            _wtcf_cooldown_ok = True
            if _wtcf_cooldown_s > 0.0 and state.wt_crossunder_last_fire_ts > 0.0:
                _wtcf_cooldown_ok = (bar_ts - state.wt_crossunder_last_fire_ts) >= _wtcf_cooldown_s
                # 2026-05-26 BATCH 4 — refined bypass semantics. The Batch 3
                # cooldown was too aggressive (Arm C -0.0535 vs Arm B +0.0151)
                # because valid loss-cut exits got blocked. When enabled, the
                # cooldown is bypassed if (a) last reduce was a loss (urgent
                # loss-cut — must cut bleeding) OR (b) the WT bias flipped
                # opposite vs the recorded value at last fire.
                if (not _wtcf_cooldown_ok
                        and bool(getattr(config, "WT_CROSSUNDER_REFINED_BYPASS_ENABLED", False))):
                    # (a) loss-cut bypass
                    if state.last_reduce_was_loss:
                        _wtcf_cooldown_ok = True
                    else:
                        # (b) WT-bias-flip bypass — current bias sign vs recorded
                        _wt1_3m_cur = float(npz.get("wt1_3m", np.zeros(n))[i]) if "wt1_3m" in npz else 0.0
                        _wt2_3m_cur = float(npz.get("wt2_3m", np.zeros(n))[i]) if "wt2_3m" in npz else 0.0
                        # +1 = LONG-aligned (wt1 > wt2), -1 = SHORT-aligned
                        _wt_bias_cur = 1 if _wt1_3m_cur > _wt2_3m_cur else (-1 if _wt1_3m_cur < _wt2_3m_cur else 0)
                        if (state.wt_state_last_fire != 0
                                and _wt_bias_cur != 0
                                and _wt_bias_cur != state.wt_state_last_fire):
                            _wtcf_cooldown_ok = True
            if _wtcf_hold_ok and _wtcf_cooldown_ok and (gain >= _min_gain_bar or gain < comm_buf):
                _wtcf = check_wt_crossunder_final_exit(_store, i, _pos, mode, config)
                if _wtcf is not None:
                    exit_id = 91
                    exit_reason = _wtcf["reason"]
                    state.wt_crossunder_last_fire_ts = bar_ts
                    # 2026-05-26 BATCH 4 — record WT bias sign at fire (for
                    # refined-bypass flip detection on subsequent fires).
                    _wt1_fire = float(npz.get("wt1_3m", np.zeros(n))[i]) if "wt1_3m" in npz else 0.0
                    _wt2_fire = float(npz.get("wt2_3m", np.zeros(n))[i]) if "wt2_3m" in npz else 0.0
                    state.wt_state_last_fire = 1 if _wt1_fire > _wt2_fire else (-1 if _wt1_fire < _wt2_fire else 0)

        # R2 WT VELOCITY SLOW (near-breakeven slowdown — matches live R2_WT_VEL_SLOW)
        if exit_id == EXIT_NONE and check_r2_wt_vel_slow_exit is not None and state.qty > 0.0001:
            _r2 = check_r2_wt_vel_slow_exit(_store, i, _pos, mode, config)
            if _r2 is not None:
                # R2 bypasses noloss gate — execute close directly
                pnl_pct = gain
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_r2["reason"], is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
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
                    "bb_pct_b_D": float(npz["bb_pct_b_D"][i]) if "bb_pct_b_D" in npz else 0.5,
                    "bb_pct_b_4h": float(npz["bb_pct_b_4h"][i]) if "bb_pct_b_4h" in npz else 0.5,
                    "bb_pct_b_1h": float(npz["bb_pct_b_1h"][i]) if "bb_pct_b_1h" in npz else 0.5,
                    "wt1_4h": float(npz["wt1_4h"][i]) if "wt1_4h" in npz else 0.0,
                    "wt2_4h": float(npz["wt2_4h"][i]) if "wt2_4h" in npz else 0.0,
                }
                _r4_state_vec = _sm_r4.compute_stdev_macro_state(_r4_ind_vec, config_obj=config)
                _r4_side_vec = "LONG" if is_long else "SHORT"
                _r4_close_vec, _r4_reason_vec = _sm_r4.r4_exit(_r4_side_vec, _r4_state_vec, _r4_ind_vec, config)
                if _r4_close_vec:
                    pnl_pct = gain
                    _r4_full_reason = f"{_r4_reason_vec}_pctbD={_r4_state_vec.get('bb_pct_b_D', 0.5):.2f}_pctb4h={_r4_state_vec.get('bb_pct_b_4h', 0.5):.2f}"
                    if _vec_exit_to_reduce is not None:
                        _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                            trade_returns=trade_returns, ts=bar_ts, mark=mark,
                            gain=pnl_pct, reason=_r4_full_reason, is_long=is_long,
                            cfg=config, TradeEvent=TradeEvent)
                        if _closed:
                            continue
                        # REDUCE: position still open — fall through
                    else:
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
                _hf_reason = f"HEDGE_FAILED_FALLBACK_CLOSE_g{gain:.2f}"
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                        trade_returns=trade_returns, ts=bar_ts, mark=mark,
                        gain=pnl_pct, reason=_hf_reason, is_long=is_long,
                        cfg=config, TradeEvent=TradeEvent)
                    if _closed:
                        continue
                    # REDUCE: position still open — fall through
                else:
                    ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_hf_reason,
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
            if _vec_exit_to_reduce is not None:
                _closed = _vec_exit_to_reduce(state=state, pos=_pos, events=events,
                    trade_returns=trade_returns, ts=bar_ts, mark=mark,
                    gain=pnl_pct, reason=reason_str, is_long=is_long,
                    cfg=config, TradeEvent=TradeEvent)
                if _closed:
                    continue
                # REDUCE: position still open — fall through
            else:
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
            # 2026-05-22 B3: MAX_AUGMENTS cap
            _max_aug = int(getattr(config, "MAX_AUGMENTS_PER_POSITION", 20))
            if state.augmented_count >= _max_aug:
                continue
            # 2026-05-26 BATCH 3 UAG: mirrors ez_manage.py:23956 — block AUGMENT
            # when gain_since_last_add < MIN_GAIN_TO_BUY_AGGRESSIVELY.
            if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", False))
                    and state.qty > 0.0001):
                _uag_last_px = (state.last_augmentation_price
                    if state.last_augmentation_price > 0 else state.entry_price)
                if _uag_last_px > 0:
                    _uag_gain_since = (
                        (mark - _uag_last_px) / _uag_last_px * 100.0
                        if is_long
                        else (_uag_last_px - mark) / _uag_last_px * 100.0
                    )
                    _uag_min_gain = float(getattr(config, "MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0))
                    if _uag_gain_since < _uag_min_gain:
                        continue
            # 2026-05-26 BATCH 4 — AUG_COOLDOWN_S time-axis sibling.
            # Mirrors live _recent_augments Redis floor 60-300s.
            _aug_cd_s_re = float(getattr(config, "AUG_COOLDOWN_S", 0.0))
            if (_aug_cd_s_re > 0.0
                    and state.last_augment_ts > 0.0
                    and (bar_ts - state.last_augment_ts) < _aug_cd_s_re):
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
            state.last_augmentation_price = mark

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

        # 2026-05-26 RATIO_REDUCE_PROXY — per-sym proxy for live ratio-trim REDUCE.
        # Default OFF (VEC_RATIO_REDUCE_PROXY_ENABLED=False) → check returns None.
        if _vec_check_ratio_proxy is not None and state.qty > 0.0001:
            _rrp = _vec_check_ratio_proxy(state, bar_ts, mark, gain, config)
            if _rrp is not None:
                frac = float(_rrp.get("frac", 0.0))
                if 0.0 < frac <= 1.0:
                    reduce_qty = state.qty * frac
                    if reduce_qty > 0:
                        ev = TradeEvent(ts=bar_ts, type="REDUCE", qty=reduce_qty, price=mark,
                            value=reduce_qty * mark, reason=_rrp.get("reason", "RATIO_REDUCE_PROXY"),
                            pnl_pct=gain)
                        events.append(ev)
                        trade_returns.append(gain * frac)
                        state.qty -= reduce_qty
                        state.last_reduce_ts = bar_ts

        # GOLDEN_RULE augment while holding
        if check_golden_rule_enforce is not None and config.GOLDEN_RULE_ENABLED and state.qty > 0.0001:
            _gr_cooldown_ok = (bar_ts - state.gr_last_fire_ts) >= float(config.GOLDEN_RULE_COOLDOWN_S)
            _gr_htf_ok = (_gr_htf_entry_mask is None) or bool(_gr_htf_entry_mask[i])
            if _gr_cooldown_ok and _gr_htf_ok:
                _gr_aug = check_golden_rule_enforce(_store, i, symbol, side, _pos, config, mode)
                if _gr_aug is not None and _gr_aug.get("action") == "AUGMENT":
                    # 2026-05-22 B3: MAX_AUGMENTS cap
                    _max_aug = int(getattr(config, "MAX_AUGMENTS_PER_POSITION", 20))
                    # 2026-05-26 BATCH 3 UAG: mirrors ez_manage.py:23956 — block
                    # AUGMENT when gain_since_last_add < MIN_GAIN_TO_BUY_AGGRESSIVELY.
                    _uag_block = False
                    if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", False))
                            and state.qty > 0.0001):
                        _uag_last_px = (state.last_augmentation_price
                            if state.last_augmentation_price > 0 else state.entry_price)
                        if _uag_last_px > 0:
                            _uag_gain_since = (
                                (mark - _uag_last_px) / _uag_last_px * 100.0
                                if is_long
                                else (_uag_last_px - mark) / _uag_last_px * 100.0
                            )
                            _uag_min_gain = float(getattr(config, "MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0))
                            if _uag_gain_since < _uag_min_gain:
                                _uag_block = True
                    # 2026-05-26 BATCH 4 — AUG_COOLDOWN_S time-axis sibling.
                    _aug_cd_s_gr = float(getattr(config, "AUG_COOLDOWN_S", 0.0))
                    _aug_cd_block_gr = (
                        _aug_cd_s_gr > 0.0
                        and state.last_augment_ts > 0.0
                        and (bar_ts - state.last_augment_ts) < _aug_cd_s_gr
                    )
                    if state.augmented_count < _max_aug and not _uag_block and not _aug_cd_block_gr:
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
                            state.last_augmentation_price = mark
                            state.gr_last_fire_ts = bar_ts

        # 2026-05-22 USER: end-of-bar bookkeeping — capture current bar's gain as prev_gain
        # for the next bar's MICRO_SCALP decel comparison. Only meaningful when position is
        # still open after the exit cascade (closed positions set prev_gain=0 on close).
        if state.qty > 0.0001:
            state.prev_gain = gain

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

    # Deduct round-trip spread/slippage from every trade return (NO-LIES: gross ≠ net).
    # 2026-05-22 FIX: use per-sym suffix (USDC=0.01%, USDT=0.06%) — was applying live USDT-class
    # cost to zero-fee USDC syms, dragging baseline 800%+ negative on 10k+ trades.
    _rt_cost = _vec_round_trip_cost_for_sym(symbol, config)
    if _rt_cost != 0.0:
        trade_returns = [r - _rt_cost for r in trade_returns]

    return events, trade_returns, n


# ════════════════════════════════════════════════════════════════════════════════
# Trade event writer → /history/<acct>/<SYM>_<SIDE>.jsonl compatible
# ════════════════════════════════════════════════════════════════════════════════

def _vec_round_trip_cost_for_sym(sym: str, cfg: Optional["SweepConfig"] = None) -> float:
    """2026-05-22 FIX: previous impl referenced an unbound module-level `config`
    name (NameError swallowed by try/except), silently returning the 0.08 literal
    regardless of SweepConfig override. NameError fixed — function now takes a
    SweepConfig instance so overrides actually flow through.

    USDC and USDT default to 0.08% per USER MANDATE 2026-05-22 (conservative cost
    covering webhook-taker fallback). Stocks read config_tradier (~0.05%)."""
    s = (sym or "").upper()
    if s.endswith("USDC"):
        if cfg is not None:
            return float(getattr(cfg, "ROUND_TRIP_COST_USDC_PCT", 0.08))
        return 0.08
    if s.endswith("USDT"):
        if cfg is not None:
            return float(getattr(cfg, "ROUND_TRIP_COST_USDT_PCT", 0.08))
        return 0.08
    try:
        import config_tradier as _ct
        return float(getattr(_ct, "ROUND_TRIP_COST_PCT", 0.05))
    except Exception:
        return 0.05


def write_history_jsonl(
    account: str,
    symbol: str,
    side: str,
    events: List[TradeEvent],
    out_root: Path,
    cfg: Optional["SweepConfig"] = None,
):
    """Append events to <out_root>/<account>/<SYMBOL>_<SIDE>.jsonl in the
    schema used by /history/<acct>/*.jsonl (used by /:5057 dashboard etc.).

    2026-05-18 NET MANDATE: ev.pnl_pct is the ENGINE-INTERNAL gross
    price-only return. The JSONL row gets pnl_pct = NET (gross minus per-symbol
    round-trip cost) and pnl_pct_gross = original gross for audit. This makes
    the JSONL consistent with the trade_returns list that the engine already
    cost-adjusts (this function's row is what chart_server and all sweep
    aggregators consume, so it MUST be net by default)."""
    acct_dir = out_root / account
    acct_dir.mkdir(parents=True, exist_ok=True)
    path = acct_dir / f"{symbol}_{side}.jsonl"
    _rt_cost = _vec_round_trip_cost_for_sym(symbol, cfg)
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
                _gross = float(ev.pnl_pct)
                _net = _gross - _rt_cost
                row["pnl_pct"] = round(_net, 6)
                row["pnl_pct_gross"] = round(_gross, 6)
                row["round_trip_cost_pct"] = _rt_cost
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
# Per-account active_config.json override loader (2026-05-26 grind-orchestrator)
# ════════════════════════════════════════════════════════════════════════════════
# Live per-account/per-sym/per-side configs live at:
#   data/hourly_reconfig/<account>/active_config.json
# Schema (one entry per "SYM_SIDE" key):
#   { "BTCUSDC_LONG": { "winning_tag":..., "wsharpe":..., "trades":...,
#                       "sample_tag":..., "overrides": { knob: val, ... },
#                       "cycle_id":... }, ... }
# Per-task overrides are applied to a per-task SweepConfig copy INSIDE the worker
# so each (sym, side) cell sees ONLY its own overrides — base process config is
# untouched. Unknown knobs (not on SweepConfig dataclass) skip silently and are
# reported once-per-run by run_sweep at top-level.
# USER MANDATE 2026-05-20/2026-05-26: hedge + no-loss are DEAD. Even if a per-sym
# override sets HEDGE_MODE/NOLOSS_ENABLED True, we force these False AFTER the
# override is applied — REENTRY is the only allowed protection.

# Mandate-locked knobs — ALWAYS False regardless of active_config override value.
_VEC_LOCKED_FALSE_KNOBS = (
    "UNIVERSAL_NOLOSS_GATE",
    "VEC_NOLOSS_GATE_ENABLED",
    "HEDGE_SCAN_ENABLED",
    "HEDGE_MODE",
    "OBLIGATORY_HEDGE_ENABLED",
    "NOLOSS_ENABLED",
)


def _load_active_config_overrides(account: str) -> Dict[str, Dict[str, Any]]:
    """Read data/hourly_reconfig/<account>/active_config.json and return
    {(sym_side_key): overrides_dict}. Fail-open: missing file → empty dict (log warn).
    Entries with no 'overrides' subdict are skipped.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not account:
        return out
    try:
        repo_root = Path(__file__).resolve().parent
        ac_path = repo_root / "data" / "hourly_reconfig" / account / "active_config.json"
        if not ac_path.exists():
            sys.stderr.write(
                f"V8_VEC_ACTIVE_CONFIG: WARN no per-account override file at {ac_path} — "
                f"continuing with global SweepConfig defaults\n"
            )
            return out
        with ac_path.open("r") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            sys.stderr.write(
                f"V8_VEC_ACTIVE_CONFIG: WARN {ac_path} not a dict — skipping\n"
            )
            return out
        for key, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            ov = entry.get("overrides")
            if isinstance(ov, dict) and ov:
                out[str(key).upper()] = dict(ov)
        sys.stderr.write(
            f"V8_VEC_ACTIVE_CONFIG: loaded {len(out)} per-(sym,side) override sets from {ac_path}\n"
        )
    except Exception as e:
        sys.stderr.write(
            f"V8_VEC_ACTIVE_CONFIG: ERROR loading per-account overrides for {account}: "
            f"{type(e).__name__}: {e} — continuing with global defaults\n"
        )
    return out


def _apply_per_task_overrides(
    base_config: "SweepConfig",
    overrides: Dict[str, Any],
) -> Tuple["SweepConfig", int, int, List[str]]:
    """Apply overrides to a SweepConfig copy. Returns (new_cfg, applied_n, unknown_n, unknown_keys).
    Mandate-locked knobs (NOLOSS/HEDGE family) are FORCED False afterwards regardless of
    what the override dict says — user mandate 2026-05-20 + 2026-05-26.
    """
    # dataclass shallow copy so base config stays untouched
    cfg = _dc_replace(base_config)
    applied = 0
    unknown_keys: List[str] = []
    # 2026-05-26 FIX: setattr ALL keys, not just hasattr ones. v8_vec_sweep and
    # vec_paths/* read knobs via `getattr(config, "KEY", default)` — dynamically
    # added attributes are honored. Filtering by hasattr threw away 110 of 114
    # active_config overrides per cell (BTC_DEDICATED, REGIME_*, RZ_*, BB_SQUEEZE,
    # WT_DIV_EXIT, etc.) producing Sharpe -0.15 on flz proof. Tracking unknowns
    # for telemetry only; they DO take effect on the cfg object.
    for k, v in overrides.items():
        if k.startswith("_"):
            # Skip metadata keys (_meta, _score, _wsharpe, _trades_in_7d, _promoted_at)
            continue
        try:
            setattr(cfg, k, v)
            applied += 1
            if not hasattr(type(cfg), k) and k not in {f.name for f in __import__("dataclasses").fields(cfg)}:
                # Track dynamic-attr knobs separately for visibility (they still WORK via getattr)
                unknown_keys.append(k)
        except Exception:
            unknown_keys.append(k)
    # USER MANDATE 2026-05-20 + 2026-05-26: hedge + no_loss are DEAD. Force locks
    # back to False even if a per-sym override tried to flip them on.
    for locked in _VEC_LOCKED_FALSE_KNOBS:
        if hasattr(cfg, locked):
            setattr(cfg, locked, False)
    return cfg, applied, len(unknown_keys), unknown_keys


# ════════════════════════════════════════════════════════════════════════════════
# Multi-symbol sweep entry
# ════════════════════════════════════════════════════════════════════════════════

def _run_sweep_worker(args_tuple):
    """Worker for parallel run_sweep — simulates ONE (sym, side) cell in a subprocess.

    Returns (sym, side, n_bars, elapsed_s, status, payload):
        status == "ok":      payload = (events, returns, applied_n, unknown_keys)
        status == "skip":    payload = error_message (FileNotFoundError)
        status == "fail":    payload = "TypeName: error_message"

    2026-05-26 PER-TASK OVERRIDES: args_tuple now carries an optional per-task
    overrides dict (7th field). The worker applies it to a fresh SweepConfig copy
    before simulation so each (sym, side) cell sees ITS OWN per-account active
    config, not just the global defaults. Mandate-locked knobs (NOLOSS/HEDGE
    family) are forced False after the override pass regardless.
    """
    # Back-compat unpack: tolerate the legacy 6-tuple (no per_task_overrides).
    if len(args_tuple) >= 7:
        sym, side, mode, config, start_ts, max_bars, per_task_overrides = args_tuple[:7]
    else:
        sym, side, mode, config, start_ts, max_bars = args_tuple
        per_task_overrides = None

    applied_n = 0
    unknown_keys: List[str] = []
    if per_task_overrides:
        config, applied_n, _unk_n, unknown_keys = _apply_per_task_overrides(config, per_task_overrides)
        # one-line per-cell log so we can see overrides being applied in the run log
        sys.stderr.write(
            f"V8_VEC_OVERRIDES: {sym}_{side} applied {applied_n} / unknown {_unk_n}\n"
        )

    t0 = time.perf_counter()
    try:
        events, returns, n_bars = simulate_one_symbol(
            sym, side, mode, config,
            start_ts=start_ts, max_bars=max_bars,
        )
    except FileNotFoundError as e:
        return (sym, side, 0, time.perf_counter() - t0, "skip", str(e))
    except Exception as e:
        return (sym, side, 0, time.perf_counter() - t0, "fail",
                f"{type(e).__name__}: {e}")
    elapsed = time.perf_counter() - t0
    return (sym, side, n_bars, elapsed, "ok", (events, returns, applied_n, unknown_keys))


def _resolve_workers(workers: int) -> int:
    """Workers default: max(1, min(8, cpu_count() - 2)). Pass workers <= 0 for default."""
    if workers and workers > 0:
        return int(workers)
    try:
        cpu = os.cpu_count() or 2
    except Exception:
        cpu = 2
    return max(1, min(8, cpu - 2))


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

    2026-05-19 PARALLEL: dispatches (sym, side) cells across a ProcessPoolExecutor
    (workers= controls pool size; <=0 picks max(1, min(8, cpu_count()-2))). Cells
    are independent so a 60-sym x 2-side run goes from sequential hours to
    ~minutes on an 8-core box. JSONL writes still happen in the parent so the
    file remains coherent; V8_VEC_PROGRESS lines may interleave (one per worker
    completion, ordered by finish-time).
    """
    config = config or SweepConfig()
    sides = sides or ["LONG", "SHORT"]
    # 2026-05-21 PER-SIDE ALLOWLIST FIX (user-mandated): drop (sym, side) cells that the live
    # system would refuse via symbols_<acct>_long/short.json. Mirrors live tradier_positions.py
    # is_symbol_tradeable. File missing → fail-open (allow). File present (even empty list) →
    # honor strictly. Skipped cells don't run the simulation; lower wall-clock, honest numbers.
    # 2026-05-21 20:40 — BYPASS for parameter sweeps that test arbitrary symbol universes.
    #   sweep_coordinator runs --account ang --symbols BTC/ETH/SOL/XRP, but ang's allowlist
    #   contains 25 different syms (and ang_short.json=[]). Result: 0 cells, 0 trades, 200+
    #   sweep arms wasted as USELESS dedups. Bypass with env V8_VEC_SWEEP_BYPASS_ACCT_FILTER=1
    #   (set by coord) — preserves live-mirror filter for non-sweep callers.
    _bypass_acct_filter = os.environ.get("V8_VEC_SWEEP_BYPASS_ACCT_FILTER", "0") in ("1", "true", "True")
    _repo_root = Path(__file__).resolve().parent
    _vec_allow_long: Optional[set] = None
    _vec_allow_short: Optional[set] = None
    if account and not _bypass_acct_filter:
        _vl_path = _repo_root / f"symbols_{account}_long.json"
        _vs_path = _repo_root / f"symbols_{account}_short.json"
        try:
            if _vl_path.exists():
                _vec_allow_long = set(json.load(open(_vl_path)))
        except Exception:
            _vec_allow_long = None
        try:
            if _vs_path.exists():
                _vec_allow_short = set(json.load(open(_vs_path)))
        except Exception:
            _vec_allow_short = None
    def _vec_side_allowed(_sym: str, _side: str) -> bool:
        if _side == "LONG":
            return _vec_allow_long is None or _sym in _vec_allow_long
        if _side == "SHORT":
            return _vec_allow_short is None or _sym in _vec_allow_short
        return True
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if "T" in start else \
               datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_dt.timestamp())

    # 2026-05-19 RACE FIX: epoch+PID for uniqueness when concurrent cells finish in same second
    ts_run = f"{int(time.time())}_{os.getpid()}"
    SWEEP_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = out_jsonl or (SWEEP_RESULTS_DIR / f"v8_vec_sweep_{ts_run}.jsonl")
    trades_path = SWEEP_RESULTS_DIR / f"v8_vec_sweep_{ts_run}_trades.jsonl"

    returns_by_sym: Dict[str, List[float]] = {}
    total_bars = 0
    elapsed_per_sym = []
    # 2026-05-26 PER-TASK OVERRIDES: aggregate unknown-knob set across all cells
    # so we can report once-per-run instead of spamming per-cell.
    _aggregated_unknown_knobs: set = set()

    n_workers = _resolve_workers(workers)

    # 2026-05-26 PER-ACCOUNT ACTIVE_CONFIG OVERRIDE LOADER
    # Load data/hourly_reconfig/<account>/active_config.json ONCE here and pass the
    # per-(sym,side) overrides into each worker task tuple. Worker applies them to
    # a fresh SweepConfig copy so each cell uses ITS OWN per-sym/per-side knobs.
    # Without this every cell ran on global SweepConfig defaults (BTCUSDC LONG flz
    # proof 2026-05-26: pool_sharpe -0.12 vs flz8_BEST 0.518 — root cause was
    # missing per-account overrides).
    _per_acct_overrides = _load_active_config_overrides(account)
    tasks = [
        (sym, side, mode, config, start_ts, max_bars,
         _per_acct_overrides.get(f"{sym}_{side}".upper()))
        for sym in symbols
        for side in sides
        if _vec_side_allowed(sym, side)
    ]
    n_tasks = len(tasks)
    _vec_skipped = len(symbols) * len(sides) - n_tasks
    _n_with_overrides = sum(1 for t in tasks if t[6])
    print(
        f"V8_VEC_SWEEP_START: mode={mode} account={account} symbols={len(symbols)} "
        f"sides={sides} tasks={n_tasks} (per_side_skipped={_vec_skipped}) "
        f"workers={n_workers} start={start} per_task_overrides_loaded={_n_with_overrides}",
        flush=True,
    )

    with summary_path.open("w") as smry, trades_path.open("w") as trd:
        # Sequential fast-path keeps the existing single-process behaviour (also
        # used by callers that have already entered a ProcessPoolExecutor, since
        # nested daemon processes would crash).
        if n_workers <= 1 or n_tasks <= 1:
            iterator = (_run_sweep_worker(t) for t in tasks)
        else:
            pool = ProcessPoolExecutor(max_workers=n_workers)
            futures = [pool.submit(_run_sweep_worker, t) for t in tasks]
            iterator = (fut.result() for fut in as_completed(futures))
        completed = 0
        for sym, side, n_bars, elapsed, status, payload in iterator:
            completed += 1
            if status == "skip":
                sys.stderr.write(f"SKIP {sym}_{side}: {payload}\n")
                continue
            if status == "fail":
                sys.stderr.write(f"FAIL {sym}_{side}: {payload}\n")
                continue
            # 2026-05-26 worker payload extended to (events, returns, applied_n, unknown_keys)
            # Back-compat: tolerate the legacy 2-tuple format if some other caller path runs.
            if len(payload) >= 4:
                events, returns, _applied_n, _unknown_keys = payload[:4]
                if _unknown_keys:
                    _aggregated_unknown_knobs.update(_unknown_keys)
            else:
                events, returns = payload
            elapsed_per_sym.append((sym, side, n_bars, elapsed))
            total_bars += n_bars
            key = f"{sym}_{side}"
            if returns:
                returns_by_sym[key] = returns
            # 2026-05-19 PROGRESS HEARTBEAT — sweep_coordinator silence-detector kills
            # any engine that prints nothing for PRE_SIM_SILENCE_S (300s). Without this
            # line, v8_vec_sweep prints only its banner then nothing until completion,
            # so the coordinator timed out every full-universe run at ~5min. One line
            # per (sym, side) gives the watchdog a heartbeat AND lets ops see progress.
            print(
                f"V8_VEC_PROGRESS: [{completed}/{n_tasks}] sym={sym} side={side} "
                f"n_bars={n_bars} trades={len(returns)} elapsed_s={elapsed:.2f}",
                flush=True,
            )

            # Write each event to the trades JSONL (source of truth).
            # 2026-05-18 NET MANDATE: pnl_pct stored NET of round-trip cost;
            # raw price-only return preserved as pnl_pct_gross. Mirrors
            # write_history_jsonl above so both outputs are consistent.
            _trd_rt_cost = _vec_round_trip_cost_for_sym(sym, config)
            for ev in events:
                iso = datetime.fromtimestamp(ev.ts, tz=timezone.utc).isoformat()
                _row = {
                    "ts": iso, "symbol": sym, "side": side, "type": ev.type,
                    "qty": ev.qty, "price": ev.price, "value": ev.value,
                    "reason": ev.reason,
                }
                if ev.pnl_pct:
                    _gross = float(ev.pnl_pct)
                    _row["pnl_pct"] = _gross - _trd_rt_cost
                    _row["pnl_pct_gross"] = _gross
                    _row["round_trip_cost_pct"] = _trd_rt_cost
                else:
                    _row["pnl_pct"] = ev.pnl_pct
                trd.write(json.dumps(_row) + "\n")

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

        if n_workers > 1 and n_tasks > 1:
            pool.shutdown(wait=True)

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

    # Final-line: canonical 9-field via metrics_guard. Per CLAUDE.md NO-LIES
    # MANDATE / IMPOSTER BLOCK rule 4 this is one of the quarantined producers
    # and MUST exit non-zero if metrics_guard refuses the row — never silently
    # downgrade.
    mg_mode = "stocks" if mode == "tradier" else "crypto"
    try:
        canonical_line = metrics_guard.format_standard_set(std, mode=mode)
    except metrics_guard.FakeMetricRefused as e:
        sys.stderr.write(
            f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_sweep "
            f"format_standard_set: {e}\n"
        )
        sys.exit(2)

    # Aggregate canonical row → CSV via metrics_guard.write_sharpe_row(). This
    # is the chokepoint required by CLAUDE.md to prevent v0-style sub-floor
    # publication (e.g. pool_sharpe 0.1057, 28 syms, 85.1% dd surfaced as
    # "good"). write_sharpe_row will refuse banned column names, missing
    # canonical fields, and inflated values. Sub-floor sample is allowed but
    # tagged in the verdict column.
    agg_csv = SWEEP_RESULTS_DIR / f"v8_vec_sweep_{ts_run}.csv"
    agg_row = {
        "pool_sharpe": float(std.get("pool_sharpe", 0.0)),
        "sym_sharpe": float(std.get("sym_sharpe", 0.0)),
        "avg_gain_trade": float(std.get("avg_gain_trade", 0.0)),
        "gain_per_yr": float(std.get("gain_per_yr", 0.0)),
        "gain_sym_yr": float(std.get("gain_sym_yr", 0.0)),
        "trades": int(std.get("trades", 0) or 0),
        "max_dd_pct": float(std.get("max_dd_pct", 0.0)),
        "n_syms": int(std.get("n_syms", 0) or 0),
        "years": float(std.get("years", 0.0) or 0.0),
        "engine": "v8_vec_sweep",
        "tier": "run_sweep",
        "mode": mode,
        "account": account,
        "start": start,
        "ts_run": ts_run,
        "summary_path": str(summary_path),
        "trades_path": str(trades_path),
    }
    try:
        metrics_guard.write_sharpe_row(agg_csv, agg_row, mode=mg_mode, append=False)
    except metrics_guard.FakeMetricRefused as e:
        sys.stderr.write(
            f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_sweep "
            f"write_sharpe_row: {e}\n"
        )
        sys.exit(2)

    # Sub-floor sample = NOT a promote-able publication. The DIAGNOSTIC row is
    # left in place for transparency, but we exit non-zero so no orchestrator
    # (sweep_coordinator, leaderboard, HTML generator, etc.) can mistake the
    # result for a "green row" candidate. Override with env V8_VEC_ALLOW_DIAGNOSTIC=1
    # to deliberately run diagnostic sweeps without exit code.
    floor_syms = (metrics_guard.MIN_SYMS_STOCKS if mg_mode == "stocks"
                  else metrics_guard.MIN_SYMS_CRYPTO)
    sub_floor = (int(agg_row["n_syms"]) < floor_syms or
                 float(agg_row["years"]) < metrics_guard.MIN_YEARS)
    allow_diag = (os.environ.get("V8_VEC_ALLOW_DIAGNOSTIC", "0").strip()
                  in ("1", "true", "True"))
    if sub_floor and not allow_diag:
        sys.stderr.write(
            f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_sweep sub-floor sample "
            f"(n_syms={int(agg_row['n_syms'])} < floor={floor_syms} or "
            f"years={float(agg_row['years']):.2f} < {metrics_guard.MIN_YEARS}). "
            f"pool_sharpe={float(agg_row['pool_sharpe']):+.4f} is DIAGNOSTIC-only "
            f"per CLAUDE.md NO-LIES MANDATE sample floor. Row tagged DIAGNOSTIC "
            f"and left at {agg_csv}; engine exits non-zero so this cannot be "
            f"promoted. Set V8_VEC_ALLOW_DIAGNOSTIC=1 to suppress this exit "
            f"on deliberate diagnostic runs.\n"
        )
        sys.exit(3)

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

    # 2026-05-26 PER-TASK OVERRIDES: log unknown-config-keys once per run so the
    # caller can see which active_config knobs the vec engine doesn't yet wire.
    if _aggregated_unknown_knobs:
        _uk_sorted = sorted(_aggregated_unknown_knobs)
        sys.stderr.write(
            f"V8_VEC_UNKNOWN_KNOBS: {len(_uk_sorted)} keys not on SweepConfig "
            f"(skipped in workers, none crashed): {','.join(_uk_sorted)}\n"
        )

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
        "agg_csv": str(agg_csv),
        "elapsed_per_sym": elapsed_per_sym,
        "unknown_override_knobs": sorted(_aggregated_unknown_knobs),
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
    # GR activation params (2026-05-18 2-stage gate — mirror live golden_rule_htf).
    _gr_require_act = bool(getattr(base_config, "GOLDEN_RULE_REQUIRE_ACTIVATION", False))
    _gr_act_tfs = list(getattr(base_config, "GOLDEN_RULE_ACTIVATION_TF_LIST", None) or [])
    _gr_entry_tfs = list(getattr(base_config, "GOLDEN_RULE_ENTRY_TF_LIST", None) or [])
    # Precompute all 16 HTF confirmation masks
    htf_masks: List[np.ndarray] = []
    for _label, dc_thr, bb_thr in _DCBB_GRID:
        mask, _ = evaluate_gr_htf_vec(
            npz, is_long, mode,
            min_tfs=min_tfs, min_ind=min_ind,
            invert_dc_bb=True,
            dc_threshold=dc_thr, bb_threshold=bb_thr,
            n=n,
            require_activation=_gr_require_act,
            activation_tfs=_gr_act_tfs,
            entry_tfs=_gr_entry_tfs,
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
    workers = _resolve_workers(workers)

    # Accumulate per-variant trade returns BY SYMBOL so metrics_guard can compute
    # proper sym_sharpe (not just pool_sharpe). Routing through
    # metrics_guard.standard_metric_set per CLAUDE.md NO-LIES mandate.
    variant_returns_by_sym: Dict[str, Dict[str, List[float]]] = {
        label: {} for label, _, _ in _DCBB_GRID
    }

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
                if ret_list:
                    variant_returns_by_sym[label].setdefault(sym_key, []).extend(ret_list)
            n_trades_here = sum(len(v) for v in sym_result.values())
            print(f"[gr_dcbb_sweep] [{completed}/{n_tasks}] {sym_key}/{side_key} done — variants={len(sym_result)} trades_all_variants={n_trades_here}", flush=True)

    elapsed_total = time.perf_counter() - t_run_start
    print(f"\n[gr_dcbb_sweep] all {n_tasks} tasks done in {elapsed_total:.1f}s — reporting {n_variants} variants\n", flush=True)

    # Report canonical 9-field metrics for every variant — routed exclusively
    # through metrics_guard.write_sharpe_row() per CLAUDE.md IMPOSTER BLOCK
    # rule 4. ANY refusal aborts the publication with non-zero exit.
    results_dir = Path(__file__).resolve().parent / "data" / "sweep_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_csv = results_dir / f"gr_dcbb_sweep_{mode}_{ts_str}.csv"
    seen_fps: Dict[tuple, str] = {}
    mg_mode = "stocks" if mode == "tradier" else "crypto"
    rows_written = 0
    for label, dc_thr, bb_thr in _DCBB_GRID:
        rets_by_sym = variant_returns_by_sym[label]
        # Flat list for max_dd_pct (cumulative-curve metric); per-sym dict for
        # metrics_guard.standard_metric_set so sym_sharpe is real, not pool_sharpe.
        flat_rets: List[float] = []
        for _sym_rets in rets_by_sym.values():
            flat_rets.extend(_sym_rets)
        if len(flat_rets) >= 2:
            std = metrics_guard.standard_metric_set(rets_by_sym, n_years)
        else:
            std = {
                "pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0,
                "gain_per_yr": 0.0, "gain_sym_yr": 0.0, "trades": len(flat_rets),
                "n_syms": len(rets_by_sym), "years": n_years,
            }
        row = {
            "pool_sharpe": round(float(std["pool_sharpe"]), 4),
            "sym_sharpe": round(float(std["sym_sharpe"]), 4),
            "avg_gain_trade": round(float(std["avg_gain_trade"]), 4),
            "gain_per_yr": round(float(std["gain_per_yr"]), 2),
            "gain_sym_yr": round(float(std["gain_sym_yr"]), 4),
            "trades": int(std["trades"]),
            "max_dd_pct": round(_max_dd_pct(flat_rets), 4) if flat_rets else 0.0,
            "n_syms": int(std["n_syms"]),
            "years": round(float(std["years"]), 3),
            "label": label,
            "dc_threshold": dc_thr,
            "bb_threshold": bb_thr,
            "engine": "v8_vec_sweep",
            "tier": "gr_dcbb_threshold",
            "mode": mode,
            "account": account,
            "start": start,
            "ts_run": ts_str,
        }
        try:
            metrics_guard.write_sharpe_row(out_csv, row, mode=mg_mode, append=True)
            rows_written += 1
        except metrics_guard.FakeMetricRefused as e:
            sys.stderr.write(
                f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_gr_dcbb_sweep "
                f"label={label} dc={dc_thr} bb={bb_thr}: {e}\n"
            )
            sys.exit(2)
        # Identical-score detection
        _ps_round = float(row["pool_sharpe"])
        _trades_int = int(row["trades"])
        _gain_per_yr_val = float(row["gain_per_yr"])
        if _trades_int >= 5:
            fp = (_ps_round, _trades_int)
            if fp in seen_fps:
                print(
                    f"[IDENTICAL_SCORE_WARNING] '{label}' (DC={dc_thr} BB={bb_thr}) == '{seen_fps[fp]}':"
                    f" pool_sharpe={_ps_round:.4f} trades={_trades_int} — knob not differentiating!", flush=True,
                )
            else:
                seen_fps[fp] = label
        print(
            f"  {label:35s}  DC={dc_thr:.2f}  BB={bb_thr:.2f}  "
            f"pool_sharpe={_ps_round:+.4f}  trades={_trades_int:5d}  gain/yr={_gain_per_yr_val:+.1f}%",
            flush=True,
        )
    print(f"\n[gr_dcbb_sweep] results -> {out_csv} (rows={rows_written}/{len(_DCBB_GRID)})", flush=True)

    # Sub-floor sample-floor gate (same as run_sweep). Exit non-zero so no
    # orchestrator can promote a sub-floor DC/BB sweep.
    floor_syms = (metrics_guard.MIN_SYMS_STOCKS if mg_mode == "stocks"
                  else metrics_guard.MIN_SYMS_CRYPTO)
    sub_floor = (len(symbols) < floor_syms or n_years < metrics_guard.MIN_YEARS)
    allow_diag = (os.environ.get("V8_VEC_ALLOW_DIAGNOSTIC", "0").strip()
                  in ("1", "true", "True"))
    if sub_floor and not allow_diag:
        sys.stderr.write(
            f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_gr_dcbb_sweep sub-floor "
            f"sample (n_syms={len(symbols)} < floor={floor_syms} or "
            f"years={n_years:.2f} < {metrics_guard.MIN_YEARS}). Rows written to "
            f"{out_csv} are DIAGNOSTIC-only per CLAUDE.md NO-LIES MANDATE. "
            f"Set V8_VEC_ALLOW_DIAGNOSTIC=1 to suppress this exit on deliberate "
            f"diagnostic runs.\n"
        )
        sys.exit(3)


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
    ap.add_argument("--workers", type=int, default=0,
                    help="ProcessPool size for run_sweep / gr_dcbb_sweep. "
                         "0 (default) = max(1, min(8, cpu_count()-2)). "
                         "1 = sequential single-process.")
    ap.add_argument("--max-bars", type=int, default=None, help="Cap bars per symbol (smoke testing)")
    ap.add_argument("--no-history", action="store_true", help="Skip /history/<acct>/ JSONL writes")
    ap.add_argument("--override", action="append", default=[], help="KEY=VAL config overrides (repeatable)")
    ap.add_argument("--tier", default="", help="Sweep tier: 'gr_dcbb_threshold' for fast DC/BB threshold sweep")
    args = ap.parse_args()

    # NO-LIES MANDATE / IMPOSTER BLOCK retrofit banner (CLAUDE.md 2026-04-30).
    # Every Sharpe row this engine emits to data/sweep_results/ is now routed
    # through metrics_guard.write_sharpe_row(); refusals exit non-zero rather
    # than downgrade silently.
    print(
        "v8_vec_sweep imposter-block retrofit active: every row routed through "
        "metrics_guard.write_sharpe_row()",
        flush=True,
    )

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    sides = [s.strip().upper() for s in args.sides.split(",") if s.strip()]
    cfg = SweepConfig()
    # DEAD-KNOB REWIRE 2026-05-18: refuse vec runs that toggle ENGINE-ONLY knobs.
    # Listed in vec_paths/__init__.py:VEC_ENGINE_ONLY_KNOBS. Without this guard,
    # such overrides silently pass through to baseline because the vec path
    # doesn't simulate the live-only state machine they gate — wastes hours of
    # sweep compute producing DUPLICATE_OF_<baseline>.
    try:
        from vec_paths import vec_refuses_knob, vec_engine_only_reason
    except ImportError:
        vec_refuses_knob = lambda _: False
        vec_engine_only_reason = lambda _: ""
    _parsed_overrides = _parse_overrides(args.override)
    _engine_only_violations = [k for k in _parsed_overrides if vec_refuses_knob(k)]
    if _engine_only_violations:
        sys.stderr.write(
            "VEC_ENGINE_ONLY_REFUSED: v8_vec_sweep does not honour these knobs "
            "(would produce DUPLICATE_OF_<baseline>): "
            + ", ".join(_engine_only_violations) + "\n"
        )
        for _k in _engine_only_violations:
            sys.stderr.write(f"  {_k}: {vec_engine_only_reason(_k)}\n")
        sys.stderr.write(
            "Run with backtest_v8_engine.py (V8_USE_VEC_ALL=0) or remove these "
            "overrides. See vec_paths/__init__.py:VEC_ENGINE_ONLY_KNOBS.\n"
        )
        sys.exit(4)
    for k, v in _parsed_overrides.items():
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
    print(f"  agg_csv={s['agg_csv']}")
    print(s["canonical_line"])
    # 2026-05-19 COORD-COMPATIBLE V8_RESULT LINE — sweep_coordinator parses
    # `V8_RESULT: pool_sharpe=… sym_sharpe=… sharpe=… gain_pct=… closes=… wins=… losses=…`
    # (crypto) or `… pnl=… trades=… …` (tradier). Emit the same shape so the
    # coord ledger captures pool_sharpe/trades/etc. when dispatching v8_vec_sweep.
    _trades_int = int(s.get("n_trades", s.get("trades", 0)) or 0)
    _wins_int = int(s.get("wins", 0) or 0)
    _losses_int = int(s.get("losses", 0) or 0)
    if _wins_int == 0 and _losses_int == 0 and _trades_int > 0:
        # standard_metric_set doesn't track wins/losses across pooled returns;
        # leave zero — coord uses trades count as ground truth.
        pass
    _ps_val = float(s.get("pool_sharpe", 0.0))
    _ss_val = float(s.get("sym_sharpe", 0.0))
    _agt = float(s.get("avg_gain_trade", 0.0))
    _gain_pct_total = _agt * _trades_int  # reconstruct total gain (used for V8_RESULT only)
    if args.mode == "tradier":
        print(
            f"V8_RESULT: pool_sharpe={_ps_val:.6f} sym_sharpe={_ss_val:.6f} "
            f"sharpe={_ps_val:.6f} pnl={_gain_pct_total:+.4f} "
            f"trades={_trades_int} wins={_wins_int} losses={_losses_int}",
            flush=True,
        )
    else:
        print(
            f"V8_RESULT: pool_sharpe={_ps_val:.6f} sym_sharpe={_ss_val:.6f} "
            f"sharpe={_ps_val:.6f} gain_pct={_gain_pct_total:+.4f} "
            f"closes={_trades_int} wins={_wins_int} losses={_losses_int}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
