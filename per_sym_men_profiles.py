#!/usr/bin/env python3
"""per_sym_men_profiles — standard-path per-sym sweep for men account symbols.

Sweeps WA_MIN_GAIN_PCT + PYRAMID_MIN_GAIN_PCT (1–4%) plus core v8_quick timing
knobs for all men symbols that have NPZ files. Writes winner JSONs to
data/hourly_reconfig/men/_candidates/.

Identical sweep grid to per_sym_fin_profiles — only account name, symbol source,
and output dir differ.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "men" / "_candidates"
SYMBOLS_FILE = ROOT / "symbols_men.json"

PROMOTE_POOL_MIN = 0.3
PROMOTE_TRADES_MIN = 5000
PROMOTE_DD_MAX = 10.0
PROMOTE_GAIN_PER_YR_MIN = 100.0
PROMOTE_WR_MIN = 60.0


def load_men_symbols() -> List[str]:
    syms = json.loads(SYMBOLS_FILE.read_text())
    return [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]


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
        "gain_sym_yr": total_gain / yrs, "trades": n, "n_syms": 1,
        "years": yrs, "total_gain_pct": total_gain, "wr_pct": wr,
        "max_dd_pct": worst, "n_wins": n_wins, "tag": f"men_per_sym_{sym}",
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


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def fmt_canonical(sym: str, m: Dict, v: str) -> str:
    return (
        f"{sym:14s} | pool={m['pool_sharpe']:+.4f} | wr={m['wr_pct']:.1f}% | "
        f"avg_gain={m['avg_gain_trade']:+.4f}% | gpy={m['gain_per_yr']:+.1f}% | "
        f"trades={m['trades']:,} | dd={m['max_dd_pct']:.2f}% | {v}"
    )


def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict) -> List[float]:
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if not k.startswith("_"):
            try:
                setattr(cfg, k, v)
            except Exception:
                pass
    try:
        simulate({sym: npz}, cfg, capital=10000.0)
    except SystemExit:
        pass
    except Exception as e:
        print(f"    [engine] EXC {sym} {e}")
        return []
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    rets: List[float] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rets.append(float(json.loads(line).get("pnl_pct", 0.0)))
                except Exception:
                    pass
    return rets


def mutation_grid() -> List[Tuple[str, Dict]]:
    grid: List[Tuple[str, Dict]] = []
    grid.append(("baseline", {}))
    for mg_pct in (1.0, 2.0, 3.0, 4.0):
        grid.append((f"mingain_{mg_pct:.0f}pct",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct}))
    for es in (12.0, 15.0, 18.0, 24.0):
        grid.append((f"es_{es:.0f}", {"ENTRY_SCORE_THRESHOLD": es}))
    for mh in (3, 5, 10, 20):
        grid.append((f"mh_{mh}", {"MIN_HOLD_BARS": mh}))
    for wt in (1, 2, 3, 4):
        grid.append((f"wt_exit_{wt}", {"WT_EXIT_MIN_TFS": wt}))
    for cd in (1, 3, 6, 12):
        grid.append((f"cd_{cd}", {"COOLDOWN_BARS": cd}))
    grid.append(("dtrend_on", {"D_TREND_REQUIRED": True}))
    grid.append(("dtrend_off", {"D_TREND_REQUIRED": False}))
    for htf in (1, 2, 3):
        grid.append((f"htf_{htf}", {"HTF_MIN_ALIGNED": htf}))
    for mg_pct, es in ((1.0, 15.0), (2.0, 18.0), (3.0, 18.0), (4.0, 24.0),
                       (1.0, 24.0), (2.0, 15.0)):
        grid.append((f"mg{mg_pct:.0f}_es{es:.0f}",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
                      "ENTRY_SCORE_THRESHOLD": es}))
    for mg_pct, mh in ((1.0, 5), (2.0, 10), (3.0, 10), (4.0, 20)):
        grid.append((f"mg{mg_pct:.0f}_mh{mh}",
                     {"WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
                      "MIN_HOLD_BARS": mh}))
    return grid


def sweep_sym(sym: str, run_root: Path, sweep_csv: Path,
              full_years: float) -> Tuple[Dict, Dict]:
    print(f"\n{'─'*60}")
    print(f"  {sym}")
    print(f"{'─'*60}")
    npz_path = NPZ_DIR / f"{sym}.npz"
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()
    run_dir = run_root / sym
    run_dir.mkdir(parents=True, exist_ok=True)
    grid = mutation_grid()
    print(f"  grid size: {len(grid)}")
    results: List[Tuple[str, Dict, Dict]] = []
    t0 = time.time()
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}__{tag}__{i:03d}"
        rets = run_engine_for_sym(sym, ovr, run_dir, run_id, npz)
        m = per_sym_metrics(rets, full_years, sym)
        m["tag"] = f"men_mut_{sym}_{tag}"
        v = verdict(m)
        m["verdict"] = v
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m))
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="crypto", append=True)
        except Exception as e:
            print(f"    [csv] REFUSED {tag}: {e}")
        if i <= 4 or i % 10 == 0:
            print(f"    [{i:3d}/{len(grid)}] {tag:25s} pool={m['pool_sharpe']:+.4f} "
                  f"tr={m['trades']:>5d} dd={m['max_dd_pct']:.2f}% eff={m['effective_score']:+.3f} {v}")
    print(f"  done in {time.time()-t0:.1f}s")
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["max_dd_pct"] <= PROMOTE_DD_MAX and m["wr_pct"] >= PROMOTE_WR_MIN
                 and m["gain_per_yr"] >= PROMOTE_GAIN_PER_YR_MIN and m["trades"] >= 30]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["effective_score"])
        bucket = "QUALIFIED"
    else:
        viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= 30] or results
        best = max(viable, key=lambda t: t[2]["effective_score"])
        bucket = "FALLBACK"
    tag, ovr, m = best
    print(f"  WINNER ({bucket}): {tag} pool={m['pool_sharpe']:+.4f} "
          f"tr={m['trades']:,} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
          f"gpy={m['gain_per_yr']:+.1f}% eff={m['effective_score']:+.3f}")
    return ovr, m


def write_winner_json(sym: str, overrides: Dict, m: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CAND_OUT_DIR / f"men_sym_{sym}_winner.json"
    payload = dict(overrides)
    payload["_meta"] = (
        f"men per_sym winner for {sym} | pool={m['pool_sharpe']:+.4f} "
        f"trades={m['trades']} dd={m['max_dd_pct']:.2f}% wr={m['wr_pct']:.1f}% "
        f"verdict={m.get('verdict')} effective_score={m.get('effective_score', 0):+.3f}"
    )
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="", help="comma-separated subset (default all with NPZ)")
    ap.add_argument("--years", type=float, default=2.0)
    args = ap.parse_args()
    all_syms = load_men_symbols()
    syms = [s for s in args.syms.split(",") if s] if args.syms else all_syms
    syms = [s for s in syms if (NPZ_DIR / f"{s}.npz").exists()]
    print("=" * 72)
    print(f"per_sym_men_profiles — {len(syms)} symbols")
    print(f"NPZ dir: {NPZ_DIR}")
    print(f"Out: {CAND_OUT_DIR}")
    print("=" * 72)
    print(f"Symbols: {syms}")
    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"canonical_per_sym_men_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_men_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)
    winners: Dict[str, Tuple[Dict, Dict]] = {}
    for sym in syms:
        try:
            ovr, m = sweep_sym(sym, run_root, sweep_csv, args.years)
            winners[sym] = (ovr, m)
        except Exception as e:
            print(f"  EXC {sym}: {e}")
        gc.collect()
    print()
    print("=" * 72)
    print("Writing winner JSONs")
    print("=" * 72)
    for sym, (ovr, m) in winners.items():
        p = write_winner_json(sym, ovr, m)
        print(f"  {p.name}")
    print()
    print("LEADERBOARD (effective_score = pool_sharpe × √(trades/1000))")
    print("─" * 72)
    table = sorted(winners.items(), key=lambda kv: effective_score(kv[1][1]), reverse=True)
    for i, (sym, (ovr, m)) in enumerate(table, 1):
        print(f"  {i:3d}. {fmt_canonical(sym, m, m.get('verdict', verdict(m)))}  eff={effective_score(m):+.3f}")
    print()
    print(f"CSV: {sweep_csv}")
    print(f"Candidates: {CAND_OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
