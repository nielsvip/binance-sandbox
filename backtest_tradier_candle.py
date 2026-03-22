#!/usr/bin/env python3
"""Tradier candle-pattern backtest across ALL timeframes.
Tests: structure entry/exit (higher_low/lower_high) with stoch confirmation.
Special focus on higher TFs (1h/4h/D) for stocks due to overnight gaps.
Also tests gap-fill patterns specific to stocks."""
import json, os, time, sys
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from pathlib import Path

BASE = Path(os.path.dirname(os.path.abspath(__file__)))
KLINES_DIR = BASE / "klines_cache" / "tradier"
FEE_PCT = 0.0  # Tradier commission-free
STOCH_LEN = 14
STOCH_K = 5
STOCH_D = 5

def load_klines(symbol: str, tf: str) -> Optional[np.ndarray]:
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    with open(path) as f:
        raw = json.load(f)
    if not raw or len(raw) < 50: return None
    cleaned = []
    for r in raw:
        ts = r.get('timestamp', r.get('time', r.get('close_time', '')))
        o = r.get('open', 0)
        h = r.get('high', 0)
        l = r.get('low', 0)
        c = r.get('close', 0)
        v = r.get('volume', 0)
        try:
            o, h, l, c, v = float(o), float(h), float(l), float(c), float(v)
        except (TypeError, ValueError):
            continue
        if not ts or o <= 0 or c <= 0: continue
        cleaned.append((str(ts), float(o), float(h), float(l), float(c), float(v)))
    if len(cleaned) < 50: return None
    dtype = [('ts', 'U30'), ('o', 'f8'), ('h', 'f8'), ('l', 'f8'), ('c', 'f8'), ('v', 'f8')]
    return np.array(cleaned, dtype=dtype)

def rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)
    if len(close) <= period: return np.full_like(close, 50.0)
    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(close) * 50, where=avg_loss > 0)
    return 100 - (100 / (1 + rs))

def stoch_rsi(close, length=14, k=5, d=5):
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

@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    entry_bar: int
    entry_reason: str
    exit_price: float = 0.0
    exit_bar: int = 0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    hold_bars: int = 0
    gap_pct: float = 0.0

def detect_gaps(open_arr, close_arr, high_arr, low_arr):
    """Detect overnight gaps (stock-specific). Gap up: open > prev high. Gap down: open < prev low."""
    n = len(open_arr)
    gap_up = np.zeros(n, dtype=bool)
    gap_down = np.zeros(n, dtype=bool)
    gap_pct = np.zeros(n, dtype='f8')
    for i in range(1, n):
        if open_arr[i] > high_arr[i - 1] * 1.001:
            gap_up[i] = True
            gap_pct[i] = (open_arr[i] - close_arr[i - 1]) / close_arr[i - 1] * 100
        elif open_arr[i] < low_arr[i - 1] * 0.999:
            gap_down[i] = True
            gap_pct[i] = (open_arr[i] - close_arr[i - 1]) / close_arr[i - 1] * 100
    return gap_up, gap_down, gap_pct

def run_strategy(symbol, strategy, params):
    tf = params.get('entry_tf', '15m')
    confirm_tf = params.get('confirm_tf', '')
    kl = load_klines(symbol, tf)
    if kl is None: return []
    close = kl['c']; high = kl['h']; low = kl['l']; opn = kl['o']
    k, d = stoch_rsi(close, STOCH_LEN, STOCH_K, STOCH_D)
    # Candle structure
    n = len(close)
    higher_low = np.zeros(n, dtype=bool)
    lower_high = np.zeros(n, dtype=bool)
    for i in range(2, n):
        higher_low[i] = low[i] > low[i - 1] and low[i - 1] > 0
        lower_high[i] = high[i] < high[i - 1] and high[i - 1] > 0
    # Gaps
    gap_up, gap_down, gap_pct_arr = detect_gaps(opn, close, high, low)
    # Confirmation TF stoch
    k_c = np.full(n, 50.0)
    d_c = np.full(n, 50.0)
    if confirm_tf:
        kl_c = load_klines(symbol, confirm_tf)
        if kl_c is not None and len(kl_c) > 50:
            kc_raw, dc_raw = stoch_rsi(kl_c['c'], STOCH_LEN, STOCH_K, STOCH_D)
            c_ts = kl_c['ts']
            e_ts = kl['ts']
            ci = 0
            for ei in range(n):
                while ci < len(c_ts) - 1 and c_ts[ci + 1] <= e_ts[ei]:
                    ci += 1
                k_c[ei] = kc_raw[ci]
                d_c[ei] = dc_raw[ci]
    trades = []
    in_trade = False
    current = None
    max_hold = params.get('max_hold_bars', 50)
    sl = params.get('stop_loss_pct', -3.0)
    tp = params.get('take_profit_pct', 2.0)
    require_confirm = params.get('require_confirm', False)
    gap_filter = params.get('gap_filter', False)
    warmup = max(STOCH_LEN * 3, 30)
    for i in range(warmup, n):
        price = close[i]
        if in_trade:
            if current.side == 'LONG':
                pnl = (price - current.entry_price) / current.entry_price * 100
                struct_break = lower_high[i] and k[i] < k[i - 1]
            else:
                pnl = (current.entry_price - price) / current.entry_price * 100
                struct_break = higher_low[i] and k[i] > k[i - 1]
            hold = i - current.entry_bar
            reason = ""
            if pnl <= sl: reason = f"STOP_LOSS"
            elif pnl >= tp: reason = f"TAKE_PROFIT"
            elif hold >= max_hold: reason = f"MAX_HOLD"
            elif strategy in ('structure', 'gap_structure') and struct_break: reason = f"STRUCT_BREAK"
            # Gap exit: if gap against position on next bar
            if not reason and strategy == 'gap_structure':
                if current.side == 'LONG' and gap_down[i]: reason = "GAP_DOWN_EXIT"
                elif current.side == 'SHORT' and gap_up[i]: reason = "GAP_UP_EXIT"
            if reason:
                current.exit_price = price
                current.exit_bar = i
                current.exit_reason = reason
                current.pnl_pct = pnl
                current.hold_bars = hold
                trades.append(current)
                in_trade = False
                current = None
        else:
            # Entry conditions
            long_struct = higher_low[i]
            long_k = k[i] > d[i] and k[i] < 70 and k[i] > k[i - 1]
            long_confirm = (not require_confirm) or (k_c[i] > d_c[i])
            short_struct = lower_high[i]
            short_k = k[i] < d[i] and k[i] > 30 and k[i] < k[i - 1]
            short_confirm = (not require_confirm) or (k_c[i] < d_c[i])
            # Gap filter: for gap_structure, also enter on gap fills
            if strategy == 'gap_structure':
                if gap_down[i] and abs(gap_pct_arr[i]) > 0.3 and k[i] < 30:
                    long_struct = True  # Gap down + oversold = gap fill long
                if gap_up[i] and abs(gap_pct_arr[i]) > 0.3 and k[i] > 70:
                    short_struct = True  # Gap up + overbought = gap fill short
            if long_struct and long_k and long_confirm:
                current = Trade(symbol=symbol, side='LONG', entry_price=price, entry_bar=i, entry_reason=f"HL_K{k[i]:.0f}", gap_pct=gap_pct_arr[i])
                in_trade = True
            elif short_struct and short_k and short_confirm:
                current = Trade(symbol=symbol, side='SHORT', entry_price=price, entry_bar=i, entry_reason=f"LH_K{k[i]:.0f}", gap_pct=gap_pct_arr[i])
                in_trade = True
    return trades

def main():
    sym_file = BASE / "symbols_tradier.json"
    if sym_file.exists():
        with open(sym_file) as f:
            all_symbols = json.load(f)
    else:
        all_symbols = list(set(p.stem.split('_')[0] for p in KLINES_DIR.glob('*_D.json')))
    test_symbols = []
    for sym in all_symbols:
        if (KLINES_DIR / f"{sym}_D.json").exists():
            test_symbols.append(sym)
        if len(test_symbols) >= 80: break
    print(f"Testing {len(test_symbols)} stock symbols across ALL timeframes")
    print("=" * 140)
    configs = [
        # TF, confirm_tf, label, max_hold, sl, tp, gap
        ('1m', '5m', 'structure', '1m+5m_confirm', 200, -1.5, 0.8, False),
        ('5m', '15m', 'structure', '5m+15m_confirm', 100, -2.0, 1.0, False),
        ('5m', '', 'structure', '5m_only', 100, -2.0, 1.0, False),
        ('15m', '1h', 'structure', '15m+1h_confirm', 60, -2.0, 1.5, False),
        ('15m', '', 'structure', '15m_only', 60, -2.0, 1.5, False),
        ('1h', '4h', 'structure', '1h+4h_confirm', 30, -3.0, 2.0, False),
        ('1h', '', 'structure', '1h_only', 30, -3.0, 2.0, False),
        ('4h', 'D', 'structure', '4h+D_confirm', 20, -4.0, 3.0, False),
        ('4h', '', 'structure', '4h_only', 20, -4.0, 3.0, False),
        ('D', '', 'structure', 'D_only', 15, -5.0, 4.0, False),
        ('D', '', 'structure', 'D_tight_SL3_TP2', 10, -3.0, 2.0, False),
        # Gap-fill strategies (stock-specific)
        ('15m', '1h', 'gap_structure', '15m+1h_gap_fill', 60, -2.0, 1.5, True),
        ('1h', '4h', 'gap_structure', '1h+4h_gap_fill', 30, -3.0, 2.0, True),
        ('D', '', 'gap_structure', 'D_gap_fill', 15, -5.0, 4.0, True),
    ]
    results = []
    for entry_tf, confirm_tf, strat, label, max_hold, sl, tp, gap in configs:
        params = {'entry_tf': entry_tf, 'confirm_tf': confirm_tf, 'require_confirm': bool(confirm_tf), 'max_hold_bars': max_hold, 'stop_loss_pct': sl, 'take_profit_pct': tp, 'gap_filter': gap}
        full_label = f"{strat}_{label}"
        all_trades = []
        t0 = time.time()
        for sym in test_symbols:
            trades = run_strategy(sym, strat, params)
            all_trades.extend(trades)
        elapsed = time.time() - t0
        if not all_trades:
            results.append({'label': full_label, 'tf': entry_tf, 'trades': 0})
            continue
        pnls = [t.pnl_pct for t in all_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        wr = len(wins) / len(pnls) * 100
        avg_pnl = np.mean(pnls)
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        pf = gp / gl
        avg_hold = np.mean([t.hold_bars for t in all_trades])
        equity = np.cumsum(pnls)
        peak = np.maximum.accumulate(equity)
        max_dd = np.min(equity - peak) if len(equity) > 0 else 0
        std = np.std(pnls) if len(pnls) > 1 else 0.001
        sharpe = (avg_pnl / std * np.sqrt(252)) if std > 0 else 0
        longs = [t for t in all_trades if t.side == 'LONG']
        shorts = [t for t in all_trades if t.side == 'SHORT']
        l_wr = len([t for t in longs if t.pnl_pct > 0]) / max(len(longs), 1) * 100
        s_wr = len([t for t in shorts if t.pnl_pct > 0]) / max(len(shorts), 1) * 100
        # Gap trades analysis
        gap_trades = [t for t in all_trades if abs(t.gap_pct) > 0.3]
        gap_wr = len([t for t in gap_trades if t.pnl_pct > 0]) / max(len(gap_trades), 1) * 100 if gap_trades else 0
        gap_avg = np.mean([t.pnl_pct for t in gap_trades]) if gap_trades else 0
        exit_reasons = {}
        for t in all_trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
        results.append({'label': full_label, 'tf': entry_tf, 'trades': len(all_trades), 'wr': wr, 'avg_pnl': avg_pnl, 'total_pnl': total_pnl, 'pf': pf, 'avg_hold': avg_hold, 'max_dd': max_dd, 'sharpe': sharpe, 'l_wr': l_wr, 's_wr': s_wr, 'longs': len(longs), 'shorts': len(shorts), 'gap_trades': len(gap_trades), 'gap_wr': gap_wr, 'gap_avg': gap_avg, 'exit_reasons': exit_reasons, 'elapsed': elapsed})
    # Print
    print(f"\n{'Strategy':<45s} {'TF':>4s} {'Trades':>7s} {'WR%':>6s} {'AvgPnL':>8s} {'TotPnL':>9s} {'PF':>6s} {'Hold':>6s} {'MaxDD':>8s} {'Sharpe':>7s} {'L_WR':>5s} {'S_WR':>5s} {'Gaps':>5s} {'GapWR':>5s}")
    print("-" * 140)
    results.sort(key=lambda x: x.get('pf', 0), reverse=True)
    for r in results:
        if r['trades'] == 0:
            print(f"{r['label']:<45s} {r['tf']:>4s} {'0':>7s}")
            continue
        print(f"{r['label']:<45s} {r['tf']:>4s} {r['trades']:>7d} {r['wr']:>5.1f}% {r['avg_pnl']:>+7.3f}% {r['total_pnl']:>+8.1f}% {r['pf']:>5.2f} {r['avg_hold']:>5.1f}b {r['max_dd']:>+7.1f}% {r['sharpe']:>6.2f} {r.get('l_wr',0):>4.0f}% {r.get('s_wr',0):>4.0f}% {r.get('gap_trades',0):>5d} {r.get('gap_wr',0):>4.0f}%")
    # Top 5 exit breakdown
    print(f"\n{'='*140}")
    print("TOP 5 — Exit Reason Breakdown:")
    for r in results[:5]:
        if r['trades'] == 0: continue
        print(f"\n  {r['label']} (TF={r['tf']}, {r['trades']} trades, {r.get('elapsed',0):.1f}s):")
        for reason, count in sorted(r.get('exit_reasons', {}).items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count} ({count/r['trades']*100:.1f}%)")
    # Save CSV
    csv_path = BASE / "backtest_tradier_candle_results.csv"
    with open(csv_path, 'w') as f:
        f.write("strategy,timeframe,trades,win_rate,avg_pnl,total_pnl,profit_factor,avg_hold,max_dd,sharpe,long_wr,short_wr,gap_trades,gap_wr,gap_avg_pnl\n")
        for r in results:
            f.write(f"{r['label']},{r.get('tf','')},{r['trades']},{r.get('wr',0):.2f},{r.get('avg_pnl',0):.4f},{r.get('total_pnl',0):.2f},{r.get('pf',0):.3f},{r.get('avg_hold',0):.1f},{r.get('max_dd',0):.2f},{r.get('sharpe',0):.3f},{r.get('l_wr',0):.1f},{r.get('s_wr',0):.1f},{r.get('gap_trades',0)},{r.get('gap_wr',0):.1f},{r.get('gap_avg',0):.4f}\n")
    print(f"\nResults saved to {csv_path}")

if __name__ == '__main__':
    main()
