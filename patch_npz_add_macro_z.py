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


def patch_one(npz_path: Path) -> tuple[str, dict]:
    d = dict(np.load(str(npz_path), allow_pickle=True))
    if "macro_z_D" in d and "macro_z_W" in d and "macro_z_M" in d:
        return "skip_already_present", {}
    stats = {}
    for tf in ("D", "W", "M"):
        key_z = f"macro_z_{tf}"
        if key_z in d:
            continue
        key_close = f"close_{tf}"
        if key_close not in d:
            return f"skip_missing_{key_close}", {}
        close_arr = d[key_close].astype(np.float64)
        n = close_arr.shape[0]
        window = DEFAULT_WINDOWS[tf]
        if n < window:
            d[key_z] = np.zeros(n, dtype=np.float32)
            stats[tf] = f"len={n}<window={window} → zeros"
        else:
            z = rolling_log_zscore(close_arr, window).astype(np.float32)
            d[key_z] = z
            nz = int(np.sum(np.abs(z) > 0.001))
            stats[tf] = f"len={n} nz={nz} max|z|={float(np.max(np.abs(z))):.2f}"
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
