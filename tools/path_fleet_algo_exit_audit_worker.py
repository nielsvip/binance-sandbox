#!/usr/bin/env python3
"""Finish job66 as a red wiring audit; never invent an ALGO vector strategy."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402
from tools.audit_algo_exit_wiring import audit_repo, render_markdown  # noqa: E402
from tools.path_fleet_e02_worker import _claim  # noqa: E402


PATH_ID = "EXIT_ALGO_EXIT_ENABLED"


def cohort_rows(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for summary in summaries:
        for row in summary["symbols"]:
            if (
                row.get("status") not in {"CONTROL_ROW", "CONTROL_FAILURE"}
                or not row.get("artifact")
            ):
                continue
            result = json.loads(
                (Path(row["artifact"]) / "result.json").read_text()
            )
            side = str(result["manifest"]["side"]).upper()
            rows.append(
                {
                    "symbol": str(row["symbol"]).upper(),
                    "side": side,
                    "status": "RED_DISCONNECTED_NO_SCREEN",
                    "actual_exit_events": 0,
                    "vector_screen_performed": False,
                    "exact_replay_eligible": False,
                    "matrix_eligible": False,
                    "control_artifact": str(row["artifact"]),
                }
            )
    rows.sort(key=lambda row: (row["side"], row["symbol"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--long-summary", type=Path, required=True)
    ap.add_argument("--short-summary", type=Path, required=True)
    ap.add_argument("--worker", default="path-fleet-algo-exit-audit")
    args = ap.parse_args()
    audit = audit_repo(ROOT)
    if (
        audit["classification"]
        != "DISCONNECTED_DISABLED_INERT_REGISTRY_ROW"
    ):
        raise RuntimeError(
            f"job66 audit no longer fail-closed: {audit['classification']}"
        )
    summaries = [
        json.loads(args.long_summary.read_text()),
        json.loads(args.short_summary.read_text()),
    ]
    symbols = cohort_rows(summaries)
    if len(symbols) != 20:
        raise RuntimeError(f"expected top/bottom-10 cohort, got {len(symbols)}")
    if sum(row["side"] == "LONG" for row in symbols) != 10:
        raise RuntimeError("expected exactly 10 LONG controls")
    if sum(row["side"] == "SHORT" for row in symbols) != 10:
        raise RuntimeError("expected exactly 10 SHORT controls")
    root = args.root.resolve()
    job = _claim(root, args.worker, path_id=PATH_ID)
    job_id = int(job["id"])
    run_dir = root / f"job_{job_id}_{PATH_ID}"
    run_dir.mkdir(parents=True, exist_ok=True)
    fleet.atomic_json(run_dir / "wiring_audit.json", audit)
    (run_dir / "wiring_audit.md").write_text(render_markdown(audit))
    summary = {
        "job_id": job_id,
        "path_id": PATH_ID,
        "created_at": fleet.utc_now(),
        "completed_at": fleet.utc_now(),
        "status": "RED_DISCONNECTED_NO_SCREEN",
        "contract": {
            "cohort": "top-10 LONG / bottom-10 SHORT frozen job33 controls",
            "same_entry_control_available_but_not_executed": True,
            "vector_screen_performed": False,
            "screen_block_reason": audit["screen_block_reason"],
            "event_types_kept_separate": list(
                audit["historical_hardcoded_components"]
            ),
            "research_reconstruction_authorized": False,
            "actual_exit_required": True,
            "actual_exit_events": 0,
            "exact_replay_queue": [],
            "matrix_written": False,
            "promotion_allowed": False,
        },
        "audit": audit,
        "symbols": symbols,
        "errors": 0,
        "exact_replay_queue": [],
    }
    summary_path = run_dir / "summary.json"
    fleet.atomic_json(summary_path, summary)
    fleet.finish(
        root,
        job_id,
        args.worker,
        summary_path,
        (
            "red disconnected/inert wiring audit; 20 aligned controls; "
            "no vector strategy manufactured; exact queue=0"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
