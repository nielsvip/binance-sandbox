#!/usr/bin/env python3
"""Summarize all 12 mirrored job451 arms."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_report(summary: dict[str, Any]) -> str:
    candidates = []
    for selected in summary["symbols"]:
        result = json.loads(
            (Path(selected["artifact"]) / "result.json").read_text()
        )
        for row in result["candidates"]:
            if row["family"] != "EXIT_ALGO_STOCH_4H_ROLL":
                continue
            validation = row["nested"]["validation"]
            candidates.append(
                {
                    "symbol": result["symbol"],
                    "side": result["side"],
                    "params": row["params"],
                    "return": validation["capital_return_pct_sum"],
                    "bh": validation["bh_capital_return_pct_sum"],
                    "control": validation["capital_return_pct_sum"]
                    - row["nested"][
                        "validation_alpha_vs_same_entry_e02_pp"
                    ],
                    "exits": validation["exit_fills"],
                    "every_fold": row["nested"][
                        "robust_discovery_all_folds"
                    ]
                    and row["nested"]["robust_validation_fold"],
                }
            )
    if len(candidates) != 240:
        raise ValueError(f"expected 240 exact arms, got {len(candidates)}")
    lines = [
        "# EXIT_ALGO_STOCH_4H_ROLL job451 result",
        "",
        "## Verdict",
        "",
        (
            f"20 aligned keys × 12 completed-4h mirrored arms finished with "
            f"{summary['errors']} errors. Strict survivors: "
            f"**{len(summary['exact_replay_queue'])}**."
        ),
        "",
        (
            f"Across all 240 arms, **{sum(r['return'] > r['bh'] for r in candidates)}** "
            f"beat B&H, **{sum(r['return'] > r['control'] for r in candidates)}** "
            f"beat same-entry E02, **{sum(r['exits'] > 0 for r in candidates)}** "
            f"made actual exits, and **{sum(r['every_fold'] for r in candidates)}** "
            "passed every-fold alpha/safety before exposure selection."
        ),
        "",
        "The removed -5 score contribution is provenance only. State and true "
        "cross events remain distinct; the old asymmetric LONG>60/SHORT<20 "
        "constants were not combined into a biased arm.",
        "",
        "## Exact arm aggregates",
        "",
        "| mirror L/S | mode | gate | >B&H | >E02 | exits |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for long_k in (60.0, 70.0, 80.0):
        for mode in ("STATE", "CROSS"):
            for profit in (0.0, 3.0):
                rows = [
                    r
                    for r in candidates
                    if r["params"]["long_k_min"] == long_k
                    and r["params"]["event_mode"] == mode
                    and r["params"]["profit_gate_pct"] == profit
                ]
                lines.append(
                    f"| {long_k:g}/{100-long_k:g} | {mode} | {profit:g}% | "
                    f"{sum(r['return'] > r['bh'] for r in rows)}/20 | "
                    f"{sum(r['return'] > r['control'] for r in rows)}/20 | "
                    f"{sum(r['exits'] > 0 for r in rows)}/20 |"
                )
    lines += [
        "",
        "## Selected validation rows",
        "",
        "| key | mirror/mode/gate | return | B&H | E02 | TIM | exits | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(summary["symbols"], key=lambda x: (x["side"], x["symbol"])):
        p = row["params"]
        verdict = []
        if row["strategy_return_pct"] <= row["bh_return_pct"]:
            verdict.append("≤B&H")
        if row["strategy_return_pct"] <= row["same_entry_control_return_pct"]:
            verdict.append("≤E02")
        if not 70 <= row["tim_pct"] <= 80:
            verdict.append("TIM")
        if row["insolvent_folds"]:
            verdict.append("insolvent fold")
        lines.append(
            f"| {row['symbol']} {row['side']} | "
            f"`{p['long_k_min']:g}/{p['short_k_max']:g} "
            f"{p['event_mode']} p≥{p['profit_gate_pct']:g}%` | "
            f"{row['strategy_return_pct']:.2f}% | {row['bh_return_pct']:.2f}% | "
            f"{row['same_entry_control_return_pct']:.2f}% | {row['tim_pct']:.2f}% | "
            f"{row['trades']} | {', '.join(verdict) or 'earlier-fold gate'} |"
        )
    lines += [
        "",
        "No live switch or compound ALGO score was changed. Profit-turn and "
        "bear-regime/filter jobs remain separate.",
        "",
    ]
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
