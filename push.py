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

# SERVER1: backtest-only, no restart. Syncs live code into sandbox so backtests always use latest.
# 2026-06-03: S2 destroyed 2026-05-08. Re-pointed the "sandbox parity" sync to S1's own
# binance-sandbox so backtests on S1 always use the latest live code (was s2-int).
SERVER1_HOST = "s1-int"
SERVER1_SANDBOX = "/home/niels/binance-sandbox"
# Files that backtest imports from sandbox (must stay current)
SERVER1_SYNC_FILES = [
    "tradier_manage.py", "tradier_indicators.py", "tradier_api.py", "tradier_positions.py",
    "tradier_rankings.py", "config_tradier.py", "config.py", "utils.py",
    "per_sym_20d_agent_stocks.py",
    "ez_manage.py", "ez_indicators.py", "ez_positions_quick.py", "ez_positions_service.py","ez_positions_realtime.py"
    "ez_reentry.py", "ez_reentry_daemon.py", "ez_reentry_vectorized.py",
    # Agent advisory bundle — paper-account agent-supervisor wiring (trc + fin)
    "trc_advisory_consumer.py", "fin_advisory_consumer.py",
    "agent_snapshot_writer.py", "compare_trc_trb.py", "agent_inbox_poller.py",
]

# THE SOURCE OF TRUTH - only tradier_*, ez_*, and shared config/utils
FILES = [
    # --- Tradier ---
    "tradier_api.py", "tradier_prices.py", "tradier_positions.py", "tradier_indicators.py",
    "tradier_webhook_bridge.py", "tradier_rankings.py", "tradier_manage.py",
    # --- EZ (crypto) ---
    "ez_manage.py", "ez_indicators.py", "ez_prices.py",
    "ez_crosses.py", "ez_rankings.py", "ez_klines.py", "ez_positions.py",
    "ez_positions_service.py", "ez_market_data.py", "ez_share_ind.py", "ez_news_scanner.py",
    "ez_disk_cleanup.py",
    "ez_positions_quick.py", "ez_positions_realtime.py",
    "ez_reentry.py", "ez_reentry_daemon.py", "ez_reentry_vectorized.py",
    "ez_copilot.py", "trade_analytics.py",
    # --- Agent advisory bundle (paper-account supervisor channel) ---
    "trc_advisory_consumer.py", "fin_advisory_consumer.py",
    "agent_snapshot_writer.py", "compare_trc_trb.py", "agent_inbox_poller.py",
    # --- Shared config/utils ---
    "utils.py", "config.py", "config_tradier.py",
    # --- Managed on server, pulled before pushing ---
    "symbols.json", "symbols_men.json", "symbols_fin.json", "symbols_tradier.json",
    # --- Templates ---
    "templates/stocks.html","CLAUDE.md",
    # --- Docs ---
    "100.md",
    # --- vec_paths (shared by v8_vec_sweep + backtest_v8_engine; not in live dir but pushed to sandbox) ---
    "vec_paths/gr_filter_vec.py", "vec_paths/funding_gate.py",
    # --- per_sym sweep tools ---
    "tools/per_sym_sweep_100.py",
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
    # 2026-06-03: Mac is the SOLE source of truth (S1 = sweeps only). "Local newer than server"
    # is the NORMAL, correct state — it just means we have code to push. Informational only,
    # never blocks (the old 15s abort countdown was for the retired S1-as-live era).
    if conflicts:
        log(f"{len(conflicts)} file(s) newer locally — will push to S1 (expected; Mac is source of truth).", "ℹ️")
    else:
        log("All files in sync — server same age or newer than local.", "✅")

def stop_mac_live():
    """STOP-MAC-FIRST cutover safety: halt the Mac live stack so S1 becomes the SOLE trader (never two
    boxes on the same accounts = double orders). Returns True iff the Mac was ACTUALLY live-trading before
    the stop (-> real cutover -> caller snapshots positions). On a routine dev push (Mac already idle)
    returns False -> caller must NOT clobber S1's live positions with stale Mac dirs."""
    rc, out, _ = run_cmd("ps -ef | grep -E 'python.*(ez_manage|tradier_manage)\\\\.py --account' | grep -v grep | wc -l")
    were_live = (out or "0").strip() not in ("", "0")
    if were_live:
        log("STOP-MAC-FIRST: Mac is live — booting out launchd + killing live procs before S1 starts...", "🛑")
        for label in ("com.niels.ez-launcher", "com.niels.stocks-market-open"):
            run_cmd(f"launchctl bootout gui/$(id -u)/{label} 2>/dev/null; launchctl stop {label} 2>/dev/null")
        run_cmd("pkill -9 -f 'python.*ez_manage.py' 2>/dev/null; pkill -9 -f 'python.*ez_positions_quick.py' 2>/dev/null; pkill -9 -f 'python.*ez_positions_service.py' 2>/dev/null; pkill -9 -f 'python.*tradier_manage.py' 2>/dev/null; pkill -9 -f 'python.*tradier_positions.py' 2>/dev/null")
        time.sleep(5)
    else:
        log("Mac already idle (no live procs) — routine push, NOT a cutover (positions NOT snapshotted).", "ℹ️")
    return were_live

def _mac_live_count():
    rc, out, _ = run_cmd("ps -ef | grep -E 'python.*(ez_manage|tradier_manage)\\\\.py --account' | grep -v grep | wc -l")
    return (out or "1").strip()

def snapshot_positions_to_s1(base_dir):
    log("Snapshotting Mac position state -> S1 live dir (authoritative at cutover instant)...", "📸")
    for acct in ("ang", "inf", "flz", "men", "fin", "trb", "trc"):
        d = base_dir / acct
        if d.exists():
            run_cmd(f"rsync -az {d}/ {REMOTE_USER_HOST}:{REMOTE_DIR}/{acct}/")
    run_cmd(f"rsync -az {base_dir}/symbols.json {REMOTE_USER_HOST}:{REMOTE_DIR}/symbols.json")

def main():
    import platform
    if platform.system() != "Darwin":
        print("FATAL: push.py runs from the Mac dev box only."); sys.exit(1)
    t0 = time.time()
    base_dir = Path(__file__).resolve().parent
    # ── STOP-MAC-FIRST one-shot cutover safety ──────────────────────────────────────
    _mac_was_live = stop_mac_live()
    if _mac_was_live:
        if _mac_live_count() not in ("", "0"):
            log("ABORT: Mac live procs STILL present after stop — NOT starting S1 (would double-trade). Kill manually + re-run.", "🛑")
            sys.exit(2)
        snapshot_positions_to_s1(base_dir)
        log("Cutover: Mac stopped + positions snapshotted -> safe to start S1 as sole trader.", "✅")
    log("BIDIRECTIONAL SYNC (pull if S1 newer / else push) -> S1, then conditional restart.", "🔄")
    log("NOTE: on changes this FULL-restarts the S1 live stack (sh.sh). Ensure no other box trades these accounts.", "🛡️")
    all_files = FILES + SH_FILES
    remote_cmd = "stat --format='%n %Y' " + " ".join(f"{REMOTE_DIR}/{f}" for f in all_files) + " 2>/dev/null"
    rc, out, _ = run_cmd(f"ssh {REMOTE_USER_HOST} \"{remote_cmd}\"", timeout=60)
    server_times = {}
    for line in (out or "").splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            server_times[parts[0].replace(f"{REMOTE_DIR}/", "")] = int(parts[1])
    pulled, pushed = [], []
    for f in all_files:
        lp = base_dir / f
        lm = int(lp.stat().st_mtime) if lp.exists() else 0
        sm = server_times.get(f, 0)
        if sm > lm + 1:                      # S1 newer -> PULL (preserve newer server edits)
            rc, _, _ = run_cmd(f"rsync -az --no-perms {REMOTE_USER_HOST}:{REMOTE_DIR}/{f} {lp}")
            if rc == 0: pulled.append(f)
        elif lm > sm + 1:                    # local newer/new -> PUSH to live dir + sandbox
            ok = True
            for tgt in (REMOTE_DIR, SERVER1_SANDBOX):
                rc, _, _ = run_cmd(f"rsync -az --no-perms --update {lp} {REMOTE_USER_HOST}:{tgt}/{f}")
                ok = ok and rc == 0
            if ok: pushed.append(f)
    changed = pulled + pushed
    log(f"pulled {len(pulled)} (S1 newer), pushed {len(pushed)}. changed={len(changed)}", "📦")
    if pulled: log(f"PULLED from S1 (were newer there): {pulled}", "⬇️")
    for tgt in (REMOTE_DIR, SERVER1_SANDBOX):
        run_cmd(f"ssh {REMOTE_USER_HOST} 'find {tgt} -name \"*.pyc\" -delete 2>/dev/null; find {tgt} -name __pycache__ -type d -exec rm -rf {{}} + 2>/dev/null'")
    no_restart = run_cmd(f"ssh {REMOTE_USER_HOST} 'test -f /home/niels/S1_NO_RESTART && echo YES || echo NO'")[1].strip() == 'YES'
    if no_restart:
        log("S1_NO_RESTART flag set — skipping sh.sh restart (testing/sweep mode). Sync only.", "🔒")
    elif changed:
        log("Changes -> FULL restart of all ez_/tradier_ services via sh.sh.", "♻️")
        run_cmd(f"ssh {REMOTE_USER_HOST} 'cd {REMOTE_DIR} && pkill -9 -f run_with_watchdog_LINUX 2>/dev/null; chmod +x sh.sh; nohup ./sh.sh > {LOG_DIR}/push_restart.log 2>&1 & disown'", timeout=120)
    else:
        log("No changes -> bounce ONLY ez_manage + ez_positions_quick (avoid bans); sh.sh idempotently restarts just those.", "♻️")
        run_cmd(f"ssh {REMOTE_USER_HOST} 'cd {REMOTE_DIR} && pkill -9 -f \"ez_manage.py --account\" 2>/dev/null; pkill -9 -f \"ez_positions_quick.py\" 2>/dev/null; sleep 2; chmod +x sh.sh; nohup ./sh.sh > {LOG_DIR}/push_bounce.log 2>&1 & disown'", timeout=120)
    log(f"DEPLOY+RESTART done in {time.time()-t0:.1f}s", "✅")

if __name__ == "__main__":
    main()
