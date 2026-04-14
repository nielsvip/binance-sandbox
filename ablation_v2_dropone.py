#!/usr/bin/env python3
"""DROP-ONE-OUT ABLATION: Start from ALL_CHANGES, remove one at a time."""
import sys, os
sys.stdout = open(sys.stdout.fileno(), mode='w', buffering=1)
sys.path.insert(0, "/home/niels/binance-sandbox/backtest_framework")
import ablation_backtest as ab
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

# Patch load_tf for corrupt files
_orig_load = ab.load_tf
def safe_load(sym, tf):
    try:
        return _orig_load(sym, tf)
    except:
        return None
ab.load_tf = safe_load

def load_symbol(sym):
    try:
        sd = ab.precompute_symbol(sym)
        return (sym, sd) if sd else None
    except:
        return None

syms = ab.get_symbols()
baseline = ab.build_baseline()
all_changes = dict(baseline)
for c in ab.CHANGES:
    all_changes[c[1]] = c[3]

print(f"Loading {len(syms)} symbols with 14 workers...")
sym_cache = {}
with ProcessPoolExecutor(max_workers=14) as pool:
    for result in pool.map(load_symbol, syms):
        if result:
            sym_cache[result[0]] = result[1]
            if len(sym_cache) % 50 == 0:
                print(f"  Loaded {len(sym_cache)} symbols...")
print(f"Loaded {len(sym_cache)} symbols")

# Cache ALL_CHANGES results per symbol
print("Computing ALL_CHANGES baseline per symbol...")
base_cache = {}
for s, sd in sym_cache.items():
    try:
        r = ab.simulate_config(sd, all_changes)
        if r:
            base_cache[s] = r
    except:
        pass
base_sharpes = [r["sharpe"] for r in base_cache.values()]
print(f"ALL_CHANGES: Sharpe {np.mean(base_sharpes):+.3f} | WR {np.mean([r['wr'] for r in base_cache.values()]):.1f}% | {len(base_cache)} symbols")

print(f"\nDROP-ONE-OUT: removing each change, comparing to ALL_CHANGES")
print(f"Positive delta = removing helps = change was HURTING")
print(f"Negative delta = removing hurts = change was HELPING")
print()

results = []
for change in ab.CHANGES:
    cid, param, bval, cval, desc = change
    if bval == cval:
        continue
    dropped = dict(all_changes)
    dropped[param] = bval
    sharpe_deltas = []
    improved = 0
    tested = 0
    for s, sd in sym_cache.items():
        if s not in base_cache:
            continue
        try:
            r_drop = ab.simulate_config(sd, dropped)
            if r_drop:
                delta = r_drop["sharpe"] - base_cache[s]["sharpe"]
                sharpe_deltas.append(delta)
                tested += 1
                if delta > 0:
                    improved += 1
        except:
            pass
    if sharpe_deltas:
        avg_d = np.mean(sharpe_deltas)
        pct = improved / tested * 100
        if avg_d > 0.5 and pct > 55:
            verdict = "REVERT"
        elif avg_d < -0.5 and pct < 45:
            verdict = "KEEP"
        else:
            verdict = "NEUTRAL"
        results.append((cid, param, desc, avg_d, pct, tested, verdict))

results.sort(key=lambda x: -x[3])
print(f"{'ID':15s} {'Param':35s} {'AvgShpDelta':>12s} {'Improved%':>10s} {'N':>5s} {'Verdict':>8s}")
print("-" * 90)
for r in results:
    print(f"{r[0]:15s} {r[1]:35s} {r[3]:+12.3f} {r[4]:9.0f}% {r[5]:5d} {r[6]:>8s}")

print("\n=== CHANGES TO REVERT (removing them helps) ===")
for r in results:
    if r[6] == "REVERT":
        print(f"  {r[0]}: {r[2]} — removing improves Sharpe by {r[3]:+.3f}")

print("\n=== CHANGES TO KEEP (removing them hurts) ===")
for r in results:
    if r[6] == "KEEP":
        print(f"  {r[0]}: {r[2]} — removing worsens Sharpe by {r[3]:+.3f}")
