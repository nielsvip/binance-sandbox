#!/usr/bin/env python3
"""Big sweep around the Chapter-ALL baseline.

ALL chapter won +1.60 Sharpe / 15 trades / 80% WR. Explore parameter space
around it to find higher volume at maintained Sharpe, or higher Sharpe at
maintained volume.
"""
import itertools
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from v8_quick_engine import QuickConfig, load_npz, simulate

SYMS_48 = json.load(open(BASE / "backtest_48_symbols.json"))
START = "2022-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")

# All QuickConfig defaults are now chapter-ALL aligned (flipped today).
# Sweep grid: tune each key knob within reasonable ranges.
GRID = {
    "ENTRY_SCORE_THRESHOLD":        [0.0, 10.0, 15.0, 20.0, 25.0],  # now wired!
    "HTF_MIN_ALIGNED":              [1, 2, 3],
    "CT_WT_VELOCITY_1H_MIN":        [1.0, 1.5, 2.0, 2.5],
    "REENTRY_RALLY_K15M_MAX":       [50.0, 60.0, 70.0],
    "RANK_CONVICTION_MIN":          [1, 2, 3],
    "WINNER_PROTECT_GAIN_PCT":      [1.5, 2.0, 2.5],
}
# 5 × 3 × 4 × 3 × 3 × 3 = 1620 configs


def run(overrides, stores):
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return simulate(stores, cfg, 10000.0)


def main():
    print(f"Loading 48 NPZ …")
    t0 = time.time()
    stores = load_npz("crypto", SYMS_48, START, NPZ_DIR)
    print(f"  {len(stores)} symbols in {time.time()-t0:.1f}s\n")

    r_base = run({}, stores)
    print(f"BASELINE (all QuickConfig defaults = chapter ALL)")
    print(f"  sharpe={r_base['sharpe']:+.4f}  trades={r_base['trades']}  WR={r_base['wr']:.1f}%  avg={r_base['avg_pnl_pct']:+.3f}%\n")

    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    combos = list(itertools.product(*values))
    total = len(combos)
    print(f"Sweep grid: {total} configs\n")

    results = []
    best = {"sharpe": -999, "cfg": None}
    print(f"{'#':>4s} {' '.join(f'{k[:14]:>14s}' for k in keys)}   {'sharpe':>8s} {'trades':>7s} {'WR':>5s} {'avg':>7s}")
    print("-" * 140)
    t_start = time.time()

    for i, combo in enumerate(combos):
        ov = dict(zip(keys, combo))
        r = run(ov, stores)
        r["cfg"] = ov
        results.append(r)
        flag = ""
        if r["sharpe"] > best["sharpe"] and r["trades"] >= 10:
            best = {"sharpe": r["sharpe"], "cfg": ov, "trades": r["trades"], "wr": r["wr"], "avg": r["avg_pnl_pct"]}
            flag = " ★"
        if i % 10 == 0 or flag or i == total - 1:
            vals_str = " ".join(f"{str(v):>14s}" for v in combo)
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed
            eta = (total - i - 1) / rate if rate > 0 else 0
            print(f"{i+1:>4d} {vals_str}   {r['sharpe']:+.4f} {r['trades']:>6d} {r['wr']:>4.1f}% {r['avg_pnl_pct']:+.3f}%{flag}  [{rate:.1f}/s ETA {eta/60:.1f}min]")

    # Top 20
    ranked = sorted(results, key=lambda r: -r["sharpe"])
    print("\n" + "=" * 140)
    print(f"BEST: sharpe={best['sharpe']:+.4f}  trades={best.get('trades')}  WR={best.get('wr')}%  avg={best.get('avg')}%")
    print(f"       cfg: {best['cfg']}\n")
    print("TOP 20 by Sharpe (trades >= 10):")
    cnt = 0
    for r in ranked:
        if r["trades"] < 10: continue
        print(f"  sharpe={r['sharpe']:+.4f}  trades={r['trades']:>5d}  WR={r['wr']:4.1f}%  avg={r['avg_pnl_pct']:+.3f}%  {r['cfg']}")
        cnt += 1
        if cnt >= 20: break

    out = BASE / "data" / "sweep_results" / f"big_sweep_crypto_48sym_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"baseline": r_base, "results": results, "best": best, "grid": GRID}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
