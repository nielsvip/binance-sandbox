#!/usr/bin/env python3
"""Ingest strict final-fold E05 same-entry winners into path-fleet."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402


PATH_ID = "EXIT_E05_DIVERGENCE_RETEST"


def _claim(root: Path, worker: str) -> int:
    definition = next(item for item in fleet.PATHS if item.path_id == PATH_ID)
    con = sqlite3.connect(root / "queue.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("BEGIN IMMEDIATE")
    row = con.execute(
        "SELECT * FROM jobs WHERE path_id=?", (PATH_ID,)
    ).fetchone()
    if row is None or row["status"] not in {
        "ADAPTER_REQUIRED",
        "READY",
        "READY_BOTH_SIDES",
        "SCREENED",
    }:
        state = None if row is None else row["status"]
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(f"{PATH_ID} cannot be claimed from {state!r}")
    now = time.time()
    con.execute(
        """UPDATE jobs SET status='RUNNING',claimed_by=?,claimed_at=?,
           heartbeat_at=?,attempts=attempts+1,message=?,contract_json=?
           WHERE id=?""",
        (
            worker,
            now,
            now,
            "E05 causal same-entry screen complete; ingesting final folds",
            json.dumps(fleet.contract_for(definition), sort_keys=True),
            int(row["id"]),
        ),
    )
    con.execute("COMMIT")
    con.close()
    fleet.write_report(root)
    return int(row["id"])


def _comparison(winner: dict[str, Any], alpha_key: str) -> float:
    strategy = float(winner["nested"]["validation"]["capital_return_pct"])
    return strategy - float(winner["nested"][alpha_key])


def _ingest(root: Path, job_id: int, row: dict[str, Any]) -> dict[str, Any]:
    if row["status"] != "OK":
        raise RuntimeError(row.get("stderr", "cohort error"))
    artifact = Path(row["artifact"])
    result = json.loads((artifact / "result.json").read_text())
    winner = result["frozen_discovery_winners"][0]
    validation = winner["nested"]["validation"]
    connected = bool(
        validation["raw_completed_signal_events"] > 0
        and validation["technical_exit_fills"] > 0
    )
    robust = winner in result["survivors"]
    status = (
        "VECTOR_SURVIVOR_EXACT_PENDING"
        if robust
        else "GRAY_REJECTED"
        if connected
        else "RED_DIAGNOSTIC_INERT"
    )
    payload = {
        "job_id": job_id,
        "symbol": result["symbol"],
        "side": result["side"],
        "stage": "VEC_UNTOUCHED_OOS",
        "status": status,
        "strategy_return_pct": float(validation["capital_return_pct"]),
        "bh_return_pct": _comparison(
            winner, "validation_alpha_vs_bh_pp"
        ),
        "same_entry_control_return_pct": _comparison(
            winner, "validation_alpha_vs_same_entry_e02_pp"
        ),
        "tim_pct": float(validation["exposure_weighted_tim_pct"]),
        "trades": int(validation["technical_exit_fills"]),
        "untouched_oos": True,
        "exact_replay": False,
        "future_htf_count": int(
            winner["metrics"]["future_htf_source_count"]
        ),
        "artifact": str(artifact),
        "metric_scope": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
        "return_unit": "CAPITAL_RETURN_PCT",
        "return_aggregation": "NONE_SINGLE_FOLD",
        "tim_unit": "PCT",
        "tim_aggregation": "NONE_SINGLE_FOLD",
        "trades_unit": "TECHNICAL_EXIT_FILLS",
        "trades_aggregation": "NONE_SINGLE_FOLD",
        "params": winner["params"],
        "raw_completed_signal_events": validation[
            "raw_completed_signal_events"
        ],
        "actual_exit_fills": validation["technical_exit_fills"],
        "all_fold_gates": winner["fold_gate_pass"],
        "open_reclaim_obligations": validation[
            "clip_obligations_unfilled_at_end"
        ],
        "inert_candidate_count": result["inert_candidate_count"],
        "zero_actual_exit_candidate_count": result[
            "zero_actual_exit_candidate_count"
        ],
        "registry_reconciliation": result["registry_reconciliation"],
        "discovery": winner["nested"]["discovery"],
        "validation": validation,
        "discovery_alpha_vs_bh_pp": winner["nested"][
            "discovery_alpha_vs_bh_pp"
        ],
        "discovery_alpha_vs_control_pp": winner["nested"][
            "discovery_alpha_vs_same_entry_e02_pp"
        ],
        "validation_alpha_vs_bh_pp": winner["nested"][
            "validation_alpha_vs_bh_pp"
        ],
        "validation_alpha_vs_control_pp": winner["nested"][
            "validation_alpha_vs_same_entry_e02_pp"
        ],
        "entry_schedule_sha256_by_fold": winner["metrics"][
            "entry_schedule_sha256_by_fold"
        ],
        "promotion_allowed": False,
    }
    path = (
        root
        / f"job_{job_id}_{PATH_ID}"
        / f"{result['symbol']}_{result['side']}.result.json"
    )
    fleet.atomic_json(path, payload)
    fleet.add_result(root, path)
    return {
        "symbol": result["symbol"],
        "side": result["side"],
        "status": status,
        "strategy_return_pct": payload["strategy_return_pct"],
        "bh_return_pct": payload["bh_return_pct"],
        "same_entry_control_return_pct": payload[
            "same_entry_control_return_pct"
        ],
        "tim_pct": payload["tim_pct"],
        "raw_signals": payload["raw_completed_signal_events"],
        "actual_exits": payload["actual_exit_fills"],
        "all_fold_gates": payload["all_fold_gates"],
        "open_obligations": payload["open_reclaim_obligations"],
        "params": payload["params"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    parser.add_argument("--cohort-result", type=Path, required=True)
    parser.add_argument("--worker", default="path-fleet-e05")
    args = parser.parse_args()
    root = args.root.resolve()
    cohort = json.loads(args.cohort_result.resolve().read_text())
    if cohort["candidate_count_per_key"] != 108:
        raise RuntimeError("E05 cohort missing 108-arm active registry grid")
    job_id = _claim(root, args.worker)
    run_dir = root / f"job_{job_id}_{PATH_ID}"
    summary: dict[str, Any] = {
        "job_id": job_id,
        "path_id": PATH_ID,
        "created_at": fleet.utc_now(),
        "source_cohort": str(args.cohort_result.resolve()),
        "contract": {
            "candidates_per_key": 108,
            "same_entry": True,
            "sides_separate": True,
            "completed_4h_only": True,
            "first_break_exit": False,
            "required_sequence": (
                "divergence -> structural break -> rebound -> rollover"
            ),
            "live_config_status": "DISCONNECTED_RESEARCH_ONLY",
            "every_fold_gates": [
                "actual signals",
                "actual exits",
                "alpha>B&H",
                "alpha>same-entry E02",
                "70-80% weighted TIM",
                "no open reclaim obligations",
                "solvent",
            ],
            "exposure_gate_pct": [70.0, 80.0],
        },
        "symbols": [],
    }
    errors = 0
    for row in cohort["results"]:
        try:
            summary["symbols"].append(_ingest(root, job_id, row))
        except Exception as exc:
            errors += 1
            summary["symbols"].append(
                {
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "status": "ERROR",
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
        fleet.heartbeat(root, job_id, args.worker)
        fleet.atomic_json(run_dir / "summary.partial.json", summary)
    summary["completed_at"] = fleet.utc_now()
    summary["errors"] = errors
    summary["exact_replay_queue"] = [
        f"{row['symbol']}_{row['side']}"
        for row in summary["symbols"]
        if row["status"] == "VECTOR_SURVIVOR_EXACT_PENDING"
    ]
    path = run_dir / "summary.json"
    fleet.atomic_json(path, summary)
    fleet.finish(
        root,
        job_id,
        args.worker,
        path,
        f"E05 keys={len(summary['symbols'])}; errors={errors}; "
        f"exact queue={len(summary['exact_replay_queue'])}",
    )
    print(json.dumps(summary, sort_keys=True))
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
