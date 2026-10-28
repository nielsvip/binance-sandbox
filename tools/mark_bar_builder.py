#!/usr/bin/env python3
"""Stock 15m OHLCV from polled Tradier quote marks (mid = (bid+ask)/2).

Bar label = bar start (epoch s, :00/:15/:30/:45 ET-aligned in UTC). Bar [start, start+900) is
complete at start+900 (:15:00). Samples are the polled quote marks; the bar's last minute
(start+840..start+899) is the mark-price minute, so the bar is ready as soon as the last poll
at or after :14:59 lands. Shadow output only: data/mark_bars/tradier/<SYM>.jsonl.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BAR = 900
MINUTE = 60
BATCH = 100


def load_key(base):
    sys.path.insert(0, str(base))
    import config_tradier as ct

    return ct.TradierConfig().ACCOUNTS["tra"]["key"]


def fetch_quotes(key, symbols):
    out = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i : i + BATCH]
        req = urllib.request.Request(
            "https://api.tradier.com/v1/markets/quotes?" + urllib.parse.urlencode({"symbols": ",".join(chunk)}),
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            doc = json.loads(r.read())
        rows = (doc.get("quotes") or {}).get("quote") or []
        if isinstance(rows, dict):
            rows = [rows]
        for q in rows:
            bid, ask = q.get("bid"), q.get("ask")
            if bid is None or ask is None or not (float(bid) > 0 and float(ask) > 0):
                continue
            out[q["symbol"]] = {
                "mid": (float(bid) + float(ask)) / 2.0,
                "last": q.get("last"),
                "vol": q.get("volume"),
                "quote_ms": q.get("trade_date"),
            }
    return out


def build_bar(samples, start):
    """samples: list of (t_epoch, mid, vol) for one symbol. Returns the OHLCV bar or None."""
    end = start + BAR
    inbar = sorted(s for s in samples if start <= s[0] < end)
    if not inbar:
        return None
    last_minute = [s for s in inbar if s[0] >= end - MINUTE]
    if not last_minute:
        return {"start": start, "complete": False, "reason": "no mark in :14 minute"}
    mids = [s[1] for s in inbar]
    vols = [s[2] for s in inbar if s[2] is not None]
    vol = (max(vols) - min(vols)) if len(vols) >= 2 else None
    return {
        "start": start,
        "open": inbar[0][1],
        "high": max(mids),
        "low": min(mids),
        "close": inbar[-1][1],
        "volume_day_delta": vol,
        "samples": len(inbar),
        "first_sample_t": inbar[0][0],
        "last_sample_t": inbar[-1][0],
        "ready_at": inbar[-1][0],
        "complete": True,
    }


def utc_now():
    return time.time()


def last_closed_bar_start(now):
    return int(now // BAR) * BAR - BAR


def run(base, symbols, out_dir, interval, once):
    key = load_key(base)
    hist = {s: [] for s in symbols}
    out_dir.mkdir(parents=True, exist_ok=True)
    done = set()
    while True:
        t = utc_now()
        try:
            quotes = fetch_quotes(key, symbols)
        except Exception as exc:
            print(json.dumps({"event": "poll_error", "error": f"{type(exc).__name__}: {str(exc)[:160]}", "t": t}), flush=True)
            quotes = {}
        for s, q in quotes.items():
            hist[s].append((t, q["mid"], float(q["vol"]) if q.get("vol") is not None else None))
        # emit bars whose end has passed and whose last minute has a sample
        start = last_closed_bar_start(t)
        if start not in done and t >= start + BAR:
            for s in symbols:
                bar = build_bar(hist[s], start)
                if bar is None:
                    continue
                bar["symbol"] = s
                with (out_dir / f"{s}.jsonl").open("a") as fh:
                    fh.write(json.dumps(bar, separators=(",", ":")) + "\n")
            done.add(start)
            print(json.dumps({"event": "bar", "start": start, "symbols": len(quotes), "t": t}), flush=True)
        # keep only the last two bars of samples in memory
        cutoff = t - 2 * BAR
        for s in symbols:
            hist[s] = [x for x in hist[s] if x[0] >= cutoff]
        if once:
            return 0
        time.sleep(max(0.0, interval - (utc_now() - t)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    base = Path(a.base).expanduser()
    syms = [s for s in a.symbols.split(",") if s] or sorted(load_symbols(base))
    out = Path(a.out) if a.out else base / "data" / "mark_bars" / "tradier"
    return run(base, syms, out, a.interval, a.once)


def load_symbols(base):
    sys.path.insert(0, str(base))
    import npz_tail_service as T

    uni = T.load_universe(str(base))
    return {s.rsplit("_", 1)[0] for s in T.owned_symbols(uni, "stocks")}


if __name__ == "__main__":
    raise SystemExit(main())
