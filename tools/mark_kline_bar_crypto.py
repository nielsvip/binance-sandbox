#!/usr/bin/env python3
"""Crypto 15m OHLCV: native 1m klines for minutes :00-:13, mark-price 1m kline for minute :14.

A bar [start, start+900) is ready at start+900 (:15:00) because the mark 1m kline for :14:00-:14:59
closes at :15:00. Shadow output only: data/mark_bars/binance/<SYM>.jsonl.
Binance REST: /fapi/v1/klines (native) and /fapi/v1/markPriceKlines (mark). Read-only.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BAR = 900
MIN_MS = 60_000
API = "https://fapi.binance.com/fapi/v1"


def get(path, params):
    url = API + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def native_minutes(sym, start_s):
    rows = get("/klines", {"symbol": sym, "interval": "1m", "startTime": start_s * 1000,
                           "endTime": (start_s + 14 * 60) * 1000 - 1, "limit": 20})
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5])} for r in rows]


def mark_minute(sym, start_s, minute=14):
    t0 = start_s + minute * 60
    rows = get("/markPriceKlines", {"symbol": sym, "interval": "1m", "startTime": t0 * 1000, "limit": 2})
    rows = [r for r in rows if int(r[0]) // 1000 == t0]
    if not rows:
        return None
    r = rows[0]
    return {"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}


def build(sym, start_s):
    """Initial bar from what is available at close: native 1m :00-:12, minute :13 native if
    it has arrived else its mark, minute :14 always its mark (never waits for the kline)."""
    nat = native_minutes(sym, start_s)
    nat_by_min = {(m["t"] - start_s) // 60: m for m in nat}
    mk14 = mark_minute(sym, start_s)
    if mk14 is None:
        return {"symbol": sym, "start": start_s, "complete": False, "reason": "no mark :14"}
    mins = []
    for k in range(13):
        m = nat_by_min.get(k)
        if m is None:
            return {"symbol": sym, "start": start_s, "complete": False, "reason": f"native :{k:02d} missing"}
        mins.append(m)
    m13 = nat_by_min.get(13)
    if m13 is not None:
        mins.append(m13)
        src13 = "native"
    else:
        mk13 = mark_minute(sym, start_s, minute=13)
        if mk13 is None:
            return {"symbol": sym, "start": start_s, "complete": False, "reason": "no :13 native or mark"}
        mins.append(mk13)
        src13 = "mark"
    mins.append(mk14)
    highs = [m["h"] for m in mins]
    lows = [m["l"] for m in mins]
    return {"symbol": sym, "start": start_s, "complete": True, "open": mins[0]["o"],
            "high": max(highs), "low": min(lows), "close": mk14["c"],
            "volume_native_0_12": sum(m.get("v", 0.0) for m in mins[:13] if "v" in m),
            "minute13_source": src13, "ready_at": start_s + BAR, "real_kline_pending": True}


def compare_native_15m(sym, start_s, built):
    rows = get("/klines", {"symbol": sym, "interval": "15m", "startTime": start_s * 1000, "limit": 1})
    if not rows:
        return None
    r = rows[0]
    o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
    return {"symbol": sym, "start": start_s,
            "open_diff": built["open"] - o, "high_diff": built["high"] - h,
            "low_diff": built["low"] - l, "close_diff": built["close"] - c}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--start", type=int, required=True, help="bar start epoch s, multiple of 900")
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()
    if a.start % BAR:
        raise SystemExit("start must align to 15m")
    out = Path(a.out) if a.out else None
    for sym in a.symbols.split(","):
        b = build(sym, a.start)
        print(json.dumps(b), flush=True)
        if a.compare and b.get("complete"):
            print(json.dumps({"compare": compare_native_15m(sym, a.start, b)}), flush=True)
        if out and b.get("complete"):
            out.mkdir(parents=True, exist_ok=True)
            with (out / f"{sym}.jsonl").open("a") as fh:
                fh.write(json.dumps(b, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
