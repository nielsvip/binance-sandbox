"""Phase 4 Round 4 — start from BEST round-3 config, sweep entry-tightening switches.
Goal: cut trade count 50% via stricter entries → higher-quality average → push pool ≥0.6+.
"""
import os, sys, time, argparse, json
from itertools import product
from pathlib import Path
import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_validation_sweep import _crypto_symbols, _tradier_symbols
from phase4_round3_baseline_plus import load_baseline_overrides, apply_overrides, TRADIER_BASELINE_PATH
import metrics_guard


# Entry-tightening grid (applied on top of baseline + Phase 4 + tight exits).
ENTRY_GRID = {
    'TRADIER_ENTRY_SCORE_THRESHOLD': [30, 40, 50, 60],
    'HTF_DIRECTION_GATE_ENABLED':    [True, False],
    'HTF_GATE_APPLY_TO_OPEN':        [True],
    'WT_LTF_REQUIRED':               [1, 2, 3],
    'MFI_ENTRY_ENABLED':             [True, False],
}


def run_one(mode, syms, npz_dir, label, base_overrides, tighten, max_bars, start_date):
    cfg = QuickConfig()
    if mode == 'tradier':
        cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.MODE = mode
    apply_overrides(cfg, base_overrides)
    # Phase 4 ALL ON
    cfg.USE_LIVE_EVALUATOR_VEC = True
    cfg.APPLY_QTY_PIPELINE_TO_PNL = (mode == 'tradier')
    cfg.USE_PROCESS_POSITION_EXIT_GATES = True
    # Tight exits (best from R3)
    cfg.WT_4H_VEL_EXIT_K_EXTREME_HIGH = 70.0
    cfg.DC_HOPELESS_EXIT_MIN_AGE_S = 300.0
    cfg.WT_PERCENTILE_EXIT_OB_D = 80.0
    cfg.E_3_USE_WT_STRUCTURE_EXIT_MODE = 2
    # Entry tightening
    apply_overrides(cfg, tighten)
    t1 = time.time()
    res = simulate(iter_npz(mode, syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0)
    if years <= 0:
        years = (max_bars * (3 if mode == 'crypto' else 5) / 60.0 / 24.0) / 365.25
    trades = int(res.get('trades', 0) or 0)
    acc_gain = float(res.get('acc_gain_pct', 0) or 0)
    return {
        "label": label,
        "pool_sharpe": round(float(res.get('pool_sharpe', 0)), 4),
        "sym_sharpe": round(float(res.get('sym_sharpe', 0)), 4),
        "avg_gain_trade": round(acc_gain / trades, 4) if trades > 0 else 0.0,
        "gain_per_yr": round(acc_gain / years, 4) if years > 0 else 0.0,
        "gain_sym_yr": round(acc_gain / max(n_syms, 1) / years, 6) if years > 0 else 0.0,
        "trades": trades,
        "max_dd_pct": round(float(res.get('max_dd_pct', 0)), 4),
        "n_syms": n_syms,
        "years": round(years, 4),
        "elapsed_s": round(elapsed, 1),
        "tighten_json": json.dumps(tighten),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], required=True)
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--max-bars', type=int, default=100000)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    if args.mode == 'crypto':
        syms = _crypto_symbols(npz_dir)
        base_overrides = {}
    else:
        syms = _tradier_symbols(npz_dir)
        base_overrides = load_baseline_overrides(TRADIER_BASELINE_PATH)
    print(f"[PHASE4_R4] {args.mode}: {len(syms)} syms, {args.max_bars} bars, {len(base_overrides)} baseline overrides", flush=True)

    out_path = args.out or f"data/sweep_results/phase4_round4_{args.mode}_{int(time.time())}.csv"
    print(f"[PHASE4_R4] output → {out_path}", flush=True)

    keys = list(ENTRY_GRID.keys())
    values = [ENTRY_GRID[k] for k in keys]
    combos = list(product(*values))
    print(f"[PHASE4_R4] {len(combos)} entry-tightening combos", flush=True)

    results = []
    for n, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        label = "|".join(f"{k.split('_')[-2 if 'ENABLED' in k else -1]}={v}" for k, v in params.items())[:80]
        try:
            row = run_one(args.mode, syms, npz_dir, label, base_overrides, params, args.max_bars, args.start)
            metrics_guard.write_sharpe_row(Path(out_path), row, mode=args.mode, append=True)
            results.append(row)
            if n % 5 == 0 or row['pool_sharpe'] >= 0.5:
                print(f"[{n}/{len(combos)}] pool={row['pool_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.1f}% trades={row['trades']:>6} {label[:60]}", flush=True)
        except Exception as e:
            print(f"[{n}/{len(combos)}] ERROR: {type(e).__name__}: {e} {label[:50]}", flush=True)

    print("\n" + "=" * 100)
    print(f"PHASE 4 ROUND 4 RANKED — {args.mode} (top 15 of {len(results)})")
    print(f"{'#':>3} {'pool':>8} {'dd%':>6} {'trades':>7}  {'tighten':<70}")
    for i, r in enumerate(sorted(results, key=lambda x: -x['pool_sharpe'])[:15]):
        print(f"{i+1:>3} {r['pool_sharpe']:>8.4f} {r['max_dd_pct']:>6.2f} {r['trades']:>7}  {r['tighten_json'][:70]}")


if __name__ == '__main__':
    main()
