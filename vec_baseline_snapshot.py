#!/usr/bin/env python3
"""Live-config baseline snapshot — computes per-trade Sharpe/WR/Mean + equity-curve max drawdown
for the validated winning combos on the FULL dataset under current live config regime.

Separates tradier vs crypto. Reports both per-trade metrics AND equity-curve max drawdown.
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import build_crypto_conditions, build_tradier_conditions, fwd_returns, score
from vec_validate import load_full


# Current live-config winning combos (validated on full 109-sym × 3yr via vec_phase2)
TRADIER_BASELINE = [
    # (name, side, combo_keys, horizon)
    ("LONG#1 mean-rev", "L", ["above_sma5pct","bb1h_lt0","k4h_lt30","rsi1h_lt30","wt_D"], 128),
    ("LONG#2 mean-rev", "L", ["rsi1h_lt25","sma200up_D","wt_2of3","wt_D"], 512),
    ("LONG#3 momentum", "L", ["mom_dcpos_gt50","rsi15_lt35","rsi1h_lt45","wt_2of3"], 384),
    ("LONG#4 A/B-winner", "L", ["mom_dcpos_gt50","rsi15_lt40","rsi1h_lt22","wt_2of3"], 384),
    ("SHORT#1 RSI-overbought", "S", ["k4h_gt80","not_wt_all3","rsi15_gt60","rsi1h_gt65"], 512),
    ("SHORT#2 RSI+DC", "S", ["dcpos_gt60","k4h_gt80","not_wt_all3","rsi15_gt65"], 512),
    ("SHORT#3 RSI-multi-TF", "S", ["k4h_gt80","not_wt_all3","rsi15_gt65","rsi5_gt65"], 512),
]

CRYPTO_BASELINE = [
    ("LONG#1 mean-rev", "L", ["dc_x1h","dcpos_lt30","mfi1h_lt40","sma200up"], 4),
    ("LONG#2 breakout", "L", ["dc_x1h","dcpos_lt30","wt_1h","wt_4h"], 16),
    ("LONG#3 mom-cont", "L", ["dc_x1h","mfi15_lt40","mom_wt_all3","wt_1h"], 32),
    ("LONG#4 pick=5", "L", ["dc_x1h","k15_lt40","mfi15_lt40","rsi15_lt35","sma200up"], 4),
]


def compute_drawdown(trade_returns: np.ndarray):
    """Given an array of per-trade returns (in percent), compute peak-to-trough max drawdown."""
    if len(trade_returns) == 0:
        return 0.0
    equity = np.cumsum(trade_returns)  # cumulative percent (simplification — real compound uses log-returns)
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    return float(drawdown.max())


def evaluate_combo(C, fwd, side, combo_keys, horizon, min_trades=100):
    """Return (sharpe, wr, mean, std, pf, n_trades, max_drawdown_pct, total_pnl_pct)."""
    if horizon not in fwd:
        return None
    keys = [f"{side}_{k}" for k in combo_keys]
    missing = [k for k in keys if k not in C]
    if missing:
        return {"error": f"MISSING: {missing}"}
    mask = np.ones_like(C[keys[0]])
    for k in keys:
        mask &= C[k]
    m = score(mask, fwd[horizon], min_trades)
    if m is None:
        return None
    # Extract per-trade returns in time order to compute drawdown
    # fwd[horizon] shape = (bars, n_symbols). mask shape same. Flatten in time order.
    ret = fwd[horizon]
    # Convert to per-symbol, per-bar; drawdown computed on pooled chronological stream
    # (crude but directional — real portfolio sim would require per-symbol position tracking)
    rets_flat = ret[mask]  # 1-D array in mask-iteration order (row-major: by bar then symbol)
    rets_pct = rets_flat * 100  # convert to percent
    max_dd = compute_drawdown(rets_pct)
    total_pnl = float(rets_pct.sum())
    return {
        **m,
        "max_dd_pct": max_dd,
        "total_pnl_pct": total_pnl,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier", "both"], default="both")
    ap.add_argument("--bars", type=int, default=80000)
    ap.add_argument("--min-bars", type=int, default=15000)
    ap.add_argument("--min-trades", type=int, default=200)
    ap.add_argument("--npz-dir", default="")
    args = ap.parse_args()

    def run_mode(mode, baseline):
        print(f"\n╔══════ {mode.upper()} LIVE BASELINE ══════╗", flush=True)
        npz_dir = args.npz_dir
        if not npz_dir:
            for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
                sub = "backtest_v4_tradier" if mode == "tradier" else "backtest_v8"
                d = Path(base) / sub / "indicators"
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d); break
        print(f"NPZ: {npz_dir}", flush=True)
        t0 = time.time()
        loaded, n_bars = load_full(mode, npz_dir, args.bars, args.min_bars)
        print(f"[{time.time()-t0:.1f}s] {len(loaded)} symbols × {n_bars} bars", flush=True)
        builder = build_crypto_conditions if mode == "crypto" else build_tradier_conditions
        C, close = builder(loaded)
        horizons_needed = sorted({h for _,_,_,h in baseline})
        fwd = fwd_returns(close, horizons_needed)
        print(f"[{time.time()-t0:.1f}s] Built {len(C)} conditions, fwd for {list(fwd.keys())}", flush=True)

        print(f"\n{'Name':<22} {'H':>5} {'N':>6} {'Sharpe':>7} {'WR%':>6} {'Mean%':>7} {'PF':>6} {'MaxDD%':>7} {'TotPnL%':>8}", flush=True)
        print("─" * 100, flush=True)
        for name, side, keys, h in baseline:
            r = evaluate_combo(C, fwd, side, keys, h, args.min_trades)
            if r is None:
                print(f"{name:<22} {h:>5d} — no trades (<{args.min_trades})", flush=True)
                continue
            if "error" in r:
                print(f"{name:<22} {h:>5d} — {r['error']}", flush=True)
                continue
            print(f"{name:<22} {h:>5d} {r['n']:>6d} {r['sharpe']:>7.3f} {r['wr']:>6.1f} {r['mean']*100:>7.3f} {r['pf']:>6.2f} {r['max_dd_pct']:>7.2f} {r['total_pnl_pct']:>8.1f}", flush=True)

    if args.mode in ("crypto", "both"):
        run_mode("crypto", CRYPTO_BASELINE)
    if args.mode in ("tradier", "both"):
        run_mode("tradier", TRADIER_BASELINE)


if __name__ == "__main__":
    main()
