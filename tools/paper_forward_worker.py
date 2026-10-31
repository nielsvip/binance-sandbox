#!/usr/bin/env python3
"""Run one isolated multi-frame paper scenario through the real scalar engine.

This is a bounded finite replay worker, not a live-feed poller or a promotion tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FRAMES = 512
MAX_INPUT_BYTES = 32 * 1024 * 1024

CHILD = r'''
import asyncio, hashlib, json, os, sys
from pathlib import Path
from tools.paper_worker_isolation import install
from tools.paper_frame_stream import FrameJournal
from tools.paper_snapshot_store import PaperSnapshotStore
from tools.paper_journal_session import JournalForwardSession

root = Path(sys.argv[1]).resolve()
install(root)
request = json.loads((root / "request.json").read_text(encoding="utf-8"))
frames = request["frames"]
symbol = request["symbol"]
mode = request["mode"]
settings = request["full_settings"]
candidate = request["candidate_overrides"]
effective = dict(settings)
effective.update(candidate)

import backtest_v12_engine as engine
code_path = Path(engine.__file__).resolve()
code_hash = hashlib.sha256(code_path.read_bytes()).hexdigest()
settings_hash = hashlib.sha256(json.dumps({"full_settings": settings, "candidate_overrides": candidate}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
symside = request["sym_side"]
override_snapshot = engine._run_one_apply_overrides(effective, mode)
precedence_state = engine._run_one_set_precedence_on(symside, effective, mode)
if precedence_state.get("error"):
    raise RuntimeError("per-symbol settings precedence could not be installed: " + precedence_state["error"])

first = frames[0]
stores = {symbol: PaperSnapshotStore([{"ts": first["ts"], "indicators": first["indicators"]}])}
journal = FrameJournal(root / "receipts.sqlite3", {
    "scenario_id": request["scenario_id"], "sym_side": symside,
    "settings_sha256": settings_hash, "code_sha256": code_hash,
    "candidate_sha256": hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
})
for frame in frames:
    journal.append_frame(frame["ts"], {symbol: frame["indicators"]})

async def apply_frame(payload, stores):
    for frame_symbol, values in payload.items():
        if frame_symbol != symbol:
            raise RuntimeError("scenario frame contains an unexpected symbol")
        stores[frame_symbol].append(current_ts[0], values)

current_ts = [None]
async def tracked_apply(payload, stores):
    current_ts[0] = session._current["ts"]
    await apply_frame(payload, stores)

session = JournalForwardSession(journal, stores, tracked_apply, code_path=code_path, mode=mode)
start_date = datetime.fromtimestamp(float(first["ts"]), timezone.utc).strftime("%Y-%m-%d")
if mode == "crypto":
    trades = asyncio.run(engine.run_simulation("crypto", request.get("account", "ang"), start_date, float(request.get("capital", 10000)), stores, "1m", forward_session=session))
else:
    trades = asyncio.run(engine.run_simulation_tradier(request.get("account", "trb"), start_date, float(request.get("capital", 10000)), stores, "5m", forward_session=session))

receipts = journal.frames()
committed = [row for row in receipts if row["receipt_hash"]]
if len(committed) != len(frames):
    raise RuntimeError("engine returned without committing every frame receipt")
result = {
    "status": "COMPLETED_MULTI_FRAME_ENGINE_RUN",
    "evidence_kind": "independent_forward",
    "scenario_id": request["scenario_id"], "sym_side": symside, "mode": mode,
    "frame_count": len(frames), "committed_receipts": len(committed),
    "settings_sha256": settings_hash, "code_sha256": code_hash,
    "candidate_overrides": candidate,
    "override_issues": list(engine._RUN_ONE_OVERRIDE_ISSUES),
    "store_row_existed": bool(precedence_state.get("store_row_existed")),
    "trades": trades,
    "receipt_db": str(root / "receipts.sqlite3"),
    "promotion_eligible": False,
}
(root / "result.json").write_text(json.dumps(result, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
journal.close()
engine._run_one_restore_overrides(override_snapshot, mode)
engine._run_one_set_precedence_off(precedence_state)
'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read_json(path, limit=MAX_INPUT_BYTES):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")))


def load_frames(path, symbol):
    frames = []
    total = 0
    with Path(path).open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            total += len(raw)
            if total > MAX_INPUT_BYTES:
                raise ValueError("frame stream exceeds 32 MiB")
            if not raw.strip():
                continue
            frame = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")))
            if not isinstance(frame, dict) or set(frame) != {"ts", "indicators"}:
                raise ValueError(f"frame line {line_number} must contain only ts and indicators")
            if isinstance(frame["ts"], bool) or not isinstance(frame["ts"], (int, float)):
                raise ValueError(f"frame line {line_number} timestamp must be epoch seconds")
            ts = float(frame["ts"])
            if not (ts > 0 and ts < float("inf")) or (frames and ts <= frames[-1]["ts"]):
                raise ValueError(f"frame line {line_number} timestamp is invalid or not increasing")
            indicators = frame["indicators"]
            if not isinstance(indicators, dict) or "current_price" not in indicators:
                raise ValueError(f"frame line {line_number} lacks explicit indicators/current_price")
            frames.append({"ts": ts, "indicators": indicators})
            if len(frames) > MAX_FRAMES:
                raise ValueError(f"frame stream exceeds {MAX_FRAMES} frames")
    if len(frames) < 2:
        raise ValueError("independent forward worker requires at least two distinct closed frames")
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sym-side", required=True)
    parser.add_argument("--venue", required=True, choices=("crypto", "stocks"))
    parser.add_argument("--frames", required=True, type=Path, help="NDJSON: one {ts,indicators} object per distinct closed frame")
    parser.add_argument("--full-settings", required=True, type=Path)
    parser.add_argument("--candidate-overrides", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "paper_forward_lab" / "runs")
    parser.add_argument("--account")
    parser.add_argument("--capital", type=float, default=10000)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 1800:
        parser.error("--timeout must be 1..1800 seconds")
    symbol, side = args.sym_side.rsplit("_", 1)
    if side not in {"LONG", "SHORT"} or not symbol:
        parser.error("--sym-side must end in _LONG or _SHORT")
    expected_venue = "crypto" if symbol.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "stocks"
    if expected_venue != args.venue:
        parser.error("venue does not match sym_side symbol convention")
    frames = load_frames(args.frames, symbol)
    full_settings = _read_json(args.full_settings)
    candidate = _read_json(args.candidate_overrides)
    if not isinstance(full_settings, dict) or not full_settings or not isinstance(candidate, dict):
        parser.error("settings and candidate overrides must be JSON objects")
    if any(not isinstance(key, str) for key in (*full_settings, *candidate)):
        parser.error("settings keys must be strings")
    output_root = args.output_root.resolve()
    if not output_root.is_relative_to(ROOT.resolve()):
        parser.error("output root must remain inside the repository workspace")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10])
    run_dir.mkdir(mode=0o700)
    request = {"scenario_id": f"{args.sym_side}:{_digest({ 'settings': full_settings, 'candidate': candidate })[:16]}", "sym_side": args.sym_side, "symbol": symbol, "side": side, "mode": "crypto" if args.venue == "crypto" else "tradier", "full_settings": full_settings, "candidate_overrides": candidate, "frames": frames, "account": args.account or ("ang" if args.venue == "crypto" else "trb"), "capital": args.capital}
    (run_dir / "request.json").write_text(json.dumps(request, sort_keys=True, allow_nan=False), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("V8_", "V12_", "PAPER_"))}
    env.update({"PYTHONHASHSEED": "0", "V8_HASHSEED_LOCKED": "1", "PYTHONDONTWRITEBYTECODE": "1", "V12_BASE_PATH": str(run_dir), "BASE_PATH": str(run_dir), "EZ_LOG_DIR": str(run_dir), "TRADIER_API_LOG_DIR": str(run_dir), "TMPDIR": str(run_dir), "PAPER_FORWARD_ISOLATED": "1", "V8_FORCE_REAL": "1", "V12_LIVE_ONLY_PRODUCERS": "1", "V8_RATE_GUARD_DISABLED": "1", "V8_MAX_WALL_SECS": str(args.timeout), "V8_ISOLATE_SIDE": side, "V8_LADDER_ONLY_SIDE": side, "V8_SWEEP_MODE": "1", "FORWARD_AUTO_PROMOTE": "0"})
    completed = subprocess.run([sys.executable, "-c", CHILD, str(run_dir)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=args.timeout + 15, close_fds=True)
    (run_dir / "worker.stdout.log").write_text(completed.stdout[-20000:], encoding="utf-8")
    (run_dir / "worker.stderr.log").write_text(completed.stderr[-20000:], encoding="utf-8")
    result_path = run_dir / "result.json"
    if completed.returncode or not result_path.exists():
        result = {"status": "FAILED", "returncode": completed.returncode, "run_dir": str(run_dir), "last_error": completed.stderr.splitlines()[-1] if completed.stderr else "worker exited without receipt"}
    else:
        result = _read_json(result_path)
        result["run_dir"] = str(run_dir)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result.get("status") == "COMPLETED_MULTI_FRAME_ENGINE_RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
