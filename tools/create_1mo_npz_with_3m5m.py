#!/usr/bin/env python3
"""create_1mo_npz_with_3m5m — create 1mo slice of every NPZ, keeping 3/5m fields, next to original.

Originals: backtest_v8/indicators/*.npz (full ~6yr, 3m crypto /5m stocks, 659 on S1, 4 on Mac)
New:       backtest_v8/indicators_1mo_3m5m/*.npz  (last 30d slice, same fields, 3/5m intact)
Never overwrites originals — writes to NEW dir only. Reversible: rm -rf indicators_1mo_3m5m
Purpose: see whether 1mo + 3/5m granularity improves results vs 15m default.

Usage:
  python tools/create_1mo_npz_with_3m5m.py --symbols BTCUSDC,ZECUSDC,NVDA,GOOGL,MSFT  # sample
  python tools/create_1mo_npz_with_3m5m.py --all  # all 659 (S1 only, ~2min)
  python tools/create_1mo_npz_with_3m5m.py --all --dry-run  # count only
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "backtest_v8" / "indicators"
DST_DIR = ROOT / "backtest_v8" / "indicators_1mo_3m5m"

def slice_one(src: Path, dst: Path) -> dict:
    z = np.load(str(src), allow_pickle=True)
    ts = z["timestamps"].astype(np.int64)
    cutoff = int(ts[-1] - 30*86400)
    si = int(np.searchsorted(ts, cutoff))
    # quick stats
    n_full, n_slice = len(ts), len(ts)-si
    # build sliced dict
    out = {}
    for k in z.files:
        a = z[k]
        if isinstance(a, np.ndarray) and a.ndim == 1 and len(a) == n_full:
            out[k] = a[si:]
        else:
            # 0-d (bar_pattern_codes) or mismatched len — keep as is
            out[k] = a
    z.close()
    # ensure dst parent
    dst.parent.mkdir(parents=True, exist_ok=True)
    # atomic write
    tmp = dst.with_name(dst.name + ".tmp.npz")
    np.savez_compressed(str(tmp), **out)
    tmp.replace(dst)
    # verify
    z2 = np.load(str(dst), allow_pickle=True)
    ok = "timestamps" in z2.files and len(z2["timestamps"]) == n_slice
    z2.close()
    return {"src": str(src), "dst": str(dst), "n_full": n_full, "n_slice": n_slice, "ok": ok, "cutoff": cutoff}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        srcs = [SRC_DIR / f"{s}.npz" for s in syms]
    elif args.all:
        srcs = sorted(SRC_DIR.glob("*.npz"))
    else:
        # default sample
        srcs = [SRC_DIR / f"{s}.npz" for s in ["BTCUSDC","ZECUSDC","NVDA","GOOGL","MSFT"]]
    srcs = [p for p in srcs if p.exists()]
    if not srcs:
        print(f"No src NPZ found in {SRC_DIR} (Mac has 4, S1 has 659)", flush=True)
        return 1
    print(f"Src {SRC_DIR} -> Dst {DST_DIR}  n={len(srcs)} dry={args.dry_run}", flush=True)
    print(f"Originals untouched — new dir only. Revert: rm -rf {DST_DIR}", flush=True)
    if args.dry_run:
        for p in srcs[:10]:
            print(f"  would slice {p.name}", flush=True)
        return 0
    DST_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ok_n = 0
    for p in srcs:
        dst = DST_DIR / p.name
        try:
            r = slice_one(p, dst)
            ok_n += int(r["ok"])
            print(f"  {p.name} {r['n_full']} -> {r['n_slice']} bars {'OK' if r['ok'] else 'FAIL'}", flush=True)
        except Exception as e:
            print(f"  {p.name} FAIL {e}", flush=True)
    print(f"Done {ok_n}/{len(srcs)} in {time.time()-t0:.1f}s -> {DST_DIR} (reversible rm -rf)", flush=True)
    # also write manifest
    man = DST_DIR / "manifest.json"
    man.write_text(json.dumps({"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n": len(srcs), "ok": ok_n, "src_dir": str(SRC_DIR), "dst_dir": str(DST_DIR)}, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
