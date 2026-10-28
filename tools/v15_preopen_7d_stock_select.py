#!/usr/bin/env python3
"""Stock 7D queue: every tradeable stock sym_side, best-first by the latest 30D backtest.

Source: per_sym_active rows (overrides + delta_30d + gain_30d + updated_at). Tradeable means:
  - symbol is in symbols_tradier.json (the Tradier stock universe),
  - symbol is NOT a Binance TradFi perpetual (data/excluded_tokenized_stocks.json 'symbols'),
  - the row has a 30D delta.
Ranked by delta_30d descending (ties by gain_30d). The start set is the stored overrides, exactly like the crypto
queue. Config drift (full_config != defaults + overrides) is counted and listed per side, never hidden.

Output: data/preopen_7d/stock_queue_<day>.json  (same shape as the crypto queue, scope trc_stocks).
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDE_RE = re.compile(r"([A-Z]{1,6})_(LONG|SHORT)")


def build(root: Path, now: dt.datetime, db: Path):
    universe = set(json.loads((root / "symbols_tradier.json").read_text()))
    excl = json.loads((root / "data/excluded_tokenized_stocks.json").read_text())
    tokenized = set(excl["symbols"]) | set(excl["tradfi_non_equity_symbols"])
    db_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    con = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    rows = con.execute("SELECT sym_side, updated_at, delta_30d, gain_30d, overrides_json, defaults_snapshot_json, "
                       "full_config_json FROM per_sym_active").fetchall()
    con.close()
    accepted, excluded = [], {"not_in_tradier_universe": [], "tradfi_perpetual": [], "no_delta_30d": [],
                              "bad_json": []}
    for ss, upd, d30, g30, ov, df, fc in rows:
        m = SIDE_RE.fullmatch(ss or "")
        if not m:
            continue
        sym = m.group(1)
        if sym not in universe:
            excluded["not_in_tradier_universe"].append(ss)
            continue
        if sym in tokenized:
            excluded["tradfi_perpetual"].append(ss)
            continue
        if d30 is None:
            excluded["no_delta_30d"].append(ss)
            continue
        try:
            overrides, defaults, full = json.loads(ov), json.loads(df), json.loads(fc)
        except (TypeError, ValueError):
            excluded["bad_json"].append(ss)
            continue
        if not overrides:
            excluded["bad_json"].append(ss)
            continue
        drift = sorted(k for k in set(overrides) | set(defaults) | set(full)
                       if ({**defaults, **overrides}).get(k) != full.get(k))
        accepted.append({"ss": ss, "updated_at": upd, "delta_30d": float(d30), "gain_30d": g30,
                         "overrides": overrides, "drift": drift})
    accepted.sort(key=lambda x: (-x["delta_30d"], -(x["gain_30d"] or 0), x["ss"]))
    out = {"_meta": {"created_at": now.isoformat(), "scope": "trc_stocks", "rank": "delta_30d desc",
                     "source_db": str(db), "source_db_sha256": db_sha, "n": len(accepted),
                     "excluded_counts": {k: len(v) for k, v in excluded.items()},
                     "drift_sides": sum(1 for a in accepted if a["drift"]), "require_replay": True,
                     "window_days": 7},
           "accounts": {"trc": [], "fin": []}, "sym_sides": {}, "ranking": []}
    for rank, a in enumerate(accepted, 1):
        ss = a["ss"]
        side = ss.rsplit("_", 1)[1]
        out["sym_sides"][ss] = {
            "cat_side": f"STOCKS_{side}", "overrides": a["overrides"], "source_kind": "per_sym_sql",
            "sql_baseline_ok": True, "source_db_sha256": db_sha, "source_updated_at": a["updated_at"],
            "source_row_sha256": hashlib.sha256(json.dumps({"ss": ss, "u": a["updated_at"], "o": a["overrides"]},
                                                           sort_keys=True).encode()).hexdigest(),
            "delta_30d": a["delta_30d"], "gain_30d": a["gain_30d"], "rank": rank, "config_drift": a["drift"]}
        out["accounts"]["trc"].append(ss)
        out["ranking"].append(ss)
    out["excluded"] = excluded
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/hourly_reconfig/per_sym_store.db"))
    ap.add_argument("--out", default="")
    ap.add_argument("--day", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    doc = build(ROOT, now, Path(a.db))
    out = Path(a.out) if a.out else ROOT / f"data/preopen_7d/stock_queue_{a.day}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    print(json.dumps({"out": str(out), "n": doc["_meta"]["n"], "drift_sides": doc["_meta"]["drift_sides"],
                      "excluded": doc["_meta"]["excluded_counts"],
                      "top": doc["ranking"][:5], "bottom": doc["ranking"][-3:]}))


if __name__ == "__main__":
    main()
