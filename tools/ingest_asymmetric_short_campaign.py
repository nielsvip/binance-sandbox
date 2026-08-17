#!/usr/bin/env python3
"""Idempotently append asymmetric-SHORT final rows to the path fleet."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402


STAGE = "VEC_ASYMMETRIC_SHORT_UNTOUCHED_OOS"
CAMPAIGN_ID = "asymmetric_short_20260727T020146Z"
PATH_BY_BOOK = {"CORRECTION": "ENTRY_DELTA_MTF", "BEAR": "ENTRY_WT_DC"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    result = args.result.resolve()
    root = args.root.resolve()
    data = json.loads(result.read_text())
    con = sqlite3.connect(root / "queue.db")
    jobs = dict(con.execute("SELECT path_id,id FROM jobs"))
    existing = set()
    for symbol, side, raw in con.execute(
        "SELECT symbol,side,payload_json FROM results WHERE stage=?", (STAGE,)
    ):
        payload = json.loads(raw)
        if payload.get("campaign_id") == CAMPAIGN_ID:
            existing.add((symbol, side, payload.get("book")))
    con.close()
    out = root / "asymmetric_short_ingest" / CAMPAIGN_ID
    appended = skipped = 0
    for row in data["results"]:
        key = (row["symbol"], row["side"], row["book"])
        if key in existing:
            skipped += 1
            continue
        path_id = PATH_BY_BOOK[row["book"]]
        final = row["untouched_final"]
        # There is no identical-entry E02 control for this new compound
        # hypothesis.  Pin control=strategy so alpha_vs_control is zero and the
        # gray row can never be mistaken for promotable evidence.
        payload = {
            "job_id": jobs[path_id],
            "symbol": row["symbol"],
            "side": "SHORT",
            "stage": STAGE,
            "status": "GRAY_REJECTED",
            "strategy_return_pct": final["strategy_return_pct"],
            "bh_return_pct": final["short_bh_return_pct"],
            "same_entry_control_return_pct": final["strategy_return_pct"],
            "tim_pct": final["time_in_market_pct"],
            "trades": final["technical_exits"],
            "untouched_oos": True,
            "exact_replay": False,
            "future_htf_count": row["source_future_count"],
            "artifact": str(result.parent),
            "campaign_id": CAMPAIGN_ID,
            "book": row["book"],
            "entry_family": path_id,
            "selected_label": row["selected_label"],
            "selected_params": row["selected_on_discovery_only"],
            "discovery_gate_pass": row["discovery_gate_pass"],
            "exact_replay_eligible": False,
            "benchmark_contract": (
                "fixed-notional side B&H; cash floor; ratio undefined when "
                "short B&H<=0"
            ),
            "control_contract": "NO_IDENTICAL_ENTRY_CONTROL_ALPHA_PINNED_ZERO",
            "long_bh_opportunity_return_pct": final[
                "long_bh_opportunity_return_pct"
            ],
            "cash_benchmark_pct": 0.0,
            "max_drawdown_account_pct": final["max_drawdown_account_pct"],
            "correction_capture_pct": final["correction_capture_pct"],
            "promotion_allowed": False,
        }
        path = out / f"{row['symbol']}_SHORT_{row['book']}.json"
        fleet.atomic_json(path, payload)
        fleet.add_result(root, path)
        appended += 1
    summary = {
        "campaign_id": CAMPAIGN_ID,
        "stage": STAGE,
        "appended": appended,
        "skipped_existing": skipped,
    }
    fleet.atomic_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
