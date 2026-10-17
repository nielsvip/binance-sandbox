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
# USER 2026-10-17: "we have 3 but they are all flaky and all appear from time to time" — so write to EVERY
# drive that is genuinely mounted right now, and let a drive that reappears be caught up by the next sweep.
# A /Volumes entry on the SAME device as the boot volume is a phantom directory, not a drive: /Volumes/WD25
# was exactly that on 2026-10-17 (present and "flashing", never mounted), and writing there silently fills
# the internal disk instead of backing anything up.
EXT_ROOTS = (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT"))
EXT_SUBDIR = Path("binance_archive") / "script_vault"
EXT_MIN_FREE_GB_ROOT = 3.0
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


_REFS_CACHE: dict[str, list[str]] = {}


def existing_refs_for(rel: str) -> list[str]:
    """Every refs/vault ref already held for this path. Listed ONCE per process via a single
    for-each-ref over refs/vault, so a restart costs one git call instead of one per file."""
    if not _REFS_CACHE:
        r = subprocess.run(["git", "for-each-ref", "--format=%(refname)", "refs/vault"],
                           cwd=REPO, capture_output=True, text=True, timeout=300)
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            body = line[len("refs/vault/"):]
            path_part = body.rsplit("/", 1)[0] if "/" in body else body
            _REFS_CACHE.setdefault(path_part, []).append(line)
        _REFS_CACHE.setdefault("", [])   # mark as populated even when empty
    safe = re.sub(r"[^A-Za-z0-9._/-]", "_", rel).strip("./")
    return _REFS_CACHE.get(safe, [])


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


def ext_vaults() -> list[Path]:
    """Every genuinely-mounted external drive's vault dir. Phantom /Volumes dirs (same device as the boot
    volume) and nearly-full drives are skipped, so a copy is never written to the internal disk by mistake."""
    out = []
    try:
        boot_dev = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        boot_dev = None
    for root in EXT_ROOTS:
        try:
            if not root.is_dir() or not os.access(root, os.W_OK):
                continue
            if boot_dev is not None and os.stat(root).st_dev == boot_dev:
                continue
            st = os.statvfs(root)
            if (st.f_bavail * st.f_frsize) / 1e9 < EXT_MIN_FREE_GB_ROOT:
                continue
            out.append(root / EXT_SUBDIR)
        except OSError:
            continue
    return out


def capture_ext(p: Path, rel: str, ts: str, sha12: str) -> str | None:
    """Gzipped copy on EVERY mounted external drive — recoverable with gunzip alone, no git involved.
    Returns the first destination written (or already present); absence of all drives is never fatal."""
    global _ext_warned
    vaults = ext_vaults()
    if not vaults:
        if not _ext_warned:
            log("  no external drive mounted — git capture continues; ext copies resume when one appears")
            _ext_warned = True
        return None
    _ext_warned = False
    first = None
    for vault in vaults:
        try:
            dest = vault / rel / f"{ts}_{sha12}.gz"
            if dest.exists():
                first = first or str(dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".gz.tmp")
            with p.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as out:
                shutil.copyfileobj(src, out)
            os.replace(tmp, dest)
            first = first or str(dest)
        except Exception as e:
            log(f"  ext copy failed on {vault.parents[1].name} for {rel}: {e}")
    return first


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
    sha12 = sha[:12]
    # Already captured in a PREVIOUS process? The ref name and the external filename both end in sha12, so
    # an existing capture is detectable from the sha alone — check that BEFORE forking git. Without this, every
    # restart re-ran `git hash-object -w` once per in-scope file (4494 subprocesses on this repo) just to
    # rediscover versions it already held, which is what put the watcher in uninterruptible disk wait on boot.
    prior_ref = next((r for r in existing_refs_for(rel) if r.endswith(f"_{sha12}")), None)
    # Per-drive, not "any drive": a vault that is missing this version must still receive it, or a drive that
    # has just come back (E3T on 2026-10-17) would stay permanently behind because another drive already had
    # the copy. capture_ext itself is idempotent per destination, so re-running it is free for the drives that
    # are already current — only the stale one is written.
    vaults = ext_vaults()
    have_ext = [v for v in vaults if (v / rel).is_dir() and any((v / rel).glob(f"*_{sha12}.gz"))]
    prior_ext = next(((v / rel) for v in have_ext), None)
    if prior_ref and vaults and len(have_ext) == len(vaults):
        _seen[rel] = sha
        return False
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob = capture_git(p, rel, ts, sha12) if not prior_ref else prior_ref
    # reuse the timestamp already on disk so a backfilled drive matches the original capture name
    if prior_ext is not None:
        ts = next(iter(sorted(prior_ext.glob(f"*_{sha12}.gz")))).name.split(f"_{sha12}.gz")[0]
    ext = capture_ext(p, rel, ts, sha12) if len(have_ext) < len(vaults) else str(next(iter(sorted(prior_ext.glob(f"*_{sha12}.gz")))))
    if blob is None and ext is None:
        log(f"  CAPTURE FAILED on both stores: {rel}")
        return False
    _seen[rel] = sha
    if blob and not prior_ref:
        # keep the in-process ref list current, so an A -> B -> A edit sequence recognises A without re-forking git
        safe = re.sub(r"[^A-Za-z0-9._/-]", "_", rel).strip("./")
        _REFS_CACHE.setdefault(safe, []).append(_ref_name(rel, ts, sha12))
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
    for vault in ext_vaults():
        ext_dir = vault / rel
        gz = sorted(ext_dir.glob("*.gz")) if ext_dir.is_dir() else []
        print(f"{rel}: {len(gz)} version(s) on {vault.parents[1].name}" + (", newest:" if gz else ""))
        for g in gz[-5:]:
            print(f"   {g}")
    if not ext_vaults():
        print(f"{rel}: no external drive mounted right now")
    return 0


def watch() -> int:
    if not Path(FSWATCH).exists():
        log(f"FATAL: {FSWATCH} not found — install fswatch or run with --once from cron")
        return 1
    log(f"VAULT STARTED — watching {REPO} (git refs/vault + ext: {[str(v) for v in ext_vaults()] or 'none mounted'})")
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
