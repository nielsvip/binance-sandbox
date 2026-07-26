#!/usr/bin/env python3
"""Append connected-wiring proof rows for DC_TIER_AUG_ENABLED."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402
from tools.audit_dc_tier_augment_wiring import audit_repo  # noqa: E402


FAMILY = "ENTRY_DC_TIER_AUG_ENABLED"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet-root", type=Path, required=True)
    args = ap.parse_args()
    root = args.fleet_root.resolve()
    audit = audit_repo()
    if audit["classification"] != "CONNECTED_DEFAULT_TRUE_EXISTING_BEHAVIOR_PRESERVED":
        raise RuntimeError(audit)
    con = sqlite3.connect(root / "queue.db")
    job_id = con.execute(
        "select id from jobs where path_id=?", (FAMILY,)
    ).fetchone()[0]
    controls = con.execute(
        """select r.symbol,r.side,r.artifact from results r join jobs j on j.id=r.job_id
           where j.path_id='ENTRY_LADDER_GREEN'
             and r.stage='VEC_UNTOUCHED_OOS' and r.payload_json like ?
           order by r.id""",
        ('%"normalization_version": "entry_fleet_metric_scope_v1"%',),
    ).fetchall()
    if not controls:
        controls = con.execute(
            """select r.symbol,r.side,r.artifact from results r join jobs j on j.id=r.job_id
               where j.path_id='ENTRY_LADDER_GREEN' and r.stage='VEC_UNTOUCHED_OOS'
               order by r.id"""
        ).fetchall()
    inserted = 0
    seen = set()
    for symbol, side, artifact in controls:
        if (symbol, side) in seen:
            continue
        seen.add((symbol, side))
        exists = con.execute(
            """select 1 from results where job_id=? and symbol=? and side=?
               and stage='WIRING_REPAIR_AUDIT' limit 1""",
            (job_id, symbol, side),
        ).fetchone()
        if exists:
            continue
        result = json.loads((Path(artifact) / "result.json").read_text())
        fold = max(
            result["outer_folds"],
            key=lambda row: int(row["validation_metrics"]["end_ts"]),
        )
        metrics = fold["validation_metrics"]
        payload = {
            "job_id": job_id, "symbol": symbol, "side": side,
            "stage": "WIRING_REPAIR_AUDIT",
            "status": "CONNECTED_DEFAULT_TRUE_WIRING_PROOF",
            "strategy_return_pct": 0.0,
            "bh_return_pct": metrics["bh_capital_return_pct"],
            "same_entry_control_return_pct": metrics["capital_return_pct"],
            "tim_pct": 0.0, "trades": 0, "untouched_oos": False,
            "exact_replay": False, "future_htf_count": 0,
            "artifact": str(root / f"job_{job_id}_{FAMILY}" / "wiring_audit.json"),
            "augment_signal_count": 0, "augment_request_count": 0,
            "augment_fill_count": 0, "wiring_audit": audit,
            "metric_scope": "WIRING_ONLY_NO_PERFORMANCE_CLAIM",
        }
        tmp = root / f".dc_tier_wiring_{symbol}_{side}.json"
        fleet.atomic_json(tmp, payload)
        con.commit()
        con.close()
        fleet.add_result(root, tmp)
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(root / "queue.db")
        inserted += 1
    out = root / f"job_{job_id}_{FAMILY}" / "wiring_audit.json"
    fleet.atomic_json(out, audit)
    con.close()
    print(json.dumps({"inserted": inserted, "audit": audit}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
