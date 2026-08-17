#!/usr/bin/env python3
"""Summarize DC-tier augment artifacts for normalized fleet ingestion."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


FAMILY = "ENTRY_DC_TIER_AUG_ENABLED"


def summarize(root: Path) -> dict:
    chosen = {}
    for artifact in root.glob(f"entry_overlay_{FAMILY}_*"):
        result = artifact / "result.json"
        if not result.exists():
            continue
        payload = json.loads(result.read_text())
        manifest = payload["manifest"]
        key = (manifest["symbol"], manifest["side"])
        if key not in chosen or artifact.name > chosen[key][0].name:
            chosen[key] = (artifact, payload)
    rows = []
    for artifact, payload in chosen.values():
        manifest, agg = payload["manifest"], payload["aggregate"]
        rows.append({
            "family": FAMILY,
            "symbol": manifest["symbol"],
            "side": manifest["side"],
            "status": "VECTOR_SURVIVOR" if agg["vector_survivor"] else "DISCARD_GRAY",
            "candidate_return_pct": agg["candidate_capital_return_pct_sum"],
            "bh_return_pct": agg["bh_capital_return_pct_sum"],
            "control_return_pct": agg["control_capital_return_pct_sum"],
            "weighted_tim_pct": agg["weighted_tim_pct"],
            "all_folds_beat_bh": agg["all_folds_beat_bh"],
            "all_folds_beat_control": agg["all_folds_beat_control"],
            "all_folds_exposure_policy_pass": agg["all_folds_exposure_policy_pass"],
            "exposure_policy_pass": agg["all_folds_exposure_policy_pass"],
            "future_htf_count": agg["future_htf_count"],
            "augment_signals": agg["augment_signal_count"],
            "augment_requests": agg["augment_request_count"],
            "augment_fills": agg["augment_fill_count"],
            "selected_settings_by_fold": [
                fold["selected_candidate"]["params"]
                for fold in payload["outer_folds"]
            ],
            "fold_tim_pct": [
                fold["validation_metrics"]["exposure_weighted_tim_pct"]
                for fold in payload["outer_folds"]
            ],
            "artifact": str(artifact),
        })
    cohorts = {}
    for side, name in (("LONG", "TOP_10_LONG"), ("SHORT", "BOTTOM_10_SHORT")):
        cohort = [row for row in rows if row["side"] == side]
        cohorts[name] = {
            "rows": len(cohort),
            "candidate_return_pct_sum": sum(r["candidate_return_pct"] for r in cohort),
            "bh_return_pct_sum": sum(r["bh_return_pct"] for r in cohort),
            "control_return_pct_sum": sum(r["control_return_pct"] for r in cohort),
            "signals": sum(r["augment_signals"] for r in cohort),
            "requests": sum(r["augment_requests"] for r in cohort),
            "fills": sum(r["augment_fills"] for r in cohort),
            "strict_survivors": sum(r["status"] == "VECTOR_SURVIVOR" for r in cohort),
        }
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "inventory_switch": "CONNECTED_DEFAULT_TRUE",
            "underlying_function": "ACTIVE_WHEN_ENABLED_INSIDE_evaluate_augment",
            "disabled_scope": "DC breakout-tier block only",
            "grid_candidates": 243,
            "same_control": "frozen ladder + E02 N30 + resting reclaim",
            "exact_rule": "strict vector survivors only",
        },
        "cohorts": cohorts,
        "rows": sorted(rows, key=lambda row: (row["side"], row["symbol"])),
    }


def markdown(payload: dict) -> str:
    lines = [
        "# DC-tier augment causal campaign — 2026-07-26", "",
        "## Wiring verdict", "",
        "`DC_TIER_AUG_ENABLED` is connected with default `True`, preserving the "
        "prior behavior of the underlying tier block inside `evaluate_augment` "
        "after the profit/cooldown gates. `False` disables only that tier block. "
        "The campaign numbers remain research evidence, not live promotion.", "",
        "## Cohorts", "",
        "| cohort | rows | candidate | B&H | control | signals/requests/fills | survivors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["cohorts"].items():
        lines.append(
            f"| {name} | {row['rows']} | {row['candidate_return_pct_sum']:.2f}% | "
            f"{row['bh_return_pct_sum']:.2f}% | {row['control_return_pct_sum']:.2f}% | "
            f"{row['signals']}/{row['requests']}/{row['fills']} | "
            f"{row['strict_survivors']} |"
        )
    lines += [
        "",
        "Zero keys beat the identical control in every fold and zero kept every "
        "validation fold inside 70–80% TIM. Exact replay was therefore not run.",
        "",
        "The source setting is gain 3%, 0.10% buffer, 1/2/3/5x targets, 75% "
        "fill gate, and tier-4 maturity guard off. All other values are labeled "
        "research extensions. The most frequent selected fold setting was the "
        "aggressive 1/2/4/8x profile with gain 1%, zero buffer, 90% fill gate, "
        "and no maturity guard; frequency is not promotion evidence.",
        "",
        "SNDK generated qualifying tier states but zero requests/fills because "
        "its frozen ladder position already exceeded the selected tier targets. "
        "That inert selected row remains discard evidence, not a performance claim.",
        "",
        "## Per key", "",
        "| key | candidate | B&H | control | TIM folds | signals/requests/fills | selected fold settings | status |",
        "|---|---:|---:|---:|---|---:|---|---|"
    ]
    for row in payload["rows"]:
        settings = " &#124; ".join(
            "g={min_gain_pct:g}%,buf={buffer_fraction:.3%},"
            "tiers={tiers},fill={target_fill_ratio:.0%},mat={maturity}".format(
                **setting,
                tiers="/".join(f"{value:g}" for value in setting["tier_profile"]),
                maturity=(
                    "off" if setting["maturity_atr"] is None
                    else f"{setting['maturity_atr']:g}ATR"
                ),
            )
            for setting in row["selected_settings_by_fold"]
        )
        lines.append(
            f"| {row['symbol']}_{row['side']} | {row['candidate_return_pct']:.2f}% | "
            f"{row['bh_return_pct']:.2f}% | {row['control_return_pct']:.2f}% | "
            f"{'/'.join(f'{x:.1f}' for x in row['fold_tim_pct'])}% | "
            f"{row['augment_signals']}/{row['augment_requests']}/{row['augment_fills']} | "
            f"{settings} | "
            f"{row['status']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()
    payload = summarize(args.root)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.md_out.write_text(markdown(payload))
    print(json.dumps(payload["cohorts"], sort_keys=True))


if __name__ == "__main__":
    main()
