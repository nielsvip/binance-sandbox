#!/usr/bin/env python3
"""Patch existing NPZ files to add macro_z_{D,W,M} fields without full regen.

Macro z-score = long-window log-price displacement on D/W/M timeframes.
Distinct from BB (short-window breakout envelope). Source: stdev_macro_vec.

Usage:
    python patch_npz_add_macro_z.py [npz_dir] [--syms SYM1,SYM2,...]

Defaults to backtest_v8/indicators/. Skips files already containing macro_z_D.
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from vec_paths.stdev_macro_vec import rolling_log_zscore, DEFAULT_WINDOWS


def _dedup_broadcast(close_bcast: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Extract unique-bar close from a forward-filled broadcast array.
    Returns (unique_values, segment_start_indices_in_broadcast).
    A daily/weekly/monthly close field in NPZ is forward-filled to base-TF
    length (each unique D bar value repeats ~480 times for 3m base, ~78 for
    5m base). To compute a rolling z-score on the macro TF, we must operate
    on the unique value series, not the broadcast.
    """
    bcast = np.asarray(close_bcast, dtype=np.float64)
    n = bcast.shape[0]
    if n == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.int64)
    changes = np.concatenate(([True], bcast[1:] != bcast[:-1]))
    idx = np.flatnonzero(changes).astype(np.int64)
    unique = bcast[idx]
    return unique, idx


def _broadcast_back(unique_z: np.ndarray, idx: np.ndarray, n_total: int) -> np.ndarray:
    """Reverse of _dedup_broadcast: each unique z holds until the next idx."""
    out = np.zeros(n_total, dtype=np.float64)
    if idx.size == 0:
        return out
    ends = np.concatenate((idx[1:], [n_total]))
    for k in range(idx.size):
        out[idx[k]:ends[k]] = unique_z[k]
    return out


def patch_one(npz_path: Path) -> tuple[str, dict]:
    d = dict(np.load(str(npz_path), allow_pickle=True))
    stats = {}
    # If all three already correct (unique-count plausible), skip. Otherwise
    # always recompute — overwrites the buggy broadcast-z fields from prior run.
    for tf in ("D", "W", "M"):
        key_z = f"macro_z_{tf}"
        key_close = f"close_{tf}"
        if key_close not in d:
            return f"skip_missing_{key_close}", {}
        close_bcast = d[key_close]
        unique_close, idx = _dedup_broadcast(close_bcast)
        n_unique = unique_close.shape[0]
        window = DEFAULT_WINDOWS[tf]
        if n_unique < window:
            z_bcast = np.zeros(close_bcast.shape[0], dtype=np.float32)
            stats[tf] = f"uniq={n_unique}<window={window} → zeros"
        else:
            z_unique = rolling_log_zscore(unique_close, window)
            z_bcast = _broadcast_back(z_unique, idx, close_bcast.shape[0]).astype(np.float32)
            max_z = float(np.max(np.abs(z_unique)))
            stats[tf] = f"uniq={n_unique} max|z|={max_z:.2f}"
        d[key_z] = z_bcast
    np.savez_compressed(str(npz_path), **d)
    return "patched", stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("npz_dir", nargs="?", default="backtest_v8/indicators")
    parser.add_argument("--syms", type=str, default="")
    args = parser.parse_args()
    npz_dir = Path(args.npz_dir)
    if not npz_dir.is_absolute():
        npz_dir = _HERE / npz_dir
    if not npz_dir.exists():
        print(f"ERROR: {npz_dir} does not exist")
        return 2
    if args.syms:
        files = [npz_dir / f"{s.strip()}.npz" for s in args.syms.split(",") if s.strip()]
        files = [f for f in files if f.exists()]
    else:
        files = sorted(npz_dir.glob("*.npz"))
    patched = skipped = errored = 0
    for npz_path in files:
        try:
            status, stats = patch_one(npz_path)
        except Exception as e:
            print(f"  ERROR {npz_path.name}: {e}")
            errored += 1
            continue
        if status == "patched":
            patched += 1
            print(f"  patched {npz_path.name}: D={stats.get('D','-')} W={stats.get('W','-')} M={stats.get('M','-')}")
        elif status.startswith("skip"):
            skipped += 1
            if status != "skip_already_present" and skipped < 5:
                print(f"  SKIP {npz_path.name}: {status}")
    print(f"Done: patched={patched} skipped={skipped} errored={errored} total={len(files)}")
    return 0 if errored == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
