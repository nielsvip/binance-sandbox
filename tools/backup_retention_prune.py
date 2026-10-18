#!/usr/bin/env python3
"""backup_retention_prune — reclaim disk from REDUNDANT backup copies, never from unique history (USER 2026-10-18).

CLAUDE.md makes backups/ the only reliable history, so this tool deletes nothing that is the sole copy of anything. It
removes repeated snapshots of the SAME artifact, keeping a defensible recovery ladder, and prints exactly what it will do
before it does it.

WHY (measured 2026-10-18): the volume was at 100% with 2.4 GiB free of 460 GiB. backups/ held 127 GB, of which
**96.3 GB was 79 hourly copies of one 1.2 GB SQLite store** (`before_h2h_{crypto,stocks}_<stamp>.db`, written every hour
by the h2h chain), plus 13 GB of per-board tars in backups/board_name_truth/. The store itself is archived off-machine
daily (BACKTEST_BIBLE §107: per_sym_history jsonl + monthly db snapshot to /Volumes/E3T), so hourly local duplicates are
redundancy on top of redundancy.

RULES (each group independently):
  * db-snapshot groups (same prefix family): keep the newest --keep-recent, plus the LAST snapshot of every day, plus
    anything newer than --min-age-h. Everything else goes.
  * board tar groups: keep the newest --keep-tars.
  * never touch: anything whose basename appears only once in its group, .gz archives, directories not listed below.
Deletions are listed in data/reports/backup_retention/<stamp>.json with size, mtime and the rule that matched, so what
was removed is auditable after the fact.

Usage: tools/backup_retention_prune.py            # dry run, prints the plan and the reclaim
       tools/backup_retention_prune.py --apply
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports" / "backup_retention"
STAMP_RE = re.compile(r"(\d{8}T?\d{4,6}Z?)")


def group_of(name: str) -> str:
    """Family key: the filename with its timestamp removed, so hourly copies of one artifact land together."""
    return STAMP_RE.sub("<STAMP>", name)


def mtime_day(p: Path) -> str:
    return dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%d")


def plan(keep_recent: int, keep_tars: int, min_age_h: float):
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    keep, drop = [], []
    groups = collections.defaultdict(list)
    for f in glob.glob(str(ROOT / "backups" / "*.db")) + glob.glob(str(ROOT / "backups" / "*.db-wal")):
        p = Path(f)
        groups[group_of(p.name)].append(p)
    for g, files in groups.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if len(files) == 1:
            keep.append((files[0], "sole copy of its artifact"))
            continue
        seen_days, kept_recent = set(), 0
        for i, p in enumerate(files):
            age_h = (now - p.stat().st_mtime) / 3600.0
            day = mtime_day(p)
            if kept_recent < keep_recent:
                kept_recent += 1
                seen_days.add(day)
                keep.append((p, f"newest {keep_recent}/{keep_recent if False else keep_recent}"))
                continue
            if age_h < min_age_h:
                keep.append((p, f"younger than {min_age_h:.0f}h"))
                continue
            if day not in seen_days:
                seen_days.add(day)
                keep.append((p, f"last snapshot of {day}"))
                continue
            drop.append((p, f"redundant hourly copy ({day}, {age_h:.0f}h old, group has {len(files)})"))
    tars = sorted(glob.glob(str(ROOT / "backups" / "board_name_truth" / "*.tar")), key=os.path.getmtime, reverse=True)
    for i, f in enumerate(tars):
        p = Path(f)
        (keep if i < keep_tars else drop).append((p, f"board tar #{i + 1} newest" if i < keep_tars else f"board tar #{i + 1} beyond --keep-tars {keep_tars}"))
    return keep, drop


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep-recent", type=int, default=3, help="newest N snapshots per artifact family always kept")
    ap.add_argument("--keep-tars", type=int, default=10)
    ap.add_argument("--min-age-h", type=float, default=6.0, help="never delete a snapshot younger than this")
    a = ap.parse_args()
    keep, drop = plan(a.keep_recent, a.keep_tars, a.min_age_h)
    k_sz = sum(p.stat().st_size for p, _ in keep if p.exists())
    d_sz = sum(p.stat().st_size for p, _ in drop if p.exists())
    print(f"[retention] keep {len(keep)} files ({human(k_sz)}) · delete {len(drop)} files ({human(d_sz)}) · {'APPLYING' if a.apply else 'DRY RUN'}")
    print("\nkeeping:")
    for p, why in sorted(keep, key=lambda x: -x[0].stat().st_size)[:12]:
        print(f"  {human(p.stat().st_size):>9s}  {p.name[:64]:64s} {why}")
    print("\ndeleting (largest first):")
    for p, why in sorted(drop, key=lambda x: -x[0].stat().st_size)[:12]:
        print(f"  {human(p.stat().st_size):>9s}  {p.name[:64]:64s} {why}")
    rec = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "applied": bool(a.apply),
           "keep_recent": a.keep_recent, "keep_tars": a.keep_tars, "min_age_h": a.min_age_h,
           "reclaim_bytes": d_sz, "kept": [{"f": str(p), "why": w, "bytes": p.stat().st_size} for p, w in keep],
           "deleted": [{"f": str(p), "why": w, "bytes": p.stat().st_size,
                        "mtime_utc": dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc).isoformat()} for p, w in drop]}
    OUT.mkdir(parents=True, exist_ok=True)
    rp = OUT / f"retention_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%MZ')}{'_applied' if a.apply else '_dryrun'}.json"
    rp.write_text(json.dumps(rec, indent=1))
    if a.apply:
        freed = 0
        for p, _w in drop:
            try:
                sz = p.stat().st_size
                p.unlink()
                freed += sz
            except Exception as exc:
                print(f"  could not delete {p.name}: {exc}")
        print(f"\nfreed {human(freed)}")
    print(f"wrote {rp}")


if __name__ == "__main__":
    main()
