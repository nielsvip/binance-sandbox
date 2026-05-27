# -*- coding: utf-8 -*-
"""uve_backtest_harness.py — Backtest Harness for UVE Engine.

Loads NPZ indicator files, runs vectorized simulations, and writes audit-compliant Sharpe rows via metrics_guard.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import metrics_guard
from v8_vec_sweep import load_npz
from uve_engine import simulate_uve

def run_uve_backtest(symbols: List[str], mode: str, is_long: bool, start_ts: int | None = None) -> Dict[str, Any]:
    returns_by_sym = {}
    total_trades = 0
    all_returns = []
    for sym in symbols:
        try:
            npz, ts = load_npz(sym, mode, start_ts=start_ts)
            res = simulate_uve(npz, is_long, mode)
            rets = res["returns"]
            returns_by_sym[sym] = rets
            all_returns.extend(rets)
            total_trades += len(rets)
        except Exception as e:
            sys.stderr.write(f"Error running backtest on {sym}: {e}\n")
    if not all_returns:
        return {"pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0, "gain_per_yr": 0.0, "gain_sym_yr": 0.0, "trades": 0, "max_dd_pct": 0.0, "n_syms": len(symbols), "years": 1.0}
    # Calculate peak-to-trough drawdown from cumulative returns
    cum_ret, max_dd, peak = 0.0, 0.0, 0.0
    for r in all_returns:
        cum_ret += r
        peak = max(peak, cum_ret)
        max_dd = max(max_dd, peak - cum_ret)
    years = 1.0  # Default to 1 year for diagnostic/smoke test purposes
    metrics = metrics_guard.standard_metric_set(returns_by_sym, years)
    metrics["max_dd_pct"] = max_dd
    return metrics

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="Run a quick smoke test on BTCUSDC")
    ap.add_argument("--mode", default="crypto", choices=["crypto", "tradier"])
    ap.add_argument("--syms", default="BTCUSDC,ETHUSDC,SOLUSDC")
    ap.add_argument("--long", action="store_true", default=True, help="Force LONG side")
    args = ap.parse_args()
    if args.smoke:
        sym_list = ["BTCUSDC"] if args.mode == "crypto" else ["NVDA"]
        print(f"Running UVE Engine smoke test on {sym_list} (mode={args.mode})...")
        res = run_uve_backtest(sym_list, args.mode, args.long)
        print(f"UVE Smoke Test Result:")
        print(f"  pool_sharpe    = {res['pool_sharpe']:.4f}")
        print(f"  sym_sharpe     = {res['sym_sharpe']:.4f}")
        print(f"  avg_gain_trade = {res['avg_gain_trade']:.2f}%")
        print(f"  trades         = {res['trades']}")
        print(f"  max_dd_pct     = {res['max_dd_pct']:.2f}%")
        # Save validated Sharpe row
        csv_path = BASE_DIR / "data" / "sweep_results" / "uve_smoke_results.csv"
        row = {
            "pool_sharpe": res["pool_sharpe"],
            "sym_sharpe": res["sym_sharpe"],
            "avg_gain_trade": res["avg_gain_trade"],
            "gain_per_yr": res["gain_per_yr"],
            "gain_sym_yr": res["gain_sym_yr"],
            "trades": res["trades"],
            "max_dd_pct": res["max_dd_pct"],
            "n_syms": res["n_syms"],
            "years": res["years"],
            "engine": "uve_engine"
        }
        metrics_guard.write_sharpe_row(csv_path, row, mode=args.mode)
        print(f"Saved audit-compliant result row to {csv_path}")

if __name__ == "__main__":
    main()
