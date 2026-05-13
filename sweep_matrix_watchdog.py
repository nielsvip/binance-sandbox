#!/usr/bin/env python3
"""
sweep_matrix_watchdog.py — Sweep queue manager for backtest_v8_sweep.py.

Reads sweep_matrix.json, monitors running sweeps for stalls, kills stalled
processes, advances the queue to the next pending sweep, and writes error
reports for human/agent diagnosis.

Designed to be invoked by cron every 5 minutes:
  */5 * * * * cd /home/niels/binance-sandbox && \
    /home/niels/.conda/envs/binance_env/bin/python sweep_matrix_watchdog.py \
    >> /home/niels/logs/sweep_matrix_watchdog.log 2>&1

Usage:
  python sweep_matrix_watchdog.py           # live run
  python sweep_matrix_watchdog.py --dry-run # inspect only, no kills or launches
"""
import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# ── paths ──────────────────────────────────────────────────────────────────
SANDBOX_DIR = "/home/niels/binance-sandbox"
MATRIX_PATH = os.path.join(SANDBOX_DIR, "sweep_matrix.json")
SWEEP_RESULTS_DIR = os.path.join(SANDBOX_DIR, "data/sweep_results")
SWEEP_ERRORS_DIR = os.path.join(SANDBOX_DIR, "data/sweep_errors")
STATUS_FILE = os.path.join(SANDBOX_DIR, "data/sweep_matrix_status.txt")
LOGS_DIR = "/home/niels/logs"
PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"

# ── thresholds ─────────────────────────────────────────────────────────────
STALL_TIMEOUT_S = 1200    # 20 min: one variant × safety factor
MEM_GUARD_PCT = 85.0      # refuse to launch new sweep if mem used >= this
MAX_RETRIES = 2           # stalled sweep may be re-queued at most this many times


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _ts_hhmm() -> str:
    return datetime.now(timezone.utc).strftime("%H%M%S")


def _log(msg: str):
    print(f"[{_now_utc()}] {msg}", flush=True)


# ── matrix I/O ─────────────────────────────────────────────────────────────

def load_matrix() -> dict:
    with open(MATRIX_PATH) as fh:
        return json.load(fh)


def save_matrix(matrix: dict):
    tmp = MATRIX_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(matrix, fh, indent=2)
    os.replace(tmp, MATRIX_PATH)


# ── process helpers ─────────────────────────────────────────────────────────

def _find_sweep_procs(tier: str) -> list[dict]:
    """Return list of {pid, cmdline} for backtest_v8_sweep processes matching tier."""
    results = []
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", f"backtest_v8_sweep.*{tier}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return results
    for line in out.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            try:
                pid = int(parts[0])
                results.append({"pid": pid, "cmdline": parts[1]})
            except ValueError:
                pass
    return results


def _find_procs_by_mode(mode: str) -> list[dict]:
    """Return all backtest_v8_sweep procs for a given mode."""
    results = []
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", f"backtest_v8_sweep.*--mode {mode}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return results
    for line in out.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            try:
                pid = int(parts[0])
                results.append({"pid": pid, "cmdline": parts[1]})
            except ValueError:
                pass
    return results


def _kill_procs(procs: list[dict], dry_run: bool) -> list[int]:
    """SIGKILL a list of {pid, cmdline} process dicts; return killed pids."""
    killed = []
    for p in procs:
        pid = p["pid"]
        if dry_run:
            _log(f"  DRY-RUN: would kill PID {pid} ({p['cmdline'][:80]})")
        else:
            try:
                os.kill(pid, signal.SIGKILL)
                _log(f"  KILLED PID {pid}")
                killed.append(pid)
            except ProcessLookupError:
                _log(f"  PID {pid} already gone")
            except PermissionError as exc:
                _log(f"  CANNOT KILL PID {pid}: {exc}")
    return killed


# ── CSV helpers ─────────────────────────────────────────────────────────────

def _latest_csv_for_tier(tier: str) -> str | None:
    """Return path of the most recently modified CSV for the given tier."""
    pattern = os.path.join(SWEEP_RESULTS_DIR, f"backtest_v8_sweep_{tier}_*.csv")
    candidates = [p for p in glob.glob(pattern) if not p.endswith(".version.json")]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _csv_row_count(path: str) -> int:
    """Count data rows (excluding header) in a CSV."""
    try:
        with open(path) as fh:
            lines = [l for l in fh if l.strip()]
        return max(0, len(lines) - 1)
    except OSError:
        return 0


def _csv_last_modified(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ── log tail helper ─────────────────────────────────────────────────────────

def _latest_log_tail(tier: str, n: int = 20) -> str:
    """Return last N lines of the most recent sweep log for this tier."""
    pattern = os.path.join(LOGS_DIR, f"*{tier}*")
    candidates = glob.glob(pattern)
    if not candidates:
        pattern2 = os.path.join(LOGS_DIR, f"bt_sweep_{tier}*")
        candidates = glob.glob(pattern2)
    if not candidates:
        return "(no log found)"
    latest = max(candidates, key=os.path.getmtime)
    try:
        with open(latest) as fh:
            lines = fh.readlines()
        tail = lines[-n:] if len(lines) >= n else lines
        return "".join(tail).strip()
    except OSError as exc:
        return f"(could not read {latest}: {exc})"


# ── memory guard ────────────────────────────────────────────────────────────

def _mem_used_pct() -> float:
    """Return percentage of RAM currently in use (0–100)."""
    try:
        out = subprocess.check_output(["free", "-m"], text=True)
        for line in out.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                total, used = int(parts[1]), int(parts[2])
                return 100.0 * used / total if total else 0.0
    except Exception:
        pass
    return 0.0


# ── error report writer ─────────────────────────────────────────────────────

def _write_error_report(sweep: dict, csv_path: str | None, last_mtime: float, procs: list[dict]):
    os.makedirs(SWEEP_ERRORS_DIR, exist_ok=True)
    ts = _ts_hhmm()
    report_path = os.path.join(SWEEP_ERRORS_DIR, f"{sweep['id']}_{ts}.log")
    last_csv_at = (
        datetime.fromtimestamp(last_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        if last_mtime
        else "UNKNOWN"
    )
    rows = _csv_row_count(csv_path) if csv_path else 0
    proc_cmdline = procs[0]["cmdline"] if procs else "(not found)"
    log_tail = _latest_log_tail(sweep["tier"])
    report = (
        f"SWEEP_STALL_REPORT\n"
        f"id: {sweep['id']}\n"
        f"tier: {sweep['tier']}\n"
        f"mode: {sweep['mode']}\n"
        f"detected_at: {_now_utc()}\n"
        f"last_csv_row_at: {last_csv_at}\n"
        f"csv_path: {csv_path or 'NONE'}\n"
        f"csv_rows: {rows}\n"
        f"process_cmdline: {proc_cmdline}\n"
        f"last_log_lines:\n{log_tail}\n"
        f"FIX_NEEDED: [human or agent to diagnose and either fix the tier config or restart with different params]\n"
    )
    with open(report_path, "w") as fh:
        fh.write(report)
    _log(f"  Error report written: {report_path}")
    return report_path


# ── launcher ────────────────────────────────────────────────────────────────

def _launch_sweep(sweep: dict, dry_run: bool) -> bool:
    """Launch a sweep subprocess using the canonical nohup+disown pattern."""
    ts = _ts_compact()
    log_file = os.path.join(LOGS_DIR, f"sweep_{sweep['tier']}_{ts}.log")
    cmd_parts = [
        PYTHON,
        "backtest_v8_sweep.py",
        "--mode", sweep["mode"],
        "--account", sweep["account"],
        "--start", sweep["start"],
        "--symbols", sweep["symbols"],
        "--tier", sweep["tier"],
        "--workers", str(sweep["workers"]),
        "--timeout", str(sweep["timeout"]),
        "--mem-throttle-pct", str(int(MEM_GUARD_PCT)),
    ]
    shell_cmd = (
        f"cd {SANDBOX_DIR} && "
        f"nohup env V8_RATE_GUARD_DISABLED=1 V8_DISABLE_RELAXED_SRS=1 "
        + " ".join(cmd_parts)
        + f" > {log_file} 2>&1 < /dev/null & disown"
    )
    if dry_run:
        _log(f"  DRY-RUN: would launch: {shell_cmd}")
        return True
    _log(f"  Launching: {' '.join(cmd_parts)}")
    _log(f"  Log: {log_file}")
    ret = subprocess.call(["bash", "-c", shell_cmd])
    if ret != 0:
        _log(f"  WARNING: launch shell returned {ret}")
    time.sleep(5)
    procs = _find_sweep_procs(sweep["tier"])
    if procs:
        _log(f"  Verified running: {len(procs)} proc(s) found")
        return True
    _log(f"  WARNING: no procs found after launch — may have failed silently")
    return False


# ── status writer ────────────────────────────────────────────────────────────

def _write_status(matrix: dict, actions: list[str]):
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    lines = [
        f"sweep_matrix_status  updated={_now_utc()}",
        "-" * 60,
    ]
    for sw in matrix["sweeps"]:
        csv_path = _latest_csv_for_tier(sw["tier"])
        rows = _csv_row_count(csv_path) if csv_path else 0
        lines.append(
            f"  [{sw['status']:10s}] id={sw['id']}  mode={sw['mode']}  tier={sw['tier']}"
            f"  csv_rows={rows}  retries={sw['retries']}"
        )
    lines.append("-" * 60)
    lines.append("ACTIONS THIS RUN:")
    for a in actions or ["(none)"]:
        lines.append(f"  {a}")
    with open(STATUS_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# ── main logic ───────────────────────────────────────────────────────────────

def run(dry_run: bool):
    os.makedirs(SWEEP_ERRORS_DIR, exist_ok=True)
    _log(f"sweep_matrix_watchdog START  dry_run={dry_run}")

    matrix = load_matrix()
    sweeps = matrix["sweeps"]
    actions: list[str] = []
    now = time.time()

    # ── Step 1: check running sweeps for stalls ──────────────────────────
    for sw in sweeps:
        if sw["status"] != "running":
            continue
        tier = sw["tier"]
        csv_path = _latest_csv_for_tier(tier)
        procs = _find_sweep_procs(tier)

        if not procs:
            _log(f"  [{tier}] status=running but NO process found — marking stalled")
            sw["status"] = "stalled"
            sw["error"] = f"No process found at {_now_utc()}"
            actions.append(f"STALL(no-proc): {sw['id']}")
            _write_error_report(sw, csv_path, _csv_last_modified(csv_path) if csv_path else 0, [])
            continue

        last_mtime = _csv_last_modified(csv_path) if csv_path else 0.0
        age_s = now - last_mtime if last_mtime else now

        _log(
            f"  [{tier}] procs={len(procs)}  csv={'YES' if csv_path else 'NONE'}"
            f"  last_row_age={age_s:.0f}s  stall_threshold={STALL_TIMEOUT_S}s"
        )

        if age_s >= STALL_TIMEOUT_S:
            _log(f"  [{tier}] STALLED — last CSV row {age_s:.0f}s ago >= {STALL_TIMEOUT_S}s")
            report = _write_error_report(sw, csv_path, last_mtime, procs)
            killed = _kill_procs(procs, dry_run)
            sw["status"] = "stalled"
            sw["error"] = (
                f"Stall detected at {_now_utc()}: last CSV mtime {age_s:.0f}s ago. "
                f"Killed PIDs: {killed}. Report: {report}"
            )
            actions.append(f"STALL+KILL: {sw['id']}  pids={[p['pid'] for p in procs]}")
        else:
            _log(f"  [{tier}] OK — last row {age_s:.0f}s ago")

    if not dry_run:
        save_matrix(matrix)

    # ── Step 2: decide running counts per mode ──────────────────────────
    running_modes: dict[str, int] = {}
    for sw in sweeps:
        if sw["status"] == "running":
            running_modes[sw["mode"]] = running_modes.get(sw["mode"], 0) + 1

    _log(f"  Running mode counts: {running_modes}")

    # ── Step 3: launch pending sweeps where slot is free ────────────────
    mem_pct = _mem_used_pct()
    _log(f"  Memory used: {mem_pct:.1f}%  guard={MEM_GUARD_PCT}%")

    if mem_pct >= MEM_GUARD_PCT:
        _log(f"  MEM_GUARD: {mem_pct:.1f}% >= {MEM_GUARD_PCT}% — skipping all launches")
        actions.append(f"MEM_GUARD: {mem_pct:.1f}% — no launches")
    else:
        for sw in sweeps:
            if sw["status"] != "pending":
                continue
            mode = sw["mode"]
            if running_modes.get(mode, 0) >= 1:
                _log(f"  [{sw['tier']}] pending but {mode} already has a running sweep — skip")
                continue
            _log(f"  [{sw['tier']}] LAUNCHING (mode={mode}, account={sw['account']})")
            launched = _launch_sweep(sw, dry_run)
            if launched:
                if not dry_run:
                    sw["status"] = "running"
                running_modes[mode] = running_modes.get(mode, 0) + 1
                actions.append(f"LAUNCHED: {sw['id']}")
            else:
                sw["error"] = f"Launch failed at {_now_utc()}"
                actions.append(f"LAUNCH_FAILED: {sw['id']}")
            if not dry_run:
                save_matrix(matrix)

    # ── Step 4: re-queue stalled sweeps if retries remain ───────────────
    for sw in sweeps:
        if sw["status"] != "stalled":
            continue
        if sw["retries"] >= MAX_RETRIES:
            _log(f"  [{sw['tier']}] stalled but max retries ({MAX_RETRIES}) exhausted — marking failed")
            sw["status"] = "failed"
            actions.append(f"FAILED(max_retries): {sw['id']}")
            continue
        sw["retries"] += 1
        sw["status"] = "pending"
        sw["error"] = sw.get("error", "") + f" | re-queued attempt {sw['retries']}"
        _log(f"  [{sw['tier']}] re-queued as pending (retry {sw['retries']}/{MAX_RETRIES})")
        actions.append(f"REQUEUE(retry={sw['retries']}): {sw['id']}")

    if not dry_run:
        save_matrix(matrix)

    # ── Step 5: write status summary ────────────────────────────────────
    _write_status(matrix, actions)

    # ── Summary ─────────────────────────────────────────────────────────
    _log("=" * 60)
    _log(f"sweep_matrix_watchdog DONE  actions={len(actions)}")
    for a in actions:
        _log(f"  ACTION: {a}")
    counts = {}
    for sw in sweeps:
        counts[sw["status"]] = counts.get(sw["status"], 0) + 1
    _log(f"  Matrix status counts: {counts}")
    _log("=" * 60)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Inspect only — no kills, no launches, no matrix writes")
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
