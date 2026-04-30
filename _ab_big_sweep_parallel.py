#!/usr/bin/env python3
"""Parallel big sweep — 8 workers, fork-based NPZ sharing.
Dedups against /tmp/big_sweep_done.json (configs completed by the single-process run).
"""
import itertools
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

# CRITICAL: fork lets workers inherit loaded NPZ from parent (copy-on-write) — huge speedup.
if sys.platform == "darwin":
    mp.set_start_method("fork", force=True)

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

SYMS_48 = json.load(open(BASE / "backtest_48_symbols.json"))
START = "2022-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")
WORKERS = 8

GRID = {
    "ENTRY_SCORE_THRESHOLD":        [0.0, 10.0, 15.0, 20.0, 25.0],
    "HTF_MIN_ALIGNED":              [1, 2, 3],
    "CT_WT_VELOCITY_1H_MIN":        [1.0, 1.5, 2.0, 2.5],
    "REENTRY_RALLY_K15M_MAX":       [50.0, 60.0, 70.0],
    "RANK_CONVICTION_MIN":          [1, 2, 3],
    "WINNER_PROTECT_GAIN_PCT":      [1.5, 2.0, 2.5],
}

# Module-level stores will be inherited by fork'd workers (zero-copy on Linux, COW on macOS)
_STORES = None


def _worker_init():
    # Each worker imports quick engine once; stores already in memory from parent
    pass


def _run_one(overrides):
    from v8_quick_engine import QuickConfig, simulate
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    r = simulate(_STORES, cfg, 10000.0)
    r["cfg"] = overrides
    return r


def cfg_key(cfg):
    return tuple(sorted(cfg.items()))


def main():
    global _STORES
    from v8_quick_engine import load_npz

    print(f"Loading 48 NPZ (parent process) …")
    t0 = time.time()
    _STORES = load_npz("crypto", SYMS_48, START, NPZ_DIR)
    print(f"  {len(_STORES)} symbols in {time.time()-t0:.1f}s\n")

    # Build full combos
    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    combos = [dict(zip(keys, c)) for c in itertools.product(*values)]
    total = len(combos)
    print(f"Total configs: {total}")

    # Load prior done (from single-process run)
    done = {}
    seed_path = Path("/tmp/big_sweep_done.json")
    if seed_path.exists():
        for d in json.load(open(seed_path)):
            k = cfg_key(d["cfg"])
            done[k] = {"sharpe": d["sharpe"], "trades": d["trades"],
                       "wr": d["wr"], "avg_pnl_pct": d["avg"],
                       "cfg": d["cfg"], "pnl": 0.0, "wins": 0, "losses": 0}
        print(f"Seeded {len(done)} configs from prior run")

    todo = [c for c in combos if cfg_key(c) not in done]
    print(f"To run: {len(todo)} with {WORKERS} workers\n")

    results = list(done.values())
    best = {"sharpe": -999, "cfg": None}
    for r in results:
        if r["sharpe"] > best["sharpe"] and r.get("trades", 0) >= 10:
            best = {"sharpe": r["sharpe"], "cfg": r["cfg"],
                    "trades": r["trades"], "wr": r.get("wr", 0),
                    "avg": r.get("avg_pnl_pct", 0)}

    t_start = time.time()
    with mp.Pool(WORKERS) as pool:
        completed = 0
        for r in pool.imap_unordered(_run_one, todo, chunksize=4):
            results.append(r)
            completed += 1
            flag = ""
            if r["sharpe"] > best["sharpe"] and r["trades"] >= 10:
                best = {"sharpe": r["sharpe"], "cfg": r["cfg"],
                        "trades": r["trades"], "wr": r.get("wr", 0),
                        "avg": r.get("avg_pnl_pct", 0)}
                flag = " ★"
            if completed % 25 == 0 or flag or completed == len(todo):
                elapsed = time.time() - t_start
                rate = completed / elapsed if elapsed > 0 else 0
                eta_min = (len(todo) - completed) / rate / 60 if rate > 0 else 0
                cfg_s = " ".join(f"{k[:4]}={v}" for k, v in sorted(r["cfg"].items()))
                print(f"[{completed + len(done)}/{total}] {cfg_s}  sh={r['sharpe']:+.4f}  tr={r['trades']}  WR={r['wr']:.1f}%  best={best['sharpe']:+.4f}  ETA={eta_min:.1f}m{flag}", flush=True)

    # Final ranking
    ranked = sorted(results, key=lambda r: -r["sharpe"])
    print("\n" + "=" * 140)
    print(f"BEST: sharpe={best['sharpe']:+.4f}  trades={best.get('trades')}  WR={best.get('wr')}%  avg={best.get('avg')}%")
    print(f"       cfg: {best['cfg']}\n")
    print("TOP 25 by Sharpe (trades >= 10):")
    cnt = 0
    for r in ranked:
        if r.get("trades", 0) < 10: continue
        print(f"  sharpe={r['sharpe']:+.4f}  trades={r['trades']:>5d}  WR={r['wr']:4.1f}%  avg={r['avg_pnl_pct']:+.3f}%  {r['cfg']}")
        cnt += 1
        if cnt >= 25: break

    out = BASE / "data" / "sweep_results" / f"big_sweep_parallel_48sym_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results, "best": best, "grid": GRID}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
