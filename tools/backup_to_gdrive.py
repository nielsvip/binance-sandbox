#!/usr/bin/env python3
"""
Automated backup archival to Google Drive.
Daily job: uploads 50 files (~2GB) to avoid API throttling.
"""

import os, sys, subprocess
from datetime import datetime, timedelta
from pathlib import Path

BACKUP_DIR = "/Users/niels/Documents/binance/backups"
CREDS_JSON = "/Users/niels/Downloads/civil-ripple-439101-j3-d50fb6537cdb.json"
LOG_FILE = "/Users/niels/Documents/binance/data/reports/gdrive_backup.log"
RCLONE_CONFIG = "/tmp/rclone_gdrive_backup.conf"

# Per-run limits (avoid API throttle)
KEEP_LOCAL_DAYS = 14
MAX_FILES_PER_RUN = 50
MAX_SIZE_GB = 2

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg_fmt = f"[{ts}] [{level}] {msg}"
    print(msg_fmt)
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg_fmt + "\n")

def setup_rclone():
    if not Path(CREDS_JSON).exists():
        log("ERROR: Credentials not found", "ERROR")
        return False

    Path(RCLONE_CONFIG).write_text(f"""[binance-gdrive]
type = drive
service_account_file = {CREDS_JSON}
""")
    return True

def get_old_files(days=KEEP_LOCAL_DAYS, max_count=MAX_FILES_PER_RUN):
    cutoff = datetime.now() - timedelta(days=days)
    old_files = []
    total_size = 0

    try:
        for fpath in sorted(Path(BACKUP_DIR).rglob("*.json")):
            if len(old_files) >= max_count or total_size > MAX_SIZE_GB * 1024**3:
                break

            mtime = datetime.fromtimestamp(fpath.stat().st_mtime)
            if mtime < cutoff:
                fsize = fpath.stat().st_size
                old_files.append((fpath, fsize))
                total_size += fsize
    except Exception as e:
        log(f"Scan error: {e}", "WARN")

    return old_files

def upload_file(fpath):
    try:
        result = subprocess.run([
            "rclone", "copyto", str(fpath),
            f"binance-gdrive:binance_backups/{fpath.name}",
            "--config", RCLONE_CONFIG,
            "--ignore-checksum", "--no-update-modtime",
            "--checkers=1", "--transfers=1", "--retries=1"
        ], capture_output=True, timeout=120)

        return result.returncode == 0
    except Exception as e:
        log(f"Upload error {fpath.name}: {e}", "WARN")
        return False

def main():
    log("=" * 60)
    log("Starting Google Drive backup archival")

    if not setup_rclone():
        return 1

    old_files = get_old_files()
    if not old_files:
        log("✓ No old backups found (all recent)")
        return 0

    total_size_gb = sum(s for _, s in old_files) / (1024**3)
    log(f"Found {len(old_files)} old files (~{total_size_gb:.1f}GB)")

    uploaded = 0
    for fpath, fsize in old_files:
        if upload_file(fpath):
            try:
                fpath.unlink()
                log(f"✓ {fpath.name}")
                uploaded += 1
            except Exception as e:
                log(f"Delete failed {fpath.name}: {e}", "WARN")
        else:
            log(f"⚠ {fpath.name} (keeping local)", "WARN")

    log("=" * 60)
    log(f"✓ Archival complete: {uploaded}/{len(old_files)} files")
    log("=" * 60)
    return 0

if __name__ == "__main__":
    sys.exit(main())
