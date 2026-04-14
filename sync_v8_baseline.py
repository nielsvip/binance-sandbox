#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""sync_v8_baseline.py — Sync MacBook live config + V8 engine files to backtest servers.

SERVER ROLES (ABSOLUTE — DO NOT CONFUSE):
  MacBook (local)              = ALL live trading (crypto + stocks). Source of truth.
  Server 1  s1-int 157.180.125.52  /home/niels/binance/           = CRYPTO backtest ONLY
  Server 2  s2-int 204.168.181.211 /home/niels/binance-sandbox/   = STOCKS backtest ONLY

NO LIVE TRADING runs on either server. This script NEVER restarts any services.
push.py is for live deployment and must NEVER be used here.

What this script does:
  - Syncs crypto baseline (config.py + ez_manage + V8 engine) → Server 1
  - Syncs stocks baseline (config_tradier.py + tradier_manage + V8 engine) → Server 2
  - Common files (utils.py, backtest infra) go to both

Usage:
    python3 sync_v8_baseline.py            # dry-run (show what would change)
    python3 sync_v8_baseline.py --execute  # actually sync both servers
    python3 sync_v8_baseline.py --s1-only  # dry-run Server 1 (crypto) only
    python3 sync_v8_baseline.py --s2-only  # dry-run Server 2 (stocks) only
    python3 sync_v8_baseline.py --execute --s1-only   # sync Server 1 only
    python3 sync_v8_baseline.py --execute --clear-cache  # sync + clear __pycache__

V8 engine intentional divergences from live config (DO NOT "FIX" THESE):
  - DELTA_ENGINE_ENABLED forced True in run_simulation_tradier() regardless of config
  - DELTA_EXIT_ENABLED forced True in run_simulation_tradier()
  - SHOULD_ENTER_FALLBACK_ENABLED = not SATOSHIT_ENTRY_FILTER (test non-SAT regime in backtest)
  - VETO flags (K_ZONE_VETO etc.) activated when sweep sets corresponding knobs
  - Crypto: FAST_CUT_LOSS_THRESHOLD / BREAKOUT_GUARD_LOSS_THRESHOLD / STOP_LOSS_THRESHOLD = 3.0
  - TRA_WT_DC_ENTRY_THRESHOLD mirrored from WT_DC_ENTRY_THRESHOLD when swept
  - Redis regime calls patched to None (no live Redis in backtest)
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

# --- Server 1: CRYPTO backtest only ---
S1_HOST = "s1-int"
S1_DIR = "/home/niels/binance"
S1_PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"

# --- Server 2: STOCKS backtest only ---
S2_HOST = "s2-int"
S2_DIR = "/home/niels/binance-sandbox"
S2_PYTHON = "/home/niels/miniconda3/envs/binance_env/bin/python"

# Files synced to Server 1 (crypto backtest baseline)
S1_FILES = [
    # Crypto config — V8 reads this from sys.path[0]
    "config.py",
    # Crypto live logic — V8 imports these
    "ez_manage.py",
    "ez_indicators.py",
    "ez_positions_quick.py",
    "ez_positions_service.py",
    "ez_satoshit.py",
    # Shared
    "utils.py",
    # V8 engine + sweep infra
    "backtest_v8_engine.py",
    "backtest_v8_sweep.py",
    "backtest_v8_harness.py",
    "v8_test_queue.py",
    "macbook_nightsweep.py",
    # V5 crypto backtest
    "backtest_v5_engine.py",
    "backtest_v5_sweep.py",
    "backtest_v5_harness.py",
    "backtest_v5_analyze.py",
    "sweep_cockpit.py",
]

# Files synced to Server 2 (stocks backtest baseline)
S2_FILES = [
    # Stocks config — V8 reads this from sys.path[0]
    "config_tradier.py",
    # config.py: NO tradier settings live here — the two configs are completely separate.
    # Required ONLY because backtest_v8_engine.py has a hard top-level `import config`
    # at line 69 that runs before any mode check. Engine crashes without it on S2.
    # Tradier simulation reads 100% from config_tradier.py / tradier_manage.config.
    "config.py",
    # Stocks live logic — V8 imports these
    "tradier_manage.py",
    "tradier_indicators.py",
    "tradier_api.py",
    "tradier_positions.py",
    "tradier_rankings.py",
    # Shared
    "utils.py",
    # V8 engine + sweep infra
    "backtest_v8_engine.py",
    "backtest_v8_sweep.py",
    "backtest_v8_harness.py",
    "v8_test_queue.py",
    "macbook_nightsweep.py",
    # V5 stocks backtest
    "backtest_v5_full_tradier.py",
    "backtest_v5_sweep.py",
    "backtest_v5_harness.py",
    "backtest_v5_analyze.py",
    "sweep_cockpit.py",
]

# Keys spot-checked after sync to confirm critical values landed
S1_VERIFY_KEYS = [
    ("config.py", "REENTRY_RALLY_K15M_MAX"),
    ("config.py", "REENTRY_RALLY_HTF_MIN"),
    ("config.py", "DELTA_ENTRY_Z_THRESHOLD"),
]
S2_VERIFY_KEYS = [
    ("config_tradier.py", "RZ_EXIT_ENABLED"),
    ("config_tradier.py", "REENTRY_RALLY_K15M_MAX"),
    ("config_tradier.py", "REENTRY_RALLY_HTF_MIN"),
    ("config.py", "REENTRY_RALLY_K15M_MAX"),
]


def ts():
    return datetime.now().strftime("%H:%M:%S")


def log(msg, icon=""):
    print(f"[{ts()}] {icon} {msg}".strip(), flush=True)


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"timed out after {timeout}s"


def check_reachable(host):
    rc, _, err = run(f"ssh -o ConnectTimeout=5 {host} 'echo ok'", timeout=12)
    if rc != 0:
        log(f"Cannot reach {host}: {err}", "❌")
        return False
    return True


def get_server_mtimes(host, remote_dir, files):
    paths = " ".join(f"{remote_dir}/{f}" for f in files)
    rc, out, _ = run(f"ssh {host} \"stat --format='%n %Y' {paths} 2>/dev/null\"", timeout=30)
    result = {}
    if rc != 0 or not out:
        return result
    for line in out.splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            name = parts[0].replace(f"{remote_dir}/", "")
            try:
                result[name] = int(parts[1])
            except ValueError:
                pass
    return result


def sync_to_server(label, host, remote_dir, files, execute, clear_cache, verify_keys):
    log(f"\n{'='*60}", "")
    log(f"{label}  →  {host}:{remote_dir}", "🖥️")
    log(f"{'='*60}", "")

    if not check_reachable(host):
        return False

    missing = [f for f in files if not (BASE / f).exists()]
    if missing:
        log(f"WARNING: {len(missing)} file(s) missing locally, skipping: {missing}", "⚠️")
    to_sync = [f for f in files if (BASE / f).exists()]
    if not to_sync:
        log("No files to sync.", "⚠️")
        return True

    server_mtimes = get_server_mtimes(host, remote_dir, to_sync)
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
        log("Server already up to date.", "✅")
    else:
        mode_str = "SYNCING" if execute else "DRY-RUN"
        log(f"Files to sync [{mode_str}]:", "📁")
        for f, lm, sm in changed:
            diff_min = (lm - sm) // 60 if sm else None
            age_str = f"+{diff_min}min" if diff_min is not None else "NEW"
            print(f"  {f}  ({age_str})")

        if not execute:
            log(f"DRY-RUN: {len(changed)} file(s) would be synced. Pass --execute to apply.", "ℹ️")
        else:
            file_list = " ".join(str(BASE / f) for f, _, _ in changed)
            rc, out, err = run(f"rsync -avz --checksum {file_list} {host}:{remote_dir}/", timeout=120)
            if rc != 0:
                log(f"rsync FAILED (rc={rc}): {err}", "❌")
                return False
            log(f"rsync complete — {len(changed)} file(s) synced.", "✅")
            for line in out.splitlines()[-8:]:
                if line.strip():
                    print(f"  {line}")

    if clear_cache:
        cmd = (
            f"ssh {host} \"find {remote_dir} -maxdepth 2 -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null; "
            f"find {remote_dir} -maxdepth 2 -name '*.pyc' -delete 2>/dev/null; echo done\""
        )
        if not execute:
            log("DRY-RUN: would clear __pycache__ + *.pyc on server", "ℹ️")
        else:
            log("Clearing __pycache__ and .pyc files...", "🧹")
            rc, _, err = run(cmd, timeout=30)
            log("Cache cleared." if rc == 0 else f"Cache clear warning: {err}", "✅" if rc == 0 else "⚠️")

    if execute and verify_keys:
        log("Verifying critical config keys on server...", "🔍")
        for cfg, key in verify_keys:
            rc, out, _ = run(f"ssh {host} \"grep -n '{key}' {remote_dir}/{cfg} | head -2\"", timeout=10)
            status = out.strip() if out.strip() else "(not found)"
            print(f"  {cfg}: {key} → {status}")

    return True


def main():
    parser = argparse.ArgumentParser(description="Sync V8 baseline to backtest servers (NO live restarts)")
    parser.add_argument("--execute", action="store_true", help="Actually sync (default: dry-run)")
    parser.add_argument("--s1-only", action="store_true", help="Only process Server 1 (crypto)")
    parser.add_argument("--s2-only", action="store_true", help="Only process Server 2 (stocks)")
    parser.add_argument("--clear-cache", action="store_true", help="Clear __pycache__ on server after sync")
    args = parser.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log(f"sync_v8_baseline.py [{mode}]", "🚀")
    log("MacBook = live trading | S1 = crypto backtest | S2 = stocks backtest", "🛡️")
    log("NO live services will be started or restarted on any server.", "🛡️")

    run_s1 = not args.s2_only
    run_s2 = not args.s1_only

    if run_s1:
        sync_to_server(
            "SERVER 1 — CRYPTO BACKTEST",
            S1_HOST, S1_DIR, S1_FILES,
            execute=args.execute,
            clear_cache=args.clear_cache,
            verify_keys=S1_VERIFY_KEYS,
        )

    if run_s2:
        sync_to_server(
            "SERVER 2 — STOCKS BACKTEST",
            S2_HOST, S2_DIR, S2_FILES,
            execute=args.execute,
            clear_cache=args.clear_cache,
            verify_keys=S2_VERIFY_KEYS,
        )

    log(f"\nDone.", "✅")
    if not args.execute:
        log("Re-run with --execute to apply.", "ℹ️")


if __name__ == "__main__":
    main()
