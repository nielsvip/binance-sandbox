#!/usr/bin/env python3
"""Run and ingest the explicit per-TF EXIT_GR_OPPOSITE cohort job."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402
from tools.path_fleet_e02_worker import _claim, _ingest_symbol  # noqa: E402


PATH_ID = "EXIT_GR_OPPOSITE"


def _mark_prior_zero_exit_red(root: Path, job_id: int) -> int:
    """Preserve the wiring failure but prevent it reading as gray performance."""
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        """SELECT id,payload_json FROM results
           WHERE job_id=? AND stage='VEC_UNTOUCHED_OOS' AND trades=0""",
        (job_id,),
    ).fetchall()
    for row_id, payload_json in rows:
        payload = json.loads(payload_json)
        payload["status"] = "RED_DIAGNOSTIC_INERT"
        payload["inert"] = True
        payload["inert_reason"] = (
            "superseded edge-trigger wiring forgot an already-active GR vote"
        )
        con.execute(
            "UPDATE results SET status=?,payload_json=? WHERE id=?",
            (
                "RED_DIAGNOSTIC_INERT",
                json.dumps(payload, sort_keys=True),
                row_id,
            ),
        )
    con.commit()
    con.close()
    if rows:
        fleet.write_report(root)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--npz-dir", type=Path, default=fleet.DEFAULT_NPZ)
    ap.add_argument("--long-summary", type=Path, required=True)
    ap.add_argument("--short-summary", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker", default="path-fleet-gr-opposite")
    args = ap.parse_args()
    root = args.root.resolve()
    job = _claim(root, args.worker, path_id=PATH_ID)
    job_id = int(job["id"])
    prior_zero_exit_rows_marked_red = _mark_prior_zero_exit_red(root, job_id)
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
        "GR_OPPOSITE",
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
            f"GR_OPPOSITE cohort failed rc={proc.returncode}: "
            f"{proc.stderr[-2000:]}"
        )
    cohort = json.loads(cohort_path.read_text())
    summary = {
        "job_id": job_id,
        "path_id": PATH_ID,
        "created_at": fleet.utc_now(),
        "contract": {
            "grid": {
                "timeframes": ["15m", "1h", "4h", "D"],
                "min_tfs": [1, 2, 3],
                "min_indicators": [1, 2],
                "min_weighted_score": [4, 6, 8, 10, 12, 15.5],
                "weights_15m_1h_4h_D": [
                    [1, 1, 1, 1],
                    [0.5, 1, 2, 3],
                    [0, 1, 2, 4],
                ],
                "profit_gate_pct": [0, 0.25, 0.5, 1],
            },
            "candidate_count_per_key": 432,
            "score_formula": (
                "sum(raw_opposite_indicator_votes_by_tf * explicit_tf_weight)"
            ),
            "same_entry": True,
            "actual_exit_required": True,
            "zero_exit_mtm_demonstrates_exit_value": False,
            "completed_htf_only": True,
            "per_tf_votes_and_weights_retained": True,
            "side_mirrored": True,
            "fold_mode": "nested",
            "exposure_gate_pct": [70.0, 80.0],
            "matrix_written": False,
            "promotion_allowed": False,
        },
        "symbols": [],
        "prior_zero_exit_rows_marked_red": prior_zero_exit_rows_marked_red,
    }
    errors = 0
    for row in cohort["results"]:
        try:
            summary["symbols"].append(
                _ingest_symbol(root, job_id, row, path_id=PATH_ID)
            )
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
    summary["zero_exit_mtm_rows"] = [
        f"{row['symbol']}_{row['side']}"
        for row in summary["symbols"]
        if row.get("zero_exit_mtm")
    ]
    summary_path = run_dir / "summary.json"
    fleet.atomic_json(summary_path, summary)
    fleet.finish(
        root,
        job_id,
        args.worker,
        summary_path,
        (
            f"GR_OPPOSITE same-entry vector keys={len(summary['symbols'])}; "
            f"errors={summary['errors']}; exact queue="
            f"{len(summary['exact_replay_queue'])}; zero-exit rows="
            f"{len(summary['zero_exit_mtm_rows'])}"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
