#!/usr/bin/env python3
"""Phase 2: validate top-N survivors from vec_backlog on FULL NPZ dataset.

Reads survivors table, picks top-N by composite, then for each combo
re-runs the mask + forward-return scoring on ALL symbols with sufficient
history (--min-bars), writing validated results to a separate table.

This separates "looks good on 15 samples" from "actually generalizes to all".
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import (
    build_crypto_conditions,
    build_tradier_conditions,
    fwd_returns,
    score,
)
from vec_validate import load_full


def db_init_p2(db_path):
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS validated (
        config_hash TEXT PRIMARY KEY, combo TEXT, side TEXT, horizon INT,
        sample_sharpe REAL, full_sharpe REAL, full_wr REAL, full_mean REAL,
        full_std REAL, full_pf REAL, full_n_trades INT, n_symbols INT,
        full_bars INT, created_at REAL)""")
    con.commit()
    return con


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--db", default="")
    ap.add_argument("--top-n", type=int, default=1000)
    ap.add_argument("--bars", type=int, default=80000)
    ap.add_argument("--min-bars", type=int, default=15000)
    ap.add_argument("--min-trades", type=int, default=500)
    ap.add_argument("--npz-dir", default="")
    args = ap.parse_args()

    if not args.db:
        args.db = str(Path(__file__).parent / "data" / "sweep_results" / f"vec_backlog_{args.mode}.sqlite")
    if not Path(args.db).exists():
        print(f"DB not found: {args.db}", flush=True); return

    con = db_init_p2(args.db)

    rows = list(con.execute(
        "SELECT config_hash, combo, side, horizon, sharpe FROM survivors "
        "ORDER BY composite DESC LIMIT ?", (args.top_n,)
    ))
    print(f"[vec_phase2] Loaded {len(rows)} top survivors from {args.db}", flush=True)
    if not rows:
        return

    done = set(r[0] for r in con.execute("SELECT config_hash FROM validated"))
    rows = [r for r in rows if r[0] not in done]
    print(f"[vec_phase2] {len(rows)} still to validate (resume skipped {len(done)})", flush=True)
    if not rows:
        return

    if not args.npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            sub = "backtest_v4_tradier" if args.mode == "tradier" else "backtest_v8"
            d = Path(base) / sub / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                args.npz_dir = str(d)
                break
    print(f"[vec_phase2] NPZ dir: {args.npz_dir}", flush=True)

    t0 = time.time()
    loaded, n_bars = load_full(args.mode, args.npz_dir, args.bars, args.min_bars)
    print(f"[vec_phase2] Loaded {len(loaded)} symbols × {n_bars} bars in {time.time()-t0:.1f}s", flush=True)

    if args.mode == "crypto":
        C, close = build_crypto_conditions(loaded)
    else:
        C, close = build_tradier_conditions(loaded)
    fwd = fwd_returns(close, sorted(set(r[3] for r in rows)))

    batch = []
    BATCH = 200
    for i, (ch, combo, side, horizon, sample_sharpe) in enumerate(rows):
        if horizon not in fwd:
            continue
        keys = [f"{side}_{k}" for k in combo.split("+")]
        missing = [k for k in keys if k not in C]
        if missing:
            continue
        mask = np.ones_like(C[keys[0]])
        for k in keys:
            mask &= C[k]
        m = score(mask, fwd[horizon], args.min_trades)
        if m is None:
            continue
        batch.append((ch, combo, side, horizon, sample_sharpe,
                      m["sharpe"], m["wr"], m["mean"], m["std"], m["pf"], m["n"],
                      len(loaded), n_bars, time.time()))
        if len(batch) >= BATCH:
            con.executemany("INSERT OR REPLACE INTO validated VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            con.commit()
            print(f"[vec_phase2] {i+1}/{len(rows)} validated — last: combo={combo[:40]} full_sharpe={m['sharpe']:.3f} wr={m['wr']:.1f} n={m['n']}", flush=True)
            batch = []
    if batch:
        con.executemany("INSERT OR REPLACE INTO validated VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        con.commit()

    # Report top 30 by full_sharpe
    print(f"\n=== TOP 30 after full-data validation (mode={args.mode}, {len(loaded)} sym, {n_bars} bars) ===", flush=True)
    print(f"{'Horizon':>7} {'Sharpe15':>8} {'SharpeAll':>9} {'WR%':>6} {'Mean%':>7} {'N':>8} {'PF':>7}  Combo", flush=True)
    for r in con.execute(
        "SELECT horizon, sample_sharpe, full_sharpe, full_wr, full_mean, full_n_trades, full_pf, combo "
        "FROM validated ORDER BY full_sharpe DESC LIMIT 30"
    ):
        h, ss, fs, wr, mean, n, pf, combo = r
        print(f"{h:>7d} {ss:>8.3f} {fs:>9.3f} {wr:>6.1f} {mean*100:>7.3f} {n:>8d} {pf:>7.2f}  {combo}", flush=True)


if __name__ == "__main__":
    main()
