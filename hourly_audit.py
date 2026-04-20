#!/usr/bin/env python3
"""Hourly audit puller: pulls latest sector sweep results from S2, prints cross-iteration trend.

Runs on MacBook. Usage:
  python3 hourly_audit.py [--top-n 20] [--side L|S|both]

Pulls:
  - Most recent sector_audit_iter*.md from S2
  - Pulls SQLite DBs for cross-sector deep analysis
  - Reports iteration-over-iteration delta (is each iter improving?)
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
SECTORS = ["mix_12", "tech_big", "tech_growth", "metals_miners", "energy_oil", "industrials_ag", "financials_etfs", "misc_industrial"]
PICK = 3
BARS = 40000


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
    return r.returncode, r.stdout, r.stderr


def pull_sector_dbs():
    local_d = BASE / "data" / "sweep_results"
    local_d.mkdir(parents=True, exist_ok=True)
    # Pull all sector DBs + audit MDs
    cmd = f"rsync -az --ignore-missing-args s2-int:/home/niels/binance-sandbox/data/sweep_results/vec_sector_*.sqlite {local_d}/"
    rc, o, e = run(cmd)
    print(f"[pull DBs rc={rc}]")
    cmd2 = f"rsync -az --ignore-missing-args s2-int:/home/niels/binance-sandbox/data/sweep_results/sector_audit_iter*.md {local_d}/"
    rc2, o2, e2 = run(cmd2)
    print(f"[pull MDs rc={rc2}]")
    cmd3 = f"rsync -az --ignore-missing-args s2-int:/home/niels/binance-sandbox/data/sweep_results/sector_orchestrator_heartbeat.json {local_d}/"
    run(cmd3)


def sector_top_row(db_path, source, n=5):
    """source: 'sample' | 'validated' | 'combined'. Returns list of tuples."""
    if not db_path.exists():
        return []
    try:
        c = sqlite3.connect(str(db_path))
        if source == "sample":
            rows = c.execute("SELECT side, combo, sharpe_robust, sharpe_avg, sharpe_pool, syms_included, n_trades_total, wr_avg FROM results ORDER BY sharpe_robust DESC LIMIT ?", (n,)).fetchall()
        elif source == "validated":
            try:
                rows = c.execute("SELECT side, combo, full_sharpe_robust, full_sharpe_avg, full_sharpe_pool, full_syms, full_trades, full_wr, dropoff_pct FROM validated_full ORDER BY full_sharpe_robust DESC LIMIT ?", (n,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        elif source == "combined":
            try:
                rows = c.execute("SELECT side, combo_merged, full_sharpe_robust, full_sharpe_avg, full_sharpe_pool, full_syms, full_trades, full_wr FROM combined_results ORDER BY full_sharpe_robust DESC LIMIT ?", (n,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        c.close()
        return rows
    except Exception as e:
        print(f"  DB error {db_path.name}: {e}")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--side", choices=["L", "S", "both"], default="both")
    ap.add_argument("--no-pull", action="store_true", help="skip rsync pull")
    args = ap.parse_args()

    if not args.no_pull:
        pull_sector_dbs()

    local_d = BASE / "data" / "sweep_results"
    hb_f = local_d / "sector_orchestrator_heartbeat.json"
    if hb_f.exists():
        import json
        hb = json.load(open(hb_f))
        print(f"\n=== HEARTBEAT ===")
        print(f"Started: {hb.get('start')}")
        print(f"Stop at: {hb.get('stop_at')}")
        print(f"Current iteration: {hb.get('current_iter')}")
        print(f"Iterations done: {len(hb.get('iterations', []))}")
        print(f"Updated: {hb.get('updated_at')}")

    print(f"\n=== GLOBAL TOP {args.top_n} PER SOURCE (across sectors) ===\n")
    all_hits = []
    for sec in SECTORS:
        db = local_d / f"vec_sector_{sec}_p{PICK}_b{BARS}.sqlite"
        for src in ("sample", "validated", "combined"):
            rows = sector_top_row(db, src, args.top_n)
            for r in rows:
                if args.side != "both" and r[0] != args.side:
                    continue
                all_hits.append((src, sec, r))

    # Sort by robust (sample: r[2]; validated/combined: r[2])
    all_hits.sort(key=lambda x: x[2][2] or 0, reverse=True)
    print(f"{'Rk':>3} {'Src':<9} {'Sector':<16} {'Sd':<2} {'Rob':>5} {'Avg':>5} {'Pool':>5} {'N':>7} {'T/day':>6} {'WR':>5} Combo")
    # 504 = ~trading days in 2yr
    for rank, (src, sec, r) in enumerate(all_hits[:40], 1):
        side, combo, rob, avg, pool = r[0], r[1], r[2], r[3], r[4]
        if src == "sample":
            n = r[6]; wr = r[7]
        elif src == "validated":
            n = r[6]; wr = r[7]
        else:
            n = r[6]; wr = r[7]
        tpd = n / 504.0  # trades per day (across all syms in sector)
        print(f"{rank:>3} {src:<9} {sec:<16} {side:<2} {rob:>5.2f} {avg:>5.2f} {pool:>5.2f} {n:>7} {tpd:>5.1f} {wr:>4.1f}% {combo[:80]}")

    # Elite check: robust >= 3
    elite = [h for h in all_hits if (h[2][2] or 0) >= 3.0]
    print(f"\n=== ELITE (robust >= 3.0): {len(elite)} ===")
    if elite:
        for src, sec, r in elite[:10]:
            print(f"  [{src}|{sec}|{r[0]}] rob={r[2]:.3f} n={r[6]} {r[1]}")
    # Scalping check: ≥200/day, robust>=1
    scalp = [h for h in all_hits if (h[2][6] or 0) / 504.0 >= 200 and (h[2][2] or 0) >= 1.0]
    print(f"\n=== SCALP (>=200 trades/day & robust>=1): {len(scalp)} ===")
    if scalp:
        for src, sec, r in scalp[:10]:
            print(f"  [{src}|{sec}|{r[0]}] rob={r[2]:.3f} n={r[6]} t/day={r[6]/504.0:.0f} {r[1]}")


if __name__ == "__main__":
    main()
