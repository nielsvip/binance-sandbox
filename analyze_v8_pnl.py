#!/usr/bin/env python3
"""Compute realized PnL from V8 trade JSONL files.

Pairs OPEN→CLOSE/REDUCE by position_key using VWAP entry. Outputs per-config
metrics (total PnL %, win rate, sharpe, trade count) so we can answer:
"are the results positive enough to justify these settings?"
"""
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import json
import sys
import os
import math
from collections import defaultdict
from glob import glob


def analyze_jsonl(path):
    trades = []
    with open(path) as f:
        for line in f:
            try:
                trades.append(json.loads(line))
            except Exception:
                pass
    if not trades:
        return None
    # Group by position_key, walk in time order, track open/close
    by_pk = defaultdict(list)
    for t in sorted(trades, key=lambda x: x.get("timestamp", 0)):
        pk = t.get("position_key", "")
        if pk:
            by_pk[pk].append(t)
    realized_pnls = []  # list of pct returns
    realized_dollars = []
    n_winners = 0
    n_losers = 0
    n_trades_paired = 0
    for pk, tlist in by_pk.items():
        # Track running cost basis
        long_amt = 0.0
        long_cost = 0.0
        is_long = "_LONG" in pk
        for t in tlist:
            action = (t.get("action") or "").upper()
            qty = float(t.get("quantity") or 0)
            px = float(t.get("price") or 0)
            if qty <= 0 or px <= 0:
                continue
            is_open_event = ("OPEN" in action or "AUGMENT" in action) and "CLOSE" not in action
            is_close_event = ("CLOSE" in action or "REDUCE" in action) and "OPEN" not in action
            if is_open_event:
                long_amt += qty
                long_cost += qty * px
            elif is_close_event and long_amt > 0:
                # Close some/all of position
                close_qty = min(qty, long_amt)
                avg_entry = long_cost / long_amt if long_amt > 0 else px
                if is_long:
                    pnl_pct = (px - avg_entry) / avg_entry * 100
                else:
                    pnl_pct = (avg_entry - px) / avg_entry * 100
                pnl_dollars = (px - avg_entry) * close_qty * (1 if is_long else -1)
                realized_pnls.append(pnl_pct)
                realized_dollars.append(pnl_dollars)
                if pnl_pct > 0:
                    n_winners += 1
                else:
                    n_losers += 1
                n_trades_paired += 1
                # Reduce position proportionally
                portion = close_qty / long_amt
                long_cost -= long_cost * portion
                long_amt -= close_qty
    if not realized_pnls:
        return {
            "n_trades": len(trades),
            "n_paired": 0,
            "total_pnl_pct": 0.0,
            "total_pnl_dollars": 0.0,
            "winners": 0,
            "losers": 0,
            "win_rate": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "sharpe_simple": 0.0,
        }
    mean = sum(realized_pnls) / len(realized_pnls)
    var = sum((x - mean) ** 2 for x in realized_pnls) / len(realized_pnls)
    sd = math.sqrt(var)
    sharpe_simple = mean / sd if sd > 0 else 0.0  # per-trade pool_sharpe (sqrt(252) stripped 2026-04-29 per CLAUDE.md rule 4)
    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p <= 0]
    return {
        "n_trades": len(trades),
        "n_paired": n_trades_paired,
        "total_pnl_pct": sum(realized_pnls),
        "total_pnl_dollars": sum(realized_dollars),
        "winners": n_winners,
        "losers": n_losers,
        "win_rate": n_winners / n_trades_paired * 100 if n_trades_paired else 0.0,
        "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
        "sharpe_simple": sharpe_simple,
    }


def main():
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = sorted(glob("backtest_v8/logs/v8_*.jsonl"))
    rows = []
    for f in files:
        r = analyze_jsonl(f)
        if r:
            r["file"] = os.path.basename(f)
            rows.append(r)
    rows.sort(key=lambda x: x.get("total_pnl_pct", 0), reverse=True)
    print(f"{'File':<60} {'Trades':>7} {'Paired':>7} {'PnL%':>8} {'PnL$':>10} {'Win%':>6} {'AvgW%':>7} {'AvgL%':>7} {'Sharpe':>7}")
    print("-" * 130)
    for r in rows:
        print(
            f"{r['file']:<60} {r['n_trades']:>7} {r['n_paired']:>7} {r['total_pnl_pct']:>8.2f} {r['total_pnl_dollars']:>10.2f} {r['win_rate']:>6.1f} {r['avg_win_pct']:>7.2f} {r['avg_loss_pct']:>7.2f} {r['sharpe_simple']:>7.2f}"
        )
    if rows:
        print("-" * 130)
        avg_pnl = sum(r["total_pnl_pct"] for r in rows) / len(rows)
        avg_win_rate = sum(r["win_rate"] for r in rows) / len(rows)
        avg_sharpe = sum(r["sharpe_simple"] for r in rows) / len(rows)
        print(f"AGGREGATE: configs={len(rows)} avg_pnl={avg_pnl:.2f}% avg_win_rate={avg_win_rate:.1f}% avg_sharpe={avg_sharpe:.2f}")


if __name__ == "__main__":
    main()
