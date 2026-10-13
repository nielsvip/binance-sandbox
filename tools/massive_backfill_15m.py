#!/usr/bin/env python3
"""Backfill tradier 15m caches with Massive.com history (stocks only).

Tradier intraday starts 2026-07-13 (~91d); 400D needs older history. Massive
/v2/aggs (adjusted=false = raw, matches Tradier) provides it. Pure backfill:
only bars OLDER than the cache head are added (zero impact on recent data);
overlap-week closes are compared and a >2%% mismatch skips the symbol.

  python3 tools/massive_backfill_15m.py --symbols ALKT --apply
"""
import argparse
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://api.massive.com/v2/aggs/ticker/{sym}/range/15/minute/{frm}/{to}"
GRID = [f"{h:02d}:{m:02d}" for h in range(12, 21) for m in (0, 15, 30, 45) if not (h == 12 and m < 30) and not (h == 20 and m > 0)]
GRIDSET = set(GRID)


def api_key():
    k = os.environ.get("MASSIVE_API_KEY", "").strip()
    if k:
        return k
    for c in (Path.home() / ".config" / "massive_api_key", Path.home() / ".massive_api_key"):
        if c.exists():
            return c.read_text().strip()
    raise RuntimeError("MASSIVE_API_KEY missing")


def fetch(sym, frm, to):
    q = urllib.parse.urlencode({"limit": 50000, "sort": "asc", "adjusted": "false", "apiKey": api_key()})
    req = urllib.request.Request(f"{URL.format(sym=sym, frm=frm, to=to)}?{q}", headers={"Authorization": f"Bearer {api_key()}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("results", [])


def to_row(x):
    return {"timestamp": dt.datetime.fromtimestamp(x["t"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
            "open": float(x["o"]), "high": float(x["h"]), "low": float(x["l"]), "close": float(x["c"]), "volume": float(x.get("v", 0))}


def in_session(ts):
    d = dt.datetime.strptime(ts[:10], "%Y-%m-%d")
    return d.weekday() < 5 and ts[11:16] in GRIDSET


def backfill(sym, frm, apply):
    p = ROOT / "klines_cache_backtest" / "tradier" / f"{sym}_15m.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        rows = []
    have = len(rows)
    head = rows[0]["timestamp"][:10] if rows else None
    try:
        res = fetch(sym, frm, head or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    except Exception as e:
        return {"sym": sym, "have": have, "action": "fetch-err", "error": repr(e)[:120]}
    mrows = [to_row(x) for x in res if in_session(dt.datetime.fromtimestamp(x["t"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))]
    if rows:
        old = [r for r in mrows if r["timestamp"] < rows[0]["timestamp"]]
    else:
        old = mrows
    if not apply:
        return {"sym": sym, "have": have, "would_add": len(old), "action": "dry"}
    if not old:
        return {"sym": sym, "have": have, "now": have, "action": "noop"}
    by_ts = {r["timestamp"]: r for r in rows}
    for r in old:
        by_ts.setdefault(r["timestamp"], r)
    merged = [by_ts[k] for k in sorted(by_ts)]
    if len(merged) <= have:
        return {"sym": sym, "have": have, "now": have, "action": "noop"}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged))
    os.replace(tmp, p)
    return {"sym": sym, "have": have, "now": len(merged), "added": len(merged) - have, "action": "written"}


def overlap_guard(sym):
    """Max |close diff| on overlap week vs current cache; None if no overlap."""
    p = ROOT / "klines_cache_backtest" / "tradier" / f"{sym}_15m.json"
    try:
        rows = json.loads(p.read_text())
    except Exception:
        return None
    if not rows:
        return None
    head = rows[0]["timestamp"][:10]
    hd = dt.datetime.strptime(head, "%Y-%m-%d")
    to = (hd + dt.timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        res = fetch(sym, head, to)
    except Exception:
        return None
    trm = {x["timestamp"][:16]: x for x in rows}
    diffs = []
    for x in res:
        k = dt.datetime.fromtimestamp(x["t"] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        t = trm.get(k)
        if t and t["close"] and x["c"]:
            diffs.append(abs(t["close"] - x["c"]) / t["close"])
    return max(diffs) if diffs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--from-date", default="2025-08-01")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-diff", type=float, default=0.02)
    a = ap.parse_args()
    for s in a.symbols:
        try:
            g = overlap_guard(s)
            if g is not None and g > a.max_diff:
                print(json.dumps({"sym": s, "action": "skip-seam", "max_diff": round(g, 4)}), flush=True)
                continue
            print(json.dumps(backfill(s, a.from_date, a.apply)), flush=True)
        except Exception as e:
            print(json.dumps({"sym": s, "error": repr(e)[:160]}), flush=True)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
