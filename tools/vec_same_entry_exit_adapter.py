#!/usr/bin/env python3
"""Same-entry vector adapter for alternative stock exit paths.

This research-only runner consumes the already-selected band-ladder curve from
``vec_band_ladder_walkforward`` artifacts.  It never re-ranks or re-selects an
entry curve while testing an exit.  Every candidate therefore receives the
same causal ladder request schedule, target/add semantics, next-RTH fill rule,
$16k hard capacity, costs, and persistent reclaim obligation.

Implemented exit books:

* ``E02``: completed 4h opposite Donchian close (same-adapter control);
* ``WT_MTF``: completed-TF WaveTrend exhaustion/rollover vote;
* ``STRUCTURAL_WT``: completed 4h lower-low arm followed by a later 1h lower
  price/WT1 top and rollover.  It never treats ``dc_low4`` as a profit exit.

LONG and SHORT accounting are isolated.  The first campaign intentionally runs
LONG only because the path-fleet's accepted ladder cohort is currently
LONG-only.  Output is VEC research evidence and cannot write the switch matrix
or live configuration.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
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
        allowed = {"15m", "1h", "4h", "D"}
        if not self.timeframes or not set(self.timeframes) <= allowed:
            raise ValueError("WT timeframes must be a non-empty subset of 15m/1h/4h/D")
        if not 1 <= self.min_against_tfs <= len(self.timeframes):
            raise ValueError("min_against_tfs exceeds selected timeframes")
        if self.extreme < 0 or self.velocity < 0:
            raise ValueError("WT extreme/velocity must be non-negative")
        if self.recent_extreme_bars < 2:
            raise ValueError("recent_extreme_bars must be >=2")
        if self.profit_gate_pct < 0:
            raise ValueError("profit gate must be non-negative")


class StaticExitBook:
    def __init__(
        self,
        label: str,
        events: np.ndarray,
        references: np.ndarray,
        source_by_row: dict[int, dict[str, int]],
    ):
        if events.dtype != np.uint8 or len(events) != len(references):
            raise ValueError("static exit arrays have incompatible shape/type")
        self.label = label
        self.events = events
        self.references = references
        self.source_by_row = source_by_row

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
            reason=self.label,
            reclaim_reference=float(candidate.retest_price),
            source_timestamps={
                self.params.arm_tf: int(candidate.arm_source_ts),
                self.params.confirm_tf: int(candidate.source_ts),
            },
        )


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
    max_dd = requested = filled = weighted = 0.0
    held = clamps = entry_fills = exit_fills = reclaim = lower = 0
    signals_seen = rejected_profit = future_sources = beyond = 0
    partial_exit_fills = runner_exit_fills = clip_reclaims = 0
    clip_obligations: list[dict[str, float]] = []
    ledger: list[dict[str, Any]] = []

    def active() -> bool:
        return side_sign * qty > 1e-12

    def mark_equity(px: float) -> float:
        return cash + qty * px

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
                cash += side_sign * (
                    notional - side_sign * commission_rate * notional
                )
                prior_exit_notional = min(ladder.CAPACITY, notional)
                last_exit_fill = px
                ref = float(pending["ref"])
                reclaim_level = max(px, ref) if is_long else min(px, ref)
                qty = 0.0
                average_entry = math.nan
                gap_seen = False
                exit_fill_row = i
                exit_fills += 1
                runner_exit_fills += int(
                    pending["reason"].startswith("E02_DONCHIAN")
                )
                clip_obligations.clear()
                ledger.append(
                    {
                        "type": "EXIT",
                        "reason": pending["reason"],
                        "signal_ts": int(data.ts[pending["signal_index"]]),
                        "fill_ts": int(data.ts[i]),
                        "fill_price": px,
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
                    }
                )
                partial_exit_fills += 1
                exit_fills += 1
                ledger.append(
                    {
                        "type": "PARTIAL_EXIT",
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
                px = op * (1.0 + side_sign * slippage_rate)
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
                    entry_fills += 1
                    lower += int(
                        pending["reason"] in {"ladder_lower", "ladder_higher"}
                    )
                    reclaim += int(pending["reason"] == "reclaim")
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
                target = max(ladder.BASE_UNIT, prior_exit_notional)
                actual = min(target, ladder.CAPACITY)
                requested += target
                filled += actual
                clamps += int(actual + 1e-9 < target)
                cash -= side_sign * actual + commission_rate * actual
                qty = side_sign * actual / reclaim_px
                average_entry = reclaim_px
                entry_fills += 1
                reclaim += 1
                last_exit_fill = math.nan
                reclaim_level = math.nan

        # A partial WT clip owns an independent resting reclaim even while the
        # E02 runner remains open.  Reclaims are bounded by the same capacity.
        remaining_clip_obligations: list[dict[str, float]] = []
        for obligation in clip_obligations:
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
                entry_fills += 1
                reclaim += 1
                clip_reclaims += 1
            # A touched but capacity-clipped obligation is complete. Keeping it
            # alive would create repeated free attempts at the same historical
            # level and violate the one-obligation/one-fill contract.
        clip_obligations = remaining_clip_obligations

        equity = mark_equity(close)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_dd = max(max_dd, 100.0 * (peak_equity - equity) / peak_equity)
        held += int(active())
        weighted += min(ladder.CAPACITY, abs(qty) * close) / ladder.CAPACITY

        runner_decision = (
            runner_exit_book.update(i, active=active())
            if runner_exit_book is not None
            else None
        )
        decision = exit_book.update(i, active=active())
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
        "strategy_bh_multiple": pnl / bh_pnl if abs(bh_pnl) > 1e-12 else None,
        "alpha_vs_bh_pp": 100.0 * (pnl - bh_pnl) / ladder.BASE_UNIT,
        "binary_tim_pct": 100.0 * held / rows,
        "exposure_weighted_tim_pct": 100.0 * weighted / rows,
        "max_drawdown_account_pct": max_dd,
        "signals": signals_seen,
        "rejected_by_profit_gate": rejected_profit,
        "exit_fills": exit_fills,
        "partial_exit_fills": partial_exit_fills,
        "runner_exit_fills": runner_exit_fills,
        "clip_reclaim_reentries": clip_reclaims,
        "clip_obligations_unfilled_at_end": len(clip_obligations),
        "entry_fills": entry_fills,
        "lower_or_higher_reentries": lower,
        "reclaim_reentries": reclaim,
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "clamp_count": clamps,
        "future_htf_source_count": future_sources,
        "bars_flat_beyond_reclaim": beyond,
        "mandatory_reclaim_execution": "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN",
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
    """Compact block grid; settings vary coherently rather than OFAT."""
    groups = (
        ("15m", "1h"),
        ("1h", "4h"),
        ("15m", "1h", "4h"),
        ("1h", "4h", "D"),
        ("15m", "1h", "4h", "D"),
    )
    rows: list[WtMtfParams] = []
    for tfs in groups:
        for extreme in (35.0, 50.0, 65.0, 75.0):
            for velocity in (0.0, 0.5, 1.0):
                rows.append(
                    WtMtfParams(
                        timeframes=tfs,
                        min_against_tfs=max(1, (len(tfs) + 1) // 2),
                        extreme=extreme,
                        velocity=velocity,
                    )
                )
                rows.append(
                    WtMtfParams(
                        timeframes=tfs,
                        min_against_tfs=max(1, (len(tfs) + 1) // 2),
                        extreme=extreme,
                        velocity=velocity,
                        require_fast_structure=True,
                        profit_gate_pct=0.5,
                    )
                )
    return rows


def structural_grid() -> list[StructuralWtParams]:
    return [
        StructuralWtParams(
            rebound_atr=rebound,
            prebreak_lookback=lookback,
            max_wait_1h=wait,
        )
        for rebound in (0.25, 0.5, 1.0)
        for lookback in (4, 6, 10)
        for wait in (12, 20, 30)
    ]


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
    if side != "LONG":
        raise ValueError("current accepted path-fleet cohort is LONG-only")
    data = ladder.top._load_execution(
        symbol,
        npz_dir,
        "2024-01-01",
        "ladder",
        None,
    )
    if not data.contract["valid"]:
        data.z.close()
        raise RuntimeError(f"{symbol} quarantined: {data.contract['errors']}")
    current_sha = hashlib.sha256(Path(data.path).read_bytes()).hexdigest()
    if current_sha != manifest["npz_sha256"]:
        data.z.close()
        raise RuntimeError(
            f"{symbol} NPZ hash drift: artifact={manifest['npz_sha256']} current={current_sha}"
        )
    htfs = {
        tf: ladder.top._compress_htf(data, tf)
        for tf in ("15m", "1h", "4h", "D")
    }
    folds = list(source["outer_folds"])
    if fold_mode == "latest":
        folds = folds[-1:]
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
    return {
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
        "exit_fills": sum(int(r["exit_fills"]) for r in rows),
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
            }
        return row

    if "WT_MTF" in families:
        for params in wt_grid():
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
                    )
                )
            candidates.append(
                candidate_row(
                    "EXIT_WT_MTF", dataclasses.asdict(params), fold_rows
                )
            )
    if "STRUCTURAL_WT" in families:
        for params in structural_grid():
            fold_rows = []
            for ctx in contexts:
                fold_rows.append(
                    simulate(
                        data,
                        ctx["signals"],
                        ctx["curve"],
                        StructuralWtExitBookAdapter(
                            data, htfs, params, side=side
                        ),
                        ctx["left"],
                        ctx["right"],
                        commission,
                        slippage,
                        side=side,
                        profit_gate_pct=0.5,
                    )
                )
            candidates.append(
                candidate_row(
                    "EXIT_STRUCTURAL_WT_LOWER_TOP",
                    dataclasses.asdict(params),
                    fold_rows,
                    profit_gate_pct=0.5,
                )
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
    ]
    survivors = []
    frozen_discovery_winners: list[dict[str, Any]] = []
    if fold_mode == "nested":
        for family in sorted({row["family"] for row in candidates}):
            family_rows = [row for row in candidates if row["family"] == family]
            family_rows.sort(
                key=lambda row: (
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
        survivors = [
            row
            for row in frozen_discovery_winners
            if row["nested"]["discovery_alpha_vs_bh_pp"] > 0
            and row["nested"]["discovery_alpha_vs_same_entry_e02_pp"] > 0
            and row["nested"]["validation_alpha_vs_bh_pp"] > 0
            and row["nested"]["validation_alpha_vs_same_entry_e02_pp"] > 0
            and exposure_min_pct
            <= float(
                row["nested"]["validation"][
                    "exposure_weighted_tim_pct_row_weighted"
                ]
            )
            <= exposure_max_pct
            and row["metrics"]["future_htf_source_count"] == 0
            and row["metrics"]["bars_flat_beyond_reclaim"] == 0
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
        "provisional_survivor_count": len(provisional_survivors),
        "frozen_discovery_winners": frozen_discovery_winners,
        "survivor_count": len(survivors),
        "survivors": survivors,
        "candidates": candidates,
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
        help="comma-separated WT_MTF, STRUCTURAL_WT, and/or PARTIAL_WT",
    )
    ap.add_argument(
        "--fold-mode", choices=("latest", "all", "nested"), default="latest"
    )
    ap.add_argument("--exposure-min-pct", type=float, default=70.0)
    ap.add_argument("--exposure-max-pct", type=float, default=80.0)
    args = ap.parse_args()
    families = tuple(
        item.strip().upper() for item in args.families.split(",") if item.strip()
    )
    invalid = set(families) - {"WT_MTF", "STRUCTURAL_WT", "PARTIAL_WT"}
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
