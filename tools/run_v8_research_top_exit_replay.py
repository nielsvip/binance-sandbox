#!/usr/bin/env python3
"""Run one frozen VEC_RESEARCH schedule through backtest_v8_engine.

The runner writes only beneath data/reports/vec_research.  It never writes the
switch matrix, config, symbol universes, or any live state.  The engine adapter
is enabled solely by the explicit --research-top-exit-spec argument.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from v8_research_top_exit_adapter import build_spec_from_artifact


ROOT = Path(__file__).resolve().parents[1]
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
    ap.add_argument("--seed-notional", type=float, default=2_000.0)
    args = ap.parse_args()

    artifact = args.artifact.resolve()
    manifest = json.loads((artifact / "run_manifest.json").read_text())
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (
        args.output_root.resolve()
        / f"v8_exact_replay_{run_id}_{manifest['symbol']}_{manifest['side']}"
    )
    out.mkdir(parents=True, exist_ok=False)

    spec = build_spec_from_artifact(
        artifact,
        account=args.account,
        seed_notional_usd=args.seed_notional,
    )
    spec_path = out / "research_top_exit_spec.json"
    spec_path.write_text(json.dumps(spec, sort_keys=True, indent=2) + "\n")
    override_path = out / "backtest_only_override.json"
    override_path.write_text(
        json.dumps({"ROUND_TRIP_COST_PCT": 0.10}, sort_keys=True, indent=2) + "\n"
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
            "V8_RESEARCH_TOP_EXIT_AUDIT_FILE": str(audit_path),
            "V8_RESULT_FILE": str(result_path),
            "V8_RAW_EVENTS_FILE": str(raw_path),
            "V8_TRADES_OUT_DIR": str(out / "chart_trades"),
            "V8_TRADES_RUN_ID": f"research_top_exit_{run_id}",
        }
    )
    end_epoch = int(manifest["end_epoch"])
    end_date = datetime.fromtimestamp(end_epoch, tz=timezone.utc).date() + timedelta(days=1)
    env["V8_BACKTEST_END_DATE"] = end_date.isoformat()
    command = [
        sys.executable,
        str(args.engine.resolve()),
        "--mode",
        "tradier",
        "--account",
        args.account,
        "--start",
        manifest["start"],
        "--symbols",
        manifest["symbol"],
        "--capital",
        str(args.capital),
        "--npz-dir",
        str(Path(manifest["npz_path"]).resolve().parent),
        "--research-top-exit-spec",
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
            if proc.returncode == 0 and audit is not None and audit["status"] == "PASS"
            else "FAIL"
        ),
        "tier": "VEC_RESEARCH_EXACT_ENGINE_ROUTE_SMOKE",
        "source_artifact": str(artifact),
        "output": str(out),
        "command": command,
        "returncode": proc.returncode,
        "engine_sha256": _sha256(args.engine.resolve()),
        "adapter_sha256": _sha256(
            ROOT / "tools" / "v8_research_top_exit_adapter.py"
        ),
        "spec_sha256": _sha256(spec_path),
        "audit": audit,
        "matrix_written": False,
        "signal_parity": False,
        "promotion_allowed": False,
    }
    (out / "run_summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n"
    )
    print("V8_RESEARCH_TOP_EXIT_RUNNER: " + json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
