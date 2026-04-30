#!/usr/bin/env python3
"""Extended gate ablation — tests COMBINATIONS of best findings across ALL 48 symbols.
Reuses the EXACT same _replay_with_overrides from gate_ablation.py (known working)."""
import asyncio, json, math, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from backtest_rate_real import (precompute_indicators_all_tfs, StubTrackerManager,
                                 StubPosition, KLINES_DIR, RESULTS_DIR, FEE, WARMUP, MIN_TRADES)

# Import the EXACT working replay from gate_ablation
from backtest_gate_ablation import _replay_with_overrides, run_single_ablation

SYMBOLS_FILE = KLINES_DIR.parent / "backtest_48_symbols.json"
if SYMBOLS_FILE.exists():
    ALL_SYMBOLS = json.load(open(SYMBOLS_FILE))
else:
    ALL_SYMBOLS = json.load(open(SCRIPT_DIR / "backtest_48_symbols.json"))


def main():
    print("=" * 80)
    print("  EXTENDED GATE ABLATION — Combo tests across ALL 48 symbols")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80, flush=True)
    ablations = [
        ("BASELINE", {}),
        ("TIGHT MTS (b=15,eq=8)", {"MTS_BOTTOM_MIN": 15.0, "MTS_ENTRY_QUALITY_MIN": 8.0}),
        ("TIGHT MTS + NO_DEAD_GATES", {"MTS_BOTTOM_MIN": 15.0, "MTS_ENTRY_QUALITY_MIN": 8.0, "ADX_REGIME_FILTER_ENABLED": False, "MOMENTUM_FADE_ENABLED": False}),
        ("SCORE>=20 (higher bar)", {"ENTRY_SCORE_MIN": 20}),
        ("TIGHT MTS + SCORE>=20", {"MTS_BOTTOM_MIN": 15.0, "MTS_ENTRY_QUALITY_MIN": 8.0, "ENTRY_SCORE_MIN": 20}),
        ("MTS b=12,eq=6 (medium)", {"MTS_BOTTOM_MIN": 12.0, "MTS_ENTRY_QUALITY_MIN": 6.0}),
    ]
    all_results = []
    start = time.time()
    for name, overrides in ablations:
        print(f"\n--- {name} ---", flush=True)
        t0 = time.time()
        result = run_single_ablation(ALL_SYMBOLS, name, overrides)
        elapsed = time.time() - t0
        result["elapsed"] = round(elapsed, 0)
        all_results.append(result)
        syms = result.get("symbols", 0)
        med = result.get("median_sharpe", 0)
        print(f"  => Sharpe={result['sharpe']:.3f} median={med:.3f} trades={result['trades']} PnL={result['pnl']:.1f}% WR={result['wr']:.1f}% ({syms} symbols, {elapsed:.0f}s)", flush=True)
    total = time.time() - start
    print(f"\n{'=' * 80}")
    print(f"  EXTENDED ABLATION RESULTS ({total:.0f}s)")
    print(f"{'=' * 80}")
    print(f"  {'Ablation':<35s} {'Sharpe':>7s} {'Median':>7s} {'Trades':>7s} {'PnL%':>9s} {'WR%':>5s} {'Syms':>4s}")
    print(f"  {'-' * 75}")
    bl = all_results[0]
    for r in all_results:
        ds = f"({r['sharpe'] - bl['sharpe']:+.3f})" if r != bl else ""
        marker = " ***" if r != bl and r["sharpe"] > bl["sharpe"] + 0.3 else ""
        med = r.get("median_sharpe", 0)
        print(f"  {r['name']:<35s} {r['sharpe']:>7.3f} {med:>7.3f} {r['trades']:>7d} {r['pnl']:>9.1f} {r['wr']:>5.1f} {r.get('symbols',0):>4d}  {ds}{marker}", flush=True)
    out = RESULTS_DIR / f"gate_ablation_extended_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
