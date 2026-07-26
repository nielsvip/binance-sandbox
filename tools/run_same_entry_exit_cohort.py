#!/usr/bin/env python3
"""Parallel cohort wrapper for ``vec_same_entry_exit_adapter``.

The input is the completed ENTRY_LADDER_GREEN path-fleet summary, which is the
authoritative mapping from frozen top-LONG cohort symbols to their artifacts.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _run_one(
    row: dict[str, Any],
    *,
    npz_dir: Path,
    output_root: Path,
    families: str,
    fold_mode: str,
    exposure_min_pct: float,
    exposure_max_pct: float,
) -> dict[str, Any]:
    symbol = str(row["symbol"]).upper()
    artifact = Path(row["artifact"])
    out = output_root / f"{symbol}_LONG"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "vec_same_entry_exit_adapter.py"),
        "--artifact",
        str(artifact),
        "--npz-dir",
        str(npz_dir),
        "--out-dir",
        str(out),
        "--families",
        families,
        "--fold-mode",
        fold_mode,
        "--exposure-min-pct",
        str(exposure_min_pct),
        "--exposure-max-pct",
        str(exposure_max_pct),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        return {
            "symbol": symbol,
            "status": "ERROR",
            "returncode": proc.returncode,
            "stderr": proc.stderr[-4000:],
        }
    payload = json.loads((out / "result.json").read_text())
    best = payload["candidates"][0] if payload["candidates"] else None
    return {
        "symbol": symbol,
        "status": "OK",
        "artifact": str(out),
        "candidate_count": payload["candidate_count"],
        "survivor_count": payload["survivor_count"],
        "same_entry_e02_return_pct": payload["same_adapter_e02_control"][
            "capital_return_pct_sum"
        ],
        "bh_return_pct": payload["same_adapter_e02_control"][
            "bh_capital_return_pct_sum"
        ],
        "best": best,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-summary", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--families", default="WT_MTF,STRUCTURAL_WT")
    ap.add_argument(
        "--fold-mode", choices=("latest", "all", "nested"), default="latest"
    )
    ap.add_argument("--exposure-min-pct", type=float, default=70.0)
    ap.add_argument("--exposure-max-pct", type=float, default=80.0)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    source = json.loads(args.control_summary.read_text())
    rows = [
        row for row in source["symbols"] if row.get("status") == "CONTROL_ROW"
    ]
    args.output_root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as pool:
        futures = [
            pool.submit(
                _run_one,
                row,
                npz_dir=args.npz_dir.resolve(),
                output_root=args.output_root.resolve(),
                families=args.families,
                fold_mode=args.fold_mode,
                exposure_min_pct=args.exposure_min_pct,
                exposure_max_pct=args.exposure_max_pct,
            )
            for row in rows
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
    results.sort(key=lambda row: row["symbol"])
    survivor_symbols = [
        row["symbol"]
        for row in results
        if row["status"] == "OK" and int(row["survivor_count"]) > 0
    ]
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_COHORT",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "source_control_summary": str(args.control_summary.resolve()),
        "families": args.families.split(","),
        "fold_mode": args.fold_mode,
        "exposure_survivor_gate_pct": [
            args.exposure_min_pct,
            args.exposure_max_pct,
        ],
        "symbols_requested": len(rows),
        "symbols_completed": sum(r["status"] == "OK" for r in results),
        "survivor_symbols": survivor_symbols,
        "exact_replay_queue": survivor_symbols,
        "results": results,
    }
    (args.output_root / "cohort_result.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    return int(any(row["status"] != "OK" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
