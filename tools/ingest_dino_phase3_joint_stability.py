#!/usr/bin/env python3
"""Append one discovery-only DINO joint-stability row to path-fleet evidence."""
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


STAGE = "VEC_DINO_PHASE3_JOINT_STABILITY_DISCOVERY_ONLY"
PATH_ID = "ENTRY_STOCH_HHHL"


def _job_id(root: Path) -> int:
    con = sqlite3.connect(root / "queue.db")
    row = con.execute(
        "SELECT id FROM jobs WHERE path_id=?", (PATH_ID,)
    ).fetchone()
    con.close()
    if row is None:
        raise RuntimeError(f"path-fleet job missing: {PATH_ID}")
    return int(row[0])


def _seen(root: Path, artifact: str) -> bool:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        "SELECT payload_json FROM results WHERE stage=? AND symbol='DINO' "
        "AND side='LONG'",
        (STAGE,),
    ).fetchall()
    con.close()
    return any(json.loads(raw).get("campaign_artifact") == artifact for (raw,) in rows)


def payload(result: dict[str, Any], artifact: str, job_id: int) -> dict[str, Any]:
    frozen = result["frozen_discovery_winner"]
    folds = frozen["discovery_evidence"]
    if len(folds) != 2 or result["final_fold_evaluated"]:
        raise ValueError("expected a two-fold discovery-only rejected result")
    strategy = sum(float(row["strategy_return_pct"]) for row in folds)
    bh = sum(float(row["bh_return_pct"]) for row in folds)
    control = sum(float(row["same_entry_e02_return_pct"]) for row in folds)
    return {
        "job_id": job_id,
        "symbol": "DINO",
        "side": "LONG",
        "stage": STAGE,
        "status": "GRAY_REJECTED",
        "strategy_return_pct": strategy,
        "bh_return_pct": bh,
        "same_entry_control_return_pct": control,
        "tim_pct": sum(float(row["weighted_tim_pct"]) for row in folds)
        / len(folds),
        "trades": sum(int(row["actual_exit_fills"]) for row in folds),
        "untouched_oos": False,
        "exact_replay": False,
        "future_htf_count": sum(
            int(row["future_htf_source_count"]) for row in folds
        ),
        "artifact": result["source_artifact"],
        "campaign_artifact": artifact,
        "campaign": result["campaign"],
        "path_id": PATH_ID,
        "fixed_exit_path": "EXIT_STRUCTURAL_WT_LOWER_TOP",
        "fixed_exit_label": result["frozen_exit_label"],
        "profile": frozen["profile"],
        "discovery_fold_evidence": folds,
        "discovery_fold_gates": frozen["discovery_fold_gate_pass"],
        "discovery_strict": frozen["discovery_strict"],
        "final_fold_evaluated": False,
        "all_fold_strict": False,
        "metric_scope": "SUM_OF_TWO_DISCOVERY_VALIDATION_FOLDS",
        "return_unit": "CAPITAL_RETURN_PCT_ON_FIXED_2000_UNIT",
        "tim_unit": "UNWEIGHTED_MEAN_OF_DISCOVERY_FOLD_WEIGHTED_CAPACITY_PCT",
        "trades_unit": "SUM_OF_DISCOVERY_FOLD_ACTUAL_TECHNICAL_EXIT_FILLS",
        "availability_clock": result["availability_clock"],
        "fill_timing": result["fill_timing"],
        "capital_contract": result["capital_contract"],
        "costs_included": True,
        "mandatory_reclaim": True,
        "matrix_written": False,
        "promotion_allowed": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--fleet-root", type=Path, required=True)
    args = ap.parse_args()
    result_path = args.result.resolve()
    root = args.fleet_root.resolve()
    artifact = str(result_path.parent)
    if _seen(root, artifact):
        receipt = {"appended": 0, "skipped_existing": 1, "artifact": artifact}
    else:
        row = payload(
            json.loads(result_path.read_text()), artifact, _job_id(root)
        )
        out = root / "dino_phase3_joint_stability_ingest" / result_path.parent.name
        out.mkdir(parents=True, exist_ok=True)
        row_path = out / "DINO_LONG.json"
        fleet.atomic_json(row_path, row)
        fleet.add_result(root, row_path)
        receipt = {
            "appended": 1,
            "skipped_existing": 0,
            "artifact": artifact,
            "row": row,
        }
        fleet.atomic_json(out / "summary.json", receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
