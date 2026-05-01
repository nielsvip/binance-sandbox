#!/usr/bin/env python3
"""tradier_settings_search — continuous stock-cluster settings optimizer.

User directive 2026-05-01: "Both in parallel — we need actual trades and results
I can see on the 5057 charts."

Mirrors btc_settings_search.py but for the tradier (stock) universe. Stocks need
different mutations than the BTC cluster — TRADIER_*-prefixed knobs and stock-
specific entry/exit gates. The 4 default candidates in flz_hourly_reconfig
produced 0 trades / 7d on the trc/trb universes; this daemon searches for
mutations that get stocks above ≥6 trades/sym/7d.

Per cycle:
  1. Pick a baseline (BEST or dedv3) and apply random mutations to TRADIER_*
     and stock-relevant entry/exit knobs.
  2. Run v8_quick_engine.simulate (mode=tradier) over the last 7 days.
  3. Score = 0.5 * weighted_pool_sharpe + 0.5 * normalized_trades_per_week.
  4. Promote winners to data/hourly_reconfig/_candidates_tradier/ — auto-picked
     up by hr_trc / hr_trb on next cycle.
  5. Loop forever (--daemon) or single iter (--once).

Usage:
  python3 tradier_settings_search.py --daemon
  python3 tradier_settings_search.py --once
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
CAND_DIR = ROOT / "data" / "hourly_reconfig" / "_candidates_tradier"
LOG_DIR = ROOT / "data" / "hourly_reconfig" / "_tradier_search"
LOG_DIR.mkdir(parents=True, exist_ok=True)
CAND_DIR.mkdir(parents=True, exist_ok=True)

# Liquid, high-volume stocks — the search focuses here for representative results.
# These are typical trb/trc holdings; if the search finds a config that fires here
# it's likely to fire across the broader universe too.
TRADIER_SYMS = ("AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "SPY", "QQQ")
TARGET_TRADES_PER_WEEK = 6

# Stock-specific mutation grid. NO BTC_DEDICATED knobs here.
MUTATIONS = {
    # WT/DC entry score gate (lower = more permissive). Stock universe needs MUCH
    # lower thresholds than crypto — engine tradier path was firing <1 trade/wk
    # at default ~25, even ~18 not enough. Probe down to single digits.
    "TRADIER_WT_DC_ENTRY_THRESHOLD":           [3, 5, 7, 10, 14, 18, 22, 26, 30],
    # Required HTF alignment for entry — 0 = no alignment required (any TF)
    "TRADIER_ENTRY_MIN_ALIGNMENT":             [0, 1, 2, 3, 4],
    "TRADIER_MIN_EXIT_TF_AGAINST_TRADIER":     [1, 2, 3],
    # Hold time / cooldown
    "MIN_HOLD_BARS":                           [1, 3, 5, 10, 20],
    "COOLDOWN_BARS":                           [1, 2, 3, 5, 10],
    "MIN_HOLD_BARS_TRADIER":                   [1, 3, 5, 10],
    "COOLDOWN_BARS_TRADIER":                   [1, 2, 3],
    # K-zone / stoch gates (for reentry & open)
    "K_ZONE_LONG_THRESHOLD_TRADIER":           [25, 35, 45, 55, 65],
    "K_ZONE_SHORT_THRESHOLD_TRADIER":          [35, 45, 55, 65, 75],
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER":   [25, 35, 45, 55, 65],
    # SATOSHIT entry
    "SATOSHIT_MIN_VOTES":                      [1, 2, 3],
    "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER":      [25, 30, 37.5, 45, 55],
    "SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER":      [30, 35, 41.25, 50, 60],
    # Stdev/BB squeeze
    "STDEV_BREAKOUT_ENABLED":                  [True, False],
    "STDEV_BOUNCE_ENABLED":                    [True, False],
    "BB_SQUEEZE_ENABLED":                      [True, False],
    "BB_SQUEEZE_ENTRY_ENABLED":                [True, False],
    "BB_SQUEEZE_WIDTH_PERCENTILE":             [0.05, 0.1, 0.15, 0.2],
    # Stoch cross / RSI / MFI
    "STOCH_CROSS_ENTRY_TRADIER":               [True, False],
    "REENTRY2_STOCH_CROSS_ENABLED":            [True, False],
    "TRADIER_RSI2_EXIT_THRESHOLD_LONG":        [10, 50, 100, 200, 270, 400],
    "TRADIER_RSI2_EXIT_THRESHOLD_SHORT":       [10, 30, 60, 100, 200],
    "TRADIER_MFI_ENTRY_LONG_ENABLED":          [True, False],
    "TRADIER_RSI_ENTRY_LONG_TRADIER":          [-3.0, -1.5, 0, 5, 15],
    "TRADIER_RSI_ENTRY_SHORT_TRADIER":         [25, 35, 45, 52.5, 65, 80],
    # First-hour momentum / DC daytrade
    "TRADIER_FH_MOMENTUM_ENABLED":             [True, False],
    "TRADIER_FH_MOMENTUM_MFI_CONFIRM":         [True, False],
    "TRADIER_DC_DAYTRADE_ENABLED":             [True, False],
    "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION":[True, False],
    "TRADIER_GAP_FILL_ENABLED":                [True, False],
    # B-main entry gate (recent ship)
    "B_MAIN_ENTRY_GATE_ENABLED":               [True, False],
    # Sizing scalars (frequency-relevant)
    "TRADIER_START_POSITION_SIZE":             [3500, 7000, 14000],
    "TRADIER_MAX_POSITION_SIZE":               [7500, 15000, 30000],
}


def load_baseline(name: str) -> Dict:
    """Stocks have no curated 'BEST' override yet — start from QuickConfig defaults
    + a few sensible toggles to surface trades on stocks."""
    if name == "loose":
        return {
            "TRADIER_WT_DC_ENTRY_THRESHOLD": 18,
            "TRADIER_ENTRY_MIN_ALIGNMENT": 1,
            "TRADIER_MIN_EXIT_TF_AGAINST_TRADIER": 1,
            "MIN_HOLD_BARS": 3,
            "COOLDOWN_BARS": 1,
            "STDEV_BREAKOUT_ENABLED": True,
            "STDEV_BOUNCE_ENABLED": True,
            "BB_SQUEEZE_ENABLED": True,
            "B_MAIN_ENTRY_GATE_ENABLED": True,
        }
    if name == "tight":
        return {
            "TRADIER_WT_DC_ENTRY_THRESHOLD": 28,
            "TRADIER_ENTRY_MIN_ALIGNMENT": 3,
            "TRADIER_MIN_EXIT_TF_AGAINST_TRADIER": 3,
            "MIN_HOLD_BARS": 10,
            "COOLDOWN_BARS": 5,
        }
    return {}


def mutate(base: Dict, n_mutations: int = 3) -> Tuple[Dict, List[str]]:
    new_cfg = {k: v for k, v in base.items() if not k.startswith("_")}
    keys = random.sample(list(MUTATIONS.keys()), min(n_mutations, len(MUTATIONS)))
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


def score_candidate(wsharpe: float, n_trades: int) -> float:
    sharpe_part = min(max(wsharpe, -2.0), 2.0) / 2.0
    trade_part = min(n_trades / TARGET_TRADES_PER_WEEK, 2.0) / 2.0
    return 0.5 * sharpe_part + 0.5 * trade_part


def run_one_test(sym: str, side: str, cfg_dict: Dict, run_dir: Path,
                 now_ts: int, window_start_ts: int) -> Tuple[float, int]:
    run_id = f"trasearch__{sym}__{side}__{int(time.time()*1000) % 100000}"
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    cfg = QuickConfig()
    cfg.MODE = "tradier"
    for k, v in cfg_dict.items():
        if k.startswith("_"):
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    z = None
    try:
        z = np.load(str(NPZ_DIR / f"{sym}.npz"))
        simulate({sym: z}, cfg, capital=10000.0)
    except SystemExit:
        return 0.0, 0
    except Exception:
        return 0.0, 0
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
                    if (rec.get("side") or "").upper() != side.upper():
                        continue
                    ets = int(rec.get("exit_ts", 0))
                    if ets < window_start_ts:
                        continue
                    sided_rets.append((float(rec.get("pnl_pct", 0)), ets))
                except Exception:
                    pass
        try: jp.unlink()
        except Exception: pass
    ws, n = time_weighted_pool_sharpe(sided_rets, now_ts)
    return ws, n


def search_one_iteration(iter_id: int) -> Dict:
    baseline_name = random.choice(["loose", "tight", "loose", ""])  # bias toward loose
    base = load_baseline(baseline_name)
    new_cfg, notes = mutate(base, n_mutations=random.randint(2, 5))
    run_dir = LOG_DIR / f"iter_{iter_id}"
    run_dir.mkdir(exist_ok=True)
    now_ts = int(time.time())
    results = {"iter": iter_id, "baseline": baseline_name or "default", "mutations": notes, "syms": {}}
    for sym in TRADIER_SYMS:
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
            ws, n = run_one_test(sym, side, new_cfg, run_dir, now_ts, window_start_ts)
            score = score_candidate(ws, n)
            results["syms"][f"{sym}_{side}"] = {
                "wsharpe": round(ws, 4),
                "trades": n,
                "score": round(score, 4),
                "tier": mg.tier_name(ws),
            }
    # Tradier promotion floor: 1 trade is enough to surface a config. Stocks
    # are inherently lower-frequency than crypto; the broader 100+ sym universe
    # of trc/trb will multiply trade count when this config gets picked up by
    # hr_trc/hr_trb on next cycle.
    promoted: List[str] = []
    for sym_side, r in results["syms"].items():
        if r["trades"] < 1 or r["wsharpe"] <= 0:
            continue
        cand_path = CAND_DIR / f"tradier_{sym_side}_top.json"
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
                f"tradier_settings_search winner for {sym_side} iter={iter_id} "
                f"baseline={baseline_name or 'default'} mutations={notes}"
            )
            new_cfg_to_save["_score"] = r["score"]
            new_cfg_to_save["_wsharpe"] = r["wsharpe"]
            new_cfg_to_save["_trades_in_7d"] = r["trades"]
            new_cfg_to_save["_promoted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            cand_path.write_text(json.dumps(new_cfg_to_save, indent=2))
            promoted.append(sym_side)
    results["promoted"] = promoted
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
    ap.add_argument("--max-iters", type=int, default=0)
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
            top = sorted(
                ((sk, d) for sk, d in r["syms"].items() if d["wsharpe"] > 0),
                key=lambda kv: -kv[1]["score"],
            )[:3]
            top_str = " | ".join(
                f"{sk}: ws={d['wsharpe']:+.3f} n={d['trades']}" for sk, d in top
            )
            promoted_str = ",".join(r.get("promoted", [])) or "-"
            print(f"[tradier_search] iter={iter_id} t={elapsed:.0f}s baseline={r['baseline']} promoted={promoted_str} top: {top_str}", flush=True)
            n_done += 1
            iter_id += 1
            gc.collect()
            if args.once or (args.max_iters > 0 and n_done >= args.max_iters):
                return 0
            time.sleep(5)
        except KeyboardInterrupt:
            return 0
        except Exception:
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
