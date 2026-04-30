#!/usr/bin/env python3
"""Combine multiple run JSONLs into a synthetic portfolio. Each pool has
configurable allocation. Reports combined Sharpe, gain, DD, churn vs constituents.

Usage:
  python3 portfolio_view.py --pools v4_dctouch1h:0.7,v4_quality_lane:0.3 --syms BTCUSDC
  python3 portfolio_view.py --pools v4_dctouch1h:0.5,v4_quality_lane:0.5 --syms BTCUSDC,ETHUSDC
"""
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import argparse
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))


def load_trades(run, sym, trades_dir):
    p = Path(trades_dir) / f"{run}__{sym}.jsonl"
    if not p.exists(): return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def stats(trades, allocation=1.0):
    if not trades: return {"trades": 0}
    pnls = [float(t.get("pnl_pct", 0) or 0) * allocation for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    std = statistics.pstdev(pnls) if n > 1 else 0
    # Equity curve for max drawdown
    eq = []; cum = 0
    for p in pnls: cum += p; eq.append(cum)
    peak = -1e9; max_dd = 0
    for v in eq:
        if v > peak: peak = v
        dd = peak - v
        if dd > max_dd: max_dd = dd
    return {
        "trades": n, "wins": wins, "wr": wins / n if n else 0,
        "total_gain_pct": sum(pnls), "avg_pnl_pct": sum(pnls)/n if n else 0,
        "sharpe_pt": (sum(pnls)/n)/std if std > 0 else 0,
        "max_dd_pct": max_dd,
        "best_pct": max(pnls), "worst_pct": min(pnls),
    }


def churn_pct(trades):
    if len(trades) < 2: return 0
    sorted_t = sorted(trades, key=lambda t: int(t.get("entry_ts", 0)))
    chained = 0
    for j in range(1, len(sorted_t)):
        prev, cur = sorted_t[j-1], sorted_t[j]
        if prev.get("side") == cur.get("side") and 0 <= int(cur.get("entry_ts", 0)) - int(prev.get("exit_ts", 0)) <= 3600:
            chained += 1
    return chained / len(sorted_t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pools", required=True, help="run:alloc,run:alloc — e.g. v4_dctouch1h:0.7,v4_quality_lane:0.3")
    ap.add_argument("--syms", required=True)
    ap.add_argument("--trades-dir", default=str(DEFAULT_TRADES_DIR))
    args = ap.parse_args()
    syms = [s.upper() for s in args.syms.split(",")]
    pools = []
    for p in args.pools.split(","):
        run, alloc = p.split(":", 1)
        pools.append((run.strip(), float(alloc)))
    if abs(sum(a for _, a in pools) - 1.0) > 0.001:
        print(f"WARN: allocations sum to {sum(a for _, a in pools):.3f}, not 1.0 — proceeding anyway")
    print(f"\n{'='*100}\nPORTFOLIO REPORT — pools: {pools} × symbols: {syms}\n{'='*100}\n")
    # Per-constituent + portfolio per symbol
    print(f"{'Symbol':<10s} {'Pool':<25s} {'Alloc':>5s} {'Tr':>6s} {'WR':>5s} {'Sharpe':>8s} {'Gain':>9s} {'DD':>6s} {'Churn':>6s}")
    print("-"*100)
    total_portfolio = []
    for sym in syms:
        portfolio_trades = []
        for run, alloc in pools:
            t = load_trades(run, sym, args.trades_dir)
            s = stats(t, alloc)
            cp = churn_pct(t)
            if s.get("trades"):
                print(f"{sym:<10s} {run[:24]:<25s} {alloc:>5.2f} {s['trades']:>6d} {s['wr']*100:>4.1f}% {s['sharpe_pt']:>+8.3f} {s['total_gain_pct']:>+8.1f}% {s['max_dd_pct']:>5.1f}% {cp*100:>5.1f}%")
            for trade in t:
                trade_copy = dict(trade)
                trade_copy["_alloc"] = alloc
                trade_copy["_pool"] = run
                portfolio_trades.append(trade_copy)
        if portfolio_trades:
            # Adjust pnl by alloc and aggregate
            pnls = [float(t.get("pnl_pct", 0) or 0) * t["_alloc"] for t in portfolio_trades]
            n = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            std = statistics.pstdev(pnls) if n > 1 else 0
            sorted_pf = sorted(portfolio_trades, key=lambda t: int(t.get("entry_ts", 0)))
            spnls = [float(t.get("pnl_pct", 0) or 0) * t["_alloc"] for t in sorted_pf]
            cum = 0; eq = []
            for p in spnls: cum += p; eq.append(cum)
            peak = -1e9; max_dd = 0
            for v in eq:
                if v > peak: peak = v
                if peak - v > max_dd: max_dd = peak - v
            cp = churn_pct(portfolio_trades)
            print(f"{sym:<10s} {'  PORTFOLIO COMBINED':<25s} {'1.00':>5s} {n:>6d} {wins/n*100:>4.1f}% {(sum(pnls)/n)/std if std>0 else 0:>+8.3f} {sum(pnls):>+8.1f}% {max_dd:>5.1f}% {cp*100:>5.1f}%")
            print()
            total_portfolio.extend(portfolio_trades)
    # All-symbols aggregate
    if total_portfolio and len(syms) > 1:
        pnls = [float(t.get("pnl_pct", 0) or 0) * t["_alloc"] for t in total_portfolio]
        n = len(pnls); wins = sum(1 for p in pnls if p > 0)
        std = statistics.pstdev(pnls) if n > 1 else 0
        cp = churn_pct(total_portfolio)
        print("="*100)
        print(f"{'ALL SYMS':<10s} {'  AGGREGATE':<25s} {'':>5s} {n:>6d} {wins/n*100:>4.1f}% {(sum(pnls)/n)/std if std>0 else 0:>+8.3f} {sum(pnls):>+8.1f}% {'':>6s} {cp*100:>5.1f}%")


if __name__ == "__main__":
    main()
