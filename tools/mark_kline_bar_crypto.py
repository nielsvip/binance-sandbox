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


def mark_minute(sym, start_s):
    rows = get("/markPriceKlines", {"symbol": sym, "interval": "1m", "startTime": (start_s + 14 * 60) * 1000,
                                    "limit": 2})
    rows = [r for r in rows if int(r[0]) // 1000 == start_s + 14 * 60]
    if not rows:
        return None
    r = rows[0]
    return {"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}


def build(sym, start_s):
    nat = native_minutes(sym, start_s)
    mk = mark_minute(sym, start_s)
    if mk is None or len(nat) < 14:
        return {"symbol": sym, "start": start_s, "complete": False,
                "native_minutes": len(nat), "mark_minute": mk is not None}
    highs = [m["h"] for m in nat] + [mk["h"]]
    lows = [m["l"] for m in nat] + [mk["l"]]
    return {"symbol": sym, "start": start_s, "complete": True, "open": nat[0]["o"],
            "high": max(highs), "low": min(lows), "close": mk["c"],
            "volume_native_0_13": sum(m["v"] for m in nat), "native_minutes": len(nat),
            "ready_at": start_s + BAR}


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
