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

    # ── RZ exit ──────────────────────────────────────────────
    RZ_EXIT_ENABLED: bool = False

    # ── SATOSHIT ─────────────────────────────────────────────
    SATOSHIT_ENABLED_TRADIER: bool = False
    SATOSHIT_ENTRY_FILTER: bool = True   # crypto

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

        # Tracking for sizing scalars
        dd_state: Dict[str, float] = {"peak": 0.0, "dd_pct": 0.0}

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
                        if pos.side == "LONG":
                            pos.gain_pct = (price - pos.entry_price) / pos.entry_price * 100.0
                        else:
                            pos.gain_pct = (pos.entry_price - price) / pos.entry_price * 100.0
                        if pos.gain_pct > pos.max_gain_pct:
                            pos.max_gain_pct = pos.gain_pct

                # ── R1: DC emergency exit (within newborn window) ──
                if cfg.R1_DC_LOW4_3M_EMERGENCY_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        age_min = (ts_i - pos.entry_ts) / 60.0
                        if age_min > cfg.R1_NEWBORN_WINDOW_MIN:
                            continue
                        tf = cfg.R1_TF
                        if cfg.R1_USE_DC_4BAR:
                            dc_level_l = store.f(f"dc_low4_{tf}", bar_idx)
                            dc_level_h = store.f(f"dc_high4_{tf}", bar_idx)
                        else:
                            dc_level_l = store.f(f"dc_low_{tf}", bar_idx)
                            dc_level_h = store.f(f"dc_high_{tf}", bar_idx)
                        r1_fired = False
                        if pos.side == "LONG" and dc_level_l > 0 and price < dc_level_l:
                            r1_fired = True
                        elif pos.side == "SHORT" and dc_level_h > 0 and price > dc_level_h:
                            r1_fired = True
                        if r1_fired:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False
                            pos.r1_fired = True
                            continue

                # ── R2: WT velocity slow exit ──────────────────
                if cfg.WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        gain = pos.gain_pct
                        floor = cfg.WT_15M_VEL_SLOW_GAIN_FLOOR_PCT
                        band = cfg.WT_15M_VEL_SLOW_GAIN_BAND_PCT
                        if not (floor <= gain < band):
                            continue
                        r2_fired = False
                        for r2_tf in cfg.R2_TF_LIST:
                            vel = store.f(f"wt_velocity_{r2_tf}", bar_idx)
                            vel_prev_idx = max(0, bar_idx - 1)
                            vel_prev = store.f(f"wt_velocity_{r2_tf}", vel_prev_idx)
                            vel_against = (pos.side == "LONG" and vel < 0) or (pos.side == "SHORT" and vel > 0)
                            decel = (abs(vel) <= 0.1) or (abs(vel) < abs(vel_prev) * cfg.WT_VEL_DECEL_RATIO)
                            if vel_against and decel:
                                r2_fired = True
                                break
                        if r2_fired:
                            pnl = pos.gain_pct
                            returns_by_sym[sym].append(pnl)
                            all_returns.append(pnl)
                            running_gain += pnl
                            pos.open = False

                # ── PARTIAL_PROFIT_LOCK v2 ──────────────────────
                if cfg.PARTIAL_PROFIT_LOCK_ENABLED:
                    for pos in (pos_long, pos_short):
                        if not pos.open:
                            continue
                        gain = pos.gain_pct
                        if not pos.ppl_fired and gain >= cfg.PARTIAL_PROFIT_LOCK_GAIN_PCT:
                            # Step 1: close FRAC at current price (approximation)
                            partial_pnl = gain * cfg.PARTIAL_PROFIT_LOCK_FRAC
                            returns_by_sym[sym].append(partial_pnl)
                            all_returns.append(partial_pnl)
                            running_gain += partial_pnl
                            pos.ppl_fired = True
                            pos.ppl_first_exit_price = price
                            be_buf = cfg.PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT / 100.0
                            if pos.side == "LONG":
                                pos.ppl_stop_level = pos.entry_price * (1.0 + be_buf)
                            else:
                                pos.ppl_stop_level = pos.entry_price * (1.0 - be_buf)
                            pos.qty *= (1.0 - cfg.PARTIAL_PROFIT_LOCK_FRAC)
                        elif pos.ppl_fired and not pos.ppl_stop_upgraded and gain >= cfg.PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT:
                            # Step 2: upgrade stop
                            pos.ppl_stop_level = pos.ppl_first_exit_price
                            pos.ppl_stop_upgraded = True
                        elif pos.ppl_fired and pos.ppl_stop_level > 0:
                            # Step 3: stop hit check
                            stop_hit = (pos.side == "LONG" and price <= pos.ppl_stop_level) or \
                                       (pos.side == "SHORT" and price >= pos.ppl_stop_level)
                            if stop_hit:
                                pnl = pos.gain_pct
                                returns_by_sym[sym].append(pnl)
                                all_returns.append(pnl)
                                running_gain += pnl
                                pos.open = False

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
                    # UNIVERSAL_NOLOSS_GATE: block loss exits (except R1/R2 already handled)
                    if cfg.UNIVERSAL_NOLOSS_GATE and pos.gain_pct < 0:
                        continue
                    # WT_CROSSUNDER_FINAL_ENABLED: require additional crossunder
                    if cfg.WT_CROSSUNDER_FINAL_ENABLED:
                        crossunder = store.b(f"stoch_crossunder_{btf}", bar_idx)
                        if not crossunder:
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
                    # STRUCTURAL_RANGE_SHIFT_EXIT (tradier)
                    if self.mode == "tradier" and cfg.STRUCTURAL_RANGE_SHIFT_EXIT:
                        tf_srs = cfg.STRUCTURAL_RANGE_SHIFT_TF
                        bb_pctb = store.f(f"bb_pct_b_{tf_srs}", bar_idx, 0.5)
                        bb_pctb_prev = store.f(f"bb_pct_b_{tf_srs}_prev", bar_idx, 0.5)
                        srs_long_exit = (pos.side == "LONG" and bb_pctb < 0.5 and bb_pctb_prev >= 0.5)
                        srs_short_exit = (pos.side == "SHORT" and bb_pctb > 0.5 and bb_pctb_prev <= 0.5)
                        if not (srs_long_exit or srs_short_exit):
                            continue
                    pnl = pos.gain_pct
                    returns_by_sym[sym].append(pnl)
                    all_returns.append(pnl)
                    running_gain += pnl
                    pos.open = False

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
                                pos.open = False

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
                            pos.open = False

                # ── Entry logic ─────────────────────────────────
                for side in ("LONG", "SHORT"):
                    pos = pos_states[sym][side]
                    if pos.open:
                        continue

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
                    if self.mode == "crypto" and cfg.SATOSHIT_ENTRY_FILTER:
                        sat_ok = self._check_satoshit(store, bar_idx, side, cfg)
                        if not sat_ok:
                            continue

                    # ── DELTA_ENTRY: velocity filter ─────────
                    if cfg.DELTA_ENTRY_ENABLED:
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

                    # ── Entry accepted — open position ───────
                    pos.open = True
                    pos.side = side
                    pos.entry_price = price
                    pos.entry_ts = ts_i
                    pos.mark_price = price
                    pos.gain_pct = 0.0
                    pos.max_gain_pct = 0.0
                    pos.ppl_fired = False
                    pos.ppl_stop_level = 0.0
                    pos.ppl_stop_upgraded = False
                    pos.ppl_first_exit_price = 0.0
                    pos.r1_fired = False
                    pos.qty = self._compute_sizing(store, bar_idx, side, cfg, dd_state, running_gain)

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
        "SATOSHIT_ENABLED_TRADIER": "APPROX",  # tradier satoshit not yet wired (stocks only)
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
        "TRADIER_FH_MOMENTUM_DC_CONFIRM": "APPROX",
        "TRADIER_FH_MOMENTUM_DC_MAX_LONG": "APPROX",
        "TRADIER_FH_MOMENTUM_MFI_CONFIRM": "APPROX",
        "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": "APPROX",
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
