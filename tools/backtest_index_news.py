# pylint: disable=W,C,R,I
"""backtest_index_news — parameter sweep for tradier_index_news.py mechanics on s1 NPZs.
Mirrors the live daemon rules (premarket gap long + ORB breakout, 200SMA regime gate,
breakeven trail, structural stop at opposite range edge, EOD flat) on 15m bars.
HONESTY: the live news-bias gate CANNOT be backtested (no historical news archive) —
all results assume the bias gate open, so live trade count will be LOWER than backtest.
Exit checks use 15m closes here vs 5m in the daemon (coarser; disclosed).
Fills at bar close with 1bp slippage per side, commission 0 (Tradier equities).
Gain metric = sum of per-trade % returns on fixed equal notional (no compounding),
max_dd computed on the cumulative per-trade % curve. NO Sharpe is emitted.
Run on s1: python3 backtest_index_news.py --npz-dir ~/binance-sandbox/backtest_v8/indicators \
    --daily index_news_daily.json --mode sweep30 | confirm365
"""
import argparse
import itertools
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import numpy as np

ET = ZoneInfo('America/New_York')
SLIP = 0.0001

def load_symbol(npz_dir, symbol):
    z = np.load(Path(npz_dir) / f'{symbol}.npz', allow_pickle=True)
    ts = z['timestamps'].astype(float)
    if ts[0] > 1e12:
        ts = ts / 1000.0
    o = z['open_15m'].astype(float)
    h = z['high_15m'].astype(float)
    l = z['low_15m'].astype(float)
    c = z['close_15m'].astype(float)
    n = min(len(ts), len(o), len(h), len(l), len(c))
    return ts[:n], o[:n], h[:n], l[:n], c[:n]

def build_sessions(ts):
    """Group bar indices by ET session date; classify premarket (07:00-09:25 ET) and RTH (09:30-15:59 ET)."""
    sessions = {}
    for i, t in enumerate(ts):
        dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET)
        if dt.weekday() >= 5:
            continue
        key = dt.date().isoformat()
        rec = sessions.setdefault(key, {'pre': [], 'rth': [], 'weekday': dt.weekday()})
        hm = dt.hour * 60 + dt.minute
        if 7 * 60 <= hm < 9 * 60 + 25:
            rec['pre'].append(i)
        elif 9 * 60 + 30 <= hm < 16 * 60:
            rec['rth'].append(i)
    return dict(sorted(sessions.items()))

def sma200_map(daily_rows):
    closes = [(r['date'], r['close']) for r in daily_rows]
    out = {}
    for i in range(len(closes)):
        if i >= 199:
            out[closes[i][0]] = sum(x[1] for x in closes[i - 199:i + 1]) / 200.0
    return out

def prev_close_map(daily_rows):
    out = {}
    for i in range(1, len(daily_rows)):
        out[daily_rows[i]['date']] = daily_rows[i - 1]['close']
    return out

def run_backtest(ts, o, h, l, c, sessions, smamap, pcmap, cfg, start_date, end_date):
    trades = []
    for day, rec in sessions.items():
        if day < start_date or day > end_date:
            continue
        if not rec['rth']:
            continue
        prev_close = pcmap.get(day)
        sma = smamap.get(day)
        if not prev_close or not sma:
            continue
        regime_long = prev_close > sma
        day_gap = (o[rec['rth'][0]] - prev_close) / prev_close * 100.0
        if abs(day_gap) < cfg.get('gap_min', 0.0):
            continue
        pos = None
        if cfg['pre'] and regime_long:
            for i in rec['pre']:
                gap_pct = (c[i] - prev_close) / prev_close * 100.0
                if gap_pct > cfg['gap_max'] or gap_pct < -0.3:
                    continue
                if rec['weekday'] == 0 and gap_pct > 0.3:
                    continue
                entry = c[i] * (1 + SLIP)
                pos = {'side': 'LONG', 'entry': entry, 'stop': entry * 0.994, 'risk': entry * 0.006, 'be': False, 'reason': f'PRE gap={gap_pct:+.2f}'}
                break
        k = max(1, cfg['orb'] // 15)
        rth = rec['rth']
        if len(rth) <= k:
            continue
        or_high = max(h[i] for i in rth[:k])
        or_low = min(l[i] for i in rth[:k])
        rng_pct = (or_high - or_low) / or_low * 100.0
        for j, i in enumerate(rth):
            if pos is None and j >= k and rng_pct <= cfg['rng_cap']:
                if regime_long and c[i] > or_high:
                    entry = c[i] * (1 + SLIP)
                    pos = {'side': 'LONG', 'entry': entry, 'stop': or_low, 'risk': entry - or_low, 'be': False, 'reason': f'ORB_L rng={rng_pct:.2f}'}
                    continue
                if not regime_long and cfg['shorts'] and c[i] < or_low:
                    entry = c[i] * (1 - SLIP)
                    pos = {'side': 'SHORT', 'entry': entry, 'stop': or_high, 'risk': or_high - entry, 'be': False, 'reason': f'ORB_S rng={rng_pct:.2f}'}
                    continue
            if pos is not None and j >= 1:
                is_long = pos['side'] == 'LONG'
                gain_abs = (c[i] - pos['entry']) if is_long else (pos['entry'] - c[i])
                if not pos['be'] and pos['risk'] > 0 and gain_abs >= cfg['be_r'] * pos['risk']:
                    pos['stop'] = pos['entry']
                    pos['be'] = True
                stop_hit = (is_long and c[i] < pos['stop']) or (not is_long and c[i] > pos['stop'])
                is_last = i == rth[-1]
                if stop_hit or is_last:
                    exit_px = c[i] * (1 - SLIP) if is_long else c[i] * (1 + SLIP)
                    gain_pct = (exit_px / pos['entry'] - 1) * 100.0 * (1 if is_long else -1)
                    trades.append({'day': day, 'side': pos['side'], 'entry': round(pos['entry'], 4), 'exit': round(exit_px, 4), 'gain_pct': round(gain_pct, 4), 'reason': 'EOD' if (is_last and not stop_hit) else ('BE_STOP' if pos['be'] else 'STRUCT_STOP'), 'entry_reason': pos['reason']})
                    pos = None
    return trades

def summarize(trades):
    if not trades:
        return {'gain': 0.0, 'trades': 0, 'win_rate': 0.0, 'max_dd_pct': 0.0, 'avg_gain_trade': 0.0}
    gains = [t['gain_pct'] for t in trades]
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for g in gains:
        cum += g
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    wins = sum(1 for g in gains if g > 0)
    return {'gain': round(sum(gains), 3), 'trades': len(gains), 'win_rate': round(wins / len(gains) * 100, 1), 'max_dd_pct': round(max_dd, 3), 'avg_gain_trade': round(sum(gains) / len(gains), 4)}

def buy_hold(sessions, pcmap, ts, c, start_date, end_date):
    days = [d for d in sessions if start_date <= d <= end_date and sessions[d]['rth']]
    if len(days) < 2:
        return 0.0
    first = c[sessions[days[0]]['rth'][0]]
    last = c[sessions[days[-1]]['rth'][-1]]
    return round((last / first - 1) * 100.0, 3)

GRID = {
    'orb': [15, 30, 45, 60],
    'be_r': [0.25, 0.5, 1.0, 99.0],
    'gap_max': [0.5, 1.0, 1.5],
    'pre': [0, 1],
    'rng_cap': [0.5, 1.0, 1.5],
    'shorts': [0, 1],
    'gap_min': [0.0, 0.3, 0.5],
}
LIVE_DEFAULT = {'orb': 30, 'be_r': 0.5, 'gap_max': 1.0, 'pre': 1, 'rng_cap': 1.0, 'shorts': 1, 'gap_min': 0.0}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--npz-dir', default='backtest_v8/indicators')
    ap.add_argument('--daily', default='index_news_daily.json')
    ap.add_argument('--symbols', default='SPY,QQQ,VT')
    ap.add_argument('--mode', default='sweep30', choices=['sweep30', 'confirm365', 'sweep365'])
    ap.add_argument('--best', default='', help='json of per-symbol best cfg for confirm365')
    args = ap.parse_args()
    with open(args.daily) as f:
        daily = json.load(f)
    results = {}
    for symbol in args.symbols.split(','):
        ts, o, h, l, c = load_symbol(args.npz_dir, symbol)
        sessions = build_sessions(ts)
        smamap = sma200_map(daily[symbol])
        pcmap = prev_close_map(daily[symbol])
        last_day = max(d for d in sessions if sessions[d]['rth'])
        end_dt = datetime.fromisoformat(last_day)
        if args.mode == 'sweep30':
            start_date = (end_dt - timedelta(days=44)).date().isoformat()
            window = '30D'
        else:
            start_date = (end_dt - timedelta(days=365)).date().isoformat()
            window = '365D'
        do_sweep = args.mode in ('sweep30', 'sweep365')
        avail_days = [d for d in sessions if start_date <= d <= last_day and sessions[d]['rth']]
        bh = buy_hold(sessions, pcmap, ts, c, start_date, last_day)
        sym_res = {'window': window, 'start': start_date, 'end': last_day, 'sessions': len(avail_days), 'buy_hold': bh, 'configs': []}
        if do_sweep:
            keys = list(GRID.keys())
            for combo in itertools.product(*(GRID[k] for k in keys)):
                cfg = dict(zip(keys, combo))
                trades = run_backtest(ts, o, h, l, c, sessions, smamap, pcmap, cfg, start_date, last_day)
                s = summarize(trades)
                sym_res['configs'].append({'cfg': cfg, **s})
            trades = run_backtest(ts, o, h, l, c, sessions, smamap, pcmap, LIVE_DEFAULT, start_date, last_day)
            sym_res['live_default'] = {'cfg': LIVE_DEFAULT, **summarize(trades)}
            sym_res['configs'].sort(key=lambda x: -x['gain'])
        else:
            best = json.loads(args.best) if args.best else {}
            cfg = best.get(symbol, LIVE_DEFAULT)
            trades = run_backtest(ts, o, h, l, c, sessions, smamap, pcmap, cfg, start_date, last_day)
            s = summarize(trades)
            sym_res['configs'].append({'cfg': cfg, **s, 'trade_returns': [t['gain_pct'] for t in trades], 'trades_detail': trades[-10:]})
            trades_d = run_backtest(ts, o, h, l, c, sessions, smamap, pcmap, LIVE_DEFAULT, start_date, last_day)
            sym_res['live_default'] = {'cfg': LIVE_DEFAULT, **summarize(trades_d), 'trade_returns': [t['gain_pct'] for t in trades_d]}
        results[symbol] = sym_res
    print(json.dumps(results, indent=1))

if __name__ == '__main__':
    main()
