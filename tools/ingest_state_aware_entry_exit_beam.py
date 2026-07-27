#!/usr/bin/env python3
"""Append discovery-selected state-beam final rows to path-fleet as evidence."""
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
from tools import run_entry_exit_beam_campaign as beam  # noqa: E402

STAGE = "VEC_STATE_AWARE_ENTRY_EXIT_BEAM_UNTOUCHED_OOS"


def _job_ids(root: Path) -> dict[str, int]:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute("SELECT id,path_id FROM jobs").fetchall()
    con.close()
    return {str(path): int(job) for job, path in rows}


def _already(
    root: Path, campaign_id: str, symbol: str, side: str, entry_artifact: str
) -> bool:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        "SELECT payload_json FROM results WHERE stage=? AND symbol=? AND side=?",
        (STAGE, symbol, side),
    ).fetchall()
    con.close()
    return any(
        (payload := json.loads(raw)).get("campaign_id") == campaign_id
        and payload.get("entry_artifact") == entry_artifact
        for (raw,) in rows
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    root = args.root.resolve()
    campaign_id = manifest_path.parent.name
    jobs = _job_ids(root)
    out = root / "state_aware_entry_exit_beam_ingest" / campaign_id
    appended = skipped = 0
    summary_rows = []
    for item in manifest["entry_results"]:
        compact = json.loads(Path(item["compact_result"]).read_text())
        winner = compact["frozen_state_exit_beam"][0]
        symbol, side = compact["symbol"], compact["side"]
        if _already(root, campaign_id, symbol, side, compact["entry_artifact"]):
            skipped += 1
            continue
        fold = winner["untouched_final_validation"]["fold_evidence"]
        path_id = beam.EXIT_TO_PATH[winner["exit_family"]]
        status = (
            "VECTOR_SURVIVOR_EXACT_PENDING"
            if winner["all_folds_strict"]
            else "GRAY_REJECTED"
        )
        payload = {
            "job_id": jobs[path_id],
            "symbol": symbol,
            "side": side,
            "stage": STAGE,
            "status": status,
            "strategy_return_pct": float(fold["strategy_return_pct"]),
            "bh_return_pct": float(fold["bh_return_pct"]),
            "same_entry_control_return_pct": float(
                fold["same_entry_e02_return_pct"]
            ),
            "tim_pct": float(fold["weighted_tim_pct"]),
            "trades": int(fold.get("exit_fills", 0)),
            "untouched_oos": True,
            "exact_replay": False,
            "future_htf_count": int(fold.get("future_htf_source_count", 0)),
            "artifact": str(manifest_path.parent),
            "metric_scope": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
            "return_unit": "CAPITAL_RETURN_PCT",
            "return_aggregation": "NONE_SINGLE_FOLD",
            "tim_unit": "PCT",
            "tim_aggregation": "NONE_SINGLE_FOLD",
            "trades_unit": "ACTUAL_TECHNICAL_EXIT_FILLS",
            "entry_family": winner["entry_family"],
            "entry_artifact": winner["entry_artifact"],
            "state_policy": winner["state_policy"],
            "exit_family": winner["exit_family"],
            "params": winner["exit_params"],
            "discovery_fold_evidence": winner["discovery_fold_evidence"],
            "discovery_fold_gate_pass": winner["discovery_fold_gate_pass"],
            "all_folds_strict": winner["all_folds_strict"],
            "bh_capital_usd": 2000.0,
            "strategy_capacity_usd": 16000.0,
            "costs_included": True,
            "promotion_allowed": False,
            "campaign_id": campaign_id,
        }
        path = out / f"{symbol}_{side}_{winner['entry_family']}.json"
        fleet.atomic_json(path, payload)
        fleet.add_result(root, path)
        appended += 1
        summary_rows.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_family": winner["entry_family"],
                "state_policy": winner["state_policy"],
                "exit_family": winner["exit_family"],
                "strategy_return_pct": payload["strategy_return_pct"],
                "bh_return_pct": payload["bh_return_pct"],
                "control_return_pct": payload[
                    "same_entry_control_return_pct"
                ],
                "tim_pct": payload["tim_pct"],
                "status": status,
            }
        )
    summary = {
        "campaign_id": campaign_id,
        "appended": appended,
        "skipped_existing": skipped,
        "rows": summary_rows,
    }
    fleet.atomic_json(out / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

