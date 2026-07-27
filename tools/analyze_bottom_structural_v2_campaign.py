#!/usr/bin/env python3
"""Create compact machine/human evidence for bottom structural v2."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_bottom_structural_v2_campaign import _fold_failures


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def analyze(root: Path) -> dict[str, Any]:
    campaign = _read(root / "campaign_result.json")
    family_counts: Counter[str] = Counter()
    gate_counts: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    outcomes: Counter[str] = Counter()
    family_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    arm_profiles: dict[str, Counter[str]] = defaultdict(Counter)
    emergency_effects: dict[str, list[float]] = defaultdict(list)
    candidate_total = 0

    for result in campaign["results"]:
        raw = _read(Path(result["artifact"]) / "result.json")
        b_index = {}
        for row in raw["candidates"]:
            if row["family"] == "BOTTOM_B_STRUCTURAL_V2":
                digest = hashlib.sha256(
                    json.dumps(row["params"], sort_keys=True).encode("utf-8")
                ).hexdigest()
                b_index[digest] = row
        for row in raw["candidates"]:
            family = row["family"]
            if family not in {
                "BOTTOM_B_STRUCTURAL_V2",
                "BOTTOM_C_STRUCTURAL_V2_EMERGENCY",
            }:
                continue
            candidate_total += 1
            family_counts[family] += 1
            emergency = family == "BOTTOM_C_STRUCTURAL_V2_EMERGENCY"
            passes = []
            for index, fold in enumerate(row["fold_evidence"]):
                failures = _fold_failures(fold, emergency)
                passes.append(not failures)
                role = (
                    "FINAL"
                    if index == len(row["fold_evidence"]) - 1
                    else f"DISCOVERY_{index + 1}"
                )
                gate_counts[family][role][
                    "PASS" if not failures else "FAIL"
                ] += 1
                for failure in failures:
                    gate_counts[family][role][failure] += 1
            discovery_all = all(passes[:-1])
            final = passes[-1]
            outcome = (
                "ALL_FOLDS"
                if discovery_all and final
                else "DISCOVERY_ONLY"
                if discovery_all
                else "FINAL_ONLY"
                if final
                else "NO_PARTITION_SURVIVOR"
            )
            outcomes[outcome] += 1
            family_outcomes[family][outcome] += 1
            profile = (
                f"{row['params']['arm_tf']}:"
                f"{row['params']['arm_break_mode']}:"
                f"{row['params']['arm_break_threshold']}"
            )
            arm_profiles[profile][
                "ALL_FOLDS" if discovery_all and final else "REJECTED"
            ] += 1
            if emergency:
                base = b_index[row["paired_b_params_sha256"]]
                delta = (
                    float(row["fold_evidence"][-1]["strategy_return_pct"])
                    - float(base["fold_evidence"][-1]["strategy_return_pct"])
                )
                emergency_effects[row["params"]["emergency_label"]].append(delta)

    selected = []
    for result in campaign["results"]:
        for winner in result["winners"]:
            final = winner["fold_evidence"][-1]
            selected.append(
                {
                    "key": result["key"],
                    "family": winner["family"],
                    "params": winner["params"],
                    "discovery_gate_pass": [
                        fold["gate_pass"]
                        for fold in winner["fold_evidence"][:-1]
                    ],
                    "discovery_failures": [
                        fold["failures"]
                        for fold in winner["fold_evidence"][:-1]
                    ],
                    "final_return_pct": float(final["strategy_return_pct"]),
                    "final_bh_return_pct": float(final["bh_return_pct"]),
                    "final_e02_return_pct": float(
                        final["same_entry_e02_return_pct"]
                    ),
                    "final_tim_pct": float(final["weighted_tim_pct"]),
                    "final_exit_fills": int(final["exit_fills"]),
                    "final_failures": final["failures"],
                    "all_folds_strict": winner["all_folds_strict"],
                    "isolated_versioned_data": result[
                        "isolated_versioned_data"
                    ],
                }
            )
    return {
        "campaign_id": campaign["campaign_id"],
        "candidate_evaluations": candidate_total,
        "family_counts": dict(family_counts),
        "outcomes": dict(outcomes),
        "family_outcomes": {
            family: dict(counts)
            for family, counts in family_outcomes.items()
        },
        "fold_failure_counts": {
            family: {
                role: dict(counter)
                for role, counter in counters.items()
            }
            for family, counters in gate_counts.items()
        },
        "arm_profile_counts": {
            profile: dict(counts)
            for profile, counts in sorted(arm_profiles.items())
        },
        "emergency_final_paired_effect_pp": {
            label: {
                "pairs": len(values),
                "mean": statistics.mean(values),
                "median": statistics.median(values),
                "positive_pairs": sum(value > 0 for value in values),
            }
            for label, values in sorted(emergency_effects.items())
        },
        "selected_discovery_winners": selected,
        "strict_survivor_count": sum(
            row["all_folds_strict"] for row in selected
        ),
        "exact_replay_queue_count": len(campaign["exact_replay_queue"]),
        "errors": campaign["errors"],
    }


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Qualified-arm delayed lower-top exits — v2 result (2026-07-27)",
        "",
        "## Outcome",
        "",
        f"The preregistered vector pass completed **{summary['candidate_evaluations']:,}** "
        "candidate evaluations with zero execution errors. It tested one immutable "
        "side-specific entry schedule for each top/bottom-20 key, plus VT_LONG and "
        "isolated/versioned HAO_SHORT. No candidate passed all chronological folds; "
        "the exact-engine queue is empty and no live setting or canonical NPZ changed.",
        "",
        "The first adverse break never sells. ATR, rolling-STDEV, or prior-window "
        "DC/support displacement on completed 15m/1h bars only arms the state. A "
        "later completed 5m/15m/1h rebound must form a lower price/WT1 top (exact "
        "SHORT mirror), then a distinct adverse price and/or WT rollover confirms "
        "the next-RTH exit. Emergency overlays are limited to ATR6, STDEV7, or "
        "eight uninterrupted adverse bars and must remain at or below 10% of exits.",
        "",
        "## Candidate funnel",
        "",
        "| family | candidates | final-only | discovery-only | all folds |",
        "|---|---:|---:|---:|---:|",
    ]
    for family, count in summary["family_counts"].items():
        outcomes = summary["family_outcomes"][family]
        lines.append(
            f"| {family} | {count:,} | "
            f"{outcomes.get('FINAL_ONLY', 0):,} | "
            f"{outcomes.get('DISCOVERY_ONLY', 0):,} | "
            f"{outcomes.get('ALL_FOLDS', 0):,} |"
        )
    lines += [
        "",
        "Global disjoint outcomes: "
        + ", ".join(
            f"{name}={value:,}"
            for name, value in sorted(summary["outcomes"].items())
        )
        + ".",
        "",
        "## Fold failure counts",
        "",
    ]
    for family, folds in summary["fold_failure_counts"].items():
        lines += [
            f"### {family}",
            "",
            "| fold | pass | fail | not >B&H | not >E02 | TIM | insolvent | capacity | future | reclaim | no exit | emergency >10% |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        roles = [
            role for role in ("DISCOVERY_1", "DISCOVERY_2", "FINAL")
            if role in folds
        ]
        for role in roles:
            counts = folds[role]
            lines.append(
                f"| {role.lower().replace('_', ' ')} | "
                f"{counts.get('PASS', 0):,} | {counts.get('FAIL', 0):,} | "
                f"{counts.get('NOT_ABOVE_BH', 0):,} | "
                f"{counts.get('NOT_ABOVE_SAME_ENTRY_E02', 0):,} | "
                f"{counts.get('TIM_OUTSIDE_70_80', 0):,} | "
                f"{counts.get('INSOLVENT', 0):,} | "
                f"{counts.get('CAPACITY_BREACH', 0):,} | "
                f"{counts.get('FUTURE_HTF', 0):,} | "
                f"{counts.get('FORGOTTEN_RECLAIM', 0):,} | "
                f"{counts.get('NO_ACTUAL_EXIT', 0):,} | "
                f"{counts.get('EMERGENCY_NOT_RARE', 0):,} |"
            )
        lines.append("")
    lines += [
        "## Discovery-selected winners",
        "",
        "| key | family | discovery gates | final return / B&H / E02 | TIM | exits | final failures |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for row in summary["selected_discovery_winners"]:
        gates = "/".join("pass" if value else "fail" for value in row["discovery_gate_pass"])
        failures = ", ".join(row["final_failures"]) or "pass"
        lines.append(
            f"| {row['key']} | {row['family'].replace('BOTTOM_', '')} | "
            f"{gates} | {_fmt(row['final_return_pct'])} / "
            f"{_fmt(row['final_bh_return_pct'])} / "
            f"{_fmt(row['final_e02_return_pct'])} | "
            f"{_fmt(row['final_tim_pct'])}% | {row['final_exit_fills']} | "
            f"{failures} |"
        )
    lines += [
        "",
        "## Emergency overlay effect",
        "",
        "| brake | pairs | mean / median final delta | positive pairs |",
        "|---|---:|---:|---:|",
    ]
    for label, row in summary["emergency_final_paired_effect_pp"].items():
        lines.append(
            f"| {label} | {row['pairs']:,} | {_fmt(row['mean'])} / "
            f"{_fmt(row['median'])}pp | {row['positive_pairs']:,} |"
        )
    lines += [
        "",
        "## Decision",
        "",
        "All rows remain attributable gray research evidence. A high final-only "
        "return cannot enter exact replay. HAO remains isolated/versioned even if a "
        "future vector setting passes. The result confirms the mechanics are now "
        "tested as requested, but arm qualification alone does not repair the "
        "cross-fold TIM/control instability inherited from the frozen entry "
        "schedules.",
        "",
        "Artifacts:",
        "",
        f"- `{summary['campaign_id']}/preregistered_contract.json`",
        f"- `{summary['campaign_id']}/campaign_result.json`",
        f"- `{summary['campaign_id']}/analysis.json`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()
    summary = analyze(args.campaign_root.resolve())
    json_out = args.json_out or args.campaign_root / "analysis.json"
    md_out = args.md_out or args.campaign_root / "RESULTS.md"
    json_out.write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    md_out.write_text(markdown(summary))
    print(
        json.dumps(
            {
                "candidate_evaluations": summary["candidate_evaluations"],
                "strict_survivors": summary["strict_survivor_count"],
                "exact_queue": summary["exact_replay_queue_count"],
                "json": str(json_out),
                "markdown": str(md_out),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
