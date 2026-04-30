#!/usr/bin/env python3
"""v8_canonical_csv.py — recompute the CLAUDE.md canonical 9-field Sharpe row
from a backtest_v8_engine sweep output (which writes per-trade JSONLs to
V8_TRADES_OUT_DIR).

The legacy backtest_v8_sweep CSV captures only the V8_RESULT line stats.
That violates rule 2 (every CSV in data/sweep_results/ MUST have:
pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr,
trades, max_dd_pct, n_syms, years).

This tool reads the per-trade JSONLs, runs metrics_guard.standard_metric_set,
and emits a canonical CSV via metrics_guard.write_sharpe_row() — which
REFUSES rows that fail any rule.

Usage:
  python3 v8_canonical_csv.py --runs-glob 'sysC_*' \\
      --trades-dir /tmp/v8_trades \\
      --start 2022-01-01 --end 2026-04-28 \\
      --out data/sweep_results/v8_system_combo_canonical_$(date +%Y%m%d_%H%M).csv
"""
from __future__ import annotations
import argparse
import datetime as dt
import glob
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import metrics_guard as mg


def trades_to_returns(jsonl_path: Path):
    rets = []
    pnl_sum = 0.0
    cum, peak, dd = 0.0, 0.0, 0.0
    ts_min, ts_max = None, None
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        p = float(t.get("pnl_pct", 0) or 0)
        rets.append(p / 100.0)  # convert pct → fraction (mg uses fractions)
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        et = int(t.get("entry_ts", 0) or 0)
        xt = int(t.get("exit_ts", 0) or 0)
        if et:
            ts_min = et if ts_min is None else min(ts_min, et)
            ts_max = xt if ts_max is None else max(ts_max, xt)
    return rets, dd, ts_min, ts_max


def aggregate_run(run_id: str, trades_dir: Path):
    """Pool returns across all symbols this run touched."""
    by_sym = {}
    dd_max = 0.0
    ts_min, ts_max = None, None
    for p in trades_dir.glob(f"{run_id}__*.jsonl"):
        sym = p.stem.partition("__")[2]
        if not sym:
            continue
        rets, dd, t_lo, t_hi = trades_to_returns(p)
        if not rets:
            continue
        by_sym[sym] = rets
        dd_max = max(dd_max, dd)
        if t_lo is not None:
            ts_min = t_lo if ts_min is None else min(ts_min, t_lo)
        if t_hi is not None:
            ts_max = t_hi if ts_max is None else max(ts_max, t_hi)
    if not by_sym:
        return None
    years = max(0.01, (ts_max - ts_min) / 86400.0 / 365.25) if (ts_min and ts_max) else 0.01
    metrics = mg.standard_metric_set(by_sym, years=years)
    metrics["max_dd_pct"] = dd_max
    return {
        "run_id": run_id,
        "n_syms": len(by_sym),
        "years": round(years, 2),
        **{k: v for k, v in metrics.items() if k != "n_syms" and k != "years"},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades-dir", default="/tmp/v8_trades")
    ap.add_argument("--runs-glob", default="vec_*",
                    help="run-id pattern; matches files {pattern}__*.jsonl")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument("--mode", default="crypto",
                    help="'crypto' or 'tradier' — chooses sample-floor (48 vs 100)")
    ap.add_argument("--metadata-csv", default="",
                    help="optional sweep CSV from backtest_v8_sweep.py to merge "
                         "label/overrides_json into the canonical output")
    args = ap.parse_args()

    trades_dir = Path(args.trades_dir)
    if not trades_dir.exists():
        print(f"FATAL: trades_dir not found: {trades_dir}"); sys.exit(2)
    pat = re.compile(args.runs_glob.replace("*", ".*"))
    run_ids = sorted({p.stem.partition("__")[0] for p in trades_dir.glob("*__*.jsonl")
                      if pat.match(p.stem.partition("__")[0])})
    print(f"[canonical] {len(run_ids)} runs match '{args.runs_glob}'")
    if not run_ids:
        sys.exit(0)

    # Optional metadata merge from sweep CSV
    meta_by_label = {}
    if args.metadata_csv and Path(args.metadata_csv).exists():
        import csv as _csv
        with open(args.metadata_csv) as f:
            for row in _csv.DictReader(f):
                meta_by_label[row.get("label", "")] = row

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_refused = 0
    for run_id in run_ids:
        agg = aggregate_run(run_id, trades_dir)
        if not agg:
            print(f"  SKIP {run_id}: no trades"); continue
        # Multiply per-trade-fraction-based metrics back to pct units for
        # human-friendly CSV. metrics_guard works in 'whatever-units-you-give-it'.
        avg_gain = agg["avg_gain_trade"] * 100.0
        gain_per_yr = agg["gain_per_yr"] * 100.0
        gain_sym_yr = agg["gain_sym_yr"] * 100.0
        total_gain = agg["total_gain_pct"] * 100.0
        row = {
            "pool_sharpe": agg["pool_sharpe"],
            "sym_sharpe": agg["sym_sharpe"],
            "avg_gain_trade": avg_gain,
            "gain_per_yr": gain_per_yr,
            "gain_sym_yr": gain_sym_yr,
            "trades": agg["trades"],
            "max_dd_pct": agg["max_dd_pct"],
            "n_syms": agg["n_syms"],
            "years": agg["years"],
            "run_id": run_id,
            "total_gain_pct": total_gain,
        }
        meta = meta_by_label.get(run_id) or {}
        if meta.get("overrides_json"):
            row["overrides_json"] = meta["overrides_json"]
        if meta.get("label"):
            row["label"] = meta["label"]
        try:
            mg.write_sharpe_row(out_path, row, mode=args.mode, append=True)
            n_written += 1
        except mg.FakeMetricRefused as e:
            print(f"  REFUSED {run_id}: {e}")
            n_refused += 1
    print(f"\n[canonical] wrote {n_written} rows, refused {n_refused}, into {out_path}")
    print(f"[canonical] every row passed metrics_guard — no Sharpe lies")


if __name__ == "__main__":
    main()
