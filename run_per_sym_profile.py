#!/usr/bin/env python3
"""Per-sym custom profile builder for 8 flz BTC_DEDICATED symbols.

Phase 1: Baseline eval under override_btc_BEST.json
Phase 2: Per-sym mutation search for syms below promote criteria
Phase 3: Write winning configs to data/hourly_reconfig/_candidates/

Promote criteria (feedback_revised_tier_trade_count_weighted_20260501.md):
  pool_sharpe >= 0.3 AND trades >= 5000 AND max_dd <= 10% AND
  gain_per_yr >= 100% AND wr >= 60%
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np

import metrics_guard as mg
from v8_quick_engine import simulate, iter_npz, QuickConfig

BEST_JSON = ROOT / "backtest_v8" / "btc_loop_results" / "override_btc_BEST.json"
NPZ_DIR = str(ROOT / "backtest_v8" / "indicators")
START_DATE = "2024-07-01"
CANDIDATES_DIR = ROOT / "data" / "hourly_reconfig" / "_candidates"
SWEEP_CSV_DIR = ROOT / "data" / "sweep_results"

SYMS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC",
        "XRPUSDC", "DOGEUSDC", "ZECUSDC", "BTCDOMUSDT"]

# Promote criteria
PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0

YEARS_SPAN = (2026.33 - 2024.5)  # 2024-07-01 to ~2026-04-30 = ~1.83 yr


def load_best_overrides() -> Dict:
    with open(BEST_JSON) as f:
        return json.load(f)


def make_cfg(overrides: Dict) -> QuickConfig:
    cfg = QuickConfig()
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool):
                setattr(cfg, k, bool(v))
            elif isinstance(cur, int):
                setattr(cfg, k, int(v))
            elif isinstance(cur, float):
                setattr(cfg, k, float(v))
            else:
                setattr(cfg, k, v)
    cfg._clamp_overrides()
    return cfg


def run_single_sym(sym: str, overrides: Dict) -> Dict:
    """Run simulate on one symbol, return result dict."""
    cfg = make_cfg(overrides)
    stores = iter_npz("crypto", [sym], START_DATE, NPZ_DIR)
    r = simulate(stores, cfg)
    return r


def score(r: Dict) -> float:
    """Effective score = pool_sharpe * sqrt(trades/1000) (revised tier framework)."""
    ps = r.get("pool_sharpe", 0.0)
    trades = r.get("trades", 0)
    if ps <= 0 or trades == 0:
        return 0.0
    return ps * math.sqrt(trades / 1000.0)


def verdict(r: Dict, years: float = YEARS_SPAN) -> str:
    ps = r.get("pool_sharpe", 0.0)
    trades = r.get("trades", 0)
    dd = r.get("max_dd_pct", 999.0)
    wr = r.get("wr", 0.0)
    acc = r.get("accumulated_gain_pct", 0.0)
    gain_yr = acc / years if years > 0 else 0.0
    if dd > PROMOTE_DD_MAX:
        return "REJECT_DD"
    if wr < PROMOTE_WR_MIN:
        return "REJECT_WR"
    if trades < PROMOTE_TRADES_MIN:
        return "REJECT_TRADES"
    if gain_yr < PROMOTE_GAIN_YR_MIN:
        return "REJECT_PNL"
    if ps < PROMOTE_POOL_MIN:
        return "DIAGNOSTIC"
    return "PROMOTE"


def canonical_line(sym: str, r: Dict, years: float = YEARS_SPAN) -> str:
    ps = r.get("pool_sharpe", 0.0)
    ss = r.get("sym_sharpe", 0.0)
    wr = r.get("wr", 0.0)
    trades = r.get("trades", 0)
    dd = r.get("max_dd_pct", 0.0)
    acc = r.get("accumulated_gain_pct", 0.0)
    avg_gain = r.get("avg_pnl_pct", acc / trades if trades else 0.0)
    gain_yr = acc / years
    gain_sym_yr = acc / 1 / years  # per-sym (single sym run)
    v = verdict(r, years)
    return (
        f"{sym} | pool={ps:.4f} | sym={ss:.4f} | wr={wr:.1f}% | "
        f"avg_gain_trade={avg_gain:.3f}% | gain_per_yr={gain_yr:.1f}% | "
        f"gain_sym_yr={gain_sym_yr:.1f}% | trades={trades} | dd={dd:.2f}% | "
        f"timespan=1sym×{years:.2f}yr×{START_DATE} | {v}"
    )


def write_canonical_csv(csv_path: Path, sym: str, tag: str, r: Dict,
                        years: float = YEARS_SPAN) -> None:
    ps = r.get("pool_sharpe", 0.0)
    ss = r.get("sym_sharpe", 0.0)
    trades = r.get("trades", 0)
    dd = r.get("max_dd_pct", 0.0)
    acc = r.get("accumulated_gain_pct", 0.0)
    avg_gain = r.get("avg_pnl_pct", acc / trades if trades else 0.0)
    gain_yr = acc / years
    gain_sym_yr = acc / 1 / years
    row = {
        "pool_sharpe": ps,
        "sym_sharpe": ss,
        "avg_gain_trade": avg_gain,
        "gain_per_yr": gain_yr,
        "gain_sym_yr": gain_sym_yr,
        "trades": trades,
        "max_dd_pct": dd,
        "n_syms": 1,
        "years": years,
        "sym": sym,
        "tag": tag,
        "wr": r.get("wr", 0.0),
        "acc_gain_pct": acc,
        "eff_score": score(r),
        "verdict": verdict(r, years),
    }
    mg.write_sharpe_row(csv_path, row, mode="crypto", append=True)


def perturb_overrides(base: Dict, rng: random.Random) -> Dict:
    """Randomly perturb BTC_* and related knobs by ±20%."""
    knobs = [
        "BTC_BREAKOUT_HTF_MIN_ALIGNED",
        "BTC_BREAKOUT_ACCEL_MIN_TFS",
        "BTC_BREAKOUT_MIN_HOLD_BARS",
        "BTC_BREAKOUT_COOLDOWN_BARS",
        "BTC_ACCEL_RAMP_MIN_TFS",
        "BTC_TECH_EXIT_WT_MIN_TFS",
        "BTC_COOLDOWN_BARS",
        "BTC_MIN_HOLD_BARS",
        "BTC_HARD_LOSS_USD_PER_TRADE",
        "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE",
        "BTC_FOLLOW_THROUGH_MIN_MOVE_PCT",
        "BTC_RZ_PROXIMITY_PCT",
        "ENTRY_SCORE_THRESHOLD",
        "REENTRY_RALLY_K15M_MAX",
        "BTC_FIB_LOOKBACK_W",
        "BTC_ROUND_BANDS_EACH_SIDE",
        "BTC_HEDGE_MIN_HOLD_BARS",
        "MIN_HOLD_BARS_BEFORE_EXIT",
        "ATR_ADAPTIVE_SIZING_TARGET_PCT",
        "ATR_LONG_WINDOW",
    ]
    mut = dict(base)
    # pick 3-6 knobs to mutate
    n_mut = rng.randint(3, 6)
    chosen = rng.sample([k for k in knobs if k in base], min(n_mut, sum(1 for k in knobs if k in base)))
    for k in chosen:
        v = base[k]
        if isinstance(v, bool):
            continue
        factor = rng.uniform(0.80, 1.20)
        if isinstance(v, int):
            new_v = max(1, int(round(v * factor)))
            mut[k] = new_v
        elif isinstance(v, float):
            new_v = v * factor
            mut[k] = new_v
    # Also randomly toggle some bool knobs
    bool_knobs = [
        "BTC_BREAKOUT_BLOCK_OPPOSING_DIV",
        "BTC_BREAKOUT_REQUIRE_HTF_ALIGNED",
        "BTC_DIVERGENCE_BLOCK_AGAINST",
        "BTC_DIVERGENCE_EXIT_AGAINST",
        "BTC_REVERSE_ON_EXIT_ENABLED",
        "BTC_FOLLOW_THROUGH_REENTRY_ENABLED",
        "BTC_ACCEL_RAMP_REQUIRE_POSITIVE",
        "BTC_RZ_WT_DC_MULTIFACTOR",
    ]
    if rng.random() < 0.4:
        bk = rng.choice([k for k in bool_knobs if k in base])
        mut[bk] = not base[bk]
    return mut


def phase1_baseline(syms: List[str], best_overrides: Dict) -> Dict[str, Dict]:
    """Phase 1: evaluate all 8 syms under BEST config."""
    results = {}
    for sym in syms:
        print(f"\n[Phase 1] {sym} under BEST...", flush=True)
        t0 = time.time()
        r = run_single_sym(sym, best_overrides)
        elapsed = time.time() - t0
        results[sym] = r
        line = canonical_line(sym, r)
        print(f"  {line}  [{elapsed:.1f}s]", flush=True)
    return results


def phase2_mutation(sym: str, best_overrides: Dict,
                    csv_path: Path,
                    n_iters: int = 40) -> Tuple[Dict, Dict]:
    """Phase 2: random mutation search for one sym.
    Returns (best_overrides, best_result)."""
    rng = random.Random(hash(sym) & 0xFFFFFFFF)
    best_r = run_single_sym(sym, best_overrides)
    best_ov = dict(best_overrides)
    write_canonical_csv(csv_path, sym, "BEST_base", best_r)
    best_s = score(best_r)
    print(f"  [Phase 2] {sym} base score={best_s:.4f}", flush=True)
    for i in range(n_iters):
        ov = perturb_overrides(best_overrides, rng)
        r = run_single_sym(sym, ov)
        s = score(r)
        dd = r.get("max_dd_pct", 999.0)
        wr = r.get("wr", 0.0)
        acc = r.get("accumulated_gain_pct", 0.0)
        gain_yr = acc / YEARS_SPAN
        tag = f"mut_{i+1:03d}"
        write_canonical_csv(csv_path, sym, tag, r)
        # only accept if within hard constraints
        if dd <= PROMOTE_DD_MAX and wr >= PROMOTE_WR_MIN and gain_yr >= PROMOTE_GAIN_YR_MIN:
            if s > best_s:
                best_s = s
                best_r = r
                best_ov = ov
                print(f"  [Phase 2] {sym} iter={i+1} NEW BEST score={s:.4f} pool={r.get('pool_sharpe',0):.4f} trades={r.get('trades',0)} dd={dd:.2f}%", flush=True)
    return best_ov, best_r


def needs_mutation(r: Dict) -> bool:
    """Return True if sym needs mutation (doesn't meet promote criteria under BEST)."""
    v = verdict(r)
    return v != "PROMOTE"


def main():
    print("=" * 70, flush=True)
    print("Per-sym flz profile builder — 8 BTC_DEDICATED symbols", flush=True)
    print(f"BEST config: {BEST_JSON}", flush=True)
    print(f"Start date: {START_DATE}  |  NPZ dir: {NPZ_DIR}", flush=True)
    print(f"Promote: pool≥{PROMOTE_POOL_MIN} AND trades≥{PROMOTE_TRADES_MIN} AND "
          f"dd≤{PROMOTE_DD_MAX}% AND gain_yr≥{PROMOTE_GAIN_YR_MIN}% AND wr≥{PROMOTE_WR_MIN}%", flush=True)
    print("=" * 70, flush=True)

    best_overrides = load_best_overrides()

    # Phase 1
    print("\n### PHASE 1 — BASELINE UNDER BEST ###", flush=True)
    phase1_results = phase1_baseline(SYMS, best_overrides)

    print("\n### PHASE 1 SUMMARY ###", flush=True)
    for sym in SYMS:
        print(f"  {canonical_line(sym, phase1_results[sym])}", flush=True)

    # Phase 2 — mutate syms that don't promote under BEST
    print("\n### PHASE 2 — MUTATION SEARCH FOR NON-PROMOTERS ###", flush=True)
    final_overrides: Dict[str, Dict] = {}
    final_results: Dict[str, Dict] = {}
    mutation_done: Dict[str, bool] = {}

    for sym in SYMS:
        r1 = phase1_results[sym]
        if not needs_mutation(r1):
            print(f"  {sym}: ALREADY PROMOTES under BEST — skipping mutation", flush=True)
            final_overrides[sym] = dict(best_overrides)
            final_results[sym] = r1
            mutation_done[sym] = False
        else:
            v = verdict(r1)
            print(f"\n  {sym}: {v} — running mutation search...", flush=True)
            csv_path = SWEEP_CSV_DIR / f"canonical_per_sym_flz_{sym}.csv"
            best_ov, best_r = phase2_mutation(sym, best_overrides, csv_path, n_iters=40)
            final_overrides[sym] = best_ov
            final_results[sym] = best_r
            mutation_done[sym] = True
            v2 = verdict(best_r)
            print(f"  {sym} post-mutation: {canonical_line(sym, best_r)}", flush=True)
            print(f"  {sym} mutation verdict: {v} → {v2}", flush=True)

    # Phase 3 — save winner configs
    print("\n### PHASE 3 — SAVING PER-SYM PROFILES ###", flush=True)
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths = []
    for sym in SYMS:
        v = verdict(final_results[sym])
        if v == "REJECT_DD":
            print(f"  {sym}: {v} — NOT saving (DD too high, risky to promote)", flush=True)
            continue
        out_path = CANDIDATES_DIR / f"extra_btc_{sym}_winner.json"
        ov = dict(final_overrides[sym])
        ov["_meta"] = (
            f"Per-sym profile for {sym} | verdict={v} | "
            f"pool_sharpe={final_results[sym].get('pool_sharpe',0):.4f} | "
            f"trades={final_results[sym].get('trades',0)} | "
            f"dd={final_results[sym].get('max_dd_pct',0):.2f}% | "
            f"timespan=1sym×{YEARS_SPAN:.2f}yr×{START_DATE} | "
            f"built=2026-05-01"
        )
        with open(out_path, "w") as f:
            json.dump(ov, f, indent=2)
        saved_paths.append(out_path)
        print(f"  Saved: {out_path}", flush=True)

    # Phase 4 — final report
    print("\n### PHASE 4 — FINAL REPORT ###", flush=True)
    print("\nPer-sym baseline (Phase 1 under BEST):", flush=True)
    for sym in SYMS:
        print(f"  {canonical_line(sym, phase1_results[sym])}", flush=True)

    print("\nPer-sym winner (Phase 2 where applicable):", flush=True)
    for sym in SYMS:
        if mutation_done.get(sym, False):
            print(f"  {canonical_line(sym, final_results[sym])} [MUTATION]", flush=True)
        else:
            print(f"  {canonical_line(sym, final_results[sym])} [BEST_unchanged]", flush=True)

    print("\nFiles saved to _candidates/:", flush=True)
    for p in saved_paths:
        print(f"  {p}", flush=True)

    print("\nLeaderboard (sorted by effective score):", flush=True)
    ranked = sorted(SYMS, key=lambda s: score(final_results[s]), reverse=True)
    for rank, sym in enumerate(ranked, 1):
        r = final_results[sym]
        eff = score(r)
        v = verdict(r)
        print(f"  #{rank} {sym}: eff_score={eff:.4f} pool={r.get('pool_sharpe',0):.4f} "
              f"trades={r.get('trades',0)} dd={r.get('max_dd_pct',0):.2f}% | {v}", flush=True)

    print("\nRecommendation — syms to REMOVE from BTC_DEDICATED_SYMBOLS bundle:", flush=True)
    rejects = [s for s in SYMS if verdict(final_results[s]) == "REJECT_DD"]
    if rejects:
        print(f"  REMOVE (DD > {PROMOTE_DD_MAX}%): {', '.join(rejects)}", flush=True)
    else:
        print("  No syms require removal (all pass DD gate after mutation).", flush=True)

    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
