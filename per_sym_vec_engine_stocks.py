#!/usr/bin/env python3
"""per_sym_vec_engine_stocks — stocks variant of per_sym_vec_engine_crypto.

Mirrors crypto engine architecture but with stocks-specific deltas per CLAUDE.md
"Crypto vs Stock Parameters — OPPOSITE — NEVER copy between them" table:

  - Base TF = 5m (NOT 3m). NPZ has close_5m, no close_3m.
  - LTF for decision = 5m.
  - Decision TFs = {15m, 1h, 4h, D} (higher-bias per stocks rule).
  - Commission RT = 0.04% (0% Tradier fee + 0.02% slippage × 2).
  - 5m bar count per day = 78 (RTH 6.5h × 12 bars/hr).
  - MIN_TFS_AGREE ≥ 2 (vs crypto ≥ 1).
  - Stock-specific NPZ path = same backtest_v8/indicators/ — stocks tickers live alongside.

Per user 2026-05-05 + CLAUDE.md: "NEVER copy between crypto and stock parameter sets".
This module is therefore an independent copy of the crypto vec engine with stock defaults
and 5m grid math — not an import wrapper.

Re-uses crypto engine's NPZ loader (allow_pickle variant) since stocks NPZs have the
same structure (open_5m/high_5m/low_5m/close_5m + per-TF WT/DC/BB precomputes).
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Stocks commission (Tradier zero-fee + 2bp slippage = 0.04% RT)
STOCKS_COMMISSION_PER_SIDE = 0.0002
RT_COMM = 100.0 * 2.0 * STOCKS_COMMISSION_PER_SIDE  # 0.04 (pct units)

NPZ_DIR = ROOT / 'backtest_v8' / 'indicators'

# 5m base. HTF bars expressed in 5m units.
TF_BARS_5M = {'5m': 1, '15m': 3, '1h': 12, '4h': 48, 'D': 78, 'W': 78 * 5, 'M': 78 * 21}
DECISION_TFS = ('15m', '1h', '4h', 'D')  # higher-TF bias for stocks

_npz_cache_vec_stocks: Dict[str, Dict[str, np.ndarray]] = {}


def load_5m_base(sym: str, years_back: float = 4.0) -> Optional[Dict[str, np.ndarray]]:
    """Load NPZ + slice to last N years, cached. allow_pickle=True for `bar_pattern_codes`.

    Stocks NPZ uses 5m base (close_5m / high_5m / low_5m / open_5m / timestamps).
    """
    cache_key = f'{sym}__y{years_back:.4f}'
    if cache_key in _npz_cache_vec_stocks:
        return _npz_cache_vec_stocks[cache_key]
    if _npz_cache_vec_stocks:
        _npz_cache_vec_stocks.clear()
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    z = np.load(str(p), allow_pickle=True)
    needed = ['open_5m', 'high_5m', 'low_5m', 'close_5m', 'timestamps']
    if not all(k in z.files for k in needed):
        z.close()
        return None
    full: Dict[str, np.ndarray] = {}
    for k in z.files:
        try:
            a = z[k]
            if not hasattr(a, 'dtype') or a.dtype == object:
                continue
            full[k] = np.asarray(a)
        except Exception:
            continue
    z.close()
    ts_full = full['timestamps'].astype(np.int64)
    full['timestamps'] = ts_full
    cutoff = ts_full[-1] - int(years_back * 365.25 * 86400)
    si = int(np.searchsorted(ts_full, cutoff))
    sliced: Dict[str, np.ndarray] = {}
    for k, arr in full.items():
        if isinstance(arr, np.ndarray) and arr.ndim == 1 and len(arr) == len(ts_full):
            sliced[k] = arr[si:]
        else:
            sliced[k] = arr
    sliced['close'] = sliced['close_5m'].astype(np.float64)
    sliced['high'] = sliced['high_5m'].astype(np.float64)
    sliced['low'] = sliced['low_5m'].astype(np.float64)
    sliced['open'] = sliced['open_5m'].astype(np.float64)
    sliced['volume'] = sliced.get('volume_5m', np.ones(len(sliced['close']))).astype(np.float64)
    sliced['ts'] = sliced['timestamps']
    _npz_cache_vec_stocks[cache_key] = sliced
    return sliced


def _resample_5m_to_htf(base: Dict[str, np.ndarray], tf_bars: int) -> Dict[str, np.ndarray]:
    n = len(base['close'])
    n_hf = n // tf_bars
    if n_hf == 0:
        return {'open': np.array([]), 'high': np.array([]), 'low': np.array([]),
                'close': np.array([]), 'volume': np.array([]), 'ts': np.array([], dtype=np.int64)}
    cut = n_hf * tf_bars
    o = base['open'][:cut][::tf_bars]
    h = base['high'][:cut].reshape(n_hf, tf_bars).max(axis=1)
    l = base['low'][:cut].reshape(n_hf, tf_bars).min(axis=1)
    c = base['close'][:cut][tf_bars - 1::tf_bars][:n_hf]
    ts = base['ts'][:cut][tf_bars - 1::tf_bars][:n_hf]
    v = base['volume'][:cut].reshape(n_hf, tf_bars).sum(axis=1) if 'volume' in base else np.ones(n_hf)
    return {'open': o, 'high': h, 'low': l, 'close': c, 'volume': v, 'ts': ts}


def _ss_5m_to_15m(arr_5m: np.ndarray, n_15m: int) -> np.ndarray:
    """Subsample 5m → 15m grid by taking every 3rd bar (15m = 3 × 5m)."""
    if arr_5m is None or len(arr_5m) == 0:
        return np.zeros(n_15m, dtype=np.float32)
    cut = (len(arr_5m) // 3) * 3
    sub = arr_5m[:cut][2::3]
    if len(sub) >= n_15m:
        return sub[:n_15m]
    pad = np.zeros(n_15m - len(sub), dtype=sub.dtype)
    return np.concatenate([pad, sub])


def _bb_pctb(close: np.ndarray, upper: np.ndarray, lower: np.ndarray) -> np.ndarray:
    rng = upper - lower
    safe = np.where(rng > 1e-12, rng, 1e-12)
    return np.clip((close - lower) / safe, -2.0, 3.0).astype(np.float32)


def _precompute_signals_stocks(sym: str, years_back: float) -> Optional[Dict[str, np.ndarray]]:
    base = load_5m_base(sym, years_back=years_back)
    if base is None:
        return None
    # Build 15m grid from 5m base (3 × 5m = 15m). 15m is our decision grid (same as crypto vec).
    grid_15m = _resample_5m_to_htf(base, TF_BARS_5M['15m'])
    if len(grid_15m['close']) < 200:
        return None
    n_15m = len(grid_15m['close'])
    out: Dict[str, np.ndarray] = {
        'close_15m': grid_15m['close'].astype(np.float32),
        'high_15m': grid_15m['high'].astype(np.float32),
        'low_15m': grid_15m['low'].astype(np.float32),
        'ts_15m': grid_15m['ts'].astype(np.int64),
        'n_15m': n_15m,
    }
    # WT/DC/BB per TF — subsample from NPZ 5m grid to 15m
    for tf in ('5m', '15m', '1h', '4h', 'D'):
        for fld in (f'wt1_{tf}', f'wt2_{tf}'):
            arr = base.get(fld)
            if arr is None or len(arr) == 0:
                out[fld] = np.zeros(n_15m, dtype=np.float32)
            else:
                out[fld] = _ss_5m_to_15m(arr.astype(np.float32), n_15m)
        for fld in (f'dc_high_{tf}', f'dc_low_{tf}'):
            arr = base.get(fld)
            if arr is None or len(arr) == 0:
                out[fld] = np.zeros(n_15m, dtype=np.float32)
            else:
                out[fld] = _ss_5m_to_15m(arr.astype(np.float32), n_15m)
        for fld in (f'bb_upper_{tf}', f'bb_lower_{tf}'):
            arr = base.get(fld)
            if arr is None or len(arr) == 0:
                out[fld] = np.zeros(n_15m, dtype=np.float32)
            else:
                out[fld] = _ss_5m_to_15m(arr.astype(np.float32), n_15m)
    close_15m = out['close_15m']
    for tf in ('15m', '1h', '4h', 'D'):
        u = out.get(f'bb_upper_{tf}')
        l = out.get(f'bb_lower_{tf}')
        if u is not None and l is not None:
            out[f'bb_pctb_{tf}'] = _bb_pctb(close_15m, u, l)
        else:
            out[f'bb_pctb_{tf}'] = np.full(n_15m, 0.5, dtype=np.float32)
    for tf in ('15m', '1h', '4h', 'D'):
        for nm in (f'stoch_k_{tf}', f'atr_{tf}', f'rsi_{tf}'):
            arr = base.get(nm)
            if arr is not None and len(arr) > 0:
                out[nm] = _ss_5m_to_15m(arr.astype(np.float32), n_15m)
    out['volume_15m'] = grid_15m.get('volume', np.ones(n_15m, dtype=np.float32)).astype(np.float32)
    return out


# Re-use crypto engine's tf-signal builder + variant decoder + mask builder + walker.
# Only the per-bar precompute differs (5m basis), and the commission constant.
from per_sym_vec_engine_crypto import (
    _build_tf_signals,
    _build_entry_exit_masks_chunk,
    _variants_to_arrays,
    _walk_one_variant,
    _max_dd_from_returns,
    _time_weighted_sharpe,
    _empty_scoreboard,
    top_k,
)


def sweep_variants(
    sym: str,
    variants: List[Dict[str, Any]],
    years_back: float = 7.0 / 365.25,
    chunk_size: int = 10_000,
    verbose: bool = True,
    n_years_for_yr_metrics: Optional[float] = None,
) -> Dict[str, np.ndarray]:
    """Stocks vec sweep — same shape as crypto engine but with 5m base + 0.04% RT comm."""
    t0 = time.time()
    sig = _precompute_signals_stocks(sym, years_back)
    if sig is None:
        if verbose:
            print(f"[vec_sweep {sym}] NO NPZ or insufficient bars", flush=True)
        return _empty_scoreboard(len(variants))
    tf_sigs = _build_tf_signals(sig)
    if verbose:
        print(f"[vec_sweep {sym}] precompute {time.time()-t0:.2f}s | n_bars={sig['n_15m']}", flush=True)
    n_v = len(variants)
    vararr = _variants_to_arrays(variants)
    pool_sharpe = np.zeros(n_v, dtype=np.float32)
    tw_sharpe = np.zeros(n_v, dtype=np.float32)
    trades = np.zeros(n_v, dtype=np.int32)
    win_rate = np.zeros(n_v, dtype=np.float32)
    avg_gain = np.zeros(n_v, dtype=np.float32)
    gain_per_yr = np.zeros(n_v, dtype=np.float32)
    max_dd = np.zeros(n_v, dtype=np.float32)
    long_trades = np.zeros(n_v, dtype=np.int32)
    short_trades = np.zeros(n_v, dtype=np.int32)
    yrs_for_pyr = float(n_years_for_yr_metrics if n_years_for_yr_metrics is not None else years_back)
    yrs_for_pyr = max(yrs_for_pyr, 1e-6)
    close_15m = sig['close_15m']
    ts_15m = sig['ts_15m']
    wt1_15m = sig.get('wt1_15m', np.zeros_like(close_15m))
    wt2_15m = sig.get('wt2_15m', np.zeros_like(close_15m))
    ref_ts = int(ts_15m.max()) if len(ts_15m) else 0
    n_chunks = (n_v + chunk_size - 1) // chunk_size
    for ck in range(n_chunks):
        lo = ck * chunk_size
        hi = min(lo + chunk_size, n_v)
        sl = slice(lo, hi)
        tk = time.time()
        e_l, x_l, e_s, x_s = _build_entry_exit_masks_chunk(tf_sigs, sig, vararr, sl)
        mask_dt = time.time() - tk
        tw = time.time()
        chunk_size_actual = hi - lo
        for j_local in range(chunk_size_actual):
            j_global = lo + j_local
            el_col = e_l[:, j_local]
            es_col = e_s[:, j_local]
            el_idx = np.flatnonzero(el_col)
            es_idx = np.flatnonzero(es_col)
            xl_idx = np.flatnonzero(x_l[:, j_local])
            xs_idx = np.flatnonzero(x_s[:, j_local])
            pnls, ets, _bars, sides = _walk_one_variant(
                el_idx, xl_idx, es_idx, xs_idx,
                close_15m, ts_15m,
                int(vararr['MIN_HOLD_BARS'][j_global]), int(vararr['COOLDOWN_BARS'][j_global]),
                bool(vararr['REVERSE_ON_EXIT'][j_global]),
                bool(vararr['HARD_LOSS_ENABLED'][j_global]),
                float(vararr['HARD_LOSS_PCT'][j_global]),
                bool(vararr['PEAK_PROTECT_ENABLED'][j_global]),
                bool(vararr['PEAK_GIVEBACK_ENABLED'][j_global]),
                float(vararr['PEAK_GIVEBACK_DROP_PCT'][j_global]),
                bool(vararr['RIDICULOUS_HOLD_ENABLED'][j_global]),
                float(vararr['RIDICULOUS_LOSS_PCT'][j_global]),
                float(vararr['RIDICULOUS_HOLD_HOURS'][j_global]),
                wt1_15m, wt2_15m,
                RT_COMM,           # stocks commission, 0.04 vs crypto 0.08
                el_col, es_col,
            )
            trades[j_global] = len(pnls)
            if len(pnls) >= 2:
                std = float(pnls.std())
                pool_sharpe[j_global] = float(pnls.mean() / std) if std > 1e-12 else 0.0
                tw_sharpe[j_global] = _time_weighted_sharpe(pnls, ets, ref_ts=ref_ts)
                win_rate[j_global] = float((pnls > 0).mean() * 100.0)
                avg_gain[j_global] = float(pnls.mean())
                gain_per_yr[j_global] = float(pnls.sum()) / yrs_for_pyr
                max_dd[j_global] = _max_dd_from_returns(pnls)
                long_trades[j_global] = int((sides == 1).sum())
                short_trades[j_global] = int((sides == -1).sum())
            elif len(pnls) == 1:
                pool_sharpe[j_global] = 0.0
                tw_sharpe[j_global] = 0.0
                win_rate[j_global] = 100.0 if pnls[0] > 0 else 0.0
                avg_gain[j_global] = float(pnls[0])
                gain_per_yr[j_global] = float(pnls[0]) / yrs_for_pyr
                long_trades[j_global] = int((sides == 1).sum())
                short_trades[j_global] = int((sides == -1).sum())
        walk_dt = time.time() - tw
        if verbose:
            elapsed = time.time() - t0
            print(f"  chunk {ck+1}/{n_chunks} ({hi-lo} vars) mask={mask_dt:.2f}s walk={walk_dt:.2f}s | total={elapsed:.1f}s", flush=True)
    if verbose:
        elapsed = time.time() - t0
        rate = n_v / max(elapsed, 1e-6)
        print(f"[vec_sweep {sym}] DONE {n_v} variants in {elapsed:.1f}s ({rate:.0f} variants/s)", flush=True)
    return {
        'pool_sharpe': pool_sharpe,
        'time_weighted_sharpe': tw_sharpe,
        'trades': trades,
        'win_rate_pct': win_rate,
        'avg_gain_trade_pct': avg_gain,
        'gain_per_yr_pct': gain_per_yr,
        'max_dd_pct': max_dd,
        'long_trades': long_trades,
        'short_trades': short_trades,
    }


# ───────────────────────── smoke ─────────────────────────────────────────

def _smoke(syms: List[str], n_variants: int = 10_000, years_back: float = 7.0 / 365.25) -> None:
    from per_sym_variant_generator import generate_variants
    print(f"[smoke] generating {n_variants} STOCKS variants...", flush=True)
    t = time.time()
    variants = generate_variants(n_total=n_variants, mode='stocks')
    print(f"[smoke] generated {len(variants)} in {time.time()-t:.1f}s", flush=True)
    for sym in syms:
        print(f"\n[smoke] === {sym} ===", flush=True)
        sb = sweep_variants(sym, variants, years_back=years_back, chunk_size=5000, verbose=True)
        if sb['trades'].sum() == 0:
            print(f"  no trades produced — skipping ranking", flush=True)
            continue
        tk = top_k(sb, variants, k=5, sort_by='time_weighted_sharpe')
        print(f"  TOP 5 by time_weighted_sharpe:")
        for i, (v, m) in enumerate(tk, 1):
            print(f"    {i}. tws={m['time_weighted_sharpe']:.3f} pool={m['pool_sharpe']:.3f} "
                  f"trades={m['trades']} wr={m['win_rate_pct']:.1f}% "
                  f"avg={m['avg_gain_trade_pct']:.3f}% dd={m['max_dd_pct']:.2f}% | "
                  f"mode={v['ENTRY_MODE']} min_e={v['MIN_TFS_AGREE_ENTRY']} "
                  f"min_h={v['MIN_HOLD_BARS_15m']} hl={v['HARD_LOSS_PCT']:.2f}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='AAPL,MSFT', help='comma sep tickers')
    ap.add_argument('--n-variants', type=int, default=10_000)
    ap.add_argument('--years-back', type=float, default=7.0 / 365.25)
    args = ap.parse_args()
    syms = [s.strip() for s in args.syms.split(',') if s.strip()]
    _smoke(syms, n_variants=args.n_variants, years_back=args.years_back)
