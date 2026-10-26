#!/usr/bin/env python3
"""Run one isolated 7D row optimization from a verified 30D published set."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_preopen_7d_stage import digest, validate_one  # noqa: E402


def _active_symbols(lines: list[str]) -> set[str]:
    active = set()
    for line in lines:
        match = re.match(r"^\S*python\S*(?: -u)? (?:\S*/)?v15_(?:pilot(?:_7d)?|365_cycle)\.py --sym-side ([A-Z0-9_]+)", line)
        if match:
            active.add(match.group(1))
    return active


@contextmanager
def _host_slot_guard():
    """Hold the shared 30D launch lock for a standalone 7D job."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        available = re.search(r"^MemAvailable:\s+(\d+) kB$", meminfo.read_text(), re.MULTILINE)
        floor_mb = int(os.environ.get("V15_7D_MIN_MEM_MB", "8192"))
        if available and int(available.group(1)) // 1024 < floor_mb:
            raise RuntimeError(f"7D OOM throttle: MemAvailable below {floor_mb} MB")
    if os.environ.get("V15_7D_ALREADY_PAUSED") == "1":
        yield
        return
    with open("/tmp/v15_fleet_launch_v2.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            lines = subprocess.check_output(["ps", "-eo", "args"], text=True).splitlines()
            active = _active_symbols(lines)
            if len(active) >= 4:
                raise RuntimeError(f"7D host full: {len(active)} active sym_sides {sorted(active)}")
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def build_launch(queue: dict, ss: str, result_dir: Path, workers: int, python: str):
    source = (queue.get("sym_sides") or {}).get(ss)
    if not isinstance(source, dict) or not (source.get("manifest_ok") is True or
                                            (source.get("source_kind") == "per_sym_sql" and
                                             source.get("sql_baseline_ok") is True and
                                             source.get("source_db_sha256") and source.get("source_row_sha256"))):
        raise ValueError(f"{ss}: missing verified per-sym SQL baseline")
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
    # The regular pilot's diagnostic repair can consume a further 600 seconds
    # after the row sweep. Bound it for the pre-open deadline without skipping
    # the template row evaluations or the final parity gate.
    env["V15_DIAG_REPAIR_S"] = "150"
    env["V15_ENDGAME_S"] = "120"
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
    overlay_dir = folder / "npz_overlay"
    stock_overlay = str(queue["sym_sides"][ss].get("cat_side", "")).startswith("STOCKS_")
    try:
        if stock_overlay:
            from tools.opt.evaluate_v12 import resolve_tokenised
            from tools.v15_preopen_7d_npz import build_overlay

            symbol, _ = resolve_tokenised(ss.rsplit("_", 1)[0])
            overlay = build_overlay(symbol, overlay_dir)
            env["V12_NPZ_DIR"] = str(overlay_dir)
            env["V15_7D_NPZ_OVERLAY"] = "1"
            (folder / "npz_overlay_manifest.json").write_text(json.dumps(overlay, sort_keys=True) + "\n")
        with _host_slot_guard():
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
                "result_dir": str(folder), "log": str(log),
                "npz_overlay_sha256": overlay.get("overlay_sha256") if stock_overlay else None,
                "premarket_row_count": overlay.get("premarket_row_count") if stock_overlay else None}
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        return {"sym_side": ss, "rc": 2, "valid": False, "why": str(exc),
                "result_dir": str(folder), "log": str(log)}
    finally:
        if stock_overlay:
            shutil.rmtree(overlay_dir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--result-dir", required=True)
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--timeout-sec", type=int, default=2400)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    try:
        result = run_one(Path(a.queue), a.sym_side, Path(a.result_dir), a.workers,
                         a.timeout_sec, a.python)
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"sym_side": a.sym_side, "rc": 2, "valid": False, "why": str(exc)}
    print(json.dumps(result))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
