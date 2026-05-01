#!/usr/bin/env python3
"""per_sym_tradier_profiles — per-symbol sweep for trb account (stocks).

For each symbol in symbols_trb_long/short.json, tests three configurations:
  LONG_ONLY  — allow long entries, block short
  SHORT_ONLY — allow short entries, block long
  BOTH       — allow both sides (standard)

Winner per symbol = configuration with highest total_gain_pct, subject to:
  pool_sharpe > 0 AND dd <= 10% AND trades >= 30

Also sweeps key tuning params (WA_MIN_GAIN_PCT, MIN_HOLD_BARS, ENTRY_SCORE_THRESHOLD,
WT_EXIT_MIN_TFS, COOLDOWN_BARS, D_TREND_REQUIRED) for each winning side.

Output: winner JSONs in data/hourly_reconfig/trb/_candidates/
  Format: {sym}_{side}_winner.json  e.g. AAPL_SHORT_winner.json
  Content: {overrides + _meta + _best_side}
These are picked up by tradier_hourly_reconfig.py (runs hourly on S2).
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
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
SWEEP_DIR = ROOT / "data" / "sweep_results"
CAND_OUT_DIR = ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates"
SYMBOLS_LONG_FILE = ROOT / "symbols_trb_long.json"
SYMBOLS_SHORT_FILE = ROOT / "symbols_trb_short.json"

PROMOTE_DD_MAX = 10.0


def load_symbols() -> Tuple[List[str], List[str]]:
    long_syms = json.loads(SYMBOLS_LONG_FILE.read_text())
    short_syms = json.loads(SYMBOLS_SHORT_FILE.read_text())
    return long_syms, short_syms


def per_sym_metrics(rets: List[float], years: float, sym: str, tag: str = "") -> Dict:
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
        "gain_sym_yr": total_gain / yrs, "trades": n, "n_syms": 1, "years": yrs,
        "total_gain_pct": total_gain, "wr_pct": wr, "max_dd_pct": worst,
        "n_wins": n_wins, "tag": tag or f"tradier_{sym}",
    }


def effective_score(m: Dict) -> float:
    return float(m["pool_sharpe"]) * math.sqrt(max(1, m["trades"]) / 1000.0)


def pick_winner(results: List[Tuple[str, Dict, Dict]]) -> Tuple[str, Dict, Dict, str]:
    qualified = [(tag, ovr, m) for tag, ovr, m in results
                 if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX
                 and m["trades"] >= 30]
    if qualified:
        best = max(qualified, key=lambda t: t[2]["total_gain_pct"])
        return best[0], best[1], best[2], "QUALIFIED_BY_PNL"
    viable = [(tag, ovr, m) for tag, ovr, m in results if m["trades"] >= 30] or results
    best = max(viable, key=lambda t: t[2]["effective_score"])
    return best[0], best[1], best[2], "FALLBACK_EFF"


def run_engine_for_sym(sym: str, overrides: Dict, run_dir: Path, run_id: str,
                       npz: dict, years: float) -> List[float]:
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    os.environ["V8_RATE_GUARD_DISABLED"] = "1"
    cfg = QuickConfig()
    cfg.MODE = "tradier"
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


# Side restriction overrides
_LONG_ONLY_OVR: Dict = {
    "LONG_ENABLED": True, "SHORT_ENABLED": False,
    "WT_DC_LONG_ENABLED": True, "WT_DC_SHORT_ENABLED": False,
}
_SHORT_ONLY_OVR: Dict = {
    "LONG_ENABLED": False, "SHORT_ENABLED": True,
    "WT_DC_LONG_ENABLED": False, "WT_DC_SHORT_ENABLED": True,
}
_BOTH_OVR: Dict = {
    "LONG_ENABLED": True, "SHORT_ENABLED": True,
    "WT_DC_LONG_ENABLED": True, "WT_DC_SHORT_ENABLED": True,
}


def build_grid(base_side_ovr: Dict) -> List[Tuple[str, Dict]]:
    """Build param sweep grid on top of a side restriction baseline.
    Systematic variation of key tuning params, all anchored to the
    side's base overrides. Winner = max(total_gain_pct)."""
    grid: List[Tuple[str, Dict]] = []

    def add(tag: str, deltas: Dict) -> None:
        o = dict(base_side_ovr)
        o.update(deltas)
        grid.append((tag, o))

    grid.append(("baseline", dict(base_side_ovr)))

    # 1. MIN_GAIN / augment threshold
    for mg_pct in (1.0, 2.0, 3.0, 4.0):
        add(f"mingain_{mg_pct:.0f}pct", {
            "WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
        })

    # 2. Entry score threshold
    for es in (12.0, 15.0, 18.0, 21.0, 24.0):
        add(f"es_{es:.0f}", {"ENTRY_SCORE_THRESHOLD": es})

    # 3. Min hold bars
    for mh in (5, 10, 20, 30, 40):
        add(f"mh_{mh}", {"MIN_HOLD_BARS": mh})

    # 4. WT exit strictness
    for wt in (1, 2, 3, 4):
        add(f"wt_exit_{wt}", {"WT_EXIT_MIN_TFS": wt})

    # 5. Cooldown
    for cd in (1, 3, 6, 12):
        add(f"cd_{cd}", {"COOLDOWN_BARS": cd})

    # 6. D-trend required
    add("dtrend_on",  {"D_TREND_REQUIRED": True})
    add("dtrend_off", {"D_TREND_REQUIRED": False})

    # 7. HTF alignment
    for htf in (1, 2, 3):
        add(f"htf_{htf}", {"HTF_MIN_ALIGNED": htf})

    # 8. Strength filter
    for sc in (3.0, 5.0, 7.0, 10.0):
        add(f"strength_{sc:.0f}", {"STRENGTH_FILTER_ENABLED": True, "STRENGTH_MIN_SCORE": sc})
    add("strength_off", {"STRENGTH_FILTER_ENABLED": False})

    # 9. Key combos: MIN_GAIN × entry score
    for mg_pct, es in ((1.0, 15.0), (2.0, 18.0), (3.0, 18.0), (4.0, 24.0),
                       (1.0, 12.0), (2.0, 21.0)):
        add(f"mg{mg_pct:.0f}_es{es:.0f}", {
            "WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
            "ENTRY_SCORE_THRESHOLD": es,
        })

    # 10. Key combos: MIN_GAIN × hold
    for mg_pct, mh in ((1.0, 10), (2.0, 20), (3.0, 20), (4.0, 30)):
        add(f"mg{mg_pct:.0f}_mh{mh}", {
            "WA_MIN_GAIN_PCT": mg_pct, "PYRAMID_MIN_GAIN_PCT": mg_pct,
            "MIN_HOLD_BARS": mh,
        })

    # 11. Hold × exit strictness combos
    for mh, wt in ((10, 2), (20, 3), (30, 2), (10, 3)):
        add(f"mh{mh}_wt{wt}", {"MIN_HOLD_BARS": mh, "WT_EXIT_MIN_TFS": wt})

    return grid


def sweep_sym_side(sym: str, side_tag: str, side_ovr: Dict,
                   run_root: Path, sweep_csv: Path, years: float,
                   npz: dict) -> Tuple[float, Dict, Dict]:
    """Sweep one symbol×side combination. Returns (best_total_gain, winning_ovr, winning_metrics)."""
    grid = build_grid(side_ovr)
    run_dir = run_root / f"{sym}_{side_tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results: List[Tuple[str, Dict, Dict]] = []
    best_pnl = -1e9
    for i, (tag, ovr) in enumerate(grid, 1):
        run_id = f"{sym}_{side_tag}__{tag}__{i:03d}"
        rets = run_engine_for_sym(sym, ovr, run_dir, run_id, npz, years)
        m = per_sym_metrics(rets, years, sym, tag=f"tr_{sym}_{side_tag}_{tag}")
        m["effective_score"] = effective_score(m)
        results.append((tag, ovr, m))
        try:
            mg.write_sharpe_row(sweep_csv, m, mode="tradier", append=True)
        except Exception:
            pass
        if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= 30:
            if m["total_gain_pct"] > best_pnl:
                best_pnl = m["total_gain_pct"]
    tag, ovr, m, bucket = pick_winner(results)
    return m["total_gain_pct"], ovr, m


def sweep_sym(sym: str, can_long: bool, can_short: bool,
              run_root: Path, sweep_csv: Path, years: float) -> Optional[Dict]:
    """Sweep LONG_ONLY, SHORT_ONLY, BOTH for this symbol. Return winner JSON payload."""
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists():
        return None
    z = np.load(str(npz_path))
    npz = {k: z[k] for k in z.files}
    z.close()

    candidates = []
    if can_long:
        pnl_l, ovr_l, m_l = sweep_sym_side(sym, "LONG", _LONG_ONLY_OVR, run_root, sweep_csv, years, npz)
        candidates.append(("LONG_ONLY", pnl_l, ovr_l, m_l))
    if can_short:
        pnl_s, ovr_s, m_s = sweep_sym_side(sym, "SHORT", _SHORT_ONLY_OVR, run_root, sweep_csv, years, npz)
        candidates.append(("SHORT_ONLY", pnl_s, ovr_s, m_s))
    if can_long and can_short:
        pnl_b, ovr_b, m_b = sweep_sym_side(sym, "BOTH", _BOTH_OVR, run_root, sweep_csv, years, npz)
        candidates.append(("BOTH", pnl_b, ovr_b, m_b))

    if not candidates:
        return None

    # Pick best by total_gain_pct (guard: pool_sharpe > 0, dd <= cap)
    qualified = [(side, pnl, ovr, m) for side, pnl, ovr, m in candidates
                 if m["pool_sharpe"] > 0 and m["max_dd_pct"] <= PROMOTE_DD_MAX and m["trades"] >= 30]
    if qualified:
        best_side, best_pnl, best_ovr, best_m = max(qualified, key=lambda t: t[1])
        bucket = "QUALIFIED"
    else:
        best_side, best_pnl, best_ovr, best_m = max(candidates, key=lambda t: t[1])
        bucket = "FALLBACK"

    side_summary = " | ".join(
        f"{s}={pnl:+.1f}%({m['pool_sharpe']:+.3f}/{m['trades']}tr)"
        for s, pnl, _, m in candidates
    )
    print(f"  {sym:6s} winner={best_side}({bucket}) pnl={best_pnl:+.1f}% "
          f"pool={best_m['pool_sharpe']:+.4f} dd={best_m['max_dd_pct']:.2f}% "
          f"tr={best_m['trades']:,} || {side_summary}")

    # Build output payload
    payload = dict(best_ovr)
    payload["_best_side"] = best_side
    payload["_meta"] = (
        f"tradier per_sym winner {sym} | side={best_side} | "
        f"pool={best_m['pool_sharpe']:+.4f} trades={best_m['trades']} "
        f"dd={best_m['max_dd_pct']:.2f}% wr={best_m['wr_pct']:.1f}% "
        f"pnl={best_m['total_gain_pct']:+.1f}% gpy={best_m['gain_per_yr']:+.1f}%/yr"
    )
    payload["_pool_sharpe"] = best_m["pool_sharpe"]
    payload["_total_gain_pct"] = best_m["total_gain_pct"]
    payload["_trades"] = best_m["trades"]
    payload["_dd"] = best_m["max_dd_pct"]
    payload["_wr"] = best_m["wr_pct"]
    return payload


def write_winner_json(sym: str, payload: Dict) -> Path:
    CAND_OUT_DIR.mkdir(parents=True, exist_ok=True)
    best_side = payload.get("_best_side", "BOTH")
    out_path = CAND_OUT_DIR / f"trb_{sym}_{best_side}_winner.json"
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="", help="comma-separated subset")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--long-only", action="store_true", help="only sweep long side")
    ap.add_argument("--short-only", action="store_true", help="only sweep short side")
    args = ap.parse_args()

    long_syms, short_syms = load_symbols()
    long_set = set(long_syms)
    short_set = set(short_syms)
    all_syms = sorted(long_set | short_set)

    if args.syms:
        all_syms = [s for s in args.syms.split(",") if s]
    all_syms = [s for s in all_syms if (NPZ_DIR / f"{s}.npz").exists()]

    ts = int(time.time())
    sweep_csv = SWEEP_DIR / f"canonical_per_sym_tradier_{ts}.csv"
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    run_root = SWEEP_DIR / f"per_sym_tradier_{ts}" / "engine_runs"
    run_root.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print(f"per_sym_tradier_profiles — {len(all_syms)} symbols")
    print(f"long={len([s for s in all_syms if s in long_set])} "
          f"short={len([s for s in all_syms if s in short_set])} "
          f"both={len([s for s in all_syms if s in long_set and s in short_set])}")
    print(f"CSV: {sweep_csv}")
    print("=" * 80)

    winners_written: List[Path] = []
    all_results: List[Tuple[str, Dict]] = []
    t_total = time.time()

    for sym in all_syms:
        can_long = sym in long_set and not args.short_only
        can_short = sym in short_set and not args.long_only
        try:
            payload = sweep_sym(sym, can_long, can_short, run_root, sweep_csv, args.years)
            if payload:
                p = write_winner_json(sym, payload)
                winners_written.append(p)
                all_results.append((sym, payload))
        except Exception as e:
            print(f"  EXC {sym}: {e}")
        gc.collect()

    elapsed = time.time() - t_total
    print()
    print("=" * 80)
    print(f"LEADERBOARD — sorted by total_gain_pct (ran {len(all_syms)} syms in {elapsed/60:.1f}min)")
    print("=" * 80)
    all_results.sort(key=lambda kv: kv[1].get("_total_gain_pct", -1e9), reverse=True)
    for i, (sym, pl) in enumerate(all_results[:30], 1):
        print(f"  {i:3d}. {sym:6s} side={pl.get('_best_side','?'):10s} "
              f"pnl={pl.get('_total_gain_pct', 0):+9.1f}% "
              f"pool={pl.get('_pool_sharpe', 0):+.4f} "
              f"tr={pl.get('_trades', 0):>5d} dd={pl.get('_dd', 0):.2f}%")

    # Side recommendation summary
    side_counts: Dict[str, int] = {}
    for _, pl in all_results:
        s = pl.get("_best_side", "BOTH")
        side_counts[s] = side_counts.get(s, 0) + 1
    print()
    print("Side distribution:", " | ".join(f"{k}={v}" for k, v in sorted(side_counts.items())))
    print(f"\nCSV: {sweep_csv}")
    print("Candidates written:")
    for p in winners_written[:10]:
        print(f"  {p.name}")
    if len(winners_written) > 10:
        print(f"  ... and {len(winners_written)-10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
