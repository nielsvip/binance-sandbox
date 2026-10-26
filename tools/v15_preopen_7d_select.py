#!/usr/bin/env python3
"""Select proven 30D sym_sides tradeable and actually traded in the past seven days.

The output is an evaluation queue, not a live promotion. No settings are changed.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    keys = set(json.loads((root / "tradeable_keys.json").read_text()))
    fin_ok = {k.split(":", 1)[1] for k in keys if k.startswith("fin:")}
    stock_ok = set()
    for side in ("LONG", "SHORT"):
        p = root / f"symbols_trb_{side.lower()}.json"
        try:
            stock_ok |= {f"{sym}_{side}" for sym in json.loads(p.read_text())}
        except (OSError, ValueError, TypeError):
            pass
    # The side-specific books can be empty outside RTH; use the active stock
    # universe as the fallback, then require a published board and a real trade.
    if not stock_ok:
        try:
            stock_ok = {f"{sym}_{side}" for sym in json.loads((root / "stock_symbols.json").read_text())
                        for side in ("LONG", "SHORT")}
        except (OSError, ValueError, TypeError):
            pass
    source = selection.get("sym_sides") or {}
    out = {"_meta": {"created_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                      "cutoff": dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).isoformat(),
                      "source": "published_selection + tradeable universe + executed trade history",
                      "window_days": 7}, "accounts": {"fin": [], "trc": []}, "sym_sides": {}}
    for ss, rec in source.items():
        if not isinstance(rec, dict) or not isinstance(rec.get("overrides"), dict) or rec.get("manifest_ok") is False:
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
