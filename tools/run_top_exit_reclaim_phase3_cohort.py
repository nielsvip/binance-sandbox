#!/usr/bin/env python3
"""Parallel, receipt-producing wrapper for the phase-3 top-exit campaign."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "tools" / "run_top_exit_reclaim_phase3.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(item: dict[str, Any], output_root: Path) -> dict[str, Any]:
    symbol = str(item["symbol"]).upper()
    output = output_root / f"{symbol}_LONG"
    cmd = [
        sys.executable,
        str(CAMPAIGN),
        "--artifact",
        str(Path(item["artifact"]).resolve()),
        "--npz-dir",
        str(Path(item["npz_dir"]).resolve()),
        "--out-dir",
        str(output),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    row: dict[str, Any] = {
        "symbol": symbol,
        "status": "OK" if proc.returncode == 0 else "ERROR",
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4_000:],
        "stderr": proc.stderr[-8_000:],
        "artifact": str(output),
    }
    if proc.returncode == 0:
        result_path = output / "result.json"
        result = json.loads(result_path.read_text())
        winner = result["frozen_discovery_winner"]
        row.update(
            {
                "candidate_count": result["candidate_count"],
                "discovery_strict_count": result["discovery_strict_count"],
                "strict_survivor_count": result["strict_survivor_count"],
                "winner": winner["label"],
                "winner_family": winner["family"],
                "winner_fold_evidence": winner["fold_evidence"],
                "result_sha256": _sha(result_path),
            }
        )
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    manifest = json.loads(args.manifest.resolve().read_text())
    items = manifest["cohort"]
    symbols = [str(row["symbol"]).upper() for row in items]
    if symbols != ["MU", "MRVL", "SNDK", "DINO", "ARM", "VT"]:
        raise ValueError("phase-3 cohort/order must be MU,MRVL,SNDK,DINO,ARM,VT")
    args.output_root.mkdir(parents=True, exist_ok=False)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as pool:
        rows = list(pool.map(lambda item: _run(item, args.output_root), items))
    summary = {
        "campaign": "TOP_EXIT_RECLAIM_PHASE3_V1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(args.manifest.resolve()),
        "source_manifest_sha256": _sha(args.manifest.resolve()),
        "campaign_script_sha256": _sha(CAMPAIGN),
        "cohort": symbols,
        "results": rows,
        "candidate_evaluations": sum(
            int(row.get("candidate_count", 0)) for row in rows
        ),
        "strict_survivors": sum(
            int(row.get("strict_survivor_count", 0)) for row in rows
        ),
        "errors": sum(row["status"] != "OK" for row in rows),
        "matrix_written": False,
        "promotion_allowed": False,
    }
    (args.output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))
    return int(summary["errors"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
