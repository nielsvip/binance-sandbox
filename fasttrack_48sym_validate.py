"""
Fast-track validator: test top autonomous candidates on 48 symbols immediately,
without waiting for the full funnel Tier-A→B→C pipeline.

Tests both PASS and FAIL (high-sharpe low-trades) candidates from 12-sym Tier-A.
Goal: find configs that maintain 1.0+ Sharpe at 48-sym scale.

Usage:
  python3 fasttrack_48sym_validate.py --mode crypto --npz-dir /path/to/npz \
    --stage1-glob '/path/to/autonomous/*/w*/autonomous_crypto.csv' \
    --symbols-json /path/to/backtest_48_symbols.json \
    --start 2022-01-01 --top-n 50 --out results_48sym.csv
"""
import argparse
import csv
import glob
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v8_quick_engine import QuickConfig, simulate, iter_npz


FORBIDDEN_KEYS = {
    "_score_pool_sharpe", "_pool_sharpe", "_sharpe", "_trades", "_wr",
    "_accumulated_gain_pct", "_max_dd_pct", "_meta", "_symbols_tested",
    "_acc_gain_pct", "MODE", "LTF",
}


def load_stage1_candidates(glob_pattern, min_sharpe=0.3, top_n=100):
    rows = []
    for path in glob.glob(glob_pattern, recursive=True):
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ps = float(row.get("pool_sharpe", 0))
                        tr = int(float(row.get("trades", 0)))
                        if ps >= min_sharpe:
                            rows.append((ps, tr, path, dict(row)))
                    except (ValueError, KeyError):
                        continue
        except Exception:
            continue
    rows.sort(key=lambda x: x[0], reverse=True)
    return rows[:top_n]


def apply_overrides(cfg, row_dict):
    for k, v in row_dict.items():
        if k is None or v is None:
            continue
        if k in FORBIDDEN_KEYS or k.startswith("_"):
            continue
        if not hasattr(cfg, k):
            continue
        try:
            cur = getattr(cfg, k)
            if isinstance(cur, bool):
                setattr(cfg, k, str(v).lower() in ("true", "1", "yes"))
            elif isinstance(cur, int):
                setattr(cfg, k, int(float(v)))
            elif isinstance(cur, float):
                setattr(cfg, k, float(v))
            elif isinstance(cur, str):
                setattr(cfg, k, str(v))
        except (ValueError, TypeError):
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="crypto", choices=["crypto", "tradier"])
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--stage1-glob", required=True)
    ap.add_argument("--symbols-json", required=True)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--min-stage1-sharpe", type=float, default=0.3)
    ap.add_argument("--out", default="fasttrack_results.csv")
    args = ap.parse_args()

    symbols = json.load(open(args.symbols_json))
    existing = {
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(args.npz_dir, "*.npz"))
    }
    available = [s for s in symbols if s in existing]
    print(f"Symbols available: {len(available)}/{len(symbols)}")

    print(f"Loading candidates from stage1 glob...")
    candidates = load_stage1_candidates(args.stage1_glob, args.min_stage1_sharpe, args.top_n)
    print(f"Loaded {len(candidates)} candidates (top {args.top_n} by pool_sharpe)")

    out_rows = []
    fieldnames = ["rank", "stage1_sharpe", "stage1_trades", "sharpe_48", "trades_48", "gain_48", "dd_48", "elapsed_s"]

    for rank, (ps12, tr12, src_path, row_dict) in enumerate(candidates, 1):
        t0 = time.time()
        cfg = QuickConfig()
        cfg.MODE = args.mode
        cfg.LTF = "3m" if args.mode == "crypto" else "5m"
        apply_overrides(cfg, row_dict)

        stream = iter_npz(
            mode=args.mode,
            symbols=available,
            start_date=args.start,
            npz_dir=args.npz_dir,
        )
        try:
            res = simulate(stream, cfg)
        except Exception as e:
            print(f"  [{rank}] ERROR: {e}")
            continue

        ps48 = res.get("pool_sharpe", 0.0)
        tr48 = res.get("trades", 0)
        gain48 = res.get("accumulated_gain_pct", 0.0)
        dd48 = res.get("max_dd_pct", 0.0)
        elapsed = time.time() - t0

        status = "HIGH_SHARPE" if ps48 >= 0.90 else ("OK" if ps48 >= 0.75 else "WEAK")
        print(
            f"  [{rank:3d}] stage1={ps12:.4f}/{tr12}tr → 48sym={ps48:.4f}/{tr48}tr "
            f"gain={gain48:.0f}% dd={dd48:.1f}% [{status}] {elapsed:.0f}s"
        )

        out_rows.append({
            "rank": rank,
            "stage1_sharpe": round(ps12, 4),
            "stage1_trades": tr12,
            "sharpe_48": ps48,
            "trades_48": tr48,
            "gain_48": round(gain48, 1),
            "dd_48": round(dd48, 2),
            "elapsed_s": round(elapsed, 1),
        })

    out_rows.sort(key=lambda r: r["sharpe_48"], reverse=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    print(f"\nResults saved to {args.out}")
    print(f"Top 10 by 48-sym Sharpe:")
    for r in out_rows[:10]:
        print(
            f"  rank={r['rank']} stage1={r['stage1_sharpe']} → 48sym={r['sharpe_48']} "
            f"trades={r['trades_48']} gain={r['gain_48']}%"
        )


if __name__ == "__main__":
    main()
