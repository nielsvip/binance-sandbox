#!/usr/bin/env python3
"""Summarize all eight completed-15m job452 arms."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_report(summary: dict[str, Any]) -> str:
    rows = []
    for selected in summary["symbols"]:
        result = json.loads((Path(selected["artifact"]) / "result.json").read_text())
        for candidate in result["candidates"]:
            if candidate["family"] != "EXIT_ALGO_PROFIT_TAKE_15M":
                continue
            val = candidate["nested"]["validation"]
            rows.append({
                "params": candidate["params"],
                "return": val["capital_return_pct_sum"],
                "bh": val["bh_capital_return_pct_sum"],
                "control": val["capital_return_pct_sum"]
                - candidate["nested"]["validation_alpha_vs_same_entry_e02_pp"],
                "exits": val["exit_fills"],
                "every_fold": candidate["nested"]["robust_discovery_all_folds"]
                and candidate["nested"]["robust_validation_fold"],
            })
    if len(rows) != 160:
        raise ValueError(f"expected 160 arms, got {len(rows)}")
    lines = [
        "# EXIT_ALGO_PROFIT_TAKE_15M job452 result", "",
        "## Verdict", "",
        f"20 aligned keys × 8 completed-15m arms; errors **{summary['errors']}**; "
        f"strict survivors **{len(summary['exact_replay_queue'])}**.", "",
        f"Across all 160 arms, **{sum(r['return'] > r['bh'] for r in rows)}** beat "
        f"B&H, **{sum(r['return'] > r['control'] for r in rows)}** beat E02, "
        f"**{sum(r['exits'] > 0 for r in rows)}** made exits, and "
        f"**{sum(r['every_fold'] for r in rows)}** passed every-fold alpha/safety "
        "before exposure selection.", "",
        "The >5% historical seed and -5 score are provenance only. State and "
        "true-cross events are separate; the compound ALGO score is absent.", "",
        "| mode | min profit | >B&H | >E02 | exits |",
        "|---|---:|---:|---:|---:|",
    ]
    for mode in ("STATE", "CROSS"):
        for profit in (3.0, 5.0, 7.0, 10.0):
            arm = [r for r in rows if r["params"]["event_mode"] == mode
                   and r["params"]["min_profit_pct"] == profit]
            lines.append(
                f"| {mode} | {profit:g}% | "
                f"{sum(r['return'] > r['bh'] for r in arm)}/20 | "
                f"{sum(r['return'] > r['control'] for r in arm)}/20 | "
                f"{sum(r['exits'] > 0 for r in arm)}/20 |"
            )
    lines += ["", "## Selected rows", "",
              "| key | mode/gate | return | B&H | E02 | TIM | exits | verdict |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in sorted(summary["symbols"], key=lambda x: (x["side"], x["symbol"])):
        p = row["params"]; verdict = []
        if row["strategy_return_pct"] <= row["bh_return_pct"]: verdict.append("≤B&H")
        if row["strategy_return_pct"] <= row["same_entry_control_return_pct"]: verdict.append("≤E02")
        if not 70 <= row["tim_pct"] <= 80: verdict.append("TIM")
        if row["insolvent_folds"]: verdict.append("insolvent fold")
        lines.append(
            f"| {row['symbol']} {row['side']} | `{p['event_mode']} "
            f"p≥{p['min_profit_pct']:g}%` | {row['strategy_return_pct']:.2f}% | "
            f"{row['bh_return_pct']:.2f}% | {row['same_entry_control_return_pct']:.2f}% | "
            f"{row['tim_pct']:.2f}% | {row['trades']} | "
            f"{', '.join(verdict) or 'earlier-fold gate'} |"
        )
    lines += ["", "Bear-mode remains a filter-only job; no live switch changed.", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.write_text(build_report(json.loads(args.summary.read_text())))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
