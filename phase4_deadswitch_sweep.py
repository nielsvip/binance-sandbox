"""Phase 4 Dead-Switch Wiring Validation — 2026-04-30.

Tests 9 newly-wired baseline switches that were previously definition-only in v8_quick_engine.py.
Each switch is tested individually ON-vs-OFF on top of the honest baseline (tradier_baseline_0p5823),
plus an "ALL ON" combo and a "best stack" combo (ALL + Phase 4 retrofit + tight exits).

Activation rule: switch lifts pool_sharpe >= +0.05 over baseline_alone AND DD < 25% → flip to default ON.

Goal: lift tradier honest pool_sharpe from 0.3856 toward 1.0.
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


def load_baseline_overrides(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith('_')}


def apply_overrides(cfg: QuickConfig, overrides: dict):
    applied = 0; skipped = []
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v); applied += 1
        else:
            skipped.append(k)
    return applied, skipped


# Each switch's ON-extras includes companion knobs (master + thresholds).
# Baseline JSON already sets the thresholds; here we add the master enable companions.
SWITCH_ON_EXTRAS = {
    "MOM3_ENTRY":           {"MOM3_ENTRY_ENABLED": True, "MOM3_LONG_THRESHOLD": -1.0, "MOM3_SHORT_THRESHOLD": 0.25},
    "BASIS_CONDITION":      {"BASIS_CONDITION": True},
    "BB_RECOVERY_EXIT":     {"BB_RECOVERY_EXIT_ENABLED_TRADIER": True},
    "SATOSHIT_ENTRY_TRAD":  {"SATOSHIT_ENTRY_TRADIER_ENABLED": True, "SATOSHIT_LONG_STOCH_K_MAX_TRADIER": 90.0, "SATOSHIT_LONG_RSI_MAX_TRADIER": 70.0, "SATOSHIT_LONG_MFI_MAX_TRADIER": 70.0, "SATOSHIT_MIN_VOTES_TRADIER": 3},
    "TRADIER_FH_MOMENTUM":  {"TRADIER_FH_MOMENTUM_ENABLED": True, "TRADIER_FH_MOMENTUM_MFI_CONFIRM": False, "TRADIER_FH_MOMENTUM_DC_CONFIRM": True, "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": 0.5},
    "REGIME_TRENDING":      {"REGIME_GATE_ENABLED": True, "REGIME_ENTER_TRENDING_THRESHOLD": 15.0},
    "HIER_RZ_TOP_BB":       {"HIER_SIGNAL_MODE": "entry", "HIER_RZ_TOP_BB": 0.2125, "HIER_RZ_BOT_BB": 0.85},
    "BB_SQUEEZE_ENTRY":     {"BB_SQUEEZE_ENTRY_ENABLED": True, "BB_SQUEEZE_MIN_ALIGNMENT": 5},
    "RSI_MOMENTUM_MODE":    {"RSI_MOMENTUM_MODE": True},
}


# Phase 4 retrofit + tight-exit best stack
PHASE4_BEST_EXTRAS = {
    "USE_LIVE_EVALUATOR_VEC": True,
    "APPLY_QTY_PIPELINE_TO_PNL": True,
    "USE_PROCESS_POSITION_EXIT_GATES": True,
    "WT_4H_VEL_EXIT_K_EXTREME_HIGH": 70.0,
    "DC_HOPELESS_EXIT_MIN_AGE_S": 300.0,
    "WT_PERCENTILE_EXIT_OB_D": 80.0,
    "E_3_USE_WT_STRUCTURE_EXIT_MODE": 2,
}


def build_combos():
    combos = []
    combos.append(("ref_baseline_alone", {}))
    for name, extras in SWITCH_ON_EXTRAS.items():
        combos.append((f"only_{name}", extras))
    # ALL switches ON together
    all_on = {}
    for v in SWITCH_ON_EXTRAS.values():
        all_on.update(v)
    combos.append(("ALL_DEAD_ON", all_on))
    # ALL + Phase 4 + tight exits
    best_stack = dict(all_on); best_stack.update(PHASE4_BEST_EXTRAS)
    combos.append(("ALL_DEAD_+_p4_+_tight", best_stack))
    return combos


def run_one(syms, npz_dir, label, base_overrides, extras, max_bars, start_date):
    cfg = QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.MODE = 'tradier'
    apply_overrides(cfg, base_overrides)
    apply_overrides(cfg, extras)
    t1 = time.time()
    res = simulate(iter_npz('tradier', syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0)
    if years <= 0:
        years = (max_bars * 5 / 60.0 / 24.0) / 365.25
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
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2023-01-01')
    ap.add_argument('--max-bars', type=int, default=200000)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    syms = _tradier_symbols(npz_dir)
    base_overrides = load_baseline_overrides(TRADIER_BASELINE_PATH)
    print(f"[DEADSWITCH] tradier: {len(syms)} syms, {args.max_bars} bars, start={args.start}, "
          f"{len(base_overrides)} baseline overrides", flush=True)

    out_path = args.out or f"data/sweep_results/phase4_deadswitch_tradier_{int(time.time())}.csv"
    print(f"[DEADSWITCH] output → {out_path}", flush=True)

    combos = build_combos()
    results = []
    ref_pool = None
    for label, extras in combos:
        try:
            row = run_one(syms, npz_dir, label, base_overrides, extras, args.max_bars, args.start)
            metrics_guard.write_sharpe_row(Path(out_path), row, mode='tradier', append=True)
            if label == "ref_baseline_alone":
                ref_pool = row['pool_sharpe']
            results.append(row)
            lift = (row['pool_sharpe'] - ref_pool) if ref_pool is not None else 0.0
            print(f"[{label[:30]:<30}] pool={row['pool_sharpe']:>7.4f} sym={row['sym_sharpe']:>7.4f} "
                  f"dd={row['max_dd_pct']:>5.1f}% trades={row['trades']:>6} "
                  f"lift={lift:+.4f} elapsed={row['elapsed_s']:>4.0f}s", flush=True)
        except metrics_guard.FakeMetricRefused as e:
            print(f"[{label}] REFUSED: {e}", flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[{label}] ERROR: {e}", flush=True)

    print("\n" + "=" * 100)
    print(f"DEADSWITCH RANKED — tradier ({len(syms)} syms × {args.max_bars} bars)")
    print(f"{'#':>3} {'pool':>8} {'sym':>8} {'dd%':>6} {'trades':>7}  {'lift':>8}  {'label':<35}")
    for i, r in enumerate(sorted(results, key=lambda x: -x['pool_sharpe'])):
        lift = (r['pool_sharpe'] - ref_pool) if ref_pool is not None else 0.0
        print(f"{i+1:>3} {r['pool_sharpe']:>8.4f} {r['sym_sharpe']:>8.4f} "
              f"{r['max_dd_pct']:>6.2f} {r['trades']:>7}  {lift:>+7.4f}  {r['label']:<35}")
    if ref_pool is not None:
        print(f"\nReference baseline_alone pool_sharpe = {ref_pool:.4f}")
        print(f"Activation threshold: lift >= +0.05 AND dd < 25%")
        approved = []
        for r in results:
            if r['label'].startswith('only_'):
                lift = r['pool_sharpe'] - ref_pool
                if lift >= 0.05 and r['max_dd_pct'] < 25.0:
                    approved.append((r['label'][5:], lift, r['max_dd_pct'], r['pool_sharpe']))
        print(f"\nAPPROVED for default=True ({len(approved)}):")
        for n, l, d, p in approved:
            print(f"  - {n}: pool={p:.4f} (lift {l:+.4f}, dd={d:.2f}%)")


if __name__ == '__main__':
    main()
