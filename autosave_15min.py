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
    # Smart pruning: only delete when disk usage exceeds 5GB.
    # Keep versions with LARGEST edit gaps (stable versions that ran longest).
    import os as _os
    def _dirsize(p):
        total = 0
        for root, _, files in _os.walk(p):
            for f in files:
                try: total += _os.path.getsize(_os.path.join(root, f))
                except OSError: pass
        return total
    MAX_BYTES = 5 * 1024 * 1024 * 1024  # 5GB
    current_size = _dirsize(BACKUP_DIR)
    if current_size < MAX_BYTES:
        return  # plenty of space, keep everything
    log(f"Backup dir at {current_size/1e9:.1f}GB — pruning by stability score")
    # Sort by timestamp, compute edit gap (time to next backup)
    all_backups = sorted(BACKUP_DIR.glob("*"))
    if len(all_backups) < 10:
        return
    # For each backup, compute gap to next one. Keep the ones with LARGEST gaps.
    scored = []
    for i, b in enumerate(all_backups[:-1]):
        try:
            gap = all_backups[i+1].stat().st_mtime - b.stat().st_mtime
            scored.append((gap, b))
        except OSError:
            pass
    # Sort by gap DESC — largest gap = most stable = keep
    scored.sort(reverse=True)
    keep = set(b for _, b in scored[:48])  # keep top 48 stable versions
    keep.add(all_backups[-1])  # always keep newest
    for b in all_backups:
        if b not in keep:
            try: shutil.rmtree(b); log(f"  pruned {b.name}")
            except Exception: pass
            if _dirsize(BACKUP_DIR) < MAX_BYTES * 0.7: break


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


GITHUB_REMOTE = "github-main"
GITHUB_SSH_KEY = Path.home() / ".ssh" / "id_ed25519_github_binance_main"


def git_push():
    """One-way mirror of HEAD to GitHub. NEVER fetch/pull/merge/reset here —
    Mac is the live-trading source of truth (CLAUDE.md); this repo must never
    pull code back down from GitHub. No-ops until GITHUB_REMOTE is configured."""
    r = subprocess.run(["git", "remote", "get-url", GITHUB_REMOTE], cwd=str(REPO),
                      capture_output=True, text=True)
    if r.returncode != 0:
        return
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = f"ssh -i {GITHUB_SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    try:
        r = subprocess.run(["git", "push", GITHUB_REMOTE, "HEAD:main"], cwd=str(REPO),
                          env=env, timeout=60, capture_output=True, text=True)
        if r.returncode == 0:
            log("git push: OK")
        elif "up-to-date" in (r.stdout + r.stderr).lower() or "up to date" in (r.stdout + r.stderr).lower():
            log("git push: up to date")
        else:
            log(f"git push failed: {(r.stderr or r.stdout).strip()[:300]}")
    except Exception as e:
        log(f"git push failed: {e}")


log("AUTOSAVE STARTED — 15 min cycles, file backups + git commit")
while True:
    try:
        backup_cycle()
        git_commit()
        git_push()
    except Exception as e:
        log(f"ERROR: {e}")
    time.sleep(900)  # 15 minutes
