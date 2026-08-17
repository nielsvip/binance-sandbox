#!/usr/bin/env python3
"""Idempotently add explicit units to existing SHORT exact fleet receipts.

Numeric DB columns and result values are never changed.  Only ``payload_json``
metadata is repaired, after a DB backup, so the digest can distinguish the
fixed-$2k FINAL return and binary/weighted TIM measures.
"""
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


def repair(root: Path, symbol: str = "LRCX") -> dict:
    root = root.resolve()
    db = root / "queue.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = db.with_name(f"queue.db.bak_short_exact_scope_{stamp}")
    shutil.copy2(db, backup)
    con = sqlite3.connect(db)
    rows = con.execute(
        """SELECT id,payload_json,strategy_return_pct,bh_return_pct,tim_pct
           FROM results
           WHERE symbol=? AND side='SHORT' AND stage='V8_EXACT_REPLAY'
             AND status='EXACT_PARITY_ONLY'""",
        (symbol.upper(),),
    ).fetchall()
    updated = unchanged = 0
    numeric_before = {
        row[0]: (row[2], row[3], row[4]) for row in rows
    }
    for result_id, raw, _, _, _ in rows:
        payload = json.loads(raw)
        audit = payload.get("schedule_audit") or {}
        tim = audit.get("time_in_market") or {}
        if "actual_binary_pct" not in tim or "actual_weighted_pct" not in tim:
            raise RuntimeError(f"result {result_id}: missing exact TIM audit")
        metadata = {
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
        }
        if all(payload.get(key) == value for key, value in metadata.items()):
            unchanged += 1
            continue
        payload.update(metadata)
        con.execute(
            "UPDATE results SET payload_json=? WHERE id=?",
            (json.dumps(payload, sort_keys=True), result_id),
        )
        updated += 1
    con.commit()
    numeric_after = {
        row[0]: (row[1], row[2], row[3])
        for row in con.execute(
            """SELECT id,strategy_return_pct,bh_return_pct,tim_pct
               FROM results WHERE id IN (%s)"""
            % ",".join("?" for _ in numeric_before),
            tuple(numeric_before),
        ).fetchall()
    } if numeric_before else {}
    con.close()
    if numeric_after != numeric_before:
        raise RuntimeError("numeric exact result changed during metadata repair")
    fleet.write_report(root)
    receipt = {
        "status": "PASS",
        "symbol": symbol.upper(),
        "matched": len(rows),
        "updated": updated,
        "unchanged": unchanged,
        "numeric_result_unchanged": True,
        "backup": str(backup),
    }
    out = root / "short_exact_metric_scope_repair" / stamp / "summary.json"
    fleet.atomic_json(out, receipt)
    return receipt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--symbol", default="LRCX")
    args = ap.parse_args()
    result = repair(args.root, args.symbol)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
