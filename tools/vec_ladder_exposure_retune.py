#!/usr/bin/env python3
"""Nested, causal exposure retuning for a frozen band-ladder/E02 control.

This is an ENTRY-only research adapter.  Every exit, fill, capacity, cost and
resting-reclaim rule is inherited unchanged from ``vec_band_ladder_walkforward``.
For each outer fold it builds coherent multiplier/shape/trigger candidates from
earlier data, selects without seeing the validation rows, and compares the
untouched validation result with both side-aware B&H and the exact source
ladder/E02 control.

The output deliberately matches the ordinary ladder artifact schema so strict
vector survivors can be passed to ``run_v8_research_ladder_replay.py``.
Nothing here writes the switch matrix or live/per-symbol configuration.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import vec_band_ladder_walkforward as ladder  # noqa: E402
import vec_top_exit_campaign as top  # noqa: E402


NUMERIC_FIELDS = (
    "stoch_low",
    "d_bottom",
    "d_top",
    "h4_bottom",
    "h4_top",
    "h1_bottom",
    "h1_top",
)
CATEGORICAL_FIELDS = ("mode", "trigger", "semantics")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _curve_key(curve: ladder.Curve) -> tuple[Any, ...]:
    return dataclasses.astuple(curve)[1:]


def _local_curves(base: ladder.Curve, fold: int) -> list[ladder.Curve]:
    """Coherent neighborhoods around the frozen source curve.

    A scale changes all six order sizes together. ``lower_heavy`` and
    ``upper_heavy`` change the curve slope as a block across all timeframes;
    no single multiplier is optimized in isolation.
    """
    rows: list[ladder.Curve] = [base]
    scales = (0.35, 0.50, 0.70, 0.90, 1.15, 1.50)
    shapes = {
        "same": (1.0, 1.0),
        "lower_heavy": (1.15, 0.80),
    }
    for scale in scales:
        for shape_name, (bottom_scale, top_scale) in shapes.items():
            values = []
            for tf in ladder.TF_ORDER:
                bottom, top_value = base.pair(tf)
                bottom = max(0.05, bottom * scale * bottom_scale)
                top_value = max(0.025, top_value * scale * top_scale)
                # Preserve the remembered ladder direction: size does not grow
                # toward the upper band for LONG.
                top_value = min(top_value, bottom)
                values.extend((bottom, top_value))
            profiles = [
                (mode, trigger, semantics)
                for mode in ("linear", "center_plateau")
                for trigger in ("green", "structure", "union")
                for semantics in ("target", "add")
            ]
            # Persistent WT state is evaluated only on newly completed HTF
            # bars. Repeated additive orders would be capacity churn, so state
            # profiles deliberately use absolute targets only.
            profiles.extend(
                (mode, trigger, "target")
                for mode in ("linear", "center_plateau")
                for trigger in ("wt_state", "union_state")
            )
            for mode, trigger, semantics in profiles:
                stoch = 30.0
                rows.append(
                    ladder.Curve(
                        label=(
                            f"LOCAL_F{fold}_{shape_name}_S{scale:g}_"
                            f"{mode}_{trigger}_{semantics}_K{stoch:g}"
                        ),
                        mode=mode,
                        trigger=trigger,
                        semantics=semantics,
                        stoch_low=stoch,
                        d_bottom=values[0],
                        d_top=values[1],
                        h4_bottom=values[2],
                        h4_top=values[3],
                        h1_bottom=values[4],
                        h1_top=values[5],
                    )
                )
    for stoch in (20.0, 40.0):
        rows.append(dataclasses.replace(base, label=f"LOCAL_F{fold}_SOURCE_K{stoch:g}", stoch_low=stoch))
    unique: dict[tuple[Any, ...], ladder.Curve] = {}
    for row in rows:
        unique.setdefault(_curve_key(row), row)
    return list(unique.values())


def _weighted_tim(rows: list[dict[str, Any]]) -> float:
    total_rows = sum(int(row["rows"]) for row in rows)
    return (
        sum(
            float(row["exposure_weighted_tim_pct"]) * int(row["rows"])
            for row in rows
        )
        / max(1, total_rows)
    )


def _range_summary(
    rows: list[tuple[ladder.Curve, list[dict[str, Any]]]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"count": len(rows)}
    if not rows:
        return result
    for field in NUMERIC_FIELDS:
        values = [float(getattr(curve, field)) for curve, _ in rows]
        result[field] = {
            "min": min(values),
            "max": max(values),
            "median": float(np.median(values)),
        }
    for field in CATEGORICAL_FIELDS:
        result[field] = dict(
            sorted(Counter(str(getattr(curve, field)) for curve, _ in rows).items())
        )
    result["weighted_tim_pct"] = {
        "min": min(_weighted_tim(metrics) for _, metrics in rows),
        "max": max(_weighted_tim(metrics) for _, metrics in rows),
    }
    return result


def _aggregate(validations: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sum(int(row["rows"]) for row in validations)
    strategy = sum(float(row["capital_return_pct"]) for row in validations)
    bh = sum(float(row["bh_capital_return_pct"]) for row in validations)
    return {
        "folds": len(validations),
        "capital_return_pct_sum": strategy,
        "bh_capital_return_pct_sum": bh,
        "alpha_vs_bh_pp_sum": strategy - bh,
        "strategy_bh_multiple": strategy / bh if bh > 1e-12 else None,
        "max_drawdown_account_pct_max": max(
            (float(row["max_drawdown_account_pct"]) for row in validations),
            default=0.0,
        ),
        "exposure_weighted_tim_pct_row_weighted": (
            sum(
                float(row["exposure_weighted_tim_pct"]) * int(row["rows"])
                for row in validations
            )
            / max(1, rows)
        ),
        "fill_ratio": (
            sum(float(row["filled_notional_usd"]) for row in validations)
            / max(
                1e-12,
                sum(float(row["requested_notional_usd"]) for row in validations),
            )
        ),
        "clamp_count": sum(int(row["clamp_count"]) for row in validations),
        "bars_flat_beyond_reclaim": sum(
            int(row["bars_flat_beyond_reclaim"]) for row in validations
        ),
        "insolvent_folds": sum(bool(row["insolvent"]) for row in validations),
        "minimum_account_equity_usd": min(
            (float(row["minimum_account_equity_usd"]) for row in validations),
            default=ladder.ACCOUNT_EQUITY,
        ),
    }


def run(args: argparse.Namespace) -> Path:
    artifact = args.control_artifact.resolve()
    source = json.loads((artifact / "result.json").read_text())
    manifest = source["manifest"]
    symbol = str(manifest["symbol"]).upper()
    side = str(manifest["side"]).upper()
    if side != "LONG":
        raise RuntimeError("this cohort retune is LONG-only; SHORT needs its own mirror")
    if int(manifest["exit"]["n"]) != 30:
        raise RuntimeError("control exit must remain E02 4h N=30")
    if manifest.get("promotion_allowed") or manifest.get("matrix_eligible"):
        raise RuntimeError("control artifact must remain research-only")

    npz_path = Path(args.npz_path or manifest["npz"]).resolve()
    data = top._load_execution(
        symbol,
        npz_path.parent,
        str(manifest.get("data_start") or args.start),
        "ladder",
        args.end,
    )
    if not data.contract["valid"]:
        raise RuntimeError(f"{symbol} NPZ quarantined: {data.contract['errors']}")
    expected_sha = str(manifest["npz_sha256"])
    actual_sha = _sha256(Path(data.path))
    if actual_sha != expected_sha:
        data.z.close()
        raise RuntimeError(
            f"{symbol} immutable NPZ mismatch: {actual_sha} != {expected_sha}"
        )
    htfs = {tf: top._compress_htf(data, tf) for tf in ladder.TF_ORDER}
    commission = float(manifest["commission_bps_one_way"]) / 10_000.0
    slippage = float(manifest["slippage_bps_one_way"]) / 10_000.0
    signal_cache: dict[ladder.Curve, ladder.SignalData] = {}

    def signals(curve: ladder.Curve) -> ladder.SignalData:
        if curve not in signal_cache:
            signal_cache[curve] = ladder._build_signals(data, htfs, curve, 30, side)
        return signal_cache[curve]

    folds: list[dict[str, Any]] = []
    for source_fold in source["outer_folds"]:
        fold_no = int(source_fold["fold"])
        source_curve = ladder.Curve(**source_fold["selected_curve"])
        candidates = _local_curves(source_curve, fold_no)
        if args.include_global_archetypes:
            candidates.extend(
                ladder._curves(args.seed + fold_no, args.random_curves)
            )
        # Every source-fold curve stays eligible, protecting the exact control
        # and improving cross-fold stability without validation leakage.
        candidates.extend(
            ladder.Curve(**row["selected_curve"]) for row in source["outer_folds"]
        )
        unique: dict[tuple[Any, ...], ladder.Curve] = {}
        for curve in candidates:
            unique.setdefault(_curve_key(curve), curve)
        candidates = list(unique.values())

        train_start, validation_start = source_fold["train"]
        _, validation_end = source_fold["validation"]
        tl = ladder._date_index(data, train_start)
        tr = ladder._date_index(data, validation_start)
        vl = tr
        vr = ladder._date_index(data, validation_end)
        inner_slices = ladder._inner_slices(tl, tr)
        control_inner = [
            ladder._simulate(
                data,
                signals(source_curve),
                source_curve,
                left,
                right,
                commission,
                slippage,
                side,
            )
            for left, right in inner_slices
        ]
        evaluated: list[
            tuple[
                tuple[Any, ...],
                ladder.Curve,
                list[dict[str, Any]],
                dict[str, Any],
            ]
        ] = []
        training_target_above_bh: list[
            tuple[ladder.Curve, list[dict[str, Any]]]
        ] = []
        training_target_non_degrade: list[
            tuple[ladder.Curve, list[dict[str, Any]]]
        ] = []
        control_sum = sum(float(row["capital_return_pct"]) for row in control_inner)
        for curve in candidates:
            inner = [
                ladder._simulate(
                    data,
                    signals(curve),
                    curve,
                    left,
                    right,
                    commission,
                    slippage,
                    side,
                )
                for left, right in inner_slices
            ]
            tim = _weighted_tim(inner)
            inner_tim = [
                float(row["exposure_weighted_tim_pct"]) for row in inner
            ]
            per_inner_distance = [
                max(
                    args.target_tim_low - value,
                    0.0,
                    value - args.target_tim_high,
                )
                for value in inner_tim
            ]
            distance = max(per_inner_distance, default=math.inf)
            tim_spread = (
                max(inner_tim) - min(inner_tim) if inner_tim else math.inf
            )
            all_above_bh = all(
                float(row["capital_return_pct"])
                > float(row["bh_capital_return_pct"])
                for row in inner
            )
            all_non_degrade = all(
                float(row["capital_return_pct"])
                >= float(control["capital_return_pct"]) - args.control_tolerance_pp
                for row, control in zip(inner, control_inner)
            )
            candidate_sum = sum(float(row["capital_return_pct"]) for row in inner)
            aggregate_non_degrade = (
                candidate_sum >= control_sum - args.control_tolerance_pp
            )
            # The aggregate can look acceptable while individual regimes range
            # from 20% to 95%. Require every inner fold in-band.
            target = distance <= 1e-12
            if target and all_above_bh:
                training_target_above_bh.append((curve, inner))
            if target and all_above_bh and all_non_degrade:
                training_target_non_degrade.append((curve, inner))
            # Exact same-control preservation wins first, then aggregate
            # preservation, then target+B&H. Distance is only relevant when no
            # target-compliant candidate exists. Robust alpha breaks ties.
            tier = (
                0
                if target and all_above_bh and all_non_degrade
                else 1
                if target and all_above_bh and aggregate_non_degrade
                else 2
                if target and all_above_bh
                else 3
                if all_above_bh
                else 4
            )
            score = ladder._score(inner)
            rank = (
                tier,
                distance,
                tim_spread,
                -int(all_non_degrade),
                -int(aggregate_non_degrade),
                -score,
                -candidate_sum,
                curve.label,
            )
            evaluated.append(
                (
                    rank,
                    curve,
                    inner,
                    {
                        "weighted_tim_pct": tim,
                        "weighted_tim_pct_by_inner_fold": inner_tim,
                        "weighted_tim_spread_pp": tim_spread,
                        "all_inner_exposure_target": target,
                        "all_above_bh": all_above_bh,
                        "all_non_degrade": all_non_degrade,
                        "aggregate_non_degrade": aggregate_non_degrade,
                        "candidate_return_pct_sum": candidate_sum,
                        "control_return_pct_sum": control_sum,
                    },
                )
            )
        evaluated.sort(key=lambda row: row[0])
        _, winner, inner, training = evaluated[0]
        validation = ladder._simulate(
            data,
            signals(winner),
            winner,
            vl,
            vr,
            commission,
            slippage,
            side,
        )
        control_validation = ladder._simulate(
            data,
            signals(source_curve),
            source_curve,
            vl,
            vr,
            commission,
            slippage,
            side,
        )
        folds.append(
            {
                "fold": fold_no,
                "train": source_fold["train"],
                "validation": source_fold["validation"],
                "selection_policy": (
                    "target TIM; all inner >B&H; each inner >= identical control; "
                    "then relaxed tiers; robust alpha tie-break"
                ),
                "candidate_count": len(candidates),
                "selected_curve": dataclasses.asdict(winner),
                "source_control_curve": dataclasses.asdict(source_curve),
                "selection_score": ladder._score(inner),
                "training_constraints": training,
                "training_target_above_bh_range": _range_summary(
                    training_target_above_bh
                ),
                "training_target_non_degrade_range": _range_summary(
                    training_target_non_degrade
                ),
                "inner_metrics": inner,
                "source_control_inner_metrics": control_inner,
                "validation_metrics": validation,
                "same_frozen_ladder_e02_control": control_validation,
                "beats_bh": (
                    float(validation["capital_return_pct"])
                    > float(validation["bh_capital_return_pct"])
                ),
                "does_not_degrade_control": (
                    float(validation["capital_return_pct"])
                    >= float(control_validation["capital_return_pct"])
                    - args.control_tolerance_pp
                ),
                "delta_vs_control_pp": (
                    float(validation["capital_return_pct"])
                    - float(control_validation["capital_return_pct"])
                ),
            }
        )

    validations = [row["validation_metrics"] for row in folds]
    controls = [row["same_frozen_ladder_e02_control"] for row in folds]
    aggregate = _aggregate(validations)
    control_aggregate = _aggregate(controls)
    tim = float(aggregate["exposure_weighted_tim_pct_row_weighted"])
    future_count = sum(
        int(row["source_timestamp_future_count"])
        for signal in signal_cache.values()
        for row in signal.causality.values()
    )
    aggregate.update(
        {
            "same_control_capital_return_pct_sum": control_aggregate[
                "capital_return_pct_sum"
            ],
            "delta_vs_same_control_pp_sum": (
                aggregate["capital_return_pct_sum"]
                - control_aggregate["capital_return_pct_sum"]
            ),
            "candidate_control_multiple": (
                aggregate["capital_return_pct_sum"]
                / control_aggregate["capital_return_pct_sum"]
                if control_aggregate["capital_return_pct_sum"] > 1e-12
                else None
            ),
            "all_folds_beat_bh": all(row["beats_bh"] for row in folds),
            "all_folds_non_degrade_control": all(
                row["does_not_degrade_control"] for row in folds
            ),
            "future_htf_source_count": future_count,
            "exposure_target": [args.target_tim_low, args.target_tim_high],
            "exposure_target_pass": args.target_tim_low <= tim <= args.target_tim_high,
        }
    )
    aggregate["vector_survivor"] = bool(
        aggregate["exposure_target_pass"]
        and aggregate["all_folds_beat_bh"]
        and aggregate["all_folds_non_degrade_control"]
        and aggregate["bars_flat_beyond_reclaim"] == 0
        and aggregate["insolvent_folds"] == 0
        and future_count == 0
    )
    aggregate["relaxed_bh_survivor"] = bool(
        aggregate["exposure_target_pass"]
        and aggregate["all_folds_beat_bh"]
        and aggregate["bars_flat_beyond_reclaim"] == 0
        and aggregate["insolvent_folds"] == 0
        and future_count == 0
    )

    output = {
        "manifest": {
            "tier": "VEC_RESEARCH_LADDER_EXPOSURE_RETUNE",
            "matrix_eligible": False,
            "matrix_written": False,
            "promotion_allowed": False,
            "symbol": symbol,
            "side": side,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "control_artifact": str(artifact),
            "data_start": str(manifest.get("data_start") or args.start),
            "data_end_exclusive": args.end,
            "npz": str(Path(data.path).resolve()),
            "npz_sha256": actual_sha,
            "contract": data.contract,
            "base_unit_usd": ladder.BASE_UNIT,
            "account_equity_usd": ladder.ACCOUNT_EQUITY,
            "hard_capacity_usd": ladder.CAPACITY,
            "hard_max_multiplier": ladder.MAX_MULT,
            "commission_bps_one_way": float(manifest["commission_bps_one_way"]),
            "slippage_bps_one_way": float(manifest["slippage_bps_one_way"]),
            "exit": {"family": "E02_DONCHIAN", "tf": "4h", "n": 30},
            "reentry": (
                "ladder lower first; mandatory zero-buffer stored exit/top reclaim"
            ),
            "selection": (
                "nested frozen coherent entry-curve retune; E02 and execution fixed"
            ),
            "target_weighted_tim_pct": [
                args.target_tim_low,
                args.target_tim_high,
            ],
        },
        "outer_folds": folds,
        "frozen_oos_aggregate": aggregate,
        "source_control_oos_aggregate": control_aggregate,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        args.out_dir.resolve()
        / f"ladder_exposure_retune_{stamp}_{symbol}_{side}"
    )
    out.mkdir(parents=True, exist_ok=False)
    (out / "result.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    snapshot = out / "source_snapshot"
    snapshot.mkdir()
    snapshot.joinpath(Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"artifact": str(out), **aggregate}, sort_keys=True))
    data.z.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-artifact", type=Path, required=True)
    ap.add_argument("--npz-path", type=Path)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "data" / "reports" / "vec_research",
    )
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    ap.add_argument("--random-curves", type=int, default=96)
    ap.add_argument("--include-global-archetypes", action="store_true")
    ap.add_argument("--seed", type=int, default=20260726)
    ap.add_argument("--target-tim-low", type=float, default=70.0)
    ap.add_argument("--target-tim-high", type=float, default=80.0)
    ap.add_argument("--control-tolerance-pp", type=float, default=0.0)
    args = ap.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
