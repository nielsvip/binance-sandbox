#!/usr/bin/env python3
"""Ingest a completed structural-WT cohort into the durable path-fleet queue.

The cohort runner freezes discovery chronologically.  This ingester records
only that frozen winner's untouched validation fold for each symbol/side.
Compiled candidates must have passed the Python-function parity oracle.
Vector survivors remain exact-pending and can never promote directly.
"""
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


PATH_ID = "EXIT_STRUCTURAL_WT_LOWER_TOP"


def _claim(root: Path, worker: str) -> int:
    definition = next(path for path in fleet.PATHS if path.path_id == PATH_ID)
    con = sqlite3.connect(root / "queue.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("BEGIN IMMEDIATE")
    row = con.execute(
        "SELECT * FROM jobs WHERE path_id=?", (PATH_ID,)
    ).fetchone()
    if row is None:
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(f"missing path-fleet job {PATH_ID}")
    if row["status"] not in {"ADAPTER_REQUIRED", "READY", "SCREENED"}:
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(
            f"{PATH_ID} cannot be claimed from {row['status']!r}"
        )
    now = time.time()
    con.execute(
        """UPDATE jobs SET status='RUNNING',claimed_by=?,claimed_at=?,
           heartbeat_at=?,attempts=attempts+1,message=?,contract_json=?
           WHERE id=?""",
        (
            worker,
            now,
            now,
            "parity-gated structural-WT cohort completed; ingesting validation",
            json.dumps(fleet.contract_for(definition), sort_keys=True),
            int(row["id"]),
        ),
    )
    con.execute("COMMIT")
    con.close()
    fleet.write_report(root)
    return int(row["id"])


def _comparison(candidate: dict[str, Any], alpha_key: str) -> float:
    strategy = float(
        candidate["nested"]["validation"]["capital_return_pct_sum"]
    )
    return strategy - float(candidate["nested"][alpha_key])


def _ingest(
    root: Path,
    job_id: int,
    cohort_row: dict[str, Any],
) -> dict[str, Any]:
    if cohort_row["status"] != "OK":
        raise RuntimeError(cohort_row.get("stderr", "cohort worker error"))
    artifact = Path(cohort_row["artifact"])
    result = json.loads((artifact / "result.json").read_text())
    frozen = result["frozen_discovery_winners"]
    if len(frozen) != 1 or frozen[0]["family"] != PATH_ID:
        raise RuntimeError(f"{result['symbol']}: invalid frozen winner")
    winner = frozen[0]
    parity = winner.get("compiled_python_parity", {})
    if parity.get("status") != "PASS":
        raise RuntimeError(
            f"{result['symbol']}_{result['side']}: compiled/Python parity failed"
        )
    robust = winner in result["survivors"]
    validation = winner["nested"]["validation"]
    payload = {
        "job_id": job_id,
        "symbol": result["symbol"],
        "side": result["side"],
        "stage": "VEC_UNTOUCHED_OOS",
        "status": (
            "VECTOR_SURVIVOR_EXACT_PENDING" if robust else "GRAY_REJECTED"
        ),
        "strategy_return_pct": float(validation["capital_return_pct_sum"]),
        "bh_return_pct": _comparison(
            winner, "validation_alpha_vs_bh_pp"
        ),
        "same_entry_control_return_pct": _comparison(
            winner, "validation_alpha_vs_same_entry_e02_pp"
        ),
        "tim_pct": float(
            validation["exposure_weighted_tim_pct_row_weighted"]
        ),
        "trades": int(validation["exit_fills"]),
        "untouched_oos": True,
        "exact_replay": False,
        "future_htf_count": int(
            winner["metrics"]["future_htf_source_count"]
        ),
        "artifact": str(artifact),
        "params": {
            **winner["params"],
            "profit_gate_pct": winner["profit_gate_pct"],
        },
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
        "compiled_python_parity": parity,
        "entry_schedule_sha256_by_fold": winner["metrics"][
            "entry_schedule_sha256_by_fold"
        ],
        "fill_ratio": winner["metrics"]["fill_ratio"],
        "clamp_count": winner["metrics"]["clamp_count"],
        "insolvent_folds": winner["metrics"]["insolvent_folds"],
        "entry_capacity_breach": winner["metrics"][
            "entry_capacity_breach"
        ],
        "bars_flat_beyond_reclaim": winner["metrics"][
            "bars_flat_beyond_reclaim"
        ],
        "dc_low4_profit_exit_used": result["dc_low4_profit_exit_used"],
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
        "status": payload["status"],
        "strategy_return_pct": payload["strategy_return_pct"],
        "bh_return_pct": payload["bh_return_pct"],
        "same_entry_control_return_pct": payload[
            "same_entry_control_return_pct"
        ],
        "tim_pct": payload["tim_pct"],
        "params": payload["params"],
        "parity": parity["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    parser.add_argument("--cohort-result", type=Path, required=True)
    parser.add_argument("--worker", default="path-fleet-structural")
    args = parser.parse_args()
    root = args.root.resolve()
    cohort = json.loads(args.cohort_result.resolve().read_text())
    if cohort["families"] != ["STRUCTURAL_WT"]:
        raise RuntimeError("cohort is not the structural-WT registry screen")
    job_id = _claim(root, args.worker)
    run_dir = root / f"job_{job_id}_{PATH_ID}"
    summary: dict[str, Any] = {
        "job_id": job_id,
        "path_id": PATH_ID,
        "created_at": fleet.utc_now(),
        "source_cohort": str(args.cohort_result.resolve()),
        "contract": {
            "candidate_count_per_key": 768,
            "sides_separate": True,
            "completed_htf_only": True,
            "no_dc_low4_5m": True,
            "compiled_python_parity_required": True,
            "fold_mode": "nested",
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
    summary_path = run_dir / "summary.json"
    fleet.atomic_json(summary_path, summary)
    fleet.finish(
        root,
        job_id,
        args.worker,
        summary_path,
        (
            f"structural-WT keys={len(summary['symbols'])}; errors={errors}; "
            f"exact queue={len(summary['exact_replay_queue'])}"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
