"""Phase 4 Round 2 — parameter sweep on the winning combo.
Fixes the 3 phase-4 flags ON, sweeps exit-gate thresholds + entry-block toggles.

Goal: find a config with pool_sharpe ≥ 0.6 (Best-of-current tier) on full universe.
"""
import os, sys, time, argparse, glob, json
from pathlib import Path
from itertools import product

import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_validation_sweep import _crypto_symbols, _tradier_symbols
import metrics_guard


# Sweep grid — 2 × 2 × 3 × 3 × 2 × 2 = 144 combos. ~12s/combo on tradier.
GRID = {
    'WT_4H_VEL_EXIT_K_EXTREME_HIGH': [70.0, 90.0],            # tighter K vs looser
    'WT_4H_VEL_EXIT_REQUIRE_PROFIT': [True, False],            # demand profit before exit
    'DC_HOPELESS_EXIT_MIN_AGE_S':    [300.0, 900.0, 1800.0],  # how long to wait
    'WT_PERCENTILE_EXIT_OB_D':       [80.0, 90.0, 95.0],      # OB threshold
    'E_1_WT_EXIT_USE_DELTA_ENABLED': [False, True],            # add delta exit
    'E_3_USE_WT_STRUCTURE_EXIT_MODE': [0, 2],                  # add structure exit
}

# Entry-block ablation: try dropping the weak blocks
ENTRY_ABLATION = [
    ('keep_all', {}),
    ('drop_b14',  {'REENTRY_B14_HA_TREND_ENABLED': False}),
    ('drop_b10',  {'REENTRY_B10_STOCH_REV_ENABLED': False}),
    ('drop_both', {'REENTRY_B14_HA_TREND_ENABLED': False, 'REENTRY_B10_STOCH_REV_ENABLED': False}),
]


def run_one(mode, syms, npz_dir, label, params, max_bars, start_date):
    cfg = QuickConfig()
    if mode == 'tradier':
        cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    # Always-on phase-4 flags
    cfg.USE_LIVE_EVALUATOR_VEC = True
    cfg.APPLY_QTY_PIPELINE_TO_PNL = True
    cfg.USE_PROCESS_POSITION_EXIT_GATES = True
    cfg.MODE = mode
    for k, v in params.items():
        setattr(cfg, k, v)
    t1 = time.time()
    res = simulate(iter_npz(mode, syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0)
    if years <= 0:
        years = (max_bars * (3 if mode == 'crypto' else 5) / 60.0 / 24.0) / 365.25
    trades = int(res.get('trades', 0) or 0)
    acc_gain = float(res.get('acc_gain_pct', 0) or 0)
    avg_gain_trade = (acc_gain / trades) if trades > 0 else 0.0
    gain_per_yr = (acc_gain / years) if years > 0 else 0.0
    gain_sym_yr = (gain_per_yr / max(n_syms, 1))
    return {
        "label": label,
        "pool_sharpe": round(float(res.get('pool_sharpe', 0)), 4),
        "sym_sharpe": round(float(res.get('sym_sharpe', 0)), 4),
        "avg_gain_trade": round(avg_gain_trade, 4),
        "gain_per_yr": round(gain_per_yr, 4),
        "gain_sym_yr": round(gain_sym_yr, 6),
        "trades": trades,
        "max_dd_pct": round(float(res.get('max_dd_pct', 0)), 4),
        "n_syms": n_syms,
        "years": round(years, 4),
        "elapsed_s": round(elapsed, 1),
        "params_json": json.dumps(params),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], required=True)
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--max-bars', type=int, default=120000)
    ap.add_argument('--out', default=None)
    ap.add_argument('--max-combos', type=int, default=200)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    if args.mode == 'crypto':
        syms = _crypto_symbols(npz_dir)
    else:
        syms = _tradier_symbols(npz_dir)
    print(f"[PHASE4_R2] {args.mode}: {len(syms)} syms, {args.max_bars} bars/sym", flush=True)

    out_path = args.out or f"data/sweep_results/phase4_round2_{args.mode}_{int(time.time())}.csv"
    print(f"[PHASE4_R2] output → {out_path}", flush=True)

    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    grid_combos = list(product(*values))
    total = len(grid_combos) * len(ENTRY_ABLATION)
    if total > args.max_combos:
        # Trim by sampling — keep all entry ablations + truncate grid
        per_ablation = max(1, args.max_combos // len(ENTRY_ABLATION))
        grid_combos = grid_combos[:per_ablation]
        total = len(grid_combos) * len(ENTRY_ABLATION)
    print(f"[PHASE4_R2] {total} configs to test", flush=True)

    results = []
    n = 0
    for ab_label, ab_params in ENTRY_ABLATION:
        for combo in grid_combos:
            n += 1
            params = dict(zip(keys, combo))
            params.update(ab_params)
            label = f"{ab_label}|" + "|".join(f"{k.split('_')[-1]}={v}" for k, v in zip(keys, combo))
            try:
                row = run_one(args.mode, syms, npz_dir, label, params, args.max_bars, args.start)
                metrics_guard.write_sharpe_row(Path(out_path), row, mode=args.mode, append=True)
                results.append(row)
                if n % 5 == 0 or row['pool_sharpe'] >= 0.5:
                    print(f"[{n}/{total}] {label[:60]:<60} pool={row['pool_sharpe']:.4f} dd={row['max_dd_pct']:.1f}% trades={row['trades']}", flush=True)
            except Exception as e:
                print(f"[{n}/{total}] ERROR {label[:60]}: {type(e).__name__}: {e}", flush=True)

    # Top 10
    results.sort(key=lambda x: -x['pool_sharpe'])
    print("\n" + "=" * 100)
    print(f"TOP 10 of {len(results)} configs ({args.mode}):")
    print(f"{'#':>3} {'pool':>7} {'sym':>7} {'dd%':>6} {'trades':>7}  {'label':<70}")
    for i, r in enumerate(results[:10]):
        print(f"{i+1:>3} {r['pool_sharpe']:>7.4f} {r['sym_sharpe']:>7.4f} {r['max_dd_pct']:>6.2f} {r['trades']:>7}  {r['label'][:70]}")
    if results:
        best = results[0]
        print(f"\nBEST {args.mode}: {best['label']}")
        print(f"  pool={best['pool_sharpe']:.4f} sym={best['sym_sharpe']:.4f} dd={best['max_dd_pct']:.2f}% trades={best['trades']}")


if __name__ == '__main__':
    main()
