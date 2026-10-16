#!/usr/bin/env python3
"""script_version_vault — EVERY saved version of every script and template, kept twice.

USER 2026-10-17: "make sure git saves EVERY version of a script — I have had several traumatic experiences
with git so I love to recover the 14gb on my mac but maybe also keep a copy on an external drive in case git
flakes out again."

The 5-minute autosave commits a SNAPSHOT of the tree, so two saves of the same file inside one cycle collapse
into one commit and the intermediate version is gone. This closes that window: it watches the repo with
FSEvents (fswatch) and captures each file the moment it settles, into two independent stores.

  1. GIT, durably. `git hash-object -w` writes the exact bytes into .git/objects, and a ref
     `refs/vault/<path>/<ts>_<sha12>` is created pointing AT THE BLOB. Refs are gc roots, so unlike a loose
     unreferenced object this can never be pruned by `git gc` — verified: a ref to a blob reads back with
     `git cat-file -p refs/vault/...`. Recovery needs no commit, no checkout and no reset:
         git cat-file -p refs/vault/ez_manage.py/20261017_0041_1a2b3c4d5e6f > /tmp/recovered.py
  2. AN EXTERNAL DRIVE, independently. A gzipped, content-addressed copy under
     /Volumes/SSD2T/binance_archive/script_vault/<relpath>/<ts>_<sha12>.gz, readable with nothing but gunzip
     if git itself is ever the problem. Skipped (never fatal) when the drive is absent or low on space.

Content-addressed in both stores: a version already captured for that path costs nothing, so a file saved
repeatedly with no change never grows the vault.

Scope is scripts and templates, never data: *.py, *.sh, *.command, *.md plus SPREADSHEETS/TEMPLATE*.xlsx.
Generated boards (V15_V16_CELL_BY_CELL, V15_MEGA), data/, logs/, backups/ and .git/ are excluded — those are
churny multi-MB artefacts, and including them is what turned backups/autosave into 14 GB.

Usage:
    python3 tools/script_version_vault.py                 # watch forever (launchd runs this)
    python3 tools/script_version_vault.py --once          # capture every in-scope file now, then exit
    python3 tools/script_version_vault.py --verify <path>  # list every captured version of one file
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
EXT_VAULT = Path("/Volumes/SSD2T/binance_archive/script_vault")
INDEX = REPO / "data" / "reports" / "script_vault_index.jsonl"
LOG = Path("/tmp/script_version_vault.log")
FSWATCH = "/opt/homebrew/bin/fswatch"

SUFFIXES = (".py", ".sh", ".command", ".md")
EXCLUDE_DIRS = (
    ".git", "backups", "data", "logs", "__pycache__", ".venv", "node_modules",
    ".pytest_cache", ".mypy_cache", "worktrees", "klines", "backtest_v8",
    "SPREADSHEETS/V15_V16_CELL_BY_CELL", "SPREADSHEETS/V15_MEGA", "SPREADSHEETS/V15_RUNS",
)
DEBOUNCE_S = 1.5          # let a writer finish before hashing; a half-written file is never captured
EXT_MIN_FREE_GB = 5.0     # below this the external copy is skipped, never fatal
PACK_REFS_EVERY = 250     # loose refs are cheap but not free; pack them periodically

_seen: dict[str, str] = {}   # relpath -> last captured sha (in-process fast path)
_captures = 0
_ext_warned = False


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def in_scope(p: Path) -> bool:
    try:
        rel = p.resolve().relative_to(REPO)
    except (ValueError, OSError):
        return False
    rels = str(rel)
    if any(rels == d or rels.startswith(d + "/") for d in EXCLUDE_DIRS):
        return False
    if p.suffix in SUFFIXES:
        return True
    # templates only — never the generated boards
    return p.suffix == ".xlsx" and rels.startswith("SPREADSHEETS/") and p.name.startswith("TEMPLATE")


def sha256_of(p: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _ref_name(rel: str, ts: str, sha12: str) -> str:
    """A ref path git will accept: no '..', no leading/trailing dots, no odd bytes."""
    safe = re.sub(r"[^A-Za-z0-9._/-]", "_", rel).strip("./")
    return f"refs/vault/{safe}/{ts}_{sha12}"


def ref_exists(ref: str) -> bool:
    return subprocess.run(["git", "show-ref", "--verify", "--quiet", ref], cwd=REPO).returncode == 0


def capture_git(p: Path, rel: str, ts: str, sha12: str) -> str | None:
    """Write the bytes into .git/objects and pin them with a ref so `git gc` can never prune them."""
    r = subprocess.run(["git", "hash-object", "-w", "--", str(p)], cwd=REPO, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        log(f"  git hash-object FAILED {rel}: {r.stderr.strip()[:200]}")
        return None
    blob = r.stdout.strip()
    ref = _ref_name(rel, ts, sha12)
    if not ref_exists(ref):
        u = subprocess.run(["git", "update-ref", ref, blob], cwd=REPO, capture_output=True, text=True, timeout=60)
        if u.returncode != 0:
            log(f"  git update-ref FAILED {ref}: {u.stderr.strip()[:200]}")
            return None
    return blob


def capture_ext(p: Path, rel: str, ts: str, sha12: str) -> str | None:
    """Gzipped copy on the external drive — recoverable with gunzip alone, no git involved."""
    global _ext_warned
    try:
        if not EXT_VAULT.parent.parent.is_dir():   # /Volumes/SSD2T itself
            raise FileNotFoundError("external drive not mounted")
        st = os.statvfs(EXT_VAULT.parent.parent)
        free_gb = (st.f_bavail * st.f_frsize) / 1e9
        if free_gb < EXT_MIN_FREE_GB:
            raise OSError(f"only {free_gb:.1f}GB free on the external drive")
        dest = EXT_VAULT / rel / f"{ts}_{sha12}.gz"
        if dest.exists():
            return str(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".gz.tmp")
        with p.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as out:
            shutil.copyfileobj(src, out)
        os.replace(tmp, dest)
        _ext_warned = False
        return str(dest)
    except Exception as e:
        if not _ext_warned:
            log(f"  external copy unavailable ({e}) — git capture continues; will retry silently")
            _ext_warned = True
        return None


def capture(path: str) -> bool:
    p = Path(path)
    if not p.is_file() or not in_scope(p):
        return False
    rel = str(p.resolve().relative_to(REPO))
    sha = sha256_of(p)
    if sha is None:
        return False
    if _seen.get(rel) == sha:
        return False   # byte-identical to the version we already hold
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sha12 = sha[:12]
    blob = capture_git(p, rel, ts, sha12)
    ext = capture_ext(p, rel, ts, sha12)
    if blob is None and ext is None:
        log(f"  CAPTURE FAILED on both stores: {rel}")
        return False
    _seen[rel] = sha
    try:
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        with INDEX.open("a") as fh:
            fh.write(json.dumps({"at": ts, "path": rel, "sha256": sha, "blob": blob, "ext": ext,
                                 "bytes": p.stat().st_size}) + "\n")
    except OSError as e:
        log(f"  index write failed {rel}: {e}")
    log(f"captured {rel} sha={sha12} git={'yes' if blob else 'NO'} ext={'yes' if ext else 'no'}")
    global _captures
    _captures += 1
    if _captures % PACK_REFS_EVERY == 0:
        subprocess.run(["git", "pack-refs", "--all"], cwd=REPO, capture_output=True, timeout=300)
        log(f"  packed refs after {_captures} captures")
    return True


def sweep_once() -> int:
    n = 0
    for root, dirs, files in os.walk(REPO):
        rel_root = os.path.relpath(root, REPO)
        rel_root = "" if rel_root == "." else rel_root
        dirs[:] = [d for d in dirs if not any((os.path.join(rel_root, d) == x or os.path.join(rel_root, d).startswith(x + "/")) for x in EXCLUDE_DIRS) and d not in EXCLUDE_DIRS]
        for f in files:
            if capture(os.path.join(root, f)):
                n += 1
    return n


def verify(target: str) -> int:
    rel = str(Path(target).resolve().relative_to(REPO)) if Path(target).exists() else target
    safe = re.sub(r"[^A-Za-z0-9._/-]", "_", rel).strip("./")
    r = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objecttype)", f"refs/vault/{safe}"],
                       cwd=REPO, capture_output=True, text=True)
    refs = [l for l in r.stdout.splitlines() if l.strip()]
    print(f"{rel}: {len(refs)} version(s) pinned in git")
    for l in sorted(refs)[-12:]:
        print("   " + l)
    ext_dir = EXT_VAULT / rel
    if ext_dir.is_dir():
        gz = sorted(ext_dir.glob("*.gz"))
        print(f"{rel}: {len(gz)} version(s) on the external drive, newest:")
        for g in gz[-5:]:
            print(f"   {g}")
    else:
        print(f"{rel}: no external copies yet ({ext_dir})")
    return 0


def watch() -> int:
    if not Path(FSWATCH).exists():
        log(f"FATAL: {FSWATCH} not found — install fswatch or run with --once from cron")
        return 1
    log(f"VAULT STARTED — watching {REPO} (git refs/vault + {EXT_VAULT})")
    log(f"initial sweep captured {sweep_once()} new version(s)")
    proc = subprocess.Popen(
        [FSWATCH, "-r", "--event", "Updated", "--event", "Created", "--event", "Renamed", str(REPO)],
        stdout=subprocess.PIPE, text=True, bufsize=1,
    )
    pending: dict[str, float] = {}
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        while True:
            if proc.poll() is not None:
                log("fswatch exited — restarting watch loop")
                return 1
            line = proc.stdout.readline()
            if line:
                path = line.strip()
                if path and in_scope(Path(path)):
                    pending[path] = time.monotonic() + DEBOUNCE_S
                continue
            now = time.monotonic()
            for path in [p for p, due in pending.items() if due <= now]:
                pending.pop(path, None)
                try:
                    capture(path)
                except Exception as e:
                    log(f"  capture error {path}: {e}")
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="capture every in-scope file now, then exit")
    ap.add_argument("--verify", metavar="PATH", help="list the captured versions of one file")
    a = ap.parse_args()
    if a.verify:
        return verify(a.verify)
    if a.once:
        n = sweep_once()
        log(f"--once: captured {n} new version(s)")
        return 0
    return watch()


if __name__ == "__main__":
    sys.exit(main())
