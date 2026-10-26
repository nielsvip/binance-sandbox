#!/usr/bin/env python3
"""Apply newer published per-sym SQL rows delivered as small JSON files."""
import argparse
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLS = "sym_side,cat_side,updated_at,winning_tag,wsharpe,pool_sharpe,trades,max_dd_pct,acc_gain_pct,gain_vs_bh,bh_pct,delta_365,delta_30d,gain_30d,baseline_30d,campaign_ts,overrides_json,defaults_snapshot_json,full_config_json,template_md5,defaults_round,meta_json".split(",")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", action="append", required=True)
    parser.add_argument("--input-dir", default=str(ROOT / "data" / "per_sym_rows_current"))
    args = parser.parse_args()
    names = json.load(sys.stdin)
    if not isinstance(names, list) or len(names) > 800:
        raise SystemExit("invalid row list")
    rows = []
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Z0-9_]+\.json", name):
            raise SystemExit(f"invalid row name {name!r}")
        payload = json.loads((Path(args.input_dir) / name).read_text())
        row = payload.get("row")
        if not isinstance(row, list) or len(row) != len(COLS) or row[0] + ".json" != name or row[2] != payload.get("updated_at"):
            raise SystemExit(f"invalid row payload {name}")
        full = json.loads(row[18] or "{}")
        if not isinstance(full, dict) or not full:
            raise SystemExit(f"missing full_config {name}")
        rows.append(row)
    report = {}
    columns = ",".join(COLS)
    marks = ",".join("?" for _ in COLS)
    updates = ",".join(f"{column}=excluded.{column}" for column in COLS if column != "sym_side")
    sql = f"INSERT INTO per_sym_active ({columns}) VALUES ({marks}) ON CONFLICT(sym_side) DO UPDATE SET {updates}"
    for db_name in args.db:
        db = Path(db_name)
        if not db.is_file():
            raise SystemExit(f"missing target DB {db}")
        con = sqlite3.connect(str(db), timeout=20)
        changed = 0
        try:
            con.execute("BEGIN IMMEDIATE")
            for row in rows:
                previous = con.execute(f"SELECT {columns} FROM per_sym_active WHERE sym_side=?", (row[0],)).fetchone()
                if previous and str(previous[2] or "") >= str(row[2] or ""):
                    continue
                if previous:
                    backup = ROOT / "backups" / "per_sym_active_rows" / row[0]
                    backup.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    (backup / f"before_{stamp}.json").write_text(json.dumps(dict(zip(COLS, previous)), default=str))
                con.execute(sql, row)
                changed += 1
            con.commit()
            report[str(db)] = changed
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    print(json.dumps({"rows": len(rows), "written": report}), flush=True)


if __name__ == "__main__":
    main()
