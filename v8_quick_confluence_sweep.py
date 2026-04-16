#!/usr/bin/env python3
"""
v8_quick_confluence_sweep.py — Sweep confluence + strength + exit params to find Sharpe > 1.8.

Each config runs in ~15-20s on 11 symbols. Target: Sharpe per-trade > 1.8 with >=50 trades.
"""
import itertools, json, os, sys, time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent


def run(args_tuple):
    npz_dir, mode, symbols, start, cfg_dict, run_id = args_tuple
    stores = load_npz(mode, symbols, start, npz_dir)
    if not stores:
        return {"run_id": run_id, "sharpe": 0, "trades": 0, "pnl": 0, "wr": 0, "avg_pnl_pct": 0, "cfg": cfg_dict}
    cfg = QuickConfig()
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    for k, v in cfg_dict.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["run_id"] = run_id
    r["elapsed"] = round(time.time() - t0, 1)
    r["cfg"] = cfg_dict
    return r


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="crypto")
    p.add_argument("--symbols", default="fast")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--npz-dir", default="")
    args = p.parse_args()

    symbols = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",") if args.symbols == "fast" else [s.strip() for s in args.symbols.split(",")]

    npz_dir = args.npz_dir
    if not npz_dir:
        for prefix in ["backtest_v8", "backtest_v7"]:
            d = BASE / prefix / "indicators"
            if d.exists() and any(d.glob("*.npz")):
                npz_dir = str(d)
                break

    # Grid: confluence OR strength + hold + cooldown + HTF + k3m
    grid = {
        "STRENGTH_FILTER_ENABLED": [True],
        "STRENGTH_MIN_SCORE": [3, 4, 5, 6, 7, 8, 9, 10],
        "MIN_HOLD_BARS": [10, 20, 40, 80],
        "COOLDOWN_BARS": [3, 20, 60],
        "HTF_MIN_ALIGNED": [1, 2, 3],
        "D_TREND_REQUIRED": [True],
        "K3M_FLOOR": [20, 30],
        "WT_EXIT_MIN_TFS": [2, 3, 4],  # MEMORY RULE: valid [2,3,4]. 5 banned, 1 too loose.
    }
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    configs = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
    print(f"Total configs: {len(configs)} | workers: {args.workers}")

    todo = [(npz_dir, args.mode, symbols, args.start, c, f"conf_{i:05d}") for i, c in enumerate(configs)]

    out_path = BASE / "data" / "sweep_results" / f"v8_confluence_{args.mode}_{int(time.time())}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run, t): t for t in todo}
        import csv
        with open(out_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["run_id", "sharpe", "pnl", "trades", "wins", "losses", "wr", "avg_pnl_pct", "elapsed"] + keys)
            completed = 0
            best_sharpe_valid = 0
            best_cfg_valid = None
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    print(f"  ERROR: {e}"); continue
                completed += 1
                w.writerow([r.get("run_id"), r.get("sharpe", 0), r.get("pnl", 0), r.get("trades", 0),
                            r.get("wins", 0), r.get("losses", 0), r.get("wr", 0), r.get("avg_pnl_pct", 0),
                            r.get("elapsed", 0)] + [r["cfg"].get(k, "") for k in keys])
                f.flush()
                # Track best with >=50 trades for statistical validity
                if r.get("trades", 0) >= 50 and r.get("sharpe", 0) > best_sharpe_valid:
                    best_sharpe_valid = r["sharpe"]
                    best_cfg_valid = r["cfg"]
                    print(f"  [{completed}/{len(todo)}] NEW BEST (valid): Sharpe={r['sharpe']:.4f} trades={r['trades']} wr={r['wr']}% avg={r['avg_pnl_pct']:.2f}% cfg={r['cfg']}")
                elif completed % 50 == 0:
                    print(f"  [{completed}/{len(todo)}] best_valid={best_sharpe_valid:.4f}")

    print(f"\n{'='*80}")
    print(f"  SWEEP COMPLETE — {completed} configs")
    print(f"  Best Sharpe (>=50 trades): {best_sharpe_valid:.4f}")
    if best_cfg_valid:
        print(f"  Best config: {best_cfg_valid}")
    print(f"  Results: {out_path}")


if __name__ == "__main__":
    main()
