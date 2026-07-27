#!/usr/bin/env python3
"""Preregistered MU_LONG ladder stability screen on the parent-clock v3 NPZ.

This is a research-only, two-stage screen:

* folds 1 and 2 are discovery folds;
* exactly one discovery survivor may be frozen before fold 3 is opened;
* exact replay is permitted only when that untouched fold also passes.

E02 4h N=30 stays fixed.  The grid changes coherent ladder shapes, signal
families, interpolation, an effective ladder cap, and a bounded reclaim
observation cadence.  It deliberately excludes additive sizing: the source
control's 150 fold-2 clamps are the defect being isolated.
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
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools.research_availability_clock import (  # noqa: E402
    CLOCK_CONTRACT,
    next_strictly_later_index,
)


CONTRACT = "MU_LADDER_STABILITY_PARENT_CLOCK_V3"
BASELINE_BY_FOLD = {
    1: 226.67277986608406,
    2: 909.1467733675928,
    3: 1316.0213778308714,
}
WINDOWS = (
    (1, "2025-01-01", "2025-07-01"),
    (2, "2025-07-01", "2026-01-01"),
    (3, "2026-01-01", "2026-07-25"),
)
MAX_CLAMPS_PER_FOLD = 5


@dataclasses.dataclass(frozen=True)
class Shape:
    label: str
    values: tuple[float, float, float, float, float, float]
    stoch_low: float


@dataclasses.dataclass(frozen=True)
class Candidate:
    label: str
    curve: ladder.Curve
    effective_cap_x: float
    reclaim_cadence: str
    exact_eligible: bool


SHAPES = (
    Shape("DEEP_TILT", (8.0, 1.0, 6.0, 1.0, 4.0, 0.5), 30.0),
    Shape("DEEP_SOFT", (7.0, 1.5, 5.0, 1.25, 3.0, 0.75), 30.0),
    Shape("DAILY_DEEP", (8.0, 2.0, 4.0, 1.5, 2.0, 0.75), 30.0),
    Shape("BALANCED", (7.0, 3.0, 5.0, 2.0, 3.0, 1.0), 30.0),
    Shape("MID_TILT", (6.0, 2.0, 4.0, 1.5, 2.0, 0.75), 20.0),
    Shape("HIGH_CONTRAST", (8.0, 0.75, 5.0, 0.75, 3.0, 0.5), 40.0),
)


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def preregistered_candidates() -> list[Candidate]:
    """Return the byte-stable, coherent grid declared before result access."""
    rows: list[Candidate] = []
    seen: set[tuple[Any, ...]] = set()
    for shape in SHAPES:
        for mode in ("linear", "center_plateau"):
            for trigger in ("green", "union", "wt_state", "union_state"):
                for cap in (5.0, 6.0, 7.0, 8.0):
                    values = tuple(min(cap, value) for value in shape.values)
                    for cadence in (
                        "close_confirm_next_availability",
                        "intrabar_touch_next_availability",
                    ):
                        curve = ladder.Curve(
                            label=(
                                f"{shape.label}_{mode}_{trigger}_TARGET_"
                                f"CAP{cap:g}_{cadence}"
                            ),
                            mode=mode,
                            trigger=trigger,
                            semantics="target",
                            stoch_low=shape.stoch_low,
                            d_bottom=values[0],
                            d_top=values[1],
                            h4_bottom=values[2],
                            h4_top=values[3],
                            h1_bottom=values[4],
                            h1_top=values[5],
                        )
                        key = (
                            dataclasses.astuple(curve)[1:],
                            cadence,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(
                            Candidate(
                                label=curve.label,
                                curve=curve,
                                effective_cap_x=cap,
                                reclaim_cadence=cadence,
                                exact_eligible=(
                                    cadence
                                    == "close_confirm_next_availability"
                                ),
                            )
                        )
    return rows


def _simulate(
    data: Any,
    signals: ladder.SignalData,
    candidate: Candidate,
    left: int,
    right: int,
    commission_rate: float,
    slippage_rate: float,
) -> dict[str, Any]:
    """Stateful LONG accounting with a bounded reclaim-cadence branch."""
    if right - left < 100:
        raise ValueError("simulation window too short")
    cash = ladder.ACCOUNT_EQUITY
    qty = 0.0
    last_exit_fill = math.nan
    reclaim_level = math.nan
    prior_exit_notional = 0.0
    gap_seen = False
    pending: dict[str, Any] | None = None
    peak_equity = min_equity = ladder.ACCOUNT_EQUITY
    max_dd = requested = filled = 0.0
    clamp_count = fill_count = exit_count = reclaim_count = lower_count = 0
    held_bars = 0
    weighted_exposure = 0.0
    peak_mark_notional = peak_post_fill_notional = 0.0
    beyond_reclaim = 0
    reclaim_outstanding = False

    for i in range(left, right):
        op = float(data.open[i])
        close = float(data.close[i])
        if pending is not None and i == int(pending["fill_index"]):
            if pending["kind"] == "exit" and qty > 1e-12:
                px = op * (1.0 - slippage_rate)
                notional = qty * px
                cash += notional - commission_rate * notional
                prior_exit_notional = min(ladder.CAPACITY, notional)
                last_exit_fill = px
                reclaim_level = max(px, float(pending["ref"]))
                reclaim_outstanding = True
                qty = 0.0
                gap_seen = False
                exit_count += 1
            elif pending["kind"] == "entry":
                px = op * (1.0 + slippage_rate)
                current = qty * px
                target = float(pending["requested_notional"])
                want = max(0.0, target - current)
                actual = min(want, max(0.0, ladder.CAPACITY - current))
                requested += want
                filled += actual
                clamp_count += int(actual + 1e-9 < want)
                if actual > 0.0:
                    cash -= actual + commission_rate * actual
                    qty += actual / px
                    peak_post_fill_notional = max(
                        peak_post_fill_notional, qty * px
                    )
                    fill_count += 1
                    reclaim_count += int(
                        pending.get("reason") == "reclaim"
                    )
                    lower_count += int(
                        pending.get("reason") == "ladder_lower"
                    )
                    if pending.get("reason") in {
                        "reclaim",
                        "ladder_lower",
                    }:
                        reclaim_outstanding = False
            pending = None

        mark_equity = cash + qty * close
        min_equity = min(min_equity, mark_equity)
        peak_equity = max(peak_equity, mark_equity)
        if peak_equity > 0:
            max_dd = max(
                max_dd,
                100.0 * (peak_equity - mark_equity) / peak_equity,
            )
        notional = qty * close
        peak_mark_notional = max(peak_mark_notional, notional)
        held_bars += int(qty > 1e-12)
        weighted_exposure += min(ladder.CAPACITY, notional) / ladder.CAPACITY

        if pending is not None or i + 1 >= right:
            continue
        fill_index = next_strictly_later_index(data.ts, i, right)
        if fill_index is None:
            continue
        if qty > 1e-12:
            if signals.exit_event[i]:
                ref = float(signals.exit_ref[i])
                if not math.isfinite(ref):
                    ref = float(data.high[i])
                pending = {
                    "kind": "exit",
                    "ref": ref,
                    "fill_index": fill_index,
                }
            elif signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "requested_notional": (
                        ladder.BASE_UNIT * float(signals.entry_mult[i])
                    ),
                    "reason": "ladder_add",
                    "fill_index": fill_index,
                }
        else:
            if math.isfinite(last_exit_fill):
                gap_seen |= float(data.low[i]) < last_exit_fill
                if candidate.reclaim_cadence.startswith("intrabar_touch"):
                    reclaim_crossed = float(data.high[i]) >= reclaim_level
                else:
                    reclaim_crossed = close >= reclaim_level
                if reclaim_crossed:
                    pending = {
                        "kind": "entry",
                        "requested_notional": max(
                            ladder.BASE_UNIT, prior_exit_notional
                        ),
                        "reason": "reclaim",
                        "fill_index": fill_index,
                    }
                elif signals.entry_mult[i] > 0 and gap_seen:
                    pending = {
                        "kind": "entry",
                        "requested_notional": (
                            ladder.BASE_UNIT * float(signals.entry_mult[i])
                        ),
                        "reason": "ladder_lower",
                        "fill_index": fill_index,
                    }
                elif close > reclaim_level:
                    beyond_reclaim += 1
            elif signals.entry_mult[i] > 0:
                pending = {
                    "kind": "entry",
                    "requested_notional": (
                        ladder.BASE_UNIT * float(signals.entry_mult[i])
                    ),
                    "reason": "initial_ladder",
                    "fill_index": fill_index,
                }

    if qty > 1e-12:
        px = float(data.close[right - 1]) * (1.0 - slippage_rate)
        notional = qty * px
        cash += notional - commission_rate * notional
        qty = 0.0
    min_equity = min(min_equity, cash)
    strategy_pnl = cash - ladder.ACCOUNT_EQUITY
    bh_entry = float(data.open[left]) * (1.0 + slippage_rate)
    bh_exit = float(data.close[right - 1]) * (1.0 - slippage_rate)
    bh_pnl = (
        ladder.BASE_UNIT * (bh_exit - bh_entry) / bh_entry
        - 2.0 * commission_rate * ladder.BASE_UNIT
    )
    bars = right - left
    return {
        "capital_return_pct": 100.0 * strategy_pnl / ladder.BASE_UNIT,
        "bh_capital_return_pct": 100.0 * bh_pnl / ladder.BASE_UNIT,
        "alpha_vs_bh_pp": 100.0 * (strategy_pnl - bh_pnl) / ladder.BASE_UNIT,
        "alpha_vs_source_control_pp": (
            100.0 * strategy_pnl / ladder.BASE_UNIT
        ),
        "strategy_bh_multiple": (
            strategy_pnl / bh_pnl if bh_pnl > 1e-12 else None
        ),
        "max_drawdown_account_pct": max_dd,
        "minimum_account_equity_usd": min_equity,
        "insolvent": bool(min_equity <= 0),
        "binary_tim_pct": 100.0 * held_bars / bars,
        "exposure_weighted_tim_pct": 100.0 * weighted_exposure / bars,
        "peak_mark_to_market_notional_usd": peak_mark_notional,
        "peak_post_fill_notional_usd": peak_post_fill_notional,
        "entry_capacity_breach": bool(
            peak_post_fill_notional > ladder.CAPACITY + 1e-6
        ),
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "fill_ratio": filled / requested if requested else 1.0,
        "clamp_count": clamp_count,
        "fill_count": fill_count,
        "exit_count": exit_count,
        "reclaim_reentries": reclaim_count,
        "lower_reentries": lower_count,
        "bars_flat_beyond_reclaim": beyond_reclaim,
        "reclaim_obligations_unfilled_at_end": int(
            reclaim_outstanding and qty <= 1e-12
        ),
        "rows": bars,
        "start_ts": int(data.ts[left]),
        "end_ts": int(data.ts[right - 1]),
    }


def _gate(metrics: dict[str, Any], fold: int) -> tuple[bool, list[str]]:
    metrics["alpha_vs_source_control_pp"] = (
        metrics["capital_return_pct"] - BASELINE_BY_FOLD[fold]
    )
    failures = [
        name
        for failed, name in (
            (
                metrics["capital_return_pct"]
                < 2.0 * metrics["bh_capital_return_pct"],
                "below_2x_bh",
            ),
            (
                metrics["alpha_vs_source_control_pp"] <= 0,
                "not_above_source_ladder_e02",
            ),
            (
                not 70.0 <= metrics["exposure_weighted_tim_pct"] <= 80.0,
                "weighted_tim_outside_70_80",
            ),
            (metrics["insolvent"], "insolvent"),
            (
                metrics["minimum_account_equity_usd"] <= 0,
                "nonpositive_min_equity",
            ),
            (
                metrics["entry_capacity_breach"],
                "entry_capacity_breach",
            ),
            (
                metrics["clamp_count"] > MAX_CLAMPS_PER_FOLD,
                "clamps_above_preregistered_limit",
            ),
            (
                metrics["bars_flat_beyond_reclaim"] > 0,
                "flat_beyond_reclaim",
            ),
            (
                metrics["reclaim_obligations_unfilled_at_end"] > 0,
                "unfilled_reclaim_at_end",
            ),
        )
        if failed
    ]
    return not failures, failures


def _prereg_payload(args: argparse.Namespace, candidates: list[Candidate]) -> dict:
    rows = [
        {
            "label": row.label,
            "curve": dataclasses.asdict(row.curve),
            "effective_cap_x": row.effective_cap_x,
            "reclaim_cadence": row.reclaim_cadence,
            "exact_eligible": row.exact_eligible,
        }
        for row in candidates
    ]
    return {
        "contract": CONTRACT,
        "created_before_result_access": True,
        "symbol": "MU",
        "side": "LONG",
        "npz_dir": str(Path(args.npz_dir).resolve()),
        "npz_required_sha256": args.npz_sha256,
        "availability_clock": CLOCK_CONTRACT,
        "windows": [list(row) for row in WINDOWS],
        "discovery_folds": [1, 2],
        "untouched_final_fold": 3,
        "exit": {"family": "E02_DONCHIAN", "tf": "4h", "n": 30},
        "costs": {
            "commission_bps_one_way": args.commission_bps,
            "slippage_bps_one_way": args.slippage_bps,
        },
        "account": {
            "base_benchmark_usd": ladder.BASE_UNIT,
            "capacity_usd": ladder.CAPACITY,
            "solvency_account_usd": ladder.ACCOUNT_EQUITY,
        },
        "gates": {
            "minimum_multiple_of_positive_side_bh_each_fold": 2.0,
            "positive_alpha_vs_source_ladder_e02_each_fold": True,
            "weighted_tim_pct_each_fold": [70.0, 80.0],
            "maximum_clamps_each_fold": MAX_CLAMPS_PER_FOLD,
            "no_insolvency": True,
            "no_future_htf": True,
            "no_flat_beyond_reclaim": True,
        },
        "selection": (
            "discovery folds only; exact-eligible strict survivors ranked by "
            "minimum fold alpha versus source ladder/E02, then TIM distance, "
            "then label; one frozen winner opens fold 3"
        ),
        "candidate_count": len(rows),
        "candidates": rows,
        "no_live_or_canonical_writes": True,
    }


def run(args: argparse.Namespace) -> Path:
    candidates = preregistered_candidates()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    prereg = _prereg_payload(args, candidates)
    prereg_path = out / "preregistered_grid.json"
    prereg_path.write_text(json.dumps(prereg, indent=2, sort_keys=True) + "\n")
    prereg_sha = hashlib.sha256(prereg_path.read_bytes()).hexdigest()

    npz_path = Path(args.npz_dir).resolve() / "MU.npz"
    actual_sha = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    if actual_sha != args.npz_sha256:
        raise RuntimeError(
            f"immutable NPZ mismatch: {actual_sha} != {args.npz_sha256}"
        )
    data = ladder.top._load_execution(
        "MU", Path(args.npz_dir), "2024-01-01", "ladder", "2026-07-25"
    )
    try:
        if not data.contract["valid"]:
            raise RuntimeError(f"MU NPZ quarantined: {data.contract['errors']}")
        htfs = {
            tf: ladder.top._compress_htf(data, tf)
            for tf in ladder.TF_ORDER
        }
        signals: dict[ladder.Curve, ladder.SignalData] = {}

        def get_signals(curve: ladder.Curve) -> ladder.SignalData:
            if curve not in signals:
                signals[curve] = ladder._build_signals(
                    data, htfs, curve, 30, "LONG"
                )
            return signals[curve]

        discovery: list[dict[str, Any]] = []
        for number, candidate in enumerate(candidates, 1):
            fold_rows = []
            for fold, start, end in WINDOWS[:2]:
                metrics = _simulate(
                    data,
                    get_signals(candidate.curve),
                    candidate,
                    ladder._date_index(data, start),
                    ladder._date_index(data, end),
                    args.commission_bps / 10_000.0,
                    args.slippage_bps / 10_000.0,
                )
                passed, failures = _gate(metrics, fold)
                fold_rows.append(
                    {
                        "fold": fold,
                        "window": [start, end],
                        "metrics": metrics,
                        "gate_pass": passed,
                        "gate_failures": failures,
                    }
                )
            future = sum(
                int(tf["source_timestamp_future_count"])
                for tf in get_signals(candidate.curve).causality.values()
            )
            strict = (
                candidate.exact_eligible
                and future == 0
                and all(row["gate_pass"] for row in fold_rows)
            )
            discovery.append(
                {
                    "candidate_number": number,
                    "candidate": {
                        "label": candidate.label,
                        "curve": dataclasses.asdict(candidate.curve),
                        "effective_cap_x": candidate.effective_cap_x,
                        "reclaim_cadence": candidate.reclaim_cadence,
                        "exact_eligible": candidate.exact_eligible,
                    },
                    "future_htf_count": future,
                    "folds": fold_rows,
                    "strict_discovery_survivor": strict,
                }
            )

        eligible = [
            row for row in discovery if row["strict_discovery_survivor"]
        ]
        eligible.sort(
            key=lambda row: (
                -min(
                    fold["metrics"]["alpha_vs_source_control_pp"]
                    for fold in row["folds"]
                ),
                sum(
                    abs(
                        fold["metrics"]["exposure_weighted_tim_pct"] - 75.0
                    )
                    for fold in row["folds"]
                ),
                row["candidate"]["label"],
            )
        )
        frozen = eligible[0] if eligible else None
        final = None
        if frozen is not None:
            candidate = candidates[frozen["candidate_number"] - 1]
            fold, start, end = WINDOWS[2]
            metrics = _simulate(
                data,
                get_signals(candidate.curve),
                candidate,
                ladder._date_index(data, start),
                ladder._date_index(data, end),
                args.commission_bps / 10_000.0,
                args.slippage_bps / 10_000.0,
            )
            passed, failures = _gate(metrics, fold)
            final = {
                "fold": fold,
                "window": [start, end],
                "candidate_number": frozen["candidate_number"],
                "candidate": frozen["candidate"],
                "metrics": metrics,
                "gate_pass": passed,
                "gate_failures": failures,
                "exact_replay_allowed": bool(passed),
            }

        ranked = sorted(
            discovery,
            key=lambda row: (
                -sum(fold["gate_pass"] for fold in row["folds"]),
                sum(
                    len(fold["gate_failures"]) for fold in row["folds"]
                ),
                sum(
                    abs(
                        fold["metrics"]["exposure_weighted_tim_pct"] - 75.0
                    )
                    for fold in row["folds"]
                ),
                row["candidate"]["label"],
            ),
        )
        payload = {
            "manifest": {
                "contract": CONTRACT,
                "tier": "VEC_RESEARCH",
                "promotion_allowed": False,
                "matrix_written": False,
                "preregistered_grid": str(prereg_path),
                "preregistered_grid_sha256": prereg_sha,
                "npz": str(npz_path),
                "npz_sha256": actual_sha,
                "availability_clock": CLOCK_CONTRACT,
                "candidate_count": len(candidates),
            },
            "summary": {
                "discovery_candidates": len(discovery),
                "strict_discovery_survivors": len(eligible),
                "final_opened": final is not None,
                "final_pass": bool(final and final["gate_pass"]),
                "exact_replay_allowed": bool(
                    final and final["exact_replay_allowed"]
                ),
                "path_fleet_verdict": (
                    "RESEARCH_SURVIVOR_REQUIRES_EXACT"
                    if final and final["gate_pass"]
                    else "GRAY_NO_ALL_FOLD_SURVIVOR"
                ),
            },
            "frozen_selection": frozen,
            "untouched_final": final,
            "top_discovery_rows": ranked[:24],
            "discovery": discovery,
        }
        (out / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps(payload["summary"], sort_keys=True))
        return out
    finally:
        data.z.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument(
        "--npz-sha256",
        default=(
            "82b9110fac514bc9eddfe4bc50227ab135e4d55783f7c712dc2b85e3eca77def"
        ),
    )
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    args = ap.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
