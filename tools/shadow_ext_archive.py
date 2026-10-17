#!/usr/bin/env python3
"""shadow_ext_archive — the shadow/observation folders on the external drives, append-only.

USER 2026-10-17: "that is the previous wd25 I am cleaning it so you can use it again for anything that is in
the macbook shadow folder that might not be there".

These folders are not code and not klines: they are what the system OBSERVED. A shadow jsonl line is a thing
that happened once and can never be recomputed, unlike an NPZ (rebuildable from klines) or a .py (already in
git + the script vault). Nothing here is covered by the other archives, because the script vault takes only
*.py/*.sh/*.md/TEMPLATE*.xlsx and skips data/ entirely.

TWO RULES, both append-only:
  LOGS (.jsonl/.log/.ndjson) grow by appending, so the archived copy is replaced only when the local file is
      a strict EXTENSION of it — same bytes up to the archived length, then more. If the local file diverged
      (shorter, or different bytes inside the archived prefix) it was rewritten or truncated, so the archived
      copy is KEPT and the local one is saved beside it as <name>.diverged_<ts>_<sha12>. Nothing is dropped
      and nothing is silently overwritten.
  EVERYTHING ELSE is stored content-addressed as <name>/<ts>_<sha12><ext>, so every distinct version of a
      json/npz/csv is kept side by side and an overwrite upstream can never destroy the earlier content.

NPZ are excluded by default: data/forward_parity/shadow_root alone is 744 MB of arrays that
backtest_v8_precompute can rebuild from the klines archive, and E3T has 23.8 GB free. --with-npz includes them.

Usage:
    python3 tools/shadow_ext_archive.py                 # logs + json/csv from every shadow folder
    python3 tools/shadow_ext_archive.py --with-npz      # include the rebuildable npz too
    python3 tools/shadow_ext_archive.py --coverage      # what is on each drive vs what is local
    python3 tools/shadow_ext_archive.py --dir data/foo  # archive an extra folder as well
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
SHADOW_DIRS = ("reports/shadow", "data/scalp_v3_shadow", "data/options_shadow", "data/forward_parity/shadow_root")
EXT_ROOTS = (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT"))
ARCH_SUBDIR = Path("binance_archive") / "shadow_archive"
LOG_EXT = (".jsonl", ".log", ".ndjson")
SKIP_NAMES = (".DS_Store",)
MIN_FREE_GB = 3.0
LOG = Path("/tmp/shadow_ext_archive.log")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def drives() -> list[Path]:
    """Mounted externals with room. A /Volumes entry on the boot volume's device is a phantom directory
    (/Volumes/WD25 was one on 2026-10-17) and writing there fills the internal disk instead of a drive."""
    out = []
    try:
        boot = os.stat("/System/Volumes/Data").st_dev
    except OSError:
        boot = None
    for r in EXT_ROOTS:
        try:
            if not r.is_dir() or not os.access(r, os.W_OK):
                continue
            if boot is not None and os.stat(r).st_dev == boot:
                continue
            st = os.statvfs(r)
            free = (st.f_bavail * st.f_frsize) / 1e9
            if free < MIN_FREE_GB:
                log(f"  {r.name}: {free:.1f}GB free — skipped (never risk an ENOSPC write)")
                continue
            out.append(r)
        except OSError:
            continue
    return out


def sha12(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def is_extension_of(local: Path, archived: Path) -> bool:
    """True when `local` starts with the exact bytes of `archived` and is at least as long — i.e. the log was
    appended to, not rewritten. Compared in 1 MB blocks so a 50 MB jsonl costs one pass, no full load."""
    try:
        a_size = archived.stat().st_size
        if local.stat().st_size < a_size:
            return False
        with local.open("rb") as lf, archived.open("rb") as af:
            left = a_size
            while left > 0:
                n = min(1 << 20, left)
                if lf.read(n) != af.read(n):
                    return False
                left -= n
        return True
    except OSError:
        return False


def files_to_archive(extra: list[str], with_npz: bool) -> list[tuple[str, Path]]:
    out = []
    for rel in list(SHADOW_DIRS) + list(extra):
        root = REPO / rel
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.name in SKIP_NAMES:
                continue
            # a dotfile here is a half-written temp from a live writer (seen: .ALTUSDT.npz.CrkBEb, a numpy
            # savez temp that vanished mid-copy). Archiving those produces a new "created" object on every
            # run and an error when the name disappears, so skip anything hidden or obviously in-flight.
            if p.name.startswith(".") or p.name.endswith((".tmp", ".part", ".swp")) or ".tmp." in p.name:
                continue
            if p.suffix == ".npz" and not with_npz:
                continue
            out.append((str(p.relative_to(REPO)), p))
    return out


def archive_one(rel: str, src: Path, arch_root: Path) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if src.suffix.lower() in LOG_EXT:
        dest = arch_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
            return "created"
        if src.stat().st_size == dest.stat().st_size and sha12(src) == sha12(dest):
            return "unchanged"
        if is_extension_of(src, dest):
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            shutil.copy2(src, tmp)
            if tmp.stat().st_size < dest.stat().st_size:
                tmp.unlink(missing_ok=True)
                return "refused_shrink"
            os.replace(tmp, dest)
            return "grown"
        # rewritten or truncated upstream: keep what we hold, park the new shape beside it
        side = dest.with_name(f"{dest.name}.diverged_{ts}_{sha12(src)}")
        if not side.exists():
            shutil.copy2(src, side)
        return "diverged"
    # non-log: content-addressed, so no version is ever overwritten
    h = sha12(src)
    dest = arch_root / rel / f"{ts}_{h}{src.suffix}"
    if any((arch_root / rel).glob(f"*_{h}{src.suffix}")):
        return "unchanged"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return "created"


def run(extra: list[str], with_npz: bool) -> int:
    ds = drives()
    if not ds:
        log("no external drive mounted with room — nothing archived (the next tick catches up)")
        return 0
    items = files_to_archive(extra, with_npz)
    total_mb = sum(p.stat().st_size for _, p in items) / 1e6
    log(f"shadow archive: {len(items)} file(s), {total_mb:.0f}MB local -> {[d.name for d in ds]}"
        f"{' (npz included)' if with_npz else ' (npz excluded: rebuildable)'}")
    for d in ds:
        arch_root = d / ARCH_SUBDIR
        arch_root.mkdir(parents=True, exist_ok=True)
        stats: dict[str, int] = {}
        for rel, src in items:
            try:
                st = archive_one(rel, src, arch_root)
            except Exception as e:
                st = "error"
                log(f"  {d.name} {rel}: {e}")
            stats[st] = stats.get(st, 0) + 1
        try:
            free = os.statvfs(d)
            free_gb = (free.f_bavail * free.f_frsize) / 1e9
        except OSError:
            free_gb = 0.0
        log(f"  {d.name}: {stats}, {free_gb:.1f}GB free")
        if stats.get("diverged"):
            log(f"      {stats['diverged']} log(s) diverged from the archived copy — both kept, see *.diverged_*")
    return 0


def coverage(extra: list[str], with_npz: bool) -> int:
    items = files_to_archive(extra, with_npz)
    log(f"local shadow files: {len(items)}")
    for d in drives():
        arch_root = d / ARCH_SUBDIR
        have = 0
        for rel, src in items:
            if src.suffix.lower() in LOG_EXT:
                have += 1 if (arch_root / rel).exists() else 0
            else:
                have += 1 if any((arch_root / rel).glob("*")) else 0
        n_files = sum(1 for _ in arch_root.rglob("*")) if arch_root.is_dir() else 0
        size = sum(p.stat().st_size for p in arch_root.rglob("*") if p.is_file()) / 1e6 if arch_root.is_dir() else 0
        log(f"  {d.name}: {have}/{len(items)} local files present, {n_files} objects, {size:.0f}MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-npz", action="store_true")
    ap.add_argument("--dir", action="append", default=[], help="extra folder to archive (repo-relative)")
    ap.add_argument("--coverage", action="store_true")
    a = ap.parse_args()
    if a.coverage:
        return coverage(a.dir, a.with_npz)
    return run(a.dir, a.with_npz)


if __name__ == "__main__":
    sys.exit(main())
