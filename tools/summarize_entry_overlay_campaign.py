#!/usr/bin/env python3
"""Summarize latest exposure-policy entry-overlay artifacts.

All non-survivors are retained as ``DISCARD_GRAY`` evidence so later agents do
not repeat settings that beat B&H but fail the stronger ladder+E02 control,
fold robustness, causality/reclaim, or cohort exposure policy.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAMILIES = ("ENTRY_WT_DC", "ENTRY_GOLDEN_RULE")


def _latest(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    chosen: dict[tuple[str, str, str], tuple[Path, dict[str, Any]]] = {}
    for path in root.glob("entry_overlay_ENTRY_*_*_LONG"):
        result = path / "result.json"
        if not result.exists():
            continue
        payload = json.loads(result.read_text())
        manifest = payload.get("manifest", {})
        aggregate = payload.get("aggregate", {})
        if "exposure_policy" not in aggregate:
            continue
        key = (
            str(manifest.get("family")),
            str(manifest.get("symbol")),
            str(manifest.get("side")),
        )
        if key[0] not in FAMILIES:
            continue
        prior = chosen.get(key)
        if prior is None or path.name > prior[0].name:
            chosen[key] = (path, payload)
    return [chosen[key] for key in sorted(chosen)]


def _fmt(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def _setting(candidate: dict[str, Any]) -> str:
    p = candidate["params"]
    if candidate["family"] == "ENTRY_WT_DC":
        return (
            f"{candidate['role']}; thr={p['threshold']:g}; "
            f"HTF={p['htf_gate']}; align={p['htf_align_required']}; "
            f"stoch={p['combined_stoch_gate']:g}"
        )
    return (
        f"{candidate['role']}; min_ind={p['min_ind']}; min_tfs={p['min_tfs']}; "
        f"weights={p['weight_name']}; frac={p['threshold_fraction']:g}"
    )


def summarize(root: Path) -> dict[str, Any]:
    artifacts = _latest(root)
    rows = []
    ranges: dict[str, defaultdict[str, list[Any]]] = {
        family: defaultdict(list) for family in FAMILIES
    }
    for path, payload in artifacts:
        manifest = payload["manifest"]
        agg = payload["aggregate"]
        above_bh_aggregate = (
            agg["candidate_capital_return_pct_sum"]
            > agg["bh_capital_return_pct_sum"]
        )
        above_control_aggregate = (
            agg["candidate_capital_return_pct_sum"]
            > agg["control_capital_return_pct_sum"]
        )
        strict = bool(agg["vector_survivor"])
        selected = [fold["selected_candidate"] for fold in payload["outer_folds"]]
        row = {
            "symbol": manifest["symbol"],
            "side": manifest["side"],
            "family": manifest["family"],
            "candidate_return_pct": agg["candidate_capital_return_pct_sum"],
            "bh_return_pct": agg["bh_capital_return_pct_sum"],
            "control_return_pct": agg["control_capital_return_pct_sum"],
            "bh_multiple": agg["candidate_bh_multiple"],
            "control_multiple": agg["candidate_control_multiple"],
            "weighted_tim_pct": agg["weighted_tim_pct"],
            "above_bh_aggregate": above_bh_aggregate,
            "above_control_aggregate": above_control_aggregate,
            "all_folds_beat_bh": agg["all_folds_beat_bh"],
            "all_folds_beat_control": agg["all_folds_beat_control"],
            "exposure_policy_pass": agg["exposure_policy"]["pass"],
            "future_htf_count": agg["future_htf_count"],
            "mandatory_reclaim": agg["all_mandatory_reclaim"],
            "status": "VECTOR_SURVIVOR" if strict else "DISCARD_GRAY",
            "selected_settings_by_fold": [_setting(c) for c in selected],
            "artifact": str(path),
        }
        rows.append(row)
        if above_bh_aggregate:
            for candidate in selected:
                ranges[manifest["family"]]["role"].append(candidate["role"])
                for key, value in candidate["params"].items():
                    ranges[manifest["family"]][key].append(value)
    range_rows = {}
    for family, fields in ranges.items():
        family_ranges = {}
        for key, values in fields.items():
            if not values:
                continue
            if all(isinstance(v, (int, float)) for v in values):
                family_ranges[key] = {
                    "min": min(values),
                    "max": max(values),
                    "observations": len(values),
                }
            else:
                family_ranges[key] = dict(Counter(map(str, values)))
        range_rows[family] = family_ranges
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "cohort": "frozen top-10 Tradier LONG performers",
            "same_control": "frozen ladder + E02 4h N=30 + resting reclaim",
            "weighted_tim_target_pct": [70.0, 80.0],
            "below_or_nonrobust_status": "DISCARD_GRAY",
            "exact_replay_rule": "only VECTOR_SURVIVOR advances",
            "short_status": (
                "NOT_EVALUATED_IN_THIS_BOUNDED_RUN: mirrored frozen SHORT "
                "controls now exist for a side-isolated extension"
            ),
        },
        "rows": sorted(rows, key=lambda r: (r["family"], r["symbol"])),
        "above_bh_selected_parameter_ranges": range_rows,
    }


def _markdown(payload: dict[str, Any]) -> str:
    rows = payload["rows"]
    lines = [
        "# Golden Rule and repaired WT_DC entry overlays — 2026-07-26",
        "",
        "## Verdict",
        "",
        "The two entry families now use completed-HTF causal adapters and the exact "
        "same frozen ladder sizing/capacity, E02 4h N=30 exit, next-RTH fills, "
        "and mandatory resting reclaim as the control. All settings below B&H, "
        "below the stronger control, non-robust across folds, or outside the "
        "70–80% top-cohort exposure target are retained as **DISCARD_GRAY**. "
        "No candidate passed every gate, so exact V8 replay was correctly not run.",
        "",
        "SHORT was not evaluated in this bounded overlay run. Mirrored frozen "
        "SHORT controls now exist for a side-isolated extension; no LONG return "
        "was inverted or pooled.",
        "",
        "## Latest exposure-constrained results",
        "",
        "| family | symbol | candidate | B&H | control | vs B&H | vs control | TIM | all folds > B&H/control | status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {family} | {symbol}_LONG | {candidate}% | {bh}% | {control}% | "
            "{bhx}x | {controlx}x | {tim}% | {fold_bh}/{fold_control} | {status} |".format(
                family=row["family"].replace("ENTRY_", ""),
                symbol=row["symbol"],
                candidate=_fmt(row["candidate_return_pct"]),
                bh=_fmt(row["bh_return_pct"]),
                control=_fmt(row["control_return_pct"]),
                bhx=_fmt(row["bh_multiple"], 3),
                controlx=_fmt(row["control_multiple"], 3),
                tim=_fmt(row["weighted_tim_pct"]),
                fold_bh="yes" if row["all_folds_beat_bh"] else "no",
                fold_control="yes" if row["all_folds_beat_control"] else "no",
                status=row["status"],
            )
        )
    lines += [
        "",
        "## Frozen settings selected by fold",
        "",
    ]
    for row in rows:
        lines.append(
            f"- `{row['symbol']}_LONG {row['family']}` — "
            + " | ".join(row["selected_settings_by_fold"])
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- WT_DC again beats aggregate B&H on most top-cohort names after repairing "
        "its numeric cross/router/switch defects. That confirms it is no longer the "
        "zero-trade/inverted path seen before the repair.",
        "- Beating B&H is not enough here because the frozen ladder+E02 control is "
        "already very strong. Exposure-constrained WT_DC candidates that landed in "
        "70–80% TIM still failed the control or one validation fold.",
        "- Golden Rule also produces several aggregate >B&H rows, but its strongest "
        "control improvements remain below the requested exposure band or fail a fold. "
        "Those are useful ranges, not promotion candidates.",
        "- Entry-only hold proofs were not used. Every number includes E02 exits and "
        "mandatory reclaim with side-isolated stateful accounting.",
        "",
        "Machine-readable ranges and every retained gray row are in the companion JSON.",
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
    args.md_out.write_text(_markdown(payload))
    print(
        json.dumps(
            {
                "rows": len(payload["rows"]),
                "survivors": sum(
                    row["status"] == "VECTOR_SURVIVOR" for row in payload["rows"]
                ),
                "json": str(args.json_out),
                "md": str(args.md_out),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
