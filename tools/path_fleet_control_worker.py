#!/usr/bin/env python3
"""Run the initial ENTRY_LADDER_GREEN control cohort on S1.

The worker claims exactly one fleet job, heartbeats after every symbol, runs the
causal nested vector ladder campaign, ingests each frozen-OOS aggregate as a
control row, and closes the job with an auditable summary. Top performers run
LONG and bottom performers run the independently accounted causal SHORT mirror.
Neither side is promotable until its frozen vector result passes exact replay.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402


def _resume_job(root: Path, job_id: int, worker: str) -> dict:
    """Claim a previously screened control job for an append-only side backfill."""
    con = sqlite3.connect(root / "queue.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["path_id"] != "ENTRY_LADDER_GREEN":
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(f"job {job_id} is not the ladder control")
    if row["status"] not in {"SCREENED", "READY"}:
        con.execute("ROLLBACK")
        con.close()
        raise RuntimeError(f"job {job_id} cannot resume from {row['status']}")
    now = time.time()
    con.execute(
        """UPDATE jobs SET status='RUNNING',claimed_by=?,claimed_at=?,
           heartbeat_at=?,attempts=attempts+1 WHERE id=?""",
        (worker, now, now, job_id),
    )
    con.execute("COMMIT")
    out = dict(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    con.close()
    out["universe"] = json.loads(out.pop("universe_json"))
    out["contract"] = json.loads(out.pop("contract_json"))
    return out


def _run_symbol(
    symbol: str,
    side: str,
    npz_dir: Path,
    output_root: Path,
    random_curves: int,
) -> tuple[Path, dict]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "vec_band_ladder_walkforward.py"),
        "--symbol",
        symbol,
        "--side",
        side,
        "--npz-dir",
        str(npz_dir),
        "--out-dir",
        str(output_root),
        "--random-curves",
        str(random_curves),
        "--exit-n",
        "30",
    ]
    before = set(output_root.glob(f"band_ladder_walkforward_*_{symbol}_{side}"))
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    log_dir = output_root / "path_fleet_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{symbol}_{side}.stdout.log").write_text(proc.stdout)
    (log_dir / f"{symbol}_{side}.stderr.log").write_text(proc.stderr)
    if proc.returncode:
        raise RuntimeError(
            f"{symbol}: runner rc={proc.returncode}: {proc.stderr[-1000:]}"
        )
    after = set(output_root.glob(f"band_ladder_walkforward_*_{symbol}_{side}"))
    created = sorted(after - before, key=lambda p: p.stat().st_mtime)
    if not created:
        raise RuntimeError(f"{symbol}: no artifact created")
    artifact = created[-1]
    return artifact, json.loads((artifact / "result.json").read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--npz-dir", type=Path, default=fleet.DEFAULT_NPZ)
    ap.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "reports" / "vec_research",
    )
    ap.add_argument("--worker", default="path-fleet-control")
    ap.add_argument("--random-curves", type=int, default=32)
    ap.add_argument(
        "--resume-job-id",
        type=int,
        help="append missing side controls to a screened ladder job",
    )
    ap.add_argument(
        "--short-only",
        action="store_true",
        help="process only the frozen bottom-SHORT cohort",
    )
    args = ap.parse_args()
    root = args.root.resolve()
    job = (
        _resume_job(root, args.resume_job_id, args.worker)
        if args.resume_job_id is not None
        else fleet.claim(root, args.worker, 7200)
    )
    if not job:
        print("NO_READY_JOB")
        return 0
    if job["path_id"] != "ENTRY_LADDER_GREEN":
        raise RuntimeError(f"refusing unexpected job {job['path_id']}")
    summary = {
        "job_id": job["id"],
        "path_id": job["path_id"],
        "worker": args.worker,
        "created_at": fleet.utc_now(),
        "random_curves": args.random_curves,
        "symbols": [],
        "short_status": "VEC_CONTROL_RUNNING_EXACT_REPLAY_REQUIRED",
    }
    result_dir = root / f"job_{job['id']}_ENTRY_LADDER_GREEN"
    result_dir.mkdir(parents=True, exist_ok=True)
    errors = 0
    cohort = ([] if args.short_only else [
        (row["symbol"], "LONG") for row in job["universe"]["top_long"]
    ]) + [
        (row["symbol"], "SHORT") for row in job["universe"]["bottom_short"]
    ]
    for symbol, side in cohort:
        started = time.time()
        try:
            artifact, payload = _run_symbol(
                symbol,
                side,
                args.npz_dir.resolve(),
                args.output_root.resolve(),
                args.random_curves,
            )
            agg = payload["frozen_oos_aggregate"]
            folds = payload["outer_folds"]
            exit_count = sum(
                int(f["validation_metrics"].get("exit_count", 0)) for f in folds
            )
            future = sum(
                int(tf_row.get("source_timestamp_future_count", 0))
                for fold in payload.get("causality_audit", [])
                for tf_row in fold.get("by_tf", {}).values()
            )
            strategy = float(agg["capital_return_pct_sum"])
            control_failure = bool(agg.get("control_failure"))
            result_payload = {
                "job_id": job["id"],
                "symbol": symbol,
                "side": side,
                "stage": "VEC_UNTOUCHED_OOS",
                "status": (
                    "CONTROL_FAILURE" if control_failure else "CONTROL_ROW"
                ),
                "strategy_return_pct": strategy,
                "bh_return_pct": float(agg["bh_capital_return_pct_sum"]),
                # This row establishes the control; it is not a candidate that
                # can claim improvement over itself.
                "same_entry_control_return_pct": strategy,
                "tim_pct": float(agg["exposure_weighted_tim_pct_row_weighted"]),
                "trades": exit_count,
                "untouched_oos": True,
                "exact_replay": False,
                "future_htf_count": future,
                "artifact": str(artifact),
                "strategy_bh_multiple": agg.get("strategy_bh_multiple"),
                "fill_ratio": agg.get("fill_ratio"),
                "max_drawdown_pct": agg.get("max_drawdown_account_pct_max"),
                "control_failure": control_failure,
                "control_failure_reasons": agg.get(
                    "control_failure_reasons", []
                ),
                "insolvent_folds": agg.get("insolvent_folds", 0),
            }
            result_file = result_dir / f"{symbol}_{side}.result.json"
            fleet.atomic_json(result_file, result_payload)
            fleet.add_result(root, result_file)
            summary["symbols"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "status": result_payload["status"],
                    "artifact": str(artifact),
                    "strategy_return_pct": strategy,
                    "bh_return_pct": result_payload["bh_return_pct"],
                    "strategy_bh_multiple": agg.get("strategy_bh_multiple"),
                    "tim_pct": result_payload["tim_pct"],
                    "future_htf_count": future,
                    "control_failure_reasons": result_payload[
                        "control_failure_reasons"
                    ],
                    "seconds": time.time() - started,
                }
            )
        except Exception as exc:
            errors += 1
            summary["symbols"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "status": "ERROR",
                    "error": f"{type(exc).__name__}:{exc}",
                    "seconds": time.time() - started,
                }
            )
        fleet.heartbeat(root, job["id"], args.worker)
        fleet.atomic_json(result_dir / "summary.partial.json", summary)
    summary["completed_at"] = fleet.utc_now()
    summary["errors"] = errors
    summary["short_status"] = (
        "VEC_CONTROL_COMPLETE_EXACT_REPLAY_REQUIRED"
        if not any(
            row["status"] == "ERROR" and row["side"] == "SHORT"
            for row in summary["symbols"]
        )
        else "VEC_CONTROL_ERRORS"
    )
    summary_path = result_dir / (
        "summary.short.json" if args.short_only else "summary.json"
    )
    fleet.atomic_json(summary_path, summary)
    fleet.finish(
        root,
        job["id"],
        args.worker,
        summary_path,
        (
            f"LONG/SHORT vector controls={len(summary['symbols']) - errors}; "
            f"errors={errors}; exact replay required before promotion"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
