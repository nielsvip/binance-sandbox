#!/usr/bin/env python3
"""Bounded isolated real-engine startup probe; never forward performance evidence."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHILD = '''
import sys,json,hashlib,asyncio,time
from pathlib import Path
from tools.paper_worker_isolation import install
root=Path(sys.argv[1]); install(root)
import backtest_v12_engine as engine
from tools.paper_snapshot_store import PaperSnapshotStore
from tools.paper_frame_stream import FrameJournal
from tools.paper_journal_session import JournalForwardSession
symbol=sys.argv[2]; mode=sys.argv[3]
values=json.loads((root/"input.json").read_text())
ts=float(int(time.time()))
stores={symbol:PaperSnapshotStore([{"ts":ts,"indicators":values}])}
identity={"scenario_id":"startup-diagnostic-only","settings_sha256":hashlib.sha256(b"startup-defaults-unverified").hexdigest(),"code_sha256":hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest()}
journal=FrameJournal(root/"frames.db",identity)
journal.append_frame(ts,{symbol:values})
async def apply_frame(payload,stores):
    for symbol,values in payload.items():
        stores[symbol].append(ts,values)
session=JournalForwardSession(journal,stores,apply_frame,code_path=engine.__file__,mode=mode)
if mode=="crypto":
    asyncio.run(engine.run_simulation("crypto","ang","2026-10-31",10000,stores,"1m",forward_session=session))
else:
    asyncio.run(engine.run_simulation_tradier("trb","2026-10-31",10000,stores,"5m",forward_session=session))
frames=journal.frames()
(root/"probe-result.json").write_text(json.dumps({"receipts":sum(row['receipt_hash'] is not None for row in frames),"frame_count":len(frames),"state":"DIAGNOSTIC_ONLY","settings_verified":False}))
journal.close()
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--venue", choices=("crypto", "stocks"), required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("timeout must be 1..60 seconds")
    with args.snapshot.open("rb") as handle:
        raw = handle.read(32 * 1024**2 + 1)
    if len(raw) > 32 * 1024**2:
        raise ValueError("snapshot exceeds 32 MiB bounded reader")
    values = json.loads(raw)[args.symbol]
    report = {"observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "scope": "ONE_FRAME_STARTUP_PROBE", "venue": args.venue, "symbol": args.symbol, "source": str(args.snapshot), "promotion_eligible": False, "trading_parity_verified": False}
    with tempfile.TemporaryDirectory(prefix="paper_engine_probe_") as folder:
        folder = Path(folder)
        (folder / "input.json").write_text(json.dumps(values, allow_nan=False))
        env = {key: value for key, value in os.environ.items() if not key.startswith(("V8_", "V12_", "PAPER_"))}
        env.update(PYTHONHASHSEED="0", V8_HASHSEED_LOCKED="1", PYTHONDONTWRITEBYTECODE="1", V12_BASE_PATH=str(folder), BASE_PATH=str(folder), EZ_LOG_DIR=str(folder), TRADIER_API_LOG_DIR=str(folder), TMPDIR=str(folder), PAPER_FORWARD_ISOLATED="1", V8_FORCE_REAL="1", V12_LIVE_ONLY_PRODUCERS="1", V8_RATE_GUARD_DISABLED="1", V8_MAX_WALL_SECS="10", V8_ISOLATE_SIDE="LONG", V8_LADDER_ONLY_SIDE="LONG", FORWARD_AUTO_PROMOTE="0")
        try:
            completed = subprocess.run([args.python, "-c", CHILD, str(folder), args.symbol, "crypto" if args.venue == "crypto" else "tradier"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=args.timeout, close_fds=True)
            report["returncode"] = completed.returncode
            result = folder / "probe-result.json"
            if completed.returncode == 0 and result.exists():
                report["result"] = json.loads(result.read_text())
            else:
                report["last_error"] = completed.stderr.splitlines()[-1] if completed.stderr else "no completion receipt"
        except subprocess.TimeoutExpired:
            report["error"] = "STARTUP_TIMEOUT"
            report["timeout_seconds"] = args.timeout
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(rendered)
    print(rendered, end="")
    return 0 if report.get("result", {}).get("receipts") == 1 else 1

if __name__ == "__main__":
    sys.exit(main())
