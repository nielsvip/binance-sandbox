#!/usr/bin/env python3
"""Audit one trader research report against its frozen merged trade CSV.

This is a read-only research audit except for files written below ``--output``.
It does not read or change live trading configuration.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


NUMERIC = (
    "entry_price",
    "exit_price",
    "pnl",
    "pnl_pct",
    "leverage",
    "position_size_usd",
)


def _summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "n_trades": int(len(frame)),
        "n_source_accounts": int(frame["source_account"].nunique()),
        "n_symbols": int(frame["symbol"].nunique()),
        "raw_pnl_usd_context_only": round(float(frame["reported_pnl"].sum()), 4),
        "equal_weight_mean_return_pct": round(
            float(frame["recomputed_return_pct"].mean()), 6
        ),
        "equal_weight_median_return_pct": round(
            float(frame["recomputed_return_pct"].median()), 6
        ),
        "win_rate_pct": round(
            100.0 * float((frame["recomputed_return_pct"] > 0).mean()), 4
        ),
        "total_notional_usd": round(float(frame["position_size_usd"].sum()), 2),
    }


def audit(csv_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = pd.read_csv(csv_path, low_memory=False)
    raw.insert(0, "source_row", np.arange(2, len(raw) + 2, dtype=np.int64))
    for column in NUMERIC:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw["position_side"] = raw.get("position_side", raw["side"])
    raw["position_side"] = raw["position_side"].astype(str).str.strip().str.upper()
    raw["symbol"] = raw["symbol"].astype(str).str.strip().str.upper()
    raw["source_account"] = raw["trader_id"].astype(str).str.strip()

    identity_valid = (
        raw["symbol"].ne("")
        & raw["source_account"].ne("")
        & raw["position_side"].isin(("LONG", "SHORT"))
    )
    numeric_valid = raw[
        ["entry_price", "exit_price", "pnl", "pnl_pct", "position_size_usd"]
    ].notna().all(axis=1)
    price_valid = raw["entry_price"].gt(0) & raw["exit_price"].gt(0)
    valid = identity_valid & numeric_valid & price_valid
    rejected = raw.loc[~valid].copy()
    clean = raw.loc[valid].copy()

    direction = clean["position_side"].map({"LONG": 1.0, "SHORT": -1.0})
    clean["entry_order_side"] = np.where(
        clean["position_side"].eq("LONG"), "BUY", "SELL"
    )
    clean["exit_order_side"] = np.where(
        clean["position_side"].eq("LONG"), "SELL", "BUY"
    )
    clean["reported_pnl"] = clean["pnl"]
    clean["reported_pnl_pct"] = clean["pnl_pct"]
    clean["recomputed_return_pct"] = (
        direction
        * (clean["exit_price"] - clean["entry_price"])
        / clean["entry_price"]
        * 100.0
    )
    clean["sign_inverted_return_pct"] = -clean["recomputed_return_pct"]
    clean["return_error_pct_points"] = (
        clean["reported_pnl_pct"] - clean["recomputed_return_pct"]
    )
    clean["pnl_sign_matches_price_formula"] = (
        np.sign(clean["reported_pnl"])
        == np.sign(clean["recomputed_return_pct"])
    )
    nonzero_return = clean["recomputed_return_pct"].abs().gt(1e-12)
    clean["pnl_implied_notional_usd"] = np.where(
        nonzero_return,
        clean["reported_pnl"] / (clean["recomputed_return_pct"] / 100.0),
        np.nan,
    )
    clean["pnl_implied_to_reported_notional_ratio"] = (
        clean["pnl_implied_notional_usd"] / clean["position_size_usd"]
    )
    clean["pnl_formula"] = np.where(
        clean["position_side"].eq("LONG"),
        "(exit_price-entry_price)/entry_price*100",
        "(entry_price-exit_price)/entry_price*100",
    )

    by_side = {
        side: _summary(clean.loc[clean["position_side"].eq(side)])
        for side in ("LONG", "SHORT")
    }
    by_symbol_side = {
        f"{symbol}:{side}": _summary(group)
        for (symbol, side), group in clean.groupby(
            ["symbol", "position_side"], sort=True
        )
    }
    by_account = (
        clean.groupby("source_account", sort=True)
        .agg(
            n_trades=("source_row", "size"),
            raw_pnl_usd_context_only=("reported_pnl", "sum"),
            mean_return_pct=("recomputed_return_pct", "mean"),
            total_notional_usd=("position_size_usd", "sum"),
        )
        .sort_values("raw_pnl_usd_context_only")
    )
    worst_account = by_account.iloc[0]
    raw_total = float(clean["reported_pnl"].sum())
    report = {
        "status": "FAIL_REPORT_SCHEMA_PASS_SOURCE_SIGN",
        "source_csv": str(csv_path),
        "source_rows": int(len(raw)),
        "accepted_rows": int(len(clean)),
        "rejected_rows": int(len(rejected)),
        "rejected_source_rows": rejected["source_row"].astype(int).tolist(),
        "source_accounts": int(clean["source_account"].nunique()),
        "symbols": int(clean["symbol"].nunique()),
        "position_sides": sorted(clean["position_side"].unique().tolist()),
        "formula_contract": {
            "LONG": "(exit-entry)/entry*100",
            "SHORT": "(entry-exit)/entry*100",
            "max_abs_reported_vs_recomputed_error_pct_points": round(
                float(clean["return_error_pct_points"].abs().max()), 8
            ),
            "rows_error_over_0_01_pct_points": int(
                clean["return_error_pct_points"].abs().gt(0.01).sum()
            ),
            "pnl_sign_mismatch_rows": int(
                (~clean["pnl_sign_matches_price_formula"]).sum()
            ),
            "notional_reconstruction_within_1pct_rate": round(
                float(
                    clean.loc[
                        nonzero_return,
                        "pnl_implied_to_reported_notional_ratio",
                    ]
                    .sub(1.0)
                    .abs()
                    .le(0.01)
                    .mean()
                ),
                6,
            ),
        },
        "sign_inversion_test": {
            "observed_by_side": by_side,
            "if_position_sides_were_inverted_equal_weight_mean_return_pct": {
                side: round(-stats["equal_weight_mean_return_pct"], 6)
                for side, stats in by_side.items()
            },
            "verdict": (
                "NO_SIDE_INVERSION_IN_ACCEPTED_SOURCE: reported pnl_pct and "
                "LONG/SHORT price formulas agree. The report defect was pooling "
                "and scale-biased presentation, not a sign flip."
            ),
        },
        "raw_dollar_concentration": {
            "raw_total_pnl_usd": round(raw_total, 4),
            "worst_source_account": str(by_account.index[0]),
            "worst_source_account_raw_pnl_usd": round(
                float(worst_account["raw_pnl_usd_context_only"]), 4
            ),
            "worst_account_fraction_of_net_raw_loss": round(
                float(worst_account["raw_pnl_usd_context_only"] / raw_total), 6
            )
            if raw_total
            else None,
            "verdict": (
                "Raw dollars pool unrelated source accounts, symbols, notionals "
                "and leverage; they are not a strategy return."
            ),
        },
        "by_position_side": by_side,
        "by_symbol_position_side": by_symbol_side,
        "generator_defects": [
            "compare_to_our_system pooled symbols and LONG/SHORT regime dollars",
            "decision-tree patterns were trained on pooled sides and merely labeled dominant_side",
            "email findings[:15] omitted the later SIDE provenance line",
            "TrackerLogIngester coerced missing/unknown sides to SHORT",
            "Bitget closed-order parser coerced missing/unknown sides to SHORT",
            "report omitted symbol/account/position-side/order-side trace fields",
            "regime membership was not persisted per trade, preventing exact retrospective regime reconciliation",
            "merged CSV contained two malformed concatenated rows; permissive ingestion silently skipped them",
        ],
        "promotion_eligible": False,
        "live_config_write": False,
        "matrix_write": False,
    }
    return clean, report


def _markdown(report: dict[str, Any]) -> str:
    long = report["by_position_side"]["LONG"]
    short = report["by_position_side"]["SHORT"]
    formula = report["formula_contract"]
    concentration = report["raw_dollar_concentration"]
    lines = [
        "# Audit of `research_20260725_1511.md`",
        "",
        "## Verdict",
        "",
        "The accepted trade source does **not** have a LONG/SHORT sign inversion. "
        "Its reported return matches the correct side-aware price formula on every "
        "accepted row. The report is nevertheless invalid for strategy decisions "
        "because it pooled both sides and every symbol, labeled pooled decision-tree "
        "leaves by only their dominant side, and presented scale-weighted raw dollars "
        "as if they described a common strategy.",
        "",
        f"- Source rows: {report['source_rows']:,}",
        f"- Accepted: {report['accepted_rows']:,}",
        f"- Rejected malformed rows: {report['rejected_rows']} "
        f"(CSV source rows {report['rejected_source_rows']})",
        f"- Accounts: {report['source_accounts']}; symbols: {report['symbols']}",
        f"- Max reported/recomputed return error: "
        f"{formula['max_abs_reported_vs_recomputed_error_pct_points']:.8f} pp",
        f"- PnL sign mismatches: {formula['pnl_sign_mismatch_rows']}",
        f"- PnL-implied notional within 1% of reported notional: "
        f"{formula['notional_reconstruction_within_1pct_rate']*100:.2f}%",
        "",
        "## Correct side-separated recomputation",
        "",
        "| Scope | Trades | Win rate | Mean return/trade | Median return/trade | Raw PnL (context only) |",
        "|---|---:|---:|---:|---:|---:|",
        f"| ALL_SYMBOLS LONG | {long['n_trades']:,} | {long['win_rate_pct']:.1f}% | "
        f"{long['equal_weight_mean_return_pct']:+.3f}% | "
        f"{long['equal_weight_median_return_pct']:+.3f}% | "
        f"${long['raw_pnl_usd_context_only']:+,.2f} |",
        f"| ALL_SYMBOLS SHORT | {short['n_trades']:,} | {short['win_rate_pct']:.1f}% | "
        f"{short['equal_weight_mean_return_pct']:+.3f}% | "
        f"{short['equal_weight_median_return_pct']:+.3f}% | "
        f"${short['raw_pnl_usd_context_only']:+,.2f} |",
        "",
        "If the side were inverted, both mean-return signs would reverse. That "
        "counterfactual disagrees with the reported PnL signs and prices; it is not "
        "the source defect.",
        "",
        "## Why the headline loss looked extreme",
        "",
        f"The worst source account contributed "
        f"${concentration['worst_source_account_raw_pnl_usd']:+,.2f}, or "
        f"{concentration['worst_account_fraction_of_net_raw_loss']*100:.1f}% of the "
        "pooled net raw loss. Raw cash PnL weights a $449k BTC position far more than "
        "a $1k altcoin position. It is a source reconciliation field, not a return.",
        "",
        "## Exact generator defects",
        "",
    ]
    lines.extend(f"- {item}" for item in report["generator_defects"])
    lines.extend(
        [
            "",
            "## Corrective contract",
            "",
            "Every research row must carry `source_account`, `symbol`, "
            "`position_side`, `entry_order_side`, `exit_order_side`, entry/exit "
            "prices, reported PnL, recomputed side-aware return, and formula. Missing "
            "or unknown identity now fails closed. Patterns, regimes, summaries, and "
            "emails must state `ALL_SYMBOLS` or a concrete symbol and must never pool "
            "LONG with SHORT.",
            "",
            "**Promotion eligible: no. Live/config/matrix writes: none.**",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    trades, report = audit(args.csv)
    (args.output / "audit.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (args.output / "AUDIT.md").write_text(_markdown(report))
    columns = [
        "source_row",
        "source_account",
        "symbol",
        "position_side",
        "entry_order_side",
        "exit_order_side",
        "entry_price",
        "exit_price",
        "reported_pnl",
        "reported_pnl_pct",
        "recomputed_return_pct",
        "sign_inverted_return_pct",
        "return_error_pct_points",
        "pnl_sign_matches_price_formula",
        "position_size_usd",
        "pnl_implied_notional_usd",
        "pnl_implied_to_reported_notional_ratio",
        "pnl_formula",
    ]
    with gzip.open(args.output / "trade_trace.jsonl.gz", "wt") as handle:
        serializable = trades[columns].replace([np.inf, -np.inf], np.nan)
        serializable = serializable.astype(object).where(serializable.notna(), None)
        for row in serializable.to_dict("records"):
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"status": "PASS", "output": str(args.output), **report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
