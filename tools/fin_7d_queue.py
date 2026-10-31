"""Build fin's 7D queue from what MEN ACTUALLY TRADES: S1's tradeable keys and S1's live 30D store.

USER 2026-10-31: "the 7D backtest has to run in <1h for allt radeable sym_sides for men and start executing trades
immediateluy".

Why not tools/v15_preopen_7d_select.py (measured 2026-10-31):
  * it runs where the driver runs (the Mac) and reads the MAC's per_sym_store.db and the Mac's tradeable view, not
    S1's live store men's states are built from;
  * it drops a row unless full_config == defaults + overrides. On S1, 17 of men's 36 tradeable keys failed that for
    one reason only: the retired key MIN_HOLD_BARS_BEFORE_EXIT is in defaults/overrides but stripped from
    full_config (the engine rejects it), plus one row where full_config carries the unified MIN_HOLD_BARS. Those are
    good baselines, excluded for nothing — the selector queued 9 sides when 35 qualify.

THE BASELINE IS WHAT MEN TRADES. Each side's start is its live `full_config_json` written as a diff from its
`defaults_snapshot_json`, so defaults + start == the config men trades, exactly, and fin's only difference from its
control is what the 7D board changes. Keys the publisher retired (in defaults, absent from full_config) are not
traded by men and are left out. A side with no 30D row has no baseline and is reported, never invented.
Output is the queue format tools/v15_preopen_7d_launch.py reads (`sym_sides[ss].overrides` seeds the 30D start).
"""
import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

SCOPE = "fin_men_crypto"


def build(tradeable_keys, store_db, control="men"):
    keys = sorted({str(k).split(":", 1)[1].upper() for k in json.loads(Path(tradeable_keys).read_text())
                   if str(k).startswith(f"{control}:")})
    db_sha256 = hashlib.sha256(Path(store_db).read_bytes()).hexdigest()
    con = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True, timeout=30)
    out = {"_meta": {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "scope": SCOPE, "window_days": 7,
                     "source": f"S1 live per_sym_store full_config (what {control} trades) + S1 tradeable_keys {control}:*",
                     "source_db": str(store_db), "source_db_sha256": db_sha256, "fin_book_source": control,
                     "control_keys": len(keys)},
           "accounts": {"fin": [], "trc": []}, "sym_sides": {}}
    missing, retired_total = [], {}
    try:
        for ss in keys:
            row = con.execute("SELECT updated_at, defaults_snapshot_json, full_config_json FROM per_sym_active WHERE sym_side=?",
                              (ss,)).fetchone()
            if row is None:
                missing.append({"sym_side": ss, "why": "no 30D row — no baseline to lay a 7D set on"})
                continue
            updated_at, defaults_raw, full_raw = row
            try:
                defaults, full = json.loads(defaults_raw or "{}"), json.loads(full_raw or "{}")
            except ValueError:
                missing.append({"sym_side": ss, "why": "30D row unparsable"})
                continue
            if not isinstance(defaults, dict) or not isinstance(full, dict) or not full:
                missing.append({"sym_side": ss, "why": "30D row has an empty full_config"})
                continue
            start = {k: v for k, v in full.items() if defaults.get(k, object()) != v}
            for k in set(defaults) - set(full):
                retired_total[k] = retired_total.get(k, 0) + 1
            out["accounts"]["fin"].append(ss)
            out["sym_sides"][ss] = {
                "cat_side": "CRYPTO_" + ss.rsplit("_", 1)[1], "overrides": start, "source_kind": "per_sym_sql",
                "sql_baseline_ok": True, "source_db_sha256": db_sha256, "source_updated_at": updated_at,
                "baseline": f"{control} full_config as a diff from defaults",
                "source_row_sha256": hashlib.sha256(json.dumps({"sym_side": ss, "updated_at": updated_at, "full": full},
                                                               sort_keys=True, default=str).encode()).hexdigest()}
    finally:
        con.close()
    out["_meta"].update(n=len(out["accounts"]["fin"]), missing_baseline=missing, retired_keys_left_out=retired_total)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tradeable-keys", default="/home/niels/binance/tradeable_keys.json")
    ap.add_argument("--store-db", default="/home/niels/binance/data/hourly_reconfig/per_sym_store.db")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    doc = build(a.tradeable_keys, a.store_db)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    tmp.replace(p)
    print(json.dumps({"out": str(p), "n": doc["_meta"]["n"], "control_keys": doc["_meta"]["control_keys"],
                      "missing": [m["sym_side"] for m in doc["_meta"]["missing_baseline"]],
                      "retired_keys_left_out": doc["_meta"]["retired_keys_left_out"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
