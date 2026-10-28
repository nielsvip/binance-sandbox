#!/usr/bin/env python3
"""Shadow daemon: at every 15m close, build crypto bars (native 1m :00-:13 + mark 1m :14) for all symbols.

Writes data/mark_bars/binance/<SYM>.jsonl and logs per-bar latency (close -> last symbol built).
"""
import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mark_kline_bar_crypto as M  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--delay", type=float, default=2.0)
    a = ap.parse_args()
    syms = [s for s in Path(a.symbols_file).read_text().strip().split(",") if s]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    while True:
        now = time.time()
        start = int(now // M.BAR) * M.BAR  # bar that just closed (its end is `start` + ...)
        nxt = start + M.BAR + a.delay
        if now < nxt:
            time.sleep(nxt - now)
        bar_start = int(time.time() // M.BAR) * M.BAR - M.BAR
        t0 = time.time()
        results = []
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(M.build, s, bar_start): s for s in syms}
            for f in cf.as_completed(futs):
                try:
                    b = f.result()
                except Exception as exc:
                    b = {"symbol": futs[f], "complete": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                results.append(b)
                if b.get("complete"):
                    with (out / f"{b['symbol']}.jsonl").open("a") as fh:
                        fh.write(json.dumps(b, separators=(",", ":")) + "\n")
        done = sum(1 for r in results if r.get("complete"))
        print(json.dumps({"event": "bar", "start": bar_start, "complete": done, "of": len(syms),
                          "close_to_done_s": round(time.time() - (bar_start + M.BAR), 1),
                          "build_s": round(time.time() - t0, 1)}), flush=True)


if __name__ == "__main__":
    main()
