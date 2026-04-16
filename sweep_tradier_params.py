#!/usr/bin/env python3
"""Quick ad-hoc tradier parameter sweep on top of V8 engine.

Rewritten 2026-04-16: V5 chain retired → this wrapper now dispatches each
config as an independent V8 engine subprocess via backtest_v8_sweep.run_config.
Results aggregated into data/sweep_results/sweep_tradier_params_results.json.

Add/edit entries in PARAMS below. Each entry is name → {CONFIG_KEY: value} dict.
Empty dict = baseline. Keys must be valid config_tradier.py or config.py attrs.
"""
import json
import sys
import time
import uuid
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

import backtest_v8_sweep as v8s  # run_config, SWEEP_DIR, config_name

SYMBOLS = "NVDA,AAPL,META,MSFT,XOM,GLD,USO"  # 7-symbol fast fixture
START = "2024-06-01"
CAPITAL = 70000.0
ACCOUNT = "trb"
MODE = "tradier"

# name → config overrides (empty dict = baseline)
PARAMS = {
    "BASELINE": {},
    # NOLOSS_MIN_PROFIT_PCT_TRADIER sweep
    "noloss_0.0pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.0},
    "noloss_0.5pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.5},
    "noloss_1.0pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 1.0},
    "noloss_3.0pct": {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 3.0},
    # RATIO_MULTIPLIER_TRADIER
    "ratio_2.0": {"RATIO_MULTIPLIER_TRADIER": 2.0},
    "ratio_3.0": {"RATIO_MULTIPLIER_TRADIER": 3.0},
    "ratio_3.5": {"RATIO_MULTIPLIER_TRADIER": 3.5},
    # Position sizing
    "sizing_400": {"START_POSITION_SIZE": 400},
    "sizing_600": {"START_POSITION_SIZE": 600},
    "sizing_800": {"START_POSITION_SIZE": 800},
    # MIN_GAIN_TO_BUY_AGGRESSIVELY (augment gate)
    "aug_gate_1.5pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 1.5},
    "aug_gate_3.0pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0},
    "aug_gate_5.0pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 5.0},
    # LS_RATIO bounds
    "ls_ratio_0.5_2.0": {"LS_RATIO_MIN_TRADIER": 0.50, "LS_RATIO_MAX_TRADIER": 2.00},
    "ls_ratio_0.7_1.5": {"LS_RATIO_MIN_TRADIER": 0.70, "LS_RATIO_MAX_TRADIER": 1.50},
}


def main():
    out_path = v8s.SWEEP_DIR / "sweep_tradier_params_results.json"
    v8s.SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    baseline_pnl = None
    t_all = time.time()
    for name, overrides in PARAMS.items():
        print(f"[{len(results)+1}/{len(PARAMS)}] {name} overrides={overrides}", flush=True)
        run_id = f"stp_{name}_{uuid.uuid4().hex[:6]}"
        args_tuple = (overrides, MODE, ACCOUNT, START, CAPITAL, run_id, "", SYMBOLS)
        try:
            r = v8s.run_config(args_tuple)
        except Exception as e:
            r = {"run_id": run_id, "name": name, "config": overrides, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "status": "error", "error": str(e), "elapsed": 0}
        r["display_name"] = name
        results.append(r)
        if name == "BASELINE":
            baseline_pnl = r.get("pnl", 0.0)
        pnl = r.get("pnl", 0.0)
        delta = "" if baseline_pnl is None else f" Δ={pnl-baseline_pnl:+.2f}"
        print(f"  → trades={r.get('trades',0)} pnl={pnl:+.2f}%{delta} sharpe={r.get('sharpe',0):.3f} status={r.get('status','?')} ({r.get('elapsed',0):.0f}s)", flush=True)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
    total_elapsed = time.time() - t_all
    print(f"\n{'='*110}")
    print(f"{'Name':30s} {'Trades':>7s} {'Sharpe':>8s} {'PnL%':>8s} {'Δvs baseline':>14s} {'Status':>10s}")
    print(f"{'='*110}")
    for r in sorted(results, key=lambda x: -x.get("sharpe", 0)):
        delta = r.get("pnl", 0) - (baseline_pnl or 0)
        sign = "+" if delta >= 0 else ""
        print(f"{r['display_name']:30s} {r.get('trades',0):7d} {r.get('sharpe',0):8.3f} {r.get('pnl',0):+7.2f}% {sign}{delta:+.2f}%{'':8s} {r.get('status','?'):>10s}")
    print(f"\nSaved to {out_path}")
    print(f"Total elapsed {total_elapsed:.0f}s across {len(PARAMS)} configs")


if __name__ == "__main__":
    main()
