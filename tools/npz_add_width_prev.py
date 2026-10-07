#!/usr/bin/env python3
"""npz_add_width_prev — backfill dc_width_{tf}_prev into existing NPZs.

prev[i] = width[i-1], prev[0] = width[0] (roll-first convention, identical to
backtest_v8_precompute.py). Idempotent: skips files/keys already present.
Atomic rewrite only (tmp + os.replace; readers see old-or-new, never partial).
No per-file backups (S1 disk 97% full) — the patch only ADDS keys and is
idempotent; use --force to recompute existing _prev keys.

  python3 tools/npz_add_width_prev.py indicators/IBIT.npz
  python3 tools/npz_add_width_prev.py indicators/*.npz
  python3 tools/npz_add_width_prev.py --force indicators/*.npz
"""
import os
import sys

import numpy as np


def patch(path, force=False):
    d = np.load(path, allow_pickle=True)
    keys = set(d.files)
    widths = sorted(k for k in keys if k.startswith("dc_width_") and not k.endswith("_prev") and k != "dc_width")
    new = {}
    for w in widths:
        p = w + "_prev"
        if p in keys and not force:
            continue
        arr = np.asarray(d[w], dtype=np.float32)
        if arr.size == 0:
            continue
        pr = np.roll(arr, 1).astype(np.float32)
        pr[0] = arr[0]
        new[p] = pr
    if not new:
        return 0
    merged = {k: np.asarray(d[k]) for k in keys}
    merged.update(new)
    tmp = path + ".tmpw.npz"
    np.savez_compressed(tmp, **merged)
    os.replace(tmp, path)
    return len(new)


def main():
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    tot = 0
    for p in args:
        try:
            n = patch(p, force)
        except Exception as e:
            print(f"ERROR {p}: {e}")
            continue
        tot += 1 if n else 0
    print(f"files_patched={tot}")


if __name__ == "__main__":
    main()
