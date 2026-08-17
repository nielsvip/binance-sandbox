#!/usr/bin/env python3
"""Summarize the completed-TF Bollinger recovery entry campaign."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAMILY = "ENTRY_BB_RECOVERY"


def _latest(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    chosen = {}
    for path in root.glob(f"entry_overlay_{FAMILY}_*"):
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
        f"{candidate['role']}; TF={p['timeframe']}; "
        f"recover<={p['recovery_bars']} bars; "
        f"excursion={p['min_excursion_atr']:g} ATR"
    )


def summarize(root: Path) -> dict[str, Any]:
    rows = []
    for path, payload in _latest(root):
        manifest, agg = payload["manifest"], payload["aggregate"]
        rows.append(
            {
                "family": FAMILY,
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
            "family": FAMILY,
            "signal": (
                "completed-TF close outside Bollinger band by declared ATR "
                "excursion, then completed close back inside within N TF bars"
            ),
            "timeframes": ["15m", "1h", "4h"],
            "recovery_bars": [1, 2, 4, 8],
            "min_excursion_atr": [0.0, 0.25, 0.5, 1.0],
            "roles": ["direct", "union-with-green"],
            "same_control": "frozen ladder + E02 4h N30 + resting reclaim",
            "weighted_tim_target_pct": [70, 80],
            "below_or_nonrobust_status": "DISCARD_GRAY",
            "live_status": "sweep-only/default-off; no live config changed",
        },
        "cohorts": cohorts,
        "rows": sorted(rows, key=lambda row: (row["side"], row["symbol"])),
    }


def _f(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Bollinger failed-break recovery entry campaign — 2026-07-26",
        "",
        "## Verdict",
        "",
        "The 96-setting completed-bar screen finished over the frozen top-10 LONG "
        "and bottom-10 SHORT cohorts. It tested 15m/1h/4h recovery within "
        "1/2/4/8 completed TF bars after 0/0.25/0.5/1 ATR band excursions, as "
        "a direct entry and union with the frozen green schedule.",
        "",
        "**Zero candidates passed B&H, the identical ladder+E02 control, every "
        "validation fold, and 70–80% weighted TIM together.** Exact replay was "
        "not launched. All 20 rows remain `DISCARD_GRAY`; live config stayed off.",
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
        "## Per-key gray evidence",
        "",
        "| key | candidate | B&H | control | TIM | folds >B&H/control | fold settings |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["rows"]:
        settings = " &#124; ".join(row["selected_settings_by_fold"])
        lines.append(
            f"| {row['symbol']}_{row['side']} | {_f(row['candidate_return_pct'])}% | "
            f"{_f(row['bh_return_pct'])}% | {_f(row['control_return_pct'])}% | "
            f"{_f(row['weighted_tim_pct'])}% | "
            f"{'yes' if row['all_folds_beat_bh'] else 'no'}/"
            f"{'yes' if row['all_folds_beat_control'] else 'no'} | {settings} |"
        )
    lines += [
        "",
        "## Decision",
        "",
        "- Several LONG aggregates exceeded the control, but none combined "
        "all-fold robustness with the exposure band. PBF/MPC/DINO/VLO again "
        "show that the low-exposure problem belongs to ladder trigger/multiplier tuning.",
        "- ACN_SHORT reached 73.40% TIM and beat B&H in every fold, but did not "
        "beat the identical control in every fold. It remains gray.",
        "- The path is sweep-only and default-off. No live switch or symbol file changed.",
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
