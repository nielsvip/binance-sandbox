#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# --- CONFIGURATION ---
REMOTE_USER_HOST = "s1-int"
REMOTE_DIR = "/home/niels/binance"
WORKINGSET_BASE = "/home/niels/binance/workingset"
LOG_DIR = "/home/niels/logs"

# Server2: backtest-only, no restart. Syncs live code into sandbox so backtests always use latest.
SERVER2_HOST = "s2-int"
SERVER2_SANDBOX = "/home/niels/binance-sandbox"
# Files that backtest imports from sandbox (must stay current)
SERVER2_SYNC_FILES = [
    "tradier_manage.py", "tradier_indicators.py", "tradier_api.py", "tradier_positions.py",
    "tradier_rankings.py", "config_tradier.py", "config.py", "utils.py",
    "ez_manage.py", "ez_indicators.py", "ez_positions_quick.py", "ez_positions_service.py",
    "backtest_v5_full_tradier.py", "backtest_v5_engine.py", "backtest_v5_sweep.py",
    "backtest_v5_harness.py", "backtest_v5_analyze.py",
    "backtest_wt_intel_sweep.py",
]

# THE SOURCE OF TRUTH - only tradier_*, ez_*, and shared config/utils
FILES = [
    # --- Tradier ---
    "tradier_api.py", "tradier_prices.py", "tradier_positions.py", "tradier_indicators.py",
    "tradier_webhook_bridge.py", "tradier_rankings.py", "tradier_manage.py",
    # --- EZ (crypto) ---
    "ez_manage.py", "ez_indicators.py", "ez_indicators_merger.py", "ez_prices.py", "ez_mark_prices.py",
    "ez_prices_ws.py", "ez_crosses.py", "ez_rankings.py", "ez_klines.py", "ez_positions.py",
    "ez_positions_service.py", "ez_market_data.py", "ez_share_ind.py", "ez_news_scanner.py",
    "ez_disk_cleanup.py", "ez_double.py", "ez_positions_backup_account.py", "ez_positions_backup.py",
    "ez_positions_quick.py", "ez_positions_realtime.py",
    "ez_positions_realtime_ang.py", "ez_positions_realtime_fin.py", "ez_positions_realtime_flz.py",
    "ez_positions_realtime_inf.py", "ez_positions_realtime_men.py", "ez_positions_watchdog.py",
    "ez_copilot.py", "trade_analytics.py",
    # --- Shared config/utils ---
    "utils.py", "config.py", "config_tradier.py",
    # --- Managed on server, pulled before pushing ---
    "symbols.json", "symbols_men.json", "symbols_fin.json", "symbols_tradier.json",
    # --- Templates ---
    "templates/stocks.html","CLAUDE.md",
    # --- Backtest & docs ---
    "100.md", "backtest_v5_sweep.py", "backtest_v5_engine.py", "backtest_v5_full_tradier.py",
    "backtest_v5_harness.py", "backtest_v5_analyze.py",
]

# Shell/bash scripts pushed separately (not pulled — they're the authority)
SH_FILES = [
    "sh.sh", "run_with_watchdog_LINUX.sh", "nuke_everything_LINUX.sh",
]

def log(msg, symbol="ℹ️"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {symbol} {msg}", flush=True)

def run_cmd(cmd, timeout=300):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def check_server_older(base_dir):
    """Compare local vs server timestamps. Alert if server file is OLDER than local (local changed but server wasn't updated)."""
    log("Checking for files where server is older than local...", "🔍")
    all_files = FILES + SH_FILES
    remote_cmd = "stat --format='%n %Y' " + " ".join(f"{REMOTE_DIR}/{f}" for f in all_files) + " 2>/dev/null"
    rc, out, _ = run_cmd(f"ssh {REMOTE_USER_HOST} \"{remote_cmd}\"", timeout=30)
    if rc != 0 or not out:
        return
    server_times = {}
    for line in out.strip().split("\n"):
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            if "/" in parts[0]:
                rel = parts[0].replace(f"{REMOTE_DIR}/", "")
                server_times[rel] = int(parts[1])
            else:
                server_times[Path(parts[0]).name] = int(parts[1])
    conflicts = []
    for f in all_files:
        local_path = base_dir / f
        if not local_path.exists():
            continue
        local_mtime = int(local_path.stat().st_mtime)
        server_mtime = server_times.get(f, 0)
        if server_mtime > 0 and local_mtime > server_mtime:
            diff_s = local_mtime - server_mtime
            conflicts.append((f, diff_s))
    if conflicts:
        log(f"ALERT: {len(conflicts)} file(s) are NEWER locally than on server!", "🚨")
        log("This means the server version is OLDER — you may be pushing code that wasn't pulled from server first.", "🚨")
        for fname, diff in conflicts:
            m, s = divmod(diff, 60)
            log(f"  {fname} — local is {m}m{s}s newer than server", "⚠️")
        log("Run 'diff' against server before continuing. Ctrl+C to abort.", "⚠️")
        print("\a")  # terminal bell
        try:
            for i in range(15, 0, -1):
                print(f"\r  Continuing in {i}s... (Ctrl+C to abort)", end="", flush=True)
                time.sleep(1)
            print()
        except KeyboardInterrupt:
            log("Aborted by user.", "❌")
            sys.exit(1)
    else:
        log("All files in sync — server is same age or newer than local.", "✅")

def main():
    import platform
    if platform.system() != "Darwin":
        print("FATAL: push.py must only run from the LOCAL Mac, not on the server!")
        sys.exit(1)
    start_time = time.time()
    base_dir = Path(__file__).resolve().parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workingset_folder = f"{WORKINGSET_BASE}/workingset_{timestamp}"

    log("🚀 NUCLEAR DEPLOYMENT: STOP, DELETE, PUSH, RESTART", "🔥")

    # GUARD: If server is in backtest-only mode, skip server sync and restart
    rc_guard, out_guard, _ = run_cmd(f"ssh {REMOTE_USER_HOST} 'ls /home/niels/_binance_PAUSED* /home/niels/binance_scripts_paused 2>/dev/null | head -1'")
    if rc_guard == 0 and out_guard.strip():
        log(f"SERVER IN BACKTEST MODE ({out_guard.strip()}). Skipping server sync+restart. Local workingset only.", "🛑")
        workingset_folder = f"{WORKINGSET_BASE}/workingset_{timestamp}"
        run_cmd(f"ssh {REMOTE_USER_HOST} 'mkdir -p {workingset_folder}'")
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            for item in FILES + SH_FILES: f.write(f"{item}\n")
            f_list_all = f.name
        run_cmd(f"rsync -avzu --no-perms --timeout=60 --files-from={f_list_all} {base_dir}/ {REMOTE_USER_HOST}:{workingset_folder}/")
        os.unlink(f_list_all)
        log(f"Archive saved to {workingset_folder}. Server NOT touched.", "✅")
        elapsed = time.time() - start_time
        log(f"DEPLOYMENT COMPLETE (local-only) in {elapsed:.1f}s", "✅")
        return

    # Check for files where server is OLDER than local — alert before overwriting server
    check_server_older(base_dir)

    # Process nuking and script deletion is now handled by sh.sh directly.

    # 3. SYNC FRESH CODE
    log("PUSHING NEW CODE TO LIVE + WORKINGSETS...", "⚡")
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        for item in FILES: f.write(f"{item}\n")
        f_list = f.name

    all_files = FILES + SH_FILES
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        for item in all_files: f.write(f"{item}\n")
        f_list_all = f.name

    try:
        # Create directories
        run_cmd(f"ssh {REMOTE_USER_HOST} 'mkdir -p {REMOTE_DIR} {WORKINGSET_BASE} {workingset_folder}'")
        rsync_opts = "-avzu --no-perms --timeout=60"

        # PULL: Download from server first — if server has a newer version, update local before pushing
        log("Pulling latest from server (--update: only if server is newer)...", "⬇️")
        rc_pull, _, err_pull = run_cmd(f"rsync {rsync_opts} --files-from={f_list} {REMOTE_USER_HOST}:{REMOTE_DIR}/ {base_dir}/")
        if rc_pull != 0:
            log(f"Pull from server failed (continuing anyway): {err_pull}", "⚠️")
        else:
            log("Pull complete — local is now up to date with server.", "✅")

        # LIVE
        log("Syncing to LIVE...", "📡")
        rc1, _, err1 = run_cmd(f"rsync {rsync_opts} --files-from={f_list_all} {base_dir}/ {REMOTE_USER_HOST}:{REMOTE_DIR}/")
        if rc1 != 0: log(f"LIVE Rsync FAILED: {err1}", "❌"); sys.exit(1)
        
        # WORKINGSET (LATEST) — REMOVED: was dumping stray .py files into workingset root
        # Services run from /home/niels/binance/ directly (watchdog BASE_DIR fixed 2026-03-16)
        # Only the dated ARCHIVE below is kept for rollback history

        # HISTORICAL
        log(f"Syncing to ARCHIVE: {workingset_folder}...", "📡")
        run_cmd(f"rsync {rsync_opts} --files-from={f_list_all} {base_dir}/ {REMOTE_USER_HOST}:{workingset_folder}/")

        log("ALL CODE SENT SUCCESSFULLY.", "✅")
    finally:
        for fl in [f_list, f_list_all]:
            if os.path.exists(fl): os.unlink(fl)

    # 4. RESTART
    log("Restarting whole system via remote ./sh.sh...", "🔄")
    restart_job = f"cd {REMOTE_DIR} && chmod +x sh.sh && ./sh.sh"
    final_cmd = f"ssh {REMOTE_USER_HOST} 'nohup bash -c \"{restart_job}\" > {LOG_DIR}/push_restore.log 2>&1 &'"
    run_cmd(final_cmd)

    # 5. SYNC TO SERVER2 SANDBOX (no restart — backtest-only server)
    # Ensures any backtest run on server2 uses the LATEST live code
    log("Syncing live code to SERVER2 sandbox (no restart)...", "🖥️")
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f2:
        for item in SERVER2_SYNC_FILES:
            f2.write(f"{item}\n")
        f2_list = f2.name
    try:
        rc2, _, err2 = run_cmd(f"rsync -avzu --no-perms --timeout=30 --files-from={f2_list} {base_dir}/ {SERVER2_HOST}:{SERVER2_SANDBOX}/")
        if rc2 != 0:
            log(f"Server2 sync failed (non-fatal): {err2}", "⚠️")
        else:
            # Clear stale bytecode cache on server2
            run_cmd(f"ssh {SERVER2_HOST} 'find {SERVER2_SANDBOX} -name \"*.pyc\" -delete 2>/dev/null; find {SERVER2_SANDBOX} -name \"__pycache__\" -type d -exec rm -rf {{}} + 2>/dev/null; echo pycache_cleared'")
            log("Server2 sandbox synced + pycache cleared.", "✅")
    finally:
        if os.path.exists(f2_list):
            os.unlink(f2_list)

    elapsed = time.time() - start_time
    log(f"DEPLOYMENT COMPLETE in {elapsed:.1f}s", "✅")

if __name__ == "__main__":
    main()
