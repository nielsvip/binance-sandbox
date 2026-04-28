#!/usr/bin/env python3
"""Detect 'churn' — chains of consecutive same-side trades on the same symbol within
a tight time window. Quantify the cost: actual realized PnL vs the unrealized PnL of
holding the first entry through the last exit.

A churn-chain is N>=2 same-side trades where each entry is within --window-minutes
of the previous exit. The chain's "held PnL" is computed from the first entry_price
to the last exit_price (going the same direction as the trades). The "churned PnL"
is the sum of individual trade pnls. Difference = churn cost (positive = lost alpha).

Outputs:
  - Markdown report at /tmp/v8_trades/CHURN_<run>__<sym>.md
  - Structured JSON for downstream tooling

Usage:
  python3 churn_analyzer.py --run 5SYM_BEST --sym BTCUSDT
  python3 churn_analyzer.py --run-pattern 'auto_*' --sym BTCUSDT
  python3 churn_analyzer.py --all-runs --sym BTCUSDT --window-minutes 60 --min-chain 3
"""
import argparse
import glob
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
    out.sort(key=lambda t: int(t.get("entry_ts", 0)))
    return out


def find_chains(trades, window_sec):
    """Group consecutive same-side trades where (next.entry_ts - prev.exit_ts) <= window."""
    chains = []
    if not trades: return chains
    cur = [trades[0]]
    for t in trades[1:]:
        prev = cur[-1]
        same_side = t.get("side") == prev.get("side")
        gap = int(t.get("entry_ts", 0)) - int(prev.get("exit_ts", 0))
        if same_side and 0 <= gap <= window_sec:
            cur.append(t)
        else:
            if len(cur) >= 2:
                chains.append(cur)
            cur = [t]
    if len(cur) >= 2:
        chains.append(cur)
    return chains


def chain_metrics(chain):
    """Compute chain stats."""
    side = chain[0].get("side", "LONG")
    first_entry = chain[0]["entry_price"]
    last_exit = chain[-1]["exit_price"]
    if side == "LONG":
        held_pnl_pct = (last_exit - first_entry) / first_entry * 100.0
    else:
        held_pnl_pct = (first_entry - last_exit) / first_entry * 100.0
    churned_pnl_pct = sum(float(t.get("pnl_pct", 0) or 0) for t in chain)
    span_sec = int(chain[-1]["exit_ts"]) - int(chain[0]["entry_ts"])
    return {
        "side": side,
        "trades": len(chain),
        "span_sec": span_sec,
        "span_min": span_sec / 60.0,
        "first_entry_ts": int(chain[0]["entry_ts"]),
        "first_entry_price": first_entry,
        "last_exit_ts": int(chain[-1]["exit_ts"]),
        "last_exit_price": last_exit,
        "held_pnl_pct": held_pnl_pct,
        "churned_pnl_pct": churned_pnl_pct,
        "churn_cost_pct": held_pnl_pct - churned_pnl_pct,
        "exit_reasons": [t.get("exit_reason", "") for t in chain[:-1]],  # exclude final exit
        "entry_reasons": [t.get("entry_reason", "") for t in chain[1:]],  # exclude first entry
    }


def analyze(run, sym, trades_dir, window_sec, min_chain):
    trades = load_trades(run, sym, trades_dir)
    if not trades:
        return None
    all_chains = find_chains(trades, window_sec)
    chains = [c for c in all_chains if len(c) >= min_chain]
    chain_data = [chain_metrics(c) for c in chains]
    chain_data.sort(key=lambda d: -d["churn_cost_pct"])
    # Aggregate
    total_trades = len(trades)
    chained_trades = sum(c["trades"] for c in chain_data)
    total_churn_cost = sum(c["churn_cost_pct"] for c in chain_data)
    # Reason frequency in churn middle (exits that triggered re-entries)
    middle_exits = defaultdict(int)
    middle_entries = defaultdict(int)
    for c in chain_data:
        for r in c["exit_reasons"]:
            middle_exits[r] += 1
        for r in c["entry_reasons"]:
            middle_entries[r] += 1
    return {
        "run": run, "sym": sym,
        "window_sec": window_sec, "min_chain": min_chain,
        "total_trades": total_trades,
        "n_chains": len(chain_data),
        "chained_trades": chained_trades,
        "chained_pct": (chained_trades / total_trades * 100.0) if total_trades else 0,
        "total_churn_cost_pct": total_churn_cost,
        "top_chains": chain_data[:20],
        "middle_exit_reasons": sorted(middle_exits.items(), key=lambda x: -x[1])[:10],
        "middle_entry_reasons": sorted(middle_entries.items(), key=lambda x: -x[1])[:10],
        "all_chains_count": len(chain_data),
    }


def render_report(report):
    out = []
    out.append(f"# Churn Report — `{report['run']}` × `{report['sym']}`")
    out.append(f"*generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*")
    out.append(f"window={report['window_sec']}s ({report['window_sec']/60:.0f} min), min chain length={report['min_chain']}")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- Total trades: **{report['total_trades']}**")
    out.append(f"- Chains found (>={report['min_chain']} consecutive same-side within window): **{report['n_chains']}**")
    out.append(f"- Trades chained: **{report['chained_trades']}** ({report['chained_pct']:.1f}% of total)")
    out.append(f"- **Total alpha lost to churn: {report['total_churn_cost_pct']:+.1f}%**")
    out.append("")
    out.append("## Top 20 worst churn windows")
    out.append("")
    out.append("| # | Side | N | Span | Held % | Churned % | Cost % | Window UTC |")
    out.append("|--:|---|--:|--:|--:|--:|--:|---|")
    for i, c in enumerate(report["top_chains"], 1):
        en = datetime.fromtimestamp(c["first_entry_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        out.append(f"| {i} | {c['side']} | {c['trades']} | {c['span_min']:>4.0f}m | {c['held_pnl_pct']:+.2f}% | {c['churned_pnl_pct']:+.2f}% | **{c['churn_cost_pct']:+.2f}%** | {en} |")
    out.append("")
    out.append("## Top exit reasons inside churn middles")
    out.append("(These are the exits that closed a position only for it to re-open seconds later — "
              "the prime candidates to *skip* when in trending regime.)")
    out.append("")
    for r, n in report["middle_exit_reasons"]:
        out.append(f"- **{r}**: {n} occurrences")
    out.append("")
    out.append("## Top entry reasons after churn-mid exits")
    out.append("")
    for r, n in report["middle_entry_reasons"]:
        out.append(f"- **{r}**: {n} occurrences")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--run-pattern", help="glob pattern, e.g. 'auto_*'")
    ap.add_argument("--all-runs", action="store_true")
    ap.add_argument("--sym", required=True)
    ap.add_argument("--trades-dir", default=str(DEFAULT_TRADES_DIR))
    ap.add_argument("--window-minutes", type=float, default=60.0,
                    help="max gap between exit and next entry to consider chained (default 60min)")
    ap.add_argument("--min-chain", type=int, default=3,
                    help="min consecutive trades to flag as a chain (default 3)")
    args = ap.parse_args()
    window_sec = int(args.window_minutes * 60)

    # Build run list
    runs = []
    if args.run:
        runs.append(args.run)
    if args.run_pattern:
        for p in sorted(Path(args.trades_dir).glob(f"{args.run_pattern}__{args.sym}.jsonl")):
            run, _, _ = p.stem.partition("__")
            runs.append(run)
    if args.all_runs:
        for p in sorted(Path(args.trades_dir).glob(f"*__{args.sym}.jsonl")):
            run, _, _ = p.stem.partition("__")
            runs.append(run)
    runs = sorted(set(runs))
    if not runs:
        print("No runs matched"); return

    print(f"\n{'Run':<35s} {'Trades':>7s} {'Chains':>7s} {'Chained%':>9s} {'ChurnCost%':>11s}")
    print("-" * 75)
    aggregate = []
    for run in runs:
        r = analyze(run, args.sym, args.trades_dir, window_sec, args.min_chain)
        if not r: continue
        aggregate.append(r)
        print(f"{run[:34]:<35s} {r['total_trades']:>7d} {r['n_chains']:>7d} {r['chained_pct']:>8.1f}% {r['total_churn_cost_pct']:>+10.1f}%")
        # Per-run markdown
        md_path = Path(args.trades_dir) / f"CHURN_{run}__{args.sym}.md"
        md_path.write_text(render_report(r))
    # Top-level summary
    if aggregate:
        agg_path = Path(args.trades_dir) / f"CHURN_SUMMARY_{args.sym}.md"
        agg_md = [f"# Churn Summary — `{args.sym}`", f"*generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*", "",
                  f"window={window_sec}s ({args.window_minutes:.0f} min), min chain={args.min_chain}", "",
                  "| Run | Trades | Chains | Chained% | ChurnCost% | Top exit reason | Top entry reason |",
                  "|---|--:|--:|--:|--:|---|---|"]
        for r in aggregate:
            top_x = r["middle_exit_reasons"][0][0] if r["middle_exit_reasons"] else "—"
            top_e = r["middle_entry_reasons"][0][0] if r["middle_entry_reasons"] else "—"
            agg_md.append(f"| `{r['run']}` | {r['total_trades']} | {r['n_chains']} | {r['chained_pct']:.1f}% | **{r['total_churn_cost_pct']:+.1f}%** | `{top_x[:40]}` | `{top_e[:40]}` |")
        agg_path.write_text("\n".join(agg_md))
        print(f"\nWrote {agg_path}")
        print(f"Per-run reports: /tmp/v8_trades/CHURN_<run>__{args.sym}.md")


if __name__ == "__main__":
    main()
