#!/usr/bin/env python3
"""Append extended A/B/C evidence to the existing bottom-exit fleet jobs."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import path_fleet_campaign as fleet
from tools.path_fleet_e02_worker import _ingest_symbol


FAMILIES = {
    "BOTTOM_A_PROTECTIVE_TRAIL": (
        "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "extended_a",
    ),
    "BOTTOM_B_DELAYED_LOWER_TOP": (
        "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
        "extended_b",
    ),
    "BOTTOM_C_DELAYED_EMERGENCY": (
        "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED",
        "extended_c",
    ),
}


def _job_ids(root: Path) -> dict[str, int]:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        "SELECT id,path_id FROM jobs WHERE path_id IN (?,?,?)",
        tuple(FAMILIES),
    ).fetchall()
    con.close()
    found = {str(path_id): int(job_id) for job_id, path_id in rows}
    missing = set(FAMILIES) - found.keys()
    if missing:
        raise RuntimeError(f"missing bottom fleet jobs: {sorted(missing)}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    args = ap.parse_args()
    root = args.root.resolve()
    cohort = json.loads(args.cohort.read_text())
    if int(cohort["symbols_completed"]) != int(cohort["symbols_requested"]):
        raise RuntimeError("refusing partial extended cohort ingest")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / f"queue.db.bak_bottom_extended_{stamp}"
    shutil.copy2(root / "queue.db", backup)
    job_ids = _job_ids(root)
    summary: dict[str, object] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_cohort": str(args.cohort.resolve()),
        "queue_backup": str(backup),
        "families": {},
    }
    for path_id, (winner_family, suffix) in FAMILIES.items():
        rows = []
        for cohort_row in cohort["results"]:
            rows.append(
                _ingest_symbol(
                    root,
                    job_ids[path_id],
                    cohort_row,
                    path_id=path_id,
                    winner_family=winner_family,
                    result_suffix=f".{suffix}",
                )
            )
        summary["families"][path_id] = {
            "job_id": job_ids[path_id],
            "winner_family": winner_family,
            "rows": rows,
        }
    out = root / f"bottom_extended_ingest_{stamp}.json"
    fleet.atomic_json(out, summary)
    fleet.write_report(root)
    print(json.dumps({"summary": str(out), "backup": str(backup)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
