#!/usr/bin/env python3
"""
Automated backup archival to Google Drive with external drive fallback.
Daily job: uploads 50 files (~2GB) to avoid API throttling.
If Google Drive >70% capacity, offloads to connected external drives.
"""

import hashlib, json, os, shlex, sys, subprocess, shutil, stat
from datetime import datetime, timedelta
from pathlib import Path

BACKUP_DIR = "/Users/niels/Documents/binance/backups"
CREDS_JSON = "/Users/niels/Downloads/civil-ripple-439101-j3-d50fb6537cdb.json"
LOG_FILE = "/Users/niels/Documents/binance/data/reports/gdrive_backup.log"
RCLONE_CONFIG = "/tmp/rclone_gdrive_backup.conf"
GOOGLE_FOLDER_ID = os.environ.get("V15_ARCHIVE_GOOGLE_FOLDER_ID", "").strip()
DRIVE_REMOTE = os.environ.get("V15_ARCHIVE_RCLONE_REMOTE", "").strip()
DRIVE_CONFIG_PATH = Path(os.environ.get("V15_ARCHIVE_RCLONE_CONFIG", Path.home() / ".config" / "rclone" / "rclone.conf"))
DRIVE_SSH_HOST = os.environ.get("V15_ARCHIVE_RCLONE_HOST", "").strip()
DRIVE_PREFIX = os.environ.get("V15_ARCHIVE_RCLONE_PREFIX", "binance_backups").strip("/")
RCLONE_REMOTE = DRIVE_REMOTE or "binance-gdrive"

# External drive mount points (checked in order, first available wins)
EXT_DRIVE_MOUNTS = [
    "/Volumes/TOSHIBA_EXT",
    "/Volumes/SSD2T",
    "/Volumes/E3T",
    "/Volumes/WD25",
]
EXT_BACKUP_FOLDER = "binance_backups_archive"
GDRIVE_CAPACITY_THRESHOLD = 0.70  # Fallback if >70% capacity
EXT_DRIVE_MIN_FREE_GB = 50  # Require 50GB free on ext drive

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
    global RCLONE_CONFIG, RCLONE_REMOTE
    if DRIVE_REMOTE:
        RCLONE_REMOTE = DRIVE_REMOTE
        if DRIVE_SSH_HOST:
            probe = rclone_on_server(["lsd", f"{RCLONE_REMOTE}:"])
            if probe.returncode:
                log(f"Server-side Google Drive remote unavailable: {probe.stderr.decode(errors='replace')[-300:]}", "ERROR")
                return False
            return True
        if not DRIVE_CONFIG_PATH.is_file():
            log("OAuth rclone config is missing", "ERROR")
            return False
        os.chmod(DRIVE_CONFIG_PATH, 0o600)
        RCLONE_CONFIG = str(DRIVE_CONFIG_PATH)
        RCLONE_REMOTE = DRIVE_REMOTE
        return True
    if not Path(CREDS_JSON).exists():
        log("ERROR: Credentials not found", "ERROR")
        return False
    if not GOOGLE_FOLDER_ID:
        log("Google Drive archive skipped: V15_ARCHIVE_GOOGLE_FOLDER_ID is not configured", "WARN")
        return False

    Path(RCLONE_CONFIG).write_text(f"""[binance-gdrive]
type = drive
service_account_file = {CREDS_JSON}
scope = drive
root_folder_id = {GOOGLE_FOLDER_ID}
""")
    os.chmod(RCLONE_CONFIG, 0o600)
    return True

def rclone_on_server(args, timeout=300, stdin=None):
    command = '"$HOME/.local/bin/rclone" ' + " ".join(shlex.quote(str(arg)) for arg in args)
    return subprocess.run(["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=10", DRIVE_SSH_HOST, command], stdin=stdin, capture_output=True, timeout=timeout)

def sha256_file(fpath):
    digest = hashlib.sha256()
    with open(fpath, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def get_old_files(days=KEEP_LOCAL_DAYS, max_count=MAX_FILES_PER_RUN):
    cutoff = datetime.now() - timedelta(days=days)
    old_files = []
    total_size = 0

    try:
        for fpath in sorted(Path(BACKUP_DIR).rglob("*.json")):
            if len(old_files) >= max_count or total_size > MAX_SIZE_GB * 1024**3:
                break
            if any("untouchable" in part.lower() for part in fpath.parts) or fpath.stat().st_flags & getattr(stat, "UF_IMMUTABLE", 2):
                log(f"Preserving protected backup {fpath.relative_to(BACKUP_DIR)}", "WARN")
                continue

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
        before = fpath.stat()
        digest = sha256_file(fpath)
        remote_name = f"{fpath.stem}_{digest[:16]}{fpath.suffix}"
        remote = f"{RCLONE_REMOTE}:{DRIVE_PREFIX}/{remote_name}"
        if DRIVE_SSH_HOST:
            remote_dir = f"{RCLONE_REMOTE}:{DRIVE_PREFIX}"
            made = rclone_on_server(["mkdir", remote_dir])
            if made.returncode:
                log(f"Drive directory creation failed for {fpath.name}: {made.stderr.decode(errors='replace')[-300:]}", "WARN")
                return False
            with fpath.open("rb") as stream:
                result = rclone_on_server(["rcat", remote, "--drive-upload-cutoff=64M", "--drive-chunk-size=64M"], timeout=86400, stdin=stream)
            if result.returncode:
                detail = (result.stderr + result.stdout).decode(errors="replace")
                log(f"Server-side Drive upload failed rc={result.returncode} for {fpath.name}: {detail[-400:]}", "WARN")
                return False
            local_md5 = hashlib.md5()
            with fpath.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    local_md5.update(block)
            checked = rclone_on_server(["md5sum", remote], timeout=600)
            fields = checked.stdout.decode(errors="replace").split()
            after = fpath.stat()
            if checked.returncode or not fields or fields[0].lower() != local_md5.hexdigest() or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or sha256_file(fpath) != digest:
                log(f"Server-side Drive checksum/source-stability failed for {fpath.name}; keeping local", "WARN")
                return False
            log(f"Verified {fpath.name} sha256={digest} as {remote}")
            return True
        result = subprocess.run([
            "rclone", "copyto", str(fpath),
            remote,
            "--config", RCLONE_CONFIG,
            "--no-update-modtime",
            "--checkers=1", "--transfers=1", "--retries=1"
        ], capture_output=True, timeout=120)
        if result.returncode:
            log(f"Upload error {fpath.name}: {result.stderr.decode(errors='replace')[-300:]}", "WARN")
            return False
        checked = subprocess.run([
            "rclone", "check", str(fpath), remote,
            "--config", RCLONE_CONFIG,
            "--checkers=1", "--one-way"
        ], capture_output=True, timeout=120)
        if checked.returncode:
            log(f"Remote checksum verification failed for {fpath.name}: {checked.stderr.decode(errors='replace')[-300:]}", "WARN")
            return False
        log(f"Verified {fpath.name} sha256={digest} as {remote}")
        return True
    except Exception as e:
        log(f"Upload error {fpath.name}: {e}", "WARN")
        return False

def get_gdrive_capacity():
    """Check Google Drive usage. Returns (used_gb, total_gb, percent_used) or None if unavailable."""
    try:
        if DRIVE_SSH_HOST:
            result = rclone_on_server(["about", f"{RCLONE_REMOTE}:", "--json"], timeout=60)
        else:
            result = subprocess.run([
                "rclone", "about", f"{RCLONE_REMOTE}:", "--config", RCLONE_CONFIG, "--json"
            ], capture_output=True, timeout=60)
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            used = data.get("used", 0) / (1024**3)
            total = data.get("total", 1)  # Avoid div by zero
            total_gb = total / (1024**3) if total > 0 else 1
            percent = (used / total_gb) if total_gb > 0 else 0
            return (used, total_gb, percent)
    except Exception as e:
        log(f"Could not check Google Drive capacity: {e}", "WARN")
    return None

def find_available_ext_drive():
    """Find first available external drive with sufficient free space. Returns path or None."""
    for mount in EXT_DRIVE_MOUNTS:
        mount_path = Path(mount)
        if not mount_path.exists() or not os.path.ismount(mount_path):
            continue
        try:
            stat = shutil.disk_usage(mount_path)
            free_gb = stat.free / (1024**3)
            if free_gb >= EXT_DRIVE_MIN_FREE_GB:
                return mount_path
            else:
                log(f"⚠ {mount}: insufficient space ({free_gb:.1f}GB < {EXT_DRIVE_MIN_FREE_GB}GB)", "WARN")
        except Exception as e:
            log(f"⚠ Could not check {mount}: {e}", "WARN")
    return None

def copy_to_ext_drive(fpath, ext_drive_path):
    """Copy backup file to external drive with verification. Returns True if successful."""
    try:
        dest_dir = ext_drive_path / EXT_BACKUP_FOLDER
        dest_dir.mkdir(parents=True, exist_ok=True)

        digest = sha256_file(fpath)
        dest_file = dest_dir / f"{fpath.stem}_{digest[:16]}{fpath.suffix}"

        shutil.copy2(fpath, dest_file)

        # Verify
        if sha256_file(dest_file) == digest and sha256_file(fpath) == digest:
            log(f"✓ Copied to {ext_drive_path.name}: {fpath.name}")
            return True
        else:
            log(f"⚠ Verify failed on {ext_drive_path.name} for {fpath.name}", "WARN")
            try:
                dest_file.unlink()
            except:
                pass
            return False
    except Exception as e:
        log(f"⚠ Copy to {ext_drive_path.name} failed for {fpath.name}: {e}", "WARN")
        return False

def archive_to_gcs(fpath):
    try:
        before = fpath.stat()
        digest = sha256_file(fpath)
        relative = fpath.relative_to(Path(BACKUP_DIR)).as_posix()
        object_name = f"mac/backups_from_drive_job/{relative}_{digest[:16]}.tar.zst"
        python = Path("/opt/anaconda3/envs/binance_env/bin/python")
        if not python.is_file():
            python = Path(sys.executable)
        result = subprocess.run([str(python), str(Path(__file__).resolve().with_name("gcs_cold_store.py")), "push", "--local", str(fpath), "--name", object_name],
                                capture_output=True, text=True, timeout=86400)
        if result.returncode:
            log(f"GCS archive failed for {fpath.name}: {result.stderr[-300:]}", "WARN")
            return False
        rows = [line for line in result.stdout.splitlines() if line.startswith("{")]
        receipt = json.loads(rows[-1]) if rows else {}
        after = fpath.stat()
        if receipt.get("verified") is not True or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            log(f"GCS verification/source-stability failed for {fpath.name}; keeping local", "WARN")
            return False
        log(f"Verified GCS archive for {fpath.name}: gs://{receipt.get('bucket')}/{object_name}")
        return True
    except Exception as e:
        log(f"GCS archive failed for {fpath.name}: {e}", "WARN")
        return False

def main():
    log("=" * 60)
    log("Starting Google Drive backup archival")

    drive_ready = setup_rclone()
    gdrive_status = get_gdrive_capacity() if drive_ready else None
    use_ext_drive_fallback = False
    ext_drive_path = None
    use_gcs_fallback = not drive_ready

    if gdrive_status:
        used_gb, total_gb, percent = gdrive_status
        log(f"Google Drive: {used_gb:.1f}GB / {total_gb:.1f}GB ({percent*100:.1f}%)")

        if percent > GDRIVE_CAPACITY_THRESHOLD:
            log(f"⚠ Google Drive >70% capacity. Checking external drives...", "WARN")
            ext_drive_path = find_available_ext_drive()
            if ext_drive_path:
                log(f"✓ Using fallback: {ext_drive_path.name}")
                use_ext_drive_fallback = True
            else:
                use_gcs_fallback = True
                log("No external drive available; using the verified GCS cold store", "WARN")
    else:
        log("Google Drive unavailable; checking external drives, then GCS", "WARN")
        ext_drive_path = find_available_ext_drive()
        if ext_drive_path:
            log(f"✓ Using external drive: {ext_drive_path.name}")
            use_ext_drive_fallback = True
            use_gcs_fallback = False
        else:
            use_gcs_fallback = True

    # Probe Google Drive only if not using fallback
    if drive_ready and not use_ext_drive_fallback and not use_gcs_fallback:
        try:
            if DRIVE_SSH_HOST:
                probe = rclone_on_server(["lsd", f"{RCLONE_REMOTE}:"])
            else:
                probe = subprocess.run(["rclone", "lsd", f"{RCLONE_REMOTE}:", "--config", RCLONE_CONFIG], capture_output=True, timeout=60)
            if probe.returncode:
                log(f"Google Drive destination is not accessible: {probe.stderr.decode(errors='replace')[-300:]}", "WARN")
                ext_drive_path = find_available_ext_drive()
                use_ext_drive_fallback = bool(ext_drive_path)
                use_gcs_fallback = not use_ext_drive_fallback
        except Exception as e:
            log(f"Google Drive destination probe failed: {e}", "WARN")
            ext_drive_path = find_available_ext_drive()
            use_ext_drive_fallback = bool(ext_drive_path)
            use_gcs_fallback = not use_ext_drive_fallback

    old_files = get_old_files()
    if not old_files:
        log("✓ No old backups found (all recent)")
        return 0

    total_size_gb = sum(s for _, s in old_files) / (1024**3)
    log(f"Found {len(old_files)} old files (~{total_size_gb:.1f}GB)")

    uploaded = 0
    for fpath, fsize in old_files:
        success = False

        if use_ext_drive_fallback and ext_drive_path:
            if copy_to_ext_drive(fpath, ext_drive_path):
                success = True
        elif drive_ready and not use_gcs_fallback:
            if upload_file(fpath):
                success = True
        if not success:
            success = archive_to_gcs(fpath)

        if success:
            try:
                fpath.unlink()
                uploaded += 1
            except Exception as e:
                log(f"Delete failed {fpath.name}: {e}", "WARN")
        else:
            log(f"⚠ {fpath.name} (keeping local)", "WARN")

    log("=" * 60)
    dest = f"{ext_drive_path.name}" if use_ext_drive_fallback else "Google Drive"
    log(f"✓ Archival complete: {uploaded}/{len(old_files)} files to {dest}")
    log("=" * 60)
    return 0

if __name__ == "__main__":
    sys.exit(main())
