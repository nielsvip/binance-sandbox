#!/usr/bin/env python3
"""Summarize separated causal reconstructions of removed bounce reasons."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.normalize_entry_fleet_metrics import normalized_payloads


FAMILIES = (
    "ENTRY_BOUNCE_15M_LOW",
    "ENTRY_BOUNCE_5M_LOW",
    "ENTRY_4H_DEEP_VALUE",
    "ENTRY_1H_TURN_UP",
)


def summarize(root: Path) -> dict:
    chosen = {}
    for family in FAMILIES:
        for artifact in root.glob(f"entry_overlay_{family}_*"):
            result_path = artifact / "result.json"
            if not result_path.exists():
                continue
            result = json.loads(result_path.read_text())
            manifest = result["manifest"]
            key = (family, manifest["symbol"], manifest["side"])
            if key not in chosen or artifact.name > chosen[key][0].name:
                chosen[key] = (artifact, result)
    rows = []
    for artifact, result in chosen.values():
        manifest, agg = result["manifest"], result["aggregate"]
        source = {
            "job_id": 0,
            "symbol": manifest["symbol"],
            "side": manifest["side"],
            "stage": "VEC_RESEARCH_SOURCE",
            "status": "REPORT_ONLY",
            "artifact": str(artifact),
        }
        aggregate_metrics, final_metrics = normalized_payloads(source, result)
        final_fold = max(
            result["outer_folds"],
            key=lambda fold: int(fold["validation_metrics"]["end_ts"]),
        )
        wired = agg["entry_request_count"] > 0 and agg["entry_fill_count"] > 0
        strict = bool(agg["vector_survivor"])
        rows.append(
            {
                "family": manifest["family"],
                "symbol": manifest["symbol"],
                "side": manifest["side"],
                "status": (
                    "VECTOR_SURVIVOR_AWAIT_EXACT"
                    if strict
                    else "DISCARD_GRAY"
                    if wired
                    else "RED_DIAGNOSTIC_INERT_RECONSTRUCTION"
                ),
                "candidate_return_pct": final_metrics["strategy_return_pct"],
                "bh_return_pct": final_metrics["bh_return_pct"],
                "control_return_pct": final_metrics[
                    "same_entry_control_return_pct"
                ],
                "weighted_tim_pct": final_metrics["tim_pct"],
                "metric_scope": final_metrics["metric_scope"],
                "return_unit": final_metrics["return_unit"],
                "tim_unit": final_metrics["tim_unit"],
                "aggregate_metrics": {
                    key: aggregate_metrics[key]
                    for key in (
                        "strategy_return_pct",
                        "bh_return_pct",
                        "same_entry_control_return_pct",
                        "tim_pct",
                        "trades",
                        "metric_scope",
                        "return_unit",
                        "return_aggregation",
                        "tim_unit",
                        "tim_aggregation",
                    )
                },
                "final_oos_metrics": {
                    key: final_metrics[key]
                    for key in (
                        "strategy_return_pct",
                        "bh_return_pct",
                        "same_entry_control_return_pct",
                        "tim_pct",
                        "trades",
                        "metric_scope",
                        "return_unit",
                        "return_aggregation",
                        "tim_unit",
                        "tim_aggregation",
                        "fold_index",
                        "validation_window",
                    )
                },
                "all_folds_beat_bh": agg["all_folds_beat_bh"],
                "all_folds_beat_control": agg["all_folds_beat_control"],
                "all_folds_exposure_policy_pass": agg[
                    "all_folds_exposure_policy_pass"
                ],
                "exposure_policy_pass": agg[
                    "all_folds_exposure_policy_pass"
                ],
                "future_htf_count": agg["future_htf_count"],
                "entry_signal_rows": agg["entry_signal_rows"],
                "entry_request_count": agg["entry_request_count"],
                "entry_fill_count": agg["entry_fill_count"],
                "fold_tim_pct": [
                    fold["validation_metrics"]["exposure_weighted_tim_pct"]
                    for fold in result["outer_folds"]
                ],
                "selected_settings_by_fold": [
                    fold["selected_candidate"]
                    for fold in result["outer_folds"]
                ],
                "frozen_final_entry_schedule": {
                    "selected_candidate": final_fold["selected_candidate"],
                    "ladder_curve": final_fold["curve"],
                    "validation_window": final_fold["validation"],
                    "artifact": str(artifact),
                    "materializer": (
                        "tools.vec_entry_overlay_walkforward."
                        "build_frozen_overlay_signals"
                    ),
                    "combination_authorized": False,
                },
                "artifact": str(artifact),
            }
        )
    cohorts = {}
    for family in FAMILIES:
        for side, cohort_name in (
            ("LONG", "TOP_10_LONG"),
            ("SHORT", "BOTTOM_10_SHORT"),
        ):
            cohort = [
                row for row in rows
                if row["family"] == family and row["side"] == side
            ]
            name = f"{family}__{cohort_name}"
            cohorts[name] = {
                "rows": len(cohort),
                "candidate_return_pct_sum": sum(
                    row["candidate_return_pct"] for row in cohort
                ),
                "bh_return_pct_sum": sum(
                    row["bh_return_pct"] for row in cohort
                ),
                "control_return_pct_sum": sum(
                    row["control_return_pct"] for row in cohort
                ),
                "signals": sum(row["entry_signal_rows"] for row in cohort),
                "requests": sum(row["entry_request_count"] for row in cohort),
                "fills": sum(row["entry_fill_count"] for row in cohort),
                "all_folds_beat_bh_count": sum(
                    row["all_folds_beat_bh"] for row in cohort
                ),
                "all_folds_beat_control_count": sum(
                    row["all_folds_beat_control"] for row in cohort
                ),
                "all_folds_tim_count": sum(
                    row["all_folds_exposure_policy_pass"] for row in cohort
                ),
                "strict_survivors": sum(
                    row["status"] == "VECTOR_SURVIVOR_AWAIT_EXACT"
                    for row in cohort
                ),
                "nested_fold_candidate_return_pct_sum": sum(
                    row["aggregate_metrics"]["strategy_return_pct"]
                    for row in cohort
                ),
                "nested_fold_bh_return_pct_sum": sum(
                    row["aggregate_metrics"]["bh_return_pct"]
                    for row in cohort
                ),
                "nested_fold_control_return_pct_sum": sum(
                    row["aggregate_metrics"][
                        "same_entry_control_return_pct"
                    ]
                    for row in cohort
                ),
            }
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "families": list(FAMILIES),
        "contract": {
            "parent_label": "LONG_WAIT_REMOVED_AND_QUARANTINED",
            "historical_reasons": "SEPARATE_SOURCE_REASONS_PROVEN_AT_f83bc7b9",
            "reconstruction_only": True,
            "short_side": "EXPLICIT_RESEARCH_MIRROR",
            "grid_candidates_by_path": {
                "ENTRY_BOUNCE_15M_LOW": 64,
                "ENTRY_BOUNCE_5M_LOW": 64,
                "ENTRY_4H_DEEP_VALUE": 8,
                "ENTRY_1H_TURN_UP": 24,
            },
            "bounce_timeframe": ["5m", "15m"],
            "bounce_distance": [0.004, 0.008, 0.015, 0.025],
            "recovery_only": [False, True],
            "confirmation": ["none", "stoch5", "stoch15", "two-of-two"],
            "roles": ["direct", "union-with-green"],
            "same_control": "frozen ladder + E02 N30 + resting reclaim",
            "exact_rule": "strict every-fold survivors only",
            "report_primary_metric_scope": (
                "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD"
            ),
            "nested_fold_sums": (
                "retained separately under aggregate_metrics; never presented "
                "as single final-OOS returns"
            ),
        },
        "cohorts": cohorts,
        "rows": sorted(
            rows, key=lambda row: (row["family"], row["side"], row["symbol"])
        ),
    }


def markdown(payload: dict) -> str:
    lines = [
        "# Separated historical LONG_WAIT reasons — 2026-07-26",
        "",
        "## Wiring verdict",
        "",
        "`LONG_WAIT_ENABLED` is quarantined as a removed compound label. Commit "
        "`f83bc7b9` separately proves `Bounce_15m_Low` (1.5%) and "
        "`Bounce_5m_Low` (0.8%). Each reason is screened independently using "
        "completed prior channels. `4h_Deep_Value` and `1h_Turn_Up` are also "
        "independent completed-bar paths. SHORT is an explicit research mirror.",
        "",
        "## Cohorts",
        "",
        "| path/cohort | rows | final candidate | final B&H | final control | nested candidate/B&H/control | signals/requests/fills | every-fold >B&H/control/TIM | survivors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["cohorts"].items():
        lines.append(
            f"| {name} | {row['rows']} | "
            f"{row['candidate_return_pct_sum']:.2f}% | "
            f"{row['bh_return_pct_sum']:.2f}% | "
            f"{row['control_return_pct_sum']:.2f}% | "
            f"{row['nested_fold_candidate_return_pct_sum']:.2f}/"
            f"{row['nested_fold_bh_return_pct_sum']:.2f}/"
            f"{row['nested_fold_control_return_pct_sum']:.2f}% | "
            f"{row['signals']}/{row['requests']}/{row['fills']} | "
            f"{row['all_folds_beat_bh_count']}/"
            f"{row['all_folds_beat_control_count']}/"
            f"{row['all_folds_tim_count']} | "
            f"{row['strict_survivors']} |"
        )
    lines += [
        "",
        "## Per-key retained evidence",
        "",
        "| path/key | verdict | final candidate | final B&H | final control | final TIM | nested candidate/B&H/control | signals/requests/fills | fold TIM |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['family']} / {row['symbol']}_{row['side']} | {row['status']} | "
            f"{row['candidate_return_pct']:.2f}% | {row['bh_return_pct']:.2f}% | "
            f"{row['control_return_pct']:.2f}% | {row['weighted_tim_pct']:.2f}% | "
            f"{row['aggregate_metrics']['strategy_return_pct']:.2f}/"
            f"{row['aggregate_metrics']['bh_return_pct']:.2f}/"
            f"{row['aggregate_metrics']['same_entry_control_return_pct']:.2f}% | "
            f"{row['entry_signal_rows']}/{row['entry_request_count']}/"
            f"{row['entry_fill_count']} | "
            f"{', '.join(f'{value:.1f}' for value in row['fold_tim_pct'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()
    payload = summarize(args.root)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.md_out.write_text(markdown(payload))
    print(json.dumps(payload["cohorts"], sort_keys=True))


if __name__ == "__main__":
    main()
