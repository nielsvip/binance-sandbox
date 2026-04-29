#!/usr/bin/env python3
"""Continuous-search optimizer that mutates baseline configs and ranks them on a
composite quality score:

  score = w_avg * avg_pnl_pct
        + w_total * (total_gain_pct / 1000)
        + w_botq * bottom_quartile_entry_rate
        + w_topq * top_quartile_exit_rate
        - w_count * (trade_count / 1000)
        - w_churn * (churn_cost_pct / 1000)

Defaults reward: high per-trade gain, high total gain, entries near local bottoms
(longs) / tops (shorts), exits near local tops/bottoms, AND penalize high trade
counts and churn.

Pipeline (per iteration):
  1. Read latest run-id list from $V8_TRADES_OUT_DIR.
  2. For each, compute quality + churn metrics and the composite score.
  3. Pick the top-K runs as "elite" baselines.
  4. Mutate each elite by tweaking 1-3 of MUTABLE_KNOBS (random walk).
  5. Spawn chart_sweep on each new variant.
  6. Append leaderboard to QUALITY_LEADERBOARD.md.
  7. Sleep, loop.

NEVER touches live code. Tier-1 vec only.
NEVER mutates a `tier2_*` run.

Usage:
  python3 quality_optimizer.py --once                              # one iteration
  python3 quality_optimizer.py --loop --interval 1800              # every 30min forever
  python3 quality_optimizer.py --top-k 3 --new-per-elite 2 --once  # 6 new variants per round
  python3 quality_optimizer.py --syms BTCUSDT,ETHUSDT --score-weights 'avg=2,total=1,botq=3,topq=3,count=0.5,churn=2'
"""
import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path("/Users/niels/Documents/binance")
DEFAULT_TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
OVERRIDE_DIRS = [
    ROOT / "backtest_v8" / "btc_loop_results",
    DEFAULT_TRADES_DIR / "auto_overrides",
]
LEADERBOARD = DEFAULT_TRADES_DIR / "QUALITY_LEADERBOARD.md"

# Mutable knobs — switches we feel safe randomly tweaking. Each entry: (name, value-list).
# The optimizer picks ONE name per mutation and randomly selects a different value.
MUTABLE_KNOBS = {
    "BTC_BREAKOUT_MIN_HOLD_BARS":       [1, 3, 5, 10, 20],
    "BTC_BREAKOUT_COOLDOWN_BARS":       [1, 3, 5, 10],
    "BTC_MIN_HOLD_BARS":                [1, 3, 5, 10, 20],
    "BTC_COOLDOWN_BARS":                [1, 3, 5, 10],
    "BTC_BREAKOUT_HTF_MIN_ALIGNED":     [1, 2, 3],
    "BTC_TECH_EXIT_WT_MIN_TFS":         [2, 3, 4, 5],
    "BTC_FOLLOW_THROUGH_REENTRY_ENABLED": [True, False],
    "BTC_REVERSE_ON_EXIT_ENABLED":      [True, False],
    "BTC_DIVERGENCE_BLOCK_AGAINST":     [True, False],
    "BTC_DIVERGENCE_EXIT_AGAINST":      [True, False],
    "BTC_DIVERGENCE_BULL_MIN_INDS":     [1, 2, 3],
    "BTC_DIVERGENCE_BEAR_MIN_INDS":     [1, 2, 3],
    "BTC_DIVERGENCE_MIN_TF":            ["15m", "1h", "4h"],
    "BTC_RZ_PROXIMITY_PCT":             [0.3, 0.5, 0.8, 1.0],
    "BTC_ACCEL_RAMP_MIN_TFS":           [1, 2, 3, 4, 5],
    "BTC_BREAKOUT_USE_REJECTION_EXIT":  [True, False],
    "BTC_BREAKOUT_USE_LEGACY_ACCEL_REVERSAL": [True, False],
    "BTC_BREAKOUT_USE_WT_3M_FLIP_EXIT": [True, False],
    "FUNDING_GATE_ENABLED":             [True, False],
    "OI_CONFIRM_ENABLED":               [True, False],
}


def proximity_score(price, lo, hi):
    if hi <= lo: return None
    return (price - lo) / (hi - lo)


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


def compute_run_metrics(run, sym, trades_dir, entry_lookback=20, churn_window_sec=3600):
    trades = load_trades(run, sym, trades_dir)
    if len(trades) < 5: return None
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists(): return None
    z = np.load(npz_path, mmap_mode="r")
    ts = np.asarray(z["timestamps"])
    high = np.asarray(z["high_3m"])
    low = np.asarray(z["low_3m"])
    n_ts = len(ts)
    eq_list, xq_list, pnl_list = [], [], []
    for t in trades:
        en_ts = int(t.get("entry_ts", 0))
        ex_ts = int(t.get("exit_ts", 0))
        en_idx = int(np.searchsorted(ts, en_ts))
        ex_idx = int(np.searchsorted(ts, ex_ts))
        if en_idx >= n_ts or ex_idx >= n_ts or en_idx < 0: continue
        side = t.get("side", "LONG")
        en_price = float(t.get("entry_price", 0))
        ex_price = float(t.get("exit_price", 0))
        en_lo_idx = max(0, en_idx - entry_lookback)
        en_hi_idx = min(n_ts, en_idx + entry_lookback + 1)
        win_lo = float(np.min(low[en_lo_idx:en_hi_idx]))
        win_hi = float(np.max(high[en_lo_idx:en_hi_idx]))
        eq_score = proximity_score(en_price, win_lo, win_hi)
        if eq_score is None: continue
        eq = (1.0 - eq_score) if side == "LONG" else eq_score
        held_lo = float(np.min(low[en_idx:ex_idx + 1])) if ex_idx > en_idx else en_price
        held_hi = float(np.max(high[en_idx:ex_idx + 1])) if ex_idx > en_idx else en_price
        xq_score = proximity_score(ex_price, held_lo, held_hi)
        if xq_score is None: continue
        xq = xq_score if side == "LONG" else (1.0 - xq_score)
        eq_list.append(eq); xq_list.append(xq); pnl_list.append(float(t.get("pnl_pct", 0) or 0))
    if not pnl_list: return None
    n = len(pnl_list)
    avg_pnl = sum(pnl_list) / n
    total_gain = sum(pnl_list)
    bot_q = sum(1 for v in eq_list if v >= 0.75) / n
    top_q = sum(1 for v in xq_list if v >= 0.75) / n
    # Churn: count chained trades (same-side, gap <= window)
    chained = 0
    for i in range(1, len(trades)):
        prev = trades[i - 1]; cur = trades[i]
        if prev.get("side") == cur.get("side") and 0 <= int(cur.get("entry_ts", 0)) - int(prev.get("exit_ts", 0)) <= churn_window_sec:
            chained += 1
    chained_pct = chained / n
    std = statistics.pstdev(pnl_list) if n > 1 else 0
    sharpe_pt = avg_pnl / std if std > 0 else 0
    return {
        "trades": n,
        "avg_pnl_pct": avg_pnl,
        "total_gain_pct": total_gain,
        "sharpe_pt": sharpe_pt,
        "wr": sum(1 for v in pnl_list if v > 0) / n,
        "median_entry_quality": statistics.median(eq_list),
        "median_exit_quality": statistics.median(xq_list),
        "bottom_quartile_entry_rate": bot_q,
        "top_quartile_exit_rate": top_q,
        "chained_pct": chained_pct,
    }


def composite_score(m, weights):
    # 2026-04-29 user directive: "sharpe per symbol per trade and pool sharpe DESTROY everything else".
    # Make per-trade Sharpe the dominant term; quality + churn stay as soft secondary signals.
    # Default sharpe weight (10x) ensures higher-Sharpe variants always rank above
    # higher-trade-count variants of equal Sharpe.
    return (
        weights.get("sharpe", 10.0) * m["sharpe_pt"]
        + weights["avg"]   * m["avg_pnl_pct"]
        + weights["total"] * (m["total_gain_pct"] / 1000.0)
        + weights["botq"]  * m["bottom_quartile_entry_rate"]
        + weights["topq"]  * m["top_quartile_exit_rate"]
        - weights["count"] * (m["trades"] / 10000.0)
        - weights["churn"] * m["chained_pct"]
    )


def find_override_for_run(run):
    for d in OVERRIDE_DIRS:
        if not d.exists(): continue
        for p in d.glob("*.json"):
            stem = p.stem
            stem_clean = stem[len("override_"):] if stem.startswith("override_") else stem
            if stem_clean == run or stem == run:
                return p
    return None


def mutate(baseline_cfg, n_mutations=2):
    new_cfg = dict(baseline_cfg)
    available = list(MUTABLE_KNOBS.keys())
    random.shuffle(available)
    chosen = []
    for k in available[:n_mutations]:
        cur = baseline_cfg.get(k)
        choices = [v for v in MUTABLE_KNOBS[k] if v != cur]
        if not choices: continue
        new_val = random.choice(choices)
        new_cfg[k] = new_val
        chosen.append((k, cur, new_val))
    return new_cfg, chosen


def write_override(cfg, mutations, run_id_prefix):
    out_dir = DEFAULT_TRADES_DIR / "auto_overrides"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = "_".join(f"{k}={str(v).replace('.', 'p')}" for k, _, v in mutations)
    name = f"qopt_{ts_str}_{run_id_prefix}_{suffix}.json"[:200]
    path = out_dir / name
    cfg = dict(cfg)
    cfg["_meta"] = (
        f"quality_optimizer mutation @ {datetime.now(timezone.utc).isoformat()}. "
        f"Baseline: {run_id_prefix}. Changes: " +
        "; ".join(f"{k}: {old} → {new}" for k, old, new in mutations)
    )
    path.write_text(json.dumps(cfg, indent=2))
    return path


def run_chart_sweep(override_path, syms, run_id):
    cmd = [sys.executable, str(ROOT / "chart_sweep.py"),
           "--override", str(override_path),
           "--symbols", ",".join(syms)]
    env = os.environ.copy()
    env["V8_TRADES_OUT_DIR"] = str(DEFAULT_TRADES_DIR)
    env["V8_TRADES_RUN_ID"] = run_id
    print(f"  [qopt] {run_id} → chart_sweep...", flush=True)
    try:
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
        if out.returncode != 0:
            print(f"  [qopt] return={out.returncode} stderr={(out.stderr or '')[:200]}")
        # Pull a summary
        last = ""
        for line in (out.stdout or "").splitlines()[::-1]:
            if "sharpe=" in line and "trades=" in line:
                last = line; break
        if last: print(f"  [qopt]   {last.strip()}")
        return out.returncode
    except Exception as e:
        print(f"  [qopt] ERROR {e}"); return -1


def append_leaderboard(scored_rows, weights):
    LEADERBOARD.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LEADERBOARD.exists()
    with LEADERBOARD.open("a") as f:
        f.write(f"\n## Iteration @ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write(f"weights: avg={weights['avg']} total={weights['total']} botq={weights['botq']} topq={weights['topq']} count={weights['count']} churn={weights['churn']}\n\n")
        f.write("| Rank | Score | Run | Sym | Tr | EntQ | ExtQ | Bot25% | Top25% | avg/tr | TotGain | Sharpe | Chain% |\n")
        f.write("|--:|--:|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|\n")
        for i, (run, sym, m, score) in enumerate(scored_rows[:20], 1):
            f.write(f"| {i} | {score:+.3f} | `{run[:30]}` | {sym} | {m['trades']} | {m['median_entry_quality']:.2f} | {m['median_exit_quality']:.2f} | {m['bottom_quartile_entry_rate']*100:.1f}% | {m['top_quartile_exit_rate']*100:.1f}% | {m['avg_pnl_pct']:+.4f}% | {m['total_gain_pct']:+.1f}% | {m['sharpe_pt']:+.3f} | {m['chained_pct']*100:.1f}% |\n")


def parse_weights(s):
    out = {"sharpe": 10.0, "avg": 1.0, "total": 1.0, "botq": 1.0, "topq": 1.0, "count": 1.0, "churn": 1.0}
    if not s: return out
    for part in s.split(","):
        if "=" not in part: continue
        k, v = part.split("=", 1)
        out[k.strip()] = float(v)
    return out


def iteration(args):
    print(f"\n=== quality_optimizer @ {datetime.now(timezone.utc).isoformat()} ===")
    weights = parse_weights(args.score_weights)
    syms = [s.upper() for s in (args.syms.split(",") if args.syms else ["BTCUSDT"])]
    # 1. Score all existing runs
    runs = set()
    for p in DEFAULT_TRADES_DIR.glob("*__*.jsonl"):
        run, _, _ = p.stem.partition("__")
        if run and not run.startswith("tier2_"): runs.add(run)
    scored = []
    for run in sorted(runs):
        for sym in syms:
            m = compute_run_metrics(run, sym, DEFAULT_TRADES_DIR)
            if not m: continue
            score = composite_score(m, weights)
            scored.append((run, sym, m, score))
    scored.sort(key=lambda r: -r[3])
    if not scored:
        print("[qopt] no runs to score"); return
    print(f"[qopt] scored {len(scored)} run×sym pairs")
    print(f"[qopt] top 5:")
    for run, sym, m, score in scored[:5]:
        print(f"  score={score:+.3f}  {run[:30]:<30s} {sym}  Tr={m['trades']} EntQ={m['median_entry_quality']:.2f} ExtQ={m['median_exit_quality']:.2f} Bot%={m['bottom_quartile_entry_rate']*100:.0f} Top%={m['top_quartile_exit_rate']*100:.0f} avg={m['avg_pnl_pct']:+.4f} tot={m['total_gain_pct']:+.1f}")
    append_leaderboard(scored, weights)
    if args.score_only:
        print("[qopt] score-only mode — not generating new variants")
        return
    # 2. Mutate top-K elites — PER SYMBOL when --per-sym, else global
    if args.per_sym:
        # Track per-symbol best — top elites per-sym, mutate each, save per-sym winner override
        for sym in syms:
            sym_scored = [(r, s, m, sc) for r, s, m, sc in scored if s == sym]
            if not sym_scored: continue
            elites = []
            seen_runs = set()
            for run, _, m, score in sym_scored:
                if run in seen_runs: continue
                seen_runs.add(run)
                elites.append((run, m))
                if len(elites) >= args.top_k: break
            print(f"  [qopt][{sym}] elites: {[r for r, _ in elites]}")
            for run, m in elites:
                ovr_path = find_override_for_run(run)
                if not ovr_path: continue
                try: baseline = json.loads(ovr_path.read_text())
                except Exception: continue
                for j in range(args.new_per_elite):
                    new_cfg, mutations = mutate(baseline, n_mutations=random.randint(1, 3))
                    if not mutations: continue
                    # Tag the override file with the symbol it's optimizing for
                    new_path = write_override(new_cfg, mutations, f"{sym}_{run}")
                    run_id = f"qopt_{sym}_" + new_path.stem
                    print(f"  [qopt][{sym}] mutate {run} → {new_path.name}: {[(k, old, new) for k, old, new in mutations]}")
                    run_chart_sweep(new_path, [sym], run_id)
            # Save per-symbol "current best" — refresh after each iteration
            best_run, _, best_m, best_score = sym_scored[0]
            best_ovr = find_override_for_run(best_run)
            if best_ovr:
                per_sym_best_path = ROOT / "backtest_v8" / "btc_loop_results" / f"override_per_sym_{sym}_BEST.json"
                try:
                    cfg_data = json.loads(best_ovr.read_text())
                    cfg_data["_meta"] = (
                        f"Auto-promoted PER-SYMBOL best for {sym} @ {datetime.now(timezone.utc).isoformat()}. "
                        f"Source run: {best_run}. Score: {best_score:.1f}. "
                        f"Stats: trades={best_m['trades']} WR={best_m['wr']*100:.1f}% gain={best_m['total_gain_pct']:+.1f}%. "
                        f"Promoted by: quality_optimizer.py --per-sym."
                    )
                    per_sym_best_path.write_text(json.dumps(cfg_data, indent=2))
                except Exception as e:
                    print(f"  [qopt][{sym}] best-save error: {e}")
    else:
        # Global mode — mutate top-K and run on all syms simultaneously
        elites = []
        seen_runs = set()
        for run, sym, m, score in scored:
            if run in seen_runs: continue
            seen_runs.add(run)
            elites.append((run, m))
            if len(elites) >= args.top_k: break
        new_paths = []
        for run, m in elites:
            ovr_path = find_override_for_run(run)
            if not ovr_path: continue
            try: baseline = json.loads(ovr_path.read_text())
            except Exception: continue
            for j in range(args.new_per_elite):
                new_cfg, mutations = mutate(baseline, n_mutations=random.randint(1, 3))
                if not mutations: continue
                new_path = write_override(new_cfg, mutations, run)
                new_paths.append(new_path)
                print(f"  [qopt] mutate {run} → {new_path.name}: {[(k, old, new) for k, old, new in mutations]}")
        for p in new_paths:
            run_id = "qopt_" + p.stem
            run_chart_sweep(p, syms, run_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=1800)
    ap.add_argument("--syms", default="BTCUSDT")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--new-per-elite", type=int, default=2)
    ap.add_argument("--score-weights", default="avg=1,total=1,botq=1,topq=1,count=1,churn=1")
    ap.add_argument("--score-only", action="store_true", help="just score and write leaderboard, no new sweeps")
    ap.add_argument("--per-sym", action="store_true", help="optimize each symbol independently with its own elites + mutations + per-sym BEST file")
    args = ap.parse_args()
    if args.loop:
        print(f"[quality_optimizer] LOOP every {args.interval}s")
        while True:
            t0 = time.time()
            try: iteration(args)
            except Exception as e: print(f"[qopt] iteration err: {e}")
            elapsed = time.time() - t0
            time.sleep(max(60, args.interval - int(elapsed)))
    else:
        iteration(args)


if __name__ == "__main__":
    main()
