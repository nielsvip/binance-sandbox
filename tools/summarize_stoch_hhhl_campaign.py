#!/usr/bin/env python3
"""Summarize the frozen top/bottom-10 structural Stoch entry campaign."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _latest(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    chosen = {}
    for path in root.glob("entry_overlay_ENTRY_STOCH_HHHL_*"):
        result = path / "result.json"
        if not result.exists():
            continue
        payload = json.loads(result.read_text())
        manifest = payload["manifest"]
        if "exposure_policy" not in payload["aggregate"]:
            continue
        key = (manifest["symbol"], manifest["side"])
        if key not in chosen or path.name > chosen[key][0].name:
            chosen[key] = (path, payload)
    return [chosen[key] for key in sorted(chosen)]


def _setting(candidate: dict[str, Any]) -> str:
    p = candidate["params"]
    return (
        f"{candidate['role']}; K={p['stoch_threshold']:g}; "
        f"TF={'+'.join(p['enabled_tfs'])}; "
        f"confirm={p['min_confirming_tfs']}"
    )


def summarize(root: Path) -> dict[str, Any]:
    rows = []
    for path, payload in _latest(root):
        manifest = payload["manifest"]
        agg = payload["aggregate"]
        rows.append(
            {
                "family": "ENTRY_STOCH_HHHL",
                "symbol": manifest["symbol"],
                "side": manifest["side"],
                "candidate_return_pct": agg["candidate_capital_return_pct_sum"],
                "bh_return_pct": agg["bh_capital_return_pct_sum"],
                "control_return_pct": agg["control_capital_return_pct_sum"],
                "bh_multiple": agg["candidate_bh_multiple"],
                "control_multiple": agg["candidate_control_multiple"],
                "weighted_tim_pct": agg["weighted_tim_pct"],
                "all_folds_beat_bh": agg["all_folds_beat_bh"],
                "all_folds_beat_control": agg["all_folds_beat_control"],
                "exposure_policy_pass": agg["exposure_policy"]["pass"],
                "future_htf_count": agg["future_htf_count"],
                "mandatory_reclaim": agg["all_mandatory_reclaim"],
                "status": (
                    "VECTOR_SURVIVOR"
                    if agg["vector_survivor"]
                    else "DISCARD_GRAY"
                ),
                "selected_settings_by_fold": [
                    _setting(fold["selected_candidate"])
                    for fold in payload["outer_folds"]
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
            "all_folds_beat_bh_count": sum(
                row["all_folds_beat_bh"] for row in cohort
            ),
            "all_folds_beat_control_count": sum(
                row["all_folds_beat_control"] for row in cohort
            ),
            "exposure_policy_pass_count": sum(
                row["exposure_policy_pass"] for row in cohort
            ),
            "strict_survivors": sum(
                row["status"] == "VECTOR_SURVIVOR" for row in cohort
            ),
        }
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "family": "ENTRY_STOCH_HHHL",
            "long": "completed HH+HL with low and rising Stoch",
            "short": "completed LH+LL with high and falling Stoch",
            "thresholds": [15, 20, 25, 30, 35, 40],
            "timeframes": ["1h", "4h", "D"],
            "min_confirming_tfs": [1, 2],
            "roles": ["direct", "union-with-green"],
            "same_control": "frozen ladder + E02 4h N30 + resting reclaim",
            "weighted_tim_target_pct": [70, 80],
            "below_or_nonrobust_status": "DISCARD_GRAY",
        },
        "cohorts": cohorts,
        "rows": sorted(rows, key=lambda row: (row["side"], row["symbol"])),
    }


def _f(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Structural Stoch HH/HL entry campaign — 2026-07-26",
        "",
        "## Verdict",
        "",
        "The 132-setting completed-bar grid finished over the frozen top-10 LONG "
        "and bottom-10 SHORT cohorts. LONG and SHORT used separate ledgers and "
        "mirrored structure; no P&L was pooled or inverted. Every candidate kept "
        "the fold's ladder sizing/capacity, E02 4h N=30 exit, next-RTH fills, and "
        "mandatory resting reclaim.",
        "",
        "**Zero candidates passed B&H, the identical ladder+E02 control, every "
        "validation fold, and the 70–80% exposure gate together.** Exact replay "
        "was therefore not launched. All 20 rows remain `DISCARD_GRAY` evidence.",
        "",
        "## Cohort aggregates",
        "",
        "| cohort | rows | candidate sum | B&H sum | control sum | all-fold >B&H | all-fold >control | TIM pass | survivors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cohort in payload["cohorts"].items():
        lines.append(
            f"| {name} | {cohort['rows']} | {_f(cohort['candidate_return_pct_sum'])}% | "
            f"{_f(cohort['bh_return_pct_sum'])}% | {_f(cohort['control_return_pct_sum'])}% | "
            f"{cohort['all_folds_beat_bh_count']} | {cohort['all_folds_beat_control_count']} | "
            f"{cohort['exposure_policy_pass_count']} | {cohort['strict_survivors']} |"
        )
    lines += [
        "",
        "## Per-key results",
        "",
        "| key | candidate | B&H | control | TIM | folds >B&H/control | setting folds | status |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["rows"]:
        settings = " &#124; ".join(row["selected_settings_by_fold"])
        lines.append(
            f"| {row['symbol']}_{row['side']} | {_f(row['candidate_return_pct'])}% | "
            f"{_f(row['bh_return_pct'])}% | {_f(row['control_return_pct'])}% | "
            f"{_f(row['weighted_tim_pct'])}% | "
            f"{'yes' if row['all_folds_beat_bh'] else 'no'}/"
            f"{'yes' if row['all_folds_beat_control'] else 'no'} | "
            f"{settings} | {row['status']} |"
        )
    lines += [
        "",
        "## Decision",
        "",
        "- PBF_LONG beat both comparisons in every fold, but only reached 46.88% "
        "weighted TIM. It is not a survivor.",
        "- The low-exposure MPC/VLO/PBF controls could not be lifted into 70–80% "
        "while preserving robust control alpha with this entry overlay. Their "
        "next owner is ladder multiplier/trigger tuning, not another Stoch threshold sweep.",
        "- SHORT structure did not repair the weak/negative frozen SHORT controls. "
        "Only TTD_SHORT beat B&H in every fold, and it still missed control and TIM.",
        "",
    ]
    return "\n".join(lines)


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
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
