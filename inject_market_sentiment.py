#!/usr/bin/env python3
"""Standalone post-pass: inject market_sentiment_score into all NPZ files in a directory.

Two-pass, memory-efficient:
  Pass 1 — load ONLY timestamps + wt_composite_bias (tiny arrays) from every NPZ.
            If wt_composite_bias is absent, derives it from wt_composite_long/short (V4 compat).
  Pass 2 — process ONE NPZ at a time: load, inject market_sentiment_score, save, free.

Score = 50 + (bull_count - bear_count) / total_count * 50  (range 0-100, float32).

Usage:
    python3 inject_market_sentiment.py /path/to/indicators/
    python3 inject_market_sentiment.py /path/to/dir1 /path/to/dir2 ...
    python3 inject_market_sentiment.py /path/to/indicators/ --dry-run
    python3 inject_market_sentiment.py /path/to/indicators/ --crypto-only
    python3 inject_market_sentiment.py /path/to/indicators/ --tradier-only
"""
import argparse
import gc
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB")


def is_crypto(sym: str) -> bool:
    return any(sym.upper().endswith(s) for s in CRYPTO_SUFFIXES)


def _get_bias(z: dict, n: int) -> np.ndarray:
    """Return (n,) int8 array: 1=bull, -1=bear, 0=neutral.
    Uses wt_composite_bias directly (V8 NPZs) or derives from wt_composite_long/short (V4)."""
    bias = z.get("wt_composite_bias")
    if bias is not None and len(bias) == n:
        return np.asarray(bias, dtype=np.int8)
    cl = z.get("wt_composite_long")
    cs = z.get("wt_composite_short")
    if cl is not None and cs is not None and len(cl) == n and len(cs) == n:
        cl = np.asarray(cl, dtype=np.float32)
        cs = np.asarray(cs, dtype=np.float32)
        return np.where(cl > cs, np.int8(1), np.where(cs > cl, np.int8(-1), np.int8(0)))
    return None


def build_ts_score(npz_dir: Path, filter_fn=None) -> dict:
    """Pass 1: load only timestamps+bias per symbol, aggregate per-bar counts, return ts→score dict."""
    ts_bull: dict = defaultdict(int)
    ts_bear: dict = defaultdict(int)
    ts_total: dict = defaultdict(int)
    loaded = 0
    skipped = 0
    for p in sorted(npz_dir.glob("*.npz")):
        sym = p.stem
        if filter_fn and not filter_fn(sym):
            continue
        try:
            z = dict(np.load(str(p), allow_pickle=True))
            ts = z.get("timestamps")
            if ts is None:
                skipped += 1
                continue
            ts = np.asarray(ts, dtype=np.int64)
            n = len(ts)
            bias = _get_bias(z, n)
            if bias is None:
                print(f"  SKIP {sym}: no wt_composite_bias or wt_composite_long/short", flush=True)
                skipped += 1
                continue
            for t, b in zip(ts.tolist(), bias.tolist()):
                ts_total[t] += 1
                if b == 1:
                    ts_bull[t] += 1
                elif b == -1:
                    ts_bear[t] += 1
            loaded += 1
        except Exception as e:
            print(f"  ERR {sym}: {e}", flush=True)
            skipped += 1
    print(f"  Pass 1: {loaded} loaded, {skipped} skipped, {len(ts_total)} unique timestamps", flush=True)
    if not ts_total:
        return {}
    return {t: float(50.0 + (ts_bull[t] - ts_bear[t]) / ts_total[t] * 50.0) for t in ts_total}


def inject(npz_dir: Path, filter_fn=None, dry_run=False) -> int:
    npz_dir = Path(npz_dir)
    if not npz_dir.exists():
        print(f"  DIR NOT FOUND: {npz_dir}", flush=True)
        return 0
    print(f"\n[INJECT] {npz_dir}", flush=True)
    t0 = time.time()
    ts_score = build_ts_score(npz_dir, filter_fn)
    if not ts_score:
        print("  No usable symbols — skipping dir", flush=True)
        return 0
    # Pass 2: inject one NPZ at a time
    updated = 0
    for p in sorted(npz_dir.glob("*.npz")):
        sym = p.stem
        if filter_fn and not filter_fn(sym):
            continue
        try:
            z = dict(np.load(str(p), allow_pickle=True))
            ts = z.get("timestamps")
            if ts is None:
                del z; gc.collect()
                continue
            ts = np.asarray(ts, dtype=np.int64)
            n = len(ts)
            if _get_bias(z, n) is None:
                del z; gc.collect()
                continue
            mss = np.array([ts_score.get(int(t), 50.0) for t in ts], dtype=np.float32)
            if dry_run:
                print(f"  DRY {sym}: mss [{mss.min():.1f},{mss.max():.1f}] mean={mss.mean():.1f}", flush=True)
            else:
                z["market_sentiment_score"] = mss
                np.savez_compressed(str(p), **z)
                updated += 1
            del z, mss; gc.collect()
        except Exception as e:
            print(f"  WRITE_ERR {sym}: {e}", flush=True)
            gc.collect()
    label = "would update" if dry_run else "updated"
    print(f"  Pass 2: {label} {updated} NPZs in {time.time()-t0:.1f}s", flush=True)
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="+")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--crypto-only", action="store_true")
    parser.add_argument("--tradier-only", action="store_true")
    args = parser.parse_args()
    if args.crypto_only and args.tradier_only:
        print("ERROR: --crypto-only and --tradier-only are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    filter_fn = None
    if args.crypto_only:
        filter_fn = is_crypto
    elif args.tradier_only:
        filter_fn = lambda s: not is_crypto(s)
    total = 0
    for d in args.dirs:
        total += inject(Path(d), filter_fn=filter_fn, dry_run=args.dry_run)
    label = "would update" if args.dry_run else "updated"
    print(f"\n[INJECT] Done — {label} {total} NPZs total", flush=True)


if __name__ == "__main__":
    main()
