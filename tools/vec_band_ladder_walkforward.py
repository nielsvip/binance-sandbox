#!/usr/bin/env python3
"""Causal, vector-first study of the Tradier regression-band entry ladder.

Research-only: this script never writes the switch matrix, per-symbol config, or
live state.  It tests the remembered D 10/6, 4h 6/4, 1h 4/1 ladder as a
hypothesis, after clipping every order to the declared 8x/$16k capacity. LONG
and SHORT are separate runs and separate ledgers; SHORT is a causal semantic
mirror, not the negative of a LONG result.

Signals:

* ``green`` is the existing NPZ ``wt_cross_bull_<tf>`` flag for LONG and
  ``wt_cross_bear_<tf>`` for SHORT, observed only when a newly completed HTF
  bar first becomes available.
* ``structure`` is HH+HL with low/rising StochRSI for LONG and LH+LL with
  high/falling StochRSI for SHORT on that same completed bar.
* ``union`` accepts either signal.

The slow exit is a completed-4h opposite Donchian close.  It is deliberately
kept fixed while the sizing curve is selected.  After an exit, the same ladder
may re-enter lower; a zero-buffer reclaim of the stored exit/top level is
mandatory so a move cannot run away while the strategy remains flat.

Selection is blockwise, not OFAT: coherent six-number curves, trigger family,
interpolation mode, and accumulation semantics are searched together.  Each
outer validation window is frozen after a three-fold inner chronological
selection.  Results are VEC_RESEARCH evidence only.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import vec_top_exit_campaign as top  # noqa: E402


TF_ORDER = ("D", "4h", "1h")
BASE_UNIT = 2_000.0
ACCOUNT_EQUITY = 10_000.0
CAPACITY = 16_000.0
MAX_MULT = CAPACITY / BASE_UNIT
KNOWN_GAPS = {
    "VT": (("2026-03-30", "2026-06-08"),),
}


@dataclasses.dataclass(frozen=True)
class Curve:
    label: str
    mode: str
    trigger: str
    semantics: str
    stoch_low: float
    d_bottom: float
    d_top: float
    h4_bottom: float
    h4_top: float
    h1_bottom: float
    h1_top: float

    def pair(self, tf: str) -> tuple[float, float]:
        if tf == "D":
            return self.d_bottom, self.d_top
        if tf == "4h":
            return self.h4_bottom, self.h4_top
        return self.h1_bottom, self.h1_top


@dataclasses.dataclass
class SignalData:
    entry_mult: np.ndarray
    event_tf: np.ndarray
    exit_event: np.ndarray
    exit_ref: np.ndarray
    causality: dict[str, Any]
    # Optional completed-HTF provenance for entry paths that can fire between
    # the D/4h/1h ladder's own event rows (for example a 5m WT_DC episode).
    # The exact replay adapter validates every timestamp against signal time.
    entry_source_ts: dict[int, dict[str, int]] | None = None


def _epoch(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def ladder_mult(pb: float, bottom: float, top_: float, mode: str) -> float:
    """Pure parity function for ``tradier_manage.band_ladder_mult``.

    Below the lower band is zero; above the upper band keeps the top size.
    Multipliers are clipped to the campaign's hard 8x capacity.
    """
    if not math.isfinite(pb):
        return 0.0
    if pb < 0.0:
        return 0.0
    if pb > 1.0:
        return min(MAX_MULT, max(0.0, top_))
    if mode == "center_plateau":
        value = bottom if pb <= 0.5 else bottom + (top_ - bottom) * ((pb - 0.5) / 0.5)
    else:
        value = bottom + (top_ - bottom) * pb
    return min(MAX_MULT, max(0.0, value))


def _curves(seed: int, n_random: int) -> list[Curve]:
    rows: list[Curve] = []

    def add(label: str, values: tuple[float, ...], mode: str, trigger: str, semantics: str, stoch: float) -> None:
        rows.append(Curve(label, mode, trigger, semantics, stoch, *values))

    remembered = (10.0, 6.0, 6.0, 4.0, 4.0, 1.0)
    for mode in ("linear", "center_plateau"):
        for trigger in ("green", "structure", "union"):
            for semantics in ("target", "add"):
                add(f"REM_{mode}_{trigger}_{semantics}", remembered, mode, trigger, semantics, 30.0)

    # Coherent block profiles.  A whole curve is scaled/tilted together; no
    # field is changed in isolation.
    archetypes = (
        (8, 5, 6, 3, 3, 1),
        (8, 3, 5, 2, 3, 1),
        (6, 4, 4, 2, 2, 1),
        (5, 2, 4, 2, 2, 0.5),
        (4, 2, 3, 1.5, 2, 0.5),
        (3, 1, 2, 1, 1, 0.5),
    )
    for i, vals in enumerate(archetypes):
        for mode in ("linear", "center_plateau"):
            for trigger in ("green", "structure", "union"):
                for semantics in ("target", "add"):
                    add(f"ARC{i}_{mode}_{trigger}_{semantics}", vals, mode, trigger, semantics, 30.0)

    rng = np.random.default_rng(seed)
    for i in range(n_random):
        # Random block generation preserves bottom>=top and D>=4h>=1h
        # hierarchy.  It explores shapes instead of a Cartesian OFAT grid.
        d_bottom = rng.uniform(3.0, 12.0)
        d_top = rng.uniform(0.35, 0.85) * d_bottom
        h4_bottom = rng.uniform(0.45, 0.9) * d_bottom
        h4_top = rng.uniform(0.35, 0.85) * h4_bottom
        h1_bottom = rng.uniform(0.30, 0.85) * h4_bottom
        h1_top = rng.uniform(0.15, 0.80) * h1_bottom
        vals = (d_bottom, d_top, h4_bottom, h4_top, h1_bottom, h1_top)
        add(
            f"BLOCK{i:03d}",
            vals,
            "center_plateau" if rng.random() < 0.5 else "linear",
            ("green", "structure", "union")[int(rng.integers(0, 3))],
            "target" if rng.random() < 0.5 else "add",
            (20.0, 30.0, 40.0)[int(rng.integers(0, 3))],
        )
    # Stable de-duplication protects deterministic nested selection.
    unique: dict[tuple[Any, ...], Curve] = {}
    for row in rows:
        key = dataclasses.astuple(row)[1:]
        unique.setdefault(key, row)
    return list(unique.values())


def _completed_field(data: top.ExecutionData, h: top.HTFData, key: str) -> np.ndarray:
    full_event = data.full_indices[h.event_index]
    return np.asarray(data.z[key], dtype=np.float64)[full_event]


def _build_signals(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    curve: Curve,
    exit_n: int,
    side: str = "LONG",
) -> SignalData:
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported side: {side}")
    is_long = side == "LONG"
    n = len(data.ts)
    mult_by_tf = np.zeros((3, n), dtype=np.float64)
    event_tf = np.zeros((3, n), dtype=np.uint8)
    causal_rows: dict[str, Any] = {}

    for slot, tf in enumerate(TF_ORDER):
        h = htfs[tf]
        z = data.z
        full_event = data.full_indices[h.event_index]
        source_ts = h.source_ts
        observed_ts = data.ts[h.event_index]
        causal = source_ts <= observed_ts
        cross_direction = "bull" if is_long else "bear"
        green = (
            np.asarray(z[f"wt_cross_{cross_direction}_{tf}"], dtype=np.uint8)[
                full_event
            ]
            > 0
        )
        stoch = np.asarray(z[f"stoch_k_{tf}"], dtype=np.float64)[full_event]
        stoch_prev = np.concatenate(([np.nan], stoch[:-1]))
        structure = np.zeros(len(h.close), dtype=bool)
        if is_long:
            structure[1:] = (
                (h.high[1:] > h.high[:-1])
                & (h.low[1:] > h.low[:-1])
                & (stoch[1:] <= curve.stoch_low)
                & (stoch[1:] > stoch_prev[1:])
            )
        else:
            structure[1:] = (
                (h.high[1:] < h.high[:-1])
                & (h.low[1:] < h.low[:-1])
                & (stoch[1:] >= 100.0 - curve.stoch_low)
                & (stoch[1:] < stoch_prev[1:])
            )
        if curve.trigger == "green":
            event = green
        elif curve.trigger == "structure":
            event = structure
        else:
            event = green | structure
        event &= causal

        raw_pb = np.asarray(z[f"lrL_pct_b_{tf}"], dtype=np.float64)[full_event]
        # A LONG ladder is largest at the lower band (pct_b=0). A SHORT
        # ladder is largest at the upper band, so reflect the same sizing
        # curve through the band centre rather than reusing LONG depth.
        pb = raw_pb if is_long else 1.0 - raw_pb
        bottom, top_ = curve.pair(tf)
        values = np.fromiter(
            (ladder_mult(float(x), bottom, top_, curve.mode) for x in pb),
            dtype=np.float64,
            count=len(pb),
        )
        values[~event] = 0.0
        mult_by_tf[slot, h.event_index] = values
        event_tf[slot, h.event_index] = event.astype(np.uint8)
        causal_rows[tf] = {
            "side": side,
            "completed_bars": int(len(h.close)),
            "source_timestamp_future_count": int(np.count_nonzero(~causal)),
            "directional_wt_cross_events": int(np.count_nonzero(green & causal)),
            (
                "hh_hl_low_rising_stoch_events"
                if is_long
                else "lh_ll_high_falling_stoch_events"
            ): int(np.count_nonzero(structure & causal)),
            "selected_events": int(np.count_nonzero(event)),
            "first_observation_lag_seconds_max": int(np.max(observed_ts - source_ts)),
        }

    # Same-bar TF events are one order request.  Under add semantics they sum;
    # target semantics takes the strongest requested target.
    if curve.semantics == "add":
        entry_mult = np.sum(mult_by_tf, axis=0)
    else:
        entry_mult = np.max(mult_by_tf, axis=0)
    entry_mult = np.clip(entry_mult, 0.0, MAX_MULT)

    h4 = htfs["4h"]
    prior_low = top._rolling_prior(h4.low, exit_n, "min")
    prior_high = top._rolling_prior(h4.high, exit_n, "max")
    event_h = h4.close < prior_low if is_long else h4.close > prior_high
    reclaim_ref = prior_high if is_long else prior_low
    mapped, refs = top._map_events(n, h4, event_h, reclaim_ref)
    return SignalData(
        entry_mult=np.ascontiguousarray(entry_mult),
        event_tf=event_tf,
        exit_event=mapped,
        exit_ref=refs,
        causality=causal_rows,
    )


def _simulate(
    data: top.ExecutionData,
    signals: SignalData,
    curve: Curve,
    left: int,
    right: int,
    commission_rate: float,
    slippage_rate: float,
    side: str = "LONG",
) -> dict[str, Any]:
    """Stateful accounting loop over vector-precomputed events."""
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported side: {side}")
    side_sign = 1.0 if side == "LONG" else -1.0
    is_long = side_sign > 0
    if right - left < 100:
        raise ValueError("simulation window too short")
    cash = ACCOUNT_EQUITY
    qty = 0.0
    entry_notional = 0.0
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    pending: dict[str, Any] | None = None
    peak_equity = ACCOUNT_EQUITY
    min_equity = ACCOUNT_EQUITY
    max_dd = 0.0
    requested = filled = 0.0
    clamp_count = fill_count = exit_count = reclaim_count = lower_count = 0
    held_bars = 0
    weighted_exposure = 0.0
    peak_mark_notional = 0.0
    peak_post_fill_notional = 0.0
    beyond_reclaim = 0

    def equity(px: float) -> float:
        return cash + qty * px

    def has_position() -> bool:
        return side_sign * qty > 1e-12

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        # Every completed-bar signal fills at the next RTH open.
        if pending is not None:
            kind = pending["kind"]
            if kind == "exit" and has_position():
                px = op * (1.0 - side_sign * slippage_rate)
                close_qty = abs(qty)
                notional = close_qty * px
                cash += side_sign * (notional - side_sign * commission_rate * notional)
                prior_exit_notional = min(CAPACITY, notional)
                last_exit_fill = px
                reclaim_level = (
                    max(px, float(pending["ref"]))
                    if is_long
                    else min(px, float(pending["ref"]))
                )
                qty = 0.0
                entry_notional = 0.0
                gap_seen = False
                exit_count += 1
            elif kind == "entry":
                px = op * (1.0 + side_sign * slippage_rate)
                current = abs(qty) * px
                requested_notional = float(pending["requested_notional"])
                if pending.get("absolute_target"):
                    want = max(0.0, requested_notional - current)
                else:
                    want = requested_notional
                capacity_left = max(0.0, CAPACITY - current)
                actual = min(want, capacity_left)
                requested += max(0.0, want)
                filled += actual
                clamp_count += int(actual + 1e-9 < want)
                if actual > 0:
                    cash -= side_sign * actual + commission_rate * actual
                    qty += side_sign * actual / px
                    entry_notional = abs(qty) * px
                    peak_post_fill_notional = max(peak_post_fill_notional, entry_notional)
                    fill_count += 1
                    reclaim_count += int(pending.get("reason") == "reclaim")
                    lower_count += int(
                        pending.get("reason") in {"ladder_lower", "ladder_higher"}
                    )
            pending = None

        mark_equity = equity(close)
        min_equity = min(min_equity, mark_equity)
        peak_equity = max(peak_equity, mark_equity)
        if peak_equity > 0:
            max_dd = max(max_dd, 100.0 * (peak_equity - mark_equity) / peak_equity)
        notional = abs(qty) * close
        peak_mark_notional = max(peak_mark_notional, notional)
        held_bars += int(has_position())
        weighted_exposure += min(CAPACITY, notional) / CAPACITY

        if i + 1 >= right:
            continue
        if has_position():
            if signals.exit_event[i]:
                ref = float(signals.exit_ref[i])
                if not math.isfinite(ref):
                    ref = float(data.high[i] if is_long else data.low[i])
                pending = {"kind": "exit", "ref": ref}
            elif signals.entry_mult[i] > 0:
                request = BASE_UNIT * float(signals.entry_mult[i])
                pending = {
                    "kind": "entry",
                    "requested_notional": request,
                    "absolute_target": curve.semantics == "target",
                    "reason": "ladder_add",
                }
        else:
            if math.isfinite(last_exit_fill):
                gap_seen |= (
                    float(data.low[i]) < last_exit_fill
                    if is_long
                    else float(data.high[i]) > last_exit_fill
                )
                reclaim_crossed = (
                    close >= reclaim_level if is_long else close <= reclaim_level
                )
                if reclaim_crossed:
                    pending = {
                        "kind": "entry",
                        "requested_notional": max(BASE_UNIT, prior_exit_notional),
                        "absolute_target": True,
                        "reason": "reclaim",
                    }
                elif signals.entry_mult[i] > 0 and gap_seen:
                    pending = {
                        "kind": "entry",
                        "requested_notional": BASE_UNIT * float(signals.entry_mult[i]),
                        "absolute_target": curve.semantics == "target",
                        "reason": "ladder_lower" if is_long else "ladder_higher",
                    }
                elif (
                    close > reclaim_level if is_long else close < reclaim_level
                ):
                    beyond_reclaim += 1
            elif signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "requested_notional": BASE_UNIT * float(signals.entry_mult[i]),
                    "absolute_target": curve.semantics == "target",
                    "reason": "initial_ladder",
                }

    # Final liquidation makes every fold independently accountable.
    if has_position():
        px = float(data.close[right - 1]) * (
            1.0 - side_sign * slippage_rate
        )
        notional = abs(qty) * px
        cash += side_sign * (
            notional - side_sign * commission_rate * notional
        )
        qty = 0.0
    min_equity = min(min_equity, cash)
    final_equity = cash
    strategy_pnl = final_equity - ACCOUNT_EQUITY
    bh_entry = float(data.open[left]) * (
        1.0 + side_sign * slippage_rate
    )
    bh_exit = float(data.close[right - 1]) * (
        1.0 - side_sign * slippage_rate
    )
    bh_pnl = (
        BASE_UNIT * side_sign * (bh_exit - bh_entry) / bh_entry
        - 2.0 * commission_rate * BASE_UNIT
    )
    bars = right - left
    return {
        "capital_return_pct": 100.0 * strategy_pnl / BASE_UNIT,
        "side": side,
        "account_return_pct": 100.0 * strategy_pnl / ACCOUNT_EQUITY,
        "bh_capital_return_pct": 100.0 * bh_pnl / BASE_UNIT,
        "alpha_vs_bh_pp": 100.0 * (strategy_pnl - bh_pnl) / BASE_UNIT,
        # A non-positive side-aware B&H is not a denominator. In particular,
        # negative strategy / negative short-and-hold must never be presented
        # as a positive "multiple"; alpha and absolute P&L carry the verdict.
        "strategy_bh_multiple": strategy_pnl / bh_pnl if bh_pnl > 1e-12 else None,
        "max_drawdown_account_pct": max_dd,
        "minimum_account_equity_usd": min_equity,
        "insolvent": bool(min_equity <= 0.0),
        "binary_tim_pct": 100.0 * held_bars / bars,
        "exposure_weighted_tim_pct": 100.0 * weighted_exposure / bars,
        "peak_mark_to_market_notional_usd": peak_mark_notional,
        "peak_mark_to_market_capacity_pct": 100.0 * peak_mark_notional / CAPACITY,
        "peak_post_fill_notional_usd": peak_post_fill_notional,
        "entry_capacity_breach": bool(peak_post_fill_notional > CAPACITY + 1e-6),
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "fill_ratio": filled / requested if requested else 1.0,
        "clamp_count": clamp_count,
        "fill_count": fill_count,
        "exit_count": exit_count,
        "reclaim_reentries": reclaim_count,
        "lower_reentries": lower_count if is_long else 0,
        "higher_reentries": lower_count if not is_long else 0,
        "bars_flat_beyond_reclaim": beyond_reclaim,
        "start_ts": int(data.ts[left]),
        "end_ts": int(data.ts[right - 1]),
        "rows": bars,
    }


def _date_index(data: top.ExecutionData, value: str) -> int:
    return int(np.searchsorted(data.ts, _epoch(value), side="left"))


def _outer_windows(symbol: str, data: top.ExecutionData) -> list[tuple[str, str, str]]:
    if symbol == "VT":
        return [
            ("2024-07-11", "2025-01-01", "2025-05-01"),
            ("2024-07-11", "2025-05-01", "2025-09-01"),
            ("2024-07-11", "2025-09-01", "2026-03-29"),
        ]
    return [
        ("2024-03-26", "2025-01-01", "2025-07-01"),
        ("2024-03-26", "2025-07-01", "2026-01-01"),
        ("2024-03-26", "2026-01-01", "2026-07-25"),
    ]


def _inner_slices(left: int, right: int) -> list[tuple[int, int]]:
    # Three contiguous folds over the latter 75% of the available training
    # history; the oldest quarter remains feature/regime warmup.
    start = left + (right - left) // 4
    edges = np.linspace(start, right, 4, dtype=int)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(3)]


def _score(rows: list[dict[str, Any]]) -> float:
    alpha = float(np.median([r["alpha_vs_bh_pp"] for r in rows]))
    dd = float(np.max([r["max_drawdown_account_pct"] for r in rows]))
    fill = float(np.min([r["fill_ratio"] for r in rows]))
    # Robust alpha is primary. Drawdown and systematic clipping are explicit
    # tie-break penalties, not hidden post-selection filters.
    return alpha - 0.20 * dd - 5.0 * max(0.0, 0.75 - fill)


def run(args: argparse.Namespace) -> Path:
    symbol = args.symbol.upper()
    side = args.side.upper()
    data = top._load_execution(symbol, Path(args.npz_dir), args.start, "ladder", args.end)
    if not data.contract["valid"]:
        raise RuntimeError(f"{symbol} NPZ quarantined: {data.contract['errors']}")
    htfs = {tf: top._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
    curves = _curves(args.seed, args.random_curves)
    cache: dict[tuple[Curve, int], SignalData] = {}

    def sig(curve: Curve) -> SignalData:
        key = (curve, args.exit_n)
        if key not in cache:
            cache[key] = _build_signals(data, htfs, curve, args.exit_n, side)
        return cache[key]

    folds: list[dict[str, Any]] = []
    for fold_no, (train_start, validation_start, validation_end) in enumerate(
        _outer_windows(symbol, data), 1
    ):
        tl = _date_index(data, train_start)
        tr = _date_index(data, validation_start)
        vl, vr = tr, _date_index(data, validation_end)
        if tr - tl < 300 or vr - vl < 100:
            continue
        ranked: list[tuple[float, Curve, list[dict[str, Any]]]] = []
        for curve in curves:
            inner = [
                _simulate(
                    data,
                    sig(curve),
                    curve,
                    l,
                    r,
                    args.commission_bps / 10_000.0,
                    args.slippage_bps / 10_000.0,
                    side,
                )
                for l, r in _inner_slices(tl, tr)
            ]
            ranked.append((_score(inner), curve, inner))
        ranked.sort(key=lambda x: (-x[0], x[1].label))
        score, winner, inner = ranked[0]
        validation = _simulate(
            data,
            sig(winner),
            winner,
            vl,
            vr,
            args.commission_bps / 10_000.0,
            args.slippage_bps / 10_000.0,
            side,
        )
        folds.append(
            {
                "fold": fold_no,
                "train": [train_start, validation_start],
                "validation": [validation_start, validation_end],
                "selection_score": score,
                "selected_curve": dataclasses.asdict(winner),
                "inner_metrics": inner,
                "validation_metrics": validation,
            }
        )

    remembered = next(
        c
        for c in curves
        if c.label == "REM_linear_union_add"
    )
    full_left, full_right = 0, len(data.ts)
    remembered_result = _simulate(
        data,
        sig(remembered),
        remembered,
        full_left,
        full_right,
        args.commission_bps / 10_000.0,
        args.slippage_bps / 10_000.0,
        side,
    )

    # Frozen OOS aggregation is additive in dollars because every fold resets to
    # the same account and $2k comparison unit.
    validations = [f["validation_metrics"] for f in folds]
    aggregate = {
        "folds": len(validations),
        "capital_return_pct_sum": sum(r["capital_return_pct"] for r in validations),
        "bh_capital_return_pct_sum": sum(r["bh_capital_return_pct"] for r in validations),
        "alpha_vs_bh_pp_sum": sum(r["alpha_vs_bh_pp"] for r in validations),
        "max_drawdown_account_pct_max": max(
            (r["max_drawdown_account_pct"] for r in validations), default=0.0
        ),
        "exposure_weighted_tim_pct_row_weighted": (
            sum(r["exposure_weighted_tim_pct"] * r["rows"] for r in validations)
            / max(1, sum(r["rows"] for r in validations))
        ),
        "fill_ratio": (
            sum(r["filled_notional_usd"] for r in validations)
            / max(1e-12, sum(r["requested_notional_usd"] for r in validations))
        ),
        "clamp_count": sum(r["clamp_count"] for r in validations),
        "bars_flat_beyond_reclaim": sum(
            r["bars_flat_beyond_reclaim"] for r in validations
        ),
        "insolvent_folds": sum(bool(r["insolvent"]) for r in validations),
        "minimum_account_equity_usd": min(
            (r["minimum_account_equity_usd"] for r in validations),
            default=ACCOUNT_EQUITY,
        ),
    }
    aggregate["strategy_bh_multiple"] = (
        aggregate["capital_return_pct_sum"] / aggregate["bh_capital_return_pct_sum"]
        if aggregate["bh_capital_return_pct_sum"] > 1e-12
        else None
    )
    aggregate["control_failure"] = bool(
        aggregate["capital_return_pct_sum"] <= 0.0
        or aggregate["alpha_vs_bh_pp_sum"] <= 0.0
        or aggregate["insolvent_folds"] > 0
        or aggregate["max_drawdown_account_pct_max"] >= 100.0
    )
    aggregate["control_failure_reasons"] = [
        reason
        for failed, reason in (
            (
                aggregate["capital_return_pct_sum"] <= 0.0,
                "non_positive_strategy_return",
            ),
            (
                aggregate["alpha_vs_bh_pp_sum"] <= 0.0,
                "did_not_beat_side_aware_bh",
            ),
            (aggregate["insolvent_folds"] > 0, "account_insolvency"),
            (
                aggregate["max_drawdown_account_pct_max"] >= 100.0,
                "account_drawdown_at_least_100pct",
            ),
        )
        if failed
    ]

    # Trigger/causality audit is curve-independent except selected trigger and
    # stoch threshold; retain every frozen winner's evidence.
    causality = [
        {
            "fold": f["fold"],
            "curve": f["selected_curve"]["label"],
            "by_tf": sig(Curve(**f["selected_curve"])).causality,
        }
        for f in folds
    ]
    manifest = {
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "promotion_allowed": False,
        "symbol": symbol,
        "side": side,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "data_start": args.start,
        "data_end_exclusive": args.end,
        "npz": data.path,
        "npz_sha256": hashlib.sha256(Path(data.path).read_bytes()).hexdigest(),
        "contract": data.contract,
        "known_source_gaps": KNOWN_GAPS.get(symbol, ()),
        "base_unit_usd": BASE_UNIT,
        "account_equity_usd": ACCOUNT_EQUITY,
        "hard_capacity_usd": CAPACITY,
        "hard_max_multiplier": MAX_MULT,
        "commission_bps_one_way": args.commission_bps,
        "slippage_bps_one_way": args.slippage_bps,
        "exit": {"family": "E02_DONCHIAN", "tf": "4h", "n": args.exit_n},
        "reentry": (
            "ladder lower first; mandatory zero-buffer stored exit/top reclaim"
            if side == "LONG"
            else "ladder higher first; mandatory zero-buffer stored exit/bottom reclaim"
        ),
        "candidate_count": len(curves),
        "selection": "nested frozen 3-fold inner robust block/Pareto-style search",
    }
    payload = {
        "manifest": manifest,
        "remembered_hypothesis": {
            "curve": dataclasses.asdict(remembered),
            "note": "D bottom 10x is clipped to the hard 8x capacity",
            "full_sample_metrics_not_selection_evidence": remembered_result,
            "causality": sig(remembered).causality,
        },
        "outer_folds": folds,
        "frozen_oos_aggregate": aggregate,
        "causality_audit": causality,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out_dir) / f"band_ladder_walkforward_{stamp}_{symbol}_{side}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    with gzip.open(out / "frozen_fold_rows.jsonl.gz", "wt") as fh:
        for row in folds:
            fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    snap = out / "source_snapshot"
    snap.mkdir()
    snap.joinpath(Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"artifact": str(out), **aggregate}, sort_keys=True))
    data.z.close()
    return out


def _self_test() -> None:
    assert ladder_mult(-0.01, 10, 6, "linear") == 0.0
    assert ladder_mult(0.0, 10, 6, "linear") == 8.0
    assert ladder_mult(1.0, 10, 6, "linear") == 6.0
    assert ladder_mult(0.5, 6, 4, "linear") == 5.0
    assert ladder_mult(0.25, 6, 4, "center_plateau") == 6.0
    assert ladder_mult(2.0, 4, 1, "linear") == 1.0
    rows = _curves(7, 12)
    assert any(r.label.startswith("REM_") for r in rows)
    assert all(r.d_bottom >= r.d_top for r in rows)
    print(f"PASS {len(rows)} coherent curves")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MU")
    ap.add_argument("--side", default="LONG", choices=("LONG", "SHORT"))
    ap.add_argument("--npz-dir", default=str(top.DEFAULT_NPZ))
    ap.add_argument("--out-dir", default=str(top.DEFAULT_OUT))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    ap.add_argument("--exit-n", type=int, default=30)
    ap.add_argument("--random-curves", type=int, default=160)
    ap.add_argument("--seed", type=int, default=20260725)
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        _self_test()
        return
    if args.symbol.upper() == "HAO":
        # HAO may only proceed after the same ladder data contract passes.
        # ``_load_execution`` enforces that contract and raises with the exact
        # quarantine reasons.
        pass
    run(args)


if __name__ == "__main__":
    main()
