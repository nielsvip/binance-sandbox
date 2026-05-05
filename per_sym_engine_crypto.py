#!/usr/bin/env python3
"""per_sym_engine_crypto — vectorized per-symbol crypto backtest engine.

Reads 3m base OHLC from NPZ, resamples to {15m,1h,4h,D,W} in numpy, computes BB/WT/DC
at any (length, std/chan/avg/period) per TF, runs vectorized multi-TF entry/exit with
≥MIN_TFS_AGREE_FLOOR (=2) hard floor, deducts commission per side, returns trade list +
canonical metrics. Crypto-only — keep stocks completely separate per user 2026-05-05.

Source: backtest_v8/indicators/{SYM}.npz (3m granularity, ~6.3yr coverage).
NOT a full mirror of ez_manage live logic — multi-TF DC-break + WT-cross + BB-position
core. Additional v8 paths (RZ_EXIT, SATOSHIT, hedge, reentry, augment) ported in phases.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import lfilter

ROOT = Path(__file__).resolve().parent

# Crypto commission: 0.02% Binance USDC maker + 0.02% slippage = 0.04% per side
CRYPTO_COMMISSION_PER_SIDE = 0.0004
COMMISSION_RT_PCT = 100.0 * 2.0 * CRYPTO_COMMISSION_PER_SIDE  # 0.08% RT in % units

# Hard floor: never trade on a single TF — multi-TF agreement required (user 2026-05-05).
MIN_TFS_AGREE_FLOOR = 2

# 3m bars per HTF bar
TF_BARS_3M = {'15m': 5, '1h': 20, '4h': 80, 'D': 480, 'W': 480 * 7, 'M': 480 * 30}
DECISION_TFS = ('15m', '1h', '4h')


@dataclass
class SymParams:
    BB_LEN_15m: int = 20
    BB_STD_15m: float = 2.0
    BB_LEN_1h: int = 20
    BB_STD_1h: float = 2.0
    BB_LEN_4h: int = 20
    BB_STD_4h: float = 2.0
    WT_CHAN_15m: int = 10
    WT_AVG_15m: int = 21
    WT_CHAN_1h: int = 10
    WT_AVG_1h: int = 21
    WT_CHAN_4h: int = 10
    WT_AVG_4h: int = 21
    DC_PERIOD_15m: int = 20
    DC_PERIOD_1h: int = 20
    DC_PERIOD_4h: int = 20
    MIN_TFS_AGREE: int = 2
    MIN_HOLD_BARS_15m: int = 5
    COOLDOWN_BARS_15m: int = 3
    USE_BB_FILTER: bool = True
    USE_WT_CROSS: bool = True
    USE_DC_BREAK: bool = True
    BB_TOP_THRESHOLD: float = 0.95
    BB_BOT_THRESHOLD: float = 0.05
    BB_LONG_ENTRY_MAX: float = 0.50  # LONG: only enter when bb_pctb<this (pullback bias)
    BB_SHORT_ENTRY_MIN: float = 0.50  # SHORT: only enter when bb_pctb>this
    REQUIRE_D_TREND: bool = False  # daily trend gate (HTF context filter)
    REQUIRE_W_TREND: bool = False  # weekly trend gate
    # Entry mode: 'dc_break' (close > prev dc_high) | 'pullback' (bb_pctb<EMA crossing) | 'or' (either)
    ENTRY_MODE: str = 'or'  # 'or' is loosest; profiler may set tighter

    def to_dict(self) -> Dict:
        return asdict(self)

    def copy(self) -> 'SymParams':
        return SymParams(**self.to_dict())


# ───────────────────────── vectorized indicator primitives ─────────────────

def _ema(x: np.ndarray, span: int) -> np.ndarray:
    if span <= 1:
        return x.astype(np.float64)
    a = 2.0 / (span + 1.0)
    return lfilter([a], [1.0, -(1.0 - a)], x.astype(np.float64))


def _rolling_mean_csum(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    csum = np.concatenate([[0.0], np.cumsum(x.astype(np.float64))])
    out[w - 1:] = (csum[w:] - csum[:-w]) / w
    return out


def _rolling_std_csum(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    xf = x.astype(np.float64)
    csum = np.concatenate([[0.0], np.cumsum(xf)])
    csum2 = np.concatenate([[0.0], np.cumsum(xf * xf)])
    mean = (csum[w:] - csum[:-w]) / w
    var = (csum2[w:] - csum2[:-w]) / w - mean * mean
    out[w - 1:] = np.sqrt(np.maximum(var, 0.0))
    return out


def _rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[w - 1:] = sliding_window_view(x.astype(np.float64), w).max(axis=1)
    return out


def _rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[w - 1:] = sliding_window_view(x.astype(np.float64), w).min(axis=1)
    return out


def compute_bb(close: np.ndarray, length: int, std: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = _rolling_mean_csum(close, length)
    s = _rolling_std_csum(close, length)
    upper = m + std * s
    lower = m - std * s
    rng = upper - lower
    rng_safe = np.where(rng > 1e-12, rng, 1e-12)
    pct_b = np.clip((close - lower) / rng_safe, -2.0, 3.0)
    return upper, lower, pct_b


def compute_wt(high: np.ndarray, low: np.ndarray, close: np.ndarray, chan: int, avg: int) -> Tuple[np.ndarray, np.ndarray]:
    typical = (high + low + close) / 3.0
    esa = _ema(typical, chan)
    d = _ema(np.abs(typical - esa), chan)
    ci = (typical - esa) / (0.015 * np.maximum(d, 1e-10))
    wt1 = _ema(ci, avg)
    # WT2 = 3-bar SMA of WT1
    wt2 = _rolling_mean_csum(wt1, 3)
    wt2[:2] = wt1[:2]  # backfill startup
    return wt1, wt2


def compute_dc(high: np.ndarray, low: np.ndarray, period: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dc_high = _rolling_max(high, period)
    dc_low = _rolling_min(low, period)
    dc_high_prev = np.empty_like(dc_high)
    dc_high_prev[0] = np.nan
    dc_high_prev[1:] = dc_high[:-1]
    dc_low_prev = np.empty_like(dc_low)
    dc_low_prev[0] = np.nan
    dc_low_prev[1:] = dc_low[:-1]
    return dc_high, dc_low, dc_high_prev, dc_low_prev


# ───────────────────────── data loader / resampler ─────────────────────────

NPZ_DIR = ROOT / 'backtest_v8' / 'indicators'

_npz_cache: Dict[str, Dict[str, np.ndarray]] = {}


def load_3m_base(sym: str, years_back: float = 4.0) -> Optional[Dict[str, np.ndarray]]:
    """Load 3m OHLC + ts from NPZ, sliced to last N years. Cached per process.
    Returns dict {'open','high','low','close','ts'} or None if missing fields.
    """
    cache_key = f'{sym}__y{years_back:.2f}'
    if cache_key in _npz_cache:
        return _npz_cache[cache_key]
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    z = np.load(str(p))
    needed = ['open_3m', 'high_3m', 'low_3m', 'close_3m', 'timestamps']
    if not all(k in z.files for k in needed):
        z.close()
        return None
    o = z['open_3m'][:].astype(np.float64)
    h = z['high_3m'][:].astype(np.float64)
    l = z['low_3m'][:].astype(np.float64)
    c = z['close_3m'][:].astype(np.float64)
    ts = z['timestamps'][:].astype(np.int64)
    z.close()
    cutoff = ts[-1] - int(years_back * 365.25 * 86400)
    si = int(np.searchsorted(ts, cutoff))
    out = {'open': o[si:], 'high': h[si:], 'low': l[si:], 'close': c[si:], 'ts': ts[si:]}
    _npz_cache[cache_key] = out
    return out


def resample_3m_to_htf(base: Dict[str, np.ndarray], tf_bars: int) -> Dict[str, np.ndarray]:
    """Resample 3m OHLC arrays to HTF (tf_bars=3m bars per HTF bar). Pure numpy."""
    n = len(base['close'])
    n_hf = n // tf_bars
    if n_hf == 0:
        return {'open': np.array([]), 'high': np.array([]), 'low': np.array([]), 'close': np.array([]), 'ts': np.array([], dtype=np.int64)}
    cut = n_hf * tf_bars
    o = base['open'][:cut][::tf_bars]
    h = base['high'][:cut].reshape(n_hf, tf_bars).max(axis=1)
    l = base['low'][:cut].reshape(n_hf, tf_bars).min(axis=1)
    c = base['close'][:cut][tf_bars - 1::tf_bars][:n_hf]
    ts = base['ts'][:cut][tf_bars - 1::tf_bars][:n_hf]
    return {'open': o, 'high': h, 'low': l, 'close': c, 'ts': ts}


def build_tf_data(base: Dict[str, np.ndarray]) -> Dict[str, Dict[str, np.ndarray]]:
    """Build {tf: ohlc-dict} for 15m, 1h, 4h, D, W. M skipped — too few samples on 4yr."""
    out: Dict[str, Dict[str, np.ndarray]] = {}
    for tf, ratio in TF_BARS_3M.items():
        if tf == 'M':
            continue
        d = resample_3m_to_htf(base, ratio)
        if len(d['close']) > 0:
            out[tf] = d
    return out


# ───────────────────────── per-TF directional signal ───────────────────────

def per_tf_signals(ohlc: Dict[str, np.ndarray], side: str, tf: str, params: SymParams) -> Tuple[np.ndarray, np.ndarray]:
    """Return (entry_sig, exit_sig) boolean arrays for this TF and side."""
    h, l, c = ohlc['high'], ohlc['low'], ohlc['close']
    n = len(c)
    bb_len = int(getattr(params, f'BB_LEN_{tf}'))
    bb_std = float(getattr(params, f'BB_STD_{tf}'))
    wt_chan = int(getattr(params, f'WT_CHAN_{tf}'))
    wt_avg = int(getattr(params, f'WT_AVG_{tf}'))
    dc_per = int(getattr(params, f'DC_PERIOD_{tf}'))

    bb_u, bb_l, bb_pctb = compute_bb(c, bb_len, bb_std)
    wt1, wt2 = compute_wt(h, l, c, wt_chan, wt_avg)
    dc_h, dc_l, dc_h_prev, dc_l_prev = compute_dc(h, l, dc_per)

    if side == 'LONG':
        wt_ok = (wt1 > wt2) if params.USE_WT_CROSS else np.ones(n, dtype=bool)
        # Entry signals: dc_break = close above prev DC high (breakout). pullback = bb_pctb < BB_LONG_ENTRY_MAX (buying dips).
        dc_break = (c > dc_h_prev)
        pullback = (bb_pctb < params.BB_LONG_ENTRY_MAX)
        if params.ENTRY_MODE == 'dc_break':
            base_entry = dc_break
        elif params.ENTRY_MODE == 'pullback':
            base_entry = pullback
        else:  # 'or'
            base_entry = dc_break | pullback
        # Hard ceiling: never buy at the top
        ceiling_ok = (bb_pctb < params.BB_TOP_THRESHOLD) if params.USE_BB_FILTER else np.ones(n, dtype=bool)
        entry = wt_ok & base_entry & ceiling_ok
        # Exit on WT cross down OR price at top OR price breaks down through dc_low_prev
        exit_ = (wt1 < wt2) | (bb_pctb > params.BB_TOP_THRESHOLD) | (c < dc_l_prev)
    else:  # SHORT
        wt_ok = (wt1 < wt2) if params.USE_WT_CROSS else np.ones(n, dtype=bool)
        dc_break = (c < dc_l_prev)
        pullback = (bb_pctb > params.BB_SHORT_ENTRY_MIN)
        if params.ENTRY_MODE == 'dc_break':
            base_entry = dc_break
        elif params.ENTRY_MODE == 'pullback':
            base_entry = pullback
        else:
            base_entry = dc_break | pullback
        ceiling_ok = (bb_pctb > params.BB_BOT_THRESHOLD) if params.USE_BB_FILTER else np.ones(n, dtype=bool)
        entry = wt_ok & base_entry & ceiling_ok
        exit_ = (wt1 > wt2) | (bb_pctb < params.BB_BOT_THRESHOLD) | (c > dc_h_prev)
    # NaN safety: treat NaN-context bars as no-signal
    valid = ~(np.isnan(bb_pctb) | np.isnan(wt1) | np.isnan(dc_h_prev))
    return entry & valid, exit_ & valid


def htf_trend_pass(ohlc_d: Dict[str, np.ndarray], ohlc_w: Optional[Dict[str, np.ndarray]],
                   side: str, params: SymParams, n_15m: int) -> np.ndarray:
    """Build a 15m-grid boolean array: True when D (and optionally W) trend confirms side.
    D trend = close > rolling_mean(close, 20). W trend = same on weekly.
    Returns array of length n_15m.
    """
    out = np.ones(n_15m, dtype=bool)
    if not (params.REQUIRE_D_TREND or params.REQUIRE_W_TREND):
        return out

    def _trend_arr(d: Dict[str, np.ndarray], side: str, ratio_to_15m: int) -> np.ndarray:
        c = d['close']
        m = _rolling_mean_csum(c, 20)
        if side == 'LONG':
            up = c > m
        else:
            up = c < m
        # repeat to 15m grid
        rep = np.repeat(up, ratio_to_15m)
        if len(rep) < n_15m:
            rep = np.concatenate([rep, np.zeros(n_15m - len(rep), dtype=bool)])
        return rep[:n_15m]

    if params.REQUIRE_D_TREND:
        out &= _trend_arr(ohlc_d, side, ratio_to_15m=TF_BARS_3M['D'] // TF_BARS_3M['15m'])  # 96
    if params.REQUIRE_W_TREND and ohlc_w is not None and len(ohlc_w['close']) >= 10:
        out &= _trend_arr(ohlc_w, side, ratio_to_15m=TF_BARS_3M['W'] // TF_BARS_3M['15m'])  # 672
    return out


# ───────────────────────── trade walker (vectorized state) ────────────────

def walk_trades(enter_15m: np.ndarray, leave_15m: np.ndarray, c15: np.ndarray, ts15: np.ndarray,
                side: str, min_hold: int, cooldown: int) -> List[Dict]:
    """Walk the 15m grid with one position at a time. Returns trade list.
    O(num_trades) using np.flatnonzero scans.
    """
    trades: List[Dict] = []
    n = len(c15)
    i = 0
    rt_comm = COMMISSION_RT_PCT
    while i < n:
        e_remaining = enter_15m[i:]
        e_idxs = np.flatnonzero(e_remaining)
        if not len(e_idxs):
            break
        entry_i = i + int(e_idxs[0])
        x_start = entry_i + min_hold
        if x_start >= n:
            break
        x_remaining = leave_15m[x_start:]
        x_idxs = np.flatnonzero(x_remaining)
        if len(x_idxs):
            exit_i = x_start + int(x_idxs[0])
        else:
            exit_i = n - 1
        ep = float(c15[entry_i])
        xp = float(c15[exit_i])
        if ep <= 0 or xp <= 0:
            i = exit_i + cooldown + 1
            continue
        if side == 'LONG':
            pnl_gross = (xp - ep) / ep * 100.0
        else:
            pnl_gross = (ep - xp) / ep * 100.0
        pnl_net = pnl_gross - rt_comm
        trades.append({
            'side': side,
            'entry_idx': entry_i, 'exit_idx': exit_i,
            'entry_ts': int(ts15[entry_i]), 'exit_ts': int(ts15[exit_i]),
            'entry_price': ep, 'exit_price': xp,
            'pnl_gross_pct': pnl_gross, 'pnl_pct': pnl_net,
            'bars_held': exit_i - entry_i,
        })
        i = exit_i + cooldown + 1
    return trades


# ───────────────────────── top-level simulate ──────────────────────────────

def simulate(sym: str, side: str, params: SymParams, years_back: float = 4.0) -> Optional[Dict]:
    """Returns dict with per-(sym,side) trades + canonical metrics, or None on data miss."""
    if side not in ('LONG', 'SHORT'):
        raise ValueError(f"side must be LONG or SHORT, got {side}")
    base = load_3m_base(sym, years_back=years_back)
    if base is None or len(base['close']) < 5000:
        return None
    tf_data = build_tf_data(base)
    needed = list(DECISION_TFS) + ['D']
    for tf in needed:
        if tf not in tf_data or len(tf_data[tf]['close']) < 50:
            return None

    n_15m = len(tf_data['15m']['close'])
    e_count = np.zeros(n_15m, dtype=np.int16)
    x_count = np.zeros(n_15m, dtype=np.int16)
    for tf in DECISION_TFS:
        e_sig, x_sig = per_tf_signals(tf_data[tf], side, tf, params)
        ratio = TF_BARS_3M[tf] // TF_BARS_3M['15m']
        if ratio == 1:
            e15 = e_sig
            x15 = x_sig
        else:
            e15 = np.repeat(e_sig, ratio)
            x15 = np.repeat(x_sig, ratio)
            if len(e15) < n_15m:
                e15 = np.concatenate([e15, np.zeros(n_15m - len(e15), dtype=bool)])
                x15 = np.concatenate([x15, np.zeros(n_15m - len(x15), dtype=bool)])
            else:
                e15 = e15[:n_15m]
                x15 = x15[:n_15m]
        e_count += e15.astype(np.int16)
        x_count += x15.astype(np.int16)

    min_tfs = max(MIN_TFS_AGREE_FLOOR, int(params.MIN_TFS_AGREE))
    enter = e_count >= min_tfs
    # Per user 2026-05-05: "no decision based on single TF" — exits also require ≥MIN_TFS_AGREE_FLOOR.
    # This holds positions longer than single-TF-flip exit (the live-system root cause of 0.0001%-0.01% closes).
    leave = x_count >= MIN_TFS_AGREE_FLOOR

    htf_pass = htf_trend_pass(tf_data['D'], tf_data.get('W'), side, params, n_15m)
    enter = enter & htf_pass

    c15 = tf_data['15m']['close']
    ts15 = tf_data['15m']['ts']
    trades = walk_trades(enter, leave, c15, ts15, side,
                         int(params.MIN_HOLD_BARS_15m), int(params.COOLDOWN_BARS_15m))

    if not trades:
        return {'sym': sym, 'side': side, 'trades': 0, 'trades_per_day': 0.0,
                'pool_sharpe': 0.0, 'sym_sharpe': 0.0, 'wr_pct': 0.0,
                'max_dd_pct': 0.0, 'total_gain_pct': 0.0, 'avg_gain_trade': 0.0,
                'gain_per_yr': 0.0, 'gain_sym_yr': 0.0, 'years': (ts15[-1] - ts15[0]) / 86400 / 365.25,
                'n_syms': 1, 'tag': f'per_sym_{sym}_{side}',
                'trade_list': [], 'params': params.to_dict()}

    rets = np.array([t['pnl_pct'] for t in trades], dtype=np.float64)
    n = len(rets)
    span_days = max(1.0, (ts15[-1] - ts15[0]) / 86400.0)
    yrs = max(0.01, span_days / 365.25)
    sd = float(rets.std())
    pool = float(rets.mean() / sd) if sd > 1e-12 else 0.0
    wr = float((rets > 0).mean() * 100.0)
    eq = np.cumsum(rets)
    peak = np.maximum.accumulate(eq)
    dd = float((peak - eq).max())
    total = float(rets.sum())

    return {
        'sym': sym, 'side': side, 'trades': n, 'trades_per_day': n / span_days,
        'pool_sharpe': pool, 'sym_sharpe': max(-5.0, min(5.0, pool)),
        'wr_pct': wr, 'max_dd_pct': dd, 'total_gain_pct': total,
        'avg_gain_trade': total / n, 'gain_per_yr': total / yrs, 'gain_sym_yr': total / yrs,
        'years': yrs, 'n_syms': 1,
        'tag': f'per_sym_{sym}_{side}',
        'trade_list': trades, 'params': params.to_dict(),
    }


# ───────────────────────── CLI smoke test ──────────────────────────────────

def _smoke(syms: List[str]) -> None:
    print(f"smoke test on {syms}")
    for s in syms:
        for side in ('LONG', 'SHORT'):
            r = simulate(s, side, SymParams(), years_back=4.0)
            if r is None:
                print(f"  {s} {side}: NPZ MISS or insufficient data")
                continue
            print(f"  {s:12s} {side:5s} pool={r['pool_sharpe']:+.4f} "
                  f"wr={r['wr_pct']:5.1f}% trades={r['trades']:>5d} "
                  f"tpd={r['trades_per_day']:5.2f} dd={r['max_dd_pct']:5.2f}% "
                  f"yrs={r['years']:.2f}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='BTCUSDC,ETHUSDC,SOLUSDC')
    args = ap.parse_args()
    _smoke([s.strip() for s in args.syms.split(',') if s.strip()])
