#!/usr/bin/env python3
"""
Freshen crypto 15m klines in klines_cache_backtest by downloading the tail directly from
the Binance Futures API. Self-sufficient: does NOT depend on the local live klines feed
(which is only running when crypto trades on this host). Appends ONLY fully-closed bars
newer than each file's current last bar, atomically.

Symbol = file name as-is (BTCUSDC fetched as BTCUSDC — native USDC-perp series since 2024).
Universe: backtest_48_symbols.json ∪ crypto tradeable_keys, restricted to files that exist.

15m base only (CLAUDE.md: 15m is BASIS, all TFs derived by precompute).
"""
import json
import os
import time
import datetime
import urllib.request

BASE = os.environ.get("BINANCE_BASE", "/home/niels/binance-sandbox")
BT = os.path.join(BASE, "klines_cache_backtest")
FAPI = "https://fapi.binance.com/fapi/v1/klines"
TF = "15m"
TF_MS = 15 * 60 * 1000


def _load(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def iso(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000.0).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def to_ms(ts):
    try:
        return int(datetime.datetime.strptime(ts.replace("Z", ""), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def fetch(sym, start_ms):
    out = []
    now_ms = int(time.time() * 1000)
    cur = start_ms
    while cur < now_ms:
        url = f"{FAPI}?symbol={sym}&interval={TF}&startTime={cur}&limit=1500"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                rows = json.loads(r.read().decode())
        except Exception:
            break
        if not rows:
            break
        for k in rows:
            ot, cl = int(k[0]), int(k[6])
            if cl >= now_ms:  # skip the still-forming bar
                continue
            out.append({"timestamp": iso(ot), "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]), "_ot": ot})
        last_ot = int(rows[-1][0])
        if last_ot <= cur:
            break
        cur = last_ot + 1
        time.sleep(0.12)  # be gentle on the API / idle-friendly
    return out


def universe():
    syms = []
    seen = set()
    f48 = os.path.join(BASE, "backtest_48_symbols.json")
    if os.path.exists(f48):
        for s in (_load(f48) or []):
            if s not in seen:
                seen.add(s)
                syms.append(s)
    tk = _load(os.path.join(BASE, "tradeable_keys.json")) or []
    for k in tk:
        if k.endswith("_LONG") or k.endswith("_SHORT"):
            s = k.split(":", 1)[1].rsplit("_", 1)[0] if ":" in k else k.rsplit("_", 1)[0]
            if (s.endswith("USDT") or s.endswith("USDC")) and s not in seen:
                seen.add(s)
                syms.append(s)
    # keep only those with an existing backtest 15m file
    return [s for s in syms if os.path.exists(os.path.join(BT, f"{s}_{TF}.json"))]


def freshen(sym):
    path = os.path.join(BT, f"{sym}_{TF}.json")
    data = _load(path)
    if not data:
        return 0
    last_ms = to_ms(data[-1].get("timestamp", ""))
    if last_ms is None:
        return 0
    new = fetch(sym, last_ms + 1)
    new = [b for b in new if to_ms(b["timestamp"]) > last_ms]
    if not new:
        return 0
    for b in new:
        b.pop("_ot", None)
    data.extend(new)
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)
    return len(new)


def main():
    syms = universe()
    total = 0
    fresh_syms = 0
    for s in syms:
        n = freshen(s)
        total += n
        if n:
            fresh_syms += 1
    print(f"crypto freshen: {len(syms)} syms, {fresh_syms} updated, {total} bars appended", flush=True)


if __name__ == "__main__":
    main()
