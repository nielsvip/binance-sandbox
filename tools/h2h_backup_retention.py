#!/usr/bin/env python3
"""h2h_backup_retention — prune redundant hourly per_sym_store.db snapshots in backups/.

USER 2026-10-22: the h2h cron (:25 crypto, :55 stocks) writes a ~1.37 GB `backups/before_h2h_<class>_<ts>.db` EVERY
HOUR and nothing ever pruned them. Measured 2026-10-22 03:30Z: 25 snapshots + 55 SQLite sidecars = 31.6 GB, and the
boot volume was down to 257 MB free with all three external drives at 0 bytes. That ENOSPC is not cosmetic -- it is
the documented cause of kline files cut at 4096-byte boundaries (KLINES_TRUNC_GUARD), and it made the position
writers unable to update `.positions_service_heartbeat.json`, so mac_pos_writer_keepalive.sh read a heartbeat OLDER
than the process uptime (945s stale on a writer up 302s), declared healthy live engines WEDGED and SIGKILLed them in
a loop. Retention policy, deliberately conservative because backups/ is the only reliable history:
  keep the newest KEEP_PER_CLASS per class  +  the newest snapshot of every calendar day  +  their -wal/-shm sidecars.
A sidecar is NEVER separated from its parent .db (a kept .db without its WAL can be an inconsistent snapshot), and a
file any process holds open is never touched. Dry-run by default; --apply deletes.
"""
import argparse, collections, glob, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERN = os.path.join(ROOT, "backups", "before_h2h_*")
KEEP_PER_CLASS = 2


def classify(path):
    m = re.search(r"before_h2h_(.+?)_(\d{8})T?\d{4,6}Z?\.db$", os.path.basename(path))
    return (m.group(1), m.group(2)) if m else ("unknown", "?")


def parent_of(path):
    return re.sub(r"-(wal|shm)$", "", path)


def is_open(path):
    try:
        r = subprocess.run(["lsof", "--", path], capture_output=True, text=True, timeout=30)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return True


def plan():
    files = glob.glob(PATTERN)
    dbs = [f for f in files if f.endswith(".db")]
    by_class = collections.defaultdict(list)
    for f in dbs:
        cls, day = classify(f)
        by_class[cls].append((f, day, os.stat(f).st_mtime))
    keep = set()
    for rows in by_class.values():
        rows.sort(key=lambda x: -x[2])
        keep.update(r[0] for r in rows[:KEEP_PER_CLASS])
        newest_of_day = {}
        for f, day, _ in rows:
            newest_of_day.setdefault(day, f)
        keep.update(newest_of_day.values())
    for f in files:
        if not f.endswith(".db") and parent_of(f) in keep:
            keep.add(f)
    return files, keep, [f for f in files if f not in keep]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    files, keep, prune = plan()
    skipped, removed, freed = [], 0, 0
    for f in list(prune):
        if is_open(f):
            skipped.append(f)
            prune.remove(f)
    size = lambda rows: sum(os.stat(f).st_size for f in rows if os.path.exists(f))
    reclaim = size(prune)
    if a.apply:
        for f in prune:
            try:
                n = os.stat(f).st_size
                os.remove(f)
                removed += 1
                freed += n
            except OSError as e:
                print(f"[h2h-retention] FAILED {f}: {e}", file=sys.stderr)
    verb = "removed" if a.apply else "would remove"
    if not a.quiet or a.apply:
        print(f"[h2h-retention] {len(files)} files, keep {len(keep)} ({size(keep) / 1e9:.1f} GB), {verb} {removed if a.apply else len(prune)} ({(freed if a.apply else reclaim) / 1e9:.1f} GB), skipped-open {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
