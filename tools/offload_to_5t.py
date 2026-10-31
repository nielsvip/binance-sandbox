"""Move bulky backups from the Mac and the servers onto the 5 TB drive (WD25), verified, then free the source.

USER 2026-10-31 (verbatim): "we need ALL those tools to go to 5T directly wihout putting in danger mac integrity ever
again (or any of the s1 2 5 6 servers".

A file is removed from its source ONLY after its copy on the drive matches it in size AND sha256 — the same rule the
NPZ storage law sets for any removal. Anything that fails a check stays where it is. Files modified in the last
--settle-min minutes are left alone (a board-guard tar is appended to for the whole of its run). The drive's health is
re-checked through tools/offload_target.py (child-process probe, hard timeout) before every batch, so a stalled or
dropped drive stops the move instead of hanging it — WD25 shares a USB hub with TOSHIBA_EXT, whose stalls have reset
that hub. Copies run under a timeout for the same reason.

  local   a directory on this Mac        -> WD25/binance_backups/mac/<path relative to the repo>
  remote  host:directory on a server     -> WD25/binance_backups/servers/<host>/<path relative to home>

Servers cannot write to a drive on the Mac's USB bus, so the Mac pulls; that is the only route to "5T directly" a
server has.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import offload_target as OT  # noqa: E402

SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
COPY_TIMEOUT_S = float(os.environ.get("OFFLOAD_COPY_TIMEOUT_S", "1800"))
HEALTH_EVERY_S = float(os.environ.get("OFFLOAD_HEALTH_EVERY_S", "30"))


def log(msg):
    try:
        print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}", flush=True)
    except OSError:
        pass


def sha256_local(path, chunk=8 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class Mover:
    def __init__(self, subdir, dry=False):
        self.subdir, self.dry = subdir, dry
        self.checked_at, self.target = 0.0, None
        self.moved = self.kept = self.bytes = 0
        self.problems = []

    def drive_ok(self, need=0):
        if self.target is None or time.time() - self.checked_at > HEALTH_EVERY_S:
            self.target, why = OT.healthy_target(self.subdir, need)
            self.checked_at = time.time()
            if self.target is None:
                self.problems.append(f"drive unhealthy: {why}")
                log(f"STOP: {why} — nothing further is moved, nothing unverified is removed")
                return False
        return True

    def _dest(self, rel):
        d = self.target / rel
        if not self.dry:   # a dry run touches nothing, not even a directory on the drive
            d.parent.mkdir(parents=True, exist_ok=True)
        return d

    # ---- local (Mac) ----
    def move_local(self, src, rel):
        size = src.stat().st_size
        if not self.drive_ok(size):
            return False
        dest = self._dest(rel)
        if self.dry:
            log(f"DRY would move {src} -> {dest} ({size / 1e6:.1f} MB)")
            return True
        tmp = dest.with_name(dest.name + ".offload_tmp")
        try:
            subprocess.run(["cp", "-p", str(src), str(tmp)], check=True, timeout=COPY_TIMEOUT_S, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            self.problems.append(f"{src}: copy failed {type(exc).__name__}")
            self.kept += 1
            try:
                tmp.unlink()
            except OSError:
                pass
            return True
        ok = tmp.stat().st_size == size and sha256_local(tmp) == sha256_local(src)
        if not ok:
            self.problems.append(f"{src}: copy did not verify — source kept")
            self.kept += 1
            tmp.unlink()
            return True
        os.replace(tmp, dest)
        src.unlink()
        self.moved += 1
        self.bytes += size
        return True

    # ---- remote (server) ----
    def move_remote(self, host, path, rel, size):
        if not self.drive_ok(size):
            return False
        dest = self._dest(rel)
        if self.dry:
            log(f"DRY would move {host}:{path} -> {dest} ({size / 1e6:.1f} MB)")
            return True
        tmp = dest.with_name(dest.name + ".offload_tmp")
        try:
            subprocess.run(["rsync", "-a", "--timeout=120", "-e", " ".join(SSH), f"{host}:{path}", str(tmp)],
                           check=True, timeout=COPY_TIMEOUT_S, capture_output=True)
            remote = subprocess.run(SSH + [host, f"sha256sum {shlex.quote(path)} | cut -d' ' -f1"], check=True,
                                    timeout=600, capture_output=True, text=True).stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            self.problems.append(f"{host}:{path}: copy failed {type(exc).__name__}")
            self.kept += 1
            try:
                tmp.unlink()
            except OSError:
                pass
            return True
        ok = tmp.stat().st_size == size and len(remote) == 64 and sha256_local(tmp) == remote
        if not ok:
            self.problems.append(f"{host}:{path}: copy did not verify — source kept")
            self.kept += 1
            tmp.unlink()
            return True
        os.replace(tmp, dest)
        try:
            subprocess.run(SSH + [host, f"rm -f {shlex.quote(path)}"], check=True, timeout=60, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            self.problems.append(f"{host}:{path}: verified on the drive but could not be removed: {type(exc).__name__}")
        self.moved += 1
        self.bytes += size
        return True


def move_remote_batch(host, rdir, patterns, settle_s, dry=False, max_gb=0.0, min_bytes=0):
    """One sha256 manifest computed ON the server, one rsync, every file verified here, one removal of the verified.
    A file that is missing, short or mismatched on the drive is never removed from the server."""
    mover = Mover(f"servers/{host}", dry)
    files = list(list_remote(host, rdir, patterns, settle_s, min_bytes))
    if max_gb:
        keep, total = [], 0
        for size, path in files:
            if total + size > max_gb * 1024 ** 3 and keep:
                break
            keep.append((size, path))
            total += size
        files = keep
    total = sum(s for s, _ in files)
    log(f"{host}:{rdir}: {len(files)} settled files, {total / 1024 ** 3:.2f} GB to move")
    if not files or not mover.drive_ok(total):
        return mover
    if dry:
        mover.moved, mover.bytes = len(files), total
        return mover
    sizes = {path: size for size, path in files}
    listing = "\0".join(sizes) + "\0"
    man = subprocess.run(SSH + [host, "cd ~ && xargs -0 -r sha256sum"], input=listing, capture_output=True, text=True, timeout=3600)
    manifest = {}
    for line in man.stdout.splitlines():
        digest, _, path = line.partition("  ")
        if len(digest) == 64 and path in sizes:
            manifest[path] = digest
    lst = mover.target / f".offload_list_{os.getpid()}"
    lst.write_text("\n".join(manifest) + "\n")
    try:
        r = subprocess.run(["rsync", "-a", "--timeout=120", "--files-from", str(lst), "-e", " ".join(SSH), f"{host}:", str(mover.target) + "/"],
                           capture_output=True, text=True, timeout=COPY_TIMEOUT_S * 4)
        if r.returncode not in (0, 23, 24):
            mover.problems.append(f"rsync rc={r.returncode}: {r.stderr[-160:]}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        mover.problems.append(f"rsync {type(exc).__name__}")
    finally:
        try:
            lst.unlink()
        except OSError:
            pass
    verified = []
    for path, digest in manifest.items():
        local = mover.target / path
        try:
            if local.stat().st_size == sizes[path] and sha256_local(local) == digest:
                verified.append(path)
                mover.bytes += sizes[path]
            else:
                mover.kept += 1
        except OSError:
            mover.kept += 1
    mover.kept += len(sizes) - len(manifest)   # files the server could not hash are never removed
    if verified:
        rm = subprocess.run(SSH + [host, "cd ~ && xargs -0 -r rm -f"], input="\0".join(verified) + "\0", capture_output=True, text=True, timeout=900)
        if rm.returncode:
            mover.problems.append(f"verified on the drive but removal on {host} failed: {rm.stderr[-160:]}")
    mover.moved = len(verified)
    return mover


def iter_local(src_dir, patterns, settle_s, min_bytes=0):
    now = time.time()
    for p in sorted(Path(src_dir).rglob("*")):
        if not p.is_file() or p.name.endswith(".offload_tmp"):
            continue
        if min_bytes and p.stat().st_size < min_bytes:
            continue
        if patterns and not any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
            continue
        if now - p.stat().st_mtime < settle_s:
            continue
        yield p


def list_remote(host, src_dir, patterns, settle_s, min_bytes=0):
    names = " -o ".join(f"-name {shlex.quote(x)}" for x in patterns) if patterns else ""
    clause = ("\\( " + names + " \\) ") if names else ""   # built outside the f-string: no backslash in an expression (py3.11)
    size_clause = f"-size +{int(min_bytes // 1024)}k " if min_bytes else ""
    cmd = (f"cd ~ && find {shlex.quote(src_dir)} -type f {clause}{size_clause}"
           f"-mmin +{max(1, int(settle_s // 60))} -printf '%s\\t%p\\n'")
    out = subprocess.run(SSH + [host, cmd], capture_output=True, text=True, timeout=300).stdout
    for line in out.splitlines():
        size, _, path = line.partition("\t")
        if path and not path.endswith(".offload_tmp"):
            yield int(size), path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("local", "remote"))
    ap.add_argument("src", help="local dir (relative to the repo or absolute) or host:dir (dir relative to the server home)")
    ap.add_argument("--pattern", action="append", default=[], help="filename glob; repeat for several")
    ap.add_argument("--settle-min", type=float, default=15.0, help="leave files modified more recently than this")
    ap.add_argument("--max-gb", type=float, default=0.0, help="stop after moving this much (0 = no limit)")
    ap.add_argument("--min-mb", type=float, default=0.0, help="only files at least this large")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    started = time.time()
    if a.mode == "local":
        src = Path(a.src) if Path(a.src).is_absolute() else ROOT / a.src
        mover = Mover("mac", a.dry_run)
        for p in iter_local(src, a.pattern, a.settle_min * 60, int(a.min_mb * 1024 * 1024)):
            rel = p.relative_to(ROOT) if str(p).startswith(str(ROOT)) else Path(*p.parts[1:])
            if not mover.move_local(p, rel):
                break
            if a.max_gb and mover.bytes >= a.max_gb * 1024 ** 3:
                break
    else:
        host, _, rdir = a.src.partition(":")
        mover = move_remote_batch(host, rdir, a.pattern, a.settle_min * 60, a.dry_run, a.max_gb, int(a.min_mb * 1024 * 1024))
    report = {"mode": a.mode, "src": a.src, "moved": mover.moved, "kept": mover.kept, "gb": round(mover.bytes / 1024 ** 3, 2),
              "minutes": round((time.time() - started) / 60, 1), "problems": mover.problems[:20], "dry_run": a.dry_run}
    log(json.dumps(report))
    return 0 if not mover.problems else 1


if __name__ == "__main__":
    sys.exit(main())
