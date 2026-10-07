#!/usr/bin/env python3
"""One-shot flip: OPTIONS_SPREAD_ENABLED False → True in config_tradier.py.

Scheduled via cron for 2026-04-20 16:00 UTC (noon ET Monday).
Idempotent: if already True, logs and exits clean.
Safety: backs up config_tradier.py first; refuses to run on wrong date unless --force.
"""
import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
CONFIG_FILE = BASE / "config_tradier.py"
BACKUP_DIR = BASE / "backups"
LOG_FILE = Path.home() / "logs" / "flip_spread.log"

TARGET_DATE = "2026-04-20"  # safety double-check


def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"{ts} | {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Bypass date check")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, no edit")
    args = ap.parse_args()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if today != TARGET_DATE and not args.force:
        log(f"SKIP today={today} != target={TARGET_DATE} (use --force to override)")
        sys.exit(0)
    if not CONFIG_FILE.exists():
        log(f"ABORT config_tradier.py not found at {CONFIG_FILE}")
        sys.exit(2)
    text = CONFIG_FILE.read_text()
    pattern = re.compile(r"^(\s*OPTIONS_SPREAD_ENABLED\s*:\s*bool\s*=\s*)(False|True)(.*)$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        log("ABORT OPTIONS_SPREAD_ENABLED line not found — schema changed?")
        sys.exit(3)
    current_value = m.group(2)
    if current_value == "True":
        log(f"NOOP OPTIONS_SPREAD_ENABLED already True (no change needed)")
        sys.exit(0)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M")
    backup_path = BACKUP_DIR / f"before_spread_flip_{ts}.py"
    if not args.dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CONFIG_FILE, backup_path)
        log(f"BACKUP {backup_path}")
    new_text = pattern.sub(r"\1True\3", text, count=1)
    # Verify the replacement
    verify_m = re.search(r"^\s*OPTIONS_SPREAD_ENABLED\s*:\s*bool\s*=\s*True", new_text, re.MULTILINE)
    if not verify_m:
        log("ABORT replacement verification failed — aborting")
        sys.exit(4)
    if args.dry_run:
        log(f"DRY_RUN would flip False→True (no edit written)")
        sys.exit(0)
    CONFIG_FILE.write_text(new_text)
    # Syntax-check by compiling
    import py_compile
    try:
        py_compile.compile(str(CONFIG_FILE), doraise=True)
    except py_compile.PyCompileError as e:
        log(f"SYNTAX_ERROR after flip — restoring backup: {e}")
        shutil.copy2(backup_path, CONFIG_FILE)
        log(f"RESTORED from {backup_path}")
        sys.exit(5)
    log(f"FLIPPED OPTIONS_SPREAD_ENABLED False→True in {CONFIG_FILE} (backup {backup_path.name})")
    # Lightweight notification — append to morning_email inbox pipeline
    try:
        import subprocess
        subprocess.run(["osascript", "-e", 'display notification "Spread strategy LIVE — OPTIONS_SPREAD_ENABLED=True" with title "Tradier Options"'], check=False, timeout=5)
    except Exception:
        pass
    log("DONE")


if __name__ == "__main__":
    main()
