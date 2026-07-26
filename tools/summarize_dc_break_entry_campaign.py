#!/usr/bin/env python3
"""Summarize the stale-switch audit and causal DC-break reconstruction."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAMILY = "ENTRY_DC_BREAK_ENTRY_ENABLED"


def _setting(candidate: dict[str, Any]) -> str:
    p = candidate["params"]
    return (
        f"{candidate['role']}; tf={p['timeframe']}; "
        f"buffer={100*p['buffer_fraction']:.2f}%; "
        f"expand1h={p['require_1h_expansion']}; "
        f"confirm={p['confirmation']}"
    )


def summarize(summary_path: Path) -> dict[str, Any]:
    summary = json.loads(summary_path.read_text())
    rows = []
    for source in summary["symbols"]:
        artifact = Path(source["artifact"])
        payload = json.loads((artifact / "result.json").read_text())
        agg = payload["aggregate"]
        rows.append(
            {
                "symbol": source["symbol"],
                "side": source["side"],
                "status": source["status"],
                "candidate_return_pct": agg["candidate_capital_return_pct_sum"],
                "bh_return_pct": agg["bh_capital_return_pct_sum"],
                "control_return_pct": agg["control_capital_return_pct_sum"],
                "weighted_tim_pct": agg["weighted_tim_pct"],
                "all_folds_beat_bh": agg["all_folds_beat_bh"],
                "all_folds_beat_control": agg["all_folds_beat_control"],
                "all_folds_exposure_policy_pass": agg[
                    "all_folds_exposure_policy_pass"
                ],
                "all_capacity_safe": agg["all_capacity_safe"],
                "all_mandatory_reclaim": agg["all_mandatory_reclaim"],
                "future_htf_count": agg["future_htf_count"],
                "entry_signal_rows": agg["entry_signal_rows"],
                "entry_request_count": agg["entry_request_count"],
                "entry_fill_count": agg["entry_fill_count"],
                "settings_by_fold": [
                    _setting(fold["selected_candidate"])
                    for fold in payload["outer_folds"]
                ],
                "artifact": str(artifact),
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
            "all_folds_exposure_pass_count": sum(
                row["all_folds_exposure_policy_pass"] for row in cohort
            ),
            "strict_survivors": sum(
                row["status"] == "VECTOR_SURVIVOR_AWAIT_EXACT"
                for row in cohort
            ),
        }
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "family": FAMILY,
        "wiring_audit": summary["wiring_audit"],
        "contract": {
            "real_switch_result": (
                "RED_DIAGNOSTIC_DISCONNECTED: zero entry requests/fills because "
                "DC_BREAK_ENTRY_ENABLED has no declaration or reader"
            ),
            "reconstruction_status": (
                "RESEARCH_ONLY: combines semantics from fail-closed swing branch "
                "and separately switched StockDaytradeWing"
            ),
            "grid_candidates_per_symbol": 192,
            "timeframes": ["5m", "15m", "1h", "4h"],
            "buffer_fraction": [0.0, 0.0005, 0.001, 0.002],
            "require_1h_expansion": [False, True],
            "confirmation": ["none", "not-exhausted", "directional-stoch"],
            "roles": ["direct", "union-with-green"],
            "same_control": "frozen ladder + E02 4h N30 + resting reclaim",
            "weighted_tim_target_pct_each_fold": [70, 80],
            "exact_replay_rule": "strict vector survivors only",
        },
        "cohorts": cohorts,
        "rows": sorted(rows, key=lambda row: (row["side"], row["symbol"])),
    }


def _f(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Donchian-break entry wiring and reconstruction — 2026-07-26",
        "",
        "## Verdict",
        "",
        "`DC_BREAK_ENTRY_ENABLED` is a stale, disconnected matrix switch: it is "
        "not declared in `config_tradier.py` and no active router reads it. The old "
        "swing branch instead reads `DC_BREAK_ENTRY_DISABLED` with fail-closed "
        "`True`; the launched daytrade loop is a separate strategy controlled by "
        "`DC_DAYTRADE_ENABLED` / `TRADIER_DC_DAYTRADE_ENABLED`.",
        "",
        "The real named path therefore has zero signals, requests, fills, and TIM. "
        "Those rows are retained red. A 192-setting causal reconstruction was also "
        "run over the frozen top-10 LONG and bottom-10 SHORT cohorts. It uses the "
        "same ladder sizing, $16k capacity, E02 N30 exit, costs, next-RTH fills, "
        "and mandatory reclaim as the control. **No row passed both benchmarks, "
        "every validation fold, and 70–80% TIM in every fold.** All reconstructed "
        "rows remain gray and no exact replay or live setting change was made.",
        "",
        "## Cohort aggregates",
        "",
        "| cohort | rows | candidate sum | B&H sum | control sum | all-fold >B&H | all-fold >control | all-fold TIM | survivors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cohort in payload["cohorts"].items():
        lines.append(
            f"| {name} | {cohort['rows']} | "
            f"{_f(cohort['candidate_return_pct_sum'])}% | "
            f"{_f(cohort['bh_return_pct_sum'])}% | "
            f"{_f(cohort['control_return_pct_sum'])}% | "
            f"{cohort['all_folds_beat_bh_count']} | "
            f"{cohort['all_folds_beat_control_count']} | "
            f"{cohort['all_folds_exposure_pass_count']} | "
            f"{cohort['strict_survivors']} |"
        )
    lines += [
        "",
        "## Per-key retained evidence",
        "",
        "| key | candidate | B&H | control | TIM | signals / requests / fills | folds >B&H/control/TIM | selected fold settings |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["rows"]:
        settings = " &#124; ".join(row["settings_by_fold"])
        lines.append(
            f"| {row['symbol']}_{row['side']} | "
            f"{_f(row['candidate_return_pct'])}% | "
            f"{_f(row['bh_return_pct'])}% | "
            f"{_f(row['control_return_pct'])}% | "
            f"{_f(row['weighted_tim_pct'])}% | "
            f"{row['entry_signal_rows']} / {row['entry_request_count']} / "
            f"{row['entry_fill_count']} | "
            f"{'yes' if row['all_folds_beat_bh'] else 'no'}/"
            f"{'yes' if row['all_folds_beat_control'] else 'no'}/"
            f"{'yes' if row['all_folds_exposure_policy_pass'] else 'no'} | "
            f"{settings} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "- The reconstruction is causal and wired: all 20 rows produced requests "
        "and fills, with zero future-HTF, capacity, or reclaim violations.",
        "- MU_LONG beat B&H and the control in every fold, but its aggregate TIM "
        "was 68.29% and the fold exposure constraint failed. This is a useful "
        "range, not a survivor.",
        "- MRVL_LONG met aggregate TIM and exceeded both aggregate benchmarks, "
        "but failed the control in at least one fold.",
        "- SHORT behavior was poor and unstable overall. Positive-looking ratios "
        "against negative returns were not treated as wins; strict comparisons "
        "use signed side-specific returns fold by fold.",
        "- Reconnecting the stale switch is a separate live-code decision. These "
        "research settings cannot be promoted under the current contract.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()
    payload = summarize(args.summary)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    args.md_out.write_text(markdown(payload) + "\n")
    print(json.dumps({"cohorts": payload["cohorts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
