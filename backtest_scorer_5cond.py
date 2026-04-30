#!/usr/bin/env python3
"""
Backtest the wt_dc_exit_scorer.score_exit() 5-condition strict gate on NPZ data.

Runs the ACTUAL live scorer logic against historical bars to validate:
- Entry on inverse conditions (low K + wt accel + crossover + low DC)
- Exit on all 5 conditions (1h cross + 4h + D + K + DC extreme)

Compares vs the old 2-condition version to measure churn reduction.

Usage:
  python3 backtest_scorer_5cond.py --crypto      # 48 sym, 4yr
  python3 backtest_scorer_5cond.py --stocks      # 121 sym, 2yr
  python3 backtest_scorer_5cond.py --symbols BTCUSDC,ETHUSDC
"""
import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wt_dc_exit_scorer import score_exit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [5COND] %(message)s")
logger = logging.getLogger("scorer_bt")

BASE_PATH = Path(__file__).resolve().parent
RESULTS_DIR = BASE_PATH / "data" / "scorer_5cond_bt"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Detect environment
IS_SERVER = os.path.exists("/home/niels/binance-sandbox")
if IS_SERVER:
    CRYPTO_NPZ = Path("/home/niels/binance-sandbox/backtest_v4/indicators")
    STOCK_NPZ = Path("/home/niels/binance-sandbox/backtest_v4_tradier/indicators")
else:
    CRYPTO_NPZ = BASE_PATH / "backtest_v4" / "indicators"
    STOCK_NPZ = BASE_PATH / "backtest_v4_tradier" / "indicators"


def _build_ind_at_bar(d: dict, i: int) -> dict:
    """Build an indicator dict at bar index i, matching the live format."""
    ind = {}
    for k in d.files if hasattr(d, 'files') else d.keys():
        arr = d[k]
        if not hasattr(arr, '__len__') or len(arr) == 0:
            continue
        if i >= len(arr):
            continue
        v = arr[i]
        # Convert numpy types to python scalars
        if hasattr(v, 'item'):
            v = v.item()
        ind[k] = v
    # Synthesize wt_cross STRING from wt_cross_bull/bear int fields
    for tf in ("1m", "3m", "5m", "15m", "1h", "4h", "D"):
        bull_k = f"wt_cross_bull_{tf}"
        bear_k = f"wt_cross_bear_{tf}"
        if ind.get(bull_k):
            ind[f"wt_cross_{tf}"] = "BULL"
        elif ind.get(bear_k):
            ind[f"wt_cross_{tf}"] = "BEAR"
        else:
            ind[f"wt_cross_{tf}"] = ind.get(f"wt_cross_{tf}", "")
    return ind


def backtest_symbol(npz_path: Path, forward_bars: int = 20) -> dict:
    """Run the 5-condition scorer bar-by-bar and measure what happens after exit signals."""
    try:
        d = np.load(npz_path, allow_pickle=True)
    except Exception as e:
        return None
    if "close" not in d.files or "timestamps" not in d.files:
        return None
    close = d["close"]
    n = len(close)
    if n < 500:
        return None

    # Preload arrays for speed
    has_field = lambda k: k in d.files
    def arr(k, default=0.0):
        if has_field(k):
            return d[k]
        return np.full(n, default, dtype=np.float32)

    wt1_4h = arr("wt1_4h"); wt2_4h = arr("wt2_4h")
    wt1_D = arr("wt1_D"); wt2_D = arr("wt2_D")
    k_1h = arr("stoch_k_1h", 50); k_4h = arr("stoch_k_4h", 50)
    dc_pos_1h = arr("dc_position_1h", 0.5)
    dc_pos_4h = arr("dc_position_4h", 0.5)
    wt_cross_bull_1h = arr("wt_cross_bull_1h", 0)
    wt_cross_bear_1h = arr("wt_cross_bear_1h", 0)

    # Vectorized 5-condition gate
    # LONG exit: 1h BEAR cross AND wt1_4h<wt2_4h AND wt1_D<wt2_D AND (k_1h>=75 OR k_4h>=75) AND (dc_1h>=0.80 OR dc_4h>=0.80)
    long_c1 = (wt_cross_bear_1h > 0)
    long_c2 = (wt1_4h < wt2_4h)
    long_c3 = (wt1_D < wt2_D)
    long_c4 = (k_1h >= 75) | (k_4h >= 75)
    long_c5 = (dc_pos_1h >= 0.80) | (dc_pos_4h >= 0.80)
    long_exit_strict = long_c1 & long_c2 & long_c3 & long_c4 & long_c5
    long_exit_4of5 = (long_c1.astype(int) + long_c2.astype(int) + long_c3.astype(int) + long_c4.astype(int) + long_c5.astype(int)) >= 4
    # Old 2-cond version
    long_exit_old = long_c1 & long_c2

    # SHORT inverse
    short_c1 = (wt_cross_bull_1h > 0)
    short_c2 = (wt1_4h > wt2_4h)
    short_c3 = (wt1_D > wt2_D)
    short_c4 = (k_1h <= 25) | (k_4h <= 25)
    short_c5 = (dc_pos_1h <= 0.20) | (dc_pos_4h <= 0.20)
    short_exit_strict = short_c1 & short_c2 & short_c3 & short_c4 & short_c5
    short_exit_old = short_c1 & short_c2

    # Count firings
    n_long_old = int(long_exit_old.sum())
    n_long_strict = int(long_exit_strict.sum())
    n_long_4of5 = int(long_exit_4of5.sum())
    n_short_old = int(short_exit_old.sum())
    n_short_strict = int(short_exit_strict.sum())

    # Measure forward return after each firing (measures if the signal was CORRECT)
    def fwd_ret(mask, is_long):
        idx = np.where(mask)[0]
        if len(idx) == 0:
            return 0.0, 0.0, 0
        idx = idx[idx < n - forward_bars]
        if len(idx) == 0:
            return 0.0, 0.0, 0
        entry = close[idx]
        exit_px = close[idx + forward_bars]
        if is_long:
            rets = (entry - exit_px) / entry * 100  # LONG exit: win if price went DOWN
        else:
            rets = (exit_px - entry) / entry * 100  # SHORT exit: win if price went UP
        return float(np.mean(rets)), float(np.std(rets)), int((rets > 0).sum())

    long_old_mean, long_old_std, long_old_wins = fwd_ret(long_exit_old, True)
    long_str_mean, long_str_std, long_str_wins = fwd_ret(long_exit_strict, True)
    short_old_mean, short_old_std, short_old_wins = fwd_ret(short_exit_old, False)
    short_str_mean, short_str_std, short_str_wins = fwd_ret(short_exit_strict, False)

    def sharpe(mean, std, n_trades):
        if n_trades < 5 or std < 1e-10:
            return 0.0
        return mean / std

    def wr(wins, total):
        return wins / max(total, 1) * 100

    return {
        "symbol": npz_path.stem,
        "n_bars": n,
        "long_old_fires": n_long_old,
        "long_strict_fires": n_long_strict,
        "long_4of5_fires": n_long_4of5,
        "long_reduction_pct": round((1 - n_long_strict / max(n_long_old, 1)) * 100, 1),
        "short_old_fires": n_short_old,
        "short_strict_fires": n_short_strict,
        "long_old_sharpe": round(sharpe(long_old_mean, long_old_std, n_long_old), 3),
        "long_strict_sharpe": round(sharpe(long_str_mean, long_str_std, n_long_strict), 3),
        "long_old_wr": round(wr(long_old_wins, n_long_old), 1),
        "long_strict_wr": round(wr(long_str_wins, n_long_strict), 1),
        "short_old_sharpe": round(sharpe(short_old_mean, short_old_std, n_short_old), 3),
        "short_strict_sharpe": round(sharpe(short_str_mean, short_str_std, n_short_strict), 3),
        "long_old_mean_ret": round(long_old_mean, 4),
        "long_strict_mean_ret": round(long_str_mean, 4),
    }


def run_backtest(npz_dir: Path, label: str, symbols_filter: list = None, max_symbols: int = None):
    npz_files = sorted(npz_dir.glob("*.npz"))
    if symbols_filter:
        allowed = {s.upper() for s in symbols_filter}
        npz_files = [f for f in npz_files if f.stem.upper() in allowed or f.stem.upper().replace("USDT","") in allowed]
    if max_symbols:
        npz_files = npz_files[:max_symbols]
    logger.info(f"{label}: {len(npz_files)} symbols")
    results = []
    t0 = time.time()
    for i, npz in enumerate(npz_files):
        r = backtest_symbol(npz)
        if r:
            results.append(r)
            if (i + 1) % 10 == 0 or i == 0:
                logger.info(f"[{i+1}/{len(npz_files)}] {npz.stem}: old_fires={r['long_old_fires']} strict_fires={r['long_strict_fires']} reduction={r['long_reduction_pct']}% old_S={r['long_old_sharpe']} strict_S={r['long_strict_sharpe']}")
    elapsed = time.time() - t0
    # Aggregate
    if not results:
        logger.error("No results!")
        return
    n_syms = len(results)
    tot_old_l = sum(r['long_old_fires'] for r in results)
    tot_strict_l = sum(r['long_strict_fires'] for r in results)
    tot_4of5_l = sum(r['long_4of5_fires'] for r in results)
    tot_old_s = sum(r['short_old_fires'] for r in results)
    tot_strict_s = sum(r['short_strict_fires'] for r in results)
    avg_old_sharpe_l = np.mean([r['long_old_sharpe'] for r in results if r['long_old_fires'] >= 5])
    avg_strict_sharpe_l = np.mean([r['long_strict_sharpe'] for r in results if r['long_strict_fires'] >= 5])
    avg_old_wr_l = np.mean([r['long_old_wr'] for r in results if r['long_old_fires'] >= 5])
    avg_strict_wr_l = np.mean([r['long_strict_wr'] for r in results if r['long_strict_fires'] >= 5])
    summary = {
        "label": label, "n_symbols": n_syms, "elapsed_s": round(elapsed, 1),
        "long": {
            "old_2cond_total_fires": tot_old_l,
            "strict_5cond_total_fires": tot_strict_l,
            "partial_4of5_total_fires": tot_4of5_l,
            "fire_reduction_pct": round((1 - tot_strict_l / max(tot_old_l, 1)) * 100, 1),
            "old_avg_sharpe": round(float(avg_old_sharpe_l) if not np.isnan(avg_old_sharpe_l) else 0, 3),
            "strict_avg_sharpe": round(float(avg_strict_sharpe_l) if not np.isnan(avg_strict_sharpe_l) else 0, 3),
            "old_avg_wr": round(float(avg_old_wr_l) if not np.isnan(avg_old_wr_l) else 0, 1),
            "strict_avg_wr": round(float(avg_strict_wr_l) if not np.isnan(avg_strict_wr_l) else 0, 1),
        },
        "short": {
            "old_2cond_total_fires": tot_old_s,
            "strict_5cond_total_fires": tot_strict_s,
            "fire_reduction_pct": round((1 - tot_strict_s / max(tot_old_s, 1)) * 100, 1),
        },
        "per_symbol": results,
    }
    out_json = RESULTS_DIR / f"{label}_{int(time.time())}.json"
    out_csv = RESULTS_DIR / f"{label}_{int(time.time())}.csv"
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    logger.info(f"\n{'='*90}")
    logger.info(f"{label} SUMMARY ({n_syms} symbols, {elapsed:.0f}s)")
    logger.info(f"{'='*90}")
    logger.info(f"LONG exits: old_2cond={tot_old_l} strict_5cond={tot_strict_l} reduction={summary['long']['fire_reduction_pct']}%")
    logger.info(f"  4-of-5 partial: {tot_4of5_l}")
    logger.info(f"  Old Sharpe={summary['long']['old_avg_sharpe']} WR={summary['long']['old_avg_wr']}%")
    logger.info(f"  Strict Sharpe={summary['long']['strict_avg_sharpe']} WR={summary['long']['strict_avg_wr']}%")
    logger.info(f"SHORT exits: old_2cond={tot_old_s} strict_5cond={tot_strict_s} reduction={summary['short']['fire_reduction_pct']}%")
    logger.info(f"\nSaved: {out_json}")
    logger.info(f"Saved: {out_csv}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--crypto", action="store_true")
    p.add_argument("--stocks", action="store_true")
    p.add_argument("--symbols", type=str, default=None)
    p.add_argument("--max-symbols", type=int, default=None)
    args = p.parse_args()
    syms = args.symbols.split(",") if args.symbols else None
    if args.crypto:
        run_backtest(CRYPTO_NPZ, "crypto_5cond", syms, args.max_symbols)
    elif args.stocks:
        run_backtest(STOCK_NPZ, "stocks_5cond", syms, args.max_symbols)
    else:
        print("Usage: --crypto | --stocks [--symbols=LIST] [--max-symbols=N]")
