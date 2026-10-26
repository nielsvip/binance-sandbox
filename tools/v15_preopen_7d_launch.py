#!/usr/bin/env python3
"""Run one isolated 7D row optimization from a verified 30D published set."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_preopen_7d_stage import digest, validate_one  # noqa: E402


def build_launch(queue: dict, ss: str, result_dir: Path, workers: int, python: str):
    source = (queue.get("sym_sides") or {}).get(ss)
    if not isinstance(source, dict) or source.get("manifest_ok") is not True:
        raise ValueError(f"{ss}: missing verified published 30D set")
    overrides = source.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError(f"{ss}: empty 30D start set")
    cat = source.get("cat_side")
    if cat not in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        raise ValueError(f"{ss}: unknown category {cat!r}")
    if ss not in queue.get("accounts", {}).get("fin", []) and ss not in queue.get("accounts", {}).get("trc", []):
        raise ValueError(f"{ss}: not in FIN/TRC 7D queue")
    folder = result_dir / ss
    out = folder / "out"
    progress = folder / "progress"
    out.mkdir(parents=True, exist_ok=True)
    progress.mkdir(parents=True, exist_ok=True)
    start = folder / "start_30d.json"
    start.write_text(json.dumps(overrides, sort_keys=True, default=str) + "\n")
    template = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cat}.xlsx"
    if not template.is_file():
        raise ValueError(f"{ss}: missing template {template}")
    pilot_name = os.environ.get("V15_7D_PILOT_FILE", "v15_pilot.py")
    if pilot_name not in ("v15_pilot.py", "v15_pilot_7d.py") or not (ROOT / pilot_name).is_file():
        raise ValueError(f"{ss}: invalid 7D pilot file {pilot_name!r}")
    env = os.environ.copy()
    env.update({"V15_OUT_DIR": str(out), "V15_PROGRESS_DIR": str(progress),
                "V15_START_OVERRIDES": str(start), "V15_7D_SOURCE_SHA256": digest(overrides),
                "V15_FRESH_RUN": "1", "V15_INGEST_BEST": "1", "V15_PAIR_MODE": "0",
                "V15_ISOLATED_PROOF": "1", "V15_SKIP_CAT_PERSYM_BASELINE": "1",
                "V15_PREV_BEST_START": "0", "V15_ADAPT_BASELINE": "0",
                "V15_OTHER_SIDE_PROBE_RATE": "0", "V15_SKIP_LIVE_VERIFY": "1"})
    # The final live-engine parity run remains enabled. It is the promotion gate.
    env.pop("V15_SKIP_LIVE_AT_DONE", None)
    command = [python, "-u", str(ROOT / pilot_name), "--sym-side", ss,
               "--template", str(template), "--out", str(out / f"{ss}_7d_matrix.xlsx"),
               "--window-days", "7", "--workers", str(workers), "--vector-only",
               "--seq-mode", "worst2best"]
    return command, env, folder


def run_one(queue_path: Path, ss: str, result_dir: Path, workers: int,
            timeout_sec: int, python: str) -> dict:
    queue = json.loads(queue_path.read_text())
    command, env, folder = build_launch(queue, ss, result_dir, workers, python)
    log = folder / "pilot.log"
    with log.open("ab") as f:
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            rc = 124
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        result = validate_one(ss, queue["sym_sides"][ss], result_dir, today)
        valid = True
        why = None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        valid = False
        why = str(exc)
    return {"sym_side": ss, "rc": rc, "valid": valid and rc == 0,
            "why": why if why else (f"pilot rc={rc}" if rc else None),
            "result_dir": str(folder), "log": str(log)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--result-dir", required=True)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--timeout-sec", type=int, default=2400)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    result = run_one(Path(a.queue), a.sym_side, Path(a.result_dir), a.workers,
                     a.timeout_sec, a.python)
    print(json.dumps(result))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
