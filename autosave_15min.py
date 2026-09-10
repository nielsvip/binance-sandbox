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
    "v12_quick_engine.py", "v12_wide_engine.py", "SPREADSHEETS/TEMPLATE.xlsx", "SPREADSHEETS/TEMPLATE.xlsx.sha256",
    "vec_decisions/bb_pullback_gate.py", "vec_decisions/filter_tf_gate.py", "tradier_matrix_gates.py",
    "sweep_cockpit.py", "v8_watchdog.py", "cpu_enforcer.py", "log_healer.py",
    "LOCKED_FILES.md", "CLAUDE.md",
    "symbols_tradier.json", "symbols_tradier.last_known_good.json",
    "start_everything_1.command", "start_everything_2.command", "start_everything_3.command",
]

MASTER_SYMBOLS = REPO / "symbols_tradier.json"
MASTER_SYMBOLS_RECOVERY = REPO / "symbols_tradier.last_known_good.json"


def ensure_master_symbols():
    """Restore the master Tradier allowlist if another process removes it."""
    try:
        if MASTER_SYMBOLS.exists():
            return
        if not MASTER_SYMBOLS_RECOVERY.exists():
            log("CRITICAL: symbols_tradier.json missing and recovery seed unavailable")
            return
        tmp = MASTER_SYMBOLS.with_name(f".{MASTER_SYMBOLS.name}.{os.getpid()}.tmp")
        shutil.copy2(MASTER_SYMBOLS_RECOVERY, tmp)
        os.replace(tmp, MASTER_SYMBOLS)
        log("CRITICAL: restored missing symbols_tradier.json from last-known-good seed")
    except Exception as e:
        log(f"CRITICAL: failed to restore symbols_tradier.json: {e}")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def backup_cycle():
    """Copy all critical files to timestamped backup dir, keep last 96 (24h)."""
    ensure_master_symbols()
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    dest = BACKUP_DIR / ts
    dest.mkdir(exist_ok=True)
    saved = 0
    for fn in CRITICAL:
        src = REPO / fn
        if src.exists():
            try:
                target = dest / fn
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
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


STALE_LOCK_MIN_AGE_S = 120


def clear_stale_index_lock():
    """Remove .git/index.lock only when provably abandoned (old + no open fd)."""
    lock = REPO / ".git" / "index.lock"
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    if age < STALE_LOCK_MIN_AGE_S:
        log(f"index.lock is only {age:.0f}s old — a live git may own it, not clearing")
        return False
    lsof_bin = shutil.which("lsof") or "/usr/sbin/lsof"
    try:
        probe = subprocess.run([lsof_bin, "--", str(lock)], timeout=15, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"index.lock age={age:.0f}s but lsof probe unavailable ({e}) — not clearing")
        return False
    if probe.returncode == 0 and probe.stdout.strip():
        log(f"index.lock age={age:.0f}s but still has an open fd — not clearing")
        return False
    try:
        lock.unlink()
    except OSError as e:
        log(f"index.lock removal failed: {e}")
        return False
    log(f"CRITICAL: cleared stale .git/index.lock (age={age:.0f}s, no open fd) — autosave was blocked")
    return True


def git_commit():
    """Commit all changes with timestamp message."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        a = subprocess.run(["git", "add", "-A"], cwd=str(REPO), timeout=120,
                          capture_output=True, text=True)
        if a.returncode != 0 and "index.lock" in a.stderr and clear_stale_index_lock():
            a = subprocess.run(["git", "add", "-A"], cwd=str(REPO), timeout=120,
                              capture_output=True, text=True)
        if a.returncode != 0:
            log(f"git add: rc={a.returncode} stderr={a.stderr.strip()[:200]!r}")
        r = subprocess.run(["git", "commit", "-m", f"Autosave {ts}", "--no-verify"],
                          cwd=str(REPO), timeout=30, capture_output=True, text=True)
        if r.returncode == 0:
            log(f"git commit: OK")
        elif "nothing to commit" in r.stdout.lower() or "nothing to commit" in r.stderr.lower():
            log(f"git commit: no changes")
        else:
            log(f"git commit: rc={r.returncode} stderr={r.stderr.strip()[:200]!r} stdout={r.stdout.strip()[:300]!r}")
    except Exception as e:
        log(f"git commit failed: {e}")


GITHUB_REMOTE = "origin"
GITHUB_SSH_KEY = Path.home() / ".ssh" / "id_ed25519_github_binance_main"
GITHUB_SSH_REMOTE = "origin-ssh"


def git_push():
    """One-way mirror of HEAD to GitHub + S1. NEVER fetch/pull/merge/reset here —
    Mac is the live-trading source of truth (CLAUDE.md); this repo must never
    pull code back down from GitHub. Pushes to origin (GitHub) and s1-backup."""
    for remote in (GITHUB_REMOTE, GITHUB_SSH_REMOTE, "s1-backup"):
        r = subprocess.run(["git", "remote", "get-url", remote], cwd=str(REPO),
                          capture_output=True, text=True)
        if r.returncode != 0:
            continue
        url = r.stdout.strip()
        env = os.environ.copy()
        if url.startswith("git@") or url.startswith("ssh://") or "s1-int" in url:
            env["GIT_SSH_COMMAND"] = f"ssh -i {GITHUB_SSH_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
        try:
            r = subprocess.run(["git", "push", remote, "HEAD:main"], cwd=str(REPO),
                              env=env, timeout=300, capture_output=True, text=True)
            if r.returncode == 0:
                log(f"git push {remote}: OK")
            elif "up-to-date" in (r.stdout + r.stderr).lower() or "up to date" in (r.stdout + r.stderr).lower():
                log(f"git push {remote}: up to date")
            else:
                log(f"git push {remote} failed: {(r.stderr or r.stdout).strip()[:400]}")
        except Exception as e:
            log(f"git push {remote} failed: {e}")


log("AUTOSAVE STARTED — 15 min cycles, file backups + git commit")
while True:
    try:
        backup_cycle()
        git_commit()
        git_push()
    except Exception as e:
        log(f"ERROR: {e}")
    time.sleep(900)  # 15 minutes
