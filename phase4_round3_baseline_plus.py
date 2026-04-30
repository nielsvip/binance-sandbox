"""Phase 4 Round 3 — start from honest baseline, add Phase 4 flags on top.

For tradier: tradier_baseline_0p5823_20260421.json (the verified 0.5823 baseline).
For crypto: no honest baseline exists yet; uses QuickConfig defaults + best round-2
            params + entry-tightening exploration.

Goal: confirm Phase 4 flags push baseline 0.5823 → ≥1.0 on tradier.
"""
import os, sys, time, argparse, glob, json
from pathlib import Path

import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_validation_sweep import _crypto_symbols, _tradier_symbols
import metrics_guard


TRADIER_BASELINE_PATH = Path("data/orchestrator/winner_overrides/tradier_baseline_0p5823_20260421.json")
CRYPTO_BASELINE_PATH = Path("data/orchestrator/winner_overrides/crypto_baseline_0p7574_20260421.json")


def load_baseline_overrides(path: Path) -> dict:
    """Load JSON overrides, filter out _meta + unknown attrs at apply time."""
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith('_')}


def apply_overrides(cfg: QuickConfig, overrides: dict) -> int:
    """Apply only the overrides whose keys are real QuickConfig attrs.
    Returns number applied (vs skipped). Skipped keys go to live config_tradier.py
    fields that v8_quick doesn't read — that's expected for many."""
    applied = 0
    skipped = []
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
            applied += 1
        else:
            skipped.append(k)
    return applied, skipped


# Phase 4 flag combos to test ON TOP of baseline
PHASE4_COMBOS = [
    ("baseline_alone",                False, False, False),
    ("baseline_+_eval",               True,  False, False),
    ("baseline_+_qty",                False, True,  False),
    ("baseline_+_exit",               False, False, True),
    ("baseline_+_eval+exit",          True,  False, True),
    ("baseline_+_qty+exit",           False, True,  True),
    ("baseline_+_all_three",          True,  True,  True),
    # Aggressive: + drop weak entry blocks
    ("baseline_+_all_+_drop_b14",     True,  True,  True, {'REENTRY_B14_HA_TREND_ENABLED': False}),
    ("baseline_+_all_+_drop_b10b14",  True,  True,  True, {'REENTRY_B14_HA_TREND_ENABLED': False, 'REENTRY_B10_STOCH_REV_ENABLED': False}),
    # Aggressive exits
    ("baseline_+_all_+_tight_exits",  True,  True,  True, {
        'WT_4H_VEL_EXIT_K_EXTREME_HIGH': 70.0,
        'DC_HOPELESS_EXIT_MIN_AGE_S': 300.0,
        'WT_PERCENTILE_EXIT_OB_D': 80.0,
        'E_3_USE_WT_STRUCTURE_EXIT_MODE': 2,
    }),
]


def run_one(mode, syms, npz_dir, label, base_overrides, ue, aq, ux, extra_overrides, max_bars, start_date):
    cfg = QuickConfig()
    if mode == 'tradier':
        cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.MODE = mode
    # Apply baseline overrides FIRST
    n_applied, n_skipped = apply_overrides(cfg, base_overrides)
    # Then Phase 4 flags
    cfg.USE_LIVE_EVALUATOR_VEC = ue
    cfg.APPLY_QTY_PIPELINE_TO_PNL = aq
    cfg.USE_PROCESS_POSITION_EXIT_GATES = ux
    # Extra params
    if extra_overrides:
        apply_overrides(cfg, extra_overrides)
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
        "use_live_evaluator_vec": ue,
        "apply_qty_pipeline_to_pnl": aq,
        "use_process_position_exit_gates": ux,
        "elapsed_s": round(elapsed, 1),
        "n_baseline_applied": n_applied,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier'], required=True)
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--max-bars', type=int, default=120000)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    if args.mode == 'crypto':
        syms = _crypto_symbols(npz_dir)
        base_overrides = load_baseline_overrides(CRYPTO_BASELINE_PATH)
    else:
        syms = _tradier_symbols(npz_dir)
        base_overrides = load_baseline_overrides(TRADIER_BASELINE_PATH)
    print(f"[PHASE4_R3] {args.mode}: {len(syms)} syms, {args.max_bars} bars/sym, {len(base_overrides)} baseline overrides", flush=True)

    out_path = args.out or f"data/sweep_results/phase4_round3_{args.mode}_{int(time.time())}.csv"
    print(f"[PHASE4_R3] output → {out_path}", flush=True)

    results = []
    for combo in PHASE4_COMBOS:
        if len(combo) == 5:
            label, ue, aq, ux, extra = combo
        else:
            label, ue, aq, ux = combo; extra = None
        try:
            row = run_one(args.mode, syms, npz_dir, label, base_overrides, ue, aq, ux, extra, args.max_bars, args.start)
            metrics_guard.write_sharpe_row(Path(out_path), row, mode=args.mode, append=True)
            results.append(row)
            print(f"[{label[:35]:<35}] pool={row['pool_sharpe']:>7.4f} sym={row['sym_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.1f}% trades={row['trades']:>6} elapsed={row['elapsed_s']:>4.0f}s applied={row['n_baseline_applied']}", flush=True)
        except metrics_guard.FakeMetricRefused as e:
            print(f"[{label}] REFUSED: {e}", flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[{label}] ERROR: {e}", flush=True)

    print("\n" + "=" * 100)
    print(f"PHASE 4 ROUND 3 RANKED — {args.mode} ({len(syms)} syms)")
    print(f"{'#':>3} {'pool':>8} {'sym':>8} {'dd%':>6} {'trades':>7}  {'label':<40}")
    for i, r in enumerate(sorted(results, key=lambda x: -x['pool_sharpe'])[:15]):
        print(f"{i+1:>3} {r['pool_sharpe']:>8.4f} {r['sym_sharpe']:>8.4f} {r['max_dd_pct']:>6.2f} {r['trades']:>7}  {r['label']:<40}")


if __name__ == '__main__':
    main()
