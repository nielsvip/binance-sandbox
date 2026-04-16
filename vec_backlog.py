#!/usr/bin/env python3
"""Billion-param vectorized backlog runner.

Phase 1: enumerate (condition_combo × horizon × side × pick_size) space,
score each on a 15-symbol sample in numpy, store results.
  - Sharpe < floor → record as dead, skip
  - Sharpe >= floor → kept in survivors table, ranked by composite
  - Survivors auto-trimmed to top_n to keep DB bounded

Resume-aware: SQLite holds done_hashes so restarts pick up where they left off.
Resource-aware: monitors CPU/mem, sleeps when overloaded (target: CPU<95, mem<90).
Sharded across workers: --worker-id N --total-workers M filters configs by hash%M.

Phase 2 (separate vec_phase2.py): takes top 1000 from survivors and validates on
all symbols × 4 years to find the ones that really generalize.
"""
import argparse
import hashlib
import itertools
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import (
    build_crypto_conditions,
    build_tradier_conditions,
    fwd_returns,
    load_slice,
    score,
)

CRYPTO_15 = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,UNIUSDT,ATOMUSDT,BCHUSDT,ETCUSDT,FILUSDT".split(",")
TRADIER_15 = "AAPL,MSFT,NVDA,AMZN,JPM,XOM,META,TSLA,SPY,QQQ,XLF,XLE,GLD,USO,IWM".split(",")

HORIZONS = [4, 8, 16, 32, 64, 128, 256]
PICK_SIZES = [3, 4, 5, 6]


def db_init(db_path):
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""CREATE TABLE IF NOT EXISTS survivors (
        config_hash TEXT PRIMARY KEY, combo TEXT, side TEXT, horizon INT,
        sharpe REAL, wr REAL, mean_ret REAL, std REAL, pf REAL, n_trades INT,
        composite REAL, created_at REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_composite ON survivors(composite DESC)")
    con.execute("""CREATE TABLE IF NOT EXISTS dead (
        config_hash TEXT PRIMARY KEY, sharpe REAL, created_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT)""")
    con.commit()
    return con


def config_hash(side, combo, horizon):
    key = f"{side}|{'+'.join(sorted(combo))}|{horizon}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def composite_score(sharpe, wr, mean_ret):
    return float(sharpe) + (float(wr) / 100.0) * 0.5 + float(mean_ret) * 10.0


def check_resources(cpu_cap, mem_cap):
    if not HAVE_PSUTIL:
        return True, 0, 0
    cpu = psutil.cpu_percent(interval=0.0)
    mem = psutil.virtual_memory().percent
    return (cpu < cpu_cap and mem < mem_cap), cpu, mem


def run(args):
    mode = args.mode
    symbols = CRYPTO_15 if mode == "crypto" else TRADIER_15
    print(f"[vec_backlog] mode={mode} symbols={len(symbols)} bars={args.bars} worker={args.worker_id}/{args.total_workers}", flush=True)

    t0 = time.time()
    loaded, n_bars, npz_dir = load_slice(mode, symbols, args.bars)
    print(f"[vec_backlog] Loaded {len(loaded)} NPZ from {npz_dir} ({n_bars} bars each) in {time.time()-t0:.1f}s", flush=True)
    if mode == "crypto":
        C, close = build_crypto_conditions(loaded)
    else:
        C, close = build_tradier_conditions(loaded)
    fwd = fwd_returns(close, HORIZONS)
    long_keys = sorted([k for k in C if k.startswith("L_")])
    short_keys = sorted([k for k in C if k.startswith("S_")])
    print(f"[vec_backlog] Built {len(C)} conditions (L={len(long_keys)}, S={len(short_keys)})", flush=True)

    db = Path(args.db) if args.db else Path(__file__).parent / "data" / "sweep_results" / f"vec_backlog_{mode}.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = db_init(db)
    print(f"[vec_backlog] DB: {db}", flush=True)

    done_hashes = set()
    for row in con.execute("SELECT config_hash FROM survivors"):
        done_hashes.add(row[0])
    for row in con.execute("SELECT config_hash FROM dead"):
        done_hashes.add(row[0])
    print(f"[vec_backlog] Resume: {len(done_hashes)} configs already processed", flush=True)

    sh_floor = float(args.sharpe_floor)
    top_n = int(args.top_n)
    worker_id = int(args.worker_id)
    total_workers = int(args.total_workers)

    n_processed = 0
    n_survivors = 0
    n_dead = 0
    n_skipped_other_worker = 0
    batch_survivors = []
    batch_dead = []
    BATCH = 2000

    total_estimated = sum(
        (len(long_keys) + len(short_keys)) * (len(HORIZONS))
        * _comb(len(long_keys) if True else len(long_keys), pick)
        for pick in PICK_SIZES
    )

    def _flush():
        nonlocal batch_survivors, batch_dead
        if batch_survivors:
            con.executemany(
                "INSERT OR REPLACE INTO survivors VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                batch_survivors,
            )
        if batch_dead:
            con.executemany(
                "INSERT OR IGNORE INTO dead (config_hash, sharpe, created_at) VALUES (?,?,?)",
                batch_dead,
            )
        con.execute(
            f"DELETE FROM survivors WHERE config_hash NOT IN "
            f"(SELECT config_hash FROM survivors ORDER BY composite DESC LIMIT {top_n})"
        )
        con.commit()
        batch_survivors = []
        batch_dead = []

    _interrupted = {"v": False}
    def _sigterm(_s, _f):
        _interrupted["v"] = True
        print("[vec_backlog] SIGTERM — flushing and exiting", flush=True)
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    last_print = time.time()

    for pick in PICK_SIZES:
        for side, keys in [("L", long_keys), ("S", short_keys)]:
            for combo in itertools.combinations(keys, pick):
                for h in HORIZONS:
                    if h not in fwd:
                        continue
                    ch = config_hash(side, combo, h)
                    # Shard by worker_id
                    if int(ch, 16) % total_workers != worker_id:
                        n_skipped_other_worker += 1
                        continue
                    if ch in done_hashes:
                        continue
                    # Resource check every ~200 iters
                    if n_processed % 200 == 0:
                        ok, cpu, mem = check_resources(args.cpu_cap, args.mem_cap)
                        while not ok:
                            time.sleep(0.5)
                            ok, cpu, mem = check_resources(args.cpu_cap, args.mem_cap)
                    # Mask build
                    mask_keys = [f"{side}_{k}" for k in combo]
                    try:
                        mask = np.ones_like(C[mask_keys[0]])
                        for k in mask_keys:
                            mask &= C[k]
                    except KeyError:
                        batch_dead.append((ch, -99.0, time.time()))
                        n_dead += 1
                        n_processed += 1
                        continue
                    m = score(mask, fwd[h], args.min_trades)
                    n_processed += 1
                    if m is None:
                        batch_dead.append((ch, -99.0, time.time()))
                        n_dead += 1
                    elif m["sharpe"] < sh_floor:
                        batch_dead.append((ch, m["sharpe"], time.time()))
                        n_dead += 1
                    else:
                        comp = composite_score(m["sharpe"], m["wr"], m["mean"])
                        batch_survivors.append(
                            (ch, "+".join(combo), side, h, m["sharpe"], m["wr"], m["mean"],
                             m["std"], m["pf"], m["n"], comp, time.time())
                        )
                        n_survivors += 1
                    done_hashes.add(ch)
                    if len(batch_survivors) + len(batch_dead) >= BATCH:
                        _flush()
                    if time.time() - last_print > 30.0:
                        elapsed = time.time() - t0
                        rate = n_processed / max(elapsed, 1)
                        _ok, cpu, mem = check_resources(args.cpu_cap, args.mem_cap)
                        print(
                            f"[vec_backlog] proc={n_processed:,} surv={n_survivors:,} dead={n_dead:,} "
                            f"rate={rate:.0f}/s cpu={cpu:.0f}% mem={mem:.0f}% pick={pick} side={side}",
                            flush=True,
                        )
                        last_print = time.time()
                    if _interrupted["v"]:
                        _flush()
                        print(f"[vec_backlog] INTERRUPTED: proc={n_processed} surv={n_survivors} dead={n_dead}", flush=True)
                        return

    _flush()
    print(f"[vec_backlog] COMPLETE: proc={n_processed:,} surv={n_survivors:,} dead={n_dead:,} skipped(other_worker)={n_skipped_other_worker:,}", flush=True)


def _comb(n, k):
    from math import comb
    return comb(n, k) if k <= n else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--bars", type=int, default=80000)
    ap.add_argument("--db", default="")
    ap.add_argument("--sharpe-floor", type=float, default=1.0)
    ap.add_argument("--top-n", type=int, default=5000)
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--worker-id", type=int, default=0)
    ap.add_argument("--total-workers", type=int, default=1)
    ap.add_argument("--cpu-cap", type=float, default=95.0)
    ap.add_argument("--mem-cap", type=float, default=90.0)
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
