#!/usr/bin/env python3
"""Explicitly invalidate evidence made by the pre-parent-batch partial scanner.

The historical scanner required ``pending_signal + 1`` and therefore could
not honor the shared parent-close observation clock.  This tool preserves
every original report beside a byte-identical backup, adds an explicit
invalidation block, and updates matching path-fleet rows transactionally.

The terminal-liquidation duplicate initially suspected during diagnosis did
not exist in either tracked HEAD or deployed S1 SHA d8d2497c...; it is not an
invalidation reason.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INVALID_STATUS = "INVALIDATED_UNSAFE_PARENT_BATCH_CLOCK"
INVALID_REASON = (
    "pre-fix vec_same_entry_partial_scan required pending_signal+1 and could "
    "fill or fail inside a duplicate synthetic parent-close availability "
    "batch; evidence cannot prove first-strictly-later execution"
)
OLD_SCANNER_SHA256 = (
    "d8d2497c1234d4033c70b5556d124e139a43a87e3a857531d2fef72ad4725510"
)
TARGET_TIERS = {
    "VEC_RESEARCH_SAME_ENTRY_E06",
    "VEC_RESEARCH_SAME_ENTRY_PARTIAL_RUNNER",
    "VEC_RESEARCH_SAME_ENTRY_E06_COHORT",
    "VEC_RESEARCH_SAME_ENTRY_PARTIAL_COHORT",
}
TARGET_PATHS = {
    "EXIT_E06_REGRESSION_RETEST",
    "EXIT_PARTIAL_RUNNER",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_target_report(payload: dict[str, Any]) -> bool:
    tier = str(payload.get("tier") or payload.get("manifest", {}).get("tier"))
    return (
        tier in TARGET_TIERS
        or tier.startswith("VEC_RESEARCH_SAME_ENTRY_E06")
        or tier.startswith("VEC_RESEARCH_SAME_ENTRY_PARTIAL")
    )


def find_reports(report_root: Path) -> list[Path]:
    rows = []
    for path in report_root.rglob("result.json"):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if _is_target_report(payload):
            rows.append(path)
    return sorted(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-root", type=Path, required=True)
    ap.add_argument("--fleet-root", type=Path, required=True)
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    report_root = args.report_root.resolve()
    fleet_root = args.fleet_root.resolve()
    reports = find_reports(report_root)
    now = datetime.now(timezone.utc).isoformat()
    report_rows = []
    for path in reports:
        original_sha = sha256(path)
        backup = path.with_name("result.pre_parent_clock_invalidation.json")
        payload = json.loads(path.read_text())
        already = payload.get("evidence_status") == INVALID_STATUS
        report_rows.append(
            {
                "path": str(path),
                "original_sha256": (
                    payload.get("invalidation", {}).get(
                        "original_result_sha256", original_sha
                    )
                ),
                "backup": str(backup),
                "already_invalidated": already,
            }
        )
        if args.dry_run or already:
            continue
        if backup.exists():
            if sha256(backup) != original_sha:
                raise RuntimeError(f"existing backup differs: {backup}")
        else:
            shutil.copy2(path, backup)
        payload["evidence_status"] = INVALID_STATUS
        payload["promotion_allowed"] = False
        payload["matrix_written"] = False
        payload["exact_replay_allowed"] = False
        payload["invalidation"] = {
            "status": INVALID_STATUS,
            "invalidated_utc": now,
            "reason": INVALID_REASON,
            "old_scanner_sha256": OLD_SCANNER_SHA256,
            "original_result_sha256": original_sha,
            "rollback": (
                f"restore byte-identical {backup.name}; restoration does not "
                "make the old evidence causal"
            ),
            "terminal_duplicate_diagnosis_correction": (
                "DISPROVED: tracked HEAD and deployed old scanner contain "
                "one terminal liquidation booking; this is not a reason"
            ),
        }
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        )

    db = fleet_root / "queue.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    fleet_rows = con.execute(
        """SELECT r.id,r.status,r.payload_json,j.path_id
           FROM results r JOIN jobs j ON j.id=r.job_id
           WHERE j.path_id IN ('EXIT_E06_REGRESSION_RETEST',
                               'EXIT_PARTIAL_RUNNER')"""
    ).fetchall()
    fleet_changes = []
    for row in fleet_rows:
        payload = json.loads(row["payload_json"])
        # The new MU contingency explicitly identifies its independent Python
        # simulator and must never be swept into this historical invalidation.
        if payload.get("stage") == "VEC_MU_LADDER_TOP_EXIT_DISCOVERY":
            continue
        if payload.get("status") == INVALID_STATUS:
            continue
        fleet_changes.append(
            {
                "result_id": int(row["id"]),
                "path_id": row["path_id"],
                "old_status": row["status"],
            }
        )
        if args.dry_run:
            continue
        payload["pre_invalidation_status"] = row["status"]
        payload["status"] = INVALID_STATUS
        payload["promotion_candidate"] = False
        payload["invalidation"] = {
            "status": INVALID_STATUS,
            "invalidated_utc": now,
            "reason": INVALID_REASON,
            "old_scanner_sha256": OLD_SCANNER_SHA256,
            "terminal_duplicate_diagnosis_correction": (
                "DISPROVED; not an invalidation reason"
            ),
        }
        con.execute(
            "UPDATE results SET status=?,payload_json=? WHERE id=?",
            (
                INVALID_STATUS,
                json.dumps(payload, sort_keys=True),
                int(row["id"]),
            ),
        )
    if args.dry_run:
        con.rollback()
    else:
        con.commit()
    con.close()
    receipt = {
        "created_utc": now,
        "dry_run": args.dry_run,
        "status": INVALID_STATUS,
        "reason": INVALID_REASON,
        "old_scanner_sha256": OLD_SCANNER_SHA256,
        "terminal_duplicate_diagnosis": {
            "verdict": "DISPROVED",
            "tracked_and_deployed_terminal_booking_count": 1,
            "not_an_invalidation_reason": True,
        },
        "report_count": len(report_rows),
        "reports": report_rows,
        "fleet_change_count": len(fleet_changes),
        "fleet_changes": fleet_changes,
    }
    if not args.dry_run:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(
        {
            "dry_run": args.dry_run,
            "reports": len(report_rows),
            "fleet_changes": len(fleet_changes),
        },
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
