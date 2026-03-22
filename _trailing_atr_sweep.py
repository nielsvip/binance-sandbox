#!/usr/bin/env python3
"""
TRAILING ATR EXIT SWEEP
Tests trailing ATR exits with multipliers 1.0, 1.5, 2.0, 2.5, 3.0
on ATR from timeframes: 3m, 15m, 1h, 4h
With activation thresholds: 0.3%, 0.5%, 1.0%
Retrace ratios: 0.3, 0.5, 0.7

Total combos: 5 mults × 4 TFs × 3 activations × 3 retraces = 180 combos
Tested on top 50 liquid crypto symbols, LONG and SHORT
"""

import json, os, sys, time, csv
from pathlib import Path
from datetime import datetime
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

KLINES_DIR = Path("/home/niels/binance/klines_cache")
OUTPUT = Path("/home/niels/binance-sandbox/backtest_framework/results/trailing_atr_sweep.csv")

ATR_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0]
ATR_TFS = ['3m', '15m', '1h', '4h']
ACTIVATIONS = [0.3, 0.5, 1.0]  # min gain % before trailing activates
RETRACES = [0.3, 0.5, 0.7]  # fraction of ATR that triggers exit
HARD_STOP_PCT = 3.0  # fixed hard stop

# Top 50 liquid symbols
TOP_SYMBOLS = [
    'BTCUSDC', 'ETHUSDC', 'BNBUSDC', 'SOLUSDC', 'XRPUSDT', 'DOGEUSDT',
    'ADAUSDT', 'AVAXUSDC', 'DOTUSDT', 'LINKUSDC', 'MATICUSDT', 'UNIUSDT',
    'LTCUSDT', 'ATOMUSDT', 'NEARUSDC', 'APTUSDT', 'AAVEUSDC', 'ARBUSDC',
    'OPUSDT', 'SUIUSDC', 'FILUSDT', 'TRXUSDT', 'SHIBUSDT', 'TONUSDT',
    'INJUSDT', 'TIAUSDC', 'STXUSDT', 'IMXUSDT', 'RENDERUSDT', 'FETUSDT',
    'RNDRUSDT', 'GRTUSDT', 'SANDUSDT', 'MANAUSDT', 'AXSUSDT', 'ARUSDT',
    'DYDXUSDT', 'SEIUSDT', 'WOOUSDT', 'CRVUSDC', 'MKRUSDT', 'COMPUSDT',
    'SNXUSDT', 'ZRXUSDT', 'ENAUSDC', 'EIGENUSDT', 'PENDLEUSDT', 'WLDUSDT',
    'RUNEUSDT', 'TRUMPUSDC'
]


def load_klines(symbol, tf):
    """Load kline data for a symbol and timeframe."""
    fpath = KLINES_DIR / symbol / f"{tf}.json"
    if not fpath.exists():
        return []
    try:
        data = json.loads(fpath.read_text())
        if isinstance(data, list):
            return data[-2000:]  # last 2000 candles
    except:
        pass
    return []


def compute_atr(candles, period=14):
    """Compute ATR for each candle."""
    n = len(candles)
    if n < period + 1:
        return candles
    for i in range(1, n):
        h = float(candles[i].get('high', candles[i].get('h', 0)))
        l = float(candles[i].get('low', candles[i].get('l', 0)))
        pc = float(candles[i-1].get('close', candles[i-1].get('c', 0)))
        tr = max(h - l, abs(h - pc), abs(l - pc))
        candles[i]['tr'] = tr
    # SMA ATR
    for i in range(period, n):
        candles[i]['atr'] = np.mean([candles[j]['tr'] for j in range(i - period + 1, i + 1) if 'tr' in candles[j]])
    return candles


def compute_stoch(candles, k_period=14, smooth_k=5):
    """Compute stochastic K for entry signals."""
    n = len(candles)
    closes = [float(c.get('close', c.get('c', 0))) for c in candles]
    highs = [float(c.get('high', c.get('h', 0))) for c in candles]
    lows = [float(c.get('low', c.get('l', 0))) for c in candles]
    raw_k = [50.0] * n
    for i in range(k_period, n):
        ll = min(lows[i-k_period:i+1])
        hh = max(highs[i-k_period:i+1])
        raw_k[i] = (closes[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
    for i in range(k_period + smooth_k, n):
        candles[i]['k'] = np.mean(raw_k[i-smooth_k+1:i+1])
        candles[i]['k_prev'] = np.mean(raw_k[i-smooth_k:i])
    return candles


def run_backtest(symbol, side, atr_mult, atr_tf, activation_pct, retrace_ratio):
    """Run a single backtest with given trailing ATR parameters."""
    # Load entry TF (3m for crypto)
    entry_candles = load_klines(symbol, '3m')
    if len(entry_candles) < 200:
        return None

    # Load ATR TF
    atr_candles = load_klines(symbol, atr_tf)
    if len(atr_candles) < 50:
        return None

    entry_candles = compute_stoch(entry_candles)
    atr_candles = compute_atr(atr_candles)

    # Build ATR lookup by timestamp
    atr_by_ts = {}
    for c in atr_candles:
        ts = c.get('timestamp', c.get('t', c.get('open_time', 0)))
        if 'atr' in c:
            atr_by_ts[ts] = c['atr']

    is_long = (side == 'LONG')
    trades = []
    in_trade = False
    entry_price = 0
    max_gain_pct = 0
    trailing_active = False
    trade_entry_ts = 0

    for i in range(50, len(entry_candles)):
        c = entry_candles[i]
        price = float(c.get('close', c.get('c', 0)))
        if price <= 0:
            continue

        k = c.get('k', 50)
        k_prev = c.get('k_prev', 50)
        ts = c.get('timestamp', c.get('t', c.get('open_time', 0)))

        if not in_trade:
            # Entry: stoch cross in oversold/overbought
            if is_long and k > 30 and k_prev <= 30 and k < 70:
                in_trade = True
                entry_price = price
                max_gain_pct = 0
                trailing_active = False
                trade_entry_ts = ts
            elif not is_long and k < 70 and k_prev >= 70 and k > 30:
                in_trade = True
                entry_price = price
                max_gain_pct = 0
                trailing_active = False
                trade_entry_ts = ts
        else:
            # Calculate gain
            gain_pct = ((price - entry_price) / entry_price * 100) if is_long else ((entry_price - price) / entry_price * 100)
            max_gain_pct = max(max_gain_pct, gain_pct)

            # Get current ATR (find closest ATR timestamp <= current)
            current_atr = 0
            for ats in sorted(atr_by_ts.keys(), reverse=True):
                if ats <= ts:
                    current_atr = atr_by_ts[ats]
                    break
            if current_atr <= 0:
                continue

            atr_pct = current_atr / entry_price * 100
            trail_distance = atr_pct * atr_mult
            retrace_trigger = trail_distance * retrace_ratio

            exit_reason = None

            # Hard stop
            if gain_pct <= -HARD_STOP_PCT:
                exit_reason = 'HARD_STOP'

            # Trailing ATR exit
            elif gain_pct >= activation_pct:
                trailing_active = True

            if trailing_active and (max_gain_pct - gain_pct) >= retrace_trigger:
                exit_reason = f'TRAIL_ATR_{atr_mult}x_{atr_tf}'

            # Max hold: 500 candles
            bars_held = i - next((j for j in range(len(entry_candles)) if entry_candles[j].get('timestamp', entry_candles[j].get('t', 0)) == trade_entry_ts), i)
            if bars_held > 500:
                exit_reason = 'MAX_HOLD'

            if exit_reason:
                trades.append({
                    'gain_pct': gain_pct,
                    'max_gain_pct': max_gain_pct,
                    'exit_reason': exit_reason,
                    'bars_held': bars_held,
                })
                in_trade = False

    if not trades:
        return None

    gains = [t['gain_pct'] for t in trades]
    wins = [g for g in gains if g > 0]
    losses = [g for g in gains if g <= 0]

    total_return = sum(gains)
    win_rate = len(wins) / len(gains) * 100 if gains else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 999.0

    # Sharpe
    if len(gains) > 1 and np.std(gains) > 0:
        sharpe = np.mean(gains) / np.std(gains) * np.sqrt(len(gains))
    else:
        sharpe = 0

    # Max drawdown
    equity = np.cumsum(gains)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = np.max(dd) if len(dd) > 0 else 0

    return {
        'symbol': symbol, 'side': side,
        'atr_mult': atr_mult, 'atr_tf': atr_tf,
        'activation_pct': activation_pct, 'retrace_ratio': retrace_ratio,
        'trades': len(trades), 'win_rate': round(win_rate, 1),
        'total_return': round(total_return, 2), 'avg_win': round(avg_win, 3),
        'avg_loss': round(avg_loss, 3), 'profit_factor': round(min(profit_factor, 999), 2),
        'sharpe': round(sharpe, 2), 'max_dd': round(max_dd, 2),
    }


def run_single(args):
    return run_backtest(*args)


def main():
    combos = []
    for symbol in TOP_SYMBOLS:
        for side in ['LONG', 'SHORT']:
            for atr_mult in ATR_MULTS:
                for atr_tf in ATR_TFS:
                    for act in ACTIVATIONS:
                        for ret in RETRACES:
                            combos.append((symbol, side, atr_mult, atr_tf, act, ret))

    print(f"Running {len(combos)} backtest combinations...")
    print(f"Symbols: {len(TOP_SYMBOLS)} | Sides: 2 | ATR mults: {ATR_MULTS}")
    print(f"ATR TFs: {ATR_TFS} | Activations: {ACTIVATIONS} | Retraces: {RETRACES}")

    results = []
    t0 = time.time()
    workers = min(multiprocessing.cpu_count(), 8)

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_single, c): c for c in combos}
        done = 0
        for f in as_completed(futures):
            done += 1
            r = f.result()
            if r:
                results.append(r)
            if done % 1000 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed
                print(f"  {done}/{len(combos)} ({done/len(combos)*100:.0f}%) | {rate:.0f}/s | results={len(results)}")

    elapsed = time.time() - t0
    print(f"\nDone: {len(results)} results from {len(combos)} combos in {elapsed:.0f}s")

    # Write CSV
    if results:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)
        print(f"Results saved to {OUTPUT}")

    # Aggregate by (atr_mult, atr_tf, activation, retrace)
    from collections import defaultdict
    agg = defaultdict(list)
    for r in results:
        key = (r['atr_mult'], r['atr_tf'], r['activation_pct'], r['retrace_ratio'])
        agg[key].append(r)

    print(f"\n{'='*120}")
    print(f"{'ATR_MULT':>8} {'ATR_TF':>6} {'ACT%':>5} {'RET':>5} | {'Sharpe':>8} {'WinRate':>8} {'PF':>8} {'Return':>8} {'MaxDD':>8} {'Trades':>7}")
    print(f"{'='*120}")

    ranked = []
    for key, rows in agg.items():
        avg_sharpe = np.mean([r['sharpe'] for r in rows])
        avg_wr = np.mean([r['win_rate'] for r in rows])
        avg_pf = np.mean([min(r['profit_factor'], 50) for r in rows])
        avg_ret = np.mean([r['total_return'] for r in rows])
        avg_dd = np.mean([r['max_dd'] for r in rows])
        avg_trades = np.mean([r['trades'] for r in rows])
        ranked.append((key, avg_sharpe, avg_wr, avg_pf, avg_ret, avg_dd, avg_trades, len(rows)))

    ranked.sort(key=lambda x: x[1], reverse=True)
    for key, sh, wr, pf, ret, dd, tr, n in ranked[:30]:
        print(f"{key[0]:>8.1f} {key[1]:>6} {key[2]:>5.1f} {key[3]:>5.1f} | {sh:>8.2f} {wr:>7.1f}% {pf:>8.2f} {ret:>7.2f}% {dd:>7.2f}% {tr:>7.0f}  (n={n})")

    print(f"\n{'='*120}")
    print("TOP 10 WINNING COMBOS:")
    print(f"{'='*120}")
    for i, (key, sh, wr, pf, ret, dd, tr, n) in enumerate(ranked[:10]):
        print(f"  #{i+1}: ATR {key[0]}x on {key[1]}, activate at {key[2]}%, retrace {key[3]*100:.0f}% of trail")
        print(f"       Sharpe={sh:.2f} WR={wr:.1f}% PF={pf:.2f} Return={ret:.2f}% MaxDD={dd:.2f}%")


if __name__ == '__main__':
    main()
