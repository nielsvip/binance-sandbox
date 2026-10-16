#!/usr/bin/env python3
"""per_sym_daily_archive — the permanent, per-day record of WHAT WAS APPLIED and WHAT IT TRADED.

USER 2026-10-16: "make sure the sql has a copy on ext drive that keeps every day's version of each
sym_side with full settings and technicals for years so we can always match it back to the live trades
for that day."

The live store (data/hourly_reconfig/per_sym_store.db) is a CURRENT-STATE table: per_sym_active holds
one row per sym_side and go-live OVERWRITES it, so once a set is replaced there is no way left to ask
"what exactly was DOGEUSDT_LONG running when it took that trade on the 14th". per_sym_history keeps
published boards, but published != applied, and nothing at all preserves the indicator values the live
decision actually saw. This tool closes both gaps, on the external drive, append-only.

WHAT IS WRITTEN, per UTC day:
  sets        every distinct full set, CONTENT-ADDRESSED by sha256 over its canonical JSON, gzipped
              once. A sym_side that did not change costs one 100-byte row per day instead of 120 KB,
              which is what makes "every day, for years" affordable: ~20 MB/day if every one of the
              742 sets changed, in practice a small fraction of that.
  day_sets    (day, sym_side) -> that sha256 + the board identity that produced it (applied_at,
              winning_tag, cat_side, template_md5, defaults_round) and the board's own measured
              numbers (wsharpe, pool_sharpe, trades, max_dd_pct, gain_vs_bh). So the matchback is a
              single lookup, not a reconstruction.
  day_trades  every /history event of that day per account, VERBATIM, gzipped — which carries each
              event's `indicators` block, i.e. the technicals the live decision was taken on.
  db_copies   a consistent whole-file snapshot of the live sqlite (sqlite3 backup API, never a cp of a
              file being written), gzipped. The literal "copy of the sql on the ext drive". These are
              1.1 GB each raw, so dailies are pruned to --keep-db-dailies (14) while the FIRST snapshot
              of every month is kept forever; the day_sets/sets tables above are the durable record and
              do not depend on them.
  runs        one row per run: what it saw, what it added, and the sha256 of the live db it read.

NOTHING IS EVER DELETED except surplus db_copies dailies. The archive db is opened with
journal_mode=DELETE (not WAL): WAL needs shared memory the exFAT external volume does not give us.

When the drive is not mounted the run does NOT fail and does NOT skip the day — it stages into
data/per_sym_archive_pending/ and the next run with the drive present flushes every pending day in
order. A skipped day cannot be recovered later; a staged one can.

USAGE
  archive today:        python3 tools/per_sym_daily_archive.py
  backfill a day:       python3 tools/per_sym_daily_archive.py --day 2026-10-15
  THE MATCHBACK:        python3 tools/per_sym_daily_archive.py --show DOGEUSDT_LONG --day 2026-10-15
  integrity:            python3 tools/per_sym_daily_archive.py --verify
  what is in there:     python3 tools/per_sym_daily_archive.py --list-days
Exit 1 on a real failure (unreadable live db, corrupt archive, nothing written and nothing staged).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIVE_DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
HISTORY_DIRS = (ROOT / "data" / "history", ROOT / "data" / "tradier" / "history")
PENDING = ROOT / "data" / "per_sym_archive_pending"
EXT_CANDIDATES = (
    Path("/Volumes/SSD2T/binance_archive/per_sym_daily"),
    Path("/Volumes/TOSHIBA_EXT/binance_archive/per_sym_daily"),
    Path("/Volumes/WD25/binance_archive/per_sym_daily"),
)
ACCOUNTS = ("ang", "fin", "flz", "inf", "men", "trb")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    sha256 TEXT PRIMARY KEY, n_keys INTEGER, first_day TEXT, body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS day_sets (
    day TEXT NOT NULL, sym_side TEXT NOT NULL, sha256 TEXT NOT NULL, cat_side TEXT,
    applied_at TEXT, winning_tag TEXT, template_md5 TEXT, defaults_round TEXT,
    wsharpe REAL, pool_sharpe REAL, trades INTEGER, max_dd_pct REAL, gain_vs_bh REAL,
    source TEXT NOT NULL DEFAULT 'applied_observed',
    PRIMARY KEY (day, sym_side));
CREATE TABLE IF NOT EXISTS day_trades (
    day TEXT NOT NULL, account TEXT NOT NULL, sym_side TEXT NOT NULL,
    n_events INTEGER, body BLOB NOT NULL, PRIMARY KEY (day, account, sym_side));
CREATE TABLE IF NOT EXISTS runs (
    day TEXT NOT NULL, ran_at TEXT NOT NULL, n_sym_sides INTEGER, n_new_sets INTEGER,
    n_accounts INTEGER, n_events INTEGER, live_db_sha256 TEXT, live_db_bytes INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS ix_day_sets_sym ON day_sets(sym_side, day);
CREATE INDEX IF NOT EXISTS ix_day_trades_sym ON day_trades(sym_side, day);
"""


def _utc_today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _ext_root(explicit: str = "") -> Path | None:
    """The archive root on a MOUNTED external volume, or None. A path under /Volumes that is not an
    actual mount point is an empty placeholder directory on the boot disk — writing the archive there
    would silently fill the system volume, so require a real mount."""
    cands = (Path(explicit),) if explicit else EXT_CANDIDATES
    for c in cands:
        vol = Path("/Volumes") / c.parts[2] if len(c.parts) > 2 and c.parts[1] == "Volumes" else None
        if vol is not None and not os.path.ismount(vol):
            continue
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".write_probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            return c
        except Exception:
            continue
    return None


def _connect_archive(root: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(root / "archive.db"), timeout=120.0)
    con.execute("PRAGMA journal_mode=DELETE")  # exFAT has no shared memory for WAL
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(day_sets)")}
    if "source" not in cols:  # archives written before provenance was added
        con.execute("ALTER TABLE day_sets ADD COLUMN source TEXT NOT NULL DEFAULT 'applied_observed'")
    con.commit()
    return con


def _sha_file(p: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with p.open("rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += len(b)
    return h.hexdigest(), n


def _snapshot_live_db(dest: Path) -> tuple[str, int]:
    """Consistent copy via the sqlite backup API — a plain cp of a 1.1 GB db being written mid-go-live
    yields a torn file that opens fine and is wrong."""
    tmp = dest.with_suffix(".tmp.db")
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=300.0)
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    sha, nbytes = _sha_file(tmp)
    with tmp.open("rb") as fi, gzip.open(str(dest) + ".gz", "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    tmp.unlink()
    return sha, nbytes


def _read_live_sets() -> dict:
    """Every applied set, exactly as live resolves it (per_sym_store's own sanitize), plus the board
    identity and the board's measured numbers."""
    from per_sym_store import _mandate_sanitize, _maybe_decompress

    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=300.0)
    con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute("SELECT * FROM per_sym_active"):
        try:
            fc = _mandate_sanitize(json.loads(_maybe_decompress(r["full_config_json"])))
        except Exception:
            continue
        if not fc:
            continue
        out[r["sym_side"]] = {
            "full_config": fc,
            "cat_side": r["cat_side"],
            "applied_at": r["updated_at"],
            "winning_tag": r["winning_tag"],
            "template_md5": r["template_md5"],
            "defaults_round": r["defaults_round"],
            "wsharpe": r["wsharpe"],
            "pool_sharpe": r["pool_sharpe"],
            "trades": r["trades"],
            "max_dd_pct": r["max_dd_pct"],
            "gain_vs_bh": r["gain_vs_bh"],
        }
    con.close()
    return out


def _reconstruct_sets(day: str) -> dict:
    """For a PAST day: the newest per_sym_history board published at or before the END of that day.

    This is NOT the same evidence as reading per_sym_active live on the day itself — published is not
    proven applied — so every row it produces is stamped source='history_reconstructed' and --show says
    so. Writing per_sym_active into a past day instead would record today's settings as if they had been
    in force then, which is a fabrication; the archive only ever observes the present."""
    from per_sym_store import _mandate_sanitize, _maybe_decompress

    cut = f"{day}T23:59:59.999999+00:00"
    con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=300.0)
    con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute(
        "SELECT * FROM per_sym_history h WHERE h.updated_at<=? AND h.id="
        "(SELECT id FROM per_sym_history x WHERE x.sym_side=h.sym_side AND x.updated_at<=? "
        " ORDER BY COALESCE(x.epoch,0) DESC, x.id DESC LIMIT 1)", (cut, cut)):
        try:
            fc = _mandate_sanitize(json.loads(_maybe_decompress(r["full_config_json"])))
        except Exception:
            continue
        if not fc:
            continue
        out[r["sym_side"]] = {
            "full_config": fc, "cat_side": r["cat_side"], "applied_at": r["updated_at"],
            "winning_tag": r["winning_tag"], "template_md5": r["template_md5"],
            "defaults_round": r["defaults_round"], "wsharpe": None, "pool_sharpe": r["pool_sharpe"],
            "trades": r["trades"], "max_dd_pct": r["max_dd_pct"], "gain_vs_bh": None,
        }
    con.close()
    return out


def _read_day_trades(day: str) -> dict:
    """{(account, sym_side): [event, ...]} for that UTC day, events verbatim (indicators included)."""
    out: dict = {}
    for base in HISTORY_DIRS:
        if not base.is_dir():
            continue
        for acct_dir in sorted(base.iterdir()):
            acct = acct_dir.name
            if not acct_dir.is_dir() or acct not in ACCOUNTS:
                continue
            for fn in sorted(acct_dir.iterdir()):
                if fn.suffix != ".jsonl":
                    continue
                rows = []
                try:
                    with fn.open(errors="replace") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line or day not in line[:64]:
                                continue
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            if str(ev.get("ts") or "")[:10] == day:
                                rows.append(ev)
                except Exception:
                    continue
                if rows:
                    out[(acct, fn.stem)] = rows
    return out


def _write_day(con: sqlite3.Connection, day: str, sets: dict, trades: dict,
               source: str = "applied_observed") -> tuple[int, int]:
    new_sets = 0
    for sym_side, e in sets.items():
        body = json.dumps(e["full_config"], sort_keys=True, separators=(",", ":")).encode()
        sha = hashlib.sha256(body).hexdigest()
        if con.execute("SELECT 1 FROM sets WHERE sha256=?", (sha,)).fetchone() is None:
            con.execute("INSERT INTO sets(sha256,n_keys,first_day,body) VALUES(?,?,?,?)",
                        (sha, len(e["full_config"]), day, gzip.compress(body, 6)))
            new_sets += 1
        con.execute(
            "INSERT OR REPLACE INTO day_sets(day,sym_side,sha256,cat_side,applied_at,winning_tag,"
            "template_md5,defaults_round,wsharpe,pool_sharpe,trades,max_dd_pct,gain_vs_bh,source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, sym_side, sha, e["cat_side"], e["applied_at"], e["winning_tag"], e["template_md5"],
             str(e["defaults_round"]), e["wsharpe"], e["pool_sharpe"], e["trades"], e["max_dd_pct"],
             e["gain_vs_bh"], source))
    n_events = 0
    for (acct, sym_side), rows in trades.items():
        body = ("\n".join(json.dumps(r, separators=(",", ":")) for r in rows)).encode()
        con.execute("INSERT OR REPLACE INTO day_trades(day,account,sym_side,n_events,body) VALUES(?,?,?,?,?)",
                    (day, acct, sym_side, len(rows), gzip.compress(body, 6)))
        n_events += len(rows)
    con.commit()
    return new_sets, n_events


def _prune_db_copies(root: Path, keep_dailies: int) -> int:
    """Keep the newest `keep_dailies` snapshots plus the FIRST snapshot of every month, forever."""
    d = root / "db_copies"
    if not d.is_dir():
        return 0
    files = sorted(p for p in d.iterdir() if p.name.endswith(".db.gz"))
    if len(files) <= keep_dailies:
        return 0
    keep = set(files[-keep_dailies:])
    first_of_month: dict = {}
    for p in files:
        stamp = p.name.replace("per_sym_store_", "")[:10]
        first_of_month.setdefault(stamp[:7], p)
    keep |= set(first_of_month.values())
    removed = 0
    for p in files:
        if p not in keep:
            try:
                p.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def _stage(day: str, sets: dict, trades: dict, source: str = "applied_observed") -> Path:
    PENDING.mkdir(parents=True, exist_ok=True)
    p = PENDING / f"{day}.json.gz"
    payload = {"day": day, "sets": sets, "source": source,
               "trades": {f"{a}/{s}": rows for (a, s), rows in trades.items()}}
    with gzip.open(p, "wt", compresslevel=6) as fh:
        json.dump(payload, fh, separators=(",", ":"))
    return p


def _flush_pending(con: sqlite3.Connection) -> list:
    done = []
    if not PENDING.is_dir():
        return done
    for p in sorted(PENDING.iterdir()):
        if not p.name.endswith(".json.gz"):
            continue
        try:
            with gzip.open(p, "rt") as fh:
                payload = json.load(fh)
            trades = {}
            for k, rows in (payload.get("trades") or {}).items():
                acct, _, sym_side = k.partition("/")
                trades[(acct, sym_side)] = rows
            _write_day(con, payload["day"], payload.get("sets") or {}, trades,
                       payload.get("source") or "applied_observed")
            p.unlink()
            done.append(payload["day"])
        except Exception as exc:
            print(f"  !! pending {p.name} not flushed: {exc}", file=sys.stderr)
    return done


def _show(root: Path, sym_side: str, day: str) -> int:
    con = _connect_archive(root)
    r = con.execute("SELECT * FROM day_sets WHERE sym_side=? AND day<=? ORDER BY day DESC LIMIT 1",
                    (sym_side, day)).fetchone()
    if r is None:
        print(f"no archived set for {sym_side} on or before {day}")
        return 1
    cols = [c[0] for c in con.execute("SELECT * FROM day_sets LIMIT 0").description]
    meta = dict(zip(cols, r))
    body = con.execute("SELECT body FROM sets WHERE sha256=?", (meta["sha256"],)).fetchone()
    fc = json.loads(gzip.decompress(body[0])) if body else {}
    stale = " (carried forward — no newer archived version)" if meta["day"] != day else ""
    print(f"=== {sym_side}  settings in force on {day}{stale}")
    prov = {"applied_observed": "OBSERVED live in per_sym_active that day",
            "history_reconstructed": "RECONSTRUCTED from the board published at/before that day "
                                     "(published != proven applied)"}
    print(f"    provenance: {meta.get('source')} — {prov.get(meta.get('source'), 'unknown')}")
    print(f"    archived day {meta['day']}   applied_at {meta['applied_at']}   board {meta['winning_tag']}")
    print(f"    cat_side {meta['cat_side']}  template_md5 {meta['template_md5']}  defaults_round {meta['defaults_round']}")
    print(f"    board numbers: wsharpe={meta['wsharpe']} pool_sharpe={meta['pool_sharpe']} "
          f"trades={meta['trades']} max_dd_pct={meta['max_dd_pct']} gain_vs_bh={meta['gain_vs_bh']}")
    print(f"    full set: {len(fc)} keys   sha256 {meta['sha256'][:16]}")
    for row in con.execute("SELECT account,n_events,body FROM day_trades WHERE sym_side=? AND day=? "
                           "ORDER BY account", (sym_side, day)):
        print(f"\n--- {row[0]}: {row[1]} events on {day} (indicators as recorded live)")
        for line in gzip.decompress(row[2]).decode().splitlines():
            ev = json.loads(line)
            ind = ev.get("indicators") or {}
            print(f"    {ev.get('ts')}  {str(ev.get('type')):12s} qty={ev.get('qty')} px={ev.get('price')} "
                  f"val={ev.get('value')}  {ev.get('reason')}")
            if ind:
                print(f"        technicals: {json.dumps(ind, sort_keys=True)[:400]}")
    con.close()
    return 0


def _verify(root: Path) -> int:
    con = _connect_archive(root)
    bad = 0
    n = 0
    for sha, blob in con.execute("SELECT sha256,body FROM sets"):
        n += 1
        if hashlib.sha256(gzip.decompress(blob)).hexdigest() != sha:
            print(f"  CORRUPT set blob {sha}", file=sys.stderr)
            bad += 1
    orphan = con.execute("SELECT COUNT(*) FROM day_sets WHERE sha256 NOT IN (SELECT sha256 FROM sets)").fetchone()[0]
    days = con.execute("SELECT COUNT(DISTINCT day) FROM day_sets").fetchone()[0]
    print(f"=== VERIFY {root}")
    print(f"    distinct sets {n}  content-hash failures {bad}  day_sets rows with no blob {orphan}  days {days}")
    con.close()
    return 1 if (bad or orphan) else 0


def _list_days(root: Path) -> int:
    con = _connect_archive(root)
    print(f"=== {root}")
    print(f"{'day':12s} {'sym_sides':>9s} {'new sets':>9s} {'trade events':>12s}  ran_at")
    for day, n in con.execute("SELECT day,COUNT(*) FROM day_sets GROUP BY day ORDER BY day"):
        r = con.execute("SELECT n_new_sets,n_events,ran_at FROM runs WHERE day=? ORDER BY ran_at DESC LIMIT 1",
                        (day,)).fetchone() or (None, None, "")
        print(f"{day:12s} {n:9d} {str(r[0]):>9s} {str(r[1]):>12s}  {r[2]}")
    sz = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    print(f"    archive size on disk: {sz/1e9:.2f} GB")
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="", help="UTC day to archive (default today)")
    ap.add_argument("--root", default="", help="archive root (default: first mounted ext volume)")
    ap.add_argument("--keep-db-dailies", type=int, default=14,
                    help="daily whole-db snapshots to keep; the 1st of each month is kept forever")
    ap.add_argument("--no-db-copy", action="store_true", help="skip the 1.1 GB whole-db snapshot")
    ap.add_argument("--show", default="", help="SYM_SIDE: print the set in force that day + that day's trades")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--list-days", action="store_true")
    a = ap.parse_args()
    day = a.day or _utc_today()
    root = _ext_root(a.root)

    if a.show or a.verify or a.list_days:
        if root is None:
            print("!! no mounted external archive volume — cannot read the archive", file=sys.stderr)
            return 1
        if a.show:
            return _show(root, a.show, day)
        return _verify(root) if a.verify else _list_days(root)

    if not LIVE_DB.exists():
        print(f"!! live store missing: {LIVE_DB}", file=sys.stderr)
        return 1
    today = _utc_today()
    if day == today:
        sets, source = _read_live_sets(), "applied_observed"
    else:
        sets, source = _reconstruct_sets(day), "history_reconstructed"
    trades = _read_day_trades(day)
    if not sets:
        print("!! no sets for that day — refusing to write an empty day", file=sys.stderr)
        return 1
    n_ev = sum(len(v) for v in trades.values())
    print(f"=== per_sym_daily_archive {day}   {len(sets)} sets [{source}], {n_ev} trade events, "
          f"{len({a for a, _ in trades})} accounts")
    if source == "history_reconstructed":
        print("    NOTE: a past day — sets come from the newest board PUBLISHED at or before that day."
              "\n          Published is not proven applied; only a same-day run observes what live held.")

    if root is None:
        p = _stage(day, sets, trades, source)
        print(f"    NO external volume mounted — staged to {p}")
        print(f"    tried: {', '.join(str(c) for c in EXT_CANDIDATES)}")
        print("    the next run with the drive present flushes it; the day is NOT lost")
        return 0

    con = _connect_archive(root)
    flushed = _flush_pending(con)
    if flushed:
        print(f"    flushed staged days: {', '.join(flushed)}")
    new_sets, n_events = _write_day(con, day, sets, trades, source)
    sha, nbytes = "", 0
    if not a.no_db_copy:
        (root / "db_copies").mkdir(parents=True, exist_ok=True)
        sha, nbytes = _snapshot_live_db(root / "db_copies" / f"per_sym_store_{day}.db")
        removed = _prune_db_copies(root, a.keep_db_dailies)
        print(f"    db snapshot {nbytes/1e9:.2f} GB -> db_copies/per_sym_store_{day}.db.gz "
              f"(sha {sha[:16]}, pruned {removed})")
    con.execute("INSERT INTO runs(day,ran_at,n_sym_sides,n_new_sets,n_accounts,n_events,"
                "live_db_sha256,live_db_bytes,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (day, _dt.datetime.now(_dt.timezone.utc).isoformat(), len(sets), new_sets,
                 len({a for a, _ in trades}), n_events, sha, nbytes,
                 "ok" if not a.no_db_copy else "no-db-copy"))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
    ndays = con.execute("SELECT COUNT(DISTINCT day) FROM day_sets").fetchone()[0]
    con.close()
    print(f"    wrote {len(sets)} day_sets ({new_sets} sets new to the archive), {n_events} events")
    print(f"    archive now holds {ndays} days / {total} distinct sets at {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
