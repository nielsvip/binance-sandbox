#!/usr/bin/env python3
"""Patch existing stock NPZ files to add vwap_D = (high_D + low_D + close_D) / 3."""
import sys
from pathlib import Path
import numpy as np

NPZ_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("backtest_v8/indicators")

patched = 0
skipped = 0
for npz_path in sorted(NPZ_DIR.glob("*.npz")):
    d = dict(np.load(str(npz_path), allow_pickle=True))
    if "vwap_D" in d:
        skipped += 1
        continue
    if "high_D" not in d or "low_D" not in d or "close_D" not in d:
        print(f"  SKIP {npz_path.name}: missing high_D/low_D/close_D")
        continue
    d["vwap_D"] = ((d["high_D"] + d["low_D"] + d["close_D"]) / 3.0).astype(np.float32)
    np.savez_compressed(str(npz_path), **d)
    print(f"  patched {npz_path.name}: vwap_D shape={d['vwap_D'].shape} min={d['vwap_D'].min():.2f}")
    patched += 1

print(f"Done: {patched} patched, {skipped} already had vwap_D")
