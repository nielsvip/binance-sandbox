"""
scalp_v3_trendfollow_bt.py — V3 trend-follow scalp backtest with exit-toggle sweep.

Drives `check_scalp_v3_live_entry` / `check_scalp_v3_live_exit` (from
`scalp_v3_live.py`) over historical 3m bars (resampled from 15m klines per
CLAUDE.md "15m → 3m via × 5 split"), with stoch_k_3m / stoch_k_15m / stoch_k_1h
and wt1/wt2_3m precomputed using the live formulas in `ez_indicators.py`.

Sweep grid:
  - bar_on × wt_on × k_on (skip all-False) → 7 combos
  - max_hold ∈ {0, 5, 15, 60}
  - side_mode ∈ {BOTH, LONG_ONLY, SHORT_ONLY}
  → 84 combos × 12 syms = 1008 sym-runs (cached indicators reused per sym)

Per-trade returns. Sharpe = mean/std (no annualization). Open positions at
end-of-test marked-to-market. Reports STANDARD METRIC SET per CLAUDE.md.
"""
from __future__ import annotations
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd

sys.path.insert(0, '/Users/niels/Documents/binance')
from scalp_v3_live import check_scalp_v3_live_entry, check_scalp_v3_live_exit

KLINES_DIR = '/Users/niels/Documents/binance/klines_cache'
OUT_DIR = '/Users/niels/Documents/binance/data'

# 12 symbols: majors + diverse alts that have 6mo of 15m data.
SYMBOLS = [
    'ETHUSDT', 'ADAUSDT',
    'AAVEUSDT', '1000PEPEUSDT', '1000SHIBUSDT', '1000BONKUSDT',
    'AEROUSDT', 'AEVOUSDT', 'AKTUSDT', 'ALICEUSDT',
    'ACXUSDT', 'AERGOUSDT',
]

WT_TF_PARAMS = {
    "3m":  {"esa": 6, "chan": 10, "sig": 12, "smooth": 3, "esa_ma": "dema", "ci": 0.010},
    "15m": {"esa": 8, "chan": 25, "sig": 30, "smooth": 5, "esa_ma": "dema", "ci": 0.020},
    "1h":  {"esa": 8, "chan": 10, "sig": 12, "smooth": 3, "esa_ma": "dema", "ci": 0.010},
}
STOCH_LEN, STOCH_K, STOCH_D = 14, 7, 7


def _dema(s: pd.Series, span: int) -> pd.Series:
    e1 = s.ewm(span=span, adjust=False).mean()
    e2 = e1.ewm(span=span, adjust=False).mean()
    return 2 * e1 - e2


def _ma(s: pd.Series, span: int, mode: str) -> pd.Series:
    if mode == "dema":
        return _dema(s, span)
    return s.ewm(span=span, adjust=False).mean()


def wavetrend(df: pd.DataFrame, tf: str) -> Tuple[pd.Series, pd.Series]:
    p = WT_TF_PARAMS[tf]
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    esa = _ma(typical, p["esa"], p["esa_ma"])
    d = _ma((typical - esa).abs(), p["chan"], "ema")
    ci = (typical - esa) / (p["ci"] * d.replace(0, 1e-10))
    wt1 = ci.ewm(span=p["sig"], adjust=False).mean()
    wt2 = wt1.rolling(window=p["smooth"], min_periods=1).mean()
    return wt1, wt2


def rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0).ewm(alpha=1/length, adjust=False).mean()
    down = (-delta.clip(upper=0.0)).ewm(alpha=1/length, adjust=False).mean()
    rs = up / down.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def stoch_k(series: pd.Series) -> pd.Series:
    r = rsi(series, STOCH_LEN)
    lo = r.rolling(STOCH_LEN, min_periods=STOCH_LEN).min()
    hi = r.rolling(STOCH_LEN, min_periods=STOCH_LEN).max()
    rng = (hi - lo).replace(0, np.nan)
    s = ((r - lo) / rng).clip(0, 1) * 100.0
    k = s.rolling(STOCH_K, min_periods=STOCH_K).mean()
    return k


def load_15m(sym: str) -> Optional[pd.DataFrame]:
    p = os.path.join(KLINES_DIR, f'{sym}_15m.json')
    if not os.path.exists(p):
        return None
    with open(p) as f:
        d = json.load(f)
    if not d:
        return None
    df = pd.DataFrame(d)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert(None)
    df = df.sort_values('timestamp').reset_index(drop=True)
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)
    return df


def resample_to_3m(df15: pd.DataFrame) -> pd.DataFrame:
    """Per CLAUDE.md: each 15m bar → 5 × 3m bars, interpolated OHLCV."""
    rows = []
    o = df15['open'].values
    h = df15['high'].values
    l = df15['low'].values
    c = df15['close'].values
    v = df15['volume'].values
    ts = df15['timestamp'].values
    n = len(df15)
    for i in range(n):
        # Linear interpolate close across 5 sub-bars from prev_close → close
        prev_c = c[i-1] if i > 0 else o[i]
        sub_closes = np.linspace(prev_c, c[i], 6)[1:]  # 5 close values ending at c[i]
        sub_opens = np.concatenate([[prev_c], sub_closes[:-1]])
        sub_vol = v[i] / 5.0
        for j in range(5):
            t = pd.Timestamp(ts[i]) + pd.Timedelta(minutes=3*j)
            so = sub_opens[j]
            sc = sub_closes[j]
            # Spread the 15m H/L: bar containing peak/trough gets it; else midrange
            sh = max(so, sc) + (h[i] - max(o[i], c[i])) * 0.4 if j == 2 else max(so, sc)
            sl = min(so, sc) - (min(o[i], c[i]) - l[i]) * 0.4 if j == 2 else min(so, sc)
            rows.append((t, so, sh, sl, sc, sub_vol))
    df3 = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df3


def resample_15m_to_1h(df15: pd.DataFrame) -> pd.DataFrame:
    df = df15.set_index('timestamp')
    g = df.resample('1h').agg(open=('open', 'first'), high=('high', 'max'),
                                low=('low', 'min'), close=('close', 'last'),
                                volume=('volume', 'sum')).dropna()
    g = g.reset_index()
    return g


@dataclass
class V3Cfg:
    SCALP_V3_EXIT_BAR_REVERSAL_ENABLED: bool = True
    SCALP_V3_EXIT_WT_FLIP_ENABLED: bool = True
    SCALP_V3_EXIT_K_CROSS_ENABLED: bool = True
    SCALP_V3_MAX_HOLD_MIN: float = 0.0
    SCALP_V3_SIDE_MODE: str = 'BOTH'


@dataclass
class FakePos:
    positionAmt: float = 0.0
    entry_price: float = 0.0
    opened_at: float = 0.0
    augment_reason: str = ''


def precompute(sym: str) -> Optional[Dict]:
    df15 = load_15m(sym)
    if df15 is None or len(df15) < 200:
        return None
    df3 = resample_to_3m(df15)
    df1h = resample_15m_to_1h(df15)
    # Stoch K / WT per TF
    df3['stoch_k_3m'] = stoch_k(df3['close'])
    df15['stoch_k_15m'] = stoch_k(df15['close'])
    df1h['stoch_k_1h'] = stoch_k(df1h['close'])
    wt1_3m, wt2_3m = wavetrend(df3, '3m')
    df3['wt1_3m'] = wt1_3m
    df3['wt2_3m'] = wt2_3m
    # Map 15m and 1h TF stoch onto 3m timeline via merge_asof
    df3 = df3.sort_values('timestamp').reset_index(drop=True)
    df15s = df15[['timestamp', 'stoch_k_15m']].sort_values('timestamp').reset_index(drop=True)
    df1hs = df1h[['timestamp', 'stoch_k_1h']].sort_values('timestamp').reset_index(drop=True)
    df3 = pd.merge_asof(df3, df15s, on='timestamp', direction='backward')
    df3 = pd.merge_asof(df3, df1hs, on='timestamp', direction='backward')
    # Previous-bar fields
    df3['k_3m_prev'] = df3['stoch_k_3m'].shift(1)
    df3['high_3m_prev'] = df3['high'].shift(1)
    df3['low_3m_prev'] = df3['low'].shift(1)
    df3 = df3.dropna(subset=['stoch_k_3m', 'k_3m_prev', 'wt1_3m', 'wt2_3m',
                              'stoch_k_15m', 'stoch_k_1h']).reset_index(drop=True)
    return {
        'sym': sym,
        'df3': df3,
    }


def run_one(sym_data: Dict, cfg: V3Cfg) -> Dict:
    df = sym_data['df3']
    sym = sym_data['sym']
    n = len(df)
    pos = FakePos()
    trade_returns: List[float] = []
    trade_exit_reasons: List[str] = []
    open_count = 0
    opens_long = 0
    opens_short = 0
    POS_CAP = 20.0  # SCALP_V3_POSITION_CAP_USD
    # Build dict views per row for speed
    cols = ['stoch_k_3m', 'k_3m_prev', 'stoch_k_15m', 'stoch_k_1h',
            'high', 'low', 'high_3m_prev', 'low_3m_prev',
            'wt1_3m', 'wt2_3m', 'close']
    arr = {c: df[c].values for c in cols}
    ts = df['timestamp'].values
    # Iterate
    for i in range(n):
        price = float(arr['close'][i])
        ind = {
            'stoch_k_3m': float(arr['stoch_k_3m'][i]),
            'k_3m_prev': float(arr['k_3m_prev'][i]),
            'stoch_k_15m': float(arr['stoch_k_15m'][i]),
            'stoch_k_1h': float(arr['stoch_k_1h'][i]),
            'high_3m': float(arr['high'][i]),
            'low_3m': float(arr['low'][i]),
            'high_3m_prev': float(arr['high_3m_prev'][i]),
            'low_3m_prev': float(arr['low_3m_prev'][i]),
            'wt1_3m': float(arr['wt1_3m'][i]),
            'wt2_3m': float(arr['wt2_3m'][i]),
        }
        # Time-based exits use position.opened_at vs time.time(). Override with bar time.
        # We'll directly check max hold here instead since we control time.
        # Let's monkeypatch via a custom check that mirrors check_scalp_v3_live_exit.
        if pos.positionAmt != 0.0:
            # Custom exit using bar-time semantics
            exit_res = _bar_time_exit(pos, ind, cfg, bar_ts=ts[i])
            if exit_res:
                # Realize the trade
                if pos.positionAmt > 0:
                    ret = (price - pos.entry_price) / pos.entry_price
                else:
                    ret = (pos.entry_price - price) / pos.entry_price
                trade_returns.append(ret)
                trade_exit_reasons.append(exit_res['reason'])
                pos = FakePos()
        if pos.positionAmt == 0.0:
            entry = check_scalp_v3_live_entry(sym, sym + '_LONG', ind, price, None, 'inf', cfg)
            if entry:
                side = entry['side']
                pos.entry_price = price
                pos.opened_at = float(pd.Timestamp(ts[i]).timestamp())
                pos.augment_reason = entry['reason']
                qty = POS_CAP / price if price > 0 else 0
                pos.positionAmt = qty if side == 'LONG' else -qty
                open_count += 1
                if side == 'LONG':
                    opens_long += 1
                else:
                    opens_short += 1
    # Mark-to-market open position at end (per CLAUDE.md)
    if pos.positionAmt != 0.0:
        last_price = float(arr['close'][-1])
        if pos.positionAmt > 0:
            ret = (last_price - pos.entry_price) / pos.entry_price
        else:
            ret = (pos.entry_price - last_price) / pos.entry_price
        trade_returns.append(ret)
        trade_exit_reasons.append('MTM_END')
    # Metrics
    if not trade_returns:
        return {'sym': sym, 'trades': 0, 'pool_sharpe': 0.0, 'sym_sharpe': 0.0,
                'avg_gain_trade': 0.0, 'acc_gain_pct': 0.0, 'max_dd_pct': 0.0,
                'opens_long': opens_long, 'opens_short': opens_short,
                'trade_returns': []}
    arr_r = np.asarray(trade_returns)
    mean_r = float(arr_r.mean())
    std_r = float(arr_r.std())
    sharpe = mean_r / std_r if std_r > 0 else 0.0
    acc_gain_pct = float(arr_r.sum()) * 100.0
    avg_gain_trade = float(arr_r.mean()) * 100.0
    # Drawdown on cumulative returns
    cum = np.cumsum(arr_r) * 100.0  # cum gain in %
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0
    return {
        'sym': sym,
        'trades': len(trade_returns),
        'pool_sharpe': sharpe,
        'sym_sharpe': sharpe,
        'avg_gain_trade': avg_gain_trade,
        'acc_gain_pct': acc_gain_pct,
        'max_dd_pct': max_dd,
        'opens_long': opens_long,
        'opens_short': opens_short,
        'trade_returns': trade_returns,
    }


def _bar_time_exit(pos: FakePos, ind: Dict, cfg: V3Cfg, bar_ts) -> Optional[Dict]:
    """Mirrors check_scalp_v3_live_exit but uses bar-time for max-hold."""
    if pos.positionAmt == 0.0:
        return None
    reason = pos.augment_reason or ''
    if 'SCALP_V3_OPEN_' not in reason:
        return None
    side = 'LONG' if '_LONG_' in reason else 'SHORT' if '_SHORT_' in reason else None
    if side is None:
        return None
    high_3m = ind['high_3m']
    low_3m = ind['low_3m']
    high_3m_prev = ind['high_3m_prev']
    low_3m_prev = ind['low_3m_prev']
    if high_3m_prev <= 0 or low_3m_prev <= 0:
        return None
    k_3m = ind['stoch_k_3m']
    k_3m_prev = ind['k_3m_prev']
    wt1 = ind['wt1_3m']
    wt2 = ind['wt2_3m']
    bar_on = cfg.SCALP_V3_EXIT_BAR_REVERSAL_ENABLED
    wt_on = cfg.SCALP_V3_EXIT_WT_FLIP_ENABLED
    k_on = cfg.SCALP_V3_EXIT_K_CROSS_ENABLED
    if side == 'LONG':
        bar_falling = (low_3m < low_3m_prev) or (high_3m < high_3m_prev)
        wt_flip_bear = wt1 < wt2
        k_cross_down = (k_3m < k_3m_prev) and (k_3m < 50)
        if bar_on and bar_falling:
            return {'reason': 'BAR_DOWN'}
        if wt_on and wt_flip_bear:
            return {'reason': 'WT_FLIP'}
        if k_on and k_cross_down:
            return {'reason': 'K_DOWN'}
    else:
        bar_rising = (high_3m > high_3m_prev) or (low_3m > low_3m_prev)
        wt_flip_bull = wt1 > wt2
        k_cross_up = (k_3m > k_3m_prev) and (k_3m > 50)
        if bar_on and bar_rising:
            return {'reason': 'BAR_UP'}
        if wt_on and wt_flip_bull:
            return {'reason': 'WT_FLIP'}
        if k_on and k_cross_up:
            return {'reason': 'K_UP'}
    max_hold = cfg.SCALP_V3_MAX_HOLD_MIN
    if max_hold > 0:
        bar_t = float(pd.Timestamp(bar_ts).timestamp())
        if (bar_t - pos.opened_at) > max_hold * 60.0:
            return {'reason': 'MAX_HOLD'}
    return None


def run_combo(precomputed: List[Dict], cfg: V3Cfg, n_years: float) -> Dict:
    n_syms = len(precomputed)
    per_sym = []
    all_returns: List[float] = []
    sharpes: List[float] = []
    total_trades = 0
    for sd in precomputed:
        r = run_one(sd, cfg)
        per_sym.append(r)
        all_returns.extend(r['trade_returns'])
        if r['trades'] >= 5:
            sharpes.append(r['pool_sharpe'])
        total_trades += r['trades']
    if not all_returns:
        return {'pool_sharpe': 0.0, 'sym_sharpe': 0.0,
                'avg_gain_trade': 0.0, 'gain_per_yr': 0.0, 'gain_sym_yr': 0.0,
                'acc_gain_pct': 0.0, 'max_dd_pct': 0.0, 'trades': 0,
                'opens_long': sum(p['opens_long'] for p in per_sym),
                'opens_short': sum(p['opens_short'] for p in per_sym),
                'syms_with_trades': 0}
    a = np.asarray(all_returns)
    mean_r = float(a.mean())
    std_r = float(a.std())
    pool_sharpe = mean_r / std_r if std_r > 0 else 0.0
    sym_sharpe = float(np.mean(sharpes)) if sharpes else 0.0
    acc_gain_pct = float(a.sum()) * 100.0
    avg_gain_trade = mean_r * 100.0
    gain_per_yr = acc_gain_pct / n_years if n_years > 0 else 0.0
    gain_sym_yr = acc_gain_pct / n_syms / n_years if n_syms > 0 and n_years > 0 else 0.0
    # Pool DD: aggregate across syms by chronological order would be complex; use per-sym DD avg + max
    max_dd_per_sym = [p['max_dd_pct'] for p in per_sym if p['trades'] >= 5]
    max_dd_pct = float(max(max_dd_per_sym)) if max_dd_per_sym else 0.0
    avg_dd_pct = float(np.mean(max_dd_per_sym)) if max_dd_per_sym else 0.0
    return {
        'pool_sharpe': pool_sharpe,
        'sym_sharpe': sym_sharpe,
        'avg_gain_trade': avg_gain_trade,
        'gain_per_yr': gain_per_yr,
        'gain_sym_yr': gain_sym_yr,
        'acc_gain_pct': acc_gain_pct,
        'max_dd_pct': max_dd_pct,
        'avg_dd_pct': avg_dd_pct,
        'trades': total_trades,
        'opens_long': sum(p['opens_long'] for p in per_sym),
        'opens_short': sum(p['opens_short'] for p in per_sym),
        'syms_with_trades': sum(1 for p in per_sym if p['trades'] > 0),
    }


def main():
    t0 = time.time()
    print(f'[{time.strftime("%H:%M:%S")}] Precomputing indicators for {len(SYMBOLS)} symbols...')
    precomputed = []
    for s in SYMBOLS:
        sd = precompute(s)
        if sd is None:
            print(f'  SKIP {s}: missing or insufficient data')
            continue
        precomputed.append(sd)
        print(f'  {s}: {len(sd["df3"])} 3m bars ready ({time.time()-t0:.1f}s)')
    print(f'Precompute done in {time.time()-t0:.1f}s. {len(precomputed)} symbols ready.')
    n_syms = len(precomputed)
    # Determine actual time span from first sym
    df3 = precomputed[0]['df3']
    days = (df3['timestamp'].iloc[-1] - df3['timestamp'].iloc[0]).total_seconds() / 86400.0
    n_years = days / 365.25
    print(f'Test window: {df3["timestamp"].iloc[0].date()} → {df3["timestamp"].iloc[-1].date()} ({days:.1f}d, {n_years:.3f}y)')
    # Sweep grid
    exit_combos = []
    for bar_on in (True, False):
        for wt_on in (True, False):
            for k_on in (True, False):
                if not (bar_on or wt_on or k_on):
                    continue  # must have at least one technical exit
                exit_combos.append((bar_on, wt_on, k_on))
    max_holds = [0.0, 5.0, 15.0, 60.0]
    side_modes = ['BOTH', 'LONG_ONLY', 'SHORT_ONLY']
    rows = []
    total = len(exit_combos) * len(max_holds) * len(side_modes)
    done = 0
    for (bar_on, wt_on, k_on) in exit_combos:
        for max_hold in max_holds:
            for side_mode in side_modes:
                cfg = V3Cfg(
                    SCALP_V3_EXIT_BAR_REVERSAL_ENABLED=bar_on,
                    SCALP_V3_EXIT_WT_FLIP_ENABLED=wt_on,
                    SCALP_V3_EXIT_K_CROSS_ENABLED=k_on,
                    SCALP_V3_MAX_HOLD_MIN=max_hold,
                    SCALP_V3_SIDE_MODE=side_mode,
                )
                m = run_combo(precomputed, cfg, n_years)
                done += 1
                row = {
                    'bar_on': bar_on, 'wt_on': wt_on, 'k_on': k_on,
                    'max_hold_min': max_hold, 'side_mode': side_mode,
                    'pool_sharpe': round(m['pool_sharpe'], 4),
                    'sym_sharpe': round(m['sym_sharpe'], 4),
                    'avg_gain_trade': round(m['avg_gain_trade'], 4),
                    'gain_per_yr': round(m['gain_per_yr'], 2),
                    'gain_sym_yr': round(m['gain_sym_yr'], 4),
                    'acc_gain_pct': round(m['acc_gain_pct'], 2),
                    'max_dd_pct': round(m['max_dd_pct'], 2),
                    'avg_dd_pct': round(m['avg_dd_pct'], 2),
                    'trades': m['trades'],
                    'opens_long': m['opens_long'],
                    'opens_short': m['opens_short'],
                    'syms_with_trades': m['syms_with_trades'],
                    'n_syms': n_syms,
                    'n_years': round(n_years, 3),
                }
                rows.append(row)
                if done % 10 == 0 or done == total:
                    print(f'  [{done}/{total}] last cfg bar={bar_on} wt={wt_on} k={k_on} hold={max_hold} side={side_mode} → sharpe={row["pool_sharpe"]} trades={row["trades"]} gain={row["acc_gain_pct"]}% ({time.time()-t0:.1f}s)')
    df = pd.DataFrame(rows)
    df = df.sort_values('pool_sharpe', ascending=False).reset_index(drop=True)
    ts_label = time.strftime('%Y%m%d_%H%M')
    out_path = os.path.join(OUT_DIR, f'v3_backtest_sweep_{ts_label}.csv')
    df.to_csv(out_path, index=False)
    print(f'\nWrote {len(df)} rows → {out_path}')
    print('\n=== TOP 10 by pool_sharpe ===')
    print(df.head(10).to_string(index=False))
    print(f'\nElapsed: {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
