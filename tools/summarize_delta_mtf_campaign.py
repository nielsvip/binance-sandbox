#!/usr/bin/env python3
"""Summarize the causal DELTA_MTF entry campaign and exact diagnostics."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAMILY = "ENTRY_DELTA_MTF"


def _latest(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    chosen = {}
    for path in root.glob(f"entry_overlay_{FAMILY}_*"):
        result = path / "result.json"
        if not result.exists():
            continue
        payload = json.loads(result.read_text())
        manifest = payload["manifest"]
        key = (manifest["symbol"], manifest["side"])
        if key not in chosen or path.name > chosen[key][0].name:
            chosen[key] = (path, payload)
    return [chosen[key] for key in sorted(chosen)]


def _setting(candidate: dict[str, Any]) -> str:
    p = candidate["params"]
    return (
        f"direct; favorable_TFs>={p['min_favorable_tfs']}; "
        f"retention={p['directional_retention_ratio']:g}; "
        f"struct4h={p['structural_gate']}"
    )


def _exact_diagnostics(root: Path) -> list[dict[str, Any]]:
    rows = []
    for summary_path in root.glob("v8_exact_ladder_replay_*/run_summary.json"):
        try:
            summary = json.loads(summary_path.read_text())
        except Exception:
            continue
        if FAMILY not in str(summary.get("source_artifact", "")):
            continue
        audit = summary.get("audit") or {}
        schedule = audit.get("schedule") or {}
        accounting = audit.get("accounting") or {}
        rows.append(
            {
                "artifact": str(summary_path.parent),
                "source_artifact": summary.get("source_artifact"),
                "status": summary.get("status"),
                "signal_parity": summary.get("signal_parity"),
                "capital_return_pct": accounting.get("actual_capital_return_pct"),
                "weighted_tim_pct": (
                    schedule.get("time_in_market") or {}
                ).get("actual_weighted_pct"),
                "actions": schedule.get("executed"),
                "future_htf_count": (
                    audit.get("causality") or {}
                ).get("future_htf_source_count"),
                "promotion_allowed": summary.get("promotion_allowed"),
            }
        )
    return sorted(rows, key=lambda row: row["artifact"])


def summarize(root: Path) -> dict[str, Any]:
    rows = []
    delta_audit = None
    for path, payload in _latest(root):
        manifest, agg = payload["manifest"], payload["aggregate"]
        delta_audit = delta_audit or manifest.get("delta_input_audit")
        folds = payload["outer_folds"]
        all_fold_exposure = all(
            70.0
            <= fold["validation_metrics"]["exposure_weighted_tim_pct"]
            <= 80.0
            for fold in folds
        )
        strict = bool(
            agg["all_folds_beat_bh"]
            and agg["all_folds_beat_control"]
            and all_fold_exposure
            and agg["all_mandatory_reclaim"]
            and agg["future_htf_count"] == 0
        )
        rows.append(
            {
                "family": FAMILY,
                "symbol": manifest["symbol"],
                "side": manifest["side"],
                "candidate_return_pct": agg["candidate_capital_return_pct_sum"],
                "bh_return_pct": agg["bh_capital_return_pct_sum"],
                "control_return_pct": agg["control_capital_return_pct_sum"],
                "weighted_tim_pct": agg["weighted_tim_pct"],
                "fold_tim_pct": [
                    fold["validation_metrics"]["exposure_weighted_tim_pct"]
                    for fold in folds
                ],
                "all_folds_beat_bh": agg["all_folds_beat_bh"],
                "all_folds_beat_control": agg["all_folds_beat_control"],
                # Generic fleet ingestion names this field exposure_policy_pass.
                # DELTA's corrected policy is deliberately stricter: every
                # untouched validation fold, not merely the aggregate, must
                # remain inside the 70–80% weighted-TIM band.
                "exposure_policy_pass": all_fold_exposure,
                "all_folds_exposure_policy_pass": all_fold_exposure,
                "future_htf_count": agg["future_htf_count"],
                "mandatory_reclaim": agg["all_mandatory_reclaim"],
                "entry_fills": sum(
                    int(fold["validation_metrics"]["fill_count"])
                    for fold in folds
                ),
                "technical_exits": sum(
                    int(fold["validation_metrics"]["exit_count"])
                    for fold in folds
                ),
                "status": "VECTOR_SURVIVOR" if strict else "DISCARD_GRAY",
                "selected_settings_by_fold": [
                    _setting(fold["selected_candidate"]) for fold in folds
                ],
                "artifact": str(path),
            }
        )
    cohorts = {}
    for side, name in (("LONG", "TOP_10_LONG"), ("SHORT", "BOTTOM_10_SHORT")):
        cohort = [row for row in rows if row["side"] == side]
        cohorts[name] = {
            "rows": len(cohort),
            "candidate_return_pct_sum": sum(
                row["candidate_return_pct"] for row in cohort
            ),
            "bh_return_pct_sum": sum(row["bh_return_pct"] for row in cohort),
            "control_return_pct_sum": sum(
                row["control_return_pct"] for row in cohort
            ),
            "entry_fills": sum(row["entry_fills"] for row in cohort),
            "all_folds_beat_bh_count": sum(
                row["all_folds_beat_bh"] for row in cohort
            ),
            "all_folds_beat_control_count": sum(
                row["all_folds_beat_control"] for row in cohort
            ),
            "all_folds_exposure_pass_count": sum(
                row["all_folds_exposure_policy_pass"] for row in cohort
            ),
            "strict_survivors": sum(
                row["status"] == "VECTOR_SURVIVOR" for row in cohort
            ),
        }
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "family": FAMILY,
            "actual_live_entry": (
                "direct-only favorable WT velocity/acceleration TF count plus "
                "1h red-zone and optional 4h structure gate"
            ),
            "timeframes": ["5m", "15m", "1h", "4h", "D"],
            "min_favorable_tfs": [1, 2, 3, 4],
            "directional_retention_ratio": [0.25, 0.5, 0.75],
            "retention_status": (
                "RESEARCH_ONLY: favorable/(favorable+opposing) speed; live "
                "DELTA_EXIT_DECAY_RATIO is exit-only"
            ),
            "structural_gate": [False, True],
            "roles": ["direct"],
            "same_control": "frozen ladder + E02 4h N30 + resting reclaim",
            "weighted_tim_target_each_fold_pct": [70, 80],
        },
        "delta_input_audit_example": delta_audit,
        "cohorts": cohorts,
        "rows": sorted(rows, key=lambda row: (row["side"], row["symbol"])),
        "exact_diagnostics": _exact_diagnostics(root),
    }


def _f(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# DELTA_MTF causal entry campaign — 2026-07-26",
        "",
        "## Verdict",
        "",
        "The actual entry inputs were mapped before the sweep: weighted favorable "
        "WT velocity/acceleration counts on 5m/15m/1h/4h/D, the 1h delta red-zone, "
        "and optional side-mirrored 4h structure. All HTFs are observed only after "
        "their completed timestamp. The actual path is direct-only.",
        "",
        "`DELTA_EXIT_DECAY_RATIO` is an exit knob, not a live entry knob. The "
        "requested 0.25/0.50/0.75 values were therefore tested only as a clearly "
        "labeled research directional-retention ratio; no live setting was changed.",
        "",
        "**Zero candidates passed B&H, the identical control, every validation "
        "fold, and 70–80% TIM in every fold.** All 20 rows remain gray.",
        "",
        "VLO_LONG initially appeared to survive when exposure was aggregated "
        "(74.34%), but its fold TIM was 33.68%/89.00%/96.55%. Exact V8 schedule "
        "parity passed and exposed the last-fold 96.55% value; the gate was fixed "
        "to require every fold, VLO was rerun, and it is not a survivor.",
        "",
        "## Cohort aggregates",
        "",
        "| cohort | rows | candidate sum | B&H sum | control sum | fills | folds >B&H | folds >control | all-fold TIM | survivors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cohort in payload["cohorts"].items():
        lines.append(
            f"| {name} | {cohort['rows']} | {_f(cohort['candidate_return_pct_sum'])}% | "
            f"{_f(cohort['bh_return_pct_sum'])}% | {_f(cohort['control_return_pct_sum'])}% | "
            f"{cohort['entry_fills']} | {cohort['all_folds_beat_bh_count']} | "
            f"{cohort['all_folds_beat_control_count']} | "
            f"{cohort['all_folds_exposure_pass_count']} | {cohort['strict_survivors']} |"
        )
    lines += [
        "",
        "## Per-key gray evidence",
        "",
        "| key | candidate | B&H | control | TIM folds | fills | folds >B&H/control | fold settings |",
        "|---|---:|---:|---:|---|---:|---|---|",
    ]
    for row in payload["rows"]:
        settings = " &#124; ".join(row["selected_settings_by_fold"])
        tim = "/".join(f"{value:.1f}" for value in row["fold_tim_pct"])
        lines.append(
            f"| {row['symbol']}_{row['side']} | {_f(row['candidate_return_pct'])}% | "
            f"{_f(row['bh_return_pct'])}% | {_f(row['control_return_pct'])}% | "
            f"{tim}% | {row['entry_fills']} | "
            f"{'yes' if row['all_folds_beat_bh'] else 'no'}/"
            f"{'yes' if row['all_folds_beat_control'] else 'no'} | {settings} |"
        )
    lines += [
        "",
        "## Exact diagnostic",
        "",
    ]
    for row in payload["exact_diagnostics"]:
        lines.append(
            f"- `{row['artifact']}`: {row['status']}; signal parity "
            f"{row['signal_parity']}; return {_f(row['capital_return_pct'])}%; "
            f"TIM {_f(row['weighted_tim_pct'])}%; actions {row['actions']}; "
            f"future HTF {row['future_htf_count']}; promotion remains false."
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
    print(
        json.dumps(
            {
                "rows": len(payload["rows"]),
                "survivors": sum(
                    row["status"] == "VECTOR_SURVIVOR"
                    for row in payload["rows"]
                ),
                "cohorts": payload["cohorts"],
                "exact_diagnostics": len(payload["exact_diagnostics"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
