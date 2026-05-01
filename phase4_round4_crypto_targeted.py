"""Crypto Round 4 — small targeted grid on the proven 0.7770 baseline.
Tests param tweaks that might actually move v8_quick on crypto path.
16 combos × ~63s = ~17 min.
"""
import os, sys, time, argparse, json
from itertools import product
from pathlib import Path
import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_validation_sweep import _crypto_symbols
from phase4_round3_baseline_plus import load_baseline_overrides, apply_overrides, CRYPTO_BASELINE_PATH
import metrics_guard


GRID = {
    'COOLDOWN_BARS':                   [1, 5],
    'CONFLUENCE_MIN_BLOCKS':           [1, 2],
    'WT_EXIT_MIN_TFS':                 [1, 3],
    'REENTRY_B12_WT_MOM_ENABLED':      [True, False],
}


def run_one(syms, npz_dir, label, base_overrides, params, max_bars, start_date):
    cfg = QuickConfig()
    cfg.MAX_BARS = max_bars
    cfg.MODE = 'crypto'
    apply_overrides(cfg, base_overrides)
    apply_overrides(cfg, params)
    t1 = time.time()
    res = simulate(iter_npz('crypto', syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0) or (max_bars * 3 / 60.0 / 24.0 / 365.25)
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
        "params_json": json.dumps(params),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--max-bars', type=int, default=200000)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    syms = _crypto_symbols(npz_dir)
    base_overrides = load_baseline_overrides(CRYPTO_BASELINE_PATH)
    print(f"[R4_CRYPTO] {len(syms)} syms, {args.max_bars} bars, {len(base_overrides)} baseline overrides", flush=True)

    out_path = f"data/sweep_results/phase4_round4_crypto_targeted_{int(time.time())}.csv"
    print(f"[R4_CRYPTO] output → {out_path}", flush=True)

    keys = list(GRID.keys())
    values = [GRID[k] for k in keys]
    combos = list(product(*values))
    print(f"[R4_CRYPTO] {len(combos)} combos", flush=True)

    results = []
    for n, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        label = "|".join(f"{k.split('_')[0]}{k.split('_')[1] if 'CONFLUENCE' in k else ''}={v}" for k, v in params.items())[:90]
        try:
            row = run_one(syms, npz_dir, label, base_overrides, params, args.max_bars, args.start)
            metrics_guard.write_sharpe_row(Path(out_path), row, mode='crypto', append=True)
            results.append(row)
            print(f"[{n:>2}/{len(combos)}] pool={row['pool_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.2f}% trades={row['trades']:>5} {label}", flush=True)
        except Exception as e:
            print(f"[{n}/{len(combos)}] ERROR: {type(e).__name__}: {e}", flush=True)

    print("\n" + "=" * 100)
    print(f"R4 CRYPTO RANKED — top 10 of {len(results)} (baseline ref pool=0.7770)")
    for i, r in enumerate(sorted(results, key=lambda x: -x['pool_sharpe'])[:10]):
        delta = r['pool_sharpe'] - 0.7770
        print(f"{i+1:>2} pool={r['pool_sharpe']:>7.4f} ({delta:+.4f}) dd={r['max_dd_pct']:>5.2f}% trades={r['trades']:>5}  {r['params_json']}")


if __name__ == '__main__':
    main()
