#!/usr/bin/env python3
"""build_per_sym_full_sets — ONE json holding the full switch dict for EVERY sym_side ever tested.

USER 2026-10-15: "data/per_sym_settings.json <- these are NOT the per_sym settings but the TEMPLATE defaults for each
cat_side. The per_sym settings should be in the db and in another json file that has the full dict exactly like the
per_sym_settings.json but not for crypto or stock but for every single sym_side that has ever been tested."

So the layers are, and the names now say so:
  data/per_sym_settings.json          4 keys  (CRYPTO_LONG/SHORT, STOCKS_LONG/SHORT) x ~3571 switches
                                      = the TEMPLATE / cat_side default layer, built from each template's bold row.
  data/hourly_reconfig/per_sym_store.db
                                      per_sym_active   (740 rows) = the sets applied live right now
                                      per_sym_history  (11341 rows) = every set ever published
  data/per_sym_full_sets.json         THIS FILE: 747 keys (every sym_side ever tested) x ~3573 switches,
                                      the same {key: {switch: value}} shape as per_sym_settings.json.

Per sym_side the full set is taken from per_sym_active when the sym_side is applied live (that is the proven-best set
in force), otherwise from its most recent per_sym_history row. `_meta.rows` records which source, when it was
published and its metrics, so the file is self-describing without changing the payload shape.

Streamed to disk key-by-key (747 x 3573 values is ~100 MB; never held as one serialized string), written to a temp
file and os.replace()d, so a reader never sees a partial file. A .gz sibling is written for cheap syncing.

Usage: python3 tools/build_per_sym_full_sets.py [--db PATH] [--out PATH] [--no-gz] [--quiet]
"""
import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# history blobs are zlib+base64 with a 'z:' prefix (per_sym_store keeps the DB small that way). Use the store's own
# decoder — 4 sym_sides (BLOK_SHORT, COST_SHORT, ETN_LONG, PYPL_LONG) were silently dropped before this.
from per_sym_store import _maybe_decompress
DEFAULT_DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
DEFAULT_OUT = ROOT / "data" / "per_sym_full_sets.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-gz", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--force", action="store_true", help="rebuild even when the output is newer than the db")
    a = ap.parse_args()
    t0 = time.time()
    db = Path(a.db)
    if not db.exists():
        print(f"no db at {db}", file=sys.stderr)
        return 2
    # skip when nothing changed: a stable mtime lets the S1 rsync no-op instead of rewriting 94 MB every run
    out_p = Path(a.out)
    if not a.force and out_p.exists() and out_p.stat().st_mtime >= db.stat().st_mtime:
        if not a.quiet:
            print(f"{out_p}: up to date (db unchanged since last build) — skipped")
        return 0
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=120.0)
    con.row_factory = sqlite3.Row

    # live-applied sets win; everything else falls back to its newest history row
    active = {}
    for r in con.execute("SELECT sym_side, cat_side, updated_at, winning_tag, pool_sharpe, trades, max_dd_pct, "
                         "acc_gain_pct, full_config_json FROM per_sym_active "
                         "WHERE full_config_json IS NOT NULL AND length(full_config_json) > 100"):
        active[r["sym_side"]] = r
    hist = {}
    for r in con.execute("SELECT sym_side, cat_side, updated_at, winning_tag, pool_sharpe, trades, max_dd_pct, "
                         "gain_pct, full_config_json, MAX(COALESCE(epoch, 0)) AS _e FROM per_sym_history "
                         "WHERE full_config_json IS NOT NULL AND length(full_config_json) > 100 "
                         "GROUP BY sym_side"):
        hist[r["sym_side"]] = r

    keys = sorted(set(active) | set(hist))
    out = Path(a.out)
    tmp = out.with_name(out.name + f".building.{os.getpid()}")
    meta_rows = {}
    n_active = n_hist = 0
    switch_counts = []
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as fh:
        fh.write("{\n")
        for key in keys:
            row, src = (active.get(key), "per_sym_active") if key in active else (hist.get(key), "per_sym_history")
            try:
                full = json.loads(_maybe_decompress(row["full_config_json"]))
            except Exception as exc:
                print(f"  !! {key}: unreadable full_config ({exc}) — skipped", file=sys.stderr)
                continue
            if not isinstance(full, dict) or not full:
                continue
            if src == "per_sym_active":
                n_active += 1
            else:
                n_hist += 1
            switch_counts.append(len(full))
            gain = row["acc_gain_pct"] if src == "per_sym_active" else row["gain_pct"]
            meta_rows[key] = {"source": src, "cat_side": row["cat_side"], "updated_at": row["updated_at"],
                              "winning_tag": row["winning_tag"], "pool_sharpe": row["pool_sharpe"],
                              "trades": row["trades"], "max_dd_pct": row["max_dd_pct"], "gain_pct": gain,
                              "n_switches": len(full)}
            fh.write(json.dumps(key) + ": " + json.dumps(full, sort_keys=True) + ",\n")
        meta = {"built_at": datetime.now(timezone.utc).isoformat(), "builder": "tools/build_per_sym_full_sets.py",
                "db": str(db), "n_sym_sides": len(meta_rows), "from_per_sym_active": n_active,
                "from_per_sym_history": n_hist,
                "switches_min": min(switch_counts) if switch_counts else 0,
                "switches_max": max(switch_counts) if switch_counts else 0,
                "shape": "{sym_side: {switch: value}} — same shape as data/per_sym_settings.json, which is the "
                         "cat_side TEMPLATE default layer (4 keys), not per-sym settings",
                "rows": meta_rows}
        fh.write(json.dumps("_meta") + ": " + json.dumps(meta, sort_keys=True) + "\n}\n")
        fh.flush()
        os.fsync(fh.fileno())
    con.close()
    # never publish a file that cannot be parsed
    with open(tmp) as fh:
        json.load(fh)
    os.replace(tmp, out)
    size = out.stat().st_size
    if not a.no_gz:
        gz = out.with_name(out.name + ".gz")
        gtmp = gz.with_name(gz.name + f".building.{os.getpid()}")
        with open(out, "rb") as src_fh, gzip.open(gtmp, "wb", compresslevel=6) as dst_fh:
            shutil.copyfileobj(src_fh, dst_fh, 1 << 20)
        os.replace(gtmp, gz)
    if not a.quiet:
        print(f"{out}: {len(meta_rows)} sym_sides ({n_active} live-applied, {n_hist} history-only), "
              f"{meta['switches_min']}-{meta['switches_max']} switches each, {size/1e6:.1f} MB"
              + ("" if a.no_gz else f" (+{out.with_name(out.name + '.gz').stat().st_size/1e6:.1f} MB gz)")
              + f" in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
