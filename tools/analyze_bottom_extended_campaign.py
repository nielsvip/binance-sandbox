#!/usr/bin/env python3
"""Summarize the extended causal bottom-exit campaign.

The input is one combined cohort containing extended A/B/C candidates.  The
report keeps LONG and SHORT rows separate, reports every-fold promotion gates,
and compares every emergency overlay to its exact pruned family-B base.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


FAMILIES = (
    "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
    "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
    "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.{digits}f}" if math.isfinite(number) else "—"


def _param_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(params, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _control_return(row: dict[str, Any], partition: str) -> float:
    nested = row["nested"]
    return float(nested[partition]["capital_return_pct_sum"]) - float(
        nested[f"{partition}_alpha_vs_same_entry_e02_pp"]
    )


def _strict(row: dict[str, Any]) -> bool:
    return bool(
        row["nested"]["robust_discovery_all_folds"]
        and row["nested"]["robust_validation_fold"]
        and row.get("compiled_python_parity", {}).get("status")
        in {"PASS", "NOT_APPLICABLE"}
    )


def analyze(cohort_path: Path) -> dict[str, Any]:
    cohort = _load(cohort_path)
    keys: list[dict[str, Any]] = []
    family_stats: dict[str, Counter[str]] = {
        family: Counter() for family in FAMILIES
    }
    emergency_pairs: list[dict[str, Any]] = []
    for cohort_row in cohort["results"]:
        if cohort_row.get("status") != "OK":
            continue
        result = _load(Path(cohort_row["artifact"]) / "result.json")
        candidates = result["candidates"]
        by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            by_family[candidate["family"]].append(candidate)
        winners = {
            row["family"]: row
            for row in result["frozen_discovery_winners"]
        }
        key_row: dict[str, Any] = {
            "symbol": result["symbol"],
            "side": result["side"],
            "families": {},
            "strict_survivors": len(result["survivors"]),
        }
        b_index = {
            _param_hash(row["params"]): row
            for row in by_family[
                "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED"
            ]
        }
        for family in FAMILIES:
            rows = by_family[family]
            stats = family_stats[family]
            stats["keys"] += 1
            stats["candidates"] += len(rows)
            stats["with_exits"] += sum(
                int(row["metrics"]["exit_fills"]) > 0 for row in rows
            )
            stats["aggregate_above_bh"] += sum(
                float(row["alpha_vs_bh_pp"]) > 0 for row in rows
            )
            stats["aggregate_above_both"] += sum(
                float(row["alpha_vs_bh_pp"]) > 0
                and float(row["alpha_vs_same_entry_e02_pp"]) > 0
                for row in rows
            )
            stats["all_fold_discovery"] += sum(
                bool(row["nested"]["robust_discovery_all_folds"])
                for row in rows
            )
            stats["all_fold_validation"] += sum(
                bool(row["nested"]["robust_validation_fold"])
                for row in rows
            )
            winner = winners.get(family)
            if winner is None:
                continue
            validation = winner["nested"]["validation"]
            bh = float(validation["bh_capital_return_pct_sum"])
            strategy = float(validation["capital_return_pct_sum"])
            key_row["families"][family] = {
                "strategy_return_pct": strategy,
                "bh_return_pct": bh,
                "same_entry_e02_return_pct": _control_return(
                    winner, "validation"
                ),
                "bh_multiple": (
                    strategy / bh if abs(bh) > 1e-12 else None
                ),
                "weighted_tim_pct": float(
                    validation[
                        "exposure_weighted_tim_pct_row_weighted"
                    ]
                ),
                "exit_fills": int(validation["exit_fills"]),
                "strict": _strict(winner),
                "params": winner["params"],
            }
        for emergency in by_family[
            "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED"
        ]:
            base = b_index.get(emergency["paired_b_params_sha256"])
            if base is None:
                raise RuntimeError(
                    f"{result['symbol']}_{result['side']}: missing paired B"
                )
            for partition in ("discovery", "validation"):
                b_metrics = base["nested"][partition]
                c_metrics = emergency["nested"][partition]
                emergency_pairs.append(
                    {
                        "symbol": result["symbol"],
                        "side": result["side"],
                        "partition": partition,
                        "label": emergency["params"]["emergency_label"],
                        "base_rank": emergency["paired_b_discovery_rank"],
                        "delta_return_pp": float(
                            c_metrics["capital_return_pct_sum"]
                        )
                        - float(b_metrics["capital_return_pct_sum"]),
                        "delta_tim_pp": float(
                            c_metrics[
                                "exposure_weighted_tim_pct_row_weighted"
                            ]
                        )
                        - float(
                            b_metrics[
                                "exposure_weighted_tim_pct_row_weighted"
                            ]
                        ),
                        "emergency_share": float(
                            c_metrics["emergency_exit_share"]
                        ),
                        "emergency_fills": int(
                            c_metrics["emergency_exit_fills"]
                        ),
                        "emergency_pnl_usd": float(
                            c_metrics["emergency_exit_pnl_usd"]
                        ),
                    }
                )
        keys.append(key_row)
    brake_summary = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in emergency_pairs:
        if row["partition"] == "validation":
            grouped[row["label"]].append(row)
    for label, rows in sorted(grouped.items()):
        deltas = [row["delta_return_pp"] for row in rows]
        brake_summary.append(
            {
                "label": label,
                "pairs": len(rows),
                "positive_delta": sum(value > 0 for value in deltas),
                "median_delta_return_pp": statistics.median(deltas),
                "mean_delta_return_pp": statistics.fmean(deltas),
                "rare_share_pass": sum(
                    row["emergency_share"] <= 0.25 for row in rows
                ),
                "total_emergency_pnl_usd": sum(
                    row["emergency_pnl_usd"] for row in rows
                ),
            }
        )
    return {
        "source_cohort": str(cohort_path.resolve()),
        "keys": sorted(keys, key=lambda row: (row["side"], row["symbol"])),
        "family_stats": {
            family: dict(stats) for family, stats in family_stats.items()
        },
        "emergency_pairs": emergency_pairs,
        "brake_summary": brake_summary,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Extended causal bottom-exit results — 2026-07-26",
        "",
        "Frozen entry requests, side-isolated LONG/SHORT accounting, $2,000 "
        "B&H, $16,000 strategy capacity, completed bars, next-RTH fills, and "
        "persistent reclaim are held constant. First-break/DC-low rows are "
        "diagnostic controls only. Promotion requires every fold to beat both "
        "B&H and identical-entry E02 while keeping 70–80% weighted TIM.",
        "",
        "## Per-key frozen discovery selections, untouched validation",
        "",
        "| key | family | return | B&H | E02 | ×B&H | TIM | exits | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    labels = {
        FAMILIES[0]: "A protective",
        FAMILIES[1]: "B lower-top",
        FAMILIES[2]: "C + brake",
    }
    for key in report["keys"]:
        for family in FAMILIES:
            row = key["families"].get(family)
            if row is None:
                continue
            multiple = row["bh_multiple"]
            target = (
                multiple is not None
                and 2.0 <= multiple <= 10.0
                and 70.0 <= row["weighted_tim_pct"] <= 80.0
            )
            verdict = (
                "STRICT"
                if row["strict"]
                else "2–10×/TIM"
                if target
                else "GRAY"
            )
            lines.append(
                f"| {key['symbol']}_{key['side']} | {labels[family]} | "
                f"{_fmt(row['strategy_return_pct'])} | "
                f"{_fmt(row['bh_return_pct'])} | "
                f"{_fmt(row['same_entry_e02_return_pct'])} | "
                f"{_fmt(multiple, 3)} | "
                f"{_fmt(row['weighted_tim_pct'])}% | "
                f"{row['exit_fills']} | {verdict} |"
            )
    lines.extend(
        [
            "",
            "## Candidate funnel",
            "",
            "| family | candidates | actual exits | >B&H | >B&H and E02 | "
            "all-fold discovery | untouched validation |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for family in FAMILIES:
        stats = report["family_stats"][family]
        lines.append(
            f"| {labels[family]} | {stats.get('candidates', 0)} | "
            f"{stats.get('with_exits', 0)} | "
            f"{stats.get('aggregate_above_bh', 0)} | "
            f"{stats.get('aggregate_above_both', 0)} | "
            f"{stats.get('all_fold_discovery', 0)} | "
            f"{stats.get('all_fold_validation', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Emergency brake paired effect",
            "",
            "| brake | pairs | validation improved | median Δreturn | "
            "mean Δreturn | rare-share pass | emergency P&L |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["brake_summary"]:
        lines.append(
            f"| {row['label']} | {row['pairs']} | "
            f"{row['positive_delta']} | "
            f"{_fmt(row['median_delta_return_pp'])}pp | "
            f"{_fmt(row['mean_delta_return_pp'])}pp | "
            f"{row['rare_share_pass']} | "
            f"${_fmt(row['total_emergency_pnl_usd'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()
    report = analyze(args.cohort)
    args.json_out.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    args.md_out.write_text(render_markdown(report))
    print(
        json.dumps(
            {
                "keys": len(report["keys"]),
                "json": str(args.json_out),
                "markdown": str(args.md_out),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
