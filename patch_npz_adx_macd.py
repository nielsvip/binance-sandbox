#!/usr/bin/env python3
"""
patch_npz_adx_macd.py — In-place add 14 missing fields to existing v8 NPZ files.

Fields added (where close_{tf}/high_{tf}/low_{tf} exist):
  - macd_{tf}, macd_signal_{tf}, macd_hist_{tf}
  - macd_crossover_{tf}, macd_crossunder_{tf}    (int8 boolean)
  - adx_{tf}                                      (Wilder ADX(14))

For each TF in: 3m (crypto base), 5m (tradier base), 15m, 1h, 4h, D.
W and M skipped — engine doesn't read MACD/ADX on W/M.

Usage:
  python3 patch_npz_adx_macd.py [--dir /path/to/indicators] [--workers N]

NPZ files are mutated in place via np.savez_compressed (atomic via tmp+rename).
"""
import argparse
import os
import sys
import time
import multiprocessing as mp
from pathlib import Path

import numpy as np
import pandas as pd


TFS_TO_PATCH = ["3m", "5m", "15m", "1h", "4h", "D"]


def _ewm(series_np: np.ndarray, span: int = None, alpha: float = None) -> np.ndarray:
    """Pandas EWM helper — returns numpy float64 array."""
    if span is not None:
        return pd.Series(series_np).ewm(span=span, adjust=False).mean().values
    if alpha is not None:
        return pd.Series(series_np).ewm(alpha=alpha, adjust=False).mean().values
    raise ValueError("need span or alpha")


def _compute_macd_and_cross(close_arr: np.ndarray):
    """Return (macd, signal, hist, crossover_int8, crossunder_int8)."""
    n = len(close_arr)
    if n < 30:
        z32 = np.zeros(n, dtype=np.float32)
        z8 = np.zeros(n, dtype=np.int8)
        return z32, z32.copy(), z32.copy(), z8, z8.copy()
    c = close_arr.astype(np.float64)
    ema12 = _ewm(c, span=12)
    ema26 = _ewm(c, span=26)
    macd = ema12 - ema26
    signal = _ewm(macd, span=9)
    hist = macd - signal
    diff_cur = macd - signal
    diff_prev = np.roll(diff_cur, 1); diff_prev[0] = 0.0
    crossover = ((diff_prev <= 0.0) & (diff_cur > 0.0)).astype(np.int8)
    crossunder = ((diff_prev >= 0.0) & (diff_cur < 0.0)).astype(np.int8)
    return (
        macd.astype(np.float32),
        signal.astype(np.float32),
        hist.astype(np.float32),
        crossover,
        crossunder,
    )


def _compute_adx(high_arr: np.ndarray, low_arr: np.ndarray, close_arr: np.ndarray) -> np.ndarray:
    """Wilder ADX(14) — same formula as backtest_v8_precompute.py adx_1h block."""
    n = len(close_arr)
    if n < 30:
        return np.zeros(n, dtype=np.float32)
    h = high_arr.astype(np.float64)
    l = low_arr.astype(np.float64)
    c = close_arr.astype(np.float64)
    up = np.zeros(n)
    dn = np.zeros(n)
    up[1:] = h[1:] - h[:-1]
    dn[1:] = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr1 = h - l
    tr2 = np.zeros(n); tr2[1:] = np.abs(h[1:] - c[:-1])
    tr3 = np.zeros(n); tr3[1:] = np.abs(l[1:] - c[:-1])
    tr = np.maximum.reduce([tr1, tr2, tr3])
    atr14 = _ewm(tr, alpha=1.0 / 14)
    plus_di = 100.0 * _ewm(plus_dm, alpha=1.0 / 14) / np.where(atr14 > 0, atr14, 1e-10)
    minus_di = 100.0 * _ewm(minus_dm, alpha=1.0 / 14) / np.where(atr14 > 0, atr14, 1e-10)
    dx = 100.0 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) > 0, plus_di + minus_di, 1e-10)
    adx14 = _ewm(dx, alpha=1.0 / 14)
    return np.nan_to_num(adx14, nan=0.0).astype(np.float32)


def patch_one(npz_path: Path) -> tuple:
    """Load NPZ, compute missing fields, save back. Returns (path, status, n_added, err)."""
    try:
        with np.load(npz_path) as z:
            data = {k: z[k] for k in z.files}
    except Exception as e:
        return (str(npz_path), "load_fail", 0, str(e))
    n_base = len(data.get("close", []))
    if n_base == 0:
        return (str(npz_path), "no_close", 0, "")
    added = []
    skipped = []
    for tf in TFS_TO_PATCH:
        ck = f"close_{tf}"
        hk = f"high_{tf}"
        lk = f"low_{tf}"
        if ck not in data:
            continue  # this NPZ doesn't have this TF — skip silently
        close_arr = data[ck]
        if len(close_arr) != n_base:
            skipped.append(f"{tf}_len_mismatch")
            continue
        # MACD + cross — needs only close_{tf}
        macd_k = f"macd_{tf}"
        sig_k = f"macd_signal_{tf}"
        hist_k = f"macd_hist_{tf}"
        co_k = f"macd_crossover_{tf}"
        cu_k = f"macd_crossunder_{tf}"
        if any(k not in data for k in (macd_k, sig_k, hist_k, co_k, cu_k)):
            macd, sig, hist, co, cu = _compute_macd_and_cross(close_arr)
            for k, v in [(macd_k, macd), (sig_k, sig), (hist_k, hist), (co_k, co), (cu_k, cu)]:
                if k not in data:
                    data[k] = v
                    added.append(k)
        # ADX — needs high+low+close
        adx_k = f"adx_{tf}"
        if adx_k not in data:
            if hk in data and lk in data:
                if len(data[hk]) == n_base and len(data[lk]) == n_base:
                    data[adx_k] = _compute_adx(data[hk], data[lk], close_arr)
                    added.append(adx_k)
                else:
                    skipped.append(f"{adx_k}_len_mismatch")
            else:
                skipped.append(f"{adx_k}_no_hl")
    if not added:
        return (str(npz_path), "no_change", 0, "")
    # Atomic save: write to .tmp.npz (np.savez_compressed AUTO-APPENDS .npz to a
    # name that doesn't end in .npz — so we must write to a basename without the
    # .npz suffix, then rename the resulting .npz file onto the target).
    tmp_stem = str(npz_path) + ".tmp"     # e.g. /…/AAPL.npz.tmp (NO .npz suffix)
    tmp_written = tmp_stem + ".npz"        # what savez_compressed actually writes
    try:
        np.savez_compressed(tmp_stem, **data)
        os.replace(tmp_written, npz_path)
    except Exception as e:
        for p in (tmp_stem, tmp_written):
            try:
                if os.path.exists(p): os.unlink(p)
            except Exception:
                pass
        return (str(npz_path), "save_fail", len(added), str(e))
    return (str(npz_path), "patched", len(added), ";".join(skipped) if skipped else "")


def _worker(args):
    return patch_one(Path(args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="NPZ directory (default: auto-detect on this machine)")
    ap.add_argument("--workers", type=int, default=4, help="Parallel processes (default 4)")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N files (0=all)")
    args = ap.parse_args()

    if args.dir:
        npz_dir = Path(args.dir)
    else:
        for cand in (
            Path("/home/niels/binance-sandbox/backtest_v8/indicators"),
            Path("/Users/niels/Documents/binance/backtest_v8/indicators"),
        ):
            if cand.is_dir():
                npz_dir = cand
                break
        else:
            print("ERROR: no NPZ directory found", file=sys.stderr)
            sys.exit(1)

    files = sorted(npz_dir.glob("*.npz"))
    if args.limit > 0:
        files = files[: args.limit]
    print(f"[patch_npz] dir={npz_dir} files={len(files)} workers={args.workers}")
    t0 = time.time()
    counts = {"patched": 0, "no_change": 0, "load_fail": 0, "save_fail": 0, "no_close": 0}
    total_added = 0
    if args.workers <= 1:
        results = [patch_one(p) for p in files]
    else:
        with mp.Pool(args.workers) as pool:
            results = []
            for i, res in enumerate(pool.imap_unordered(_worker, [str(p) for p in files], chunksize=1), 1):
                results.append(res)
                if i % 10 == 0 or i == len(files):
                    elapsed = time.time() - t0
                    eta = elapsed / i * (len(files) - i) if i else 0
                    print(f"  [{i}/{len(files)}] elapsed={elapsed:.0f}s eta={eta:.0f}s last={Path(res[0]).name}:{res[1]}({res[2]})")
    for path, status, n_added, err in results:
        counts[status] = counts.get(status, 0) + 1
        total_added += n_added
        if status in ("load_fail", "save_fail"):
            print(f"  FAIL {Path(path).name}: {status}: {err}", file=sys.stderr)
    print(f"[patch_npz] done in {time.time()-t0:.0f}s — {counts} fields_added={total_added}")


if __name__ == "__main__":
    main()
