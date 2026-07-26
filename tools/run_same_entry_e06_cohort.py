#!/usr/bin/env python3
"""Memory-bounded frozen top/bottom cohort for E06 regression/retest."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _one(row: dict[str, Any], npz_dir: Path, output: Path) -> dict[str, Any]:
    artifact = Path(row["artifact"])
    source = json.loads((artifact/"result.json").read_text())
    side = source["manifest"]["side"]
    out = output/f"{row['symbol']}_{side}"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT/"tools"/"vec_same_entry_e06_adapter.py"),
            "--artifact", str(artifact),
            "--npz-dir", str(npz_dir),
            "--out-dir", str(out),
        ],
        cwd=ROOT, text=True, capture_output=True,
    )
    if proc.returncode:
        return {
            "symbol": row["symbol"], "side": side, "status": "ERROR",
            "returncode": proc.returncode, "stderr": proc.stderr[-4000:],
        }
    result = json.loads((out/"result.json").read_text())
    winner = result["frozen_discovery_winners"][0]
    validation = winner["nested"]["validation"]
    return {
        "symbol": result["symbol"],
        "side": result["side"],
        "status": "OK",
        "artifact": str(out),
        "candidate_count": result["candidate_count"],
        "survivor_count": result["survivor_count"],
        "elapsed": result["compiled_grid_elapsed_seconds"],
        "inert_candidates": result["inert_candidate_count"],
        "zero_fill_candidates": result["zero_actual_exit_candidate_count"],
        "winner_params": winner["params"],
        "validation_raw_signals": validation[
            "raw_completed_signal_events"
        ],
        "validation_actual_exits": validation["technical_exit_fills"],
        "validation_alpha_vs_bh_pp": winner["nested"][
            "validation_alpha_vs_bh_pp"
        ],
        "validation_alpha_vs_control_pp": winner["nested"][
            "validation_alpha_vs_same_entry_e02_pp"
        ],
        "validation_tim_pct": validation["exposure_weighted_tim_pct"],
        "validation_open_obligations": validation[
            "clip_obligations_unfilled_at_end"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--control-summary", type=Path, required=True, action="append"
    )
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in args.control_summary:
        source = json.loads(path.read_text())
        rows.extend(
            row for row in source["symbols"]
            if row.get("status") in {"CONTROL_ROW", "CONTROL_FAILURE"}
            and row.get("artifact")
        )
    args.output_root.mkdir(parents=True, exist_ok=False)
    results = []
    for row in rows:
        result = _one(row, args.npz_dir.resolve(), args.output_root.resolve())
        results.append(result)
        print(
            json.dumps(
                {
                    "key": f"{result['symbol']}_{result['side']}",
                    "status": result["status"],
                    "survivors": result.get("survivor_count"),
                    "signals": result.get("validation_raw_signals"),
                    "exits": result.get("validation_actual_exits"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_E06_COHORT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "candidate_count_per_key": 192,
        "symbols_requested": len(rows),
        "symbols_completed": sum(row["status"] == "OK" for row in results),
        "survivor_symbols": [
            f"{row['symbol']}_{row['side']}" for row in results
            if row["status"] == "OK" and row["survivor_count"] > 0
        ],
        "results": results,
    }
    payload["exact_replay_queue"] = list(payload["survivor_symbols"])
    (args.output_root/"cohort_result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)+"\n"
    )
    return int(any(row["status"]!="OK" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
