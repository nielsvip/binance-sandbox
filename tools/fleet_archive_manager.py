#!/usr/bin/env python3
"""Low-priority fleet disk monitor and verified cold-archive manager.

Only explicit archive paths in FLEET_ARCHIVES are eligible. Active run data,
NPZ stores, live state, and unclassified files are never candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "reports" / "fleet_archive_manager.jsonl"
KEY = Path(os.environ.get("V15_ARCHIVE_GOOGLE_KEY", Path.home() / "Downloads/civil-ripple-439101-j3-d50fb6537cdb.json"))
GOOGLE_FOLDER_ID = os.environ.get("V15_ARCHIVE_GOOGLE_FOLDER_ID", "")
HOSTS = {"s1": "s1-int", "s2": "s2", "s5": "s5", "s6": "s6"}
DISK_WARN_MB = {"s1": 7000, "s2": 6000, "s5": 8000, "s6": 8000}
S6_SOURCE = "/home/niels/rebuild_scratch_s6/experience_cold/run34_35_20261022"
S5_MIRROR = "/home/niels/rebuild_scratch_s5/experience_cold"
S6_ARCHIVE = "/home/niels/rebuild_scratch_s6/s1_backups_archive_20261026.tar.zst"
S6_ARCHIVE_SHA256 = "95d23bff372926f17a63f3f79b00cbaf017a766a7ee5fcdd04228af2353be58e"
EXPERIENCE_SHA256 = {
    "SHA256SUMS": "70bf8e852930508aaed2b081095dc739e2968271f0d8f9039019e07ab5b65de7",
    "experience_CRYPTO_LONG_202610.db.zst": "6d451c5faa545621de1d425c06859aa3c189b21c8d0cae10ef0e96b5b3cdbd92",
    "experience_CRYPTO_LONG_202610.db.zst.manifest.json": "ab79b5d134529f88f3af05cf2d85196301496c8839c4232c2cab1a2b19d66d84",
    "experience_CRYPTO_SHORT_202610.db.zst": "b6bc3c8fb4543136b62f183163a9c9e4f0b6d81ccc3b20cc70bf28f8e776babf",
    "experience_CRYPTO_SHORT_202610.db.zst.manifest.json": "2749af844186ec7cc7df460cb96667b4b4578abb7415fa3bd21b845e4e379101",
    "experience_STOCKS_LONG_202610.db.zst": "212dd28648fcd11343cb2b736cd2820b75a54567fd4543200f14bcfc1f1049a6",
    "experience_STOCKS_LONG_202610.db.zst.manifest.json": "96d5cde35d10da081bf17c1ee43db282e04b567e564aa24cbcf82c6cbd3c73e1",
    "experience_STOCKS_SHORT_202610.db.zst": "5533d2c01320ef038d06edafe852e8da8b516b801693ecd9cd76643e8832872d",
    "experience_STOCKS_SHORT_202610.db.zst.manifest.json": "7ec2d16589f74a11a5da439fcc2708247356812046bd833b29767ec128f96d46",
}
EXTS = (Path("/Volumes/TOSHIBA_EXT"), Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"))


def _log(event: str, **fields) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(row, sort_keys=True)
    print(line, flush=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def _ssh(host: str, command: str, timeout: int = 90, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOSTS[host], command], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{host}: remote command failed rc={result.returncode}: {result.stderr[-300:]}")
    return result


def _free_mb(host: str) -> int:
    out = _ssh(host, "df -Pm /home | awk 'NR==2 {print $4}'").stdout.strip()
    return int(out)


def _tree_inventory(host: str, path: str) -> dict[str, tuple[int, str]]:
    py = "import hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); out={}; " \
         "[(lambda h,x: ([(h.update(b) for b in iter(lambda:f.read(1048576),b''))],out.__setitem__(str(x.relative_to(p)),(x.stat().st_size,h.hexdigest()))))(hashlib.sha256(),x) " \
         "for x in sorted(p.rglob('*')) if x.is_file() for f in [x.open('rb')]]; print(json.dumps(out,sort_keys=True))"
    command = "nice -n 19 ionice -c3 python3 -c %s %s" % (shlex.quote(py), shlex.quote(path))
    result = _ssh(host, command, timeout=900)
    return {k: tuple(v) for k, v in json.loads(result.stdout).items()}


def _open_handles(host: str, path: str) -> int:
    result = _ssh(host, "if ! command -v lsof >/dev/null 2>&1; then echo NO_LSOF; exit 3; fi; lsof +D %s 2>/dev/null | tail -n +2 | wc -l" % shlex.quote(path), timeout=180, check=False)
    if result.returncode or "NO_LSOF" in result.stdout:
        raise RuntimeError(f"{host}: cannot prove the archive has no open handles")
    return int(result.stdout.strip())


def _remove_verified_s6_duplicate(apply: bool) -> None:
    free = _free_mb("s6")
    if free >= DISK_WARN_MB["s6"]:
        _log("s6_duplicate_retained", free_mb=free, threshold_mb=DISK_WARN_MB["s6"], reason="disk headroom sufficient")
        return
    source = _tree_inventory("s6", S6_SOURCE)
    mirror = _tree_inventory("s5", S5_MIRROR)
    if not source or source != {k: v for k, v in mirror.items() if k in source}:
        raise RuntimeError("S5 mirror does not exactly match the S6 cold archive; source retained")
    if set(source) != set(EXPERIENCE_SHA256):
        raise RuntimeError("S6 archive file set changed; source retained")
    if any(source[name][1] != digest for name, digest in EXPERIENCE_SHA256.items()):
        raise RuntimeError("S6 archive SHA256 differs from the recorded verified set; source retained")
    handles = _open_handles("s6", S6_SOURCE)
    if handles:
        raise RuntimeError(f"S6 archive has {handles} open file handles; source retained")
    _log("s6_duplicate_verified", free_mb=free, files=len(source), mirror_host="s5", mirror_path=S5_MIRROR, apply=apply)
    if apply:
        py = "import pathlib,shutil; p=pathlib.Path(%s); assert str(p)==%s and p.is_dir() and not p.is_symlink(); shutil.rmtree(p)" % (repr(S6_SOURCE), repr(S6_SOURCE))
        _ssh("s6", "python3 -c %s" % shlex.quote(py))
        _log("s6_duplicate_removed", source=S6_SOURCE, retained_copy=f"s5:{S5_MIRROR}", free_mb=_free_mb("s6"))


def _mounts() -> list[Path]:
    out = []
    for path in EXTS:
        if path.is_dir() and os.path.ismount(path):
            free = shutil.disk_usage(path).free // (1024 * 1024 * 1024)
            if free >= 50:
                out.append(path)
    return out


def _make_local_archive(host: str, source: str, stage: Path) -> tuple[Path, str]:
    name = Path(source).name
    if not source.endswith("/") and Path(source).suffix == ".zst":
        dest = stage / name
        result = subprocess.run(["rsync", "-a", f"{HOSTS[host]}:{source}", str(dest)], capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(f"rsync failed for {host}:{source}: {result.stderr[-300:]}")
    else:
        dest = stage / (name + ".tar.zst")
        parent, leaf = os.path.split(source.rstrip("/"))
        remote = "set -o pipefail; tar -C %s -cf - -- %s | zstd -T1 -3 -c" % (shlex.quote(parent), shlex.quote(leaf))
        with dest.open("wb") as output:
            proc = subprocess.Popen(["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOSTS[host], "bash", "-lc", remote], stdout=subprocess.PIPE)
            for block in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                output.write(block)
            if proc.wait(timeout=3600):
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"remote compression failed for {host}:{source}")
        if subprocess.run(["zstd", "-t", str(dest)], capture_output=True).returncode:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"compressed archive failed integrity check for {host}:{source}")
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return dest, digest


def _archive_to_external(host: str, source: str, drive: Path, apply: bool) -> None:
    _log("external_archive_plan", host=host, source=source, destination=str(drive), apply=apply)
    if not apply:
        return
    free = shutil.disk_usage(drive).free
    with tempfile.TemporaryDirectory(prefix="fleet-archive-stage-") as stage_name:
        stage = Path(stage_name)
        local, digest = _make_local_archive(host, source, stage)
        if local.stat().st_size + 5 * 1024**3 > free:
            raise RuntimeError("external drive lacks safe staging headroom")
        dest = drive / "binance_archive" / "fleet_archive" / host / local.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["rclone", "copyto", str(local), str(dest)], capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(f"external archive transfer failed: {result.stderr[-300:]}")
        check = subprocess.run(["rclone", "check", str(local), str(dest)], capture_output=True, text=True, timeout=1800)
        if check.returncode:
            raise RuntimeError("external archive checksum verification failed")
        _log("external_archive_verified", source=f"{host}:{source}", destination=str(dest), sha256=digest)


def _drive_config(folder_id: str):
    tmp = tempfile.TemporaryDirectory(prefix="fleet-archive-rclone-")
    config = Path(tmp.name) / "rclone.conf"
    env = os.environ.copy()
    env["RCLONE_CONFIG"] = str(config)
    subprocess.run(["rclone", "config", "create", "fleetarchive", "drive", f"service_account_file={KEY}", "scope=drive", f"root_folder_id={folder_id}"], env=env, capture_output=True, check=True)
    os.chmod(config, 0o600)
    return tmp, env


def _archive_to_google(host: str, source: str, folder_id: str, apply: bool) -> None:
    if not KEY.is_file() or not folder_id:
        _log("google_archive_unavailable", key_present=KEY.is_file(), folder_id_configured=bool(folder_id))
        return
    name = Path(source.rstrip("/")).name
    _log("google_archive_plan", host=host, source=source, object=name, apply=apply)
    if not apply:
        return
    tmp, env = _drive_config(folder_id)
    try:
        probe = subprocess.run(["rclone", "lsd", "fleetarchive:"], env=env, capture_output=True, text=True, timeout=60)
        if probe.returncode:
            raise RuntimeError("Google Drive folder access failed; source retained")
        with tempfile.TemporaryDirectory(prefix="fleet-archive-stage-") as stage_name:
            stage = Path(stage_name)
            local, expected = _make_local_archive(host, source, stage)
            if local.stat().st_size + 5 * 1024**3 > shutil.disk_usage(stage).free:
                raise RuntimeError("Mac lacks safe staging headroom for Google Drive upload")
            remote = f"fleetarchive:{host}/{local.name}"
            put = subprocess.run(["rclone", "copyto", str(local), remote], env=env, capture_output=True, text=True, timeout=1800)
            if put.returncode:
                raise RuntimeError(f"Google Drive upload failed; source retained: {put.stderr[-300:]}")
            check = subprocess.run(["rclone", "check", str(local), remote], env=env, capture_output=True, text=True, timeout=600)
            if check.returncode:
                raise RuntimeError("Google Drive copy did not verify; source retained")
            _log("google_archive_verified", source=f"{host}:{source}", destination=remote, sha256=expected)
    finally:
        tmp.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--apply", action="store_true", help="perform verified transfers and the allowlisted S6 duplicate cleanup")
    parser.add_argument("--google-folder-id", default=GOOGLE_FOLDER_ID)
    args = parser.parse_args()
    statuses = {}
    for host in HOSTS:
        try:
            statuses[host] = _free_mb(host)
        except Exception as exc:
            statuses[host] = None
            _log("host_disk_probe_failed", host=host, error=type(exc).__name__)
    _log("disk_status", free_mb=statuses, warn_mb=DISK_WARN_MB)
    try:
        _remove_verified_s6_duplicate(args.apply)
    except Exception as exc:
        _log("s6_reclaim_skipped", error=str(exc))
    drives = _mounts()
    for host, source in (("s6", S6_ARCHIVE), ("s5", S5_MIRROR)):
        for drive in drives:
            try:
                _archive_to_external(host, source, drive, args.apply)
            except Exception as exc:
                _log("external_archive_failed", host=host, drive=str(drive), error=str(exc))
        try:
            _archive_to_google(host, source, args.google_folder_id, args.apply)
        except Exception as exc:
            _log("google_archive_failed", host=host, error=str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
