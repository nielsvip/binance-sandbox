#!/usr/bin/env python3
"""Select proven 30D sym_sides tradeable and actually traded in the past seven days.

The output is an evaluation queue, not a live promotion. No settings are changed.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.account_comparison_books import FIN_MIRROR_END, side_symbols, source_account  # noqa: E402
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


def select(root, now, selection):
    cutoff = now - 7 * 86400
    at = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    fin_ok = set()
    stock_ok = set()
    for side in ("LONG", "SHORT"):
        fin_ok |= {f"{sym}_{side}" for sym in side_symbols(root, "fin", side, at)}
        stock_ok |= {f"{sym}_{side}" for sym in side_symbols(root, "trc", side, at)}
    source = selection.get("sym_sides") or {}
    out = {"_meta": {"created_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                      "cutoff": dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).isoformat(),
                      "source": "published_selection + tradeable universe + executed trade history",
                      "window_days": 7, "fin_book_source": source_account("fin", at),
                      "trc_book_source": "trb", "fin_mirror_end_utc": FIN_MIRROR_END.isoformat()},
           "accounts": {"fin": [], "trc": []}, "sym_sides": {}}
    for ss, rec in source.items():
        if not isinstance(rec, dict) or not isinstance(rec.get("overrides"), dict) or rec.get("manifest_ok") is not True:
            continue
        for account, accepted in (("fin", fin_ok), ("trc", stock_ok)):
            if ss not in accepted or not _traded(ss, ACCOUNTS[account], cutoff, root):
                continue
            out["accounts"][account].append(ss)
            out["sym_sides"][ss] = rec
    for account in out["accounts"]:
        out["accounts"][account].sort()
    out["_meta"]["n"] = len(out["sym_sides"])
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
