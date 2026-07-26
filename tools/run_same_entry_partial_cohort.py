#!/usr/bin/env python3
"""Memory-bounded top-LONG/bottom-SHORT partial-runner cohort."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _one(
    row: dict[str, Any], npz_dir: Path, output: Path,
    exposure_min: float, exposure_max: float,
) -> dict[str, Any]:
    artifact = Path(row["artifact"])
    source = json.loads((artifact / "result.json").read_text())
    side = source["manifest"]["side"]
    key = f"{row['symbol']}_{side}"
    out = output / key
    command = [
        sys.executable,
        str(ROOT / "tools" / "vec_same_entry_partial_adapter.py"),
        "--artifact", str(artifact),
        "--npz-dir", str(npz_dir),
        "--out-dir", str(out),
        "--exposure-min-pct", str(exposure_min),
        "--exposure-max-pct", str(exposure_max),
    ]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        return {
            "symbol": row["symbol"], "side": side, "status": "ERROR",
            "stderr": proc.stderr[-4000:], "returncode": proc.returncode,
        }
    result = json.loads((out / "result.json").read_text())
    winner = result["frozen_discovery_winners"][0]
    return {
        "symbol": result["symbol"],
        "side": result["side"],
        "status": "OK",
        "artifact": str(out),
        "candidate_count": result["candidate_count"],
        "survivor_count": result["survivor_count"],
        "compiled_grid_elapsed_seconds": result[
            "compiled_grid_elapsed_seconds"
        ],
        "winner_params": winner["params"],
        "validation_alpha_vs_bh_pp": winner["nested"][
            "validation_alpha_vs_bh_pp"
        ],
        "validation_alpha_vs_control_pp": winner["nested"][
            "validation_alpha_vs_same_entry_e02_pp"
        ],
        "validation_tim_pct": winner["nested"]["validation"][
            "exposure_weighted_tim_pct"
        ],
        "validation_partial_exit_fills": winner["nested"]["validation"][
            "partial_exit_fills"
        ],
        "validation_realized_partial_pnl_net_usd": winner["nested"][
            "validation"
        ]["realized_partial_pnl_net_usd"],
        "validation_unfilled_obligations": winner["nested"]["validation"][
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
    parser.add_argument("--exposure-min-pct", type=float, default=70.0)
    parser.add_argument("--exposure-max-pct", type=float, default=80.0)
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
    # NPZ execution objects are large; one worker is deliberate and still fast.
    for row in rows:
        result = _one(
            row, args.npz_dir.resolve(), args.output_root.resolve(),
            args.exposure_min_pct, args.exposure_max_pct,
        )
        results.append(result)
        print(
            json.dumps(
                {
                    "key": f"{result['symbol']}_{result['side']}",
                    "status": result["status"],
                    "survivors": result.get("survivor_count"),
                    "elapsed": result.get("compiled_grid_elapsed_seconds"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_PARTIAL_COHORT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "candidate_count_per_key": 192,
        "registry_count_correction": (
            "listed 4x4x3x2x2 grid is 192; all clip sums <=0.83"
        ),
        "symbols_requested": len(rows),
        "symbols_completed": sum(row["status"] == "OK" for row in results),
        "survivor_symbols": [
            f"{row['symbol']}_{row['side']}" for row in results
            if row["status"] == "OK" and row["survivor_count"] > 0
        ],
        "results": results,
    }
    payload["exact_replay_queue"] = list(payload["survivor_symbols"])
    (args.output_root / "cohort_result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    return int(any(row["status"] != "OK" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
