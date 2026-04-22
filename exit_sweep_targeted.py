"""
Targeted exit-mode sweep on the current best baseline (crypto_0p7574.json).
Tests specific exit combinations found common across top autonomous candidates.

Run on S1 (12 syms for speed):
  python3 exit_sweep_targeted.py --npz-dir /path/to/npz --baseline-json /path/to/crypto_0p7574.json

Then best configs get validated on 48 syms automatically.
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

CRYPTO_12 = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT",
    "LINKUSDT", "ADAUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT",
]

SWEEP_VARIANTS = [
    ("baseline", {}),
    ("wt_momentum_exit", {"WT_MOMENTUM_EXIT_ENABLED": True}),
    ("wt_struct_exit", {"WT_STRUCT_EXIT_ENABLED": True}),
    ("wt_accel_exit", {"WT_ACCEL_EXIT_ENABLED": True}),
    ("wt_comp_delta_exit", {"WT_COMP_DELTA_EXIT_ENABLED": True}),
    ("all_wt_exits", {
        "WT_MOMENTUM_EXIT_ENABLED": True, "WT_STRUCT_EXIT_ENABLED": True,
        "WT_ACCEL_EXIT_ENABLED": True, "WT_COMP_DELTA_EXIT_ENABLED": True,
    }),
    ("partial_exit_full", {"PARTIAL_EXIT_ENABLED": True, "PARTIAL_EXIT_FRAC": 1.0}),
    ("partial_exit_half", {"PARTIAL_EXIT_ENABLED": True, "PARTIAL_EXIT_FRAC": 0.5}),
    ("exit_scorer_on", {"EXIT_SCORER_ENABLED": True}),
    ("all_wt_exits_plus_partial", {
        "WT_MOMENTUM_EXIT_ENABLED": True, "WT_STRUCT_EXIT_ENABLED": True,
        "WT_ACCEL_EXIT_ENABLED": True, "WT_COMP_DELTA_EXIT_ENABLED": True,
        "PARTIAL_EXIT_ENABLED": True, "PARTIAL_EXIT_FRAC": 1.0,
    }),
    ("local_extremes_scorer", {"LOCAL_EXTREMES_SCORER_ENABLED": True}),
    ("strength_filter_2", {"STRENGTH_FILTER_ENABLED": True, "STRENGTH_MIN_SCORE": 2.0}),
    ("strength_filter_3", {"STRENGTH_FILTER_ENABLED": True, "STRENGTH_MIN_SCORE": 3.0}),
    ("tf_alignment_6", {"TF_ALIGNMENT_MIN_TOTAL": 6}),
    ("tf_alignment_6_strict", {"TF_ALIGNMENT_MIN_TOTAL": 6, "TF_FOCUS_ENTRY_HARD_GATE": True}),
    ("wt_momentum_thresh_1", {"WT_MOMENTUM_EXIT_ENABLED": True, "WT_MOMENTUM_EXIT_THRESHOLD": 1}),
    ("all_exits_tf6_strength", {
        "WT_MOMENTUM_EXIT_ENABLED": True, "WT_STRUCT_EXIT_ENABLED": True,
        "WT_ACCEL_EXIT_ENABLED": True, "WT_COMP_DELTA_EXIT_ENABLED": True,
        "PARTIAL_EXIT_ENABLED": True, "PARTIAL_EXIT_FRAC": 1.0,
        "TF_ALIGNMENT_MIN_TOTAL": 6, "TF_FOCUS_ENTRY_HARD_GATE": True,
        "STRENGTH_FILTER_ENABLED": True, "STRENGTH_MIN_SCORE": 2.5,
        "LOCAL_EXTREMES_SCORER_ENABLED": True,
    }),
    ("mi_exit_on", {"MI_EXIT_ENABLED": True}),
    ("satoshit_exit_on", {"SATOSHIT_EXIT_ENABLED": True}),
    ("wt_vel_mtf_1", {"WT_VEL_MTF_EXIT_MIN_TFS": 1, "WT_VEL_MTF_EXIT_THRESHOLD": -0.125}),
    ("stoch_cross_3m_exit", {"STOCH_CROSS_3M_EXIT_ENABLED": True}),
    ("regime_adaptive", {"REGIME_ADAPTIVE_ENABLED": True}),
]


def load_baseline(path, mode="crypto"):
    with open(path) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def build_cfg(base_overrides, extra_overrides, mode="crypto"):
    cfg = QuickConfig()
    cfg.MODE = mode
    cfg.LTF = "3m" if mode == "crypto" else "5m"
    for k, v in base_overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    for k, v in extra_overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def run_test(cfg, symbols, npz_dir, start_date, mode):
    stream = iter_npz(mode=mode, symbols=symbols, start_date=start_date, npz_dir=npz_dir)
    t0 = time.time()
    res = simulate(stream, cfg)
    return res, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="crypto")
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--baseline-json", required=True)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--symbols-json", default="")
    ap.add_argument("--out", default="exit_sweep_results.csv")
    ap.add_argument("--validate-48", action="store_true", help="Also test winners on 48 symbols")
    ap.add_argument("--symbols-48-json", default="")
    args = ap.parse_args()

    base_overrides = load_baseline(args.baseline_json, args.mode)
    print(f"Baseline loaded: {len(base_overrides)} keys")

    if args.symbols_json:
        syms_12 = json.load(open(args.symbols_json))
    else:
        syms_12 = CRYPTO_12

    syms_48 = []
    if args.validate_48 and args.symbols_48_json:
        syms_48 = json.load(open(args.symbols_48_json))

    results = []
    print(f"\nRunning {len(SWEEP_VARIANTS)} exit variants on {len(syms_12)} symbols...")
    print(f"{'Variant':<40} {'Sharpe':>7} {'Trades':>7} {'Gain%':>8} {'DD%':>6} {'Time':>6}")
    print("-" * 80)

    for name, overrides in SWEEP_VARIANTS:
        cfg = build_cfg(base_overrides, overrides, args.mode)
        try:
            res, elapsed = run_test(cfg, syms_12, args.npz_dir, args.start, args.mode)
            ps = res.get("pool_sharpe", 0.0)
            tr = res.get("trades", 0)
            gain = res.get("accumulated_gain_pct", 0.0)
            dd = res.get("max_dd_pct", 0.0)
            print(f"{name:<40} {ps:>7.4f} {tr:>7d} {gain:>8.1f} {dd:>6.2f} {elapsed:>5.0f}s")
            results.append({
                "variant": name, "pool_sharpe": ps, "trades": tr,
                "gain_pct": round(gain, 1), "dd_pct": round(dd, 2),
                "elapsed_s": round(elapsed, 1), "overrides_count": len(overrides),
            })
        except Exception as e:
            print(f"{name:<40} ERROR: {e}")

    results.sort(key=lambda r: r["pool_sharpe"], reverse=True)
    print(f"\nTop 5 by Sharpe:")
    for r in results[:5]:
        print(f"  {r['variant']}: sharpe={r['pool_sharpe']} trades={r['trades']} gain={r['gain_pct']}%")

    if args.validate_48 and syms_48:
        print(f"\nValidating top 5 on 48 symbols...")
        for r in results[:5]:
            name = r["variant"]
            overrides = dict(next(v for n, v in SWEEP_VARIANTS if n == name))
            cfg = build_cfg(base_overrides, overrides, args.mode)
            try:
                res48, el = run_test(cfg, syms_48, args.npz_dir, args.start, args.mode)
                ps48 = res48.get("pool_sharpe", 0.0)
                tr48 = res48.get("trades", 0)
                print(f"  {name}: 12sym={r['pool_sharpe']:.4f} → 48sym={ps48:.4f} trades={tr48} {el:.0f}s")
                r["sharpe_48"] = ps48
                r["trades_48"] = tr48
            except Exception as e:
                print(f"  {name}: ERROR {e}")

    with open(args.out, "w", newline="") as f:
        fieldnames = ["variant", "pool_sharpe", "trades", "gain_pct", "dd_pct", "elapsed_s", "overrides_count"]
        if args.validate_48:
            fieldnames += ["sharpe_48", "trades_48"]
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    print(f"\nResults saved to {args.out}")


if __name__ == "__main__":
    main()
