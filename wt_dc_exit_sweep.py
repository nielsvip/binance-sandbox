#!/usr/bin/env python3
"""
WT/DC EXIT-ONLY parameter sweep — built on top of wt_dc_delta_engine.py.

Goal per user 2026-04-10: get DELTA + WT_DC exit strategies to 90%+ WR before
wiring into live. Exits are priority #1 — they're the system killer right now.

ENTRY is FIXED at WHALE base. We only vary EXIT parameters:
  - exit_speed_decay_ratio  : how fast z-speed has to die before exit fires
  - exit_accel_threshold    : negative-acceleration floor
  - exit_min_tf             : how many TFs must drop below z-threshold
  - exit_dominant_tf        : "3m", "15m", "1h", or "ANY" — which TF leads the exit
  - require_dc_validity     : when True, also require price to be near dc_low (LONG)
                              or dc_high (SHORT) on the dominant exit TF — confirms
                              the slowdown is real, not noise
  - require_bb_validity     : same idea using bollinger band highs/lows

Each combo runs the full 48-symbol × 4yr backtest. Sequential ~3-5 min per combo,
so ~80-100 combos take 4-8 hours.

Usage:
    python3 wt_dc_exit_sweep.py                    # 80 combo default sweep
    python3 wt_dc_exit_sweep.py --quick            # 8 combos for sanity test
    python3 wt_dc_exit_sweep.py --explore          # ~240 combo deep sweep
    python3 wt_dc_exit_sweep.py --target-wr 90     # stop early when WR ≥90%
"""
import argparse
import itertools
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

BASE_PATH = Path("/Users/niels/Documents/binance")
NPZ_DIR = BASE_PATH / "backtest_v4" / "indicators"
RESULTS_DIR = BASE_PATH / "data" / "wt_dc_research"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_PATH))
from wt_dc_delta_engine import compute_signals, compute_stats, TFS

TF_BAR_MIN = {"3m": 3, "15m": 15, "1h": 60, "4h": 240, "D": 1440}


def whale_base() -> dict:
    """The WHALE config from wt_dc_delta_engine.py — proven Sharpe 46.47, WR 84%."""
    return {
        "tf_weights": {"3m": 0.5, "15m": 1.0, "1h": 2.0, "4h": 3.0, "D": 4.0},
        "min_tf_agree": 4,
        "entry_z_threshold": 1.5,
        "entry_accel_threshold": 0.3,
        "exit_speed_decay_ratio": 0.15,
        "exit_accel_threshold": -0.1,
        "exit_opposing_ratio": 1.5,
        "exit_min_tf": 2,
        "min_hold_bars": 24,
        "max_hold_bars": 1920,
        "pyramid_max": 8,
        "pyramid_min_bars": 24,
        "pyramid_qty_mult": 2.5,
        "pyramid_price_tolerance": 0.01,
        "pyramid_accel_threshold": 0.0,
        "cooldown_bars": 12,
        "tf_z_threshold": 1.0,
        "speed_smooth": 3,
        "accel_lookback": 5,
        "consolidation_bars": 30,
        "consolidation_ratio": 0.4,
        "post_consol_window": 10,
        "warmup": 200,
        "base_qty": 1.0,
        "fee_pct": 0.04,
    }


def run_trades_with_exit_variant(close, sig, cfg, dom_tf, dc_high_3m=None, dc_low_3m=None,
                                 dc_high_15m=None, dc_low_15m=None, dc_high_1h=None,
                                 dc_low_1h=None, require_dc=False):
    """Modified trade runner that lets the exit fire on a SPECIFIC TF's speed decay
    instead of the global combined speed. Also supports DC validity check.

    dom_tf: "3m", "15m", "1h", or "ANY" (use the global combined speed)
    require_dc: when True, only exit if price is touching DC channel edge in our favor
                (LONG: price ≤ dc_low_dom_tf × 1.005; SHORT: price ≥ dc_high_dom_tf × 0.995)
    """
    n = len(close)
    warmup = cfg["warmup"]
    trades = []
    pos = None
    cooldown = 0
    max_hold = cfg["max_hold_bars"]
    pyr_max = cfg["pyramid_max"]
    pyr_min_bars = cfg["pyramid_min_bars"]
    pyr_qty_mult = cfg["pyramid_qty_mult"]
    pyr_price_tol = cfg["pyramid_price_tolerance"]
    pyr_accel_t = cfg["pyramid_accel_threshold"]
    exit_decay = cfg["exit_speed_decay_ratio"]
    exit_accel_t = cfg["exit_accel_threshold"]
    exit_opp = cfg["exit_opposing_ratio"]
    exit_min_tf = cfg["exit_min_tf"]
    min_hold = cfg["min_hold_bars"]
    base_qty = cfg["base_qty"]
    fee_rate = cfg["fee_pct"] / 100 * 2
    min_tf = cfg["min_tf_agree"]

    # Per-TF z-speed accessors
    tf_bull_z = sig.get("tf_bull_z") or {tf: sig["total_bull_z"] for tf in TFS}
    tf_bear_z = sig.get("tf_bear_z") or {tf: sig["total_bear_z"] for tf in TFS}

    if dom_tf in TFS and "tf_bull_z" in sig:
        spd_bull_arr = sig["tf_bull_z"][dom_tf]
        spd_bear_arr = sig["tf_bear_z"][dom_tf]
    else:
        spd_bull_arr = sig["total_bull_z"]
        spd_bear_arr = sig["total_bear_z"]

    ba = sig["bull_accel"]
    bea = sig["bear_accel"]
    btf = sig["bull_tf_count"]
    betf = sig["bear_tf_count"]
    el = sig["entry_long"]
    es = sig["entry_short"]
    pc = sig["post_consol"]

    dc_low_arr = {"3m": dc_low_3m, "15m": dc_low_15m, "1h": dc_low_1h}.get(dom_tf)
    dc_high_arr = {"3m": dc_high_3m, "15m": dc_high_15m, "1h": dc_high_1h}.get(dom_tf)

    for i in range(warmup, n):
        if cooldown > 0:
            cooldown -= 1

        if pos is None:
            if cooldown > 0:
                continue
            if el[i]:
                qty = base_qty * (1.5 if pc[i] else 1.0)
                pos = {"s": "L", "ep": close[i], "eb": i, "q": qty, "c": close[i] * qty,
                       "ne": 1, "ms": spd_bull_arr[i], "leb": i}
            elif es[i]:
                qty = base_qty * (1.5 if pc[i] else 1.0)
                pos = {"s": "S", "ep": close[i], "eb": i, "q": qty, "c": close[i] * qty,
                       "ne": 1, "ms": spd_bear_arr[i], "leb": i}
            continue

        held = i - pos["eb"]
        is_long = pos["s"] == "L"
        avg = pos["c"] / pos["q"]

        if is_long:
            spd = spd_bull_arr[i]
            acc = ba[i]
            opp = spd_bear_arr[i]
            pnl = (close[i] - avg) / avg
            tf_ok = btf[i] >= min_tf
            tf_gone = btf[i] < exit_min_tf
        else:
            spd = spd_bear_arr[i]
            acc = bea[i]
            opp = spd_bull_arr[i]
            pnl = (avg - close[i]) / avg
            tf_ok = betf[i] >= min_tf
            tf_gone = betf[i] < exit_min_tf

        pos["ms"] = max(pos["ms"], spd)

        # Pyramid (unchanged from base)
        if pos["ne"] < pyr_max and (i - pos["leb"]) >= pyr_min_bars:
            spd_back = acc > pyr_accel_t
            if is_long:
                price_ok = close[i] <= close[pos["leb"]] * (1 + pyr_price_tol)
            else:
                price_ok = close[i] >= close[pos["leb"]] * (1 - pyr_price_tol)
            if spd_back and tf_ok and price_ok:
                add_qty = base_qty * pyr_qty_mult * pos["ne"]
                pos["q"] += add_qty
                pos["c"] += close[i] * add_qty
                pos["ne"] += 1
                pos["leb"] = i

        # Exit
        ms = pos["ms"]
        ratio = spd / ms if ms > 0.01 else 0
        dying = ratio < exit_decay
        acc_neg = acc < exit_accel_t
        opp_strong = opp > spd * exit_opp if spd > 0.1 else opp > 1.0

        # DC validity check (optional) — only exit if DC alignment confirms
        dc_ok = True
        if require_dc and dc_low_arr is not None and dc_high_arr is not None:
            if is_long:
                # LONG: exit only if price is near DC low (channel breaking down)
                dc_ok = close[i] <= dc_low_arr[i] * 1.005
            else:
                dc_ok = close[i] >= dc_high_arr[i] * 0.995

        should_exit = False
        reason = ""
        if dying and acc_neg and dc_ok:
            should_exit = True
            reason = "SPEED_DIED"
        elif opp_strong and acc_neg:
            should_exit = True
            reason = "OPPOSING"
        elif held >= max_hold:
            should_exit = True
            reason = "MAX_HOLD"
        elif acc_neg and held > min_hold and tf_gone:
            should_exit = True
            reason = "TF_LOST"

        if should_exit:
            fee = fee_rate * pos["ne"]
            trades.append({
                "s": pos["s"], "avg": avg, "xp": close[i],
                "eb": pos["eb"], "xb": i, "h": held,
                "pnl": pnl - fee, "ne": pos["ne"], "q": pos["q"], "r": reason,
            })
            pos = None
            cooldown = cfg["cooldown_bars"]

    if pos is not None:
        avg = pos["c"] / pos["q"]
        pnl = ((close[-1] - avg) / avg) if pos["s"] == "L" else ((avg - close[-1]) / avg)
        fee = fee_rate * pos["ne"]
        trades.append({
            "s": pos["s"], "avg": avg, "xp": close[-1],
            "eb": pos["eb"], "xb": n - 1, "h": n - 1 - pos["eb"],
            "pnl": pnl - fee, "ne": pos["ne"], "q": pos["q"], "r": "EOD",
        })

    return trades


def compute_signals_with_per_tf(d, cfg):
    """Wrapper around compute_signals that ALSO returns per-TF z-speed arrays
    so the exit can fire on a single TF instead of the combined."""
    sig = compute_signals(d, cfg)
    # Recompute per-TF speeds (compute_signals only returns the combined)
    n = len(d["close"])
    keys = list(d.keys())
    wt_dc_keys = sorted([k for k in keys if "wt" in k or k.startswith("dc")])
    smooth = cfg["speed_smooth"]
    tf_bull = {tf: np.zeros(n) for tf in TFS}
    tf_bear = {tf: np.zeros(n) for tf in TFS}
    tf_c = {tf: 0 for tf in TFS}
    for k in wt_dc_keys:
        arr = d[k].astype(np.float64)
        prev_k = k + "_prev"
        if prev_k in d:
            delta = arr - d[prev_k].astype(np.float64)
        elif d[k].dtype in (np.float32, np.float64) and not k.endswith("_prev") and not k.endswith("_ant"):
            delta = np.empty(n)
            delta[0] = 0
            delta[1:] = arr[1:] - arr[:-1]
        else:
            continue
        delta = np.nan_to_num(delta, 0)
        mt = None
        for tf in TFS:
            if f"_{tf}" in k:
                mt = tf
                break
        if mt:
            tf_bull[mt] += np.maximum(delta, 0)
            tf_bear[mt] += np.maximum(-delta, 0)
            tf_c[mt] += 1
    for tf in TFS:
        if tf_c[tf] > 0:
            tf_bull[tf] /= tf_c[tf]
            tf_bear[tf] /= tf_c[tf]
    if smooth > 1:
        kernel = np.ones(smooth) / smooth
        for tf in TFS:
            tf_bull[tf] = np.convolve(tf_bull[tf], kernel, mode="same")
            tf_bear[tf] = np.convolve(tf_bear[tf], kernel, mode="same")
    tf_bull_z, tf_bear_z = {}, {}
    for tf in TFS:
        m, s = np.mean(tf_bull[tf]), np.std(tf_bull[tf])
        tf_bull_z[tf] = (tf_bull[tf] - m) / s if s > 1e-10 else np.zeros(n)
        m, s = np.mean(tf_bear[tf]), np.std(tf_bear[tf])
        tf_bear_z[tf] = (tf_bear[tf] - m) / s if s > 1e-10 else np.zeros(n)
    sig["tf_bull_z"] = tf_bull_z
    sig["tf_bear_z"] = tf_bear_z
    return sig


def run_one_combo(combo: dict, files: list) -> dict:
    """Run one exit-parameter combo across all 48 symbols."""
    cfg = whale_base()
    cfg.update({
        "exit_speed_decay_ratio": combo["decay"],
        "exit_accel_threshold": combo["accel_t"],
        "exit_min_tf": combo["min_tf_lost"],
    })
    dom_tf = combo["dom_tf"]
    require_dc = combo.get("require_dc", False)

    all_trades = []
    t0 = time.time()
    for fname in files:
        d = np.load(NPZ_DIR / fname, allow_pickle=True)
        close = d["close"].astype(np.float64)
        sig = compute_signals_with_per_tf(d, cfg)
        # Pull DC arrays for the dominant TF if validity check enabled
        dc_low_3m = d.get("dc_low_3m")
        dc_high_3m = d.get("dc_high_3m")
        dc_low_15m = d.get("dc_low_15m")
        dc_high_15m = d.get("dc_high_15m")
        dc_low_1h = d.get("dc_low_1h")
        dc_high_1h = d.get("dc_high_1h")
        try:
            dc_low_3m = d["dc_low_3m"].astype(np.float64) if "dc_low_3m" in d.files else None
            dc_high_3m = d["dc_high_3m"].astype(np.float64) if "dc_high_3m" in d.files else None
            dc_low_15m = d["dc_low_15m"].astype(np.float64) if "dc_low_15m" in d.files else None
            dc_high_15m = d["dc_high_15m"].astype(np.float64) if "dc_high_15m" in d.files else None
            dc_low_1h = d["dc_low_1h"].astype(np.float64) if "dc_low_1h" in d.files else None
            dc_high_1h = d["dc_high_1h"].astype(np.float64) if "dc_high_1h" in d.files else None
        except Exception:
            pass
        trades = run_trades_with_exit_variant(
            close, sig, cfg, dom_tf,
            dc_high_3m=dc_high_3m, dc_low_3m=dc_low_3m,
            dc_high_15m=dc_high_15m, dc_low_15m=dc_low_15m,
            dc_high_1h=dc_high_1h, dc_low_1h=dc_low_1h,
            require_dc=require_dc,
        )
        all_trades.extend(trades)
    elapsed = time.time() - t0

    if not all_trades:
        return {**combo, "n": 0, "sh": 0, "wr": 0, "pf": 0, "ret": 0, "elapsed": round(elapsed, 1), "status": "no_trades"}

    s = compute_stats(all_trades)
    return {
        **combo,
        "n": s["n"], "sh": s["sh"], "wr": s["wr"], "pf": s["pf"],
        "ret": s["ret"], "simple_ret_pct": s.get("simple_ret_pct", 0),
        "dd": s["dd"], "apnl": s["apnl"],
        "ah": s["ah"], "lo": s["lo"], "so": s["so"],
        "elapsed": round(elapsed, 1), "status": "ok",
    }


# ──────────────────────────────────────────────────────────────────────
# Sweep grids
# ──────────────────────────────────────────────────────────────────────
DEFAULT_GRID = {
    "decay": [0.10, 0.15, 0.20, 0.25, 0.30],            # 5
    "accel_t": [-0.3, -0.1, 0.0],                        # 3
    "min_tf_lost": [1, 2, 3],                            # 3
    "dom_tf": ["3m", "15m", "1h", "ANY"],                # 4
    "require_dc": [False],                               # 1 (no DC for default)
}  # = 5 * 3 * 3 * 4 * 1 = 180

EXPLORE_GRID = {
    "decay": [0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30, 0.40],  # 9
    "accel_t": [-0.5, -0.3, -0.1, 0.0, 0.1],                          # 5
    "min_tf_lost": [1, 2, 3, 4],                                       # 4
    "dom_tf": ["3m", "15m", "1h", "ANY"],                              # 4
    "require_dc": [False, True],                                       # 2
}  # = 9 * 5 * 4 * 4 * 2 = 1440

QUICK_GRID = {
    "decay": [0.15, 0.25],
    "accel_t": [-0.1],
    "min_tf_lost": [2],
    "dom_tf": ["3m", "15m", "1h", "ANY"],
    "require_dc": [False],
}  # = 8


def build_combos(grid):
    keys = list(grid.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*[grid[k] for k in keys])]


def combo_name(c: dict) -> str:
    dc = "+DC" if c.get("require_dc") else ""
    return f"dom={c['dom_tf']}|decay={c['decay']}|accel<{c['accel_t']}|tf_lost<{c['min_tf_lost']}{dc}"


def print_report(results: list, top_n: int = 25):
    ok = [r for r in results if r.get("status") == "ok" and r.get("n", 0) > 0]
    print(f"\n{'='*120}")
    print(f"  WT/DC EXIT SWEEP REPORT — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*120}")
    print(f"  Combos: {len(results)} | OK: {len(ok)} | Empty: {len(results) - len(ok)}")
    if not ok:
        return
    # Filter for "production candidates": WR ≥80%, n ≥1000 trades, PF ≥3
    candidates = [r for r in ok if r["wr"] >= 80 and r["n"] >= 1000 and r["pf"] >= 3]
    print(f"  PRODUCTION CANDIDATES (WR≥80, n≥1000, PF≥3): {len(candidates)}")
    print(f"\n  TOP {top_n} BY WIN RATE:")
    print(f"  {'#':<3} {'WR%':>6} {'PF':>7} {'Sharpe':>8} {'AvgPnL':>9} {'Trades':>8} {'Hold':>5}  Combo")
    print(f"  {'-'*116}")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["wr"])[:top_n], 1):
        print(f"  {i:<3} {r['wr']:>5.1f}% {r['pf']:>7.2f} {r['sh']:>+8.2f} {r['apnl']:>+9.4f} {r['n']:>8} {r['ah']:>5.1f}  {combo_name(r)}")
    print(f"\n  TOP 10 BY PF:")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["pf"])[:10], 1):
        print(f"  {i:<3} {r['wr']:>5.1f}% {r['pf']:>7.2f} {r['sh']:>+8.2f} {r['apnl']:>+9.4f} {r['n']:>8}  {combo_name(r)}")
    print(f"\n  TOP 10 BY SHARPE:")
    for i, r in enumerate(sorted(ok, key=lambda x: -x["sh"])[:10], 1):
        print(f"  {i:<3} {r['wr']:>5.1f}% {r['pf']:>7.2f} {r['sh']:>+8.2f} {r['apnl']:>+9.4f} {r['n']:>8}  {combo_name(r)}")
    print(f"{'='*120}\n")


def main():
    p = argparse.ArgumentParser(description="WT/DC EXIT-only parameter sweep")
    p.add_argument("--quick", action="store_true", help="8 combo sanity test")
    p.add_argument("--explore", action="store_true", help="Deep 1440-combo grid")
    p.add_argument("--target-wr", type=float, default=0.0, help="Stop early when WR ≥ this %")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    grid = QUICK_GRID if args.quick else (EXPLORE_GRID if args.explore else DEFAULT_GRID)
    combos = build_combos(grid)
    if args.limit > 0:
        combos = combos[:args.limit]

    files = sorted([f for f in os.listdir(NPZ_DIR) if f.endswith(".npz")])
    print(f"\n{'='*120}")
    print(f"  WT/DC EXIT-ONLY PARAMETER SWEEP")
    print(f"  Symbols: {len(files)} | Combos: {len(combos)}")
    print(f"  NPZ: {NPZ_DIR}")
    print(f"  Target WR: {args.target_wr if args.target_wr > 0 else 'none'}")
    print(f"{'='*120}\n")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    progress_path = RESULTS_DIR / f"exit_sweep_progress_{ts}.json"

    results = []
    t_start = time.time()
    best_wr = 0.0
    for i, c in enumerate(combos, 1):
        r = run_one_combo(c, files)
        results.append(r)
        if r.get("wr", 0) > best_wr:
            best_wr = r["wr"]
        elapsed_total = time.time() - t_start
        eta = (elapsed_total / i) * (len(combos) - i)
        print(f"  [{i:>3}/{len(combos)}] {r['status']:<10} WR={r.get('wr',0):>5.1f}% PF={r.get('pf',0):>6.2f} Sh={r.get('sh',0):>+7.2f} avg={r.get('apnl',0):>+.4f}% n={r.get('n',0):>6} ({r['elapsed']:>5.0f}s, eta {eta/60:.0f}m, best WR={best_wr:.1f}%) | {combo_name(c)}")
        with open(progress_path, "w") as f:
            json.dump({"completed": i, "total": len(combos), "best_wr": best_wr, "results": results}, f, indent=2, default=str)
        if args.target_wr > 0 and best_wr >= args.target_wr:
            print(f"\n  🎯 Target WR {args.target_wr}% reached at combo {i}/{len(combos)}. Stopping early.")
            break

    print_report(results)
    out_path = RESULTS_DIR / f"exit_sweep_{ts}.json"
    with open(out_path, "w") as f:
        json.dump({"args": vars(args), "best_wr": best_wr,
                   "elapsed_total_s": round(time.time() - t_start, 1), "results": results},
                  f, indent=2, default=str)
    print(f"  Saved: {out_path}\n")


if __name__ == "__main__":
    main()
