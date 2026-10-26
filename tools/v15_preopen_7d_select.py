#!/usr/bin/env python3
"""Select current tradeable sides with a complete per-sym SQL baseline.

The output is an evaluation queue, not a live promotion. No settings are changed.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.account_comparison_books import FIN_MIRROR_END, side_symbols, source_account  # noqa: E402
from tools.v15_best_first import tradeable  # noqa: E402
ACCOUNTS = {"fin": ("fin", "men"), "trc": ("trc", "trb")}


def _epoch(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0


def _traded(sym_side, accounts, cutoff, root):
    for account in accounts:
        path = root / "data" / "history" / account / f"{sym_side}.jsonl"
        try:
            # History is append-only. Inspect all records: file mtime does not prove trade time.
            with path.open() as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if _epoch(row.get("ts")) >= cutoff and str(row.get("type") or "").upper() in {
                        "OPEN", "CLOSE", "AUGMENT", "REDUCE", "REENTRY", "GHOST_CLOSE", "BUY", "SELL"
                    }:
                        return True
        except OSError:
            pass
    return False


def select(root, now, selection, source_db=None):
    cutoff = now - 7 * 86400
    at = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    source_db = Path(source_db or os.environ.get("V15_7D_SOURCE_DB") or root / "data/hourly_reconfig/per_sym_store.db")
    db_sha256 = hashlib.sha256(source_db.read_bytes()).hexdigest()
    fin_ok = set()
    stock_ok = set()
    for side in ("LONG", "SHORT"):
        fin_ok |= {f"{sym}_{side}" for sym in side_symbols(root, "fin", side, at)}
        stock_ok |= {f"{sym}_{side}" for sym in side_symbols(root, "trc", side, at)}
    allowed_now = tradeable(root)
    fin_ok &= allowed_now
    stock_ok &= allowed_now
    out = {"_meta": {"created_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                      "cutoff": dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).isoformat(),
                      "source": "per_sym_active SQL + current tradeable side books",
                      "source_db_sha256": db_sha256,
                      "window_days": 7, "fin_book_source": source_account("fin", at),
                      "trc_book_source": "trb", "fin_mirror_end_utc": FIN_MIRROR_END.isoformat()},
           "accounts": {"fin": [], "trc": []}, "sym_sides": {}}
    missing = []
    con = sqlite3.connect(f"file:{source_db.resolve()}?mode=ro", uri=True)
    try:
        for account, accepted in (("fin", fin_ok), ("trc", stock_ok)):
            for ss in sorted(accepted):
                row = con.execute("SELECT updated_at, overrides_json, defaults_snapshot_json, full_config_json FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
                if row is None:
                    missing.append(ss)
                    continue
                try:
                    updated_at, overrides_raw, defaults_raw, full_raw = row
                    overrides, defaults, full = (json.loads(value) for value in (overrides_raw, defaults_raw, full_raw))
                except (TypeError, ValueError):
                    missing.append(ss)
                    continue
                if not all(isinstance(value, dict) and value for value in (overrides, defaults, full)) or full != {**defaults, **overrides}:
                    missing.append(ss)
                    continue
                out["accounts"][account].append(ss)
                out["sym_sides"][ss] = {"cat_side": ("CRYPTO" if ss.rsplit("_", 1)[0].endswith(("USDT", "USDC")) else "STOCKS") + "_" + ss.rsplit("_", 1)[1],
                                         "overrides": overrides, "source_kind": "per_sym_sql", "sql_baseline_ok": True,
                                         "source_db_sha256": db_sha256, "source_updated_at": updated_at,
                                         "source_row_sha256": hashlib.sha256(json.dumps({"sym_side": ss, "updated_at": updated_at, "overrides": overrides, "defaults": defaults, "full": full}, sort_keys=True, default=str).encode()).hexdigest()}
    finally:
        con.close()
    for account in out["accounts"]:
        out["accounts"][account].sort()
    out["_meta"]["n"] = len(out["sym_sides"])
    out["_meta"]["missing_sql_baseline"] = sorted(set(missing))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", default=str(ROOT / "data/published_selection.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--at", default=None, help="ISO UTC evaluation time, default now")
    args = ap.parse_args()
    now = _epoch(args.at) if args.at else dt.datetime.now(dt.timezone.utc).timestamp()
    doc = select(ROOT, now, json.loads(Path(args.selection).read_text()))
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.replace(p)
    print(json.dumps({"out": str(p), "n": doc["_meta"]["n"],
                      "fin": len(doc["accounts"]["fin"]), "trc": len(doc["accounts"]["trc"])}))


if __name__ == "__main__":
    main()
