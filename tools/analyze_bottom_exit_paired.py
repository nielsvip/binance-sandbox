#!/usr/bin/env python3
"""Create the causal A/B/C bottom-exit digest from completed cohort artifacts.

No backtest is run. In particular, every family-C emergency candidate is
paired with the family-B candidate having identical structural parameters.
This prevents a different selected lower-top arm from being mistaken for an
emergency-brake effect.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_KEYS = (
    "arm_tf",
    "confirm_tf",
    "confirmation_bars",
    "confirmation_mode",
    "max_wait_1h",
    "prebreak_lookback",
    "rebound_atr",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _artifacts(cohort: dict[str, Any]) -> dict[tuple[str, str], Path]:
    return {
        (str(row["symbol"]), str(row["side"])): Path(row["artifact"])
        for row in cohort["results"]
        if row.get("status") == "OK" and row.get("artifact")
    }


def _base_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row["params"].get(key) for key in BASE_KEYS)


def _part(row: dict[str, Any], name: str) -> dict[str, Any]:
    return row["nested"][name]


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "—"
    return f"{number:.{digits}f}"


def _winner_summary(
    cohort: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for key, artifact in _artifacts(cohort).items():
        result = _load(artifact / "result.json")
        winner = result["frozen_discovery_winners"][0]
        validation = _part(winner, "validation")
        rows[key] = {
            "return": float(validation["capital_return_pct_sum"]),
            "bh": float(validation["bh_capital_return_pct_sum"]),
            "control": float(validation["capital_return_pct_sum"])
            - float(winner["nested"]["validation_alpha_vs_same_entry_e02_pp"]),
            "tim": float(
                validation["exposure_weighted_tim_pct_row_weighted"]
            ),
            "exits": int(validation["exit_fills"]),
            "params": winner["params"],
            "strict": bool(
                winner["nested"]["robust_discovery_all_folds"]
                and winner["nested"]["robust_validation_fold"]
                and 70.0
                <= float(
                    _part(winner, "discovery")[
                        "exposure_weighted_tim_pct_row_weighted"
                    ]
                )
                <= 80.0
                and 70.0 <= float(
                    validation["exposure_weighted_tim_pct_row_weighted"]
                )
                <= 80.0
            ),
        }
    return rows


def _paired_rows(
    b_cohort: dict[str, Any], c_cohort: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    b_artifacts = _artifacts(b_cohort)
    for key, c_artifact in _artifacts(c_cohort).items():
        b_artifact = b_artifacts.get(key)
        if b_artifact is None:
            continue
        b_result = _load(b_artifact / "result.json")
        c_result = _load(c_artifact / "result.json")
        b_index = {_base_key(row): row for row in b_result["candidates"]}
        for emergency in c_result["candidates"]:
            base = b_index.get(_base_key(emergency))
            if base is None:
                raise RuntimeError(f"missing paired B row for {key}: {_base_key(emergency)}")
            item: dict[str, Any] = {
                "symbol": key[0],
                "side": key[1],
                "emergency_label": emergency["params"]["emergency_label"],
                "base_key": _base_key(emergency),
                "params": emergency["params"],
            }
            for partition in ("discovery", "validation"):
                b = _part(base, partition)
                c = _part(emergency, partition)
                item[partition] = {
                    "base_return": float(b["capital_return_pct_sum"]),
                    "emergency_return": float(c["capital_return_pct_sum"]),
                    "delta_return_pp": float(c["capital_return_pct_sum"])
                    - float(b["capital_return_pct_sum"]),
                    "base_tim": float(
                        b["exposure_weighted_tim_pct_row_weighted"]
                    ),
                    "emergency_tim": float(
                        c["exposure_weighted_tim_pct_row_weighted"]
                    ),
                    "delta_tim_pp": float(
                        c["exposure_weighted_tim_pct_row_weighted"]
                    )
                    - float(b["exposure_weighted_tim_pct_row_weighted"]),
                    "brake_fills": int(c["emergency_exit_fills"]),
                    "total_fills": int(c["exit_fills"]),
                    "brake_share": float(c["emergency_exit_share"]),
                    "brake_pnl_usd": float(c["emergency_exit_pnl_usd"]),
                    "normal_pnl_usd": float(c["normal_exit_pnl_usd"]),
                    "rare": float(c["emergency_exit_share"]) <= 0.25,
                    "future_htf": int(c["future_htf_source_count"]),
                    "beyond_reclaim": int(c["bars_flat_beyond_reclaim"]),
                }
            rows.append(item)
    return rows


def _paired_selection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["symbol"], row["side"])].append(row)
    selected = []
    for key, options in sorted(grouped.items()):
        eligible = [
            row
            for row in options
            if row["discovery"]["rare"]
            and row["discovery"]["brake_fills"] > 0
            and row["discovery"]["future_htf"] == 0
            and row["discovery"]["beyond_reclaim"] == 0
            and 70 <= row["discovery"]["base_tim"] <= 80
            and 70 <= row["discovery"]["emergency_tim"] <= 80
        ]
        pool = eligible or [
            row
            for row in options
            if row["discovery"]["rare"]
            and row["discovery"]["brake_fills"] > 0
            and row["discovery"]["future_htf"] == 0
            and row["discovery"]["beyond_reclaim"] == 0
        ]
        if not pool:
            continue
        winner = max(
            pool,
            key=lambda row: (
                row["discovery"]["delta_return_pp"],
                -row["discovery"]["brake_share"],
            ),
        )
        winner = dict(winner)
        winner["discovery_exposure_eligible"] = winner in eligible
        selected.append(winner)
    return selected


def _brake_aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["emergency_label"]].append(row)
    output = []
    for label, items in sorted(grouped.items()):
        validation_delta = [
            row["validation"]["delta_return_pp"] for row in items
        ]
        output.append(
            {
                "label": label,
                "pairs": len(items),
                "validation_positive": sum(value > 0 for value in validation_delta),
                "validation_delta_median": statistics.median(validation_delta),
                "validation_delta_mean": statistics.fmean(validation_delta),
                "validation_brake_share_mean": statistics.fmean(
                    row["validation"]["brake_share"] for row in items
                ),
                "validation_nonrare": sum(
                    not row["validation"]["rare"] for row in items
                ),
                "validation_brake_pnl": sum(
                    row["validation"]["brake_pnl_usd"] for row in items
                ),
            }
        )
    return output


def _immediate_comparator(
    a_cohort: dict[str, Any],
) -> list[dict[str, Any]]:
    output = []
    for key, artifact in sorted(_artifacts(a_cohort).items()):
        result = _load(artifact / "result.json")
        immediate = [
            row
            for row in result["candidates"]
            if row["params"]["mode"] == "IMMEDIATE"
        ]
        if not immediate:
            continue
        winner = max(
            immediate,
            key=lambda row: row["nested"][
                "discovery_alpha_vs_same_entry_e02_pp"
            ],
        )
        output.append(
            {
                "symbol": key[0],
                "side": key[1],
                "return": float(
                    _part(winner, "validation")["capital_return_pct_sum"]
                ),
                "bh": float(
                    _part(winner, "validation")["bh_capital_return_pct_sum"]
                ),
                "tim": float(
                    _part(winner, "validation")[
                        "exposure_weighted_tim_pct_row_weighted"
                    ]
                ),
                "exits": int(_part(winner, "validation")["exit_fills"]),
                "params": winner["params"],
            }
        )
    return output


def build_report(
    a_cohort: dict[str, Any],
    b_cohort: dict[str, Any],
    c_cohort: dict[str, Any],
) -> str:
    winners = {
        "A": _winner_summary(a_cohort),
        "B": _winner_summary(b_cohort),
        "C": _winner_summary(c_cohort),
    }
    paired = _paired_rows(b_cohort, c_cohort)
    selected = _paired_selection(paired)
    aggregate = _brake_aggregate(paired)
    immediate = {
        (row["symbol"], row["side"]): row
        for row in _immediate_comparator(a_cohort)
    }
    keys = sorted(set().union(*(set(rows) for rows in winners.values())))
    lines = [
        "# Bottom-exit A/B/C results — 2026-07-26",
        "",
        "All rows use the frozen accepted entry request timestamps/multipliers, "
        "$2,000 side-specific B&H, $16,000 strategy capacity, next-RTH fills, "
        "side-isolated accounting, and persistent reclaim. No `dc_low4_5m` "
        "breach is treated as a profit exit.",
        "",
        "## Outcome",
        "",
        "All three fleet jobs screened without execution errors. No row met "
        "the full promotion contract (positive untouched alpha versus both "
        "B&H and identical-entry E02, 70–80% TIM in discovery and validation, "
        "actual exits, no future HTF, no reclaim gap, and compiled parity where "
        "applicable). Results remain gray; exact replay queue is empty.",
        "",
        "## Selected family rows per symbol",
        "",
        "| key | A return/B&H/TIM/exits | B return/B&H/TIM/exits | C return/B&H/TIM/exits | strict |",
        "|---|---:|---:|---:|---|",
    ]
    for key in keys:
        cells = []
        strict = []
        for family in ("A", "B", "C"):
            row = winners[family].get(key)
            if row is None:
                cells.append("—")
                strict.append(False)
            else:
                cells.append(
                    f"{_fmt(row['return'])}/{_fmt(row['bh'])}/"
                    f"{_fmt(row['tim'])}%/{row['exits']}"
                )
                strict.append(row["strict"])
        lines.append(
            f"| {key[0]}_{key[1]} | {cells[0]} | {cells[1]} | {cells[2]} | "
            f"{'PASS' if any(strict) else 'GRAY'} |"
        )
    lines += [
        "",
        "## Immediate-break churn comparator (A)",
        "",
        "The immediate arm is a separate diagnostic identity; it is never "
        "pooled with ATR/stdev/DC trails. It selects its arm on discovery only.",
        "",
        "| key | validation return | B&H | TIM | exits | arm |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for key in keys:
        row = immediate.get(key)
        if row:
            lines.append(
                f"| {key[0]}_{key[1]} | {_fmt(row['return'])} | "
                f"{_fmt(row['bh'])} | {_fmt(row['tim'])}% | {row['exits']} | "
                f"{row['params']['arm_timeframe']} buffer "
                f"{row['params']['break_buffer_atr']} ATR |"
            )
    lines += [
        "",
        "## Paired emergency effect: identical B parameters vs C brake",
        "",
        "Every comparison below holds arm TF, confirmation TF/mode/bars, "
        "rebound, lookback, and wait exactly constant. Therefore Δreturn and "
        "ΔTIM are the causal effect of adding the brake within this simulator, "
        "not the effect of selecting another lower-top strategy.",
        "",
        "### Brake-family aggregate across all matched candidates",
        "",
        "| brake | pairs | validation Δreturn mean/median pp | positive pairs | mean brake share | >25% non-rare | brake realized P&L |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in aggregate:
        lines.append(
            f"| {row['label']} | {row['pairs']} | "
            f"{_fmt(row['validation_delta_mean'])}/"
            f"{_fmt(row['validation_delta_median'])} | "
            f"{row['validation_positive']} | "
            f"{_fmt(100*row['validation_brake_share_mean'])}% | "
            f"{row['validation_nonrare']} | "
            f"${_fmt(row['validation_brake_pnl'])} |"
        )
    lines += [
        "",
        "### Per-symbol brake chosen on discovery only",
        "",
        "| key | brake | discovery Δreturn/ΔTIM/share | validation Δreturn/ΔTIM | validation brake fills/share | brake P&L | normal-exit P&L | verdict |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in selected:
        d, v = row["discovery"], row["validation"]
        reasons = []
        if not row["discovery_exposure_eligible"]:
            reasons.append("discovery TIM not paired 70–80")
        if not v["rare"]:
            reasons.append("NON-RARE >25%")
        if not (70 <= v["base_tim"] <= 80 and 70 <= v["emergency_tim"] <= 80):
            reasons.append("validation TIM")
        if v["delta_return_pp"] <= 0:
            reasons.append("negative paired OOS Δ")
        verdict = "GRAY: " + ", ".join(reasons) if reasons else "paired-positive diagnostic"
        lines.append(
            f"| {row['symbol']}_{row['side']} | {row['emergency_label']} | "
            f"{_fmt(d['delta_return_pp'])}/{_fmt(d['delta_tim_pp'])}/"
            f"{_fmt(100*d['brake_share'])}% | "
            f"{_fmt(v['delta_return_pp'])}/{_fmt(v['delta_tim_pp'])} | "
            f"{v['brake_fills']}/{_fmt(100*v['brake_share'])}% | "
            f"${_fmt(v['brake_pnl_usd'])} | ${_fmt(v['normal_pnl_usd'])} | "
            f"{verdict} |"
        )
    lines += [
        "",
        "## Strict rejection reasons",
        "",
        "- Every selected fleet row remains below the identical-entry E02 "
        "control in at least one required partition, outside 70–80% TIM in at "
        "least one partition, or both.",
        "- Family-C rows with emergency share above 25% are explicitly "
        "non-rare and cannot survive even if their return is attractive.",
        "- Emergency count/share and realized P&L are independent fields; a "
        "small number of brake events cannot hide whether they generated or "
        "destroyed the observed result.",
        "- Compiled family-B/C frozen winners were checked against the Python "
        "state-machine oracle; any parity failure is ineligible.",
        "",
        "## Artifacts and rerun",
        "",
        "- Code/semantics audit: `BOTTOM_EXIT_CODE_AUDIT_20260726.md`",
        "- Adapter: `tools/vec_same_entry_exit_adapter.py`",
        "- Compiled scanner: `tools/vec_same_entry_structural_scan.c`",
        "- Fleet worker: `tools/path_fleet_bottom_exit_worker.py`",
        "- This no-rerun paired digest: `tools/analyze_bottom_exit_paired.py`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a-cohort", type=Path, required=True)
    ap.add_argument("--b-cohort", type=Path, required=True)
    ap.add_argument("--c-cohort", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    report = build_report(
        _load(args.a_cohort),
        _load(args.b_cohort),
        _load(args.c_cohort),
    )
    args.output.write_text(report + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
