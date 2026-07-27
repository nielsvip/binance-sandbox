#!/usr/bin/env python3
"""Idempotently append SHORT guard-decomposition rows to path-fleet."""
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


STAGE = "VEC_SHORT_GUARD_DECOMPOSITION_UNTOUCHED_OOS"
PATH_ID = "ENTRY_DISASTER_GUARD_ENABLED"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    result = args.result.resolve()
    root = args.root.resolve()
    campaign_id = result.parent.name
    data = json.loads(result.read_text())
    con = sqlite3.connect(root / "queue.db")
    job_id = int(con.execute(
        "SELECT id FROM jobs WHERE path_id=?", (PATH_ID,)
    ).fetchone()[0])
    existing = set()
    for raw, in con.execute(
        "SELECT payload_json FROM results WHERE stage=?", (STAGE,)
    ):
        p = json.loads(raw)
        if p.get("campaign_id") == campaign_id:
            existing.add((p.get("symbol"), p.get("book"), p.get("guard_profile")))
    con.close()
    out = root / "short_guard_decomposition_ingest" / campaign_id
    appended = skipped = 0
    for item in data["results"]:
        baseline = next(
            r for r in item["profiles"]
            if r["profile"]["label"] == "G00_ALL_VETOES"
        )["untouched_final"]["strategy_return_pct"]
        for row in item["profiles"]:
            profile = row["profile"]["label"]
            key = (item["symbol"], item["book"], profile)
            if key in existing:
                skipped += 1
                continue
            final = row["untouched_final"]
            payload = {
                "job_id": job_id,
                "symbol": item["symbol"],
                "side": "SHORT",
                "stage": STAGE,
                "status": row["status"],
                "strategy_return_pct": final["strategy_return_pct"],
                "bh_return_pct": final["short_bh_return_pct"],
                "same_entry_control_return_pct": baseline,
                "tim_pct": final["time_in_market_pct"],
                "trades": final["technical_exits"],
                "untouched_oos": True,
                "exact_replay": False,
                "future_htf_count": row["source_future_count"],
                "artifact": str(result.parent),
                "campaign_id": campaign_id,
                "book": item["book"],
                "guard_profile": profile,
                "bypassed_conflicts": row["profile"]["bypass"],
                "selected_candidate": row["selected_candidate"],
                "discovery_gate_pass": row["discovery_gate_pass"],
                "all_fold_survivor": row["all_fold_survivor"],
                "guard_audit": row["guard_audit"],
                "cash_benchmark_pct": 0.0,
                "long_bh_opportunity_return_pct": final[
                    "long_bh_opportunity_return_pct"
                ],
                "realized_cash_return_pct": final[
                    "realized_cash_return_pct"
                ],
                "open_mtm_return_pct": final["open_mtm_return_pct"],
                "max_drawdown_account_pct": final[
                    "max_drawdown_account_pct"
                ],
                "correction_capture_pct": final["correction_capture_pct"],
                "exit_reason_counts": final["exit_reason_counts"],
                "emergency_invariants": data["manifest"][
                    "emergency_invariants"
                ],
                "control_contract": (
                    "SAME_SYMBOL G00 ALL-VETO PROFILE; not identical entry; "
                    "promotion prohibited"
                ),
                "promotion_allowed": False,
            }
            path = out / f"{item['symbol']}_SHORT_{item['book']}_{profile}.json"
            fleet.atomic_json(path, payload)
            fleet.add_result(root, path)
            appended += 1
    summary = {
        "campaign_id": campaign_id,
        "stage": STAGE,
        "appended": appended,
        "skipped_existing": skipped,
    }
    fleet.atomic_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
