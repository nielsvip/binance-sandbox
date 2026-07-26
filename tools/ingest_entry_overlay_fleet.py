#!/usr/bin/env python3
"""Ingest the bounded GR/WT_DC OOS screens into the path-fleet ledger."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402
from tools.normalize_entry_fleet_metrics import (  # noqa: E402
    NORMALIZATION_VERSION,
    normalized_payloads,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet-root", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    args = ap.parse_args()
    root = args.fleet_root.resolve()
    summary = json.loads(args.summary.read_text())
    con = sqlite3.connect(root / "queue.db")
    families = sorted({str(row["family"]) for row in summary["rows"]})
    placeholders = ",".join("?" for _ in families)
    jobs = {
        path_id: job_id
        for job_id, path_id in con.execute(
            f"SELECT id,path_id FROM jobs WHERE path_id IN ({placeholders})",
            families,
        )
    }
    if set(jobs) != set(families):
        raise RuntimeError(f"missing fleet jobs: {jobs}")
    inserted = 0
    for row in summary["rows"]:
        job_id = jobs[row["family"]]
        artifact = Path(row["artifact"])
        if not artifact.is_absolute():
            artifact = ROOT / artifact
        result = json.loads((artifact / "result.json").read_text())
        existing = con.execute(
            """SELECT 1 FROM results
               WHERE job_id=? AND artifact=?
                 AND payload_json LIKE ?
               LIMIT 1""",
            (
                job_id,
                str(row["artifact"]),
                f'%"normalization_version": "{NORMALIZATION_VERSION}"%',
            ),
        ).fetchone()
        if existing:
            continue
        source = {
            "job_id": job_id,
            "id": None,
            "symbol": row["symbol"],
            "side": row["side"],
            "stage": "VEC_UNTOUCHED_OOS",
            "status": row["status"],
            "artifact": str(row["artifact"]),
        }
        aggregate_payload, final_payload = normalized_payloads(source, result)
        for payload in (aggregate_payload, final_payload):
            payload.update(
                {
                    "exposure_policy_pass": row["exposure_policy_pass"],
                    "all_folds_beat_bh": row["all_folds_beat_bh"],
                    "all_folds_beat_control": row["all_folds_beat_control"],
                }
            )
            tmp = root / (
                f".ingest_{row['family']}_{row['symbol']}_{payload['stage']}.json"
            )
            fleet.atomic_json(tmp, payload)
            con.commit()
            con.close()
            fleet.add_result(root, tmp)
            tmp.unlink(missing_ok=True)
            inserted += 1
            con = sqlite3.connect(root / "queue.db")
    by_family = {
        family: [row for row in summary["rows"] if row["family"] == family]
        for family in families
    }
    for family, job_id in jobs.items():
        family_rows = by_family[family]
        side_counts = {
            side: sum(row["side"] == side for row in family_rows)
            for side in ("LONG", "SHORT")
        }
        con.execute(
            """UPDATE jobs SET status='SCREENED', claimed_by='entry-overlay',
               heartbeat_at=?, result_path=?, message=?, attempts=attempts+1
               WHERE id=?""",
            (
                time.time(),
                str(args.summary),
                f"{side_counts['LONG']} LONG + {side_counts['SHORT']} SHORT "
                "side-isolated untouched-OOS rows; strict B&H+control+70-80% "
                "TIM survivors recorded in summary; exact only for survivors",
                job_id,
            ),
        )
    con.commit()
    con.close()
    fleet.write_report(root)
    print(json.dumps({"inserted": inserted, "jobs": jobs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
