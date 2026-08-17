#!/usr/bin/env python3
"""Build and run one frozen SHORT guard candidate through exact v8 replay."""
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
from tools.v8_research_short_guard_adapter import build_spec_and_schedule


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--npz-path", type=Path, required=True)
    ap.add_argument("--engine", type=Path, default=ROOT / "backtest_v8_engine.py")
    ap.add_argument("--output-root", type=Path, default=ROOT / "data/reports/vec_research")
    ap.add_argument("--account", default="trb")
    args = ap.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.output_root.resolve() / (
        f"v8_exact_short_guard_{stamp}_{args.symbol.upper()}_{args.profile}"
    )
    out.mkdir(parents=True, exist_ok=False)
    schedule = out / "frozen_short_guard_schedule.jsonl.gz"
    spec = build_spec_and_schedule(
        args.artifact,
        symbol=args.symbol,
        profile_label=args.profile,
        schedule_path=schedule,
        account=args.account,
        npz_path=args.npz_path,
    )
    spec_path = out / "research_short_guard_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    override = out / "backtest_only_override.json"
    commission_round_trip_pct = (
        2.0 * float(spec["commission_bps_one_way"]) / 100.0
    )
    override.write_text(
        json.dumps({"ROUND_TRIP_COST_PCT": commission_round_trip_pct}) + "\n"
    )
    audit = out / "exact_engine_audit.json"
    env = os.environ.copy()
    env.update({
        "V8_OVERRIDE_FILE": str(override),
        "V8_SWEEP_MODE": "1",
        "V8_RATE_GUARD_DISABLED": "1",
        "V8_RESEARCH_LADDER_AUDIT_FILE": str(audit),
        "V8_RESULT_FILE": str(out / "v8_result.txt"),
        "V8_RAW_EVENTS_FILE": str(out / "raw_events.jsonl"),
        "V8_TRADES_OUT_DIR": str(out / "chart_trades"),
        "V8_TRADES_RUN_ID": f"research_short_guard_{stamp}",
        "V8_BACKTEST_END_DATE": str(spec["validation_end_exclusive"]),
    })
    command = [
        sys.executable, str(args.engine.resolve()), "--mode", "tradier",
        "--account", args.account, "--start", spec["validation_start"],
        "--symbols", spec["symbol"], "--capital", "10000",
        "--npz-dir", str(Path(spec["npz_path"]).parent),
        "--research-ladder-spec", str(spec_path),
    ]
    proc = subprocess.run(command, env=env, text=True, capture_output=True)
    (out / "engine_stdout.log").write_text(proc.stdout)
    (out / "engine_stderr.log").write_text(proc.stderr)
    audit_payload = json.loads(audit.read_text()) if audit.exists() else None
    summary = {
        "status": "PASS" if proc.returncode == 0 and audit_payload
        and audit_payload["status"] == "PASS" else "FAIL",
        "tier": "V8_EXACT_REPLAY_SHORT_GUARD_PARITY",
        "source_artifact": str(args.artifact.resolve()),
        "output": str(out),
        "returncode": proc.returncode,
        "spec_sha256": sha(spec_path),
        "schedule_sha256": sha(schedule),
        "engine_sha256": sha(args.engine.resolve()),
        "audit": audit_payload,
        "promotion_allowed": False,
        "matrix_written": False,
    }
    (out / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
