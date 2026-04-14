#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""sync_v8_baseline.py — Sync MacBook live config + V8 engine files to Server 2 sandbox ONLY.

PURPOSE: Keep the V8 backtest baseline on Server 2 (/home/niels/binance-sandbox/) in sync
with the MacBook source of truth so that sweep variations are applied on top of current live
config values — not stale copies from weeks ago.

SAFE TO RUN: Only touches s2-int (204.168.181.211 / binance-sandbox).
NEVER touches s1-int (157.180.125.52) — that machine runs LIVE TRADING. push.py is for live.
NEVER restarts any services.

Usage:
    python3 sync_v8_baseline.py            # dry-run (shows what would change)
    python3 sync_v8_baseline.py --execute  # actually sync
    python3 sync_v8_baseline.py --execute --clear-cache  # sync + clear __pycache__ on server

V8 engine intentional divergences from live config (DO NOT "FIX" THESE):
  - DELTA_ENGINE_ENABLED forced True in run_simulation_tradier() regardless of config
  - DELTA_EXIT_ENABLED forced True in run_simulation_tradier()
  - SHOULD_ENTER_FALLBACK_ENABLED = not SATOSHIT_ENTRY_FILTER (test non-SAT regime in backtest)
  - VETO flags (K_ZONE_VETO etc.) activated when sweep sets corresponding knobs
  - Crypto: FAST_CUT_LOSS_THRESHOLD / BREAKOUT_GUARD_LOSS_THRESHOLD / STOP_LOSS_THRESHOLD = 3.0
  - TRA_WT_DC_ENTRY_THRESHOLD mirrored from WT_DC_ENTRY_THRESHOLD when swept
  - Redis regime calls patched to None (no live Redis in backtest)
These divergences are intentional. The BASELINE override file must not fight them.
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

# Server 2 ONLY — sandbox backtesting, no live trading
S2_HOST = "s2-int"
S2_DIR = "/home/niels/binance-sandbox"
S2_PYTHON = "/home/niels/miniconda3/envs/binance_env/bin/python"

# Files that set the BASELINE — live config + shared logic V8 reads from sys.path[0]
BASELINE_FILES = [
    # === Config (most critical — V8 reads these directly) ===
    "config.py",
    "config_tradier.py",
    # === Live trading logic (V8 imports these) ===
    "tradier_manage.py",
    "tradier_indicators.py",
    "tradier_api.py",
    "tradier_positions.py",
    "tradier_rankings.py",
    "ez_manage.py",
    "ez_indicators.py",
    "ez_positions_quick.py",
    "ez_positions_service.py",
    "ez_satoshit.py",
    "utils.py",
]

# V8 engine + sweep infrastructure
V8_FILES = [
    "backtest_v8_engine.py",
    "backtest_v8_sweep.py",
    "backtest_v8_harness.py",
    "v8_test_queue.py",
    "macbook_nightsweep.py",
]

# V5 backtest files (kept in sync so server can run both V5 and V8)
V5_FILES = [
    "backtest_v5_full_tradier.py",
    "backtest_v5_engine.py",
    "backtest_v5_sweep.py",
    "backtest_v5_harness.py",
    "backtest_v5_analyze.py",
    "sweep_cockpit.py",
]

ALL_FILES = BASELINE_FILES + V8_FILES + V5_FILES


def ts():
    return datetime.now().strftime("%H:%M:%S")


def log(msg, icon=""):
    print(f"[{ts()}] {icon} {msg}".strip(), flush=True)


def run(cmd, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def check_reachable():
    rc, _, err = run(f"ssh -o ConnectTimeout=5 {S2_HOST} 'echo ok'", timeout=10)
    if rc != 0:
        log(f"Cannot reach {S2_HOST}: {err}", "❌")
        return False
    return True


def get_server_mtimes(files):
    paths = " ".join(f"{S2_DIR}/{f}" for f in files)
    rc, out, _ = run(f"ssh {S2_HOST} \"stat --format='%n %Y' {paths} 2>/dev/null\"", timeout=30)
    result = {}
    if rc != 0 or not out:
        return result
    for line in out.splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            name = parts[0].replace(f"{S2_DIR}/", "")
            try:
                result[name] = int(parts[1])
            except ValueError:
                pass
    return result


def sync_files(files, execute, dry_run_label="DRY-RUN"):
    missing_local = [f for f in files if not (BASE / f).exists()]
    if missing_local:
        log(f"WARNING: {len(missing_local)} files missing locally, skipping: {missing_local}", "⚠️")
    to_sync = [f for f in files if (BASE / f).exists()]
    if not to_sync:
        log("No files to sync.", "⚠️")
        return 0

    server_mtimes = get_server_mtimes(to_sync)
    changed = []
    unchanged = []
    for f in to_sync:
        local_mtime = int((BASE / f).stat().st_mtime)
        server_mtime = server_mtimes.get(f, 0)
        if local_mtime > server_mtime:
            changed.append((f, local_mtime, server_mtime))
        else:
            unchanged.append(f)

    log(f"Files checked: {len(to_sync)} | Changed: {len(changed)} | Unchanged: {len(unchanged)}", "📊")

    if not changed:
        log("Server is already up to date.", "✅")
        return 0

    log(f"\nFiles to sync ({dry_run_label if not execute else 'SYNCING'}):", "📁")
    for f, lm, sm in changed:
        diff_min = (lm - sm) // 60 if sm else None
        age_str = f"+{diff_min}min" if diff_min is not None else "NEW"
        print(f"  {f}  ({age_str})")

    if not execute:
        log(f"\nDRY-RUN: would sync {len(changed)} file(s). Pass --execute to apply.", "ℹ️")
        return len(changed)

    # Build rsync command for changed files only
    file_list = " ".join(str(BASE / f) for f, _, _ in changed)
    rsync_cmd = (
        f"rsync -avz --checksum {file_list} {S2_HOST}:{S2_DIR}/"
    )
    log(f"Running rsync for {len(changed)} file(s)...", "🔄")
    rc, out, err = run(rsync_cmd, timeout=120)
    if rc != 0:
        log(f"rsync FAILED (rc={rc}): {err}", "❌")
        return -1
    log(f"rsync complete.", "✅")
    if out:
        for line in out.splitlines()[-10:]:
            print(f"  {line}")
    return len(changed)


def clear_pycache(execute):
    cmd = f"ssh {S2_HOST} \"find {S2_DIR} -maxdepth 2 -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null; find {S2_DIR} -maxdepth 2 -name '*.pyc' -delete 2>/dev/null; echo done\""
    if not execute:
        log("DRY-RUN: would clear __pycache__ + *.pyc on server", "ℹ️")
        return
    log("Clearing __pycache__ and .pyc files on server...", "🧹")
    rc, out, err = run(cmd, timeout=30)
    if rc == 0:
        log("Cache cleared.", "✅")
    else:
        log(f"Cache clear warning (rc={rc}): {err}", "⚠️")


def verify_config_keys(execute):
    """Spot-check that critical config values landed on server correctly."""
    if not execute:
        return
    checks = [
        ("config_tradier.py", "RZ_EXIT_ENABLED"),
        ("config_tradier.py", "REENTRY_RALLY_K15M_MAX"),
        ("config_tradier.py", "REENTRY_RALLY_HTF_MIN"),
        ("config.py", "REENTRY_RALLY_K15M_MAX"),
        ("config.py", "REENTRY_RALLY_HTF_MIN"),
        ("config.py", "DELTA_ENTRY_Z_THRESHOLD"),
    ]
    log("Verifying critical config keys on server...", "🔍")
    for cfg, key in checks:
        rc, out, _ = run(f"ssh {S2_HOST} \"grep -n '{key}' {S2_DIR}/{cfg} | head -3\"", timeout=10)
        status = out.strip() if out.strip() else "(not found)"
        print(f"  {cfg}: {key} → {status}")


def main():
    parser = argparse.ArgumentParser(description="Sync V8 baseline to Server 2 sandbox only")
    parser.add_argument("--execute", action="store_true", help="Actually perform the sync (default: dry-run)")
    parser.add_argument("--clear-cache", action="store_true", help="Clear __pycache__ on server after sync")
    parser.add_argument("--verify", action="store_true", help="Spot-check critical config keys on server")
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log(f"sync_v8_baseline.py [{mode}] — target: {S2_HOST}:{S2_DIR}", "🚀")
    log(f"SAFE: Only syncing to Server 2 sandbox. Server 1 (live trading) is NOT touched.", "🛡️")

    if not check_reachable():
        sys.exit(1)

    log("\n--- BASELINE FILES (config + live logic V8 reads) ---", "")
    n_baseline = sync_files(BASELINE_FILES, args.execute)

    log("\n--- V8 ENGINE + SWEEP FILES ---", "")
    n_v8 = sync_files(V8_FILES, args.execute)

    log("\n--- V5 BACKTEST FILES ---", "")
    n_v5 = sync_files(V5_FILES, args.execute)

    if args.clear_cache:
        clear_pycache(args.execute)

    if args.verify or args.execute:
        verify_config_keys(args.execute)

    total = sum(x for x in [n_baseline, n_v8, n_v5] if x > 0)
    log(f"\nDone. {total} file(s) {'synced' if args.execute else 'would be synced'}.", "✅")
    if not args.execute:
        log("Re-run with --execute to apply.", "ℹ️")


if __name__ == "__main__":
    main()
