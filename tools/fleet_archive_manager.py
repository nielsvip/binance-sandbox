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
REPORT = Path.home() / "Library" / "Logs" / "fleet_archive_manager.jsonl"
STATE = Path.home() / "Library" / "Caches" / "com.binance.fleetarchive" / "verified.json"
KEY = Path(os.environ.get("V15_ARCHIVE_GOOGLE_KEY", Path.home() / "Downloads/civil-ripple-439101-j3-d50fb6537cdb.json"))
GOOGLE_FOLDER_ID = os.environ.get("V15_ARCHIVE_GOOGLE_FOLDER_ID", "")
HOSTS = {"s1": "s1-int", "s2": "s2", "s5": "s5", "s6": "s6"}
DISK_WARN_MB = {"s1": 7000, "s2": 6000, "s5": 8000, "s6": 8000}
S6_SOURCE = "/home/niels/rebuild_scratch_s6/experience_cold/run34_35_20261022"
S5_MIRROR = "/home/niels/rebuild_scratch_s5/experience_cold"
S1_STAGING = "/home/niels/ext_staging"
S5_ARCHIVE_ROOT = "/home/niels/rebuild_scratch_s5/fleet_archives_from_s1"
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
    py = """import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1])
def digest(x):
 h=hashlib.sha256()
 with x.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
out={str(x.relative_to(p)):(x.stat().st_size,digest(x)) for x in sorted(p.rglob('*')) if x.is_file()}
print(json.dumps(out,sort_keys=True))"""
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
    present = _ssh("s6", "test -d %s" % shlex.quote(S6_SOURCE), check=False)
    if present.returncode:
        _log("s6_duplicate_absent", retained_copy=f"s5:{S5_MIRROR}", free_mb=free)
        return
    state = _state_load()
    proof = state.get("s6_duplicate_proof") or {}
    source_sig = _source_signature("s6", S6_SOURCE)
    mirror_sig = _source_signature("s5", S5_MIRROR)
    if proof.get("source_sig") != source_sig or proof.get("mirror_sig") != mirror_sig:
        source = _tree_inventory("s6", S6_SOURCE)
        mirror = _tree_inventory("s5", S5_MIRROR)
        if not source or source != {k: v for k, v in mirror.items() if k in source}:
            raise RuntimeError("S5 mirror does not exactly match the S6 cold archive; source retained")
        if set(source) != set(EXPERIENCE_SHA256):
            raise RuntimeError("S6 archive file set changed; source retained")
        if any(source[name][1] != digest for name, digest in EXPERIENCE_SHA256.items()):
            raise RuntimeError("S6 archive SHA256 differs from the recorded verified set; source retained")
        state["s6_duplicate_proof"] = {"source_sig": source_sig, "mirror_sig": mirror_sig, "sha256_verified": True}
        _state_save(state)
    handles = _open_handles("s6", S6_SOURCE)
    if handles:
        raise RuntimeError(f"S6 archive has {handles} open file handles; source retained")
    _log("s6_duplicate_verified", free_mb=free, files=len(source), mirror_host="s5", mirror_path=S5_MIRROR, apply=apply)
    if apply:
        py = "import pathlib,shutil; p=pathlib.Path(%s); assert str(p)==%s and p.is_dir() and not p.is_symlink(); shutil.rmtree(p)" % (repr(S6_SOURCE), repr(S6_SOURCE))
        _ssh("s6", "python3 -c %s" % shlex.quote(py))
        _log("s6_duplicate_removed", source=S6_SOURCE, retained_copy=f"s5:{S5_MIRROR}", free_mb=_free_mb("s6"))


def _staged_s1_batches() -> list[dict]:
    py = """import json,pathlib
root=pathlib.Path('/home/niels/ext_staging')
try: current=pathlib.Path(pathlib.Path('/home/niels/v15_current_progress_dir.txt').read_text().strip()).resolve().parent
except Exception: current=None
out=[]
for p in sorted(root.iterdir()):
 if not p.is_dir() or p.is_symlink() or not (p/'manifest.json').is_file(): continue
 try: m=json.loads((p/'manifest.json').read_text())
 except Exception: continue
 srcs=[pathlib.Path(x.get('src','')).resolve() for x in m.get('files',[]) if x.get('src')]
 if current is None or not srcs or any(x==current or current in x.parents for x in srcs): continue
 out.append({'name':p.name,'bytes':int(m.get('total_bytes',0)),'sources':[str(x) for x in srcs]})
print(json.dumps(out))"""
    out = _ssh("s1", "nice -n 19 python3 -c %s" % shlex.quote(py), timeout=120).stdout
    return json.loads(out)


def _verify_tar_against_stage(batch: str, archive: str) -> None:
    source = subprocess.Popen(["ssh", "-S", "none", "-o", "BatchMode=yes", "s5", "nice -n 19 ionice -c3 zstd -dc %s" % shlex.quote(archive)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    compare = subprocess.Popen(["ssh", "-S", "none", "-o", "BatchMode=yes", "s1-int", "nice -n 19 ionice -c3 tar -df - -C %s" % shlex.quote(S1_STAGING)], stdin=source.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    source.stdout.close()
    _, compare_err = compare.communicate(timeout=1800)
    source_err = source.stderr.read()
    source_rc = source.wait(timeout=60)
    if source_rc or compare.returncode:
        raise RuntimeError(f"staged archive content comparison failed: {source_err.decode(errors='replace')[-200:]} {compare_err.decode(errors='replace')[-200:]}")


def _archive_s1_staging(apply: bool) -> None:
    free = _free_mb("s1")
    if free >= DISK_WARN_MB["s1"]:
        return
    for batch in _staged_s1_batches():
        name = batch["name"]
        if not name or "/" in name or name in (".", ".."):
            continue
        source = f"{S1_STAGING}/{name}"
        handles = _open_handles("s1", source)
        if handles:
            _log("s1_stage_retained", batch=name, open_handles=handles)
            continue
        free_s5 = _free_mb("s5")
        if free_s5 * 1024**2 < int(batch["bytes"]) + 5 * 1024**3:
            _log("s1_stage_retained", batch=name, reason="S5 destination headroom", s5_free_mb=free_s5)
            continue
        dest = f"{S5_ARCHIVE_ROOT}/s1_{name}.tar.zst"
        part = dest + ".part"
        _log("s1_stage_archive_plan", batch=name, bytes=batch["bytes"], destination=f"s5:{dest}", apply=apply)
        if not apply:
            continue
        if _ssh("s5", "test -f %s" % shlex.quote(dest), check=False).returncode == 0:
            _log("s1_stage_retained", batch=name, reason="destination already exists; manual integrity review required")
            continue
        prep = "import pathlib; d=pathlib.Path(%s); d.mkdir(parents=True,exist_ok=True); pathlib.Path(%s).unlink(missing_ok=True)" % (repr(S5_ARCHIVE_ROOT), repr(part))
        _ssh("s5", "python3 -c %s" % shlex.quote(prep))
        remote_tar = "nice -n 19 ionice -c3 tar -C %s -cf - -- %s" % (shlex.quote(S1_STAGING), shlex.quote(name))
        src = subprocess.Popen(["ssh", "-S", "none", "-o", "BatchMode=yes", "s1-int", "bash", "-lc", remote_tar], stdout=subprocess.PIPE)
        zst = subprocess.Popen(["nice", "-n", "19", "/opt/homebrew/bin/zstd", "-T1", "-3", "-c"], stdin=src.stdout, stdout=subprocess.PIPE)
        src.stdout.close()
        put = subprocess.Popen(["ssh", "-S", "none", "-o", "BatchMode=yes", "s5", "cat > %s" % shlex.quote(part)], stdin=subprocess.PIPE)
        digest = hashlib.sha256()
        for block in iter(lambda: zst.stdout.read(1024 * 1024), b""):
            digest.update(block)
            put.stdin.write(block)
        put.stdin.close()
        zst.stdout.close()
        src_rc, zst_rc, put_rc = src.wait(timeout=1800), zst.wait(timeout=1800), put.wait(timeout=1800)
        if src_rc or zst_rc or put_rc:
            cleanup = "import pathlib; pathlib.Path(%s).unlink(missing_ok=True)" % repr(part)
            _ssh("s5", "python3 -c %s" % shlex.quote(cleanup), check=False)
            raise RuntimeError(f"S1 staged archive stream failed for {name}: {src_rc}/{zst_rc}/{put_rc}")
        sha = digest.hexdigest()
        remote_check = _ssh("s5", "zstd -t %s >/dev/null && sha256sum %s" % (shlex.quote(part), shlex.quote(part)))
        if remote_check.stdout.split()[0] != sha:
            raise RuntimeError(f"S1 staged archive SHA256 mismatch for {name}; source retained")
        _verify_tar_against_stage(name, part)
        if _open_handles("s1", source):
            raise RuntimeError(f"S1 staged batch {name} became busy during archive; source retained")
        _ssh("s5", "mv %s %s" % (shlex.quote(part), shlex.quote(dest)))
        py = "import pathlib,shutil; p=pathlib.Path(%s); assert p.parent==pathlib.Path(%s) and p.is_dir() and not p.is_symlink(); shutil.rmtree(p)" % (repr(source), repr(S1_STAGING))
        _ssh("s1", "python3 -c %s" % shlex.quote(py))
        _log("s1_stage_archived_and_removed", source=source, destination=f"s5:{dest}", sha256=sha, free_mb=_free_mb("s1"))


def _mounts() -> list[Path]:
    out = []
    for path in EXTS:
        if path.is_dir() and os.path.ismount(path):
            free = shutil.disk_usage(path).free // (1024 * 1024 * 1024)
            if free >= 50:
                out.append(path)
    return out


def _source_signature(host: str, source: str) -> str:
    py = """import json,pathlib,sys
p=pathlib.Path(sys.argv[1])
xs=[p] if p.is_file() else sorted(x for x in p.rglob('*') if x.is_file())
print(json.dumps([(str(x.relative_to(p.parent)),x.stat().st_size,x.stat().st_mtime_ns,x.stat().st_ctime_ns) for x in xs],separators=(',',':')))"""
    out = _ssh(host, "python3 -c %s %s" % (shlex.quote(py), shlex.quote(source)), timeout=120).stdout
    return hashlib.sha256(out.encode()).hexdigest()


def _state_load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _state_save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


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
        remote = "set -o pipefail; nice -n 19 ionice -c3 tar -C %s -cf - -- %s | nice -n 19 ionice -c3 zstd -T1 -3 -c" % (shlex.quote(parent), shlex.quote(leaf))
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
    if str(dest).endswith(".zst") and subprocess.run(["zstd", "-t", str(dest)], capture_output=True).returncode:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"source archive failed zstd integrity check for {host}:{source}")
    h = hashlib.sha256()
    with dest.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest()
    return dest, digest


def _archive_to_external(host: str, source: str, drive: Path, apply: bool) -> None:
    state = _state_load()
    key = f"{host}:{source}->{drive}"
    signature = _source_signature(host, source)
    if state.get(key) == signature:
        _log("external_archive_current", host=host, source=source, drive=str(drive))
        return
    _log("external_archive_plan", host=host, source=source, destination=str(drive), apply=apply)
    if not apply:
        return
    if shutil.disk_usage(Path(tempfile.gettempdir())).free < 8 * 1024**3:
        raise RuntimeError("Mac staging disk has less than 8 GB free")
    free = shutil.disk_usage(drive).free
    with tempfile.TemporaryDirectory(prefix="fleet-archive-stage-") as stage_name:
        stage = Path(stage_name)
        local, digest = _make_local_archive(host, source, stage)
        if local.stat().st_size + 5 * 1024**3 > free:
            raise RuntimeError("external drive lacks safe staging headroom")
        dest = drive / "binance_archive" / "fleet_archive" / host / f"{local.stem}.{digest[:12]}{local.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["rclone", "copyto", str(local), str(dest)], capture_output=True, text=True, timeout=1800)
        if result.returncode:
            raise RuntimeError(f"external archive transfer failed: {result.stderr[-300:]}")
        check = subprocess.run(["rclone", "check", str(local), str(dest)], capture_output=True, text=True, timeout=1800)
        if check.returncode:
            raise RuntimeError("external archive checksum verification failed")
        state[key] = signature
        _state_save(state)
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
    state = _state_load()
    key = f"{host}:{source}->google:{folder_id}"
    signature = _source_signature(host, source)
    if state.get(key) == signature:
        _log("google_archive_current", host=host, source=source)
        return
    name = Path(source.rstrip("/")).name
    _log("google_archive_plan", host=host, source=source, object=name, apply=apply)
    if not apply:
        return
    if shutil.disk_usage(Path(tempfile.gettempdir())).free < 8 * 1024**3:
        raise RuntimeError("Mac staging disk has less than 8 GB free")
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
            remote = f"fleetarchive:{host}/{local.stem}.{expected[:12]}{local.suffix}"
            put = subprocess.run(["rclone", "copyto", str(local), remote], env=env, capture_output=True, text=True, timeout=1800)
            if put.returncode:
                raise RuntimeError(f"Google Drive upload failed; source retained: {put.stderr[-300:]}")
            check = subprocess.run(["rclone", "check", str(local), remote], env=env, capture_output=True, text=True, timeout=600)
            if check.returncode:
                raise RuntimeError("Google Drive copy did not verify; source retained")
            state[key] = signature
            _state_save(state)
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
        _archive_s1_staging(args.apply)
    except Exception as exc:
        _log("s1_archive_skipped", error=str(exc))
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
