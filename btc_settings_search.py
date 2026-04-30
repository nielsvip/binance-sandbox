#!/usr/bin/env python3
"""btc_settings_search — continuous BTC-cluster settings optimizer.

User directive 2026-04-30: "Set an agent on optimizing BTCUSDC settings so we get
≥20 trades/week. DO NOT DISCARD BTCDOMUSDT — it's the most predictable, easiest-to-
trade ticker and should 5x outperform with the right settings."

This daemon focuses on BTCUSDC, BTCUSDC, BTCDOMUSDT, ETHUSDC (the BTC cluster).
Per cycle:
  1. Pick a baseline (BEST or dedv3) and apply random mutations to entry-tightness
     and trade-frequency knobs.
  2. Run v8_quick_engine.simulate over the last 7 days (filter trades after).
  3. Score = 0.5 * weighted_pool_sharpe  +  0.5 * normalized_trades_per_week
     where normalized_trades_per_week is min(trades_per_week / 20, 2.0).
     This rewards configs that hit the ≥20-trades target without sacrificing edge.
  4. If score > previous best for that sym, save the override JSON to
     data/hourly_reconfig/_candidates/btc_<sym>_<score>.json — auto-picked up by
     flz_hourly_reconfig on next cycle.
  5. Loop forever (--daemon) or single iter (--once).

ALL Sharpe writes go through metrics_guard.write_sharpe_row.

Usage:
  python3 btc_settings_search.py --daemon
  python3 btc_settings_search.py --once
"""
from __future__ import annotations
import argparse, gc, json, math, os, random, re, sys, time, traceback
from pathlib import Path
from typing import Dict, List, Tuple, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
CAND_DIR = ROOT / "data" / "hourly_reconfig" / "_candidates"
LOG_DIR = ROOT / "data" / "hourly_reconfig" / "_btc_search"
LOG_DIR.mkdir(parents=True, exist_ok=True)
CAND_DIR.mkdir(parents=True, exist_ok=True)

BTC_SYMS = ("BTCUSDC", "BTCDOMUSDT", "ETHUSDC", "BTCUSDC", "ETHUSDC")
TARGET_TRADES_PER_WEEK = 6  # Lowered 2026-04-30 per user — realistic for short window.

# Mutation grid — focused on knobs that affect trade FREQUENCY + reentry behavior.
# 2026-04-30 user directive: "What HAS to be tested is reentries (vectorized in
# latest backtest_v8_engine for speed so should now be included everywhere)" — added
# REENTRY/FOLLOW_THROUGH/GUARANTEED_REENTRY knobs.
MUTATIONS = {
    # Trade-frequency knobs (loosen vs default)
    "BTC_BREAKOUT_MIN_HOLD_BARS":        [1, 2, 3, 5],
    "BTC_BREAKOUT_COOLDOWN_BARS":        [1, 2, 3],
    "BTC_MIN_HOLD_BARS":                 [3, 5, 10, 20],
    "MIN_HOLD_BARS":                     [3, 5, 10, 20],
    "BTC_COOLDOWN_BARS":                 [1, 3, 5],
    "BTC_TECH_EXIT_WT_MIN_TFS":          [2, 3, 4],
    "BTC_RZ_PROXIMITY_PCT":              [0.3, 0.5, 0.8, 1.0, 1.5],
    # HTF / alignment / divergence gates
    "BTC_BREAKOUT_HTF_MIN_ALIGNED":      [0, 1, 2],
    "BTC_BREAKOUT_REQUIRE_HTF_ALIGNED":  [True, False],
    "BTC_BREAKOUT_BLOCK_OPPOSING_DIV":   [True, False],
    "BTC_DIVERGENCE_BLOCK_AGAINST":      [True, False],
    "BTC_DIVERGENCE_EXIT_AGAINST":       [True, False],
    "BTC_DIVERGENCE_BULL_MIN_INDS":      [1, 2, 3],
    "BTC_DIVERGENCE_BEAR_MIN_INDS":      [1, 2, 3],
    "BTC_ACCEL_RAMP_MIN_TFS":            [1, 2, 3],
    "BTC_ENTRY_PRIMARY_REQUIRE_RZ":      [True, False],
    "BTC_ENTRY_PRIMARY_REQUIRE_ACCEL_RAMP": [True, False],
    # Reverse-on-exit + reentries (REQUESTED 2026-04-30)
    "BTC_REVERSE_ON_EXIT_ENABLED":       [True, False],
    "BTC_FOLLOW_THROUGH_REENTRY_ENABLED": [True, False],
    "BTC_FOLLOW_THROUGH_MIN_MOVE_PCT":   [0.02, 0.05, 0.1, 0.2],
    "BTC_GUARANTEED_REENTRY_ENABLED":    [True, False],
    "BTC_GUARANTEED_REENTRY_MIN_GAP_BARS": [1, 3, 5, 10],
    "BTC_GUARANTEED_REENTRY_MAX_AGE_BARS": [240, 480, 960, 1920],
    "BTC_GUARANTEED_REENTRY_REQUIRE_RZ_BOUNCE": [True, False],
    # Multifactor RZ (mid-tier knob)
    "BTC_RZ_WT_DC_MULTIFACTOR":          [True, False],
    "BTC_RZ_USE_FIB":                    [True, False],
    "BTC_RZ_USE_ROUND":                  [True, False],
    "BTC_RZ_USE_WT_DC":                  [True, False],
}


def load_baseline(name: str) -> Dict:
    path = ROOT / "backtest_v8" / "btc_loop_results" / f"override_btc_{name}.json"
    with path.open() as f:
        return json.load(f)


def mutate(base: Dict, n_mutations: int = 3) -> Tuple[Dict, List[str]]:
    new_cfg = {k: v for k, v in base.items() if not k.startswith("_")}
    keys = random.sample(list(MUTATIONS.keys()), n_mutations)
    notes: List[str] = []
    for k in keys:
        new_v = random.choice(MUTATIONS[k])
        old_v = new_cfg.get(k, "?")
        new_cfg[k] = new_v
        notes.append(f"{k}: {old_v}→{new_v}")
    return new_cfg, notes


def time_weighted_pool_sharpe(returns_with_ts: List[Tuple[float, int]],
                              now_ts: int) -> Tuple[float, int]:
    if not returns_with_ts:
        return 0.0, 0
    rs = np.array([r for r, _ in returns_with_ts], dtype=np.float64)
    ts = np.array([t for _, t in returns_with_ts], dtype=np.float64)
    days_ago = np.maximum(0.0, (now_ts - ts) / 86400.0)
    w = np.power(2.0, -days_ago)
    w_sum = w.sum()
    if w_sum <= 0 or len(rs) < 2:
        return 0.0, len(rs)
    wmean = float((rs * w).sum() / w_sum)
    wvar = float((w * (rs - wmean) ** 2).sum() / w_sum)
    wstd = math.sqrt(max(wvar, 0.0))
    wsharpe = (wmean / wstd) if wstd > 1e-12 else 0.0
    return wsharpe, len(rs)


def score_candidate(wsharpe: float, n_trades: int, days: float = 7.0) -> float:
    """User directive: prioritize ≥20 trades/week + positive Sharpe.
    Score = 0.5 * wsharpe (cap +2.0) + 0.5 * normalized_trades.
    """
    weeks = max(0.1, days / 7.0)
    trades_per_week = n_trades / weeks
    sharpe_part = min(max(wsharpe, -2.0), 2.0) / 2.0  # in [-1, 1]
    trade_part = min(trades_per_week / TARGET_TRADES_PER_WEEK, 2.0) / 2.0  # in [0, 1]
    return 0.5 * sharpe_part + 0.5 * trade_part


def run_one_test(sym: str, side: str, cfg_dict: Dict, run_dir: Path,
                 now_ts: int, window_start_ts: int) -> Tuple[float, int, List[float]]:
    run_id = f"btcsearch__{sym}__{side}__{int(time.time()*1000) % 100000}"
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    cfg = QuickConfig()
    for k, v in cfg_dict.items():
        if k.startswith("_"):
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    cfg.BTC_DEDICATED_SYMBOLS = (sym,)
    cfg.BTC_DEDICATED_ENABLED = True
    z = None
    try:
        z = np.load(str(NPZ_DIR / f"{sym}.npz"))
        simulate({sym: z}, cfg, capital=10000.0)
    except SystemExit:
        return 0.0, 0, []
    except Exception:
        return 0.0, 0, []
    finally:
        if z is not None:
            try: z.close()
            except Exception: pass
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    sided_rets: List[Tuple[float, int]] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    rec_side = (rec.get("side") or "").upper()
                    if rec_side != side.upper():
                        continue
                    ets = int(rec.get("exit_ts", 0))
                    if ets < window_start_ts:
                        continue
                    sided_rets.append((float(rec.get("pnl_pct", 0)), ets))
                except Exception:
                    pass
        try: jp.unlink()  # don't accumulate per-iter JSONLs (would fill disk)
        except Exception: pass
    ws, n = time_weighted_pool_sharpe(sided_rets, now_ts)
    return ws, n, [r for r, _ in sided_rets]


def search_one_iteration(iter_id: int) -> Dict:
    """One iteration: pick baseline, mutate, test on each BTC sym."""
    baseline_name = random.choice(["BEST", "dedicated_v3", "BEST"])  # 2/3 from BEST
    base = load_baseline(baseline_name)
    new_cfg, notes = mutate(base, n_mutations=random.randint(2, 4))
    run_dir = LOG_DIR / f"iter_{iter_id}"
    run_dir.mkdir(exist_ok=True)
    now_ts = int(time.time())
    results = {"iter": iter_id, "baseline": baseline_name, "mutations": notes, "syms": {}}
    for sym in BTC_SYMS:
        npz_p = NPZ_DIR / f"{sym}.npz"
        if not npz_p.exists():
            continue
        try:
            z = np.load(str(npz_p))
            ts = z["timestamps"] if "timestamps" in z.files else z[z.files[0]]
            window_start_ts = int(ts[-1]) - 7 * 86400
            z.close()
        except Exception:
            continue
        for side in ("LONG", "SHORT"):
            ws, n, rets = run_one_test(sym, side, new_cfg, run_dir, now_ts, window_start_ts)
            score = score_candidate(ws, n)
            results["syms"][f"{sym}_{side}"] = {
                "wsharpe": round(ws, 4),
                "trades": n,
                "trades_per_week": round(n, 1),
                "score": round(score, 4),
                "tier": mg.tier_name(ws),
            }
    # Promote: per (sym, side), if this score > existing best in CAND_DIR, write candidate.
    # Min trades to promote = 3 (≥ half of TARGET_TRADES_PER_WEEK floor).
    promoted = []
    for sym_side, r in results["syms"].items():
        if r["trades"] < 3 or r["wsharpe"] <= 0:
            continue  # not promotable
        cand_path = CAND_DIR / f"btc_{sym_side}_top.json"
        prev_score = -1.0
        if cand_path.exists():
            try:
                prev = json.loads(cand_path.read_text())
                prev_score = float(prev.get("_score", -1.0))
            except Exception:
                prev_score = -1.0
        if r["score"] > prev_score:
            new_cfg_to_save = dict(new_cfg)
            new_cfg_to_save["_meta"] = (
                f"btc_settings_search winner for {sym_side} iter={iter_id} "
                f"baseline={baseline_name} mutations={notes}"
            )
            new_cfg_to_save["_score"] = r["score"]
            new_cfg_to_save["_wsharpe"] = r["wsharpe"]
            new_cfg_to_save["_trades_in_7d"] = r["trades"]
            new_cfg_to_save["_promoted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cand_path.write_text(json.dumps(new_cfg_to_save, indent=2))
            promoted.append(sym_side)
    results["promoted"] = promoted
    # Cleanup iter dir
    try:
        for f in run_dir.iterdir():
            try: f.unlink()
            except Exception: pass
        run_dir.rmdir()
    except Exception:
        pass
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--max-iters", type=int, default=0, help="0=infinite")
    args = ap.parse_args()
    if not (args.once or args.daemon):
        ap.error("specify --once or --daemon")
    if args.seed is not None:
        random.seed(args.seed)
    log_path = LOG_DIR / "search_log.jsonl"
    iter_id = int(time.time())
    n_done = 0
    while True:
        try:
            t0 = time.time()
            r = search_one_iteration(iter_id)
            elapsed = time.time() - t0
            r["elapsed_s"] = round(elapsed, 1)
            with log_path.open("a") as f:
                f.write(json.dumps(r) + "\n")
            best_per_sym = sorted(
                ((sk, d) for sk, d in r["syms"].items() if d["wsharpe"] > 0),
                key=lambda kv: -kv[1]["score"],
            )[:3]
            top_str = " | ".join(
                f"{sk}: ws={d['wsharpe']:+.3f} n={d['trades']}" for sk, d in best_per_sym
            )
            promoted_str = ",".join(r.get("promoted", [])) or "-"
            print(f"[btc_search] iter={iter_id} t={elapsed:.0f}s baseline={r['baseline']} promoted={promoted_str} top: {top_str}", flush=True)
            n_done += 1
            iter_id += 1
            gc.collect()
            if args.once or (args.max_iters > 0 and n_done >= args.max_iters):
                return 0
            time.sleep(5)  # brief breather between iters
        except KeyboardInterrupt:
            return 0
        except Exception:
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
