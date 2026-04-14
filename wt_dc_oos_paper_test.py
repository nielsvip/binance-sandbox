#!/usr/bin/env python3
"""
WT/DC EXIT OOS PAPER TEST — out-of-sample validation of the sweep winner config.

Splits each symbol's NPZ into IS (older) and OOS (newer) slices.
Runs ONLY the sweep winner exit config on:
  1. FULL window  (sanity baseline matching the live sweep)
  2. IS window    (training-equivalent — should match sweep WR/Sharpe)
  3. OOS window   (last 12 mo — the "forward paper test" the user wants)

If OOS WR/Sharpe holds within 10% of full-window numbers, the winner is robust
on data the optimizer never optimized to (the closest analogue we have to a
real-time forward paper test without standing up sbx infra from scratch).

Also compares against BASELINE (dom_tf=ANY, the current safe live config) on
the same OOS slice so we know the winner actually beats the safe default OOS.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BASE_PATH = Path("/Users/niels/Documents/binance")
NPZ_DIR = BASE_PATH / "backtest_v4" / "indicators"
RESULTS_DIR = BASE_PATH / "data" / "wt_dc_research"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_PATH))
from wt_dc_delta_engine import compute_stats, TFS
from wt_dc_exit_sweep import (
    whale_base,
    compute_signals_with_per_tf,
    run_trades_with_exit_variant,
)


# ─── Sweep winner from data/wt_dc_research/exit_sweep_progress_20260410_020718.json ───
WINNER = {
    "decay": 0.10,        # sweep "ratio < 0.10*max" = live "speed dropped 90% from peak"
    "accel_t": 0.0,
    "min_tf_lost": 1,
    "dom_tf": "3m",
    "require_dc": False,
}
# Live config equivalent (for the deployment write-up):
#   DELTA_EXIT_DOM_TF_ENABLED = True
#   DELTA_EXIT_TF             = "3m"
#   DELTA_EXIT_DECAY_RATIO    = 0.90   (live engine: peak_decay = cur < max * (1 - 0.90))
#   DELTA_EXIT_MIN_TF_LOST    = 1

BASELINE = {
    "decay": 0.10,
    "accel_t": 0.0,
    "min_tf_lost": 1,
    "dom_tf": "ANY",      # current live behavior — combined total_bull/bear
    "require_dc": False,
}


def slice_npz(d, start_idx, end_idx):
    """Return a dict-like wrapper that slices arrays to [start_idx:end_idx]."""
    out = {}
    for k in d.files:
        arr = d[k]
        if isinstance(arr, np.ndarray) and arr.ndim == 1 and len(arr) == len(d["close"]):
            out[k] = arr[start_idx:end_idx]
        else:
            out[k] = arr
    return out


class DictNpz:
    """np.load-compatible dict facade so compute_signals_with_per_tf works on slices."""
    def __init__(self, d):
        self._d = d
        self.files = list(d.keys())
    def __getitem__(self, k):
        return self._d[k]
    def __contains__(self, k):
        return k in self._d
    def __iter__(self):
        return iter(self._d)
    def keys(self):
        return self._d.keys()
    def values(self):
        return self._d.values()
    def items(self):
        return self._d.items()
    def get(self, k, default=None):
        return self._d.get(k, default)


def run_window(combo, files, slice_fn=None, label=""):
    cfg = whale_base()
    cfg.update({
        "exit_speed_decay_ratio": combo["decay"],
        "exit_accel_threshold": combo["accel_t"],
        "exit_min_tf": combo["min_tf_lost"],
    })
    dom_tf = combo["dom_tf"]
    require_dc = combo.get("require_dc", False)

    all_trades = []
    bars_total = 0
    sym_used = 0
    t0 = time.time()
    for fname in files:
        path = NPZ_DIR / fname
        d_full = np.load(path, allow_pickle=True)
        if slice_fn:
            d_dict = slice_fn(d_full)
            if d_dict is None:
                continue
            d = DictNpz(d_dict)
        else:
            d = d_full
        if len(d["close"]) < 1000:
            # Stub data (1586 bars) or too short to compute z-scores meaningfully
            continue
        sym_used += 1
        bars_total += len(d["close"])
        close = d["close"].astype(np.float64)
        sig = compute_signals_with_per_tf(d, cfg)

        try:
            dc_low_3m = d["dc_low_3m"].astype(np.float64) if "dc_low_3m" in d.files else None
            dc_high_3m = d["dc_high_3m"].astype(np.float64) if "dc_high_3m" in d.files else None
            dc_low_15m = d["dc_low_15m"].astype(np.float64) if "dc_low_15m" in d.files else None
            dc_high_15m = d["dc_high_15m"].astype(np.float64) if "dc_high_15m" in d.files else None
            dc_low_1h = d["dc_low_1h"].astype(np.float64) if "dc_low_1h" in d.files else None
            dc_high_1h = d["dc_high_1h"].astype(np.float64) if "dc_high_1h" in d.files else None
        except Exception:
            dc_low_3m = dc_high_3m = dc_low_15m = dc_high_15m = dc_low_1h = dc_high_1h = None

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
        return {"label": label, "n": 0, "sh": 0, "wr": 0, "pf": 0, "ret": 0,
                "simple_ret_pct": 0, "dd": 0, "apnl": 0, "ah": 0, "lo": 0, "so": 0,
                "sym_used": sym_used, "bars_total": bars_total,
                "elapsed": round(elapsed, 1), "status": "no_trades"}
    s = compute_stats(all_trades)
    return {
        "label": label,
        "n": s["n"], "sh": s["sh"], "wr": s["wr"], "pf": s["pf"],
        "ret": s["ret"], "simple_ret_pct": s.get("simple_ret_pct", 0),
        "dd": s["dd"], "apnl": s["apnl"], "ah": s["ah"],
        "lo": s["lo"], "so": s["so"],
        "sym_used": sym_used, "bars_total": bars_total,
        "elapsed": round(elapsed, 1), "status": "ok",
    }


def make_oos_slicer(window_days):
    """Returns a slicer that keeps only the LAST `window_days` of each symbol."""
    def slicer(d_full):
        ts = d_full["timestamps"]
        if len(ts) < 1000:
            return None
        cutoff = ts[-1] - window_days * 86400
        idx = int(np.searchsorted(ts, cutoff))
        if (len(ts) - idx) < 1000:
            return None
        return slice_npz(d_full, idx, len(ts))
    return slicer


def make_is_slicer(window_days):
    """Returns a slicer that keeps everything EXCEPT the last `window_days`."""
    def slicer(d_full):
        ts = d_full["timestamps"]
        if len(ts) < 1000:
            return None
        cutoff = ts[-1] - window_days * 86400
        idx = int(np.searchsorted(ts, cutoff))
        if idx < 1000:
            return None
        return slice_npz(d_full, 0, idx)
    return slicer


def fmt_row(label, r):
    return (f"  {label:<24} n={r['n']:>6} WR={r['wr']:>5.1f}% "
            f"PF={r['pf']:>6.2f} Sh={r['sh']:>+6.2f} "
            f"avg={r['apnl']:>+6.3f}% simret={r['simple_ret_pct']:>+9.0f}% "
            f"dd={r['dd']:>+6.1f}% sym={r.get('sym_used',0):>2} "
            f"bars={r.get('bars_total',0):>8} ({r['elapsed']:>4.0f}s)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--oos-days", type=int, default=365,
                   help="OOS window in days (default 365 = 12 months)")
    p.add_argument("--symbols", type=int, default=0,
                   help="Limit to first N symbols for quick check (0 = all)")
    args = p.parse_args()

    files = sorted([f for f in os.listdir(NPZ_DIR) if f.endswith(".npz")])
    if args.symbols > 0:
        files = files[:args.symbols]

    print(f"\n{'=' * 110}")
    print(f"  WT/DC EXIT OOS PAPER TEST — winner robustness check")
    print(f"  NPZ: {NPZ_DIR}  symbols: {len(files)}  OOS window: {args.oos_days} days")
    print(f"  Winner: {WINNER}")
    print(f"  Baseline: {BASELINE}")
    print(f"{'=' * 110}\n")

    results = {}

    print("  WINNER (dom_tf=3m) ────────────────────────────────────────────────────────────")
    r = run_window(WINNER, files, slice_fn=None, label="WIN_FULL")
    print(fmt_row("WINNER FULL  ", r)); results["WIN_FULL"] = r

    r = run_window(WINNER, files, slice_fn=make_is_slicer(args.oos_days), label="WIN_IS")
    print(fmt_row("WINNER IS    ", r)); results["WIN_IS"] = r

    r = run_window(WINNER, files, slice_fn=make_oos_slicer(args.oos_days), label="WIN_OOS")
    print(fmt_row("WINNER OOS   ", r)); results["WIN_OOS"] = r

    print("\n  BASELINE (dom_tf=ANY = current live) ────────────────────────────────────────")
    r = run_window(BASELINE, files, slice_fn=None, label="BASE_FULL")
    print(fmt_row("BASELINE FULL", r)); results["BASE_FULL"] = r

    r = run_window(BASELINE, files, slice_fn=make_is_slicer(args.oos_days), label="BASE_IS")
    print(fmt_row("BASELINE IS  ", r)); results["BASE_IS"] = r

    r = run_window(BASELINE, files, slice_fn=make_oos_slicer(args.oos_days), label="BASE_OOS")
    print(fmt_row("BASELINE OOS ", r)); results["BASE_OOS"] = r

    print(f"\n{'=' * 110}")
    print("  VERDICT")
    print(f"{'=' * 110}")
    win_oos = results["WIN_OOS"]
    base_oos = results["BASE_OOS"]
    win_full = results["WIN_FULL"]
    if win_oos["n"] > 0 and base_oos["n"] > 0 and win_full["n"] > 0:
        wr_drop = win_full["wr"] - win_oos["wr"]
        sh_drop = win_full["sh"] - win_oos["sh"]
        wr_lift_vs_base = win_oos["wr"] - base_oos["wr"]
        pf_lift_vs_base = win_oos["pf"] - base_oos["pf"]
        sh_lift_vs_base = win_oos["sh"] - base_oos["sh"]
        print(f"  OOS robustness:    WR drop = {wr_drop:+5.1f}pp   Sharpe drop = {sh_drop:+5.2f}")
        print(f"  Winner vs base OOS: WR lift = {wr_lift_vs_base:+5.1f}pp   PF lift = {pf_lift_vs_base:+6.2f}   Sh lift = {sh_lift_vs_base:+5.2f}")
        verdict = "PASS" if (
            win_oos["wr"] >= 80 and win_oos["sh"] >= 1.0 and
            win_oos["pf"] >= 3.0 and wr_lift_vs_base >= 0
        ) else "FAIL"
        print(f"  Deployment verdict: {verdict}")
        results["verdict"] = verdict
        results["wr_drop"] = wr_drop
        results["sh_drop"] = sh_drop
        results["wr_lift_vs_base"] = wr_lift_vs_base
    else:
        print("  Not enough trades for verdict")
        results["verdict"] = "INSUFFICIENT_DATA"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"oos_paper_test_{ts}.json"
    with open(out_path, "w") as f:
        json.dump({"args": vars(args), "winner": WINNER, "baseline": BASELINE,
                   "results": results}, f, indent=2, default=str)
    print(f"\n  Saved: {out_path}\n")


if __name__ == "__main__":
    main()
