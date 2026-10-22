#!/usr/bin/env python3
"""pure_board_step_cache_warm — one-time (and after code changes) filler for the
incremental step-value cache (USER 2026-10-22).

The lane's worker processes are daemonic, so they cannot fork inner step pools:
a cold symbol costs ~1,600 s CPU serially and always trips the 600 s watchdog.
This warmer runs OUTSIDE the lane (plain process, NPZ_STOCK_STEP_WORKERS=8,
~205 s/symbol) and fills data/pure_board/step_cache/{SYM}.pkl once; every lane
pass after that only evaluates NEW steps (seconds). Resumable (finished symbols
are full-hit and skipped), idempotent, read-only vs live (no orders, no state
writes but the cache dir). Run: nohup python3 tools/pure_board_step_cache_warm.py
[--syms AAPL,MSFT] [--limit N] >> /tmp/step_cache_warm.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("NPZ_STOCK_STEP_WORKERS", "8")
os.environ.setdefault("PURE_BOARD_STEP_CACHE_DIR", str(ROOT / "data" / "pure_board" / "step_cache"))
sys.path.insert(0, str(ROOT))


def stocks_universe(limit=0):
    syms = set()
    for name in ("symbols_trb_long.json", "symbols_trb_short.json", "symbols_tra_long.json",
                 "symbols_tra_short.json", "symbols_trc_long.json", "symbols_trc_short.json"):
        try:
            syms.update(json.loads((ROOT / name).read_text()))
        except Exception:
            continue
    out = sorted(syms)
    return out[:limit] if limit else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    syms = [s.strip().upper() for s in a.syms.split(",") if s.strip()] or stocks_universe(a.limit)
    import backtest_v8_precompute as pc

    def _alarm(_s, _f):
        raise TimeoutError("warmer per-symbol budget 720s")
    ok, fail, t_all = 0, [], time.time()
    for i, sym in enumerate(syms, 1):
        t0 = time.time()
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(720)
        try:
            raw = pc.compute_symbol(sym, "tradier", return_arrays=True)
            n = len((raw or {}).get("timestamps", []) or [])
            sz = (Path(os.environ["PURE_BOARD_STEP_CACHE_DIR"]) / f"{sym}.pkl").stat().st_size / 1e6
            print(f"[{i}/{len(syms)}] {sym} OK bars={n} cache={sz:.1f}MB {time.time()-t0:.0f}s", flush=True)
            ok += 1
        except Exception as exc:
            print(f"[{i}/{len(syms)}] {sym} FAIL {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            fail.append(sym)
        finally:
            signal.alarm(0)
    print(f"DONE {ok} ok, {len(fail)} failed {fail[:10]} in {(time.time()-t_all)/60:.0f}min", flush=True)


if __name__ == "__main__":
    main()
