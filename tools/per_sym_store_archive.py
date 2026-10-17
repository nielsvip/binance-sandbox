#!/usr/bin/env python3
"""per_sym_store_archive — the COMPLETE per_sym_store history, append-only, on every external drive present.

USER 2026-10-17: "per_sym_store.db the complete copy that should never delete a line should live on TWO
external drives (we have 3 but they are all flaky and all appear from time to time) — s1 only needs to have a
fraction of per_sym_store and macbook only needs the running best settings."

So the three tiers are:
  EXTERNAL DRIVES  the complete, never-deleted history (this tool). Target >= 2 drives.
  MACBOOK          the running best settings (per_sym_active, 743 rows) + whatever recent history live reads.
                   per_sym_store._prune_history already deletes >35d and caps 45 rows/sym_side, which is WHY
                   this archive has to exist: measured 2026-10-17, the live DB's history starts at 2026-10-02,
                   so everything older has already been dropped locally.
  S1               a fraction — the fleet_orchestrator_publish rows it owns. Nothing here writes to S1.

APPEND-ONLY, and safe to run from anything. Every row gets a content uid
(sha256 of sym_side|epoch|updated_at|overrides_json|full_config_json), so:
  * re-ingesting the same source is a no-op (INSERT OR IGNORE),
  * several sources can be merged without id collisions (the local `id` is autoincrement per DB and means
    nothing across copies — it is kept as src_id for provenance only),
  * a row present in one archive and missing in another is detectable, which is what --coverage reports.
NOTHING in this tool issues DELETE or UPDATE against an archive. The only deletion it can do is
--prune-local-history, which is OFF by default and refuses any row not already in >= MIN_ARCHIVES archives.

The flaky-drive rule: every run writes to whichever drives are mounted RIGHT NOW. A drive that reappears next
week is caught up from the union of the live DB plus every backups/*.db snapshot, so intermittent mounts
converge instead of diverging. Drives are never written unless they look like a real external mount with room.

Usage:
    python3 tools/per_sym_store_archive.py                    # ingest live DB + backups/*.db -> all drives
    python3 tools/per_sym_store_archive.py --coverage         # per-drive row counts + what is under-replicated
    python3 tools/per_sym_store_archive.py --sources live     # live DB only (fast, for a 15-min cron)
    python3 tools/per_sym_store_archive.py --prune-local-history --days 35   # OFF unless asked; >=2 archives
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
LIVE_DB = REPO / "data" / "hourly_reconfig" / "per_sym_store.db"
# All three known externals. They come and go; absence is normal and never an error.
EXT_CANDIDATES = (Path("/Volumes/SSD2T"), Path("/Volumes/E3T"), Path("/Volumes/WD25"), Path("/Volumes/TOSHIBA_EXT"))
ARCHIVE_REL = Path("binance_archive") / "per_sym_store_complete.db"
MIN_FREE_GB = 4.0
MIN_ARCHIVES = 2          # the user's rule: a row is safe once TWO drives hold it
LOG = Path("/tmp/per_sym_store_archive.log")

HIST_COLS = ("sym_side", "updated_at", "epoch", "cat_side", "template_md5", "defaults_round", "winning_tag",
             "gain_pct", "baseline_gain", "delta_pct", "bh_pct", "trades", "pool_sharpe", "max_dd_pct",
             "tim_pct", "valid", "overrides_json", "full_config_json", "defaults_snapshot_json", "meta_json")
ACTIVE_COLS = ("sym_side", "cat_side", "updated_at", "winning_tag", "wsharpe", "pool_sharpe", "trades",
               "max_dd_pct", "acc_gain_pct", "gain_vs_bh", "bh_pct", "delta_365", "delta_30d", "gain_30d",
               "baseline_30d", "campaign_ts", "overrides_json", "defaults_snapshot_json", "full_config_json",
               "template_md5", "defaults_round", "meta_json")


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def is_real_ext(root: Path) -> bool:
    """A real external mount with room — not a phantom /Volumes directory on the system disk.
    /Volumes/WD25 was exactly that on 2026-10-17: a directory whose statfs was the boot volume's."""
    try:
        if not root.is_dir():
            return False
        if os.stat(root).st_dev == os.stat("/System/Volumes/Data").st_dev:
            return False   # same device as the boot volume = phantom dir, not a mounted drive
        st = os.statvfs(root)
        if (st.f_bavail * st.f_frsize) / 1e9 < MIN_FREE_GB:
            log(f"  {root.name}: only {(st.f_bavail*st.f_frsize)/1e9:.1f}GB free — skipped")
            return False
        return os.access(root, os.W_OK)
    except OSError:
        return False


def present_drives() -> list[Path]:
    return [d for d in EXT_CANDIDATES if is_real_ext(d)]


def uid_of(row: dict) -> str:
    key = "|".join(str(row.get(k) or "") for k in ("sym_side", "epoch", "updated_at", "overrides_json", "full_config_json"))
    return hashlib.sha256(key.encode()).hexdigest()


def open_archive(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=300.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"""CREATE TABLE IF NOT EXISTS history_archive (
        uid TEXT PRIMARY KEY, {', '.join(f'{c} TEXT' if c.endswith('_json') or c in ('sym_side','updated_at','cat_side','template_md5','defaults_round','winning_tag') else f'{c} REAL' for c in HIST_COLS)},
        src_id INTEGER, first_seen_at TEXT, sources TEXT)""")
    con.execute(f"""CREATE TABLE IF NOT EXISTS active_archive (
        uid TEXT PRIMARY KEY, {', '.join(f'{c} TEXT' if c.endswith('_json') or c in ('sym_side','updated_at','cat_side','template_md5','defaults_round','winning_tag') else f'{c} REAL' for c in ACTIVE_COLS)},
        src_id INTEGER, captured_at TEXT, first_seen_at TEXT, sources TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS ingest_log (at TEXT, source TEXT, tbl TEXT, seen INTEGER, new INTEGER)")
    # Additive migration only — an archive written by an older revision of this tool is upgraded in place,
    # never rebuilt and never dropped (the first run on 2026-10-17 created active_archive without src_id).
    for tbl, want in (("history_archive", list(HIST_COLS) + ["src_id", "first_seen_at", "sources"]),
                      ("active_archive", list(ACTIVE_COLS) + ["src_id", "captured_at", "first_seen_at", "sources"])):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        for c in want:
            if c not in have:
                col_type = "INTEGER" if c == "src_id" else "TEXT"
                con.execute(f"ALTER TABLE {tbl} ADD COLUMN {c} {col_type}")
                log(f"  migrated {tbl}: added column {c}")
    for t, c in (("history_archive", "sym_side"), ("history_archive", "updated_at"), ("active_archive", "sym_side")):
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_{c} ON {t}({c})")
    con.commit()
    return con


def read_source(db: Path, tbl: str, cols) -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=300.0)
        con.row_factory = sqlite3.Row
        have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
        if not have:
            return []
        use = [c for c in cols if c in have]
        idc = "id" if "id" in have else "NULL"
        rows = [dict(r) for r in con.execute(f"SELECT {idc} AS src_id, {', '.join(use)} FROM {tbl}")]
        con.close()
        return rows
    except Exception as e:
        log(f"  source unreadable {db.name}/{tbl}: {e}")
        return []


def ingest(arch: sqlite3.Connection, rows: list[dict], tbl: str, cols, source: str) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = 0
    for r in rows:
        uid = uid_of(r)
        vals = [uid] + [r.get(c) for c in cols] + [r.get("src_id")]
        extra = [now, source] if tbl == "history_archive" else [r.get("updated_at"), now, source]
        ph = ",".join("?" * (len(vals) + len(extra)))
        names = ["uid"] + list(cols) + ["src_id"] + (["first_seen_at", "sources"] if tbl == "history_archive"
                                                     else ["captured_at", "first_seen_at", "sources"])
        cur = arch.execute(f"INSERT OR IGNORE INTO {tbl} ({','.join(names)}) VALUES ({ph})", vals + extra)
        new += cur.rowcount or 0
    arch.execute("INSERT INTO ingest_log (at, source, tbl, seen, new) VALUES (?,?,?,?,?)",
                 (now, source, tbl, len(rows), new))
    arch.commit()
    return new


def sources_for(which: str) -> list[Path]:
    out = []
    if which in ("live", "all"):
        if LIVE_DB.exists():
            out.append(LIVE_DB)
    if which in ("backups", "all"):
        out += sorted((REPO / "backups").glob("*.db"))
    return out


def coverage() -> int:
    drives = present_drives()
    log(f"drives present: {[d.name for d in drives] or 'NONE'}")
    counts = {}
    uids_per_drive = {}
    for d in drives:
        p = d / ARCHIVE_REL
        if not p.exists():
            log(f"  {d.name}: no archive yet ({p})")
            continue
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=300.0)
        h = con.execute("SELECT COUNT(*) FROM history_archive").fetchone()[0]
        a = con.execute("SELECT COUNT(*) FROM active_archive").fetchone()[0]
        span = con.execute("SELECT MIN(updated_at), MAX(updated_at) FROM history_archive").fetchone()
        counts[d.name] = (h, a)
        uids_per_drive[d.name] = {r[0] for r in con.execute("SELECT uid FROM history_archive")}
        con.close()
        log(f"  {d.name}: history {h} rows ({str(span[0])[:10]} .. {str(span[1])[:10]}), active snapshots {a}")
    live = {uid_of(r) for r in read_source(LIVE_DB, "per_sym_history", HIST_COLS)} if LIVE_DB.exists() else set()
    if uids_per_drive:
        allu = set().union(*uids_per_drive.values())
        under = [u for u in allu if sum(1 for s in uids_per_drive.values() if u in s) < MIN_ARCHIVES]
        log(f"  union across drives: {len(allu)} history rows; under-replicated (<{MIN_ARCHIVES} drives): {len(under)}")
        missing_live = [u for u in live if sum(1 for s in uids_per_drive.values() if u in s) < MIN_ARCHIVES]
        log(f"  live rows not yet on {MIN_ARCHIVES} drives: {len(missing_live)} of {len(live)}")
    else:
        log(f"  NOTHING ARCHIVED YET — {len(live)} live history rows exist only on this Mac")
    return 0


def prune_local(days: int) -> int:
    """Delete local history rows ONLY where the uid is already on >= MIN_ARCHIVES drives and older than `days`."""
    drives = [d for d in present_drives() if (d / ARCHIVE_REL).exists()]
    if len(drives) < MIN_ARCHIVES:
        log(f"REFUSING to prune: {len(drives)} archive(s) reachable, need {MIN_ARCHIVES}")
        return 1
    sets = []
    for d in drives:
        con = sqlite3.connect(f"file:{d / ARCHIVE_REL}?mode=ro", uri=True, timeout=300.0)
        sets.append({r[0] for r in con.execute("SELECT uid FROM history_archive")})
        con.close()
    safe = {u for u in set().union(*sets) if sum(1 for s in sets if u in s) >= MIN_ARCHIVES}
    cut = datetime.now(timezone.utc).timestamp() - days * 86400
    con = sqlite3.connect(str(LIVE_DB), timeout=300.0)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"SELECT id AS src_id, {', '.join(HIST_COLS)} FROM per_sym_history")]
    victims = [r["src_id"] for r in rows if (r.get("epoch") or 0) < cut and uid_of(r) in safe]
    log(f"prune-local: {len(rows)} local rows, {len(victims)} older than {days}d AND on >={MIN_ARCHIVES} drives")
    for i in range(0, len(victims), 500):
        chunk = victims[i:i + 500]
        con.execute(f"DELETE FROM per_sym_history WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    con.commit()
    con.execute("VACUUM")
    con.close()
    log(f"prune-local: deleted {len(victims)} rows; archives untouched")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="all", choices=("live", "backups", "all"))
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--prune-local-history", action="store_true")
    ap.add_argument("--days", type=int, default=35)
    a = ap.parse_args()
    if a.coverage:
        return coverage()
    if a.prune_local_history:
        return prune_local(a.days)
    drives = present_drives()
    if not drives:
        log("no external drive mounted — nothing archived this run (this is normal; the next run catches up)")
        return 0
    log(f"archiving to {[d.name for d in drives]}")
    srcs = sources_for(a.sources)
    log(f"sources: {[s.name for s in srcs]}")
    for d in drives:
        arch = open_archive(d / ARCHIVE_REL)
        tot_h = tot_a = 0
        for s in srcs:
            tot_h += ingest(arch, read_source(s, "per_sym_history", HIST_COLS), "history_archive", HIST_COLS, s.name)
            tot_a += ingest(arch, read_source(s, "per_sym_active", ACTIVE_COLS), "active_archive", ACTIVE_COLS, s.name)
        h = arch.execute("SELECT COUNT(*) FROM history_archive").fetchone()[0]
        aa = arch.execute("SELECT COUNT(*) FROM active_archive").fetchone()[0]
        arch.close()
        log(f"  {d.name}: +{tot_h} history, +{tot_a} active -> {h} history rows, {aa} active snapshots total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
