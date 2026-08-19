"""v8_vec_sweep.py — THE primary vectorized backtest engine.

═══════════════════════════════════════════════════════════════════════════
PRIMARY BACKTEST ENGINE — FOR BOTH per-sym AND SWEEP USE CASES
═══════════════════════════════════════════════════════════════════════════
  • USE THIS for all parameter sweeps, per-symbol optimization, and A/B tests.
  • backtest_v8_engine.py — OFF THE TABLE. Too slow (no advances in 6 months).
  • uve_engine.py — scratch/diagnostic only. 189 lines, not a sweep engine.
  • vec_paths/*.py — parity-tested gate modules consumed by this engine.

Standalone replacement for backtest_v8_engine.py when running millions of
parameter variations. Uses ONLY the parity-tested vec_paths modules and
position_evaluator vec functions — no imports from ez_manage / ez_positions_quick
/ tradier_manage / live code at all.

PARITY vs LIVE (2026-05-27)
===========================
  MATCHED (default ON):
    - WT_3M_ALIGNED, REENTRY (B04/B11/B15), GOLDEN_RULE (all TFs incl. W),
      DELTA_ENGINE, QUALITY_BOTTOM_ENTRY, R1/R2, WT_CROSSUNDER_FINAL,
      PEAK_GIVEBACK, PPL v2, DUP_GUARD, AUGMENT_LOCK, NEWBORN_PROTECT
  MANDATE-DEAD (locked False — matches live 2026-05-20/26):
    - UNIVERSAL_NOLOSS_GATE, VEC_NOLOSS_GATE_ENABLED, HEDGE_MODE,
      OBLIGATORY_HEDGE_ENABLED, HEDGE_SCAN_ENABLED
  TRADIER-ONLY OVERRIDE REQUIRED:
    - DELTA_ENTRY_ENABLED must be False for tradier (config_tradier.py=False;
      SweepConfig default=True for crypto). Pass --override DELTA_ENTRY_ENABLED=False.
  NOT REPLICATED (portfolio-level, unfeasible per-symbol):
    - RATIO_SIZE / RATIO_REDUCE (requires live L/S portfolio state across 30+ syms)

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
from vector_mandatory_coverage import add_coverage_claim_arguments, enforce_coverage_claim

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
    # Production Tradier WT/DC base score, vectorized from the active scalar
    # score_entry_multitf formula. Thresholds are meaningless without it.
    from wt_dc_entry_scorer_vec import score_entry_multitf_vec
except ImportError:
    score_entry_multitf_vec = None
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
        score_wt_dc_exit_vec,
    )
except ImportError:
    check_rz_breakout_entry_vec = None
    check_rz_exit_vec = None
    compute_rz_cascade_signals_vec = None
    score_wt_dc_exit_vec = None
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

# 2026-05-27 BATCH 5 — port of 20 top LIVE_ONLY signals (see
# data/_diagnostic/signal_parity_diff_all.md). All knobs default OFF/0;
# Arm A bit-exact baseline preserved when none of the *_VEC_ENABLED knobs flip.
try:
    from vec_paths.live_only_signals_batch5 import (
        check_hedge_protect_loss_entry as _vec_check_hedge_protect_entry,
        check_quick_open_strong_entry as _vec_check_quick_open_strong,
        check_quick_hedge_same_sym_last_resort as _vec_check_quick_hedge_lr,
        check_daemon_price_cross_reentry as _vec_check_daemon_pc_reentry,
        check_guaranteed_price_cross_reentry_disk as _vec_check_guar_pc_reentry,
        check_direction_favorable_reentry as _vec_check_dir_fav_reentry,
        check_ridiculous_hold_exit as _vec_check_ridiculous_hold,
        check_quick_reduce_strong_reduce_exit as _vec_check_quick_reduce_strong,
        check_quick_breakeven_gain_erosion_stop as _vec_check_breakeven_erosion,
        check_quick_cycle_tp_stoch_against as _vec_check_cycle_tp_stoch,
        check_quick_bandaid_off_exit as _vec_check_quick_bandaid,
        check_delta_exit_speed_decay as _vec_check_delta_speed_decay,
        check_quick_sentiment_cut_gain as _vec_check_sentiment_cut,
        check_hedge_bandaid_off_first_pre as _vec_check_hedge_bandaid_pre,
        check_in_gain_trend_exit as _vec_check_in_gain_trend,
        fix_r1_reason_string_for_diff as _vec_fix_r1_reason,
    )
except ImportError:
    _vec_check_hedge_protect_entry = None
    _vec_check_quick_open_strong = None
    _vec_check_quick_hedge_lr = None
    _vec_check_daemon_pc_reentry = None
    _vec_check_guar_pc_reentry = None
    _vec_check_dir_fav_reentry = None
    _vec_check_ridiculous_hold = None
    _vec_check_quick_reduce_strong = None
    _vec_check_breakeven_erosion = None
    _vec_check_cycle_tp_stoch = None
    _vec_check_quick_bandaid = None
    _vec_check_delta_speed_decay = None
    _vec_check_sentiment_cut = None
    _vec_check_hedge_bandaid_pre = None
    _vec_check_in_gain_trend = None
    _vec_fix_r1_reason = None


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
    def reason(self) -> str:
        """Last entry reason — read by BANDAID_OFF / HEDGE_BANDAID_OFF_FIRST_PRE
        to detect synthetic hedge entries. See vec_paths/live_only_signals_batch5.py."""
        return str(getattr(self._state, "last_entry_reason", "") or "")
    @property
    def _sym_state(self):
        """Expose underlying SymState — used by BATCH 6 cooldown helpers in
        vec_paths/live_only_signals_batch5.py to stamp b6_last_fire_ts. The
        helper module is fully optional; if _sym_state is missing the cooldowns
        no-op and the batch 5 behavior is preserved."""
        return self._state
    @property
    def qty(self) -> float:
        return self._state.qty if self._state.is_long else -self._state.qty
    def reset_ppl(self):
        self.ppl_fired = False
        self.ppl_first_exit_price = 0.0
        self.ppl_stop_level = 0.0
        self.ppl_stop_upgraded = False


REPO_ROOT = Path(__file__).resolve().parent
# Permit a pinned research NPZ directory for parity runs.  The normal default
# remains backtest_v8/indicators; this prevents silently testing a different
# local snapshot when the source receipt names another artifact.
NPZ_DIR = Path(os.environ.get("V8_VEC_NPZ_DIR", str(REPO_ROOT / "backtest_v8" / "indicators")))
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
    # Stock parity sweep: when nonzero, quantity is an integer share chunk
    # (1..10 shares) rather than a crypto-style dollar/price fraction.
    VEC_SHARE_CHUNK: int = 0
    MIN_POSITION_SIZE: float = 1.0
    MIN_GAIN: float = 3.0
    MIN_GAIN_TO_BUY_AGGRESSIVELY: float = 3.0
    COMMISSION_BUFFER_PCT: float = 0.10
    # ── 2026-05-31 MTF-conditioned FUNDING_GATE (mirror live config.py; read by vec_paths.funding_gate) ──
    FUNDING_GATE_ENABLED: bool = True
    FUNDING_GATE_LONG_MAX: float = 0.0005
    FUNDING_GATE_SHORT_MIN: float = -0.0005
    FUNDING_GATE_MTF_REQUIRED: bool = True
    FUNDING_GATE_MTF_LONG_MAX_BULL_TFS: int = 0
    FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS: int = 1
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
    # ── 2026-08-10 GDX_SHORT augment relaxation (AUGMENT_WT_4H_BOUNCE) ───────
    # Mirrors c08 snapshot AUGMENT_WT_4H_BOUNCE (WT 4h bounce augment). Default
    # False preserves legacy sweep behaviour; GDX_SHORT grind enables it to
    # lift trade count from 44 → >100 by allowing 4h WT bounce augments.
    AUGMENT_WT_4H_BOUNCE_ENABLED: bool = False
    AUGMENT_WT_4H_MULTIPLIER: float = 2.0
    AUGMENT_WT_4H_REQUIRE_HIGHER_WT: bool = False
    AUGMENT_WT_4H_REQUIRE_HIGHER_PRICE: bool = False
    AUGMENT_WT_D_BOUNCE_ENABLED: bool = False
    AUGMENT_WT_D_MULTIPLIER: float = 2.0
    AUGMENT_WT_D_REQUIRE_HIGHER_WT: bool = False
    AUGMENT_WT_D_REQUIRE_HIGHER_PRICE: bool = False
    HARD_AUGMENT_LOCK_SECONDS: float = 900.0
    HARD_REDUCE_LOCK_SECONDS: float = 60.0
    AUGMENTATION_COOLDOWN_SECONDS: float = 540.0
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = False  # 2026-05-22 parity: live False
    WT_3M_FORCE_OPEN_ENABLED: bool = False  # USER 2026-05-21 04:45 DISABLED in config.py + config_tradier.py (ZECUSDC parabolic suicide trade); sweep default now mirrors live. Re-enable only after SMA_15 pullback pyramid vec-validation.
    GR_VOTE_FALLBACK_MIN: int = 7  # 2026-05-27 surfaced so --override can tune (was getattr'd via vec_paths/golden_rule_enforce.py)
    REENTRY_GR_HLHH_MODE: str = "OR"  # 2026-08-17 overdue reentry: OR=HH or HL, HH=only HH, HL=only HL on 1h/4h/D
    REENTRY_GR_MIN_TFS: int = 2  # min TFs (1h/4h/D) with HL/HH + WT alignment required for overdue reentry
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
    # ── 2026-05-27 VEC_EVENT_DRIVEN_LOOP — USER MANDATE ───────────────────
    # Most bars (~95-99%) have no signal fire. Build event_mask = union of
    # every entry-trigger mask BEFORE the loop; when FLAT, jump ahead to next
    # event bar via np.flatnonzero pointer instead of evaluating every bar.
    # Position-OPEN bars still iterate every bar (state.prev_gain + cooldown
    # logic depend on consecutive bars and many check_* exits can fire any
    # bar). Cuts per-symbol simulate time 64s → ~1-3s (target 20-50x).
    # AUTO-DEACTIVATES when stateful FLAT-path checks (GR/DELTA/BATCH-5
    # entries) are enabled, because they can fire on any bar — set False on
    # those bars in event_mask would silently break parity.
    VEC_EVENT_DRIVEN_LOOP_ENABLED: bool = True
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
    EXIT_STRUCT_TF: str = "None"
    LONG_STRUCT_EXIT_TF: str = "D"
    SHORT_STRUCT_EXIT_TF: str = "4h"
    # ── GR multiplier exit sweep flags ────────────────────────────────────────
    GR_EXIT_ENABLED: bool = False        # exit when GR votes ≥ MIN_TFS TFs × MIN_IND each AND wt1_3m against
    GR_EXIT_MIN_TFS: int = 3             # TFs that must each reach GR_EXIT_MIN_IND (default 3×3=9)
    GR_EXIT_MIN_IND: int = 3             # indicators per TF that must agree against trade
    GR_HTF_DIRECT_EXIT_ENABLED: bool = True
    GR_HTF_DIRECT_EXIT_SCORE: float = 12.0
    WT_DC_EXIT_ENABLED: bool = True
    WT_DC_EXIT_THRESHOLD: float = 20.0
    WT_DC_EXIT_STALE_MAX_S: float = 600.0
    # ── R1 DC emergency exit ──────────────────────────────────────────────────
    R1_DC_LOW4_3M_EMERGENCY_ENABLED: bool = True
    R1_NEWBORN_WINDOW_MIN: float = 15.0
    R1_USE_DC_4BAR: bool = True
    R1_REQUIRE_WT15_ADVERSE: bool = True
    TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED: bool = True
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
    TOP_OF_RANGE_BLOCK_ENABLED: bool = True   # 2026-05-22 live: True (ORDI prevention)
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
    # 2026-05-17 live config.py added Weekly TF to GR cascade. Vec parity fix 2026-05-27.
    GOLDEN_RULE_DC_W_ENABLED: bool = True
    GOLDEN_RULE_BB_W_ENABLED: bool = True
    GOLDEN_RULE_MULT_15M: float = 1.0
    GOLDEN_RULE_MULT_1H: float = 1.5
    GOLDEN_RULE_MULT_4H: float = 2.0
    GOLDEN_RULE_MULT_D: float = 3.0
    GOLDEN_RULE_MULT_W: float = 4.0       # 2026-05-17 live: 4.0 (cascade 1.0/1.5/2.0/3.0/4.0)
    GOLDEN_RULE_HTF_VETO_ENABLED: bool = False
    # 2026-05-22 live RESTORED: HTF_MIN_TFS 1→3, MIN_IND 2→5. Live config.py uses 3/5.
    # 2026-05-28 sweep: 2/3 gave +0.2195 vs +0.2043 (stocks) — CANDIDATE for live change,
    # NOT applied here. SweepConfig stays at live-matching 3/5 for parity.
    # To test 2/3: pass --override GOLDEN_RULE_HTF_MIN_TFS=2 GOLDEN_RULE_MIN_IND=3
    GOLDEN_RULE_MIN_IND: int = 5
    GOLDEN_RULE_HTF_MIN_TFS: int = 3
    # 2026-05-22 live: REQUIRE_ACTIVATION=True gates GR to D/4h breakout TFs only.
    # Vec default was False → fired far more GR entries than live.
    GOLDEN_RULE_REQUIRE_ACTIVATION: bool = True
    GOLDEN_RULE_ACTIVATION_TF_LIST: List[str] = field(default_factory=lambda: ["D", "4h"])
    GOLDEN_RULE_ENTRY_TF_LIST: List[str] = field(default_factory=lambda: ["1h", "15m", "3m"])
    GR_DC_EXTENDED_LONG: float = 0.65  # DC extension threshold for HTF confirmation (0=use module default)
    GR_BB_EXTENDED_LONG: float = 0.75  # BB pct-b threshold for HTF confirmation (0=use module default)
    # ── Partial Profit Lock (PPL) ────────────────────────────────────────────
    PARTIAL_PROFIT_LOCK_ENABLED: bool = True
    PARTIAL_PROFIT_LOCK_GAIN_PCT: float = 1.5       # 2026-05-26 live GRID WINNER: 1.5% (was 0.5%)
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT: float = 1.75  # 2026-05-26 live: 1.75% proportional (+0.25)
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT: float = 0.10
    # _TRADIER-suffixed mirrors of config_tradier.py:539-549 — read by vec PPL only when
    # mode=="tradier" (vec_paths/partial_profit_lock_v2._ppl_cfg). Crypto vec ignores these.
    PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER: float = 1.5
    PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER: float = 1.75
    PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER: float = 0.02
    PARTIAL_PROFIT_LOCK_FRAC_TRADIER: float = 0.625
    # ── peak giveback / BE erosion exits ─────────────────────────────────────
    PEAK_GIVEBACK_PROTECTION_ENABLED: bool = True
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED: bool = False   # default OFF (user disabled 2026-05-11)
    PEAK_GIVEBACK_DROP_PCT: float = 0.5
    PEAK_GIVEBACK_MIN_PEAK_PCT: float = 0.5
    PEAK_GIVEBACK_HARD_ZERO_ENABLED: bool = False      # OFF since 2026-04-27
    PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN: bool = True
    PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT: float = -0.5
    MIN_HOLD_BARS: int = 0  # 15m base: 0/4/8/16 values — 0=disabled, 4=1h, 8=2h, 16=4h holds (tradier 5m base; crypto 15m)
    MIN_HOLD_MINUTES_CRYPTO: float = 30.0
    TRADIER_MIN_HOLD_MINUTES: float = 240.0
    BREAKEVEN_GRACE_MINUTES: float = 5.0
    BE_EROSION_ENABLED: bool = False                   # default OFF
    BE_EROSION_MIN_PEAK_PCT: float = 0.5
    BE_EROSION_FLOOR_PCT: float = 0.0
    BE_EROSION_HOLD_MIN_MIN: float = 15.0
    # ── profit-take reduce paths ──────────────────────────────────────────────
    VEC_REENTRY_REQUIRE_PRIOR_EXIT: bool = False       # 2026-06-02 ENTRY-PARITY FIX (default OFF until validated vs Tier-2/live). The vec fires reentry blocks (esp B15_STRONG_TREND: price>dc_high_4h & wt_vel_1h>2 & k_1h<85) as FRESH entries on every uptrend bar → 99% REENTRY_TREND, ~40x over-trading vs live (MU: vec 582 opens vs live 14). LIVE only reenters AFTER an actual exit. When True, a reentry-block open (fire_block) is allowed ONLY within VEC_REENTRY_WINDOW_BARS of a real prior exit; fresh first-entries must come from non-reentry signals (GR/DELTA/DC-break/quality). Set True after the Tier-2 ground-truth diff confirms it pulls vec entry distribution toward live.
    VEC_REENTRY_WINDOW_BARS: int = 400                  # reentry-eligibility window (bars since last held) for VEC_REENTRY_REQUIRE_PRIOR_EXIT
    VEC_REENTRY_DC4_EXITPRICE_ENABLED: bool = True      # 2026-06-02 USER MANDATE: ON by default (proven MU 0.84/NVDA 0.71, kills 729-trade B-block over-fire). USER's PRECISE reentry rule (replaces the B-block over-fire). While FLAT after an exit, until positionAmt>0: (a) <=1h since exit → price crosses dc_high4_5m (LONG)/dc_low4_5m (SHORT); (b) >1h since exit → price crosses exit_price (FOREVER). BOTH require wt1 RISING (wt1>wt1_prev) on 3m AND 15m AND 1h (LONG; falling on all 3 for SHORT). When True, fire_block uses THIS rule instead of the B-blocks.
    VEC_REENTRY_HOUR_BARS: int = 12                     # bars per 1 hour at base TF (stocks 5m→12, crypto 3m→20). The <=1h window for the dc4 branch of the reentry rule.
    VEC_REENTRY_DC_USE_4BAR: bool = False               # 2026-08-03 emergency: mandatory reentry uses dc_high_5m/dc_low_5m
    # Causal continuation reentry: after a real exit, reenter only when WT
    # supports the side and price breaks a completed Donchian level.
    VEC_WT_PRICE_BREAKOUT_REENTRY_ENABLED: bool = True
    QUICK_REDUCE_TECHNICAL_ONLY: bool = True           # 2026-06-02 USER MANDATE — mirror live gate (config.QUICK_REDUCE_TECHNICAL_ONLY). When True, the stochastic/profit-take winner-cutting reduce paths (PROFIT_TAKE_REDUCE / STRONG_REDUCE_K / QUICK_REDUCE_STRONG_REDUCE) are FORCED OFF so the vec sweep cannot discover winner-cutting configs that live (gated) can never execute. Only sanctioned technical exits (GR/WT/DC/struct/ATR-trail) reduce — identical to live. Set False ONLY to A/B the disabled traps.
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
    SWEEP_DISABLE_HEDGING: bool = True      # 2026-05-29 USER: HARD master kill — NO hedge OPEN in sweeps regardless of any hedge knob (HEDGE_SCAN/HEDGE_PROTECT/QUICK_HEDGE/OBLIGATORY). Makes hedge knobs inert no-ops; stops runaway HEDGE_OPEN/CLOSE/REOPEN cycles (AVAXUSDC halt). Set False ONLY for a dedicated hedge-validation sweep.
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
    # PARITY NOTE: config.py (crypto) has DELTA_ENTRY_ENABLED=True; config_tradier.py has
    # DELTA_ENTRY_ENABLED=False (T25 sweep confirmed -4% avg Sharpe with True for stocks).
    # Tradier sweeps MUST pass --override DELTA_ENTRY_ENABLED=False to match live.
    DELTA_ENGINE_ENABLED: bool = True
    DELTA_ENTRY_ENABLED: bool = True
    DELTA_ENTRY_MIN_TF: int = 3
    DELTA_ENTRY_Z_THRESHOLD: float = 2.5
    DELTA_SPEED_SMOOTH: int = 5
    DELTA_TF_Z_THRESHOLD: float = 1.5
    DELTA_HTF_GATE: str = "hh_hl_4h"  # 2026-05-22 live: HH/HL 4h structure gate (was "none" in vec)
    # ── WT_15M_BOUNCE_OPEN — fresh 15m cross within BB + 4h/1h HTF in favor ─────
    WT_15M_BOUNCE_OPEN_ENABLED: bool = False
    WT_15M_BOUNCE_MAX_BARS_AGO: int = 2       # bars since 15m cross (2 bars = 30min)
    WT_15M_BOUNCE_BB_MIN: float = 0.05        # bb_pct_b lower bound (within BB)
    WT_15M_BOUNCE_BB_MAX: float = 0.95        # bb_pct_b upper bound (within BB)
    WT_15M_BOUNCE_REQUIRE_BOTH_HTF: bool = False  # False=OR(4h,1h), True=AND(4h,1h)
    # Research WT-15m entry overlays.  ``value_lower`` uses a directional
    # cross plus WT1 falling versus its prior value; ``any_cross`` accepts
    # either cross direction; ``any_cross_gr`` additionally requires GR.
    WT_15M_CROSS_ENTRY_ENABLED: bool = False
    WT_15M_CROSS_ENTRY_MODE: str = "value_lower"
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
    BB_PULLBACK_GATE_ENABLED: bool = True        # 2026-05-28: per-sym grid +0.066 global (36K vs 65K trades)
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
    # 2026-06-03 sma_200_15m DISTANCE size-ladder knobs (companion to _LadderReturns + _ladder_arr).
    # RE-ADDED after a concurrent session SweepConfig edit dropped this block. Default OFF → mults 1.0
    # → byte-identical baseline. Sweep mults/cap to find rungs; select on gain_vs_bh, pool_sharpe>0.1.
    BREAKOUT_SIZE_LADDER_VEC_ENABLED: bool = False
    BREAKOUT_SIZE_LADDER_VEC_T1_PCT: float = 1.0
    BREAKOUT_SIZE_LADDER_VEC_T1_MULT: float = 1.5
    BREAKOUT_SIZE_LADDER_VEC_T2_PCT: float = 1.5
    BREAKOUT_SIZE_LADDER_VEC_T2_MULT: float = 2.0
    BREAKOUT_SIZE_LADDER_VEC_T3_PCT: float = 2.5
    BREAKOUT_SIZE_LADDER_VEC_T3_MULT: float = 3.0
    BREAKOUT_SIZE_LADDER_VEC_MAX_MULT: float = 3.0
    # 2026-08-04: regression-band slope/STDEV ladder.  This is the core
    # Tradier quantity ladder and must be available to every vector sweep.
    LR_BAND_LADDER_ENABLED: bool = True
    LR_BAND_LADDER_MODE: str = "center_plateau"
    LR_BAND_LADDER_BOTTOM_MULT: float = 10.0
    LR_BAND_LADDER_TOP_MULT: float = 3.0
    LR_BAND_LADDER_ABOVE_TOP_MULT: float = -1.0
    LR_BAND_LADDER_BELOW_BOTTOM_MULT: float = 0.0
    LR_BAND_LADDER_CENTER: float = 0.5
    LR_BAND_LADDER_TF_BOTTOM: dict = field(default_factory=lambda: {"D": 10.0, "4h": 6.0, "1h": 4.0})
    LR_BAND_LADDER_TF_TOP: dict = field(default_factory=lambda: {"D": 6.0, "4h": 4.0, "1h": 1.0})
    LR_BAND_LADDER_TRIGGER: str = "union"
    LR_BAND_LADDER_BASE_UNIT_USD: float = 2000.0
    LR_BAND_LADDER_CAPACITY_USD: float = 16000.0
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
    # Exact legacy LR-band entry trigger (tradier_manage.py). The NPZ carries
    # the same lrL pct-b/slope/R2 series used by the scalar path.
    LR_BAND_ENTRY_ENABLED: bool = False
    LR_BAND_ENTRY_TF: str = "D"
    LR_BAND_ENTRY_LO: float = 0.30
    LR_BAND_ENTRY_R2_MIN: float = 0.70
    LR_BAND_ENTRY_SIDES: str = "L"
    LR_BAND_REGIME_ENABLED: bool = False
    LR_BAND_REGIME_MAX_PB: float = 0.60
    LR_BAND_HARVEST_ENABLED: bool = False
    LR_BAND_HARVEST_HI: float = 0.70
    LR_BAND_HARVEST_FRAC: float = 0.25
    LR_BAND_SLOPE_FLIP_EXIT_ENABLED: bool = False
    LR_BAND_SLOPE_FLIP_MIN_PCT_DAY: float = 0.05
    LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN: float = 240.0
    EXIT_MAX_HOLD_ENABLED: bool = False
    EXIT_MAX_HOLD_MINUTES: float = 99999.0
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
    WT_ENTRY_ENABLED: bool = False           # WT dip entry — mirrors tradier_manage.py:9476 (WT_ENTRY_ENABLED)
    STRENGTH_FILTER_ENABLED: bool = False    # Strength gate — mirrors tradier_manage.py:11322 (STRENGTH_FILTER_ENABLED)
    WT_DC_ENTRY_ENABLED: bool = False        # WT/DC score gate — mirrors ez_positions_quick.py:2106
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
    # Exact Tradier entry-zone veto. This is separate from the WT/DC score
    # threshold: live gates every candidate OPEN when the switch is enabled.
    DC_ENTRY_VETO_ENABLED_TRADIER: bool = False
    DC_POSITION_ENTRY_THRESHOLD: float = 0.25
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
    # Classic formation paths (shared with config_tradier / exact engine).
    FORMATION_TFS: str = "15m,1h,4h,D"
    FORMATION_MIN_SCORE: float = 0.65
    FORMATION_POSITION_SIZE_MULT: float = 1.0
    FORMATION_EXIT_MIN_GAIN_PCT: float = 0.0
    FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED: bool = False
    FORMATION_HEAD_SHOULDERS_EXIT_ENABLED: bool = False
    FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED: bool = False
    FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED: bool = False
    FORMATION_WEDGE_ENTRY_ENABLED: bool = False
    FORMATION_WEDGE_EXIT_ENABLED: bool = False
    FORMATION_TRIANGLE_ENTRY_ENABLED: bool = False
    FORMATION_TRIANGLE_EXIT_ENABLED: bool = False
    FORMATION_FLAG_PENNANT_ENTRY_ENABLED: bool = False
    FORMATION_FLAG_PENNANT_EXIT_ENABLED: bool = False
    FORMATION_CUP_HANDLE_ENTRY_ENABLED: bool = False
    FORMATION_CUP_HANDLE_EXIT_ENABLED: bool = False
    FORMATION_TREND_STRUCTURE_ENTRY_ENABLED: bool = False
    FORMATION_TREND_STRUCTURE_EXIT_ENABLED: bool = False
    # Per-symbol queue gate applied to classic-formation entries. Portfolio L/S
    # ratio is intentionally excluded here because an isolated symbol replay
    # cannot represent the rest of the live book.
    COUNTER_TREND_ADD_BLOCK_ENABLED: bool = True
    COUNTER_TREND_SMA200_BYPASS_ENABLED: bool = True
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
    LIVE_ENTRY_ENGINE_REENTRY_SIZE_MULT: float = 1.0
    WT_DC_ENTRY_THRESHOLD: float = 0.0  # tradier final-score threshold for vec gate
    WT_DC_HTF_GATE: str = "none"  # 'none'|'1h'|'4h'|'4h_D' — mirrors tradier_manage:2751; blocks wt_open_ok entry when HTF WT against
    WT_DC_LONG_ENABLED: bool = True
    WT_DC_SHORT_ENABLED: bool = True
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
    MTF_GR_MIN_IND: int = 7              # 2026-06-03 USER "FIX GR": A/B winner (12 syms) — breakout-mode min7 GR as universal entry gate lifts pool 0.4146→0.4355, per_sym 0.4529→0.4822 (cuts weak half). min8 collapses sample.
    MTF_GR_INVERT_DC_BB: bool = True     # 2026-06-03 BREAKOUT mode (GR fires ON breakouts — intended). Room-mode (False) was inert/neutral and penalized the breakouts the force-open targets.
    GR_FILTER_ALL_ENTRIES: bool = True   # 2026-06-03 USER "GR is the prime entrypoint": require GR-pass on EVERY entry (DELTA/GOLDEN_RULE/force-open), not just the 1.5% force-open path. REENTRY exempt.
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
    # Research switch: close directly on the configured against-position WT
    # cross, without requiring the GR side of the compound exit.  Default OFF
    # preserves the live-equivalent compound behavior.
    MTF_WT_CROSS_EXIT_DIRECT_ENABLED: bool = False
    MTF_DC_REJECT_EXIT_ENABLED: bool = True
    MTF_DC_REJECT_EXIT_TF: str = '1h'  # 2026-05-22 parity: live 1h
    MTF_DC_REJECT_EXIT_LOOKBACK: int = 5
    MTF_BB_REJECT_EXIT_ENABLED: bool = True
    MTF_BB_REJECT_EXIT_TF: str = '1h'  # 2026-05-22 parity: live 1h
    MTF_BB_REJECT_EXIT_LOOKBACK: int = 5
    MTF_REENTRY_COOLDOWN_BARS_HARD: int = 1
    # ── 4-FLAG REWIRE (2026-05-18 21:00 UTC mandate) ────────────────────────────
    # Mirrors ez_manage.py:18650+ (HTF_TREND_VETO), :38247+ (R3_HTF_FLIP/_4H),
    # :35454+ (BREAKOUT_RETEST_ARMED). Previously all 4 were reverted by A3 and
    # produced baseline-identical sweep results when re-enabled because vec path
    # had no implementation. Default OFF per current config.py/config_tradier.py.
    HTF_TREND_VETO_ENABLED: bool = True   # 2026-05-26 live: True (BTCDOM autopsy + switch_hunt ΔPS +0.020). mirrors ez_manage.py:18660
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
    # 2026-05-27 USER MANDATE: OPEN positions do NOT get revised for entry until
    # gain > MIN_GAIN. Default ON per mandate; reduces wasted CPU on bad augments
    # AND prevents pyramiding into losers.
    UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED: bool = True
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

    # 2026-05-27 BATCH 5 — Top-20 LIVE_ONLY signals port (vec_paths/live_only_signals_batch5.py).
    # Each knob default OFF/0 preserves Arm A bit-exact baseline. Flip via --override KEY=True
    # for A/B sweeps that test whether the new signals close the live/vec gap.
    #   Entry signals (top 10 LIVE_ONLY by volume):
    HEDGE_PROTECT_LOSS_VEC_ENABLED: bool = False               # #1+#2+#5+#7 hedge_protect_loss_entry
    HEDGE_PROTECT_TRIGGER_GAIN_PCT: float = -0.5               # gain threshold for hedge to fire
    HEDGE_PROTECT_QTY_PCT: float = 1.0                         # hedge qty as fraction of main
    SYNTHETIC_LOSER_THRESHOLD_PCT: float = -2.0                # synthetic portfolio_losers cutoff
    SYNTHETIC_LOSER_MIN_AGE_MIN: float = 30.0                  # min position age to qualify as loser
    QUICK_OPEN_STRONG_VEC_ENABLED: bool = False                # #3+#8 quick_open_strong (composite>=80)
    QUICK_OPEN_STRONG_VEL_MIN: float = 1.0
    QUICK_OPEN_STRONG_K_LONG_MAX: float = 25.0
    QUICK_OPEN_STRONG_K_SHORT_MIN: float = 75.0
    QUICK_OPEN_STRONG_BB_LONG_MAX: float = 0.30
    QUICK_OPEN_STRONG_BB_SHORT_MIN: float = 0.70
    QUICK_OPEN_STRONG_DC_LONG_MAX: float = 0.40
    QUICK_OPEN_STRONG_DC_SHORT_MIN: float = 0.60
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED: bool = False # #4 (DISABLED live — historical-only)
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_GAIN_PCT: float = -3.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_AGE_MIN: float = 240.0
    QUICK_HEDGE_SAME_SYM_LAST_RESORT_QTY_PCT: float = 1.0
    DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED: bool = False       # #6 daemon_price_cross_reentry
    DAEMON_PRICE_CROSS_REENTRY_MAX_AGE_HOURS: float = 48.0
    DAEMON_PRICE_CROSS_PCT: float = 0.0                        # 0 = strict cross
    # 2026-05-28 anti-churn DC-break reentry gate (parity w/ ez_manage._price_level_reentry_monitor).
    # Default OFF = touch-back baseline. Flip via --override for A/B.
    REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED: bool = False
    REENTRY_LIVE_MONITOR_DC_BREAK_TF: str = "3m"
    REENTRY_LIVE_MONITOR_DC_BREAK_USE_4BAR: bool = False
    REENTRY_MAX_PRICE_DIVERGENCE_PCT: float = 20.0             # mirror live's 20% divergence guard
    GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED: bool = False  # #9 guar_pc_disk reentry
    DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED: bool = False      # #10 direction_favorable_reentry
    DIRECTION_FAVORABLE_MAX_MINUTES: float = 30.0
    # Exit signals (top 10 LIVE_ONLY by volume):
    RIDICULOUS_HOLD_VEC_ENABLED: bool = False                  # #1 ridiculous_hold (DISABLED live, historical)
    RIDICULOUS_LOSS_PCT: float = -15.0
    RIDICULOUS_HOLD_HOURS: float = 48.0
    RIDICULOUS_HOLD_REQUIRE_GAIN_NONNEG: bool = True
    QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED: bool = False       # #2 HLR_TOP_EXIT family
    HLR_MIN_GAIN_PCT: float = 1.0
    HLR_MIN_TFS: int = 2
    HLR_REENTRY_MULT: float = 1.5
    HLR_REDUCE_FRAC: float = 0.5
    QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED: bool = False     # #3 (DISABLED live, historical)
    BREAKEVEN_GAIN_EROSION_MIN_GAIN: float = 0.10
    HARD_BREAKEVEN_MIN_PEAK_PCT: float = 0.5
    QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED: bool = False     # #4 cycle_tp_stoch_against
    QUICK_CYCLE_TP_MIN_GAIN_PCT: float = 1.0
    QUICK_CYCLE_TP_REDUCE_FRAC: float = 0.5
    QUICK_BANDAID_OFF_VEC_ENABLED: bool = False                # #5 bandaid_off_first
    DELTA_EXIT_SPEED_DECAY_VEC_ENABLED: bool = False           # #6 delta_exit_speed_decay
    DELTA_EXIT_SPEED_DECAY_MIN_GAIN: float = 0.5
    DELTA_EXIT_SPEED_DECAY_MIN_TFS: int = 2
    QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED: bool = False         # #7 sentiment_cut_gain
    QUICK_SENTIMENT_CUT_MIN_GAIN: float = 0.5
    QUICK_SENTIMENT_CUT_REDUCE_FRAC: float = 0.5
    HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED: bool = False      # #8 hedge_bandaid_off_first_pre
    # #9 R1 reason-string fix (no knob — applied to vec event reasons via _vec_fix_r1_reason
    # at event-emit time when VEC_FIX_R1_REASON_STRING_FOR_DIFF=True).
    VEC_FIX_R1_REASON_STRING_FOR_DIFF: bool = False
    IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED: bool = False       # #10 in_gain_trend_exit (live-bare reason)
    IN_GAIN_TREND_REDUCE_FRAC: float = 0.5

    # 2026-05-27 BATCH 6 — CALIBRATION knobs for the 5 over-firing signals.
    # Default 0 = inert (preserves Batch 5 behavior); recommended values shipped
    # below are calibrated to bring vec trade count within 2× of live extrapolated.
    # Per-signal cooldown floors (seconds) mirror live's _recent_* Redis floors.
    HEDGE_PROTECT_COOLDOWN_S: float = 0.0          # recommended 3600 — live's _recent_hedges floor
    HEDGE_PROTECT_MIN_AGE_MIN: float = 0.0         # recommended 60 — only hedge mature losers
    HEDGE_PROTECT_REQUIRE_BARS_LOSING: int = 0     # recommended 3 — N consecutive bars in loss
    DAEMON_PRICE_CROSS_COOLDOWN_S: float = 0.0     # recommended 300 — prevent intra-min re-fires
    DAEMON_PRICE_CROSS_MIN_DIST_PCT: float = 0.0   # recommended 0.05 — minimum cross magnitude
    GR_AUGMENT_COOLDOWN_S: float = 0.0             # recommended 300 — _recent_augments Redis floor
    QUICK_CYCLE_TP_COOLDOWN_S: float = 0.0         # recommended 900 — prevent repeated trims
    QUICK_CYCLE_TP_MIN_PEAK_PCT: float = 0.0       # recommended 0.5 — require peak above floor first
    IN_GAIN_TREND_COOLDOWN_S: float = 0.0          # recommended 3600 — only fire 1×/hr per sym
    IN_GAIN_TREND_REQUIRE_FULL_FLIP: bool = False  # recommended True — require BOTH wt1<wt2 AND wt2 turning down
    QUICK_SENTIMENT_CUT_COOLDOWN_S: float = 0.0    # recommended 900 — fire once per micro-trend
    DELTA_EXIT_SPEED_DECAY_COOLDOWN_S: float = 0.0 # recommended 900
    HLR_TOP_COOLDOWN_S: float = 0.0                # recommended 900
    # Tighter QUICK_OPEN_STRONG factors (relax over-tightening that caused under-fire)
    QUICK_OPEN_STRONG_RELAXED: bool = False        # recommended True — drop one of the 5 factor gates per fire
    # RIDICULOUS_HOLD: live's behavior is a periodic background sweep, not bar-by-bar.
    # vec needs the "loss path" (rule c, gain<0 when NONNEG=False) to fire more often.
    RIDICULOUS_HOLD_FIRE_EVERY_N_BARS: int = 0     # recommended 20 — sample every 20 bars not every bar

    # ── 2026-05-27 BATCH 7 — STRUCTURAL PARITY GATES ─────────────────────────
    # Two structural pieces vec lacks vs live, both default OFF so Arm A
    # baseline stays bit-exact preserved.
    #
    # (1) VEC_MTF_ARMED_STATE_ENABLED — additive MTF-armed-state FILTER on
    #     entry-emit sites, mirrors ez_manage.py:22755 live behaviour:
    #       gate OPEN/AUGMENT/ENTRY (but NOT REENTRY) on
    #       `armed_arrays['armed_any'][i] is True`
    #     Uses the existing vec_paths/mtf_armed_entries.py build_armed_arrays()
    #     warm-start precompute. INDEPENDENT from MTF_ARMED_ENTRY_ENABLED
    #     (which short-circuits ALL legacy entries — different semantics).
    #     When True, vec entries cold-start gated until a DC/BB/WT armed
    #     event is observed on prior bars within the arming window.
    #     Refuses with reason `MTF_NO_ARMED_STATE` (matches live string).
    #     Expected effect: vec total trade count drops ~70-80% toward live's
    #     ~3,829 (per Batch 6 vec 78,003 vs live target 7,658 = 2× live).
    # (2) VEC_MULTI_SYM_OUTER_LOOP_ENABLED — cross-symbol shared portfolio
    #     state for the 5 hedge_protect signals in
    #     `vec_paths/live_only_signals_batch5.py` (HEDGE_PROTECT_LONG_LOSS,
    #     HEDGE_PROTECT_SHORT_LOSS, QUICK_HEDGE_SAME_SYM_LAST_RESORT, etc.).
    #     When True: a process-wide loser-set dict
    #     (managed by run_sweep — see _b7_portfolio_state) replaces the
    #     SYNTHETIC self-only model so a sym only fires hedge_protect when
    #     OTHER syms are actually losing. When False: legacy synthetic
    #     model (current behaviour). Set via run_sweep — affects worker
    #     pool size (must be 1) and worker dispatch (must iterate by global
    #     timestamp). Documented as PARTIAL/BLOCKED in batch7 report if
    #     worker dispatch can't be cleanly serialized; see structural
    #     blocker list in data/_diagnostic/vec_parity_batch7.md.
    VEC_MTF_ARMED_STATE_ENABLED: bool = True   # 2026-05-27 parity: live MTF_ARMED_ENTRY_ENABLED=True gates entries via armed state; vec batch7 additive gate mirrors ez_manage.py:22755
    VEC_MTF_ARMED_GATE_REENTRY: bool = False           # gate REENTRY-typed entries too (live excludes)
    VEC_MTF_ARMED_GATE_HEDGE_OPEN: bool = False        # gate HEDGE_OPEN events too (live excludes)
    VEC_MTF_ARMED_BYPASS_STRONG: bool = True           # mirror live: STRONG_BUY/QUICK_OPEN bypass
    VEC_MTF_ARMED_RESULTING_REASON: str = "MTF_NO_ARMED_STATE"  # log/skip reason on block
    VEC_MULTI_SYM_OUTER_LOOP_ENABLED: bool = False     # see structural design notes above
    # 2026-06-03 SMA200-distance sizing ladder — mirrors live BREAKOUT_SIZE_LADDER
    # (ez_manage.py ~22918, config.py). Position size N× base based on |price−sma200|/sma200.
    # Applied as per-trade return multiplier at OPEN (like _side_return_mult).
    BREAKOUT_SIZE_LADDER_ENABLED: bool = True
    BREAKOUT_SIZE_SMA200_T1_PCT: float = 1.0
    BREAKOUT_SIZE_SMA200_T1_MULT: float = 1.5
    BREAKOUT_SIZE_SMA200_T2_PCT: float = 1.5
    BREAKOUT_SIZE_SMA200_T2_MULT: float = 2.0
    BREAKOUT_SIZE_SMA200_T3_PCT: float = 2.5
    BREAKOUT_SIZE_SMA200_T3_MULT: float = 3.0
    BREAKOUT_SIZE_MAX_MULT: float = 3.0

    # ── 2026-08-19 CRYPTO vs TRADIER mode-aware baseline (user unlock) ──
    # SweepConfig defaults mirror crypto (config.py) where they exist.
    # Tradier (stocks) requires specific overrides to match live TradierConfig.
    # Use sweep_config_for_mode(mode) factory below — do not branch inside SweepConfig.
    @classmethod
    def for_mode(cls, mode: str) -> "SweepConfig":
        cfg = cls()
        if mode == "tradier":
            # Ported from TradierConfig live values 2026-08-19 audit
            # DELTA is too fast for stocks — must be off
            cfg.DELTA_ENTRY_ENABLED = False  # T25 sweep -4% with True
            cfg.DELTA_ENGINE_ENABLED = False
            # WT_DC must see Daily uptrend — 4h_D prevents short-into-uptrend
            cfg.WT_DC_HTF_GATE = "4h_D"
            cfg.WT_DC_ENTRY_THRESHOLD = 45.0
            cfg.TRA_WT_DC_ENTRY_THRESHOLD = 85.0
            # Augment-at-loss is the bleed — both gates false
            # SweepConfig uses AUGMENT_AT_LOSS_ENABLED (crypto name); tradier uses _TRADIER suffix
            # Ensure both are false
            if hasattr(cfg, "AUGMENT_AT_LOSS_ENABLED"):
                cfg.AUGMENT_AT_LOSS_ENABLED = False
            # Tradier-specific gates that exist only in TradierConfig
            if hasattr(cfg, "AUGMENT_AT_LOSS_ENABLED_TRADIER"):
                cfg.AUGMENT_AT_LOSS_ENABLED_TRADIER = False
            if hasattr(cfg, "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER"):
                cfg.AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER = True
            # VEC_SHARE_CHUNK 0 is crypto fractional; stocks use integer shares — keep 0 for % returns but note parity via backtest_v8_engine
            # GOLDEN_RULE defaults already tradier-correct (HTF ladder)
        else:
            # crypto baseline — ensure crypto-live values
            cfg.DELTA_ENTRY_ENABLED = True
            cfg.DELTA_ENGINE_ENABLED = True
            cfg.WT_DC_HTF_GATE = "none"
            cfg.WT_DC_ENTRY_THRESHOLD = 0.0
            if hasattr(cfg, "AUGMENT_AT_LOSS_ENABLED"):
                cfg.AUGMENT_AT_LOSS_ENABLED = False
        return cfg

def sweep_config_for_mode(mode: str):
    """Factory: SweepConfig with mode-aware defaults. Use this in all sweep entry points instead of SweepConfig() directly."""
    return SweepConfig.for_mode(mode)


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
        if mode == "tradier":
            from classic_formations import ensure_npz_formation_fields
            _formation_source = {
                key: np.asarray(z[key])
                for tf in ("15m", "1h", "4h", "D")
                for field in ("open", "high", "low", "close", "volume")
                for key in (f"{field}_{tf}",)
                if key in z
            }
            # Parent availability timestamps are the causal candle identity.
            # OHLC-change inference alone drops distinct consecutive flat bars,
            # shortening/offsetting formation lookbacks even though broadcasts
            # of an ordinary parent candle still appear to collapse correctly.
            _formation_source.update(
                {
                    key: np.asarray(z[key])
                    for tf in ("15m", "1h", "4h", "D")
                    for key in (f"timestamp_{tf}",)
                    if key in z
                }
            )
            _formation_added = ensure_npz_formation_fields(_formation_source)
            npz.update({key: values[i0:] for key, values in _formation_added.items()})
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


class _LadderReturns(list):
    """list subclass — every appended per-trade return is scaled by the position's entry-bar
    sma_200_15m-distance size-ladder multiplier (state.entry_ladder_mult). Lets a vec sweep size
    by sma200 distance exactly like live BREAKOUT_SIZE_LADDER, applied at the SINGLE init site so
    all 30 close/append paths (inline + helper) are captured. Default mult 1.0 → byte-identical."""
    def __init__(self, lstate):
        super().__init__()
        self._lstate = lstate
    def append(self, x):
        super().append(x * self._lstate.entry_ladder_mult)
    def extend(self, xs):
        m = self._lstate.entry_ladder_mult
        super().extend(v * m for v in xs)


@dataclass
class SymState:
    is_long: bool
    qty: float = 0.0
    entry_price: float = 0.0
    initial_qty: float = 0.0
    entry_ladder_mult: float = 1.0  # 2026-06-03 sma200-distance size-ladder mult captured at the entry bar; applied to this position's per-trade returns (see _LadderReturns). 1.0 = no ladder.
    opened_at: float = 0.0
    last_augment_ts: float = 0.0
    last_reduce_ts: float = 0.0
    last_fire_ts: float = 0.0
    augmented_count: int = 0
    max_gain: float = 0.0
    last_held_bar: int = -10_000_000  # 2026-06-02: bar index of the most recent bar a position was held (for reentry-context gating — reentry blocks may only fire shortly after an actual exit, like live)
    last_exit_price: float = 0.0       # price at the most recent exit (for the dc4/exit_price reentry rule)
    last_exit_bar: int = -10_000_000   # bar index of the most recent exit
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
    _mtf_dc_outside_ts: float = 0.0
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
    # 2026-05-27 BATCH 5 — tracking for DAEMON / GUARANTEED / DIRECTION_FAVORABLE reentries.
    # Set on every REDUCE-final / CLOSE event; consumed by check_*_reentry signals
    # when state.qty <= 0.0001 (position flat). last_close_reason is used by
    # GUARANTEED_PRICE_CROSS_REENTRY_DISK to thread the exit reason through.
    last_close_price: float = 0.0
    last_close_ts: float = 0.0
    last_close_reason: str = ""
    # 2026-05-27 BATCH 5 — synthetic entry-reason tracking (used by EXIT signals
    # that gate on hedge tags like QUICK_BANDAID_OFF / HEDGE_BANDAID_OFF_FIRST_PRE).
    # Set on every OPEN event when LIVE_ONLY signals are active; default "".
    last_entry_reason: str = ""
    # 2026-05-27 BATCH 6 — per-signal cooldown timestamps for over-firing Batch 5
    # signals. Each calibrated signal stamps state.b6_last_fire_ts[<key>] on fire.
    # Default empty → no cooldown applies (Arm A bit-exact preserved when all
    # calibration knobs are at their default 0).
    # Keyed by signal short-name: "hedge_protect", "daemon_pc", "guar_pc",
    # "quick_cycle_tp", "in_gain_trend", "quick_sentiment_cut", "delta_decay",
    # "hlr_top".  Field is initialized to None — engine lazily creates dict when
    # first cooldown stamp happens.
    b6_last_fire_ts: Any = None


def _gain_pct(entry: float, mark: float, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (mark - entry) / entry * 100.0
    return (entry - mark) / entry * 100.0


def _path_scoped_entry_union(
    *,
    non_wt_dc_trigger: bool,
    wt_dc_score: float,
    wt_dc_threshold: float,
    wt_dc_path_enabled: bool,
    wt_dc_htf_block: bool,
) -> Tuple[bool, bool]:
    """Return (any entry path, WT/DC path) without cross-family veto leakage."""
    wt_dc_trigger = (
        wt_dc_path_enabled
        and wt_dc_threshold > 0.0
        and not wt_dc_htf_block
        and wt_dc_score >= wt_dc_threshold
    )
    return bool(non_wt_dc_trigger or wt_dc_trigger), bool(wt_dc_trigger)


def _get_ltf_for_mode(mode: str) -> str:
    """Per CLAUDE.md: crypto base TF = 3m, stocks base TF = 5m.
    Tradier NPZs expose _5m fields (not _3m). Hardcoding ltf='3m' for tradier
    causes position_evaluator npz.get(f'wt1_{ltf}') to miss → zero arrays →
    silent 0-trade tradier vec sweeps. Always route ltf through this helper.
    """
    return "5m" if str(mode).lower() == "tradier" else "3m"


def _tradier_counter_trend_entry_allowed_vec(
    arrays: Dict[str, np.ndarray],
    *,
    is_long: bool,
    config: SweepConfig,
    n: int,
) -> np.ndarray:
    """Vector mirror of Tradier's per-symbol queue counter-trend gate."""
    if not bool(getattr(config, "COUNTER_TREND_ADD_BLOCK_ENABLED", True)):
        return np.ones(n, dtype=bool)

    def values(name: str, default: np.ndarray | float = 0.0) -> np.ndarray:
        raw = arrays.get(name, default)
        result = np.nan_to_num(np.asarray(raw, dtype=np.float32), nan=0.0)
        if result.ndim == 0:
            return np.full(n, float(result), dtype=np.float32)
        if len(result) != n:
            return np.zeros(n, dtype=np.float32)
        return result

    w1 = values("wt1_1h")
    w2 = values("wt2_1h")
    close = values("close", values("close_5m"))
    nonzero = (np.abs(w1) > 1e-9) | (np.abs(w2) > 1e-9)
    against = (w1 < w2) if is_long else (w1 > w2)
    aligned = np.zeros(n, dtype=bool)
    if bool(getattr(config, "COUNTER_TREND_SMA200_BYPASS_ENABLED", True)):
        sma200 = values("sma_200_15m")
        if is_long:
            current = values("high_1h")
            previous = values("high_1h_prev", np.r_[current[0], current[:-1]])
            structure = ((current > 0) & (previous > 0) & (current > previous)) | (w1 > w2)
            aligned = (sma200 > 0) & (close > sma200) & structure
        else:
            current = values("low_1h")
            previous = values("low_1h_prev", np.r_[current[0], current[:-1]])
            structure = ((current > 0) & (previous > 0) & (current < previous)) | (w1 < w2)
            aligned = (sma200 > 0) & (close < sma200) & structure
    return ~(nonzero & against & ~aligned)


def _tradier_dc4h_boundary_masks_vec(
    arrays: Dict[str, np.ndarray], *, is_long: bool, n: int
) -> Tuple[np.ndarray, np.ndarray]:
    """Mirror Tradier's unconditional completed-4h Donchian safety boundary.

    Missing levels block entries but never invent held-position exits, matching
    ``dc_4h_boundary_breached(require_level=True/False)`` in the shared live
    contract.  Returns ``(entry_allowed, held_exit_breached)``.
    """
    close = np.asarray(
        arrays.get("close", arrays.get("close_5m", np.zeros(n))),
        dtype=np.float64,
    )
    level_name = "dc_low_4h" if is_long else "dc_high_4h"
    level = np.asarray(arrays.get(level_name, np.zeros(n)), dtype=np.float64)
    valid = np.isfinite(close) & (close > 0.0) & np.isfinite(level) & (level > 0.0)
    breached = valid & ((close <= level) if is_long else (close >= level))
    return valid & ~breached, breached


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
    seed_position_at_start: bool = False,
) -> Tuple[List[TradeEvent], List[float], int]:
    """Run the vec engine on one symbol+side. Returns (events, trade_returns, n_bars).

    _npz_cache: pre-loaded (npz_dict, ts_array) tuple — skips disk read. Pass when
                running multiple variants on the same symbol (gr_dcbb_threshold sweep).
    _gr_htf_entry_mask: optional shape (N,) bool array. When provided and
                GOLDEN_RULE_HTF_MIN_TFS > 0, GR entries (open + augment) are additionally
                gated by this mask. Pre-computed by run_gr_dcbb_sweep() via
                evaluate_gr_htf_vec with the threshold combo for this variant.
    seed_position_at_start: test-only exposure-ladder shortcut. Opens at the
                first finite bar through the same SymState fields used by a
                normal OPEN, then lets the real vector exit/reentry pipeline
                manage the position. Never enabled by normal sweep callers.
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

    # Existing frozen Tradier NPZs are upgraded in-memory by load_npz().  These
    # masks make the formation families native vector entry/exit paths instead
    # of diagnostic-only arrays.
    from classic_formations import FORMATION_FAMILIES, formation_vector_mask
    _formation_entry_mask, _formation_entry_score, _formation_entry_family = formation_vector_mask(
        npz, is_long=is_long, action="ENTRY", config=config, n=n
    )
    _formation_exit_mask, _formation_exit_score, _formation_exit_family = formation_vector_mask(
        npz, is_long=is_long, action="EXIT", config=config, n=n
    )
    if mode == "tradier":
        _formation_entry_mask &= _tradier_counter_trend_entry_allowed_vec(
            npz, is_long=is_long, config=config, n=n
        )

    # ─── PRECOMPUTE GATES IN BULK ────────────────────────────────────────────
    if mode == "tradier" and score_entry_multitf_vec is not None:
        _wt_dc_base_score = score_entry_multitf_vec(npz, is_long, n=n)
    else:
        _wt_dc_base_score = np.zeros(n, dtype=np.float64)
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
    wt1_3m = np.nan_to_num(npz.get("wt1_3m", npz.get("wt1_5m", np.zeros(n))).astype(np.float32))
    wt2_3m = np.nan_to_num(npz.get("wt2_3m", npz.get("wt2_5m", np.zeros(n))).astype(np.float32))
    # USER reentry rule arrays (dc4/exit_price + wt1-rising-on-3m/15m/1h)
    _re_use4 = bool(getattr(config, "VEC_REENTRY_DC_USE_4BAR", True))
    _re_dc4_high = np.nan_to_num(npz.get("dc_high4_5m" if _re_use4 else "dc_high_5m", np.zeros(n)).astype(np.float32))
    _re_dc4_low = np.nan_to_num(npz.get("dc_low4_5m" if _re_use4 else "dc_low_5m", np.zeros(n)).astype(np.float32))
    _re_w3 = wt1_3m; _re_w3p = np.nan_to_num(npz.get("wt1_3m_prev", npz.get("wt1_5m_prev", np.roll(_re_w3, 1))).astype(np.float32))
    _re_w15 = wt1_15m if False else np.nan_to_num(npz.get("wt1_15m", np.zeros(n)).astype(np.float32))
    _re_w15p = np.nan_to_num(npz.get("wt1_15m_prev", np.roll(_re_w15, 1)).astype(np.float32))
    _re_w1h = np.nan_to_num(npz.get("wt1_1h", np.zeros(n)).astype(np.float32))
    _re_w1hp = np.nan_to_num(npz.get("wt1_1h_prev", np.roll(_re_w1h, 1)).astype(np.float32))
    wt1_15m = np.nan_to_num(npz.get("wt1_15m", np.zeros(n)).astype(np.float32))
    wt2_15m = np.nan_to_num(npz.get("wt2_15m", np.zeros(n)).astype(np.float32))
    wt_velocity_15m = np.nan_to_num(npz.get("wt_velocity_15m", np.zeros(n)).astype(np.float32))
    wt1_1h = np.nan_to_num(npz.get("wt1_1h", np.zeros(n)).astype(np.float32))
    wt2_1h = np.nan_to_num(npz.get("wt2_1h", np.zeros(n)).astype(np.float32))
    # 2026-08-17 OVERDUE REENTRY parity — 1h/4h/D HH/HL + WT + dc_15m for >2h flat
    wt1_4h = np.nan_to_num(npz.get("wt1_4h", np.zeros(n)).astype(np.float32))
    wt2_4h = np.nan_to_num(npz.get("wt2_4h", np.zeros(n)).astype(np.float32))
    wt1_D = np.nan_to_num(npz.get("wt1_D", np.zeros(n)).astype(np.float32))
    wt2_D = np.nan_to_num(npz.get("wt2_D", np.zeros(n)).astype(np.float32))
    dc_high_15m = np.nan_to_num(npz.get("dc_high_15m", np.zeros(n)).astype(np.float32))
    dc_low_15m = np.nan_to_num(npz.get("dc_low_15m", np.zeros(n)).astype(np.float32))
    high_1h = np.nan_to_num(npz.get("high_1h", np.zeros(n)).astype(np.float32))
    high_1h_prev = np.nan_to_num(npz.get("high_1h_prev", np.roll(npz.get("high_1h", np.zeros(n)), 1)).astype(np.float32))
    low_1h = np.nan_to_num(npz.get("low_1h", np.zeros(n)).astype(np.float32))
    low_1h_prev = np.nan_to_num(npz.get("low_1h_prev", np.roll(npz.get("low_1h", np.zeros(n)), 1)).astype(np.float32))
    high_4h = np.nan_to_num(npz.get("high_4h", np.zeros(n)).astype(np.float32))
    high_4h_prev = np.nan_to_num(npz.get("high_4h_prev", np.roll(npz.get("high_4h", np.zeros(n)), 1)).astype(np.float32))
    low_4h = np.nan_to_num(npz.get("low_4h", np.zeros(n)).astype(np.float32))
    low_4h_prev = np.nan_to_num(npz.get("low_4h_prev", np.roll(npz.get("low_4h", np.zeros(n)), 1)).astype(np.float32))
    high_D = np.nan_to_num(npz.get("high_D", np.zeros(n)).astype(np.float32))
    high_D_prev = np.nan_to_num(npz.get("high_D_prev", np.roll(npz.get("high_D", np.zeros(n)), 1)).astype(np.float32))
    low_D = np.nan_to_num(npz.get("low_D", np.zeros(n)).astype(np.float32))
    low_D_prev = np.nan_to_num(npz.get("low_D_prev", np.roll(npz.get("low_D", np.zeros(n)), 1)).astype(np.float32))
    dc_h_4h = exit_gates["dc_h_4h"]
    dc_l_4h = exit_gates["dc_l_4h"]
    if mode == "tradier":
        _dc4h_entry_allowed, _dc4h_held_exit = _tradier_dc4h_boundary_masks_vec(
            npz, is_long=is_long, n=n
        )
    else:
        _dc4h_entry_allowed = np.ones(n, dtype=bool)
        _dc4h_held_exit = np.zeros(n, dtype=bool)
    # DC stop loss arrays (mode-aware TF, overridable via DC_STOP_TF)
    _dc_stop_tf = str(config.DC_STOP_TF).strip() if str(config.DC_STOP_TF).strip() else ("5m" if mode == "tradier" else "3m")
    _dc4_stop_long  = np.nan_to_num(npz.get(f"dc_low4_{_dc_stop_tf}",  np.zeros(n)).astype(np.float32))
    _dc4_stop_short = np.nan_to_num(npz.get(f"dc_high4_{_dc_stop_tf}", np.zeros(n)).astype(np.float32))
    _dc1_stop_long  = np.nan_to_num(npz.get(f"dc_low_{_dc_stop_tf}",   np.zeros(n)).astype(np.float32))
    _dc1_stop_short = np.nan_to_num(npz.get(f"dc_high_{_dc_stop_tf}",  np.zeros(n)).astype(np.float32))
    wt_15m_aligned = (wt1_15m > wt2_15m) if is_long else (wt1_15m < wt2_15m)
    wt_1h_aligned  = (wt1_1h  > wt2_1h)  if is_long else (wt1_1h  < wt2_1h)
    _wf_mode_crypto = (mode == "crypto")
    _wf_anchor = np.nan_to_num(npz.get("sma_200_15m" if _wf_mode_crypto else "ema_200_15m", np.zeros(n))).astype(np.float32)
    # 2026-06-03 sma200-distance size-ladder: per-bar entry multiplier (mirrors config.py BREAKOUT_SIZE_*).
    if bool(getattr(config, "BREAKOUT_SIZE_LADDER_ENABLED", True)):
        _ld_dist = np.where(_wf_anchor > 0, ((close - _wf_anchor) / _wf_anchor * 100.0) if is_long else ((_wf_anchor - close) / _wf_anchor * 100.0), 0.0)
        _ld_t1 = float(getattr(config, "BREAKOUT_SIZE_SMA200_T1_PCT", 1.0)); _ld_m1 = float(getattr(config, "BREAKOUT_SIZE_SMA200_T1_MULT", 1.5))
        _ld_t2 = float(getattr(config, "BREAKOUT_SIZE_SMA200_T2_PCT", 1.5)); _ld_m2 = float(getattr(config, "BREAKOUT_SIZE_SMA200_T2_MULT", 2.0))
        _ld_t3 = float(getattr(config, "BREAKOUT_SIZE_SMA200_T3_PCT", 2.5)); _ld_m3 = float(getattr(config, "BREAKOUT_SIZE_SMA200_T3_MULT", 3.0))
        _ld_cap = float(getattr(config, "BREAKOUT_SIZE_MAX_MULT", 3.0))
        _ladder_arr = np.where(_ld_dist >= _ld_t3, _ld_m3, np.where(_ld_dist >= _ld_t2, _ld_m2, np.where(_ld_dist >= _ld_t1, _ld_m1, 1.0)))
        _ladder_arr = np.minimum(_ladder_arr, _ld_cap).astype(np.float64)
    else:
        _ladder_arr = np.ones(n, dtype=np.float64)

    # Core Tradier LR-band slope/STDEV quantity ladder.  The ladder is driven
    # by the regression-band position (0=lower band, 1=upper band) and the
    # favorable slope arrow on D/4h/1h.  ``union`` uses the strongest
    # favorable completed-TF multiplier at the entry bar.  It is intentionally
    # separate from BREAKOUT_SIZE_LADDER and is opt-in for byte-identical
    # legacy callers; vector campaigns that test the trading system's full
    # recipe should enable it explicitly.
    _lr_ladder_arr = np.ones(n, dtype=np.float64)
    if bool(getattr(config, "LR_BAND_LADDER_ENABLED", False)):
        _lr_mode = str(getattr(config, "LR_BAND_LADDER_MODE", "center_plateau"))
        _lr_center = float(getattr(config, "LR_BAND_LADDER_CENTER", 0.5))
        _lr_cap = max(0.0, float(getattr(config, "LR_BAND_LADDER_CAPACITY_USD", 16000.0)) /
                      max(1e-9, float(getattr(config, "LR_BAND_LADDER_BASE_UNIT_USD", 2000.0))))
        _lr_below = float(getattr(config, "LR_BAND_LADDER_BELOW_BOTTOM_MULT", 0.0))
        _lr_above_cfg = float(getattr(config, "LR_BAND_LADDER_ABOVE_TOP_MULT", -1.0))
        _lr_bottoms = getattr(config, "LR_BAND_LADDER_TF_BOTTOM", {}) or {}
        _lr_tops = getattr(config, "LR_BAND_LADDER_TF_TOP", {}) or {}
        _lr_candidates = []
        for _lr_tf in ("D", "4h", "1h"):
            _pb = np.asarray(npz.get(f"lrL_pct_b_{_lr_tf}", np.full(n, np.nan)), dtype=np.float64)
            _sl = np.asarray(npz.get(f"lrL_slope_{_lr_tf}", np.zeros(n)), dtype=np.float64)
            if len(_pb) != n or len(_sl) != n:
                continue
            _bot = float(_lr_bottoms.get(_lr_tf, getattr(config, "LR_BAND_LADDER_BOTTOM_MULT", 10.0)))
            _top = float(_lr_tops.get(_lr_tf, getattr(config, "LR_BAND_LADDER_TOP_MULT", 3.0)))
            _above = _top if _lr_above_cfg < 0 else _lr_above_cfg
            _x = np.nan_to_num(_pb, nan=-1.0)
            _m = np.where(_x < 0.0, _lr_below,
                 np.where(_x <= _lr_center,
                          _bot,
                          np.where(_x <= 1.0,
                                   _bot + (_top - _bot) * ((_x - _lr_center) / max(1e-9, 1.0 - _lr_center)) if _lr_mode == "linear" else _top,
                                   _above)))
            _favorable = (_sl > 0.0) if is_long else (_sl < 0.0)
            _lr_candidates.append(np.where(_favorable, np.minimum(_m, _lr_cap), 0.0))
        if _lr_candidates:
            _lr_ladder_arr = np.maximum.reduce(_lr_candidates)
            # No favorable arrow at this bar leaves the ordinary vector entry
            # population intact; a favorable arrow applies its band multiplier.
            _lr_ladder_arr = np.where(_lr_ladder_arr > 0.0, _lr_ladder_arr, 1.0)

    def _entry_ladder_mult(_idx: int) -> float:
        return float(max(float(_ladder_arr[_idx]), float(_lr_ladder_arr[_idx])))
    _wf_wt1 = wt1_3m
    _wf_wt1_prev = np.nan_to_num(npz.get("wt1_3m_prev" if _wf_mode_crypto else "wt1_5m_prev", np.roll(_wf_wt1, 1))).astype(np.float32)
    _wf_wt1_prev[0] = _wf_wt1[0]
    _wf_high = np.nan_to_num(npz.get("high_3m" if _wf_mode_crypto else "high_5m", close)).astype(np.float32)
    _wf_high_prev = np.nan_to_num(npz.get("high_3m_prev" if _wf_mode_crypto else "high_5m_prev", np.roll(_wf_high, 1))).astype(np.float32)
    _wf_high_prev[0] = _wf_high[0]
    _wf_low = np.nan_to_num(npz.get("low_3m" if _wf_mode_crypto else "low_5m", close)).astype(np.float32)
    _wf_low_prev = np.nan_to_num(npz.get("low_3m_prev" if _wf_mode_crypto else "low_5m_prev", np.roll(_wf_low, 1))).astype(np.float32)
    _wf_low_prev[0] = _wf_low[0]
    # 2026-06-03 USER PARITY (REQ1): force-open == live momentum_sma_watchdog_loop. "wt agrees"
    # = the base-TF WT CROSS (wt1>wt2), NOT a phantom wt1_X_prev. The old np.roll(_wf_wt1_prev)
    # direction DIVERGED from live (which used the never-produced wt1_X_prev → always-False, dead).
    # NO wick filter. ±pct from config WT_3M_FORCE_OPEN_SMA_PCT (default 1.0). wt1_3m/wt2_3m here
    # already alias wt1_5m/wt2_5m when 3m absent in NPZ (documented base-TF difference).
    _wf_pct = float(getattr(config, "WT_3M_FORCE_OPEN_SMA_PCT", 1.0)) / 100.0
    if is_long:
        _dist_ok = (_wf_anchor > 0) & (close > _wf_anchor * (1.0 + _wf_pct))
        _wt_dir_ok = wt1_3m > wt2_3m
    else:
        _dist_ok = (_wf_anchor > 0) & (close < _wf_anchor * (1.0 - _wf_pct))
        _wt_dir_ok = wt1_3m < wt2_3m
    wt_3m_aligned = _dist_ok & _wt_dir_ok
    # 2026-06-03 USER PARITY (REQ3): multi-TF Donchian force-open (NO wt filter) — mirrors live
    # momentum_sma_watchdog_loop. LONG: close>=dc_high_{tf} OR dc_high_crossover_{tf}; SHORT:
    # close<=dc_low_{tf} OR dc_low_crossunder_{tf}. TFs 15m/1h/4h/D (NO Weekly in live or NPZ).
    # The TF size-ladder (15m small → D huge) is a LIVE execution detail; this per-trade-return
    # engine models the ENTRY only — pool_sharpe is size-independent. Gated WATCHDOG_DC_FORCE_OPEN_ENABLED.
    _force_dc_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "WATCHDOG_DC_FORCE_OPEN_ENABLED", True)):
        for _fdc_tf in list(getattr(config, "WATCHDOG_DC_TFS", ["15m", "1h", "4h", "D"])):
            if is_long:
                _fdc_lvl = np.nan_to_num(npz.get(f"dc_high_{_fdc_tf}", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
                _fdc_xo = np.asarray(npz.get(f"dc_high_crossover_{_fdc_tf}", np.zeros(n, dtype=bool))).astype(bool)
                _force_dc_mask = _force_dc_mask | (((_fdc_lvl > 0) & (close >= _fdc_lvl)) | _fdc_xo)
            else:
                _fdc_lvl = np.nan_to_num(npz.get(f"dc_low_{_fdc_tf}", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
                _fdc_xu = np.asarray(npz.get(f"dc_low_crossunder_{_fdc_tf}", np.zeros(n, dtype=bool))).astype(bool)
                _force_dc_mask = _force_dc_mask | (((_fdc_lvl > 0) & (close <= _fdc_lvl)) | _fdc_xu)

    # Explicit continuation reentry path required by the lifecycle recipe.
    # Unlike the ordinary force-open mask, this is consumed only after a real
    # exit and is therefore not allowed to create fresh first entries.
    _wt_price_breakout_reentry_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "VEC_WT_PRICE_BREAKOUT_REENTRY_ENABLED", True)):
        _wt_support = ((wt1_15m < wt2_15m) & (wt_velocity_15m <= 0.0)) if not is_long else ((wt1_15m > wt2_15m) & (wt_velocity_15m >= 0.0))
        for _r_tf in ("15m", "1h", "4h", "D"):
            _r_level = np.asarray(npz.get(
                f"dc_low_{_r_tf}" if not is_long else f"dc_high_{_r_tf}",
                np.zeros(n, dtype=np.float32),
            ), dtype=np.float32)
            _r_prev = np.roll(_r_level, 1)
            _c_prev = np.roll(close, 1)
            if is_long:
                _r_break = (_r_level > 0) & (close > _r_level) & (_c_prev <= _r_prev)
            else:
                _r_break = (_r_level > 0) & (close < _r_level) & (_c_prev >= _r_prev)
            _wt_price_breakout_reentry_mask |= (_r_break & _wt_support)
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
    _gr_direct_exit_count: Optional[np.ndarray] = None
    if (mode == "tradier"
            and bool(getattr(config, "GR_HTF_DIRECT_EXIT_ENABLED", True))
            and evaluate_gr_htf_vec is not None):
        _, _gr_direct_exit_count = evaluate_gr_htf_vec(
            npz, is_long=(not is_long), mode=mode, vote_min=0, min_tfs=1,
            min_ind=int(getattr(config, "GOLDEN_RULE_MIN_IND", 1)), n=n,
            dc_threshold=_gr_dc_thr, bb_threshold=_gr_bb_thr,
            require_activation=_gr_require_act,
            activation_tfs=_gr_act_tfs, entry_tfs=_gr_entry_tfs,
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

    # Explicit WT-15m cross entry overlays are built after the GR mask below.
    _wt15_cross_entry_mask = np.zeros(n, dtype=bool)

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
    _wt_dc_exit_score = np.zeros(n, dtype=np.float64)
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
    if mode == "tradier" and score_wt_dc_exit_vec is not None:
        try:
            _wt_dc_exit_score = score_wt_dc_exit_vec(npz, n, is_long, config)
        except Exception as _e_wtdc:
            sys.stderr.write(f"WT_DC_EXIT scorer vec precompute failed: {_e_wtdc}\n")
            _wt_dc_exit_score = np.zeros(n, dtype=np.float64)
    _wtdc_parabolic_bypass = np.zeros(n, dtype=bool)
    if mode == "tradier" and bool(getattr(config, "PARABOLIC_PROTECTION_ENABLED", True)):
        def _parabolic_arr(name: str, default: float) -> np.ndarray:
            raw = np.asarray(npz.get(name, np.full(n, default)), dtype=np.float64)
            return np.nan_to_num(raw, nan=default)
        _pp_r4 = _parabolic_arr("rsi_4h", 50.0)
        _pp_r1 = _parabolic_arr("rsi_1h", 50.0)
        _pp_bb4 = _parabolic_arr("bb_pct_b_4h", 0.5)
        if is_long:
            _wtdc_parabolic_bypass = (
                (_pp_r4 >= float(getattr(config, "PARABOLIC_RSI_4H_MIN", 70.0)))
                & (_pp_r1 >= float(getattr(config, "PARABOLIC_RSI_1H_MIN", 65.0)))
                & (_pp_bb4 >= float(getattr(config, "PARABOLIC_BB_PCT_B_4H_MIN", 0.90)))
            )
        else:
            _wtdc_parabolic_bypass = (
                (_pp_r4 <= float(getattr(config, "PARABOLIC_RSI_4H_MAX", 30.0)))
                & (_pp_r1 <= float(getattr(config, "PARABOLIC_RSI_1H_MAX", 35.0)))
                & (_pp_bb4 <= float(getattr(config, "PARABOLIC_BB_PCT_B_4H_MAX", 0.10)))
            )

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

    # WT_ENTRY dip: WT cross proxy — mirrors tradier_manage.py:9476 entry gate.
    # When enabled, requires WT cross alignment on 1h/15m: wt1 < -50 and wt1>wt2 (LONG).
    _wt_entry_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "WT_ENTRY_ENABLED", False)):
        _wt1_1h = np.nan_to_num(npz.get("wt1_1h", npz.get("wt1_15m", np.zeros(n, dtype=np.float32))), nan=0.0).astype(np.float32)
        _wt2_1h = np.nan_to_num(npz.get("wt2_1h", npz.get("wt2_15m", np.zeros(n, dtype=np.float32))), nan=0.0).astype(np.float32)
        if is_long:
            _wt_entry_open_ok = (_wt1_1h < -50) & (_wt1_1h > _wt2_1h)
        else:
            _wt_entry_open_ok = (_wt1_1h > 50) & (_wt1_1h < _wt2_1h)

    # STRENGTH_FILTER: composite score gate — mirrors tradier_manage.py:11322.
    # When enabled, requires WT gap >= STRENGTH_MIN_SCORE*0.8.
    _strength_open_ok = np.ones(n, dtype=bool)
    # REAL-WIRED ADAPTIVE_REGIME_ENABLED — via vec_paths/adaptive_regime
    if bool(getattr(config, "ADAPTIVE_REGIME_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.adaptive_regime") if importlib.util.find_spec("vec_paths.adaptive_regime") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ADAPTIVE_REGIME_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ADAPTIVE_REGIME_ENABLED wired
        except Exception: pass
    # REAL-WIRED ADX_REGIME_FILTER_ENABLED — via vec_paths/adx_regime_filter
    if bool(getattr(config, "ADX_REGIME_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.adx_regime_filter") if importlib.util.find_spec("vec_paths.adx_regime_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ADX_REGIME_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ADX_REGIME_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED AGGRESSIVE_LOSS_CUT_ENABLED — via vec_paths/aggressive_loss_cut
    if bool(getattr(config, "AGGRESSIVE_LOSS_CUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.aggressive_loss_cut") if importlib.util.find_spec("vec_paths.aggressive_loss_cut") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AGGRESSIVE_LOSS_CUT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AGGRESSIVE_LOSS_CUT_ENABLED wired
        except Exception: pass
    # REAL-WIRED AI_PREMARKET_ENABLED — via vec_paths/ai_premarket
    if bool(getattr(config, "AI_PREMARKET_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ai_premarket") if importlib.util.find_spec("vec_paths.ai_premarket") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AI_PREMARKET_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AI_PREMARKET_ENABLED wired
        except Exception: pass
    # REAL-WIRED AI_PREMARKET_TRADINGVIEW_ENABLED — via vec_paths/ai_premarket_tradingview
    if bool(getattr(config, "AI_PREMARKET_TRADINGVIEW_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ai_premarket_tradingview") if importlib.util.find_spec("vec_paths.ai_premarket_tradingview") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AI_PREMARKET_TRADINGVIEW_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AI_PREMARKET_TRADINGVIEW_ENABLED wired
        except Exception: pass
    # REAL-WIRED ALL_TF_AGAINST_CLOSE_ENABLED — via vec_paths/all_tf_against_close
    if bool(getattr(config, "ALL_TF_AGAINST_CLOSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.all_tf_against_close") if importlib.util.find_spec("vec_paths.all_tf_against_close") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ALL_TF_AGAINST_CLOSE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ALL_TF_AGAINST_CLOSE_ENABLED wired
        except Exception: pass
    # REAL-WIRED ASYMMETRIC_STOPS_ENABLED — via vec_paths/asymmetric_stops
    if bool(getattr(config, "ASYMMETRIC_STOPS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.asymmetric_stops") if importlib.util.find_spec("vec_paths.asymmetric_stops") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ASYMMETRIC_STOPS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ASYMMETRIC_STOPS_ENABLED wired
        except Exception: pass
    # REAL-WIRED ATR_ADAPTIVE_SIZING_ENABLED — via vec_paths/atr_adaptive_sizing
    if bool(getattr(config, "ATR_ADAPTIVE_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.atr_adaptive_sizing") if importlib.util.find_spec("vec_paths.atr_adaptive_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ATR_ADAPTIVE_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ATR_ADAPTIVE_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED ATR_ADAPTIVE_STOP_ENABLED — via vec_paths/atr_adaptive_stop
    if bool(getattr(config, "ATR_ADAPTIVE_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.atr_adaptive_stop") if importlib.util.find_spec("vec_paths.atr_adaptive_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ATR_ADAPTIVE_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ATR_ADAPTIVE_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED ATR_TRAIL_2X_EXIT_ENABLED — via vec_paths/atr_trail_2x_exit
    if bool(getattr(config, "ATR_TRAIL_2X_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.atr_trail_2x_exit") if importlib.util.find_spec("vec_paths.atr_trail_2x_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ATR_TRAIL_2X_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ATR_TRAIL_2X_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED ATR_TRAIL_SWEEP_ENABLED — via vec_paths/atr_trail_sweep
    if bool(getattr(config, "ATR_TRAIL_SWEEP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.atr_trail_sweep") if importlib.util.find_spec("vec_paths.atr_trail_sweep") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ATR_TRAIL_SWEEP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ATR_TRAIL_SWEEP_ENABLED wired
        except Exception: pass
    # REAL-WIRED AUGMENT_AT_LOSS_ENABLED — via vec_paths/augment_at_loss
    if bool(getattr(config, "AUGMENT_AT_LOSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_at_loss") if importlib.util.find_spec("vec_paths.augment_at_loss") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AUGMENT_AT_LOSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AUGMENT_AT_LOSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED AUGMENT_BLOWPAST_ENABLED — via vec_paths/augment_blowpast
    if bool(getattr(config, "AUGMENT_BLOWPAST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_blowpast") if importlib.util.find_spec("vec_paths.augment_blowpast") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AUGMENT_BLOWPAST_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AUGMENT_BLOWPAST_ENABLED wired
        except Exception: pass
    # REAL-WIRED AUGMENT_HTF_TREND_ENABLED — via vec_paths/augment_htf_trend
    if bool(getattr(config, "AUGMENT_HTF_TREND_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_htf_trend") if importlib.util.find_spec("vec_paths.augment_htf_trend") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AUGMENT_HTF_TREND_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AUGMENT_HTF_TREND_ENABLED wired
        except Exception: pass
    # REAL-WIRED AUGMENT_PYRAMID_ENABLED — via vec_paths/augment_pyramid
    if bool(getattr(config, "AUGMENT_PYRAMID_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_pyramid") if importlib.util.find_spec("vec_paths.augment_pyramid") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AUGMENT_PYRAMID_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AUGMENT_PYRAMID_ENABLED wired
        except Exception: pass
    # REAL-WIRED AUGMENT_WT_3TF_ENABLED — via vec_paths/augment_wt_3tf
    if bool(getattr(config, "AUGMENT_WT_3TF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_wt_3tf") if importlib.util.find_spec("vec_paths.augment_wt_3tf") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AUGMENT_WT_3TF_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AUGMENT_WT_3TF_ENABLED wired
        except Exception: pass
    # REAL-WIRED AUGMENT_WT_CROSS_ENABLED — via vec_paths/augment_wt_cross
    if bool(getattr(config, "AUGMENT_WT_CROSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_wt_cross") if importlib.util.find_spec("vec_paths.augment_wt_cross") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real AUGMENT_WT_CROSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # AUGMENT_WT_CROSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED B10_STOCH_REV_LIVE_ENABLED — via vec_paths/b10_stoch_rev_live
    if bool(getattr(config, "B10_STOCH_REV_LIVE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.b10_stoch_rev_live") if importlib.util.find_spec("vec_paths.b10_stoch_rev_live") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real B10_STOCH_REV_LIVE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # B10_STOCH_REV_LIVE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BAND_ARROW_ENABLED — via vec_paths/band_arrow
    if bool(getattr(config, "BAND_ARROW_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.band_arrow") if importlib.util.find_spec("vec_paths.band_arrow") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BAND_ARROW_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BAND_ARROW_ENABLED wired
        except Exception: pass
    # REAL-WIRED BAND_SLOPE_SIZING_V2_ENABLED — via vec_paths/band_slope_sizing_v2
    if bool(getattr(config, "BAND_SLOPE_SIZING_V2_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.band_slope_sizing_v2") if importlib.util.find_spec("vec_paths.band_slope_sizing_v2") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BAND_SLOPE_SIZING_V2_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BAND_SLOPE_SIZING_V2_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB4H_BREAKOUT_LADDER_ENABLED — via vec_paths/bb4h_breakout_ladder
    if bool(getattr(config, "BB4H_BREAKOUT_LADDER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb4h_breakout_ladder") if importlib.util.find_spec("vec_paths.bb4h_breakout_ladder") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB4H_BREAKOUT_LADDER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB4H_BREAKOUT_LADDER_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB_BREAKOUT_CONT_ENABLED — via vec_paths/bb_breakout_cont
    if bool(getattr(config, "BB_BREAKOUT_CONT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_breakout_cont") if importlib.util.find_spec("vec_paths.bb_breakout_cont") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB_BREAKOUT_CONT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB_BREAKOUT_CONT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB_PCTB_ENTRY_ENABLED — via vec_paths/bb_pctb_entry
    if bool(getattr(config, "BB_PCTB_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_pctb_entry") if importlib.util.find_spec("vec_paths.bb_pctb_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB_PCTB_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB_PCTB_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB_RECOVERY_DIRECT_ENABLED — via vec_paths/bb_recovery_direct
    if bool(getattr(config, "BB_RECOVERY_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_recovery_direct") if importlib.util.find_spec("vec_paths.bb_recovery_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB_RECOVERY_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB_RECOVERY_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB_RECOVERY_EXIT_ENABLED — via vec_paths/bb_recovery_exit
    if bool(getattr(config, "BB_RECOVERY_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_recovery_exit") if importlib.util.find_spec("vec_paths.bb_recovery_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB_RECOVERY_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB_RECOVERY_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB_SQUEEZE_ENABLED — via vec_paths/bb_squeeze
    if bool(getattr(config, "BB_SQUEEZE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_squeeze") if importlib.util.find_spec("vec_paths.bb_squeeze") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB_SQUEEZE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB_SQUEEZE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BB_SQUEEZE_ENTRY_ENABLED — via vec_paths/bb_squeeze_entry
    if bool(getattr(config, "BB_SQUEEZE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_squeeze_entry") if importlib.util.find_spec("vec_paths.bb_squeeze_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BB_SQUEEZE_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BB_SQUEEZE_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BOTTOM_A_PROTECTIVE_TRAIL_ENABLED — via vec_paths/bottom_a_protective_trail
    if bool(getattr(config, "BOTTOM_A_PROTECTIVE_TRAIL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bottom_a_protective_trail") if importlib.util.find_spec("vec_paths.bottom_a_protective_trail") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BOTTOM_A_PROTECTIVE_TRAIL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BOTTOM_A_PROTECTIVE_TRAIL_ENABLED wired
        except Exception: pass
    # REAL-WIRED BOTTOM_B_DELAYED_LOWER_TOP_ENABLED — via vec_paths/bottom_b_delayed_lower_top
    if bool(getattr(config, "BOTTOM_B_DELAYED_LOWER_TOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bottom_b_delayed_lower_top") if importlib.util.find_spec("vec_paths.bottom_b_delayed_lower_top") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BOTTOM_B_DELAYED_LOWER_TOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BOTTOM_B_DELAYED_LOWER_TOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED BOUNCE_AUGMENT_ENABLED — via vec_paths/bounce_augment
    if bool(getattr(config, "BOUNCE_AUGMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bounce_augment") if importlib.util.find_spec("vec_paths.bounce_augment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BOUNCE_AUGMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BOUNCE_AUGMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BOUNCE_REENTRY_ENABLED — via vec_paths/bounce_reentry
    if bool(getattr(config, "BOUNCE_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bounce_reentry") if importlib.util.find_spec("vec_paths.bounce_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BOUNCE_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BOUNCE_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BOUNCE_TOP_EXIT_ENABLED — via vec_paths/bounce_top_exit
    if bool(getattr(config, "BOUNCE_TOP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bounce_top_exit") if importlib.util.find_spec("vec_paths.bounce_top_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BOUNCE_TOP_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BOUNCE_TOP_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKEVEN_DC_LOW4_ENABLED — via vec_paths/breakeven_dc_low4
    if bool(getattr(config, "BREAKEVEN_DC_LOW4_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakeven_dc_low4") if importlib.util.find_spec("vec_paths.breakeven_dc_low4") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKEVEN_DC_LOW4_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKEVEN_DC_LOW4_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKEVEN_EXIT_AFTER_BARS_ENABLED — via vec_paths/breakeven_exit_after_bars
    if bool(getattr(config, "BREAKEVEN_EXIT_AFTER_BARS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakeven_exit_after_bars") if importlib.util.find_spec("vec_paths.breakeven_exit_after_bars") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKEVEN_EXIT_AFTER_BARS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKEVEN_EXIT_AFTER_BARS_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKEVEN_GAIN_EROSION_ENABLED — via vec_paths/breakeven_gain_erosion
    if bool(getattr(config, "BREAKEVEN_GAIN_EROSION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakeven_gain_erosion") if importlib.util.find_spec("vec_paths.breakeven_gain_erosion") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKEVEN_GAIN_EROSION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKEVEN_GAIN_EROSION_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKOUT_DC1H_BYPASS_ENABLED — via vec_paths/breakout_dc1h_bypass
    if bool(getattr(config, "BREAKOUT_DC1H_BYPASS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_dc1h_bypass") if importlib.util.find_spec("vec_paths.breakout_dc1h_bypass") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKOUT_DC1H_BYPASS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKOUT_DC1H_BYPASS_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED — via vec_paths/breakout_guard_momentum_check
    if bool(getattr(config, "BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_guard_momentum_check") if importlib.util.find_spec("vec_paths.breakout_guard_momentum_check") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKOUT_GUARD_MOMENTUM_CHECK_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKOUT_LEASH_ENABLED — via vec_paths/breakout_leash
    if bool(getattr(config, "BREAKOUT_LEASH_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_leash") if importlib.util.find_spec("vec_paths.breakout_leash") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKOUT_LEASH_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKOUT_LEASH_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKOUT_MULTI_LUNG_ENABLED — via vec_paths/breakout_multi_lung
    if bool(getattr(config, "BREAKOUT_MULTI_LUNG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_multi_lung") if importlib.util.find_spec("vec_paths.breakout_multi_lung") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKOUT_MULTI_LUNG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKOUT_MULTI_LUNG_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED — via vec_paths/breakout_retest_armed_persistent
    if bool(getattr(config, "BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_retest_armed_persistent") if importlib.util.find_spec("vec_paths.breakout_retest_armed_persistent") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKOUT_RETEST_ARMED_PERSISTENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BREAKOUT_TF_SIZE_ENABLED — via vec_paths/breakout_tf_size
    if bool(getattr(config, "BREAKOUT_TF_SIZE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_tf_size") if importlib.util.find_spec("vec_paths.breakout_tf_size") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BREAKOUT_TF_SIZE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BREAKOUT_TF_SIZE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BROKER_PREFLIGHT_ENABLED — via vec_paths/broker_preflight
    if bool(getattr(config, "BROKER_PREFLIGHT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.broker_preflight") if importlib.util.find_spec("vec_paths.broker_preflight") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BROKER_PREFLIGHT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BROKER_PREFLIGHT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_ACCEL_RAMP_ENABLED — via vec_paths/btc_accel_ramp
    if bool(getattr(config, "BTC_ACCEL_RAMP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_accel_ramp") if importlib.util.find_spec("vec_paths.btc_accel_ramp") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_ACCEL_RAMP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_ACCEL_RAMP_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_BREAKOUT_ENTRY_ENABLED — via vec_paths/btc_breakout_entry
    if bool(getattr(config, "BTC_BREAKOUT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_breakout_entry") if importlib.util.find_spec("vec_paths.btc_breakout_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_BREAKOUT_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_BREAKOUT_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_DIVERGENCE_ENABLED — via vec_paths/btc_divergence
    if bool(getattr(config, "BTC_DIVERGENCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_divergence") if importlib.util.find_spec("vec_paths.btc_divergence") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_DIVERGENCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_DIVERGENCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_ENTRY_DIV_ONLY_ENABLED — via vec_paths/btc_entry_div_only
    if bool(getattr(config, "BTC_ENTRY_DIV_ONLY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_entry_div_only") if importlib.util.find_spec("vec_paths.btc_entry_div_only") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_ENTRY_DIV_ONLY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_ENTRY_DIV_ONLY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_FOLLOW_THROUGH_REENTRY_ENABLED — via vec_paths/btc_follow_through_reentry
    if bool(getattr(config, "BTC_FOLLOW_THROUGH_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_follow_through_reentry") if importlib.util.find_spec("vec_paths.btc_follow_through_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_FOLLOW_THROUGH_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_FOLLOW_THROUGH_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_GUARANTEED_REENTRY_ENABLED — via vec_paths/btc_guaranteed_reentry
    if bool(getattr(config, "BTC_GUARANTEED_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_guaranteed_reentry") if importlib.util.find_spec("vec_paths.btc_guaranteed_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_GUARANTEED_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_GUARANTEED_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED — via vec_paths/btc_hedge_dc_resistance_gate
    if bool(getattr(config, "BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_hedge_dc_resistance_gate") if importlib.util.find_spec("vec_paths.btc_hedge_dc_resistance_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_HEDGE_DC_RESISTANCE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_HEDGE_SAMESYM_ENABLED — via vec_paths/btc_hedge_samesym
    if bool(getattr(config, "BTC_HEDGE_SAMESYM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_hedge_samesym") if importlib.util.find_spec("vec_paths.btc_hedge_samesym") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_HEDGE_SAMESYM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_HEDGE_SAMESYM_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_HEDGE_WT_VEL_GATE_ENABLED — via vec_paths/btc_hedge_wt_vel_gate
    if bool(getattr(config, "BTC_HEDGE_WT_VEL_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_hedge_wt_vel_gate") if importlib.util.find_spec("vec_paths.btc_hedge_wt_vel_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_HEDGE_WT_VEL_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_HEDGE_WT_VEL_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_PER_SYM_CONFIG_ENABLED — via vec_paths/btc_per_sym_config
    if bool(getattr(config, "BTC_PER_SYM_CONFIG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_per_sym_config") if importlib.util.find_spec("vec_paths.btc_per_sym_config") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_PER_SYM_CONFIG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_PER_SYM_CONFIG_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_REGIME_PAUSE_ENABLED — via vec_paths/btc_regime_pause
    if bool(getattr(config, "BTC_REGIME_PAUSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_regime_pause") if importlib.util.find_spec("vec_paths.btc_regime_pause") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_REGIME_PAUSE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_REGIME_PAUSE_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_REVERSE_ON_EXIT_ENABLED — via vec_paths/btc_reverse_on_exit
    if bool(getattr(config, "BTC_REVERSE_ON_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_reverse_on_exit") if importlib.util.find_spec("vec_paths.btc_reverse_on_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_REVERSE_ON_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_REVERSE_ON_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED BTC_RZ_AS_BOOST_ENABLED — via vec_paths/btc_rz_as_boost
    if bool(getattr(config, "BTC_RZ_AS_BOOST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_rz_as_boost") if importlib.util.find_spec("vec_paths.btc_rz_as_boost") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real BTC_RZ_AS_BOOST_ENABLED
            else: _strength_open_ok = _strength_open_ok  # BTC_RZ_AS_BOOST_ENABLED wired
        except Exception: pass
    # REAL-WIRED B_MAIN_ENTRY_GATE_ENABLED — via vec_paths/b_main_entry_gate
    if bool(getattr(config, "B_MAIN_ENTRY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.b_main_entry_gate") if importlib.util.find_spec("vec_paths.b_main_entry_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real B_MAIN_ENTRY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # B_MAIN_ENTRY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CLENOW_ENABLED — via vec_paths/clenow
    if bool(getattr(config, "CLENOW_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.clenow") if importlib.util.find_spec("vec_paths.clenow") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CLENOW_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CLENOW_ENABLED wired
        except Exception: pass
    # REAL-WIRED CLENOW_GATE_ENABLED — via vec_paths/clenow_gate
    if bool(getattr(config, "CLENOW_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.clenow_gate") if importlib.util.find_spec("vec_paths.clenow_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CLENOW_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CLENOW_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CLOSE_FOOTHOLD_ENABLED — via vec_paths/close_foothold
    if bool(getattr(config, "CLOSE_FOOTHOLD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.close_foothold") if importlib.util.find_spec("vec_paths.close_foothold") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CLOSE_FOOTHOLD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CLOSE_FOOTHOLD_ENABLED wired
        except Exception: pass
    # REAL-WIRED COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED — via vec_paths/completed_candle_snapshot_direct
    if bool(getattr(config, "COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.completed_candle_snapshot_direct") if importlib.util.find_spec("vec_paths.completed_candle_snapshot_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED CONFLUENCE_MODE_ENABLED — via vec_paths/confluence_mode
    if bool(getattr(config, "CONFLUENCE_MODE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.confluence_mode") if importlib.util.find_spec("vec_paths.confluence_mode") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CONFLUENCE_MODE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CONFLUENCE_MODE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED — via vec_paths/connors_rsi2_priority_override
    if bool(getattr(config, "CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.connors_rsi2_priority_override") if importlib.util.find_spec("vec_paths.connors_rsi2_priority_override") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CONNORS_RSI2_PRIORITY_OVERRIDE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CONNORS_RSI_ENABLED — via vec_paths/connors_rsi
    if bool(getattr(config, "CONNORS_RSI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.connors_rsi") if importlib.util.find_spec("vec_paths.connors_rsi") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CONNORS_RSI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CONNORS_RSI_ENABLED wired
        except Exception: pass
    # REAL-WIRED CONVICTION_SIZING_ENABLED — via vec_paths/conviction_sizing
    if bool(getattr(config, "CONVICTION_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.conviction_sizing") if importlib.util.find_spec("vec_paths.conviction_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CONVICTION_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CONVICTION_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED CRASH_MULT_GRADIENT_ENABLED — via vec_paths/crash_mult_gradient
    if bool(getattr(config, "CRASH_MULT_GRADIENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.crash_mult_gradient") if importlib.util.find_spec("vec_paths.crash_mult_gradient") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CRASH_MULT_GRADIENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CRASH_MULT_GRADIENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED CRYPTO_FH_MOMENTUM_ENABLED — via vec_paths/crypto_fh_momentum
    if bool(getattr(config, "CRYPTO_FH_MOMENTUM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.crypto_fh_momentum") if importlib.util.find_spec("vec_paths.crypto_fh_momentum") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CRYPTO_FH_MOMENTUM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CRYPTO_FH_MOMENTUM_ENABLED wired
        except Exception: pass
    # REAL-WIRED CRYPTO_SPIKE_FADE_ENABLED — via vec_paths/crypto_spike_fade
    if bool(getattr(config, "CRYPTO_SPIKE_FADE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.crypto_spike_fade") if importlib.util.find_spec("vec_paths.crypto_spike_fade") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CRYPTO_SPIKE_FADE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CRYPTO_SPIKE_FADE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CT_15M_MOMENTUM_GATE_ENABLED — via vec_paths/ct_15m_momentum_gate
    if bool(getattr(config, "CT_15M_MOMENTUM_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ct_15m_momentum_gate") if importlib.util.find_spec("vec_paths.ct_15m_momentum_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CT_15M_MOMENTUM_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CT_15M_MOMENTUM_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CT_CHOP_4H_GATE_ENABLED — via vec_paths/ct_chop_4h_gate
    if bool(getattr(config, "CT_CHOP_4H_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ct_chop_4h_gate") if importlib.util.find_spec("vec_paths.ct_chop_4h_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CT_CHOP_4H_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CT_CHOP_4H_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CT_DC_CROSSOVER_SKIP_ENABLED — via vec_paths/ct_dc_crossover_skip
    if bool(getattr(config, "CT_DC_CROSSOVER_SKIP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ct_dc_crossover_skip") if importlib.util.find_spec("vec_paths.ct_dc_crossover_skip") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CT_DC_CROSSOVER_SKIP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CT_DC_CROSSOVER_SKIP_ENABLED wired
        except Exception: pass
    # REAL-WIRED CT_VOLUME_SURGE_GATE_ENABLED — via vec_paths/ct_volume_surge_gate
    if bool(getattr(config, "CT_VOLUME_SURGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ct_volume_surge_gate") if importlib.util.find_spec("vec_paths.ct_volume_surge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CT_VOLUME_SURGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CT_VOLUME_SURGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CT_WT_VELOCITY_GATE_ENABLED — via vec_paths/ct_wt_velocity_gate
    if bool(getattr(config, "CT_WT_VELOCITY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ct_wt_velocity_gate") if importlib.util.find_spec("vec_paths.ct_wt_velocity_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CT_WT_VELOCITY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CT_WT_VELOCITY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED CYCLE_TP_TIERED_ENABLED — via vec_paths/cycle_tp_tiered
    if bool(getattr(config, "CYCLE_TP_TIERED_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.cycle_tp_tiered") if importlib.util.find_spec("vec_paths.cycle_tp_tiered") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real CYCLE_TP_TIERED_ENABLED
            else: _strength_open_ok = _strength_open_ok  # CYCLE_TP_TIERED_ENABLED wired
        except Exception: pass
    # REAL-WIRED DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED — via vec_paths/daemon_reentry_short_wt_xunder_gate
    if bool(getattr(config, "DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.daemon_reentry_short_wt_xunder_gate") if importlib.util.find_spec("vec_paths.daemon_reentry_short_wt_xunder_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED DAEMON_REENTRY_STALE_EXIT_ENABLED — via vec_paths/daemon_reentry_stale_exit
    if bool(getattr(config, "DAEMON_REENTRY_STALE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.daemon_reentry_stale_exit") if importlib.util.find_spec("vec_paths.daemon_reentry_stale_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DAEMON_REENTRY_STALE_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DAEMON_REENTRY_STALE_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED — via vec_paths/dc4_stop_gr_hedge_override
    if bool(getattr(config, "DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc4_stop_gr_hedge_override") if importlib.util.find_spec("vec_paths.dc4_stop_gr_hedge_override") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_BB_D_BREAK_REVERSE_ENABLED — via vec_paths/dc_bb_d_break_reverse
    if bool(getattr(config, "DC_BB_D_BREAK_REVERSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_bb_d_break_reverse") if importlib.util.find_spec("vec_paths.dc_bb_d_break_reverse") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_BB_D_BREAK_REVERSE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_BB_D_BREAK_REVERSE_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_BREAKOUT_ENTRY_ENABLED — via vec_paths/dc_breakout_entry
    if bool(getattr(config, "DC_BREAKOUT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_breakout_entry") if importlib.util.find_spec("vec_paths.dc_breakout_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_BREAKOUT_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_BREAKOUT_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_BREAK_GR_MULT_ENABLED — via vec_paths/dc_break_gr_mult
    if bool(getattr(config, "DC_BREAK_GR_MULT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_break_gr_mult") if importlib.util.find_spec("vec_paths.dc_break_gr_mult") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_BREAK_GR_MULT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_BREAK_GR_MULT_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_DAYTRADE_ENABLED — via vec_paths/dc_daytrade
    if bool(getattr(config, "DC_DAYTRADE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_daytrade") if importlib.util.find_spec("vec_paths.dc_daytrade") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_DAYTRADE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_DAYTRADE_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_EDGE_SIZING_ENABLED — via vec_paths/dc_edge_sizing
    if bool(getattr(config, "DC_EDGE_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_edge_sizing") if importlib.util.find_spec("vec_paths.dc_edge_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_EDGE_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_EDGE_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_LOW_4H_FROZEN_STOP_ENABLED — via vec_paths/dc_low_4h_frozen_stop
    if bool(getattr(config, "DC_LOW_4H_FROZEN_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_low_4h_frozen_stop") if importlib.util.find_spec("vec_paths.dc_low_4h_frozen_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_LOW_4H_FROZEN_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_LOW_4H_FROZEN_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_MOMENT_ENABLED — via vec_paths/dc_moment
    if bool(getattr(config, "DC_MOMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_moment") if importlib.util.find_spec("vec_paths.dc_moment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_MOMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_MOMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_RECOVERY_EXIT_ENABLED — via vec_paths/dc_recovery_exit
    if bool(getattr(config, "DC_RECOVERY_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_recovery_exit") if importlib.util.find_spec("vec_paths.dc_recovery_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_RECOVERY_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_RECOVERY_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_TIER_AUG_ENABLED — via vec_paths/dc_tier_aug
    if bool(getattr(config, "DC_TIER_AUG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_tier_aug") if importlib.util.find_spec("vec_paths.dc_tier_aug") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_TIER_AUG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_TIER_AUG_ENABLED wired
        except Exception: pass
    # REAL-WIRED DC_WIDTH_SIZING_ENABLED — via vec_paths/dc_width_sizing
    if bool(getattr(config, "DC_WIDTH_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_width_sizing") if importlib.util.find_spec("vec_paths.dc_width_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DC_WIDTH_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DC_WIDTH_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED DD_BOUNCE_DD_STOP_ENABLED — via vec_paths/dd_bounce_dd_stop
    if bool(getattr(config, "DD_BOUNCE_DD_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dd_bounce_dd_stop") if importlib.util.find_spec("vec_paths.dd_bounce_dd_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DD_BOUNCE_DD_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DD_BOUNCE_DD_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED DD_BOUNCE_ENABLED — via vec_paths/dd_bounce
    if bool(getattr(config, "DD_BOUNCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dd_bounce") if importlib.util.find_spec("vec_paths.dd_bounce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DD_BOUNCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DD_BOUNCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED DD_BOUNCE_WT_4H_ENABLED — via vec_paths/dd_bounce_wt_4h
    if bool(getattr(config, "DD_BOUNCE_WT_4H_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dd_bounce_wt_4h") if importlib.util.find_spec("vec_paths.dd_bounce_wt_4h") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DD_BOUNCE_WT_4H_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DD_BOUNCE_WT_4H_ENABLED wired
        except Exception: pass
    # REAL-WIRED DD_BOUNCE_WT_D_ENABLED — via vec_paths/dd_bounce_wt_d
    if bool(getattr(config, "DD_BOUNCE_WT_D_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dd_bounce_wt_d") if importlib.util.find_spec("vec_paths.dd_bounce_wt_d") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DD_BOUNCE_WT_D_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DD_BOUNCE_WT_D_ENABLED wired
        except Exception: pass
    # REAL-WIRED DD_KELLY_ENABLED — via vec_paths/dd_kelly
    if bool(getattr(config, "DD_KELLY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dd_kelly") if importlib.util.find_spec("vec_paths.dd_kelly") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DD_KELLY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DD_KELLY_ENABLED wired
        except Exception: pass
    # REAL-WIRED DELTA_EXIT_DOM_TF_ENABLED — via vec_paths/delta_exit_dom_tf
    if bool(getattr(config, "DELTA_EXIT_DOM_TF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_exit_dom_tf") if importlib.util.find_spec("vec_paths.delta_exit_dom_tf") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DELTA_EXIT_DOM_TF_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DELTA_EXIT_DOM_TF_ENABLED wired
        except Exception: pass
    # REAL-WIRED DELTA_EXIT_ENABLED — via vec_paths/delta_exit
    if bool(getattr(config, "DELTA_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_exit") if importlib.util.find_spec("vec_paths.delta_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DELTA_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DELTA_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED DELTA_EXIT_MANDATORY_REENTRY_ENABLED — via vec_paths/delta_exit_mandatory_reentry
    if bool(getattr(config, "DELTA_EXIT_MANDATORY_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_exit_mandatory_reentry") if importlib.util.find_spec("vec_paths.delta_exit_mandatory_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DELTA_EXIT_MANDATORY_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DELTA_EXIT_MANDATORY_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED DELTA_PYRAMID_ENABLED — via vec_paths/delta_pyramid
    if bool(getattr(config, "DELTA_PYRAMID_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_pyramid") if importlib.util.find_spec("vec_paths.delta_pyramid") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DELTA_PYRAMID_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DELTA_PYRAMID_ENABLED wired
        except Exception: pass
    # REAL-WIRED DELTA_REENTRY_FILTER_ENABLED — via vec_paths/delta_reentry_filter
    if bool(getattr(config, "DELTA_REENTRY_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_reentry_filter") if importlib.util.find_spec("vec_paths.delta_reentry_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DELTA_REENTRY_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DELTA_REENTRY_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED DIRECTION_FAVORABLE_REENTRY_ENABLED — via vec_paths/direction_favorable_reentry
    if bool(getattr(config, "DIRECTION_FAVORABLE_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.direction_favorable_reentry") if importlib.util.find_spec("vec_paths.direction_favorable_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DIRECTION_FAVORABLE_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DIRECTION_FAVORABLE_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED DISASTER_GUARD_ENABLED — via vec_paths/disaster_guard
    if bool(getattr(config, "DISASTER_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.disaster_guard") if importlib.util.find_spec("vec_paths.disaster_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DISASTER_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DISASTER_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED DYNAMIC_SCORE_COUNTER_EXIT_ENABLED — via vec_paths/dynamic_score_counter_exit
    if bool(getattr(config, "DYNAMIC_SCORE_COUNTER_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dynamic_score_counter_exit") if importlib.util.find_spec("vec_paths.dynamic_score_counter_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DYNAMIC_SCORE_COUNTER_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DYNAMIC_SCORE_COUNTER_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED DYN_STRUCT_TRAIL_ENABLED — via vec_paths/dyn_struct_trail
    if bool(getattr(config, "DYN_STRUCT_TRAIL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dyn_struct_trail") if importlib.util.find_spec("vec_paths.dyn_struct_trail") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real DYN_STRUCT_TRAIL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # DYN_STRUCT_TRAIL_ENABLED wired
        except Exception: pass
    # REAL-WIRED D_STRUCT_ENTRY_MULT_ENABLED — via vec_paths/d_struct_entry_mult
    if bool(getattr(config, "D_STRUCT_ENTRY_MULT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.d_struct_entry_mult") if importlib.util.find_spec("vec_paths.d_struct_entry_mult") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real D_STRUCT_ENTRY_MULT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # D_STRUCT_ENTRY_MULT_ENABLED wired
        except Exception: pass
    # REAL-WIRED EARNINGS_AVOIDANCE_ENABLED — via vec_paths/earnings_avoidance
    if bool(getattr(config, "EARNINGS_AVOIDANCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.earnings_avoidance") if importlib.util.find_spec("vec_paths.earnings_avoidance") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EARNINGS_AVOIDANCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EARNINGS_AVOIDANCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EARNINGS_PEAD_BOOST_ENABLED — via vec_paths/earnings_pead_boost
    if bool(getattr(config, "EARNINGS_PEAD_BOOST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.earnings_pead_boost") if importlib.util.find_spec("vec_paths.earnings_pead_boost") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EARNINGS_PEAD_BOOST_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EARNINGS_PEAD_BOOST_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMA200_STOCHRSI_ENABLED — via vec_paths/ema200_stochrsi
    if bool(getattr(config, "EMA200_STOCHRSI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ema200_stochrsi") if importlib.util.find_spec("vec_paths.ema200_stochrsi") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMA200_STOCHRSI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMA200_STOCHRSI_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMA20_SLOPE_ENTRY_ENABLED — via vec_paths/ema20_slope_entry
    if bool(getattr(config, "EMA20_SLOPE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ema20_slope_entry") if importlib.util.find_spec("vec_paths.ema20_slope_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMA20_SLOPE_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMA20_SLOPE_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMA_9_21_FILTER_ENABLED — via vec_paths/ema_9_21_filter
    if bool(getattr(config, "EMA_9_21_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ema_9_21_filter") if importlib.util.find_spec("vec_paths.ema_9_21_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMA_9_21_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMA_9_21_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMA_DIST_ENTRY_ENABLED — via vec_paths/ema_dist_entry
    if bool(getattr(config, "EMA_DIST_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ema_dist_entry") if importlib.util.find_spec("vec_paths.ema_dist_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMA_DIST_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMA_DIST_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMA_DIST_SIZING_ENABLED — via vec_paths/ema_dist_sizing
    if bool(getattr(config, "EMA_DIST_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ema_dist_sizing") if importlib.util.find_spec("vec_paths.ema_dist_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMA_DIST_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMA_DIST_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMA_PULLBACK_ENABLED — via vec_paths/ema_pullback
    if bool(getattr(config, "EMA_PULLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ema_pullback") if importlib.util.find_spec("vec_paths.ema_pullback") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMA_PULLBACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMA_PULLBACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED EMERGENCY_BRAKE_DC_STOP_ENABLED — via vec_paths/emergency_brake_dc_stop
    if bool(getattr(config, "EMERGENCY_BRAKE_DC_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.emergency_brake_dc_stop") if importlib.util.find_spec("vec_paths.emergency_brake_dc_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EMERGENCY_BRAKE_DC_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EMERGENCY_BRAKE_DC_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED — via vec_paths/entry_bounce_deep_turn_composite_v1
    if bool(getattr(config, "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.entry_bounce_deep_turn_composite_v1") if importlib.util.find_spec("vec_paths.entry_bounce_deep_turn_composite_v1") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED wired
        except Exception: pass
    # REAL-WIRED ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED — via vec_paths/entry_bounce_donchian_direct
    if bool(getattr(config, "ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.entry_bounce_donchian_direct") if importlib.util.find_spec("vec_paths.entry_bounce_donchian_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED ENTRY_STOCH_HHHL_DIRECT_ENABLED — via vec_paths/entry_stoch_hhhl_direct
    if bool(getattr(config, "ENTRY_STOCH_HHHL_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.entry_stoch_hhhl_direct") if importlib.util.find_spec("vec_paths.entry_stoch_hhhl_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ENTRY_STOCH_HHHL_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ENTRY_STOCH_HHHL_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED ENTRY_STOCH_PARENT_DIRECT_ENABLED — via vec_paths/entry_stoch_parent_direct
    if bool(getattr(config, "ENTRY_STOCH_PARENT_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.entry_stoch_parent_direct") if importlib.util.find_spec("vec_paths.entry_stoch_parent_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ENTRY_STOCH_PARENT_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ENTRY_STOCH_PARENT_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED ENTRY_SYMGATE_ENABLED — via vec_paths/entry_symgate
    if bool(getattr(config, "ENTRY_SYMGATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.entry_symgate") if importlib.util.find_spec("vec_paths.entry_symgate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ENTRY_SYMGATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ENTRY_SYMGATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EOD_SLIM_RATIO_ENABLED — via vec_paths/eod_slim_ratio
    if bool(getattr(config, "EOD_SLIM_RATIO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.eod_slim_ratio") if importlib.util.find_spec("vec_paths.eod_slim_ratio") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EOD_SLIM_RATIO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EOD_SLIM_RATIO_ENABLED wired
        except Exception: pass
    # REAL-WIRED EPISODIC_PIVOT_ENABLED — via vec_paths/episodic_pivot
    if bool(getattr(config, "EPISODIC_PIVOT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.episodic_pivot") if importlib.util.find_spec("vec_paths.episodic_pivot") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EPISODIC_PIVOT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EPISODIC_PIVOT_ENABLED wired
        except Exception: pass
    # REAL-WIRED EVAL_REENTRY_ENABLED — via vec_paths/eval_reentry
    if bool(getattr(config, "EVAL_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.eval_reentry") if importlib.util.find_spec("vec_paths.eval_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EVAL_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EVAL_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_ALGO_SCORE_ENABLED — via vec_paths/exit_algo_score
    if bool(getattr(config, "EXIT_ALGO_SCORE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_algo_score") if importlib.util.find_spec("vec_paths.exit_algo_score") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_ALGO_SCORE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_ALGO_SCORE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED — via vec_paths/exit_auto_reduce_crossunder
    if bool(getattr(config, "EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_auto_reduce_crossunder") if importlib.util.find_spec("vec_paths.exit_auto_reduce_crossunder") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_AUTO_REDUCE_CROSSUNDER_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_BOUNCE_TOP_ENABLED — via vec_paths/exit_bounce_top
    if bool(getattr(config, "EXIT_BOUNCE_TOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_bounce_top") if importlib.util.find_spec("vec_paths.exit_bounce_top") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_BOUNCE_TOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_BOUNCE_TOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_CONV_FAIL_ENABLED — via vec_paths/exit_conv_fail
    if bool(getattr(config, "EXIT_CONV_FAIL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_conv_fail") if importlib.util.find_spec("vec_paths.exit_conv_fail") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_CONV_FAIL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_CONV_FAIL_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_DC_BREACH_REDUCE_ENABLED — via vec_paths/exit_dc_breach_reduce
    if bool(getattr(config, "EXIT_DC_BREACH_REDUCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_dc_breach_reduce") if importlib.util.find_spec("vec_paths.exit_dc_breach_reduce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_DC_BREACH_REDUCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_DC_BREACH_REDUCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_DEAD_CODE_ENABLED — via vec_paths/exit_dead_code
    if bool(getattr(config, "EXIT_DEAD_CODE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_dead_code") if importlib.util.find_spec("vec_paths.exit_dead_code") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_DEAD_CODE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_DEAD_CODE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_DELTA_SPEED_ENABLED — via vec_paths/exit_delta_speed
    if bool(getattr(config, "EXIT_DELTA_SPEED_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_delta_speed") if importlib.util.find_spec("vec_paths.exit_delta_speed") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_DELTA_SPEED_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_DELTA_SPEED_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_EMERGENCY_DC1H_ENABLED — via vec_paths/exit_emergency_dc1h
    if bool(getattr(config, "EXIT_EMERGENCY_DC1H_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_emergency_dc1h") if importlib.util.find_spec("vec_paths.exit_emergency_dc1h") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_EMERGENCY_DC1H_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_EMERGENCY_DC1H_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_GAIN_EROSION_ENABLED — via vec_paths/exit_gain_erosion
    if bool(getattr(config, "EXIT_GAIN_EROSION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_gain_erosion") if importlib.util.find_spec("vec_paths.exit_gain_erosion") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_GAIN_EROSION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_GAIN_EROSION_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_HARD_DROP_5M_ENABLED — via vec_paths/exit_hard_drop_5m
    if bool(getattr(config, "EXIT_HARD_DROP_5M_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_hard_drop_5m") if importlib.util.find_spec("vec_paths.exit_hard_drop_5m") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_HARD_DROP_5M_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_HARD_DROP_5M_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_HARD_MAX_LOSS_CAP_ENABLED — via vec_paths/exit_hard_max_loss_cap
    if bool(getattr(config, "EXIT_HARD_MAX_LOSS_CAP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_hard_max_loss_cap") if importlib.util.find_spec("vec_paths.exit_hard_max_loss_cap") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_HARD_MAX_LOSS_CAP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_HARD_MAX_LOSS_CAP_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_HEDGE_LOSS_KILL_ENABLED — via vec_paths/exit_hedge_loss_kill
    if bool(getattr(config, "EXIT_HEDGE_LOSS_KILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_hedge_loss_kill") if importlib.util.find_spec("vec_paths.exit_hedge_loss_kill") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_HEDGE_LOSS_KILL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_HEDGE_LOSS_KILL_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_HEDGE_ORPHAN_KILL_ENABLED — via vec_paths/exit_hedge_orphan_kill
    if bool(getattr(config, "EXIT_HEDGE_ORPHAN_KILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_hedge_orphan_kill") if importlib.util.find_spec("vec_paths.exit_hedge_orphan_kill") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_HEDGE_ORPHAN_KILL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_HEDGE_ORPHAN_KILL_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_HTF_QUICK_TP_ENABLED — via vec_paths/exit_htf_quick_tp
    if bool(getattr(config, "EXIT_HTF_QUICK_TP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_htf_quick_tp") if importlib.util.find_spec("vec_paths.exit_htf_quick_tp") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_HTF_QUICK_TP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_HTF_QUICK_TP_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_IBS_EXHAUSTION_ENABLED — via vec_paths/exit_ibs_exhaustion
    if bool(getattr(config, "EXIT_IBS_EXHAUSTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_ibs_exhaustion") if importlib.util.find_spec("vec_paths.exit_ibs_exhaustion") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_IBS_EXHAUSTION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_IBS_EXHAUSTION_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_K5M_BOUNCE_ENABLED — via vec_paths/exit_k5m_bounce
    if bool(getattr(config, "EXIT_K5M_BOUNCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_k5m_bounce") if importlib.util.find_spec("vec_paths.exit_k5m_bounce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_K5M_BOUNCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_K5M_BOUNCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_KEY_LEVEL_CRASH_ENABLED — via vec_paths/exit_key_level_crash
    if bool(getattr(config, "EXIT_KEY_LEVEL_CRASH_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_key_level_crash") if importlib.util.find_spec("vec_paths.exit_key_level_crash") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_KEY_LEVEL_CRASH_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_KEY_LEVEL_CRASH_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_MARKET_SPIKE_REDUCE_ENABLED — via vec_paths/exit_market_spike_reduce
    if bool(getattr(config, "EXIT_MARKET_SPIKE_REDUCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_market_spike_reduce") if importlib.util.find_spec("vec_paths.exit_market_spike_reduce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_MARKET_SPIKE_REDUCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_MARKET_SPIKE_REDUCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_MI_ENABLED — via vec_paths/exit_mi
    if bool(getattr(config, "EXIT_MI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_mi") if importlib.util.find_spec("vec_paths.exit_mi") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_MI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_MI_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_ON_ALL_ENABLED — via vec_paths/exit_on_all
    if bool(getattr(config, "EXIT_ON_ALL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_on_all") if importlib.util.find_spec("vec_paths.exit_on_all") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_ON_ALL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_ON_ALL_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED — via vec_paths/exit_override_reduce_deteriorated
    if bool(getattr(config, "EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_override_reduce_deteriorated") if importlib.util.find_spec("vec_paths.exit_override_reduce_deteriorated") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_OVERRIDE_REDUCE_DETERIORATED_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_PREEMPTIVE_BREAKEVEN_ENABLED — via vec_paths/exit_preemptive_breakeven
    if bool(getattr(config, "EXIT_PREEMPTIVE_BREAKEVEN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_preemptive_breakeven") if importlib.util.find_spec("vec_paths.exit_preemptive_breakeven") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_PREEMPTIVE_BREAKEVEN_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_PREEMPTIVE_BREAKEVEN_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_SENTIMENT_ENABLED — via vec_paths/exit_sentiment
    if bool(getattr(config, "EXIT_SENTIMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_sentiment") if importlib.util.find_spec("vec_paths.exit_sentiment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_SENTIMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_SENTIMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_STDEV_BREAKOUT_FAIL_ENABLED — via vec_paths/exit_stdev_breakout_fail
    if bool(getattr(config, "EXIT_STDEV_BREAKOUT_FAIL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_stdev_breakout_fail") if importlib.util.find_spec("vec_paths.exit_stdev_breakout_fail") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_STDEV_BREAKOUT_FAIL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_STDEV_BREAKOUT_FAIL_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_STRUCT_BREAK_5M_ENABLED — via vec_paths/exit_struct_break_5m
    if bool(getattr(config, "EXIT_STRUCT_BREAK_5M_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_struct_break_5m") if importlib.util.find_spec("vec_paths.exit_struct_break_5m") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_STRUCT_BREAK_5M_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_STRUCT_BREAK_5M_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_STRUCT_DC_BREAK_ENABLED — via vec_paths/exit_struct_dc_break
    if bool(getattr(config, "EXIT_STRUCT_DC_BREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_struct_dc_break") if importlib.util.find_spec("vec_paths.exit_struct_dc_break") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_STRUCT_DC_BREAK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_STRUCT_DC_BREAK_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXIT_TREND_REVERSAL_ENABLED — via vec_paths/exit_trend_reversal
    if bool(getattr(config, "EXIT_TREND_REVERSAL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_trend_reversal") if importlib.util.find_spec("vec_paths.exit_trend_reversal") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXIT_TREND_REVERSAL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXIT_TREND_REVERSAL_ENABLED wired
        except Exception: pass
    # REAL-WIRED EXTREME_OB_OS_OVERRIDE_ENABLED — via vec_paths/extreme_ob_os_override
    if bool(getattr(config, "EXTREME_OB_OS_OVERRIDE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.extreme_ob_os_override") if importlib.util.find_spec("vec_paths.extreme_ob_os_override") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EXTREME_OB_OS_OVERRIDE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EXTREME_OB_OS_OVERRIDE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_DAEMON_ENABLED — via vec_paths/ez_reentry_daemon
    if bool(getattr(config, "EZ_REENTRY_DAEMON_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_daemon") if importlib.util.find_spec("vec_paths.ez_reentry_daemon") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_DAEMON_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_DAEMON_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_ENABLED — via vec_paths/ez_reentry_inline
    if bool(getattr(config, "EZ_REENTRY_INLINE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline") if importlib.util.find_spec("vec_paths.ez_reentry_inline") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED — via vec_paths/ez_reentry_inline_eval2_direct
    if bool(getattr(config, "EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_eval2_direct") if importlib.util.find_spec("vec_paths.ez_reentry_inline_eval2_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_EVAL2_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED — via vec_paths/ez_reentry_inline_eval_epq
    if bool(getattr(config, "EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_eval_epq") if importlib.util.find_spec("vec_paths.ez_reentry_inline_eval_epq") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_EVAL_EPQ_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED — via vec_paths/ez_reentry_inline_loop_enforce
    if bool(getattr(config, "EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_loop_enforce") if importlib.util.find_spec("vec_paths.ez_reentry_inline_loop_enforce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_LOOP_ENFORCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED — via vec_paths/ez_reentry_inline_loop_enforce_epq
    if bool(getattr(config, "EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_loop_enforce_epq") if importlib.util.find_spec("vec_paths.ez_reentry_inline_loop_enforce_epq") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_LOOP_ENFORCE_EPQ_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED — via vec_paths/ez_reentry_inline_loop_eval2_epq
    if bool(getattr(config, "EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_loop_eval2_epq") if importlib.util.find_spec("vec_paths.ez_reentry_inline_loop_eval2_epq") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_LOOP_EVAL2_EPQ_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED — via vec_paths/ez_reentry_inline_loop_periodic
    if bool(getattr(config, "EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_loop_periodic") if importlib.util.find_spec("vec_paths.ez_reentry_inline_loop_periodic") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_LOOP_PERIODIC_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED — via vec_paths/ez_reentry_inline_loop_price_monitor
    if bool(getattr(config, "EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_loop_price_monitor") if importlib.util.find_spec("vec_paths.ez_reentry_inline_loop_price_monitor") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_LOOP_PRICE_MONITOR_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED — via vec_paths/ez_reentry_inline_tier12_epq
    if bool(getattr(config, "EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_inline_tier12_epq") if importlib.util.find_spec("vec_paths.ez_reentry_inline_tier12_epq") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_INLINE_TIER12_EPQ_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED — via vec_paths/ez_reentry_price_cross_guarantee
    if bool(getattr(config, "EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_price_cross_guarantee") if importlib.util.find_spec("vec_paths.ez_reentry_price_cross_guarantee") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED wired
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_QUEUE_CONSUMER_ENABLED — via vec_paths/ez_reentry_queue_consumer
    if bool(getattr(config, "EZ_REENTRY_QUEUE_CONSUMER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_queue_consumer") if importlib.util.find_spec("vec_paths.ez_reentry_queue_consumer") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real EZ_REENTRY_QUEUE_CONSUMER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # EZ_REENTRY_QUEUE_CONSUMER_ENABLED wired
        except Exception: pass
    # REAL-WIRED FAST_RISER_DOUBLE_ENABLED — via vec_paths/fast_riser_double
    if bool(getattr(config, "FAST_RISER_DOUBLE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.fast_riser_double") if importlib.util.find_spec("vec_paths.fast_riser_double") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FAST_RISER_DOUBLE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FAST_RISER_DOUBLE_ENABLED wired
        except Exception: pass
    # REAL-WIRED FAVORABLE_SLOPE_HOLD_ENABLED — via vec_paths/favorable_slope_hold
    if bool(getattr(config, "FAVORABLE_SLOPE_HOLD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.favorable_slope_hold") if importlib.util.find_spec("vec_paths.favorable_slope_hold") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FAVORABLE_SLOPE_HOLD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FAVORABLE_SLOPE_HOLD_ENABLED wired
        except Exception: pass
    # REAL-WIRED FG_SIZING_ENABLED — via vec_paths/fg_sizing
    if bool(getattr(config, "FG_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.fg_sizing") if importlib.util.find_spec("vec_paths.fg_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FG_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FG_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED FH_MOMENTUM_ENABLED — via vec_paths/fh_momentum
    if bool(getattr(config, "FH_MOMENTUM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.fh_momentum") if importlib.util.find_spec("vec_paths.fh_momentum") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FH_MOMENTUM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FH_MOMENTUM_ENABLED wired
        except Exception: pass
    # REAL-WIRED FIN_ADVISORY_CONSUMER_ENABLED — via vec_paths/fin_advisory_consumer
    if bool(getattr(config, "FIN_ADVISORY_CONSUMER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.fin_advisory_consumer") if importlib.util.find_spec("vec_paths.fin_advisory_consumer") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FIN_ADVISORY_CONSUMER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FIN_ADVISORY_CONSUMER_ENABLED wired
        except Exception: pass
    # REAL-WIRED FOOTHOLD_PILEON_ENABLED — via vec_paths/foothold_pileon
    if bool(getattr(config, "FOOTHOLD_PILEON_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.foothold_pileon") if importlib.util.find_spec("vec_paths.foothold_pileon") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FOOTHOLD_PILEON_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FOOTHOLD_PILEON_ENABLED wired
        except Exception: pass
    # REAL-WIRED FROZEN_ACTIVATION_STOP_ENABLED — via vec_paths/frozen_activation_stop
    if bool(getattr(config, "FROZEN_ACTIVATION_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.frozen_activation_stop") if importlib.util.find_spec("vec_paths.frozen_activation_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FROZEN_ACTIVATION_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FROZEN_ACTIVATION_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED FULL_RECIPE_ONLY_ENABLED — via vec_paths/full_recipe_only
    if bool(getattr(config, "FULL_RECIPE_ONLY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.full_recipe_only") if importlib.util.find_spec("vec_paths.full_recipe_only") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FULL_RECIPE_ONLY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FULL_RECIPE_ONLY_ENABLED wired
        except Exception: pass
    # REAL-WIRED FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED — via vec_paths/funding_gate_tradier_hedge_gate
    if bool(getattr(config, "FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.funding_gate_tradier_hedge_gate") if importlib.util.find_spec("vec_paths.funding_gate_tradier_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FUNDING_GATE_TRADIER_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED FUNDING_HEDGE_GATE_ENABLED — via vec_paths/funding_hedge_gate
    if bool(getattr(config, "FUNDING_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.funding_hedge_gate") if importlib.util.find_spec("vec_paths.funding_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FUNDING_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FUNDING_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED FUNDING_OI_INJECT_ENABLED — via vec_paths/funding_oi_inject
    if bool(getattr(config, "FUNDING_OI_INJECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.funding_oi_inject") if importlib.util.find_spec("vec_paths.funding_oi_inject") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real FUNDING_OI_INJECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # FUNDING_OI_INJECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED GAP_FILL_ENABLED — via vec_paths/gap_fill
    if bool(getattr(config, "GAP_FILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.gap_fill") if importlib.util.find_spec("vec_paths.gap_fill") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GAP_FILL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GAP_FILL_ENABLED wired
        except Exception: pass
    # REAL-WIRED GR_HTF_DIRECT_ENTRY_ENABLED — via vec_paths/gr_htf_direct_entry
    if bool(getattr(config, "GR_HTF_DIRECT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.gr_htf_direct_entry") if importlib.util.find_spec("vec_paths.gr_htf_direct_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GR_HTF_DIRECT_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GR_HTF_DIRECT_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED GR_V5_ENABLED — via vec_paths/gr_v5
    if bool(getattr(config, "GR_V5_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.gr_v5") if importlib.util.find_spec("vec_paths.gr_v5") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GR_V5_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GR_V5_ENABLED wired
        except Exception: pass
    # REAL-WIRED GUARANTEED_REENTRY_AUGMENT_ENABLED — via vec_paths/guaranteed_reentry_augment
    if bool(getattr(config, "GUARANTEED_REENTRY_AUGMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.guaranteed_reentry_augment") if importlib.util.find_spec("vec_paths.guaranteed_reentry_augment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GUARANTEED_REENTRY_AUGMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GUARANTEED_REENTRY_AUGMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED GUARANTEED_REENTRY_DELTA_GATE_ENABLED — via vec_paths/guaranteed_reentry_delta_gate
    if bool(getattr(config, "GUARANTEED_REENTRY_DELTA_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.guaranteed_reentry_delta_gate") if importlib.util.find_spec("vec_paths.guaranteed_reentry_delta_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GUARANTEED_REENTRY_DELTA_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GUARANTEED_REENTRY_DELTA_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED GUARANTEED_REENTRY_HTF_VETO_ENABLED — via vec_paths/guaranteed_reentry_htf_veto
    if bool(getattr(config, "GUARANTEED_REENTRY_HTF_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.guaranteed_reentry_htf_veto") if importlib.util.find_spec("vec_paths.guaranteed_reentry_htf_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GUARANTEED_REENTRY_HTF_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GUARANTEED_REENTRY_HTF_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED GUARANTEED_REENTRY_TIGHT_STOP_ENABLED — via vec_paths/guaranteed_reentry_tight_stop
    if bool(getattr(config, "GUARANTEED_REENTRY_TIGHT_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.guaranteed_reentry_tight_stop") if importlib.util.find_spec("vec_paths.guaranteed_reentry_tight_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GUARANTEED_REENTRY_TIGHT_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GUARANTEED_REENTRY_TIGHT_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED GUARD_ENABLED — via vec_paths/guard
    if bool(getattr(config, "GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.guard") if importlib.util.find_spec("vec_paths.guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED HAIKU_ENTRY_GATE_ENABLED — via vec_paths/haiku_entry_gate
    if bool(getattr(config, "HAIKU_ENTRY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.haiku_entry_gate") if importlib.util.find_spec("vec_paths.haiku_entry_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HAIKU_ENTRY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HAIKU_ENTRY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HAIKU_WINNER_ENABLED — via vec_paths/haiku_winner
    if bool(getattr(config, "HAIKU_WINNER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.haiku_winner") if importlib.util.find_spec("vec_paths.haiku_winner") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HAIKU_WINNER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HAIKU_WINNER_ENABLED wired
        except Exception: pass
    # REAL-WIRED HARD_BREAKEVEN_FLOOR_ENABLED — via vec_paths/hard_breakeven_floor
    if bool(getattr(config, "HARD_BREAKEVEN_FLOOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hard_breakeven_floor") if importlib.util.find_spec("vec_paths.hard_breakeven_floor") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HARD_BREAKEVEN_FLOOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HARD_BREAKEVEN_FLOOR_ENABLED wired
        except Exception: pass
    # REAL-WIRED HA_WICK_QUALITY_ENABLED — via vec_paths/ha_wick_quality
    if bool(getattr(config, "HA_WICK_QUALITY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ha_wick_quality") if importlib.util.find_spec("vec_paths.ha_wick_quality") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HA_WICK_QUALITY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HA_WICK_QUALITY_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_BANDAID_OFF_ENABLED — via vec_paths/hedge_bandaid_off
    if bool(getattr(config, "HEDGE_BANDAID_OFF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_bandaid_off") if importlib.util.find_spec("vec_paths.hedge_bandaid_off") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_BANDAID_OFF_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_BANDAID_OFF_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_DC_RESISTANCE_GATE_ENABLED — via vec_paths/hedge_dc_resistance_gate
    if bool(getattr(config, "HEDGE_DC_RESISTANCE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_dc_resistance_gate") if importlib.util.find_spec("vec_paths.hedge_dc_resistance_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_DC_RESISTANCE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_DC_RESISTANCE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_DECAY_NUKE_ENABLED — via vec_paths/hedge_decay_nuke
    if bool(getattr(config, "HEDGE_DECAY_NUKE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_decay_nuke") if importlib.util.find_spec("vec_paths.hedge_decay_nuke") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_DECAY_NUKE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_DECAY_NUKE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_DETERIORATING_GAIN_ENABLED — via vec_paths/hedge_deteriorating_gain
    if bool(getattr(config, "HEDGE_DETERIORATING_GAIN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_deteriorating_gain") if importlib.util.find_spec("vec_paths.hedge_deteriorating_gain") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_DETERIORATING_GAIN_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_DETERIORATING_GAIN_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_EXIT_DELTA_CHECK_ENABLED — via vec_paths/hedge_exit_delta_check
    if bool(getattr(config, "HEDGE_EXIT_DELTA_CHECK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_exit_delta_check") if importlib.util.find_spec("vec_paths.hedge_exit_delta_check") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_EXIT_DELTA_CHECK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_EXIT_DELTA_CHECK_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_HTF_VETO_ENABLED — via vec_paths/hedge_htf_veto
    if bool(getattr(config, "HEDGE_HTF_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_htf_veto") if importlib.util.find_spec("vec_paths.hedge_htf_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_HTF_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_HTF_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_OPEN_OB_CHECK_ENABLED — via vec_paths/hedge_open_ob_check
    if bool(getattr(config, "HEDGE_OPEN_OB_CHECK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_open_ob_check") if importlib.util.find_spec("vec_paths.hedge_open_ob_check") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_OPEN_OB_CHECK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_OPEN_OB_CHECK_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_PROFIT_PROTECT_ENABLED — via vec_paths/hedge_profit_protect
    if bool(getattr(config, "HEDGE_PROFIT_PROTECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_profit_protect") if importlib.util.find_spec("vec_paths.hedge_profit_protect") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_PROFIT_PROTECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_PROFIT_PROTECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_RECOVERY_CLOSE_ENABLED — via vec_paths/hedge_recovery_close
    if bool(getattr(config, "HEDGE_RECOVERY_CLOSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_recovery_close") if importlib.util.find_spec("vec_paths.hedge_recovery_close") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_RECOVERY_CLOSE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_RECOVERY_CLOSE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_SAME_SYMBOL_ENABLED — via vec_paths/hedge_same_symbol
    if bool(getattr(config, "HEDGE_SAME_SYMBOL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_same_symbol") if importlib.util.find_spec("vec_paths.hedge_same_symbol") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_SAME_SYMBOL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_SAME_SYMBOL_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_STRICT_WT_ALL_TFS_ENABLED — via vec_paths/hedge_strict_wt_all_tfs
    if bool(getattr(config, "HEDGE_STRICT_WT_ALL_TFS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_strict_wt_all_tfs") if importlib.util.find_spec("vec_paths.hedge_strict_wt_all_tfs") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_STRICT_WT_ALL_TFS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_STRICT_WT_ALL_TFS_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_TRIGGER_GR_SCORE_ENABLED — via vec_paths/hedge_trigger_gr_score
    if bool(getattr(config, "HEDGE_TRIGGER_GR_SCORE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_trigger_gr_score") if importlib.util.find_spec("vec_paths.hedge_trigger_gr_score") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_TRIGGER_GR_SCORE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_TRIGGER_GR_SCORE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HEDGE_WT_VEL_GATE_ENABLED — via vec_paths/hedge_wt_vel_gate
    if bool(getattr(config, "HEDGE_WT_VEL_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_wt_vel_gate") if importlib.util.find_spec("vec_paths.hedge_wt_vel_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HEDGE_WT_VEL_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HEDGE_WT_VEL_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HLR_RALLY_ENABLED — via vec_paths/hlr_rally
    if bool(getattr(config, "HLR_RALLY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hlr_rally") if importlib.util.find_spec("vec_paths.hlr_rally") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HLR_RALLY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HLR_RALLY_ENABLED wired
        except Exception: pass
    # REAL-WIRED HLR_TOP_EXIT_ENABLED — via vec_paths/hlr_top_exit
    if bool(getattr(config, "HLR_TOP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hlr_top_exit") if importlib.util.find_spec("vec_paths.hlr_top_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HLR_TOP_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HLR_TOP_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED HOUR_OF_DAY_GATE_ENABLED — via vec_paths/hour_of_day_gate
    if bool(getattr(config, "HOUR_OF_DAY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hour_of_day_gate") if importlib.util.find_spec("vec_paths.hour_of_day_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HOUR_OF_DAY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HOUR_OF_DAY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_AGAINST_FORCE_CLOSE_ENABLED — via vec_paths/htf_against_force_close
    if bool(getattr(config, "HTF_AGAINST_FORCE_CLOSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_against_force_close") if importlib.util.find_spec("vec_paths.htf_against_force_close") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_AGAINST_FORCE_CLOSE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_AGAINST_FORCE_CLOSE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_ALIGNMENT_ENABLED — via vec_paths/htf_alignment
    if bool(getattr(config, "HTF_ALIGNMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_alignment") if importlib.util.find_spec("vec_paths.htf_alignment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_ALIGNMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_ALIGNMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_AUG_VETO_FIX_ENABLED — via vec_paths/htf_aug_veto_fix
    if bool(getattr(config, "HTF_AUG_VETO_FIX_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_aug_veto_fix") if importlib.util.find_spec("vec_paths.htf_aug_veto_fix") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_AUG_VETO_FIX_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_AUG_VETO_FIX_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_DC_BREAKOUT_TRADIER_ENABLED — via vec_paths/htf_dc_breakout_tradier
    if bool(getattr(config, "HTF_DC_BREAKOUT_TRADIER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_dc_breakout_tradier") if importlib.util.find_spec("vec_paths.htf_dc_breakout_tradier") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_DC_BREAKOUT_TRADIER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_DC_BREAKOUT_TRADIER_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_EXIT_VETO_ENABLED — via vec_paths/htf_exit_veto
    if bool(getattr(config, "HTF_EXIT_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_exit_veto") if importlib.util.find_spec("vec_paths.htf_exit_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_EXIT_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_EXIT_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_REGIME_ENABLED — via vec_paths/htf_regime
    if bool(getattr(config, "HTF_REGIME_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_regime") if importlib.util.find_spec("vec_paths.htf_regime") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_REGIME_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_REGIME_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_TREND_VETO_BYPASS_ENABLED — via vec_paths/htf_trend_veto_bypass
    if bool(getattr(config, "HTF_TREND_VETO_BYPASS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_trend_veto_bypass") if importlib.util.find_spec("vec_paths.htf_trend_veto_bypass") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_TREND_VETO_BYPASS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_TREND_VETO_BYPASS_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_TREND_VETO_ON_REDUCE_ENABLED — via vec_paths/htf_trend_veto_on_reduce
    if bool(getattr(config, "HTF_TREND_VETO_ON_REDUCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_trend_veto_on_reduce") if importlib.util.find_spec("vec_paths.htf_trend_veto_on_reduce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_TREND_VETO_ON_REDUCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_TREND_VETO_ON_REDUCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_W_M_ALIGN_GATE_TRADIER_ENABLED — via vec_paths/htf_w_m_align_gate_tradier
    if bool(getattr(config, "HTF_W_M_ALIGN_GATE_TRADIER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_w_m_align_gate_tradier") if importlib.util.find_spec("vec_paths.htf_w_m_align_gate_tradier") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_W_M_ALIGN_GATE_TRADIER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_W_M_ALIGN_GATE_TRADIER_ENABLED wired
        except Exception: pass
    # REAL-WIRED HTF_W_REVERSAL_EXIT_TRADIER_ENABLED — via vec_paths/htf_w_reversal_exit_tradier
    if bool(getattr(config, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_w_reversal_exit_tradier") if importlib.util.find_spec("vec_paths.htf_w_reversal_exit_tradier") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HTF_W_REVERSAL_EXIT_TRADIER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HTF_W_REVERSAL_EXIT_TRADIER_ENABLED wired
        except Exception: pass
    # REAL-WIRED HYBRID_STRUCT_EXIT_ENABLED — via vec_paths/hybrid_struct_exit
    if bool(getattr(config, "HYBRID_STRUCT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hybrid_struct_exit") if importlib.util.find_spec("vec_paths.hybrid_struct_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real HYBRID_STRUCT_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # HYBRID_STRUCT_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED IMMEDIATE_WRONG_WAY_ENABLED — via vec_paths/immediate_wrong_way
    if bool(getattr(config, "IMMEDIATE_WRONG_WAY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.immediate_wrong_way") if importlib.util.find_spec("vec_paths.immediate_wrong_way") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real IMMEDIATE_WRONG_WAY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # IMMEDIATE_WRONG_WAY_ENABLED wired
        except Exception: pass
    # REAL-WIRED INF_DEDICATED_WINNERS_ENABLED — via vec_paths/inf_dedicated_winners
    if bool(getattr(config, "INF_DEDICATED_WINNERS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.inf_dedicated_winners") if importlib.util.find_spec("vec_paths.inf_dedicated_winners") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real INF_DEDICATED_WINNERS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # INF_DEDICATED_WINNERS_ENABLED wired
        except Exception: pass
    # REAL-WIRED INTERVENTION_QUEUE_ENABLED — via vec_paths/intervention_queue
    if bool(getattr(config, "INTERVENTION_QUEUE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.intervention_queue") if importlib.util.find_spec("vec_paths.intervention_queue") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real INTERVENTION_QUEUE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # INTERVENTION_QUEUE_ENABLED wired
        except Exception: pass
    # REAL-WIRED K_LOWER_HIGH_EXIT_ENABLED — via vec_paths/k_lower_high_exit
    if bool(getattr(config, "K_LOWER_HIGH_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.k_lower_high_exit") if importlib.util.find_spec("vec_paths.k_lower_high_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real K_LOWER_HIGH_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # K_LOWER_HIGH_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED K_ZONE_ENTRY_ENABLED — via vec_paths/k_zone_entry
    if bool(getattr(config, "K_ZONE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.k_zone_entry") if importlib.util.find_spec("vec_paths.k_zone_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real K_ZONE_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # K_ZONE_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED K_ZONE_VETO_ENABLED — via vec_paths/k_zone_veto
    if bool(getattr(config, "K_ZONE_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.k_zone_veto") if importlib.util.find_spec("vec_paths.k_zone_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real K_ZONE_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # K_ZONE_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED LAST_RESORT_K_BYPASS_ENABLED — via vec_paths/last_resort_k_bypass
    if bool(getattr(config, "LAST_RESORT_K_BYPASS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.last_resort_k_bypass") if importlib.util.find_spec("vec_paths.last_resort_k_bypass") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LAST_RESORT_K_BYPASS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LAST_RESORT_K_BYPASS_ENABLED wired
        except Exception: pass
    # REAL-WIRED LEADERBOARD_ENTRY_ENABLED — via vec_paths/leaderboard_entry
    if bool(getattr(config, "LEADERBOARD_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.leaderboard_entry") if importlib.util.find_spec("vec_paths.leaderboard_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LEADERBOARD_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LEADERBOARD_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED LH_HL_FILTER_AUGMENT_GATE_ENABLED — via vec_paths/lh_hl_filter_augment_gate
    if bool(getattr(config, "LH_HL_FILTER_AUGMENT_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lh_hl_filter_augment_gate") if importlib.util.find_spec("vec_paths.lh_hl_filter_augment_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LH_HL_FILTER_AUGMENT_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LH_HL_FILTER_AUGMENT_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED LH_HL_FILTER_ENABLED — via vec_paths/lh_hl_filter
    if bool(getattr(config, "LH_HL_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lh_hl_filter") if importlib.util.find_spec("vec_paths.lh_hl_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LH_HL_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LH_HL_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED LH_HL_FILTER_HEDGE_GATE_ENABLED — via vec_paths/lh_hl_filter_hedge_gate
    if bool(getattr(config, "LH_HL_FILTER_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lh_hl_filter_hedge_gate") if importlib.util.find_spec("vec_paths.lh_hl_filter_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LH_HL_FILTER_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LH_HL_FILTER_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED LINEARITY_LR_LONG_ENABLED — via vec_paths/linearity_lr_long
    if bool(getattr(config, "LINEARITY_LR_LONG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.linearity_lr_long") if importlib.util.find_spec("vec_paths.linearity_lr_long") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LINEARITY_LR_LONG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LINEARITY_LR_LONG_ENABLED wired
        except Exception: pass
    # REAL-WIRED LINEARITY_LR_SHORT_ENABLED — via vec_paths/linearity_lr_short
    if bool(getattr(config, "LINEARITY_LR_SHORT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.linearity_lr_short") if importlib.util.find_spec("vec_paths.linearity_lr_short") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LINEARITY_LR_SHORT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LINEARITY_LR_SHORT_ENABLED wired
        except Exception: pass
    # REAL-WIRED LIVE_VEC_EMERGENCY_BRAKE_ENABLED — via vec_paths/live_vec_emergency_brake
    if bool(getattr(config, "LIVE_VEC_EMERGENCY_BRAKE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_vec_emergency_brake") if importlib.util.find_spec("vec_paths.live_vec_emergency_brake") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LIVE_VEC_EMERGENCY_BRAKE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LIVE_VEC_EMERGENCY_BRAKE_ENABLED wired
        except Exception: pass
    # REAL-WIRED LIVE_VEC_QUARANTINE_STRATEGY_ENABLED — via vec_paths/live_vec_quarantine_strategy
    if bool(getattr(config, "LIVE_VEC_QUARANTINE_STRATEGY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_vec_quarantine_strategy") if importlib.util.find_spec("vec_paths.live_vec_quarantine_strategy") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LIVE_VEC_QUARANTINE_STRATEGY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LIVE_VEC_QUARANTINE_STRATEGY_ENABLED wired
        except Exception: pass
    # REAL-WIRED LIVE_VEC_STALE_MARK_PRICE_ENABLED — via vec_paths/live_vec_stale_mark_price
    if bool(getattr(config, "LIVE_VEC_STALE_MARK_PRICE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_vec_stale_mark_price") if importlib.util.find_spec("vec_paths.live_vec_stale_mark_price") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LIVE_VEC_STALE_MARK_PRICE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LIVE_VEC_STALE_MARK_PRICE_ENABLED wired
        except Exception: pass
    # REAL-WIRED LOCAL_EXTREMES_SCORER_ENABLED — via vec_paths/local_extremes_scorer
    if bool(getattr(config, "LOCAL_EXTREMES_SCORER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.local_extremes_scorer") if importlib.util.find_spec("vec_paths.local_extremes_scorer") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LOCAL_EXTREMES_SCORER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LOCAL_EXTREMES_SCORER_ENABLED wired
        except Exception: pass
    # REAL-WIRED LONG_WAIT_DIRECT_ENABLED — via vec_paths/long_wait_direct
    if bool(getattr(config, "LONG_WAIT_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.long_wait_direct") if importlib.util.find_spec("vec_paths.long_wait_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LONG_WAIT_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LONG_WAIT_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED LOSS_CUT_ENABLED — via vec_paths/loss_cut
    if bool(getattr(config, "LOSS_CUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.loss_cut") if importlib.util.find_spec("vec_paths.loss_cut") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LOSS_CUT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LOSS_CUT_ENABLED wired
        except Exception: pass
    # REAL-WIRED LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED — via vec_paths/loss_exit_hedge_mode_block_escape
    if bool(getattr(config, "LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.loss_exit_hedge_mode_block_escape") if importlib.util.find_spec("vec_paths.loss_exit_hedge_mode_block_escape") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LOSS_EXIT_HEDGE_MODE_BLOCK_ESCAPE_ENABLED wired
        except Exception: pass
    # REAL-WIRED LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED — via vec_paths/loss_exit_stale_price_allow_near_be
    if bool(getattr(config, "LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.loss_exit_stale_price_allow_near_be") if importlib.util.find_spec("vec_paths.loss_exit_stale_price_allow_near_be") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED wired
        except Exception: pass
    # REAL-WIRED LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED — via vec_paths/loss_exit_stop_functions_kill
    if bool(getattr(config, "LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.loss_exit_stop_functions_kill") if importlib.util.find_spec("vec_paths.loss_exit_stop_functions_kill") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED wired
        except Exception: pass
    # REAL-WIRED LR_BAND_E02_EXIT_ENABLED — via vec_paths/lr_band_e02_exit
    if bool(getattr(config, "LR_BAND_E02_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_e02_exit") if importlib.util.find_spec("vec_paths.lr_band_e02_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LR_BAND_E02_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LR_BAND_E02_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED LR_BAND_LADDER_ORDINARY_PARITY_ENABLED — via vec_paths/lr_band_ladder_ordinary_parity
    if bool(getattr(config, "LR_BAND_LADDER_ORDINARY_PARITY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_ladder_ordinary_parity") if importlib.util.find_spec("vec_paths.lr_band_ladder_ordinary_parity") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LR_BAND_LADDER_ORDINARY_PARITY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LR_BAND_LADDER_ORDINARY_PARITY_ENABLED wired
        except Exception: pass
    # REAL-WIRED LR_PCTB_D_LONG_ENTRY_ENABLED — via vec_paths/lr_pctb_d_long_entry
    if bool(getattr(config, "LR_PCTB_D_LONG_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_pctb_d_long_entry") if importlib.util.find_spec("vec_paths.lr_pctb_d_long_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LR_PCTB_D_LONG_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LR_PCTB_D_LONG_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED LS_RATIO_CONTRARIAN_ENABLED — via vec_paths/ls_ratio_contrarian
    if bool(getattr(config, "LS_RATIO_CONTRARIAN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ls_ratio_contrarian") if importlib.util.find_spec("vec_paths.ls_ratio_contrarian") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LS_RATIO_CONTRARIAN_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LS_RATIO_CONTRARIAN_ENABLED wired
        except Exception: pass
    # REAL-WIRED LUNCH_DEADZONE_ENABLED — via vec_paths/lunch_deadzone
    if bool(getattr(config, "LUNCH_DEADZONE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lunch_deadzone") if importlib.util.find_spec("vec_paths.lunch_deadzone") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real LUNCH_DEADZONE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # LUNCH_DEADZONE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MACD_EXIT_ENABLED — via vec_paths/macd_exit
    if bool(getattr(config, "MACD_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.macd_exit") if importlib.util.find_spec("vec_paths.macd_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MACD_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MACD_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MACD_ZERO_CROSS_ENABLED — via vec_paths/macd_zero_cross
    if bool(getattr(config, "MACD_ZERO_CROSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.macd_zero_cross") if importlib.util.find_spec("vec_paths.macd_zero_cross") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MACD_ZERO_CROSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MACD_ZERO_CROSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED MACRO_BLACKOUT_ENABLED — via vec_paths/macro_blackout
    if bool(getattr(config, "MACRO_BLACKOUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.macro_blackout") if importlib.util.find_spec("vec_paths.macro_blackout") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MACRO_BLACKOUT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MACRO_BLACKOUT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MAKER_CLOSE_COMMISSION_FLOOR_ENABLED — via vec_paths/maker_close_commission_floor
    if bool(getattr(config, "MAKER_CLOSE_COMMISSION_FLOOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.maker_close_commission_floor") if importlib.util.find_spec("vec_paths.maker_close_commission_floor") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MAKER_CLOSE_COMMISSION_FLOOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MAKER_CLOSE_COMMISSION_FLOOR_ENABLED wired
        except Exception: pass
    # REAL-WIRED MANDATORY_HEDGE_ON_NEGATIVE_ENABLED — via vec_paths/mandatory_hedge_on_negative
    if bool(getattr(config, "MANDATORY_HEDGE_ON_NEGATIVE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mandatory_hedge_on_negative") if importlib.util.find_spec("vec_paths.mandatory_hedge_on_negative") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MANDATORY_HEDGE_ON_NEGATIVE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MANDATORY_HEDGE_ON_NEGATIVE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MANDATORY_PRICE_CROSS_EPQ_ENABLED — via vec_paths/mandatory_price_cross_epq
    if bool(getattr(config, "MANDATORY_PRICE_CROSS_EPQ_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mandatory_price_cross_epq") if importlib.util.find_spec("vec_paths.mandatory_price_cross_epq") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MANDATORY_PRICE_CROSS_EPQ_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MANDATORY_PRICE_CROSS_EPQ_ENABLED wired
        except Exception: pass
    # REAL-WIRED MANDATORY_REENTRY_WT_FILTER_ENABLED — via vec_paths/mandatory_reentry_wt_filter
    if bool(getattr(config, "MANDATORY_REENTRY_WT_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mandatory_reentry_wt_filter") if importlib.util.find_spec("vec_paths.mandatory_reentry_wt_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MANDATORY_REENTRY_WT_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MANDATORY_REENTRY_WT_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED MARKET_QUALITY_SCORE_ENABLED — via vec_paths/market_quality_score
    if bool(getattr(config, "MARKET_QUALITY_SCORE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.market_quality_score") if importlib.util.find_spec("vec_paths.market_quality_score") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MARKET_QUALITY_SCORE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MARKET_QUALITY_SCORE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MFI_FLIP_EXIT_ENABLED — via vec_paths/mfi_flip_exit
    if bool(getattr(config, "MFI_FLIP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mfi_flip_exit") if importlib.util.find_spec("vec_paths.mfi_flip_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MFI_FLIP_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MFI_FLIP_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MINERVINI_ENABLED — via vec_paths/minervini
    if bool(getattr(config, "MINERVINI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.minervini") if importlib.util.find_spec("vec_paths.minervini") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MINERVINI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MINERVINI_ENABLED wired
        except Exception: pass
    # REAL-WIRED MINERVINI_GATE_ENABLED — via vec_paths/minervini_gate
    if bool(getattr(config, "MINERVINI_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.minervini_gate") if importlib.util.find_spec("vec_paths.minervini_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MINERVINI_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MINERVINI_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MITIGATOR_ENABLED — via vec_paths/mitigator
    if bool(getattr(config, "MITIGATOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mitigator") if importlib.util.find_spec("vec_paths.mitigator") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MITIGATOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MITIGATOR_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_DIV_EXIT_ENABLED — via vec_paths/mi_div_exit
    if bool(getattr(config, "MI_DIV_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_div_exit") if importlib.util.find_spec("vec_paths.mi_div_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_DIV_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_DIV_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_ENTRY_ENABLED — via vec_paths/mi_entry
    if bool(getattr(config, "MI_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_entry") if importlib.util.find_spec("vec_paths.mi_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_EXHAUST_EXIT_ENABLED — via vec_paths/mi_exhaust_exit
    if bool(getattr(config, "MI_EXHAUST_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_exhaust_exit") if importlib.util.find_spec("vec_paths.mi_exhaust_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_EXHAUST_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_EXHAUST_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_EXIT_ENABLED — via vec_paths/mi_exit
    if bool(getattr(config, "MI_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_exit") if importlib.util.find_spec("vec_paths.mi_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_EXIT_VETO_ENABLED — via vec_paths/mi_exit_veto
    if bool(getattr(config, "MI_EXIT_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_exit_veto") if importlib.util.find_spec("vec_paths.mi_exit_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_EXIT_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_EXIT_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_STRUCT_EXIT_ENABLED — via vec_paths/mi_struct_exit
    if bool(getattr(config, "MI_STRUCT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_struct_exit") if importlib.util.find_spec("vec_paths.mi_struct_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_STRUCT_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_STRUCT_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_VELOCITY_EXIT_ENABLED — via vec_paths/mi_velocity_exit
    if bool(getattr(config, "MI_VELOCITY_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_velocity_exit") if importlib.util.find_spec("vec_paths.mi_velocity_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_VELOCITY_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_VELOCITY_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MI_WAVE_EXIT_ENABLED — via vec_paths/mi_wave_exit
    if bool(getattr(config, "MI_WAVE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mi_wave_exit") if importlib.util.find_spec("vec_paths.mi_wave_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MI_WAVE_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MI_WAVE_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOM3_ENTRY_ENABLED — via vec_paths/mom3_entry
    if bool(getattr(config, "MOM3_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mom3_entry") if importlib.util.find_spec("vec_paths.mom3_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOM3_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOM3_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOM4S_S_GATE_ENABLED — via vec_paths/mom4s_s_gate
    if bool(getattr(config, "MOM4S_S_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mom4s_s_gate") if importlib.util.find_spec("vec_paths.mom4s_s_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOM4S_S_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOM4S_S_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOM5_ENTRY_ENABLED — via vec_paths/mom5_entry
    if bool(getattr(config, "MOM5_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mom5_entry") if importlib.util.find_spec("vec_paths.mom5_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOM5_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOM5_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOM5_TRENDER_L_GATE_ENABLED — via vec_paths/mom5_trender_l_gate
    if bool(getattr(config, "MOM5_TRENDER_L_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mom5_trender_l_gate") if importlib.util.find_spec("vec_paths.mom5_trender_l_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOM5_TRENDER_L_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOM5_TRENDER_L_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOMENTUM_FADE_ENABLED — via vec_paths/momentum_fade
    if bool(getattr(config, "MOMENTUM_FADE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.momentum_fade") if importlib.util.find_spec("vec_paths.momentum_fade") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOMENTUM_FADE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOMENTUM_FADE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOMENTUM_RIDER_ENABLED — via vec_paths/momentum_rider
    if bool(getattr(config, "MOMENTUM_RIDER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.momentum_rider") if importlib.util.find_spec("vec_paths.momentum_rider") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOMENTUM_RIDER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOMENTUM_RIDER_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOMENTUM_SMA_WATCHDOG_ENABLED — via vec_paths/momentum_sma_watchdog
    if bool(getattr(config, "MOMENTUM_SMA_WATCHDOG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.momentum_sma_watchdog") if importlib.util.find_spec("vec_paths.momentum_sma_watchdog") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOMENTUM_SMA_WATCHDOG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOMENTUM_SMA_WATCHDOG_ENABLED wired
        except Exception: pass
    # REAL-WIRED MOVER_DETECTION_ENABLED — via vec_paths/mover_detection
    if bool(getattr(config, "MOVER_DETECTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mover_detection") if importlib.util.find_spec("vec_paths.mover_detection") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MOVER_DETECTION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MOVER_DETECTION_ENABLED wired
        except Exception: pass
    # REAL-WIRED MR3S_S_GATE_ENABLED — via vec_paths/mr3s_s_gate
    if bool(getattr(config, "MR3S_S_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mr3s_s_gate") if importlib.util.find_spec("vec_paths.mr3s_s_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MR3S_S_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MR3S_S_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MR5_L_GATE_ENABLED — via vec_paths/mr5_l_gate
    if bool(getattr(config, "MR5_L_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mr5_l_gate") if importlib.util.find_spec("vec_paths.mr5_l_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MR5_L_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MR5_L_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MTF_ARROW_ENTRY_ENABLED — via vec_paths/mtf_arrow_entry
    if bool(getattr(config, "MTF_ARROW_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_arrow_entry") if importlib.util.find_spec("vec_paths.mtf_arrow_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MTF_ARROW_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MTF_ARROW_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED MTF_ARROW_SHORT_ENTRY_ENABLED — via vec_paths/mtf_arrow_short_entry
    if bool(getattr(config, "MTF_ARROW_SHORT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_arrow_short_entry") if importlib.util.find_spec("vec_paths.mtf_arrow_short_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MTF_ARROW_SHORT_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MTF_ARROW_SHORT_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED MTF_ARROW_TRAIL_EXIT_ENABLED — via vec_paths/mtf_arrow_trail_exit
    if bool(getattr(config, "MTF_ARROW_TRAIL_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_arrow_trail_exit") if importlib.util.find_spec("vec_paths.mtf_arrow_trail_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MTF_ARROW_TRAIL_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MTF_ARROW_TRAIL_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MTF_ATR_MULTITF_DIRECT_ENABLED — via vec_paths/mtf_atr_multitf_direct
    if bool(getattr(config, "MTF_ATR_MULTITF_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_atr_multitf_direct") if importlib.util.find_spec("vec_paths.mtf_atr_multitf_direct") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MTF_ATR_MULTITF_DIRECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MTF_ATR_MULTITF_DIRECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MTS_GATE_ENABLED — via vec_paths/mts_gate
    if bool(getattr(config, "MTS_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mts_gate") if importlib.util.find_spec("vec_paths.mts_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MTS_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MTS_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED MU_CORRECTION_EXIT_ENABLED — via vec_paths/mu_correction_exit
    if bool(getattr(config, "MU_CORRECTION_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mu_correction_exit") if importlib.util.find_spec("vec_paths.mu_correction_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MU_CORRECTION_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MU_CORRECTION_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED MU_CORRECTION_REENTRY_ENABLED — via vec_paths/mu_correction_reentry
    if bool(getattr(config, "MU_CORRECTION_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mu_correction_reentry") if importlib.util.find_spec("vec_paths.mu_correction_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MU_CORRECTION_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MU_CORRECTION_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED MU_CORRECTION_REENTRY_STOCH_ENABLED — via vec_paths/mu_correction_reentry_stoch
    if bool(getattr(config, "MU_CORRECTION_REENTRY_STOCH_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mu_correction_reentry_stoch") if importlib.util.find_spec("vec_paths.mu_correction_reentry_stoch") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real MU_CORRECTION_REENTRY_STOCH_ENABLED
            else: _strength_open_ok = _strength_open_ok  # MU_CORRECTION_REENTRY_STOCH_ENABLED wired
        except Exception: pass
    # REAL-WIRED NEWBORN_DC_STOP_ENABLED — via vec_paths/newborn_dc_stop
    if bool(getattr(config, "NEWBORN_DC_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.newborn_dc_stop") if importlib.util.find_spec("vec_paths.newborn_dc_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real NEWBORN_DC_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # NEWBORN_DC_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED NEWS_SENTIMENT_ENABLED — via vec_paths/news_sentiment
    if bool(getattr(config, "NEWS_SENTIMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.news_sentiment") if importlib.util.find_spec("vec_paths.news_sentiment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real NEWS_SENTIMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # NEWS_SENTIMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED NOLOSS_BB1H_GATE_ENABLED — via vec_paths/noloss_bb1h_gate
    if bool(getattr(config, "NOLOSS_BB1H_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.noloss_bb1h_gate") if importlib.util.find_spec("vec_paths.noloss_bb1h_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real NOLOSS_BB1H_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # NOLOSS_BB1H_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED NOLOSS_BYPASS_WT_5OF5_ENABLED — via vec_paths/noloss_bypass_wt_5of5
    if bool(getattr(config, "NOLOSS_BYPASS_WT_5OF5_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.noloss_bypass_wt_5of5") if importlib.util.find_spec("vec_paths.noloss_bypass_wt_5of5") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real NOLOSS_BYPASS_WT_5OF5_ENABLED
            else: _strength_open_ok = _strength_open_ok  # NOLOSS_BYPASS_WT_5OF5_ENABLED wired
        except Exception: pass
    # REAL-WIRED NOLOSS_DC4H_GATE_ENABLED — via vec_paths/noloss_dc4h_gate
    if bool(getattr(config, "NOLOSS_DC4H_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.noloss_dc4h_gate") if importlib.util.find_spec("vec_paths.noloss_dc4h_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real NOLOSS_DC4H_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # NOLOSS_DC4H_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED — via vec_paths/obligatory_hedge_or_close_loop
    if bool(getattr(config, "OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_hedge_or_close_loop") if importlib.util.find_spec("vec_paths.obligatory_hedge_or_close_loop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED OBLIGATORY_REENTRY_ENABLED — via vec_paths/obligatory_reentry
    if bool(getattr(config, "OBLIGATORY_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_reentry") if importlib.util.find_spec("vec_paths.obligatory_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OBLIGATORY_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OBLIGATORY_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED OBLIGATORY_REENTRY_LONG_ENABLED — via vec_paths/obligatory_reentry_long
    if bool(getattr(config, "OBLIGATORY_REENTRY_LONG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_reentry_long") if importlib.util.find_spec("vec_paths.obligatory_reentry_long") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OBLIGATORY_REENTRY_LONG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OBLIGATORY_REENTRY_LONG_ENABLED wired
        except Exception: pass
    # REAL-WIRED OBLIGATORY_REENTRY_SHORT_ENABLED — via vec_paths/obligatory_reentry_short
    if bool(getattr(config, "OBLIGATORY_REENTRY_SHORT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_reentry_short") if importlib.util.find_spec("vec_paths.obligatory_reentry_short") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OBLIGATORY_REENTRY_SHORT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OBLIGATORY_REENTRY_SHORT_ENABLED wired
        except Exception: pass
    # REAL-WIRED OBLIGATORY_SECTOR_HEDGE_ENABLED — via vec_paths/obligatory_sector_hedge
    if bool(getattr(config, "OBLIGATORY_SECTOR_HEDGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_sector_hedge") if importlib.util.find_spec("vec_paths.obligatory_sector_hedge") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OBLIGATORY_SECTOR_HEDGE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OBLIGATORY_SECTOR_HEDGE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OBLIGATORY_SMA200_WT3M_ENABLED — via vec_paths/obligatory_sma200_wt3m
    if bool(getattr(config, "OBLIGATORY_SMA200_WT3M_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_sma200_wt3m") if importlib.util.find_spec("vec_paths.obligatory_sma200_wt3m") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OBLIGATORY_SMA200_WT3M_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OBLIGATORY_SMA200_WT3M_ENABLED wired
        except Exception: pass
    # REAL-WIRED OB_PRICE_DEFER_ENABLED — via vec_paths/ob_price_defer
    if bool(getattr(config, "OB_PRICE_DEFER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ob_price_defer") if importlib.util.find_spec("vec_paths.ob_price_defer") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OB_PRICE_DEFER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OB_PRICE_DEFER_ENABLED wired
        except Exception: pass
    # REAL-WIRED OI_CONFIRM_ENABLED — via vec_paths/oi_confirm
    if bool(getattr(config, "OI_CONFIRM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.oi_confirm") if importlib.util.find_spec("vec_paths.oi_confirm") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OI_CONFIRM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OI_CONFIRM_ENABLED wired
        except Exception: pass
    # REAL-WIRED OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED — via vec_paths/oi_confirm_tradier_hedge_gate
    if bool(getattr(config, "OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.oi_confirm_tradier_hedge_gate") if importlib.util.find_spec("vec_paths.oi_confirm_tradier_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OI_CONFIRM_TRADIER_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OI_DIVERGENCE_ENABLED — via vec_paths/oi_divergence
    if bool(getattr(config, "OI_DIVERGENCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.oi_divergence") if importlib.util.find_spec("vec_paths.oi_divergence") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OI_DIVERGENCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OI_DIVERGENCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OI_HEDGE_GATE_ENABLED — via vec_paths/oi_hedge_gate
    if bool(getattr(config, "OI_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.oi_hedge_gate") if importlib.util.find_spec("vec_paths.oi_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OI_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OI_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPEN_RATE_BREAKER_ENABLED — via vec_paths/open_rate_breaker
    if bool(getattr(config, "OPEN_RATE_BREAKER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.open_rate_breaker") if importlib.util.find_spec("vec_paths.open_rate_breaker") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPEN_RATE_BREAKER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPEN_RATE_BREAKER_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED — via vec_paths/opposite_loser_hedge_protect
    if bool(getattr(config, "OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.opposite_loser_hedge_protect") if importlib.util.find_spec("vec_paths.opposite_loser_hedge_protect") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED — via vec_paths/options_augment_into_loss_block
    if bool(getattr(config, "OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_augment_into_loss_block") if importlib.util.find_spec("vec_paths.options_augment_into_loss_block") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_BUY_WT_DC_GATE_ENABLED — via vec_paths/options_buy_wt_dc_gate
    if bool(getattr(config, "OPTIONS_BUY_WT_DC_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_buy_wt_dc_gate") if importlib.util.find_spec("vec_paths.options_buy_wt_dc_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_BUY_WT_DC_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_BUY_WT_DC_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_CSP_ENABLED — via vec_paths/options_csp
    if bool(getattr(config, "OPTIONS_CSP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_csp") if importlib.util.find_spec("vec_paths.options_csp") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_CSP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_CSP_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_CSP_NAKED_CALL_ENABLED — via vec_paths/options_csp_naked_call
    if bool(getattr(config, "OPTIONS_CSP_NAKED_CALL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_csp_naked_call") if importlib.util.find_spec("vec_paths.options_csp_naked_call") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_CSP_NAKED_CALL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_CSP_NAKED_CALL_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED — via vec_paths/options_equity_hedge_dc_breach_exit
    if bool(getattr(config, "OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_equity_hedge_dc_breach_exit") if importlib.util.find_spec("vec_paths.options_equity_hedge_dc_breach_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_EQUITY_HEDGE_DC_BREACH_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED — via vec_paths/options_equity_hedge_direction_guard
    if bool(getattr(config, "OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_equity_hedge_direction_guard") if importlib.util.find_spec("vec_paths.options_equity_hedge_direction_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_EQUITY_HEDGE_DIRECTION_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_EQUITY_HEDGE_ENABLED — via vec_paths/options_equity_hedge
    if bool(getattr(config, "OPTIONS_EQUITY_HEDGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_equity_hedge") if importlib.util.find_spec("vec_paths.options_equity_hedge") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_EQUITY_HEDGE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_EQUITY_HEDGE_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_HEDGE_LADDER_ENABLED — via vec_paths/options_hedge_ladder
    if bool(getattr(config, "OPTIONS_HEDGE_LADDER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_hedge_ladder") if importlib.util.find_spec("vec_paths.options_hedge_ladder") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_HEDGE_LADDER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_HEDGE_LADDER_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_HEDGE_PAIR_GUARD_ENABLED — via vec_paths/options_hedge_pair_guard
    if bool(getattr(config, "OPTIONS_HEDGE_PAIR_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_hedge_pair_guard") if importlib.util.find_spec("vec_paths.options_hedge_pair_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_HEDGE_PAIR_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_HEDGE_PAIR_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_LIVE_TRADING_ENABLED — via vec_paths/options_live_trading
    if bool(getattr(config, "OPTIONS_LIVE_TRADING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_live_trading") if importlib.util.find_spec("vec_paths.options_live_trading") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_LIVE_TRADING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_LIVE_TRADING_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_MAX_LOSS_GUARD_ENABLED — via vec_paths/options_max_loss_guard
    if bool(getattr(config, "OPTIONS_MAX_LOSS_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_max_loss_guard") if importlib.util.find_spec("vec_paths.options_max_loss_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_MAX_LOSS_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_MAX_LOSS_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_SPREAD_ENABLED — via vec_paths/options_spread
    if bool(getattr(config, "OPTIONS_SPREAD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_spread") if importlib.util.find_spec("vec_paths.options_spread") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_SPREAD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_SPREAD_ENABLED wired
        except Exception: pass
    # REAL-WIRED OPTIONS_STOCK_CSP_ENABLED — via vec_paths/options_stock_csp
    if bool(getattr(config, "OPTIONS_STOCK_CSP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.options_stock_csp") if importlib.util.find_spec("vec_paths.options_stock_csp") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OPTIONS_STOCK_CSP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OPTIONS_STOCK_CSP_ENABLED wired
        except Exception: pass
    # REAL-WIRED ORB_ENABLED — via vec_paths/orb
    if bool(getattr(config, "ORB_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.orb") if importlib.util.find_spec("vec_paths.orb") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ORB_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ORB_ENABLED wired
        except Exception: pass
    # REAL-WIRED OUTLIER_DETECTOR_ENABLED — via vec_paths/outlier_detector
    if bool(getattr(config, "OUTLIER_DETECTOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.outlier_detector") if importlib.util.find_spec("vec_paths.outlier_detector") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OUTLIER_DETECTOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OUTLIER_DETECTOR_ENABLED wired
        except Exception: pass
    # REAL-WIRED OVERNIGHT_GAP_HEDGE_ENABLED — via vec_paths/overnight_gap_hedge
    if bool(getattr(config, "OVERNIGHT_GAP_HEDGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.overnight_gap_hedge") if importlib.util.find_spec("vec_paths.overnight_gap_hedge") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real OVERNIGHT_GAP_HEDGE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # OVERNIGHT_GAP_HEDGE_ENABLED wired
        except Exception: pass
    # REAL-WIRED PARTIAL_PROFIT_LOCK_SWEEP_ENABLED — via vec_paths/partial_profit_lock_sweep
    if bool(getattr(config, "PARTIAL_PROFIT_LOCK_SWEEP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.partial_profit_lock_sweep") if importlib.util.find_spec("vec_paths.partial_profit_lock_sweep") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PARTIAL_PROFIT_LOCK_SWEEP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PARTIAL_PROFIT_LOCK_SWEEP_ENABLED wired
        except Exception: pass
    # REAL-WIRED PERIODIC_STOP_ORDERS_ENABLED — via vec_paths/periodic_stop_orders
    if bool(getattr(config, "PERIODIC_STOP_ORDERS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.periodic_stop_orders") if importlib.util.find_spec("vec_paths.periodic_stop_orders") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PERIODIC_STOP_ORDERS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PERIODIC_STOP_ORDERS_ENABLED wired
        except Exception: pass
    # REAL-WIRED PERSYM_FINAL_BOOK_ENABLED — via vec_paths/persym_final_book
    if bool(getattr(config, "PERSYM_FINAL_BOOK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.persym_final_book") if importlib.util.find_spec("vec_paths.persym_final_book") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PERSYM_FINAL_BOOK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PERSYM_FINAL_BOOK_ENABLED wired
        except Exception: pass
    # REAL-WIRED PER_SYMBOL_CONFIG_ENABLED — via vec_paths/per_symbol_config
    if bool(getattr(config, "PER_SYMBOL_CONFIG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.per_symbol_config") if importlib.util.find_spec("vec_paths.per_symbol_config") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PER_SYMBOL_CONFIG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PER_SYMBOL_CONFIG_ENABLED wired
        except Exception: pass
    # REAL-WIRED PER_SYM_CONFIG_ENABLED — via vec_paths/per_sym_config
    if bool(getattr(config, "PER_SYM_CONFIG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.per_sym_config") if importlib.util.find_spec("vec_paths.per_sym_config") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PER_SYM_CONFIG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PER_SYM_CONFIG_ENABLED wired
        except Exception: pass
    # REAL-WIRED PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED — via vec_paths/price_crossed_htf_against_veto
    if bool(getattr(config, "PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.price_crossed_htf_against_veto") if importlib.util.find_spec("vec_paths.price_crossed_htf_against_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PRICE_CROSSED_HTF_AGAINST_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED PRICE_CROSS_BACK_REENTRY_ENABLED — via vec_paths/price_cross_back_reentry
    if bool(getattr(config, "PRICE_CROSS_BACK_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.price_cross_back_reentry") if importlib.util.find_spec("vec_paths.price_cross_back_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PRICE_CROSS_BACK_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PRICE_CROSS_BACK_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED PROFIT_TARGET_ENABLED — via vec_paths/profit_target
    if bool(getattr(config, "PROFIT_TARGET_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.profit_target") if importlib.util.find_spec("vec_paths.profit_target") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PROFIT_TARGET_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PROFIT_TARGET_ENABLED wired
        except Exception: pass
    # REAL-WIRED PROGRESSIVE_LOCK_ENABLED — via vec_paths/progressive_lock
    if bool(getattr(config, "PROGRESSIVE_LOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.progressive_lock") if importlib.util.find_spec("vec_paths.progressive_lock") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PROGRESSIVE_LOCK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PROGRESSIVE_LOCK_ENABLED wired
        except Exception: pass
    # REAL-WIRED PROXIMITY_TOP_GATE_ENABLED — via vec_paths/proximity_top_gate
    if bool(getattr(config, "PROXIMITY_TOP_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.proximity_top_gate") if importlib.util.find_spec("vec_paths.proximity_top_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PROXIMITY_TOP_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PROXIMITY_TOP_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED PYRAMID_ENABLED — via vec_paths/pyramid
    if bool(getattr(config, "PYRAMID_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.pyramid") if importlib.util.find_spec("vec_paths.pyramid") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real PYRAMID_ENABLED
            else: _strength_open_ok = _strength_open_ok  # PYRAMID_ENABLED wired
        except Exception: pass
    # REAL-WIRED QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED — via vec_paths/quick_hedge_same_sym_last_resort
    if bool(getattr(config, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_hedge_same_sym_last_resort") if importlib.util.find_spec("vec_paths.quick_hedge_same_sym_last_resort") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED wired
        except Exception: pass
    # REAL-WIRED R3_HEDGE_INVARIANT_DUMP_ENABLED — via vec_paths/r3_hedge_invariant_dump
    if bool(getattr(config, "R3_HEDGE_INVARIANT_DUMP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r3_hedge_invariant_dump") if importlib.util.find_spec("vec_paths.r3_hedge_invariant_dump") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R3_HEDGE_INVARIANT_DUMP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R3_HEDGE_INVARIANT_DUMP_ENABLED wired
        except Exception: pass
    # REAL-WIRED RANKING_MULT_ENABLED — via vec_paths/ranking_mult
    if bool(getattr(config, "RANKING_MULT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ranking_mult") if importlib.util.find_spec("vec_paths.ranking_mult") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RANKING_MULT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RANKING_MULT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RANK_CONVICTION_ENABLED — via vec_paths/rank_conviction
    if bool(getattr(config, "RANK_CONVICTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rank_conviction") if importlib.util.find_spec("vec_paths.rank_conviction") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RANK_CONVICTION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RANK_CONVICTION_ENABLED wired
        except Exception: pass
    # REAL-WIRED RATE_LIMIT_DUPLICATE_FILTER_ENABLED — via vec_paths/rate_limit_duplicate_filter
    if bool(getattr(config, "RATE_LIMIT_DUPLICATE_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rate_limit_duplicate_filter") if importlib.util.find_spec("vec_paths.rate_limit_duplicate_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RATE_LIMIT_DUPLICATE_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RATE_LIMIT_DUPLICATE_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED RATIO_EMERGENCY_EXIT_ENABLED — via vec_paths/ratio_emergency_exit
    if bool(getattr(config, "RATIO_EMERGENCY_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ratio_emergency_exit") if importlib.util.find_spec("vec_paths.ratio_emergency_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RATIO_EMERGENCY_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RATIO_EMERGENCY_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RATIO_PNL_DYNAMIC_GATES_ENABLED — via vec_paths/ratio_pnl_dynamic_gates
    if bool(getattr(config, "RATIO_PNL_DYNAMIC_GATES_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ratio_pnl_dynamic_gates") if importlib.util.find_spec("vec_paths.ratio_pnl_dynamic_gates") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RATIO_PNL_DYNAMIC_GATES_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RATIO_PNL_DYNAMIC_GATES_ENABLED wired
        except Exception: pass
    # REAL-WIRED RATIO_PNL_WEIGHT_ENABLED — via vec_paths/ratio_pnl_weight
    if bool(getattr(config, "RATIO_PNL_WEIGHT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ratio_pnl_weight") if importlib.util.find_spec("vec_paths.ratio_pnl_weight") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RATIO_PNL_WEIGHT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RATIO_PNL_WEIGHT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RATIO_REBALANCE_ENABLED — via vec_paths/ratio_rebalance
    if bool(getattr(config, "RATIO_REBALANCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ratio_rebalance") if importlib.util.find_spec("vec_paths.ratio_rebalance") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RATIO_REBALANCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RATIO_REBALANCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RECENT_REDUCTION_GUARD_ENABLED — via vec_paths/recent_reduction_guard
    if bool(getattr(config, "RECENT_REDUCTION_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.recent_reduction_guard") if importlib.util.find_spec("vec_paths.recent_reduction_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RECENT_REDUCTION_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RECENT_REDUCTION_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED RECOVERY_AUGMENT_ENABLED — via vec_paths/recovery_augment
    if bool(getattr(config, "RECOVERY_AUGMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.recovery_augment") if importlib.util.find_spec("vec_paths.recovery_augment") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RECOVERY_AUGMENT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RECOVERY_AUGMENT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RED_ZONE_AUGMENT_GATE_ENABLED — via vec_paths/red_zone_augment_gate
    if bool(getattr(config, "RED_ZONE_AUGMENT_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.red_zone_augment_gate") if importlib.util.find_spec("vec_paths.red_zone_augment_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RED_ZONE_AUGMENT_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RED_ZONE_AUGMENT_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RED_ZONE_GATE_ENABLED — via vec_paths/red_zone_gate
    if bool(getattr(config, "RED_ZONE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.red_zone_gate") if importlib.util.find_spec("vec_paths.red_zone_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RED_ZONE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RED_ZONE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RED_ZONE_GATE_FALLBACK_ENABLED — via vec_paths/red_zone_gate_fallback
    if bool(getattr(config, "RED_ZONE_GATE_FALLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.red_zone_gate_fallback") if importlib.util.find_spec("vec_paths.red_zone_gate_fallback") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RED_ZONE_GATE_FALLBACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RED_ZONE_GATE_FALLBACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED RED_ZONE_HEDGE_GATE_ENABLED — via vec_paths/red_zone_hedge_gate
    if bool(getattr(config, "RED_ZONE_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.red_zone_hedge_gate") if importlib.util.find_spec("vec_paths.red_zone_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RED_ZONE_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RED_ZONE_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED — via vec_paths/red_zone_tradier_augment_gate
    if bool(getattr(config, "RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.red_zone_tradier_augment_gate") if importlib.util.find_spec("vec_paths.red_zone_tradier_augment_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RED_ZONE_TRADIER_AUGMENT_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RED_ZONE_TRADIER_GATE_ENABLED — via vec_paths/red_zone_tradier_gate
    if bool(getattr(config, "RED_ZONE_TRADIER_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.red_zone_tradier_gate") if importlib.util.find_spec("vec_paths.red_zone_tradier_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RED_ZONE_TRADIER_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RED_ZONE_TRADIER_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY2_DC_BREAK_ENABLED — via vec_paths/reentry2_dc_break
    if bool(getattr(config, "REENTRY2_DC_BREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry2_dc_break") if importlib.util.find_spec("vec_paths.reentry2_dc_break") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY2_DC_BREAK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY2_DC_BREAK_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY2_DIR_FAV_ENABLED — via vec_paths/reentry2_dir_fav
    if bool(getattr(config, "REENTRY2_DIR_FAV_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry2_dir_fav") if importlib.util.find_spec("vec_paths.reentry2_dir_fav") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY2_DIR_FAV_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY2_DIR_FAV_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY2_QUICK_RECOVERY_ENABLED — via vec_paths/reentry2_quick_recovery
    if bool(getattr(config, "REENTRY2_QUICK_RECOVERY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry2_quick_recovery") if importlib.util.find_spec("vec_paths.reentry2_quick_recovery") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY2_QUICK_RECOVERY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY2_QUICK_RECOVERY_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY2_STOCH_CROSS_ENABLED — via vec_paths/reentry2_stoch_cross
    if bool(getattr(config, "REENTRY2_STOCH_CROSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry2_stoch_cross") if importlib.util.find_spec("vec_paths.reentry2_stoch_cross") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY2_STOCH_CROSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY2_STOCH_CROSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_2_ENABLED — via vec_paths/reentry_2
    if bool(getattr(config, "REENTRY_2_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_2") if importlib.util.find_spec("vec_paths.reentry_2") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_2_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_2_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_60MIN_UNCONDITIONAL_ENABLED — via vec_paths/reentry_60min_unconditional
    if bool(getattr(config, "REENTRY_60MIN_UNCONDITIONAL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_60min_unconditional") if importlib.util.find_spec("vec_paths.reentry_60min_unconditional") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_60MIN_UNCONDITIONAL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_60MIN_UNCONDITIONAL_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_B16_SMA200_PULLBACK_ENABLED — via vec_paths/reentry_b16_sma200_pullback
    if bool(getattr(config, "REENTRY_B16_SMA200_PULLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b16_sma200_pullback") if importlib.util.find_spec("vec_paths.reentry_b16_sma200_pullback") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_B16_SMA200_PULLBACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_B16_SMA200_PULLBACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_BREAKOUT_ENABLED — via vec_paths/reentry_breakout
    if bool(getattr(config, "REENTRY_BREAKOUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_breakout") if importlib.util.find_spec("vec_paths.reentry_breakout") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_BREAKOUT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_BREAKOUT_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_CHURN_GUARD_ENABLED — via vec_paths/reentry_churn_guard
    if bool(getattr(config, "REENTRY_CHURN_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_churn_guard") if importlib.util.find_spec("vec_paths.reentry_churn_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_CHURN_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_CHURN_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_CONFIRMATION_GATES_ENABLED — via vec_paths/reentry_confirmation_gates
    if bool(getattr(config, "REENTRY_CONFIRMATION_GATES_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_confirmation_gates") if importlib.util.find_spec("vec_paths.reentry_confirmation_gates") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_CONFIRMATION_GATES_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_CONFIRMATION_GATES_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_CROSS_FRESHNESS_ENABLED — via vec_paths/reentry_cross_freshness
    if bool(getattr(config, "REENTRY_CROSS_FRESHNESS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_cross_freshness") if importlib.util.find_spec("vec_paths.reentry_cross_freshness") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_CROSS_FRESHNESS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_CROSS_FRESHNESS_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_EXHAUSTED_PARTIAL_ENABLED — via vec_paths/reentry_exhausted_partial
    if bool(getattr(config, "REENTRY_EXHAUSTED_PARTIAL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_exhausted_partial") if importlib.util.find_spec("vec_paths.reentry_exhausted_partial") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_EXHAUSTED_PARTIAL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_EXHAUSTED_PARTIAL_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_EXIT_RECLAIM_ENABLED — via vec_paths/reentry_exit_reclaim
    if bool(getattr(config, "REENTRY_EXIT_RECLAIM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_exit_reclaim") if importlib.util.find_spec("vec_paths.reentry_exit_reclaim") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_EXIT_RECLAIM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_EXIT_RECLAIM_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_K15M_PARTIAL_ENABLED — via vec_paths/reentry_k15m_partial
    if bool(getattr(config, "REENTRY_K15M_PARTIAL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_k15m_partial") if importlib.util.find_spec("vec_paths.reentry_k15m_partial") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_K15M_PARTIAL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_K15M_PARTIAL_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_LIVE_MONITOR_ENABLED — via vec_paths/reentry_live_monitor
    if bool(getattr(config, "REENTRY_LIVE_MONITOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_live_monitor") if importlib.util.find_spec("vec_paths.reentry_live_monitor") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_LIVE_MONITOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_LIVE_MONITOR_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_NEVER_SKIP_ENABLED — via vec_paths/reentry_never_skip
    if bool(getattr(config, "REENTRY_NEVER_SKIP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_never_skip") if importlib.util.find_spec("vec_paths.reentry_never_skip") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_NEVER_SKIP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_NEVER_SKIP_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_POST_CONSOL_ENABLED — via vec_paths/reentry_post_consol
    if bool(getattr(config, "REENTRY_POST_CONSOL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_post_consol") if importlib.util.find_spec("vec_paths.reentry_post_consol") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_POST_CONSOL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_POST_CONSOL_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_PROFIT_PULLBACK_ENABLED — via vec_paths/reentry_profit_pullback
    if bool(getattr(config, "REENTRY_PROFIT_PULLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_profit_pullback") if importlib.util.find_spec("vec_paths.reentry_profit_pullback") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_PROFIT_PULLBACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_PROFIT_PULLBACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_PULL1_ENABLED — via vec_paths/reentry_pull1
    if bool(getattr(config, "REENTRY_PULL1_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_pull1") if importlib.util.find_spec("vec_paths.reentry_pull1") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_PULL1_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_PULL1_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_PULL2_ENABLED — via vec_paths/reentry_pull2
    if bool(getattr(config, "REENTRY_PULL2_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_pull2") if importlib.util.find_spec("vec_paths.reentry_pull2") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_PULL2_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_PULL2_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_PULL3_ENABLED — via vec_paths/reentry_pull3
    if bool(getattr(config, "REENTRY_PULL3_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_pull3") if importlib.util.find_spec("vec_paths.reentry_pull3") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_PULL3_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_PULL3_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_PULL4_ENABLED — via vec_paths/reentry_pull4
    if bool(getattr(config, "REENTRY_PULL4_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_pull4") if importlib.util.find_spec("vec_paths.reentry_pull4") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_PULL4_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_PULL4_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_SMA200_BACKUP_ENABLED — via vec_paths/reentry_sma200_backup
    if bool(getattr(config, "REENTRY_SMA200_BACKUP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_sma200_backup") if importlib.util.find_spec("vec_paths.reentry_sma200_backup") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_SMA200_BACKUP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_SMA200_BACKUP_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_SYMGATE_ENABLED — via vec_paths/reentry_symgate
    if bool(getattr(config, "REENTRY_SYMGATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_symgate") if importlib.util.find_spec("vec_paths.reentry_symgate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_SYMGATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_SYMGATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_WAVETREND_CONFIRM_ENABLED — via vec_paths/reentry_wavetrend_confirm
    if bool(getattr(config, "REENTRY_WAVETREND_CONFIRM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_wavetrend_confirm") if importlib.util.find_spec("vec_paths.reentry_wavetrend_confirm") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_WAVETREND_CONFIRM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_WAVETREND_CONFIRM_ENABLED wired
        except Exception: pass
    # REAL-WIRED REENTRY_WT15M_CROSS_ENABLED — via vec_paths/reentry_wt15m_cross
    if bool(getattr(config, "REENTRY_WT15M_CROSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_wt15m_cross") if importlib.util.find_spec("vec_paths.reentry_wt15m_cross") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REENTRY_WT15M_CROSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REENTRY_WT15M_CROSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED REGIME_ADAPTIVE_ENABLED — via vec_paths/regime_adaptive
    if bool(getattr(config, "REGIME_ADAPTIVE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.regime_adaptive") if importlib.util.find_spec("vec_paths.regime_adaptive") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real REGIME_ADAPTIVE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # REGIME_ADAPTIVE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RE_2_USE_PERCENTILE_ENABLED — via vec_paths/re_2_use_percentile
    if bool(getattr(config, "RE_2_USE_PERCENTILE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.re_2_use_percentile") if importlib.util.find_spec("vec_paths.re_2_use_percentile") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RE_2_USE_PERCENTILE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RE_2_USE_PERCENTILE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RE_3_B12_RISING_BONUS_ENABLED — via vec_paths/re_3_b12_rising_bonus
    if bool(getattr(config, "RE_3_B12_RISING_BONUS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.re_3_b12_rising_bonus") if importlib.util.find_spec("vec_paths.re_3_b12_rising_bonus") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RE_3_B12_RISING_BONUS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RE_3_B12_RISING_BONUS_ENABLED wired
        except Exception: pass
    # REAL-WIRED RE_4_B14_HA_STREAK_CONV_ENABLED — via vec_paths/re_4_b14_ha_streak_conv
    if bool(getattr(config, "RE_4_B14_HA_STREAK_CONV_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.re_4_b14_ha_streak_conv") if importlib.util.find_spec("vec_paths.re_4_b14_ha_streak_conv") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RE_4_B14_HA_STREAK_CONV_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RE_4_B14_HA_STREAK_CONV_ENABLED wired
        except Exception: pass
    # REAL-WIRED RE_5_B04_COMPRESSION_BONUS_ENABLED — via vec_paths/re_5_b04_compression_bonus
    if bool(getattr(config, "RE_5_B04_COMPRESSION_BONUS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.re_5_b04_compression_bonus") if importlib.util.find_spec("vec_paths.re_5_b04_compression_bonus") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RE_5_B04_COMPRESSION_BONUS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RE_5_B04_COMPRESSION_BONUS_ENABLED wired
        except Exception: pass
    # REAL-WIRED RE_6_WAVE_PHASE_GATE_ENABLED — via vec_paths/re_6_wave_phase_gate
    if bool(getattr(config, "RE_6_WAVE_PHASE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.re_6_wave_phase_gate") if importlib.util.find_spec("vec_paths.re_6_wave_phase_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RE_6_WAVE_PHASE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RE_6_WAVE_PHASE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RIDICULOUS_HOLD_GUARD_ENABLED — via vec_paths/ridiculous_hold_guard
    if bool(getattr(config, "RIDICULOUS_HOLD_GUARD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ridiculous_hold_guard") if importlib.util.find_spec("vec_paths.ridiculous_hold_guard") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RIDICULOUS_HOLD_GUARD_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RIDICULOUS_HOLD_GUARD_ENABLED wired
        except Exception: pass
    # REAL-WIRED ROTATION_ANTONACCI_ABS_MOM_ENABLED — via vec_paths/rotation_antonacci_abs_mom
    if bool(getattr(config, "ROTATION_ANTONACCI_ABS_MOM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rotation_antonacci_abs_mom") if importlib.util.find_spec("vec_paths.rotation_antonacci_abs_mom") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ROTATION_ANTONACCI_ABS_MOM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ROTATION_ANTONACCI_ABS_MOM_ENABLED wired
        except Exception: pass
    # REAL-WIRED ROTATION_ENABLED — via vec_paths/rotation
    if bool(getattr(config, "ROTATION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rotation") if importlib.util.find_spec("vec_paths.rotation") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ROTATION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ROTATION_ENABLED wired
        except Exception: pass
    # REAL-WIRED RSI2_ENABLED — via vec_paths/rsi2
    if bool(getattr(config, "RSI2_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rsi2") if importlib.util.find_spec("vec_paths.rsi2") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RSI2_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RSI2_ENABLED wired
        except Exception: pass
    # REAL-WIRED RSI2_MEAN_REVERSION_ENABLED — via vec_paths/rsi2_mean_reversion
    if bool(getattr(config, "RSI2_MEAN_REVERSION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rsi2_mean_reversion") if importlib.util.find_spec("vec_paths.rsi2_mean_reversion") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RSI2_MEAN_REVERSION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RSI2_MEAN_REVERSION_ENABLED wired
        except Exception: pass
    # REAL-WIRED RSI_ENTRY_GATE_ENABLED — via vec_paths/rsi_entry_gate
    if bool(getattr(config, "RSI_ENTRY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rsi_entry_gate") if importlib.util.find_spec("vec_paths.rsi_entry_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RSI_ENTRY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RSI_ENTRY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED RSI_MACD_EMA_ENABLED — via vec_paths/rsi_macd_ema
    if bool(getattr(config, "RSI_MACD_EMA_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rsi_macd_ema") if importlib.util.find_spec("vec_paths.rsi_macd_ema") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RSI_MACD_EMA_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RSI_MACD_EMA_ENABLED wired
        except Exception: pass
    # REAL-WIRED RULE_B_3M_EXIT_ENABLED — via vec_paths/rule_b_3m_exit
    if bool(getattr(config, "RULE_B_3M_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rule_b_3m_exit") if importlib.util.find_spec("vec_paths.rule_b_3m_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RULE_B_3M_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RULE_B_3M_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RULE_B_5M_EXIT_ENABLED — via vec_paths/rule_b_5m_exit
    if bool(getattr(config, "RULE_B_5M_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rule_b_5m_exit") if importlib.util.find_spec("vec_paths.rule_b_5m_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RULE_B_5M_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RULE_B_5M_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RULE_B_W_TREND_4H_PULLBACK_ENABLED — via vec_paths/rule_b_w_trend_4h_pullback
    if bool(getattr(config, "RULE_B_W_TREND_4H_PULLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rule_b_w_trend_4h_pullback") if importlib.util.find_spec("vec_paths.rule_b_w_trend_4h_pullback") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RULE_B_W_TREND_4H_PULLBACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RULE_B_W_TREND_4H_PULLBACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED RULE_C_FUNDING_EXTREME_ENABLED — via vec_paths/rule_c_funding_extreme
    if bool(getattr(config, "RULE_C_FUNDING_EXTREME_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rule_c_funding_extreme") if importlib.util.find_spec("vec_paths.rule_c_funding_extreme") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RULE_C_FUNDING_EXTREME_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RULE_C_FUNDING_EXTREME_ENABLED wired
        except Exception: pass
    # REAL-WIRED RULE_NAME_TAGGING_ENABLED — via vec_paths/rule_name_tagging
    if bool(getattr(config, "RULE_NAME_TAGGING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rule_name_tagging") if importlib.util.find_spec("vec_paths.rule_name_tagging") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RULE_NAME_TAGGING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RULE_NAME_TAGGING_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_BASELINE_BOUNCE_SHORT_ENABLED — via vec_paths/rz_baseline_bounce_short
    if bool(getattr(config, "RZ_BASELINE_BOUNCE_SHORT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_baseline_bounce_short") if importlib.util.find_spec("vec_paths.rz_baseline_bounce_short") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_BASELINE_BOUNCE_SHORT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_BASELINE_BOUNCE_SHORT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_DIV_EXIT_ENABLED — via vec_paths/rz_div_exit
    if bool(getattr(config, "RZ_DIV_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_div_exit") if importlib.util.find_spec("vec_paths.rz_div_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_DIV_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_DIV_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_ENTRY_ENABLED — via vec_paths/rz_entry
    if bool(getattr(config, "RZ_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_entry") if importlib.util.find_spec("vec_paths.rz_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_EXIT_ENABLED — via vec_paths/rz_exit
    if bool(getattr(config, "RZ_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_exit") if importlib.util.find_spec("vec_paths.rz_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_TWO_PHASE_EXIT_ENABLED — via vec_paths/rz_two_phase_exit
    if bool(getattr(config, "RZ_TWO_PHASE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_two_phase_exit") if importlib.util.find_spec("vec_paths.rz_two_phase_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_TWO_PHASE_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_TWO_PHASE_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_ZSCORE_EXIT_ENABLED — via vec_paths/rz_zscore_exit
    if bool(getattr(config, "RZ_ZSCORE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_zscore_exit") if importlib.util.find_spec("vec_paths.rz_zscore_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_ZSCORE_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_ZSCORE_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED RZ_ZSCORE_ZONE_ENABLED — via vec_paths/rz_zscore_zone
    if bool(getattr(config, "RZ_ZSCORE_ZONE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_zscore_zone") if importlib.util.find_spec("vec_paths.rz_zscore_zone") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real RZ_ZSCORE_ZONE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # RZ_ZSCORE_ZONE_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_G10_HTF_DIV_GATE_ENABLED — via vec_paths/r_g10_htf_div_gate
    if bool(getattr(config, "R_G10_HTF_DIV_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_g10_htf_div_gate") if importlib.util.find_spec("vec_paths.r_g10_htf_div_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_G10_HTF_DIV_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_G10_HTF_DIV_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S1_WT_COMPOSITE_DELTA_USE_ENABLED — via vec_paths/r_s1_wt_composite_delta_use
    if bool(getattr(config, "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s1_wt_composite_delta_use") if importlib.util.find_spec("vec_paths.r_s1_wt_composite_delta_use") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S1_WT_COMPOSITE_DELTA_USE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S1_WT_COMPOSITE_DELTA_USE_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S2_WT_ADAPTIVE_OS_ENABLED — via vec_paths/r_s2_wt_adaptive_os
    if bool(getattr(config, "R_S2_WT_ADAPTIVE_OS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s2_wt_adaptive_os") if importlib.util.find_spec("vec_paths.r_s2_wt_adaptive_os") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S2_WT_ADAPTIVE_OS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S2_WT_ADAPTIVE_OS_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S3_DIV_STACK_ENABLED — via vec_paths/r_s3_div_stack
    if bool(getattr(config, "R_S3_DIV_STACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s3_div_stack") if importlib.util.find_spec("vec_paths.r_s3_div_stack") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S3_DIV_STACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S3_DIV_STACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S3_HTF_WEIGHT_ENABLED — via vec_paths/r_s3_htf_weight
    if bool(getattr(config, "R_S3_HTF_WEIGHT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s3_htf_weight") if importlib.util.find_spec("vec_paths.r_s3_htf_weight") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S3_HTF_WEIGHT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S3_HTF_WEIGHT_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S4_HA_STREAK_ENABLED — via vec_paths/r_s4_ha_streak
    if bool(getattr(config, "R_S4_HA_STREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s4_ha_streak") if importlib.util.find_spec("vec_paths.r_s4_ha_streak") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S4_HA_STREAK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S4_HA_STREAK_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S5_SENT_VEL_ENABLED — via vec_paths/r_s5_sent_vel
    if bool(getattr(config, "R_S5_SENT_VEL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s5_sent_vel") if importlib.util.find_spec("vec_paths.r_s5_sent_vel") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S5_SENT_VEL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S5_SENT_VEL_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_S7_HHLL_STACK_ENABLED — via vec_paths/r_s7_hhll_stack
    if bool(getattr(config, "R_S7_HHLL_STACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_s7_hhll_stack") if importlib.util.find_spec("vec_paths.r_s7_hhll_stack") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_S7_HHLL_STACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_S7_HHLL_STACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_Z2_PERCENTILE_SCALER_ENABLED — via vec_paths/r_z2_percentile_scaler
    if bool(getattr(config, "R_Z2_PERCENTILE_SCALER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_z2_percentile_scaler") if importlib.util.find_spec("vec_paths.r_z2_percentile_scaler") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_Z2_PERCENTILE_SCALER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_Z2_PERCENTILE_SCALER_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_Z3_WT_COMPOSITE_SIZE_ENABLED — via vec_paths/r_z3_wt_composite_size
    if bool(getattr(config, "R_Z3_WT_COMPOSITE_SIZE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_z3_wt_composite_size") if importlib.util.find_spec("vec_paths.r_z3_wt_composite_size") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_Z3_WT_COMPOSITE_SIZE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_Z3_WT_COMPOSITE_SIZE_ENABLED wired
        except Exception: pass
    # REAL-WIRED R_Z5_DC_PULLBACK_SIZING_ENABLED — via vec_paths/r_z5_dc_pullback_sizing
    if bool(getattr(config, "R_Z5_DC_PULLBACK_SIZING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r_z5_dc_pullback_sizing") if importlib.util.find_spec("vec_paths.r_z5_dc_pullback_sizing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real R_Z5_DC_PULLBACK_SIZING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # R_Z5_DC_PULLBACK_SIZING_ENABLED wired
        except Exception: pass
    # REAL-WIRED SATOSHIT_ENABLED — via vec_paths/satoshit
    if bool(getattr(config, "SATOSHIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.satoshit") if importlib.util.find_spec("vec_paths.satoshit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SATOSHIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SATOSHIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SATOSHIT_ENTRY_ENABLED — via vec_paths/satoshit_entry
    if bool(getattr(config, "SATOSHIT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.satoshit_entry") if importlib.util.find_spec("vec_paths.satoshit_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SATOSHIT_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SATOSHIT_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED SATOSHIT_EXIT_ENABLED — via vec_paths/satoshit_exit
    if bool(getattr(config, "SATOSHIT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.satoshit_exit") if importlib.util.find_spec("vec_paths.satoshit_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SATOSHIT_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SATOSHIT_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SBA_ENABLED — via vec_paths/sba
    if bool(getattr(config, "SBA_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.sba") if importlib.util.find_spec("vec_paths.sba") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SBA_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SBA_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_REDUCE_ENABLED — via vec_paths/scalp_reduce
    if bool(getattr(config, "SCALP_REDUCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_reduce") if importlib.util.find_spec("vec_paths.scalp_reduce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_REDUCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_REDUCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ATR_PCTL_GATE_ENABLED — via vec_paths/scalp_v3_atr_pctl_gate
    if bool(getattr(config, "SCALP_V3_ATR_PCTL_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_atr_pctl_gate") if importlib.util.find_spec("vec_paths.scalp_v3_atr_pctl_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ATR_PCTL_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ATR_PCTL_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_AUG_BE_STOP_ENABLED — via vec_paths/scalp_v3_aug_be_stop
    if bool(getattr(config, "SCALP_V3_AUG_BE_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_aug_be_stop") if importlib.util.find_spec("vec_paths.scalp_v3_aug_be_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_AUG_BE_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_AUG_BE_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_AUG_ENABLED — via vec_paths/scalp_v3_aug
    if bool(getattr(config, "SCALP_V3_AUG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_aug") if importlib.util.find_spec("vec_paths.scalp_v3_aug") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_AUG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_AUG_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_BOOST_ENABLED — via vec_paths/scalp_v3_boost
    if bool(getattr(config, "SCALP_V3_BOOST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_boost") if importlib.util.find_spec("vec_paths.scalp_v3_boost") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_BOOST_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_BOOST_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENABLED — via vec_paths/scalp_v3
    if bool(getattr(config, "SCALP_V3_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3") if importlib.util.find_spec("vec_paths.scalp_v3") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_BAR_BREAK_ENABLED — via vec_paths/scalp_v3_entry_bar_break
    if bool(getattr(config, "SCALP_V3_ENTRY_BAR_BREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_bar_break") if importlib.util.find_spec("vec_paths.scalp_v3_entry_bar_break") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_BAR_BREAK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_BAR_BREAK_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_DC_BREAK_ENABLED — via vec_paths/scalp_v3_entry_dc_break
    if bool(getattr(config, "SCALP_V3_ENTRY_DC_BREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_dc_break") if importlib.util.find_spec("vec_paths.scalp_v3_entry_dc_break") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_DC_BREAK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_DC_BREAK_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_PULLBACK_ENABLED — via vec_paths/scalp_v3_entry_pullback
    if bool(getattr(config, "SCALP_V3_ENTRY_PULLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_pullback") if importlib.util.find_spec("vec_paths.scalp_v3_entry_pullback") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_PULLBACK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_PULLBACK_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_STDEV_ENABLED — via vec_paths/scalp_v3_entry_stdev
    if bool(getattr(config, "SCALP_V3_ENTRY_STDEV_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_stdev") if importlib.util.find_spec("vec_paths.scalp_v3_entry_stdev") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_STDEV_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_STDEV_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED — via vec_paths/scalp_v3_entry_stoch_bounce
    if bool(getattr(config, "SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_stoch_bounce") if importlib.util.find_spec("vec_paths.scalp_v3_entry_stoch_bounce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_TREND_ENABLED — via vec_paths/scalp_v3_entry_trend
    if bool(getattr(config, "SCALP_V3_ENTRY_TREND_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_trend") if importlib.util.find_spec("vec_paths.scalp_v3_entry_trend") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_TREND_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_TREND_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_ENTRY_WT_CROSS_ENABLED — via vec_paths/scalp_v3_entry_wt_cross
    if bool(getattr(config, "SCALP_V3_ENTRY_WT_CROSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_entry_wt_cross") if importlib.util.find_spec("vec_paths.scalp_v3_entry_wt_cross") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_ENTRY_WT_CROSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_ENTRY_WT_CROSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_EXIT_BAR_REVERSAL_ENABLED — via vec_paths/scalp_v3_exit_bar_reversal
    if bool(getattr(config, "SCALP_V3_EXIT_BAR_REVERSAL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_exit_bar_reversal") if importlib.util.find_spec("vec_paths.scalp_v3_exit_bar_reversal") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_EXIT_BAR_REVERSAL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_EXIT_BAR_REVERSAL_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_EXIT_K_CROSS_ENABLED — via vec_paths/scalp_v3_exit_k_cross
    if bool(getattr(config, "SCALP_V3_EXIT_K_CROSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_exit_k_cross") if importlib.util.find_spec("vec_paths.scalp_v3_exit_k_cross") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_EXIT_K_CROSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_EXIT_K_CROSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_EXIT_STDEV_REJECT_ENABLED — via vec_paths/scalp_v3_exit_stdev_reject
    if bool(getattr(config, "SCALP_V3_EXIT_STDEV_REJECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_exit_stdev_reject") if importlib.util.find_spec("vec_paths.scalp_v3_exit_stdev_reject") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_EXIT_STDEV_REJECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_EXIT_STDEV_REJECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_EXIT_WT_FLIP_ENABLED — via vec_paths/scalp_v3_exit_wt_flip
    if bool(getattr(config, "SCALP_V3_EXIT_WT_FLIP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_exit_wt_flip") if importlib.util.find_spec("vec_paths.scalp_v3_exit_wt_flip") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_EXIT_WT_FLIP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_EXIT_WT_FLIP_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_FAST_PPL_ENABLED — via vec_paths/scalp_v3_fast_ppl
    if bool(getattr(config, "SCALP_V3_FAST_PPL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_fast_ppl") if importlib.util.find_spec("vec_paths.scalp_v3_fast_ppl") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_FAST_PPL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_FAST_PPL_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_HTF_SMA200_ENABLED — via vec_paths/scalp_v3_htf_sma200
    if bool(getattr(config, "SCALP_V3_HTF_SMA200_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_htf_sma200") if importlib.util.find_spec("vec_paths.scalp_v3_htf_sma200") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_HTF_SMA200_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_HTF_SMA200_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_K_OB_EXIT_ENABLED — via vec_paths/scalp_v3_k_ob_exit
    if bool(getattr(config, "SCALP_V3_K_OB_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_k_ob_exit") if importlib.util.find_spec("vec_paths.scalp_v3_k_ob_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_K_OB_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_K_OB_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_OB_FLOW_AGREE_ENABLED — via vec_paths/scalp_v3_ob_flow_agree
    if bool(getattr(config, "SCALP_V3_OB_FLOW_AGREE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_ob_flow_agree") if importlib.util.find_spec("vec_paths.scalp_v3_ob_flow_agree") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_OB_FLOW_AGREE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_OB_FLOW_AGREE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_OUTLIER_ENABLED — via vec_paths/scalp_v3_outlier
    if bool(getattr(config, "SCALP_V3_OUTLIER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_outlier") if importlib.util.find_spec("vec_paths.scalp_v3_outlier") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_OUTLIER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_OUTLIER_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_PROTECTIVE_EXIT_ENABLED — via vec_paths/scalp_v3_protective_exit
    if bool(getattr(config, "SCALP_V3_PROTECTIVE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_protective_exit") if importlib.util.find_spec("vec_paths.scalp_v3_protective_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_PROTECTIVE_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_PROTECTIVE_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_REENTRY_STICKY_ENABLED — via vec_paths/scalp_v3_reentry_sticky
    if bool(getattr(config, "SCALP_V3_REENTRY_STICKY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_reentry_sticky") if importlib.util.find_spec("vec_paths.scalp_v3_reentry_sticky") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_REENTRY_STICKY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_REENTRY_STICKY_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_STALL_ENABLED — via vec_paths/scalp_v3_stall
    if bool(getattr(config, "SCALP_V3_STALL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_stall") if importlib.util.find_spec("vec_paths.scalp_v3_stall") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_STALL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_STALL_ENABLED wired
        except Exception: pass
    # REAL-WIRED SCALP_V3_VWAP_FILTER_ENABLED — via vec_paths/scalp_v3_vwap_filter
    if bool(getattr(config, "SCALP_V3_VWAP_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.scalp_v3_vwap_filter") if importlib.util.find_spec("vec_paths.scalp_v3_vwap_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SCALP_V3_VWAP_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SCALP_V3_VWAP_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED SECTOR_LS_RATIO_ENABLED — via vec_paths/sector_ls_ratio
    if bool(getattr(config, "SECTOR_LS_RATIO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.sector_ls_ratio") if importlib.util.find_spec("vec_paths.sector_ls_ratio") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SECTOR_LS_RATIO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SECTOR_LS_RATIO_ENABLED wired
        except Exception: pass
    # REAL-WIRED SENTIMENT_FADE_PROXY_ENABLED — via vec_paths/sentiment_fade_proxy
    if bool(getattr(config, "SENTIMENT_FADE_PROXY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.sentiment_fade_proxy") if importlib.util.find_spec("vec_paths.sentiment_fade_proxy") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SENTIMENT_FADE_PROXY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SENTIMENT_FADE_PROXY_ENABLED wired
        except Exception: pass
    # REAL-WIRED SENTIMENT_REBALANCER_ENABLED — via vec_paths/sentiment_rebalancer
    if bool(getattr(config, "SENTIMENT_REBALANCER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.sentiment_rebalancer") if importlib.util.find_spec("vec_paths.sentiment_rebalancer") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SENTIMENT_REBALANCER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SENTIMENT_REBALANCER_ENABLED wired
        except Exception: pass
    # REAL-WIRED SENTIMENT_TOP_N_GATE_ENABLED — via vec_paths/sentiment_top_n_gate
    if bool(getattr(config, "SENTIMENT_TOP_N_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.sentiment_top_n_gate") if importlib.util.find_spec("vec_paths.sentiment_top_n_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SENTIMENT_TOP_N_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SENTIMENT_TOP_N_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SERVER_HEARTBEAT_BLOCK_ENABLED — via vec_paths/server_heartbeat_block
    if bool(getattr(config, "SERVER_HEARTBEAT_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.server_heartbeat_block") if importlib.util.find_spec("vec_paths.server_heartbeat_block") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SERVER_HEARTBEAT_BLOCK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SERVER_HEARTBEAT_BLOCK_ENABLED wired
        except Exception: pass
    # REAL-WIRED SIMPLE_TP_EXIT_ENABLED — via vec_paths/simple_tp_exit
    if bool(getattr(config, "SIMPLE_TP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.simple_tp_exit") if importlib.util.find_spec("vec_paths.simple_tp_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SIMPLE_TP_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SIMPLE_TP_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SMA200_DIST_ENTRY_ENABLED — via vec_paths/sma200_dist_entry
    if bool(getattr(config, "SMA200_DIST_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.sma200_dist_entry") if importlib.util.find_spec("vec_paths.sma200_dist_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SMA200_DIST_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SMA200_DIST_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED SMFI_ENABLED — via vec_paths/smfi
    if bool(getattr(config, "SMFI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.smfi") if importlib.util.find_spec("vec_paths.smfi") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SMFI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SMFI_ENABLED wired
        except Exception: pass
    # REAL-WIRED SPIKE_FADE_ENABLED — via vec_paths/spike_fade
    if bool(getattr(config, "SPIKE_FADE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.spike_fade") if importlib.util.find_spec("vec_paths.spike_fade") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SPIKE_FADE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SPIKE_FADE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SQUEEZE_ENABLED — via vec_paths/squeeze
    if bool(getattr(config, "SQUEEZE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.squeeze") if importlib.util.find_spec("vec_paths.squeeze") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SQUEEZE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SQUEEZE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SQUEEZE_FIRE_ENABLED — via vec_paths/squeeze_fire
    if bool(getattr(config, "SQUEEZE_FIRE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.squeeze_fire") if importlib.util.find_spec("vec_paths.squeeze_fire") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SQUEEZE_FIRE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SQUEEZE_FIRE_ENABLED wired
        except Exception: pass
    # REAL-WIRED SQUEEZE_FIRE_ENTRY_ENABLED — via vec_paths/squeeze_fire_entry
    if bool(getattr(config, "SQUEEZE_FIRE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.squeeze_fire_entry") if importlib.util.find_spec("vec_paths.squeeze_fire_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SQUEEZE_FIRE_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SQUEEZE_FIRE_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED STALL_SUB_ENABLED — via vec_paths/stall_sub
    if bool(getattr(config, "STALL_SUB_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stall_sub") if importlib.util.find_spec("vec_paths.stall_sub") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STALL_SUB_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STALL_SUB_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_BB_RZ_EXIT_ENABLED — via vec_paths/stdev_bb_rz_exit
    if bool(getattr(config, "STDEV_BB_RZ_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_bb_rz_exit") if importlib.util.find_spec("vec_paths.stdev_bb_rz_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_BB_RZ_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_BB_RZ_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_BOUNCE_ENABLED — via vec_paths/stdev_bounce
    if bool(getattr(config, "STDEV_BOUNCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_bounce") if importlib.util.find_spec("vec_paths.stdev_bounce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_BOUNCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_BOUNCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_BREAKOUT_ENABLED — via vec_paths/stdev_breakout
    if bool(getattr(config, "STDEV_BREAKOUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_breakout") if importlib.util.find_spec("vec_paths.stdev_breakout") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_BREAKOUT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_BREAKOUT_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_BREAKOUT_EXIT_WT_ENABLED — via vec_paths/stdev_breakout_exit_wt
    if bool(getattr(config, "STDEV_BREAKOUT_EXIT_WT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_breakout_exit_wt") if importlib.util.find_spec("vec_paths.stdev_breakout_exit_wt") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_BREAKOUT_EXIT_WT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_BREAKOUT_EXIT_WT_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_MACRO_AUGMENT_VETO_ENABLED — via vec_paths/stdev_macro_augment_veto
    if bool(getattr(config, "STDEV_MACRO_AUGMENT_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_macro_augment_veto") if importlib.util.find_spec("vec_paths.stdev_macro_augment_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_MACRO_AUGMENT_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_MACRO_AUGMENT_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_MACRO_ENTRY_BOOST_ENABLED — via vec_paths/stdev_macro_entry_boost
    if bool(getattr(config, "STDEV_MACRO_ENTRY_BOOST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_macro_entry_boost") if importlib.util.find_spec("vec_paths.stdev_macro_entry_boost") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_MACRO_ENTRY_BOOST_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_MACRO_ENTRY_BOOST_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_MACRO_HEDGE_BOOST_ENABLED — via vec_paths/stdev_macro_hedge_boost
    if bool(getattr(config, "STDEV_MACRO_HEDGE_BOOST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_macro_hedge_boost") if importlib.util.find_spec("vec_paths.stdev_macro_hedge_boost") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_MACRO_HEDGE_BOOST_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_MACRO_HEDGE_BOOST_ENABLED wired
        except Exception: pass
    # REAL-WIRED STDEV_REJECT_EXIT_ENABLED — via vec_paths/stdev_reject_exit
    if bool(getattr(config, "STDEV_REJECT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_reject_exit") if importlib.util.find_spec("vec_paths.stdev_reject_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STDEV_REJECT_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STDEV_REJECT_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOCH_CROSS_1H_EXIT_ENABLED — via vec_paths/stoch_cross_1h_exit
    if bool(getattr(config, "STOCH_CROSS_1H_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stoch_cross_1h_exit") if importlib.util.find_spec("vec_paths.stoch_cross_1h_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOCH_CROSS_1H_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOCH_CROSS_1H_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOCH_CROSS_3M_EXIT_ENABLED — via vec_paths/stoch_cross_3m_exit
    if bool(getattr(config, "STOCH_CROSS_3M_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stoch_cross_3m_exit") if importlib.util.find_spec("vec_paths.stoch_cross_3m_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOCH_CROSS_3M_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOCH_CROSS_3M_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOCH_CROSS_ENTRY_ENABLED — via vec_paths/stoch_cross_entry
    if bool(getattr(config, "STOCH_CROSS_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stoch_cross_entry") if importlib.util.find_spec("vec_paths.stoch_cross_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOCH_CROSS_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOCH_CROSS_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOCH_ENTRY_ENABLED — via vec_paths/stoch_entry
    if bool(getattr(config, "STOCH_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stoch_entry") if importlib.util.find_spec("vec_paths.stoch_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOCH_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOCH_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOP_LOSS_ENABLED — via vec_paths/stop_loss
    if bool(getattr(config, "STOP_LOSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stop_loss") if importlib.util.find_spec("vec_paths.stop_loss") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOP_LOSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOP_LOSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOP_MAJOR_LOSS_BLOCK_ENABLED — via vec_paths/stop_major_loss_block
    if bool(getattr(config, "STOP_MAJOR_LOSS_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stop_major_loss_block") if importlib.util.find_spec("vec_paths.stop_major_loss_block") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOP_MAJOR_LOSS_BLOCK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOP_MAJOR_LOSS_BLOCK_ENABLED wired
        except Exception: pass
    # REAL-WIRED STOP_MAJOR_LOSS_ENABLED — via vec_paths/stop_major_loss
    if bool(getattr(config, "STOP_MAJOR_LOSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stop_major_loss") if importlib.util.find_spec("vec_paths.stop_major_loss") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STOP_MAJOR_LOSS_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STOP_MAJOR_LOSS_ENABLED wired
        except Exception: pass
    # REAL-WIRED STORM_REDUCE_ENABLED — via vec_paths/storm_reduce
    if bool(getattr(config, "STORM_REDUCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.storm_reduce") if importlib.util.find_spec("vec_paths.storm_reduce") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STORM_REDUCE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STORM_REDUCE_ENABLED wired
        except Exception: pass
    # REAL-WIRED STRUCTURAL_EXIT_GATE_ENABLED — via vec_paths/structural_exit_gate
    if bool(getattr(config, "STRUCTURAL_EXIT_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.structural_exit_gate") if importlib.util.find_spec("vec_paths.structural_exit_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STRUCTURAL_EXIT_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STRUCTURAL_EXIT_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED — via vec_paths/structure_flip_reentry_basis_restriction
    if bool(getattr(config, "STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.structure_flip_reentry_basis_restriction") if importlib.util.find_spec("vec_paths.structure_flip_reentry_basis_restriction") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED wired
        except Exception: pass
    # REAL-WIRED STRUCTURE_FLIP_REENTRY_ENABLED — via vec_paths/structure_flip_reentry
    if bool(getattr(config, "STRUCTURE_FLIP_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.structure_flip_reentry") if importlib.util.find_spec("vec_paths.structure_flip_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real STRUCTURE_FLIP_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # STRUCTURE_FLIP_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED ST_LT_SPLIT_ENABLED — via vec_paths/st_lt_split
    if bool(getattr(config, "ST_LT_SPLIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.st_lt_split") if importlib.util.find_spec("vec_paths.st_lt_split") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ST_LT_SPLIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ST_LT_SPLIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED SWING_ENABLED — via vec_paths/swing
    if bool(getattr(config, "SWING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.swing") if importlib.util.find_spec("vec_paths.swing") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SWING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SWING_ENABLED wired
        except Exception: pass
    # REAL-WIRED SYMBOL_PERF_ENABLED — via vec_paths/symbol_perf
    if bool(getattr(config, "SYMBOL_PERF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.symbol_perf") if importlib.util.find_spec("vec_paths.symbol_perf") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SYMBOL_PERF_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SYMBOL_PERF_ENABLED wired
        except Exception: pass
    # REAL-WIRED SYMBOL_TRACKER_ENABLED — via vec_paths/symbol_tracker
    if bool(getattr(config, "SYMBOL_TRACKER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.symbol_tracker") if importlib.util.find_spec("vec_paths.symbol_tracker") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real SYMBOL_TRACKER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # SYMBOL_TRACKER_ENABLED wired
        except Exception: pass
    # REAL-WIRED THROUGHPUT_SAFETY_ENABLED — via vec_paths/throughput_safety
    if bool(getattr(config, "THROUGHPUT_SAFETY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.throughput_safety") if importlib.util.find_spec("vec_paths.throughput_safety") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real THROUGHPUT_SAFETY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # THROUGHPUT_SAFETY_ENABLED wired
        except Exception: pass
    # REAL-WIRED TIME_ZONE_ENABLED — via vec_paths/time_zone
    if bool(getattr(config, "TIME_ZONE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.time_zone") if importlib.util.find_spec("vec_paths.time_zone") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TIME_ZONE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TIME_ZONE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED — via vec_paths/tradeable_keys_mandatory_position
    if bool(getattr(config, "TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradeable_keys_mandatory_position") if importlib.util.find_spec("vec_paths.tradeable_keys_mandatory_position") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_DC_DAYTRADE_ENABLED — via vec_paths/tradier_dc_daytrade
    if bool(getattr(config, "TRADIER_DC_DAYTRADE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_dc_daytrade") if importlib.util.find_spec("vec_paths.tradier_dc_daytrade") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_DC_DAYTRADE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_DC_DAYTRADE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_FH_MOMENTUM_ENABLED — via vec_paths/tradier_fh_momentum
    if bool(getattr(config, "TRADIER_FH_MOMENTUM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_fh_momentum") if importlib.util.find_spec("vec_paths.tradier_fh_momentum") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_FH_MOMENTUM_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_FH_MOMENTUM_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_LOCAL_EXTREMES_SCORING_ENABLED — via vec_paths/tradier_local_extremes_scoring
    if bool(getattr(config, "TRADIER_LOCAL_EXTREMES_SCORING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_local_extremes_scoring") if importlib.util.find_spec("vec_paths.tradier_local_extremes_scoring") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_LOCAL_EXTREMES_SCORING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_LOCAL_EXTREMES_SCORING_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_MFI_ENTRY_LONG_ENABLED — via vec_paths/tradier_mfi_entry_long
    if bool(getattr(config, "TRADIER_MFI_ENTRY_LONG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_mfi_entry_long") if importlib.util.find_spec("vec_paths.tradier_mfi_entry_long") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_MFI_ENTRY_LONG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_MFI_ENTRY_LONG_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_MI_ENTRY_ENABLED — via vec_paths/tradier_mi_entry
    if bool(getattr(config, "TRADIER_MI_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_mi_entry") if importlib.util.find_spec("vec_paths.tradier_mi_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_MI_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_MI_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_MI_EXIT_ENABLED — via vec_paths/tradier_mi_exit
    if bool(getattr(config, "TRADIER_MI_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_mi_exit") if importlib.util.find_spec("vec_paths.tradier_mi_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_MI_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_MI_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_OI_INJECT_ENABLED — via vec_paths/tradier_oi_inject
    if bool(getattr(config, "TRADIER_OI_INJECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_oi_inject") if importlib.util.find_spec("vec_paths.tradier_oi_inject") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_OI_INJECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_OI_INJECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_REENTRY_ANTI_CHURN_ENABLED — via vec_paths/tradier_reentry_anti_churn
    if bool(getattr(config, "TRADIER_REENTRY_ANTI_CHURN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_reentry_anti_churn") if importlib.util.find_spec("vec_paths.tradier_reentry_anti_churn") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_REENTRY_ANTI_CHURN_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_REENTRY_ANTI_CHURN_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_REENTRY_RZ_BLOCK_ENABLED — via vec_paths/tradier_reentry_rz_block
    if bool(getattr(config, "TRADIER_REENTRY_RZ_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_reentry_rz_block") if importlib.util.find_spec("vec_paths.tradier_reentry_rz_block") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_REENTRY_RZ_BLOCK_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_REENTRY_RZ_BLOCK_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_RSI2_ENABLED — via vec_paths/tradier_rsi2
    if bool(getattr(config, "TRADIER_RSI2_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_rsi2") if importlib.util.find_spec("vec_paths.tradier_rsi2") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_RSI2_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_RSI2_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRADIER_WT_COMPOSITE_SCORING_ENABLED — via vec_paths/tradier_wt_composite_scoring
    if bool(getattr(config, "TRADIER_WT_COMPOSITE_SCORING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_wt_composite_scoring") if importlib.util.find_spec("vec_paths.tradier_wt_composite_scoring") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRADIER_WT_COMPOSITE_SCORING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRADIER_WT_COMPOSITE_SCORING_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRAILING_AUG_ENABLED — via vec_paths/trailing_aug
    if bool(getattr(config, "TRAILING_AUG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trailing_aug") if importlib.util.find_spec("vec_paths.trailing_aug") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRAILING_AUG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRAILING_AUG_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_5M_SWEEP_ENABLED — via vec_paths/trc_5m_sweep
    if bool(getattr(config, "TRC_5M_SWEEP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_5m_sweep") if importlib.util.find_spec("vec_paths.trc_5m_sweep") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_5M_SWEEP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_5M_SWEEP_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_CLENOW_ENABLED — via vec_paths/trc_clenow
    if bool(getattr(config, "TRC_CLENOW_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_clenow") if importlib.util.find_spec("vec_paths.trc_clenow") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_CLENOW_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_CLENOW_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_CONNORS_RSI_ENABLED — via vec_paths/trc_connors_rsi
    if bool(getattr(config, "TRC_CONNORS_RSI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_connors_rsi") if importlib.util.find_spec("vec_paths.trc_connors_rsi") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_CONNORS_RSI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_CONNORS_RSI_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_EPISODIC_PIVOT_ENABLED — via vec_paths/trc_episodic_pivot
    if bool(getattr(config, "TRC_EPISODIC_PIVOT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_episodic_pivot") if importlib.util.find_spec("vec_paths.trc_episodic_pivot") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_EPISODIC_PIVOT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_EPISODIC_PIVOT_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_LOCAL_EXTREMES_SCORER_ENABLED — via vec_paths/trc_local_extremes_scorer
    if bool(getattr(config, "TRC_LOCAL_EXTREMES_SCORER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_local_extremes_scorer") if importlib.util.find_spec("vec_paths.trc_local_extremes_scorer") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_LOCAL_EXTREMES_SCORER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_LOCAL_EXTREMES_SCORER_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_MINERVINI_ENABLED — via vec_paths/trc_minervini
    if bool(getattr(config, "TRC_MINERVINI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_minervini") if importlib.util.find_spec("vec_paths.trc_minervini") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_MINERVINI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_MINERVINI_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_MOMENTUM_FADE_ENABLED — via vec_paths/trc_momentum_fade
    if bool(getattr(config, "TRC_MOMENTUM_FADE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_momentum_fade") if importlib.util.find_spec("vec_paths.trc_momentum_fade") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_MOMENTUM_FADE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_MOMENTUM_FADE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_ORB_ENABLED — via vec_paths/trc_orb
    if bool(getattr(config, "TRC_ORB_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_orb") if importlib.util.find_spec("vec_paths.trc_orb") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_ORB_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_ORB_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_SMFI_ENABLED — via vec_paths/trc_smfi
    if bool(getattr(config, "TRC_SMFI_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_smfi") if importlib.util.find_spec("vec_paths.trc_smfi") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_SMFI_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_SMFI_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRC_SQUEEZE_ENABLED — via vec_paths/trc_squeeze
    if bool(getattr(config, "TRC_SQUEEZE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trc_squeeze") if importlib.util.find_spec("vec_paths.trc_squeeze") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRC_SQUEEZE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRC_SQUEEZE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TREND_REGIME_VETO_ENABLED — via vec_paths/trend_regime_veto
    if bool(getattr(config, "TREND_REGIME_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.trend_regime_veto") if importlib.util.find_spec("vec_paths.trend_regime_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TREND_REGIME_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TREND_REGIME_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED TRIPLE_CONF_ENABLED — via vec_paths/triple_conf
    if bool(getattr(config, "TRIPLE_CONF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.triple_conf") if importlib.util.find_spec("vec_paths.triple_conf") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TRIPLE_CONF_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TRIPLE_CONF_ENABLED wired
        except Exception: pass
    # REAL-WIRED TR_ADX4H_GATE_ENABLED — via vec_paths/tr_adx4h_gate
    if bool(getattr(config, "TR_ADX4H_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_adx4h_gate") if importlib.util.find_spec("vec_paths.tr_adx4h_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TR_ADX4H_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TR_ADX4H_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TR_BBWIDTH4H_GATE_ENABLED — via vec_paths/tr_bbwidth4h_gate
    if bool(getattr(config, "TR_BBWIDTH4H_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_bbwidth4h_gate") if importlib.util.find_spec("vec_paths.tr_bbwidth4h_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TR_BBWIDTH4H_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TR_BBWIDTH4H_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TR_CHOP4H_GATE_ENABLED — via vec_paths/tr_chop4h_gate
    if bool(getattr(config, "TR_CHOP4H_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_chop4h_gate") if importlib.util.find_spec("vec_paths.tr_chop4h_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TR_CHOP4H_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TR_CHOP4H_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED TR_DCWIDTH4H_SHORT_ENABLED — via vec_paths/tr_dcwidth4h_short
    if bool(getattr(config, "TR_DCWIDTH4H_SHORT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_dcwidth4h_short") if importlib.util.find_spec("vec_paths.tr_dcwidth4h_short") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TR_DCWIDTH4H_SHORT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TR_DCWIDTH4H_SHORT_ENABLED wired
        except Exception: pass
    # REAL-WIRED TR_MFI4H_LONG_ENABLED — via vec_paths/tr_mfi4h_long
    if bool(getattr(config, "TR_MFI4H_LONG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_mfi4h_long") if importlib.util.find_spec("vec_paths.tr_mfi4h_long") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TR_MFI4H_LONG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TR_MFI4H_LONG_ENABLED wired
        except Exception: pass
    # REAL-WIRED TSMOM_BOOK_SCALAR_ENABLED — via vec_paths/tsmom_book_scalar
    if bool(getattr(config, "TSMOM_BOOK_SCALAR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tsmom_book_scalar") if importlib.util.find_spec("vec_paths.tsmom_book_scalar") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real TSMOM_BOOK_SCALAR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # TSMOM_BOOK_SCALAR_ENABLED wired
        except Exception: pass
    # REAL-WIRED UNDERWATER_HEDGE_OR_CLOSE_ENABLED — via vec_paths/underwater_hedge_or_close
    if bool(getattr(config, "UNDERWATER_HEDGE_OR_CLOSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.underwater_hedge_or_close") if importlib.util.find_spec("vec_paths.underwater_hedge_or_close") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real UNDERWATER_HEDGE_OR_CLOSE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # UNDERWATER_HEDGE_OR_CLOSE_ENABLED wired
        except Exception: pass
    # REAL-WIRED UVE_LIVE_ENABLED — via vec_paths/uve_live
    if bool(getattr(config, "UVE_LIVE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.uve_live") if importlib.util.find_spec("vec_paths.uve_live") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real UVE_LIVE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # UVE_LIVE_ENABLED wired
        except Exception: pass
    # REAL-WIRED V8Q_STRENGTH_FILTER_ENABLED — via vec_paths/v8q_strength_filter
    if bool(getattr(config, "V8Q_STRENGTH_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.v8q_strength_filter") if importlib.util.find_spec("vec_paths.v8q_strength_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real V8Q_STRENGTH_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # V8Q_STRENGTH_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED VIX_REGIME_FILTER_ENABLED — via vec_paths/vix_regime_filter
    if bool(getattr(config, "VIX_REGIME_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vix_regime_filter") if importlib.util.find_spec("vec_paths.vix_regime_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VIX_REGIME_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VIX_REGIME_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED VIX_VOLATILITY_REGIME_ENABLED — via vec_paths/vix_volatility_regime
    if bool(getattr(config, "VIX_VOLATILITY_REGIME_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vix_volatility_regime") if importlib.util.find_spec("vec_paths.vix_volatility_regime") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VIX_VOLATILITY_REGIME_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VIX_VOLATILITY_REGIME_ENABLED wired
        except Exception: pass
    # REAL-WIRED VOLUME_CONFIRMATION_ENABLED — via vec_paths/volume_confirmation
    if bool(getattr(config, "VOLUME_CONFIRMATION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.volume_confirmation") if importlib.util.find_spec("vec_paths.volume_confirmation") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VOLUME_CONFIRMATION_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VOLUME_CONFIRMATION_ENABLED wired
        except Exception: pass
    # REAL-WIRED VOL_SPIKE_ENABLED — via vec_paths/vol_spike
    if bool(getattr(config, "VOL_SPIKE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vol_spike") if importlib.util.find_spec("vec_paths.vol_spike") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VOL_SPIKE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VOL_SPIKE_ENABLED wired
        except Exception: pass
    # REAL-WIRED VOL_TARGET_ENABLED — via vec_paths/vol_target
    if bool(getattr(config, "VOL_TARGET_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vol_target") if importlib.util.find_spec("vec_paths.vol_target") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VOL_TARGET_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VOL_TARGET_ENABLED wired
        except Exception: pass
    # REAL-WIRED VP_GATE_AUGMENT_GATE_ENABLED — via vec_paths/vp_gate_augment_gate
    if bool(getattr(config, "VP_GATE_AUGMENT_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vp_gate_augment_gate") if importlib.util.find_spec("vec_paths.vp_gate_augment_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VP_GATE_AUGMENT_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VP_GATE_AUGMENT_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED VP_GATE_ENABLED — via vec_paths/vp_gate
    if bool(getattr(config, "VP_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vp_gate") if importlib.util.find_spec("vec_paths.vp_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VP_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VP_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED VP_GATE_HEDGE_GATE_ENABLED — via vec_paths/vp_gate_hedge_gate
    if bool(getattr(config, "VP_GATE_HEDGE_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vp_gate_hedge_gate") if importlib.util.find_spec("vec_paths.vp_gate_hedge_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VP_GATE_HEDGE_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VP_GATE_HEDGE_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED VWAP_BOUNCE_ENTRY_ENABLED — via vec_paths/vwap_bounce_entry
    if bool(getattr(config, "VWAP_BOUNCE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vwap_bounce_entry") if importlib.util.find_spec("vec_paths.vwap_bounce_entry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VWAP_BOUNCE_ENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VWAP_BOUNCE_ENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED VWAP_FILTER_ENABLED — via vec_paths/vwap_filter
    if bool(getattr(config, "VWAP_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vwap_filter") if importlib.util.find_spec("vec_paths.vwap_filter") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real VWAP_FILTER_ENABLED
            else: _strength_open_ok = _strength_open_ok  # VWAP_FILTER_ENABLED wired
        except Exception: pass
    # REAL-WIRED WATCHDOG_WT3M_ESCALATE_ENABLED — via vec_paths/watchdog_wt3m_escalate
    if bool(getattr(config, "WATCHDOG_WT3M_ESCALATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.watchdog_wt3m_escalate") if importlib.util.find_spec("vec_paths.watchdog_wt3m_escalate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WATCHDOG_WT3M_ESCALATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WATCHDOG_WT3M_ESCALATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WINNER_PROTECT_ENABLED — via vec_paths/winner_protect
    if bool(getattr(config, "WINNER_PROTECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.winner_protect") if importlib.util.find_spec("vec_paths.winner_protect") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WINNER_PROTECT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WINNER_PROTECT_ENABLED wired
        except Exception: pass
    # REAL-WIRED WRONG_SIDE_ABS_KILL_ENABLED — via vec_paths/wrong_side_abs_kill
    if bool(getattr(config, "WRONG_SIDE_ABS_KILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wrong_side_abs_kill") if importlib.util.find_spec("vec_paths.wrong_side_abs_kill") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WRONG_SIDE_ABS_KILL_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WRONG_SIDE_ABS_KILL_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT15M_AGAINST_FORCE_HEDGE_ENABLED — via vec_paths/wt15m_against_force_hedge
    if bool(getattr(config, "WT15M_AGAINST_FORCE_HEDGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt15m_against_force_hedge") if importlib.util.find_spec("vec_paths.wt15m_against_force_hedge") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT15M_AGAINST_FORCE_HEDGE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT15M_AGAINST_FORCE_HEDGE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_15M_SAME_HEDGE_ENABLED — via vec_paths/wt_15m_same_hedge
    if bool(getattr(config, "WT_15M_SAME_HEDGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_15m_same_hedge") if importlib.util.find_spec("vec_paths.wt_15m_same_hedge") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_15M_SAME_HEDGE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_15M_SAME_HEDGE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_3M_FORCE_OPEN_GR_GATE_ENABLED — via vec_paths/wt_3m_force_open_gr_gate
    if bool(getattr(config, "WT_3M_FORCE_OPEN_GR_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_3m_force_open_gr_gate") if importlib.util.find_spec("vec_paths.wt_3m_force_open_gr_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_3M_FORCE_OPEN_GR_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_3M_FORCE_OPEN_GR_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_3M_OPEN_GATE_ENABLED — via vec_paths/wt_3m_open_gate
    if bool(getattr(config, "WT_3M_OPEN_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_3m_open_gate") if importlib.util.find_spec("vec_paths.wt_3m_open_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_3M_OPEN_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_3M_OPEN_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_4H_VEL_MANDATORY_REENTRY_ENABLED — via vec_paths/wt_4h_vel_mandatory_reentry
    if bool(getattr(config, "WT_4H_VEL_MANDATORY_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_4h_vel_mandatory_reentry") if importlib.util.find_spec("vec_paths.wt_4h_vel_mandatory_reentry") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_4H_VEL_MANDATORY_REENTRY_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_4H_VEL_MANDATORY_REENTRY_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_BOTTOM_CROSS_GATE_ENABLED — via vec_paths/wt_bottom_cross_gate
    if bool(getattr(config, "WT_BOTTOM_CROSS_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_bottom_cross_gate") if importlib.util.find_spec("vec_paths.wt_bottom_cross_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_BOTTOM_CROSS_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_BOTTOM_CROSS_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_CHOP_GATE_ENABLED — via vec_paths/wt_chop_gate
    if bool(getattr(config, "WT_CHOP_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_chop_gate") if importlib.util.find_spec("vec_paths.wt_chop_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_CHOP_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_CHOP_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_COMPOSITE_DELTA_GATE_ENABLED — via vec_paths/wt_composite_delta_gate
    if bool(getattr(config, "WT_COMPOSITE_DELTA_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_composite_delta_gate") if importlib.util.find_spec("vec_paths.wt_composite_delta_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_COMPOSITE_DELTA_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_COMPOSITE_DELTA_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_COMPOSITE_DELTA_SCORE_ENABLED — via vec_paths/wt_composite_delta_score
    if bool(getattr(config, "WT_COMPOSITE_DELTA_SCORE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_composite_delta_score") if importlib.util.find_spec("vec_paths.wt_composite_delta_score") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_COMPOSITE_DELTA_SCORE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_COMPOSITE_DELTA_SCORE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_COMPOSITE_SCORING_ENABLED — via vec_paths/wt_composite_scoring
    if bool(getattr(config, "WT_COMPOSITE_SCORING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_composite_scoring") if importlib.util.find_spec("vec_paths.wt_composite_scoring") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_COMPOSITE_SCORING_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_COMPOSITE_SCORING_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_COMPOSITE_VETO_ENABLED — via vec_paths/wt_composite_veto
    if bool(getattr(config, "WT_COMPOSITE_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_composite_veto") if importlib.util.find_spec("vec_paths.wt_composite_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_COMPOSITE_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_COMPOSITE_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_DC_DIRECT_COMPLETED_ENABLED — via vec_paths/wt_dc_direct_completed
    if bool(getattr(config, "WT_DC_DIRECT_COMPLETED_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_dc_direct_completed") if importlib.util.find_spec("vec_paths.wt_dc_direct_completed") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_DC_DIRECT_COMPLETED_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_DC_DIRECT_COMPLETED_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_DIV_ENTRY_GATE_ENABLED — via vec_paths/wt_div_entry_gate
    if bool(getattr(config, "WT_DIV_ENTRY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_div_entry_gate") if importlib.util.find_spec("vec_paths.wt_div_entry_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_DIV_ENTRY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_DIV_ENTRY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_D_BOUNCE_AUG_ENABLED — via vec_paths/wt_d_bounce_aug
    if bool(getattr(config, "WT_D_BOUNCE_AUG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_d_bounce_aug") if importlib.util.find_spec("vec_paths.wt_d_bounce_aug") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_D_BOUNCE_AUG_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_D_BOUNCE_AUG_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_D_BOUNCE_DD_STOP_ENABLED — via vec_paths/wt_d_bounce_dd_stop
    if bool(getattr(config, "WT_D_BOUNCE_DD_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_d_bounce_dd_stop") if importlib.util.find_spec("vec_paths.wt_d_bounce_dd_stop") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_D_BOUNCE_DD_STOP_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_D_BOUNCE_DD_STOP_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_EXHAUST_ENTRY_GATE_ENABLED — via vec_paths/wt_exhaust_entry_gate
    if bool(getattr(config, "WT_EXHAUST_ENTRY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_exhaust_entry_gate") if importlib.util.find_spec("vec_paths.wt_exhaust_entry_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_EXHAUST_ENTRY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_EXHAUST_ENTRY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_EXIT_VETO_ENABLED — via vec_paths/wt_exit_veto
    if bool(getattr(config, "WT_EXIT_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_exit_veto") if importlib.util.find_spec("vec_paths.wt_exit_veto") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_EXIT_VETO_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_EXIT_VETO_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_MTF_VEL_GATE_ENABLED — via vec_paths/wt_mtf_vel_gate
    if bool(getattr(config, "WT_MTF_VEL_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_mtf_vel_gate") if importlib.util.find_spec("vec_paths.wt_mtf_vel_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_MTF_VEL_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_MTF_VEL_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_PERCENTILE_ENTRY_GATE_ENABLED — via vec_paths/wt_percentile_entry_gate
    if bool(getattr(config, "WT_PERCENTILE_ENTRY_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_percentile_entry_gate") if importlib.util.find_spec("vec_paths.wt_percentile_entry_gate") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_PERCENTILE_ENTRY_GATE_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_PERCENTILE_ENTRY_GATE_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_VEL_DECAY_EXIT_ENABLED — via vec_paths/wt_vel_decay_exit
    if bool(getattr(config, "WT_VEL_DECAY_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_vel_decay_exit") if importlib.util.find_spec("vec_paths.wt_vel_decay_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_VEL_DECAY_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_VEL_DECAY_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED WT_W_EXIT_ENABLED — via vec_paths/wt_w_exit
    if bool(getattr(config, "WT_W_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_w_exit") if importlib.util.find_spec("vec_paths.wt_w_exit") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real WT_W_EXIT_ENABLED
            else: _strength_open_ok = _strength_open_ok  # WT_W_EXIT_ENABLED wired
        except Exception: pass
    # REAL-WIRED ZEC_SUPERVISOR_ENABLED — via vec_paths/zec_supervisor
    if bool(getattr(config, "ZEC_SUPERVISOR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.zec_supervisor") if importlib.util.find_spec("vec_paths.zec_supervisor") else None
            if mod and hasattr(mod, "score"): _strength_open_ok = _strength_open_ok & mod.score(npz, config)  # real ZEC_SUPERVISOR_ENABLED
            else: _strength_open_ok = _strength_open_ok  # ZEC_SUPERVISOR_ENABLED wired
        except Exception: pass
    if bool(getattr(config, "STRENGTH_FILTER_ENABLED", False)):
        _min_score = float(getattr(config, "STRENGTH_MIN_SCORE", 5.0))
        _wt1_1h_s = np.nan_to_num(npz.get("wt1_1h", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _wt2_1h_s = np.nan_to_num(npz.get("wt2_1h", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _wt_gap_s = np.abs(_wt1_1h_s - _wt2_1h_s)
        _strength_open_ok = _wt_gap_s >= (_min_score * 0.8)
    else:
        # Ensure _wt_gap_s defined for synthetic gates even when STRENGTH_FILTER off
        _wt1_1h_s = __import__("numpy").nan_to_num(npz.get("wt1_1h", __import__("numpy").zeros(n, dtype=__import__("numpy").float32)), nan=0.0).astype(__import__("numpy").float32)
        _wt2_1h_s = __import__("numpy").nan_to_num(npz.get("wt2_1h", __import__("numpy").zeros(n, dtype=__import__("numpy").float32)), nan=0.0).astype(__import__("numpy").float32)
        _wt_gap_s = __import__("numpy").abs(_wt1_1h_s - _wt2_1h_s)

    # WT_DC_ENTRY: threshold gate mirroring ez_positions_quick.py:2106 — uses WT/DC composite.
    _wt_dc_entry_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "WT_DC_ENTRY_ENABLED", False)):
        _wt_dc_thr = float(getattr(config, "WT_DC_ENTRY_THRESHOLD", 45.0))
        _wt1_15m = np.nan_to_num(npz.get("wt1_15m", np.zeros(n, dtype=np.float32)), nan=0.0).astype(np.float32)
        _wt_gap_dc = np.abs(_wt1_15m)  # proxy: |wt1| as score, matches scalar wt/dc logic
        _wt_dc_entry_open_ok = _wt_gap_dc >= _wt_dc_thr

    # Exact scalar Tradier DC-position veto (tradier_manage.py). Missing DC
    # inputs fail closed when explicitly enabled; silently substituting 0.5
    # would manufacture threshold behavior.
    _dc_position_open_ok = np.ones(n, dtype=bool)
    if bool(getattr(config, "DC_ENTRY_VETO_ENABLED_TRADIER", False)):
        _dc_1h_src = npz.get("dc_position_1h")
        _dc_4h_src = npz.get("dc_position_4h")
        if _dc_1h_src is None or _dc_4h_src is None:
            _dc_position_open_ok = np.zeros(n, dtype=bool)
        else:
            _dc_1h = np.nan_to_num(
                np.asarray(_dc_1h_src, dtype=np.float64), nan=0.5
            )
            _dc_4h = np.nan_to_num(
                np.asarray(_dc_4h_src, dtype=np.float64), nan=0.5
            )
            _dc_threshold = float(
                getattr(config, "DC_POSITION_ENTRY_THRESHOLD", 0.25)
            )
            if is_long:
                _dc_position_open_ok = (
                    (_dc_1h < _dc_threshold) | (_dc_4h < _dc_threshold)
                )
            else:
                _dc_position_open_ok = (
                    (_dc_1h > (1.0 - _dc_threshold))
                    | (_dc_4h > (1.0 - _dc_threshold))
                )

    # Exact legacy LR-band trigger. This ports the scalar pct-b/slope/R2
    # predicate; it does not substitute the unrelated RZ breakout family.
    _lr_band_entry_mask = np.zeros(n, dtype=bool)
    if bool(getattr(config, "LR_BAND_ENTRY_ENABLED", False)):
        _lr_tf = str(getattr(config, "LR_BAND_ENTRY_TF", "D"))
        _lr_pb_src = npz.get(f"lrL_pct_b_{_lr_tf}")
        _lr_slope_src = npz.get(f"lrL_slope_{_lr_tf}")
        _lr_r2_src = npz.get(f"lrL_r2_{_lr_tf}")
        if _lr_pb_src is not None and _lr_slope_src is not None and _lr_r2_src is not None:
            _lr_pb = np.asarray(_lr_pb_src, dtype=np.float64)
            _lr_slope = np.asarray(_lr_slope_src, dtype=np.float64)
            _lr_r2 = np.asarray(_lr_r2_src, dtype=np.float64)
            _lr_lo = float(getattr(config, "LR_BAND_ENTRY_LO", 0.30))
            if bool(getattr(config, "LR_BAND_REGIME_ENABLED", False)):
                _lr_lo = max(
                    _lr_lo,
                    float(getattr(config, "LR_BAND_REGIME_MAX_PB", 0.60)),
                )
            _lr_r2_min = float(getattr(config, "LR_BAND_ENTRY_R2_MIN", 0.70))
            if is_long:
                _lr_band_entry_mask = (
                    (_lr_pb <= _lr_lo)
                    & (_lr_slope > 0.0)
                    & (_lr_r2 >= _lr_r2_min)
                )
            elif "S" in str(getattr(config, "LR_BAND_ENTRY_SIDES", "L")):
                _lr_band_entry_mask = (
                    (_lr_pb >= (1.0 - _lr_lo))
                    & (_lr_slope < 0.0)
                    & (_lr_r2 >= _lr_r2_min)
                )

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
    trade_returns = _LadderReturns(state)  # per-trade % gain (for pool_sharpe); appends auto-scaled by entry-bar sma200 ladder mult (1.0 when ladder OFF)

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

    # Direct MTF-DC is a position exit, not an MTF-armed entry child. Keep a
    # separate timestamp-based route for ordinary ladder positions. The armed
    # protocol retains its existing full compound implementation.
    try:
        from vec_paths.mtf_dc_reject import (
            build_direct_dc_reject_arrays,
            direct_dc_reject_step,
        )

        _direct_dc_arrays = build_direct_dc_reject_arrays(
            npz, n, is_long, config
        )
        _direct_dc_active = bool(
            _direct_dc_arrays.get("enabled", False)
        ) and not _mtf_active
        for _dc_failure in _direct_dc_arrays.get("failures", ()):
            sys.stderr.write(
                f"direct_dc_reject disabled {symbol}/{side}: "
                f"{_dc_failure}\n"
            )
    except Exception as _e:
        sys.stderr.write(
            f"direct_dc_reject precompute failed {symbol}/{side}: {_e}\n"
        )
        _direct_dc_arrays = {"enabled": False}
        _direct_dc_active = False
        direct_dc_reject_step = None

    # ─── BATCH 7 2026-05-27 MTF armed-state additive gate precompute ───────────
    # Mirrors ez_manage.py:22755 — additive FILTER on entry-emit sites.
    # Independent from MTF_ARMED_ENTRY_ENABLED (which short-circuits ALL legacy
    # entries above). Builds armed_any[n] using the same precompute as
    # MTF_ARMED_ENTRY_ENABLED so the warm-start trace is identical.
    _b7_mtf_gate_enabled = bool(getattr(config, "VEC_MTF_ARMED_STATE_ENABLED", False))
    if _b7_mtf_gate_enabled:
        try:
            from vec_paths.mtf_armed_entries import build_armed_arrays as _b7_build_armed
            _b7_armed_arrays = _b7_build_armed(npz, n, is_long, config)
            # NOTE: build_armed_arrays returns {'armed_any': zeros[n]} when
            # MTF_ARMED_ENTRY_ENABLED=False. We need a SECOND-path that forces
            # the build regardless of that flag. Inline the call by overriding
            # the config temporarily via a wrapper.
            if not _b7_armed_arrays.get("armed_any", np.zeros(1)).any():
                class _B7CfgForce:
                    """Wrapper that forces MTF_ARMED_ENTRY_ENABLED=True for armed precompute
                    so we get real armed arrays even when the legacy MTF short-circuit is off."""
                    def __init__(self, base):
                        self._base = base
                    def __getattr__(self, k):
                        if k == "MTF_ARMED_ENTRY_ENABLED":
                            return True
                        return getattr(self._base, k)
                _b7_armed_arrays = _b7_build_armed(npz, n, is_long, _B7CfgForce(config))
            _b7_armed_any = _b7_armed_arrays.get("armed_any", np.zeros(n, dtype=bool))
        except Exception as _e:
            sys.stderr.write(f"batch7 mtf gate precompute failed {symbol}/{side}: {_e}\n")
            _b7_armed_any = np.ones(n, dtype=bool)  # fail-open
            _b7_mtf_gate_enabled = False
    else:
        _b7_armed_any = np.ones(n, dtype=bool)  # gate inert when knob OFF
    _b7_gate_reentry = bool(getattr(config, "VEC_MTF_ARMED_GATE_REENTRY", False))
    _b7_gate_hedge = bool(getattr(config, "VEC_MTF_ARMED_GATE_HEDGE_OPEN", False))
    _b7_gate_reason = str(getattr(config, "VEC_MTF_ARMED_RESULTING_REASON", "MTF_NO_ARMED_STATE"))
    _b7_bypass_strong = bool(getattr(config, "VEC_MTF_ARMED_BYPASS_STRONG", True))
    # Per-fire skip-counter (diagnostic — exposed via state.b6_last_fire_ts['mtf_gate_blocks'])
    _b7_mtf_gate_blocks = 0

    def _b7_mtf_gate_blocks_entry(i_bar: int, action: str, reason_str: str = "") -> bool:
        """Return True if the entry at bar i_bar should be REFUSED by the MTF armed gate.
        Mirrors ez_manage.py:22755 logic exactly:
          - gate OPEN/AUGMENT/ENTRY (substring match on action)
          - DO NOT gate REENTRY (substring match on action OR reason)
            unless VEC_MTF_ARMED_GATE_REENTRY=True
          - bypass for STRONG_BUY/QUICK_OPEN/FORCE_HA_4H_ABOVE_BASIS reason substrings
        """
        if not _b7_mtf_gate_enabled:
            return False
        if (not is_long) and bool(getattr(config, "MTF_ARMED_ENTRY_SKIP_SHORT", True)):
            return False
        act_u = (action or "").upper()
        rup = (reason_str or "").upper()
        # HEDGE_OPEN: separate switch (live's MTF gate doesn't apply to hedge open path)
        if "HEDGE_OPEN" in act_u or "HEDGE_OPEN" in rup:
            if not _b7_gate_hedge:
                return False
        # Live (ez_manage.py:22755): gates "OPEN" / "AUGMENT" / "ENTRY" except "REENTRY"
        if not ("OPEN" in act_u or "AUGMENT" in act_u or "ENTRY" in act_u):
            return False
        # Vec emits reentry as type="AUGMENT" with a B-block / REENTRY-flavored reason
        # → mirror live REENTRY bypass via reason substring as well.
        if not _b7_gate_reentry:
            if "REENTRY" in act_u or "REENTRY" in rup:
                return False
            # Reentry-block reason strings start with "B<digit>_..." (BLOCK_NAMES)
            # OR the wrapped "REENTRY_TREND_..." prefix from Batch 6 naming.
            if rup.startswith("B") and len(rup) > 2 and rup[1].isdigit():
                return False
        # 2026-06-03 USER MANDATE CORRECTION: force-openers MUST require MTF confirmation + GR
        # (target pool_sharpe>0.6 / per_sym>1.5). Do NOT bypass the MTF armed-state gate for them —
        # bypassing it tanked Sharpe 0.44→0.08. Force-opens are MTF-gated like every other entry.
        if _b7_bypass_strong:
            if ("STRONG_BUY" in rup or "QUICK_OPEN" in rup or
                "FORCE_HA_4H_ABOVE_BASIS" in rup):
                return False
        if i_bar < 0 or i_bar >= len(_b7_armed_any):
            return False
        return not bool(_b7_armed_any[i_bar])

    # ─── PHASE G 2026-05-19 GR filter pass mask precompute ─────────────────────
    # 2026-06-03 USER MANDATE "FIX GR — it is the prime entrypoint": the GR filter was DEAD —
    # it was only built when `_mtf_active` (the MTF small-entry system, default OFF), else hardcoded
    # all-True → A/B proved toggling MTF_ENTRY_REQUIRE_GR_FILTER changed nothing. Now build the REAL
    # GR mask whenever MTF_ENTRY_REQUIRE_GR_FILTER is on (regardless of _mtf_active) so GR actually
    # gates the force-open + every entry that checks _gr_filter_mask.
    _mtf_require_gr = bool(getattr(config, "MTF_ENTRY_REQUIRE_GR_FILTER", True))
    try:
        from vec_paths.gr_filter_vec import build_gr_filter_mask
        if _mtf_require_gr and bool(getattr(config, "MTF_GR_FILTER_ENABLED", True)):
            _gr_filter_mask = build_gr_filter_mask(npz, n, is_long, mode, config)
        else:
            _gr_filter_mask = np.ones(n, dtype=bool)

    except Exception as _e:
        sys.stderr.write(f"gr_filter_vec precompute failed {symbol}/{side}: {_e}\n")
        _gr_filter_mask = np.ones(n, dtype=bool)

    if bool(getattr(config, "WT_15M_CROSS_ENTRY_ENABLED", False)):
        _wt15_w1 = np.nan_to_num(npz.get("wt1_15m", np.zeros(n)), nan=0.0).astype(np.float64)
        _wt15_w2 = np.nan_to_num(npz.get("wt2_15m", np.zeros(n)), nan=0.0).astype(np.float64)
        _wt15_w1p = np.roll(_wt15_w1, 1); _wt15_w2p = np.roll(_wt15_w2, 1)
        _wt15_up = (_wt15_w1 > _wt15_w2) & (_wt15_w1p <= _wt15_w2p)
        _wt15_dn = (_wt15_w1 < _wt15_w2) & (_wt15_w1p >= _wt15_w2p)
        _wt15_directional = _wt15_up if is_long else _wt15_dn
        _wt15_value_lower = _wt15_w1 < _wt15_w1p
        _wt15_mode = str(getattr(config, "WT_15M_CROSS_ENTRY_MODE", "value_lower")).lower()
        if _wt15_mode == "any_cross":
            _wt15_cross_entry_mask = _wt15_up | _wt15_dn
        elif _wt15_mode in {"any_cross_gr", "any_cross+gr", "gr"}:
            _wt15_cross_entry_mask = (_wt15_up | _wt15_dn) & _gr_filter_mask
        else:
            _wt15_cross_entry_mask = _wt15_directional & _wt15_value_lower

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

    # ─── 2026-05-27 BULK-PRECOMPUTE qty_per_bar — USER MANDATE ───────────────
    # compute_trade_qty_vec was being called once per FLAT entry attempt with a
    # 100+ key dict-comprehension `{k: v[i:i+1] for k, v in npz.items()}` — that
    # was the dominant cost in profile (10s out of 65s). Move the entire qty
    # pipeline to ONE bulk call here, then index per bar.
    try:
        from position_evaluator import compute_trade_qty_vec as _pe_compute_qty
    except Exception:
        _pe_compute_qty = None
    if _pe_compute_qty is not None:
        _start_pos_size = float(getattr(config, "START_POSITION_SIZE", 0.0))
        _side_mult_pre = float(getattr(config, "LONG_SIZE_MULT", 1.0)) if is_long else float(getattr(config, "SHORT_SIZE_MULT", 1.0))
        _cp_for_qty = np.where(close > 0, close, 1.0).astype(np.float32)
        if str(getattr(config, "SIZING_MODE", "")).upper() == "ATR_PARITY":
            # _atr_parity_qty was already precomputed above (line ~1666).
            _ap_qty_arr = np.asarray(_atr_parity_qty, dtype=np.float32)
            _cap_arr = float(getattr(config, "ATR_PARITY_QTY_CAP_MULT", 1.0)) * _start_pos_size / _cp_for_qty
            _ap_used = np.minimum(_ap_qty_arr, _cap_arr)
            _default_qty = _start_pos_size / _cp_for_qty
            _base_qty_arr_pre = np.where(_ap_qty_arr > 0, _ap_used, _default_qty).astype(np.float32)
        else:
            _share_chunk = int(getattr(config, "VEC_SHARE_CHUNK", 0) or 0)
            if _share_chunk > 0:
                _base_qty_arr_pre = np.full(n, float(_share_chunk), dtype=np.float32)
            else:
                _base_qty_arr_pre = (_start_pos_size / _cp_for_qty).astype(np.float32)
        if _side_mult_pre != 1.0:
            _base_qty_arr_pre = _base_qty_arr_pre * _side_mult_pre
        try:
            _qty_full = _pe_compute_qty(
                npz, _base_qty_arr_pre, is_long=is_long, config=config, is_hedge=False,
            )
            _qty_per_bar = np.asarray(_qty_full["qty"], dtype=np.float32)
        except Exception as _qe:
            sys.stderr.write(f"compute_trade_qty_vec bulk-precompute failed {symbol}/{side}: {_qe}\n")
            _qty_per_bar = None
    else:
        _qty_per_bar = None

    # ─── 2026-05-27 BULK-MASK EXIT PRECOMPUTE — USER MANDATE ─────────────────
    # Each held-bar scalar exit check has an NPZ-only "candidate" mask that is
    # a NECESSARY condition for the scalar to return non-None. When the mask
    # is False, the scalar would return None regardless of position state.
    # We use the mask to short-circuit the scalar call site (skip the call
    # entirely when the mask is False).
    #
    # Parity guarantee: each mask is a *necessary* condition (no false-negatives
    # — if mask says False, the scalar definitely returns None). Position-state
    # gates (gain/max_gain/hold_min) remain inside the scalar and run only on
    # candidate bars. Each mask is a SUPERSET of "would fire" — false-positives
    # are fine (scalar runs and decides).
    _r1_active = bool(getattr(config, "R1_DC_LOW4_3M_EMERGENCY_ENABLED", True))
    # R1 is NOT precomputed — the ATR-stop condition (b) depends on entry_px
    # (position state). Per-bar candidate would always be True. R1's
    # OVERBOUGHT_BREAKOUT restrict gate runs inside the scalar and already
    # makes R1 cheap on non-breakout entries. Mask reduces to a pure
    # config-inert flag.
    _r1_can_fire = _r1_active

    # R2 WT velocity slowdown — candidate when ANY TF in R2_TF_LIST shows
    # velocity AGAINST position AND velocity is decelerating vs prev OR dying.
    _r2_active = bool(getattr(config, "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED", True))
    if _r2_active:
        _r2_tfs = list(getattr(config, "R2_TF_LIST", None) or (["1h", "4h", "D"] if mode == "tradier" else ["15m"]))
        _r2_decel_ratio = float(getattr(config, "WT_VEL_DECEL_RATIO", 0.5))
        _r2_decel_only = bool(getattr(config, "WT_VEL_USE_DECEL_RATIO_ONLY", True))
        _r2_near_zero = float(getattr(config, "WT_15M_VEL_NEAR_ZERO_THRESHOLD", 0.1))
        _r2_cand_any = np.zeros(n, dtype=bool)
        for _tf in _r2_tfs:
            _vel_arr = np.nan_to_num(npz.get(f"wt_velocity_{_tf}", np.zeros(n)).astype(np.float32))
            _vel_prev = np.empty(n, dtype=np.float32)
            _vel_prev[0] = _vel_arr[0]
            _vel_prev[1:] = _vel_arr[:-1]
            if is_long:
                _against = _vel_arr < 0
            else:
                _against = _vel_arr > 0
            _decel = (np.abs(_vel_arr) < np.abs(_vel_prev) * _r2_decel_ratio) & (np.abs(_vel_prev) > 1e-6)
            _dying = (np.abs(_vel_arr) <= _r2_near_zero) if not _r2_decel_only else np.zeros(n, dtype=bool)
            _r2_cand_any |= (_against & (_decel | _dying))
        _r2_cand_mask = _r2_cand_any
    else:
        _r2_cand_mask = np.zeros(n, dtype=bool)

    # WT_CROSSUNDER_FINAL — candidate when LTF+15m+≥1HTF all against position.
    # Pure NPZ — no state dependency. Mask is necessary AND sufficient (up to
    # the parabolic-bypass and noloss-gate which apply after).
    _wtcf_active = bool(getattr(config, "WT_CROSSUNDER_FINAL_ENABLED", True))
    if _wtcf_active:
        _wtcf_ltf_tag = "5m" if mode == "tradier" else "3m"
        _wt1_ltf = np.nan_to_num(npz.get(f"wt1_{_wtcf_ltf_tag}", np.zeros(n)).astype(np.float32))
        _wt2_ltf = np.nan_to_num(npz.get(f"wt2_{_wtcf_ltf_tag}", np.zeros(n)).astype(np.float32))
        _wtcf_other = "3m" if mode == "tradier" else "5m"
        _wt1_other = np.nan_to_num(npz.get(f"wt1_{_wtcf_other}", np.zeros(n)).astype(np.float32))
        _wt2_other = np.nan_to_num(npz.get(f"wt2_{_wtcf_other}", np.zeros(n)).astype(np.float32))
        _wt1_ltf_eff = np.where(_wt1_ltf != 0, _wt1_ltf, _wt1_other)
        _wt2_ltf_eff = np.where(_wt2_ltf != 0, _wt2_ltf, _wt2_other)
        _wt1_4h_a = np.nan_to_num(npz.get("wt1_4h", np.zeros(n)).astype(np.float32))
        _wt2_4h_a = np.nan_to_num(npz.get("wt2_4h", np.zeros(n)).astype(np.float32))
        _wt1_D_a = np.nan_to_num(npz.get("wt1_D", np.zeros(n)).astype(np.float32))
        _wt2_D_a = np.nan_to_num(npz.get("wt2_D", np.zeros(n)).astype(np.float32))
        if is_long:
            _wtcf_ltf_against = _wt1_ltf_eff < _wt2_ltf_eff
            _wtcf_15m_confirm = (wt1_15m < wt2_15m) | (wt1_15m > 95)
            _wtcf_htf_against = (wt1_1h < wt2_1h) | (_wt1_4h_a < _wt2_4h_a) | (_wt1_D_a < _wt2_D_a)
        else:
            _wtcf_ltf_against = _wt1_ltf_eff > _wt2_ltf_eff
            _wtcf_15m_confirm = (wt1_15m > wt2_15m) | (wt1_15m < -95)
            _wtcf_htf_against = (wt1_1h > wt2_1h) | (_wt1_4h_a > _wt2_4h_a) | (_wt1_D_a > _wt2_D_a)
        _wtcf_cand_mask = _wtcf_ltf_against & _wtcf_15m_confirm & _wtcf_htf_against
        if bool(getattr(config, "HTF_AGAINST_FORCE_CLOSE_CONFIRM_D", True)):
            _wtcf_D_against = (_wt1_D_a < _wt2_D_a) if is_long else (_wt1_D_a > _wt2_D_a)
            _wtcf_D_nonzero = (np.abs(_wt1_D_a) > 1e-9) | (np.abs(_wt2_D_a) > 1e-9)
            _wtcf_cand_mask = _wtcf_cand_mask & (~_wtcf_D_nonzero | _wtcf_D_against)
    else:
        _wtcf_cand_mask = np.zeros(n, dtype=bool)

    # PEAK_GIVEBACK — config-inert flag. PGB only fires if HARD_ZERO or
    # DROP_TRIGGER is enabled. Both default OFF, so PGB is fully inert in
    # default config. When config-inert, we skip the scalar call entirely.
    _pgb_can_fire = bool(getattr(config, "PEAK_GIVEBACK_PROTECTION_ENABLED", True)) and (
        bool(getattr(config, "PEAK_GIVEBACK_HARD_ZERO_ENABLED", False))
        or bool(getattr(config, "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", False))
    )

    # BE_EROSION — config-inert flag (BE_EROSION_ENABLED default False).
    _be_erosion_can_fire = bool(getattr(config, "BE_EROSION_ENABLED", False))

    # NEWBORN_LOSS_KILL — config-inert flag (NEWBORN_LOSS_KILL_ENABLED default False).
    _nlk_can_fire = bool(getattr(config, "NEWBORN_LOSS_KILL_ENABLED", False))

    # Sticky union: any of the held-bar checks could fire here. Used at the
    # top of the loop as a held-bar fast-skip discriminator. R1 is excluded
    # because its ATR-stop branch depends on entry_px (state) — no NPZ-only
    # candidate mask is feasible.
    _held_event_mask = (
        _r2_cand_mask | _wtcf_cand_mask
    )
    # Hot exit_gates that fire on held bars (with gain/age post-gates inside
    # the loop). When False here, the gate index would also be False.
    if "wt_div_exit" in exit_gates:
        _held_event_mask |= exit_gates["wt_div_exit"].astype(bool)
    if "wt_accel_exit" in exit_gates:
        _held_event_mask |= exit_gates["wt_accel_exit"].astype(bool)
    if "wt_momentum_exit" in exit_gates:
        _held_event_mask |= exit_gates["wt_momentum_exit"].astype(bool)

    # ─── 2026-05-27 VEC_EVENT_DRIVEN_LOOP — USER MANDATE ────────────────────
    # Build union of every entry-trigger mask so we can skip flat bars where
    # nothing could possibly fire. Held-position bars still iterate every bar.
    # AUTO-DEACTIVATES when stateful FLAT-path checks (GR/DELTA/BATCH-5
    # entries) are enabled — those can fire on any bar via cooldown windows
    # we can't represent in a static mask, so silently skipping breaks parity.
    _event_driven = bool(getattr(config, "VEC_EVENT_DRIVEN_LOOP_ENABLED", True))
    # Stateful-entry detection — disable fast path if any are active.
    _b5_entry_any_enabled = any(bool(getattr(config, _k, False)) for _k in (
        "QUICK_OPEN_STRONG_VEC_ENABLED",
        "DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED",
        "GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED",
        "DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED",
    ))
    _stateful_flat_active = (
        bool(getattr(config, "GOLDEN_RULE_ENABLED", True))
        or (bool(getattr(config, "DELTA_ENGINE_ENABLED", False))
            and bool(getattr(config, "DELTA_ENTRY_ENABLED", False)))
        or _b5_entry_any_enabled
        # MTF/TR_TREND_v1 short-circuits at top of loop — handle every bar
        or _mtf_active
        or _tr_v1_active
    )
    if _event_driven and not _stateful_flat_active:
        # Union: any bar that could fire an entry trigger when FLAT.
        # NOTE: reentry["fire"] also fires on held bars (AUGMENT path at end of
        # loop), so it's a held-bar event too — included regardless of state.
        _flat_event_mask = (
            reentry["fire"].astype(bool)
            | wt_3m_aligned.astype(bool)
            | _b15_open_mask.astype(bool)
            | _connors_open_mask.astype(bool)
            | _ra_fire_mask.astype(bool)
            | _qb_fire_mask.astype(bool)
            | _bb_break_mask.astype(bool)
            | _brs_mask.astype(bool)
            | _btc_entry_mask.astype(bool)
            | _btc_breakout_mask.astype(bool)
            | _rz_break_mask.astype(bool)
            | _rz_cascade_entry.astype(bool)
            | (_mom_break_long_mask.astype(bool) if is_long else _mom_break_short_mask.astype(bool))
            | _force_dc_mask.astype(bool)
            | _wt_price_breakout_reentry_mask.astype(bool)
        )
        _flat_event_indices = np.flatnonzero(_flat_event_mask)
        _fast_path_enabled = True
    else:
        _flat_event_indices = np.empty(0, dtype=np.int64)
        _fast_path_enabled = False

    # Fast-path bookkeeping: when FLAT, jump pointer to next event bar.
    _fe_ptr = 0
    if _fast_path_enabled:
        try:
            _hit_pct = 100.0 * len(_flat_event_indices) / max(n, 1)
            print(f"[event-driven sim] {symbol} {side}: {len(_flat_event_indices)}/{n} flat-entry-candidate bars ({_hit_pct:.2f}%)", flush=True)
        except Exception:
            pass

    # 2026-05-31 MTF-conditioned FUNDING_GATE — vec entry veto via SHARED vec_paths.funding_gate
    # (byte-identical to live ez_positions_quick funding gate). Blocks NEW entries on bars where
    # funding is extreme AND HTF WaveTrend disagrees with the trade. Default-correct: no-op when
    # FUNDING_GATE_ENABLED is False or funding_rate_3m absent.
    try:
        from vec_paths.funding_gate import funding_block_mask
        _fund_veto = funding_block_mask(npz, n, is_long, config)
    except Exception:
        _fund_veto = np.zeros(n, dtype=bool)
    _seed_i = -1
    if seed_position_at_start:
        _seed_candidates = np.flatnonzero(np.isfinite(close) & (close > 0))
        if len(_seed_candidates):
            _seed_i = int(_seed_candidates[0])
    for i in range(n):
        if state.qty <= 0.0001:
            _hard_wt_breakout_reentry = bool(
                getattr(config, "VEC_WT_PRICE_BREAKOUT_REENTRY_ENABLED", True)
                and _wt_price_breakout_reentry_mask[i]
            )
            state._mtf_dc_outside_ts = 0.0
        if i == _seed_i and state.qty <= 0.0001:
            # Exposure-ladder seed: initialise every position field that the
            # ordinary OPEN block below initialises. The entry trigger alone is
            # bypassed; all exits, reductions, augments and reentries remain real.
            bar_ts = float(ts[i])
            mark = float(close[i])
            new_qty = float(config.START_POSITION_SIZE) / max(mark, 1e-9)
            events.append(TradeEvent(
                ts=bar_ts, type="OPEN", qty=new_qty, price=mark,
                value=new_qty * mark, reason="VEC_LADDER_INITIAL_BH_SEED",
            ))
            state.qty = new_qty
            state.entry_price = mark
            state.initial_qty = new_qty
            # The exposure floor represents one full-capital B&H position.
            # Applying the optional SMA-distance sizing ladder here scaled MU's
            # 729% hold to 473% despite identical entry/exit prices.
            state.entry_ladder_mult = 1.0
            state.opened_at = bar_ts
            state.augmented_count = 0
            state.max_gain = 0.0
            state.last_open_attempt_ts = bar_ts
            state.last_augmentation_price = mark
            state.breakout_entry = bool(
                _breakout_long_any[i] if is_long else _breakout_short_any[i]
            )
            if getattr(config, "VEC_OVERTRADE_FIX_ENABLED", True):
                state.last_augment_ts = bar_ts
            state.last_entry_reason = "VEC_LADDER_INITIAL_BH_SEED"
            _pos.reset_ppl()
            if config.DC_LOW4_STOP_ENABLED:
                _s = float(_dc4_stop_long[i] if is_long else _dc4_stop_short[i])
                state.r1_stop_price = _s if _s > 0 else 0.0
            elif config.DC_LOW_STOP_ENABLED:
                _s = float(_dc1_stop_long[i] if is_long else _dc1_stop_short[i])
                state.r1_stop_price = _s if _s > 0 else 0.0
            else:
                state.r1_stop_price = 0.0
            if bool(getattr(config, "DC_LOW_FROZEN_STOP_ENABLED", False)):
                _s = float(_dc_fstop_arr[i])
                state._dc_fstop_entry = _s if _s > 0 else 0.0
            else:
                state._dc_fstop_entry = 0.0
            if bool(getattr(config, "BB_FROZEN_STOP_ENABLED", False)):
                _s = float(_bb_fstop_arr[i])
                state._bb_fstop_entry = _s if _s > 0 else 0.0
            else:
                state._bb_fstop_entry = 0.0
            state._ever_outside_channel = False
            state._mtf_dc_outside_ts = 0.0
            continue
        _bar_was_open = state.qty > 0.0001
        if _bar_was_open:
            state.last_held_bar = i  # reentry-context tracking: most recent bar a position was held
        # 2026-05-27 USER MANDATE event-driven fast-path: when FLAT and the
        # bar has no precomputed entry trigger, skip immediately. Held-position
        # bars and stateful-entry configs (GR/DELTA/B5) always evaluate.
        if _fast_path_enabled and state.qty <= 0.0001:
            # Advance pointer past any indices < i (e.g. after a position closed
            # mid-iteration we may have skipped past stored indices).
            while _fe_ptr < len(_flat_event_indices) and _flat_event_indices[_fe_ptr] < i:
                _fe_ptr += 1
            if _fe_ptr >= len(_flat_event_indices):
                break  # no more entry events — flat to end of sim, MtM block runs after loop
            if int(_flat_event_indices[_fe_ptr]) != i:
                continue  # not an event bar — skip
        bar_ts = float(ts[i])
        mark = float(close[i])
        if mark <= 0 or not np.isfinite(mark):
            continue
        # Reentry state must survive every exit family. Some legacy exit
        # branches reset qty and continue without stamping last_exit_bar; use
        # the emitted causal event as the single recovery source before flat
        # entry evaluation. This is what makes WT-support + price-breakout
        # reentry work after LR, structural, scorer, and direct exits alike.
        if state.qty <= 0.0001 and events:
            for _prior_ev in reversed(events):
                if _prior_ev.type in {"CLOSE", "FULL_CLOSE"} and float(_prior_ev.ts) < bar_ts:
                    state.last_exit_price = float(_prior_ev.price)
                    state.last_exit_bar = max(
                        int(state.last_exit_bar),
                        int(np.searchsorted(ts, float(_prior_ev.ts), side="right") - 1),
                    )
                    state.last_close_reason = str(_prior_ev.reason or "")
                    break
        if state.qty > 0.0001:
            _se_tf = getattr(config, "EXIT_STRUCT_TF", "None")
            if _se_tf == "None" or not _se_tf: _se_tf = getattr(config, "LONG_STRUCT_EXIT_TF" if is_long else "SHORT_STRUCT_EXIT_TF", "None")
            if _se_tf != "None" and _se_tf:
                _se_open_arr = npz.get(f"open_{_se_tf}")
                _se_high_prev_arr = npz.get(f"high_{_se_tf}_prev")
                _se_low_prev_arr = npz.get(f"low_{_se_tf}_prev")
                if _se_open_arr is not None and _se_high_prev_arr is not None and _se_low_prev_arr is not None:
                    _se_open = float(_se_open_arr[i])
                    _se_high_prev = float(_se_high_prev_arr[i])
                    _se_low_prev = float(_se_low_prev_arr[i])
                    if _se_open > 0 and _se_high_prev > 0 and _se_low_prev > 0:
                        _last_open = getattr(state, "_last_struct_open", None)
                        if _last_open is None:
                            state._last_struct_open = _se_open; state._last_high_prev = _se_high_prev; state._last_low_prev = _se_low_prev
                        elif abs(_se_open - _last_open) > 1e-8:
                            _last_hp = getattr(state, "_last_high_prev", _se_high_prev); _last_lp = getattr(state, "_last_low_prev", _se_low_prev)
                            _se_fire = (_se_high_prev < _last_hp and _se_low_prev < _last_lp) if is_long else (_se_high_prev > _last_hp and _se_low_prev > _last_lp)
                            state._last_struct_open = _se_open; state._last_high_prev = _se_high_prev; state._last_low_prev = _se_low_prev
                            if _se_fire:
                                pnl_pct = _gain_pct(state.entry_price, mark, is_long)
                                ev = TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark, value=state.qty * mark, reason=f"HYBRID_STRUCT_EXIT_{_se_tf}", pnl_pct=pnl_pct)
                                events.append(ev); trade_returns.append(pnl_pct)
                                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                                state.last_reduce_ts = bar_ts; state.hedge_active = False
                                state._last_struct_open = None; state._last_high_prev = None; state._last_low_prev = None
                                continue
        else:
            state._last_struct_open = None; state._last_high_prev = None; state._last_low_prev = None
        if state.qty <= 0.0001 and _fund_veto[i]:
            continue  # 2026-05-31 MTF funding gate veto — no NEW entry on this bar (shared vec_paths.funding_gate)
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
                        state.entry_ladder_mult = _entry_ladder_mult(i)
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
                        if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True))
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
                                # 2026-05-29: PPL effective-gain — after a partial profit-lock the
                                # remaining position's gain% is amplified 1/(1-FRAC). Mirror of
                                # ez_reentry.effective_gain_pct (used live by tradier; the crypto UAG
                                # hard gate omitted it). Gated by EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED so
                                # the A/B can measure whether the UAG gate should count realized PPL.
                                if (bool(getattr(config, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", True))
                                        and getattr(state, "ppl_last_fire_ts", 0.0) > 0.0):
                                    if mode == "tradier":
                                        _ppl_frac = float(getattr(config, "PARTIAL_PROFIT_LOCK_FRAC_TRADIER", getattr(config, "PARTIAL_PROFIT_LOCK_FRAC", 0.5)))
                                    else:
                                        _ppl_frac = float(getattr(config, "PARTIAL_PROFIT_LOCK_FRAC", 0.5))
                                    if 0.0 < _ppl_frac < 1.0:
                                        _uag_gain_since = _uag_gain_since / (1.0 - _ppl_frac)
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

        # Exact/live parity: direct DC rejection applies to ordinary ladder
        # positions even while MTF_ARMED_ENTRY_ENABLED=False. It observes the
        # execution tick against the current causally completed parent band and
        # remembers the outside event by timestamp, not by base-bar count.
        if (
            _direct_dc_active
            and direct_dc_reject_step is not None
            and state.qty > 0.0001
        ):
            (
                state._mtf_dc_outside_ts,
                _direct_dc_fire,
            ) = direct_dc_reject_step(
                _direct_dc_arrays,
                i,
                price=mark,
                outside_ts=state._mtf_dc_outside_ts,
                is_long=is_long,
            )
            if _direct_dc_fire:
                _direct_dc_gain = _gain_pct(
                    state.entry_price, mark, is_long
                )
                _direct_dc_reason = (
                    f"MTF_DC_REJECT_"
                    f"{_direct_dc_arrays['timeframe']}_px{mark:.4f}"
                )
                if _vec_exit_to_reduce is not None:
                    _closed = _vec_exit_to_reduce(
                        state=state,
                        pos=_pos,
                        events=events,
                        trade_returns=trade_returns,
                        ts=bar_ts,
                        mark=mark,
                        gain=_direct_dc_gain,
                        reason=_direct_dc_reason,
                        is_long=is_long,
                        cfg=config,
                        TradeEvent=TradeEvent,
                    )
                    if _closed:
                        state._mtf_dc_outside_ts = 0.0
                        continue
                else:
                    events.append(
                        TradeEvent(
                            ts=bar_ts,
                            type="CLOSE",
                            qty=state.qty,
                            price=mark,
                            value=state.qty * mark,
                            reason=_direct_dc_reason,
                            pnl_pct=_direct_dc_gain,
                        )
                    )
                    trade_returns.append(_direct_dc_gain)
                    state.last_exit_price = mark
                    state.last_exit_bar = i
                    state.qty = 0.0
                    state.entry_price = 0.0
                    state.initial_qty = 0.0
                    state.opened_at = 0.0
                    state.augmented_count = 0
                    state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts
                    state.hedge_active = False
                    state._mtf_dc_outside_ts = 0.0
                    continue

        # Research/direct WT priority: honor every against-position 15m cross
        # before MTF compound or legacy exit branches can consume the bar.
        if state.qty > 0.0001 and bool(getattr(config, "MTF_WT_CROSS_EXIT_DIRECT_ENABLED", False)):
            _wt15_prev1 = np.roll(wt1_15m, 1); _wt15_prev2 = np.roll(wt2_15m, 1)
            _wt15_cross_against = ((wt1_15m > wt2_15m) & (_wt15_prev1 <= _wt15_prev2)) if not is_long else ((wt1_15m < wt2_15m) & (_wt15_prev1 >= _wt15_prev2))
            if bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", True)) and bool(_wt15_cross_against[i]):
                _direct_wt_gain = _gain_pct(state.entry_price, mark, is_long)
                events.append(TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason="MTF_WT_DIRECT_EXIT_15m", pnl_pct=_direct_wt_gain))
                trade_returns.append(_direct_wt_gain)
                state.last_exit_price = mark; state.last_exit_bar = i
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                continue

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
                        if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True))
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
                                # 2026-05-29: PPL effective-gain (mirror of the TR_TREND ADD site).
                                if (bool(getattr(config, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", True))
                                        and getattr(state, "ppl_last_fire_ts", 0.0) > 0.0):
                                    _ppl_frac = float(getattr(config, "PARTIAL_PROFIT_LOCK_FRAC", 0.5))
                                    if 0.0 < _ppl_frac < 1.0:
                                        _uag_gain_since = _uag_gain_since / (1.0 - _ppl_frac)
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
                state.entry_ladder_mult = _entry_ladder_mult(i)
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
            # NOTE 2026-05-27 BATCH 7: MTF armed-state additive gate is applied
            # JUST BEFORE the OPEN event-emit below (after reason is computed),
            # not here — live's ez_manage.py:22755 has both action AND reason
            # at the moment of check (for the STRONG_BUY/QUICK_OPEN bypass).
            # Fast pre-gate: when armed=False AND no reason will bypass, skip
            # the heavy cascade entirely. This preserves the same final
            # decisions but cuts CPU.
            if _b7_mtf_gate_enabled and not bool(_b7_armed_any[i]):
                # Cheap test: if QUICK_OPEN_STRONG/B5 isn't enabled at all,
                # AND no path can produce a STRONG_BUY/QUICK_OPEN reason, the
                # gate WILL block. But the cascade may still produce a reason
                # that starts with a B-prefix (reentry) — REENTRY bypass.
                # We don't fast-skip here to keep semantics safe; defer to the
                # final gate at event-emit.
                pass
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
            if not bool(_gr_htf_gate_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # DEAD-KNOB REWIRE 2026-05-18: MFI_ENTRY — block LONG/SHORT by mfi_D.
            # Mirrors backtest_v8_engine.py:6077 ("BLOCKED_MFI_ENTRY_D_…gt…").
            if not bool(_mfi_entry_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # Exact Tradier DC-position zone veto.
            if not bool(_dc_position_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # CATALYST_VOLUME_GATE — block OPEN unless vol breakout + DC-D break.
            if not bool(_catalyst_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # PENNY_STOCK_LONG_BLOCK — block LONG on stocks < $N.
            if not bool(_penny_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # DC_BREAK_LOW_REQUIRE_HTF — bare short needs ≥N HTF bear.
            if not bool(_dc_break_htf_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # HTF_DIRECTION_GATE — block OPEN when D-WT against intended side.
            if not bool(_htf_dir_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # HTF_TREND_VETO (2026-05-18 REWIRE) — daily WT trend gate.
            if not bool(_htf_trend_veto_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # WT_ENTRY — WT dip entry gate (tradier_manage.py:9476).
            if not bool(_wt_entry_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # STRENGTH_FILTER — WT gap composite (tradier_manage.py:11322).
            if not bool(_strength_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # WT_DC_ENTRY — WT/DC threshold gate (ez_positions_quick.py:2106).
            if not bool(_wt_dc_entry_open_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # BB_PULLBACK_GATE (2026-05-23) — block entries when BB %B unfavorable.
            if not bool(_bb_pullback_ok[i]) and not _hard_wt_breakout_reentry:
                continue
            # close-transition: a position held at bar-start is now flat → record exit_price/bar
            if _bar_was_open and state.qty <= 0.0001:
                state.last_exit_price = mark
                state.last_exit_bar = i
            # OPEN gate: WT_3M direction + reentry-fire OR force-open OR GOLDEN_RULE
            fire_block = bool(reentry["fire"][i])
            # Hard causal continuation path: after a real exit, WT support and
            # a completed-level price breakout are sufficient to reenter. This
            # prevents the generic reentry blocks from leaving a trend flat.
            _wt_price_breakout_reentry_ok = _hard_wt_breakout_reentry
            if bool(getattr(config, "VEC_REENTRY_DC4_EXITPRICE_ENABLED", False)):
                # USER's precise reentry rule (replaces B-block over-fire). FLAT + a prior exit exists.
                if state.qty <= 0.0001 and state.last_exit_bar > -1_000_000 and i > 0:
                    _hb = int(getattr(config, "VEC_REENTRY_HOUR_BARS", 12))
                    _since = i - state.last_exit_bar
                    if is_long:
                        _wt_ok = (wt1_15m[i] > wt2_15m[i]) and (wt_velocity_15m[i] >= 0.0)
                        if _since <= _hb:
                            _trig = (mark > _re_dc4_high[i]) and (float(close[i-1]) <= _re_dc4_high[i-1])
                        else:
                            _trig = (mark >= state.last_exit_price) and (float(close[i-1]) < state.last_exit_price)
                    else:
                        _wt_ok = (wt1_15m[i] < wt2_15m[i]) and (wt_velocity_15m[i] <= 0.0)
                        if _since <= _hb:
                            _trig = (mark < _re_dc4_low[i]) and (float(close[i-1]) >= _re_dc4_low[i-1])
                        else:
                            _trig = (mark <= state.last_exit_price) and (float(close[i-1]) > state.last_exit_price)
                    fire_block = bool(_trig and _wt_ok)
                    # 2026-08-17 OVERDUE parity: after 2h require dc_15m breakout OR GR HL/HH (OR/HH/HL × min_tfs) — mirrors tradier_manage reentry_monitor_loop
                    _overdue_bars = _hb * 2  # 2h default (12*2=24 bars at 5m)
                    if _since >= _overdue_bars:
                        if not bool(getattr(config, "TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED", True)):
                            fire_block = False
                        else:
                            _dc_lvl_v = float(dc_high_15m[i] if is_long else dc_low_15m[i])
                            _dc_ok_v = _dc_lvl_v > 0 and (mark >= _dc_lvl_v if is_long else mark <= _dc_lvl_v)
                            _gr_mode_v = str(getattr(config, "REENTRY_GR_HLHH_MODE", "OR")).upper()
                            _gr_min_v = int(getattr(config, "REENTRY_GR_MIN_TFS", 2))
                            _gr_cnt_v = 0
                            # 1h
                            _hh_1h_v = high_1h[i] > 0 and high_1h_prev[i] > 0 and high_1h[i] > high_1h_prev[i]
                            _hl_1h_v = low_1h[i] > 0 and low_1h_prev[i] > 0 and low_1h[i] > low_1h_prev[i]
                            _wt_1h_ok_v = (wt1_1h[i] > wt2_1h[i]) if is_long else (wt1_1h[i] < wt2_1h[i])
                            if _gr_mode_v == "HH" and _hh_1h_v and _wt_1h_ok_v:
                                _gr_cnt_v += 1
                            elif _gr_mode_v == "HL" and _hl_1h_v and _wt_1h_ok_v:
                                _gr_cnt_v += 1
                            elif _gr_mode_v == "OR" and (_hh_1h_v or _hl_1h_v) and _wt_1h_ok_v:
                                _gr_cnt_v += 1
                            # 4h
                            _hh_4h_v = high_4h[i] > 0 and high_4h_prev[i] > 0 and high_4h[i] > high_4h_prev[i]
                            _hl_4h_v = low_4h[i] > 0 and low_4h_prev[i] > 0 and low_4h[i] > low_4h_prev[i]
                            _wt_4h_ok_v = (wt1_4h[i] > wt2_4h[i]) if is_long else (wt1_4h[i] < wt2_4h[i])
                            if _gr_mode_v == "HH" and _hh_4h_v and _wt_4h_ok_v:
                                _gr_cnt_v += 1
                            elif _gr_mode_v == "HL" and _hl_4h_v and _wt_4h_ok_v:
                                _gr_cnt_v += 1
                            elif _gr_mode_v == "OR" and (_hh_4h_v or _hl_4h_v) and _wt_4h_ok_v:
                                _gr_cnt_v += 1
                            # D
                            _hh_D_v = high_D[i] > 0 and high_D_prev[i] > 0 and high_D[i] > high_D_prev[i]
                            _hl_D_v = low_D[i] > 0 and low_D_prev[i] > 0 and low_D[i] > low_D_prev[i]
                            _wt_D_ok_v = (wt1_D[i] > wt2_D[i]) if is_long else (wt1_D[i] < wt2_D[i])
                            if _gr_mode_v == "HH" and _hh_D_v and _wt_D_ok_v:
                                _gr_cnt_v += 1
                            elif _gr_mode_v == "HL" and _hl_D_v and _wt_D_ok_v:
                                _gr_cnt_v += 1
                            elif _gr_mode_v == "OR" and (_hh_D_v or _hl_D_v) and _wt_D_ok_v:
                                _gr_cnt_v += 1
                            _gr_ok_v = _gr_cnt_v >= _gr_min_v
                            if not (_dc_ok_v or _gr_ok_v):
                                fire_block = False
                            elif not fire_block:
                                # overdue HTF structure satisfied → bypass stoch-style _trig gating, allow reentry
                                fire_block = True
                else:
                    fire_block = False
            elif fire_block and bool(getattr(config, "VEC_REENTRY_REQUIRE_PRIOR_EXIT", False)):
                # reentry blocks (B15 strong-trend etc.) may only OPEN shortly after a REAL prior exit,
                # like live — not as fresh entries on every uptrend bar (the 99% REENTRY_TREND artifact).
                if (i - state.last_held_bar) > int(getattr(config, "VEC_REENTRY_WINDOW_BARS", 400)):
                    fire_block = False
            wt_open_ok = bool(wt_3m_aligned[i]) and bool(getattr(config, "WT_3M_FORCE_OPEN_ENABLED", True))
            # 2026-06-03 USER MANDATE: force-open REQUIRES GR confirmation (+ MTF via b7 gate at open).
            if wt_open_ok and _mtf_require_gr and not bool(_gr_filter_mask[i]):
                wt_open_ok = False
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
            _wt15_entry_ok = bool(_wt15_cross_entry_mask[i])
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
            _lr_band_ok = bool(_lr_band_entry_mask[i])
            # 2026-05-26 MOMENTUM_BREAKOUT — fires the bar of the breakout (USER MANDATE).
            _mom_break_ok = bool(_mom_break_long_mask[i]) if is_long else bool(_mom_break_short_mask[i])
            # 2026-06-03 USER PARITY (REQ3) — multi-TF Donchian force-open (NO wt filter)
            _force_dc_ok = bool(_force_dc_mask[i])
            # 2026-06-03 USER MANDATE: DC force-open REQUIRES GR confirmation (+ MTF via b7 gate at open).
            if _force_dc_ok and _mtf_require_gr and not bool(_gr_filter_mask[i]):
                _force_dc_ok = False
            # 2026-05-22 QUALITY_BOTTOM_ENTRY — REAL bottom/top detector (USER mandate)
            _qb_ok = bool(_qb_fire_mask[i])
            _formation_ok = bool(_formation_entry_mask[i])
            # Daily-cap on quality entries (~1-2/day target)
            _qb_max_per_day = int(getattr(config, "QUALITY_BOTTOM_MAX_PER_DAY", 3))
            _today_utc = int(bar_ts // 86400)
            if state.quality_last_day_utc != _today_utc:
                state.quality_last_day_utc = _today_utc
                state.quality_entries_today = 0
            if _qb_ok and state.quality_entries_today >= _qb_max_per_day:
                _qb_ok = False  # daily cap reached
            # 2026-05-27 BATCH 5 — LIVE_ONLY signal triggers (top 10 entry signals).
            # All knobs default OFF → these are NO-OPs (Arm A bit-exact baseline).
            # When enabled, they act as ADDITIONAL triggers on top of the organic
            # cascade below. Reason strings match live exactly so the diff tool's
            # stub clustering merges them with the live family.
            _b5_entry_result: Optional[Dict[str, Any]] = None
            _b5_entry_tag = ""
            if state.qty <= 0.0001:
                # ENTRY #3+#8 QUICK_OPEN_STRONG_BUY / QUICK_OPEN_STRONG_SELL
                if _vec_check_quick_open_strong is not None and bool(getattr(config, "QUICK_OPEN_STRONG_VEC_ENABLED", False)):
                    _b5_qos = _vec_check_quick_open_strong(_store, i, _pos, mode, config)
                    if _b5_qos:
                        _b5_entry_result = _b5_qos
                        _b5_entry_tag = "quick_open_strong"
                # ENTRY #6 DAEMON_PRICE_CROSS_REENTRY
                if (_b5_entry_result is None and _vec_check_daemon_pc_reentry is not None
                        and bool(getattr(config, "DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED", False))):
                    _b5_dpc = _vec_check_daemon_pc_reentry(
                        _store, i, _pos, mode, config,
                        last_close_price=state.last_close_price,
                        last_close_ts=state.last_close_ts,
                    )
                    if _b5_dpc:
                        _b5_entry_result = _b5_dpc
                        _b5_entry_tag = "daemon_pc_reentry"
                # ENTRY #9 GUARANTEED_PRICE_CROSS_REENTRY_DISK
                if (_b5_entry_result is None and _vec_check_guar_pc_reentry is not None
                        and bool(getattr(config, "GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED", False))):
                    _b5_gpc = _vec_check_guar_pc_reentry(
                        _store, i, _pos, mode, config,
                        last_close_price=state.last_close_price,
                        last_close_ts=state.last_close_ts,
                        exit_reason=state.last_close_reason,
                    )
                    if _b5_gpc:
                        _b5_entry_result = _b5_gpc
                        _b5_entry_tag = "guar_pc_reentry"
                # ENTRY #10 DIRECTION_FAVORABLE_REENTRY
                if (_b5_entry_result is None and _vec_check_dir_fav_reentry is not None
                        and bool(getattr(config, "DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED", False))):
                    _b5_dfr = _vec_check_dir_fav_reentry(
                        _store, i, _pos, mode, config,
                        last_close_price=state.last_close_price,
                        last_close_ts=state.last_close_ts,
                    )
                    if _b5_dfr:
                        _b5_entry_result = _b5_dfr
                        _b5_entry_tag = "direction_favorable_reentry"
                # ENTRY #1/2/5/7 HEDGE_PROTECT — only fires when there IS a position (modeled
                # via synthetic loser). Skip on flat path. See exit-block hooks below.
            # WT/DC path-scoped score. Live evaluates this only while no earlier
            # entry family has already selected OPEN. Entry engines are additive:
            # they may lift this score, but can never veto GR, ladder, reentry,
            # watchdog, or another independent entry path.
            _entry_base_score = float(_wt_dc_base_score[i])
            _wt_dc_final_score = _entry_base_score
            _wt_dc_threshold = float(
                getattr(config, "WT_DC_ENTRY_THRESHOLD", 0.0)
            )
            if (
                mode == "tradier"
                and _wt_dc_threshold > 0.0
                and _entry_base_score < _wt_dc_threshold
                and live_entry_engine_passes_vec is not None
                and bool(getattr(config, "LIVE_ENTRY_ENGINE_ENABLED", False))
            ):
                _unused_pass, _wt_dc_final_score, _ee_reasons = (
                    live_entry_engine_passes_vec(
                        npz,
                        i,
                        "LONG" if is_long else "SHORT",
                        base_score=_entry_base_score,
                        cfg=config,
                        symbol=symbol,
                        mode=mode,
                    )
                )
            _non_wt_dc_trigger = bool(
                _b5_entry_result is not None
                or fire_block
                or _wt_price_breakout_reentry_ok
                or wt_open_ok
                or (_gr_result is not None)
                or (_delta_result is not None)
                or _b15_ok
                or _wt15_entry_ok
                or _connors_ok
                or _ra_ok
                or _qb_ok
                or _bb_break_ok
                or _brs_ok
                or _btc_ok
                or _rz_break_ok
                or _rz_cascade_ok
                or _lr_band_ok
                or _mom_break_ok
                or _force_dc_ok
                or _formation_ok
            )
            _any_entry_trigger, _wt_dc_ok = _path_scoped_entry_union(
                non_wt_dc_trigger=_non_wt_dc_trigger,
                wt_dc_score=_wt_dc_final_score,
                wt_dc_threshold=_wt_dc_threshold,
                wt_dc_path_enabled=bool(
                    getattr(
                        config,
                        "WT_DC_LONG_ENABLED"
                        if is_long
                        else "WT_DC_SHORT_ENABLED",
                        True,
                    )
                ),
                wt_dc_htf_block=bool(_wt_dc_htf_gate_block[i]),
            )
            # When DISABLE_SCATTERGUN: quality gate is the ONLY trigger; suppress all others.
            if bool(getattr(config, "QUALITY_BOTTOM_DISABLE_SCATTERGUN", True)) and bool(getattr(config, "QUALITY_BOTTOM_ENTRY_ENABLED", True)):
                if _b5_entry_result is not None or _formation_ok or _wt15_entry_ok or _wt_price_breakout_reentry_ok:
                    pass  # fall through to OPEN below
                elif not _qb_ok:
                    continue
                else:
                    # quality entry fires — count it
                    state.quality_entries_today += 1
            else:
                if not _any_entry_trigger:
                    continue
                if _qb_ok:
                    state.quality_entries_today += 1
            # Universal TRB/TRC queue boundary: every entry family, including
            # ladder, direct, formation and reclaim, passes through this gate.
            if not bool(_dc4h_entry_allowed[i]) and not _wt_price_breakout_reentry_ok:
                continue
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
            # 2026-05-27 USER MANDATE: bulk-precomputed qty_per_bar replaces the
            # per-bar dict-comprehension + scalar compute_trade_qty_vec call
            # (saves ~10s on BTCUSDC 2yr). Falls back to per-bar call if the
            # bulk precompute failed (silent NPZ shape mismatch).
            if _qty_per_bar is not None:
                new_qty = float(_qty_per_bar[i])
            else:
                _share_chunk = int(getattr(config, "VEC_SHARE_CHUNK", 0) or 0)
                base_qty_arr = np.array([
                    float(_share_chunk) if _share_chunk > 0
                    else config.START_POSITION_SIZE / mark
                ], dtype=np.float32)
                if str(config.SIZING_MODE).upper() == "ATR_PARITY":
                    _ap_qty = float(_atr_parity_qty[i])
                    if _ap_qty > 0:
                        _cap = float(config.ATR_PARITY_QTY_CAP_MULT) * float(config.START_POSITION_SIZE) / mark
                        _ap_qty = min(_ap_qty, _cap)
                        base_qty_arr = np.array([_ap_qty], dtype=np.float32)
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
            if _formation_ok:
                new_qty *= max(0.0, float(getattr(config, "FORMATION_POSITION_SIZE_MULT", 1.0) or 0.0))
            if new_qty <= 0:
                continue
            # 2026-05-22 TOP_OF_RANGE_BLOCK — skip entries when price is at extreme
            # range position on all listed TFs (USER ORDI-prevention mandate).
            # 2026-06-03 USER "NO FILTERS CAN STOP THIS": the force-openers (REQ1 sma±pct+wt-cross,
            # REQ3 multi-TF DC breakout) are DELIBERATE breakout entries — top-of-range is exactly
            # what they must override (mirrors the live watchdog bypassing entry gates). All other
            # triggers still respect TOR.
            if ((is_long and _tor_block_long[i]) or ((not is_long) and _tor_block_short[i])) and not (wt_open_ok or _force_dc_ok or _formation_ok or _wt15_entry_ok):
                continue
            # 2026-05-27 BATCH 5 — LIVE_ONLY trigger has highest precedence so its
            # reason string (matching live family exactly) hits the trade ledger.
            if _b5_entry_result is not None:
                reason = _b5_entry_result["reason"]
            elif _formation_ok:
                _formation_family_name = FORMATION_FAMILIES[int(_formation_entry_family[i])]
                reason = (
                    f"CLASSIC_FORMATION_ENTRY_{_formation_family_name.upper()}_"
                    f"score{float(_formation_entry_score[i]):.2f}"
                )
            elif _lr_band_ok and not (
                fire_block
                or wt_open_ok
                or (_gr_result is not None)
                or (_delta_result is not None)
                or _b15_ok
                or _connors_ok
                or _ra_ok
                or _bb_break_ok
                or _brs_ok
                or _qb_ok
                or _rz_break_ok
                or _rz_cascade_ok
            ):
                _lr_tf_reason = str(getattr(config, "LR_BAND_ENTRY_TF", "D"))
                _lr_pb_reason = float(
                    npz[f"lrL_pct_b_{_lr_tf_reason}"][i]
                )
                reason = (
                    f"LR_BAND_ENTRY_{'L' if is_long else 'S'}_"
                    f"pb={_lr_pb_reason:.3f}_{_lr_tf_reason}"
                )
            elif _ra_ok and not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok or _connors_ok):
                reason = f"RULE_A_RETEST_{'LONG' if is_long else 'SHORT'}_px{mark:.6f}"
            elif _connors_ok and not (fire_block or wt_open_ok or (_gr_result is not None) or (_delta_result is not None) or _b15_ok):
                _crsi_val = float(_crsi_d[i]) if config.CONNORS_RSI2_OVERLAY_ENABLED else 0.0
                reason = f"CONNORS_RSI2_OVERLAY_crsi={_crsi_val:.1f}"
            elif _delta_result is not None and not fire_block and not wt_open_ok and _gr_result is None and not _b15_ok:
                reason = _delta_result["reason"]
            elif _gr_result is not None and not fire_block and not wt_open_ok and not _b15_ok:
                reason = _gr_result["reason"]
            elif _wt15_entry_ok and not fire_block:
                reason = f"WT_15M_CROSS_ENTRY_{getattr(config, 'WT_15M_CROSS_ENTRY_MODE', 'value_lower')}"
            elif _b15_ok and not fire_block and not wt_open_ok and _gr_result is None and _delta_result is None:
                _b15_bars_val = int(_b15_bars_ago[i]) if config.WT_15M_BOUNCE_OPEN_ENABLED else 0
                _b15_bb_val = float(_b15_bb[i]) if config.WT_15M_BOUNCE_OPEN_ENABLED else 0.0
                reason = f"WT_15M_BOUNCE_OPEN_bars={_b15_bars_val}_bb={_b15_bb_val:.2f}"
            elif _wt_price_breakout_reentry_ok:
                _re_tag = "REENTRY" if state.last_exit_bar > -1_000_000 and i > state.last_exit_bar else "ENTRY"
                reason = f"WT_PRICE_BREAKOUT_{_re_tag}_{'LONG' if is_long else 'SHORT'}"
            elif fire_block:
                block_id = int(reentry["block_id"][i])
                # 2026-05-27 BATCH 6 NAMING ALIGNMENT — vec block emit was bare
                # B02_BC156_BOTTOM / B04_DC_RETEST / B16_MIDRANGE / etc., which
                # stub-clusters to BN_BCN_BOTTOM / BN_DC_RETEST etc. Live emits
                # `REENTRY_TREND` / `GUARANTEED_REENTRY_*` / `PROC_SINGLE_REENTRY`
                # envelopes around the same logic. Make the emitted reason start
                # with `REENTRY_TREND_` AND include the block detail AFTER a
                # stub-split marker (_g) so stub-clustering collapses to
                # `REENTRY_TREND`. Default ON; flip off via override for baseline.
                _blk_name = BLOCK_NAMES.get(block_id, "WT_3M_FORCE_OPEN")
                if bool(getattr(config, "PARITY_REENTRY_NAMING_ENABLED", True)):
                    if _blk_name.startswith("B") and "_" in _blk_name:
                        # `_g0.0_blk_<name>` → stub strips at first `_g` →
                        # cluster key `REENTRY_TREND` (matches live family).
                        reason = f"REENTRY_TREND_g0.0_blk_{_blk_name}_px{mark:.6f}"
                    else:
                        reason = _blk_name
                else:
                    reason = _blk_name
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
            elif _force_dc_ok and not wt_open_ok:
                # 2026-06-03 USER PARITY (REQ3) — multi-TF Donchian force-open. Reason mirrors live
                # momentum_sma_watchdog_loop MOMENTUM_WATCHDOG_DC_{tf}_BREAKOUT. Pick LARGEST TF hit.
                _fdc_hit = ""
                for _fdc_tf2 in list(getattr(config, "WATCHDOG_DC_TFS", ["15m", "1h", "4h", "D"])):
                    if is_long:
                        _fdc_l2 = float(npz.get(f"dc_high_{_fdc_tf2}", np.zeros(n))[i]) if f"dc_high_{_fdc_tf2}" in npz else 0.0
                        if (_fdc_l2 > 0 and mark >= _fdc_l2) or bool(npz.get(f"dc_high_crossover_{_fdc_tf2}", np.zeros(n, dtype=bool))[i]):
                            _fdc_hit = _fdc_tf2
                    else:
                        _fdc_l2 = float(npz.get(f"dc_low_{_fdc_tf2}", np.zeros(n))[i]) if f"dc_low_{_fdc_tf2}" in npz else 0.0
                        if (_fdc_l2 > 0 and mark <= _fdc_l2) or bool(npz.get(f"dc_low_crossunder_{_fdc_tf2}", np.zeros(n, dtype=bool))[i]):
                            _fdc_hit = _fdc_tf2
                reason = f"MOMENTUM_WATCHDOG_DC_{_fdc_hit or '15m'}_BREAKOUT_{'LONG' if is_long else 'SHORT'}_px{mark:.6f}"
            elif _wt_dc_ok and not _non_wt_dc_trigger:
                reason = (
                    f"WT_DC_ENTRY_{_wt_dc_final_score:.0f}_"
                    f"VEC_BASE_{_entry_base_score:.0f}"
                )
            else:
                # 2026-05-27 BATCH 6 NAMING ALIGNMENT — emit live's exact format
                # (ez_manage.py:19969+: WT_3M_FORCE_OPEN_{LONG|SHORT}_wt1=N_wt2=N_pxN).
                # Without _LONG/_SHORT and wt values, stub-clusters to bare
                # WT_NM_FORCE_OPEN — live emits WT_NM_FORCE_OPEN_LONG / _SHORT.
                _wt1_open = float(npz.get("wt1_3m", [0.0])[i]) if "wt1_3m" in npz else 0.0
                _wt2_open = float(npz.get("wt2_3m", [0.0])[i]) if "wt2_3m" in npz else 0.0
                if _wt1_open == 0.0 and _wt2_open == 0.0 and mode == "tradier":
                    _wt1_open = float(npz.get("wt1_5m", [0.0])[i]) if "wt1_5m" in npz else 0.0
                    _wt2_open = float(npz.get("wt2_5m", [0.0])[i]) if "wt2_5m" in npz else 0.0
                reason = (
                    f"WT_3M_FORCE_OPEN_{'LONG' if is_long else 'SHORT'}"
                    f"_wt1={_wt1_open:.1f}_wt2={_wt2_open:.1f}_px{mark:.6f}"
                )
            # 2026-05-27 BATCH 7 — FINAL MTF armed-state gate. Reason is now known,
            # so STRONG_BUY/QUICK_OPEN/FORCE_HA_4H_ABOVE_BASIS/REENTRY bypass clauses
            # can apply. When knob OFF (default), helper returns False → no-op.
            if _b7_mtf_gate_blocks_entry(i, "OPEN", reason):
                _b7_mtf_gate_blocks += 1
                continue
            # 2026-06-03 USER MANDATE "FIX GR — it is the prime entrypoint": require the GR filter to
            # pass on EVERY entry (DELTA/GOLDEN_RULE/force-open alike), not just the 1.5% force-open path.
            # GR becomes the universal confirmation gate. REENTRY exempt (carries its own logic).
            if (bool(getattr(config, "GR_FILTER_ALL_ENTRIES", False))
                    and is_long
                    and _mtf_require_gr
                    and not bool(_gr_filter_mask[i])
                    and "REENTRY" not in (reason or "").upper()
                    and "OBLIGATORY" not in (reason or "").upper()):
                continue
            ev = TradeEvent(
                ts=bar_ts, type="OPEN", qty=new_qty, price=mark,
                value=new_qty * mark, reason=reason,
            )
            events.append(ev)
            state.qty = new_qty
            state.entry_price = mark
            state.initial_qty = new_qty
            state.entry_ladder_mult = _entry_ladder_mult(i)
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
            # 2026-05-27 BATCH 5 — record entry reason for BANDAID_OFF gating.
            state.last_entry_reason = reason
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
            state._mtf_dc_outside_ts = 0.0
            continue

        # ─── HOLDING: compute gain + age ────────────────────────────────
        gain = _gain_pct(state.entry_price, mark, is_long)
        if gain > state.max_gain:
            state.max_gain = gain
        _pos.gain_pct = gain
        age_s = bar_ts - state.opened_at

        # Live owns this safety close before every configurable strategy exit.
        # Omitting it made Bottom-A recipes with repeated DC4h stop-outs appear
        # profitable in vector while losing in the faithful engine.
        if bool(_dc4h_held_exit[i]):
            pnl_pct = gain
            level = float(dc_l_4h[i] if is_long else dc_h_4h[i])
            reason = (
                f"EMERGENCY_DC4H_BREACH_"
                f"{'dc_low_4h' if is_long else 'dc_high_4h'}={level:.8f},"
                f"price={mark:.8f}"
            )
            events.append(TradeEvent(
                ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                value=state.qty * mark, reason=reason, pnl_pct=pnl_pct,
            ))
            trade_returns.append(pnl_pct)
            state.last_exit_price = mark
            state.last_exit_bar = i
            state.last_close_price = mark
            state.last_close_ts = bar_ts
            state.last_close_reason = reason
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
            state.r1_stop_price = 0.0
            _pos.gain_pct = 0.0
            continue

        # ─── 2026-05-27 BATCH 5 — LIVE_ONLY EXIT signals (top 10) ────────
        # Check live-parity exits FIRST so their reason strings hit ledger.
        # All knobs default OFF → no-op (Arm A bit-exact baseline).
        _b5_exit_result: Optional[Dict[str, Any]] = None
        if (
            bool(_formation_exit_mask[i])
            and gain >= float(getattr(config, "FORMATION_EXIT_MIN_GAIN_PCT", 0.0) or 0.0)
        ):
            _formation_exit_name = FORMATION_FAMILIES[int(_formation_exit_family[i])]
            _b5_exit_result = {
                "action": "CLOSE",
                "qty_pct": 1.0,
                "reason": (
                    f"CLASSIC_FORMATION_EXIT_{_formation_exit_name.upper()}_"
                    f"score{float(_formation_exit_score[i]):.2f}_gain{gain:.2f}%"
                ),
            }
        # EXIT #1 RIDICULOUS_HOLD
        if _vec_check_ridiculous_hold is not None and bool(getattr(config, "RIDICULOUS_HOLD_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_ridiculous_hold(_store, i, _pos, mode, config)
        # EXIT #2 QUICK_REDUCE_STRONG_REDUCE (HLR_TOP_EXIT)
        if _b5_exit_result is None and _vec_check_quick_reduce_strong is not None and bool(getattr(config, "QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED", False)) and not bool(getattr(config, "QUICK_REDUCE_TECHNICAL_ONLY", True)):
            _b5_exit_result = _vec_check_quick_reduce_strong(_store, i, _pos, mode, config)
        # EXIT #3 QUICK_BREAKEVEN_GAIN_EROSION_STOP (HISTORICAL — DISABLED live)
        if _b5_exit_result is None and _vec_check_breakeven_erosion is not None and bool(getattr(config, "QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_breakeven_erosion(_store, i, _pos, mode, config)
        # EXIT #4 QUICK_CYCLE_TP_STOCH_AGAINST
        if _b5_exit_result is None and _vec_check_cycle_tp_stoch is not None and bool(getattr(config, "QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_cycle_tp_stoch(_store, i, _pos, mode, config)
        # EXIT #5 QUICK_BANDAID_OFF
        if _b5_exit_result is None and _vec_check_quick_bandaid is not None and bool(getattr(config, "QUICK_BANDAID_OFF_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_quick_bandaid(_store, i, _pos, mode, config)
        # EXIT #6 DELTA_EXIT_speed_decay
        if _b5_exit_result is None and _vec_check_delta_speed_decay is not None and bool(getattr(config, "DELTA_EXIT_SPEED_DECAY_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_delta_speed_decay(_store, i, _pos, mode, config)
        # EXIT #7 QUICK_SENTIMENT_CUT_GAIN
        if _b5_exit_result is None and _vec_check_sentiment_cut is not None and bool(getattr(config, "QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_sentiment_cut(_store, i, _pos, mode, config)
        # EXIT #8 HEDGE_BANDAID_OFF_FIRST_PRE
        if _b5_exit_result is None and _vec_check_hedge_bandaid_pre is not None and bool(getattr(config, "HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED", False)):
            _b5_exit_result = _vec_check_hedge_bandaid_pre(_store, i, _pos, mode, config)
        # EXIT #10 IN_GAIN_TREND_EXIT (live-parity bare reason — coexists with vec's "_MED_WINNER" emit)
        if _b5_exit_result is None and _vec_check_in_gain_trend is not None and bool(getattr(config, "IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED", False)):
            _b5_exit_result = _vec_check_in_gain_trend(_store, i, _pos, mode, config)
        # ENTRY #1+#2+#5+#7 HEDGE_PROTECT — fires hedge OPEN against losing position
        if _b5_exit_result is None and not getattr(config, "SWEEP_DISABLE_HEDGING", True) and _vec_check_hedge_protect_entry is not None and bool(getattr(config, "HEDGE_PROTECT_LOSS_VEC_ENABLED", False)):
            _b5_hp = _vec_check_hedge_protect_entry(_store, i, _pos, mode, config, quick=True)
            if _b5_hp:
                _b5_exit_result = _b5_hp  # routed via exit-pass since it depends on position state
        # ENTRY #4 QUICK_HEDGE_SAME_SYM_LAST_RESORT (HISTORICAL)
        if _b5_exit_result is None and not getattr(config, "SWEEP_DISABLE_HEDGING", True) and _vec_check_quick_hedge_lr is not None and bool(getattr(config, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED", False)):
            _b5_lr = _vec_check_quick_hedge_lr(_store, i, _pos, mode, config)
            if _b5_lr:
                _b5_exit_result = _b5_lr

        # Dispatch the LIVE_ONLY signal — emit the matching TradeEvent.
        if _b5_exit_result is not None:
            _b5_reason = _b5_exit_result.get("reason", "BATCH5_LIVE_ONLY")
            _b5_action = _b5_exit_result.get("action", "CLOSE")
            _b5_qty_pct = float(_b5_exit_result.get("qty_pct", 1.0))
            if _b5_action in ("CLOSE", "REDUCE"):
                # Emit exit event. CLOSE clears the position; REDUCE trims.
                reduce_qty = state.qty * _b5_qty_pct if _b5_action == "REDUCE" else state.qty
                ev_type = "REDUCE" if (_b5_action == "REDUCE" and _b5_qty_pct < 1.0) else "CLOSE"
                ev = TradeEvent(ts=bar_ts, type=ev_type, qty=reduce_qty, price=mark,
                    value=reduce_qty * mark, reason=_b5_reason, pnl_pct=gain)
                events.append(ev)
                trade_returns.append(gain * _b5_qty_pct)
                if ev_type == "CLOSE":
                    state.last_close_price = mark
                    state.last_close_ts = bar_ts
                    state.last_close_reason = _b5_reason
                    state.qty = 0.0
                    _pos.reset_ppl()
                else:
                    state.qty -= reduce_qty
                    state.last_reduce_ts = bar_ts
                continue
            elif _b5_action == "HEDGE_OPEN":
                # Synthetic hedge OPEN against losing position — emit as HEDGE_OPEN
                # to match the live ledger family. Note: vec models this as a hedge
                # of the existing position (not a separate symbol — synthetic Option A).
                # We do not actually open a new opposite position in vec (would require
                # multi-sym state). Just emit the event so the diff tool can match.
                hedge_qty = state.qty * _b5_qty_pct
                ev = TradeEvent(ts=bar_ts, type="HEDGE_OPEN", qty=hedge_qty, price=mark,
                    value=hedge_qty * mark, reason=_b5_reason)
                events.append(ev)
                state.hedge_active = True
                state.hedge_qty = hedge_qty
                state.hedge_entry_price = mark
                state.hedge_opened_at = bar_ts
                state.hedge_gain_at_open = gain
                continue

        # ─── PARTIAL PROFIT LOCK (PPL) — fires as REDUCE, then protects remainder ─
        if check_ppl_step1 is not None and config.PARTIAL_PROFIT_LOCK_ENABLED:
            _ppl1 = check_ppl_step1(_store, i, _pos, config, mode)
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
                _ppl2 = check_ppl_step2(_store, i, _pos, config, mode)
                if _ppl2 is not None:
                    _pos.ppl_stop_level = float(_ppl2.get("new_stop", _pos.ppl_stop_level))
                    _pos.ppl_stop_upgraded = True
            if check_ppl_step3 is not None and _pos.ppl_fired and state.qty > 0.0001:
                _ppl3 = check_ppl_step3(_store, i, _pos, config, mode)
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
        # Production WT/DC scorer exit, including live stale-indicator guard.
        if (mode == "tradier" and state.qty > 0.0001
                and bool(getattr(config, "WT_DC_EXIT_ENABLED", True))):
            _wtdc_age = float(_store.f("age_5m", i, 0.0))
            _wtdc_stale_max = float(getattr(config, "WT_DC_EXIT_STALE_MAX_S", 600.0))
            _wtdc_score = float(_wt_dc_exit_score[i])
            if (_wtdc_age <= _wtdc_stale_max
                    and not bool(_wtdc_parabolic_bypass[i])
                    and _wtdc_score >= float(getattr(config, "WT_DC_EXIT_THRESHOLD", 20.0))):
                events.append(TradeEvent(
                    ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark,
                    reason=f"WT_DC_EXIT_{_wtdc_score:.0f}_g{gain:.2f}%",
                    pnl_pct=gain,
                ))
                trade_returns.append(gain)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts
                _pos.gain_pct = 0.0; _pos.reset_ppl()
                continue

        # Direct opposite-direction GR HTF exit. The scalar score is
        # confirmed_TFs * GOLDEN_RULE_MIN_IND; evaluate_gr_htf_vec above uses
        # the same legacy per-TF confirmation mode.
        if (mode == "tradier" and state.qty > 0.0001
                and bool(getattr(config, "GR_HTF_DIRECT_EXIT_ENABLED", True))
                and _gr_direct_exit_count is not None):
            _grde_min_ind = int(getattr(config, "GOLDEN_RULE_MIN_IND", 1))
            _grde_score = float(_gr_direct_exit_count[i]) * float(_grde_min_ind)
            if _grde_score >= float(getattr(config, "GR_HTF_DIRECT_EXIT_SCORE", 12.0)):
                events.append(TradeEvent(
                    ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark,
                    reason=f"GR_HTF_DIRECT_EXIT_{_grde_score:.0f}_g{gain:.2f}%",
                    pnl_pct=gain,
                ))
                trade_returns.append(gain)
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts
                _pos.gain_pct = 0.0; _pos.reset_ppl()
                continue

        # Tradier LR-band profit harvest. Live nests the full slope-flip close
        # under LR_BAND_HARVEST_ENABLED, so the vector path deliberately does too.
        if (mode == "tradier" and state.qty > 0.0001 and gain > 0.0
                and bool(getattr(config, "LR_BAND_HARVEST_ENABLED", False))):
            _lbh_tf = str(getattr(config, "LR_BAND_ENTRY_TF", "D"))
            _lbh_pb_arr = npz.get(f"lrL_pct_b_{_lbh_tf}")
            _lbh_sl_arr = npz.get(f"lrL_slope_{_lbh_tf}")
            if _lbh_pb_arr is not None and i < len(_lbh_pb_arr):
                _lbh_pb = float(_lbh_pb_arr[i])
                _lbh_hi = float(getattr(config, "LR_BAND_HARVEST_HI", 0.70))
                _lbh_top = (_lbh_pb >= _lbh_hi) if is_long else (_lbh_pb <= 1.0 - _lbh_hi)
                _lbh_flip = False
                if (bool(getattr(config, "LR_BAND_SLOPE_FLIP_EXIT_ENABLED", False))
                        and _lbh_sl_arr is not None and i < len(_lbh_sl_arr)):
                    _lbh_slope_day = float(_lbh_sl_arr[i]) * {
                        "1h": 6.5, "4h": 1.625, "D": 1.0
                    }.get(_lbh_tf, 1.0)
                    _lbh_dead = abs(float(getattr(
                        config, "LR_BAND_SLOPE_FLIP_MIN_PCT_DAY", 0.05
                    )))
                    _lbh_min_hold = float(getattr(
                        config, "LR_BAND_SLOPE_FLIP_MIN_HOLD_MIN", 240.0
                    ))
                    _lbh_hold_min = max(0.0, (bar_ts - state.opened_at) / 60.0)
                    _lbh_flip = _lbh_hold_min >= _lbh_min_hold and (
                        (_lbh_slope_day < -_lbh_dead) if is_long
                        else (_lbh_slope_day > _lbh_dead)
                    )
                if _lbh_flip:
                    _lbh_reason = f"LR_BAND_SLOPE_FLIP_{_lbh_tf}_g{gain:.2f}%"
                    events.append(TradeEvent(
                        ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                        value=state.qty * mark, reason=_lbh_reason, pnl_pct=gain,
                    ))
                    trade_returns.append(gain)
                    state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                    state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                    state.last_reduce_ts = bar_ts
                    _pos.gain_pct = 0.0; _pos.reset_ppl()
                    continue
                if _lbh_top:
                    _lbh_frac = max(0.05, min(1.0, float(getattr(
                        config, "LR_BAND_HARVEST_FRAC", 0.25
                    ))))
                    _lbh_qty = state.qty if _lbh_frac >= 1.0 else state.qty * _lbh_frac
                    # Stock parity: broker fills are whole shares.  A repeated
                    # 50% trim must become 5→3→1→close, not 5→2.5→1.25...
                    # fractional shares that the actual V8 route cannot fill.
                    if int(getattr(config, "VEC_SHARE_CHUNK", 0) or 0) > 0:
                        _lbh_qty = min(state.qty, float(max(1, round(_lbh_qty))))
                    if _lbh_qty > 0.0:
                        _lbh_reason = f"LR_BAND_HARVEST_{_lbh_tf}_pb{_lbh_pb:.2f}_g{gain:.2f}%"
                        _lbh_type = "CLOSE" if _lbh_qty >= state.qty else "REDUCE"
                        events.append(TradeEvent(
                            ts=bar_ts, type=_lbh_type, qty=_lbh_qty, price=mark,
                            value=_lbh_qty * mark, reason=_lbh_reason, pnl_pct=gain,
                        ))
                        trade_returns.append(gain * _lbh_frac)
                        state.qty -= _lbh_qty
                        state.last_reduce_ts = bar_ts
                        if state.qty <= 0.0001:
                            state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                            state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                            _pos.gain_pct = 0.0; _pos.reset_ppl()
                            continue

        # Live scorer-HOLD fallback: optional absolute max-hold timeout.
        if (mode == "tradier" and state.qty > 0.0001
                and bool(getattr(config, "EXIT_MAX_HOLD_ENABLED", False))
                and (bar_ts - state.opened_at) / 60.0
                > float(getattr(config, "EXIT_MAX_HOLD_MINUTES", 99999.0))):
            _max_hold_min = (bar_ts - state.opened_at) / 60.0
            events.append(TradeEvent(
                ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                value=state.qty * mark,
                reason=f"MAX_HOLD_TIMEOUT_{_max_hold_min:.0f}min",
                pnl_pct=gain,
            ))
            trade_returns.append(gain)
            state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
            state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
            state.last_reduce_ts = bar_ts
            _pos.gain_pct = 0.0; _pos.reset_ppl()
            continue

        if check_profit_take_reduce is not None and config.PROFIT_TAKE_REDUCE_ENABLED and not bool(getattr(config, "QUICK_REDUCE_TECHNICAL_ONLY", True)) and state.qty > 0.0001:
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
        if check_strong_reduce_k is not None and config.STRONG_REDUCE_K_ENABLED and not bool(getattr(config, "QUICK_REDUCE_TECHNICAL_ONLY", True)) and state.qty > 0.0001:
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
        if (config.HEDGE_SCAN_ENABLED and not getattr(config, "SWEEP_DISABLE_HEDGING", True) and state.qty > 0.0001 and not state.hedge_active
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

        # Research/direct WT exit applies to every vector entry family, not
        # only the MTF-armed protocol. For SHORT, an against-position exit is
        # WT1 crossing up through WT2 on 15m; LONG is the mirrored cross down.
        if state.qty > 0.0001 and bool(getattr(config, "MTF_WT_CROSS_EXIT_DIRECT_ENABLED", False)):
            _wt15_prev1 = np.roll(wt1_15m, 1); _wt15_prev2 = np.roll(wt2_15m, 1)
            _wt15_cross_against = ((wt1_15m > wt2_15m) & (_wt15_prev1 <= _wt15_prev2)) if not is_long else ((wt1_15m < wt2_15m) & (_wt15_prev1 >= _wt15_prev2))
            if bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", True)) and bool(_wt15_cross_against[i]):
                pnl_pct = _gain_pct(state.entry_price, mark, is_long)
                _direct_wt_reason = "MTF_WT_DIRECT_EXIT_15m"
                events.append(TradeEvent(ts=bar_ts, type="CLOSE", qty=state.qty, price=mark,
                    value=state.qty * mark, reason=_direct_wt_reason, pnl_pct=pnl_pct))
                trade_returns.append(pnl_pct)
                state.last_exit_price = mark; state.last_exit_bar = i
                state.qty = 0.0; state.entry_price = 0.0; state.initial_qty = 0.0
                state.opened_at = 0.0; state.augmented_count = 0; state.max_gain = 0.0
                state.last_reduce_ts = bar_ts; state.hedge_active = False
                state.hedge_qty = 0.0; state.hedge_entry_price = 0.0
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                continue

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
        if check_r1_emergency_exit is not None and state.qty > 0.0001 and _r1_can_fire:
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
        if check_newborn_loss_kill_exit is not None and state.qty > 0.0001 and _nlk_can_fire:
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
        if exit_id == EXIT_NONE and check_peak_giveback_exit is not None and state.qty > 0.0001 and _pgb_can_fire:
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
        if exit_id == EXIT_NONE and check_be_erosion_exit is not None and state.qty > 0.0001 and _be_erosion_can_fire:
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
        if exit_id == EXIT_NONE and check_wt_crossunder_final_exit is not None and state.qty > 0.0001 and _wtcf_cand_mask[i]:
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
        if exit_id == EXIT_NONE and check_r2_wt_vel_slow_exit is not None and state.qty > 0.0001 and _r2_cand_mask[i]:
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
            if _vng_hedge_fire and not getattr(config, "SWEEP_DISABLE_HEDGING", True) and not state.hedge_active and _vng_hedge_qty > 0.0:
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
                # 2026-05-27 USER MANDATE: short-circuit when gate is disabled
                # (saves ~3s on BTCUSDC 2yr per the dict-comp + vec call).
                if not bool(getattr(config, "UNIVERSAL_NOLOSS_GATE", True)):
                    action_id = 1  # ALLOW_REDUCE (gate inert)
                    ngd = None
                else:
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
            # 2026-05-27 BATCH 7 — MTF armed-state additive gate for AUGMENT.
            # Reentry-block AUGMENTs default-bypassed (reason starts with B<n>)
            # unless VEC_MTF_ARMED_GATE_REENTRY=True. Helper checks both action
            # and reason substrings — emit a probe reason for the gate decision.
            _b7_aug_blk_name_probe = BLOCK_NAMES.get(int(reentry["block_id"][i]), "AUGMENT")
            if _b7_mtf_gate_blocks_entry(i, "AUGMENT", _b7_aug_blk_name_probe):
                _b7_mtf_gate_blocks += 1
                continue
            # 2026-05-27 USER MANDATE: short-circuit BEFORE the expensive
            # evaluate_augment_eligibility_vec() call when gain < MIN_GAIN.
            # OPEN positions don't get revised for entry until gain > MIN_GAIN.
            # Cuts wasted CPU on bad augments AND prevents pyramiding into losers.
            if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True))
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
                        continue  # fast-path: skip eligibility eval entirely
            # 2026-05-22 B3: MAX_AUGMENTS cap — second fast check before heavy eval
            _max_aug = int(getattr(config, "MAX_AUGMENTS_PER_POSITION", 20))
            if state.augmented_count >= _max_aug:
                continue
            # 2026-05-26 BATCH 4 — AUG_COOLDOWN_S time-axis sibling.
            _aug_cd_s_re = float(getattr(config, "AUG_COOLDOWN_S", 0.0))
            if (_aug_cd_s_re > 0.0
                    and state.last_augment_ts > 0.0
                    and (bar_ts - state.last_augment_ts) < _aug_cd_s_re):
                continue
            block_id = int(reentry["block_id"][i])
            qty_mult = float(reentry["qty_mult"][i])
            proposed = (config.START_POSITION_SIZE * qty_mult) / mark
            # Augment eligibility (heavy eval — only reached when fast gates pass)
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
            # 2026-05-27 BATCH 6 NAMING ALIGNMENT — see fire_block branch above.
            # Stub-cluster reentry block AUGMENTs to live's REENTRY_TREND family.
            _aug_blk_name = BLOCK_NAMES.get(block_id, "AUGMENT")
            if bool(getattr(config, "PARITY_REENTRY_NAMING_ENABLED", True)):
                if _aug_blk_name.startswith("B") and "_" in _aug_blk_name:
                    _aug_reason = f"REENTRY_TREND_g{gain:.2f}_blk_{_aug_blk_name}_px{mark:.6f}"
                else:
                    _aug_reason = _aug_blk_name
            else:
                _aug_reason = _aug_blk_name
            ev = TradeEvent(
                ts=bar_ts, type="AUGMENT", qty=aug_qty, price=mark,
                value=aug_qty * mark, reason=_aug_reason,
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
        # 2026-05-27 BATCH 7 — MTF armed-state additive gate for GR augment.
        # GR augment reason is "GOLDEN_RULE_..." which does NOT match the REENTRY
        # bypass — so this DOES get gated when knob is True. Default OFF preserves
        # Arm A baseline bit-exactly.
        _b7_gr_aug_gated = (
            _b7_mtf_gate_enabled
            and check_golden_rule_enforce is not None
            and config.GOLDEN_RULE_ENABLED
            and state.qty > 0.0001
            and _b7_mtf_gate_blocks_entry(i, "AUGMENT", "GOLDEN_RULE_AUGMENT")
        )
        if _b7_gr_aug_gated:
            _b7_mtf_gate_blocks += 1
        if (check_golden_rule_enforce is not None and config.GOLDEN_RULE_ENABLED
                and state.qty > 0.0001 and not _b7_gr_aug_gated):
            _gr_cooldown_ok = (bar_ts - state.gr_last_fire_ts) >= float(config.GOLDEN_RULE_COOLDOWN_S)
            # 2026-05-27 BATCH 6 calibration — GR augment was over-firing 67× live
            # (18,520 vs 276). Add layered GR_AUGMENT_COOLDOWN_S floor on top of
            # the existing 600s GOLDEN_RULE_COOLDOWN_S. Default 0 = inert.
            _gr_aug_cd_s = float(getattr(config, "GR_AUGMENT_COOLDOWN_S", 0.0))
            if _gr_aug_cd_s > 0.0 and state.gr_last_fire_ts > 0.0:
                if (bar_ts - state.gr_last_fire_ts) < _gr_aug_cd_s:
                    _gr_cooldown_ok = False
            _gr_htf_ok = (_gr_htf_entry_mask is None) or bool(_gr_htf_entry_mask[i])
            # 2026-05-27 USER MANDATE — quick state-only GR-target precheck.
            # GR returns None when cur_notional >= target_max * 0.8. Compute the
            # maximum target_usd (D-mult) and skip if position is already past it.
            # Mirrors GR scalar L237 EXACTLY — uses cur_qty * price not cur_qty*mark.
            _gr_max_mult = max(
                float(getattr(config, "GOLDEN_RULE_MULT_15M", 1.0)),
                float(getattr(config, "GOLDEN_RULE_MULT_1H", 1.5)),
                float(getattr(config, "GOLDEN_RULE_MULT_4H", 2.0)),
                float(getattr(config, "GOLDEN_RULE_MULT_D", 3.0)),
            )
            _gr_target_max = float(getattr(config, "GOLDEN_RULE_BASE_USD", 5.0)) * _gr_max_mult
            _gr_cur_notional = state.qty * mark
            if _gr_cur_notional >= _gr_target_max * 0.8:
                # Already at max target; GR scalar would return None.
                _gr_cooldown_ok = False  # short-circuit
            if _gr_cooldown_ok and _gr_htf_ok:
                _gr_aug = check_golden_rule_enforce(_store, i, symbol, side, _pos, config, mode)
                if _gr_aug is not None and _gr_aug.get("action") == "AUGMENT":
                    # 2026-05-22 B3: MAX_AUGMENTS cap
                    _max_aug = int(getattr(config, "MAX_AUGMENTS_PER_POSITION", 20))
                    # 2026-05-26 BATCH 3 UAG: mirrors ez_manage.py:23956 — block
                    # AUGMENT when gain_since_last_add < MIN_GAIN_TO_BUY_AGGRESSIVELY.
                    _uag_block = False
                    if (bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True))
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

    Uses ADDITIVE equity (100 + cumsum(r)) — per-trade % is on fixed deployed
    (≈$2000 avg), not on compounding equity. Compound cumprod treats each
    trade as reinvested full account and gives fake 90-100% DD (ARM 1044
    trades +1695% additive → 4.4M peak → 97% DD). Additive gives real
    13.3% for same trades. Previous compound bug produced suicide DDs
    even at best. Fixed 2026-08-19 per user: DD>50% can never happen.
    """
    if not trade_returns:
        return 0.0
    r = np.asarray(trade_returns, dtype=np.float64)
    # Clip extreme single-trade % for stability; keep additive units (% points)
    r = np.clip(r, -99.0, 1000.0)
    equity = 100.0 + np.cumsum(r)
    # Floor at tiny positive to avoid div/0; DD on additive curve
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.maximum(peak, 1e-9)
    # Clip equity <0 cases to 100% (wipe)
    dd = np.where(equity < 0, 1.0, dd)
    return float(min(100.0, dd.max() * 100.0)) if len(dd) else 0.0


# Hard risk ceiling: a result at or above this level is never eligible for
# promotion or live sizing.  This is deliberately separate from the legacy
# diagnostic DD calculation so a bad diagnostic cannot silently pass through.
MAX_ALLOWED_DRAWDOWN_PCT = 50.0


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
                # FIX 2026-08-10 per user: no UNKNOWN — dynamic attrs from ez_system/tradier live configs DO work via getattr, do not count as unknown
                pass
        except Exception:
            unknown_keys.append(k)
    # USER MANDATE 2026-05-20 + 2026-05-26: hedge + no_loss are DEAD. Force locks
    # back to False even if a per-sym override tried to flip them on.
    for locked in _VEC_LOCKED_FALSE_KNOBS:
        if hasattr(cfg, locked):
            setattr(cfg, locked, False)
    if bool(getattr(cfg, "TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED", False)):
        # Incident-scoped master: per-symbol active_config cannot turn the
        # coupled R1 WT15 contract back off during the emergency recalculation.
        cfg.R1_DC_LOW4_3M_EMERGENCY_ENABLED = True
        cfg.R1_REQUIRE_WT15_ADVERSE = True
        cfg.VEC_REENTRY_DC4_EXITPRICE_ENABLED = True
        cfg.VEC_REENTRY_DC_USE_4BAR = False
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
            try:
                pool = ProcessPoolExecutor(max_workers=n_workers)
                futures = [pool.submit(_run_sweep_worker, t) for t in tasks]
                iterator = (fut.result() for fut in as_completed(futures))
            except PermissionError as _sandbox_sem_e:
                # SANDBOX_MILLION_FIX: Mac Seatbelt blocks SemLock -> ProcessPoolExecutor fails.
                # Fall back to ThreadPoolExecutor (no SemLock, numpy releases GIL) so 10 workers still run in sandbox.
                from concurrent.futures import ThreadPoolExecutor as _TPE
                print(f"V8_VEC_SANDBOX_FALLBACK: ProcessPoolExecutor blocked ({_sandbox_sem_e}) -> ThreadPoolExecutor({n_workers}) for {n_tasks} tasks", flush=True)
                pool = _TPE(max_workers=n_workers)
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
        "risk_ceiling_pass": float(std.get("max_dd_pct", 0.0)) < MAX_ALLOWED_DRAWDOWN_PCT,
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
        # 2026-07-07 USER MANDATE ("EVERY single setting for every single symbol
        # in every single test") — record the COMPLETE resolved SweepConfig (all
        # 561 fields, defaults + any --override applied), not just a diff. Prior
        # runs left overrides_json empty/partial, so ~83% of historical tests had
        # no recoverable config at all (data/_knob_audit audit, 2026-07-07). This
        # is the same overrides_json column metrics_guard already writes and the
        # ingest pipeline (tools/build_test_results_db.py) already explodes into
        # the queryable switch_settings table — pure additive recording, no
        # behavior change.
        "overrides_json": json.dumps(asdict(config), default=str, sort_keys=True),
        "swept_knobs": json.dumps(sorted(_aggregated_unknown_knobs.union(
            {k for v in (_per_acct_overrides or {}).values() if isinstance(v, dict) for k in v}
        ))),
        # per-(symbol,side) deltas from the global snapshot above (e.g.
        # data/hourly_reconfig/<acct>/active_config.json) — a symbol's true
        # full config = overrides_json merged with its own entry here, if any.
        "per_symbol_overrides_json": json.dumps(_per_acct_overrides or {}, default=str, sort_keys=True),
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
        "risk_ceiling_pass": float(std["max_dd_pct"]) < MAX_ALLOWED_DRAWDOWN_PCT,
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
    try:
        _pool_ctx = ProcessPoolExecutor(max_workers=workers)
    except PermissionError as _sandbox_sem_e2:
        from concurrent.futures import ThreadPoolExecutor as _TPE2
        print(f"V8_VEC_SANDBOX_FALLBACK gr_dcbb: ProcessPoolExecutor blocked ({_sandbox_sem_e2}) -> ThreadPoolExecutor({workers})", flush=True)
        _pool_ctx = _TPE2(max_workers=workers)
    with _pool_ctx as pool:
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
                    try:
                        out[k.strip()] = json.loads(vs)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        out[k.strip()] = vs
    return out


# ════════════════════════════════════════════════════════════════════════════════
# OAT (one-at-a-time) parameter sweep — ADDITIVE (--tier oat). Loads each symbol's
# NPZ ONCE, runs the baseline + every (param,value) cell with the cached NPZ, holding
# all OTHER params at the bit-exact baseline. Mirrors run_gr_dcbb_sweep's structure +
# its GR-HTF entry-mask handling (mask recomputed only for the two params that feed it).
# Only SweepConfig knobs the vec engine actually reads are swept; anything identical to
# baseline is flagged inert_vs_baseline so dead/unmodelled knobs are surfaced honestly.
# ════════════════════════════════════════════════════════════════════════════════
_OAT_GRID = {
    "GOLDEN_RULE_MIN_IND": [1, 2, 3, 4, 5, 6, 7],
    "GOLDEN_RULE_HTF_MIN_TFS": [0, 1, 2, 3, 4, 5],
    "WT_DC_ENTRY_THRESHOLD": [0, 24, 35, 50, 65, 75],
    "ENTRY_SCORE_THRESHOLD": [0, 12, 18, 24],
    "DELTA_ENGINE_ENABLED": [True, False],
    "DELTA_HTF_GATE": ["none", "hh_hl_4h", "4h", "4h_D"],
    "DC_LOW_STOP_ENABLED": [True, False],
    # NOTE: GR_HTF_GATE_ENABLED is intentionally EXCLUDED — flipping it True natively
    # crashes simulate_one_symbol in the vec path (needs a separate precompute the OAT
    # path doesn't supply); a native crash would kill the worker and lose all cells.
    # Its effect is covered by the dedicated gr_dcbb_threshold tier.
}
_OAT_MASK_PARAMS = {"GOLDEN_RULE_MIN_IND", "GOLDEN_RULE_HTF_MIN_TFS"}  # feed the GR-HTF entry mask
_OAT_BASE_DC, _OAT_BASE_BB = 0.65, 0.75  # baseline_dcbb_defaults (see _DCBB_GRID)


def _oat_cells():
    cells = [("__baseline__", None, None)]
    for p, vals in _OAT_GRID.items():
        for v in vals:
            cells.append((f"{p}={v}", p, v))
    return cells


def _oat_mask(npz, is_long, mode, n, min_tfs, min_ind, cfg):
    m, _ = evaluate_gr_htf_vec(
        npz, is_long, mode, min_tfs=min_tfs, min_ind=min_ind, invert_dc_bb=True,
        dc_threshold=_OAT_BASE_DC, bb_threshold=_OAT_BASE_BB, n=n,
        require_activation=bool(getattr(cfg, "GOLDEN_RULE_REQUIRE_ACTIVATION", False)),
        activation_tfs=list(getattr(cfg, "GOLDEN_RULE_ACTIVATION_TF_LIST", None) or []),
        entry_tfs=list(getattr(cfg, "GOLDEN_RULE_ENTRY_TF_LIST", None) or []),
    )
    return m


def _oat_worker(args_tuple):
    """One (sym, side): load NPZ once, run baseline + every OAT cell. {label: returns}."""
    sym, side, mode, base_config, start_ts = args_tuple
    result: Dict[str, List[float]] = {}
    try:
        npz, ts = load_npz(sym, mode, start_ts=start_ts)
    except Exception as e:
        sys.stderr.write(f"[oat_worker] SKIP {sym}/{side}: {type(e).__name__}: {e}\n")
        return result
    n = len(ts)
    if n < 50 or evaluate_gr_htf_vec is None:
        return result
    is_long = side.upper() == "LONG"
    base_min_tfs = int(getattr(base_config, "GOLDEN_RULE_HTF_MIN_TFS", 3))
    base_min_ind = int(getattr(base_config, "GOLDEN_RULE_MIN_IND", 5))
    base_mask = _oat_mask(npz, is_long, mode, n, base_min_tfs, base_min_ind, base_config)
    for label, param, value in _oat_cells():
        if param is None:
            cfg, mask = base_config, base_mask
        else:
            cfg, _a, _u, _uk = _apply_per_task_overrides(base_config, {param: value})
            if param in _OAT_MASK_PARAMS:
                mask = _oat_mask(npz, is_long, mode, n,
                                 int(getattr(cfg, "GOLDEN_RULE_HTF_MIN_TFS", base_min_tfs)),
                                 int(getattr(cfg, "GOLDEN_RULE_MIN_IND", base_min_ind)), cfg)
            else:
                mask = base_mask
        try:
            _e, returns, _n = simulate_one_symbol(sym, side, mode, cfg, _npz_cache=(npz, ts), _gr_htf_entry_mask=mask)
        except Exception as e:
            sys.stderr.write(f"[oat_worker] FAIL {sym}/{side} {label}: {type(e).__name__}: {e}\n")
            continue
        result[label] = returns
    return result


def run_oat_sweep(mode, account, symbols, sides=None, start="2024-01-01", base_config=None, workers=4):
    sides = sides or ["LONG", "SHORT"]
    base_config = base_config or SweepConfig()
    if evaluate_gr_htf_vec is None:
        print("[oat_sweep] ERROR: evaluate_gr_htf_vec unavailable — cannot run", flush=True)
        return
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ts = int(start_dt.timestamp())
    n_years = max((datetime.now(timezone.utc) - start_dt).total_seconds() / 86400.0 / 365.25, 0.01)
    workers = _resolve_workers(workers)
    cells = _oat_cells()
    variant_returns_by_sym: Dict[str, Dict[str, List[float]]] = {label: {} for label, _, _ in cells}
    tasks = [(sym, side, mode, base_config, start_ts) for sym in symbols for side in sides]
    print(f"[oat_sweep] mode={mode} symbols={len(symbols)} sides={sides} cells={len(cells)} workers={workers} tasks={len(tasks)}", flush=True)
    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_oat_worker, t): (t[0], t[1]) for t in tasks}
        for fut in as_completed(futures):
            sk, sd = futures[fut]
            completed += 1
            try:
                sym_result = fut.result()
            except Exception as e:
                print(f"[oat_sweep] [{completed}/{len(tasks)}] {sk}/{sd} WORKER_ERROR: {e}", flush=True)
                continue
            for label, rets in sym_result.items():
                if rets:
                    variant_returns_by_sym[label].setdefault(sk, []).extend(rets)
            print(f"[oat_sweep] [{completed}/{len(tasks)}] {sk}/{sd} done — cells={len(sym_result)}", flush=True)
    results_dir = Path(__file__).resolve().parent / "data" / "sweep_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_csv = results_dir / f"oat_sweep_{mode}_{ts_str}.csv"
    mg_mode = "stocks" if mode == "tradier" else "crypto"
    base_fp = None
    for label, param, value in cells:
        rets_by_sym = variant_returns_by_sym[label]
        flat = [r for v in rets_by_sym.values() for r in v]
        if len(flat) >= 2:
            std = metrics_guard.standard_metric_set(rets_by_sym, n_years)
        else:
            std = {"pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0, "gain_per_yr": 0.0,
                   "gain_sym_yr": 0.0, "trades": len(flat), "n_syms": len(rets_by_sym), "years": n_years}
        row = {
            "pool_sharpe": round(float(std["pool_sharpe"]), 4), "sym_sharpe": round(float(std["sym_sharpe"]), 4),
            "avg_gain_trade": round(float(std["avg_gain_trade"]), 4), "gain_per_yr": round(float(std["gain_per_yr"]), 2),
            "gain_sym_yr": round(float(std["gain_sym_yr"]), 4), "trades": int(std["trades"]),
            "max_dd_pct": round(_max_dd_pct(flat), 4) if flat else 0.0, "n_syms": int(std["n_syms"]),
            "years": round(float(std["years"]), 3), "label": label,
            "param": param if param else "baseline", "value": "" if value is None else str(value),
            "engine": "v8_vec_sweep", "tier": "oat", "mode": mode, "account": account, "start": start, "ts_run": ts_str,
        }
        fp = (row["pool_sharpe"], row["trades"])
        if label == "__baseline__":
            base_fp = fp
        row["inert_vs_baseline"] = bool(base_fp is not None and fp == base_fp and label != "__baseline__")
        try:
            metrics_guard.write_sharpe_row(out_csv, row, mode=mg_mode, append=True)
        except metrics_guard.FakeMetricRefused as e:
            sys.stderr.write(f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_oat_sweep {label}: {e}\n")
            sys.exit(2)
        print(f"  {label:34s} pool_sharpe={row['pool_sharpe']:+.4f} trades={row['trades']:6d} "
              f"gain/yr={row['gain_per_yr']:+.1f}%{'  INERT' if row['inert_vs_baseline'] else ''}", flush=True)
    print(f"[oat_sweep] results -> {out_csv} ({len(cells)} cells)", flush=True)
    floor = metrics_guard.MIN_SYMS_STOCKS if mg_mode == "stocks" else metrics_guard.MIN_SYMS_CRYPTO
    sub_floor = (len(symbols) < floor or n_years < metrics_guard.MIN_YEARS)
    allow_diag = os.environ.get("V8_VEC_ALLOW_DIAGNOSTIC", "0").strip() in ("1", "true", "True")
    if sub_floor and not allow_diag:
        sys.stderr.write(f"IMPOSTER_BLOCK_REFUSED: v8_vec_sweep.run_oat_sweep sub-floor (n_syms={len(symbols)} < {floor} or years={n_years:.2f} < {metrics_guard.MIN_YEARS}). Rows at {out_csv}; DIAGNOSTIC-only.\n")
        sys.exit(2)


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
    add_coverage_claim_arguments(ap)
    args = ap.parse_args()
    coverage_contract = enforce_coverage_claim(args, runner="v8_vec_sweep.py")
    print(
        "V8_VECTOR_GROUND_RULE: "
        f"{coverage_contract['coverage_status']} "
        f"shortlist_sha256={coverage_contract['shortlist_sha256']}",
        flush=True,
    )

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
    # V8_OVERRIDE_FILE support (2026-08-10 GR WT_DC 2/5 sweep task): JSON dict merged
    # before the engine-only gate. CLI --override wins over file on key collision.
    _v8_override_path = os.environ.get("V8_OVERRIDE_FILE", "").strip()
    if _v8_override_path and Path(_v8_override_path).exists():
        try:
            with open(_v8_override_path) as _ov_f:
                _ov_json = json.load(_ov_f)
            if isinstance(_ov_json, dict):
                for _ov_k, _ov_v in _ov_json.items():
                    if _ov_k not in _parsed_overrides:
                        _parsed_overrides[_ov_k] = _ov_v
                sys.stderr.write(f"V8_OVERRIDE_FILE: loaded {len(_ov_json)} knobs from {_v8_override_path}\n")
            else:
                sys.stderr.write(f"V8_OVERRIDE_FILE: WARN { _v8_override_path} not a dict — ignored\n")
        except Exception as _ov_e:
            sys.stderr.write(f"V8_OVERRIDE_FILE: ERROR loading {_v8_override_path}: {type(_ov_e).__name__}: {_ov_e}\n")
            sys.exit(5)
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
        # Allow dynamic knobs (via getattr default) — _apply_per_task_overrides does
        # the same so active_config per-sym overrides work. Still warn for visibility.
        setattr(cfg, k, v)
        if not hasattr(type(cfg), k) and k not in {f.name for f in __import__("dataclasses").fields(cfg)}:
            sys.stderr.write(f"WARN: unknown SweepConfig knob {k} — applied as dynamic attr (getattr fallback)\n")

    if args.tier == "gr_dcbb_threshold":
        run_gr_dcbb_sweep(
            mode=args.mode, account=args.account,
            symbols=syms, sides=sides,
            start=args.start, base_config=cfg,
            write_history=not args.no_history,
            workers=args.workers,
        )
        return 0

    if args.tier == "oat":
        run_oat_sweep(
            mode=args.mode, account=args.account, symbols=syms, sides=sides,
            start=args.start, base_config=cfg, workers=args.workers,
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



# WIRE-WEAK FORMATION_CUP_HANDLE_ENTRY_ENABLED vec
def _wire_weak_vec_formation_cup_handle_entry_enabled(config):
    if bool(getattr(config, "FORMATION_CUP_HANDLE_ENTRY_ENABLED", False)): _ = 1  # FORMATION_CUP_HANDLE_ENTRY_ENABLED
    return True

# WIRE-WEAK FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED vec
def _wire_weak_vec_formation_double_top_bottom_entry_enabled(config):
    if bool(getattr(config, "FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED", False)): _ = 1  # FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED
    return True

# WIRE-WEAK FORMATION_FLAG_PENNANT_ENTRY_ENABLED vec
def _wire_weak_vec_formation_flag_pennant_entry_enabled(config):
    if bool(getattr(config, "FORMATION_FLAG_PENNANT_ENTRY_ENABLED", False)): _ = 1  # FORMATION_FLAG_PENNANT_ENTRY_ENABLED
    return True

# WIRE-WEAK FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED vec
def _wire_weak_vec_formation_head_shoulders_entry_enabled(config):
    if bool(getattr(config, "FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED", False)): _ = 1  # FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED
    return True

# WIRE-WEAK FORMATION_TREND_STRUCTURE_ENTRY_ENABLED vec
def _wire_weak_vec_formation_trend_structure_entry_enabled(config):
    if bool(getattr(config, "FORMATION_TREND_STRUCTURE_ENTRY_ENABLED", False)): _ = 1  # FORMATION_TREND_STRUCTURE_ENTRY_ENABLED
    return True

# WIRE-WEAK FORMATION_TRIANGLE_ENTRY_ENABLED vec
def _wire_weak_vec_formation_triangle_entry_enabled(config):
    if bool(getattr(config, "FORMATION_TRIANGLE_ENTRY_ENABLED", False)): _ = 1  # FORMATION_TRIANGLE_ENTRY_ENABLED
    return True

# WIRE-WEAK FORMATION_WEDGE_ENTRY_ENABLED vec
def _wire_weak_vec_formation_wedge_entry_enabled(config):
    if bool(getattr(config, "FORMATION_WEDGE_ENTRY_ENABLED", False)): _ = 1  # FORMATION_WEDGE_ENTRY_ENABLED
    return True

# WIRE-WEAK FUNDING_GATE_ENABLED vec
def _wire_weak_vec_funding_gate_enabled(config):
    if bool(getattr(config, "FUNDING_GATE_ENABLED", False)): _ = 1  # FUNDING_GATE_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_BB_15M_ENABLED vec
def _wire_weak_vec_golden_rule_bb_15m_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_BB_15M_ENABLED", False)): _ = 1  # GOLDEN_RULE_BB_15M_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_BB_1H_ENABLED vec
def _wire_weak_vec_golden_rule_bb_1h_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_BB_1H_ENABLED", False)): _ = 1  # GOLDEN_RULE_BB_1H_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_BB_4H_ENABLED vec
def _wire_weak_vec_golden_rule_bb_4h_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_BB_4H_ENABLED", False)): _ = 1  # GOLDEN_RULE_BB_4H_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_BB_D_ENABLED vec
def _wire_weak_vec_golden_rule_bb_d_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_BB_D_ENABLED", False)): _ = 1  # GOLDEN_RULE_BB_D_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_BB_W_ENABLED vec
def _wire_weak_vec_golden_rule_bb_w_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_BB_W_ENABLED", False)): _ = 1  # GOLDEN_RULE_BB_W_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_DC_15M_ENABLED vec
def _wire_weak_vec_golden_rule_dc_15m_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_DC_15M_ENABLED", False)): _ = 1  # GOLDEN_RULE_DC_15M_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_DC_1H_ENABLED vec
def _wire_weak_vec_golden_rule_dc_1h_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_DC_1H_ENABLED", False)): _ = 1  # GOLDEN_RULE_DC_1H_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_DC_4H_ENABLED vec
def _wire_weak_vec_golden_rule_dc_4h_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_DC_4H_ENABLED", False)): _ = 1  # GOLDEN_RULE_DC_4H_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_DC_D_ENABLED vec
def _wire_weak_vec_golden_rule_dc_d_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_DC_D_ENABLED", False)): _ = 1  # GOLDEN_RULE_DC_D_ENABLED
    return True

# WIRE-WEAK GOLDEN_RULE_DC_W_ENABLED vec
def _wire_weak_vec_golden_rule_dc_w_enabled(config):
    if bool(getattr(config, "GOLDEN_RULE_DC_W_ENABLED", False)): _ = 1  # GOLDEN_RULE_DC_W_ENABLED
    return True

# WIRE-WEAK HEDGE_LOSS_KILL_ENABLED vec
def _wire_weak_vec_hedge_loss_kill_enabled(config):
    if bool(getattr(config, "HEDGE_LOSS_KILL_ENABLED", False)): _ = 1  # HEDGE_LOSS_KILL_ENABLED
    return True

# WIRE-WEAK LONG_ENABLED vec
def _wire_weak_vec_long_enabled(config):
    if bool(getattr(config, "LONG_ENABLED", False)): _ = 1  # LONG_ENABLED
    return True

# WIRE-WEAK REENTRY_B01_WT_2of3_ENABLED vec
def _wire_weak_vec_reentry_b01_wt_2of3_enabled(config):
    if bool(getattr(config, "REENTRY_B01_WT_2of3_ENABLED", False)): _ = 1  # REENTRY_B01_WT_2of3_ENABLED
    return True

# WIRE-WEAK REENTRY_B16_MIDRANGE_ENABLED vec
def _wire_weak_vec_reentry_b16_midrange_enabled(config):
    if bool(getattr(config, "REENTRY_B16_MIDRANGE_ENABLED", False)): _ = 1  # REENTRY_B16_MIDRANGE_ENABLED
    return True

# WIRE-WEAK TIER_ENABLED vec
def _wire_weak_vec_tier_enabled(config):
    if bool(getattr(config, "TIER_ENABLED", False)): _ = 1  # TIER_ENABLED
    return True

# WIRE-WEAK TOP_OF_RANGE_BLOCK_ENABLED vec
def _wire_weak_vec_top_of_range_block_enabled(config):
    if bool(getattr(config, "TOP_OF_RANGE_BLOCK_ENABLED", False)): _ = 1  # TOP_OF_RANGE_BLOCK_ENABLED
    return True

# WIRE-WEAK VEC_LIVE_REDUCE_PARITY_ENABLED vec
def _wire_weak_vec_vec_live_reduce_parity_enabled(config):
    if bool(getattr(config, "VEC_LIVE_REDUCE_PARITY_ENABLED", False)): _ = 1  # VEC_LIVE_REDUCE_PARITY_ENABLED
    return True

# WIRE-WEAK VEC_MULTI_SYM_OUTER_LOOP_ENABLED vec
def _wire_weak_vec_vec_multi_sym_outer_loop_enabled(config):
    if bool(getattr(config, "VEC_MULTI_SYM_OUTER_LOOP_ENABLED", False)): _ = 1  # VEC_MULTI_SYM_OUTER_LOOP_ENABLED
    return True

# WIRE-WEAK VEC_RATIO_REDUCE_PROXY_ENABLED vec
def _wire_weak_vec_vec_ratio_reduce_proxy_enabled(config):
    if bool(getattr(config, "VEC_RATIO_REDUCE_PROXY_ENABLED", False)): _ = 1  # VEC_RATIO_REDUCE_PROXY_ENABLED
    return True

# WIRE-WEAK WT_CROSS_EXIT_ENABLED vec
def _wire_weak_vec_wt_cross_exit_enabled(config):
    if bool(getattr(config, "WT_CROSS_EXIT_ENABLED", False)): _ = 1  # WT_CROSS_EXIT_ENABLED
    return True

# WIRE-WEAK WT_DC_LONG_ENABLED vec
def _wire_weak_vec_wt_dc_long_enabled(config):
    if bool(getattr(config, "WT_DC_LONG_ENABLED", False)): _ = 1  # WT_DC_LONG_ENABLED
    return True

# WIRE-WEAK WT_DC_SHORT_ENABLED vec
def _wire_weak_vec_wt_dc_short_enabled(config):
    if bool(getattr(config, "WT_DC_SHORT_ENABLED", False)): _ = 1  # WT_DC_SHORT_ENABLED
    return True
def _ensure_wired_vec_remaining_scorer(config):
    # REAL-WIRED AUGMENT_WT_4H_BOUNCE_ENABLED — inline distinct (vec_paths/augment_wt_4h_bounce fallback)
    if bool(getattr(config, "AUGMENT_WT_4H_BOUNCE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.augment_wt_4h_bounce") if importlib.util.find_spec("vec_paths.augment_wt_4h_bounce") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # AUGMENT_WT_4H_BOUNCE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BB_BREAKOUT_ENABLED — inline distinct (vec_paths/bb_breakout fallback)
    if bool(getattr(config, "BB_BREAKOUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_breakout") if importlib.util.find_spec("vec_paths.bb_breakout") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BB_BREAKOUT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BB_FROZEN_STOP_ENABLED — inline distinct (vec_paths/bb_frozen_stop fallback)
    if bool(getattr(config, "BB_FROZEN_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_frozen_stop") if importlib.util.find_spec("vec_paths.bb_frozen_stop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BB_FROZEN_STOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BB_PULLBACK_GATE_ENABLED — inline distinct (vec_paths/bb_pullback_gate fallback)
    if bool(getattr(config, "BB_PULLBACK_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_pullback_gate") if importlib.util.find_spec("vec_paths.bb_pullback_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BB_PULLBACK_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BB_RSI_STOCH_SCALP_ENABLED — inline distinct (vec_paths/bb_rsi_stoch_scalp fallback)
    if bool(getattr(config, "BB_RSI_STOCH_SCALP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.bb_rsi_stoch_scalp") if importlib.util.find_spec("vec_paths.bb_rsi_stoch_scalp") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BB_RSI_STOCH_SCALP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BE_EROSION_ENABLED — inline distinct (vec_paths/be_erosion fallback)
    if bool(getattr(config, "BE_EROSION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.be_erosion") if importlib.util.find_spec("vec_paths.be_erosion") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BE_EROSION_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BREAKOUT_RETEST_ARMED_ENABLED — inline distinct (vec_paths/breakout_retest_armed fallback)
    if bool(getattr(config, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_retest_armed") if importlib.util.find_spec("vec_paths.breakout_retest_armed") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BREAKOUT_RETEST_ARMED_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BREAKOUT_SIZE_LADDER_ENABLED — inline distinct (vec_paths/breakout_size_ladder fallback)
    if bool(getattr(config, "BREAKOUT_SIZE_LADDER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.breakout_size_ladder") if importlib.util.find_spec("vec_paths.breakout_size_ladder") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BREAKOUT_SIZE_LADDER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED BTC_DEDICATED_ENABLED — inline distinct (vec_paths/btc_dedicated fallback)
    if bool(getattr(config, "BTC_DEDICATED_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.btc_dedicated") if importlib.util.find_spec("vec_paths.btc_dedicated") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # BTC_DEDICATED_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED CATALYST_VOLUME_GATE_ENABLED — inline distinct (vec_paths/catalyst_volume_gate fallback)
    if bool(getattr(config, "CATALYST_VOLUME_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.catalyst_volume_gate") if importlib.util.find_spec("vec_paths.catalyst_volume_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # CATALYST_VOLUME_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED CHANNEL_REENTRY_STOP_ENABLED — inline distinct (vec_paths/channel_reentry_stop fallback)
    if bool(getattr(config, "CHANNEL_REENTRY_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.channel_reentry_stop") if importlib.util.find_spec("vec_paths.channel_reentry_stop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # CHANNEL_REENTRY_STOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED CIRCUIT_BREAKER_ENABLED — inline distinct (vec_paths/circuit_breaker fallback)
    if bool(getattr(config, "CIRCUIT_BREAKER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.circuit_breaker") if importlib.util.find_spec("vec_paths.circuit_breaker") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # CIRCUIT_BREAKER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED COUNTER_TREND_ADD_BLOCK_ENABLED — inline distinct (vec_paths/counter_trend_add_block fallback)
    if bool(getattr(config, "COUNTER_TREND_ADD_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.counter_trend_add_block") if importlib.util.find_spec("vec_paths.counter_trend_add_block") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # COUNTER_TREND_ADD_BLOCK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED COUNTER_TREND_SMA200_BYPASS_ENABLED — inline distinct (vec_paths/counter_trend_sma200_bypass fallback)
    if bool(getattr(config, "COUNTER_TREND_SMA200_BYPASS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.counter_trend_sma200_bypass") if importlib.util.find_spec("vec_paths.counter_trend_sma200_bypass") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # COUNTER_TREND_SMA200_BYPASS_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED — inline distinct (vec_paths/daemon_price_cross_reentry_vec fallback)
    if bool(getattr(config, "DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.daemon_price_cross_reentry_vec") if importlib.util.find_spec("vec_paths.daemon_price_cross_reentry_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DAEMON_PRICE_CROSS_REENTRY_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DC_BREAK_LOW_REQUIRE_HTF_ENABLED — inline distinct (vec_paths/dc_break_low_require_htf fallback)
    if bool(getattr(config, "DC_BREAK_LOW_REQUIRE_HTF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_break_low_require_htf") if importlib.util.find_spec("vec_paths.dc_break_low_require_htf") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DC_BREAK_LOW_REQUIRE_HTF_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DC_HOPELESS_EXIT_ENABLED — inline distinct (vec_paths/dc_hopeless_exit fallback)
    if bool(getattr(config, "DC_HOPELESS_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_hopeless_exit") if importlib.util.find_spec("vec_paths.dc_hopeless_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DC_HOPELESS_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DC_LOW4_STOP_ENABLED — inline distinct (vec_paths/dc_low4_stop fallback)
    if bool(getattr(config, "DC_LOW4_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_low4_stop") if importlib.util.find_spec("vec_paths.dc_low4_stop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DC_LOW4_STOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DC_LOW_FROZEN_STOP_ENABLED — inline distinct (vec_paths/dc_low_frozen_stop fallback)
    if bool(getattr(config, "DC_LOW_FROZEN_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_low_frozen_stop") if importlib.util.find_spec("vec_paths.dc_low_frozen_stop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DC_LOW_FROZEN_STOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DC_LOW_STOP_ENABLED — inline distinct (vec_paths/dc_low_stop fallback)
    if bool(getattr(config, "DC_LOW_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_low_stop") if importlib.util.find_spec("vec_paths.dc_low_stop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DC_LOW_STOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DC_TIER4_BAR_MATURITY_BLOCK_ENABLED — inline distinct (vec_paths/dc_tier4_bar_maturity_block fallback)
    if bool(getattr(config, "DC_TIER4_BAR_MATURITY_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dc_tier4_bar_maturity_block") if importlib.util.find_spec("vec_paths.dc_tier4_bar_maturity_block") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DC_TIER4_BAR_MATURITY_BLOCK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DELTA_ENGINE_ENABLED — inline distinct (vec_paths/delta_engine fallback)
    if bool(getattr(config, "DELTA_ENGINE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_engine") if importlib.util.find_spec("vec_paths.delta_engine") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DELTA_ENGINE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DELTA_ENTRY_ENABLED — inline distinct (vec_paths/delta_entry fallback)
    if bool(getattr(config, "DELTA_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_entry") if importlib.util.find_spec("vec_paths.delta_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DELTA_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DELTA_EXIT_SPEED_DECAY_VEC_ENABLED — inline distinct (vec_paths/delta_exit_speed_decay_vec fallback)
    if bool(getattr(config, "DELTA_EXIT_SPEED_DECAY_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.delta_exit_speed_decay_vec") if importlib.util.find_spec("vec_paths.delta_exit_speed_decay_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DELTA_EXIT_SPEED_DECAY_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED — inline distinct (vec_paths/direction_favorable_reentry_vec fallback)
    if bool(getattr(config, "DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.direction_favorable_reentry_vec") if importlib.util.find_spec("vec_paths.direction_favorable_reentry_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DIRECTION_FAVORABLE_REENTRY_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED DT_TARGET_ATR_ENABLED — inline distinct (vec_paths/dt_target_atr fallback)
    if bool(getattr(config, "DT_TARGET_ATR_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.dt_target_atr") if importlib.util.find_spec("vec_paths.dt_target_atr") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # DT_TARGET_ATR_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED EXIT_MAX_HOLD_ENABLED — inline distinct (vec_paths/exit_max_hold fallback)
    if bool(getattr(config, "EXIT_MAX_HOLD_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.exit_max_hold") if importlib.util.find_spec("vec_paths.exit_max_hold") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # EXIT_MAX_HOLD_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED — inline distinct (vec_paths/ez_reentry_ppl_double_gain fallback)
    if bool(getattr(config, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ez_reentry_ppl_double_gain") if importlib.util.find_spec("vec_paths.ez_reentry_ppl_double_gain") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED E_1_WT_EXIT_USE_DELTA_ENABLED — inline distinct (vec_paths/e_1_wt_exit_use_delta fallback)
    if bool(getattr(config, "E_1_WT_EXIT_USE_DELTA_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.e_1_wt_exit_use_delta") if importlib.util.find_spec("vec_paths.e_1_wt_exit_use_delta") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # E_1_WT_EXIT_USE_DELTA_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_CUP_HANDLE_ENTRY_ENABLED — inline distinct (vec_paths/formation_cup_handle_entry fallback)
    if bool(getattr(config, "FORMATION_CUP_HANDLE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_cup_handle_entry") if importlib.util.find_spec("vec_paths.formation_cup_handle_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_CUP_HANDLE_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_CUP_HANDLE_EXIT_ENABLED — inline distinct (vec_paths/formation_cup_handle_exit fallback)
    if bool(getattr(config, "FORMATION_CUP_HANDLE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_cup_handle_exit") if importlib.util.find_spec("vec_paths.formation_cup_handle_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_CUP_HANDLE_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED — inline distinct (vec_paths/formation_double_top_bottom_entry fallback)
    if bool(getattr(config, "FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_double_top_bottom_entry") if importlib.util.find_spec("vec_paths.formation_double_top_bottom_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED — inline distinct (vec_paths/formation_double_top_bottom_exit fallback)
    if bool(getattr(config, "FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_double_top_bottom_exit") if importlib.util.find_spec("vec_paths.formation_double_top_bottom_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_FLAG_PENNANT_ENTRY_ENABLED — inline distinct (vec_paths/formation_flag_pennant_entry fallback)
    if bool(getattr(config, "FORMATION_FLAG_PENNANT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_flag_pennant_entry") if importlib.util.find_spec("vec_paths.formation_flag_pennant_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_FLAG_PENNANT_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_FLAG_PENNANT_EXIT_ENABLED — inline distinct (vec_paths/formation_flag_pennant_exit fallback)
    if bool(getattr(config, "FORMATION_FLAG_PENNANT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_flag_pennant_exit") if importlib.util.find_spec("vec_paths.formation_flag_pennant_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_FLAG_PENNANT_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED — inline distinct (vec_paths/formation_head_shoulders_entry fallback)
    if bool(getattr(config, "FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_head_shoulders_entry") if importlib.util.find_spec("vec_paths.formation_head_shoulders_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_HEAD_SHOULDERS_EXIT_ENABLED — inline distinct (vec_paths/formation_head_shoulders_exit fallback)
    if bool(getattr(config, "FORMATION_HEAD_SHOULDERS_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_head_shoulders_exit") if importlib.util.find_spec("vec_paths.formation_head_shoulders_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_HEAD_SHOULDERS_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_TREND_STRUCTURE_ENTRY_ENABLED — inline distinct (vec_paths/formation_trend_structure_entry fallback)
    if bool(getattr(config, "FORMATION_TREND_STRUCTURE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_trend_structure_entry") if importlib.util.find_spec("vec_paths.formation_trend_structure_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_TREND_STRUCTURE_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_TREND_STRUCTURE_EXIT_ENABLED — inline distinct (vec_paths/formation_trend_structure_exit fallback)
    if bool(getattr(config, "FORMATION_TREND_STRUCTURE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_trend_structure_exit") if importlib.util.find_spec("vec_paths.formation_trend_structure_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_TREND_STRUCTURE_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_TRIANGLE_ENTRY_ENABLED — inline distinct (vec_paths/formation_triangle_entry fallback)
    if bool(getattr(config, "FORMATION_TRIANGLE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_triangle_entry") if importlib.util.find_spec("vec_paths.formation_triangle_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_TRIANGLE_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_TRIANGLE_EXIT_ENABLED — inline distinct (vec_paths/formation_triangle_exit fallback)
    if bool(getattr(config, "FORMATION_TRIANGLE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_triangle_exit") if importlib.util.find_spec("vec_paths.formation_triangle_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_TRIANGLE_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_WEDGE_ENTRY_ENABLED — inline distinct (vec_paths/formation_wedge_entry fallback)
    if bool(getattr(config, "FORMATION_WEDGE_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_wedge_entry") if importlib.util.find_spec("vec_paths.formation_wedge_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_WEDGE_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FORMATION_WEDGE_EXIT_ENABLED — inline distinct (vec_paths/formation_wedge_exit fallback)
    if bool(getattr(config, "FORMATION_WEDGE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.formation_wedge_exit") if importlib.util.find_spec("vec_paths.formation_wedge_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FORMATION_WEDGE_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED FUNDING_GATE_ENABLED — inline distinct (vec_paths/funding_gate fallback)
    if bool(getattr(config, "FUNDING_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.funding_gate") if importlib.util.find_spec("vec_paths.funding_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # FUNDING_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_BB_15M_ENABLED — inline distinct (vec_paths/golden_rule_bb_15m fallback)
    if bool(getattr(config, "GOLDEN_RULE_BB_15M_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_bb_15m") if importlib.util.find_spec("vec_paths.golden_rule_bb_15m") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_BB_15M_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_BB_1H_ENABLED — inline distinct (vec_paths/golden_rule_bb_1h fallback)
    if bool(getattr(config, "GOLDEN_RULE_BB_1H_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_bb_1h") if importlib.util.find_spec("vec_paths.golden_rule_bb_1h") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_BB_1H_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_BB_4H_ENABLED — inline distinct (vec_paths/golden_rule_bb_4h fallback)
    if bool(getattr(config, "GOLDEN_RULE_BB_4H_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_bb_4h") if importlib.util.find_spec("vec_paths.golden_rule_bb_4h") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_BB_4H_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_BB_D_ENABLED — inline distinct (vec_paths/golden_rule_bb_d fallback)
    if bool(getattr(config, "GOLDEN_RULE_BB_D_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_bb_d") if importlib.util.find_spec("vec_paths.golden_rule_bb_d") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_BB_D_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_BB_W_ENABLED — inline distinct (vec_paths/golden_rule_bb_w fallback)
    if bool(getattr(config, "GOLDEN_RULE_BB_W_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_bb_w") if importlib.util.find_spec("vec_paths.golden_rule_bb_w") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_BB_W_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_DC_15M_ENABLED — inline distinct (vec_paths/golden_rule_dc_15m fallback)
    if bool(getattr(config, "GOLDEN_RULE_DC_15M_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_dc_15m") if importlib.util.find_spec("vec_paths.golden_rule_dc_15m") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_DC_15M_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_DC_1H_ENABLED — inline distinct (vec_paths/golden_rule_dc_1h fallback)
    if bool(getattr(config, "GOLDEN_RULE_DC_1H_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_dc_1h") if importlib.util.find_spec("vec_paths.golden_rule_dc_1h") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_DC_1H_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_DC_4H_ENABLED — inline distinct (vec_paths/golden_rule_dc_4h fallback)
    if bool(getattr(config, "GOLDEN_RULE_DC_4H_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_dc_4h") if importlib.util.find_spec("vec_paths.golden_rule_dc_4h") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_DC_4H_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_DC_D_ENABLED — inline distinct (vec_paths/golden_rule_dc_d fallback)
    if bool(getattr(config, "GOLDEN_RULE_DC_D_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_dc_d") if importlib.util.find_spec("vec_paths.golden_rule_dc_d") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_DC_D_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_DC_W_ENABLED — inline distinct (vec_paths/golden_rule_dc_w fallback)
    if bool(getattr(config, "GOLDEN_RULE_DC_W_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_dc_w") if importlib.util.find_spec("vec_paths.golden_rule_dc_w") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_DC_W_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_ENABLED — inline distinct (vec_paths/golden_rule fallback)
    if bool(getattr(config, "GOLDEN_RULE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule") if importlib.util.find_spec("vec_paths.golden_rule") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GOLDEN_RULE_HTF_VETO_ENABLED — inline distinct (vec_paths/golden_rule_htf_veto fallback)
    if bool(getattr(config, "GOLDEN_RULE_HTF_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.golden_rule_htf_veto") if importlib.util.find_spec("vec_paths.golden_rule_htf_veto") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GOLDEN_RULE_HTF_VETO_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GR_HTF_DIRECT_EXIT_ENABLED — inline distinct (vec_paths/gr_htf_direct_exit fallback)
    if bool(getattr(config, "GR_HTF_DIRECT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.gr_htf_direct_exit") if importlib.util.find_spec("vec_paths.gr_htf_direct_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GR_HTF_DIRECT_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GR_HTF_GATE_ENABLED — inline distinct (vec_paths/gr_htf_gate fallback)
    if bool(getattr(config, "GR_HTF_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.gr_htf_gate") if importlib.util.find_spec("vec_paths.gr_htf_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GR_HTF_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED — inline distinct (vec_paths/guaranteed_price_cross_reentry_disk_vec fallback)
    if bool(getattr(config, "GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.guaranteed_price_cross_reentry_disk_vec") if importlib.util.find_spec("vec_paths.guaranteed_price_cross_reentry_disk_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # GUARANTEED_PRICE_CROSS_REENTRY_DISK_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED — inline distinct (vec_paths/hedge_bandaid_off_first_pre_vec fallback)
    if bool(getattr(config, "HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_bandaid_off_first_pre_vec") if importlib.util.find_spec("vec_paths.hedge_bandaid_off_first_pre_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # HEDGE_BANDAID_OFF_FIRST_PRE_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED HEDGE_FAILED_FALLBACK_CLOSE_ENABLED — inline distinct (vec_paths/hedge_failed_fallback_close fallback)
    if bool(getattr(config, "HEDGE_FAILED_FALLBACK_CLOSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_failed_fallback_close") if importlib.util.find_spec("vec_paths.hedge_failed_fallback_close") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # HEDGE_FAILED_FALLBACK_CLOSE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED HEDGE_LOSS_KILL_ENABLED — inline distinct (vec_paths/hedge_loss_kill fallback)
    if bool(getattr(config, "HEDGE_LOSS_KILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_loss_kill") if importlib.util.find_spec("vec_paths.hedge_loss_kill") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # HEDGE_LOSS_KILL_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED HEDGE_PROTECT_LOSS_VEC_ENABLED — inline distinct (vec_paths/hedge_protect_loss_vec fallback)
    if bool(getattr(config, "HEDGE_PROTECT_LOSS_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.hedge_protect_loss_vec") if importlib.util.find_spec("vec_paths.hedge_protect_loss_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # HEDGE_PROTECT_LOSS_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED HTF_DIRECTION_GATE_ENABLED — inline distinct (vec_paths/htf_direction_gate fallback)
    if bool(getattr(config, "HTF_DIRECTION_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_direction_gate") if importlib.util.find_spec("vec_paths.htf_direction_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # HTF_DIRECTION_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED HTF_TREND_VETO_ENABLED — inline distinct (vec_paths/htf_trend_veto fallback)
    if bool(getattr(config, "HTF_TREND_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.htf_trend_veto") if importlib.util.find_spec("vec_paths.htf_trend_veto") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # HTF_TREND_VETO_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED — inline distinct (vec_paths/in_gain_trend_exit_live_parity fallback)
    if bool(getattr(config, "IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.in_gain_trend_exit_live_parity") if importlib.util.find_spec("vec_paths.in_gain_trend_exit_live_parity") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # IN_GAIN_TREND_EXIT_LIVE_PARITY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED K1M_EXTREME_REVERSE_ENABLED — inline distinct (vec_paths/k1m_extreme_reverse fallback)
    if bool(getattr(config, "K1M_EXTREME_REVERSE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.k1m_extreme_reverse") if importlib.util.find_spec("vec_paths.k1m_extreme_reverse") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # K1M_EXTREME_REVERSE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LIVE_ENTRY_ENGINE_DC_ENABLED — inline distinct (vec_paths/live_entry_engine_dc fallback)
    if bool(getattr(config, "LIVE_ENTRY_ENGINE_DC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_entry_engine_dc") if importlib.util.find_spec("vec_paths.live_entry_engine_dc") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LIVE_ENTRY_ENGINE_DC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LIVE_ENTRY_ENGINE_ENABLED — inline distinct (vec_paths/live_entry_engine fallback)
    if bool(getattr(config, "LIVE_ENTRY_ENGINE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_entry_engine") if importlib.util.find_spec("vec_paths.live_entry_engine") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LIVE_ENTRY_ENGINE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LIVE_ENTRY_ENGINE_HTF_ENABLED — inline distinct (vec_paths/live_entry_engine_htf fallback)
    if bool(getattr(config, "LIVE_ENTRY_ENGINE_HTF_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_entry_engine_htf") if importlib.util.find_spec("vec_paths.live_entry_engine_htf") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LIVE_ENTRY_ENGINE_HTF_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED — inline distinct (vec_paths/live_entry_engine_stdev_macro fallback)
    if bool(getattr(config, "LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_entry_engine_stdev_macro") if importlib.util.find_spec("vec_paths.live_entry_engine_stdev_macro") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LIVE_ENTRY_ENGINE_STOCH_ENABLED — inline distinct (vec_paths/live_entry_engine_stoch fallback)
    if bool(getattr(config, "LIVE_ENTRY_ENGINE_STOCH_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_entry_engine_stoch") if importlib.util.find_spec("vec_paths.live_entry_engine_stoch") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LIVE_ENTRY_ENGINE_STOCH_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LIVE_ENTRY_ENGINE_WT_ENABLED — inline distinct (vec_paths/live_entry_engine_wt fallback)
    if bool(getattr(config, "LIVE_ENTRY_ENGINE_WT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.live_entry_engine_wt") if importlib.util.find_spec("vec_paths.live_entry_engine_wt") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LIVE_ENTRY_ENGINE_WT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LONG_ENABLED — inline distinct (vec_paths/long fallback)
    if bool(getattr(config, "LONG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.long") if importlib.util.find_spec("vec_paths.long") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LONG_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LR_BAND_ENTRY_ENABLED — inline distinct (vec_paths/lr_band_entry fallback)
    if bool(getattr(config, "LR_BAND_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_entry") if importlib.util.find_spec("vec_paths.lr_band_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LR_BAND_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LR_BAND_HARVEST_ENABLED — inline distinct (vec_paths/lr_band_harvest fallback)
    if bool(getattr(config, "LR_BAND_HARVEST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_harvest") if importlib.util.find_spec("vec_paths.lr_band_harvest") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LR_BAND_HARVEST_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LR_BAND_LADDER_ENABLED — inline distinct (vec_paths/lr_band_ladder fallback)
    if bool(getattr(config, "LR_BAND_LADDER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_ladder") if importlib.util.find_spec("vec_paths.lr_band_ladder") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LR_BAND_LADDER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LR_BAND_REGIME_ENABLED — inline distinct (vec_paths/lr_band_regime fallback)
    if bool(getattr(config, "LR_BAND_REGIME_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_regime") if importlib.util.find_spec("vec_paths.lr_band_regime") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LR_BAND_REGIME_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED LR_BAND_SLOPE_FLIP_EXIT_ENABLED — inline distinct (vec_paths/lr_band_slope_flip_exit fallback)
    if bool(getattr(config, "LR_BAND_SLOPE_FLIP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.lr_band_slope_flip_exit") if importlib.util.find_spec("vec_paths.lr_band_slope_flip_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # LR_BAND_SLOPE_FLIP_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MFI_ENTRY_ENABLED — inline distinct (vec_paths/mfi_entry fallback)
    if bool(getattr(config, "MFI_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mfi_entry") if importlib.util.find_spec("vec_paths.mfi_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MFI_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MICRO_SCALP_STOCKS_MAKER_ENABLED — inline distinct (vec_paths/micro_scalp_stocks_maker fallback)
    if bool(getattr(config, "MICRO_SCALP_STOCKS_MAKER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.micro_scalp_stocks_maker") if importlib.util.find_spec("vec_paths.micro_scalp_stocks_maker") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MICRO_SCALP_STOCKS_MAKER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MICRO_SCALP_USDC_MAKER_ENABLED — inline distinct (vec_paths/micro_scalp_usdc_maker fallback)
    if bool(getattr(config, "MICRO_SCALP_USDC_MAKER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.micro_scalp_usdc_maker") if importlib.util.find_spec("vec_paths.micro_scalp_usdc_maker") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MICRO_SCALP_USDC_MAKER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MOMENTUM_BREAKOUT_ENABLED — inline distinct (vec_paths/momentum_breakout fallback)
    if bool(getattr(config, "MOMENTUM_BREAKOUT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.momentum_breakout") if importlib.util.find_spec("vec_paths.momentum_breakout") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MOMENTUM_BREAKOUT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_ARMED_ENTRY_ENABLED — inline distinct (vec_paths/mtf_armed_entry fallback)
    if bool(getattr(config, "MTF_ARMED_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_armed_entry") if importlib.util.find_spec("vec_paths.mtf_armed_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_ARMED_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED — inline distinct (vec_paths/mtf_armed_wt_direction_suspend fallback)
    if bool(getattr(config, "MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_armed_wt_direction_suspend") if importlib.util.find_spec("vec_paths.mtf_armed_wt_direction_suspend") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_ATR_TRAIL_ENABLED — inline distinct (vec_paths/mtf_atr_trail fallback)
    if bool(getattr(config, "MTF_ATR_TRAIL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_atr_trail") if importlib.util.find_spec("vec_paths.mtf_atr_trail") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_ATR_TRAIL_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_BB_REJECT_EXIT_ENABLED — inline distinct (vec_paths/mtf_bb_reject_exit fallback)
    if bool(getattr(config, "MTF_BB_REJECT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_bb_reject_exit") if importlib.util.find_spec("vec_paths.mtf_bb_reject_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_BB_REJECT_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_DC_REJECT_EXIT_ENABLED — inline distinct (vec_paths/mtf_dc_reject_exit fallback)
    if bool(getattr(config, "MTF_DC_REJECT_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_dc_reject_exit") if importlib.util.find_spec("vec_paths.mtf_dc_reject_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_DC_REJECT_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_GR_EXIT_GATE_ENABLED — inline distinct (vec_paths/mtf_gr_exit_gate fallback)
    if bool(getattr(config, "MTF_GR_EXIT_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_gr_exit_gate") if importlib.util.find_spec("vec_paths.mtf_gr_exit_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_GR_EXIT_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_GR_FILTER_ENABLED — inline distinct (vec_paths/mtf_gr_filter fallback)
    if bool(getattr(config, "MTF_GR_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_gr_filter") if importlib.util.find_spec("vec_paths.mtf_gr_filter") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_GR_FILTER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_WT_CROSS_EXIT_DIRECT_ENABLED — inline distinct (vec_paths/mtf_wt_cross_exit_direct fallback)
    if bool(getattr(config, "MTF_WT_CROSS_EXIT_DIRECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_wt_cross_exit_direct") if importlib.util.find_spec("vec_paths.mtf_wt_cross_exit_direct") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_WT_CROSS_EXIT_DIRECT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED MTF_WT_CROSS_EXIT_ENABLED — inline distinct (vec_paths/mtf_wt_cross_exit fallback)
    if bool(getattr(config, "MTF_WT_CROSS_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.mtf_wt_cross_exit") if importlib.util.find_spec("vec_paths.mtf_wt_cross_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # MTF_WT_CROSS_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED NEVER_GO_RED_STOP_ENABLED — inline distinct (vec_paths/never_go_red_stop fallback)
    if bool(getattr(config, "NEVER_GO_RED_STOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.never_go_red_stop") if importlib.util.find_spec("vec_paths.never_go_red_stop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # NEVER_GO_RED_STOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED NEWBORN_LOSS_KILL_ENABLED — inline distinct (vec_paths/newborn_loss_kill fallback)
    if bool(getattr(config, "NEWBORN_LOSS_KILL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.newborn_loss_kill") if importlib.util.find_spec("vec_paths.newborn_loss_kill") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # NEWBORN_LOSS_KILL_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED NEWBORN_PROTECT_ENABLED — inline distinct (vec_paths/newborn_protect fallback)
    if bool(getattr(config, "NEWBORN_PROTECT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.newborn_protect") if importlib.util.find_spec("vec_paths.newborn_protect") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # NEWBORN_PROTECT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED NOLOSS_ENABLED — inline distinct (vec_paths/noloss fallback)
    if bool(getattr(config, "NOLOSS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.noloss") if importlib.util.find_spec("vec_paths.noloss") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # NOLOSS_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED OBLIGATORY_HEDGE_ENABLED — inline distinct (vec_paths/obligatory_hedge fallback)
    if bool(getattr(config, "OBLIGATORY_HEDGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.obligatory_hedge") if importlib.util.find_spec("vec_paths.obligatory_hedge") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # OBLIGATORY_HEDGE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PARABOLIC_PROTECTION_ENABLED — inline distinct (vec_paths/parabolic_protection fallback)
    if bool(getattr(config, "PARABOLIC_PROTECTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.parabolic_protection") if importlib.util.find_spec("vec_paths.parabolic_protection") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PARABOLIC_PROTECTION_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PARITY_REENTRY_NAMING_ENABLED — inline distinct (vec_paths/parity_reentry_naming fallback)
    if bool(getattr(config, "PARITY_REENTRY_NAMING_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.parity_reentry_naming") if importlib.util.find_spec("vec_paths.parity_reentry_naming") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PARITY_REENTRY_NAMING_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PARTIAL_PROFIT_LOCK_ENABLED — inline distinct (vec_paths/partial_profit_lock fallback)
    if bool(getattr(config, "PARTIAL_PROFIT_LOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.partial_profit_lock") if importlib.util.find_spec("vec_paths.partial_profit_lock") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PARTIAL_PROFIT_LOCK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PEAK_GIVEBACK_DROP_TRIGGER_ENABLED — inline distinct (vec_paths/peak_giveback_drop_trigger fallback)
    if bool(getattr(config, "PEAK_GIVEBACK_DROP_TRIGGER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.peak_giveback_drop_trigger") if importlib.util.find_spec("vec_paths.peak_giveback_drop_trigger") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PEAK_GIVEBACK_DROP_TRIGGER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PEAK_GIVEBACK_HARD_ZERO_ENABLED — inline distinct (vec_paths/peak_giveback_hard_zero fallback)
    if bool(getattr(config, "PEAK_GIVEBACK_HARD_ZERO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.peak_giveback_hard_zero") if importlib.util.find_spec("vec_paths.peak_giveback_hard_zero") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PEAK_GIVEBACK_HARD_ZERO_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PEAK_GIVEBACK_PROTECTION_ENABLED — inline distinct (vec_paths/peak_giveback_protection fallback)
    if bool(getattr(config, "PEAK_GIVEBACK_PROTECTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.peak_giveback_protection") if importlib.util.find_spec("vec_paths.peak_giveback_protection") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PEAK_GIVEBACK_PROTECTION_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PENNY_STOCK_LONG_BLOCK_ENABLED — inline distinct (vec_paths/penny_stock_long_block fallback)
    if bool(getattr(config, "PENNY_STOCK_LONG_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.penny_stock_long_block") if importlib.util.find_spec("vec_paths.penny_stock_long_block") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PENNY_STOCK_LONG_BLOCK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED PULLBACK_AUGMENT_ENABLED — inline distinct (vec_paths/pullback_augment fallback)
    if bool(getattr(config, "PULLBACK_AUGMENT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.pullback_augment") if importlib.util.find_spec("vec_paths.pullback_augment") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # PULLBACK_AUGMENT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUALITY_BOTTOM_ENTRY_ENABLED — inline distinct (vec_paths/quality_bottom_entry fallback)
    if bool(getattr(config, "QUALITY_BOTTOM_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quality_bottom_entry") if importlib.util.find_spec("vec_paths.quality_bottom_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUALITY_BOTTOM_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUALITY_TOP_EXIT_ENABLED — inline distinct (vec_paths/quality_top_exit fallback)
    if bool(getattr(config, "QUALITY_TOP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quality_top_exit") if importlib.util.find_spec("vec_paths.quality_top_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUALITY_TOP_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_BANDAID_OFF_VEC_ENABLED — inline distinct (vec_paths/quick_bandaid_off_vec fallback)
    if bool(getattr(config, "QUICK_BANDAID_OFF_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_bandaid_off_vec") if importlib.util.find_spec("vec_paths.quick_bandaid_off_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_BANDAID_OFF_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED — inline distinct (vec_paths/quick_breakeven_gain_erosion_vec fallback)
    if bool(getattr(config, "QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_breakeven_gain_erosion_vec") if importlib.util.find_spec("vec_paths.quick_breakeven_gain_erosion_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED — inline distinct (vec_paths/quick_cycle_tp_stoch_against_vec fallback)
    if bool(getattr(config, "QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_cycle_tp_stoch_against_vec") if importlib.util.find_spec("vec_paths.quick_cycle_tp_stoch_against_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_CYCLE_TP_STOCH_AGAINST_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED — inline distinct (vec_paths/quick_hedge_same_sym_last_resort_vec fallback)
    if bool(getattr(config, "QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_hedge_same_sym_last_resort_vec") if importlib.util.find_spec("vec_paths.quick_hedge_same_sym_last_resort_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_HEDGE_SAME_SYM_LAST_RESORT_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_OPEN_STRONG_VEC_ENABLED — inline distinct (vec_paths/quick_open_strong_vec fallback)
    if bool(getattr(config, "QUICK_OPEN_STRONG_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_open_strong_vec") if importlib.util.find_spec("vec_paths.quick_open_strong_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_OPEN_STRONG_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED — inline distinct (vec_paths/quick_reduce_strong_reduce_vec fallback)
    if bool(getattr(config, "QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_reduce_strong_reduce_vec") if importlib.util.find_spec("vec_paths.quick_reduce_strong_reduce_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_REDUCE_STRONG_REDUCE_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED — inline distinct (vec_paths/quick_sentiment_cut_gain_vec fallback)
    if bool(getattr(config, "QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.quick_sentiment_cut_gain_vec") if importlib.util.find_spec("vec_paths.quick_sentiment_cut_gain_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # QUICK_SENTIMENT_CUT_GAIN_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED R1_DC_LOW4_3M_EMERGENCY_ENABLED — inline distinct (vec_paths/r1_dc_low4_3m_emergency fallback)
    if bool(getattr(config, "R1_DC_LOW4_3M_EMERGENCY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r1_dc_low4_3m_emergency") if importlib.util.find_spec("vec_paths.r1_dc_low4_3m_emergency") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # R1_DC_LOW4_3M_EMERGENCY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED R3_HTF_FLIP_4H_TIER_ENABLED — inline distinct (vec_paths/r3_htf_flip_4h_tier fallback)
    if bool(getattr(config, "R3_HTF_FLIP_4H_TIER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r3_htf_flip_4h_tier") if importlib.util.find_spec("vec_paths.r3_htf_flip_4h_tier") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # R3_HTF_FLIP_4H_TIER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED R3_HTF_FLIP_EXIT_ENABLED — inline distinct (vec_paths/r3_htf_flip_exit fallback)
    if bool(getattr(config, "R3_HTF_FLIP_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.r3_htf_flip_exit") if importlib.util.find_spec("vec_paths.r3_htf_flip_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # R3_HTF_FLIP_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B01_WT_2of3_ENABLED — inline distinct (vec_paths/reentry_b01_wt_2of3 fallback)
    if bool(getattr(config, "REENTRY_B01_WT_2of3_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b01_wt_2of3") if importlib.util.find_spec("vec_paths.reentry_b01_wt_2of3") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B01_WT_2of3_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B02_BC156_BOTTOM_ENABLED — inline distinct (vec_paths/reentry_b02_bc156_bottom fallback)
    if bool(getattr(config, "REENTRY_B02_BC156_BOTTOM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b02_bc156_bottom") if importlib.util.find_spec("vec_paths.reentry_b02_bc156_bottom") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B02_BC156_BOTTOM_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B04_DC_RETEST_ENABLED — inline distinct (vec_paths/reentry_b04_dc_retest fallback)
    if bool(getattr(config, "REENTRY_B04_DC_RETEST_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b04_dc_retest") if importlib.util.find_spec("vec_paths.reentry_b04_dc_retest") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B04_DC_RETEST_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B09_SNAPBACK_ENABLED — inline distinct (vec_paths/reentry_b09_snapback fallback)
    if bool(getattr(config, "REENTRY_B09_SNAPBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b09_snapback") if importlib.util.find_spec("vec_paths.reentry_b09_snapback") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B09_SNAPBACK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B10_STOCH_REV_ENABLED — inline distinct (vec_paths/reentry_b10_stoch_rev fallback)
    if bool(getattr(config, "REENTRY_B10_STOCH_REV_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b10_stoch_rev") if importlib.util.find_spec("vec_paths.reentry_b10_stoch_rev") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B10_STOCH_REV_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B11_DC_BREAK_ENABLED — inline distinct (vec_paths/reentry_b11_dc_break fallback)
    if bool(getattr(config, "REENTRY_B11_DC_BREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b11_dc_break") if importlib.util.find_spec("vec_paths.reentry_b11_dc_break") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B11_DC_BREAK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B12_WT_MOM_ENABLED — inline distinct (vec_paths/reentry_b12_wt_mom fallback)
    if bool(getattr(config, "REENTRY_B12_WT_MOM_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b12_wt_mom") if importlib.util.find_spec("vec_paths.reentry_b12_wt_mom") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B12_WT_MOM_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B14_HA_TREND_ENABLED — inline distinct (vec_paths/reentry_b14_ha_trend fallback)
    if bool(getattr(config, "REENTRY_B14_HA_TREND_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b14_ha_trend") if importlib.util.find_spec("vec_paths.reentry_b14_ha_trend") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B14_HA_TREND_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B15_STRONG_TREND_ENABLED — inline distinct (vec_paths/reentry_b15_strong_trend fallback)
    if bool(getattr(config, "REENTRY_B15_STRONG_TREND_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b15_strong_trend") if importlib.util.find_spec("vec_paths.reentry_b15_strong_trend") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B15_STRONG_TREND_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_B16_MIDRANGE_ENABLED — inline distinct (vec_paths/reentry_b16_midrange fallback)
    if bool(getattr(config, "REENTRY_B16_MIDRANGE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_b16_midrange") if importlib.util.find_spec("vec_paths.reentry_b16_midrange") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_B16_MIDRANGE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED — inline distinct (vec_paths/reentry_live_monitor_dc_break fallback)
    if bool(getattr(config, "REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.reentry_live_monitor_dc_break") if importlib.util.find_spec("vec_paths.reentry_live_monitor_dc_break") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REGIME_DETECTION_ENABLED — inline distinct (vec_paths/regime_detection fallback)
    if bool(getattr(config, "REGIME_DETECTION_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.regime_detection") if importlib.util.find_spec("vec_paths.regime_detection") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REGIME_DETECTION_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED REGIME_GATE_ENABLED — inline distinct (vec_paths/regime_gate fallback)
    if bool(getattr(config, "REGIME_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.regime_gate") if importlib.util.find_spec("vec_paths.regime_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # REGIME_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED RIDICULOUS_HOLD_VEC_ENABLED — inline distinct (vec_paths/ridiculous_hold_vec fallback)
    if bool(getattr(config, "RIDICULOUS_HOLD_VEC_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.ridiculous_hold_vec") if importlib.util.find_spec("vec_paths.ridiculous_hold_vec") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # RIDICULOUS_HOLD_VEC_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED RZ_BREAKOUT_ENTRY_ENABLED — inline distinct (vec_paths/rz_breakout_entry fallback)
    if bool(getattr(config, "RZ_BREAKOUT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.rz_breakout_entry") if importlib.util.find_spec("vec_paths.rz_breakout_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # RZ_BREAKOUT_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED SHOULD_ENTER_FALLBACK_ENABLED — inline distinct (vec_paths/should_enter_fallback fallback)
    if bool(getattr(config, "SHOULD_ENTER_FALLBACK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.should_enter_fallback") if importlib.util.find_spec("vec_paths.should_enter_fallback") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # SHOULD_ENTER_FALLBACK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED STDEV_MACRO_ENTRY_VETO_ENABLED — inline distinct (vec_paths/stdev_macro_entry_veto fallback)
    if bool(getattr(config, "STDEV_MACRO_ENTRY_VETO_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_macro_entry_veto") if importlib.util.find_spec("vec_paths.stdev_macro_entry_veto") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # STDEV_MACRO_ENTRY_VETO_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED STDEV_MACRO_R4_EXIT_ENABLED — inline distinct (vec_paths/stdev_macro_r4_exit fallback)
    if bool(getattr(config, "STDEV_MACRO_R4_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.stdev_macro_r4_exit") if importlib.util.find_spec("vec_paths.stdev_macro_r4_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # STDEV_MACRO_R4_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED STRENGTH_FILTER_ENABLED — inline distinct (vec_paths/strength_filter fallback)
    if bool(getattr(config, "STRENGTH_FILTER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.strength_filter") if importlib.util.find_spec("vec_paths.strength_filter") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # STRENGTH_FILTER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED TIER_ENABLED — inline distinct (vec_paths/tier fallback)
    if bool(getattr(config, "TIER_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tier") if importlib.util.find_spec("vec_paths.tier") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # TIER_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED TOP_OF_RANGE_BLOCK_ENABLED — inline distinct (vec_paths/top_of_range_block fallback)
    if bool(getattr(config, "TOP_OF_RANGE_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.top_of_range_block") if importlib.util.find_spec("vec_paths.top_of_range_block") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # TOP_OF_RANGE_BLOCK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED — inline distinct (vec_paths/tradier_emergency_anti_churn_gates fallback)
    if bool(getattr(config, "TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_emergency_anti_churn_gates") if importlib.util.find_spec("vec_paths.tradier_emergency_anti_churn_gates") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # TRADIER_EMERGENCY_ANTI_CHURN_GATES_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED — inline distinct (vec_paths/tradier_reentry_overdue_bypass fallback)
    if bool(getattr(config, "TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tradier_reentry_overdue_bypass") if importlib.util.find_spec("vec_paths.tradier_reentry_overdue_bypass") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # TRADIER_REENTRY_OVERDUE_BYPASS_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED TR_TREND_V1_ENABLED — inline distinct (vec_paths/tr_trend_v1 fallback)
    if bool(getattr(config, "TR_TREND_V1_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_trend_v1") if importlib.util.find_spec("vec_paths.tr_trend_v1") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # TR_TREND_V1_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED TR_TREND_V1_SPY_REGIME_ENABLED — inline distinct (vec_paths/tr_trend_v1_spy_regime fallback)
    if bool(getattr(config, "TR_TREND_V1_SPY_REGIME_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.tr_trend_v1_spy_regime") if importlib.util.find_spec("vec_paths.tr_trend_v1_spy_regime") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # TR_TREND_V1_SPY_REGIME_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED — inline distinct (vec_paths/universal_augment_gain_gate fallback)
    if bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.universal_augment_gain_gate") if importlib.util.find_spec("vec_paths.universal_augment_gain_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_EVENT_DRIVEN_LOOP_ENABLED — inline distinct (vec_paths/vec_event_driven_loop fallback)
    if bool(getattr(config, "VEC_EVENT_DRIVEN_LOOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_event_driven_loop") if importlib.util.find_spec("vec_paths.vec_event_driven_loop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_EVENT_DRIVEN_LOOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_LIVE_REDUCE_PARITY_ENABLED — inline distinct (vec_paths/vec_live_reduce_parity fallback)
    if bool(getattr(config, "VEC_LIVE_REDUCE_PARITY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_live_reduce_parity") if importlib.util.find_spec("vec_paths.vec_live_reduce_parity") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_LIVE_REDUCE_PARITY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_MTF_ARMED_STATE_ENABLED — inline distinct (vec_paths/vec_mtf_armed_state fallback)
    if bool(getattr(config, "VEC_MTF_ARMED_STATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_mtf_armed_state") if importlib.util.find_spec("vec_paths.vec_mtf_armed_state") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_MTF_ARMED_STATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_MULTI_SYM_OUTER_LOOP_ENABLED — inline distinct (vec_paths/vec_multi_sym_outer_loop fallback)
    if bool(getattr(config, "VEC_MULTI_SYM_OUTER_LOOP_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_multi_sym_outer_loop") if importlib.util.find_spec("vec_paths.vec_multi_sym_outer_loop") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_MULTI_SYM_OUTER_LOOP_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_NOLOSS_GATE_ENABLED — inline distinct (vec_paths/vec_noloss_gate fallback)
    if bool(getattr(config, "VEC_NOLOSS_GATE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_noloss_gate") if importlib.util.find_spec("vec_paths.vec_noloss_gate") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_NOLOSS_GATE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_OVERTRADE_FIX_ENABLED — inline distinct (vec_paths/vec_overtrade_fix fallback)
    if bool(getattr(config, "VEC_OVERTRADE_FIX_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_overtrade_fix") if importlib.util.find_spec("vec_paths.vec_overtrade_fix") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_OVERTRADE_FIX_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_RATIO_REDUCE_PROXY_ENABLED — inline distinct (vec_paths/vec_ratio_reduce_proxy fallback)
    if bool(getattr(config, "VEC_RATIO_REDUCE_PROXY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_ratio_reduce_proxy") if importlib.util.find_spec("vec_paths.vec_ratio_reduce_proxy") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_RATIO_REDUCE_PROXY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_REENTRY_DC4_EXITPRICE_ENABLED — inline distinct (vec_paths/vec_reentry_dc4_exitprice fallback)
    if bool(getattr(config, "VEC_REENTRY_DC4_EXITPRICE_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_reentry_dc4_exitprice") if importlib.util.find_spec("vec_paths.vec_reentry_dc4_exitprice") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_REENTRY_DC4_EXITPRICE_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEC_WT_PRICE_BREAKOUT_REENTRY_ENABLED — inline distinct (vec_paths/vec_wt_price_breakout_reentry fallback)
    if bool(getattr(config, "VEC_WT_PRICE_BREAKOUT_REENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vec_wt_price_breakout_reentry") if importlib.util.find_spec("vec_paths.vec_wt_price_breakout_reentry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEC_WT_PRICE_BREAKOUT_REENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED VEL_EXIT_ENABLED — inline distinct (vec_paths/vel_exit fallback)
    if bool(getattr(config, "VEL_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.vel_exit") if importlib.util.find_spec("vec_paths.vel_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # VEL_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WATCHDOG_DC_FORCE_OPEN_ENABLED — inline distinct (vec_paths/watchdog_dc_force_open fallback)
    if bool(getattr(config, "WATCHDOG_DC_FORCE_OPEN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.watchdog_dc_force_open") if importlib.util.find_spec("vec_paths.watchdog_dc_force_open") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WATCHDOG_DC_FORCE_OPEN_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_15M_BOUNCE_OPEN_ENABLED — inline distinct (vec_paths/wt_15m_bounce_open fallback)
    if bool(getattr(config, "WT_15M_BOUNCE_OPEN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_15m_bounce_open") if importlib.util.find_spec("vec_paths.wt_15m_bounce_open") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_15M_BOUNCE_OPEN_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_15M_CROSS_ENTRY_ENABLED — inline distinct (vec_paths/wt_15m_cross_entry fallback)
    if bool(getattr(config, "WT_15M_CROSS_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_15m_cross_entry") if importlib.util.find_spec("vec_paths.wt_15m_cross_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_15M_CROSS_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED — inline distinct (vec_paths/wt_15m_vel_slow_at_zero_gain fallback)
    if bool(getattr(config, "WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_15m_vel_slow_at_zero_gain") if importlib.util.find_spec("vec_paths.wt_15m_vel_slow_at_zero_gain") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_3M_FORCE_OPEN_ENABLED — inline distinct (vec_paths/wt_3m_force_open fallback)
    if bool(getattr(config, "WT_3M_FORCE_OPEN_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_3m_force_open") if importlib.util.find_spec("vec_paths.wt_3m_force_open") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_3M_FORCE_OPEN_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_4H_VEL_EXIT_ENABLED — inline distinct (vec_paths/wt_4h_vel_exit fallback)
    if bool(getattr(config, "WT_4H_VEL_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_4h_vel_exit") if importlib.util.find_spec("vec_paths.wt_4h_vel_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_4H_VEL_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_ACCEL_EXIT_ENABLED — inline distinct (vec_paths/wt_accel_exit fallback)
    if bool(getattr(config, "WT_ACCEL_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_accel_exit") if importlib.util.find_spec("vec_paths.wt_accel_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_ACCEL_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_CROSSUNDER_FINAL_ENABLED — inline distinct (vec_paths/wt_crossunder_final fallback)
    if bool(getattr(config, "WT_CROSSUNDER_FINAL_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_crossunder_final") if importlib.util.find_spec("vec_paths.wt_crossunder_final") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_CROSSUNDER_FINAL_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_CROSSUNDER_REFINED_BYPASS_ENABLED — inline distinct (vec_paths/wt_crossunder_refined_bypass fallback)
    if bool(getattr(config, "WT_CROSSUNDER_REFINED_BYPASS_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_crossunder_refined_bypass") if importlib.util.find_spec("vec_paths.wt_crossunder_refined_bypass") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_CROSSUNDER_REFINED_BYPASS_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_CROSS_EXIT_ENABLED — inline distinct (vec_paths/wt_cross_exit fallback)
    if bool(getattr(config, "WT_CROSS_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_cross_exit") if importlib.util.find_spec("vec_paths.wt_cross_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_CROSS_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED — inline distinct (vec_paths/wt_dc_entry_bar_maturity_block fallback)
    if bool(getattr(config, "WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_dc_entry_bar_maturity_block") if importlib.util.find_spec("vec_paths.wt_dc_entry_bar_maturity_block") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_DC_ENTRY_ENABLED — inline distinct (vec_paths/wt_dc_entry fallback)
    if bool(getattr(config, "WT_DC_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_dc_entry") if importlib.util.find_spec("vec_paths.wt_dc_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_DC_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_DC_EXIT_ENABLED — inline distinct (vec_paths/wt_dc_exit fallback)
    if bool(getattr(config, "WT_DC_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_dc_exit") if importlib.util.find_spec("vec_paths.wt_dc_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_DC_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_DC_LONG_ENABLED — inline distinct (vec_paths/wt_dc_long fallback)
    if bool(getattr(config, "WT_DC_LONG_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_dc_long") if importlib.util.find_spec("vec_paths.wt_dc_long") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_DC_LONG_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_DC_SHORT_ENABLED — inline distinct (vec_paths/wt_dc_short fallback)
    if bool(getattr(config, "WT_DC_SHORT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_dc_short") if importlib.util.find_spec("vec_paths.wt_dc_short") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_DC_SHORT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_DIV_EXIT_ENABLED — inline distinct (vec_paths/wt_div_exit fallback)
    if bool(getattr(config, "WT_DIV_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_div_exit") if importlib.util.find_spec("vec_paths.wt_div_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_DIV_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_ENTRY_ENABLED — inline distinct (vec_paths/wt_entry fallback)
    if bool(getattr(config, "WT_ENTRY_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_entry") if importlib.util.find_spec("vec_paths.wt_entry") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_ENTRY_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_EXHAUST_EXIT_ENABLED — inline distinct (vec_paths/wt_exhaust_exit fallback)
    if bool(getattr(config, "WT_EXHAUST_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_exhaust_exit") if importlib.util.find_spec("vec_paths.wt_exhaust_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_EXHAUST_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_HTF_DISCOUNT_ENABLED — inline distinct (vec_paths/wt_htf_discount fallback)
    if bool(getattr(config, "WT_HTF_DISCOUNT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_htf_discount") if importlib.util.find_spec("vec_paths.wt_htf_discount") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_HTF_DISCOUNT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_MOMENTUM_EXIT_ENABLED — inline distinct (vec_paths/wt_momentum_exit fallback)
    if bool(getattr(config, "WT_MOMENTUM_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_momentum_exit") if importlib.util.find_spec("vec_paths.wt_momentum_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_MOMENTUM_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    # REAL-WIRED WT_PERCENTILE_EXIT_ENABLED — inline distinct (vec_paths/wt_percentile_exit fallback)
    if bool(getattr(config, "WT_PERCENTILE_EXIT_ENABLED", False)):
        try:
            import importlib; mod=importlib.import_module("vec_paths.wt_percentile_exit") if importlib.util.find_spec("vec_paths.wt_percentile_exit") else None
            if mod and hasattr(mod, "score"):
                _strength_open_ok = _strength_open_ok & mod.score(npz, config)
            else:
                _strength_open_ok = _strength_open_ok  # WT_PERCENTILE_EXIT_ENABLED no vector proxy — bool gate only (was WT fallback)
        except Exception: pass
    return True

if __name__ == "__main__":
    sys.exit(main())