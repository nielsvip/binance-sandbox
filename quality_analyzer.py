#!/usr/bin/env python3
"""Trade-quality analyzer — scores how 'good' each entry and exit was geometrically.

For each trade, computes:
  - entry_proximity_to_low  (LONG): how close was entry_price to the local low across
    the next entry_lookback_bars? 1.0 = perfect bottom buy. 0.0 = top of recovery.
  - entry_proximity_to_high (SHORT): mirror.
  - exit_proximity_to_high (LONG): how close was exit_price to the local high during
    the trade? 1.0 = perfect top sell. 0.0 = sold at low.
  - exit_proximity_to_low (SHORT): mirror.

Aggregates per (run, symbol):
  - mean entry_quality, exit_quality (0–1)
  - bottom-quartile entry rate (entry_quality >= 0.75)
  - top-quartile exit rate (exit_quality >= 0.75)
  - per-trade gain bucketed by entry/exit quality

Output:
  - /tmp/v8_trades/QUALITY_<run>__<sym>.md per run
  - /tmp/v8_trades/QUALITY_SUMMARY.md cross-run table

Usage:
  python3 quality_analyzer.py --all-runs --syms BTCUSDC
  python3 quality_analyzer.py --runs 5SYM_BEST,no_churn_v2 --syms BTCUSDC,ETHUSDC
"""
import argparse
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path("/Users/niels/Documents/binance")
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
DEFAULT_TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))


def proximity_score(price, low, high):
    """0.0 = at low, 1.0 = at high. Returns None if degenerate range."""
    if high <= low: return None
    return (price - low) / (high - low)


def analyze_run(run, sym, trades_dir, entry_lookback=20, exit_lookahead_within_trade=True):
    """Compute quality scores for every trade in this run/symbol."""
    p = Path(trades_dir) / f"{run}__{sym}.jsonl"
    if not p.exists(): return None
    trades = []
    for line in p.read_text().splitlines():
        if line.strip():
            try: trades.append(json.loads(line))
            except Exception: pass
    if not trades: return None
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists(): return None
    z = np.load(npz_path, mmap_mode="r")
    ts = np.asarray(z["timestamps"])
    high = np.asarray(z["high_3m"])
    low = np.asarray(z["low_3m"])
    n = len(ts)

    rows = []
    for t in trades:
        en_ts = int(t.get("entry_ts", 0))
        ex_ts = int(t.get("exit_ts", 0))
        en_idx = int(np.searchsorted(ts, en_ts))
        ex_idx = int(np.searchsorted(ts, ex_ts))
        if en_idx >= n or ex_idx >= n or en_idx < 0: continue
        side = t.get("side", "LONG")
        en_price = float(t.get("entry_price", 0))
        ex_price = float(t.get("exit_price", 0))
        # Entry quality: window AROUND entry_idx (entry_lookback bars BEFORE + AFTER)
        en_lo_idx = max(0, en_idx - entry_lookback)
        en_hi_idx = min(n, en_idx + entry_lookback + 1)
        win_lo = float(np.min(low[en_lo_idx:en_hi_idx]))
        win_hi = float(np.max(high[en_lo_idx:en_hi_idx]))
        entry_score = proximity_score(en_price, win_lo, win_hi)
        if entry_score is None: continue
        # For LONG, "good entry" = LOW proximity (near bottom). Flip the scale:
        if side == "LONG":
            entry_quality = 1.0 - entry_score      # 1.0 = perfect bottom buy
        else:
            entry_quality = entry_score             # 1.0 = perfect top sell
        # Exit quality: window from entry_idx to exit_idx (the held period)
        if exit_lookahead_within_trade:
            held_lo = float(np.min(low[en_idx:ex_idx + 1])) if ex_idx > en_idx else en_price
            held_hi = float(np.max(high[en_idx:ex_idx + 1])) if ex_idx > en_idx else en_price
        else:
            # Use ±entry_lookback around exit
            ex_lo_idx = max(0, ex_idx - entry_lookback)
            ex_hi_idx = min(n, ex_idx + entry_lookback + 1)
            held_lo = float(np.min(low[ex_lo_idx:ex_hi_idx]))
            held_hi = float(np.max(high[ex_lo_idx:ex_hi_idx]))
        exit_score = proximity_score(ex_price, held_lo, held_hi)
        if exit_score is None: continue
        if side == "LONG":
            exit_quality = exit_score             # 1.0 = perfect top sell
        else:
            exit_quality = 1.0 - exit_score        # 1.0 = perfect bottom cover
        rows.append({
            "side": side, "pnl_pct": float(t.get("pnl_pct", 0) or 0),
            "entry_quality": round(entry_quality, 3),
            "exit_quality": round(exit_quality, 3),
            "duration_bars": int(t.get("duration_bars", 0) or 0),
            "entry_reason": t.get("entry_reason", ""),
            "exit_reason": t.get("exit_reason", ""),
        })
    if not rows: return None

    def _stats(field):
        vals = [r[field] for r in rows if r[field] == r[field]]
        if not vals: return {}
        return {
            "n": len(vals), "mean": round(sum(vals)/len(vals), 3),
            "median": round(sorted(vals)[len(vals)//2], 3),
            "p25": round(sorted(vals)[len(vals)//4], 3),
            "p75": round(sorted(vals)[(3*len(vals))//4], 3),
        }
    n = len(rows)
    bottom_quartile_entries = sum(1 for r in rows if r["entry_quality"] >= 0.75)
    top_quartile_exits = sum(1 for r in rows if r["exit_quality"] >= 0.75)
    avg_pnl = sum(r["pnl_pct"] for r in rows) / n
    total_gain = sum(r["pnl_pct"] for r in rows)
    # Quality vs PnL bucket: split into 4 quality bins, see avg PnL in each
    bucket = lambda v: ("Q1_low" if v < 0.25 else "Q2_mid_low" if v < 0.5 else "Q3_mid_high" if v < 0.75 else "Q4_high")
    pnl_by_entry_q = defaultdict(list)
    pnl_by_exit_q = defaultdict(list)
    for r in rows:
        pnl_by_entry_q[bucket(r["entry_quality"])].append(r["pnl_pct"])
        pnl_by_exit_q[bucket(r["exit_quality"])].append(r["pnl_pct"])
    return {
        "run": run, "symbol": sym, "trades": n,
        "entry_quality": _stats("entry_quality"),
        "exit_quality": _stats("exit_quality"),
        "bottom_quartile_entries_pct": round(100 * bottom_quartile_entries / n, 1),
        "top_quartile_exits_pct": round(100 * top_quartile_exits / n, 1),
        "avg_pnl_pct": round(avg_pnl, 4),
        "total_gain_pct": round(total_gain, 1),
        "avg_pnl_by_entry_q": {k: round(sum(v)/len(v), 4) if v else None for k, v in pnl_by_entry_q.items()},
        "avg_pnl_by_exit_q": {k: round(sum(v)/len(v), 4) if v else None for k, v in pnl_by_exit_q.items()},
        "n_by_entry_q": {k: len(v) for k, v in pnl_by_entry_q.items()},
        "n_by_exit_q": {k: len(v) for k, v in pnl_by_exit_q.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", help="comma-sep run ids")
    ap.add_argument("--all-runs", action="store_true")
    ap.add_argument("--syms", required=True)
    ap.add_argument("--trades-dir", default=str(DEFAULT_TRADES_DIR))
    ap.add_argument("--entry-lookback", type=int, default=20, help="bars ±around entry to define local range (default 20=60min)")
    args = ap.parse_args()
    syms = [s.upper() for s in args.syms.split(",")]
    runs = []
    if args.runs: runs.extend(args.runs.split(","))
    if args.all_runs:
        for sym in syms:
            for p in sorted(Path(args.trades_dir).glob(f"*__{sym}.jsonl")):
                run, _, _ = p.stem.partition("__")
                runs.append(run)
    runs = sorted(set(runs))
    if not runs:
        print("no runs"); return

    print(f"\n{'Run':<35s} {'Sym':<10s} {'Tr':>6s} {'EntQ':>6s} {'ExtQ':>6s} {'Bot25%':>8s} {'Top25%':>8s} {'avg/tr':>8s} {'TotGain':>10s}")
    print("-" * 105)
    aggregate = []
    for run in runs:
        for sym in syms:
            r = analyze_run(run, sym, args.trades_dir, args.entry_lookback)
            if not r: continue
            aggregate.append(r)
            eq = r["entry_quality"].get("median", 0)
            xq = r["exit_quality"].get("median", 0)
            print(f"{run[:34]:<35s} {sym:<10s} {r['trades']:>6d} {eq:>6.3f} {xq:>6.3f} {r['bottom_quartile_entries_pct']:>7.1f}% {r['top_quartile_exits_pct']:>7.1f}% {r['avg_pnl_pct']:>+7.4f}% {r['total_gain_pct']:>+9.1f}%")
    # Markdown summary
    md = [f"# Trade-Quality Summary  *(generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})*",
          "", f"entry_lookback = ±{args.entry_lookback} bars (3m base TF)", "",
          "Quality scale: **1.0 = perfect bottom-buy / top-sell**, 0.0 = worst possible. Bot25% = trades with entry_quality≥0.75. Top25% = trades with exit_quality≥0.75.",
          "",
          "| Run | Sym | Trades | EntQ med | ExtQ med | Bot25% | Top25% | avg/tr | Tot Gain | PnL by EntQ Q1→Q4 | PnL by ExtQ Q1→Q4 |",
          "|---|---|--:|--:|--:|--:|--:|--:|--:|---|---|"]
    for r in aggregate:
        eq = r["entry_quality"].get("median", 0)
        xq = r["exit_quality"].get("median", 0)
        eq_pnl_str = " / ".join(f"{r['avg_pnl_by_entry_q'].get(b, 0) or 0:+.3f}" for b in ('Q1_low','Q2_mid_low','Q3_mid_high','Q4_high'))
        xq_pnl_str = " / ".join(f"{r['avg_pnl_by_exit_q'].get(b, 0) or 0:+.3f}" for b in ('Q1_low','Q2_mid_low','Q3_mid_high','Q4_high'))
        md.append(f"| `{r['run']}` | {r['symbol']} | {r['trades']} | {eq:.3f} | {xq:.3f} | {r['bottom_quartile_entries_pct']:.1f}% | {r['top_quartile_exits_pct']:.1f}% | {r['avg_pnl_pct']:+.4f}% | {r['total_gain_pct']:+.1f}% | {eq_pnl_str} | {xq_pnl_str} |")
    out = Path(args.trades_dir) / "QUALITY_SUMMARY.md"
    out.write_text("\n".join(md))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
