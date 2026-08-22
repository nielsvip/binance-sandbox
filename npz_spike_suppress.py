#!/usr/bin/env python3
"""npz_spike_suppress — kill bad ticks before they become fake alpha.

WHY
---
CRWD's NPZ carried 7,544 spike bars — 7.7% of the series. Isolated prints at ~4x
the neighbouring price that revert on the very next bar:

    [17631] 2024-09-12 12:40  63.50
    [17632] 2024-09-12 12:45  63.75
    [17633] 2024-09-12 12:50  63.50
    [17634] 2024-09-12 12:55  253.80   <-- bad tick
    [17635] 2024-09-12 13:00  63.50    <-- reverts
    [17637] 2024-09-12 13:10  254.75   <-- again

A backtest buys the real 63.50 and "sells" into the 253.80 print, booking +300%.
Repeated across 2,819 trades that produced $28.7M of imaginary profit on $20k of
capital, and the sym_side ranked as the single best result in the entire book.

Worse than the size of the error is its direction: bad ticks always look like
PROFIT, never loss, so a corrupt symbol floats to the top of any ranking and gets
promoted. Detecting it downstream (tools/opt/evaluate_v12._spike_filtered) is a
backstop; the real fix is to never write the tick.

METHOD
------
A bar is a spike when it deviates from BOTH neighbours in the same direction by
more than `ratio`. Requiring both sides is what separates a bad tick from a real
gap: a genuine move to a new level disagrees with the previous bar but agrees
with the next one, so it is left alone. Overnight gaps, halts and limit moves all
survive for that reason.

Spikes are repaired by linear interpolation between the neighbours, not deleted —
dropping bars would shift every index and silently corrupt bar-indexed ledgers.

Scope: 5 of ~474 symbols in this corpus are affected, and CRWD is 7,544 of the
7,549 bad bars. This is cheap insurance, not a hot path.

USE
---
    from npz_spike_suppress import suppress_spikes, suppress_arrays

    close, n = suppress_spikes(close)                  # one series
    arrays, report = suppress_arrays(arrays)           # whole npz dict, OHLC-aware
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

import numpy as np

# 2.5x against BOTH neighbours. Chosen well above any real 5m move and well below
# the ~4x seen in the CRWD prints, so genuine volatility is never touched.
DEFAULT_RATIO = 2.5
# 9 bars: wide enough that a run of up to 4 consecutive bad ticks cannot move the
# median, narrow enough to track real trend.
MEDIAN_WIN = 9
PRICE_KEYS = ("close", "open", "high", "low", "vwap", "mark", "price")


def _rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    """Centred rolling median via a strided view — O(n*win) but vectorised."""
    if win % 2 == 0:
        win += 1
    half = win // 2
    pad = np.pad(x, (half, half), mode="edge")
    try:
        from numpy.lib.stride_tricks import sliding_window_view

        return np.median(sliding_window_view(pad, win), axis=-1)
    except Exception:                     # very old numpy
        return np.array([np.median(pad[i:i + win]) for i in range(len(x))])


def find_spikes(a: np.ndarray, ratio: float = DEFAULT_RATIO,
                win: int = MEDIAN_WIN) -> np.ndarray:
    """Bars that a rolling median says cannot be real prices.

    Two detectors, unioned:

      NEIGHBOUR  a bar exceeding BOTH neighbours by `ratio`. Catches the classic
                 single bad print and never touches a genuine gap, because a real
                 move to a new level agrees with the bar after it.

      MEDIAN     a bar deviating from the centred rolling median of `win` bars by
                 `ratio`. This is what the neighbour test misses: CONSECUTIVE bad
                 ticks defeat it, since each spike agrees with the spike beside it.
                 CRWD had runs like that — 852 phantom trades survived the
                 neighbour-only filter. The median of a window is unmoved by a
                 minority of outliers, which is the same reason vectorised
                 indicator maths absorbs these naturally.

    Union, not intersection: either signature alone is enough to disqualify a bar.
    """
    x = np.asarray(a, dtype="float64")
    mask = np.zeros(len(x), dtype=bool)
    if len(x) < 3:
        return mask

    prev, cur, nxt = x[:-2], x[1:-1], x[2:]
    ok = np.isfinite(prev) & np.isfinite(cur) & np.isfinite(nxt) & (prev > 0) & (nxt > 0)
    mask[1:-1] = (ok & (cur > ratio * prev) & (cur > ratio * nxt)) | \
                 (ok & (cur * ratio < prev) & (cur * ratio < nxt))

    if len(x) >= win:
        finite = np.isfinite(x) & (x > 0)
        xm = np.where(finite, x, np.nan)
        filled = np.nan_to_num(xm, nan=np.nanmedian(xm) if finite.any() else 0.0)
        med = _rolling_median(filled, win)
        with np.errstate(divide="ignore", invalid="ignore"):
            hi = finite & (med > 0) & (x > ratio * med)
            lo = finite & (med > 0) & (x * ratio < med)
        mask |= hi | lo
    return mask


def suppress_spikes(a: Iterable[float], ratio: float = DEFAULT_RATIO) -> Tuple[np.ndarray, int]:
    """Interpolate spike bars away. Returns (repaired, n_repaired).

    Interpolates rather than drops: removing a bar renumbers every later index and
    would corrupt bar-indexed ledgers and any parallel array in the same NPZ.
    """
    x = np.array(a, dtype="float64", copy=True)
    mask = find_spikes(x, ratio)
    n = int(mask.sum())
    if n:
        idx = np.flatnonzero(mask)
        good = np.flatnonzero(~mask)
        x[idx] = np.interp(idx, good, x[good])
    return x, n


def suppress_arrays(arrays: Dict[str, Any], ratio: float = DEFAULT_RATIO,
                    keys: Iterable[str] = PRICE_KEYS) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Repair every price-like series in an NPZ dict.

    The spike mask is taken from `close` and applied to the whole OHLC family, so
    a repaired bar stays internally consistent — repairing close alone could leave
    high < close and break downstream indicator maths.
    """
    out = dict(arrays)
    report: Dict[str, int] = {}
    base = out.get("close")
    mask = find_spikes(np.asarray(base, dtype="float64"), ratio) if base is not None else None
    for k in list(out):
        if not any(k == pk or k.startswith(pk + "_") for pk in keys):
            continue
        v = out[k]
        if not isinstance(v, np.ndarray) or v.dtype.kind not in "fiu":
            continue
        x = np.array(v, dtype="float64", copy=True)
        m = mask if (mask is not None and len(mask) == len(x)) else find_spikes(x, ratio)
        n = int(m.sum())
        if not n:
            continue
        idx, good = np.flatnonzero(m), np.flatnonzero(~m)
        if len(good) < 2:
            continue
        x[idx] = np.interp(idx, good, x[good])
        out[k] = x.astype(v.dtype, copy=False)
        report[k] = n
    return out, report


def save_npz_atomic(path: str, arrays: Dict[str, Any]) -> None:
    """Write an NPZ via a temp file + rename, never in place.

    IOTXUSDT was destroyed by an in-place `savez_compressed` that died partway
    through (the box was at 100% disk that night): what survived was a 5.2MB
    ZIP64 stub with streaming-size placeholders instead of the 288MB original,
    and every backtest touching that symbol then failed with "File is not a zip
    file". A rename is atomic on the same filesystem, so a dead writer leaves
    the previous file completely intact.
    """
    import os

    d = os.path.dirname(os.path.abspath(path)) or "."
    # The temp name must END in .npz: savez_compressed silently appends .npz to
    # any other name, so a ".npz.tmp" target produces ".npz.tmp.npz" and the
    # rename then moves the wrong file. mkstemp is not used for the same reason
    # in reverse — it pre-creates an empty file that an existence check happily
    # mistakes for the finished archive, which is how this function's first
    # version replaced a 288MB NPZ with 0 bytes.
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.tmp.npz")
    try:
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **arrays)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def audit(path: str, ratio: float = DEFAULT_RATIO) -> Dict[str, Any]:
    """Report spikes in an existing NPZ without modifying it."""
    with np.load(path, allow_pickle=True) as z:
        c = np.asarray(z["close"], dtype="float64") if "close" in z else None
    if c is None:
        return {"path": path, "bars": 0, "spikes": 0, "frac": 0.0}
    m = find_spikes(c, ratio)
    return {"path": path, "bars": int(len(c)), "spikes": int(m.sum()),
            "frac": float(m.sum()) / max(1, len(c))}


if __name__ == "__main__":
    import argparse, glob, json

    ap = argparse.ArgumentParser(description="audit or repair NPZ price spikes")
    ap.add_argument("--dir", default="backtest_v8/indicators")
    ap.add_argument("--ratio", type=float, default=DEFAULT_RATIO)
    ap.add_argument("--repair", action="store_true", help="rewrite affected NPZs")
    ap.add_argument("--min-frac", type=float, default=0.0)
    args = ap.parse_args()

    hits = []
    for p in sorted(glob.glob(f"{args.dir}/*.npz")):
        try:
            r = audit(p, args.ratio)
        except Exception:
            continue
        if r["spikes"] and r["frac"] >= args.min_frac:
            hits.append(r)
    hits.sort(key=lambda r: -r["spikes"])
    for r in hits:
        print(f"  {r['path'].split('/')[-1][:-4]:<14} bars={r['bars']:<9} "
              f"spikes={r['spikes']:<7} {r['frac']*100:.2f}%")
    print(f"{len(hits)} symbol(s) with spikes")

    if args.repair:
        import shutil, time

        stamp = time.strftime("%Y%m%d%H%M")
        for r in hits:
            p = r["path"]
            with np.load(p, allow_pickle=True) as z:
                arrays = {k: z[k] for k in z.files}
            fixed, rep = suppress_arrays(arrays, args.ratio)
            if not rep:
                continue
            shutil.copy2(p, f"{p}.pre_spike_{stamp}.bak")
            save_npz_atomic(p, fixed)
            print(f"  repaired {p.split('/')[-1]}: {json.dumps(rep)}")
