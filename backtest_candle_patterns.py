#!/usr/bin/env python3
"""Candle-pattern backtest: k_1m + 1m/3m/15m structure.
Tests entries on higher-low (long) / lower-high (short) with stoch confirmation.
Tests exits on structure break (lower-high for longs, higher-low for shorts).
Fee: 0.04% per side (0.08% round trip)."""
import json, os, glob, sys, time
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path

BASE = Path(os.path.dirname(os.path.abspath(__file__)))
KLINES_DIR = BASE / "klines_cache" / "consolidated_klines"
FEE_PCT = 0.04
STOCH_LEN = 14
STOCH_K = 5
STOCH_D = 5

# ═══ Data loading ═══
def load_klines(symbol: str, tf: str) -> Optional[np.ndarray]:
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    with open(path) as f:
        raw = json.load(f)
    if len(raw) < 50: return None
    dtype = [('ts', 'U30'), ('o', 'f8'), ('h', 'f8'), ('l', 'f8'), ('c', 'f8'), ('v', 'f8')]
    arr = np.array([(r['timestamp'], r['open'], r['high'], r['low'], r['close'], r['volume']) for r in raw], dtype=dtype)
    return arr

# ═══ Indicators ═══
def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)
    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(close) * 50, where=avg_loss > 0)
    return 100 - (100 / (1 + rs))

def stoch_rsi(close: np.ndarray, length: int = 14, k: int = 5, d: int = 5) -> Tuple[np.ndarray, np.ndarray]:
    r = rsi(close, length)
    n = len(r)
    stoch = np.full(n, 50.0)
    for i in range(length, n):
        lo = np.min(r[i - length + 1:i + 1])
        hi = np.max(r[i - length + 1:i + 1])
        stoch[i] = ((r[i] - lo) / (hi - lo) * 100) if hi > lo else 50.0
    k_line = np.convolve(stoch, np.ones(k) / k, mode='same')
    d_line = np.convolve(k_line, np.ones(d) / d, mode='same')
    return k_line, d_line

# ═══ Candle structure detection ═══
def detect_patterns(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> Dict[str, np.ndarray]:
    n = len(h)
    higher_low = np.zeros(n, dtype=bool)
    lower_high = np.zeros(n, dtype=bool)
    higher_high = np.zeros(n, dtype=bool)
    lower_low = np.zeros(n, dtype=bool)
    for i in range(2, n):
        higher_low[i] = l[i] > l[i - 1] and l[i - 1] > 0
        lower_high[i] = h[i] < h[i - 1] and h[i - 1] > 0
        higher_high[i] = h[i] > h[i - 1]
        lower_low[i] = l[i] < l[i - 1]
    return {'higher_low': higher_low, 'lower_high': lower_high, 'higher_high': higher_high, 'lower_low': lower_low}

# ═══ Strategy definitions ═══
@dataclass
class Trade:
    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    entry_bar: int
    entry_reason: str
    exit_price: float = 0.0
    exit_bar: int = 0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    hold_bars: int = 0

def run_strategy(symbol: str, strategy: str, params: dict) -> List[Trade]:
    """Run one strategy variant on one symbol."""
    entry_tf = params.get('entry_tf', '3m')
    confirm_tf = params.get('confirm_tf', '15m')
    kl_entry = load_klines(symbol, entry_tf)
    kl_confirm = load_klines(symbol, confirm_tf)
    kl_1m = load_klines(symbol, '1m')
    if kl_entry is None or len(kl_entry) < 100: return []
    close_e = kl_entry['c']
    high_e = kl_entry['h']
    low_e = kl_entry['l']
    open_e = kl_entry['o']
    k_e, d_e = stoch_rsi(close_e, STOCH_LEN, STOCH_K, STOCH_D)
    patterns_e = detect_patterns(high_e, low_e, close_e)
    # Confirm TF stoch (resampled to entry bars by nearest timestamp)
    k_c, d_c = np.full(len(close_e), 50.0), np.full(len(close_e), 50.0)
    if kl_confirm is not None and len(kl_confirm) > 50:
        kc_raw, dc_raw = stoch_rsi(kl_confirm['c'], STOCH_LEN, STOCH_K, STOCH_D)
        # Simple mapping: for each entry bar, find closest confirm bar
        confirm_ts = kl_confirm['ts']
        entry_ts = kl_entry['ts']
        ci = 0
        for ei in range(len(entry_ts)):
            while ci < len(confirm_ts) - 1 and confirm_ts[ci + 1] <= entry_ts[ei]:
                ci += 1
            k_c[ei] = kc_raw[ci]
            d_c[ei] = dc_raw[ci]
    # 1m stoch if available
    k_1m, d_1m = np.full(len(close_e), 50.0), np.full(len(close_e), 50.0)
    if kl_1m is not None and len(kl_1m) > 50:
        k1_raw, d1_raw = stoch_rsi(kl_1m['c'], STOCH_LEN, STOCH_K, STOCH_D)
        _1m_ts = kl_1m['ts']
        _ei = 0
        for mi in range(len(entry_ts)):
            while _ei < len(_1m_ts) - 1 and _1m_ts[_ei + 1] <= entry_ts[mi]:
                _ei += 1
            k_1m[mi] = k1_raw[_ei]
            d_1m[mi] = d1_raw[_ei]
    trades: List[Trade] = []
    in_trade = False
    current_trade: Optional[Trade] = None
    max_hold = params.get('max_hold_bars', 200)
    stop_loss_pct = params.get('stop_loss_pct', -3.0)
    take_profit_pct = params.get('take_profit_pct', 1.5)
    k_entry_threshold = params.get('k_entry_threshold', 40)  # K must be below this for LONG entry
    k_exit_threshold = params.get('k_exit_threshold', 60)  # K must be above this for SHORT entry
    require_k1m = params.get('require_k1m', False)
    require_confirm = params.get('require_confirm', True)
    warmup = max(STOCH_LEN * 3, 30)
    for i in range(warmup, len(close_e)):
        price = close_e[i]
        if in_trade:
            if current_trade.side == 'LONG':
                pnl = (price - current_trade.entry_price) / current_trade.entry_price * 100
                # EXIT: lower_high on entry TF (structure break)
                structure_break = patterns_e['lower_high'][i] and k_e[i] < k_e[i - 1]
                # EXIT: k crosses below d on entry TF
                k_cross_under = k_e[i] < d_e[i] and k_e[i - 1] >= d_e[i - 1]
            else:
                pnl = (current_trade.entry_price - price) / current_trade.entry_price * 100
                structure_break = patterns_e['higher_low'][i] and k_e[i] > k_e[i - 1]
                k_cross_under = k_e[i] > d_e[i] and k_e[i - 1] <= d_e[i - 1]
            hold = i - current_trade.entry_bar
            exit_reason = ""
            if pnl <= stop_loss_pct: exit_reason = f"STOP_LOSS_{pnl:.1f}%"
            elif pnl >= take_profit_pct: exit_reason = f"TAKE_PROFIT_{pnl:.1f}%"
            elif hold >= max_hold: exit_reason = f"MAX_HOLD_{hold}bars"
            elif strategy == 'structure_break' and structure_break: exit_reason = f"STRUCTURE_BREAK_{pnl:.1f}%"
            elif strategy == 'k_crossover' and k_cross_under: exit_reason = f"K_CROSS_{pnl:.1f}%"
            elif strategy == 'combined' and (structure_break or k_cross_under): exit_reason = f"COMBINED_EXIT_{pnl:.1f}%"
            if exit_reason:
                current_trade.exit_price = price
                current_trade.exit_bar = i
                current_trade.exit_reason = exit_reason
                current_trade.pnl_pct = pnl - (FEE_PCT * 2)
                current_trade.hold_bars = hold
                trades.append(current_trade)
                in_trade = False
                current_trade = None
                continue
        else:
            # LONG entry: higher_low + k rising + k < threshold (not overbought)
            long_structure = patterns_e['higher_low'][i]
            long_k_ok = k_e[i] > d_e[i] and k_e[i] < k_entry_threshold + 20 and k_e[i] > k_e[i - 1]
            long_confirm = (not require_confirm) or (k_c[i] > d_c[i])
            long_k1m = (not require_k1m) or (k_1m[i] > d_1m[i])
            # SHORT entry: lower_high + k falling + k > threshold (not oversold)
            short_structure = patterns_e['lower_high'][i]
            short_k_ok = k_e[i] < d_e[i] and k_e[i] > k_exit_threshold - 20 and k_e[i] < k_e[i - 1]
            short_confirm = (not require_confirm) or (k_c[i] < d_c[i])
            short_k1m = (not require_k1m) or (k_1m[i] < d_1m[i])
            if long_structure and long_k_ok and long_confirm and long_k1m:
                current_trade = Trade(symbol=symbol, side='LONG', entry_price=price, entry_bar=i, entry_reason=f"HL_K{k_e[i]:.0f}_K15={k_c[i]:.0f}")
                in_trade = True
            elif short_structure and short_k_ok and short_confirm and short_k1m:
                current_trade = Trade(symbol=symbol, side='SHORT', entry_price=price, entry_bar=i, entry_reason=f"LH_K{k_e[i]:.0f}_K15={k_c[i]:.0f}")
                in_trade = True
    return trades

# ═══ Main ═══
def main():
    symbols_file = BASE / "symbols.json"
    if symbols_file.exists():
        with open(symbols_file) as f:
            all_symbols = json.load(f)
    else:
        all_symbols = [p.stem.replace('_3m', '') for p in KLINES_DIR.glob('*_3m.json')]
    # Use top volume symbols for speed
    test_symbols = []
    for sym in all_symbols:
        kp = KLINES_DIR / f"{sym}_3m.json"
        if kp.exists():
            test_symbols.append(sym)
        if len(test_symbols) >= 50: break
    print(f"Testing {len(test_symbols)} symbols")
    print("=" * 120)
    strategies = [
        ('structure_break', 'Close on lower-high (LONG) / higher-low (SHORT)'),
        ('k_crossover', 'Close on K crossing D'),
        ('combined', 'Close on structure break OR K cross'),
    ]
    param_sets = [
        {'entry_tf': '3m', 'confirm_tf': '15m', 'require_k1m': False, 'require_confirm': True, 'max_hold_bars': 150, 'stop_loss_pct': -2.0, 'take_profit_pct': 1.0, 'k_entry_threshold': 40, 'k_exit_threshold': 60, 'label': '3m+15m_confirm_SL2_TP1'},
        {'entry_tf': '3m', 'confirm_tf': '15m', 'require_k1m': True, 'require_confirm': True, 'max_hold_bars': 150, 'stop_loss_pct': -2.0, 'take_profit_pct': 1.0, 'k_entry_threshold': 40, 'k_exit_threshold': 60, 'label': '3m+15m+1m_confirm_SL2_TP1'},
        {'entry_tf': '3m', 'confirm_tf': '15m', 'require_k1m': True, 'require_confirm': True, 'max_hold_bars': 100, 'stop_loss_pct': -1.0, 'take_profit_pct': 0.5, 'k_entry_threshold': 35, 'k_exit_threshold': 65, 'label': '3m+15m+1m_tight_SL1_TP05'},
        {'entry_tf': '3m', 'confirm_tf': '15m', 'require_k1m': False, 'require_confirm': False, 'max_hold_bars': 150, 'stop_loss_pct': -2.0, 'take_profit_pct': 1.0, 'k_entry_threshold': 40, 'k_exit_threshold': 60, 'label': '3m_only_SL2_TP1'},
        {'entry_tf': '1m', 'confirm_tf': '3m', 'require_k1m': False, 'require_confirm': True, 'max_hold_bars': 200, 'stop_loss_pct': -1.5, 'take_profit_pct': 0.8, 'k_entry_threshold': 40, 'k_exit_threshold': 60, 'label': '1m+3m_confirm_SL15_TP08'},
        {'entry_tf': '3m', 'confirm_tf': '15m', 'require_k1m': True, 'require_confirm': True, 'max_hold_bars': 200, 'stop_loss_pct': -3.0, 'take_profit_pct': 1.5, 'k_entry_threshold': 45, 'k_exit_threshold': 55, 'label': '3m+15m+1m_wide_SL3_TP15'},
    ]
    results = []
    for strat_name, strat_desc in strategies:
        for params in param_sets:
            label = f"{strat_name}_{params['label']}"
            all_trades: List[Trade] = []
            t0 = time.time()
            for sym in test_symbols:
                trades = run_strategy(sym, strat_name, params)
                all_trades.extend(trades)
            elapsed = time.time() - t0
            if not all_trades:
                results.append({'label': label, 'trades': 0, 'win_rate': 0, 'avg_pnl': 0, 'total_pnl': 0, 'pf': 0, 'avg_hold': 0, 'max_dd': 0, 'sharpe': 0})
                continue
            pnls = [t.pnl_pct for t in all_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            total_pnl = sum(pnls)
            win_rate = len(wins) / len(pnls) * 100
            avg_pnl = np.mean(pnls)
            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 0.001
            pf = gross_profit / gross_loss
            avg_hold = np.mean([t.hold_bars for t in all_trades])
            # Max drawdown
            equity = np.cumsum(pnls)
            peak = np.maximum.accumulate(equity)
            dd = equity - peak
            max_dd = np.min(dd) if len(dd) > 0 else 0
            # Sharpe (annualized assuming 480 trades/day for 3m)
            sharpe = (np.mean(pnls) / np.std(pnls) * np.sqrt(480)) if np.std(pnls) > 0 else 0
            longs = [t for t in all_trades if t.side == 'LONG']
            shorts = [t for t in all_trades if t.side == 'SHORT']
            long_wr = len([t for t in longs if t.pnl_pct > 0]) / max(len(longs), 1) * 100
            short_wr = len([t for t in shorts if t.pnl_pct > 0]) / max(len(shorts), 1) * 100
            # Exit reason breakdown
            exit_reasons = {}
            for t in all_trades:
                reason = t.exit_reason.split('_')[0] + '_' + t.exit_reason.split('_')[1] if '_' in t.exit_reason else t.exit_reason
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
            results.append({'label': label, 'trades': len(all_trades), 'win_rate': win_rate, 'avg_pnl': avg_pnl, 'total_pnl': total_pnl, 'pf': pf, 'avg_hold': avg_hold, 'max_dd': max_dd, 'sharpe': sharpe, 'long_wr': long_wr, 'short_wr': short_wr, 'longs': len(longs), 'shorts': len(shorts), 'exit_reasons': exit_reasons, 'elapsed': elapsed})
    # Print results
    print(f"\n{'Strategy':<55s} {'Trades':>6s} {'WR%':>6s} {'AvgPnL':>7s} {'TotPnL':>8s} {'PF':>5s} {'AvgHold':>7s} {'MaxDD':>7s} {'Sharpe':>7s} {'L_WR':>5s} {'S_WR':>5s}")
    print("-" * 130)
    results.sort(key=lambda x: x.get('total_pnl', 0), reverse=True)
    for r in results:
        if r['trades'] == 0:
            print(f"{r['label']:<55s} {'0':>6s}")
            continue
        print(f"{r['label']:<55s} {r['trades']:>6d} {r['win_rate']:>5.1f}% {r['avg_pnl']:>+6.3f}% {r['total_pnl']:>+7.2f}% {r['pf']:>5.2f} {r['avg_hold']:>6.1f}b {r['max_dd']:>+6.2f}% {r['sharpe']:>6.2f} {r.get('long_wr',0):>4.0f}% {r.get('short_wr',0):>4.0f}%")
    # Top 5 detailed
    print(f"\n{'='*130}")
    print("TOP 5 — Exit Reason Breakdown:")
    for r in results[:5]:
        if r['trades'] == 0: continue
        print(f"\n  {r['label']} ({r['trades']} trades, {r.get('elapsed',0):.1f}s):")
        for reason, count in sorted(r.get('exit_reasons', {}).items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count} ({count/r['trades']*100:.1f}%)")
    # Save CSV
    csv_path = BASE / "backtest_candle_results.csv"
    with open(csv_path, 'w') as f:
        f.write("strategy,trades,win_rate,avg_pnl,total_pnl,profit_factor,avg_hold_bars,max_drawdown,sharpe,long_wr,short_wr,longs,shorts\n")
        for r in results:
            f.write(f"{r['label']},{r['trades']},{r.get('win_rate',0):.2f},{r.get('avg_pnl',0):.4f},{r.get('total_pnl',0):.2f},{r.get('pf',0):.3f},{r.get('avg_hold',0):.1f},{r.get('max_dd',0):.2f},{r.get('sharpe',0):.3f},{r.get('long_wr',0):.1f},{r.get('short_wr',0):.1f},{r.get('longs',0)},{r.get('shorts',0)}\n")
    print(f"\nResults saved to {csv_path}")

if __name__ == '__main__':
    main()
