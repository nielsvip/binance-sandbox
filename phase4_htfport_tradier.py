"""Phase 4 HTF-PORT — sweep new tradier-only HTF flags.

Tests F1 (HTF_W_M_ALIGN_GATE), F2 (HTF_DC_BREAKOUT), F3 (HTF_W_REVERSAL_EXIT)
on the verified tradier baseline + best-from-R3 phase4 flags + tight exits.

Each flag is tested ON in isolation, plus pairs and ALL-ON. Activation criterion:
≥0.05 pool_sharpe lift over baseline-best (0.3752) AND DD < 25%.
"""
import os, sys, time, json
from itertools import product
from pathlib import Path

import numpy as np
os.environ.setdefault("V8_RATE_GUARD_DISABLED", "1")
sys.path.insert(0, str(Path(__file__).parent))

from v8_quick_engine import QuickConfig, simulate, iter_npz, _resolve_npz_dir
from phase4_validation_sweep import _tradier_symbols
from phase4_round3_baseline_plus import load_baseline_overrides, apply_overrides, TRADIER_BASELINE_PATH
import metrics_guard


# Combos to sweep — each line maps a label to flag overrides applied on top of baseline+phase4+tight_exits.
HTF_COMBOS = [
    # Reference: baseline+phase4+tight_exits with NO new HTF flags
    ("ref_baseline_alone",                        {}),
    # Single flag ON
    ("F1_align_gate_2of2",                        {"HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": True, "HTF_W_M_ALIGN_TRADIER_REQUIRED": 2}),
    ("F1_align_gate_1of2",                        {"HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": True, "HTF_W_M_ALIGN_TRADIER_REQUIRED": 1}),
    ("F2_dc_breakout_4h",                         {"HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": True}),
    ("F2_dc_breakout_4h_no_wreq",                 {"HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": False}),
    ("F2_dc_breakout_D",                          {"HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "D", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": True}),
    ("F2_dc_breakout_W",                          {"HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "W", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": False}),
    ("F2_dc_breakout_4h_thr01",                   {"HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT": 0.1, "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": True}),
    ("F3_w_reversal_exit_w_only",                 {"HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": True, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": False}),
    ("F3_w_reversal_exit_w_and_d",                {"HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": True, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": True}),
    # Pairs
    ("F1+F3",                                     {"HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": True, "HTF_W_M_ALIGN_TRADIER_REQUIRED": 2, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": True, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": True}),
    ("F2+F3",                                     {"HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": True, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": True, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": True}),
    ("F1+F2_4h",                                  {"HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": True, "HTF_W_M_ALIGN_TRADIER_REQUIRED": 2, "HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": True}),
    # All three
    ("ALL_F1F2F3",                                {"HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": True, "HTF_W_M_ALIGN_TRADIER_REQUIRED": 2, "HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": True, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": True, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": True}),
    ("ALL_F1F2F3_softer",                         {"HTF_W_M_ALIGN_GATE_TRADIER_ENABLED": True, "HTF_W_M_ALIGN_TRADIER_REQUIRED": 1, "HTF_DC_BREAKOUT_TRADIER_ENABLED": True, "HTF_DC_BREAKOUT_TRADIER_TF": "4h", "HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT": False, "HTF_W_REVERSAL_EXIT_TRADIER_ENABLED": True, "HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D": False}),
]


def run_one(syms, npz_dir, label, base_overrides, htf_overrides, max_bars, start_date):
    cfg = QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.MAX_BARS = max_bars
    cfg.MODE = "tradier"
    apply_overrides(cfg, base_overrides)
    # Phase 4 ALL ON (best from R3)
    cfg.USE_LIVE_EVALUATOR_VEC = True
    cfg.APPLY_QTY_PIPELINE_TO_PNL = True
    cfg.USE_PROCESS_POSITION_EXIT_GATES = True
    # Tight exits (best from R3 / 0.3752)
    cfg.WT_4H_VEL_EXIT_K_EXTREME_HIGH = 70.0
    cfg.DC_HOPELESS_EXIT_MIN_AGE_S = 300.0
    cfg.WT_PERCENTILE_EXIT_OB_D = 80.0
    cfg.E_3_USE_WT_STRUCTURE_EXIT_MODE = 2
    # NEW HTF flags under test
    apply_overrides(cfg, htf_overrides)
    t1 = time.time()
    res = simulate(iter_npz("tradier", syms, start_date, npz_dir=npz_dir), cfg, capital=10000.0)
    elapsed = time.time() - t1
    n_syms = len(syms)
    years = float(res.get('years', 0) or 0)
    if years <= 0:
        years = (max_bars * 5 / 60.0 / 24.0) / 365.25
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
        "htf_overrides_json": json.dumps(htf_overrides),
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2024-01-01')
    ap.add_argument('--max-bars', type=int, default=100000)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    npz_dir = _resolve_npz_dir("")
    syms = _tradier_symbols(npz_dir)
    base_overrides = load_baseline_overrides(TRADIER_BASELINE_PATH)
    print(f"[HTFPORT] tradier: {len(syms)} syms, {args.max_bars} bars, {len(base_overrides)} baseline overrides", flush=True)

    out_path = args.out or f"data/sweep_results/phase4_htfport_tradier_{int(time.time())}.csv"
    print(f"[HTFPORT] output → {out_path}", flush=True)

    results = []
    ref_pool = None
    for n, (label, htf_over) in enumerate(HTF_COMBOS, 1):
        try:
            row = run_one(syms, npz_dir, label, base_overrides, htf_over, args.max_bars, args.start)
            metrics_guard.write_sharpe_row(Path(out_path), row, mode="tradier", append=True)
            results.append(row)
            if label == "ref_baseline_alone":
                ref_pool = row['pool_sharpe']
            delta = (row['pool_sharpe'] - ref_pool) if ref_pool is not None else 0.0
            print(f"[{n}/{len(HTF_COMBOS)}] {label:<35} pool={row['pool_sharpe']:>7.4f} dd={row['max_dd_pct']:>5.2f}% trades={row['trades']:>6} delta={delta:+.4f}", flush=True)
        except metrics_guard.FakeMetricRefused as e:
            print(f"[{label}] REFUSED: {e}", flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[{label}] ERROR: {type(e).__name__}: {e}", flush=True)

    # Top 10 + activation analysis
    results.sort(key=lambda x: -x['pool_sharpe'])
    print("\n" + "=" * 100)
    print(f"PHASE 4 HTF PORT — RANKED (top {min(10, len(results))} of {len(results)})")
    print(f"{'#':>3} {'pool':>8} {'dd%':>6} {'trades':>7} {'syms':>5}  {'label':<30}  {'delta_vs_ref':>13}")
    for i, r in enumerate(results[:10]):
        delta = (r['pool_sharpe'] - ref_pool) if ref_pool is not None else 0.0
        print(f"{i+1:>3} {r['pool_sharpe']:>8.4f} {r['max_dd_pct']:>6.2f} {r['trades']:>7} {r['n_syms']:>5}  {r['label']:<30}  {delta:>+13.4f}")

    if ref_pool is not None:
        print(f"\nREFERENCE (no new HTF flags): pool={ref_pool:.4f}")
        print(f"ACTIVATION CRITERIA: pool ≥ {ref_pool + 0.05:.4f} AND dd < 25%")
        promote = [r for r in results if (r['pool_sharpe'] - ref_pool) >= 0.05 and r['max_dd_pct'] < 25.0 and r['label'] != 'ref_baseline_alone']
        print(f"\nCANDIDATES TO PROMOTE (default ON): {len(promote)}")
        for r in promote:
            print(f"  {r['label']:<35} pool={r['pool_sharpe']:.4f} (+{r['pool_sharpe']-ref_pool:.4f}) dd={r['max_dd_pct']:.2f}% trades={r['trades']}")


if __name__ == '__main__':
    main()
