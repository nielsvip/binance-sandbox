#!/usr/bin/env python3
"""per_sym_flz8_profiles — Phase 1+2+3 builder for the 8 flz BTC_DEDICATED symbols.

User directive 2026-05-01: "work on a custom profile for each of the other flz
symbols to be used in the 7D strategy".

Pipeline:
  Phase 1  — Per-sym BEST baseline.
             Read existing canonical_trades JSONLs (the BEST run already done at
             1777522364) and compute per-sym pool_sharpe, sym_sharpe, wr, avg_pnl,
             gain_per_yr, gain_sym_yr, max_dd_pct, trades. Apply revised promote
             criteria (pool>=0.3 AND trades>=5000 AND dd<=10% AND gain_per_yr>=100%
             AND wr>=60%). Flag PROMOTE / DIAGNOSTIC / REJECT_* per sym.

  Phase 2  — Per-sym mutation search for any sym that scores below the promote
             criteria in Phase 1. Small focused grid over BTC_BREAKOUT_*,
             BTC_MIN_HOLD_*, BTC_DIVERGENCE_*, hard-loss knobs. Picks the iter
             that maximizes pool_sharpe * sqrt(trades/1000) SUBJECT TO
             dd<=10% AND wr>=60% AND gain_per_yr>=100%. Per-sym CSVs written
             to data/sweep_results/canonical_per_sym_flz_<SYM>.csv via
             metrics_guard.write_sharpe_row.

  Phase 3  — For each of the 8 syms, write the WINNING override JSON to
             data/hourly_reconfig/_candidates/extra_btc_<SYM>_winner.json.
             Naming pattern matches existing extras and avoids the imposter regex.

  Phase 4  — Print final canonical 9-field report per sym + leaderboard sorted
             by effective score. Recommend any sym to remove from
             BTC_DEDICATED_SYMBOLS if it can't clear DD<=10%.

Constraints honored:
  - All Sharpe writes via metrics_guard.write_sharpe_row()
  - Cockroach _pool_sharpe(n_syms_qualifying * 30, cap=5.0) preserved (use the
    engine's internal ps for engine runs; recompute via metrics_guard for the
    JSONL-derived metrics so we stay in canonical chokepoint form)
  - No live trading processes touched
  - Engine path: /Users/niels/Documents/binance/v8_quick_engine.py md5
    5780330e1c4ae1fa0048104170715564
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "_candidates"
BEST_JSONL_DIR = ROOT / "data" / "canonical_trades" / "flz8_BEST_1777522364"
BASE_OVERRIDE_PATH = ROOT / "backtest_v8" / "btc_loop_results" / "override_btc_BEST.json"

SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC",
           "XRPUSDC", "DOGEUSDC", "ZECUSDC", "BTCDOMUSDT"]

# Promote criteria (revised 2026-05-01).
PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0


def load_base_override() -> Dict:
    with BASE_OVERRIDE_PATH.open() as f:
        ovr = json.load(f)
    return {k: v for k, v in ovr.items() if not k.startswith("_")}


def jsonl_pnls_and_meta(path: Path) -> Tuple[List[float], int, int, float, float]:
    """Read a per-sym JSONL → (pnl_pct list, n_total, n_wins, first_ts, last_ts)."""
    rs: List[float] = []
    wins = 0
    first = None
    last = 0
    with path.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            r = float(rec.get("pnl_pct", 0.0))
            rs.append(r)
            if r > 0:
                wins += 1
            ets = int(rec.get("exit_ts", 0) or 0)
            if first is None or (ets and ets < first):
                first = ets
            if ets > last:
                last = ets
    return rs, len(rs), wins, float(first or 0), float(last or 0)


def per_sym_metrics(rets: List[float], years: float, sym: str) -> Dict:
    """Compute per-sym metrics in canonical 9-field form.

    pool_sharpe is per-sym-pool over this single sym's trades; sym_sharpe in this
    context = same value (single-sym group avg, capped). Equity-cumulative DD
    computed from pnl_pct (each trade's pct return added to equity).
    """
    n = len(rets)
    n_wins = sum(1 for r in rets if r > 0)
    wr = (100.0 * n_wins / n) if n else 0.0
    total_gain = float(sum(rets))
    avg_gain = (total_gain / n) if n else 0.0
    yrs = max(0.01, years)
    pool = mg.pool_sharpe(rets)
    # sym_sharpe with single-sym group is just the capped per-sym pool
    sym_pool_capped = max(-mg.PER_SYM_SHARPE_CAP, min(mg.PER_SYM_SHARPE_CAP, pool))
    # DD on cumulative equity (sum of pct returns across the trade order)
    eq = 0.0
    peak = 0.0
    worst = 0.0
    for r in rets:
        eq += r
        if eq > peak:
            peak = eq
        elif peak - eq > worst:
            worst = peak - eq
    return {
        "pool_sharpe": pool,
        "sym_sharpe": sym_pool_capped,
        "avg_gain_trade": avg_gain,
        "gain_per_yr": total_gain / yrs,
        "gain_sym_yr": total_gain / yrs,    # n_syms=1 here
        "trades": n,
        "n_syms": 1,
        "years": yrs,
        "total_gain_pct": total_gain,
        "wr_pct": wr,
        "max_dd_pct": worst,
        "n_wins": n_wins,
        "tag": f"per_sym_{sym}",
    }


def verdict(m: Dict) -> str:
    """Return PROMOTE / DIAGNOSTIC / REJECT_* per revised criteria."""
    if m["max_dd_pct"] > PROMOTE_DD_MAX:
        return "REJECT_DD"
    if m["wr_pct"] < PROMOTE_WR_MIN:
        return "REJECT_WR"
    if m["trades"] < PROMOTE_TRADES_MIN:
        return "REJECT_TRADES"
    if m["gain_per_yr"] < PROMOTE_GAIN_PER_YR_MIN:
        return "REJECT_PNL"
    if m["pool_sharpe"] < PROMOTE_POOL_MIN:
        return "DIAGNOSTIC"
    return "PROMOTE"


def fmt_canonical(sym: str, m: Dict, years_label: float, v: str) -> str:
    return (
        f"{sym} | pool={m['pool_sharpe']:+.4f} | sym={m['sym_sharpe']:+.4f} | "
        f"wr={m['wr_pct']:.1f}% | avg_gain_trade={m['avg_gain_trade']:+.4f}% | "
        f"gain_per_yr={m['gain_per_yr']:+.1f}% | gain_sym_yr={m['gain_sym_yr']:+.1f}% | "
        f"trades={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | "
        f"timespan=1sym×{years_label:.2f}yr×2024-07-01 | {v}"
    )


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


# ---------- Phase 1 ----------------------------------------------------------

def phase1_baseline() -> Dict[str, Dict]:
    """Read existing flz8_BEST trade JSONLs and compute per-sym metrics.

    Returns: {sym: metrics_dict}
    """
    out: Dict[str, Dict] = {}
    print("=" * 82)
    print("PHASE 1 — per-sym BEST baseline (from existing flz8_BEST_1777522364 JSONLs)")
    print("=" * 82)
    for sym in SYMBOLS:
        jp = BEST_JSONL_DIR / f"flz8_BEST__{sym}.jsonl"
        if not jp.exists():
            print(f"  {sym}: MISSING JSONL {jp}")
            continue
        rets, n, wins, first_ts, last_ts = jsonl_pnls_and_meta(jp)
        years = (last_ts - first_ts) / 86400.0 / 365.25 if (first_ts and last_ts) else 6.23
        m = per_sym_metrics(rets, years, sym)
        m["override_path"] = str(BASE_OVERRIDE_PATH)
        v = verdict(m)
        m["verdict"] = v
        out[sym] = m
        print(fmt_canonical(sym, m, years, v))
    return out


# ---------- Phase 2 ----------------------------------------------------------

START_TS_2024_07_01 = 1719792000  # 2024-07-01 00:00 UTC, matches BEST run window

def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict, start_ts: int = START_TS_2024_07_01) -> Tuple[List[float], int]:
    """Run v8_quick_engine.simulate for one sym with overrides; return pnl list and trade count.

    Filters output trades to exit_ts >= start_ts for parity with BEST's 2024-07-01 window.
    """
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    # BTC_DEDICATED single-sym path 'continue's past the rate-guard tick, so
    # final_check() always fires SystemExit on n_accts=0/elapsed-only projection.
    # Disable rate guard for these single-sym BTC runs.
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    # Single-sym BTC_DEDICATED path
    cfg.BTC_DEDICATED_SYMBOLS = (sym,)
    cfg.BTC_DEDICATED_ENABLED = True
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit as e:
        print(f"    [engine] SystemExit {e}")
        return [], 0
    except Exception as e:
        print(f"    [engine] EXC {e}")
        return [], 0
    # Read produced JSONL; filter by exit_ts >= start_ts.
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rets: List[float] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if int(rec.get("exit_ts", 0) or 0) < start_ts:
                        continue
                    rets.append(float(rec.get("pnl_pct", 0.0)))
                except Exception:
                    pass
    return rets, len(rets)


def mutation_grid(base: Dict) -> List[Tuple[str, Dict]]:
    """Focused mutation grid (~22 variants) over the BTC_* knobs.

    Curated to span: min-hold, breakout HTF gates, divergence sensitivity, hard-loss
    budgets, cooldown, tech-exit TF count. All returned configs are full overlays
    (BEST + delta).
    """
    grid: List[Tuple[str, Dict]] = []
    grid.append(("BEST", dict(base)))

    # 1. min-hold (4 variants)
    for mh, bk in ((1, 1), (3, 1), (5, 3), (8, 5)):
        o = dict(base); o["BTC_MIN_HOLD_BARS"] = mh; o["BTC_BREAKOUT_MIN_HOLD_BARS"] = bk
        grid.append((f"mh{mh}_bk{bk}", o))

    # 2. breakout HTF strictness (4 variants)
    for ht, ac in ((0, 1), (1, 1), (1, 2), (2, 2)):
        o = dict(base); o["BTC_BREAKOUT_HTF_MIN_ALIGNED"] = ht; o["BTC_BREAKOUT_ACCEL_MIN_TFS"] = ac
        grid.append((f"ht{ht}_ac{ac}", o))

    # 3. divergence (3 variants)
    for tag, dB, dE, dTF in (("dvOFF", False, False, "4h"),
                              ("dvBlk1h", True, False, "1h"),
                              ("dvExit4h", True, True, "4h")):
        o = dict(base); o["BTC_DIVERGENCE_BLOCK_AGAINST"] = dB
        o["BTC_DIVERGENCE_EXIT_AGAINST"] = dE; o["BTC_DIVERGENCE_MIN_TF"] = dTF
        grid.append((tag, o))

    # 4. hard-loss budget (4 variants)
    base_hl = float(base.get("BTC_HARD_LOSS_USD_PER_TRADE", 5.4))
    base_bhl = float(base.get("BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE", 3.75))
    for f in (0.7, 0.85, 1.15, 1.3):
        o = dict(base)
        o["BTC_HARD_LOSS_USD_PER_TRADE"] = round(base_hl * f, 3)
        o["BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE"] = round(base_bhl * f, 3)
        grid.append((f"hl_x{f:.2f}", o))

    # 5. cooldown (3 variants)
    for cd, bcd in ((1, 1), (3, 1), (3, 3)):
        o = dict(base); o["BTC_COOLDOWN_BARS"] = cd; o["BTC_BREAKOUT_COOLDOWN_BARS"] = bcd
        grid.append((f"cd{cd}_bcd{bcd}", o))

    # 6. tech-exit min TFs (3 variants)
    for t in (1, 2, 3):
        o = dict(base); o["BTC_TECH_EXIT_WT_MIN_TFS"] = t
        grid.append((f"texit_{t}", o))

    # 7. reverse-on-exit / follow-through (3 variants)
    for tag, ron, ft in (("ron0_ft1", False, True), ("ron1_ft0", True, False),
                         ("ron0_ft0", False, False)):
        o = dict(base); o["BTC_REVERSE_ON_EXIT_ENABLED"] = ron
        o["BTC_FOLLOW_THROUGH_REENTRY_ENABLED"] = ft
        grid.append((tag, o))

    return grid


def phase2_mutate_sym(sym: str, base: Dict, run_root: Path,
                      sweep_csv: Path, full_years: float) -> Tuple[Dict, Dict]:
    """Run mutation grid for one sym; return (winning_overrides, winning_metrics)."""
    print()
    print("-" * 72)
    print(f"PHASE 2 — mutation grid for {sym}")
    print("-" * 72)
    grid = mutation_grid(base)
    print(f"  grid size: {len(grid)} variants")

    # Load NPZ once for this sym
    npz_path = NPZ_DIR / f"{sym}.npz"
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()

    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)

    results: List[Tuple[str, Dict, Dict]] = []   # (tag, overrides, metrics)
    t0 = time.time()
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets, n = run_engine_for_sym(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, full_years, sym)
        m["override_path"] = "BEST+mutation"
        m["tag"] = f"per_sym_mut_{sym}_{tag}"
        v = verdict(m)
        m["verdict"] = v
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m))
        # Write canonical row through the chokepoint
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="crypto", append=True)
        except Exception as e:
            print(f"    [csv-write] REFUSED {tag}: {e}")
        if i <= 5 or i % 10 == 0:
            print(f"    [{i:3d}/{len(grid)}] {tag:25s} pool={m['pool_sharpe']:+.4f} "
                  f"trades={m['trades']:>6d} dd={m['max_dd_pct']:.2f}% "
                  f"wr={m['wr_pct']:.1f}% eff={m['effective_score']:+.3f} {v}")

    print(f"  {sym} grid done in {time.time()-t0:.1f}s")

    # Pick winner subject to constraints; if none qualify, pick highest effective score.
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["max_dd_pct"] <= PROMOTE_DD_MAX
                 and m["wr_pct"] >= PROMOTE_WR_MIN
                 and m["gain_per_yr"] >= PROMOTE_GAIN_PER_YR_MIN
                 and m["trades"] >= 30]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["effective_score"])
        bucket = "QUALIFIED"
    else:
        # Fallback: pick highest effective score among all results, plus flag
        viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= 30]
        if not viable:
            viable = results
        best = max(viable, key=lambda t: t[2]["effective_score"])
        bucket = "FALLBACK"
    tag, ovr, m = best
    print(f"  WINNER ({bucket}): tag={tag} pool={m['pool_sharpe']:+.4f} "
          f"trades={m['trades']:,} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
          f"gain_per_yr={m['gain_per_yr']:+.1f}% eff={m['effective_score']:+.3f}")
    return ovr, m


# ---------- Phase 3 ----------------------------------------------------------

def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"extra_btc_{sym}_winner.json"
    payload = dict(overrides)
    payload["_meta"] = (
        f"per_sym winner for {sym} 2026-05-01 | pool={m['pool_sharpe']:+.4f} "
        f"trades={m['trades']} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
        f"verdict={m.get('verdict')} effective_score={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


# ---------- Driver -----------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="123",
                    help="any combination of 1/2/3 (default 123)")
    ap.add_argument("--syms", default="",
                    help="comma-separated subset (default all 8)")
    ap.add_argument("--mutate-all", action="store_true",
                    help="run Phase 2 for ALL syms even if Phase 1 PROMOTEs")
    args = ap.parse_args()

    syms = [s for s in (args.syms.split(",") if args.syms else SYMBOLS) if s]
    base = load_base_override()
    sweep_csv = SWEEP_DIR / "canonical_per_sym_flz_20260501.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / "per_sym_flz_20260501" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)

    phase1: Dict[str, Dict] = {}
    if "1" in args.phase:
        phase1 = phase1_baseline()
        # Write phase1 rows through chokepoint
        for sym, m in phase1.items():
            row = dict(m)
            row["override_path"] = str(BASE_OVERRIDE_PATH)
            row["tag"] = f"per_sym_phase1_{sym}"
            try:
                mg.write_sharpe_row(sweep_csv, row, mode="crypto", append=True)
            except Exception as e:
                print(f"  [phase1 csv] REFUSED {sym}: {e}")

    winners: Dict[str, Tuple[Dict, Dict]] = {}
    if "2" in args.phase:
        for sym in syms:
            p1 = phase1.get(sym, {})
            p1_v = p1.get("verdict", "")
            # Default: mutate any sym not flagged PROMOTE.
            need_mutation = (p1_v != "PROMOTE") or args.mutate_all
            full_years = float(p1.get("years", 6.23))
            if not need_mutation:
                # Promote BEST as winner directly
                winners[sym] = (dict(base), p1)
                print(f"\n  {sym}: Phase 1 PROMOTE — using BEST as winner (no mutation)")
                continue
            try:
                ovr, m = phase2_mutate_sym(sym, base, run_root, sweep_csv, full_years)
                winners[sym] = (ovr, m)
            except Exception as e:
                print(f"  PHASE2 EXC {sym}: {e}")
                continue
            gc.collect()

    paths_written: List[Path] = []
    if "3" in args.phase:
        print()
        print("=" * 82)
        print("PHASE 3 — write winner JSONs to data/hourly_reconfig/_candidates/")
        print("=" * 82)
        for sym in syms:
            if sym not in winners:
                # Fallback: use BEST + Phase 1 metrics
                if sym in phase1:
                    winners[sym] = (dict(base), phase1[sym])
                else:
                    print(f"  {sym}: no winner data — skipping write")
                    continue
            ovr, m = winners[sym]
            p = write_winner_json(sym, ovr, m)
            paths_written.append(p)
            print(f"  wrote {p.name}")

    # ---------- Phase 4: report --------------------------------------------
    print()
    print("=" * 82)
    print("PHASE 4 — final canonical report")
    print("=" * 82)
    table: List[Tuple[str, str, Dict]] = []
    for sym in syms:
        rows = []
        if sym in phase1:
            rows.append(("Phase1_BEST", phase1[sym]))
        if sym in winners and "2" in args.phase:
            ovr, m = winners[sym]
            if m.get("tag", "").startswith("per_sym_mut_"):
                rows.append(("Phase2_winner", m))
        for label, m in rows:
            yrs_l = float(m.get("years", 6.23))
            v = m.get("verdict", verdict(m))
            print(f"  [{label:14s}] {fmt_canonical(sym, m, yrs_l, v)}")
        # Use winner if exists else phase1 for leaderboard
        if sym in winners:
            ovr, m = winners[sym]
        elif sym in phase1:
            m = phase1[sym]
        else:
            continue
        table.append((sym, m.get("verdict", verdict(m)), m))

    # Leaderboard sorted by effective score
    print()
    print("LEADERBOARD (sorted by effective_score = pool_sharpe * sqrt(trades/1000))")
    print("-" * 82)
    table.sort(key=lambda t: effective_score(t[2]), reverse=True)
    for i, (sym, v, m) in enumerate(table, 1):
        eff = effective_score(m)
        print(f"  {i}. {sym:11s} eff={eff:+.3f} pool={m['pool_sharpe']:+.4f} "
              f"trades={m['trades']:>7,} dd={m['max_dd_pct']:5.2f}% "
              f"wr={m['wr_pct']:5.1f}% gpy={m['gain_per_yr']:+8.1f}% [{v}]")

    # Recommend removals
    print()
    rejects = [(sym, m) for sym, v, m in table if m["max_dd_pct"] > PROMOTE_DD_MAX]
    if rejects:
        print("REJECT_DD recommendations (DD > 10%):")
        for sym, m in rejects:
            print(f"  - REMOVE {sym} from BTC_DEDICATED_SYMBOLS (dd={m['max_dd_pct']:.2f}%)")
    else:
        print("No syms exceed DD cap — keep all 8 in BTC_DEDICATED_SYMBOLS.")

    print()
    print("Files written:")
    for p in paths_written:
        print(f"  {p}")
    print(f"  CSV: {sweep_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
