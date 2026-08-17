#!/usr/bin/env python3
"""Invalidate reversed-SHORT evidence and append passing exact parity receipts."""
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

PATH_ID = "ENTRY_DISASTER_GUARD_ENABLED"
INVALID = "INVALIDATED_REVERSED_SLIPPAGE"


def exact_metric_scope(summary: dict, spec: dict) -> dict:
    """Explicit units for the single FINAL exact receipt.

    ``tim_pct`` in the fleet schema remains the existing binary TIM number;
    weighted TIM is retained beside it.  This metadata changes no result.
    """
    tim = summary["audit"]["schedule"]["time_in_market"]
    boundaries = spec.get("fold_boundaries") or {}
    return {
        "metric_scope": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
        "fold": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
        "return_unit": "FIXED_2000_USD_CAPITAL_RETURN_PCT",
        "return_aggregation": "NONE_SINGLE_FOLD",
        "capital_base_usd": 2000,
        "tim_unit": "PCT",
        "tim_metric": "BINARY_AND_EXPOSURE_WEIGHTED_TIME_IN_MARKET",
        "tim_aggregation": "NONE_SINGLE_FOLD",
        "tim_binary_pct": tim["actual_binary_pct"],
        "tim_weighted_pct": tim["actual_weighted_pct"],
        "validation_window": {
            "start": boundaries.get("validation_start"),
            "end_exclusive": boundaries.get("validation_end_exclusive"),
        },
    }


def _passes(summary: dict) -> bool:
    audit = summary.get("audit") or {}
    schedule = audit.get("schedule") or {}
    return bool(
        summary.get("status") == "PASS"
        and audit.get("status") == "PASS"
        and schedule.get("status") == "PASS"
        and schedule.get("schedule_status") == "PASS"
        and (schedule.get("time_in_market") or {}).get("status") == "PASS"
        and (schedule.get("capacity") or {}).get("status") == "PASS"
        and (audit.get("accounting") or {}).get("status") == "PASS"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--source-result", type=Path, required=True)
    ap.add_argument("--run-summary", type=Path, action="append", required=True)
    args = ap.parse_args()
    root = args.root.resolve()
    db = root / "queue.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db.with_name(f"queue.db.bak_short_slippage_{stamp}")
    shutil.copy2(db, backup)
    source = json.loads(args.source_result.read_text())
    source_artifact = str(args.source_result.resolve().parent)
    campaign_ids = {
        "short_guard_decomposition_20260727T022041Z",
        "asymmetric_short_20260727T020907Z",
    }
    submitted = [json.loads(path.read_text()) for path in args.run_summary]
    submitted_hashes = {summary["spec_sha256"] for summary in submitted}
    con = sqlite3.connect(db)
    invalidated = 0
    for result_id, raw in con.execute(
        "SELECT id,payload_json FROM results WHERE side='SHORT'"
    ).fetchall():
        payload = json.loads(raw)
        artifact = str(payload.get("artifact") or "")
        affected = (
            payload.get("campaign_id") in campaign_ids
            or payload.get("stage") == "VEC_ASYMMETRIC_SHORT_UNTOUCHED_OOS"
        )
        if not affected:
            continue
        payload["status"] = INVALID
        payload["invalidation_reason"] = (
            "SHORT entry/scale used open*(1+slip) and cover used "
            "open*(1-slip); exact engine requires adverse inverse."
        )
        payload["promotion_allowed"] = False
        con.execute(
            "UPDATE results SET status=?,payload_json=? WHERE id=?",
            (INVALID, json.dumps(payload, sort_keys=True), result_id),
        )
        invalidated += 1
    for result_id, raw in con.execute(
        "SELECT id,payload_json FROM results "
        "WHERE side='SHORT' AND stage='V8_EXACT_REPLAY'"
    ).fetchall():
        payload = json.loads(raw)
        if payload.get("source_result_invalidation") != INVALID:
            continue
        if payload.get("exact_spec_sha256") in submitted_hashes:
            continue
        status = (
            "INVALIDATED_DISCOVERY_BENCHMARK"
            if payload.get("symbol") == "ARM"
            else "SUPERSEDED_ACCOUNTING_AUDIT"
        )
        payload["status"] = status
        payload["promotion_allowed"] = False
        con.execute(
            "UPDATE results SET status=?,payload_json=? WHERE id=?",
            (status, json.dumps(payload, sort_keys=True), result_id),
        )
    con.commit()
    job_id = int(con.execute(
        "SELECT id FROM jobs WHERE path_id=?", (PATH_ID,)
    ).fetchone()[0])
    existing = {
        json.loads(raw).get("exact_spec_sha256")
        for raw, in con.execute(
            "SELECT payload_json FROM results WHERE stage='V8_EXACT_REPLAY'"
        )
    }
    con.close()
    source_rows = {
        (item["symbol"], row["profile"]["label"]): row
        for item in source["results"]
        for row in item["profiles"]
    }
    appended = skipped = 0
    out = root / "short_guard_exact_ingest" / stamp
    for summary_path in args.run_summary:
        summary = json.loads(summary_path.read_text())
        if not _passes(summary):
            raise RuntimeError(f"exact audit did not pass: {summary_path}")
        if summary["spec_sha256"] in existing:
            skipped += 1
            continue
        spec_path = Path(summary["output"]) / "research_short_guard_spec.json"
        spec = json.loads(spec_path.read_text())
        row = source_rows[(spec["symbol"], spec["guard_profile"]["label"])]
        accounting = summary["audit"]["accounting"]
        tim = summary["audit"]["schedule"]["time_in_market"]
        strategy = accounting["actual_capital_return_pct"]
        payload = {
            "job_id": job_id,
            "symbol": spec["symbol"],
            "side": "SHORT",
            "stage": "V8_EXACT_REPLAY",
            "status": "EXACT_PARITY_ONLY",
            "strategy_return_pct": strategy,
            "capital_return_pct": strategy,
            "account_return_pct": accounting["actual_account_return_pct"],
            "bh_return_pct": row["untouched_final"]["short_bh_return_pct"],
            "same_entry_control_return_pct": strategy,
            "tim_pct": tim["actual_binary_pct"],
            "trades": accounting["closed_lifecycles"],
            "untouched_oos": True,
            "exact_replay": True,
            "future_htf_count": 0,
            "artifact": summary["output"],
            "source_artifact": source_artifact,
            "source_result_invalidation": INVALID,
            "exact_spec_sha256": summary["spec_sha256"],
            "exact_schedule_sha256": summary["schedule_sha256"],
            "npz_sha256": summary["audit"]["fingerprints"]["npz_sha256"],
            "engine_sha256": summary["engine_sha256"],
            "guard_profile": spec["guard_profile"],
            "selected_candidate": spec["selected_candidate"],
            "emergency_invariants": spec["emergency_invariants"],
            "emergency_exit_count": spec["emergency_exit_count"],
            "schedule_audit": summary["audit"]["schedule"],
            "accounting_audit": accounting,
            "causality": summary["audit"]["causality"],
            "control_contract": (
                "exact schedule self-control; contaminated vector control "
                "invalidated; parity evidence cannot promote"
            ),
            "promotion_allowed": False,
            **exact_metric_scope(summary, spec),
        }
        path = out / f"{spec['symbol']}_SHORT_exact.json"
        fleet.atomic_json(path, payload)
        fleet.add_result(root, path)
        appended += 1
    fleet.write_report(root)
    report = {
        "backup": str(backup),
        "invalidated": invalidated,
        "exact_appended": appended,
        "exact_skipped": skipped,
        "status": "PASS",
    }
    fleet.atomic_json(out / "summary.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
