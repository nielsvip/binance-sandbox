#!/usr/bin/env python3
"""klines_tail_stage — the Mac sends S1 only the RECENT bars of each kline file, never a second full copy (USER 2026-10-27:
"keep only one copy of klines (the big one that the macbook klines append to)").

S1's klines_cache_macbook/ used to be a 6.8 GB mirror of the Mac's klines_cache/ (rsync of whole files every 60 s), a second
copy of history the big store already holds. This writes, for every Mac kline file changed since its staged copy, a TAIL
file (last N bars, N per timeframe below) into ~/klines_tail_stage/ with the same relative path (tradier/ included); the push
loop rsyncs that directory to S1's klines_cache_macbook/ and klines_append.py appends it as before (timestamp-deduped, so an
overlapping tail is free). The tail is cut by seeking from the END of the file (no full JSON parse: the Mac is the live box),
then the small tail is validated with json.loads; a file whose tail does not parse is skipped and logged, never staged.
Tail lengths cover >= 1 day of a Mac->S1 outage on every timeframe; longer gaps are filled by S1's own gateway/canonical
sources and the weekly full append.
"""
import json
import os
import sys
import time
from pathlib import Path

SRC = Path(os.environ.get("KTS_SRC", "/Users/niels/Documents/binance/klines_cache"))
DST = Path(os.environ.get("KTS_DST", str(Path.home() / "klines_tail_stage")))
TAIL_BARS = {"1m": 1440, "3m": 480, "15m": 672, "1h": 336, "4h": 200, "D": 200, "W": 100, "M": 60}
BYTES_PER_BAR = 260  # generous upper bound for one indented bar object; the seek window is TAIL_BARS * this


def tail_of(p: Path, n: int):
    size = p.stat().st_size
    with p.open("rb") as fh:
        start = max(0, size - n * BYTES_PER_BAR)
        fh.seek(start)
        chunk = fh.read()
    if start == 0:
        bars = json.loads(chunk)
        return bars[-n:] if isinstance(bars, list) else None
    i = chunk.find(b"\n  {")  # indented files (json.dump indent=2)
    if i >= 0:
        bars = json.loads(b"[" + chunk[i:])
    else:
        hits = [(j, len(sep)) for sep in (b"},{", b"}, {") for j in [chunk.find(sep)] if j >= 0]  # one-line files, with or without a space
        if not hits:
            return None
        j, w = min(hits)
        bars = json.loads(b"[" + chunk[j + w - 1:])
    return bars[-n:] if isinstance(bars, list) else None


def main():
    staged = skipped = bad = 0
    for p in list(SRC.glob("*_*.json")) + list(SRC.glob("tradier/*_*.json")):
        tf = p.name[:-5].rsplit("_", 1)[-1]
        n = TAIL_BARS.get(tf)
        if n is None:
            continue
        rel = p.relative_to(SRC)
        out = DST / rel
        try:
            if out.exists() and out.stat().st_mtime >= p.stat().st_mtime:
                skipped += 1
                continue
            bars = tail_of(p, n)
            if not bars or not all(isinstance(b, dict) and b.get("timestamp") for b in bars):
                bad += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(json.dumps(bars, separators=(",", ":")))
            os.replace(tmp, out)
            staged += 1
        except Exception as e:
            bad += 1
            print(f"[klines_tail_stage] skip {rel}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"[klines_tail_stage] {time.strftime('%Y-%m-%d %H:%M:%S')} staged={staged} unchanged={skipped} bad={bad}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
