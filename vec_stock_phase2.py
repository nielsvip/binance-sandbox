#!/usr/bin/env python3
"""Phase 2 validation: take top-N from vec_stock_mega pick3 DB, re-run on ALL available stock NPZ.

Reports full-dataset Sharpe (per-symbol avg) vs sample Sharpe (12-sym), showing
generalization / overfit. Also checks if any config clears Sharpe >= elite-floor
on full data.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
for p in (str(BASE), "/home/niels/binance-sandbox", "/Users/niels/Documents/binance"):
    if p not in sys.path:
        sys.path.insert(0, p)

from vec_stock_mega import (
    build_conditions, fwd_returns, score_per_symbol, detect_npz_dir,
    MIN_TRADES_PER_SYM, MIN_SYMS_INCLUDED,
)


def load_all_stocks(n_bars, min_bars_per_sym):
    npz_dir = detect_npz_dir()
    loaded = {}
    for p in sorted(npz_dir.glob("*.npz")):
        sym = p.stem
        try:
            z = dict(np.load(str(p), allow_pickle=True))
        except Exception:
            continue
        if len(z.get("close", [])) < min_bars_per_sym:
            continue
        loaded[sym] = z
    # Use min-length across qualifying symbols
    min_len = min(len(z["close"]) for z in loaded.values()) if loaded else 0
    use = min(n_bars, min_len)
    for s in list(loaded.keys()):
        z = loaded[s]
        for k in list(z.keys()):
            a = np.asarray(z[k])
            if a.ndim == 1 and len(a) >= use:
                z[k] = a[-use:]
        loaded[s] = z
    return loaded, use, npz_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-db", required=True)
    ap.add_argument("--top-n", type=int, default=100)
    ap.add_argument("--bars", type=int, default=22000, help="~1yr 5m stock bars")
    ap.add_argument("--min-bars-per-sym", type=int, default=20000)
    ap.add_argument("--min-syms-full", type=int, default=30, help="lower threshold than 12-sym sample; many small-cap will fail indicators")
    ap.add_argument("--out-table", default="validated_full")
    args = ap.parse_args()

    con = sqlite3.connect(args.src_db)
    con.execute(f"""CREATE TABLE IF NOT EXISTS {args.out_table} (
        rowid INTEGER PRIMARY KEY, side TEXT, combo TEXT, horizon INT,
        sample_sharpe REAL, full_sharpe_avg REAL, full_sharpe_pool REAL,
        full_sharpe_min REAL, full_sharpe_max REAL, full_syms INT,
        full_trades INT, full_wr REAL, full_mean REAL, full_dd_avg REAL,
        dropoff_pct REAL, created_at REAL)""")
    con.commit()

    rows = con.execute(
        "SELECT rowid, side, combo, horizon, sharpe_avg FROM results ORDER BY sharpe_avg DESC LIMIT ?",
        (args.top_n,)
    ).fetchall()
    if not rows:
        print("No rows to validate"); return
    print(f"Loading full NPZ... (this is a one-time cost)")
    t0 = time.time()
    loaded, n_bars, npz_dir = load_all_stocks(args.bars, args.min_bars_per_sym)
    print(f"[{time.time()-t0:.1f}s] Loaded {len(loaded)} syms × {n_bars} bars from {npz_dir}")
    syms = list(loaded.keys())

    # Need a scoring version that allows lower MIN_SYMS
    from vec_stock_mega import score_per_symbol as _score
    import vec_stock_mega as vsm

    C, close = build_conditions(loaded, syms)
    print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions on full dataset")
    fwd = fwd_returns(close, [8, 16, 32, 64, 128, 256])

    # Lower the thresholds for full-dataset validation
    vsm.MIN_SYMS_INCLUDED = args.min_syms_full
    vsm.REQUIRE_POSITIVE_MIN_SYM = False
    vsm.MIN_POOL_SHARPE = -10.0  # disable filter; we want to see the number regardless

    validated = 0
    print(f"\n[{time.time()-t0:.1f}s] Validating top {len(rows)} configs on {len(syms)} symbols...\n")
    print(f"{'Rank':>4} {'Side':<4} {'SampSh':>7} {'FullSh':>7} {'Pool':>6} {'Min':>6} {'Max':>6} {'Syms':>4} {'N':>7} {'WR%':>5} {'Drop%':>6} {'h':>4}  Combo")

    for rank, (rowid, side, combo, horizon, sample_sharpe) in enumerate(rows, 1):
        # Reconstruct condition names from combo string
        cond_names = [f"{side}_{p}" for p in combo.split("+")]
        if not all(n in C for n in cond_names):
            continue
        mask = C[cond_names[0]].copy()
        for n in cond_names[1:]:
            mask &= C[n]
        if not mask.any():
            continue
        ret = fwd[horizon]
        m = _score(mask, ret, side)
        if m is None:
            # fall back: compute without filters
            continue
        dropoff = (sample_sharpe - m["sharpe_avg"]) / max(abs(sample_sharpe), 1e-6) * 100
        print(f"{rank:>4} {side:<4} {sample_sharpe:>7.3f} {m['sharpe_avg']:>7.3f} {m['sharpe_pool']:>6.3f} {m['sharpe_min']:>6.3f} {m['sharpe_max']:>6.3f} {m['syms_included']:>4} {m['n_trades_total']:>7} {m['wr_avg']:>5.1f} {dropoff:>5.1f}% {horizon:>4}  {combo[:70]}")
        con.execute(f"INSERT OR REPLACE INTO {args.out_table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rowid, side, combo, horizon, sample_sharpe,
             m["sharpe_avg"], m["sharpe_pool"], m["sharpe_min"], m["sharpe_max"],
             m["syms_included"], m["n_trades_total"], m["wr_avg"], m["mean_ret_avg"],
             m["dd_pct_avg"], dropoff, time.time()))
        validated += 1
    con.commit()
    print(f"\n[{time.time()-t0:.1f}s] Validated {validated} / {len(rows)} on full dataset")

    # Summary: median dropoff + ceiling on full
    med_drop = con.execute(f"SELECT AVG(dropoff_pct) FROM {args.out_table}").fetchone()[0]
    top_full = con.execute(f"SELECT side, combo, horizon, sample_sharpe, full_sharpe_avg, full_sharpe_pool, full_syms FROM {args.out_table} ORDER BY full_sharpe_avg DESC LIMIT 10").fetchall()
    print(f"\n=== SUMMARY ===")
    print(f"Median dropoff: {med_drop:.1f}%  (how much Sharpe fell from 12-sym to full)")
    print(f"\nTOP 10 by FULL sharpe_avg:")
    for r in top_full:
        print(f"  full={r[4]:.3f} samp={r[3]:.3f} pool={r[5]:.3f} syms={r[6]} {r[0]} h={r[2]:<3} {r[1][:80]}")


if __name__ == "__main__":
    main()
