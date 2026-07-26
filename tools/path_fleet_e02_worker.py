#!/usr/bin/env python3
"""Claim, run, and ingest the bounded same-entry E02 path-fleet job.

The worker is intentionally specific to ``EXIT_E02_DONCHIAN``. It can move
that one audited ADAPTER_REQUIRED row into RUNNING after the adapter is
installed, runs both frozen cohort sides without pooling them, ingests the
chronologically frozen validation winner for every key, and finishes the job
SCREENED. Exact replay remains a separate mandatory stage for the survivor
queue; vector rows can never become promotion candidates.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402


PATH_ID = "EXIT_E02_DONCHIAN"


def _claim(
    root: Path,
    worker: str,
    *,
    path_id: str = PATH_ID,
) -> dict[str, Any]:
    con = sqlite3.connect(root / "queue.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("SELECT * FROM jobs WHERE path_id=?", (path_id,)).fetchone()
    if row is None:
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(f"missing path-fleet job {path_id}")
    if row["status"] not in {"ADAPTER_REQUIRED", "READY", "SCREENED"}:
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(
            f"{path_id} cannot be claimed from state {row['status']!r}"
        )
    now = time.time()
    con.execute(
        """UPDATE jobs SET status='RUNNING',claimed_by=?,claimed_at=?,
           heartbeat_at=?,attempts=attempts+1,message=?
           WHERE id=?""",
        (
            worker,
            now,
            now,
            "same-entry E02 adapter installed; bounded vector screen running",
            int(row["id"]),
        ),
    )
    con.execute("COMMIT")
    result = dict(
        con.execute("SELECT * FROM jobs WHERE id=?", (int(row["id"]),)).fetchone()
    )
    con.close()
    return result


def _same_control_validation(candidate: dict[str, Any]) -> float:
    validation_return = float(
        candidate["nested"]["validation"]["capital_return_pct_sum"]
    )
    return validation_return - float(
        candidate["nested"]["validation_alpha_vs_same_entry_e02_pp"]
    )


def _bh_validation(candidate: dict[str, Any]) -> float:
    validation_return = float(
        candidate["nested"]["validation"]["capital_return_pct_sum"]
    )
    return validation_return - float(
        candidate["nested"]["validation_alpha_vs_bh_pp"]
    )


def _ingest_symbol(
    root: Path,
    job_id: int,
    cohort_row: dict[str, Any],
    *,
    path_id: str = PATH_ID,
    winner_family: str | None = None,
    result_suffix: str = "",
) -> dict[str, Any]:
    if cohort_row["status"] != "OK":
        return {
            "symbol": cohort_row["symbol"],
            "side": cohort_row.get("side"),
            "status": "ERROR",
            "error": cohort_row.get("stderr", "cohort worker error"),
        }
    artifact = Path(cohort_row["artifact"])
    result = json.loads((artifact / "result.json").read_text())
    expected_family = winner_family or path_id
    frozen = [
        row
        for row in result["frozen_discovery_winners"]
        if row["family"] == expected_family
    ]
    if len(frozen) != 1:
        raise RuntimeError(
            f"{cohort_row['symbol']}: expected one frozen "
            f"{expected_family} winner"
        )
    winner = frozen[0]
    survivors = result["survivors"]
    robust = any(row["params"] == winner["params"] for row in survivors)
    validation = winner["nested"]["validation"]
    zero_exit_mtm = int(validation["exit_fills"]) == 0
    diagnostic_inert = path_id in {
        "EXIT_GR_OPPOSITE",
        "EXIT_E01_CHANDELIER",
        "EXIT_MTF_ATR_TRAIL",
        "EXIT_ALGO_STRUCTURE_1H_15M",
        "EXIT_ALGO_STOCH_4H_ROLL",
        "EXIT_ALGO_PROFIT_TAKE_15M",
        "BOTTOM_A_PROTECTIVE_TRAIL",
        "BOTTOM_B_DELAYED_LOWER_TOP",
        "BOTTOM_C_DELAYED_EMERGENCY",
        "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
        "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
    } and zero_exit_mtm
    payload = {
        "job_id": job_id,
        "symbol": result["symbol"],
        "side": result["side"],
        "stage": "VEC_UNTOUCHED_OOS",
        "status": (
            "VECTOR_SURVIVOR_EXACT_PENDING"
            if robust
            else (
                "RED_DIAGNOSTIC_INERT"
                if diagnostic_inert
                else "GRAY_REJECTED"
            )
        ),
        "strategy_return_pct": float(validation["capital_return_pct_sum"]),
        "bh_return_pct": _bh_validation(winner),
        "same_entry_control_return_pct": _same_control_validation(winner),
        "tim_pct": float(
            validation["exposure_weighted_tim_pct_row_weighted"]
        ),
        "trades": int(validation["exit_fills"]),
        "normal_exit_fills": int(
            validation.get("normal_exit_fills", validation["exit_fills"])
        ),
        "emergency_exit_fills": int(
            validation.get("emergency_exit_fills", 0)
        ),
        "emergency_exit_share": float(
            validation.get("emergency_exit_share", 0.0)
        ),
        "normal_exit_pnl_usd": float(
            validation.get("normal_exit_pnl_usd", 0.0)
        ),
        "emergency_exit_pnl_usd": float(
            validation.get("emergency_exit_pnl_usd", 0.0)
        ),
        "zero_exit_mtm": zero_exit_mtm,
        "inert": diagnostic_inert,
        "inert_reason": (
            f"no {path_id} fill in untouched validation"
            if diagnostic_inert
            else None
        ),
        "untouched_oos": True,
        "exact_replay": False,
        "future_htf_count": int(winner["metrics"]["future_htf_source_count"]),
        "artifact": str(artifact),
        "params": winner["params"],
        "vote_audit": winner.get("vote_audit"),
        "weakest_arm_probe": result.get("gr_opposite_weakest_arm_probe"),
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
        "fill_ratio": winner["metrics"]["fill_ratio"],
        "clamp_count": winner["metrics"]["clamp_count"],
        "minimum_account_equity_usd": winner["metrics"][
            "minimum_account_equity_usd"
        ],
        "insolvent_folds": winner["metrics"]["insolvent_folds"],
        "entry_capacity_breach": winner["metrics"]["entry_capacity_breach"],
        "bars_flat_beyond_reclaim": winner["metrics"][
            "bars_flat_beyond_reclaim"
        ],
        "dc_low4_profit_exit_used": result["dc_low4_profit_exit_used"],
        "promotion_allowed": False,
    }
    result_path = (
        root
        / f"job_{job_id}_{path_id}"
        / (
            f"{result['symbol']}_{result['side']}{result_suffix}"
            ".result.json"
        )
    )
    fleet.atomic_json(result_path, payload)
    fleet.add_result(root, result_path)
    return {
        "symbol": result["symbol"],
        "side": result["side"],
        "status": payload["status"],
        "artifact": str(artifact),
        "params": payload["params"],
        "strategy_return_pct": payload["strategy_return_pct"],
        "bh_return_pct": payload["bh_return_pct"],
        "same_entry_control_return_pct": payload[
            "same_entry_control_return_pct"
        ],
        "tim_pct": payload["tim_pct"],
        "trades": payload["trades"],
        "normal_exit_fills": payload["normal_exit_fills"],
        "emergency_exit_fills": payload["emergency_exit_fills"],
        "emergency_exit_share": payload["emergency_exit_share"],
        "normal_exit_pnl_usd": payload["normal_exit_pnl_usd"],
        "emergency_exit_pnl_usd": payload["emergency_exit_pnl_usd"],
        "zero_exit_mtm": payload["zero_exit_mtm"],
        "inert": payload["inert"],
        "weakest_arm_eligible_updates": (
            (
                payload["weakest_arm_probe"]
                .get("vote_audit", {})
                .get("eligible_completed_update_count")
            )
            if payload["weakest_arm_probe"]
            else None
        ),
        "weakest_arm_exit_fills": (
            payload["weakest_arm_probe"].get("exit_fills")
            if payload["weakest_arm_probe"]
            else None
        ),
        "clamp_count": payload["clamp_count"],
        "fill_ratio": payload["fill_ratio"],
        "insolvent_folds": payload["insolvent_folds"],
        "entry_capacity_breach": payload["entry_capacity_breach"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--npz-dir", type=Path, default=fleet.DEFAULT_NPZ)
    ap.add_argument(
        "--long-summary",
        type=Path,
        required=True,
    )
    ap.add_argument(
        "--short-summary",
        type=Path,
        required=True,
    )
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker", default="path-fleet-e02")
    args = ap.parse_args()
    root = args.root.resolve()
    job = _claim(root, args.worker)
    job_id = int(job["id"])
    command = [
        sys.executable,
        str(ROOT / "tools" / "run_same_entry_exit_cohort.py"),
        "--control-summary",
        str(args.long_summary.resolve()),
        "--control-summary",
        str(args.short_summary.resolve()),
        "--npz-dir",
        str(args.npz_dir.resolve()),
        "--output-root",
        str(args.output_root.resolve()),
        "--families",
        "E02_GRID",
        "--fold-mode",
        "nested",
        "--exposure-min-pct",
        "70",
        "--exposure-max-pct",
        "80",
        "--workers",
        str(args.workers),
    ]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    run_dir = root / f"job_{job_id}_{PATH_ID}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cohort.stdout.log").write_text(proc.stdout)
    (run_dir / "cohort.stderr.log").write_text(proc.stderr)
    cohort_path = args.output_root.resolve() / "cohort_result.json"
    if not cohort_path.exists():
        raise RuntimeError(
            f"E02 cohort failed rc={proc.returncode}: {proc.stderr[-2000:]}"
        )
    cohort = json.loads(cohort_path.read_text())
    summary = {
        "job_id": job_id,
        "path_id": PATH_ID,
        "created_at": fleet.utc_now(),
        "contract": {
            "grid": {
                "timeframe": ["1h", "4h", "D"],
                "lookback": [10, 15, 20, 30, 40, 55, 80],
                "profit_gate_pct": [0.0, 0.25, 0.5, 1.0],
            },
            "same_entry": True,
            "no_dc_low4_5m": True,
            "fold_mode": "nested",
            "exposure_gate_pct": [70.0, 80.0],
            "matrix_written": False,
            "promotion_allowed": False,
        },
        "symbols": [],
    }
    errors = 0
    for row in cohort["results"]:
        try:
            summary["symbols"].append(_ingest_symbol(root, job_id, row))
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
    summary["errors"] = errors + int(proc.returncode != 0)
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
            f"E02 same-entry vector keys={len(summary['symbols'])}; "
            f"errors={summary['errors']}; exact queue="
            f"{len(summary['exact_replay_queue'])}"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
