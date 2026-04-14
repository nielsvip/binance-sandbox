#!/usr/bin/env python3
"""Autosave — every 15 minutes backup ALL critical files + git commit.

Runs forever. Survives reboots via launchd.
"""
import os, subprocess, sys, time, shutil
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

import fcntl
_lf = open("/tmp/autosave.lock", "w")
try:
    fcntl.flock(_lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
except (IOError, OSError):
    sys.exit(0)

REPO = Path("/Users/niels/Documents/binance")
BACKUP_DIR = REPO / "backups" / "autosave"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# Files to back up every 15 min
CRITICAL = [
    "ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
    "ez_positions_watchdog.py", "ez_indicators.py", "ez_market_data.py",
    "ez_rankings.py", "ez_copilot.py",
    "tradier_manage.py", "tradier_api.py", "tradier_indicators.py",
    "tradier_positions.py", "tradier_rankings.py", "tradier_webhook_bridge.py",
    "config.py", "config_tradier.py",
    "wt_dc_delta.py", "wt_composite.py", "wt_dc_exit_scorer.py",
    "utils.py", "ez_satoshit.py",
    "backtest_v8_engine.py", "backtest_v8_harness.py", "backtest_v8_sweep.py",
    "sweep_cockpit.py", "v8_watchdog.py", "cpu_enforcer.py", "log_healer.py",
    "LOCKED_FILES.md", "CLAUDE.md",
    "start_everything_1.command", "start_everything_2.command", "start_everything_3.command",
]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def backup_cycle():
    """Copy all critical files to timestamped backup dir, keep last 96 (24h)."""
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    dest = BACKUP_DIR / ts
    dest.mkdir(exist_ok=True)
    saved = 0
    for fn in CRITICAL:
        src = REPO / fn
        if src.exists():
            try:
                shutil.copy2(src, dest / fn)
                saved += 1
            except Exception as e:
                log(f"  SKIP {fn}: {e}")
    log(f"Backup {ts}: {saved}/{len(CRITICAL)} files → {dest}")
    # Prune old — keep last 96 (24h at 15min intervals)
    all_backups = sorted(BACKUP_DIR.glob("*"))
    if len(all_backups) > 96:
        for old in all_backups[:-96]:
            try:
                shutil.rmtree(old)
            except Exception:
                pass


def git_commit():
    """Commit all changes with timestamp message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(REPO), timeout=30,
                      capture_output=True)
        r = subprocess.run(["git", "commit", "-m", f"Autosave {ts}", "--no-verify"],
                          cwd=str(REPO), timeout=30, capture_output=True, text=True)
        if r.returncode == 0:
            log(f"git commit: OK")
        elif "nothing to commit" in r.stdout.lower() or "nothing to commit" in r.stderr.lower():
            log(f"git commit: no changes")
        else:
            log(f"git commit: {r.stderr.strip()[:200]}")
    except Exception as e:
        log(f"git commit failed: {e}")


log("AUTOSAVE STARTED — 15 min cycles, file backups + git commit")
while True:
    try:
        backup_cycle()
        git_commit()
    except Exception as e:
        log(f"ERROR: {e}")
    time.sleep(900)  # 15 minutes
