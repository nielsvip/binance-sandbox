#!/usr/bin/env python3
"""Build single-family TIM beam inputs without using final-fold selection."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGET_LOW = 70.0
TARGET_HIGH = 80.0
TARGET_MID = 75.0


def _gap(tim: float) -> tuple[float, float]:
    return max(0.0, TARGET_LOW - tim), max(0.0, tim - TARGET_HIGH)


def _fold_row(fold: dict[str, Any]) -> dict[str, Any]:
    metrics = fold["validation_metrics"]
    requests = int(fold["selected_entry_request_count"])
    execution_fills = int(metrics["fill_count"])
    low_gap, high_gap = _gap(float(metrics["exposure_weighted_tim_pct"]))
    inner_tim = [
        float(row["exposure_weighted_tim_pct"])
        for row in fold.get("inner_metrics", [])
    ]
    return {
        "fold": int(fold["fold"]),
        "train": fold["train"],
        "validation": fold["validation"],
        "training_robust_tim_pct": fold["training_robust_weighted_tim_pct"],
        "validation_tim_pct": metrics["exposure_weighted_tim_pct"],
        "discovery_inner_tim_pct": inner_tim,
        "discovery_inner_min_tim_pct": min(inner_tim) if inner_tim else None,
        "discovery_inner_max_tim_pct": max(inner_tim) if inner_tim else None,
        "discovery_inner_low_gap_pp_sum": sum(
            _gap(value)[0] for value in inner_tim
        ),
        "discovery_inner_high_gap_pp_sum": sum(
            _gap(value)[1] for value in inner_tim
        ),
        "low_tim_gap_pp": low_gap,
        "high_tim_gap_pp": high_gap,
        "selected_entry_signal_rows": int(fold["selected_entry_signal_rows"]),
        "selected_entry_request_count": requests,
        "execution_fill_count": execution_fills,
        "request_minus_execution_fill_count": requests - execution_fills,
        "execution_fill_ratio": metrics["fill_ratio"],
        "selected_candidate": fold["selected_candidate"],
        "ladder_curve": fold["curve"],
        "capacity_safe": not metrics["entry_capacity_breach"],
        "future_htf_count": 0,
    }


def _family_summary(result: dict[str, Any]) -> dict[str, Any]:
    folds = sorted(
        result["outer_folds"],
        key=lambda fold: int(fold["validation_metrics"]["end_ts"]),
    )
    rows = [_fold_row(fold) for fold in folds]
    pre_final = rows[:-1]
    final = rows[-1]
    validation_tims = [
        float(row["validation_tim_pct"]) for row in pre_final
    ]
    discovery_tims = [
        value
        for row in pre_final
        for value in row["discovery_inner_tim_pct"]
    ]
    tims = [*validation_tims, *discovery_tims]
    low_gap = sum(_gap(value)[0] for value in tims)
    high_gap = sum(_gap(value)[1] for value in tims)
    minimum = min(tims)
    maximum = max(tims)
    scale_floor = TARGET_LOW / minimum if minimum > 0 else None
    scale_ceiling = TARGET_HIGH / maximum if maximum > 0 else None
    uniform_scale_feasible = (
        scale_floor is not None
        and scale_ceiling is not None
        and scale_floor <= scale_ceiling
    )
    if uniform_scale_feasible:
        scale_mid = math.sqrt(scale_floor * scale_ceiling)
        scale_grid = sorted(
            {
                round(scale_floor, 4),
                round(scale_mid, 4),
                round(scale_ceiling, 4),
            }
        )
    else:
        scale_grid = []
    raise_low_without_high_breach = (
        uniform_scale_feasible
        and minimum < TARGET_LOW
        and maximum <= TARGET_HIGH
        and bool(scale_grid)
        and min(scale_grid) >= 1.0
    )
    scale_direction = (
        "RAISE_LOW"
        if raise_low_without_high_breach
        else "LOWER_HIGH"
        if uniform_scale_feasible and minimum >= TARGET_LOW
        else "MIXED_INFEASIBLE"
        if minimum < TARGET_LOW and maximum > TARGET_HIGH
        else "NO_UNIFORM_BAND"
    )
    latest_pre_final = pre_final[-1]
    return {
        "family": result["manifest"]["family"],
        "symbol": result["manifest"]["symbol"],
        "side": result["manifest"]["side"],
        "artifact": result["manifest"].get("control_artifact"),
        "component_artifact": None,
        "pre_final_folds": pre_final,
        "final_fold_evaluation_only": final,
        "pre_final_low_gap_pp_sum": low_gap,
        "pre_final_high_gap_pp_sum": high_gap,
        "pre_final_inside_target_count": sum(
            TARGET_LOW <= value <= TARGET_HIGH for value in tims
        ),
        "pre_final_target_distance_pp_sum": sum(
            abs(value - TARGET_MID) for value in tims
        ),
        "pre_final_min_tim_pct": minimum,
        "pre_final_max_tim_pct": maximum,
        "pre_final_validation_tim_pct": validation_tims,
        "pre_final_discovery_inner_tim_pct": discovery_tims,
        "uniform_multiplier_linearized_feasible": uniform_scale_feasible,
        "linearized_multiplier_scale_floor": scale_floor,
        "linearized_multiplier_scale_ceiling": scale_ceiling,
        "multiplier_scale_beam_grid": scale_grid,
        "raise_low_without_high_breach_feasible": (
            raise_low_without_high_breach
        ),
        "scale_direction": scale_direction,
        "frozen_candidate_from_latest_pre_final_fold": latest_pre_final[
            "selected_candidate"
        ],
        "frozen_ladder_curve_from_latest_pre_final_fold": latest_pre_final[
            "ladder_curve"
        ],
        "selection_source_folds": [
            int(row["fold"]) for row in pre_final
        ],
        "excluded_final_fold": int(final["fold"]),
    }


def analyze(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text())
    root = report_path.resolve().parents[3]
    family_rows = []
    for row in report["rows"]:
        artifact = Path(row["artifact"])
        if not artifact.is_absolute():
            artifact = root / artifact
        result = json.loads((artifact / "result.json").read_text())
        family = _family_summary(result)
        family["component_artifact"] = str(artifact)
        family_rows.append(family)

    keys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in family_rows:
        keys.setdefault((row["symbol"], row["side"]), []).append(row)
    beam_inputs = []
    for (symbol, side), rows in sorted(keys.items()):
        ranked = sorted(
            rows,
            key=lambda row: (
                not row["uniform_multiplier_linearized_feasible"],
                row["pre_final_high_gap_pp_sum"] > 0,
                row["pre_final_high_gap_pp_sum"],
                row["pre_final_low_gap_pp_sum"],
                -row["pre_final_inside_target_count"],
                row["pre_final_target_distance_pp_sum"],
                row["family"],
            ),
        )
        winner = ranked[0]
        instability_components = []
        for family_row in rows:
            final = family_row["final_fold_evaluation_only"]
            final_validation = float(final["validation_tim_pct"])
            discovery_min = final["discovery_inner_min_tim_pct"]
            if (
                TARGET_LOW <= final_validation <= TARGET_HIGH
                and discovery_min is not None
                and discovery_min < 65.0
            ):
                instability_components.append(
                    {
                        "family": family_row["family"],
                        "final_validation_tim_pct": final_validation,
                        "final_training_robust_tim_pct": final[
                            "training_robust_tim_pct"
                        ],
                        "final_discovery_inner_min_tim_pct": discovery_min,
                        "final_discovery_inner_max_tim_pct": final[
                            "discovery_inner_max_tim_pct"
                        ],
                    }
                )
        final = winner["final_fold_evaluation_only"]
        unstable = bool(instability_components)
        beam_inputs.append(
            {
                "symbol": symbol,
                "side": side,
                "regime_instability": unstable,
                "regime_instability_definition": (
                    "at least one component has final validation TIM in "
                    "[70,80] while a final-fold discovery inner slice is <65; "
                    "evaluation only"
                ),
                "regime_instability_components": instability_components,
                "selected_family": winner["family"],
                "selection_source_folds": winner["selection_source_folds"],
                "excluded_final_fold": winner["excluded_final_fold"],
                "frozen_candidate": winner[
                    "frozen_candidate_from_latest_pre_final_fold"
                ],
                "frozen_ladder_curve": winner[
                    "frozen_ladder_curve_from_latest_pre_final_fold"
                ],
                "component_artifact": winner["component_artifact"],
                "pre_final_min_tim_pct": winner["pre_final_min_tim_pct"],
                "pre_final_max_tim_pct": winner["pre_final_max_tim_pct"],
                "pre_final_low_gap_pp_sum": winner[
                    "pre_final_low_gap_pp_sum"
                ],
                "pre_final_high_gap_pp_sum": winner[
                    "pre_final_high_gap_pp_sum"
                ],
                "uniform_multiplier_linearized_feasible": winner[
                    "uniform_multiplier_linearized_feasible"
                ],
                "multiplier_scale_beam_grid": winner[
                    "multiplier_scale_beam_grid"
                ],
                "raise_low_without_high_breach_feasible": winner[
                    "raise_low_without_high_breach_feasible"
                ],
                "scale_direction": winner["scale_direction"],
                "final_fold_evaluation_only": final,
                "materializer": (
                    "tools.vec_entry_overlay_walkforward."
                    "build_frozen_overlay_signals"
                ),
                "single_family_only": True,
                "blended_entry_overlay_authorized": False,
                "beam_status": (
                    "READY_RAISE_LOW_MULTIPLIER_BEAM"
                    if winner["raise_low_without_high_breach_feasible"]
                    else "READY_LOWER_HIGH_MULTIPLIER_BEAM"
                    if winner["uniform_multiplier_linearized_feasible"]
                    else "NO_UNIFORM_MULTIPLIER_BAND_USE_FAMILY_ONLY"
                ),
            }
        )
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_report": str(report_path),
        "contract": {
            "selection_folds": "all completed folds except final chronological fold",
            "final_fold": "evaluation only; never used for family/curve selection",
            "entry_overlay": "one family only; no blends",
            "multiplier_scale": (
                "linearized diagnostic beam bound from observed pre-final TIM; "
                "must be re-simulated, not treated as a prediction"
            ),
            "fills": (
                "artifact exposes total execution fill_count, not entry-only "
                "fills; report never relabels it as entry_fill_count"
            ),
        },
        "family_fold_rows": family_rows,
        "beam_inputs": beam_inputs,
        "raise_low_beam_inputs": [
            row for row in beam_inputs
            if row["raise_low_without_high_breach_feasible"]
        ],
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Entry component TIM-regime beam inputs — 2026-07-26",
        "",
        "Selection uses pre-final folds only. Final chronological folds are "
        "evaluation-only. Every row freezes one entry family and one ladder "
        "curve; blended overlays are prohibited.",
        "",
        "| key | unstable | family | pre-final TIM min/max | low/high gap | scale direction/grid | final train/validation TIM (audit only) | status |",
        "|---|---|---|---:|---:|---|---:|---|",
    ]
    for row in payload["beam_inputs"]:
        final = row["final_fold_evaluation_only"]
        scale = (
            ", ".join(f"{value:.4f}" for value in row["multiplier_scale_beam_grid"])
            or "none"
        )
        lines.append(
            f"| {row['symbol']}_{row['side']} | "
            f"{'yes' if row['regime_instability'] else 'no'} | "
            f"{row['selected_family']} | "
            f"{row['pre_final_min_tim_pct']:.2f}/{row['pre_final_max_tim_pct']:.2f}% | "
            f"{row['pre_final_low_gap_pp_sum']:.2f}/"
            f"{row['pre_final_high_gap_pp_sum']:.2f}pp | "
            f"{row['scale_direction']}: {scale} | "
            f"{final['training_robust_tim_pct']:.2f}/"
            f"{final['validation_tim_pct']:.2f}% | {row['beam_status']} |"
        )
    lines += [
        "",
        "The scale beam is only a linearized bound. TIM is not assumed linear "
        "in multiplier size; every scale must be re-simulated with the frozen "
        "candidate and curve. `execution_fill_count` includes all execution "
        "fills because the completed artifacts do not expose entry-only fills.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()
    payload = analyze(args.report)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.md_out.write_text(markdown(payload) + "\n")
    print(
        json.dumps(
            {
                "keys": len(payload["beam_inputs"]),
                "unstable": sum(
                    row["regime_instability"] for row in payload["beam_inputs"]
                ),
                "uniform_beams": sum(
                    row["uniform_multiplier_linearized_feasible"]
                    for row in payload["beam_inputs"]
                ),
                "raise_low_beams": len(payload["raise_low_beam_inputs"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
