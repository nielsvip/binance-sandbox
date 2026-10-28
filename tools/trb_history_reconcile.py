#!/usr/bin/env python3
"""Dry-run reconciler: trb history JSONL vs broker fills (Tradier /history trade events).

Reads only. Reports history rows (OPEN/CLOSE/REDUCE/...) that have no broker fill on
the same trade date and symbol+side with the same quantity. Writes nothing into
data/tradier/history. The live writer (tradier_manage._append_to_history) is unchanged.

Usage: python3 tools/trb_history_reconcile.py [--since 2026-10-01] [--out path.json]
"""
import argparse, collections, glob, json, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "live_desk"))


def broker_fills(since):
    import server as srv
    srv._load_env_gpg()
    import urllib.request
    tok = os.environ["TRADIER_API_KEY_TRB"]
    aid = os.environ["TRADIER_ACCOUNT_ID_TRB"]
    req = urllib.request.Request(
        f"https://api.tradier.com/v1/accounts/{aid}/history?limit=5000",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
    )
    ev = json.load(urllib.request.urlopen(req, timeout=60))["history"]["event"]
    ev = ev if isinstance(ev, list) else [ev]
    fills = collections.Counter()
    for e in ev:
        if e.get("type") != "trade":
            continue
        t = e["trade"]
        d = e["date"][:10]
        if d < since:
            continue
        q = float(t["quantity"])
        side = "LONG" if q > 0 else "SHORT"
        fills[(d, t["symbol"], side, round(abs(q), 4))] += 1
    return fills


def history_rows(since):
    rows = []
    for f in glob.glob(os.path.join(ROOT, "data", "tradier", "history", "trb", "*.jsonl")):
        sym_side = os.path.basename(f)[:-6]
        sym, _, side = sym_side.rpartition("_")
        for ln in open(f):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            d = str(e.get("ts", ""))[:10]
            if d < since:
                continue
            rows.append((d, sym, side, e.get("type"), round(abs(float(e.get("qty") or 0)), 4), e.get("price"), e.get("reason"), sym_side))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-01")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    fills = broker_fills(a.since)
    rows = history_rows(a.since)
    unmatched = []
    for d, sym, side, typ, q, px, reason, key in rows:
        k = (d, sym, side, q)
        if fills.get(k, 0) > 0:
            fills[k] -= 1
        else:
            unmatched.append({"date": d, "sym_side": key, "type": typ, "qty": q, "price": px, "reason": reason})
    rep = {
        "since": a.since,
        "broker_fills": sum(fills.values()) + (len(rows) - len(unmatched)),
        "history_rows": len(rows),
        "history_rows_without_fill": len(unmatched),
        "by_type": collections.Counter(u["type"] for u in unmatched),
        "sample": unmatched[:20],
    }
    rep["by_type"] = dict(rep["by_type"])
    out = json.dumps(rep, indent=1, default=str)
    print(out)
    if a.out:
        open(a.out, "w").write(out)


if __name__ == "__main__":
    main()
