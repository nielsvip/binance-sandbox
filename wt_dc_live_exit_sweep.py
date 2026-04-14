#!/usr/bin/env python3
"""
WT/DC LIVE EXIT SWEEP — clean rewrite 2026-04-10.

Uses the LIVE `wt_dc_delta.DeltaTracker` directly (not the standalone engine's
exit logic) so the sweep tests exactly what the production system will do when
`DELTA_EXIT_DOM_TF_ENABLED=True` is flipped.

Key fixes over the old `wt_dc_exit_sweep.py`:
  - Calls the LIVE tracker (post 2026-04-10 bugfix that added position_state arg)
  - Realistic metrics: no compound overflow, proper per-trade stats
  - Slippage + fees modeled: 0.04% taker × 2 sides + 0.02% slippage = 0.10% round trip
  - Fixed $100 notional per trade (no compounding, no scaling into infinity)
  - Sharpe is per-trade-based, NOT annualized-with-avg-hold hack
  - Reports: WR, PF, avg_pnl, gross_profit, gross_loss, max_dd, expectancy

Sweep dimensions match the new wt_dc_delta.py exit block keys:
  exit_zero_speed_threshold, exit_slowdown_pct, exit_decel_bars,
  exit_speed_decay_pct, exit_min_tf_lost, exit_dominant_tf

Usage:
    python3 wt_dc_live_exit_sweep.py              # 192 combo default
    python3 wt_dc_live_exit_sweep.py --quick      # 8 combo smoke
    python3 wt_dc_live_exit_sweep.py --target-wr 90
"""
import argparse
import itertools
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

BASE_PATH = Path("/Users/niels/Documents/binance")
NPZ_DIR = BASE_PATH / "backtest_v4" / "indicators"
RESULTS_DIR = BASE_PATH / "data" / "wt_dc_research"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_PATH))
from wt_dc_delta import DeltaTracker, TFS, WT_DELTA_FIELDS, DC_DELTA_FIELDS, DC_POSITION_FIELDS

# Realistic cost model (crypto futures, taker rates)
FEE_PCT = 0.04  # Binance futures taker 0.04% per side
SLIPPAGE_PCT = 0.02  # 0.02% typical market order slip
COST_PER_TRADE = (FEE_PCT * 2 + SLIPPAGE_PCT) / 100.0  # round trip

NOTIONAL_USD = 100.0  # fixed per trade — no compounding


def base_cfg() -> dict:
    """Starting point for the sweep — matches live DEFAULT_CFG."""
    return {
        "tf_weights": {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
        "entry_min_tf": 4,
        "entry_speed_threshold": 0.5,
        "exit_speed_decay_pct": 50.0,
        "exit_min_tf_lost": 2,
        "exit_min_hold": 4,
        "exit_zero_speed_threshold": 0.5,
        "exit_slowdown_pct": 30,
        "exit_decel_bars": 2,
        "exit_dominant_tf": "ANY",
        "pyramid_min_tf": 4,
        "pyramid_price_tolerance": 0.02,
        "pyramid_qty_mult": 1.5,
        "pyramid_max": 8,
    }


def extract_indicators_dict(d: dict, i: int, n: int) -> dict:
    """Build a single-bar indicators dict from the NPZ row.
    Matches the shape that live `get_hot_state()` returns."""
    ind = {}
    # Timestamps — use bar index × 3 minutes for deterministic ts
    ind["_tick_ts"] = float(i * 180)
    ind["ts"] = float(i * 180)
    # Extract every key that looks like a TF-suffixed field
    for k, arr in d.items():
        if k in ("close", "high", "low", "open", "volume", "ts", "_ts"):
            continue
        try:
            ind[k] = float(arr[i])
        except (TypeError, ValueError, IndexError):
            pass
    # Also load prev values one bar back (where they aren't precomputed)
    if i > 0:
        for field in ["dc_basis", "dc_high", "dc_low"]:
            for tf in TFS:
                k = f"{field}_{tf}"
                k_prev = f"{field}_{tf}_prev"
                if k_prev not in ind and k in ind:
                    try:
                        ind[k_prev] = float(d[k][i-1]) if k in d else ind[k]
                    except Exception:
                        pass
    return ind


def simulate_symbol(symbol: str, data: dict, cfg: dict) -> list:
    """Run the live DeltaTracker through every bar of one symbol's NPZ.
    Returns list of closed trades."""
    tracker = DeltaTracker(cfg=cfg)
    n = len(data["close"])
    closes = data["close"].astype(np.float64)
    trades = []
    pos_side = None  # "LONG" or "SHORT" or None
    entry_px = 0.0
    entry_idx = -1
    n_entries = 1
    max_speed_long = 0.0
    max_speed_short = 0.0

    for i in range(50, n):  # skip warmup
        px = float(closes[i])
        if px <= 0:
            continue
        ind = extract_indicators_dict(data, i, n)

        # Build position_state for the tracker (matches live signature)
        if pos_side:
            pstate = {
                "side": pos_side,
                "n_entries": n_entries,
                "last_entry_price": entry_px,
                "max_speed": max_speed_long if pos_side == "LONG" else max_speed_short,
            }
        else:
            pstate = None

        try:
            sig = tracker.update(symbol, ind, pstate)
        except Exception:
            continue

        if pos_side is None:
            # Flat — check for entry
            if sig.entry_long:
                pos_side = "LONG"
                entry_px = px
                entry_idx = i
                n_entries = 1
                max_speed_long = float(getattr(sig, "bull_speed", 0) or 0)
            elif sig.entry_short:
                pos_side = "SHORT"
                entry_px = px
                entry_idx = i
                n_entries = 1
                max_speed_short = float(getattr(sig, "bear_speed", 0) or 0)
        else:
            # In a position — track max speed then check exit
            if pos_side == "LONG":
                max_speed_long = max(max_speed_long, float(getattr(sig, "bull_speed", 0) or 0))
                if sig.exit_long:
                    pnl_pct = ((px - entry_px) / entry_px) - COST_PER_TRADE
                    trades.append({
                        "sym": symbol, "side": "LONG",
                        "entry_idx": entry_idx, "exit_idx": i,
                        "entry_px": entry_px, "exit_px": px,
                        "pnl_pct": pnl_pct,
                        "pnl_usd": pnl_pct * NOTIONAL_USD,
                        "bars": i - entry_idx,
                        "reason": str(getattr(sig, "tf_lost", "") or "exit"),
                    })
                    pos_side = None
                    max_speed_long = 0.0
            else:  # SHORT
                max_speed_short = max(max_speed_short, float(getattr(sig, "bear_speed", 0) or 0))
                if sig.exit_short:
                    pnl_pct = ((entry_px - px) / entry_px) - COST_PER_TRADE
                    trades.append({
                        "sym": symbol, "side": "SHORT",
                        "entry_idx": entry_idx, "exit_idx": i,
                        "entry_px": entry_px, "exit_px": px,
                        "pnl_pct": pnl_pct,
                        "pnl_usd": pnl_pct * NOTIONAL_USD,
                        "bars": i - entry_idx,
                        "reason": str(getattr(sig, "tf_lost", "") or "exit"),
                    })
                    pos_side = None
                    max_speed_short = 0.0

    return trades


def compute_stats(trades: list) -> dict:
    """Realistic per-trade stats — no compound overflow, no Sharpe annualization hack."""
    if not trades:
        return {"n": 0, "wr": 0, "pf": 0, "sharpe": 0, "avg_pnl_pct": 0,
                "total_pnl_usd": 0, "gross_profit_usd": 0, "gross_loss_usd": 0,
                "max_dd_usd": 0, "avg_win_pct": 0, "avg_loss_pct": 0,
                "avg_hold_bars": 0, "expectancy_pct": 0, "longs": 0, "shorts": 0,
                "status": "no_trades"}
    pnls_pct = np.array([t["pnl_pct"] for t in trades])
    pnls_usd = np.array([t["pnl_usd"] for t in trades])
    wins_mask = pnls_pct > 0
    losses_mask = pnls_pct < 0
    wins = pnls_pct[wins_mask]
    losses = pnls_pct[losses_mask]
    n = len(trades)
    n_wins = int(wins_mask.sum())
    n_losses = int(losses_mask.sum())
    wr = n_wins / n * 100 if n > 0 else 0
    avg = float(pnls_pct.mean()) * 100
    avg_win = float(wins.mean()) * 100 if len(wins) > 0 else 0
    avg_loss = float(losses.mean()) * 100 if len(losses) > 0 else 0
    gross_profit_usd = float(pnls_usd[wins_mask].sum()) if n_wins > 0 else 0
    gross_loss_usd = float(-pnls_usd[losses_mask].sum()) if n_losses > 0 else 1e-9
    pf = gross_profit_usd / gross_loss_usd if gross_loss_usd > 0 else 0
    total_usd = float(pnls_usd.sum())
    # Per-trade Sharpe — NOT annualized; simple mean/std ratio
    std = float(pnls_pct.std()) if n > 1 else 1e-9
    sharpe = (float(pnls_pct.mean()) / std) if std > 1e-10 else 0.0
    # Max USD drawdown on equity curve (no compounding — simple cumsum)
    cum = np.cumsum(pnls_usd)
    peak = np.maximum.accumulate(cum)
    max_dd_usd = float((peak - cum).max()) if len(cum) > 0 else 0
    # Expectancy
    expectancy = (wr / 100.0) * avg_win + ((100 - wr) / 100.0) * avg_loss
    avg_hold = float(np.mean([t["bars"] for t in trades]))
    longs = sum(1 for t in trades if t["side"] == "LONG")
    shorts = sum(1 for t in trades if t["side"] == "SHORT")
    return {
        "n": n,
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "sharpe": round(sharpe, 4),
        "avg_pnl_pct": round(avg, 4),
        "total_pnl_usd": round(total_usd, 2),
        "gross_profit_usd": round(gross_profit_usd, 2),
        "gross_loss_usd": round(gross_loss_usd, 2),
        "max_dd_usd": round(max_dd_usd, 2),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "avg_hold_bars": round(avg_hold, 1),
        "expectancy_pct": round(expectancy, 4),
        "longs": longs,
        "shorts": shorts,
        "status": "ok",
    }


def run_combo(combo: dict, files: list) -> dict:
    cfg = base_cfg()
    cfg.update({
        "exit_zero_speed_threshold": combo["zero_thresh"],
        "exit_slowdown_pct": combo["slowdown_pct"],
        "exit_decel_bars": combo["decel_bars"],
        "exit_speed_decay_pct": combo["decay_pct"],
        "exit_min_tf_lost": combo["min_tf_lost"],
        "exit_dominant_tf": combo["dom_tf"],
    })
    all_trades = []
    t0 = time.time()
    for fname in files:
        sym = fname.replace(".npz", "")
        try:
            with np.load(NPZ_DIR / fname, allow_pickle=True) as f:
                data = {k: f[k] for k in f.files}
            trades = simulate_symbol(sym, data, cfg)
            all_trades.extend(trades)
        except Exception as e:
            print(f"  SKIP {sym}: {e}", flush=True)
    elapsed = time.time() - t0
    stats = compute_stats(all_trades)
    stats.update({**combo, "elapsed_s": round(elapsed, 1)})
    return stats


DEFAULT_GRID = {
    "dom_tf": ["3m", "15m", "1h", "ANY"],       # 4
    "slowdown_pct": [20, 30, 40, 50],            # 4
    "decel_bars": [1, 2],                         # 2
    "decay_pct": [50, 70, 80],                    # 3
    "min_tf_lost": [1, 2],                        # 2
    "zero_thresh": [0.5],                         # 1
}  # = 4*4*2*3*2*1 = 192 combos

QUICK_GRID = {
    "dom_tf": ["3m", "15m", "1h", "ANY"],
    "slowdown_pct": [30],
    "decel_bars": [2],
    "decay_pct": [50, 80],
    "min_tf_lost": [2],
    "zero_thresh": [0.5],
}  # = 4*1*1*2*1*1 = 8


def build_combos(grid: dict) -> list:
    keys = list(grid.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]


def combo_name(c: dict) -> str:
    return f"dom={c['dom_tf']}|slow={c['slowdown_pct']}%|decel={c['decel_bars']}|decay={c['decay_pct']}%|tf_lost<{c['min_tf_lost']}"


def print_report(results: list, top_n: int = 20):
    ok = [r for r in results if r.get("status") == "ok" and r.get("n", 0) > 0]
    print(f"\n{'='*128}")
    print(f"  WT/DC LIVE EXIT SWEEP REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*128}")
    print(f"  Combos: {len(results)} | OK with trades: {len(ok)} | Empty: {len(results) - len(ok)}")
    if not ok:
        return
    candidates = [r for r in ok if r["wr"] >= 70 and r["n"] >= 1000 and r["pf"] >= 2.0]
    print(f"  PRODUCTION CANDIDATES (WR≥70, n≥1000, PF≥2.0): {len(candidates)}")

    print(f"\n  TOP {top_n} BY WIN RATE:")
    print(f"  {'#':<3} {'WR%':>6} {'PF':>6} {'Sharpe':>8} {'Avg%':>8} {'NetUSD':>10} {'MaxDD$':>9} {'Trades':>7} {'Hold':>5}  Combo")
    print(f"  {'-'*124}")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["wr"])[:top_n], 1):
        print(f"  {i:<3} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['sharpe']:>+8.4f} {r['avg_pnl_pct']:>+7.4f}% {r['total_pnl_usd']:>+10.2f} {r['max_dd_usd']:>9.2f} {r['n']:>7} {r['avg_hold_bars']:>5.1f}  {combo_name(r)}")

    print(f"\n  TOP 10 BY PROFIT FACTOR:")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["pf"])[:10], 1):
        print(f"  {i:<3} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['sharpe']:>+8.4f} {r['avg_pnl_pct']:>+7.4f}% {r['total_pnl_usd']:>+10.2f} {r['n']:>7}  {combo_name(r)}")

    print(f"\n  TOP 10 BY NET PnL USD ($100 per trade):")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["total_pnl_usd"])[:10], 1):
        print(f"  {i:<3} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['sharpe']:>+8.4f} {r['avg_pnl_pct']:>+7.4f}% {r['total_pnl_usd']:>+10.2f} {r['n']:>7}  {combo_name(r)}")

    # Per-dimension aggregates
    print(f"\n  PER-DIMENSION MEAN WR% (across all combos at that setting):")
    for dim in ["dom_tf", "slowdown_pct", "decel_bars", "decay_pct", "min_tf_lost"]:
        buckets = defaultdict(list)
        for r in ok:
            buckets[r[dim]].append(r["wr"])
        row = "  " + dim + ": "
        for k in sorted(buckets.keys(), key=lambda x: -sum(buckets[x]) / len(buckets[x])):
            row += f"{k}={sum(buckets[k])/len(buckets[k]):.1f}%  "
        print(row)

    print(f"{'='*128}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--target-wr", type=float, default=0.0)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    grid = QUICK_GRID if args.quick else DEFAULT_GRID
    combos = build_combos(grid)
    if args.limit > 0:
        combos = combos[:args.limit]

    files = sorted([f for f in os.listdir(NPZ_DIR) if f.endswith(".npz")])
    print(f"\n{'='*128}")
    print(f"  WT/DC LIVE EXIT SWEEP (calls live DeltaTracker directly)")
    print(f"  Symbols: {len(files)} | Combos: {len(combos)}")
    print(f"  NPZ: {NPZ_DIR}")
    print(f"  Notional: ${NOTIONAL_USD}/trade | Fees+slip: {COST_PER_TRADE*100:.3f}% round trip")
    print(f"  Target WR: {args.target_wr if args.target_wr > 0 else 'none'}")
    print(f"{'='*128}\n")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    progress_path = RESULTS_DIR / f"live_exit_sweep_progress_{ts}.json"
    results = []
    t_start = time.time()
    best_wr = 0.0
    for i, c in enumerate(combos, 1):
        r = run_combo(c, files)
        results.append(r)
        if r.get("wr", 0) > best_wr:
            best_wr = r["wr"]
        eta = (time.time() - t_start) / i * (len(combos) - i)
        print(f"  [{i:>3}/{len(combos)}] {r['status']:<10} WR={r.get('wr',0):>5.1f}% PF={r.get('pf',0):>6.2f} Sh={r.get('sharpe',0):>+7.4f} avg={r.get('avg_pnl_pct',0):>+7.4f}% net=${r.get('total_pnl_usd',0):>+9.2f} n={r.get('n',0):>6} ({r['elapsed_s']:>5.0f}s, eta {eta/60:.0f}m, best WR={best_wr:.1f}%) | {combo_name(c)}", flush=True)
        with open(progress_path, "w") as f:
            json.dump({"completed": i, "total": len(combos), "best_wr": best_wr, "results": results}, f, indent=2, default=str)
        if args.target_wr > 0 and best_wr >= args.target_wr:
            print(f"\n  🎯 Target WR {args.target_wr}% hit at combo {i}")
            break

    print_report(results)
    out = RESULTS_DIR / f"live_exit_sweep_{ts}.json"
    with open(out, "w") as f:
        json.dump({"args": vars(args), "best_wr": best_wr, "results": results,
                   "elapsed_total_s": round(time.time() - t_start, 1)}, f, indent=2, default=str)
    print(f"  Saved: {out}\n")


if __name__ == "__main__":
    main()
