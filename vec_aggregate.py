#!/usr/bin/env python3
"""vec_aggregate.py — peek at partial streamed_pooled results.

Reads the streaming_returns table from a vec_sweep DB, aggregates per-config
across whatever symbols are currently present, and prints the top-N
pool_sharpe results. SAFE to run while a streaming sweep is still going.

Per CLAUDE.md no-lies: routes through metrics_guard.pool_sharpe(), enforces
sample-floor + cap rules, marks DIAGNOSTIC vs PUBLISHABLE based on n_syms.

Usage:
    python3 vec_aggregate.py [--db data/vec_sweep.db] [--top-n 20]
        [--remote s1-int] [--remote-db /home/niels/binance-sandbox/data/vec_sweep.db]
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Dict, List

import numpy as np

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import metrics_guard as mg


def fetch_remote(remote: str, remote_db: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "vec_sweep_remote.db"
    print(f"[aggregate] copying {remote}:{remote_db} → {tmp}")
    subprocess.run(["scp", "-o", "BatchMode=yes", f"{remote}:{remote_db}", str(tmp)],
                   check=True)
    return tmp


def aggregate(db_path: Path, top_n: int, mode_filter: str = ""):
    con = sqlite3.connect(str(db_path))
    syms_done = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM streaming_returns").fetchall()]
    n_syms = len(syms_done)
    print(f"[aggregate] streaming_returns has {n_syms} syms: "
          f"{syms_done[:8]}{'...' if n_syms>8 else ''}")
    if n_syms == 0:
        print("  (no streaming data yet)"); return
    # All unique config hashes
    hashes = [r[0] for r in con.execute(
        "SELECT DISTINCT config_hash FROM streaming_returns").fetchall()]
    print(f"[aggregate] {len(hashes)} unique configs to aggregate")

    floor_syms = mg.MIN_SYMS_CRYPTO
    n_published = 0
    results = []  # (pool, sym, trades, acc, dd, hash, label)

    for h in hashes:
        rows = con.execute(
            "SELECT symbol, returns_blob, n_trades, years FROM streaming_returns "
            "WHERE config_hash=?", (h,)).fetchall()
        all_rets: List[float] = []
        per_sym: Dict[str, List[float]] = {}
        years_list: List[float] = []
        for sym, blob, n_tr, yrs in rows:
            if not blob or n_tr == 0:
                continue
            rets = np.frombuffer(zlib.decompress(blob), dtype=np.float16).astype(np.float32)
            if rets.size:
                all_rets.extend(rets.tolist())
                per_sym[sym] = rets.tolist()
                years_list.append(yrs)
        if not all_rets:
            continue
        n_evaluated_syms = len(per_sym)
        avg_years = (sum(years_list) / len(years_list)) if years_list else 0
        rets_arr = np.asarray(all_rets, dtype=np.float32)
        pool = mg.pool_sharpe(all_rets)
        ssh = mg.sym_sharpe_from_groups(per_sym)
        # Sanity caps
        if abs(pool) > 5.0 and len(all_rets) < 5000:
            continue
        if abs(ssh) > 5.0:
            continue
        total_gain = float(rets_arr.sum() * 100.0)
        if total_gain == 0 and len(all_rets) < 30:
            continue
        eq = np.cumsum(rets_arr)
        peak = np.maximum.accumulate(eq)
        dd = float((peak - eq).max() * 100.0) if eq.size else 0.0
        # Sample-floor gate
        if len(all_rets) < 30 * n_evaluated_syms:
            continue
        cfg_row = con.execute(
            "SELECT label, config_json FROM configs WHERE config_hash=?", (h,)).fetchone()
        label = (cfg_row[0] if cfg_row else "?")[:120]
        results.append((pool, ssh, len(all_rets), total_gain, dd,
                        n_evaluated_syms, avg_years, h, label))
        if n_evaluated_syms >= floor_syms and avg_years >= 1.0:
            n_published += 1

    results.sort(key=lambda r: -r[0])
    print(f"\n=== TOP {min(top_n, len(results))} (sample-floor enforced; "
          f"{'PUBLISHABLE' if n_syms >= floor_syms else 'DIAGNOSTIC'} given "
          f"current n_syms={n_syms}) ===")
    for i, (pool, ssh, tr, acc, dd, ns, yrs, h, label) in enumerate(results[:top_n], 1):
        tag = "PUB" if (ns >= floor_syms and yrs >= 1.0) else "DIAG"
        print(f"  #{i:>3} pool={pool:+.4f} sym={ssh:+.4f} t={tr:>6d} "
              f"acc={acc:+8.1f}% dd={dd:5.1f}% nsym={ns:>2} y={yrs:.2f} [{tag}] {h}")
        if i <= 3:
            print(f"        {label}")
    print(f"\n[aggregate] total configs aggregated: {len(results)}, "
          f"of which {n_published} are at PUBLISHABLE floor")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "data" / "vec_sweep.db"))
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--remote", default="",
                    help="SSH host to scp DB from (e.g. s1-int)")
    ap.add_argument("--remote-db", default="/home/niels/binance-sandbox/data/vec_sweep.db")
    args = ap.parse_args()
    if args.remote:
        path = fetch_remote(args.remote, args.remote_db)
    else:
        path = Path(args.db)
    aggregate(path, args.top_n)
