#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
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
    """Given an array of per-trade returns (in percent, chronological), compute peak-to-trough max drawdown."""
    if len(trade_returns) == 0:
        return 0.0
    equity = np.cumsum(trade_returns)
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    return float(drawdown.max())


def evaluate_combo(C, fwd, side, combo_keys, horizon, min_trades_per_symbol=30, n_symbols_hint=None):
    """Per-CLAUDE.md rule: metrics are AVG across symbols (not pool), with range + max DD.

    Returns dict:
      avg_sharpe, avg_wr, avg_mean (across symbols)
      sharpe_range (min, p25, median, p75, max), wr_range, mean_range
      avg_dd, max_dd_per_sym (worst single symbol drawdown)
      portfolio_dd (pool-level as secondary reference)
      n_syms_passing, total_trades, avg_trades_per_sym
    """
    if horizon not in fwd:
        return None
    keys = [f"{side}_{k}" for k in combo_keys]
    missing = [k for k in keys if k not in C]
    if missing:
        return {"error": f"MISSING: {missing}"}
    mask = np.ones_like(C[keys[0]])
    for k in keys:
        mask &= C[k]
    ret = fwd[horizon]  # shape (bars, n_symbols)
    n_syms = ret.shape[1]

    per_sym = []
    for s in range(n_syms):
        col_mask = mask[:, s]
        col_rets = ret[:, s][col_mask]
        if len(col_rets) < min_trades_per_symbol:
            continue
        rets_pct = col_rets * 100.0
        sh = float(rets_pct.mean() / max(rets_pct.std(), 1e-10))
        wr = float((rets_pct > 0).mean() * 100)
        mn = float(rets_pct.mean())
        dd = compute_drawdown(rets_pct)
        per_sym.append({"sym_idx": s, "n": len(col_rets), "sharpe": sh, "wr": wr, "mean": mn, "dd": dd})
    if not per_sym:
        return None
    sharpes = np.array([x["sharpe"] for x in per_sym])
    wrs = np.array([x["wr"] for x in per_sym])
    means = np.array([x["mean"] for x in per_sym])
    dds = np.array([x["dd"] for x in per_sym])
    ns = np.array([x["n"] for x in per_sym])

    def pct(arr, p):
        return float(np.percentile(arr, p))

    # Pool-level drawdown for reference (concat all symbols' trade streams, chronological within each)
    # Proper portfolio DD would need a real equity curve with concurrent positions — out of scope here.
    pool_rets = ret[mask] * 100.0
    pool_dd = compute_drawdown(pool_rets)

    return {
        "n_syms_passing": len(per_sym),
        "n_syms_tested": n_syms,
        "total_trades": int(ns.sum()),
        "avg_trades_per_sym": float(ns.mean()),
        "avg_sharpe": float(sharpes.mean()),
        "sharpe_min": float(sharpes.min()),
        "sharpe_p25": pct(sharpes, 25),
        "sharpe_median": pct(sharpes, 50),
        "sharpe_p75": pct(sharpes, 75),
        "sharpe_max": float(sharpes.max()),
        "avg_wr": float(wrs.mean()),
        "wr_min": float(wrs.min()),
        "wr_max": float(wrs.max()),
        "avg_mean": float(means.mean()),
        "mean_min": float(means.min()),
        "mean_max": float(means.max()),
        "avg_dd": float(dds.mean()),
        "max_dd_per_sym": float(dds.max()),
        "pool_dd": pool_dd,
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

        bar_min = "3m" if mode == "crypto" else "5m"
        yrs = n_bars * (3 if mode == "crypto" else 5) / 60 / 24 / 365.25
        print(f"\n{mode.upper()}: {len(loaded)} symbols × {n_bars} bars × {bar_min} base = ~{yrs:.1f} years  |  min_trades/sym={args.min_trades}", flush=True)
        print(f"\n{'Name':<24} {'H':>4} {'Syms':>6} {'AvgN':>6} {'AvgSh':>6} {'ShR':>14} {'AvgWR':>6} {'AvgM%':>6} {'MxDD':>6} {'PoolDD':>6}", flush=True)
        print("─" * 110, flush=True)
        for name, side, keys, h in baseline:
            r = evaluate_combo(C, fwd, side, keys, h, min_trades_per_symbol=args.min_trades)
            if r is None:
                print(f"{name:<24} {h:>4d} — <{args.min_trades} trades/sym on any symbol", flush=True)
                continue
            if "error" in r:
                print(f"{name:<24} {h:>4d} — {r['error']}", flush=True)
                continue
            sh_range = f"[{r['sharpe_min']:.2f}..{r['sharpe_max']:.2f}]"
            print(f"{name:<24} {h:>4d} {r['n_syms_passing']:>3d}/{r['n_syms_tested']:<2d} {r['avg_trades_per_sym']:>6.0f} {r['avg_sharpe']:>6.3f} {sh_range:>14} {r['avg_wr']:>6.1f} {r['avg_mean']:>6.2f} {r['max_dd_per_sym']:>6.1f} {r['pool_dd']:>6.1f}", flush=True)

    if args.mode in ("crypto", "both"):
        run_mode("crypto", CRYPTO_BASELINE)
    if args.mode in ("tradier", "both"):
        run_mode("tradier", TRADIER_BASELINE)


if __name__ == "__main__":
    main()
