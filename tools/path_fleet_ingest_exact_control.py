#!/usr/bin/env python3
"""Ingest an exact ladder-control replay without making it promotable.

The exact row proves that the faithful engine reproduced the frozen vector
control. It compares with itself, so alpha versus control is zero and
``path_fleet_campaign.add_result`` necessarily stores it as RESEARCH_ONLY.
"""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--vector-artifact", type=Path, required=True)
    ap.add_argument("--exact-artifact", type=Path, required=True)
    args = ap.parse_args()

    vector = json.loads((args.vector_artifact / "result.json").read_text())
    exact = json.loads((args.exact_artifact / "run_summary.json").read_text())
    if exact.get("status") != "PASS" or not exact.get("signal_parity"):
        raise RuntimeError("exact artifact is not a passing signal-parity replay")
    if exact.get("promotion_allowed") or exact.get("matrix_written"):
        raise RuntimeError("exact control artifact must remain fail-closed")
    manifest = vector["manifest"]
    fold = vector["outer_folds"][-1]
    metrics = fold["validation_metrics"]
    audit = exact["audit"]
    future = int(
        audit.get("causality", {}).get("future_htf_source_count", -1)
    )
    if future != 0:
        raise RuntimeError(f"future HTF source count is {future}")
    side = str(manifest["side"]).upper()
    symbol = str(manifest["symbol"]).upper()
    strategy = float(metrics["capital_return_pct"])
    payload = {
        "job_id": args.job_id,
        "symbol": symbol,
        "side": side,
        "stage": "V8_EXACT_REPLAY",
        "status": "PASS",
        "strategy_return_pct": strategy,
        "bh_return_pct": float(metrics["bh_capital_return_pct"]),
        "same_entry_control_return_pct": strategy,
        "tim_pct": float(metrics["exposure_weighted_tim_pct"]),
        "trades": int(metrics["exit_count"]),
        "untouched_oos": True,
        "exact_replay": True,
        "future_htf_count": future,
        "artifact": str(args.exact_artifact.resolve()),
        "source_vector_artifact": str(args.vector_artifact.resolve()),
        "exact_actions": int(audit["schedule"]["scheduled"]),
        "accounting_delta_bp": float(audit["accounting"]["delta_bp"]),
        "capacity_status": audit["schedule"]["capacity"]["status"],
        "matrix_written": False,
        "promotion_allowed": False,
    }
    out_dir = args.root / f"job_{args.job_id}_ENTRY_LADDER_GREEN"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{symbol}_{side}.exact.result.json"
    fleet.atomic_json(out, payload)
    fleet.add_result(args.root, out)
    con = sqlite3.connect(args.root / "queue.db")
    stored = con.execute(
        """SELECT status,payload_json FROM results
           WHERE job_id=? AND symbol=? AND side=? AND stage='V8_EXACT_REPLAY'
           ORDER BY id DESC LIMIT 1""",
        (args.job_id, symbol, side),
    ).fetchone()
    con.close()
    if stored is None:
        raise RuntimeError("exact control row was not stored")
    stored_status, stored_payload_json = stored
    stored_payload = json.loads(stored_payload_json)
    print(
        json.dumps(
            {
                "result": str(out),
                "symbol": symbol,
                "side": side,
                "status": stored_status,
                "promotion_candidate": stored_payload.get(
                    "promotion_candidate"
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
