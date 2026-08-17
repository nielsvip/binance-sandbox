#!/usr/bin/env python3
"""Same-entry vector adapter for alternative stock exit paths.

This research-only runner consumes the already-selected band-ladder curve from
``vec_band_ladder_walkforward`` artifacts.  It never re-ranks or re-selects an
entry curve while testing an exit.  Every candidate therefore receives the
same causal ladder request schedule, target/add semantics, next-RTH fill rule,
$16k hard capacity, costs, and persistent reclaim obligation.

Implemented exit books:

* ``E02``: completed 4h opposite Donchian close (same-adapter control);
* ``E02_GRID``: completed 1h/4h/D opposite Donchian close with a bounded
  lookback/profit-gate sweep; it never uses a 5m/DC-low churn stop;
* ``WT_MTF``: completed-TF WaveTrend exhaustion/rollover vote;
* ``GR_OPPOSITE``: explicit completed 15m/1h/4h/D opposite Golden Rule
  indicator votes. Per-TF raw votes and weights remain separately auditable;
* ``E01_CHANDELIER``: standard monotonic completed-4h/D Chandelier from
  entry, kept separate from the later structural-arm backlog adaptation;
* ``BOTTOM_A_PROTECTIVE_TRAIL``: completed adverse structure arm followed by
  an immediate diagnostic, ATR, rolling-stdev, or Donchian protective trail;
* ``MTF_ATR_TRAIL``: from-entry completed 1h/4h/D ATR ratchets whose latest
  adverse states must agree across a declared number of timeframes;
* ``STRUCTURAL_WT``: completed 4h lower-low arm followed by a later 1h lower
  price/WT1 top and rollover.  It never treats ``dc_low4`` as a profit exit.

LONG and SHORT accounting are isolated and consume separate frozen artifacts.
Output is VEC research evidence and cannot write the switch matrix or live
configuration.
"""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reentry_contract import resting_reclaim_fill  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from vec_paths.structural_wt_retest_exit import (  # noqa: E402
    CompletedBar,
    StructuralWtParams,
    StructuralWtRetestExitBook,
)

# Normal discovery exports compact aggregate folds.  The selected-combination
# chart lane explicitly enables this to retain the source ordered actions on
# S1 long enough to distil paired chart trades.
EMIT_EVENT_LEDGER = False


def chart_event_ledger(row: dict[str, Any]) -> dict[str, Any]:
    if not EMIT_EVENT_LEDGER:
        return {}
    ledger = row.get("event_ledger")
    return {
        "event_ledger": ledger if isinstance(ledger, list) else None,
        "event_ledger_status": "CAUSAL_RAW" if isinstance(ledger, list) else "UNAVAILABLE",
    }

STRUCTURAL_SCAN_SOURCE = ROOT / "tools" / "vec_same_entry_structural_scan.c"
_STRUCTURAL_SCAN_LIBRARY: ctypes.CDLL | None = None
ACTUAL_EXIT_REQUIRED_FAMILIES = {
    "EXIT_WT_MTF",
    "EXIT_GR_OPPOSITE",
    "EXIT_E01_CHANDELIER",
    "BOTTOM_A_PROTECTIVE_TRAIL",
    "BOTTOM_B_DELAYED_LOWER_TOP",
    "BOTTOM_C_DELAYED_EMERGENCY",
    "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
    "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
    "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
    "BOTTOM_B_STRUCTURAL_V2",
    "BOTTOM_C_STRUCTURAL_V2_EMERGENCY",
    "EXIT_MTF_ATR_TRAIL",
    "EXIT_ALGO_STRUCTURE_1H_15M",
    "EXIT_ALGO_STOCH_4H_ROLL",
    "EXIT_ALGO_PROFIT_TAKE_15M",
}
EMERGENCY_EXIT_FAMILIES = {
    "BOTTOM_C_DELAYED_EMERGENCY",
    "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
    "BOTTOM_C_STRUCTURAL_V2_EMERGENCY",
}
COMPILED_STRUCTURAL_FAMILIES = {
    "EXIT_STRUCTURAL_WT_LOWER_TOP",
    "BOTTOM_B_DELAYED_LOWER_TOP",
    "BOTTOM_C_DELAYED_EMERGENCY",
    "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
    "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
    "BOTTOM_B_STRUCTURAL_V2",
    "BOTTOM_C_STRUCTURAL_V2_EMERGENCY",
}


class _StructuralScanMetrics(ctypes.Structure):
    _fields_ = [
        ("capital_return_pct", ctypes.c_double),
        ("bh_capital_return_pct", ctypes.c_double),
        ("exposure_weighted_tim_pct", ctypes.c_double),
        ("binary_tim_pct", ctypes.c_double),
        ("max_drawdown_account_pct", ctypes.c_double),
        ("minimum_account_equity_usd", ctypes.c_double),
        ("peak_post_fill_notional_usd", ctypes.c_double),
        ("requested_notional_usd", ctypes.c_double),
        ("filled_notional_usd", ctypes.c_double),
        ("normal_exit_pnl_usd", ctypes.c_double),
        ("emergency_exit_pnl_usd", ctypes.c_double),
        ("insolvent", ctypes.c_int),
        ("entry_capacity_breach", ctypes.c_int),
        ("signals", ctypes.c_int),
        ("rejected_profit", ctypes.c_int),
        ("exit_fills", ctypes.c_int),
        ("normal_exit_fills", ctypes.c_int),
        ("emergency_exit_fills", ctypes.c_int),
        ("entry_fills", ctypes.c_int),
        ("ladder_reentries", ctypes.c_int),
        ("reclaim_reentries", ctypes.c_int),
        ("clamp_count", ctypes.c_int),
        ("future_htf_count", ctypes.c_int),
        ("bars_flat_beyond_reclaim", ctypes.c_int),
        ("dc4h_entry_blocks", ctypes.c_int),
        ("dc4h_safety_exit_fills", ctypes.c_int),
        ("rows", ctypes.c_int),
    ]


def _structural_scan_library() -> ctypes.CDLL:
    """Build/load the small compiled scanner keyed by exact C source."""
    global _STRUCTURAL_SCAN_LIBRARY
    if _STRUCTURAL_SCAN_LIBRARY is not None:
        return _STRUCTURAL_SCAN_LIBRARY
    digest = hashlib.sha256(STRUCTURAL_SCAN_SOURCE.read_bytes()).hexdigest()[:16]
    library_path = Path("/tmp") / f"vec_same_entry_structural_{digest}.so"
    if not library_path.exists():
        temporary = library_path.with_name(
            f"{library_path.name}.{os.getpid()}.tmp"
        )
        subprocess.run(
            [
                os.environ.get("CC", "cc"),
                "-O3",
                "-std=c11",
                "-fPIC",
                "-shared",
                str(STRUCTURAL_SCAN_SOURCE),
                "-o",
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        os.replace(temporary, library_path)
    library = ctypes.CDLL(str(library_path))
    i64 = np.ctypeslib.ndpointer(dtype=np.int64, ndim=1, flags="C_CONTIGUOUS")
    f64 = np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS")
    u8 = np.ctypeslib.ndpointer(dtype=np.uint8, ndim=1, flags="C_CONTIGUOUS")
    library.vec_same_entry_structural_scan.argtypes = [
        ctypes.c_int,  # n
        ctypes.c_int,  # left
        ctypes.c_int,  # right
        ctypes.c_int,  # side
        ctypes.c_int,  # semantics
        i64,  # ts
        f64,  # open
        f64,  # high
        f64,  # low
        f64,  # close
        f64,  # entry_mult
        f64,  # live 4h Donchian boundary
        ctypes.c_int,  # enforce live Tradier 4h boundary
        u8,  # arm_event
        i64,  # arm_source
        f64,  # arm_high
        f64,  # arm_low
        f64,  # arm_close
        f64,  # arm_wt
        f64,  # arm_atr
        u8,  # confirm_event
        i64,  # confirm_source
        f64,  # confirm_high
        f64,  # confirm_low
        f64,  # confirm_close
        f64,  # confirm_wt
        ctypes.c_double,  # rebound_atr
        ctypes.c_int,  # lookback
        ctypes.c_int,  # max_wait
        ctypes.c_int,  # confirmation_mode
        ctypes.c_int,  # confirmation_bars
        ctypes.c_int,  # arm_break_mode
        ctypes.c_double,  # arm_break_threshold
        ctypes.c_int,  # emergency_mask
        ctypes.c_double,  # emergency_adverse_atr
        ctypes.c_double,  # emergency_adverse_stdev
        ctypes.c_int,  # emergency_continued_bars
        ctypes.c_double,  # profit_gate_pct
        ctypes.c_double,  # commission
        ctypes.c_double,  # slippage
        ctypes.POINTER(_StructuralScanMetrics),
    ]
    library.vec_same_entry_structural_scan.restype = ctypes.c_int
    _STRUCTURAL_SCAN_LIBRARY = library
    return library


@dataclasses.dataclass(frozen=True)
class ExitDecision:
    reason: str
    reclaim_reference: float
    source_timestamps: dict[str, int]


class ExitBook(Protocol):
    label: str

    def update(self, row: int, *, active: bool) -> ExitDecision | None: ...


@dataclasses.dataclass(frozen=True)
class WtMtfParams:
    timeframes: tuple[str, ...]
    min_against_tfs: int
    extreme: float
    velocity: float
    recent_extreme_bars: int = 8
    require_fast_structure: bool = False
    profit_gate_pct: float = 0.0

    def validate(self) -> None:
        allowed = {"15m", "1h", "4h", "D", "W"}
        if not self.timeframes or not set(self.timeframes) <= allowed:
            raise ValueError(
                "WT timeframes must be a non-empty subset of 15m/1h/4h/D/W"
            )
        if not 1 <= self.min_against_tfs <= len(self.timeframes):
            raise ValueError("min_against_tfs exceeds selected timeframes")
        if self.extreme < 0 or self.velocity < 0:
            raise ValueError("WT extreme/velocity must be non-negative")
        if self.recent_extreme_bars < 2:
            raise ValueError("recent_extreme_bars must be >=2")
        if self.profit_gate_pct < 0:
            raise ValueError("profit gate must be non-negative")


@dataclasses.dataclass(frozen=True)
class GrOppositeParams:
    timeframes: tuple[str, ...]
    min_tfs: int
    min_indicators: int
    min_weighted_score: float
    weights: tuple[float, ...]
    profit_gate_pct: float = 0.0

    def validate(self) -> None:
        if self.timeframes != ("15m", "1h", "4h", "D"):
            raise ValueError("GR opposite contract requires 15m/1h/4h/D")
        if len(self.weights) != len(self.timeframes):
            raise ValueError("GR weights must map one-to-one to timeframes")
        if not 1 <= self.min_tfs <= 3:
            raise ValueError("GR min_tfs must be 1, 2, or 3")
        if self.min_indicators not in {1, 2}:
            raise ValueError("GR min_indicators must be 1 or 2")
        if self.min_weighted_score <= 0:
            raise ValueError("GR weighted score must be positive")
        if any(weight < 0 for weight in self.weights):
            raise ValueError("GR weights must be non-negative")
        if self.profit_gate_pct < 0:
            raise ValueError("profit gate must be non-negative")


@dataclasses.dataclass(frozen=True)
class ChandelierParams:
    timeframe: str
    lookback: int
    atr_mult: float
    profit_gate_pct: float = 0.0

    def validate(self) -> None:
        if self.timeframe not in {"4h", "D"}:
            raise ValueError("Chandelier timeframe must be 4h or D")
        if self.lookback not in {10, 20, 30, 55}:
            raise ValueError("unsupported bounded Chandelier lookback")
        if self.atr_mult not in {1.5, 2.0, 2.5, 3.0, 4.0}:
            raise ValueError("unsupported bounded Chandelier ATR multiple")
        if self.profit_gate_pct < 0:
            raise ValueError("profit gate must be non-negative")


@dataclasses.dataclass(frozen=True)
class ProtectiveTrailParams:
    arm_timeframe: str
    trail_timeframe: str
    mode: str
    break_buffer_atr: float = 0.0
    distance_mult: float = 2.0
    lookback: int = 20

    def validate(self) -> None:
        if self.arm_timeframe not in {"15m", "1h", "4h", "D"}:
            raise ValueError(
                "protective arm timeframe must be 15m, 1h, 4h, or D"
            )
        if self.trail_timeframe not in {"5m", "15m", "1h", "4h"}:
            raise ValueError(
                "protective trail timeframe must be 5m, 15m, 1h, or 4h"
            )
        if self.mode not in {"IMMEDIATE", "ATR", "STDEV", "DC"}:
            raise ValueError("unsupported protective trail mode")
        if self.break_buffer_atr not in {0.0, 0.25, 0.5}:
            raise ValueError("unsupported break buffer")
        if self.distance_mult <= 0 or self.lookback < 4:
            raise ValueError("invalid protective trail distance/lookback")


@dataclasses.dataclass(frozen=True)
class MtfAtrTrailParams:
    timeframes: tuple[str, ...]
    atr_mult: float
    min_profit_pct: float
    min_confirming_tfs: int

    def validate(self) -> None:
        if self.timeframes != ("1h", "4h", "D"):
            raise ValueError("MTF ATR trail requires completed 1h/4h/D")
        if self.atr_mult not in {1.5, 2.0, 2.5, 3.0, 4.0}:
            raise ValueError("unsupported MTF ATR multiple")
        if self.min_profit_pct not in {0.0, 0.25, 0.5, 1.0}:
            raise ValueError("unsupported MTF ATR minimum profit")
        if self.min_confirming_tfs not in {1, 2}:
            raise ValueError("MTF ATR confirmations must be 1 or 2")


@dataclasses.dataclass(frozen=True)
class AlgoStructureParams:
    timeframe: str
    lookback: int
    historical_score_delta: int
    profit_gate_pct: float

    def validate(self) -> None:
        expected = {"1h": -15, "15m": -10}
        if self.timeframe not in expected:
            raise ValueError("ALGO structure timeframe must be 1h or 15m")
        if self.lookback != 20:
            raise ValueError("only historical dc_low/dc_high seed N=20 allowed")
        if self.historical_score_delta != expected[self.timeframe]:
            raise ValueError("historical score-delta provenance mismatch")
        if self.profit_gate_pct not in {0.0, 3.0}:
            raise ValueError("unsupported ALGO structure research profit gate")


@dataclasses.dataclass(frozen=True)
class AlgoStoch4hParams:
    long_k_min: float
    short_k_max: float
    event_mode: str
    historical_score_delta: int
    profit_gate_pct: float

    def validate(self) -> None:
        if self.long_k_min + self.short_k_max != 100.0:
            raise ValueError("4h Stoch thresholds must be exact side mirrors")
        if self.long_k_min not in {60.0, 70.0, 80.0}:
            raise ValueError("unsupported 4h Stoch threshold")
        if self.event_mode not in {"STATE", "CROSS"}:
            raise ValueError("4h Stoch event mode must be STATE or CROSS")
        if self.historical_score_delta != -5:
            raise ValueError("historical 4h Stoch score delta must remain -5")
        if self.profit_gate_pct not in {0.0, 3.0}:
            raise ValueError("unsupported 4h Stoch profit gate")


@dataclasses.dataclass(frozen=True)
class AlgoProfitTake15mParams:
    event_mode: str
    min_profit_pct: float
    historical_score_delta: int = -5

    def validate(self) -> None:
        if self.event_mode not in {"STATE", "CROSS"}:
            raise ValueError("15m profit-turn mode must be STATE or CROSS")
        if self.min_profit_pct not in {3.0, 5.0, 7.0, 10.0}:
            raise ValueError("unsupported 15m profit threshold")
        if self.historical_score_delta != -5:
            raise ValueError("historical profit-turn score delta must remain -5")


class StaticExitBook:
    def __init__(
        self,
        label: str,
        events: np.ndarray,
        references: np.ndarray,
        source_by_row: dict[int, dict[str, int]],
        *,
        audit: dict[str, Any] | None = None,
    ):
        if events.dtype != np.uint8 or len(events) != len(references):
            raise ValueError("static exit arrays have incompatible shape/type")
        self.label = label
        self.events = events
        self.references = references
        self.source_by_row = source_by_row
        self.audit = audit or {}

    def update(self, row: int, *, active: bool) -> ExitDecision | None:
        if not active or not bool(self.events[row]):
            return None
        ref = float(self.references[row])
        return ExitDecision(
            reason=self.label,
            reclaim_reference=ref,
            source_timestamps=dict(self.source_by_row.get(row) or {}),
        )


def _rolling_max(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if n <= 0 or len(values) < n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, n)
    out[n - 1 :] = np.nanmax(windows, axis=1)
    return out


def _rolling_min(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if n <= 0 or len(values) < n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, n)
    out[n - 1 :] = np.nanmin(windows, axis=1)
    return out


def _completed_field(data: Any, htf: Any, key: str) -> np.ndarray:
    if key not in data.z.files:
        raise KeyError(f"{data.symbol} NPZ missing {key}")
    full = data.full_indices[htf.event_index]
    return np.asarray(data.z[key], dtype=np.float64)[full]


def _source_map(htf: Any) -> dict[int, int]:
    return {
        int(row): int(source)
        for row, source in zip(htf.event_index, htf.source_ts)
    }


def build_e02_book(
    data: Any,
    signals: ladder.SignalData,
    htfs: dict[str, Any],
) -> StaticExitBook:
    source = _source_map(htfs["4h"])
    source_by_row = {
        int(row): {"4h": int(source[row])}
        for row in np.flatnonzero(signals.exit_event)
        if int(row) in source
    }
    return StaticExitBook(
        "E02_DONCHIAN_4H_N30",
        np.asarray(signals.exit_event, dtype=np.uint8),
        np.asarray(signals.exit_ref, dtype=np.float64),
        source_by_row,
    )


def build_donchian_book(
    data: Any,
    htfs: dict[str, Any],
    *,
    timeframe: str,
    lookback: int,
    side: str,
) -> StaticExitBook:
    """Vectorize one causal completed-HTF Donchian exit book.

    LONG exits only after a completed close below the *prior* channel low;
    SHORT is the exact mirror above the prior channel high. The opposite prior
    channel edge is retained as the resting reclaim/top reference. No 5m
    series, interpolation shortcut, or first ``dc_low4`` break path is used.
    """
    if timeframe not in {"15m", "1h", "4h", "D"}:
        raise ValueError("Donchian timeframe must be 15m, 1h, 4h, or D")
    if lookback not in {10, 15, 20, 30, 40, 55, 80}:
        raise ValueError("unsupported bounded Donchian lookback")
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    h = htfs[timeframe]
    prior_low = ladder.top._rolling_prior(h.low, lookback, "min")
    prior_high = ladder.top._rolling_prior(h.high, lookback, "max")
    completed_event = (
        h.close < prior_low if side == "LONG" else h.close > prior_high
    )
    reclaim_reference = prior_high if side == "LONG" else prior_low
    events, references = ladder.top._map_events(
        len(data.ts), h, completed_event, reclaim_reference
    )
    sources = _source_map(h)
    source_by_row = {
        int(row): {timeframe: int(sources[row])}
        for row in np.flatnonzero(events)
        if int(row) in sources
    }
    for row, by_tf in source_by_row.items():
        if by_tf[timeframe] > int(data.ts[row]):
            raise RuntimeError(
                f"future {timeframe} Donchian source at row {row}"
            )
    return StaticExitBook(
        f"E02_DONCHIAN_{timeframe}_N{lookback}",
        np.asarray(events, dtype=np.uint8),
        np.asarray(references, dtype=np.float64),
        source_by_row,
    )


def e02_grid() -> list[dict[str, Any]]:
    return [
        {
            "timeframe": timeframe,
            "lookback": lookback,
            "profit_gate_pct": profit_gate,
        }
        for timeframe in ("1h", "4h", "D")
        for lookback in (10, 15, 20, 30, 40, 55, 80)
        for profit_gate in (0.0, 0.25, 0.5, 1.0)
    ]


def algo_structure_grid() -> list[AlgoStructureParams]:
    rows = [
        AlgoStructureParams(
            timeframe=timeframe,
            lookback=20,
            historical_score_delta=(-15 if timeframe == "1h" else -10),
            profit_gate_pct=profit_gate_pct,
        )
        for timeframe in ("1h", "15m")
        for profit_gate_pct in (0.0, 3.0)
    ]
    for row in rows:
        row.validate()
    assert len(rows) == 4
    return rows


def algo_stoch_4h_grid() -> list[AlgoStoch4hParams]:
    rows = [
        AlgoStoch4hParams(
            long_k_min=long_k_min,
            short_k_max=100.0 - long_k_min,
            event_mode=event_mode,
            historical_score_delta=-5,
            profit_gate_pct=profit_gate_pct,
        )
        for long_k_min in (60.0, 70.0, 80.0)
        for event_mode in ("STATE", "CROSS")
        for profit_gate_pct in (0.0, 3.0)
    ]
    for row in rows:
        row.validate()
    assert len(rows) == 12
    return rows


def build_algo_stoch_4h_book(
    data: Any,
    htfs: dict[str, Any],
    params: AlgoStoch4hParams,
    *,
    side: str,
) -> StaticExitBook:
    params.validate()
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    h = htfs["4h"]
    k = _completed_field(data, h, "k_4h")
    d = _completed_field(data, h, "d_4h")
    if side == "LONG":
        state = (k < d) & (k > params.long_k_min)
        crossed = np.concatenate(
            ([False], (k[:-1] >= d[:-1]) & (k[1:] < d[1:]))
        )
        references = np.asarray(h.high, dtype=np.float64)
    else:
        state = (k > d) & (k < params.short_k_max)
        crossed = np.concatenate(
            ([False], (k[:-1] <= d[:-1]) & (k[1:] > d[1:]))
        )
        references = np.asarray(h.low, dtype=np.float64)
    completed_event = state & crossed if params.event_mode == "CROSS" else state
    events, reclaim = ladder.top._map_events(
        len(data.ts), h, completed_event, references
    )
    sources = _source_map(h)
    source_by_row = {
        int(row): {"4h": int(sources[row])}
        for row in np.flatnonzero(events)
        if int(row) in sources
    }
    for row, by_tf in source_by_row.items():
        if by_tf["4h"] > int(data.ts[row]):
            raise RuntimeError(f"future 4h Stoch source at row {row}")
    return StaticExitBook(
        (
            f"EXIT_ALGO_STOCH_4H_ROLL_{params.event_mode}_"
            f"L{params.long_k_min:g}_S{params.short_k_max:g}"
        ),
        np.asarray(events, dtype=np.uint8),
        np.asarray(reclaim, dtype=np.float64),
        source_by_row,
        audit={
            "side_mirror": True,
            "completed_4h_only": True,
            "historical_asymmetry_not_combined": (
                "removed LONG seed >60 and SHORT seed <20 are represented "
                "at opposite ends of the mirrored threshold range"
            ),
        },
    )


def algo_profit_take_15m_grid() -> list[AlgoProfitTake15mParams]:
    rows = [
        AlgoProfitTake15mParams(event_mode, min_profit_pct)
        for event_mode in ("STATE", "CROSS")
        for min_profit_pct in (3.0, 5.0, 7.0, 10.0)
    ]
    for row in rows:
        row.validate()
    assert len(rows) == 8
    return rows


def build_algo_profit_take_15m_book(
    data: Any,
    htfs: dict[str, Any],
    params: AlgoProfitTake15mParams,
    *,
    side: str,
) -> StaticExitBook:
    params.validate()
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    h = htfs["15m"]
    k = _completed_field(data, h, "k_15m")
    d = _completed_field(data, h, "d_15m")
    if side == "LONG":
        state = k < d
        crossed = np.concatenate(
            ([False], (k[:-1] >= d[:-1]) & (k[1:] < d[1:]))
        )
        references = np.asarray(h.high, dtype=np.float64)
    else:
        state = k > d
        crossed = np.concatenate(
            ([False], (k[:-1] <= d[:-1]) & (k[1:] > d[1:]))
        )
        references = np.asarray(h.low, dtype=np.float64)
    completed_event = state & crossed if params.event_mode == "CROSS" else state
    events, reclaim = ladder.top._map_events(
        len(data.ts), h, completed_event, references
    )
    sources = _source_map(h)
    source_by_row = {
        int(row): {"15m": int(sources[row])}
        for row in np.flatnonzero(events)
        if int(row) in sources
    }
    for row, by_tf in source_by_row.items():
        if by_tf["15m"] > int(data.ts[row]):
            raise RuntimeError(f"future 15m profit-turn source at row {row}")
    return StaticExitBook(
        f"EXIT_ALGO_PROFIT_TAKE_15M_{params.event_mode}",
        np.asarray(events, dtype=np.uint8),
        np.asarray(reclaim, dtype=np.float64),
        source_by_row,
        audit={
            "side_mirror": True,
            "completed_15m_only": True,
            "historical_gain_seed": ">5%",
        },
    )


def build_wt_mtf_book(
    data: Any,
    htfs: dict[str, Any],
    params: WtMtfParams,
    *,
    side: str,
) -> StaticExitBook:
    """Build a completed-TF WT exhaustion/rollover vote.

    Each timeframe keeps its latest causal state.  A LONG vote requires WT1 to
    have visited the positive extreme recently and now be below WT2 with
    adverse velocity.  SHORT is the exact sign mirror.  An exit event occurs
    only on the edge into the required vote count, preventing flat/entry churn
    while an old exhaustion state remains true.
    """
    params.validate()
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    is_long = side == "LONG"
    n = len(data.ts)
    states: list[np.ndarray] = []
    updates = np.zeros(n, dtype=bool)
    source_maps: dict[str, dict[int, int]] = {}
    fast_structure = np.ones(n, dtype=bool)

    for tf in params.timeframes:
        h = htfs[tf]
        wt1 = _completed_field(data, h, f"wt1_{tf}")
        wt2 = _completed_field(data, h, f"wt2_{tf}")
        previous = np.concatenate(([np.nan], wt1[:-1]))
        recent = (
            _rolling_max(wt1, params.recent_extreme_bars)
            if is_long
            else _rolling_min(wt1, params.recent_extreme_bars)
        )
        if is_long:
            completed_state = (
                (recent >= params.extreme)
                & (wt1 < wt2)
                & ((previous - wt1) >= params.velocity)
            )
        else:
            completed_state = (
                (recent <= -params.extreme)
                & (wt1 > wt2)
                & ((wt1 - previous) >= params.velocity)
            )
        state = np.zeros(n, dtype=np.uint8)
        state[h.event_index] = completed_state.astype(np.uint8)
        # Forward-fill the latest completed state to execution rows.
        event_rows = np.asarray(h.event_index, dtype=np.int64)
        slots = np.searchsorted(event_rows, np.arange(n), side="right") - 1
        valid = slots >= 0
        expanded = np.zeros(n, dtype=np.uint8)
        expanded[valid] = completed_state[slots[valid]].astype(np.uint8)
        states.append(expanded)
        updates[event_rows] = True
        source_maps[tf] = _source_map(h)

    count = np.sum(np.vstack(states), axis=0)
    qualified = count >= params.min_against_tfs
    if params.require_fast_structure:
        fast_tf = params.timeframes[0]
        h = htfs[fast_tf]
        completed = np.zeros(len(h.close), dtype=bool)
        if is_long:
            completed[1:] = (
                (h.high[1:] < h.high[:-1]) & (h.low[1:] < h.low[:-1])
            )
        else:
            completed[1:] = (
                (h.high[1:] > h.high[:-1]) & (h.low[1:] > h.low[:-1])
            )
        fast_structure = np.zeros(n, dtype=bool)
        fast_structure[h.event_index] = completed
        qualified &= fast_structure
    prior = np.concatenate(([False], qualified[:-1]))
    events = (updates & qualified & ~prior).astype(np.uint8)
    references = np.full(n, np.nan, dtype=np.float64)
    references[events > 0] = (
        data.high[events > 0] if is_long else data.low[events > 0]
    )
    source_by_row: dict[int, dict[str, int]] = {}
    for row in np.flatnonzero(events):
        sources: dict[str, int] = {}
        for tf in params.timeframes:
            candidates = htfs[tf].event_index
            slot = int(np.searchsorted(candidates, row, side="right") - 1)
            if slot >= 0:
                source = int(htfs[tf].source_ts[slot])
                if source > int(data.ts[row]):
                    raise RuntimeError(f"future {tf} WT source at row {row}")
                sources[tf] = source
        source_by_row[int(row)] = sources
    return StaticExitBook(
        "EXIT_WT_MTF",
        events,
        references,
        source_by_row,
    )


def _completed_optional_field(
    data: Any,
    htf: Any,
    key: str,
) -> np.ndarray:
    """Return a completed numeric field or NaNs when the indicator is absent."""
    if key not in data.z.files:
        return np.full(len(htf.event_index), np.nan, dtype=np.float64)
    try:
        return _completed_field(data, htf, key)
    except (TypeError, ValueError):
        return np.full(len(htf.event_index), np.nan, dtype=np.float64)


def _golden_rule_completed_votes(
    data: Any,
    htf: Any,
    timeframe: str,
    *,
    vote_long: bool,
) -> np.ndarray:
    """Vectorize the raw per-TF Golden Rule indicator votes.

    This intentionally mirrors ``golden_rule_htf._ind_score`` in its default
    room-to-run mode, but returns the raw vote count instead of collapsing it
    into ``n_confirmed_tfs * min_indicators``. ``vote_long`` is the signal
    direction, which is always opposite the held side for this exit path.
    """
    field = lambda name: _completed_optional_field(  # noqa: E731
        data, htf, f"{name}_{timeframe}"
    )
    votes = np.zeros(len(htf.event_index), dtype=np.int16)

    wt1, wt2 = field("wt1"), field("wt2")
    available = np.isfinite(wt1) & np.isfinite(wt2) & (
        (wt1 != 0.0) | (wt2 != 0.0)
    )
    votes += (
        available & ((wt1 > wt2) if vote_long else (wt1 < wt2))
    ).astype(np.int16)

    rsi = field("rsi")
    votes += (
        np.isfinite(rsi)
        & (rsi >= 0.0)
        & ((rsi > 50.0) if vote_long else (rsi < 50.0))
    ).astype(np.int16)

    mfi = field("mfi")
    votes += (
        np.isfinite(mfi)
        & (mfi >= 0.0)
        & ((mfi > 50.0) if vote_long else (mfi < 50.0))
    ).astype(np.int16)

    close = field("close")
    dc_pos = field("dc_position")
    dc_high, dc_low = field("dc_high"), field("dc_low")
    derive_dc = (
        np.isfinite(close)
        & np.isfinite(dc_high)
        & np.isfinite(dc_low)
        & (dc_high > dc_low)
        & ((~np.isfinite(dc_pos)) | (dc_pos < 0.0))
    )
    derived_dc = np.divide(
        close - dc_low,
        dc_high - dc_low,
        out=np.full_like(close, np.nan),
        where=dc_high > dc_low,
    )
    dc_pos = np.where(derive_dc, derived_dc, dc_pos)
    votes += (
        np.isfinite(dc_pos)
        & (dc_pos >= 0.0)
        & ((dc_pos < 0.65) if vote_long else (dc_pos > 0.35))
    ).astype(np.int16)

    bb_pct = field("bb_pct_b")
    bb_upper, bb_lower = field("bb_upper"), field("bb_lower")
    derive_bb = (
        np.isfinite(close)
        & np.isfinite(bb_upper)
        & np.isfinite(bb_lower)
        & (bb_upper > bb_lower)
        & ((~np.isfinite(bb_pct)) | (bb_pct < 0.0))
    )
    derived_bb = np.divide(
        close - bb_lower,
        bb_upper - bb_lower,
        out=np.full_like(close, np.nan),
        where=bb_upper > bb_lower,
    )
    bb_pct = np.where(derive_bb, derived_bb, bb_pct)
    votes += (
        np.isfinite(bb_pct)
        & (bb_pct >= 0.0)
        & ((bb_pct < 0.75) if vote_long else (bb_pct > 0.25))
    ).astype(np.int16)

    relative_volume = field("relative_volume")
    votes += (
        np.isfinite(relative_volume)
        & (relative_volume >= 0.0)
        & (relative_volume > 1.0)
    ).astype(np.int16)

    stoch_k = field("stoch_k")
    votes += (
        np.isfinite(stoch_k)
        & (stoch_k >= 0.0)
        & ((stoch_k < 80.0) if vote_long else (stoch_k > 20.0))
    ).astype(np.int16)

    adx = field("adx")
    votes += (
        np.isfinite(adx) & (adx > 0.0) & (adx > 20.0)
    ).astype(np.int16)

    macd_hist = field("macd_hist")
    votes += (
        np.isfinite(macd_hist)
        & (macd_hist != 0.0)
        & ((macd_hist > 0.0) if vote_long else (macd_hist < 0.0))
    ).astype(np.int16)

    ha_color = field("ha_color")
    votes += (
        np.isfinite(ha_color)
        & (ha_color != 0.0)
        & ((ha_color > 0.0) if vote_long else (ha_color < 0.0))
    ).astype(np.int16)

    stoch_d = field("stoch_d")
    votes += (
        np.isfinite(stoch_k)
        & np.isfinite(stoch_d)
        & (stoch_k >= 0.0)
        & (stoch_d >= 0.0)
        & ((stoch_k > stoch_d) if vote_long else (stoch_k < stoch_d))
    ).astype(np.int16)
    return votes


def build_gr_opposite_book(
    data: Any,
    htfs: dict[str, Any],
    params: GrOppositeParams,
    *,
    side: str,
) -> StaticExitBook:
    """Build an explicit, side-mirrored completed-HTF GR exit book."""
    params.validate()
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    vote_long = side == "SHORT"
    n = len(data.ts)
    updates = np.zeros(n, dtype=bool)
    expanded_votes: dict[str, np.ndarray] = {}
    completed_votes: dict[str, np.ndarray] = {}

    for tf in params.timeframes:
        htf = htfs[tf]
        raw = _golden_rule_completed_votes(
            data, htf, tf, vote_long=vote_long
        )
        completed_votes[tf] = raw
        event_rows = np.asarray(htf.event_index, dtype=np.int64)
        slots = np.searchsorted(event_rows, np.arange(n), side="right") - 1
        valid = slots >= 0
        expanded = np.zeros(n, dtype=np.int16)
        expanded[valid] = raw[slots[valid]]
        expanded_votes[tf] = expanded
        updates[event_rows] = True

    qualifying_tf_count = np.zeros(n, dtype=np.int16)
    weighted_score = np.zeros(n, dtype=np.float64)
    for tf, weight in zip(params.timeframes, params.weights):
        raw = expanded_votes[tf]
        qualifying_tf_count += (raw >= params.min_indicators).astype(np.int16)
        weighted_score += raw * float(weight)
    qualified = (
        (qualifying_tf_count >= params.min_tfs)
        & (weighted_score >= params.min_weighted_score)
    )
    # GR direct exit is level-triggered on each newly completed HTF update.
    # An edge across the entire history is wrong: if the position opens while
    # the opposite vote is already active (or a fold begins after the edge),
    # the live path must still close it at the next completed update.
    events = (updates & qualified).astype(np.uint8)
    references = np.full(n, np.nan, dtype=np.float64)
    references[events > 0] = (
        data.high[events > 0] if side == "LONG" else data.low[events > 0]
    )

    source_by_row: dict[int, dict[str, int]] = {}
    for row in np.flatnonzero(events):
        sources: dict[str, int] = {}
        for tf in params.timeframes:
            htf = htfs[tf]
            slot = int(
                np.searchsorted(htf.event_index, row, side="right") - 1
            )
            if slot < 0:
                continue
            source = int(htf.source_ts[slot])
            if source > int(data.ts[row]):
                raise RuntimeError(f"future {tf} GR source at row {row}")
            sources[tf] = source
        source_by_row[int(row)] = sources

    event_rows = np.flatnonzero(events)
    update_rows = np.flatnonzero(updates)
    per_tf: dict[str, Any] = {}
    for tf, weight in zip(params.timeframes, params.weights):
        raw = completed_votes[tf]
        digest = hashlib.sha256()
        digest.update(np.asarray(htfs[tf].source_ts, dtype=np.int64).tobytes())
        digest.update(np.asarray(raw, dtype=np.int16).tobytes())
        event_raw = expanded_votes[tf][event_rows]
        input_groups = {
            "WT": (f"wt1_{tf}", f"wt2_{tf}"),
            "RSI": (f"rsi_{tf}",),
            "MFI": (f"mfi_{tf}",),
            "DC": (f"dc_position_{tf}",),
            "BB": (f"bb_pct_b_{tf}",),
            "RVOL": (f"relative_volume_{tf}",),
            "K": (f"stoch_k_{tf}",),
            "ADX": (f"adx_{tf}",),
            "MACD_H": (f"macd_hist_{tf}",),
            "HA": (f"ha_color_{tf}",),
            "K_D": (f"stoch_k_{tf}", f"stoch_d_{tf}"),
        }
        per_tf[tf] = {
            "weight": float(weight),
            "completed_vote_sha256": digest.hexdigest(),
            "completed_bars": int(len(raw)),
            "min_indicators_eligible_completed_bars": int(
                np.count_nonzero(raw >= params.min_indicators)
            ),
            "indicator_input_availability": {
                name: all(key in data.z.files for key in keys)
                for name, keys in input_groups.items()
            },
            "raw_vote_histogram": {
                str(vote): int(np.count_nonzero(raw == vote))
                for vote in range(12)
                if np.any(raw == vote)
            },
            "exit_event_vote_histogram": {
                str(vote): int(np.count_nonzero(event_raw == vote))
                for vote in range(12)
                if np.any(event_raw == vote)
            },
        }
    exit_scores = weighted_score[event_rows]
    update_scores = weighted_score[update_rows]
    update_tf_counts = qualifying_tf_count[update_rows]
    data_contract = getattr(data, "contract", {"valid": True, "errors": []})
    audit = {
        "signal_direction": "LONG" if vote_long else "SHORT",
        "held_side": side,
        "score_formula": (
            "sum(raw_opposite_indicator_votes_by_tf * explicit_tf_weight)"
        ),
        "per_timeframe": per_tf,
        "source_data_contract_valid": bool(data_contract["valid"]),
        "source_data_contract_errors": list(data_contract["errors"]),
        "completed_update_count": int(len(update_rows)),
        "eligible_completed_update_count": int(len(event_rows)),
        "weighted_score_histogram_at_completed_updates": {
            f"{score:g}": int(np.count_nonzero(update_scores == score))
            for score in np.unique(update_scores)
        },
        "qualifying_tf_count_histogram_at_completed_updates": {
            str(count): int(np.count_nonzero(update_tf_counts == count))
            for count in np.unique(update_tf_counts)
        },
        "exit_event_count": int(len(event_rows)),
        "weighted_score_at_event": {
            "min": float(np.min(exit_scores)) if len(exit_scores) else None,
            "max": float(np.max(exit_scores)) if len(exit_scores) else None,
            "mean": float(np.mean(exit_scores)) if len(exit_scores) else None,
        },
    }
    return StaticExitBook(
        "EXIT_GR_OPPOSITE",
        events,
        references,
        source_by_row,
        audit=audit,
    )


class ChandelierExitBook:
    """Stateful one-way Chandelier over completed 4h or D bars."""

    label = "EXIT_E01_CHANDELIER"

    def __init__(
        self,
        data: Any,
        htf: Any,
        params: ChandelierParams,
        *,
        side: str,
    ):
        params.validate()
        self.params = params
        self.side = side.upper()
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        self.is_long = self.side == "LONG"
        self.execution_ts = np.asarray(data.ts, dtype=np.int64)
        self.event_rows = np.asarray(htf.event_index, dtype=np.int64)
        self.source_ts = np.asarray(htf.source_ts, dtype=np.int64)
        self.completed_close = np.asarray(htf.close, dtype=np.float64)
        self.completed_atr = np.asarray(htf.atr, dtype=np.float64)
        if self.is_long:
            self.completed_extreme = _rolling_max(
                np.asarray(htf.high, dtype=np.float64), params.lookback
            )
            self.raw_stop = (
                self.completed_extreme
                - self.completed_atr * params.atr_mult
            )
        else:
            self.completed_extreme = _rolling_min(
                np.asarray(htf.low, dtype=np.float64), params.lookback
            )
            self.raw_stop = (
                self.completed_extreme
                + self.completed_atr * params.atr_mult
            )
        for row, source in zip(self.event_rows, self.source_ts):
            if int(source) > int(self.execution_ts[int(row)]):
                raise RuntimeError(
                    f"future {params.timeframe} Chandelier source at row {row}"
                )
        digest = hashlib.sha256()
        digest.update(self.source_ts.tobytes())
        digest.update(self.raw_stop.tobytes())
        self.audit = {
            "timeframe": params.timeframe,
            "lookback": params.lookback,
            "atr_mult": params.atr_mult,
            "formula": (
                "highest_high-ATR*mult"
                if self.is_long
                else "lowest_low+ATR*mult"
            ),
            "monotonic_while_active": True,
            "completed_source_count": int(len(self.event_rows)),
            "raw_stop_sha256": digest.hexdigest(),
        }
        self.reset()

    def reset(self) -> None:
        self._was_active = False
        self.current_trail = math.nan

    def clone(self) -> "ChandelierExitBook":
        clone = object.__new__(ChandelierExitBook)
        clone.params = self.params
        clone.side = self.side
        clone.is_long = self.is_long
        clone.execution_ts = self.execution_ts
        clone.event_rows = self.event_rows
        clone.source_ts = self.source_ts
        clone.completed_close = self.completed_close
        clone.completed_atr = self.completed_atr
        clone.completed_extreme = self.completed_extreme
        clone.raw_stop = self.raw_stop
        clone.audit = self.audit
        clone.reset()
        return clone

    def update(self, row: int, *, active: bool) -> ExitDecision | None:
        if not active:
            self.reset()
            return None
        slot = int(np.searchsorted(self.event_rows, row, side="right") - 1)
        if slot < 0:
            self._was_active = True
            return None
        candidate = float(self.raw_stop[slot])
        if not self._was_active:
            self._was_active = True
            self.current_trail = candidate
        # A stop decision is made only on a newly completed source bar.
        if int(self.event_rows[slot]) != int(row) or not math.isfinite(candidate):
            return None
        if self.is_long:
            self.current_trail = (
                max(self.current_trail, candidate)
                if math.isfinite(self.current_trail)
                else candidate
            )
            triggered = float(self.completed_close[slot]) <= self.current_trail
        else:
            self.current_trail = (
                min(self.current_trail, candidate)
                if math.isfinite(self.current_trail)
                else candidate
            )
            triggered = float(self.completed_close[slot]) >= self.current_trail
        if not triggered:
            return None
        return ExitDecision(
            reason=(
                f"EXIT_E01_CHANDELIER_{self.params.timeframe}_"
                f"N{self.params.lookback}_ATR{self.params.atr_mult:g}"
            ),
            reclaim_reference=float(self.completed_extreme[slot]),
            source_timestamps={
                self.params.timeframe: int(self.source_ts[slot])
            },
        )


def build_chandelier_book(
    data: Any,
    htfs: dict[str, Any],
    params: ChandelierParams,
    *,
    side: str,
) -> ChandelierExitBook:
    """Build the registered from-entry E01 path, without structural arming."""
    return ChandelierExitBook(
        data,
        htfs[params.timeframe],
        params,
        side=side,
    )


class ProtectiveTrailExitBook:
    """Adverse completed-structure arm followed by a protective trail.

    ``IMMEDIATE`` is retained only as the dc-break churn comparator. The other
    modes arm at the break and cannot exit until a later completed trail bar.
    """

    label = "BOTTOM_A_PROTECTIVE_TRAIL"

    def __init__(
        self,
        data: Any,
        htfs: dict[str, Any],
        params: ProtectiveTrailParams,
        *,
        side: str,
    ):
        params.validate()
        self.params = params
        self.side = side.upper()
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        self.is_long = self.side == "LONG"
        self.execution_ts = np.asarray(data.ts, dtype=np.int64)
        arm = htfs[params.arm_timeframe]
        trail = htfs[params.trail_timeframe]
        prior_low = np.concatenate(([np.nan], arm.low[:-1]))
        prior_high = np.concatenate(([np.nan], arm.high[:-1]))
        prior_atr = np.concatenate(([np.nan], arm.atr[:-1]))
        if self.is_long:
            arm_completed = (
                (arm.low < prior_low)
                & (
                    arm.close
                    < prior_low - params.break_buffer_atr * prior_atr
                )
            )
        else:
            arm_completed = (
                (arm.high > prior_high)
                & (
                    arm.close
                    > prior_high + params.break_buffer_atr * prior_atr
                )
            )
        self.arm_rows = np.asarray(
            arm.event_index[np.flatnonzero(arm_completed)], dtype=np.int64
        )
        arm_sources = np.asarray(
            arm.source_ts[np.flatnonzero(arm_completed)], dtype=np.int64
        )
        self.arm_source_by_row = dict(
            zip(self.arm_rows.tolist(), arm_sources.tolist())
        )
        self.trail_rows = np.asarray(trail.event_index, dtype=np.int64)
        self.trail_sources = np.asarray(trail.source_ts, dtype=np.int64)
        self.trail_close = np.asarray(trail.close, dtype=np.float64)
        self.trail_high = np.asarray(trail.high, dtype=np.float64)
        self.trail_low = np.asarray(trail.low, dtype=np.float64)
        self.trail_atr = np.asarray(trail.atr, dtype=np.float64)
        if params.mode == "STDEV":
            self.distance = np.full(len(trail.close), np.nan, dtype=np.float64)
            if len(trail.close) >= params.lookback:
                windows = np.lib.stride_tricks.sliding_window_view(
                    np.asarray(trail.close, dtype=np.float64), params.lookback
                )
                self.distance[params.lookback - 1 :] = np.std(
                    windows, axis=1, ddof=0
                )
        elif params.mode == "DC":
            self.distance = (
                ladder.top._rolling_prior(trail.low, params.lookback, "min")
                if self.is_long
                else ladder.top._rolling_prior(
                    trail.high, params.lookback, "max"
                )
            )
        else:
            self.distance = self.trail_atr
        for row, source in list(self.arm_source_by_row.items()) + list(
            zip(self.trail_rows.tolist(), self.trail_sources.tolist())
        ):
            if int(source) > int(self.execution_ts[int(row)]):
                raise RuntimeError(
                    f"future protective trail source at row {row}"
                )
        self.audit = {
            "break_is_arm": params.mode != "IMMEDIATE",
            "immediate_break_diagnostic": params.mode == "IMMEDIATE",
            "five_minute_provenance_preserved": (
                params.trail_timeframe == "5m"
            ),
            "arm_timeframe": params.arm_timeframe,
            "trail_timeframe": params.trail_timeframe,
        }
        self.reset()

    def reset(self) -> None:
        self.armed = False
        self.trail_level = math.nan
        self.reclaim_reference = math.nan
        self.arm_source = 0

    def update(self, row: int, *, active: bool) -> ExitDecision | None:
        if not active:
            self.reset()
            return None
        if row in self.arm_source_by_row:
            self.armed = True
            self.trail_level = math.nan
            self.reclaim_reference = (
                float(self.trail_high[
                    max(
                        0,
                        int(np.searchsorted(self.trail_rows, row, side="right"))
                        - 1,
                    )
                ])
                if self.is_long
                else float(self.trail_low[
                    max(
                        0,
                        int(np.searchsorted(self.trail_rows, row, side="right"))
                        - 1,
                    )
                ])
            )
            self.arm_source = int(self.arm_source_by_row[row])
            if self.params.mode == "IMMEDIATE":
                return ExitDecision(
                    reason="BOTTOM_A_IMMEDIATE_BREAK_DIAGNOSTIC",
                    reclaim_reference=self.reclaim_reference,
                    source_timestamps={
                        self.params.arm_timeframe: self.arm_source
                    },
                )
            # The adverse arm bar may never also be a trail exit.
            return None
        if not self.armed:
            return None
        slot = int(np.searchsorted(self.trail_rows, row, side="right") - 1)
        if slot < 0 or int(self.trail_rows[slot]) != int(row):
            return None
        close = float(self.trail_close[slot])
        if self.is_long:
            self.reclaim_reference = max(
                self.reclaim_reference, float(self.trail_high[slot])
            )
        else:
            self.reclaim_reference = min(
                self.reclaim_reference, float(self.trail_low[slot])
            )
        if self.params.mode == "DC":
            candidate = float(self.distance[slot])
        else:
            distance = float(self.distance[slot])
            candidate = (
                close - self.params.distance_mult * distance
                if self.is_long
                else close + self.params.distance_mult * distance
            )
        if not math.isfinite(candidate) or candidate <= 0:
            return None
        self.trail_level = (
            max(self.trail_level, candidate)
            if self.is_long and math.isfinite(self.trail_level)
            else min(self.trail_level, candidate)
            if not self.is_long and math.isfinite(self.trail_level)
            else candidate
        )
        fired = (
            close <= self.trail_level
            if self.is_long
            else close >= self.trail_level
        )
        if not fired:
            return None
        return ExitDecision(
            reason=(
                f"BOTTOM_A_{self.params.mode}_{self.params.trail_timeframe}"
            ),
            reclaim_reference=float(self.reclaim_reference),
            source_timestamps={
                self.params.arm_timeframe: self.arm_source,
                self.params.trail_timeframe: int(self.trail_sources[slot]),
            },
        )


def protective_trail_grid() -> list[ProtectiveTrailParams]:
    """Code-derived trail ranges plus explicitly tagged research breadth."""
    rows: list[ProtectiveTrailParams] = []
    for arm_tf in ("1h", "4h"):
        for break_buffer in (0.0, 0.25):
            rows.append(
                ProtectiveTrailParams(
                    arm_tf, "5m", "IMMEDIATE", break_buffer
                )
            )
            for trail_tf in ("5m", "15m", "1h"):
                for mult in (1.5, 2.0, 3.0, 4.0):
                    rows.append(
                        ProtectiveTrailParams(
                            arm_tf,
                            trail_tf,
                            "ATR",
                            break_buffer,
                            mult,
                            20,
                        )
                    )
                for lookback in (10, 20, 40):
                    for mult in (1.5, 2.0, 2.5, 3.0):
                        rows.append(
                            ProtectiveTrailParams(
                                arm_tf,
                                trail_tf,
                                "STDEV",
                                break_buffer,
                                mult,
                                lookback,
                            )
                        )
                for lookback in (4, 20):
                    rows.append(
                        ProtectiveTrailParams(
                            arm_tf,
                            trail_tf,
                            "DC",
                            break_buffer,
                            1.0,
                            lookback,
                        )
                    )
    assert len(rows) == 220
    return rows


def protective_trail_grid_extended() -> list[ProtectiveTrailParams]:
    """Bounded breadth outside the original A grid.

    This adds faster/slower structural arms, a wider adverse-break buffer, a
    completed 4h trail, and deliberately sparse outer distance ranges.  It is
    separate from :func:`protective_trail_grid` so historical evidence remains
    reproducible and the immediate-break rows stay diagnostic controls.
    """
    rows: list[ProtectiveTrailParams] = []
    for arm_tf in ("15m", "1h", "4h", "D"):
        for break_buffer in (0.0, 0.25, 0.5):
            rows.append(
                ProtectiveTrailParams(
                    arm_tf, "5m", "IMMEDIATE", break_buffer
                )
            )
            for trail_tf in ("5m", "15m", "1h", "4h"):
                for mult in (1.25, 2.5, 5.0):
                    rows.append(
                        ProtectiveTrailParams(
                            arm_tf,
                            trail_tf,
                            "ATR",
                            break_buffer,
                            mult,
                            20,
                        )
                    )
                for lookback, mult in ((6, 1.0), (20, 4.0), (60, 5.0)):
                    rows.append(
                        ProtectiveTrailParams(
                            arm_tf,
                            trail_tf,
                            "STDEV",
                            break_buffer,
                            mult,
                            lookback,
                        )
                    )
                for lookback in (10, 40, 80):
                    rows.append(
                        ProtectiveTrailParams(
                            arm_tf,
                            trail_tf,
                            "DC",
                            break_buffer,
                            1.0,
                            lookback,
                        )
                    )
    assert len(rows) == 444
    return rows


class MtfAtrTrailExitBook:
    """Research-only completed 1h/4h/D agreement ATR ratchet.

    This deliberately does not claim live parity.  The shared live/v8 path in
    ``vec_paths.mtf_atr_trail`` is one configurable timeframe.  This book
    keeps an independent canonical from-entry ratchet for each completed HTF
    and requires the configured number of latest HTF states to agree.
    """

    label = "EXIT_MTF_ATR_TRAIL"

    def __init__(
        self,
        data: Any,
        htfs: dict[str, Any],
        params: MtfAtrTrailParams,
        *,
        side: str,
    ):
        params.validate()
        self.params = params
        self.side = side.upper()
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        self.is_long = self.side == "LONG"
        self.execution_ts = np.asarray(data.ts, dtype=np.int64)
        self.execution_high = np.asarray(data.high, dtype=np.float64)
        self.execution_low = np.asarray(data.low, dtype=np.float64)
        self.tf_data: dict[str, dict[str, Any]] = {}
        self.events_by_row: dict[int, list[tuple[str, int]]] = {}
        for timeframe in params.timeframes:
            tf = htfs[timeframe]
            rows = np.asarray(tf.event_index, dtype=np.int64)
            sources = np.asarray(tf.source_ts, dtype=np.int64)
            closes = np.asarray(tf.close, dtype=np.float64)
            highs = np.asarray(tf.high, dtype=np.float64)
            lows = np.asarray(tf.low, dtype=np.float64)
            atr = np.asarray(tf.atr, dtype=np.float64)
            self.tf_data[timeframe] = {
                "rows": rows,
                "sources": sources,
                "close": closes,
                "high": highs,
                "low": lows,
                "atr": atr,
            }
            for slot, row in enumerate(rows.tolist()):
                source = int(sources[slot])
                if source > int(self.execution_ts[int(row)]):
                    raise RuntimeError(
                        f"future MTF ATR source {timeframe} at row {row}"
                    )
                self.events_by_row.setdefault(int(row), []).append(
                    (timeframe, slot)
                )
        self.audit = {
            "research_only_not_live_single_tf": True,
            "canonical_ratchet_formula": (
                "LONG max(entry-m*ATR,close-m*ATR), ratchet max; "
                "SHORT exact mirror"
            ),
            "completed_timeframes": list(params.timeframes),
            "profit_gate_uses_current_position_gain_pct": True,
            "minimum_confirming_timeframes": params.min_confirming_tfs,
        }
        self.reset()

    def reset(self) -> None:
        self.trail_by_tf = {
            timeframe: math.nan for timeframe in self.params.timeframes
        }
        self.adverse_by_tf = {
            timeframe: False for timeframe in self.params.timeframes
        }
        self.source_by_tf = {
            timeframe: 0 for timeframe in self.params.timeframes
        }
        self.reclaim_reference = math.nan

    def update(self, row: int, *, active: bool) -> ExitDecision | None:
        """Protocol fallback; simulation must supply position information."""
        if not active:
            self.reset()
            return None
        raise RuntimeError("MTF ATR trail requires entry price and current gain")

    def update_with_position(
        self,
        row: int,
        *,
        active: bool,
        entry_price: float,
        current_gain_pct: float,
    ) -> ExitDecision | None:
        if not active:
            self.reset()
            return None
        if not math.isfinite(entry_price) or entry_price <= 0:
            return None
        if self.is_long:
            self.reclaim_reference = (
                max(
                    self.reclaim_reference,
                    float(self.execution_high[row]),
                )
                if math.isfinite(self.reclaim_reference)
                else float(self.execution_high[row])
            )
        else:
            self.reclaim_reference = (
                min(
                    self.reclaim_reference,
                    float(self.execution_low[row]),
                )
                if math.isfinite(self.reclaim_reference)
                else float(self.execution_low[row])
            )
        updated = self.events_by_row.get(int(row), ())
        if not updated:
            return None
        for timeframe, slot in updated:
            values = self.tf_data[timeframe]
            close = float(values["close"][slot])
            atr = float(values["atr"][slot])
            self.source_by_tf[timeframe] = int(values["sources"][slot])
            if not (
                math.isfinite(close)
                and close > 0
                and math.isfinite(atr)
                and atr > 0
            ):
                self.adverse_by_tf[timeframe] = False
                continue
            previous = self.trail_by_tf[timeframe]
            if self.is_long:
                candidate = max(
                    entry_price - self.params.atr_mult * atr,
                    close - self.params.atr_mult * atr,
                )
                trail = (
                    max(previous, candidate)
                    if math.isfinite(previous)
                    else candidate
                )
                adverse = close < trail
            else:
                candidate = min(
                    entry_price + self.params.atr_mult * atr,
                    close + self.params.atr_mult * atr,
                )
                trail = (
                    min(previous, candidate)
                    if math.isfinite(previous)
                    else candidate
                )
                adverse = close > trail
            self.trail_by_tf[timeframe] = trail
            self.adverse_by_tf[timeframe] = adverse
        confirming = [
            timeframe
            for timeframe in self.params.timeframes
            if self.adverse_by_tf[timeframe]
        ]
        if len(confirming) < self.params.min_confirming_tfs:
            return None
        if current_gain_pct + 1e-12 < self.params.min_profit_pct:
            return None
        return ExitDecision(
            reason=(
                f"EXIT_MTF_ATR_TRAIL_{self.params.min_confirming_tfs}TF_"
                f"x{self.params.atr_mult:g}_p{self.params.min_profit_pct:g}"
            ),
            reclaim_reference=float(self.reclaim_reference),
            source_timestamps={
                timeframe: int(self.source_by_tf[timeframe])
                for timeframe in confirming
            },
        )


def mtf_atr_trail_grid() -> list[MtfAtrTrailParams]:
    rows = [
        MtfAtrTrailParams(
            timeframes=("1h", "4h", "D"),
            atr_mult=atr_mult,
            min_profit_pct=min_profit_pct,
            min_confirming_tfs=min_confirming_tfs,
        )
        for atr_mult in (1.5, 2.0, 2.5, 3.0, 4.0)
        for min_profit_pct in (0.0, 0.25, 0.5, 1.0)
        for min_confirming_tfs in (1, 2)
    ]
    assert len(rows) == 40
    return rows


class StructuralWtExitBookAdapter:
    label = "EXIT_STRUCTURAL_WT_LOWER_TOP"

    def __init__(
        self,
        data: Any,
        htfs: dict[str, Any],
        params: StructuralWtParams,
        *,
        side: str,
    ):
        self.data = data
        self.params = params
        self.side = side.upper()
        self.book = StructuralWtRetestExitBook(params)
        self.events: dict[int, list[tuple[CompletedBar, str]]] = {}
        self.wt1_at: dict[tuple[str, int], float] = {}
        for tf, role in (
            (params.arm_tf, "ARM"),
            (params.confirm_tf, "CONFIRM"),
        ):
            h = htfs[tf]
            values = _completed_field(data, h, f"wt1_{tf}")
            for slot, row in enumerate(h.event_index):
                row = int(row)
                self.events.setdefault(row, []).append(
                    (
                        CompletedBar(
                        timeframe=tf,
                        source_ts=int(h.source_ts[slot]),
                        observed_ts=int(data.ts[row]),
                        high=float(h.high[slot]),
                        low=float(h.low[slot]),
                        close=float(h.close[slot]),
                        wt1=float(values[slot]),
                        atr=float(h.atr[slot]),
                        ),
                        role,
                    )
                )
        for rows in self.events.values():
            rows.sort(key=lambda item: 0 if item[1] == "ARM" else 1)

    def update(self, row: int, *, active: bool) -> ExitDecision | None:
        candidate = None
        for bar, role in self.events.get(row, ()):
            candidate = (
                self.book.update(
                    symbol=self.data.symbol,
                    position_side=self.side,
                    active=active,
                    bar=bar,
                    role=role,
                )
                or candidate
            )
        if candidate is None:
            return None
        return ExitDecision(
            reason=candidate.reason,
            reclaim_reference=float(candidate.retest_price),
            source_timestamps={
                self.params.arm_tf: int(candidate.arm_source_ts),
                self.params.confirm_tf: int(candidate.source_ts),
            },
        )


def _aligned_structural_tf(
    data: Any,
    htf: Any,
    timeframe: str,
) -> tuple[np.ndarray, ...]:
    """Expand completed HTF events to execution rows for the C scanner."""
    n = len(data.ts)
    event = np.zeros(n, dtype=np.uint8)
    source = np.zeros(n, dtype=np.int64)
    high = np.zeros(n, dtype=np.float64)
    low = np.zeros(n, dtype=np.float64)
    close = np.zeros(n, dtype=np.float64)
    wt = np.zeros(n, dtype=np.float64)
    atr = np.zeros(n, dtype=np.float64)
    rows = np.asarray(htf.event_index, dtype=np.int64)
    event[rows] = 1
    source[rows] = np.asarray(htf.source_ts, dtype=np.int64)
    high[rows] = np.asarray(htf.high, dtype=np.float64)
    low[rows] = np.asarray(htf.low, dtype=np.float64)
    close[rows] = np.asarray(htf.close, dtype=np.float64)
    wt[rows] = _completed_field(data, htf, f"wt1_{timeframe}")
    atr[rows] = np.asarray(htf.atr, dtype=np.float64)
    return event, source, high, low, close, wt, atr


def simulate_structural_compiled(
    data: Any,
    entry_signals: ladder.SignalData,
    curve: ladder.Curve,
    htfs: dict[str, Any],
    params: StructuralWtParams,
    left: int,
    right: int,
    commission_rate: float,
    slippage_rate: float,
    *,
    side: str,
    profit_gate_pct: float,
    aligned_by_tf: dict[str, tuple[np.ndarray, ...]] | None = None,
) -> dict[str, Any]:
    """Exact-contract compiled screen; Python replay remains the parity oracle."""
    params.validate()
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    aligned_by_tf = aligned_by_tf or {}
    arm = aligned_by_tf.get(params.arm_tf) or _aligned_structural_tf(
        data, htfs[params.arm_tf], params.arm_tf
    )
    confirm = aligned_by_tf.get(params.confirm_tf) or _aligned_structural_tf(
        data, htfs[params.confirm_tf], params.confirm_tf
    )
    out = _StructuralScanMetrics()
    confirmation_mode = {
        "AND": 0,
        "PRICE_ONLY": 1,
        "WT_ONLY": 2,
        "OR": 3,
    }[params.confirmation_mode]
    arm_break_mode = {
        "PREV_BAR": 0,
        "ATR": 1,
        "STDEV": 2,
        "DC_SUPPORT": 3,
    }[params.arm_break_mode]
    emergency_mask = sum(
        {
            "ADVERSE_ATR": 1,
            "ADVERSE_STDEV": 2,
            "MAX_WAIT": 4,
            "CONTINUED": 8,
        }[mode]
        for mode in params.emergency_modes
    )
    dc4h_level, dc4h_enforced = _tradier_dc4h_boundary_series(data, side)
    rc = _structural_scan_library().vec_same_entry_structural_scan(
        len(data.ts),
        left,
        right,
        1 if side == "LONG" else -1,
        0 if curve.semantics == "target" else 1,
        np.ascontiguousarray(data.ts, dtype=np.int64),
        np.ascontiguousarray(data.open, dtype=np.float64),
        np.ascontiguousarray(data.high, dtype=np.float64),
        np.ascontiguousarray(data.low, dtype=np.float64),
        np.ascontiguousarray(data.close, dtype=np.float64),
        np.ascontiguousarray(entry_signals.entry_mult, dtype=np.float64),
        dc4h_level,
        int(dc4h_enforced),
        *arm,
        *confirm[:-1],
        params.rebound_atr,
        params.prebreak_lookback,
        params.max_wait_1h,
        confirmation_mode,
        params.confirmation_bars,
        arm_break_mode,
        params.arm_break_threshold,
        emergency_mask,
        params.emergency_adverse_atr,
        params.emergency_adverse_stdev,
        params.emergency_continued_bars,
        profit_gate_pct,
        commission_rate,
        slippage_rate,
        ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled structural scan failed with code {rc}")
    requested = float(out.requested_notional_usd)
    candidate_return = float(out.capital_return_pct)
    bh_return = float(out.bh_capital_return_pct)
    return {
        "capital_return_pct": candidate_return,
        "bh_capital_return_pct": bh_return,
        "strategy_bh_multiple": (
            candidate_return / bh_return if bh_return > 1e-12 else None
        ),
        "alpha_vs_bh_pp": candidate_return - bh_return,
        "binary_tim_pct": float(out.binary_tim_pct),
        "exposure_weighted_tim_pct": float(out.exposure_weighted_tim_pct),
        "max_drawdown_account_pct": float(out.max_drawdown_account_pct),
        "minimum_account_equity_usd": float(out.minimum_account_equity_usd),
        "insolvent": bool(out.insolvent),
        "peak_post_fill_notional_usd": float(
            out.peak_post_fill_notional_usd
        ),
        "entry_capacity_breach": bool(out.entry_capacity_breach),
        "signals": int(out.signals),
        "rejected_by_profit_gate": int(out.rejected_profit),
        "exit_fills": int(out.exit_fills),
        # The compiled structural path has no partial/runner close action:
        # each recorded exit fill is a terminal position lifecycle close.
        "real_close_trades": int(out.exit_fills),
        "terminal_lifecycle_closes": int(out.exit_fills),
        "normal_exit_fills": int(out.normal_exit_fills),
        "emergency_exit_fills": int(out.emergency_exit_fills),
        "normal_exit_pnl_usd": float(out.normal_exit_pnl_usd),
        "emergency_exit_pnl_usd": float(out.emergency_exit_pnl_usd),
        "emergency_exit_share": (
            float(out.emergency_exit_fills) / float(out.exit_fills)
            if out.exit_fills
            else 0.0
        ),
        "partial_exit_fills": 0,
        "runner_exit_fills": 0,
        "clip_reclaim_reentries": 0,
        "clip_obligations_unfilled_at_end": 0,
        "entry_fills": int(out.entry_fills),
        "lower_or_higher_reentries": int(out.ladder_reentries),
        "reclaim_reentries": int(out.reclaim_reentries),
        "requested_notional_usd": requested,
        "filled_notional_usd": float(out.filled_notional_usd),
        "fill_ratio": (
            float(out.filled_notional_usd) / requested
            if requested > 0
            else 1.0
        ),
        "clamp_count": int(out.clamp_count),
        "future_htf_source_count": int(out.future_htf_count),
        "bars_flat_beyond_reclaim": int(out.bars_flat_beyond_reclaim),
        "dc4h_entry_blocks": int(out.dc4h_entry_blocks),
        "dc4h_safety_exit_fills": int(out.dc4h_safety_exit_fills),
        "dc4h_safety_enforced": bool(dc4h_enforced),
        "mandatory_reclaim_execution": (
            "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN"
        ),
        "frozen_entry_schedule_sha256": _entry_schedule_hash(
            data, entry_signals, curve, left, right
        ),
        "entry_request_count": int(
            np.count_nonzero(entry_signals.entry_mult[left:right] > 0)
        ),
        "side": side,
        "rows": int(out.rows),
        "event_ledger": [],
        "screen_engine": "COMPILED_C_EXACT_CONTRACT",
    }


def _entry_schedule_hash(
    data: Any,
    signals: ladder.SignalData,
    curve: ladder.Curve,
    left: int,
    right: int,
) -> str:
    rows = np.flatnonzero(signals.entry_mult[left:right] > 0) + left
    digest = hashlib.sha256()
    digest.update(json.dumps(dataclasses.asdict(curve), sort_keys=True).encode())
    for row in rows:
        digest.update(
            f"{int(data.ts[row])}:{float(signals.entry_mult[row]):.12g}\n".encode()
        )
    return digest.hexdigest()


def _tradier_dc4h_boundary_series(
    data: Any, side: str
) -> tuple[np.ndarray, bool]:
    """Return the live TRB/TRC 4h boundary on the execution clock.

    Production ``ExecutionData`` carries a data-contract marker and therefore
    fails closed on a missing boundary. Small synthetic unit fixtures without
    that marker keep the safety lane disabled unless they explicitly provide
    the matching Donchian field.
    """
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    key = "dc_low_4h" if side == "LONG" else "dc_high_4h"
    z = getattr(data, "z", None)
    files = set(getattr(z, "files", ())) if z is not None else set()
    enforced = hasattr(data, "contract") or key in files
    if key not in files:
        return np.zeros(len(data.ts), dtype=np.float64), enforced
    raw = np.asarray(z[key], dtype=np.float64)
    indices = np.asarray(
        getattr(data, "full_indices", np.arange(len(data.ts))),
        dtype=np.int64,
    )
    if len(indices) != len(data.ts) or (len(indices) and int(indices[-1]) >= len(raw)):
        raise ValueError(f"{key} cannot map to the execution clock")
    return np.ascontiguousarray(raw[indices], dtype=np.float64), enforced


def _dc4h_entry_allowed(price: float, level: float, *, is_long: bool) -> bool:
    """Mirror ``dc_4h_boundary_breached(..., require_level=True)``."""
    valid = math.isfinite(price) and price > 0.0 and math.isfinite(level) and level > 0.0
    if not valid:
        return False
    return price > level if is_long else price < level


def _dc4h_held_breached(price: float, level: float, *, is_long: bool) -> bool:
    """Mirror the held-position check; missing levels never invent exits."""
    valid = math.isfinite(price) and price > 0.0 and math.isfinite(level) and level > 0.0
    return bool(valid and (price <= level if is_long else price >= level))


def simulate(
    data: Any,
    entry_signals: ladder.SignalData,
    curve: ladder.Curve,
    exit_book: ExitBook,
    left: int,
    right: int,
    commission_rate: float,
    slippage_rate: float,
    *,
    side: str,
    profit_gate_pct: float = 0.0,
    partial_exit_fraction: float = 1.0,
    runner_exit_book: ExitBook | None = None,
) -> dict[str, Any]:
    """Run one exit over a frozen ladder request schedule.

    The exit may alter how much capacity is available at later ladder requests;
    it may not change their timestamps, multipliers, or semantics.  Reclaim is
    a persistent resting obligation filled on first OHLC touch (or adverse gap
    open), not a close-cross signal that can forget a runaway move.
    """
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    if right - left < 100:
        raise ValueError("simulation window too short")
    if not 0 < partial_exit_fraction <= 1:
        raise ValueError("partial_exit_fraction must be in (0,1]")
    for book in (exit_book, runner_exit_book):
        reset = getattr(book, "reset", None)
        if callable(reset):
            reset()
    side_sign = 1.0 if side == "LONG" else -1.0
    is_long = side == "LONG"
    cash = ladder.ACCOUNT_EQUITY
    qty = 0.0
    average_entry = math.nan
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    exit_fill_row = -1
    pending: dict[str, Any] | None = None
    peak_equity = ladder.ACCOUNT_EQUITY
    minimum_equity = ladder.ACCOUNT_EQUITY
    peak_post_fill_notional = 0.0
    max_dd = requested = filled = weighted = 0.0
    held = clamps = entry_fills = exit_fills = reclaim = lower = 0
    normal_exit_fills = emergency_exit_fills = 0
    normal_exit_pnl_usd = emergency_exit_pnl_usd = 0.0
    signals_seen = rejected_profit = future_sources = beyond = 0
    partial_exit_fills = runner_exit_fills = clip_reclaims = 0
    clip_obligations: list[dict[str, float]] = []
    ledger: list[dict[str, Any]] = []
    dc4h_entry_blocks = dc4h_safety_exit_fills = 0
    dc4h_level, dc4h_enforced = _tradier_dc4h_boundary_series(data, side)

    def active() -> bool:
        return side_sign * qty > 1e-12

    def mark_equity(px: float) -> float:
        return cash + qty * px

    def update_book(
        book: ExitBook | None,
        row: int,
        close: float,
    ) -> ExitDecision | None:
        if book is None:
            return None
        positional = getattr(book, "update_with_position", None)
        if callable(positional):
            gain_pct = (
                side_sign * (close - average_entry) / average_entry * 100.0
                if active()
                and math.isfinite(average_entry)
                and average_entry > 0
                else -math.inf
            )
            return positional(
                row,
                active=active(),
                entry_price=average_entry,
                current_gain_pct=gain_pct,
            )
        return book.update(row, active=active())

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None:
            if int(pending["signal_index"]) + 1 != i:
                raise RuntimeError("signal did not fill on exact next RTH row")
            if pending["kind"] == "exit" and active():
                px = op * (1.0 - side_sign * slippage_rate)
                close_qty = abs(qty)
                notional = close_qty * px
                realized_exit_pnl = (
                    side_sign * close_qty * (px - average_entry)
                    - commission_rate
                    * (close_qty * average_entry + notional)
                )
                cash += side_sign * (
                    notional - side_sign * commission_rate * notional
                )
                safety_exit = bool(pending.get("safety_exit", False))
                if safety_exit:
                    # The live hard-safety close is outside the selected exit
                    # strategy and therefore creates no mandatory strategy
                    # reclaim obligation.
                    prior_exit_notional = 0.0
                    last_exit_fill = math.nan
                    reclaim_level = math.nan
                else:
                    prior_exit_notional = min(ladder.CAPACITY, notional)
                    last_exit_fill = px
                    ref = float(pending["ref"])
                    reclaim_level = max(px, ref) if is_long else min(px, ref)
                qty = 0.0
                average_entry = math.nan
                gap_seen = False
                exit_fill_row = i
                exit_fills += 1
                if "EMERGENCY" in str(pending["reason"]):
                    emergency_exit_fills += 1
                    emergency_exit_pnl_usd += realized_exit_pnl
                    dc4h_safety_exit_fills += int(safety_exit)
                else:
                    normal_exit_fills += 1
                    normal_exit_pnl_usd += realized_exit_pnl
                runner_exit_fills += int(
                    pending["reason"].startswith("E02_DONCHIAN")
                )
                clip_obligations.clear()
                ledger.append(
                    {
                        "type": "EXIT",
                        "signal_index": int(pending["signal_index"]),
                        "fill_index": i,
                        "reason": pending["reason"],
                        "signal_ts": int(data.ts[pending["signal_index"]]),
                        "fill_ts": int(data.ts[i]),
                        "fill_price": px,
                        "realized_exit_pnl_usd": realized_exit_pnl,
                        "source_timestamps": pending["sources"],
                    }
                )
            elif pending["kind"] == "partial_exit" and active():
                px = op * (1.0 - side_sign * slippage_rate)
                close_qty = min(
                    abs(qty),
                    abs(qty) * float(pending["fraction"]),
                )
                notional = close_qty * px
                cash += side_sign * (
                    notional - side_sign * commission_rate * notional
                )
                qty -= side_sign * close_qty
                ref = float(pending["ref"])
                clip_obligations.append(
                    {
                        "level": max(px, ref) if is_long else min(px, ref),
                        "notional": notional,
                        "exit_fill_index": i,
                    }
                )
                partial_exit_fills += 1
                exit_fills += 1
                ledger.append(
                    {
                        "type": "PARTIAL_EXIT",
                        "signal_index": int(pending["signal_index"]),
                        "fill_index": i,
                        "reason": pending["reason"],
                        "fraction": float(pending["fraction"]),
                        "signal_ts": int(data.ts[pending["signal_index"]]),
                        "fill_ts": int(data.ts[i]),
                        "fill_price": px,
                        "filled_notional_usd": notional,
                        "source_timestamps": pending["sources"],
                    }
                )
            elif pending["kind"] == "entry":
                was_active = active()
                px = op * (1.0 + side_sign * slippage_rate)
                level = float(dc4h_level[i])
                if dc4h_enforced and not _dc4h_entry_allowed(
                    px, level, is_long=is_long
                ):
                    dc4h_entry_blocks += 1
                    ledger.append(
                        {
                            "type": "ENTRY_BLOCK",
                            "signal_index": int(pending["signal_index"]),
                            "fill_index": i,
                            "reason": "ENTRY_DC4H_SAFETY",
                            "fill_ts": int(data.ts[i]),
                            "attempt_price": px,
                            "dc4h_level": level,
                        }
                    )
                    pending = None
                    continue
                current = abs(qty) * px
                target = float(pending["requested_notional"])
                want = (
                    max(0.0, target - current)
                    if pending["absolute_target"]
                    else target
                )
                actual = min(want, max(0.0, ladder.CAPACITY - current))
                requested += max(0.0, want)
                filled += actual
                clamps += int(actual + 1e-9 < want)
                if actual > 0:
                    add_qty = actual / px
                    prior_abs = abs(qty)
                    new_abs = prior_abs + add_qty
                    average_entry = (
                        px
                        if not math.isfinite(average_entry) or prior_abs <= 0
                        else (average_entry * prior_abs + px * add_qty) / new_abs
                    )
                    cash -= side_sign * actual + commission_rate * actual
                    qty += side_sign * add_qty
                    post_fill_notional = abs(qty) * px
                    peak_post_fill_notional = max(
                        peak_post_fill_notional, post_fill_notional
                    )
                    entry_fills += 1
                    lower += int(
                        pending["reason"] in {"ladder_lower", "ladder_higher"}
                    )
                    reclaim += int(pending["reason"] == "reclaim")
                    ledger.append(
                        {
                            "type": (
                                "ENTRY"
                                if was_active or exit_fill_row < left
                                else "REENTER"
                            ),
                            "signal_index": int(pending["signal_index"]),
                            "fill_index": i,
                            "reason": pending["reason"],
                            "fill_ts": int(data.ts[i]),
                            "fill_price": px,
                            "filled_notional_usd": actual,
                            "parent_exit_index": (
                                None
                                if was_active or exit_fill_row < left
                                else exit_fill_row
                            ),
                        }
                    )
                    if abs(qty) * px > ladder.CAPACITY + 1e-6:
                        raise RuntimeError("entry capacity breach")
            pending = None

        # Persistent zero-buffer reclaim.  This is checked before new signals
        # so the strategy cannot remain flat after price trades through the
        # stored exit/top level.
        if (
            not active()
            and i > exit_fill_row >= left
            and math.isfinite(reclaim_level)
        ):
            reclaim_px = resting_reclaim_fill(
                is_long=is_long,
                reclaim_level=reclaim_level,
                bar_open=op,
                bar_high=float(data.high[i]),
                bar_low=float(data.low[i]),
                slippage_bps=slippage_rate * 10_000.0,
            )
            if reclaim_px is not None:
                level = float(dc4h_level[i])
                if dc4h_enforced and not _dc4h_entry_allowed(
                    reclaim_px, level, is_long=is_long
                ):
                    dc4h_entry_blocks += 1
                    continue
                target = max(ladder.BASE_UNIT, prior_exit_notional)
                actual = min(target, ladder.CAPACITY)
                requested += target
                filled += actual
                clamps += int(actual + 1e-9 < target)
                cash -= side_sign * actual + commission_rate * actual
                qty = side_sign * actual / reclaim_px
                peak_post_fill_notional = max(
                    peak_post_fill_notional, abs(qty) * reclaim_px
                )
                average_entry = reclaim_px
                entry_fills += 1
                reclaim += 1
                ledger.append(
                    {
                        "type": "REENTER",
                        "signal_index": i,
                        "fill_index": i,
                        "reason": "mandatory_zero_buffer_reclaim",
                        "fill_ts": int(data.ts[i]),
                        "fill_price": reclaim_px,
                        "filled_notional_usd": actual,
                        "parent_exit_index": exit_fill_row,
                    }
                )
                last_exit_fill = math.nan
                reclaim_level = math.nan

        # A partial WT clip owns an independent resting reclaim even while the
        # E02 runner remains open.  Reclaims are bounded by the same capacity.
        remaining_clip_obligations: list[dict[str, float]] = []
        for obligation in clip_obligations:
            if i <= int(obligation["exit_fill_index"]):
                remaining_clip_obligations.append(obligation)
                continue
            reclaim_px = resting_reclaim_fill(
                is_long=is_long,
                reclaim_level=float(obligation["level"]),
                bar_open=op,
                bar_high=float(data.high[i]),
                bar_low=float(data.low[i]),
                slippage_bps=slippage_rate * 10_000.0,
            )
            if reclaim_px is None:
                remaining_clip_obligations.append(obligation)
                continue
            level = float(dc4h_level[i])
            if dc4h_enforced and not _dc4h_entry_allowed(
                reclaim_px, level, is_long=is_long
            ):
                dc4h_entry_blocks += 1
                remaining_clip_obligations.append(obligation)
                continue
            current = abs(qty) * reclaim_px
            actual = min(
                float(obligation["notional"]),
                max(0.0, ladder.CAPACITY - current),
            )
            requested += float(obligation["notional"])
            filled += actual
            clamps += int(actual + 1e-9 < float(obligation["notional"]))
            if actual > 0:
                add_qty = actual / reclaim_px
                prior_abs = abs(qty)
                new_abs = prior_abs + add_qty
                average_entry = (
                    reclaim_px
                    if not math.isfinite(average_entry) or prior_abs <= 0
                    else (
                        average_entry * prior_abs + reclaim_px * add_qty
                    )
                    / new_abs
                )
                cash -= side_sign * actual + commission_rate * actual
                qty += side_sign * add_qty
                peak_post_fill_notional = max(
                    peak_post_fill_notional, abs(qty) * reclaim_px
                )
                entry_fills += 1
                reclaim += 1
                clip_reclaims += 1
                ledger.append(
                    {
                        "type": "REENTER",
                        "signal_index": i,
                        "fill_index": i,
                        "reason": "partial_clip_reclaim",
                        "fill_ts": int(data.ts[i]),
                        "fill_price": reclaim_px,
                        "filled_notional_usd": actual,
                        "parent_exit_index": int(
                            obligation["exit_fill_index"]
                        ),
                    }
                )
            # A touched but capacity-clipped obligation is complete. Keeping it
            # alive would create repeated free attempts at the same historical
            # level and violate the one-obligation/one-fill contract.
        clip_obligations = remaining_clip_obligations

        equity = mark_equity(close)
        minimum_equity = min(minimum_equity, equity)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_dd = max(max_dd, 100.0 * (peak_equity - equity) / peak_equity)
        held += int(active())
        weighted += min(ladder.CAPACITY, abs(qty) * close) / ladder.CAPACITY

        # Live owns this hard close before every configurable strategy exit.
        # It is intentionally not subject to the selected recipe's profit gate.
        if (
            dc4h_enforced
            and active()
            and _dc4h_held_breached(
                close, float(dc4h_level[i]), is_long=is_long
            )
            and i + 1 < right
        ):
            level = float(dc4h_level[i])
            pending = {
                "kind": "exit",
                "signal_index": i,
                "reason": (
                    "EMERGENCY_DC4H_BREACH_"
                    f"{'dc_low_4h' if is_long else 'dc_high_4h'}="
                    f"{level:.8f},price={close:.8f}"
                ),
                "ref": level,
                "sources": {"4h_boundary_observed_ts": int(data.ts[i])},
                "safety_exit": True,
            }
            continue

        runner_decision = update_book(runner_exit_book, i, close)
        decision = update_book(exit_book, i, close)
        if i + 1 >= right:
            continue
        if runner_decision is not None and active():
            signals_seen += 1
            future_sources += sum(
                int(int(source) > int(data.ts[i]))
                for source in runner_decision.source_timestamps.values()
            )
            pending = {
                "kind": "exit",
                "signal_index": i,
                "reason": runner_decision.reason,
                "ref": runner_decision.reclaim_reference,
                "sources": runner_decision.source_timestamps,
            }
            continue
        if decision is not None and active():
            signals_seen += 1
            future_sources += sum(
                int(int(source) > int(data.ts[i]))
                for source in decision.source_timestamps.values()
            )
            gain_pct = (
                side_sign * (close - average_entry) / average_entry * 100.0
                if math.isfinite(average_entry) and average_entry > 0
                else -math.inf
            )
            if gain_pct + 1e-12 >= profit_gate_pct:
                pending = {
                    "kind": (
                        "exit"
                        if partial_exit_fraction >= 1.0
                        else "partial_exit"
                    ),
                    "signal_index": i,
                    "reason": decision.reason,
                    "ref": decision.reclaim_reference,
                    "sources": decision.source_timestamps,
                    "fraction": partial_exit_fraction,
                }
                continue
            rejected_profit += 1
        if active():
            if entry_signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "signal_index": i,
                    "requested_notional": (
                        ladder.BASE_UNIT * float(entry_signals.entry_mult[i])
                    ),
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_add",
                }
        elif math.isfinite(last_exit_fill):
            gap_seen |= (
                float(data.low[i]) < last_exit_fill
                if is_long
                else float(data.high[i]) > last_exit_fill
            )
            if entry_signals.entry_mult[i] > 0 and gap_seen:
                pending = {
                    "kind": "entry",
                    "signal_index": i,
                    "requested_notional": (
                        ladder.BASE_UNIT * float(entry_signals.entry_mult[i])
                    ),
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_lower" if is_long else "ladder_higher",
                }
            elif i > exit_fill_row and (
                close > reclaim_level if is_long else close < reclaim_level
            ):
                beyond += 1
        elif entry_signals.entry_mult[i] > 0:
            pending = {
                "kind": "entry",
                "signal_index": i,
                "requested_notional": (
                    ladder.BASE_UNIT * float(entry_signals.entry_mult[i])
                ),
                "absolute_target": curve.semantics == "target",
                "reason": "initial_ladder",
            }

    if active():
        px = float(data.close[right - 1]) * (
            1.0 - side_sign * slippage_rate
        )
        notional = abs(qty) * px
        cash += side_sign * (
            notional - side_sign * commission_rate * notional
        )
    minimum_equity = min(minimum_equity, cash)
    pnl = cash - ladder.ACCOUNT_EQUITY
    bh_entry = float(data.open[left]) * (
        1.0 + side_sign * slippage_rate
    )
    bh_exit = float(data.close[right - 1]) * (
        1.0 - side_sign * slippage_rate
    )
    bh_pnl = (
        ladder.BASE_UNIT * side_sign * (bh_exit - bh_entry) / bh_entry
        - 2.0 * commission_rate * ladder.BASE_UNIT
    )
    rows = right - left
    return {
        "capital_return_pct": 100.0 * pnl / ladder.BASE_UNIT,
        "bh_capital_return_pct": 100.0 * bh_pnl / ladder.BASE_UNIT,
        "strategy_bh_multiple": pnl / bh_pnl if bh_pnl > 1e-12 else None,
        "alpha_vs_bh_pp": 100.0 * (pnl - bh_pnl) / ladder.BASE_UNIT,
        "binary_tim_pct": 100.0 * held / rows,
        "exposure_weighted_tim_pct": 100.0 * weighted / rows,
        "max_drawdown_account_pct": max_dd,
        "minimum_account_equity_usd": minimum_equity,
        "insolvent": bool(minimum_equity <= 0.0),
        "peak_post_fill_notional_usd": peak_post_fill_notional,
        "entry_capacity_breach": bool(
            peak_post_fill_notional > ladder.CAPACITY + 1e-6
        ),
        "signals": signals_seen,
        "rejected_by_profit_gate": rejected_profit,
        "exit_fills": exit_fills,
        # Full ``exit`` is the only branch that sets qty to zero.  Partial
        # clips deliberately remain broad action evidence and never count.
        "real_close_trades": normal_exit_fills + emergency_exit_fills,
        "terminal_lifecycle_closes": normal_exit_fills + emergency_exit_fills,
        "normal_exit_fills": normal_exit_fills,
        "emergency_exit_fills": emergency_exit_fills,
        "normal_exit_pnl_usd": normal_exit_pnl_usd,
        "emergency_exit_pnl_usd": emergency_exit_pnl_usd,
        "emergency_exit_share": (
            emergency_exit_fills / exit_fills if exit_fills else 0.0
        ),
        "partial_exit_fills": partial_exit_fills,
        "runner_exit_fills": runner_exit_fills,
        "clip_reclaim_reentries": clip_reclaims,
        "clip_obligations_unfilled_at_end": len(clip_obligations),
        "entry_fills": entry_fills,
        "lower_or_higher_reentries": lower,
        "reclaim_reentries": reclaim,
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "fill_ratio": filled / requested if requested > 0 else 1.0,
        "clamp_count": clamps,
        "future_htf_source_count": future_sources,
        "bars_flat_beyond_reclaim": beyond,
        "dc4h_entry_blocks": dc4h_entry_blocks,
        "dc4h_safety_exit_fills": dc4h_safety_exit_fills,
        "dc4h_safety_enforced": bool(dc4h_enforced),
        "mandatory_reclaim_execution": "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN",
        "open_reclaim_obligations": (
            len(clip_obligations)
            + int(
                not active()
                and exit_fill_row >= left
                and math.isfinite(reclaim_level)
            )
        ),
        "frozen_entry_schedule_sha256": _entry_schedule_hash(
            data, entry_signals, curve, left, right
        ),
        "entry_request_count": int(
            np.count_nonzero(entry_signals.entry_mult[left:right] > 0)
        ),
        "side": side,
        "rows": rows,
        "event_ledger": ledger,
    }


def wt_grid() -> list[WtMtfParams]:
    """Exact bounded 256-arm completed-HTF WT exhaustion grid."""
    timeframes = ("15m", "1h", "4h", "D", "W")
    return [
        WtMtfParams(
            timeframes=timeframes,
            min_against_tfs=min_against,
            extreme=extreme,
            velocity=velocity,
            profit_gate_pct=profit_gate,
        )
        for min_against in (1, 2, 3, 4)
        for extreme in (45.0, 55.0, 65.0, 75.0)
        for velocity in (0.0, 0.25, 0.5, 1.0)
        for profit_gate in (0.0, 0.25, 0.5, 1.0)
    ]


def gr_opposite_grid() -> list[GrOppositeParams]:
    """Exact 432-arm explicit per-TF Golden Rule opposite-vote grid."""
    timeframes = ("15m", "1h", "4h", "D")
    weight_profiles = (
        (1.0, 1.0, 1.0, 1.0),
        (0.5, 1.0, 2.0, 3.0),
        (0.0, 1.0, 2.0, 4.0),
    )
    rows = [
        GrOppositeParams(
            timeframes=timeframes,
            min_tfs=min_tfs,
            min_indicators=min_indicators,
            min_weighted_score=score,
            weights=weights,
            profit_gate_pct=profit_gate,
        )
        for min_tfs in (1, 2, 3)
        for min_indicators in (1, 2)
        for score in (4.0, 6.0, 8.0, 10.0, 12.0, 15.5)
        for weights in weight_profiles
        for profit_gate in (0.0, 0.25, 0.5, 1.0)
    ]
    assert len(rows) == 432
    return rows


def chandelier_grid() -> list[ChandelierParams]:
    """Exact 160-arm standard completed-HTF E01 grid."""
    rows = [
        ChandelierParams(
            timeframe=timeframe,
            lookback=lookback,
            atr_mult=atr_mult,
            profit_gate_pct=profit_gate,
        )
        for timeframe in ("4h", "D")
        for lookback in (10, 20, 30, 55)
        for atr_mult in (1.5, 2.0, 2.5, 3.0, 4.0)
        for profit_gate in (0.0, 0.25, 0.5, 1.0)
    ]
    assert len(rows) == 160
    return rows


def structural_grid() -> list[tuple[StructuralWtParams, float, int]]:
    """Exact registry grid: 768 coherent structural/profit blocks.

    ``StructuralWtParams.max_wait_1h`` is historically named but counts
    completed confirmation bars. Convert declared wall-clock hours to the
    selected retest timeframe so 12h means 48 completed 15m bars or 12
    completed 1h bars.
    """
    rows: list[tuple[StructuralWtParams, float, int]] = []
    for arm_tf in ("1h", "4h"):
        for confirm_tf in ("15m", "1h"):
            bars_per_hour = 4 if confirm_tf == "15m" else 1
            for rebound in (0.5, 1.0, 2.0, 4.0):
                for lookback in (3, 4, 6, 10):
                    for wait_hours in (12, 20, 30, 48):
                        for profit_gate in (0.25, 0.5, 1.0):
                            rows.append(
                                (
                                    StructuralWtParams(
                                        arm_tf=arm_tf,
                                        confirm_tf=confirm_tf,
                                        rebound_atr=rebound,
                                        prebreak_lookback=lookback,
                                        max_wait_1h=wait_hours * bars_per_hour,
                                    ),
                                    profit_gate,
                                    wait_hours,
                                )
                            )
    assert len(rows) == 768
    return rows


def bottom_delayed_grid() -> list[tuple[StructuralWtParams, int]]:
    """Family B: adverse break arms, a later lower top exits."""
    rows: list[tuple[StructuralWtParams, int]] = []
    for arm_tf in ("1h", "4h"):
        for confirm_tf in ("5m", "15m", "1h"):
            bars_per_hour = {"5m": 12, "15m": 4, "1h": 1}[confirm_tf]
            for mode in ("PRICE_ONLY", "WT_ONLY", "AND", "OR"):
                for confirm_bars in (1, 2):
                    for rebound in (0.25, 0.5, 1.0):
                        for lookback in (4, 6):
                            for wait_hours in (12, 24, 48):
                                rows.append(
                                    (
                                        StructuralWtParams(
                                            arm_tf=arm_tf,
                                            confirm_tf=confirm_tf,
                                            rebound_atr=rebound,
                                            prebreak_lookback=lookback,
                                            max_wait_1h=(
                                                wait_hours * bars_per_hour
                                            ),
                                            confirmation_mode=mode,
                                            confirmation_bars=confirm_bars,
                                        ),
                                        wait_hours,
                                    )
                                )
    assert len(rows) == 864
    return rows


def bottom_delayed_grid_extended() -> list[tuple[StructuralWtParams, int]]:
    """Sparse breadth for the lower-low then lower-price/WT-top sequence.

    The additions target dimensions absent from the seed grid: a 15m or daily
    structural arm, three-bar confirmation, six/seventy-two-hour waits, and
    shallow/deep rebound thresholds.  The set is bounded and compiled rather
    than a field-by-field Cartesian explosion.
    """
    rows: list[tuple[StructuralWtParams, int]] = []

    def add(
        *,
        arm_tfs: tuple[str, ...],
        confirm_tfs: tuple[str, ...],
        modes: tuple[str, ...],
        confirm_bars_values: tuple[int, ...],
        rebounds: tuple[float, ...],
        lookbacks: tuple[int, ...],
        waits: tuple[int, ...],
    ) -> None:
        for arm_tf in arm_tfs:
            for confirm_tf in confirm_tfs:
                bars_per_hour = {
                    "5m": 12,
                    "15m": 4,
                    "1h": 1,
                    "4h": 0.25,
                }[confirm_tf]
                for mode in modes:
                    for confirm_bars in confirm_bars_values:
                        for rebound in rebounds:
                            for lookback in lookbacks:
                                for wait_hours in waits:
                                    rows.append(
                                        (
                                            StructuralWtParams(
                                                arm_tf=arm_tf,
                                                confirm_tf=confirm_tf,
                                                rebound_atr=rebound,
                                                prebreak_lookback=lookback,
                                                max_wait_1h=max(
                                                    3,
                                                    int(
                                                        wait_hours
                                                        * bars_per_hour
                                                    ),
                                                ),
                                                confirmation_mode=mode,
                                                confirmation_bars=(
                                                    confirm_bars
                                                ),
                                            ),
                                            wait_hours,
                                        )
                                    )

    add(
        arm_tfs=("15m",),
        confirm_tfs=("5m", "15m"),
        modes=("PRICE_ONLY", "WT_ONLY", "AND", "OR"),
        confirm_bars_values=(1, 3),
        rebounds=(0.125, 0.5, 1.5),
        lookbacks=(3, 8),
        waits=(6, 24, 72),
    )
    add(
        arm_tfs=("D",),
        confirm_tfs=("15m", "1h"),
        modes=("AND", "OR"),
        confirm_bars_values=(1, 3),
        rebounds=(0.125, 0.5, 1.5),
        lookbacks=(3, 8),
        waits=(6, 24, 72),
    )
    add(
        arm_tfs=("1h", "4h"),
        confirm_tfs=("5m", "15m", "1h"),
        modes=("PRICE_ONLY", "WT_ONLY", "AND", "OR"),
        confirm_bars_values=(3,),
        rebounds=(0.125, 1.5),
        lookbacks=(3, 8),
        waits=(6, 72),
    )
    assert len(rows) == 624
    assert len({dataclasses.astuple(params) for params, _ in rows}) == len(rows)
    return rows


def bottom_emergency_variants() -> list[tuple[str, dict[str, Any]]]:
    """Rare-brake overlays applied only to discovery-pruned family-B rows."""
    return [
        (
            "ADVERSE_ATR_2",
            {
                "emergency_modes": ("ADVERSE_ATR",),
                "emergency_adverse_atr": 2.0,
            },
        ),
        (
            "ADVERSE_ATR_3",
            {
                "emergency_modes": ("ADVERSE_ATR",),
                "emergency_adverse_atr": 3.0,
            },
        ),
        (
            "ADVERSE_ATR_4",
            {
                "emergency_modes": ("ADVERSE_ATR",),
                "emergency_adverse_atr": 4.0,
            },
        ),
        (
            "ADVERSE_ATR_6",
            {
                "emergency_modes": ("ADVERSE_ATR",),
                "emergency_adverse_atr": 6.0,
            },
        ),
        (
            "ADVERSE_STDEV_2.5",
            {
                "emergency_modes": ("ADVERSE_STDEV",),
                "emergency_adverse_stdev": 2.5,
            },
        ),
        (
            "ADVERSE_STDEV_3.5",
            {
                "emergency_modes": ("ADVERSE_STDEV",),
                "emergency_adverse_stdev": 3.5,
            },
        ),
        (
            "ADVERSE_STDEV_5",
            {
                "emergency_modes": ("ADVERSE_STDEV",),
                "emergency_adverse_stdev": 5.0,
            },
        ),
        (
            "ADVERSE_STDEV_7",
            {
                "emergency_modes": ("ADVERSE_STDEV",),
                "emergency_adverse_stdev": 7.0,
            },
        ),
        ("MAX_WAIT", {"emergency_modes": ("MAX_WAIT",)}),
        (
            "CONTINUED_3",
            {
                "emergency_modes": ("CONTINUED",),
                "emergency_continued_bars": 3,
            },
        ),
        (
            "CONTINUED_5",
            {
                "emergency_modes": ("CONTINUED",),
                "emergency_continued_bars": 5,
            },
        ),
        (
            "CONTINUED_8",
            {
                "emergency_modes": ("CONTINUED",),
                "emergency_continued_bars": 8,
            },
        ),
    ]


def bottom_structural_v2_grid() -> list[tuple[StructuralWtParams, int]]:
    """Bounded v2: qualify the arm, then sell only after the later top.

    This is deliberately not another field-by-field expansion.  Five fixed
    causal arm profiles cover ATR displacement, rolling-STDEV displacement,
    and prior-window DC/support breaks.  All confirmation geometry is mirrored
    by side in the compiled scanner.
    """
    arm_profiles = (
        ("ATR", 0.5),
        ("ATR", 1.0),
        ("STDEV", 1.0),
        ("STDEV", 1.5),
        ("DC_SUPPORT", 0.0),
    )
    rows: list[tuple[StructuralWtParams, int]] = []
    for arm_tf in ("15m", "1h"):
        for arm_mode, arm_threshold in arm_profiles:
            for confirm_tf in ("5m", "15m", "1h"):
                bars_per_hour = {"5m": 12, "15m": 4, "1h": 1}[confirm_tf]
                for confirmation_mode in ("PRICE_ONLY", "WT_ONLY", "AND", "OR"):
                    for confirmation_bars in (1, 2):
                        for rebound_atr in (0.5, 1.0):
                            for wait_hours in (24, 48):
                                rows.append(
                                    (
                                        StructuralWtParams(
                                            arm_tf=arm_tf,
                                            confirm_tf=confirm_tf,
                                            rebound_atr=rebound_atr,
                                            prebreak_lookback=6,
                                            max_wait_1h=wait_hours
                                            * bars_per_hour,
                                            confirmation_mode=confirmation_mode,
                                            confirmation_bars=confirmation_bars,
                                            arm_break_mode=arm_mode,
                                            arm_break_threshold=arm_threshold,
                                        ),
                                        wait_hours,
                                    )
                                )
    assert len(rows) == 960
    assert len({dataclasses.astuple(params) for params, _ in rows}) == 960
    return rows


def prune_bottom_structural_v2_bases(
    rows: list[dict[str, Any]],
    *,
    exposure_min_pct: float,
    exposure_max_pct: float,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Freeze emergency bases using discovery folds only."""
    if limit != 4:
        raise ValueError("bottom structural v2 requires four B bases")

    def rank(row: dict[str, Any]) -> tuple[Any, ...]:
        nested = row.get("nested")
        discovery = nested["discovery"] if nested else row["metrics"]
        robust = (
            bool(nested["robust_discovery_all_folds"])
            if nested
            else all(
                evidence["alpha_vs_bh_pp"] > 0
                and evidence["alpha_vs_same_entry_e02_pp"] > 0
                and exposure_min_pct <= evidence["weighted_tim_pct"] <= exposure_max_pct
                and not evidence["insolvent"]
                and not evidence["entry_capacity_breach"]
                and evidence["future_htf_source_count"] == 0
                and evidence["bars_flat_beyond_reclaim"] == 0
                and evidence["exit_fills"] > 0
                for evidence in row["fold_evidence"]
            )
        )
        return (
            not robust,
            int(discovery["exit_fills"]) <= 0,
            abs(
                float(
                    discovery["exposure_weighted_tim_pct_row_weighted"]
                )
                - (exposure_min_pct + exposure_max_pct) / 2.0
            ),
            -float(
                nested["discovery_alpha_vs_same_entry_e02_pp"]
                if nested else row["alpha_vs_same_entry_e02_pp"]
            ),
            -float(
                nested["discovery_alpha_vs_bh_pp"]
                if nested else row["alpha_vs_bh_pp"]
            ),
            float(discovery["max_drawdown_account_pct_max"]),
            hashlib.sha256(
                json.dumps(row["params"], sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )

    if len(rows) < limit:
        raise ValueError("bottom structural v2 lacks B bases")
    return sorted(rows, key=rank)[:limit]


def bottom_structural_v2_emergency_variants(
) -> list[tuple[str, dict[str, Any]]]:
    """Only rare, late brakes; tighter variants already failed generation 1."""
    return [
        (
            "ADVERSE_ATR_6",
            {
                "emergency_modes": ("ADVERSE_ATR",),
                "emergency_adverse_atr": 6.0,
            },
        ),
        (
            "ADVERSE_STDEV_7",
            {
                "emergency_modes": ("ADVERSE_STDEV",),
                "emergency_adverse_stdev": 7.0,
            },
        ),
        (
            "CONTINUED_8",
            {
                "emergency_modes": ("CONTINUED",),
                "emergency_continued_bars": 8,
            },
        ),
    ]


def prune_bottom_b_extended_bases(
    rows: list[dict[str, Any]],
    *,
    exposure_min_pct: float,
    exposure_max_pct: float,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Freeze family-C bases from discovery evidence only.

    Untouched validation fields are intentionally never read here.  Family C
    may therefore compare brakes over a bounded B shortlist without leaking
    the final fold into base selection.
    """
    if limit != 8:
        raise ValueError("C_EXT contract requires exactly eight B bases")

    def rank(row: dict[str, Any]) -> tuple[Any, ...]:
        nested = row.get("nested")
        discovery = nested["discovery"] if nested else row["metrics"]
        robust = (
            bool(nested["robust_discovery_all_folds"])
            if nested
            else all(
                evidence["alpha_vs_bh_pp"] > 0
                and evidence["alpha_vs_same_entry_e02_pp"] > 0
                and exposure_min_pct <= evidence["weighted_tim_pct"] <= exposure_max_pct
                and not evidence["insolvent"]
                and not evidence["entry_capacity_breach"]
                and evidence["future_htf_source_count"] == 0
                and evidence["bars_flat_beyond_reclaim"] == 0
                and evidence["exit_fills"] > 0
                for evidence in row["fold_evidence"]
            )
        )
        return (
            not robust,
            int(discovery["exit_fills"]) <= 0,
            abs(
                float(
                    discovery["exposure_weighted_tim_pct_row_weighted"]
                )
                - (exposure_min_pct + exposure_max_pct) / 2.0
            ),
            -float(
                nested["discovery_alpha_vs_same_entry_e02_pp"]
                if nested else row["alpha_vs_same_entry_e02_pp"]
            ),
            -float(
                nested["discovery_alpha_vs_bh_pp"]
                if nested else row["alpha_vs_bh_pp"]
            ),
            float(discovery["max_drawdown_account_pct_max"]),
            hashlib.sha256(
                json.dumps(row["params"], sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )

    if len(rows) < limit:
        raise ValueError("C_EXT requires at least eight B candidates")
    return sorted(rows, key=rank)[:limit]


def bottom_emergency_grid() -> list[tuple[StructuralWtParams, int, str]]:
    """Family C: family-B wait plus one separately counted rare brake."""
    variants = [
        ("ADVERSE_ATR_2", ("ADVERSE_ATR",), 2.0, 0.0, 0),
        ("ADVERSE_ATR_3", ("ADVERSE_ATR",), 3.0, 0.0, 0),
        ("ADVERSE_ATR_4", ("ADVERSE_ATR",), 4.0, 0.0, 0),
        ("ADVERSE_STDEV_2.5", ("ADVERSE_STDEV",), 0.0, 2.5, 0),
        ("ADVERSE_STDEV_3.5", ("ADVERSE_STDEV",), 0.0, 3.5, 0),
        ("ADVERSE_STDEV_5", ("ADVERSE_STDEV",), 0.0, 5.0, 0),
        ("MAX_WAIT", ("MAX_WAIT",), 0.0, 0.0, 0),
        ("CONTINUED_3", ("CONTINUED",), 0.0, 0.0, 3),
        ("CONTINUED_5", ("CONTINUED",), 0.0, 0.0, 5),
    ]
    rows: list[tuple[StructuralWtParams, int, str]] = []
    for arm_tf in ("1h", "4h"):
        for confirm_tf in ("5m", "15m", "1h"):
            bars_per_hour = {"5m": 12, "15m": 4, "1h": 1}[confirm_tf]
            for mode in ("AND", "OR"):
                for wait_hours in (12, 24, 48):
                    for label, emergency, adverse_atr, adverse_sd, continued in variants:
                        rows.append(
                            (
                                StructuralWtParams(
                                    arm_tf=arm_tf,
                                    confirm_tf=confirm_tf,
                                    rebound_atr=0.5,
                                    prebreak_lookback=6,
                                    max_wait_1h=wait_hours * bars_per_hour,
                                    confirmation_mode=mode,
                                    confirmation_bars=1,
                                    emergency_modes=emergency,
                                    emergency_adverse_atr=adverse_atr,
                                    emergency_adverse_stdev=adverse_sd,
                                    emergency_continued_bars=continued,
                                ),
                                wait_hours,
                                label,
                            )
                        )
    assert len(rows) == 324
    return rows


def compiled_structural_candidate_count(family: str) -> int:
    """Return the number represented by the compiled family screen."""
    if family == "EXIT_STRUCTURAL_WT_LOWER_TOP":
        return len(structural_grid())
    if family == "BOTTOM_B_DELAYED_LOWER_TOP":
        return len(bottom_delayed_grid())
    if family == "BOTTOM_C_DELAYED_EMERGENCY":
        return len(bottom_emergency_grid())
    if family == "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED":
        return len(bottom_delayed_grid_extended())
    if family == "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED":
        return 8 * len(bottom_emergency_variants())
    if family == "BOTTOM_B_STRUCTURAL_V2":
        return len(bottom_structural_v2_grid())
    if family == "BOTTOM_C_STRUCTURAL_V2_EMERGENCY":
        return 4 * len(bottom_structural_v2_emergency_variants())
    raise ValueError(f"not a compiled structural family: {family}")


def partial_wt_grid() -> list[tuple[WtMtfParams, float]]:
    """Slower/stricter WT blocks paired with bounded E02-runner clips."""
    settings: list[WtMtfParams] = []
    groups = (
        ("15m", "1h"),
        ("1h", "4h"),
        ("15m", "1h", "4h"),
        ("1h", "4h", "D"),
    )
    profiles = (
        # (extreme, velocity, recent bars, require structure, strict vote)
        (65.0, 0.5, 8, False, False),
        (75.0, 1.0, 8, False, False),
        (75.0, 2.0, 16, False, False),
        (85.0, 1.0, 16, False, True),
        (75.0, 1.0, 16, True, False),
        (85.0, 2.0, 16, True, True),
    )
    for tfs in groups:
        for extreme, velocity, recent, structure, strict in profiles:
            settings.append(
                WtMtfParams(
                    timeframes=tfs,
                    min_against_tfs=(
                        len(tfs)
                        if strict
                        else max(1, (len(tfs) + 1) // 2)
                    ),
                    extreme=extreme,
                    velocity=velocity,
                    recent_extreme_bars=recent,
                    require_fast_structure=structure,
                    profit_gate_pct=0.5 if structure else 0.0,
                )
            )
    return [
        (params, fraction)
        for params in settings
        for fraction in (0.15, 0.25, 0.33, 0.50)
    ]


def _fold_contexts(
    artifact: Path,
    npz_dir: Path,
    *,
    fold_mode: str,
) -> tuple[dict[str, Any], Any, dict[str, Any], list[dict[str, Any]]]:
    source = json.loads((artifact / "result.json").read_text())
    manifest = source["manifest"]
    symbol = str(manifest["symbol"]).upper()
    side = str(manifest["side"]).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported frozen control side: {side!r}")
    data = ladder.top._load_execution(
        symbol,
        npz_dir,
        "2024-01-01",
        "ladder",
        None,
    )
    current_sha = hashlib.sha256(Path(data.path).read_bytes()).hexdigest()
    if current_sha != manifest["npz_sha256"]:
        data.z.close()
        raise RuntimeError(
            f"{symbol} NPZ hash drift: artifact={manifest['npz_sha256']} current={current_sha}"
        )
    if not data.contract["valid"]:
        # The isolated/versioned HAO recovery contains one independently
        # verified real 2026-07-14 discontinuity.  Accept it only when the
        # source artifact carries the exact hash/timestamp-bound receipt.
        receipt_path = artifact / "verified_jump_exception_receipt.json"
        accepted = False
        if (
            symbol == "HAO"
            and receipt_path.exists()
            and len(data.contract["errors"]) == 1
            and str(data.contract["errors"][0]).startswith(
                "unadjusted/corrupt price discontinuity:"
            )
        ):
            receipt = json.loads(receipt_path.read_text())
            # Match the receipt's full NPZ bar sequence.  ``data.close`` is
            # RTH-filtered and its adjacent rows can span an overnight gap.
            receipt_ts = np.asarray(data.z["timestamps"], dtype=np.int64)
            receipt_close = np.asarray(data.z["close"], dtype=np.float64)
            jumps = np.abs(np.diff(receipt_close) / receipt_close[:-1])
            jump_index = int(np.argmax(jumps)) + 1
            jump_ts = datetime.fromtimestamp(
                int(receipt_ts[jump_index]), tz=timezone.utc
            ).isoformat()
            jump_pct = float(jumps[jump_index - 1]) * 100.0
            accepted = bool(
                receipt.get("npz_sha256") == current_sha
                and receipt.get("verified_jump_ts") == jump_ts
                and math.isclose(
                    float(receipt.get("verified_jump_pct", math.nan)),
                    jump_pct,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                and str(receipt.get("scope", "")).startswith(
                    "isolated vector control only"
                )
            )
            if accepted:
                data.contract["valid"] = True
                data.contract["errors"] = []
                data.contract["warnings"].append(
                    "hash/timestamp-bound isolated HAO jump receipt honored"
                )
        if not accepted:
            data.z.close()
            raise RuntimeError(
                f"{symbol} quarantined: {data.contract['errors']}"
            )
    htfs = {
        tf: ladder.top._compress_htf(data, tf)
        for tf in ("5m", "15m", "1h", "4h", "D", "W")
    }
    folds = list(source["outer_folds"])
    if fold_mode == "latest":
        folds = folds[-1:]
    elif fold_mode == "discovery":
        if len(folds) < 2:
            raise ValueError("discovery mode requires a separate untouched final fold")
        folds = folds[:-1]
    contexts: list[dict[str, Any]] = []
    for fold in folds:
        left = ladder._date_index(data, fold["validation"][0])
        right = ladder._date_index(data, fold["validation"][1])
        curve = ladder.Curve(**fold["selected_curve"])
        signals = ladder._build_signals(
            data,
            htfs,
            curve,
            int(manifest["exit"]["n"]),
            side,
        )
        contexts.append(
            {
                "fold": int(fold["fold"]),
                "validation": list(fold["validation"]),
                "left": left,
                "right": right,
                "curve": curve,
                "signals": signals,
            }
        )
    return source, data, htfs, contexts


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = sum(int(row["rows"]) for row in rows)
    result = {
        "folds": len(rows),
        "capital_return_pct_sum": sum(float(r["capital_return_pct"]) for r in rows),
        "bh_capital_return_pct_sum": sum(
            float(r["bh_capital_return_pct"]) for r in rows
        ),
        "exposure_weighted_tim_pct_row_weighted": (
            sum(float(r["exposure_weighted_tim_pct"]) * int(r["rows"]) for r in rows)
            / max(1, total_rows)
        ),
        "max_drawdown_account_pct_max": max(
            (float(r["max_drawdown_account_pct"]) for r in rows), default=0.0
        ),
        "minimum_account_equity_usd": min(
            (float(r["minimum_account_equity_usd"]) for r in rows),
            default=ladder.ACCOUNT_EQUITY,
        ),
        "insolvent_folds": sum(bool(r["insolvent"]) for r in rows),
        "entry_capacity_breach": any(
            bool(r["entry_capacity_breach"]) for r in rows
        ),
        "peak_post_fill_notional_usd_max": max(
            (float(r["peak_post_fill_notional_usd"]) for r in rows),
            default=0.0,
        ),
        "requested_notional_usd": sum(
            float(r["requested_notional_usd"]) for r in rows
        ),
        "filled_notional_usd": sum(
            float(r["filled_notional_usd"]) for r in rows
        ),
        "clamp_count": sum(int(r["clamp_count"]) for r in rows),
        "exit_fills": sum(int(r["exit_fills"]) for r in rows),
        "real_close_trades": sum(int(r.get("real_close_trades", 0)) for r in rows),
        "terminal_lifecycle_closes": sum(int(r.get("terminal_lifecycle_closes", 0)) for r in rows),
        "normal_exit_fills": sum(
            int(r.get("normal_exit_fills", r["exit_fills"])) for r in rows
        ),
        "emergency_exit_fills": sum(
            int(r.get("emergency_exit_fills", 0)) for r in rows
        ),
        "normal_exit_pnl_usd": sum(
            float(r.get("normal_exit_pnl_usd", 0.0)) for r in rows
        ),
        "emergency_exit_pnl_usd": sum(
            float(r.get("emergency_exit_pnl_usd", 0.0)) for r in rows
        ),
        "partial_exit_fills": sum(
            int(r.get("partial_exit_fills", 0)) for r in rows
        ),
        "runner_exit_fills": sum(
            int(r.get("runner_exit_fills", 0)) for r in rows
        ),
        "clip_reclaim_reentries": sum(
            int(r.get("clip_reclaim_reentries", 0)) for r in rows
        ),
        "clip_obligations_unfilled_at_end": sum(
            int(r.get("clip_obligations_unfilled_at_end", 0)) for r in rows
        ),
        "reclaim_reentries": sum(int(r["reclaim_reentries"]) for r in rows),
        "bars_flat_beyond_reclaim": sum(
            int(r["bars_flat_beyond_reclaim"]) for r in rows
        ),
        "future_htf_source_count": sum(
            int(r["future_htf_source_count"]) for r in rows
        ),
        "entry_schedule_sha256_by_fold": [
            r["frozen_entry_schedule_sha256"] for r in rows
        ],
    }
    result["fill_ratio"] = (
        result["filled_notional_usd"] / result["requested_notional_usd"]
        if result["requested_notional_usd"] > 0
        else 1.0
    )
    result["emergency_exit_share"] = (
        result["emergency_exit_fills"] / result["exit_fills"]
        if result["exit_fills"]
        else 0.0
    )
    return result


def _causal_action_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Publish ledger-derived action identity and strict-later pairs.

    Compiled-only scanners do not expose their ordered action stream.  They
    deliberately remain unavailable instead of synthesizing indices from
    aggregate fill counters.
    """
    ledger = row.get("event_ledger")
    unavailable = {
        "action_evidence_status": "UNAVAILABLE_NO_CAUSAL_EVENT_LEDGER",
        "action_fingerprint": None,
        "ledger_sha256": None,
        "exit_indices": [],
        "reentry_indices": [],
        "reentry_pairs": [],
        "reentry_violations": 0,
        "strictly_later_reentry_proven": False,
        "pending_reentry_count": int(
            row.get(
                "open_reclaim_obligations",
                row.get("clip_obligations_unfilled_at_end", 0),
            )
        ),
        "terminal_right_censored": False,
    }
    if not isinstance(ledger, list):
        return unavailable
    required = {"type", "fill_index", "fill_ts"}
    if any(
        not isinstance(event, dict) or not required.issubset(event)
        for event in ledger
    ):
        unavailable["action_evidence_status"] = (
            "UNAVAILABLE_INCOMPLETE_CAUSAL_EVENT_LEDGER"
        )
        return unavailable
    actions = [
        event
        for event in ledger
        if event.get("type") in {"ENTRY", "REENTER", "PARTIAL_EXIT", "EXIT"}
    ]
    exit_indices = [
        int(event["fill_index"])
        for event in actions
        if event["type"] in {"PARTIAL_EXIT", "EXIT"}
    ]
    expected_exits = int(
        row.get(
            "exit_fills",
            row.get("technical_exit_fills", len(exit_indices)),
        )
    )
    if len(exit_indices) != expected_exits:
        unavailable["action_evidence_status"] = (
            "UNAVAILABLE_EVENT_LEDGER_EXIT_COUNT_MISMATCH"
        )
        return unavailable
    pairs = [
        {
            "exit_index": int(event["parent_exit_index"]),
            "reentry_index": int(event["fill_index"]),
        }
        for event in actions
        if event["type"] == "REENTER"
        and event.get("parent_exit_index") is not None
    ]
    reentry_indices = [pair["reentry_index"] for pair in pairs]
    exit_set = set(exit_indices)
    violations = sum(
        pair["exit_index"] not in exit_set
        or pair["reentry_index"] <= pair["exit_index"]
        for pair in pairs
    )
    pending = int(
        row.get(
            "open_reclaim_obligations",
            row.get("clip_obligations_unfilled_at_end", 0),
        )
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            actions,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    proven = bool(exit_indices and pairs and violations == 0)
    return {
        "action_evidence_status": "CAUSAL_EVENT_LEDGER",
        "action_fingerprint": fingerprint,
        "ledger_sha256": fingerprint,
        "exit_indices": exit_indices,
        "reentry_indices": reentry_indices,
        "reentry_pairs": pairs,
        "reentry_violations": violations,
        "strictly_later_reentry_proven": proven,
        "pending_reentry_count": pending,
        "terminal_right_censored": bool(pending > 0 and violations == 0),
    }


def screen_artifact(
    artifact: Path,
    npz_dir: Path,
    *,
    families: tuple[str, ...],
    fold_mode: str,
    exposure_min_pct: float,
    exposure_max_pct: float,
) -> dict[str, Any]:
    source, data, htfs, contexts = _fold_contexts(
        artifact, npz_dir, fold_mode=fold_mode
    )
    manifest = source["manifest"]
    commission = float(manifest["commission_bps_one_way"]) / 10_000.0
    slippage = float(manifest["slippage_bps_one_way"]) / 10_000.0
    side = manifest["side"]

    control_folds = []
    for ctx in contexts:
        control_folds.append(
            simulate(
                data,
                ctx["signals"],
                ctx["curve"],
                build_e02_book(data, ctx["signals"], htfs),
                ctx["left"],
                ctx["right"],
                commission,
                slippage,
                side=side,
            )
        )
    control = _aggregate(control_folds)
    control_return = float(control["capital_return_pct_sum"])
    bh_return = float(control["bh_capital_return_pct_sum"])
    candidates: list[dict[str, Any]] = []
    compiled_structural_elapsed_seconds = 0.0

    def candidate_row(
        family: str,
        params: dict[str, Any],
        fold_rows: list[dict[str, Any]],
        **extra: Any,
    ) -> dict[str, Any]:
        aggregate = _aggregate(fold_rows)
        row = {
            "family": family,
            "params": params,
            "metrics": aggregate,
            "alpha_vs_bh_pp": (
                float(aggregate["capital_return_pct_sum"]) - bh_return
            ),
            "alpha_vs_same_entry_e02_pp": (
                float(aggregate["capital_return_pct_sum"]) - control_return
            ),
            **extra,
        }
        row["fold_evidence"] = [
            {
                "fold": int(contexts[index]["fold"]),
                "validation": list(contexts[index]["validation"]),
                "strategy_return_pct": float(candidate["capital_return_pct"]),
                "bh_return_pct": float(candidate["bh_capital_return_pct"]),
                "same_entry_e02_return_pct": float(
                    control_folds[index]["capital_return_pct"]
                ),
                "alpha_vs_bh_pp": float(candidate["capital_return_pct"])
                - float(candidate["bh_capital_return_pct"]),
                "alpha_vs_same_entry_e02_pp": float(
                    candidate["capital_return_pct"]
                )
                - float(control_folds[index]["capital_return_pct"]),
                "weighted_tim_pct": float(
                    candidate["exposure_weighted_tim_pct"]
                ),
                "future_htf_source_count": int(
                    candidate["future_htf_source_count"]
                ),
                "bars_flat_beyond_reclaim": int(
                    candidate["bars_flat_beyond_reclaim"]
                ),
                "insolvent": bool(candidate["insolvent"]),
                "entry_capacity_breach": bool(
                    candidate["entry_capacity_breach"]
                ),
                "exit_fills": int(candidate["exit_fills"]),
                "real_close_trades": int(candidate.get("real_close_trades", 0)),
                "terminal_lifecycle_closes": int(candidate.get("terminal_lifecycle_closes", 0)),
                "normal_exit_fills": int(
                    candidate.get("normal_exit_fills", candidate["exit_fills"])
                ),
                "emergency_exit_fills": int(
                    candidate.get("emergency_exit_fills", 0)
                ),
                "emergency_exit_share": float(
                    candidate.get("emergency_exit_share", 0.0)
                ),
                "normal_exit_pnl_usd": float(
                    candidate.get("normal_exit_pnl_usd", 0.0)
                ),
                "emergency_exit_pnl_usd": float(
                    candidate.get("emergency_exit_pnl_usd", 0.0)
                ),
                **chart_event_ledger(candidate),
                **_causal_action_evidence(candidate),
            }
            for index, candidate in enumerate(fold_rows)
        ]
        if fold_mode == "nested":
            discovery = _aggregate(fold_rows[:-1])
            validation = _aggregate(fold_rows[-1:])
            control_discovery = _aggregate(control_folds[:-1])
            control_validation = _aggregate(control_folds[-1:])
            row["nested"] = {
                "discovery": discovery,
                "validation": validation,
                "discovery_alpha_vs_bh_pp": (
                    float(discovery["capital_return_pct_sum"])
                    - float(discovery["bh_capital_return_pct_sum"])
                ),
                "discovery_alpha_vs_same_entry_e02_pp": (
                    float(discovery["capital_return_pct_sum"])
                    - float(control_discovery["capital_return_pct_sum"])
                ),
                "validation_alpha_vs_bh_pp": (
                    float(validation["capital_return_pct_sum"])
                    - float(validation["bh_capital_return_pct_sum"])
                ),
                "validation_alpha_vs_same_entry_e02_pp": (
                    float(validation["capital_return_pct_sum"])
                    - float(control_validation["capital_return_pct_sum"])
                ),
                "robust_discovery_all_folds": all(
                    evidence["alpha_vs_bh_pp"] > 0
                    and evidence["alpha_vs_same_entry_e02_pp"] > 0
                    and exposure_min_pct
                    <= evidence["weighted_tim_pct"]
                    <= exposure_max_pct
                    and not evidence["insolvent"]
                    and not evidence["entry_capacity_breach"]
                    and evidence["future_htf_source_count"] == 0
                    and evidence["bars_flat_beyond_reclaim"] == 0
                    and (
                        family not in ACTUAL_EXIT_REQUIRED_FAMILIES
                        or evidence["exit_fills"] > 0
                    )
                    and (
                        family not in EMERGENCY_EXIT_FAMILIES
                        or evidence["emergency_exit_share"] <= 0.25
                    )
                    for evidence in row["fold_evidence"][:-1]
                ),
                "robust_validation_fold": (
                    row["fold_evidence"][-1]["alpha_vs_bh_pp"] > 0
                    and row["fold_evidence"][-1][
                        "alpha_vs_same_entry_e02_pp"
                    ]
                    > 0
                    and exposure_min_pct
                    <= row["fold_evidence"][-1]["weighted_tim_pct"]
                    <= exposure_max_pct
                    and not row["fold_evidence"][-1]["insolvent"]
                    and not row["fold_evidence"][-1][
                        "entry_capacity_breach"
                    ]
                    and row["fold_evidence"][-1][
                        "future_htf_source_count"
                    ]
                    == 0
                    and row["fold_evidence"][-1][
                        "bars_flat_beyond_reclaim"
                    ]
                    == 0
                    and (
                        family not in ACTUAL_EXIT_REQUIRED_FAMILIES
                        or row["fold_evidence"][-1]["exit_fills"] > 0
                    )
                    and (
                        family not in EMERGENCY_EXIT_FAMILIES
                        or row["fold_evidence"][-1][
                            "emergency_exit_share"
                        ]
                        <= 0.25
                    )
                ),
            }
        return row

    if "E02_GRID" in families:
        # Vectorize the expensive rolling-channel discovery once per TF/N.
        # Profit gates reuse the same immutable event arrays.
        books = {
            (params["timeframe"], params["lookback"]): build_donchian_book(
                data,
                htfs,
                timeframe=str(params["timeframe"]),
                lookback=int(params["lookback"]),
                side=side,
            )
            for params in e02_grid()
        }
        for params in e02_grid():
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    books[(params["timeframe"], params["lookback"])],
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=float(params["profit_gate_pct"]),
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row("EXIT_E02_DONCHIAN", dict(params), fold_rows)
            )
    if "ALGO_STRUCTURE" in families:
        books = {}
        for params in algo_structure_grid():
            params.validate()
            if params.timeframe not in books:
                book = build_donchian_book(
                    data,
                    htfs,
                    timeframe=params.timeframe,
                    lookback=params.lookback,
                    side=side,
                )
                book.label = (
                    f"EXIT_ALGO_STRUCTURE_{params.timeframe}_N"
                    f"{params.lookback}"
                )
                books[params.timeframe] = book
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    books[params.timeframe],
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=params.profit_gate_pct,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "EXIT_ALGO_STRUCTURE_1H_15M",
                    dataclasses.asdict(params),
                    fold_rows,
                    research_decomposition_only=True,
                    removed_compound_score_not_reconstructed=True,
                )
            )
    if "ALGO_STOCH_4H" in families:
        for params in algo_stoch_4h_grid():
            book = build_algo_stoch_4h_book(
                data, htfs, params, side=side
            )
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    book,
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=params.profit_gate_pct,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "EXIT_ALGO_STOCH_4H_ROLL",
                    dataclasses.asdict(params),
                    fold_rows,
                    path_audit=book.audit,
                    research_decomposition_only=True,
                    removed_compound_score_not_reconstructed=True,
                )
            )
    if "ALGO_PROFIT_TAKE_15M" in families:
        for params in algo_profit_take_15m_grid():
            book = build_algo_profit_take_15m_book(
                data, htfs, params, side=side
            )
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    book,
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=params.min_profit_pct,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "EXIT_ALGO_PROFIT_TAKE_15M",
                    dataclasses.asdict(params),
                    fold_rows,
                    path_audit=book.audit,
                    research_decomposition_only=True,
                    removed_compound_score_not_reconstructed=True,
                )
            )
    if "WT_MTF" in families:
        grid = wt_grid()
        # Vectorize completed-TF state construction once per signal block.
        # Profit gates reuse the identical immutable event arrays.
        signal_blocks = {
            (params.min_against_tfs, params.extreme, params.velocity): params
            for params in grid
        }
        books = {
            key:
            build_wt_mtf_book(
                data,
                htfs,
                dataclasses.replace(params, profit_gate_pct=0.0),
                side=side,
            )
            for key, params in signal_blocks.items()
        }
        for params in grid:
            fold_rows = []
            for ctx in contexts:
                fold_rows.append(
                    simulate(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        books[
                            (
                                params.min_against_tfs,
                                params.extreme,
                                params.velocity,
                            )
                        ],
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=params.profit_gate_pct,
                    )
                )
            candidates.append(
                candidate_row(
                    "EXIT_WT_MTF", dataclasses.asdict(params), fold_rows
                )
            )
    if "GR_OPPOSITE" in families:
        grid = gr_opposite_grid()
        signal_blocks = {
            (
                params.min_tfs,
                params.min_indicators,
                params.min_weighted_score,
                params.weights,
            ): params
            for params in grid
        }
        books = {
            key: build_gr_opposite_book(
                data,
                htfs,
                dataclasses.replace(params, profit_gate_pct=0.0),
                side=side,
            )
            for key, params in signal_blocks.items()
        }
        for params in grid:
            key = (
                params.min_tfs,
                params.min_indicators,
                params.min_weighted_score,
                params.weights,
            )
            book = books[key]
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    book,
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=params.profit_gate_pct,
                )
                for ctx in contexts
            ]
            param_values = dataclasses.asdict(params)
            param_values["weights_by_tf"] = {
                tf: float(weight)
                for tf, weight in zip(params.timeframes, params.weights)
            }
            candidates.append(
                candidate_row(
                    "EXIT_GR_OPPOSITE",
                    param_values,
                    fold_rows,
                    vote_audit=book.audit,
                )
            )
    if "E01_CHANDELIER" in families:
        grid = chandelier_grid()
        signal_blocks = {
            (params.timeframe, params.lookback, params.atr_mult): params
            for params in grid
        }
        templates = {
            key: build_chandelier_book(
                data,
                htfs,
                dataclasses.replace(params, profit_gate_pct=0.0),
                side=side,
            )
            for key, params in signal_blocks.items()
        }
        for params in grid:
            template = templates[
                (params.timeframe, params.lookback, params.atr_mult)
            ]
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    template.clone(),
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=params.profit_gate_pct,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "EXIT_E01_CHANDELIER",
                    dataclasses.asdict(params),
                    fold_rows,
                    chandelier_audit=template.audit,
                    structural_arm_used=False,
                )
            )
    if "BOTTOM_A" in families:
        grid = protective_trail_grid()
        templates = [
            ProtectiveTrailExitBook(data, htfs, params, side=side)
            for params in grid
        ]
        for params, template in zip(grid, templates):
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    template,
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    # Protective exits must remain visible when they realize a
                    # loss; a zero profit gate would silently disable them.
                    profit_gate_pct=-999.0,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "BOTTOM_A_PROTECTIVE_TRAIL",
                    dataclasses.asdict(params),
                    fold_rows,
                    path_audit=template.audit,
                    research_range=(
                        params.mode == "STDEV"
                        or params.trail_timeframe != "5m"
                        or params.break_buffer_atr != 0.0
                    ),
                    immediate_break_churn_comparator=(
                        params.mode == "IMMEDIATE"
                    ),
                )
            )
    if "BOTTOM_A_EXT" in families:
        grid = protective_trail_grid_extended()
        templates = [
            ProtectiveTrailExitBook(data, htfs, params, side=side)
            for params in grid
        ]
        for params, template in zip(grid, templates):
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    template,
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=-999.0,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                    dataclasses.asdict(params),
                    fold_rows,
                    path_audit=template.audit,
                    extended_parameter_range=True,
                    immediate_break_churn_comparator=(
                        params.mode == "IMMEDIATE"
                    ),
                    first_break_is_diagnostic_only=(
                        params.mode == "IMMEDIATE"
                    ),
                )
            )
    if "MTF_ATR_TRAIL" in families:
        for params in mtf_atr_trail_grid():
            template = MtfAtrTrailExitBook(data, htfs, params, side=side)
            fold_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    template,
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    # The book owns the preregistered current-profit threshold.
                    profit_gate_pct=-999.0,
                )
                for ctx in contexts
            ]
            candidates.append(
                candidate_row(
                    "EXIT_MTF_ATR_TRAIL",
                    dataclasses.asdict(params),
                    fold_rows,
                    path_audit=template.audit,
                    research_only_not_live_single_tf=True,
                    overlap_control="BOTTOM_A_PROTECTIVE_TRAIL",
                )
            )
    if (
        "BOTTOM_B" in families
        or "BOTTOM_C" in families
        or "BOTTOM_B_EXT" in families
        or "BOTTOM_C_EXT" in families
        or "BOTTOM_V2" in families
    ):
        aligned_bottom = {
            tf: _aligned_structural_tf(data, htfs[tf], tf)
            for tf in ("5m", "15m", "1h", "4h", "D")
        }
        structural_started = time.perf_counter()
        if "BOTTOM_B" in families:
            for params, wait_hours in bottom_delayed_grid():
                fold_rows = [
                    simulate_structural_compiled(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        htfs,
                        params,
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=-999.0,
                        aligned_by_tf=aligned_bottom,
                    )
                    for ctx in contexts
                ]
                candidates.append(
                    candidate_row(
                        "BOTTOM_B_DELAYED_LOWER_TOP",
                        {
                            **dataclasses.asdict(params),
                            "max_wait_hours": wait_hours,
                        },
                        fold_rows,
                        break_bar_can_exit=False,
                        dc_low4_profit_exit_used=False,
                    )
                )
        bottom_b_extended_candidates: list[dict[str, Any]] = []
        if "BOTTOM_B_EXT" in families or "BOTTOM_C_EXT" in families:
            for params, wait_hours in bottom_delayed_grid_extended():
                fold_rows = [
                    simulate_structural_compiled(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        htfs,
                        params,
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=-999.0,
                        aligned_by_tf=aligned_bottom,
                    )
                    for ctx in contexts
                ]
                row = candidate_row(
                    "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
                    {
                        **dataclasses.asdict(params),
                        "max_wait_hours": wait_hours,
                    },
                    fold_rows,
                    break_bar_can_exit=False,
                    dc_low4_profit_exit_used=False,
                    extended_parameter_range=True,
                )
                bottom_b_extended_candidates.append(row)
                if "BOTTOM_B_EXT" in families:
                    candidates.append(row)
        if "BOTTOM_C" in families:
            for params, wait_hours, emergency_label in bottom_emergency_grid():
                fold_rows = [
                    simulate_structural_compiled(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        htfs,
                        params,
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=-999.0,
                        aligned_by_tf=aligned_bottom,
                    )
                    for ctx in contexts
                ]
                candidates.append(
                    candidate_row(
                        "BOTTOM_C_DELAYED_EMERGENCY",
                        {
                            **dataclasses.asdict(params),
                            "max_wait_hours": wait_hours,
                            "emergency_label": emergency_label,
                        },
                        fold_rows,
                        emergency_must_be_rare_share_max=0.25,
                        break_bar_can_exit=False,
                        dc_low4_profit_exit_used=False,
                    )
                )
        if "BOTTOM_C_EXT" in families:
            pruned_bases = prune_bottom_b_extended_bases(
                bottom_b_extended_candidates,
                exposure_min_pct=exposure_min_pct,
                exposure_max_pct=exposure_max_pct,
            )
            for base_rank, base_row in enumerate(pruned_bases, start=1):
                base_values = dict(base_row["params"])
                wait_hours = int(base_values.pop("max_wait_hours"))
                if isinstance(base_values.get("emergency_modes"), list):
                    base_values["emergency_modes"] = tuple(
                        base_values["emergency_modes"]
                    )
                base_params = StructuralWtParams(**base_values)
                base_hash = hashlib.sha256(
                    json.dumps(
                        base_row["params"], sort_keys=True
                    ).encode("utf-8")
                ).hexdigest()
                for emergency_label, overlay in bottom_emergency_variants():
                    params = dataclasses.replace(base_params, **overlay)
                    fold_rows = [
                        simulate_structural_compiled(
                            data,
                            ctx["signals"],
                            ctx["curve"],
                            htfs,
                            params,
                            ctx["left"],
                            ctx["right"],
                            commission,
                            slippage,
                            side=side,
                            profit_gate_pct=-999.0,
                            aligned_by_tf=aligned_bottom,
                        )
                        for ctx in contexts
                    ]
                    candidates.append(
                        candidate_row(
                            "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
                            {
                                **dataclasses.asdict(params),
                                "max_wait_hours": wait_hours,
                                "emergency_label": emergency_label,
                            },
                            fold_rows,
                            emergency_must_be_rare_share_max=0.25,
                            break_bar_can_exit=False,
                            dc_low4_profit_exit_used=False,
                            paired_b_discovery_rank=base_rank,
                            paired_b_params_sha256=base_hash,
                            paired_b_metrics=base_row["metrics"],
                            extended_parameter_range=True,
                        )
                    )
        if "BOTTOM_V2" in families:
            v2_b_candidates: list[dict[str, Any]] = []
            for params, wait_hours in bottom_structural_v2_grid():
                fold_rows = [
                    simulate_structural_compiled(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        htfs,
                        params,
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=-999.0,
                        aligned_by_tf=aligned_bottom,
                    )
                    for ctx in contexts
                ]
                row = candidate_row(
                    "BOTTOM_B_STRUCTURAL_V2",
                    {
                        **dataclasses.asdict(params),
                        "max_wait_hours": wait_hours,
                    },
                    fold_rows,
                    break_bar_can_exit=False,
                    dc_low4_profit_exit_used=False,
                    qualified_arm_required=True,
                    v2_preregistered=True,
                )
                v2_b_candidates.append(row)
                candidates.append(row)
            for base_rank, base_row in enumerate(
                prune_bottom_structural_v2_bases(
                    v2_b_candidates,
                    exposure_min_pct=exposure_min_pct,
                    exposure_max_pct=exposure_max_pct,
                ),
                start=1,
            ):
                base_values = dict(base_row["params"])
                wait_hours = int(base_values.pop("max_wait_hours"))
                if isinstance(base_values.get("emergency_modes"), list):
                    base_values["emergency_modes"] = tuple(
                        base_values["emergency_modes"]
                    )
                base_params = StructuralWtParams(**base_values)
                base_hash = hashlib.sha256(
                    json.dumps(
                        base_row["params"], sort_keys=True
                    ).encode("utf-8")
                ).hexdigest()
                for emergency_label, overlay in (
                    bottom_structural_v2_emergency_variants()
                ):
                    params = dataclasses.replace(base_params, **overlay)
                    fold_rows = [
                        simulate_structural_compiled(
                            data,
                            ctx["signals"],
                            ctx["curve"],
                            htfs,
                            params,
                            ctx["left"],
                            ctx["right"],
                            commission,
                            slippage,
                            side=side,
                            profit_gate_pct=-999.0,
                            aligned_by_tf=aligned_bottom,
                        )
                        for ctx in contexts
                    ]
                    candidates.append(
                        candidate_row(
                            "BOTTOM_C_STRUCTURAL_V2_EMERGENCY",
                            {
                                **dataclasses.asdict(params),
                                "max_wait_hours": wait_hours,
                                "emergency_label": emergency_label,
                            },
                            fold_rows,
                            emergency_must_be_rare_share_max=0.10,
                            break_bar_can_exit=False,
                            dc_low4_profit_exit_used=False,
                            paired_b_discovery_rank=base_rank,
                            paired_b_params_sha256=base_hash,
                            v2_preregistered=True,
                        )
                    )
        compiled_structural_elapsed_seconds += (
            time.perf_counter() - structural_started
        )
    if "STRUCTURAL_WT" in families:
        aligned_structural = {
            tf: _aligned_structural_tf(data, htfs[tf], tf)
            for tf in ("15m", "1h", "4h")
        }
        structural_started = time.perf_counter()
        for params, profit_gate, wait_hours in structural_grid():
            fold_rows = []
            for ctx in contexts:
                fold_rows.append(
                    simulate_structural_compiled(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        htfs,
                        params,
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=profit_gate,
                        aligned_by_tf=aligned_structural,
                    )
                )
            candidates.append(
                candidate_row(
                    "EXIT_STRUCTURAL_WT_LOWER_TOP",
                    {
                        **dataclasses.asdict(params),
                        "max_wait_hours": wait_hours,
                    },
                    fold_rows,
                    profit_gate_pct=profit_gate,
                )
            )
        compiled_structural_elapsed_seconds += (
            time.perf_counter() - structural_started
        )
    if "PARTIAL_WT" in families:
        for params, fraction in partial_wt_grid():
            fold_rows = []
            for ctx in contexts:
                fold_rows.append(
                    simulate(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        build_wt_mtf_book(data, htfs, params, side=side),
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=params.profit_gate_pct,
                        partial_exit_fraction=fraction,
                        runner_exit_book=build_e02_book(
                            data, ctx["signals"], htfs
                        ),
                    )
                )
            candidates.append(
                candidate_row(
                    "EXIT_WT_MTF_PARTIAL_E02_RUNNER",
                    {
                        **dataclasses.asdict(params),
                        "clip_fraction": fraction,
                    },
                    fold_rows,
                )
            )
    candidates.sort(
        key=lambda row: (
            -float(row["alpha_vs_same_entry_e02_pp"]),
            -float(row["alpha_vs_bh_pp"]),
            float(row["metrics"]["max_drawdown_account_pct_max"]),
        )
    )
    schedule_hashes = control["entry_schedule_sha256_by_fold"]
    for row in candidates:
        if row["metrics"]["entry_schedule_sha256_by_fold"] != schedule_hashes:
            data.z.close()
            raise RuntimeError("candidate changed the frozen entry request schedule")
    provisional_survivors = [
        row
        for row in candidates
        if row["alpha_vs_bh_pp"] > 0
        and row["alpha_vs_same_entry_e02_pp"] > 0
        and row["metrics"]["future_htf_source_count"] == 0
        and row["metrics"]["bars_flat_beyond_reclaim"] == 0
        and row["metrics"]["insolvent_folds"] == 0
        and not row["metrics"]["entry_capacity_breach"]
        and (
            row["family"] not in ACTUAL_EXIT_REQUIRED_FAMILIES
            or row["metrics"]["exit_fills"] > 0
        )
        and (
            row["family"] not in EMERGENCY_EXIT_FAMILIES
            or row["metrics"]["emergency_exit_share"] <= 0.25
        )
    ]
    survivors = []
    frozen_discovery_winners: list[dict[str, Any]] = []
    if fold_mode == "nested":
        for family in sorted({row["family"] for row in candidates}):
            family_rows = [row for row in candidates if row["family"] == family]
            family_rows.sort(
                key=lambda row: (
                    not bool(
                        row["nested"]["robust_discovery_all_folds"]
                    ),
                    not (
                        row["family"]
                        not in ACTUAL_EXIT_REQUIRED_FAMILIES
                        or (
                            int(
                                row["nested"]["discovery"]["exit_fills"]
                            )
                            > 0
                            and int(
                                row["nested"]["validation"]["exit_fills"]
                            )
                            > 0
                        )
                    ),
                    not (
                        exposure_min_pct
                        <= float(
                            row["nested"]["discovery"][
                                "exposure_weighted_tim_pct_row_weighted"
                            ]
                        )
                        <= exposure_max_pct
                    ),
                    -float(
                        row["nested"]["discovery_alpha_vs_same_entry_e02_pp"]
                    ),
                    -float(row["nested"]["discovery_alpha_vs_bh_pp"]),
                    float(
                        row["nested"]["discovery"][
                            "max_drawdown_account_pct_max"
                        ]
                    ),
                )
            )
            frozen_discovery_winners.append(family_rows[0])
        for winner in frozen_discovery_winners:
            if winner["family"] not in COMPILED_STRUCTURAL_FAMILIES:
                winner["compiled_python_parity"] = {
                    "status": "NOT_APPLICABLE"
                }
                continue
            param_values = dict(winner["params"])
            param_values.pop("max_wait_hours", None)
            param_values.pop("emergency_label", None)
            if isinstance(param_values.get("emergency_modes"), list):
                param_values["emergency_modes"] = tuple(
                    param_values["emergency_modes"]
                )
            oracle_params = StructuralWtParams(**param_values)
            oracle_started = time.perf_counter()
            oracle_rows = [
                simulate(
                    data,
                    ctx["signals"],
                    ctx["curve"],
                    StructuralWtExitBookAdapter(
                        data, htfs, oracle_params, side=side
                    ),
                    ctx["left"],
                    ctx["right"],
                    commission,
                    slippage,
                    side=side,
                    profit_gate_pct=float(
                        winner.get("profit_gate_pct", -999.0)
                    ),
                )
                for ctx in contexts
            ]
            oracle_elapsed = time.perf_counter() - oracle_started
            oracle = _aggregate(oracle_rows)
            differences: dict[str, dict[str, Any]] = {}
            float_keys = (
                "capital_return_pct_sum",
                "bh_capital_return_pct_sum",
                "exposure_weighted_tim_pct_row_weighted",
                "max_drawdown_account_pct_max",
                "minimum_account_equity_usd",
                "peak_post_fill_notional_usd_max",
                "requested_notional_usd",
                "filled_notional_usd",
                "fill_ratio",
                "normal_exit_pnl_usd",
                "emergency_exit_pnl_usd",
            )
            exact_keys = (
                "insolvent_folds",
                "entry_capacity_breach",
                "clamp_count",
                "exit_fills",
                "normal_exit_fills",
                "emergency_exit_fills",
                "reclaim_reentries",
                "bars_flat_beyond_reclaim",
                "future_htf_source_count",
                "entry_schedule_sha256_by_fold",
            )
            for key in float_keys:
                compiled_value = float(winner["metrics"][key])
                oracle_value = float(oracle[key])
                if not math.isclose(
                    compiled_value,
                    oracle_value,
                    rel_tol=1e-10,
                    abs_tol=1e-8,
                ):
                    differences[key] = {
                        "compiled": compiled_value,
                        "python": oracle_value,
                    }
            for key in exact_keys:
                if winner["metrics"][key] != oracle[key]:
                    differences[key] = {
                        "compiled": winner["metrics"][key],
                        "python": oracle[key],
                    }
            winner["compiled_python_parity"] = {
                "status": "PASS" if not differences else "FAIL",
                "python_oracle_elapsed_seconds": oracle_elapsed,
                "compiled_grid_elapsed_seconds": (
                    compiled_structural_elapsed_seconds
                ),
                "compiled_candidates": compiled_structural_candidate_count(
                    winner["family"]
                ),
                "estimated_python_grid_seconds": (
                    oracle_elapsed
                    * compiled_structural_candidate_count(winner["family"])
                ),
                "estimated_speedup": (
                    oracle_elapsed
                    * compiled_structural_candidate_count(winner["family"])
                    / compiled_structural_elapsed_seconds
                    if compiled_structural_elapsed_seconds > 0
                    else None
                ),
                "differences": differences,
            }
        survivors = [
            row
            for row in frozen_discovery_winners
            if row["nested"]["discovery_alpha_vs_bh_pp"] > 0
            and row["nested"]["discovery_alpha_vs_same_entry_e02_pp"] > 0
            and row["nested"]["validation_alpha_vs_bh_pp"] > 0
            and row["nested"]["validation_alpha_vs_same_entry_e02_pp"] > 0
            and row["nested"]["robust_discovery_all_folds"]
            and row["nested"]["robust_validation_fold"]
            and exposure_min_pct
            <= float(
                row["nested"]["discovery"][
                    "exposure_weighted_tim_pct_row_weighted"
                ]
            )
            <= exposure_max_pct
            and exposure_min_pct
            <= float(
                row["nested"]["validation"][
                    "exposure_weighted_tim_pct_row_weighted"
                ]
            )
            <= exposure_max_pct
            and row["metrics"]["future_htf_source_count"] == 0
            and row["metrics"]["bars_flat_beyond_reclaim"] == 0
            and row["metrics"]["insolvent_folds"] == 0
            and not row["metrics"]["entry_capacity_breach"]
            and row.get("compiled_python_parity", {}).get("status")
            in {"PASS", "NOT_APPLICABLE"}
        ]
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_SCREEN",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "exact_replay_allowed": bool(survivors),
        "symbol": manifest["symbol"],
        "side": side,
        "source_artifact": str(artifact.resolve()),
        "source_npz_sha256": manifest["npz_sha256"],
        "fold_mode": fold_mode,
        "frozen_entry_contract": {
            "curve_selected_before_exit_screen": True,
            "entry_request_schedule_sha256_by_fold": schedule_hashes,
            "next_rth_fill": True,
            "capacity_usd": ladder.CAPACITY,
            "base_unit_usd": ladder.BASE_UNIT,
        },
        "mandatory_reentry": "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN plus lower ladder",
        "exposure_survivor_gate_pct": [
            exposure_min_pct,
            exposure_max_pct,
        ],
        "dc_low4_profit_exit_used": False,
        "same_adapter_e02_control": control,
        "source_artifact_control": source["frozen_oos_aggregate"],
        "candidate_count": len(candidates),
        "compiled_structural_grid_elapsed_seconds": (
            compiled_structural_elapsed_seconds
        ),
        "provisional_survivor_count": len(provisional_survivors),
        "frozen_discovery_winners": frozen_discovery_winners,
        "survivor_count": len(survivors),
        "survivors": survivors,
        "candidates": candidates,
    }
    if "E01_CHANDELIER" in families:
        payload["e01_chandelier_contract"] = {
            "registered_standard_path": True,
            "activates_from_entry": True,
            "monotonic_while_active": True,
            "completed_timeframes_only": ["4h", "D"],
            "structural_arm_used": False,
            "structural_arm_backlog_separate": (
                "TOP_EXIT_RESEARCH_BACKLOG_20260726.md"
            ),
        }
    if "GR_OPPOSITE" in families:
        weakest = next(
            (
                row
                for row in candidates
                if row["family"] == "EXIT_GR_OPPOSITE"
                and row["params"]["min_tfs"] == 1
                and row["params"]["min_indicators"] == 1
                and row["params"]["min_weighted_score"] == 4.0
                and tuple(row["params"]["weights"])
                == (1.0, 1.0, 1.0, 1.0)
                and row["params"]["profit_gate_pct"] == 0.0
            ),
            None,
        )
        if weakest is None:
            data.z.close()
            raise RuntimeError("missing preregistered GR weakest-arm probe")
        payload["gr_opposite_weakest_arm_probe"] = {
            "params": weakest["params"],
            "vote_audit": weakest["vote_audit"],
            "fold_evidence": weakest["fold_evidence"],
            "exit_fills": weakest["metrics"]["exit_fills"],
            "future_htf_source_count": weakest["metrics"][
                "future_htf_source_count"
            ],
        }
    if any(
        name in families
        for name in (
            "BOTTOM_A",
            "BOTTOM_B",
            "BOTTOM_C",
            "BOTTOM_A_EXT",
            "BOTTOM_B_EXT",
            "BOTTOM_C_EXT",
            "BOTTOM_V2",
        )
    ):
        payload["bottom_exit_contract"] = {
            "code_audit": "BOTTOM_EXIT_CODE_AUDIT_20260726.md",
            "same_entry": True,
            "completed_timeframes_only": ["5m", "15m", "1h", "4h", "D"],
            "five_minute_native_or_interpolated_provenance_preserved": True,
            "break_bar_profit_exit_used": False,
            "immediate_break_retained_as_diagnostic_only": True,
            "normal_and_emergency_exits_counted_separately": True,
            "emergency_share_max_for_survival": 0.25,
            "side_specific_bh_usd": ladder.BASE_UNIT,
            "strategy_capacity_usd": ladder.CAPACITY,
        }
        if "BOTTOM_V2" in families:
            payload["bottom_exit_contract"].update(
                {
                    "v2_qualified_arm_modes": [
                        "ATR",
                        "STDEV",
                        "DC_SUPPORT",
                    ],
                    "v2_candidate_count": 972,
                    "v2_emergency_share_max_for_survival": 0.10,
                    "v2_emergency_bases_selected_discovery_only": 4,
                }
            )
    if "MTF_ATR_TRAIL" in families:
        payload["mtf_atr_trail_contract"] = {
            "same_entry": True,
            "completed_timeframes_only": ["1h", "4h", "D"],
            "candidate_count": 40,
            "current_position_profit_gate": True,
            "min_confirming_timeframes": [1, 2],
            "live_single_tf_path_unchanged": True,
            "live_stocks_path_currently_disabled": True,
            "bottom_a_overlap": (
                "Both ratchet ATR distance. Bottom-A first arms on an adverse "
                "1h/4h break and trails one 5m/15m/1h series; this path starts "
                "at entry and requires latest completed 1h/4h/D agreement."
            ),
            "side_specific_bh_usd": ladder.BASE_UNIT,
            "strategy_capacity_usd": ladder.CAPACITY,
        }
    if "ALGO_STRUCTURE" in families:
        payload["algo_structure_contract"] = {
            "source_inventory_job": "EXIT_ALGO_EXIT_ENABLED",
            "research_decomposition_only": True,
            "live_switch_connected": False,
            "removed_compound_score_reconstructed": False,
            "candidate_count": 4,
            "completed_timeframes_only": ["15m", "1h"],
            "historical_score_delta_provenance": {
                "1h": -15,
                "15m": -10,
            },
            "donchian_lookback_seed": 20,
            "profit_gate_pct_research": [0.0, 3.0],
            "broader_ranges": "TBD_FROM_FUNCTION_SOURCE",
            "side_specific_bh_usd": ladder.BASE_UNIT,
            "strategy_capacity_usd": ladder.CAPACITY,
        }
    if "ALGO_STOCH_4H" in families:
        payload["algo_stoch_4h_contract"] = {
            "source_inventory_job": "EXIT_ALGO_EXIT_ENABLED",
            "research_decomposition_only": True,
            "live_switch_connected": False,
            "removed_compound_score_reconstructed": False,
            "candidate_count": 12,
            "completed_timeframes_only": ["4h"],
            "historical_score_delta_provenance": -5,
            "mirrored_threshold_profiles": ["60/40", "70/30", "80/20"],
            "event_modes": ["STATE", "CROSS"],
            "profit_gate_pct_research": [0.0, 3.0],
            "side_mirror": True,
            "side_specific_bh_usd": ladder.BASE_UNIT,
            "strategy_capacity_usd": ladder.CAPACITY,
        }
    if "ALGO_PROFIT_TAKE_15M" in families:
        payload["algo_profit_take_15m_contract"] = {
            "source_inventory_job": "EXIT_ALGO_EXIT_ENABLED",
            "research_decomposition_only": True,
            "live_switch_connected": False,
            "removed_compound_score_reconstructed": False,
            "candidate_count": 8,
            "completed_timeframes_only": ["15m"],
            "historical_score_delta_provenance": -5,
            "historical_gain_seed": ">5%",
            "profit_threshold_pct": [3.0, 5.0, 7.0, 10.0],
            "event_modes": ["STATE", "CROSS"],
            "side_mirror": True,
            "side_specific_bh_usd": ladder.BASE_UNIT,
            "strategy_capacity_usd": ladder.CAPACITY,
        }
    data.z.close()
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument(
        "--families",
        default="WT_MTF,STRUCTURAL_WT",
        help=(
            "comma-separated E02_GRID, WT_MTF, GR_OPPOSITE, "
            "E01_CHANDELIER, MTF_ATR_TRAIL, ALGO_STRUCTURE, ALGO_STOCH_4H, "
            "ALGO_PROFIT_TAKE_15M, "
            "BOTTOM_A, BOTTOM_B, BOTTOM_C, "
            "BOTTOM_A_EXT, BOTTOM_B_EXT, BOTTOM_C_EXT, "
            "BOTTOM_V2, "
            "STRUCTURAL_WT, and/or PARTIAL_WT"
        ),
    )
    ap.add_argument(
        "--fold-mode", choices=("latest", "all", "nested", "discovery"), default="latest"
    )
    ap.add_argument("--exposure-min-pct", type=float, default=70.0)
    ap.add_argument("--exposure-max-pct", type=float, default=80.0)
    args = ap.parse_args()
    families = tuple(
        item.strip().upper() for item in args.families.split(",") if item.strip()
    )
    invalid = set(families) - {
        "E02_GRID",
        "WT_MTF",
        "GR_OPPOSITE",
        "E01_CHANDELIER",
        "MTF_ATR_TRAIL",
        "ALGO_STRUCTURE",
        "ALGO_STOCH_4H",
        "ALGO_PROFIT_TAKE_15M",
        "BOTTOM_A",
        "BOTTOM_B",
        "BOTTOM_C",
        "BOTTOM_A_EXT",
        "BOTTOM_B_EXT",
        "BOTTOM_C_EXT",
        "BOTTOM_V2",
        "STRUCTURAL_WT",
        "PARTIAL_WT",
    }
    if invalid:
        raise ValueError(f"unsupported families: {sorted(invalid)}")
    payload = screen_artifact(
        args.artifact.resolve(),
        args.npz_dir.resolve(),
        families=families,
        fold_mode=args.fold_mode,
        exposure_min_pct=args.exposure_min_pct,
        exposure_max_pct=args.exposure_max_pct,
    )
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.out_dir),
                "symbol": payload["symbol"],
                "candidate_count": payload["candidate_count"],
                "survivor_count": payload["survivor_count"],
                "best": payload["candidates"][0] if payload["candidates"] else None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
