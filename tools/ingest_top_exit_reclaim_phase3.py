#!/usr/bin/env python3
"""Append phase-3 discovery-frozen rows to the path-fleet evidence ledger."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402


STAGE = "VEC_TOP_EXIT_RECLAIM_PHASE3_UNTOUCHED_OOS"
PATH_MAP = {
    "EXIT_BREAK_RETEST_LOWER_TOP": "EXIT_STRUCTURAL_WT_LOWER_TOP",
    "EXIT_CONFIRMED_STRUCTURE_RETEST": "EXIT_STRUCTURAL_WT_LOWER_TOP",
    "EXIT_FAILED_HIGHER_HIGH": "EXIT_TECH_BREAKDOWN_ENABLED",
    "EXIT_E05_DIVERGENCE_RETEST": "EXIT_E05_DIVERGENCE_RETEST",
    "EXIT_E06_REGRESSION_RETEST": "EXIT_E06_REGRESSION_RETEST",
    "EXIT_MTF_VOLATILITY_EXHAUSTION": "EXIT_MTF_ATR_TRAIL",
    "EXIT_WT_DC_TOP_ROLL": "EXIT_WT_DC_EXIT_ENABLED",
    "EXIT_TOP_LOGICAL_COMBINATION": "EXIT_STRUCTURAL_WT_LOWER_TOP",
}


def _jobs(root: Path) -> dict[str, int]:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute("SELECT id,path_id FROM jobs").fetchall()
    con.close()
    return {str(path): int(job) for job, path in rows}


def _seen(root: Path, campaign_artifact: str, symbol: str) -> bool:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        "SELECT payload_json FROM results WHERE stage=? AND symbol=? AND side='LONG'",
        (STAGE, symbol),
    ).fetchall()
    con.close()
    return any(
        json.loads(raw).get("campaign_artifact") == campaign_artifact
        for (raw,) in rows
    )


def _payload(
    result: dict[str, Any], campaign_artifact: str, jobs: dict[str, int]
) -> dict[str, Any]:
    winner = result["frozen_discovery_winner"]
    final = winner["fold_evidence"][-1]
    path_id = PATH_MAP[winner["family"]]
    return {
        "job_id": jobs[path_id],
        "symbol": result["symbol"],
        "side": "LONG",
        "stage": STAGE,
        "status": (
            "VECTOR_SURVIVOR_EXACT_PENDING"
            if winner["all_fold_strict"]
            else "GRAY_REJECTED"
        ),
        "strategy_return_pct": final["strategy_return_pct"],
        "bh_return_pct": final["bh_return_pct"],
        "same_entry_control_return_pct": final[
            "same_entry_e02_return_pct"
        ],
        "tim_pct": final["weighted_tim_pct"],
        "trades": final["actual_exit_fills"],
        "untouched_oos": True,
        "exact_replay": False,
        "future_htf_count": final["future_htf_source_count"],
        "artifact": result["source_artifact"],
        "campaign_artifact": campaign_artifact,
        "campaign": result["campaign"],
        "path_id": path_id,
        "research_family": winner["family"],
        "research_label": winner["label"],
        "params": winner["params"],
        "components": winner["components"],
        "all_fold_evidence": winner["fold_evidence"],
        "all_fold_gates": winner["fold_gate_pass"],
        "discovery_strict": winner["discovery_strict"],
        "all_fold_strict": winner["all_fold_strict"],
        "entry_schedule_unchanged": winner["entry_schedule_unchanged"],
        "availability_clock": result["availability_clock"],
        "fill_timing": result["fill_timing"],
        "capital_contract": result["capital_contract"],
        "metric_scope": "FINAL_CHRONOLOGICAL_FOLD",
        "return_unit": "CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT",
        "tim_unit": "WEIGHTED_CAPACITY_PCT",
        "trades_unit": "ACTUAL_TECHNICAL_EXIT_FILLS",
        "costs_included": True,
        "mandatory_reclaim": True,
        "dc_low4_profit_exit_used": False,
        "matrix_written": False,
        "promotion_allowed": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-artifact", type=Path, required=True)
    ap.add_argument("--fleet-root", type=Path, required=True)
    args = ap.parse_args()
    campaign = args.campaign_artifact.resolve()
    root = args.fleet_root.resolve()
    summary = json.loads((campaign / "summary.json").read_text())
    jobs = _jobs(root)
    out = root / "top_exit_reclaim_phase3_ingest" / campaign.name
    appended = skipped = 0
    receipts = []
    for item in summary["results"]:
        result_path = campaign / f"{item['symbol']}_LONG" / "result.json"
        result = json.loads(result_path.read_text())
        if _seen(root, str(campaign), result["symbol"]):
            skipped += 1
            continue
        payload = _payload(result, str(campaign), jobs)
        path = out / f"{result['symbol']}_LONG.json"
        fleet.atomic_json(path, payload)
        fleet.add_result(root, path)
        appended += 1
        receipts.append(
            {
                "symbol": result["symbol"],
                "path_id": payload["path_id"],
                "status": payload["status"],
                "strategy_return_pct": payload["strategy_return_pct"],
                "bh_return_pct": payload["bh_return_pct"],
                "same_entry_control_return_pct": payload[
                    "same_entry_control_return_pct"
                ],
                "tim_pct": payload["tim_pct"],
            }
        )
    receipt = {
        "campaign_artifact": str(campaign),
        "appended": appended,
        "skipped_existing": skipped,
        "rows": receipts,
    }
    fleet.atomic_json(out / "summary.json", receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
