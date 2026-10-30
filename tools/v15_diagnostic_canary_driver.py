"""Bounded diagnostic startup, isolated outputs and observed process-tree resources."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--side", required=True)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--attempt", default="attempt_01")
    parser.add_argument("--reserve-mib", type=int, default=20000)
    parser.add_argument("--scratch-budget-mib", type=int, default=256)
    args = parser.parse_args()
    root = args.root.resolve()
    assert root.name.startswith("v15_canary_materialized_")
    assert not any(p.is_symlink() for p in root.rglob("*"))
    symbol, side = args.side.rsplit("_", 1)
    books = json.loads((root / "research/current_books.json").read_text())
    book = root / f"symbols_trb_{side.lower()}.json"
    assert hashlib.sha256(book.read_bytes()).hexdigest() == books[book.name]
    assert symbol in json.loads(book.read_bytes()), "side outside frozen current Mac book"
    available = lambda: int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))) / 1024
    assert available() > 12000, "hold canary for measured memory headroom"
    assert shutil.disk_usage(root).free / 1024 ** 2 >= args.reserve_mib + args.scratch_budget_mib, "hold canary for actual benchmark reserve plus bounded working budget"
    manifest = json.loads((root / "research/materialization.json").read_text())
    env = dict(os.environ)
    for key, value in manifest["source_env"].items():
        if not any(mark in str(value) for mark in ("/", "~")):
            env[key] = str(value)
    if not args.attempt.replace("_", "").isalnum():
        raise ValueError("invalid diagnostic attempt name")
    local = root / "research" / args.attempt
    if local.exists():
        raise ValueError("diagnostic attempt already exists")
    local.mkdir(parents=True)
    env["V15_DIAGNOSTIC_LOG_DIR"] = str(local)
    paths = {"BASE_PATH": root, "V15_OUT_DIR": local / "out", "V15_PROGRESS_DIR": local / "progress", "V15_FLAGS_DIR": local / "flags", "V15_V17_CARRY_DIR": local / "carry", "V15_EVAL_CACHE_DB": local / "eval_cache.db", "PER_SYM_STORE_DB": root / "data/hourly_reconfig/per_sym_store.db", "V15_PREV_BEST_DIR": local / "prev_best", "V15_PRIORITY_DIR": root / "data/cat_avg", "V15_GATE_FLAGS": root / "data/v15_gate_flags.json", "V15_BRAIN_DIR": root / "data/brain", "V15_LEDGER_JSON": root / "data/v15_symside_ledger.json", "V15_ENDGAME_KNOWLEDGE": root / "data/endgame/endgame_knowledge_fleet.json", "SWITCH_PARITY_CACHE_DIR": local / "parity_cache", "TMPDIR": local / "tmp", "NUMBA_CACHE_DIR": local / "numba_cache", "MPLCONFIGDIR": local / "mpl", "XDG_CACHE_HOME": local / "xdg_cache"}
    env.pop("V12_NPZ_DIR", None)
    for key, value in paths.items():
        env[key] = str(value)
        (value.parent if value.suffix else value).mkdir(parents=True, exist_ok=True)
    env.update(V15_ISOLATED_PROOF="1", V15_FRESH_RUN="1", SWITCH_PARITY_REGISTER="0", V15_SKIP_LIVE_AT_DONE="1", V15_FILL_SLICE_S="0", V15_EVAL_CACHE="0", V12_ALLOW_STALE_NPZ="1", V15_LIVE_SNAPSHOT_MAX_AGE_S=str(10 * 366 * 86400), PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMBA_NUM_THREADS="1")
    (local / "launch_env.json").write_text(json.dumps({k: v for k, v in env.items() if k.startswith(("V15_", "V12_", "V18_", "V20_")) or k in paths}, indent=2))
    startup = '''import os, pathlib, sys, runpy, json
root = pathlib.Path(sys.argv[1]).resolve()
audit = open(pathlib.Path(os.environ["V15_DIAGNOSTIC_LOG_DIR"]) / "write_audit.jsonl", "a", buffering=1)
npz_audit = open(pathlib.Path(os.environ["V15_DIAGNOSTIC_LOG_DIR"]) / "npz_reads.jsonl", "a", buffering=1)
def guard(event, args):
    paths = []
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = args[1], args[2]
        read_path = pathlib.Path(os.fsdecode(args[0])).resolve()
        if read_path.suffix == ".npz":
            npz_audit.write(json.dumps({"path": str(read_path), "inside_root": root in read_path.parents}) + "\\n")
            if root not in read_path.parents:
                raise PermissionError("diagnostic NPZ read outside frozen root: " + str(read_path))
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            paths = [args[0]]
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.utime"):
        paths = [args[0]]
    elif event in ("os.rename", "os.link", "os.symlink"):
        paths = list(args[:2])
    elif event == "sqlite3.connect" and args[0] != ":memory:":
        paths = [args[0]]
    for value in paths:
        if isinstance(value, int):
            continue
        path = pathlib.Path(os.fsdecode(value)).resolve()
        if path == pathlib.Path("/dev/null"):
            continue
        if path != root and root not in path.parents:
            audit.write(json.dumps({"event": event, "path": str(path), "refused": True}) + "\\n")
            raise PermissionError("diagnostic write outside materialized root: " + str(path))
sys.addaudithook(guard)
sys.argv = [str(root / "v15_pilot.py")] + sys.argv[2:]
sys.path.insert(0, str(root))
runpy.run_path(str(root / "v15_pilot.py"), run_name="__main__")
'''
    log = open(local / "pilot.log", "wb")
    command = [sys.executable, "-u", "-c", startup, str(root), "--sym-side", args.side, "--template", str(root / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"), "--seq-mode", "worst2best", "--window-days", "30", "--vector-only", "--workers", "1"]
    started = time.monotonic()
    before = stamp()
    process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    samples = []
    hz = os.sysconf("SC_CLK_TCK")
    reason = "completed"
    while process.poll() is None:
        cpu = 0.0
        pss = 0
        count = 0
        for file in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = file.read_text().split()
                if int(fields[4]) != process.pid:
                    continue
                count += 1
                cpu += (int(fields[13]) + int(fields[14])) / hz
                pss += sum(int(line.split()[1]) for line in (file.parent / "smaps_rollup").read_text().splitlines() if line.startswith("Pss:"))
            except (OSError, ValueError):
                pass
        samples.append({"elapsed_s": time.monotonic() - started, "cpu_s_live_processes": cpu, "pss_kb": pss, "process_count": count, "mem_available_mb": available()})
        if time.monotonic() - started >= args.seconds or available() < 6000 or pss > 8 * 1024 ** 2:
            reason = "bounded_startup_timeout" if time.monotonic() - started >= args.seconds else "resource_guard_own_canary_only"
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            break
        time.sleep(0.5)
    process.wait()
    log.close()
    report = {"started_at_utc": before, "ended_at_utc": stamp(), "side": args.side, "attempt": args.attempt, "pid": process.pid, "returncode": process.returncode, "stop_reason": reason, "reserve_mib": args.reserve_mib, "scratch_budget_mib": args.scratch_budget_mib, "wall_s": time.monotonic() - started, "sampled_peak_pss_kb": max((x["pss_kb"] for x in samples), default=0), "sampled_max_live_tree_cpu_s": max((x["cpu_s_live_processes"] for x in samples), default=0), "samples": samples, "limits": ["Startup-only diagnostic, not a complete board or speed proof", "CPU excludes processes already reaped before sampling", "Memory peak sampled at0.5s, transient peaks may be missed", "Python audit hook covers audited Python operations, not all native syscalls", "No production deployment or promotion"]}
    (local / "canary_resources.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}), flush=True)


if __name__ == "__main__":
    main()
