#!/usr/bin/env python3
"""ez_backup.py — Archive large data to external drives before disk fills up.
Runs hourly from start_everything_1. Handles both MacBook and server.

Archives: klines, indicator caches, position history, backtest results, logs.
Targets: /Volumes/SSD2T (primary), /Volumes/TOSHIBA_EXT (secondary mirror).
Server: rsync large dirs to local external drives via SSH.

NEVER deletes source data — only copies/mirrors to external drives.
"""
import json, logging, os, platform, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("ez_backup")

IS_MAC = platform.system() == "Darwin"
if IS_MAC:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    LOG_DIR = Path("/Users/niels/logs")
else:
    BASE_PATH = Path("/home/niels/binance")
    LOG_DIR = Path("/home/niels/logs")

# External drives (Mac only — server archives TO these via rsync)
SSD2T = Path("/Volumes/SSD2T")
TOSHIBA = Path("/Volumes/TOSHIBA_EXT")

# Archive layout on external drives
ARCHIVE_DIRS = {
    "klines": "binance_archive/klines_cache",
    "klines_tradier": "binance_archive/klines_cache_tradier",
    "data": "binance_archive/data",
    "backups": "binance_archive/backups",
    "logs": "binance_archive/logs",
    "positions": "binance_archive/positions",
    "indicator_cache": "binance_archive/indicator_cache",
}

SERVER = "s1-int"
SERVER_LARGE_DIRS = {
    "klines": "/home/niels/binance-sandbox/klines_cache",
    "indicator_cache": "/home/niels/binance-sandbox/indicator_cache",
    "data": "/home/niels/binance/data",
    "logs": "/home/niels/logs",
    "positions_ang": "/home/niels/binance/ang",
    "positions_inf": "/home/niels/binance/inf",
    "positions_fin": "/home/niels/binance/fin",
    "positions_men": "/home/niels/binance/men",
    "positions_flz": "/home/niels/binance/flz",
}

# Disk space thresholds (bytes)
WARN_THRESHOLD = 20 * 1024**3  # 20GB free = warning
CRITICAL_THRESHOLD = 10 * 1024**3  # 10GB free = critical, start moving


def get_free_space(path):
    """Get free space in bytes for the filesystem containing path."""
    try:
        st = os.statvfs(str(path))
        return st.f_bavail * st.f_frsize
    except Exception:
        return None


def get_dir_size(path):
    """Get total size of a directory in bytes."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(str(path)):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        pass
    return total


def fmt_size(nbytes):
    """Format bytes as human-readable."""
    if nbytes is None:
        return "N/A"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f}{unit}"
        nbytes /= 1024
    return f"{nbytes:.1f}PB"


def find_archive_drive():
    """Find the best external drive for archiving. Prefer SSD2T (faster)."""
    for drive in [SSD2T, TOSHIBA]:
        if drive.exists() and drive.is_dir():
            free = get_free_space(drive)
            if free and free > 5 * 1024**3:  # At least 5GB free
                return drive
    return None


def ensure_archive_dirs(drive):
    """Create archive directory structure on the external drive."""
    for key, subdir in ARCHIVE_DIRS.items():
        target = drive / subdir
        target.mkdir(parents=True, exist_ok=True)


def rsync_local(src, dst, delete=False):
    """rsync a local directory to the archive drive."""
    if not Path(src).exists():
        return 0
    cmd = ["rsync", "-a", "--update", "--timeout=120"]
    if delete:
        cmd.append("--delete")
    cmd.extend([str(src).rstrip("/") + "/", str(dst) + "/"])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            return 1
        else:
            logger.warning(f"rsync failed: {src} → {dst}: {result.stderr[:200]}")
            return 0
    except Exception as e:
        logger.error(f"rsync error: {src} → {dst}: {e}")
        return 0


def rsync_from_server(remote_path, local_dst):
    """rsync a server directory to a local archive drive."""
    cmd = ["rsync", "-az", "--update", "--timeout=120", f"{SERVER}:{remote_path}/", str(local_dst) + "/"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            return 1
        else:
            logger.warning(f"Server rsync failed: {remote_path} → {local_dst}: {result.stderr[:200]}")
            return 0
    except Exception as e:
        logger.error(f"Server rsync error: {remote_path} → {local_dst}: {e}")
        return 0


def archive_local_data(drive):
    """Archive local MacBook data to external drive."""
    logger.info(f"Archiving local data to {drive.name}...")
    count = 0
    # Klines (crypto)
    klines_src = BASE_PATH / "klines_cache"
    if klines_src.exists():
        count += rsync_local(klines_src, drive / ARCHIVE_DIRS["klines"])
        logger.info(f"  Klines (crypto): {fmt_size(get_dir_size(klines_src))}")
    # Klines (tradier/stocks)
    klines_tradier = BASE_PATH / "klines_cache" / "tradier"
    if klines_tradier.exists():
        count += rsync_local(klines_tradier, drive / ARCHIVE_DIRS["klines_tradier"])
        logger.info(f"  Klines (tradier): {fmt_size(get_dir_size(klines_tradier))}")
    # Data directory (decisions, backtest results, etc)
    data_src = BASE_PATH / "data"
    if data_src.exists():
        count += rsync_local(data_src, drive / ARCHIVE_DIRS["data"])
        logger.info(f"  Data: {fmt_size(get_dir_size(data_src))}")
    # Backups
    backups_src = BASE_PATH / "backups"
    if backups_src.exists():
        count += rsync_local(backups_src, drive / ARCHIVE_DIRS["backups"])
    # Logs
    if LOG_DIR.exists():
        count += rsync_local(LOG_DIR, drive / ARCHIVE_DIRS["logs"])
    # Position files (per account)
    for acct in ["ang", "inf", "fin", "men", "flz"]:
        acct_dir = BASE_PATH / acct
        if acct_dir.exists():
            target = drive / ARCHIVE_DIRS["positions"] / acct
            target.mkdir(parents=True, exist_ok=True)
            count += rsync_local(acct_dir, target)
    logger.info(f"  Local archive: {count} dirs synced")
    return count


def archive_server_data(drive):
    """Pull server large directories to external drive via SSH."""
    logger.info(f"Archiving server data to {drive.name}...")
    count = 0
    for key, remote_path in SERVER_LARGE_DIRS.items():
        if key.startswith("positions_"):
            acct = key.replace("positions_", "")
            local_dst = drive / ARCHIVE_DIRS["positions"] / f"server_{acct}"
        elif key in ARCHIVE_DIRS:
            local_dst = drive / ARCHIVE_DIRS[key].replace("binance_archive", "binance_archive/server")
        else:
            local_dst = drive / "binance_archive" / "server" / key
        local_dst.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Server {key}: {remote_path} → {local_dst}")
        count += rsync_from_server(remote_path, local_dst)
    logger.info(f"  Server archive: {count} dirs synced")
    return count


def mirror_to_secondary(primary, secondary):
    """Mirror primary archive to secondary drive (TOSHIBA as backup of SSD2T)."""
    archive_dir = primary / "binance_archive"
    if not archive_dir.exists():
        return 0
    target = secondary / "binance_archive"
    target.mkdir(parents=True, exist_ok=True)
    logger.info(f"Mirroring {primary.name}/binance_archive → {secondary.name}/binance_archive...")
    return rsync_local(archive_dir, target)


def disk_space_report():
    """Print disk space report for all locations."""
    logger.info("=" * 60)
    logger.info("DISK SPACE REPORT")
    logger.info("=" * 60)
    # MacBook internal
    mac_free = get_free_space(BASE_PATH)
    mac_status = "OK" if mac_free and mac_free > WARN_THRESHOLD else "WARNING" if mac_free and mac_free > CRITICAL_THRESHOLD else "CRITICAL"
    logger.info(f"  MacBook internal:  {fmt_size(mac_free)} free [{mac_status}]")
    # External drives
    for drive_name, drive_path in [("SSD2T", SSD2T), ("TOSHIBA_EXT", TOSHIBA)]:
        if drive_path.exists():
            free = get_free_space(drive_path)
            archive_size = get_dir_size(drive_path / "binance_archive") if (drive_path / "binance_archive").exists() else 0
            logger.info(f"  {drive_name}:          {fmt_size(free)} free, archive={fmt_size(archive_size)}")
        else:
            logger.info(f"  {drive_name}:          NOT MOUNTED")
    # Server (via SSH)
    try:
        result = subprocess.run(["ssh", SERVER, "df -h / | tail -1 | awk '{print $4}'"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info(f"  Server:            {result.stdout.strip()} free")
    except Exception:
        logger.info(f"  Server:            UNREACHABLE")
    # Local data sizes
    logger.info("  --- Local data sizes ---")
    for label, path in [("klines_cache", BASE_PATH / "klines_cache"), ("data", BASE_PATH / "data"), ("backups", BASE_PATH / "backups"), ("logs", LOG_DIR)]:
        if path.exists():
            logger.info(f"    {label}: {fmt_size(get_dir_size(path))}")
    logger.info("=" * 60)


def clean_old_server_data():
    """Clean safely deletable data on server to free space."""
    logger.info("Checking server for cleanable data...")
    cleanable = [
        ("Old backup (pre-march18)", "/home/niels/_binance_old_backup_pre_march18", 17),
        ("Old binance_ copy", "/home/niels/binance_", 3.5),
        ("Duplicate klines_cache_macbook", "/home/niels/binance/klines_cache_macbook", 4.4),
        ("Duplicate klines_cache_gateway", "/home/niels/binance/klines_cache_gateway", 1.2),
        ("Sandbox klines_cache_macbook", "/home/niels/binance-sandbox/klines_cache_macbook", 0.7),
        ("Sandbox klines_cache_gateway", "/home/niels/binance-sandbox/klines_cache_gateway", 1.4),
        ("Old framework backup", "/home/niels/binance-sandbox/backtest_framework_backup_20260316", 1.9),
    ]
    for label, path, est_gb in cleanable:
        try:
            result = subprocess.run(["ssh", SERVER, f"test -d {path} && echo EXISTS"], capture_output=True, text=True, timeout=10)
            if "EXISTS" in result.stdout:
                logger.info(f"  CLEANABLE: {label} (~{est_gb}GB) — {path}")
        except Exception:
            pass


def run_backup_cycle():
    """Run one complete backup cycle."""
    t0 = time.time()
    logger.info(f"{'=' * 60}")
    logger.info(f"BACKUP CYCLE START — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"{'=' * 60}")
    disk_space_report()
    if not IS_MAC:
        logger.info("Running on server — skipping archive (no external drives). Use MacBook to archive.")
        return
    drive = find_archive_drive()
    if not drive:
        logger.warning("NO EXTERNAL DRIVE AVAILABLE — skipping archive. Plug in SSD2T or TOSHIBA_EXT.")
        return
    logger.info(f"Using archive drive: {drive.name} ({fmt_size(get_free_space(drive))} free)")
    ensure_archive_dirs(drive)
    # 1. Archive local data
    archive_local_data(drive)
    # 2. Archive server data (only if server reachable)
    try:
        result = subprocess.run(["ssh", "-o", "ConnectTimeout=5", SERVER, "echo OK"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            archive_server_data(drive)
            clean_old_server_data()
        else:
            logger.warning("Server unreachable — skipping server archive")
    except Exception:
        logger.warning("Server unreachable — skipping server archive")
    # 3. Mirror to secondary drive if both connected
    if drive == SSD2T and TOSHIBA.exists():
        mirror_to_secondary(SSD2T, TOSHIBA)
    elif drive == TOSHIBA and SSD2T.exists():
        mirror_to_secondary(TOSHIBA, SSD2T)
    elapsed = time.time() - t0
    logger.info(f"BACKUP CYCLE COMPLETE in {elapsed:.0f}s")
    disk_space_report()


def main():
    """Run backup cycle, then repeat hourly."""
    import argparse
    parser = argparse.ArgumentParser(description="Archive trading data to external drives")
    parser.add_argument("--once", action="store_true", help="Run once and exit (no hourly loop)")
    parser.add_argument("--report", action="store_true", help="Just print disk space report")
    args = parser.parse_args()
    if args.report:
        disk_space_report()
        return
    if args.once:
        run_backup_cycle()
        return
    while True:
        try:
            run_backup_cycle()
        except Exception as e:
            logger.error(f"Backup cycle error: {e}")
        logger.info("Next backup in 1 hour...")
        time.sleep(3600)


if __name__ == "__main__":
    main()
