#!/usr/bin/env python3
"""Run the first decomposed ALGO event family over frozen LONG/SHORT entries."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402
from tools.path_fleet_e02_worker import _claim, _ingest_symbol  # noqa: E402


PATH_ID = "EXIT_ALGO_STRUCTURE_1H_15M"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--npz-dir", type=Path, default=fleet.DEFAULT_NPZ)
    ap.add_argument("--long-summary", type=Path, required=True)
    ap.add_argument("--short-summary", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--worker", default="path-fleet-algo-structure")
    args = ap.parse_args()
    root = args.root.resolve()
    job = _claim(root, args.worker, path_id=PATH_ID)
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
        "ALGO_STRUCTURE",
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
            f"{PATH_ID} cohort failed rc={proc.returncode}: "
            f"{proc.stderr[-2000:]}"
        )
    cohort = json.loads(cohort_path.read_text())
    summary = {
        "job_id": job_id,
        "path_id": PATH_ID,
        "created_at": fleet.utc_now(),
        "contract": {
            "candidate_count_per_key": 4,
            "event_types": ["STRUCTURE_1H_DC", "STRUCTURE_15M_DC"],
            "timeframes": ["1h", "15m"],
            "completed_tfs_only": True,
            "lookback_seed": 20,
            "historical_score_delta_provenance": {"1h": -15, "15m": -10},
            "profit_gate_pct_research": [0.0, 3.0],
            "broader_ranges": "TBD_FROM_FUNCTION_SOURCE",
            "removed_compound_score_reconstructed": False,
            "same_entry": True,
            "actual_exit_required": True,
            "fold_mode": "nested",
            "exposure_gate_pct": [70.0, 80.0],
            "bh_capital_usd": 2000.0,
            "strategy_capacity_usd": 16000.0,
            "matrix_written": False,
            "promotion_allowed": False,
        },
        "symbols": [],
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
            f"{PATH_ID} decomposed keys={len(summary['symbols'])}; "
            f"errors={summary['errors']}; exact queue="
            f"{len(summary['exact_replay_queue'])}; zero-exit="
            f"{len(summary['zero_exit_mtm_rows'])}"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
