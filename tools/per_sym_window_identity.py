"""Every per-sym SQL store says which window it holds, and refuses a row that declares a different one.

USER 2026-10-31 (verbatim): "MAke sure 7D and 30D and 60D are makred correctly in the sql's so nothing can use the
wrong one".

WHAT WAS WRONG (measured 2026-10-31):
  * No store carried a window marker. The window lived only in the FILENAME and in column names (`gain_30d`,
    `delta_30d`, `baseline_30d`) that the 7D store shares verbatim — a 7D gain is stored in a column called gain_30d.
  * `per_sym_store.DB_PATH` comes straight from PER_SYM_STORE_DB, so any tool inheriting another lane's env writes
    into that lane's store with no way to notice.
  * Every genuine trc 7D row was LABELLED 30D: `v15_trc_7d_run.stage` builds the row as `values = dict(row)` from its
    30D base and replaces only overrides/full_config/updated_at, so `meta_json.window_days: 30` and the base's
    `source` ride along. 22 of 125 said window_days 30 and source v15_golive_headtohead. They were NOT overwritten —
    all 125 were last written by their 7D staging and none equals its 30D set — but anything reading the marker
    would take them for 30D sets, and the fin lane's own readers (v15_preopen_7d_runtime/_stage/_replay) require
    `window_days == 7`.

WHAT THIS DOES, per store, idempotently:
  1. online backup (sqlite backup API) next to ~/backups before any change
  2. kv_json['store_identity'] = {window_days, kind, account, book} — the store's authoritative window
  3. in a 7D store, relabel each per_sym_active/per_sym_history row: meta.window_days = 7, keeping the inherited
     base visible as meta.base_window_days / meta.base_source instead of discarding it
  4. guard triggers on INSERT and UPDATE of per_sym_active and per_sym_history: a row whose meta_json DECLARES a
     window_days other than the store's is refused by SQLite itself (RAISE ABORT), whichever code path writes it.
     A row that declares no window still passes — refusing those would break every existing 30D writer — so the
     guard catches the dangerous case (a declared-wrong window) exactly, and the identity stamp covers the rest.
No settings value is changed: overrides_json, full_config_json and every metric column are left exactly as they are.
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

KINDS = {
    "per_sym_store.db": {"window_days": 30, "kind": "30d", "account": None, "book": None},
    "per_sym_store_trc7d.db": {"window_days": 7, "kind": "7d_trc", "account": "trc", "book": "trb"},
    "per_sym_store_7d.db": {"window_days": 7, "kind": "7d_fin", "account": "fin", "book": "men"},
    "per_sym_store_60d.db": {"window_days": 60, "kind": "60d", "account": None, "book": None},
}
TABLES = ("per_sym_active", "per_sym_history")


def stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def identity_for(path: Path):
    return KINDS.get(path.name)


def backup(path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"before_window_identity_{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M}_{path.parent.parent.parent.name}_{path.name}"
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def light_backup(path: Path, out_dir: Path) -> Path:
    """A store this tool only ADDS to (kv_json identity row + guard triggers, no row rewrites) does not need a
    multi-GB copy on a host already under the disk floor: record the prior state of everything touched and the exact
    SQL that reverts it. Measured 2026-10-31: S1 had 8.0 GB free against a 40 GB floor and a 1.6 GB store."""
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        prior_kv = None
        if "kv_json" in tables_present(con):
            cols = [r[1] for r in con.execute("pragma table_info(kv_json)")]
            row = con.execute("select * from kv_json where key='store_identity'").fetchone()
            prior_kv = dict(zip(cols, row)) if row else None
        prior_triggers = {n: s for n, s in con.execute("select name, sql from sqlite_master where type='trigger' and name like 'guard_window_%'")}
    finally:
        con.close()
    names = [f"guard_window_{t}_{op}" for t in TABLES for op in ("insert", "update")]
    record = {"db": str(path), "taken_utc": stamp(), "prior_store_identity": prior_kv, "prior_guard_triggers": prior_triggers,
              "revert_sql": [f"DROP TRIGGER IF EXISTS {n};" for n in names if n not in prior_triggers]
                            + ([] if prior_kv else ["DELETE FROM kv_json WHERE key='store_identity';"])}
    dest = out_dir / f"before_window_identity_{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M}_{path.parent.parent.parent.name}_{path.stem}.revert.json"
    dest.write_text(json.dumps(record, indent=1, sort_keys=True))
    return dest


def tables_present(con):
    return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}


def relabel(con, window: int, dry: bool) -> dict:
    """Make each row's declared window true, keeping the inherited base visible."""
    counts = {}
    for table in TABLES:
        if table not in tables_present(con):
            continue
        cols = {r[1] for r in con.execute(f"pragma table_info({table})")}
        if "meta_json" not in cols:
            continue
        key = "id" if "id" in cols else "sym_side"
        changed = 0
        for rowkey, raw in con.execute(f"select {key}, meta_json from {table}").fetchall():
            try:
                meta = json.loads(raw or "{}")
            except ValueError:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            declared = meta.get("window_days")
            if declared == window and "window_relabelled_utc" in meta:
                continue
            if declared is not None and declared != window:
                meta.setdefault("base_window_days", declared)
                if meta.get("source") is not None:
                    meta.setdefault("base_source", meta.get("source"))
            if declared == window:
                continue
            meta["window_days"] = window
            meta["window_relabelled_utc"] = stamp()
            changed += 1
            if not dry:
                con.execute(f"update {table} set meta_json=? where {key}=?", (json.dumps(meta, sort_keys=True), rowkey))
        counts[table] = changed
    return counts


def install_guards(con, window: int, dry: bool) -> list:
    made = []
    for table in TABLES:
        if table not in tables_present(con):
            continue
        cols = {r[1] for r in con.execute(f"pragma table_info({table})")}
        if "meta_json" not in cols:
            continue
        for op in ("INSERT", "UPDATE"):
            name = f"guard_window_{table}_{op.lower()}"
            sql = (f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {op} ON {table} "
                   f"WHEN json_valid(NEW.meta_json) AND json_extract(NEW.meta_json, '$.window_days') IS NOT NULL "
                   f"AND CAST(json_extract(NEW.meta_json, '$.window_days') AS INTEGER) != {window} "
                   f"BEGIN SELECT RAISE(ABORT, 'WRONG_WINDOW: this store holds {window}D settings only "
                   f"(see kv_json store_identity); the row declares another window'); END")
            made.append(name)
            if not dry:
                con.execute(sql)
    return made


def process(path: Path, backups: Path, dry: bool, relabel_rows=None) -> dict:
    ident = identity_for(path)
    if ident is None:
        return {"db": str(path), "skipped": "unknown store name — not guessing its window"}
    # Rows are relabelled only in the 7D stores: their labels are WRONG (inherited 30). A 30D store's unlabelled rows
    # are 30D by its identity, and rewriting meta_json on hundreds of live rows on every host, each at a different
    # moment, would make the copies diverge for the sync tools. The guards protect the 30D store either way.
    if relabel_rows is None:
        relabel_rows = ident["window_days"] != 30
    report = {"db": str(path), "identity": ident, "dry_run": dry, "relabel_rows": relabel_rows}
    if not dry:
        report["backup"] = str(backup(path, backups) if relabel_rows else light_backup(path, backups))
    con = sqlite3.connect(str(path), timeout=60)
    try:
        con.execute("PRAGMA busy_timeout=60000")
        if not dry:
            con.execute("BEGIN IMMEDIATE")
        if "kv_json" in tables_present(con):
            cols = [r[1] for r in con.execute("pragma table_info(kv_json)")]
            doc = dict(ident, stamped_utc=stamp(), contract="this store holds ONLY these settings; never read it as another window")
            if not dry:
                value_col = "value_json" if "value_json" in cols else ("value" if "value" in cols else cols[1])
                fields = {"key": "store_identity", value_col: json.dumps(doc, sort_keys=True)}
                if "updated_at" in cols:
                    fields["updated_at"] = doc["stamped_utc"]
                if "epoch" in cols:
                    fields["epoch"] = dt.datetime.now(dt.timezone.utc).timestamp()
                con.execute(f"insert or replace into kv_json ({', '.join(fields)}) values ({', '.join('?' * len(fields))})", list(fields.values()))
            report["kv_json"] = "store_identity stamped" if not dry else "would stamp store_identity"
        else:
            report["kv_json"] = "no kv_json table — identity carried by triggers and row labels only"
        report["relabelled"] = relabel(con, ident["window_days"], dry) if relabel_rows else "not relabelled (30D store: identity + guards only)"
        report["guards"] = install_guards(con, ident["window_days"], dry)
        if not dry:
            con.execute("COMMIT")
    except Exception:
        if not dry:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return report


def verify(path: Path) -> dict:
    """Prove the guard bites: a row declaring the wrong window must be refused, inside a rolled-back transaction."""
    ident = identity_for(path)
    if ident is None:
        return {"db": str(path), "verify": "skipped"}
    wrong = 7 if ident["window_days"] != 7 else 30
    con = sqlite3.connect(str(path), timeout=60)
    out = {"db": str(path)}
    try:
        con.execute("BEGIN")
        cols = [r[1] for r in con.execute("pragma table_info(per_sym_active)")]
        probe = {c: None for c in cols}
        probe.update(sym_side="__WINDOW_GUARD_PROBE__", meta_json=json.dumps({"window_days": wrong}))
        try:
            con.execute(f"insert into per_sym_active ({', '.join(probe)}) values ({', '.join('?' * len(probe))})", list(probe.values()))
            out["guard"] = f"FAILED — a {wrong}D row was accepted into a {ident['window_days']}D store"
        except sqlite3.DatabaseError as exc:
            out["guard"] = f"OK — refused a {wrong}D row: {str(exc)[:90]}"
        declared = {}
        for (raw,) in con.execute("select meta_json from per_sym_active"):
            try:
                w = (json.loads(raw or "{}") or {}).get("window_days")
            except ValueError:
                w = "unparsable"
            declared[str(w)] = declared.get(str(w), 0) + 1
        out["declared_windows"] = declared
        kv = None
        if "kv_json" in tables_present(con):
            cols_kv = [r[1] for r in con.execute("pragma table_info(kv_json)")]
            value_col = "value_json" if "value_json" in cols_kv else ("value" if "value" in cols_kv else cols_kv[1])
            row = con.execute(f"select {value_col} from kv_json where key='store_identity'").fetchone()
            kv = json.loads(row[0]) if row else None
        out["store_identity"] = kv
    finally:
        con.execute("ROLLBACK")
        con.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dbs", nargs="+", help="per-sym store files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="prove each guard refuses a wrong-window row (rolled back)")
    ap.add_argument("--backups", default=str(Path.home() / "backups"))
    a = ap.parse_args(argv)
    for raw in a.dbs:
        path = Path(raw).expanduser()
        if not path.is_file():
            print(json.dumps({"db": str(path), "skipped": "missing"}))
            continue
        result = verify(path) if a.verify else process(path, Path(a.backups).expanduser(), a.dry_run)
        print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
