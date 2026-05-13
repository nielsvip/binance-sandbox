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
    gain_pct MUST be updated each bar: pos_adapter.gain_pct = gain."""
    __slots__ = ("_state", "gain_pct")
    def __init__(self, state: "SymState"):
        self._state = state
        self.gain_pct: float = 0.0
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


REPO_ROOT = Path(__file__).resolve().parent
NPZ_DIR = REPO_ROOT / "backtest_v8" / "indicators"
KLINES_CRYPTO_DIR = REPO_ROOT / "klines_cache_backtest"
KLINES_STOCK_DIR = REPO_ROOT / "klines_cache_backtest" / "tradier"
SWEEP_RESULTS_DIR = REPO_ROOT / "data" / "sweep_results"
HISTORY_DIR = REPO_ROOT / "data" / "history"


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
    WT_PERCENTILE_EXIT_ENABLED: bool = True
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
    OBLIGATORY_HEDGE_MIN_LOSS_PCT: float = -0.25
    OBLIGATORY_HEDGE_PCT: float = 1.0
    OBLIGATORY_HEDGE_WT_USE_1M: bool = False
    OBLIGATORY_HEDGE_WT_USE_3M: bool = True
    OBLIGATORY_HEDGE_WT_USE_15M: bool = False
    OBLIGATORY_HEDGE_WT_USE_1H: bool = True
    OBLIGATORY_HEDGE_WT_TFS_REQUIRED: int = 2
    HEDGE_MODE: bool = True
    HEDGE_MAX_PCT_OF_LOSER: float = 1.0
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_15M_OR_1H: bool = True
    HEDGE_TRIGGER_REQUIRE_WT_3M_AND_1H: bool = False
    HEDGE_TRIGGER_USE_WT_3M_ALONE: bool = True
    HEDGE_FAILED_FALLBACK_CLOSE_ENABLED: bool = True
    HEDGE_COMPLETED_LOCKOUT_SECONDS: float = 60.0
    # ── augment / dup guard ───────────────────────────────────────────────
    DUP_GUARD_USE_GAIN_GATE: bool = True
    DUP_GUARD_GAIN_MULTIPLIER: float = 0.5
    PULLBACK_AUGMENT_ENABLED: bool = True
    PULLBACK_AUGMENT_REVERSAL_MIN: float = 1.0
    HARD_AUGMENT_LOCK_SECONDS: float = 900.0
    HARD_REDUCE_LOCK_SECONDS: float = 60.0
    AUGMENTATION_COOLDOWN_SECONDS: float = 600.0
    WT_3M_FORCE_OPEN_BYPASS_GATES: bool = True
    EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED: bool = True
    PARTIAL_PROFIT_LOCK_FRAC: float = 0.5
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
    EXECUTE_NOW_MAX_MARK_AGE_S: float = 120.0
    # ── ratio sizing (kept as knob; backtest reads as multiplier) ─────────
    RATIO_MULTIPLIER: float = 3.0
    # ── DC stop loss sweep flags ──────────────────────────────────────────
    DC_LOW4_STOP_ENABLED: bool = False   # stop at dc_low4_<TF> recorded at entry
    DC_LOW_STOP_ENABLED: bool = False    # stop at dc_low_<TF> (1-bar)
    DC_STOP_TF: str = ""                 # override TF (empty = auto: "3m" crypto / "5m" tradier)
    # ── GR multiplier exit sweep flags ────────────────────────────────────────
    GR_EXIT_ENABLED: bool = False        # exit when GR against-score >= threshold AND wt1_3m against
    GR_EXIT_MULT_THRESHOLD: int = 9      # 9/12/15 tested via CLI override
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
    ppl_fired: bool = False
    intent_lock_stamp: float = 0.0
    last_open_attempt_ts: float = 0.0
    r1_stop_price: float = 0.0


def _gain_pct(entry: float, mark: float, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (mark - entry) / entry * 100.0
    return (entry - mark) / entry * 100.0


def simulate_one_symbol(
    symbol: str,
    side: str,            # 'LONG' or 'SHORT'
    mode: str,
    config: SweepConfig,
    *,
    start_ts: Optional[int] = None,
    max_bars: Optional[int] = None,
) -> Tuple[List[TradeEvent], List[float], int]:
    """Run the vec engine on one symbol+side. Returns (events, trade_returns, n_bars)."""
    is_long = side.upper() == "LONG"
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
    # 1. Reentry blocks
    reentry = evaluate_reentry_vec(npz, is_long, config, ltf="3m")
    # 2. Exit gates (indicator-only)
    exit_gates = evaluate_exit_gates_vec(npz, is_long, config, ltf="3m")
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
    # wt1_3m against the trade (required by GR exit)
    _wt3m_against = (wt1_3m < wt2_3m) if is_long else (wt1_3m > wt2_3m)
    # GR against-score: sum of all indicators on all HTF TFs in direction AGAINST trade
    _gr_against_count: Optional[np.ndarray] = None
    if config.GR_EXIT_ENABLED and evaluate_gr_htf_vec is not None:
        _, _gr_against_count = evaluate_gr_htf_vec(
            npz, is_long=(not is_long), mode=mode, vote_min=1, n=n
        )

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

    for i in range(n):
        bar_ts = float(ts[i])
        mark = float(close[i])
        if mark <= 0 or not np.isfinite(mark):
            continue

        # ─── FLAT: consider OPEN ──────────────────────────────────────────
        if state.qty <= 0.0001:
            # OPEN gate: WT_3M direction + reentry-fire OR force-open
            fire_block = bool(reentry["fire"][i])
            wt_open_ok = bool(wt_3m_aligned[i])
            if not (fire_block or wt_open_ok):
                continue
            # Compute size via qty pipeline (single-bar call into vec for parity)
            base_qty_arr = np.array([config.START_POSITION_SIZE / mark], dtype=np.float32)
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
            block_id = int(reentry["block_id"][i]) if fire_block else 0
            reason = BLOCK_NAMES.get(block_id, "WT_3M_FORCE_OPEN") if fire_block else "WT_3M_FORCE_OPEN"
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
                state.hedge_completed_ts = bar_ts; state.r1_stop_price = 0.0
                continue

        # GR multiplier exit: against-score >= threshold AND wt1_3m against
        if exit_id == EXIT_NONE and config.GR_EXIT_ENABLED and _gr_against_count is not None:
            if bool(_wt3m_against[i]) and int(_gr_against_count[i]) >= int(config.GR_EXIT_MULT_THRESHOLD):
                exit_id = 99
                exit_reason = f"GR_EXIT_mult={int(_gr_against_count[i])}_thr={int(config.GR_EXIT_MULT_THRESHOLD)}"

        # WT_4H_VEL_EXIT (needs profit + age; vec gave us full mask)
        if exit_id == EXIT_NONE and exit_gates["wt_4h_vel_full"][i] and age_s > 360 and gain >= comm_buf:
            exit_id = EXIT_WT_4H_VEL
            exit_reason = f"WT_4H_VEL_g={gain:.2f}%"

        # DC_HOPELESS_EXIT (state-aware: entry outside dc_4h channel)
        if exit_id == EXIT_NONE and float(config.DC_HOPELESS_EXIT_ENABLED):
            dh = float(dc_h_4h[i]); dl = float(dc_l_4h[i])
            if dh > 0 and dl > 0 and age_s > float(config.DC_HOPELESS_EXIT_MIN_AGE_S):
                if (is_long and state.entry_price > dh) or ((not is_long) and state.entry_price < dl):
                    exit_id = EXIT_DC_HOPELESS
                    exit_reason = f"DC_HOPELESS_entry={state.entry_price:.4f}"

        if exit_id == EXIT_NONE and exit_gates["wt_exhaust"][i]:
            if not config.WT_EXHAUST_EXIT_REQUIRE_GAIN or gain > 0:
                exit_id = EXIT_WT_EXHAUST
                exit_reason = "WT_EXHAUST"

        if exit_id == EXIT_NONE and exit_gates["wt_percentile"][i]:
            exit_id = EXIT_WT_PERCENTILE
            exit_reason = "WT_PERCENTILE"

        if exit_id == EXIT_NONE and exit_gates["e1_wt_delta"][i]:
            exit_id = EXIT_E1_WT_DELTA
            exit_reason = "E_1_WT_DELTA"

        if exit_id == EXIT_NONE and exit_gates["e3_structure"][i]:
            exit_id = EXIT_E3_STRUCTURE
            exit_reason = "E_3_STRUCTURE"

        # WT_CROSSUNDER_FINAL (stateless indicator check)
        if exit_id == EXIT_NONE and check_wt_crossunder_final_exit is not None and state.qty > 0.0001:
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
                state.hedge_completed_ts = bar_ts
                _pos.gain_pct = 0.0
                continue

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
    """Compute max drawdown from cumulative trade returns. Returns positive %."""
    if not trade_returns:
        return 0.0
    cum = np.cumsum(np.asarray(trade_returns, dtype=np.float64))
    peak = np.maximum.accumulate(cum)
    dd = peak - cum  # how far below peak
    return float(dd.max()) if len(dd) else 0.0


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
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    sides = [s.strip().upper() for s in args.sides.split(",") if s.strip()]
    cfg = SweepConfig()
    for k, v in _parse_overrides(args.override).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            sys.stderr.write(f"WARN: unknown SweepConfig knob {k} — ignored\n")

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
