"""run_random_knob_sweep.py — Aggressive random-search across AggressiveCfg knob space.

Goal: break the +0.58 / +0.28 Sharpe ceiling. Standard grids exhausted; try wild
combinations no one has tried. 50-100 random variants on the full 114-sym tradier
universe (start=2024-04-01 → present, ~2.1yr). Multiprocess for speed.

NO-LIES compliance:
- pool_sharpe = mean(per_trade_returns)/std(per_trade_returns) — already what
  simulate_aggressive returns via run_universe's all_returns concat.
- No sqrt(252). No annualization. Per CLAUDE.md NO-LIES MANDATE.
- Sample floor: 114 syms × >1yr × usually >100 trades/sym → PASSES.

Usage on S1:
  V8_USE_VEC_ALL=1 nohup /home/niels/.conda/envs/binance_env/bin/python \
    /home/niels/binance-sandbox/run_random_knob_sweep.py \
    --n-variants 80 --workers 4 --seed 42 > /home/niels/logs/random_knob_sweep_<ts>.log 2>&1 &
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_struct_v4_aggressive import AggressiveCfg, simulate_aggressive

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "data" / "sweep_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 114-sym tradier universe (from data/struct_v4_universe.json)
TRADIER_114 = [
    "ACN","AEM","AGCO","AGI","AG","ALB","AMD","AMZN","ARM","AR",
    "ASML","ATI","AU","AVGO","BA","BABA","BG","BHP","BK","BKR",
    "CAT","CCJ","CDE","CENX","CF","CHRD","CLF","CLX","CMC","CME",
    "COIN","COST","CRWD","CSCO","CVX","DAL","DE","DIS","DKNG","DVN",
    "EGLE","EQT","FIVN","FCX","FLR","FSLR","GD","GE","GILD","GOLD",
    "GOOG","GS","HAL","HES","IBM","INTC","JD","JPM","KGC","KMI",
    "LLY","LMT","LOW","MAR","MCD","META","MOD","MPLX","MRK","MSFT",
    "MU","NET","NLR","NOC","NOV","NUKZ","NVDA","NVO","OKE","ORCL",
    "OXY","PBR","PFE","PG","PLTR","PYPL","QCOM","RIG","RTX","SHOP",
    "SMG","SNDK","SNOW","SQ","TGT","TMUS","TSLA","TSM","TXN","UNH",
    "UNP","USAR","V","VLO","VZ","WFC","WM","WMT","XLE","XOM",
    "XOP","ZIM","ZM","ZTS",
]
START_DATE = "2024-04-01"


# ════════════════════════════════════════════════════════════════════════════════
# Random knob sampler — covers entry paths, exit gates, sizing, pyramid, filters
# ════════════════════════════════════════════════════════════════════════════════

def sample_random_cfg(rng: random.Random) -> AggressiveCfg:
    """Sample one random AggressiveCfg from the full knob space."""
    c = AggressiveCfg()

    # ── ENTRY PATH SELECTION ──────────────────────────────────────────────
    # Each path independently on/off with ~70% on rate (need ≥2 paths usually)
    while True:
        c.path_A_struct_enabled = rng.random() < 0.5
        c.path_B_oversold_enabled = rng.random() < 0.65
        c.path_C_k1h_cross_enabled = rng.random() < 0.55
        c.path_D_wtD_cross_enabled = rng.random() < 0.80   # D usually best
        c.path_E_sma200_reclaim_enabled = rng.random() < 0.60
        c.path_F_weekly_flip_enabled = rng.random() < 0.50
        c.path_G_momentum_continuation_enabled = rng.random() < 0.55
        enabled = sum([c.path_A_struct_enabled, c.path_B_oversold_enabled,
                       c.path_C_k1h_cross_enabled, c.path_D_wtD_cross_enabled,
                       c.path_E_sma200_reclaim_enabled, c.path_F_weekly_flip_enabled,
                       c.path_G_momentum_continuation_enabled])
        if enabled >= 2:
            break

    # ── ENTRY KNOB DETAILS ────────────────────────────────────────────────
    # Path A
    c.path_A_breakout_tf = rng.choice(["D", "W", "4h"])
    c.path_A_retest_tf = rng.choice(["1h", "4h", "15m"])
    c.path_A_trigger_tf = rng.choice(["15m", "1h", "5m"])
    c.path_A_min_count = rng.choice([2, 3, 4, 5])
    c.path_A_atr_band = rng.choice([0.5, 1.0, 1.5, 2.0])
    c.path_A_regime_persist_bars = rng.choice([200, 500, 1000])
    # Path B
    c.path_B_k15_max = rng.choice([20.0, 30.0, 40.0, 50.0])
    c.path_B_k1h_max = rng.choice([30.0, 40.0, 50.0, 60.0])
    c.path_B_require_wt_bull_15m = rng.random() < 0.7
    c.path_B_require_reclaim_pivot_lo = rng.random() < 0.7
    # Path C
    c.path_C_k1h_cross_below = rng.choice([20.0, 30.0, 40.0, 50.0])
    # Path D
    c.path_D_rsi_D_min = rng.choice([30.0, 40.0, 50.0, 55.0])
    # Path E
    c.path_E_rsi_D_min = rng.choice([40.0, 50.0, 55.0, 60.0])
    # Path G
    c.path_G_k1h_cross_min = rng.choice([40.0, 50.0, 60.0])

    # ── HTF TREND FILTER ─────────────────────────────────────────────────
    c.htf_trend_filter_enabled = rng.random() < 0.65
    c.htf_require_wt_D_bull = rng.random() < 0.85
    c.htf_require_rsi_D_min = rng.choice([0.0, 40.0, 45.0, 50.0])

    # ── REGIME FILTER ────────────────────────────────────────────────────
    c.require_above_sma50_D = rng.random() < 0.30

    # ── EXIT SUITE ────────────────────────────────────────────────────────
    # X1 top-catch
    c.exit_X1_topcatch_enabled = rng.random() < 0.55
    c.exit_X1_k15_min = rng.choice([70.0, 75.0, 80.0, 85.0])
    c.exit_X1_require_k1h_min = rng.choice([0.0, 60.0, 70.0, 80.0])
    c.exit_X1_require_wt_bear_1h = rng.random() < 0.40

    # X2 trail
    c.exit_X2_trailing_pct = rng.choice([4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0])
    c.exit_X2_require_profit = rng.random() < 0.75

    # X3 hard ATR (mostly disabled per safer_v3 win)
    c.exit_X3_hardstop_atr_mult = rng.choice([0, 0, 0, 2.0, 3.0, 4.0])

    # X7 tech stop (FROZEN dc + abs floor) — top-performer in v24
    c.exit_X7_tech_stop_enabled = rng.random() < 0.65
    c.exit_X7_freeze_dc_tf = rng.choice(["1h", "4h", "D"])
    c.exit_X7_abs_floor_pct = rng.choice([-5.0, -8.0, -10.0, -12.0, -15.0, -20.0])
    c.exit_X7_freeze_bb_tf = rng.choice(["", "", "1h", "4h"])  # mostly off

    # X4 daily bear WT
    c.exit_X4_daily_bear_wt_enabled = rng.random() < 0.80

    # X5 structural flip
    c.exit_X5_structural_flip_enabled = rng.random() < 0.55
    c.exit_X5_min_count = rng.choice([2, 3, 4, 5])
    c.exit_X5_window_bars = rng.choice([2, 3, 5, 10])
    c.exit_X5_min_hold_bars = rng.choice([0, 12, 24, 48, 96])

    # X6 time stop
    c.exit_X6_time_stop_bars = rng.choice([0, 0, 96, 192, 384])
    c.exit_X6_min_gain_pct = rng.choice([3.0, 5.0, 10.0])

    # ── HOLD / CHURN ──────────────────────────────────────────────────────
    c.min_hold_bars = rng.choice([0, 12, 24, 48, 96])
    c.cooldown_bars_after_exit = rng.choice([5, 10, 20, 40, 80])
    c.x4_exit_extended_cooldown = rng.choice([0, 40, 80, 200])

    # ── PYRAMID ──────────────────────────────────────────────────────────
    c.pyramid_enabled = rng.random() < 0.85
    c.pyramid_require_new_D_HH = rng.random() < 0.50
    c.pyramid_min_gain_since_last_pct = rng.choice([0.5, 1.0, 2.0, 3.0, 5.0])
    c.pyramid_tf = rng.choice(["15m", "1h", "4h", "D"])
    c.pyramid_also_on_k1h_oversold = rng.random() < 0.40
    c.pyramid_on_price_breakout = rng.random() < 0.40
    c.pyramid_price_breakout_min_gain = rng.choice([0.5, 1.0, 2.0])
    c.max_pyramid_levels = rng.choice([3, 5, 8, 12, 15])
    c.pyramid_add_fraction = rng.choice([0.3, 0.5, 0.7, 0.8])

    # full entry fraction stays at 1.0 (use all capital each entry)
    c.full_entry_fraction = 1.0
    c.round_trip_cost_pct = 0.0  # tradier = no commission

    return c


# ════════════════════════════════════════════════════════════════════════════════
# Single-variant worker
# ════════════════════════════════════════════════════════════════════════════════

def run_variant(args: Tuple[int, Dict[str, Any], str]) -> Dict[str, Any]:
    """Worker — runs one cfg across 114 syms, returns metrics."""
    variant_idx, cfg_dict, start_date = args
    cfg = AggressiveCfg(**cfg_dict)
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    all_returns: List[float] = []
    rows = []
    t0 = time.time()
    skip_count = 0
    for sym in TRADIER_114:
        try:
            r = simulate_aggressive(sym, "tradier", cfg, start_ts=start_ts, return_events=False)
        except Exception as e:
            skip_count += 1
            continue
        if r.get("skip"):
            skip_count += 1
            continue
        # NOTE: even if 0 trades, count this sym in rows so we know we evaluated it
        all_returns.extend(r.get("trade_returns") or [])
        if not r.get("trade_returns"):
            continue
        rows.append({
            "sym": r["sym"], "years": r.get("years", 0.0),
            "trades": r["trades"], "wr_pct": r.get("wr_pct", 0.0),
            "avg_gain_trade": r.get("avg_gain_pct", 0.0),
            "compound_mult": r.get("compound_mult", 1.0),
            "ratio_vs_bh": r.get("ratio_vs_bh", 0.0),
            "bh_mult": r.get("bh_mult", 1.0),
        })
    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)
    arr = np.array(all_returns, dtype=np.float64) if all_returns else np.array([0.0])
    pool_sharpe = float(arr.mean() / arr.std()) if (len(arr) > 1 and arr.std() > 0) else 0.0
    # Per-symbol sharpe (mean of per-sym sharpes, capped ±5)
    sym_sharpes = []
    # Per-sym sharpe needs reconstructing trade list per sym; we didn't keep that.
    # Use ratio-based proxy: gain_per_trade summary
    # v8_struct_v4_aggressive.trade_returns are already in PERCENT units (e.g. 4.0 = +4%)
    avg_gain_trade = float(arr.mean()) if len(arr) > 0 else 0.0  # %/trade
    n_years = rows[0]["years"] if rows else 2.1
    acc_gain = sum(r["compound_mult"] - 1.0 for r in rows) / max(n_syms, 1) * 100.0  # avg cross-sym
    gain_per_yr = acc_gain / max(n_years, 0.01)
    gain_sym_yr = gain_per_yr  # since acc_gain is already cross-sym-avg
    # DD = worst sym (1 - min compound_mult)
    if rows:
        min_cm = min(r["compound_mult"] for r in rows)
        max_dd_pct = (1.0 - min_cm) * 100.0 if min_cm < 1.0 else 0.0
    else:
        max_dd_pct = 0.0
    # Trade-weighted DD (worst single-trade)
    worst_trade_pct = float(arr.min()) if len(arr) > 0 else 0.0

    return {
        "variant_idx": variant_idx,
        "pool_sharpe": pool_sharpe,
        "sym_sharpe": 0.0,  # placeholder
        "avg_gain_trade": avg_gain_trade,
        "gain_per_yr": gain_per_yr,
        "gain_sym_yr": gain_sym_yr,
        "trades": int(total_trades),
        "max_dd_pct": max_dd_pct,
        "worst_trade_pct": worst_trade_pct,
        "n_syms": n_syms,
        "years": float(n_years),
        "elapsed_s": elapsed,
        "skip_count": skip_count,
        "cfg": cfg_dict,
    }


# ════════════════════════════════════════════════════════════════════════════════
# Driver
# ════════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-variants", type=int, default=80)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start-date", default=START_DATE)
    ap.add_argument("--label", default="random_knob_sweep")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    ts_label = int(time.time())
    out_csv = RESULTS_DIR / f"{args.label}_{ts_label}.csv"
    out_jsonl = RESULTS_DIR / f"{args.label}_{ts_label}.jsonl"

    print(f"[random_knob_sweep] {args.n_variants} variants, {args.workers} workers, seed={args.seed}")
    print(f"  start={args.start_date}  universe=tradier_114  n_syms={len(TRADIER_114)}")
    print(f"  CSV  → {out_csv}")
    print(f"  JSONL→ {out_jsonl}")

    # Sample all variants up-front so we can reproduce
    variants = []
    for i in range(args.n_variants):
        c = sample_random_cfg(rng)
        variants.append((i, asdict(c), args.start_date))

    # Write CSV header
    canonical_cols = ["variant_idx", "pool_sharpe", "sym_sharpe", "avg_gain_trade",
                      "gain_per_yr", "gain_sym_yr", "trades", "max_dd_pct",
                      "worst_trade_pct", "n_syms", "years", "elapsed_s", "skip_count"]
    cfg_keys = [f.name for f in fields(AggressiveCfg)]
    all_cols = canonical_cols + cfg_keys
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
        w.writeheader()

    results: List[Dict[str, Any]] = []
    t0 = time.time()

    # Use ProcessPoolExecutor — each variant is independent
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_variant, v): v[0] for v in variants}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                print(f"  [{idx}] FAILED: {e}", flush=True)
                continue
            results.append(r)
            # Stream to CSV + JSONL
            row = {k: r[k] for k in canonical_cols if k in r}
            row.update(r["cfg"])
            with open(out_csv, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
                w.writerow(row)
            with open(out_jsonl, "a") as f:
                f.write(json.dumps({**{k: r[k] for k in canonical_cols if k in r},
                                    "cfg": r["cfg"]}) + "\n")
            elapsed = time.time() - t0
            done = len(results)
            eta_s = (elapsed / done) * (args.n_variants - done) if done > 0 else 0
            print(f"  [{idx:>3}] sharpe={r['pool_sharpe']:+.4f} "
                  f"trades={r['trades']:>6} dd={r['max_dd_pct']:>5.1f}% "
                  f"n_syms={r['n_syms']:>3} elapsed={r['elapsed_s']:.0f}s "
                  f"  ({done}/{args.n_variants} ETA {eta_s/60:.1f}min)", flush=True)

    # ─── Final ranking ───────────────────────────────────────────────────
    results.sort(key=lambda r: r["pool_sharpe"], reverse=True)

    print("\n" + "═"*100)
    print(f"  TOP 20 BY POOL_SHARPE (total {len(results)} variants, {time.time()-t0:.0f}s)")
    print("═"*100)
    print(f"  {'rank':>4} {'idx':>4} {'pool_sharpe':>13} {'trades':>7} {'dd%':>6} {'gain/yr%':>10}  n_syms")
    for i, r in enumerate(results[:20]):
        print(f"  {i+1:>4} {r['variant_idx']:>4} {r['pool_sharpe']:>+13.4f} "
              f"{r['trades']:>7} {r['max_dd_pct']:>5.1f}% {r['gain_per_yr']:>+9.2f}% "
              f" {r['n_syms']}")

    # ─── Honest verdict ──────────────────────────────────────────────────
    best = results[0] if results else None
    if best:
        print("\n" + "─"*100)
        print(f"  BEST: pool_sharpe={best['pool_sharpe']:.4f} "
              f"avg_gain_trade={best['avg_gain_trade']:.4f}%/trade "
              f"gain_per_yr={best['gain_per_yr']:.2f}%/yr "
              f"trades={best['trades']} dd={best['max_dd_pct']:.1f}% "
              f"n_syms={best['n_syms']} years={best['years']:.2f}")
        cleared_1 = sum(1 for r in results if r["pool_sharpe"] > 1.0)
        cleared_2 = sum(1 for r in results if r["pool_sharpe"] > 2.0)
        print(f"  Sharpe > 1.0: {cleared_1} / {len(results)}")
        print(f"  Sharpe > 2.0: {cleared_2} / {len(results)}")

    # Write top-10 JSON for downstream report
    top10_path = RESULTS_DIR / f"{args.label}_{ts_label}_top10.json"
    with open(top10_path, "w") as f:
        json.dump({
            "label": args.label, "ts": ts_label, "start": args.start_date,
            "n_variants": args.n_variants, "n_syms": len(TRADIER_114),
            "top10": results[:10],
        }, f, indent=2, default=str)
    print(f"\n  Top-10 saved → {top10_path}")


if __name__ == "__main__":
    main()
