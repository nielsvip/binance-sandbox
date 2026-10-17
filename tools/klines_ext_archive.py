#!/usr/bin/env python3
"""klines_ext_archive — a klines archive on the external drives that ONLY EVER GROWS.

USER 2026-10-17: "make sure the ext drives hold a full klines backup that only grows never shrinks".

Klines are the most-damaged asset in this repo, and every incident was a SHRINK:
  * tradier_klines window-replace erased whole ranges (2026-10-16, 29 events restored from copies);
  * two full disks truncated 333 kline files at 4096-byte boundaries (ENOSPC, 2026-10-16);
  * a UTC-vs-ET grid dropped the close of every Nov-Mar session (2026-10-16).
So the archive is append-only in BOTH directions:
  BARS   the union of every bar ever seen, keyed by timestamp. A source that has fewer bars than the archive
         (truncated, erased, re-windowed) contributes nothing and takes nothing away.
  VALUES an existing bar's values are never overwritten. A source disagreeing on the OHLCV of a timestamp the
         archive already holds is recorded in _conflicts.jsonl for review, never silently applied — a
         "repair" and a corruption look identical to code, so a human decides.
A write is refused outright if the merged result would have fewer bars or a narrower span than what is already
on the drive. The file is written to a temp path, re-parsed, counted, and only then os.replace()d, so an
ENOSPC-truncated write can never land on top of a good file.

Sources (union, priority only decides which NEW bar is taken first): klines_cache (primary live),
klines_cache_gateway (gateway backup), klines_canonical, klines_cache_backtest, data/klines_wm_staging.

A per-drive _manifest.json records each source's (mtime,size) so an unchanged file is skipped without being
parsed — the first run reads ~10 GB of JSON, later runs read almost nothing, which is what makes it safe to
run from the 30-minute tick.

Usage:
    python3 tools/klines_ext_archive.py                 # merge every source into every mounted drive
    python3 tools/klines_ext_archive.py --coverage      # per-drive bar/file totals + what is under-replicated
    python3 tools/klines_ext_archive.py --symbol ZROUSDT_4h   # one file (debug)
    python3 tools/klines_ext_archive.py --verify ZROUSDT_4h   # compare archive vs live for one file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
SOURCES = ("klines_cache", "klines_cache_gateway", "klines_canonical", "klines_cache_backtest", "data/klines_wm_staging")
EXT_ROOTS = (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT"))
ARCH_SUBDIR = Path("binance_archive") / "klines_archive"
MIN_FREE_GB = 3.0
LOG = Path("/tmp/klines_ext_archive.log")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def real_drives() -> list[Path]:
    """Mounted externals only. A /Volumes entry on the boot volume's device is a phantom directory — writing
    a klines archive there would silently fill the internal disk (/Volumes/WD25 was exactly that)."""
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
                log(f"  {r.name}: {free:.1f}GB free — below {MIN_FREE_GB}GB, skipped (never risk an ENOSPC write)")
                continue
            out.append(r)
        except OSError:
            continue
    return out


def free_gb(p: Path) -> float:
    try:
        st = os.statvfs(p)
        return (st.f_bavail * st.f_frsize) / 1e9
    except OSError:
        return 0.0


def source_files() -> dict[str, list[Path]]:
    """name (SYMBOL_TF) -> every source file that carries it, in source-priority order."""
    out: dict[str, list[Path]] = {}
    for rel in SOURCES:
        d = REPO / rel
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            if p.name.startswith("_") or p.name.startswith("."):
                continue
            out.setdefault(p.stem, []).append(p)
    return out


def load_bars(p: Path) -> list[dict]:
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    return [b for b in d if isinstance(b, dict) and b.get("timestamp")] if isinstance(d, list) else []


def merge_one(name: str, srcs: list[Path], arch_dir: Path, conflicts: list) -> tuple[str, int, int]:
    """Union every source into the archive copy. Returns (status, bars_before, bars_after)."""
    target = arch_dir / f"{name}.json"
    existing = load_bars(target) if target.exists() else []
    merged = {b["timestamp"]: b for b in existing}
    before = len(merged)
    added = 0
    for p in srcs:
        for b in load_bars(p):
            ts = b["timestamp"]
            cur = merged.get(ts)
            if cur is None:
                merged[ts] = b
                added += 1
            elif cur != b:
                # never overwrite: a repair and a corruption are indistinguishable here
                conflicts.append({"name": name, "timestamp": ts, "kept": cur, "offered": b, "source": str(p)})
    if added == 0 and target.exists():
        return "unchanged", before, before
    out = [merged[k] for k in sorted(merged)]
    after = len(out)
    # ONLY-GROWS gate: never fewer bars, never a narrower span than what is already on the drive
    if existing:
        if after < before:
            log(f"  REFUSED {name}: merge would shrink {before} -> {after} bars")
            return "refused_shrink", before, after
        if out[0]["timestamp"] > existing[0]["timestamp"] or out[-1]["timestamp"] < existing[-1]["timestamp"]:
            log(f"  REFUSED {name}: merge would narrow the span "
                f"[{existing[0]['timestamp']}..{existing[-1]['timestamp']}] -> [{out[0]['timestamp']}..{out[-1]['timestamp']}]")
            return "refused_narrow", before, after
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(out, separators=(",", ":")))
        # re-parse what actually landed: an ENOSPC-truncated temp file must never replace a good archive
        check = load_bars(tmp)
        if len(check) != after:
            tmp.unlink(missing_ok=True)
            log(f"  REFUSED {name}: temp file verified {len(check)} bars, expected {after} (short write — disk full?)")
            return "refused_shortwrite", before, after
        os.replace(tmp, target)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        log(f"  REFUSED {name}: write failed ({e})")
        return "refused_error", before, after
    return ("created" if not existing else "grown"), before, after


def run(only: str | None) -> int:
    drives = real_drives()
    if not drives:
        log("no external drive mounted — nothing archived (normal; the next tick catches up)")
        return 0
    srcs_by_name = source_files()
    if only:
        srcs_by_name = {k: v for k, v in srcs_by_name.items() if k == only}
    log(f"klines archive: {len(srcs_by_name)} symbol/timeframe files -> {[d.name for d in drives]}")
    for drive in drives:
        arch_dir = drive / ARCH_SUBDIR
        arch_dir.mkdir(parents=True, exist_ok=True)
        man_path = arch_dir / "_manifest.json"
        try:
            manifest = json.loads(man_path.read_text())
        except Exception:
            manifest = {}
        stats = {"created": 0, "grown": 0, "unchanged": 0, "skipped": 0, "refused": 0}
        bars_added = 0
        conflicts: list = []
        for i, (name, srcs) in enumerate(sorted(srcs_by_name.items())):
            sig = {str(p): [int(p.stat().st_mtime), p.stat().st_size] for p in srcs if p.exists()}
            ent = manifest.get(name) or {}
            if ent.get("srcs") == sig and (arch_dir / f"{name}.json").exists():
                stats["skipped"] += 1
                continue
            if free_gb(drive) < MIN_FREE_GB:
                log(f"  {drive.name}: dropped below {MIN_FREE_GB}GB mid-run — stopping writes to this drive")
                break
            status, before, after = merge_one(name, srcs, arch_dir, conflicts)
            if status.startswith("refused"):
                stats["refused"] += 1
                continue
            stats[status] = stats.get(status, 0) + 1
            bars_added += max(0, after - before)
            manifest[name] = {"bars": after, "first": None, "last": None, "srcs": sig}
            if status != "unchanged":
                b = load_bars(arch_dir / f"{name}.json")
                if b:
                    manifest[name]["first"], manifest[name]["last"] = b[0]["timestamp"], b[-1]["timestamp"]
            if (i + 1) % 500 == 0:
                man_path.write_text(json.dumps(manifest))
        man_path.write_text(json.dumps(manifest))
        if conflicts:
            with (arch_dir / "_conflicts.jsonl").open("a") as fh:
                for c in conflicts:
                    fh.write(json.dumps(c) + "\n")
        log(f"  {drive.name}: {stats} +{bars_added} bars, {len(conflicts)} value conflict(s) recorded, "
            f"{free_gb(drive):.1f}GB free")
    return 0


def coverage() -> int:
    drives = real_drives()
    log(f"drives present: {[d.name for d in drives] or 'NONE'}")
    live = source_files()
    live_bars = {}
    for name, srcs in live.items():
        live_bars[name] = max((len(load_bars(p)) for p in srcs), default=0)
    log(f"  local sources: {len(live)} symbol/timeframe files")
    for d in drives:
        arch = d / ARCH_SUBDIR
        try:
            man = json.loads((arch / "_manifest.json").read_text())
        except Exception:
            man = {}
        files = len(list(arch.glob("*.json"))) - (1 if (arch / "_manifest.json").exists() else 0)
        bars = sum(int(v.get("bars") or 0) for v in man.values())
        missing = [n for n in live if n not in man]
        short = [n for n, v in man.items() if live_bars.get(n, 0) > int(v.get("bars") or 0)]
        log(f"  {d.name}: {files} files, {bars} bars archived, {len(missing)} local file(s) not archived yet, "
            f"{len(short)} archived shorter than local, {free_gb(d):.1f}GB free")
        if missing[:5]:
            log(f"      not archived yet e.g. {missing[:5]}")
    return 0


def verify(name: str) -> int:
    drives = real_drives()
    srcs = source_files().get(name, [])
    log(f"{name}: local sources {[(str(p), len(load_bars(p))) for p in srcs]}")
    for d in drives:
        p = d / ARCH_SUBDIR / f"{name}.json"
        b = load_bars(p) if p.exists() else []
        if b:
            log(f"  {d.name}: {len(b)} bars  {b[0]['timestamp']} .. {b[-1]['timestamp']}")
            lost = [x["timestamp"] for p2 in srcs for x in load_bars(p2) if x["timestamp"] not in {y["timestamp"] for y in b}]
            log(f"      bars present locally but missing from the archive: {len(lost)}")
        else:
            log(f"  {d.name}: NOT ARCHIVED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--symbol", help="archive just this SYMBOL_TF")
    ap.add_argument("--verify", help="compare the archive against local sources for one SYMBOL_TF")
    a = ap.parse_args()
    if a.coverage:
        return coverage()
    if a.verify:
        return verify(a.verify)
    return run(a.symbol)


if __name__ == "__main__":
    sys.exit(main())
