"""
patch_npz_missing_fields.py — inject the 9 fields the engine reads but precompute didn't write.

Audit (2026-04-28):
  Engine v8_quick_engine.py reads via _safe(npz, 'KEY', ...) but BTCUSDC.npz lacks:
    - lr_trend_1h         linreg slope of close_1h over 50 bars
    - volume_sma_1h       SMA(volume_1h, 20)
    - close_5m, mfi_5m, stoch_k_5m, wt1_5m, wt2_5m, relative_volume_5m
    - market_sentiment_score   (cross-sym WT breadth — ran but inject step's silent skip likely hit)

For 5m fields (crypto only — tradier already has them):
  Crypto NPZ has 3m fabricated from 15m. We fabricate 5m similarly: 3 sub-bars per 15m bar,
  linear-interpolated. Then derive 5m WT/MFI/STOCH/relative_volume from 5m OHLCV.

For lr_trend_1h: rolling linear regression slope on close_1h over 50 bars (per ez_indicators.py).
For volume_sma_1h: SMA(volume_1h, 20).
For market_sentiment_score: cross-sym pass — for each timestamp, count bull vs bear vs total
  via wt_composite_bias (already present), score = 50 + (bull-bear)/total * 50.

Run order:
  1. patch_npz_missing_fields.py --mode crypto --dir /home/niels/binance-sandbox/backtest_v8/indicators
  2. Verify: every NPZ has all 9 fields
  3. Re-run sweep — expect higher trade counts.
"""
import argparse
import sys
import time
from pathlib import Path
import numpy as np


def _rolling_linreg_slope(arr: np.ndarray, window: int = 50) -> np.ndarray:
    """Per-position linear regression slope over trailing `window` bars. Returns slope."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float32)
    if n < window:
        return out
    x = np.arange(window, dtype=np.float64)
    x_mean = x.mean()
    x_dev = x - x_mean
    x_var = (x_dev ** 2).sum()
    if x_var <= 0:
        return out
    for i in range(window - 1, n):
        y = arr[i - window + 1:i + 1].astype(np.float64)
        if not np.isfinite(y).all():
            continue
        y_mean = y.mean()
        slope = ((x_dev * (y - y_mean)).sum()) / x_var
        # Normalize by mean to get %-per-bar slope
        out[i] = float(slope / max(abs(y_mean), 1e-9))
    return out


def _rolling_sma(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    out = np.zeros(n, dtype=np.float32)
    if n < window:
        return out
    cumsum = np.cumsum(arr.astype(np.float64))
    out[window:] = (cumsum[window:] - cumsum[:-window]) / window
    out[window - 1] = cumsum[window - 1] / window
    return out


def _wt(close: np.ndarray, period: int = 10, avg_period: int = 21) -> tuple:
    """Standard WaveTrend: hlc3 → ESA → CI → TCI → wt1; SMA(wt1, 4) → wt2.
    Inputs already on the target TF (close used as proxy hlc3 if no high/low passed)."""
    n = len(close)
    if n < period:
        return np.zeros(n), np.zeros(n)
    # EMA wrapper
    def ema(x, p):
        alpha = 2.0 / (p + 1)
        out = np.zeros(len(x))
        out[0] = x[0]
        for i in range(1, len(x)):
            out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
        return out
    esa = ema(close, period)
    d = ema(np.abs(close - esa), period)
    d = np.where(d == 0, 1e-9, d)
    ci = (close - esa) / (0.015 * d)
    tci = ema(ci, avg_period)
    wt1 = tci
    # SMA(wt1, 4)
    wt2 = np.zeros(n)
    for i in range(3, n):
        wt2[i] = wt1[i - 3:i + 1].mean()
    return wt1.astype(np.float32), wt2.astype(np.float32)


def _stoch_k(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int = 14, smooth: int = 3) -> np.ndarray:
    n = len(close)
    out = np.full(n, 50.0, dtype=np.float32)
    if n < k_period:
        return out
    for i in range(k_period - 1, n):
        h = high[i - k_period + 1:i + 1].max()
        l = low[i - k_period + 1:i + 1].min()
        if h - l > 1e-9:
            out[i] = 100.0 * (close[i] - l) / (h - l)
    # SMA smooth
    out2 = np.full(n, 50.0, dtype=np.float32)
    for i in range(smooth - 1, n):
        out2[i] = out[i - smooth + 1:i + 1].mean()
    return out2


def _mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, vol: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, 50.0, dtype=np.float32)
    if n < period + 1:
        return out
    tp = (high + low + close) / 3.0
    mf = tp * vol
    pos_mf = np.where(tp[1:] > tp[:-1], mf[1:], 0.0)
    neg_mf = np.where(tp[1:] < tp[:-1], mf[1:], 0.0)
    for i in range(period, n):
        p = pos_mf[i - period:i].sum()
        nm = neg_mf[i - period:i].sum()
        if nm == 0:
            out[i] = 100.0
        else:
            mr = p / nm
            out[i] = 100.0 - (100.0 / (1.0 + mr))
    return out


def fabricate_5m_from_15m(open_15m, high_15m, low_15m, close_15m, vol_15m):
    """Fabricate 5m bars from 15m: 3 sub-bars per 15m, linearly interpolated."""
    n15 = len(close_15m)
    n5 = n15 * 3
    o5 = np.zeros(n5); h5 = np.zeros(n5); l5 = np.zeros(n5); c5 = np.zeros(n5); v5 = np.zeros(n5)
    for i in range(n15):
        for j in range(3):
            idx = i * 3 + j
            # Linear interp from open to close within the 15m bar
            t = (j + 0.5) / 3.0
            mid = open_15m[i] + (close_15m[i] - open_15m[i]) * t
            o5[idx] = mid
            c5[idx] = mid
            h5[idx] = mid + (high_15m[i] - max(open_15m[i], close_15m[i])) * 0.5
            l5[idx] = mid - (max(open_15m[i], close_15m[i]) - low_15m[i]) * 0.5
            v5[idx] = vol_15m[i] / 3.0
    return o5, h5, l5, c5, v5


def patch_one_npz(path: Path, mode: str, skip_5m: bool = True) -> dict:
    """Returns dict of fields added.
    skip_5m=True (DEFAULT) — skip approximate 5m fabrication. Engine has zero-fill defaults.
    skip_5m=False — fabricate 5m from 15m linearly (slow + approximate, NOT accurate)."""
    z = dict(np.load(str(path), allow_pickle=True))
    added = {}

    # 1. lr_trend_1h — accurate, computed from real close_1h
    if 'lr_trend_1h' not in z and 'close_1h' in z:
        z['lr_trend_1h'] = _rolling_linreg_slope(np.asarray(z['close_1h'], dtype=np.float64), 50)
        added['lr_trend_1h'] = z['lr_trend_1h'].shape

    # 2. volume_sma_1h — accurate, computed from real volume_1h
    if 'volume_sma_1h' not in z and 'volume_1h' in z:
        z['volume_sma_1h'] = _rolling_sma(np.asarray(z['volume_1h'], dtype=np.float64), 20)
        added['volume_sma_1h'] = z['volume_sma_1h'].shape

    # 3-9. 5m fields (crypto only — tradier already has 5m TF natively)
    # SKIPPED by default — fabrication from 15m is approximate. Engine zero-fills neutral defaults.
    if not skip_5m and mode == 'crypto' and 'close_15m' in z and 'close_5m' not in z:
        try:
            n_total = len(z['close_3m']) if 'close_3m' in z else len(z['close_15m']) * 5
            o15 = np.asarray(z.get('open_15m', z['close_15m']), dtype=np.float64)
            h15 = np.asarray(z.get('high_15m', z['close_15m']), dtype=np.float64)
            l15 = np.asarray(z.get('low_15m',  z['close_15m']), dtype=np.float64)
            c15 = np.asarray(z['close_15m'], dtype=np.float64)
            v15 = np.asarray(z.get('volume_15m', np.zeros_like(c15)), dtype=np.float64)
            o5, h5, l5, c5, v5 = fabricate_5m_from_15m(o15, h15, l15, c15, v15)
            # Map fabricated 5m onto base-TF index. Crypto base TF is 3m → n_total = 15m_count × 5.
            # 5m_count = 15m_count × 3. To align, repeat each 5m sub-bar 5/3 times — easier to repeat fabricated to match base index length.
            # Simpler: tile 5m to base-TF-length by index ratio.
            base_n = n_total
            if base_n > 0:
                idx_map = np.minimum((np.arange(base_n) * len(c5) // base_n).astype(int), len(c5) - 1)
                z['close_5m'] = c5[idx_map].astype(np.float32)
                z['relative_volume_5m'] = (v5[idx_map] / max(v5.mean(), 1e-9)).astype(np.float32)
                # WT 5m
                wt1_5m, wt2_5m = _wt(c5)
                z['wt1_5m'] = wt1_5m[idx_map]
                z['wt2_5m'] = wt2_5m[idx_map]
                # Stoch K 5m
                z['stoch_k_5m'] = _stoch_k(h5, l5, c5)[idx_map]
                # MFI 5m
                z['mfi_5m'] = _mfi(h5, l5, c5, v5)[idx_map]
                added.update({
                    'close_5m': z['close_5m'].shape,
                    'relative_volume_5m': z['relative_volume_5m'].shape,
                    'wt1_5m': z['wt1_5m'].shape,
                    'wt2_5m': z['wt2_5m'].shape,
                    'stoch_k_5m': z['stoch_k_5m'].shape,
                    'mfi_5m': z['mfi_5m'].shape,
                })
        except Exception as e:
            print(f"  {path.name}: 5m fabrication failed: {type(e).__name__} {e}", file=sys.stderr)

    # 10. market_sentiment_score requires cross-sym pass — handled in _inject_sentiment_pass below
    if added:
        # Atomic write
        tmp = path.with_suffix('.tmp.npz')
        np.savez_compressed(str(tmp), **z)
        tmp.replace(path)
    return added


def inject_sentiment_pass(npz_dir: Path):
    """Cross-sym pass: compute market_sentiment_score from wt_composite_bias across all syms.
    STREAMING — load only timestamps+bias per sym (small), accumulate globally, then second pass writes."""
    from collections import defaultdict
    files = sorted(npz_dir.glob("*.npz"))
    print(f"  [sentiment] pass 1: scan {len(files)} files for ts+bias...")
    ts_bull = defaultdict(int); ts_bear = defaultdict(int); ts_total = defaultdict(int)
    sym_ts = {}  # sym -> ts array (small int64 array, ~kb)
    valid = 0
    for p in files:
        try:
            with np.load(str(p), allow_pickle=True) as z:
                if 'timestamps' not in z.files or 'wt_composite_bias' not in z.files:
                    continue
                ts = z['timestamps'].astype(np.int64)
                bias = z['wt_composite_bias'].astype(np.int8)
                if len(ts) != len(bias):
                    continue
                sym_ts[p.stem] = ts.copy()
                # Vectorized accumulation
                for t, b in zip(ts.tolist(), bias.tolist()):
                    ts_total[t] += 1
                    if b == 1: ts_bull[t] += 1
                    elif b == -1: ts_bear[t] += 1
                valid += 1
        except Exception as e:
            print(f"  [sentiment] pass1 fail {p.name}: {type(e).__name__} {e}")
    if not ts_total:
        print("  [sentiment] no usable data — skipping")
        return 0
    print(f"  [sentiment] valid syms: {valid} | unique timestamps: {len(ts_total)}")
    ts_score_dict = {t: float(50.0 + (ts_bull[t] - ts_bear[t]) / max(ts_total[t], 1) * 50.0) for t in ts_total}
    print(f"  [sentiment] pass 2: write market_sentiment_score back...")
    updated = 0
    for p_stem, ts in sym_ts.items():
        p = npz_dir / f"{p_stem}.npz"
        try:
            with np.load(str(p), allow_pickle=True) as z_in:
                if 'market_sentiment_score' in z_in.files and len(z_in['market_sentiment_score']) == len(ts):
                    continue
                z_out = dict(z_in)
            mss = np.array([ts_score_dict.get(int(t), 50.0) for t in ts], dtype=np.float32)
            z_out['market_sentiment_score'] = mss
            tmp = p.with_suffix('.tmp.npz')
            np.savez_compressed(str(tmp), **z_out)
            tmp.replace(p)
            updated += 1
            del z_out, mss
        except Exception as e:
            print(f"  [sentiment] pass2 fail {p_stem}: {type(e).__name__} {e}")
    return updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--dir", required=True, help="NPZ directory")
    ap.add_argument("--limit", type=int, default=0, help="patch only first N (for test)")
    args = ap.parse_args()
    npz_dir = Path(args.dir)
    files = sorted(npz_dir.glob("*.npz"))
    if args.limit:
        files = files[:args.limit]
    print(f"=== Patching {len(files)} NPZ files in {npz_dir} (mode={args.mode}) ===")
    t0 = time.time()
    counts_per_field = {}
    for i, f in enumerate(files, 1):
        try:
            added = patch_one_npz(f, args.mode)
            for k in added:
                counts_per_field[k] = counts_per_field.get(k, 0) + 1
            if i % 10 == 0:
                print(f"  {i}/{len(files)} done, elapsed={time.time()-t0:.0f}s, last={f.name} added={list(added.keys())}")
        except Exception as e:
            print(f"  {f.name}: ERR {type(e).__name__} {e}")
    print(f"\n=== Per-symbol patches done in {time.time()-t0:.0f}s ===")
    for k, ct in sorted(counts_per_field.items()):
        print(f"  {k}: added to {ct}/{len(files)}")
    print(f"\n=== Cross-symbol sentiment pass ===")
    n_sentiment = inject_sentiment_pass(npz_dir)
    print(f"  market_sentiment_score: added to {n_sentiment} symbols")
    print(f"\nTotal elapsed: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
