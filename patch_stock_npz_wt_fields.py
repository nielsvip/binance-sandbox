#!/usr/bin/env python3
"""Patch stock NPZ files to add missing structural WT fields required by RZ logic.

Fields added (computed from existing wt1/wt2/close):
    wt_momentum_state_{tf}   (int8: -2..2)
    wt_cross_{tf}            (int8: -1/0/1)
    wt_cross_bars_ago_{tf}   (float32)
    wt_cross_value_{tf}      (float32)
    wt_cross_prev_value_{tf} (float32)
    wt_cross_rising_{tf}     (int8)
    wt_peak_{tf}             (float32)
    wt_peak_prev_{tf}        (float32)
    wt_trough_{tf}           (float32)
    wt_trough_prev_{tf}      (float32)
    wt_peak_structure_{tf}   (int8: -1/0/1)
    wt_trough_structure_{tf} (int8: -1/0/1)
    wt_structure_{tf}        (int8: -1/0/1)
    wt_divergence_{tf}       (int8: -1/0/1)
    wt_divergence_strength_{tf} (float32)
    wt_wave_phase_{tf}       (int8: -1/1)
    wt_percentile_{tf}       (float32)
    wt_extreme_{tf}          (int8)
    wt_signal_{tf}           (int8)

Usage:
    python patch_stock_npz_wt_fields.py <dir>                  # all NPZ files in dir
    python patch_stock_npz_wt_fields.py <dir> AAPL SPY         # specific symbols
"""
import sys
from pathlib import Path
import numpy as np

TFS = ["5m", "15m", "1h", "4h", "D"]


def compute_structural_wt(wt1, wt2, close):
    """Compute structural WT fields from wt1/wt2/close arrays.

    Mirrors backtest_v8_precompute.py compute_tf_arrays lines 164-248 exactly."""
    n = len(wt1)
    w1 = np.asarray(wt1, dtype=np.float64)
    w2 = np.asarray(wt2, dtype=np.float64)
    cl = np.asarray(close, dtype=np.float64)
    out = {}
    # Velocity / acceleration
    vel = np.diff(w1, prepend=w1[0])
    # Cross detection
    cb = np.zeros(n, dtype=bool)
    cr = np.zeros(n, dtype=bool)
    cb[1:] = (w1[1:] > w2[1:]) & (w1[:-1] <= w2[:-1])
    cr[1:] = (w1[1:] < w2[1:]) & (w1[:-1] >= w2[:-1])
    out["wt_cross_bull"] = cb.astype(np.int8)
    out["wt_cross_bear"] = cr.astype(np.int8)
    out["wt_cross"] = np.where(cb, 1, np.where(cr, -1, 0)).astype(np.int8)
    out["wt_cross_value"] = w1.astype(np.float32)
    out["wt_cross_prev_value"] = np.roll(w1, 1).astype(np.float32)
    out["wt_cross_rising"] = (w1 > w2).astype(np.int8)
    # Bars ago since last cross
    bars_ago = np.full(n, 999, dtype=np.float32)
    lc = -999
    for i in range(n):
        if cb[i] or cr[i]:
            lc = i
        bars_ago[i] = i - lc if lc >= 0 else 999
    out["wt_cross_bars_ago"] = bars_ago
    # Signal: bull cross below -50 (strong long) / bear cross above +50 (strong short)
    sig = np.zeros(n, dtype=np.int8)
    sig[(cb) & (w1 < -50)] = 1
    sig[(cr) & (w1 > 50)] = -1
    out["wt_signal"] = sig
    out["wt_extreme"] = ((w1 > 60) | (w1 < -60)).astype(np.int8)
    # Percentile (rolling 100-bar)
    pct = np.full(n, 50.0, dtype=np.float32)
    zs = np.zeros(n, dtype=np.float32)
    for i in range(100, n):
        win = w1[i - 100 : i]
        std = np.std(win)
        pct[i] = np.sum(win < w1[i]) / 100.0 * 100
        zs[i] = (w1[i] - np.mean(win)) / std if std > 0.01 else 0
    out["wt_percentile"] = pct
    out["wt_zscore"] = zs
    # Momentum state: -2/-1/0/1/2
    score = w1 - w2
    mom = np.zeros(n, dtype=np.int8)
    mom[(score > 0) & (vel > 0)] = 2
    mom[(score > 0) & (vel <= 0)] = 1
    mom[(score < 0) & (vel < 0)] = -2
    mom[(score < 0) & (vel >= 0)] = -1
    out["wt_momentum_state"] = mom
    # Peaks and troughs — state machine to handle forward-filled HTF values
    # (where adjacent bars can have identical values). Detects turning points
    # by tracking direction changes in the smoothed w1 series.
    peaks = np.zeros(n, dtype=np.float32)
    troughs = np.zeros(n, dtype=np.float32)
    peaks_prev = np.zeros(n, dtype=np.float32)
    troughs_prev = np.zeros(n, dtype=np.float32)
    eps = 0.001
    last_direction = 0  # 1=up, -1=down, 0=flat
    last_val = w1[0]
    lp = 0.0  # current peak
    lp_prev = 0.0  # previous peak
    lt = 0.0  # current trough
    lt_prev = 0.0  # previous trough
    peaks[0] = lp
    troughs[0] = lt
    for i in range(1, n):
        v = w1[i]
        if v > last_val + eps:
            if last_direction == -1:
                # Was going down, now up → last_val was a trough
                lt_prev = lt
                lt = last_val
            last_direction = 1
            last_val = v
        elif v < last_val - eps:
            if last_direction == 1:
                # Was going up, now down → last_val was a peak
                lp_prev = lp
                lp = last_val
            last_direction = -1
            last_val = v
        # (if v == last_val ± eps, hold direction)
        peaks[i] = lp
        troughs[i] = lt
        peaks_prev[i] = lp_prev
        troughs_prev[i] = lt_prev
    out["wt_peak"] = peaks
    out["wt_trough"] = troughs
    out["wt_peak_prev"] = peaks_prev
    out["wt_trough_prev"] = troughs_prev
    # Peak / trough structure: 1 = higher than previous, -1 = lower
    pk_s = np.zeros(n, dtype=np.int8)
    tr_s = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        if peaks[i] != 0 and peaks_prev[i] != 0:
            if peaks[i] > peaks_prev[i]:
                pk_s[i] = 1
            elif peaks[i] < peaks_prev[i]:
                pk_s[i] = -1
        if troughs[i] != 0 and troughs_prev[i] != 0:
            if troughs[i] > troughs_prev[i]:
                tr_s[i] = 1
            elif troughs[i] < troughs_prev[i]:
                tr_s[i] = -1
    out["wt_peak_structure"] = pk_s
    out["wt_trough_structure"] = tr_s
    out["wt_structure"] = pk_s
    # Divergence: bearish if peak_structure=-1 + price higher, bullish if trough_structure=1 + price lower
    div = np.zeros(n, dtype=np.int8)
    for i in range(20, n):
        if pk_s[i] == -1 and cl[i] > cl[i - 20]:
            div[i] = -1
        elif tr_s[i] == 1 and cl[i] < cl[i - 20]:
            div[i] = 1
    out["wt_divergence"] = div
    out["wt_divergence_strength"] = np.abs(div).astype(np.float32)
    # Wave phase: +1 if spread |w1-w2| expanding vs prev bar, -1 if contracting
    sa = np.abs(w1 - w2)
    sp = np.roll(sa, 1)
    out["wt_wave_phase"] = np.where(sa > sp, 1, -1).astype(np.int8)
    return out


def patch_npz(path: Path) -> tuple[int, int]:
    """Returns (fields_added, fields_skipped)."""
    data = dict(np.load(path, allow_pickle=True))
    added = 0
    skipped = 0
    for tf in TFS:
        wt1_key = f"wt1_{tf}"
        wt2_key = f"wt2_{tf}"
        if wt1_key not in data or wt2_key not in data:
            skipped += 1
            continue
        wt1 = data[wt1_key]
        wt2 = data[wt2_key]
        # close key per TF
        close_key = f"close_{tf}"
        if close_key in data:
            close = data[close_key]
        elif "close" in data:
            close = data["close"]
        else:
            skipped += 1
            continue
        # Align lengths
        n = min(len(wt1), len(wt2), len(close))
        if n < 30:
            skipped += 1
            continue
        wt1 = wt1[:n]
        wt2 = wt2[:n]
        close = close[:n]
        structural = compute_structural_wt(wt1, wt2, close)
        # Only add fields that are missing or all-zero
        for base_name, arr in structural.items():
            field_name = f"{base_name}_{tf}"
            existing = data.get(field_name)
            is_empty = (
                existing is None
                or (existing.size == 0)
                or (np.all(existing == 0) and base_name != "wt_signal")  # wt_signal legitimately can be all-zero
            )
            if is_empty:
                # Pad/truncate to match length of existing base-TF arrays
                base_ref = data.get(wt1_key)
                target_n = len(base_ref)
                if len(arr) < target_n:
                    pad = np.zeros(target_n - len(arr), dtype=arr.dtype)
                    arr = np.concatenate([arr, pad])
                elif len(arr) > target_n:
                    arr = arr[:target_n]
                data[field_name] = arr
                added += 1
    np.savez_compressed(path, **data)
    return added, skipped


def main():
    if len(sys.argv) < 2:
        print("Usage: patch_stock_npz_wt_fields.py <dir> [symbol1 symbol2 ...]")
        sys.exit(1)
    d = Path(sys.argv[1])
    if not d.is_dir():
        print(f"Not a directory: {d}")
        sys.exit(1)
    symbols = sys.argv[2:]
    files = []
    if symbols:
        for s in symbols:
            p = d / f"{s}.npz"
            if p.exists():
                files.append(p)
            else:
                print(f"MISSING: {p}")
    else:
        files = sorted(d.glob("*.npz"))
    print(f"Patching {len(files)} NPZ files in {d}")
    total_added = 0
    total_skipped = 0
    for i, f in enumerate(files):
        try:
            added, skipped = patch_npz(f)
            total_added += added
            total_skipped += skipped
            if i % 10 == 0 or i == len(files) - 1:
                print(f"[{i+1}/{len(files)}] {f.name}: +{added} fields, {skipped} TFs skipped")
        except Exception as e:
            print(f"[{i+1}/{len(files)}] {f.name}: ERROR {e}")
    print(f"DONE: total {total_added} fields added across {len(files)} files, {total_skipped} TFs skipped")


if __name__ == "__main__":
    main()
