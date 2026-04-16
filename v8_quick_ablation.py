#!/usr/bin/env python3
"""
v8_quick_ablation.py — Systematic one-by-one function ablation.

Starts from CORE (DC breakout + WT gates only), then adds each feature
individually. Keeps it if Sharpe improves, ditches if same/worse.

Reports: which functions help, which are dead weight, annotates config
switches with KEEP/DITCH recommendations.

Usage:
  python v8_quick_ablation.py --mode crypto --symbols fast --start 2022-01-01
  python v8_quick_ablation.py --mode tradier --symbols fast --start 2024-01-01
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from v8_quick_engine import (
    QuickConfig, load_npz, simulate, compute_entry_signals, compute_exit_signals,
    _safe, _safeb, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER
)


def run_config(stores, cfg, label, mode):
    if mode == "tradier" and cfg.STRUCTURAL_RANGE_SHIFT_TF == "dc_4h":
        cfg.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    t0 = time.time()
    result = simulate(stores, cfg, 10000.0)
    elapsed = time.time() - t0
    result["label"] = label
    result["elapsed"] = round(elapsed, 1)
    return result


def ablation_suite(stores, mode):
    """Test each feature individually against a CORE baseline."""
    results = []

    # === CORE: minimal entry/exit (DC breakout + WT 2/3 + K3M floor + delta exit + vel exit + SRS) ===
    core = QuickConfig()
    core.CT_WT_VELOCITY_GATE_ENABLED = False
    core.CT_DC_CROSSOVER_SKIP_ENABLED = False
    core.CT_15M_MOMENTUM_GATE_ENABLED = False
    core.CT_CHOP_4H_GATE_ENABLED = False
    core.CT_VOLUME_SURGE_GATE_ENABLED = False
    core.SATOSHIT_ENABLED = False
    core.DELTA_ENGINE_ENABLED = True
    core.DELTA_ENTRY_ENABLED = False
    core.RZ_EXIT_ENABLED = False
    core.STRUCTURAL_RANGE_SHIFT_EXIT = False
    if mode == "tradier":
        core.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    results.append(run_config(stores, core, "CORE (DC_break + WT_2of3 + delta_exit + vel_exit)", mode))

    # === Add each feature one by one ===
    features = [
        ("CT_WT_VELOCITY (BC_170)", {"CT_WT_VELOCITY_GATE_ENABLED": True}),
        ("CT_DC_CROSSOVER_SKIP (BC_172)", {"CT_DC_CROSSOVER_SKIP_ENABLED": True}),
        ("CT_15M_MOMENTUM (BC_171)", {"CT_15M_MOMENTUM_GATE_ENABLED": True}),
        ("CT_CHOP_4H (BC_173)", {"CT_CHOP_4H_GATE_ENABLED": True}),
        ("CT_VOLUME_SURGE (BC_174)", {"CT_VOLUME_SURGE_GATE_ENABLED": True}),
        ("SATOSHIT_ENTRY+EXIT", {"SATOSHIT_ENABLED": True}),
        ("DELTA_ENTRY (RZ_BUY/SELL)", {"DELTA_ENTRY_ENABLED": True}),
        ("RZ_EXIT (zone reversal)", {"RZ_EXIT_ENABLED": True}),
        ("SRS_EXIT (structural range)", {"STRUCTURAL_RANGE_SHIFT_EXIT": True}),
    ]

    for label, overrides in features:
        cfg = QuickConfig()
        cfg.CT_WT_VELOCITY_GATE_ENABLED = False
        cfg.CT_DC_CROSSOVER_SKIP_ENABLED = False
        cfg.CT_15M_MOMENTUM_GATE_ENABLED = False
        cfg.CT_CHOP_4H_GATE_ENABLED = False
        cfg.CT_VOLUME_SURGE_GATE_ENABLED = False
        cfg.SATOSHIT_ENABLED = False
        cfg.DELTA_ENGINE_ENABLED = True
        cfg.DELTA_ENTRY_ENABLED = False
        cfg.RZ_EXIT_ENABLED = False
        cfg.STRUCTURAL_RANGE_SHIFT_EXIT = False
        if mode == "tradier":
            cfg.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
        for k, v in overrides.items():
            setattr(cfg, k, v)
        results.append(run_config(stores, cfg, f"CORE + {label}", mode))

    # === ALL features ON ===
    all_on = QuickConfig()
    if mode == "tradier":
        all_on.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    results.append(run_config(stores, all_on, "ALL ON", mode))

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    parser.add_argument("--symbols", type=str, default="fast")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--npz-dir", type=str, default="")
    args = parser.parse_args()

    symbols = None
    if args.symbols == "fast":
        symbols = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    stores = load_npz(args.mode, symbols, args.start, args.npz_dir)
    if not stores:
        print("No data loaded")
        return

    print(f"\n{'='*90}")
    print(f"  V8 QUICK ABLATION — {args.mode} | {len(stores)} symbols | start={args.start}")
    print(f"{'='*90}\n")

    results = ablation_suite(stores, args.mode)
    core = results[0]

    print(f"{'Label':<45} {'Sharpe':>8} {'PnL':>12} {'Trades':>8} {'WR':>6} {'ΔSharpe':>9} {'Verdict':>8}")
    print("-" * 100)
    for r in results:
        delta_s = r["sharpe"] - core["sharpe"]
        if r["label"].startswith("CORE"):
            verdict = "BASE"
        elif r["label"] == "ALL ON":
            verdict = "COMBINED"
        elif delta_s > 0.005:
            verdict = "KEEP ✓"
        elif delta_s < -0.005:
            verdict = "DITCH ✗"
        else:
            verdict = "NOISE ~"
        print(f"{r['label']:<45} {r['sharpe']:>8.4f} {r['pnl']:>12.2f} {r['trades']:>8} {r['wr']:>5.1f}% {delta_s:>+9.4f} {verdict:>8}")

    # Write machine-readable results
    out_path = Path(__file__).parent / "data" / "sweep_alerts" / f"ablation_{args.mode}_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")

    # Config annotation suggestions
    print(f"\n{'='*60}")
    print("  CONFIG SWITCH ANNOTATIONS")
    print(f"{'='*60}")
    for r in results[1:-1]:
        delta_s = r["sharpe"] - core["sharpe"]
        switch_name = r["label"].replace("CORE + ", "").split(" (")[0].strip()
        if delta_s > 0.005:
            print(f"  {switch_name}: KEEP — Sharpe +{delta_s:.4f}")
        elif delta_s < -0.005:
            print(f"  {switch_name}: DITCH (leave OFF) — Sharpe {delta_s:+.4f}")
        else:
            print(f"  {switch_name}: NOISE (leave OFF for now) — Sharpe {delta_s:+.4f}")


if __name__ == "__main__":
    main()
