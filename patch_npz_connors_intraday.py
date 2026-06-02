#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""patch_npz_connors_intraday.py — add intraday ConnorsRSI to existing NPZ in-place.

Adds connors_rsi_{3m,5m,15m,1h,4h} (D already produced by precompute) by reusing
the per-TF broadcast close arrays already in each NPZ (close_3m/close_5m/close_15m/
close_1h/close_4h). ConnorsRSI = (RSI(close,3) + RSI(streak,2) + PercentRank(1bar-ret,100))/3,
computed on the TF's UNIQUE bar closes then broadcast back to the base-TF index — byte-
identical method to backtest_v8_precompute.py's connors_rsi_D (lines 1627-1679).

This avoids a full board regen (CLAUDE.md: NPZ regen done wrong 12+ times). It is
additive + idempotent (skips a TF already present unless --force) + atomic (temp+rename).

Usage:
  python3 patch_npz_connors_intraday.py --dir backtest_v8/indicators [--symbols BTCUSDC,ETHUSDC] [--force] [--dry-run]
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

TFS = ["3m", "5m", "15m", "1h", "4h"]


def _connors_rsi_on_closes(closes: np.ndarray) -> np.ndarray:
    """ConnorsRSI on a 1-D close series (one value per TF bar). Mirrors precompute 1634-1668."""
    n = len(closes)
    if n < 5:
        return np.full(n, 50.0, dtype=np.float64)
    c = closes.astype(np.float64)
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag3 = pd.Series(gain).ewm(alpha=1.0 / 3, adjust=False).mean().values
    al3 = pd.Series(loss).ewm(alpha=1.0 / 3, adjust=False).mean().values
    rsi3 = 100.0 - 100.0 / (1.0 + ag3 / np.where(al3 > 0, al3, 1e-10))
    ret_sign = np.sign(delta)
    streak = np.zeros(n)
    for i in range(1, n):
        if ret_sign[i] == 0:
            streak[i] = 0
        elif ret_sign[i] == ret_sign[i - 1]:
            streak[i] = streak[i - 1] + ret_sign[i]
        else:
            streak[i] = ret_sign[i]
    d_streak = np.diff(streak, prepend=streak[0])
    gs = np.where(d_streak > 0, d_streak, 0.0)
    ls = np.where(d_streak < 0, -d_streak, 0.0)
    ag2 = pd.Series(gs).ewm(alpha=1.0 / 2, adjust=False).mean().values
    al2 = pd.Series(ls).ewm(alpha=1.0 / 2, adjust=False).mean().values
    rsi_streak = 100.0 - 100.0 / (1.0 + ag2 / np.where(al2 > 0, al2, 1e-10))
    c_prev = np.roll(c, 1); c_prev[0] = c[0]
    ret1 = np.where(c_prev > 0, (c - c_prev) / c_prev * 100.0, 0.0)
    ret1[0] = 0.0
    pct_rank = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 99)
        window = ret1[lo:i + 1]
        if len(window) > 1:
            pct_rank[i] = (window[:-1] < ret1[i]).sum() / max(len(window) - 1, 1) * 100.0
        else:
            pct_rank[i] = 50.0
    crsi = (rsi3 + rsi_streak + pct_rank) / 3.0
    return np.nan_to_num(crsi, nan=50.0)


def _unique_bars(broadcast: np.ndarray):
    """From a base-TF-broadcast TF-close array, recover (change_indices, unique_values).
    A new TF bar starts where the broadcast value changes (mirrors patcher extract_daily_unique)."""
    n = len(broadcast)
    mask = np.ones(n, dtype=bool)
    mask[1:] = broadcast[1:] != broadcast[:-1]
    idx = np.flatnonzero(mask)
    return idx, broadcast[idx]


def _broadcast_back(change_idx: np.ndarray, tf_vals: np.ndarray, n: int) -> np.ndarray:
    out = np.full(n, 50.0, dtype=np.float64)
    for k, start in enumerate(change_idx):
        end = change_idx[k + 1] if k + 1 < len(change_idx) else n
        out[start:end] = tf_vals[k]
    return out


def patch_one(path: Path, force: bool, dry_run: bool) -> str:
    try:
        f = np.load(str(path), allow_pickle=True)
    except Exception as e:
        return f"LOAD_FAIL {path.name}: {e}"
    data = {k: f[k] for k in f.files}
    n = len(data.get("close", []))
    if n == 0:
        return f"SKIP_EMPTY {path.name}"
    added = []
    for tf in TFS:
        field = f"connors_rsi_{tf}"
        ckey = f"close_{tf}"
        if ckey not in data:
            continue
        if field in data and not force:
            continue
        change_idx, tf_closes = _unique_bars(data[ckey].astype(np.float64))
        if len(tf_closes) < 5:
            data[field] = np.full(n, 50.0, dtype=np.float32)
        else:
            crsi_tf = _connors_rsi_on_closes(tf_closes)
            data[field] = _broadcast_back(change_idx, crsi_tf, n).astype(np.float32)
        added.append(tf)
    if not added:
        return f"NOOP {path.name} (all present)"
    if dry_run:
        return f"DRY {path.name}: would add {added}"
    tmp = path.parent / (path.stem + ".tmp.npz")  # MUST end in .npz — np.savez_compressed appends .npz otherwise
    np.savez_compressed(str(tmp), **data)
    os.replace(str(tmp), str(path))
    return f"OK {path.name}: +{added}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    d = Path(args.dir)
    if args.symbols:
        files = [d / f"{s.strip()}.npz" for s in args.symbols.split(",") if s.strip()]
    else:
        files = sorted(d.glob("*.npz"))
    print(f"patching {len(files)} NPZ in {d} (TFs={TFS}, force={args.force}, dry={args.dry_run})", flush=True)
    t0 = time.time()
    n_ok = 0
    for i, p in enumerate(files):
        if not p.exists():
            print(f"  MISSING {p.name}", flush=True); continue
        r = patch_one(p, args.force, args.dry_run)
        if r.startswith("OK") or r.startswith("DRY"):
            n_ok += 1
        if i < 5 or i % 25 == 0 or not r.startswith(("OK", "NOOP")):
            print(f"  [{i+1}/{len(files)}] {r}", flush=True)
    print(f"done: {n_ok}/{len(files)} patched in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
