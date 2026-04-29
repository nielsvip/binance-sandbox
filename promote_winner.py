#!/usr/bin/env python3
"""Promote a high-Sharpe auto-loop variant to a permanent named override.

After the auto-loop has cycled, this:
  1. Scans all trade JSONLs in $V8_TRADES_OUT_DIR
  2. Computes per-run Sharpe/T (and other metrics) across all symbols it tested
  3. Filters to runs that pass minimum bars (--min-trades, --min-syms, --min-sharpe)
  4. Optionally validates against a baseline (--vs-baseline) — winner must beat it on
     pool sharpe AND total gain across the matched symbols
  5. Copies the winner's override JSON into backtest_v8/btc_loop_results/ with a
     human-friendly name (--name) and updates _meta with promotion provenance
  6. Optionally rsyncs to S1+S2 sandboxes (--sync) — same parity protocol the rest
     of the code uses

NEVER touches live config. NEVER applies the override to live trading. Only persists
it as a candidate in the override library for later sweeps / Tier-2 validation /
human approval.

Usage:
  # List all candidates with stats
  python3 promote_winner.py --list

  # Promote a specific run
  python3 promote_winner.py --run auto_20260428_2345_BTC_RZ_PROXIMITY_PCT=0p3 --name v2_tighter_rz

  # Auto-pick the highest pool-Sharpe run that beats baseline
  python3 promote_winner.py --auto --vs-baseline 5SYM_BEST --name latest_winner

  # Promote + sync to sandboxes
  python3 promote_winner.py --auto --vs-baseline 5SYM_BEST --name v2_winner --sync
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/niels/Documents/binance")
TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))
OVERRIDE_DIR = ROOT / "backtest_v8" / "btc_loop_results"
AUTO_OVERRIDE_DIR = TRADES_DIR / "auto_overrides"


def load_run_stats():
    """Walk all <run>__<sym>.jsonl, return {run: {sym: stats}}."""
    out = {}
    for p in sorted(TRADES_DIR.glob("*__*.jsonl")):
        run, _, sym = p.stem.partition("__")
        if not run or not sym: continue
        trades = []
        for line in p.read_text().splitlines():
            if line.strip():
                try: trades.append(json.loads(line))
                except Exception: pass
        if len(trades) < 5: continue
        pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
        n = len(pnls)
        wins = sum(1 for x in pnls if x > 0)
        std = statistics.stdev(pnls) if n > 1 else 0
        out.setdefault(run, {})[sym] = {
            "trades": n,
            "wins": wins,
            "wr": wins / n if n else 0,
            "total_gain_pct": sum(pnls),
            "avg_pnl_pct": sum(pnls) / n if n else 0,
            "sharpe_pt": (sum(pnls) / n) / std if std > 0 else 0,
            "std": std,
        }
    return out


def aggregate_run(sym_stats):
    """Pool stats across symbols for one run. Returns dict or None."""
    if not sym_stats: return None
    n_syms = len(sym_stats)
    total_trades = sum(s["trades"] for s in sym_stats.values())
    total_wins = sum(s["wins"] for s in sym_stats.values())
    # Pool: combine all trades flat
    # We don't have raw pnls here; approximate pool sharpe by averaging per-sym sharpes weighted by trades
    weighted_sharpe = 0
    weighted_avg = 0
    for s in sym_stats.values():
        weighted_sharpe += s["sharpe_pt"] * s["trades"]
        weighted_avg += s["avg_pnl_pct"] * s["trades"]
    pool_sharpe = weighted_sharpe / total_trades if total_trades else 0
    pool_avg = weighted_avg / total_trades if total_trades else 0
    pool_wr = total_wins / total_trades if total_trades else 0
    total_gain = sum(s["total_gain_pct"] for s in sym_stats.values())
    return {
        "n_syms": n_syms, "trades": total_trades, "wr": pool_wr,
        "pool_sharpe_per_trade": pool_sharpe, "pool_avg_pnl_pct": pool_avg,
        "total_gain_pct": total_gain,
        "syms": sorted(sym_stats.keys()),
    }


def find_override_for_run(run):
    """Try to find the override JSON that produced this run-id."""
    candidates = []
    base = run
    if base.startswith("tier2_"): base = base[len("tier2_"):]
    # 1. EXACT match in auto_overrides (chart_sweep names runs after the override stem)
    for p in AUTO_OVERRIDE_DIR.glob("*.json"):
        if p.stem == run or p.stem == base:
            candidates.append(p)
    # 2. EXACT match in override_dir (manual overrides like override_5SYM_BEST.json)
    for p in OVERRIDE_DIR.glob("override_*.json"):
        stem = p.stem
        if stem == "override_" + base or stem == "override_" + run:
            candidates.append(p)
    # 3. Fuzzy match on auto_overrides (auto_<ts>_<switch>=<val> patterns)
    if not candidates and run.startswith("auto_"):
        for p in AUTO_OVERRIDE_DIR.glob("*.json"):
            if p.stem.endswith(run.split("_", 1)[-1]):
                candidates.append(p)
    # 4. Fuzzy match on OVERRIDE_DIR
    if not candidates:
        for p in OVERRIDE_DIR.glob("override_*.json"):
            if base.lower() in p.stem.lower():
                candidates.append(p)
    return candidates[0] if candidates else None


def list_runs(args):
    stats = load_run_stats()
    if not stats:
        print(f"No runs found in {TRADES_DIR}")
        return
    rows = []
    for run, sym_stats in stats.items():
        agg = aggregate_run(sym_stats)
        if not agg: continue
        rows.append((run, agg))
    rows.sort(key=lambda r: -r[1]["pool_sharpe_per_trade"])
    print(f"\n{'Run':<40s} {'Sym':>4s} {'Tr':>6s} {'WR':>5s} {'Sharpe/T':>9s} {'avg/tr':>8s} {'TotGain':>10s}")
    print("-" * 90)
    for run, agg in rows:
        print(f"{run[:39]:<40s} {agg['n_syms']:>4d} {agg['trades']:>6d} {agg['wr']*100:>4.1f}% {agg['pool_sharpe_per_trade']:>+9.3f} {agg['pool_avg_pnl_pct']:>+7.4f}% {agg['total_gain_pct']:>+9.1f}%")
    print()


def auto_pick(stats, baseline_run, min_trades, min_syms, min_sharpe):
    """Find the highest-pool-Sharpe run that beats baseline AND meets thresholds."""
    baseline_agg = aggregate_run(stats.get(baseline_run, {})) if baseline_run else None
    if baseline_run and not baseline_agg:
        print(f"[promote] baseline {baseline_run} has no trades; falling back to absolute thresholds only")
    candidates = []
    for run, sym_stats in stats.items():
        if run == baseline_run: continue
        agg = aggregate_run(sym_stats)
        if not agg: continue
        if agg["trades"] < min_trades: continue
        if agg["n_syms"] < min_syms: continue
        if agg["pool_sharpe_per_trade"] < min_sharpe: continue
        if baseline_agg and agg["pool_sharpe_per_trade"] <= baseline_agg["pool_sharpe_per_trade"]: continue
        if baseline_agg and agg["total_gain_pct"] <= baseline_agg["total_gain_pct"]: continue
        candidates.append((run, agg))
    if not candidates:
        return None, None
    candidates.sort(key=lambda r: -r[1]["pool_sharpe_per_trade"])
    return candidates[0]


def promote(run, agg, name, force, sync):
    src = find_override_for_run(run)
    if not src or not src.exists():
        print(f"[promote] ❌ no override JSON found for run {run}")
        return False
    dest_name = f"override_{name}.json"
    dest = OVERRIDE_DIR / dest_name
    if dest.exists() and not force:
        print(f"[promote] ❌ {dest} exists; use --force to overwrite")
        return False
    cfg = json.loads(src.read_text())
    cfg["_meta"] = (
        f"Promoted from auto-loop run '{run}' on {datetime.now(timezone.utc).isoformat()}. "
        f"Pool stats: {agg['trades']} trades across {agg['n_syms']} syms, "
        f"WR {agg['wr']*100:.1f}%, Sharpe/T {agg['pool_sharpe_per_trade']:+.3f}, "
        f"avg/tr {agg['pool_avg_pnl_pct']:+.4f}%, total gain {agg['total_gain_pct']:+.1f}%. "
        f"Source override: {src.name}. Promoted by: promote_winner.py."
    )
    dest.write_text(json.dumps(cfg, indent=2))
    print(f"[promote] ✅ wrote {dest}")
    print(f"[promote]    source: {src}")
    print(f"[promote]    stats: {agg}")
    if sync:
        for host in ("niels@157.180.125.52", "niels@204.168.181.211"):
            cmd = ["rsync", "-az", "--update", str(dest), f"{host}:/home/niels/binance-sandbox/backtest_v8/btc_loop_results/"]
            print(f"[promote] sync → {host}")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                print(f"  ❌ rsync return={r.returncode} stderr={(r.stderr or '')[:200]}")
            else:
                print(f"  ✅ rsync ok")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list all runs sorted by pool Sharpe")
    ap.add_argument("--auto", action="store_true", help="auto-pick highest-Sharpe run that beats baseline")
    ap.add_argument("--run", help="explicit run-id to promote")
    ap.add_argument("--name", help="name suffix for the promoted override (override_<name>.json)")
    ap.add_argument("--vs-baseline", help="baseline run-id; auto-pick must beat it on Sharpe AND gain")
    ap.add_argument("--min-trades", type=int, default=100)
    ap.add_argument("--min-syms", type=int, default=1)
    ap.add_argument("--min-sharpe", type=float, default=0.3)
    ap.add_argument("--force", action="store_true", help="overwrite existing override_<name>.json")
    ap.add_argument("--sync", action="store_true", help="rsync the promoted override to S1+S2 sandboxes")
    args = ap.parse_args()

    if args.list:
        list_runs(args); return

    stats = load_run_stats()
    if not stats:
        print(f"No runs found in {TRADES_DIR}"); return

    target_run = None
    target_agg = None
    if args.run:
        if args.run not in stats:
            print(f"[promote] ❌ run {args.run} not found"); return
        target_agg = aggregate_run(stats[args.run])
        if not target_agg:
            print(f"[promote] ❌ run {args.run} has no aggregable stats"); return
        target_run = args.run
    elif args.auto:
        target_run, target_agg = auto_pick(stats, args.vs_baseline,
                                          args.min_trades, args.min_syms, args.min_sharpe)
        if not target_run:
            print(f"[promote] no run met thresholds"
                  f"  min_trades={args.min_trades} min_syms={args.min_syms} min_sharpe={args.min_sharpe}"
                  f"{' beat baseline ' + args.vs_baseline if args.vs_baseline else ''}")
            return
        print(f"[promote] auto-picked: {target_run}")
    else:
        ap.error("must pass --list, --run <id>, or --auto")
    if not args.name:
        args.name = target_run.replace("=", "_").replace(".", "p")[:40]
    promote(target_run, target_agg, args.name, args.force, args.sync)


if __name__ == "__main__":
    main()
