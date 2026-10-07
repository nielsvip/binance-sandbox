#!/usr/bin/env python3
"""stock_klines_gapfill — fill the 5m/15m(/1h/4h) gaps of klines_cache/tradier for a symbol list (USER 2026-09-30 stock-NPZ refresh).

Root cause it fixes: tradier_klines.py (s1 loop, RTH only, Semaphore(64) over ~223 symbols) times out on most native 5m/15m fetches
(20 s guard), so those caches stop growing for later symbols (last bar 2026-09-24 / 09-18 / 08-26) while 1m keeps coming. This runs the SAME
process_symbol() (merge never overwrites volume>0 with provisional rows, atomic export) with low concurrency + retries, from each
cache's last bar. Run on the host that owns the live cache (s1). Usage: python tools/stock_klines_gapfill.py --symbols-file FILE [--conc 5]
"""
import argparse, asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tradier_klines as TK  # loads env + api client


def last_bar(sym, tf):
    try:
        rows = json.load(open(TK.config.KLINES_CACHE_DIR / f"{sym}_{tf}.json"))
        return str(rows[-1].get("timestamp"))[:16]
    except Exception:
        return "NA"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--conc", type=int, default=5)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()
    syms = [s.strip().upper() for s in open(a.symbols_file).read().split() if s.strip()]
    await TK.api_client.connect()
    sem = asyncio.Semaphore(a.conc)
    res = {}

    async def run(s):
        async with sem:
            before = last_bar(s, "15m")
            ok = False
            for k in range(a.retries):
                try:
                    ok = await TK.process_symbol(s)
                except Exception as e:
                    print(f"[gapfill] {s} attempt {k+1} error {e}", flush=True)
                after = last_bar(s, "15m")
                if after > before or after[:10] >= time.strftime("%Y-%m-%d", time.gmtime()):
                    break
                await asyncio.sleep(2 + 3 * k)
            res[s] = (before, last_bar(s, "15m"), last_bar(s, "5m"), ok)
            print(f"[gapfill] {s} 15m {before} -> {res[s][1]} 5m {res[s][2]} ok={ok}", flush=True)

    try:
        await asyncio.gather(*(run(s) for s in syms))
    finally:
        await TK.api_client.close()
    stale = [s for s, r in res.items() if r[1][:10] < time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 2))]
    print(f"[gapfill] done {len(res)} symbols; still >2d stale 15m: {len(stale)} {stale}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
