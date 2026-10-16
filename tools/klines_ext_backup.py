#!/usr/bin/env python3
"""klines_ext_backup — daily append-only backup of S1 klines_cache to Mac ext drives (USER 2026-10-14).

Pulls s1:~/binance-sandbox/klines_cache (top-level crypto + tradier/ stocks) to a
staging dir, then merges each file into the backup dir with append-only semantics:
a backup file NEVER shrinks. When the S1 copy is smaller (truncated/corrupt), only
its newer bars are appended into the existing backup (backup rows win timestamp
ties). Staging/rolling dirs (gateway, backups, consolidated_klines, *.bak*,
.guard_snap, *.tmp) are excluded. Two drives when they fit: E3T primary, SSD2T
mirror of the E3T backup with the same never-shrink merge.

Usage (Mac cron daily): .venv/bin/python -u tools/klines_ext_backup.py [--restore REL ...] [--dry-run]
Restore pushes backup files back to S1 after re-merging (backup always dominates
first), and refuses when the backup is shorter unless --force.
"""

import argparse
import datetime
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "s1-int:~/binance-sandbox/klines_cache/"
REMOTE_FALLBACK = "s1-pub:~/binance-sandbox/klines_cache/"
PRIMARY = Path("/Volumes/E3T/klines_backup_s1")
MIRROR = Path("/Volumes/SSD2T/klines_backup_s1")
LOG = Path.home() / "logs" / "klines_ext_backup.log"
EXCLUDE_DIRS = {"gateway", "backups", "consolidated_klines", ".guard_snap"}
# USER 2026-10-16 ("make sure the backups on ext drive remain complete", "not multiple klines backups"): klines_cache
# was the ONLY thing backed up, so the 3m/5m history that tools/v15_npz_bench.py and tools/w2/* read lived nowhere
# else — measured on /Volumes/SSD2T/klines_master, 952 series / 59.4M timestamps the E3T backup did not hold. One
# backup can only replace the others if it covers everything, so each source gets its own subtree under the backup.
SOURCES = [
    ("klines_cache", "klines_cache"),
    ("data/klines_3m_hist_w2", "data/klines_3m_hist_w2"),
    ("data/klines_5m_hist_w2", "data/klines_5m_hist_w2"),
]
MIN_FREE_EXISTING_GB = float(os.environ.get("KLINES_EXT_MIN_FREE_GB", "8"))
# USER 2026-10-16 ("not multiple klines backups that consume terabytes"): the rsync landing zone used to live ON the
# backup drive as klines_backup_s1/_stage, so E3T carried 12 GB of staging beside the 12 GB backup — half of that
# drive's kline footprint was a second copy of it, on a volume at 95%. It stays on the internal disk instead, which
# keeps the incremental pull (only changed files cross the wire) without paying for it twice on the backup drive.
VOLUMES = Path("/Volumes")
STAGE = Path(os.environ.get("KLINES_EXT_STAGE", str(Path.home() / ".klines_ext_stage")))
RSYNC_FILTER = ["--include=*/", "--include=*.json", "--exclude=*"]
RSYNC_EXCLUDE = [
    "--exclude=manifest.json",
    "--exclude=gateway/",
    "--exclude=backups/",
    "--exclude=consolidated_klines/",
    "--exclude=.guard_snap/",
    "--exclude=*.bak*/",
    "--exclude=*.tmp",
]


def log(msg):
    line = "[ext_backup %sZ] %s" % (time.strftime("%H:%M:%S", time.gmtime()), msg)
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_rows(p):
    try:
        with open(p, encoding="utf-8") as fh:
            rows = json.load(fh)
        if not isinstance(rows, list):
            return None
        return rows
    except Exception:
        return None


def ts_of(r):
    try:
        return str(r.get("timestamp", ""))
    except Exception:
        return ""


def info(rows):
    if not rows:
        return {"n": 0, "first": "", "last": ""}
    return {"n": len(rows), "first": ts_of(rows[0]), "last": ts_of(rows[-1])}


def merge_rows(backup_rows, staged_rows):
    """Union by timestamp, backup wins ties. Returns (merged, n_new_from_staged). Never raises."""
    try:
        by_ts = {}
        for r in backup_rows:
            t = ts_of(r)
            if t:
                by_ts[t] = r
        n_new = 0
        for r in staged_rows:
            t = ts_of(r)
            if not t:
                continue
            if t not in by_ts:
                by_ts[t] = r
                n_new += 1
        return [by_ts[t] for t in sorted(by_ts)], n_new
    except Exception:
        return None, 0


def atomic_write(p, rows):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(rows))
    os.replace(tmp, p)


def merge_file(staged, backup, dry_run=False):
    """Merge one staged file into its backup. Returns (action, detail). Backup never shrinks."""
    srows = load_rows(staged)
    if srows is None:
        return ("skip-corrupt-staged", "staged unparseable, backup kept")
    if not backup.exists():
        if not dry_run:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged, backup)
        return ("seeded", "new file n=%d" % len(srows))
    brows = load_rows(backup)
    if brows is None:
        if not dry_run:
            shutil.copy2(staged, backup)
        return (
            "replaced-corrupt-backup",
            "backup unparseable, staged n=%d" % len(srows),
        )
    si, bi = info(srows), info(brows)
    if si["n"] >= bi["n"] and si["last"] >= bi["last"] and si["first"] <= bi["first"]:
        if si["n"] == bi["n"] and si["last"] == bi["last"]:
            return ("up-to-date", "n=%d" % bi["n"])
        if not dry_run:
            shutil.copy2(staged, backup)
        return ("extended", "%d->%d last %s" % (bi["n"], si["n"], si["last"][:16]))
    merged, n_new = merge_rows(brows, srows)
    if merged is None or len(merged) < len(brows):
        return (
            "refused",
            "merge would shrink %d->%d" % (len(brows), len(merged) if merged else -1),
        )
    if len(merged) == len(brows) and n_new == 0:
        return ("up-to-date", "n=%d, staged adds nothing" % len(brows))
    if not dry_run:
        atomic_write(backup, merged)
    return (
        "appended-newer",
        "%d->%d (+%d newer bars, S1 copy was smaller)"
        % (len(brows), len(merged), n_new),
    )


def merge_tree(src_dir, dst_dir, dry_run=False, subtree="klines_cache"):
    """Merge every *.json under src_dir into dst_dir/subtree. Returns stats dict."""
    stats = {
        "seeded": 0,
        "extended": 0,
        "appended-newer": 0,
        "up-to-date": 0,
        "skip-corrupt-staged": 0,
        "replaced-corrupt-backup": 0,
        "refused": 0,
    }
    state_p = dst_dir / "BACKUP_STATE.json"
    try:
        raw = json.loads(state_p.read_text()) if state_p.exists() else {}
    except Exception:
        raw = {}
    # 2026-10-16: the writer below wraps the signatures as {"updated_utc", "files"} while this read took the WRAPPER
    # as the signature map, so each run nested the previous state one level deeper (observed: two levels on E3T) and
    # the keys it compared against were "updated_utc"/"files" instead of file rels. Flatten any nesting back to rel -> sig.
    state = raw if isinstance(raw, dict) else {}
    while isinstance(state.get("files"), dict):
        inner = state.pop("files")
        state.update(inner)
    state.pop("updated_utc", None)
    changed_state = False
    for staged in sorted(src_dir.rglob("*.json")):
        rel = str(staged.relative_to(src_dir))
        parts = Path(rel).parts
        if (
            set(parts[:-1]) & EXCLUDE_DIRS
            or parts[-1] == "manifest.json"
            or ".bak" in parts[-1]
            or staged.suffixes[-1:] == [".tmp"]
        ):
            continue
        backup = dst_dir / subtree / rel
        try:
            st = staged.stat()
            key = "%d:%d" % (st.st_size, int(st.st_mtime))
        except OSError:
            continue
        skey = rel if subtree == "klines_cache" else "%s/%s" % (subtree, rel)
        if state.get(skey) == key and backup.exists():
            stats["up-to-date"] += 1
            continue
        action, detail = merge_file(staged, backup, dry_run)
        stats[action] = stats.get(action, 0) + 1
        if action in ("refused", "skip-corrupt-staged"):
            log("ANOMALY %s %s" % (rel, detail))
        if not dry_run and action not in ("refused", "skip-corrupt-staged"):
            state[skey] = key
            changed_state = True
    if not dry_run and changed_state:
        tmp = state_p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "updated_utc": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "files": state,
                }
            )
        )
        os.replace(tmp, state_p)
    return stats


def ssh_ok(host):
    r = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
    )
    return r.returncode == 0


def pick_remote():
    if ssh_ok("s1-int"):
        return "s1-int:~/binance-sandbox/klines_cache/"
    if ssh_ok("s1-pub"):
        return "s1-pub:~/binance-sandbox/klines_cache/"
    return None


def rsync_pull(remote, stage_dir):
    stage_dir.mkdir(parents=True, exist_ok=True)
    cmd = (
        [
            "rsync",
            "-az",
            "--delete",
            "--timeout=60",
            "-e",
            "ssh -S none -o StrictHostKeyChecking=accept-new",
        ]
        + RSYNC_EXCLUDE
        + RSYNC_FILTER
        + [remote, str(stage_dir) + "/"]
    )
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20 * 3600)
    return r.returncode, (r.stderr or "")[-500:]


def is_ext_mount(root):
    """A detached drive leaves its /Volumes/<name> DIRECTORY behind, so an existence test is not a mount test.

    USER 2026-10-16: all three externals dropped off the bus mid-run and this function used to CREATE the backup root
    (`root.mkdir(parents=True)`), then report the BOOT disk's free space — i.e. it would rsync 12 GB onto the internal
    volume and call it a backup. Measured proof it already happened to a sibling tool: /Volumes/WD25 held 1.2 GB
    written 2026-10-14 19:34 with the same st_dev as /. Returns (ok, why).
    """
    p = Path(root)
    if not str(p).startswith(str(VOLUMES) + os.sep):
        return True, "not a removable-volume path"
    vol = VOLUMES / p.relative_to(VOLUMES).parts[0]
    if not vol.is_dir():
        return False, "%s is not present (drive detached)" % vol
    try:
        if os.stat(vol).st_dev == os.stat("/").st_dev:
            return False, "%s is a leftover mountpoint ON THE BOOT DISK, not a mounted drive" % vol
    except OSError as exc:
        return False, "%s unreadable: %s" % (vol, exc)
    if not os.path.ismount(vol):
        return False, "%s is not a mount point (drive detached)" % vol
    return True, "mounted"


def drive_ok(root, need_gb=40):
    try:
        mounted, why = is_ext_mount(root)
        if not mounted:
            return False, why
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
        st = os.statvfs(root)
        free_gb = st.f_bavail * st.f_frsize / 1e9
        if (root / "klines_cache").exists():
            # USER 2026-10-16 ("make sure the backups on ext drive remain complete"): an ESTABLISHED backup used to
            # pass this check unconditionally, so the run proceeded with any amount of free space and the rsync/merge
            # would die part-way through — a backup that stops being complete without ever saying so. E3T is at 95%
            # (27 GB free of 466 GB: mac_backup_20261014 100 GB + fleet_archive 81 GB + mac_backups_20261013 57 GB),
            # and staging alone is 12 GB. atomic_write keeps each FILE safe on ENOSPC (tmp write raises before the
            # os.replace), but the run as a whole must refuse rather than limp. KLINES_EXT_MIN_FREE_GB tunes it.
            return free_gb >= MIN_FREE_EXISTING_GB, round(free_gb, 1)
        return free_gb >= need_gb, round(free_gb, 1)
    except Exception as exc:
        return False, str(exc)[:80]


def sweep_tmp(root):
    """Reclaim .tmp leftovers from a previous ENOSPC/interrupted merge — they are pure waste on a 95% full drive."""
    n = bytes_ = 0
    try:
        for p in (root / "klines_cache").rglob("*.tmp"):
            try:
                bytes_ += p.stat().st_size
                p.unlink()
                n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n, round(bytes_ / 1e9, 2)


def do_restore(relpaths, force=False, dry_run=False):
    remote = pick_remote()
    if remote is None:
        log("RESTORE refused: S1 unreachable")
        return 1
    host = remote.split(":")[0]
    rc = 0
    fetch_dir = Path("/tmp/klines_ext_restore_stage")
    for rel in relpaths:
        backup = PRIMARY / "klines_cache" / rel
        if not backup.exists():
            log("RESTORE skip %s: not in backup" % rel)
            rc = 1
            continue
        fetched = fetch_dir / rel
        fetched.parent.mkdir(parents=True, exist_ok=True)
        if fetched.exists():
            fetched.unlink()
        subprocess.run(
            [
                "rsync",
                "-az",
                "-e",
                "ssh -S none -o StrictHostKeyChecking=accept-new",
                "%s:~/binance-sandbox/klines_cache/%s" % (host, rel),
                str(fetched),
            ],
            capture_output=True,
            timeout=600,
        )
        if fetched.exists():
            action, detail = merge_file(fetched, backup, dry_run)
            log("RESTORE pre-merge %s: %s %s" % (rel, action, detail))
        brows = load_rows(backup)
        if brows is None:
            log("RESTORE skip %s: backup unparseable" % rel)
            rc = 1
            continue
        if not dry_run:
            subprocess.run(
                [
                    "rsync",
                    "-az",
                    "-e",
                    "ssh -S none -o StrictHostKeyChecking=accept-new",
                    str(backup),
                    "%s:~/binance-sandbox/klines_cache/%s" % (host, rel),
                ],
                capture_output=True,
                timeout=600,
            )
        chk = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=15",
                host,
                "python3 -c \"import json;print(len(json.load(open('binance-sandbox/klines_cache/%s'))))\""
                % rel,
            ],
            capture_output=True,
            text=True,
        )
        try:
            n_remote = int((chk.stdout or "").strip().split()[-1])
        except Exception:
            n_remote = -1
        if n_remote < len(brows) and not force:
            log(
                "RESTORE ANOMALY %s: remote n=%d < backup n=%d, pushed anyway per append-only restore"
                % (rel, n_remote, len(brows))
            )
        log(
            "RESTORE %s backup n=%d remote n=%d%s"
            % (rel, len(brows), n_remote, " DRY" if dry_run else "")
        )
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", nargs="*", default=[])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*", default=None, help="back up just these subtrees (e.g. data/klines_3m_hist_w2)")
    a = ap.parse_args()
    lock = open("/tmp/klines_ext_backup.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another backup run holds the lock, exiting", flush=True)
        return 2
    if a.restore:
        return do_restore(a.restore, force=a.force, dry_run=a.dry_run)
    sw_n, sw_gb = sweep_tmp(PRIMARY)
    if sw_n:
        log("swept %d leftover .tmp (%s GB) from %s" % (sw_n, sw_gb, PRIMARY))
    ok, free = drive_ok(PRIMARY)
    if not ok:
        log("REFUSED primary %s not usable (free %s GB, floor %s GB) — a run that cannot finish must not half-write the backup" % (PRIMARY, free, MIN_FREE_EXISTING_GB))
        return 1
    remote = pick_remote()
    if remote is None:
        log("REFUSED S1 unreachable via s1-int and s1-pub")
        return 1
    log(
        "start %s -> %s (free %s GB)%s"
        % (" + ".join(src for src, _ in SOURCES), PRIMARY, free, " DRY" if a.dry_run else "")
    )
    only = set(a.only or []) or None
    for src, subtree in SOURCES:
        if only and subtree not in only and src not in only:
            continue
        rem = remote.replace("klines_cache/", src + "/")
        stage = STAGE / subtree
        log("pull %s" % rem)
        rc, err = rsync_pull(rem, stage)
        if rc != 0:
            log("rsync %s rc=%d err=%s" % (src, rc, err))
            if subtree == "klines_cache":
                return 1
            continue
        ok, free = drive_ok(PRIMARY)
        if not ok:
            log("STOPPED before merging %s: primary free %s GB below the %s GB floor" % (subtree, free, MIN_FREE_EXISTING_GB))
            return 1
        stats = merge_tree(stage, PRIMARY, a.dry_run, subtree=subtree)
        log("primary merged %s %s" % (subtree, json.dumps(stats)))
        mok, mfree = drive_ok(MIRROR)
        if mok:
            mstats = merge_tree(PRIMARY / subtree, MIRROR, a.dry_run, subtree=subtree)
            log("mirror merged %s %s (free %s GB)" % (subtree, json.dumps(mstats), mfree))
        else:
            log("mirror %s skipped (free %s)" % (MIRROR, mfree))
    ok, why = is_ext_mount(PRIMARY)
    if not ok:
        log("INCOMPLETE: %s went away mid-run (%s) — the backup holds whatever merged before that; reconnect and re-run" % (PRIMARY, why))
        return 1
    (PRIMARY / "RESTORE.md").write_text(
        "# Restore S1 klines from this backup\n\nPer file: `python tools/klines_ext_backup.py --restore <REL ...>` (rel like `BTCUSDC_15m.json` or `tradier/AAPL_15m.json`).\nBackup files only ever grow; restore re-merges S1 first so nothing newer is lost.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
