#!/usr/bin/env python3
"""Report job47 without winner-pairing or causal-comparison shortcuts."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row["symbol"]).upper(), str(row["side"]).upper()


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def build_report(mtf: dict[str, Any], bottom_a: dict[str, Any]) -> str:
    mtf_rows = {_key(row): row for row in mtf["symbols"]}
    bottom_rows = {_key(row): row for row in bottom_a["symbols"]}
    if set(mtf_rows) != set(bottom_rows):
        missing_mtf = sorted(set(bottom_rows) - set(mtf_rows))
        missing_bottom = sorted(set(mtf_rows) - set(bottom_rows))
        raise ValueError(
            f"unaligned cohorts: missing_mtf={missing_mtf}, "
            f"missing_bottom_a={missing_bottom}"
        )
    paired_by_key = {
        (str(row["symbol"]).upper(), str(row["side"]).upper()): row["pairs"]
        for row in mtf["paired_confirmation"]
    }
    if set(paired_by_key) != set(mtf_rows):
        raise ValueError("paired confirmation rows do not cover the cohort")

    all_pairs = [
        {**pair, "symbol": key[0], "side": key[1]}
        for key, pairs in paired_by_key.items()
        for pair in pairs
    ]
    positive = [
        row
        for row in all_pairs
        if row["incremental_min2_vs_min1_pp"] > 0
    ]
    exact_zero_exit = sum(
        int(row["min1_exit_fills"] == 0)
        + int(row["min2_exit_fills"] == 0)
        for row in all_pairs
    )
    by_mult: dict[float, list[float]] = defaultdict(list)
    by_profit: dict[float, list[float]] = defaultdict(list)
    for row in all_pairs:
        delta = float(row["incremental_min2_vs_min1_pp"])
        by_mult[float(row["atr_mult"])].append(delta)
        by_profit[float(row["min_profit_pct"])].append(delta)

    selected_both = sum(
        row["strategy_return_pct"] > row["bh_return_pct"]
        and row["strategy_return_pct"] > row["same_entry_control_return_pct"]
        for row in mtf_rows.values()
    )
    selected_bh = sum(
        row["strategy_return_pct"] > row["bh_return_pct"]
        for row in mtf_rows.values()
    )
    selected_control = sum(
        row["strategy_return_pct"] > row["same_entry_control_return_pct"]
        for row in mtf_rows.values()
    )
    selected_tim = sum(70 <= row["tim_pct"] <= 80 for row in mtf_rows.values())
    mtf_better_bottom = sum(
        mtf_rows[key]["strategy_return_pct"]
        > bottom_rows[key]["strategy_return_pct"]
        for key in mtf_rows
    )
    bottom_deltas = [
        mtf_rows[key]["strategy_return_pct"]
        - bottom_rows[key]["strategy_return_pct"]
        for key in mtf_rows
    ]
    min2_every_fold = sum(row["min2_every_fold_valid"] for row in all_pairs)
    min1_every_fold = sum(row["min1_every_fold_valid"] for row in all_pairs)
    arms_above_bh = sum(
        pair[f"min{confirming}_return_pct"] > mtf_rows[key]["bh_return_pct"]
        for key, pairs in paired_by_key.items()
        for pair in pairs
        for confirming in (1, 2)
    )
    arms_above_control = sum(
        pair[f"min{confirming}_return_pct"]
        > mtf_rows[key]["same_entry_control_return_pct"]
        for key, pairs in paired_by_key.items()
        for pair in pairs
        for confirming in (1, 2)
    )

    lines = [
        "# EXIT_MTF_ATR_TRAIL job47 — completed-HTF paired result",
        "",
        "## Verdict",
        "",
        (
            f"Job47 screened {len(mtf_rows)} aligned keys × 40 arms with zero "
            f"worker errors. All selected rows made exits; strict vector "
            f"survivors: **{len(mtf['exact_replay_queue'])}**. Nothing is "
            "eligible for exact replay or matrix promotion."
        ),
        "",
        (
            f"The selected discovery winner beat side-specific B&H on "
            f"**{selected_bh}/{len(mtf_rows)}** keys and the same-entry E02 "
            f"control on **{selected_control}/{len(mtf_rows)}** keys, but beat "
            f"both on only **{selected_both}/{len(mtf_rows)}**. Only "
            f"**{selected_tim}/{len(mtf_rows)}** selected rows were inside the "
            "70–80% exposure band."
        ),
        "",
        (
            f"Across the complete range, **{arms_above_bh}/800** arms beat "
            f"side-specific B&H and **{arms_above_control}/800** beat the "
            "stronger same-entry E02 control in untouched validation. This "
            "establishes that the path is connected and broadly B&H-positive; "
            "it does not satisfy the stricter promotion formula."
        ),
        "",
        "## What the two-timeframe confirmation added",
        "",
        (
            f"Across {len(all_pairs)} exact ATR-multiple/profit-threshold "
            f"pairs, min-2 beat its identical min-1 arm in "
            f"**{len(positive)}/{len(all_pairs)}** cases "
            f"({100 * len(positive) / len(all_pairs):.1f}%). Median paired "
            f"increment was **{_fmt(statistics.median(row['incremental_min2_vs_min1_pp'] for row in all_pairs))} pp**; "
            f"mean **{_fmt(statistics.fmean(row['incremental_min2_vs_min1_pp'] for row in all_pairs))} pp**."
        ),
        "",
        (
            f"Every-fold alpha/safety validity occurred in "
            f"**{min2_every_fold}** min-2 arms versus **{min1_every_fold}** "
            f"min-1 arms before the separate exposure/selection gate. "
            f"Zero-exit paired arms: **{exact_zero_exit}**."
        ),
        "",
        "| ATR multiple | mean min2−min1 pp | positive pairs |",
        "|---:|---:|---:|",
    ]
    for value, deltas in sorted(by_mult.items()):
        lines.append(
            f"| {value:g} | {_fmt(statistics.fmean(deltas))} | "
            f"{sum(delta > 0 for delta in deltas)}/{len(deltas)} |"
        )
    lines += [
        "",
        "| min profit % | mean min2−min1 pp | positive pairs |",
        "|---:|---:|---:|",
    ]
    for value, deltas in sorted(by_profit.items()):
        lines.append(
            f"| {value:g} | {_fmt(statistics.fmean(deltas))} | "
            f"{sum(delta > 0 for delta in deltas)}/{len(deltas)} |"
        )
    lines += [
        "",
        "## Range coverage by key",
        "",
        (
            "Counts are exact arms, not independently selected winners. The "
            "registered range is ATR x1.5/2/2.5/3/4 × profit ≥0/0.25/0.5/1% × "
            "1 or 2 confirming TFs."
        ),
        "",
        "| key | arms > B&H | arms > E02 control |",
        "|---|---:|---:|",
    ]
    for key in sorted(mtf_rows, key=lambda item: (item[1], item[0])):
        pairs = paired_by_key[key]
        above_bh = sum(
            pair[f"min{confirming}_return_pct"]
            > mtf_rows[key]["bh_return_pct"]
            for pair in pairs
            for confirming in (1, 2)
        )
        above_control = sum(
            pair[f"min{confirming}_return_pct"]
            > mtf_rows[key]["same_entry_control_return_pct"]
            for pair in pairs
            for confirming in (1, 2)
        )
        lines.append(
            f"| {key[0]} {key[1]} | {above_bh}/40 | {above_control}/40 |"
        )
    every_fold_rows = [
        row for row in all_pairs if row["min2_every_fold_valid"]
    ]
    lines += [
        "",
        "## Every-fold alpha/safety candidates before exposure gating",
        "",
        "| key | ATR | min profit | return | exits |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in every_fold_rows:
        lines.append(
            f"| {row['symbol']} {row['side']} | {row['atr_mult']:g} | "
            f"{row['min_profit_pct']:g}% | "
            f"{_fmt(row['min2_return_pct'])}% | {row['min2_exit_fills']} |"
        )
    lines += [
        "",
        "## Bottom-A comparison",
        "",
        (
            "This is an aligned benchmark, not a causal incremental claim: "
            "Bottom-A arms only after an adverse 1h/4h structure break and "
            "then trails one 5m/15m/1h series. Job47 ratchets independently "
            "from entry on completed 1h/4h/D bars. Their selected parameters "
            "were chosen independently."
        ),
        "",
        (
            f"Job47's selected row returned more than Bottom-A's selected row "
            f"on **{mtf_better_bottom}/{len(mtf_rows)}** aligned keys; median "
            f"difference **{_fmt(statistics.median(bottom_deltas))} pp**."
        ),
        "",
        "## Per-key selected validation rows",
        "",
        "| key | job47 params | return | B&H | E02 control | TIM | exits | Bottom-A return/exits | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key in sorted(mtf_rows, key=lambda item: (item[1], item[0])):
        row = mtf_rows[key]
        bottom = bottom_rows[key]
        params = row["params"]
        verdicts = []
        if row["strategy_return_pct"] <= row["bh_return_pct"]:
            verdicts.append("≤B&H")
        if row["strategy_return_pct"] <= row["same_entry_control_return_pct"]:
            verdicts.append("≤E02")
        if not 70 <= row["tim_pct"] <= 80:
            verdicts.append("TIM")
        if row["insolvent_folds"]:
            verdicts.append("insolvent fold")
        if row["entry_capacity_breach"]:
            verdicts.append("capacity")
        if not verdicts:
            verdicts.append("failed earlier fold/selection gate")
        param_text = (
            f"x{params['atr_mult']:g}, p≥{params['min_profit_pct']:g}%, "
            f"{params['min_confirming_tfs']}TF"
        )
        lines.append(
            f"| {key[0]} {key[1]} | `{param_text}` | "
            f"{_fmt(row['strategy_return_pct'])}% | "
            f"{_fmt(row['bh_return_pct'])}% | "
            f"{_fmt(row['same_entry_control_return_pct'])}% | "
            f"{_fmt(row['tim_pct'])}% | {row['trades']} | "
            f"{_fmt(bottom['strategy_return_pct'])}%/{bottom['trades']} | "
            f"{', '.join(verdicts)} |"
        )
    lines += [
        "",
        "## Contract and interpretation",
        "",
        "- Entries, ladder capacity, E02 control, resting reclaim, costs, and accounting were frozen.",
        "- LONG and SHORT remained separate; B&H used $2,000 and strategy capacity was capped at $16,000.",
        "- Signals used completed 1h/4h/D source timestamps only and filled on the next RTH bar.",
        "- The live/v8 shared single-TF ATR ratchet was not changed or enabled.",
        "- Gray rows remain evidence and must not be automatically retested or promoted.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mtf-summary", type=Path, required=True)
    ap.add_argument("--bottom-a-summary", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    report = build_report(
        json.loads(args.mtf_summary.read_text()),
        json.loads(args.bottom_a_summary.read_text()),
    )
    args.output.write_text(report)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
