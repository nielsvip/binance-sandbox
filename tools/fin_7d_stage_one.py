"""Stage ONE finished fin 7D board into per_sym_store_7d.db on S1, the store fin's 7D states are built from.

USER 2026-10-31: "any crypto sym_side that has not had an update for >20hrs has to get updated and traded on 7D fin
account simple". Publishing a side in sevenday_crypto.json only AUTHORISES fin to enter it; what fin actually trades
comes from its states, and those come from this store. Without it fin would trade men's 30D settings.

Built like `v15_trc_7d_run.stage` (the proven stock twin): the row is the side's 30D base with the board's 7D
`cumulative_overrides` laid on top of its defaults snapshot. Unlike the old trc stager it does NOT inherit the base's
label: meta_json declares window_days 7 and keeps the base visible as base_window_days / base_source. The store is
stamped 7d_fin and guarded by tools/per_sym_window_identity.py, so a 30D row can never be written into it and a 30D
state producer can never read it.

Upsert only: a side staged earlier keeps its row until it is restaged, so a held fin position always has a state to
be managed from. Entry freshness is not decided here; the trader's <30 h gate on sevenday_crypto.json decides it.
"""
import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import per_sym_window_identity as WID  # noqa: E402


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stage(sym_side, overrides, base_db, store_db, metrics=None, day=None):
    ss = str(sym_side).upper()
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError(f"{ss}: no 7D cumulative_overrides — nothing to stage")
    src = sqlite3.connect(f"file:{base_db}?mode=ro", uri=True, timeout=30)
    src.row_factory = sqlite3.Row
    try:
        cols = [r[1] for r in src.execute("PRAGMA table_info(per_sym_active)")]
        row = src.execute("SELECT * FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
        ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='per_sym_active'").fetchone()[0]
    finally:
        src.close()
    if row is None:
        raise ValueError(f"{ss}: no 30D base row in {base_db} — a 7D set is laid on its 30D base and cannot exist without it")
    values = dict(row)
    defaults = json.loads(values.get("defaults_snapshot_json") or "{}")
    full = {**defaults, **overrides}
    try:
        meta = json.loads(values.get("meta_json") or "{}")
    except ValueError:
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("window_days") not in (None, 7):
        meta.setdefault("base_window_days", meta["window_days"])
    if meta.get("source") is not None:
        meta.setdefault("base_source", meta["source"])
    meta.update(window_days=7, source="fin_7d_daily", account="fin", book="men", run_day=day, staged_utc=now_iso(),
                base_updated_at=values.get("updated_at"))
    if metrics:
        meta["metrics_7d"] = metrics
    values.update(overrides_json=json.dumps(overrides, sort_keys=True, default=str),
                  full_config_json=json.dumps(full, sort_keys=True, default=str),
                  updated_at=now_iso(), meta_json=json.dumps(meta, sort_keys=True, default=str))
    Path(store_db).parent.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(str(store_db), timeout=60)
    try:
        dst.execute("PRAGMA busy_timeout=60000")
        if not dst.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='per_sym_active'").fetchone():
            dst.execute(ddl)
        dst.execute("CREATE TABLE IF NOT EXISTS kv_json (key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT, epoch REAL)")
        dst.execute("BEGIN IMMEDIATE")
        dst.execute("DELETE FROM per_sym_active WHERE sym_side=?", (ss,))
        dst.execute(f"INSERT INTO per_sym_active ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [values[c] for c in cols])
        dst.execute("COMMIT")
    finally:
        dst.close()
    # identity + guards (idempotent; a no-op once correct)
    report = WID.process(Path(store_db), Path.home() / "backups", dry=False)
    return {"sym_side": ss, "store": str(store_db), "overrides": len(overrides), "full_config": len(full),
            "identity": report.get("state") or report.get("kv_json")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--overrides", required=True, help="JSON file holding the board's cumulative_overrides")
    ap.add_argument("--base-db", required=True, help="the 30D store fin's control (men) trades from")
    ap.add_argument("--store-db", required=True, help="per_sym_store_7d.db")
    ap.add_argument("--metrics", default=None, help="optional JSON file with the board's 7D metrics")
    ap.add_argument("--day", default=None)
    a = ap.parse_args(argv)
    overrides = json.loads(Path(a.overrides).read_text())
    metrics = json.loads(Path(a.metrics).read_text()) if a.metrics else None
    print(json.dumps(stage(a.sym_side, overrides, a.base_db, a.store_db, metrics, a.day)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
