#!/usr/bin/env python3
"""per_sym_flz8_profiles — comprehensive per-symbol BTC_DEDICATED optimizer.

Pipeline:
  Phase 1 — Baseline metrics from existing flz8_BEST canonical trade JSONLs.
  Phase 2 — Full parameter sweep per symbol. Winner = max(total_gain_pct) among
             configs with pool_sharpe > 0 AND dd <= PROMOTE_DD_MAX AND trades >= 30.
             total_gain_pct is the primary definer; pool_sharpe > 0 guards against
             negative-expectancy configs sneaking through via lucky PnL.
  Phase 3 — Write winner JSON per symbol to data/hourly_reconfig/_candidates/.
             Picked up automatically by flz_hourly_reconfig.py next hourly cycle.
  Phase 4 — Leaderboard sorted by total_gain_pct.
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

PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0

START_TS_2024_07_01 = 1719792000


def load_base_override() -> Dict:
    with BASE_OVERRIDE_PATH.open() as f:
        ovr = json.load(f)
    return {k: v for k, v in ovr.items() if not k.startswith("_")}


def jsonl_pnls_and_meta(path: Path) -> Tuple[List[float], int, int, float, float]:
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
    n = len(rets)
    n_wins = sum(1 for r in rets if r > 0)
    wr = (100.0 * n_wins / n) if n else 0.0
    total_gain = float(sum(rets))
    avg_gain = (total_gain / n) if n else 0.0
    yrs = max(0.01, years)
    pool = mg.pool_sharpe(rets)
    sym_pool_capped = max(-mg.PER_SYM_SHARPE_CAP, min(mg.PER_SYM_SHARPE_CAP, pool))
    eq = 0.0; peak = 0.0; worst = 0.0
    for r in rets:
        eq += r
        if eq > peak:
            peak = eq
        elif peak - eq > worst:
            worst = peak - eq
    return {
        "pool_sharpe": pool, "sym_sharpe": sym_pool_capped,
        "avg_gain_trade": avg_gain, "gain_per_yr": total_gain / yrs,
        "gain_sym_yr": total_gain / yrs,
        "trades": n, "n_syms": 1, "years": yrs,
        "total_gain_pct": total_gain, "wr_pct": wr,
        "max_dd_pct": worst, "n_wins": n_wins, "tag": f"per_sym_{sym}",
    }


def verdict(m: Dict) -> str:
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
        f"wr={m['wr_pct']:.1f}% | avg={m['avg_gain_trade']:+.4f}% | "
        f"gpy={m['gain_per_yr']:+.1f}% | pnl={m['total_gain_pct']:+.1f}% | "
        f"trades={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | {v}"
    )


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def pick_winner(results: List[Tuple[str, Dict, Dict]]) -> Tuple[str, Dict, Dict, str]:
    """Pick winner by total_gain_pct (primary) among configs with pool_sharpe>0, dd<=cap, trades>=30."""
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["pool_sharpe"] > 0
                 and m["max_dd_pct"] <= PROMOTE_DD_MAX
                 and m["trades"] >= 30]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["total_gain_pct"])
        return best[0], best[1], best[2], "QUALIFIED_BY_PNL"
    # Fallback: any with trades >= 30, max effective_score
    viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= 30] or results
    best = max(viable, key=lambda t: t[2]["effective_score"])
    return best[0], best[1], best[2], "FALLBACK_EFF"


# ---------- Phase 1 ----------------------------------------------------------

def phase1_baseline() -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    print("=" * 88)
    print("PHASE 1 — per-sym BEST baseline (from existing flz8_BEST_1777522364 JSONLs)")
    print("=" * 88)
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


# ---------- Phase 2 — Comprehensive mutation grid ----------------------------

def mutation_grid(base: Dict) -> List[Tuple[str, Dict]]:
    """Comprehensive BTC_* sweep. Every major knob from config.py BTC_DEDICATED section.
    Systematic single-param variations from baseline + key combos.
    Winner selection uses total_gain_pct as primary criterion.
    """
    grid: List[Tuple[str, Dict]] = []

    def add(tag: str, deltas: Dict) -> None:
        o = dict(base)
        o.update(deltas)
        grid.append((tag, o))

    # ── Baseline ──────────────────────────────────────────────────────────────
    grid.append(("BEST", dict(base)))

    # ── 1. Accel ramp strictness (PRIMARY entry gate) ─────────────────────────
    for n in (2, 3, 4, 5):
        add(f"accel_tfs_{n}", {"BTC_ACCEL_RAMP_MIN_TFS": n})
    add("accel_off",  {"BTC_ACCEL_RAMP_ENABLED": False})
    add("accel_nopos", {"BTC_ACCEL_RAMP_REQUIRE_POSITIVE": False})

    # ── 2. Entry composition: RZ gate / accel requirement ─────────────────────
    add("norz_noaccel",  {"BTC_ENTRY_PRIMARY_REQUIRE_RZ": False,
                          "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP": False})
    add("norz_accel",    {"BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})
    add("rz_noaccel",    {"BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP": False})
    add("div_only_on",   {"BTC_ENTRY_DIV_ONLY_ENABLED": True})
    add("div_only_min2", {"BTC_ENTRY_DIV_ONLY_ENABLED": True, "BTC_ENTRY_DIV_ONLY_MIN_INDS": 2})

    # ── 3. Red zone proximity & boost mode ────────────────────────────────────
    for prox in (0.5, 1.0, 1.5, 2.0, 3.0):
        add(f"rz_prox_{prox:.1f}", {"BTC_RZ_PROXIMITY_PCT": prox})
    add("rz_gate_mode",  {"BTC_RZ_AS_BOOST_ENABLED": False})
    for soften in (0, 1, 2):
        add(f"rz_soften_{soften}", {"BTC_RZ_SOFTEN_ACCEL_BY": soften})

    # ── 4. Divergence ─────────────────────────────────────────────────────────
    add("div_off",       {"BTC_DIVERGENCE_ENABLED": False})
    add("div_noblk",     {"BTC_DIVERGENCE_BLOCK_AGAINST": False})
    add("div_noexit",    {"BTC_DIVERGENCE_EXIT_AGAINST": False})
    add("div_noblk_noexit", {"BTC_DIVERGENCE_BLOCK_AGAINST": False,
                              "BTC_DIVERGENCE_EXIT_AGAINST": False})
    for tf in ("1h", "4h", "D"):
        add(f"div_min_{tf}", {"BTC_DIVERGENCE_MIN_TF": tf})
    for lb in (2, 5, 10):
        add(f"div_lb_{lb}", {"BTC_DIVERGENCE_LB_4H": lb, "BTC_DIVERGENCE_LB_D": lb})

    # ── 5. Hold / cooldown ────────────────────────────────────────────────────
    for mh, bk in ((1, 1), (3, 1), (5, 3), (8, 5), (12, 8)):
        add(f"mh{mh}_bk{bk}", {"BTC_MIN_HOLD_BARS": mh, "BTC_BREAKOUT_MIN_HOLD_BARS": bk})
    for cd, bcd in ((1, 1), (3, 1), (3, 3), (5, 3), (8, 5)):
        add(f"cd{cd}_bcd{bcd}", {"BTC_COOLDOWN_BARS": cd, "BTC_BREAKOUT_COOLDOWN_BARS": bcd})

    # ── 6. Technical exit strictness ──────────────────────────────────────────
    for t in (1, 2, 3, 4):
        add(f"texit_{t}", {"BTC_TECH_EXIT_WT_MIN_TFS": t})
    add("texit_any_pnl_off", {"BTC_TECH_EXIT_AT_ANY_PNL": False})

    # ── 7. Hard loss budgets ───────────────────────────────────────────────────
    base_hl  = float(base.get("BTC_HARD_LOSS_USD_PER_TRADE", 10.0))
    base_bhl = float(base.get("BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE", 5.0))
    for f in (0.5, 0.7, 0.85, 1.0, 1.15, 1.3, 1.5):
        add(f"hl_x{f:.2f}", {
            "BTC_HARD_LOSS_USD_PER_TRADE": round(base_hl * f, 3),
            "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": round(base_bhl * f, 3),
        })

    # ── 8. Breakout entry mode ─────────────────────────────────────────────────
    add("bk_off",        {"BTC_BREAKOUT_ENTRY_ENABLED": False})
    for ac in (1, 2, 3):
        add(f"bk_ac{ac}", {"BTC_BREAKOUT_ACCEL_MIN_TFS": ac})
    for ht in (1, 2, 3):
        add(f"bk_htf{ht}", {"BTC_BREAKOUT_HTF_MIN_ALIGNED": ht})
    add("bk_nohtf",      {"BTC_BREAKOUT_REQUIRE_HTF_ALIGNED": False})
    add("bk_nodiv",      {"BTC_BREAKOUT_BLOCK_OPPOSING_DIV": False})

    # ── 9. Reentry / follow-through / reverse ─────────────────────────────────
    add("guaren_off",    {"BTC_GUARANTEED_REENTRY_ENABLED": False})
    add("guaren_norz",   {"BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": False})
    add("guaren_off_norz", {"BTC_GUARANTEED_REENTRY_ENABLED": False,
                             "BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": False})
    for gap in (1, 3, 5, 10):
        add(f"guaren_gap{gap}", {"BTC_GUARANTEED_REENTRY_MIN_GAP_BARS": gap})
    add("rev_off",       {"BTC_REVERSE_ON_EXIT_ENABLED": False})
    add("rev_nohtf",     {"BTC_REVERSE_REQUIRE_HTF_ALIGNED": False})
    add("ft_off",        {"BTC_FOLLOW_THROUGH_REENTRY_ENABLED": False})
    for move in (0.1, 0.3, 0.5, 0.8):
        add(f"ft_move_{move:.1f}", {"BTC_FOLLOW_THROUGH_MIN_MOVE_PCT": move})

    # ── 10. Same-sym hedge ────────────────────────────────────────────────────
    add("hedge_on",      {"BTC_HEDGE_SAMESYM_ENABLED": True})
    add("hedge_on_htf2", {"BTC_HEDGE_SAMESYM_ENABLED": True,
                          "BTC_HEDGE_SAMESYM_REQUIRE_HTF_TFS_MIN": 2})

    # ── 11. MIN_GAIN / augment threshold (WA = winner-augment; PYRAMID = pyramid) ──
    for mg_pct in (1.0, 2.0, 3.0, 4.0):
        add(f"mingain_{mg_pct:.0f}pct", {
            "WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
        })

    # ── 12. Key 2-param combos (accel × RZ, hold × exit, breakout × reentry) ──
    add("ac3_norz",      {"BTC_ACCEL_RAMP_MIN_TFS": 3, "BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})
    add("ac2_norz",      {"BTC_ACCEL_RAMP_MIN_TFS": 2, "BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})
    add("mh5_texit2",    {"BTC_MIN_HOLD_BARS": 5, "BTC_TECH_EXIT_WT_MIN_TFS": 2})
    add("mh8_texit1",    {"BTC_MIN_HOLD_BARS": 8, "BTC_TECH_EXIT_WT_MIN_TFS": 1})
    add("bk_off_texit2", {"BTC_BREAKOUT_ENTRY_ENABLED": False, "BTC_TECH_EXIT_WT_MIN_TFS": 2})
    add("guaren_norz_mg2", {"BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": False,
                             "WA_MIN_GAIN_PCT": 2.0, "PYRAMID_MIN_GAIN_PCT": 2.0})
    add("tight_hl_mh5",  {"BTC_HARD_LOSS_USD_PER_TRADE": round(base_hl * 0.7, 3),
                           "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": round(base_bhl * 0.7, 3),
                           "BTC_MIN_HOLD_BARS": 5})
    add("loose_hl_mh3",  {"BTC_HARD_LOSS_USD_PER_TRADE": round(base_hl * 1.3, 3),
                           "BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE": round(base_bhl * 1.3, 3),
                           "BTC_MIN_HOLD_BARS": 3})
    add("mg2_es15",      {"WA_MIN_GAIN_PCT": 2.0, "PYRAMID_MIN_GAIN_PCT": 2.0,
                          "ENTRY_SCORE_THRESHOLD": 15.0})
    add("mg1_mh5_ac3",   {"WA_MIN_GAIN_PCT": 1.0, "PYRAMID_MIN_GAIN_PCT": 1.0,
                          "BTC_MIN_HOLD_BARS": 5, "BTC_ACCEL_RAMP_MIN_TFS": 3})
    add("mg3_texit2_norz", {"WA_MIN_GAIN_PCT": 3.0, "PYRAMID_MIN_GAIN_PCT": 3.0,
                             "BTC_TECH_EXIT_WT_MIN_TFS": 2,
                             "BTC_ENTRY_PRIMARY_REQUIRE_RZ": False})

    return grid


# ---------- Engine runner ─────────────────────────────────────────────────────

def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict, start_ts: int = START_TS_2024_07_01) -> List[float]:
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
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
    cfg.BTC_DEDICATED_SYMBOLS = (sym,)
    cfg.BTC_DEDICATED_ENABLED = True
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] EXC {e}")
        return []
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
    return rets


# ---------- Phase 2 driver ───────────────────────────────────────────────────

def phase2_mutate_sym(sym: str, base: Dict, run_root: Path,
                      sweep_csv: Path, full_years: float) -> Tuple[Dict, Dict]:
    print()
    print("─" * 80)
    print(f"PHASE 2 — {sym}")
    print("─" * 80)
    grid = mutation_grid(base)
    print(f"  grid size: {len(grid)} variants")

    npz_path = NPZ_DIR / f"{sym}.npz"
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()

    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)

    results: List[Tuple[str, Dict, Dict]] = []
    t0 = time.time()
    best_pnl = -1e9
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets = run_engine_for_sym(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, full_years, sym)
        m["tag"] = f"mut_{sym}_{tag}"
        v = verdict(m)
        m["verdict"] = v
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m))
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="crypto", append=True)
        except Exception as e:
            print(f"    [csv] REFUSED {tag}: {e}")
        marker = ""
        if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= 30:
            if m["total_gain_pct"] > best_pnl:
                best_pnl = m["total_gain_pct"]
                marker = " ← NEW BEST PNL"
        if i <= 5 or i % 20 == 0 or marker:
            print(f"    [{i:3d}/{len(grid)}] {tag:30s} pool={m['pool_sharpe']:+.4f} "
                  f"pnl={m['total_gain_pct']:+8.1f}% tr={m['trades']:>6d} "
                  f"dd={m['max_dd_pct']:.2f}% {v}{marker}")

    elapsed = time.time() - t0
    print(f"  {sym} done in {elapsed:.1f}s ({elapsed/len(grid):.1f}s/variant)")

    tag, ovr, m, bucket = pick_winner(results)
    print(f"  WINNER ({bucket}): {tag} | pool={m['pool_sharpe']:+.4f} | "
          f"pnl={m['total_gain_pct']:+.1f}% | trades={m['trades']:,} | "
          f"dd={m['max_dd_pct']:.2f}% | wr={m['wr_pct']:.1f}% | gpy={m['gain_per_yr']:+.1f}%")
    return ovr, m


# ---------- Phase 3 ──────────────────────────────────────────────────────────

def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"extra_btc_{sym}_winner.json"
    payload = dict(overrides)
    payload["_meta"] = (
        f"per_sym winner {sym} | "
        f"pool={m['pool_sharpe']:+.4f} trades={m['trades']} "
        f"dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
        f"pnl={m['total_gain_pct']:+.1f}% gpy={m['gain_per_yr']:+.1f}%/yr "
        f"verdict={m.get('verdict')} eff={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


# ---------- Driver ───────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="1234")
    ap.add_argument("--syms", default="")
    ap.add_argument("--mutate-all", action="store_true")
    args = ap.parse_args()

    syms = [s for s in (args.syms.split(",") if args.syms else SYMBOLS) if s]
    base = load_base_override()
    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"canonical_per_sym_flz_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_flz_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)

    phase1: Dict[str, Dict] = {}
    if "1" in args.phase:
        phase1 = phase1_baseline()
        for sym, m in phase1.items():
            row = dict(m); row["tag"] = f"phase1_{sym}"
            try:
                mg.write_sharpe_row(sweep_csv, row, mode="crypto", append=True)
            except Exception as e:
                print(f"  [phase1 csv] REFUSED {sym}: {e}")

    winners: Dict[str, Tuple[Dict, Dict]] = {}
    if "2" in args.phase:
        for sym in syms:
            p1 = phase1.get(sym, {})
            full_years = float(p1.get("years", 6.23))
            if p1.get("verdict") == "PROMOTE" and not args.mutate_all:
                winners[sym] = (dict(base), p1)
                print(f"\n  {sym}: Phase 1 PROMOTE — using BEST, no mutation (use --mutate-all to force)")
                continue
            try:
                ovr, m = phase2_mutate_sym(sym, base, run_root, sweep_csv, full_years)
                winners[sym] = (ovr, m)
            except Exception as e:
                print(f"  PHASE2 EXC {sym}: {e}")
            gc.collect()

    paths_written: List[Path] = []
    if "3" in args.phase:
        print()
        print("=" * 88)
        print("PHASE 3 — write winner JSONs → data/hourly_reconfig/_candidates/")
        print("=" * 88)
        for sym in syms:
            if sym not in winners:
                if sym in phase1:
                    winners[sym] = (dict(base), phase1[sym])
                else:
                    print(f"  {sym}: no data — skipping")
                    continue
            ovr, m = winners[sym]
            p = write_winner_json(sym, ovr, m)
            paths_written.append(p)
            print(f"  {p.name}")

    if "4" in args.phase:
        print()
        print("=" * 88)
        print("PHASE 4 — leaderboard (sorted by total_gain_pct — primary winner criterion)")
        print("=" * 88)
        table: List[Tuple[str, str, Dict]] = []
        for sym in syms:
            if sym in winners:
                ovr, m = winners[sym]
            elif sym in phase1:
                m = phase1[sym]
            else:
                continue
            v = m.get("verdict", verdict(m))
            table.append((sym, v, m))
        table.sort(key=lambda t: t[2].get("total_gain_pct", 0), reverse=True)
        for i, (sym, v, m) in enumerate(table, 1):
            eff = effective_score(m)
            print(f"  {i}. {sym:12s} pnl={m.get('total_gain_pct',0):+9.1f}% "
                  f"pool={m['pool_sharpe']:+.4f} gpy={m['gain_per_yr']:+.1f}%/yr "
                  f"tr={m['trades']:>7,} dd={m['max_dd_pct']:5.2f}% "
                  f"wr={m['wr_pct']:.1f}% eff={eff:+.3f} [{v}]")

        rejects = [(sym, m) for sym, v, m in table if m["max_dd_pct"] > PROMOTE_DD_MAX]
        if rejects:
            print()
            print("REJECT_DD (dd > 10%) — consider removing from BTC_DEDICATED_SYMBOLS:")
            for sym, m in rejects:
                print(f"  {sym}: dd={m['max_dd_pct']:.2f}%")
        else:
            print("\nAll symbols within DD cap.")

    print()
    print(f"CSV: {sweep_csv}")
    print("Candidates written:")
    for p in paths_written:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
