#!/usr/bin/env python3
"""Summarize all four job450 arms, not only independently selected winners."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_report(summary: dict[str, Any]) -> str:
    candidates = []
    for symbol_row in summary["symbols"]:
        result = json.loads(
            (Path(symbol_row["artifact"]) / "result.json").read_text()
        )
        for row in result["candidates"]:
            if row["family"] != "EXIT_ALGO_STRUCTURE_1H_15M":
                continue
            validation = row["nested"]["validation"]
            candidates.append(
                {
                    "symbol": result["symbol"],
                    "side": result["side"],
                    "params": row["params"],
                    "return": validation["capital_return_pct_sum"],
                    "bh": validation["bh_capital_return_pct_sum"],
                    "control": (
                        validation["capital_return_pct_sum"]
                        - row["nested"][
                            "validation_alpha_vs_same_entry_e02_pp"
                        ]
                    ),
                    "exits": validation["exit_fills"],
                    "tim": validation["exposure_weighted_tim_pct_row_weighted"],
                    "every_fold": (
                        row["nested"]["robust_discovery_all_folds"]
                        and row["nested"]["robust_validation_fold"]
                    ),
                }
            )
    if len(candidates) != len(summary["symbols"]) * 4:
        raise ValueError("job450 report expected exactly four arms per key")
    lines = [
        "# EXIT_ALGO_STRUCTURE_1H_15M job450 result",
        "",
        "## Verdict",
        "",
        (
            f"{len(summary['symbols'])} aligned keys × 4 standalone completed-"
            f"structure arms finished with {summary['errors']} errors. Strict "
            f"survivors: **{len(summary['exact_replay_queue'])}**."
        ),
        "",
        (
            f"Across all {len(candidates)} exact arms, "
            f"**{sum(r['return'] > r['bh'] for r in candidates)}** beat B&H, "
            f"**{sum(r['return'] > r['control'] for r in candidates)}** beat "
            f"same-entry E02, and **{sum(r['every_fold'] for r in candidates)}** "
            "passed every-fold alpha/safety before exposure selection."
        ),
        "",
        "The historical -15/-10 values are provenance only; the removed opaque "
        "base score was not reconstructed. Gray rows are retained evidence.",
        "",
        "## Exact arm aggregates",
        "",
        "| timeframe | profit gate | keys > B&H | keys > E02 | keys with exits |",
        "|---|---:|---:|---:|---:|",
    ]
    for timeframe in ("1h", "15m"):
        for profit in (0.0, 3.0):
            rows = [
                r
                for r in candidates
                if r["params"]["timeframe"] == timeframe
                and r["params"]["profit_gate_pct"] == profit
            ]
            lines.append(
                f"| {timeframe} | {profit:g}% | "
                f"{sum(r['return'] > r['bh'] for r in rows)}/{len(rows)} | "
                f"{sum(r['return'] > r['control'] for r in rows)}/{len(rows)} | "
                f"{sum(r['exits'] > 0 for r in rows)}/{len(rows)} |"
            )
    lines += [
        "",
        "## Selected validation row per key",
        "",
        "| key | TF/gate | return | B&H | E02 | TIM | exits | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(summary["symbols"], key=lambda x: (x["side"], x["symbol"])):
        verdict = []
        if row["strategy_return_pct"] <= row["bh_return_pct"]:
            verdict.append("≤B&H")
        if row["strategy_return_pct"] <= row["same_entry_control_return_pct"]:
            verdict.append("≤E02")
        if not 70 <= row["tim_pct"] <= 80:
            verdict.append("TIM")
        if row["insolvent_folds"]:
            verdict.append("insolvent fold")
        params = row["params"]
        lines.append(
            f"| {row['symbol']} {row['side']} | "
            f"`{params['timeframe']}/p≥{params['profit_gate_pct']:g}%` | "
            f"{row['strategy_return_pct']:.2f}% | {row['bh_return_pct']:.2f}% | "
            f"{row['same_entry_control_return_pct']:.2f}% | {row['tim_pct']:.2f}% | "
            f"{row['trades']} | {', '.join(verdict) or 'earlier-fold gate'} |"
        )
    lines += [
        "",
        "No live configuration was changed. The 4h Stoch, 15m profit-turn, and "
        "bear-regime components remain separate jobs.",
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
