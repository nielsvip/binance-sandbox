#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""patch_npz_clenow_connors.py — patch existing tradier NPZ files with CLENOW and ConnorsRSI fields.

Adds to each NPZ:
  clenow_score_D  — Clenow slope_ann * R² at each 15m bar (from 90-day rolling daily window)
  clenow_slope_D  — annualized log-regression slope (%)
  clenow_r2_D     — R² of the 90-day regression
  connors_rsi_D   — ConnorsRSI = (RSI3 + RSI_streak + PercentRank) / 3

Usage:
  python3 patch_npz_clenow_connors.py [--dir backtest_v4_tradier/indicators] [--symbols AAPL,MSFT]
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np


def compute_rsi(series: np.ndarray, period: int) -> np.ndarray:
    n = len(series)
    rsi = np.full(n, 50.0, dtype=np.float32)
    if n < period + 1:
        return rsi
    delta = np.diff(series, prepend=series[0]).astype(np.float64)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.convolve(gain, np.ones(period) / period, mode="full")[:n]
    avg_loss = np.convolve(loss, np.ones(period) / period, mode="full")[:n]
    for i in range(period, n):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / np.where(avg_loss > 0, avg_loss, 1.0), 100.0)
    rsi_vals = np.where(avg_loss == 0, 100.0, 100.0 - 100.0 / (1.0 + rs))
    rsi[:] = rsi_vals.astype(np.float32)
    return rsi


def compute_streak_rsi(series: np.ndarray) -> np.ndarray:
    n = len(series)
    streak = np.zeros(n, dtype=np.float64)
    cur = 0.0
    for i in range(1, n):
        d = series[i] - series[i - 1]
        if d > 0:
            cur = max(cur + 1.0, 1.0)
        elif d < 0:
            cur = min(cur - 1.0, -1.0)
        else:
            cur = 0.0
        streak[i] = cur
    streak = streak.astype(np.float64)
    return compute_rsi(streak, 2).astype(np.float32)


def compute_percent_rank(series: np.ndarray, period: int = 100) -> np.ndarray:
    n = len(series)
    pr = np.full(n, 50.0, dtype=np.float32)
    rets = np.diff(series, prepend=series[0]) / np.where(series > 0, series, 1.0)
    for i in range(period, n):
        window = rets[i - period:i]
        pr[i] = float(np.sum(window < rets[i]) / period * 100.0)
    return pr


def compute_connors_rsi_series(closes: np.ndarray) -> np.ndarray:
    rsi3 = compute_rsi(closes, 3)
    streak_rsi = compute_streak_rsi(closes)
    pct_rank = compute_percent_rank(closes, 100)
    crsi = (rsi3.astype(np.float32) + streak_rsi.astype(np.float32) + pct_rank.astype(np.float32)) / 3.0
    return np.clip(crsi, 0.0, 100.0)


def compute_clenow_rolling(closes: np.ndarray, lookback: int = 90):
    n = len(closes)
    slope_arr = np.zeros(n, dtype=np.float32)
    r2_arr = np.zeros(n, dtype=np.float32)
    score_arr = np.zeros(n, dtype=np.float32)
    x = np.arange(lookback, dtype=np.float64)
    mx = np.mean(x)
    xc = x - mx
    ssxx = np.dot(xc, xc)
    for i in range(lookback, n):
        chunk = closes[i - lookback:i]
        if np.any(chunk <= 0):
            continue
        y = np.log(chunk.astype(np.float64))
        my = np.mean(y)
        yc = y - my
        if ssxx == 0:
            continue
        b = np.dot(xc, yc) / ssxx
        slope_ann = (np.exp(b * 252) - 1) * 100.0
        ss_res = np.sum((yc - b * xc) ** 2)
        ss_tot = np.sum(yc ** 2)
        r2 = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        slope_arr[i] = float(slope_ann)
        r2_arr[i] = float(r2)
        score_arr[i] = float(slope_ann * r2)
    return slope_arr, r2_arr, score_arr


def extract_daily_unique(arr_15m: np.ndarray) -> tuple:
    n = len(arr_15m)
    change_mask = np.zeros(n, dtype=bool)
    change_mask[0] = True
    change_mask[1:] = arr_15m[1:] != arr_15m[:-1]
    day_indices = np.where(change_mask)[0]
    day_values = arr_15m[day_indices]
    return day_indices, day_values


def map_daily_to_15m(day_indices: np.ndarray, day_values: np.ndarray, n: int) -> np.ndarray:
    result = np.full(n, np.nan, dtype=np.float32)
    for k, idx in enumerate(day_indices):
        next_idx = day_indices[k + 1] if k + 1 < len(day_indices) else n
        result[idx:next_idx] = day_values[k]
    return result


def patch_symbol(npz_path: Path, dry_run: bool = False) -> str:
    try:
        f = np.load(str(npz_path), allow_pickle=True)
        data = dict(f)
    except Exception as e:
        return f"LOAD_ERROR: {e}"

    if "close_D" not in data:
        return "SKIP: no close_D"

    already_have = "clenow_score_D" in data and "connors_rsi_D" in data
    if already_have:
        return "SKIP: already patched"

    close_D_15m = data["close_D"].astype(np.float32)
    n = len(close_D_15m)

    day_indices, day_closes = extract_daily_unique(close_D_15m)
    if len(day_closes) < 110:
        return f"SKIP: only {len(day_closes)} unique days"

    slope_d, r2_d, score_d = compute_clenow_rolling(day_closes, lookback=90)
    crsi_d = compute_connors_rsi_series(day_closes)

    slope_15m = map_daily_to_15m(day_indices, slope_d, n)
    r2_15m = map_daily_to_15m(day_indices, r2_d, n)
    score_15m = map_daily_to_15m(day_indices, score_d, n)
    crsi_15m = map_daily_to_15m(day_indices, crsi_d, n)

    if not dry_run:
        data["clenow_slope_D"] = slope_15m
        data["clenow_r2_D"] = r2_15m
        data["clenow_score_D"] = score_15m
        data["connors_rsi_D"] = crsi_15m
        np.savez_compressed(str(npz_path), **data)

    return f"OK: {len(day_closes)} days, clenow_score range [{score_15m[~np.isnan(score_15m)].min():.1f},{score_15m[~np.isnan(score_15m)].max():.1f}], crsi range [{crsi_15m[~np.isnan(crsi_15m)].min():.1f},{crsi_15m[~np.isnan(crsi_15m)].max():.1f}]"


def _patch_worker(p: Path, dry_run: bool = False) -> tuple:
    return str(p.stem), patch_symbol(p, dry_run)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="backtest_v4_tradier/indicators")
    p.add_argument("--symbols", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()

    ind_dir = Path(args.dir)
    if not ind_dir.exists():
        print(f"ERROR: {ind_dir} does not exist")
        sys.exit(1)

    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",")]
        paths = [ind_dir / f"{s}.npz" for s in syms if (ind_dir / f"{s}.npz").exists()]
    else:
        paths = sorted(ind_dir.glob("*.npz"))

    print(f"Patching {len(paths)} NPZ files in {ind_dir} (dry_run={args.dry_run})")
    t0 = time.time()

    if args.workers > 1:
        from multiprocessing import Pool
        import functools
        _fn = functools.partial(_patch_worker, dry_run=args.dry_run)
        with Pool(args.workers) as pool:
            results = pool.map(_fn, paths)
    else:
        results = [(p.stem, patch_symbol(p, args.dry_run)) for p in paths]

    ok = sum(1 for _, r in results if r.startswith("OK"))
    skip = sum(1 for _, r in results if r.startswith("SKIP"))
    err = sum(1 for _, r in results if "ERROR" in r)
    for sym, r in results:
        if not r.startswith("SKIP"):
            print(f"  {sym}: {r}")
    print(f"\nDone: {ok} patched, {skip} skipped, {err} errors in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
