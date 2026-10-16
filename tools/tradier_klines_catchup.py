#!/usr/bin/env python3
"""tradier_klines_catchup — fill TODAY's session for the stock symbols the main pass has not reached yet.

USER 2026-10-16 (NPZ freshness): ~60 stock NPZs sat ~95 bars behind the clock, and the cause was upstream — their
klines ended at 2026-10-15T20:00Z while WMT/AAPL had 2026-10-16T20:00Z. MEASURED why:
  * A DIRECT Tradier timesales call for the lagging symbols (T, USO) returns 27 bars for TODAY in 1.0 s. The
    vendor is healthy and has the data.
  * tradier_klines.py's own log shows `15min fetch failed/timeout:` with an EMPTY message (asyncio.wait_for's
    TimeoutError stringifies to nothing) and 1-3 MINUTES per symbol: during RTH it runs every 60 s with
    sem=64 over ~220 symbols x 6 timeframes, and after the close it runs one 7-day repair pass at sem=16. The
    timeouts are self-inflicted concurrency, not a vendor outage, and the alphabetical crawl simply never
    reached T/STZ/USO/TTD/LDOS/NKE today.
  * Massive.com cannot cover it: its plan embargoes the current day (HTTP 403 for today, 60 bars for yesterday),
    so it backfills history only.

This does NOT modify tradier_klines.py: it imports that module and calls its own process_symbol() at low
concurrency for the symbols that are actually behind, so the bars are written by the proven path (same
Time&Sales source, same export_atomic, same never-shrink guard).

usage:
  python3 tools/tradier_klines_catchup.py                 # report which symbols are behind
  python3 tools/tradier_klines_catchup.py --apply [--max 80] [--conc 4]
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def log(msg):
    print(f"[tradier_catchup {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def last_bar(p):
    """last bar timestamp of a kline json by tail-read (never a full parse)."""
    try:
        sz = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, sz - 4096))
            t = f.read().decode("utf-8", "ignore")
    except OSError:
        return None
    m = re.findall(r'"timestamp":\s*"([0-9T:\-\.]+)Z?"', t)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m[-1][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def last_rth_close(now=None):
    """most recent weekday 20:00Z (16:00 ET) that has passed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    d = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now < d:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def behind(cache_dir, symbols, cutoff):
    out = []
    for s in symbols:
        p = cache_dir / f"{s}_15m.json"
        lb = last_bar(p) if p.exists() else None
        if lb is None or lb < cutoff:
            out.append((s, lb))
    return out


async def run(symbols, conc):
    import tradier_klines as tk
    await tk.api_client.connect()
    sem = asyncio.Semaphore(conc)
    done = {}

    async def one(s):
        async with sem:
            try:
                done[s] = bool(await tk.process_symbol(s, repair=False))
            except Exception as e:
                done[s] = f"{type(e).__name__}: {e}"[:120]
    try:
        await asyncio.gather(*(one(s) for s in symbols))
    finally:
        await tk.api_client.close()
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--conc", type=int, default=4, help="low on purpose: sem 64 is what produced the 20 s timeouts")
    ap.add_argument("--symbols")
    a = ap.parse_args()
    import tradier_klines as tk
    cache = Path(tk.config.KLINES_CACHE_DIR)  # already .../klines_cache/tradier
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        syms = [str(s).upper() for s in json.loads(Path(tk.config.SYMBOLS_FILE).read_text())]
    cutoff = last_rth_close()
    late = behind(cache, syms, cutoff)
    log(f"{len(late)} of {len(syms)} stock symbols are missing bars up to {cutoff.isoformat()} (cache {cache})")
    log("worst: " + ", ".join(f"{s}@{(lb.isoformat() if lb else 'NONE')}" for s, lb in late[:8]))
    if not a.apply or not late:
        return 1 if late else 0
    todo = [s for s, _ in late[: max(1, a.max)]]
    t0 = time.time()
    res = asyncio.run(run(todo, a.conc))
    ok = [s for s, v in res.items() if v is True]
    bad = {s: v for s, v in res.items() if v is not True}
    still = behind(cache, todo, cutoff)
    log(f"ran {len(todo)} symbols at conc={a.conc} in {time.time()-t0:.0f}s — all-6-timeframes ok {len(ok)}, partial/err {len(bad)}, still behind {len(still)}")
    if bad:
        log("errors: " + json.dumps({k: str(v)[:60] for k, v in list(bad.items())[:6]}))
    if still:
        log("still behind: " + ", ".join(s for s, _ in still[:10]))
    return 1 if still else 0


if __name__ == "__main__":
    sys.exit(main())
