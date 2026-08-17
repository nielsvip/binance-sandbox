#!/usr/bin/env python3
"""Materialize and replay one frozen band-ladder validation fold.

Outputs are confined to ``data/reports/vec_research``.  The command does not
write the switch matrix, live config, or symbol universes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v8_research_ladder_adapter import build_spec_and_schedule  # noqa: E402


DEFAULT_OUTPUT = ROOT / "data" / "reports" / "vec_research"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--engine", type=Path, default=ROOT / "backtest_v8_engine.py")
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--account", default="trb")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument(
        "--npz-path",
        type=Path,
        help=(
            "relocated immutable NPZ; accepted only when its SHA-256 matches "
            "the frozen source artifact"
        ),
    )
    args = ap.parse_args()

    artifact = args.artifact.resolve()
    source = json.loads((artifact / "result.json").read_text())
    manifest = source["manifest"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        args.output_root.resolve()
        / f"v8_exact_ladder_replay_{run_id}_{manifest['symbol']}_{manifest['side']}"
    )
    out.mkdir(parents=True, exist_ok=False)
    schedule_path = out / "frozen_ladder_schedule.jsonl.gz"
    spec = build_spec_and_schedule(
        artifact,
        schedule_path=schedule_path,
        account=args.account,
        npz_path_override=args.npz_path,
    )
    spec_path = out / "research_ladder_spec.json"
    spec_path.write_text(json.dumps(spec, sort_keys=True, indent=2) + "\n")
    override_path = out / "backtest_only_override.json"
    commission_round_trip_pct = (
        2.0 * float(spec["commission_bps_one_way"]) / 100.0
    )
    override_path.write_text(
        json.dumps(
            {"ROUND_TRIP_COST_PCT": commission_round_trip_pct},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    audit_path = out / "exact_engine_audit.json"
    result_path = out / "v8_result.txt"
    raw_path = out / "raw_events.jsonl"

    env = os.environ.copy()
    env.update(
        {
            "V8_OVERRIDE_FILE": str(override_path),
            "V8_SWEEP_MODE": "1",
            "V8_RATE_GUARD_DISABLED": "1",
            "V8_RESEARCH_LADDER_AUDIT_FILE": str(audit_path),
            "V8_RESULT_FILE": str(result_path),
            "V8_RAW_EVENTS_FILE": str(raw_path),
            "V8_TRADES_OUT_DIR": str(out / "chart_trades"),
            "V8_TRADES_RUN_ID": f"research_ladder_{run_id}",
            "V8_BACKTEST_END_DATE": str(spec["validation_end_exclusive"]),
        }
    )
    command = [
        sys.executable,
        str(args.engine.resolve()),
        "--mode",
        "tradier",
        "--account",
        args.account,
        "--start",
        str(spec["validation_start"]),
        "--symbols",
        str(spec["symbol"]),
        "--capital",
        str(args.capital),
        "--npz-dir",
        str(Path(spec["npz_path"]).resolve().parent),
        "--research-ladder-spec",
        str(spec_path),
    ]
    proc = subprocess.run(command, env=env, text=True, capture_output=True)
    (out / "engine_stdout.log").write_text(proc.stdout)
    (out / "engine_stderr.log").write_text(proc.stderr)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
    summary = {
        "status": (
            "PASS"
            if proc.returncode == 0 and audit and audit.get("status") == "PASS"
            else "FAIL"
        ),
        "tier": "VEC_RESEARCH_EXACT_ENGINE_LADDER_PARITY",
        "source_artifact": str(artifact),
        "output": str(out),
        "command": command,
        "returncode": proc.returncode,
        "engine_sha256": _sha256(args.engine.resolve()),
        "adapter_sha256": _sha256(
            ROOT / "tools" / "v8_research_ladder_adapter.py"
        ),
        "spec_sha256": _sha256(spec_path),
        "schedule_sha256": _sha256(schedule_path),
        "audit": audit,
        "matrix_written": False,
        "signal_parity": bool(audit and audit.get("signal_parity")),
        "promotion_allowed": False,
    }
    (out / "run_summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n"
    )
    print("V8_RESEARCH_LADDER_RUNNER: " + json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
