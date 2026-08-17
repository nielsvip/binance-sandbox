#!/usr/bin/env python3
"""Summarize the disconnected trend-resume augment reconstruction screen."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAMILY = "ENTRY_AUGMENT_TREND_RESUME_ENABLED"


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


def summarize(root: Path) -> dict[str, Any]:
    rows = []
    for path, payload in _latest(root):
        manifest, agg = payload["manifest"], payload["aggregate"]
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
                "exposure_policy_pass": all_fold_exposure,
                "all_folds_exposure_policy_pass": all_fold_exposure,
                "future_htf_count": agg["future_htf_count"],
                "mandatory_reclaim": agg["all_mandatory_reclaim"],
                "augment_fills": agg["augment_fill_count"],
                "technical_exits": sum(
                    fold["validation_metrics"]["exit_count"] for fold in folds
                ),
                "status": "VECTOR_SURVIVOR" if strict else "DISCARD_GRAY",
                "selected_settings_by_fold": [
                    fold["selected_candidate"]["params"] for fold in folds
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
            "augment_fills": sum(row["augment_fills"] for row in cohort),
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
    selected = Counter()
    for row in rows:
        for setting in row["selected_settings_by_fold"]:
            selected[
                (
                    setting["min_gain_pct"],
                    setting["rsi_boundary"],
                    setting["add_mult"],
                )
            ] += 1
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "family": FAMILY,
            "live_path_status": (
                "DISCONNECTED: switch and reason absent from active "
                "tradier_manage.py/config"
            ),
            "research_source": (
                "backups/before_desktop_tradier_fixes_20260721.py "
                "last-real evaluate_augment block"
            ),
            "last_real_setting": {
                "min_gain_pct": 0.5,
                "rsi_boundary": 70.0,
                "add_mult": 0.5,
            },
            "swept": {
                "min_gain_pct": [0.5, 1.0, 2.0, 3.0],
                "rsi_boundary": [60.0, 70.0, 80.0, 100.0],
                "add_mult": [0.25, 0.5],
            },
            "same_control": "frozen ladder + E02 4h N30 + resting reclaim",
            "weighted_tim_target_each_fold_pct": [70.0, 80.0],
            "exact_rule": "only strict vector survivor advances",
            "live_config_changed": False,
        },
        "cohorts": cohorts,
        "selected_setting_frequency": [
            {
                "min_gain_pct": key[0],
                "rsi_boundary": key[1],
                "add_mult": key[2],
                "folds": count,
            }
            for key, count in selected.most_common()
        ],
        "rows": sorted(rows, key=lambda row: (row["side"], row["symbol"])),
        "survivors": [row for row in rows if row["status"] == "VECTOR_SURVIVOR"],
    }


def _f(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Trend-resume augment reconstruction — 2026-07-26",
        "",
        "## Verdict",
        "",
        "`AUGMENT_TREND_RESUME_ENABLED` is not wired in the active stock manager "
        "or config. The inventory row was stale. This is a research-only "
        "reconstruction of the last real July 21 code, not a live-path parity claim.",
        "",
        "The reconstructed path adds only to an already-open profitable position: "
        "LONG requires close above the 5m Donchian basis, K>D, and RSI below the "
        "declared boundary; SHORT is mirrored. The sweep varied minimum gain, RSI "
        "boundary, and additive START_POSITION_SIZE fraction while preserving the "
        "frozen ladder, E02 4h N30 exit, reclaim, $16k cap, and costs.",
        "",
        "**Zero of 20 keys passed B&H, the identical control, and 70–80% weighted "
        "TIM in every validation fold. No exact replay ran and no live setting changed.**",
        "",
        "TTD_SHORT beat B&H and control in every fold but aggregated at 89.79% "
        "TIM and failed the per-fold exposure gate. It is retained gray.",
        "",
        "## Cohort aggregates",
        "",
        "| cohort | rows | candidate sum | B&H sum | control sum | augment fills | all folds >B&H | >control | all-fold TIM | survivors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cohort in payload["cohorts"].items():
        lines.append(
            f"| {name} | {cohort['rows']} | "
            f"{_f(cohort['candidate_return_pct_sum'])}% | "
            f"{_f(cohort['bh_return_pct_sum'])}% | "
            f"{_f(cohort['control_return_pct_sum'])}% | "
            f"{cohort['augment_fills']} | "
            f"{cohort['all_folds_beat_bh_count']} | "
            f"{cohort['all_folds_beat_control_count']} | "
            f"{cohort['all_folds_exposure_pass_count']} | "
            f"{cohort['strict_survivors']} |"
        )
    lines += [
        "",
        "## Per-key gray evidence",
        "",
        "| key | candidate | B&H | control | TIM folds | augments | folds >B&H/control | selected settings |",
        "|---|---:|---:|---:|---|---:|---|---|",
    ]
    for row in payload["rows"]:
        settings = " &#124; ".join(
            "gain>={min_gain_pct:g}%, RSI={rsi_boundary:g}, add={add_mult:g}x".format(
                **setting
            )
            for setting in row["selected_settings_by_fold"]
        )
        tim = "/".join(f"{value:.1f}" for value in row["fold_tim_pct"])
        lines.append(
            f"| {row['symbol']}_{row['side']} | "
            f"{_f(row['candidate_return_pct'])}% | "
            f"{_f(row['bh_return_pct'])}% | "
            f"{_f(row['control_return_pct'])}% | {tim}% | "
            f"{row['augment_fills']} | "
            f"{'yes' if row['all_folds_beat_bh'] else 'no'}/"
            f"{'yes' if row['all_folds_beat_control'] else 'no'} | "
            f"{settings} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--md-out", type=Path, required=True)
    args = parser.parse_args()
    payload = summarize(args.root)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.md_out.write_text(markdown(payload))
    print(
        json.dumps(
            {
                "rows": len(payload["rows"]),
                "survivors": len(payload["survivors"]),
                "cohorts": payload["cohorts"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
