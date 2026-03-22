#!/usr/bin/env python3
"""14-hour backtest marathon: cycles through variations every ~20 minutes.
Appends results to backtest_marathon_results.csv and updates backtest_changes_100.xlsx.
Tests: stoch params, hold times, SL/TP ratios, TF combos, candle patterns, volume filters,
gap fills, DC channel entries, multi-TF alignment, HA candle filters, and more."""
import json, os, time, sys, signal, traceback
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(os.path.dirname(os.path.abspath(__file__)))
KLINES_DIR_CRYPTO = BASE / "klines_cache" / "consolidated_klines"
KLINES_DIR_TRADIER = BASE / "klines_cache" / "tradier"
CSV_PATH = BASE / "backtest_marathon_results.csv"
XLSX_PATH = BASE / "backtest_changes_100.xlsx"
STOP_FILE = BASE / ".stop_marathon"

# ═══ Shared engine ═══
def load_klines(symbol, tf, market='crypto'):
    kdir = KLINES_DIR_CRYPTO if market == 'crypto' else KLINES_DIR_TRADIER
    path = kdir / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    with open(path) as f:
        raw = json.load(f)
    if not raw or len(raw) < 50: return None
    cleaned = []
    for r in raw:
        ts = r.get('timestamp', r.get('time', r.get('close_time', '')))
        try:
            o, h, l, c, v = float(r.get('open', 0)), float(r.get('high', 0)), float(r.get('low', 0)), float(r.get('close', 0)), float(r.get('volume', 0))
        except (TypeError, ValueError):
            continue
        if not ts or o <= 0 or c <= 0: continue
        cleaned.append((str(ts), o, h, l, c, v))
    if len(cleaned) < 50: return None
    dtype = [('ts', 'U30'), ('o', 'f8'), ('h', 'f8'), ('l', 'f8'), ('c', 'f8'), ('v', 'f8')]
    return np.array(cleaned, dtype=dtype)

def rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = np.zeros_like(close); al = np.zeros_like(close)
    if len(close) <= period: return np.full_like(close, 50.0)
    ag[period] = np.mean(gain[1:period + 1]); al[period] = np.mean(loss[1:period + 1])
    for i in range(period + 1, len(close)):
        ag[i] = (ag[i-1] * (period-1) + gain[i]) / period
        al[i] = (al[i-1] * (period-1) + loss[i]) / period
    rs = np.divide(ag, al, out=np.ones_like(close)*50, where=al > 0)
    return 100 - (100 / (1 + rs))

def stoch_rsi(close, length=14, k=5, d=5):
    r = rsi(close, length)
    n = len(r)
    stoch = np.full(n, 50.0)
    for i in range(length, n):
        lo = np.min(r[i-length+1:i+1]); hi = np.max(r[i-length+1:i+1])
        stoch[i] = ((r[i]-lo)/(hi-lo)*100) if hi > lo else 50.0
    k_line = np.convolve(stoch, np.ones(k)/k, mode='same')
    d_line = np.convolve(k_line, np.ones(d)/d, mode='same')
    return k_line, d_line

def ema(close, period):
    result = np.zeros_like(close)
    result[0] = close[0]
    m = 2.0 / (period + 1)
    for i in range(1, len(close)):
        result[i] = close[i] * m + result[i-1] * (1 - m)
    return result

def heiken_ashi(o, h, l, c):
    ha_c = (o + h + l + c) / 4
    ha_o = np.zeros_like(o); ha_o[0] = o[0]
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i-1] + ha_c[i-1]) / 2
    ha_h = np.maximum(h, np.maximum(ha_o, ha_c))
    ha_l = np.minimum(l, np.minimum(ha_o, ha_c))
    ha_green = ha_c > ha_o
    return ha_o, ha_h, ha_l, ha_c, ha_green

def rel_volume(vol, lookback=20):
    rv = np.ones_like(vol)
    for i in range(lookback, len(vol)):
        avg = np.mean(vol[i-lookback:i])
        rv[i] = vol[i] / avg if avg > 0 else 1.0
    return rv

@dataclass
class Trade:
    symbol: str; side: str; entry_price: float; entry_bar: int; entry_reason: str
    exit_price: float = 0.0; exit_bar: int = 0; exit_reason: str = ""; pnl_pct: float = 0.0; hold_bars: int = 0

def run_backtest(symbols, market, params):
    entry_tf = params['entry_tf']
    confirm_tf = params.get('confirm_tf', '')
    stoch_len = params.get('stoch_len', 14)
    stoch_k = params.get('stoch_k', 5)
    stoch_d = params.get('stoch_d', 5)
    max_hold = params.get('max_hold', 150)
    sl = params.get('sl', -2.0)
    tp = params.get('tp', 1.0)
    fee = params.get('fee', 0.04)
    exit_mode = params.get('exit_mode', 'structure')
    entry_mode = params.get('entry_mode', 'structure')
    require_ha = params.get('require_ha', False)
    require_vol = params.get('require_vol', 0)
    ema_filter = params.get('ema_filter', 0)
    k_entry_max = params.get('k_entry_max', 70)
    k_entry_min = params.get('k_entry_min', 30)
    all_trades = []
    for sym in symbols:
        kl = load_klines(sym, entry_tf, market)
        if kl is None: continue
        c = kl['c']; h = kl['h']; l = kl['l']; o = kl['o']; v = kl['v']
        n = len(c)
        k, d = stoch_rsi(c, stoch_len, stoch_k, stoch_d)
        # Confirm TF
        kc, dc = np.full(n, 50.0), np.full(n, 50.0)
        if confirm_tf:
            klc = load_klines(sym, confirm_tf, market)
            if klc is not None and len(klc) > 50:
                kc_r, dc_r = stoch_rsi(klc['c'], stoch_len, stoch_k, stoch_d)
                cts = klc['ts']; ets = kl['ts']; ci = 0
                for ei in range(n):
                    while ci < len(cts)-1 and cts[ci+1] <= ets[ei]: ci += 1
                    kc[ei] = kc_r[ci]; dc[ei] = dc_r[ci]
        # Optional filters
        ha_green = np.ones(n, dtype=bool)
        if require_ha:
            _, _, _, _, ha_green = heiken_ashi(o, h, l, c)
        rv = rel_volume(v) if require_vol > 0 else np.ones(n)
        ema_line = ema(c, ema_filter) if ema_filter > 0 else np.zeros(n)
        # Candle structure
        hl = np.zeros(n, dtype=bool); lh = np.zeros(n, dtype=bool)
        for i in range(2, n):
            hl[i] = l[i] > l[i-1] and l[i-1] > 0
            lh[i] = h[i] < h[i-1] and h[i-1] > 0
        warmup = max(stoch_len * 3, 30)
        in_trade = False; cur = None
        for i in range(warmup, n):
            price = c[i]
            if in_trade:
                pnl = ((price - cur.entry_price) / cur.entry_price * 100) if cur.side == 'LONG' else ((cur.entry_price - price) / cur.entry_price * 100)
                hold = i - cur.entry_bar
                reason = ""
                if pnl <= sl: reason = "SL"
                elif pnl >= tp: reason = "TP"
                elif hold >= max_hold: reason = "MAX_HOLD"
                elif exit_mode == 'structure':
                    if cur.side == 'LONG' and lh[i] and k[i] < k[i-1]: reason = "STRUCT"
                    elif cur.side == 'SHORT' and hl[i] and k[i] > k[i-1]: reason = "STRUCT"
                elif exit_mode == 'k_cross':
                    if cur.side == 'LONG' and k[i] < d[i] and k[i-1] >= d[i-1]: reason = "K_CROSS"
                    elif cur.side == 'SHORT' and k[i] > d[i] and k[i-1] <= d[i-1]: reason = "K_CROSS"
                elif exit_mode == 'structure_or_kcross':
                    if cur.side == 'LONG' and ((lh[i] and k[i] < k[i-1]) or (k[i] < d[i] and k[i-1] >= d[i-1])): reason = "COMBINED"
                    elif cur.side == 'SHORT' and ((hl[i] and k[i] > k[i-1]) or (k[i] > d[i] and k[i-1] <= d[i-1])): reason = "COMBINED"
                elif exit_mode == 'ha_flip':
                    if cur.side == 'LONG' and not ha_green[i] and ha_green[i-1]: reason = "HA_FLIP"
                    elif cur.side == 'SHORT' and ha_green[i] and not ha_green[i-1]: reason = "HA_FLIP"
                elif exit_mode == 'structure_ha':
                    if cur.side == 'LONG' and lh[i] and k[i] < k[i-1] and not ha_green[i]: reason = "STRUCT_HA"
                    elif cur.side == 'SHORT' and hl[i] and k[i] > k[i-1] and ha_green[i]: reason = "STRUCT_HA"
                if reason:
                    cur.exit_price = price; cur.exit_bar = i; cur.exit_reason = reason
                    cur.pnl_pct = pnl - (fee * 2); cur.hold_bars = hold
                    all_trades.append(cur); in_trade = False; cur = None
            else:
                vol_ok = require_vol <= 0 or rv[i] >= require_vol
                ema_long_ok = ema_filter <= 0 or c[i] > ema_line[i]
                ema_short_ok = ema_filter <= 0 or c[i] < ema_line[i]
                if entry_mode == 'structure':
                    long_ok = hl[i] and k[i] > d[i] and k[i] < k_entry_max and k[i] > k[i-1]
                    short_ok = lh[i] and k[i] < d[i] and k[i] > k_entry_min and k[i] < k[i-1]
                elif entry_mode == 'k_cross':
                    long_ok = k[i] > d[i] and k[i-1] <= d[i-1] and k[i] < k_entry_max
                    short_ok = k[i] < d[i] and k[i-1] >= d[i-1] and k[i] > k_entry_min
                elif entry_mode == 'structure_ha':
                    long_ok = hl[i] and k[i] > d[i] and k[i] < k_entry_max and ha_green[i]
                    short_ok = lh[i] and k[i] < d[i] and k[i] > k_entry_min and not ha_green[i]
                elif entry_mode == 'dc_break':
                    # DC channel breakout: price > prev high (long) or < prev low (short)
                    long_ok = c[i] > h[i-1] and k[i] > d[i]
                    short_ok = c[i] < l[i-1] and k[i] < d[i]
                else:
                    long_ok = short_ok = False
                confirm_long = not confirm_tf or kc[i] > dc[i]
                confirm_short = not confirm_tf or kc[i] < dc[i]
                if long_ok and confirm_long and vol_ok and ema_long_ok and (not require_ha or ha_green[i]):
                    cur = Trade(symbol=sym, side='LONG', entry_price=price, entry_bar=i, entry_reason=f"L_k{k[i]:.0f}")
                    in_trade = True
                elif short_ok and confirm_short and vol_ok and ema_short_ok and (not require_ha or not ha_green[i]):
                    cur = Trade(symbol=sym, side='SHORT', entry_price=price, entry_bar=i, entry_reason=f"S_k{k[i]:.0f}")
                    in_trade = True
    return all_trades

def compute_metrics(trades, label, params):
    if not trades: return {'label': label, 'trades': 0, 'wr': 0, 'avg_pnl': 0, 'total_pnl': 0, 'pf': 0, 'sharpe': 0, 'max_dd': 0, 'avg_hold': 0, 'params': str(params)}
    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    gp = sum(wins) if wins else 0; gl = abs(sum(losses)) if losses else 0.001
    eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq)
    std = np.std(pnls) if len(pnls) > 1 else 0.001
    return {
        'label': label, 'trades': len(trades), 'wr': len(wins)/len(pnls)*100,
        'avg_pnl': np.mean(pnls), 'total_pnl': sum(pnls), 'pf': gp/gl,
        'sharpe': (np.mean(pnls)/std*np.sqrt(252)) if std > 0 else 0,
        'max_dd': np.min(eq - peak) if len(eq) > 0 else 0,
        'avg_hold': np.mean([t.hold_bars for t in trades]),
        'l_wr': len([t for t in trades if t.side=='LONG' and t.pnl_pct>0])/max(len([t for t in trades if t.side=='LONG']),1)*100,
        's_wr': len([t for t in trades if t.side=='SHORT' and t.pnl_pct>0])/max(len([t for t in trades if t.side=='SHORT']),1)*100,
        'params': json.dumps(params, default=str),
    }

def append_csv(result):
    header_needed = not CSV_PATH.exists()
    with open(CSV_PATH, 'a') as f:
        if header_needed:
            f.write("timestamp,label,market,trades,wr,avg_pnl,total_pnl,pf,sharpe,max_dd,avg_hold,l_wr,s_wr,params\n")
        f.write(f"{datetime.now(timezone.utc).isoformat()},{result['label']},{result.get('market','')},{result['trades']},{result.get('wr',0):.2f},{result.get('avg_pnl',0):.4f},{result.get('total_pnl',0):.2f},{result.get('pf',0):.3f},{result.get('sharpe',0):.3f},{result.get('max_dd',0):.2f},{result.get('avg_hold',0):.1f},{result.get('l_wr',0):.1f},{result.get('s_wr',0):.1f},{result.get('params','')}\n")

def get_symbols(market, limit=50):
    if market == 'crypto':
        sf = BASE / "symbols.json"
        if sf.exists():
            with open(sf) as f: syms = json.load(f)
        else:
            syms = [p.stem.replace('_3m','') for p in KLINES_DIR_CRYPTO.glob('*_3m.json')]
    else:
        sf = BASE / "symbols_tradier.json"
        if sf.exists():
            with open(sf) as f: syms = json.load(f)
        else:
            syms = list(set(p.stem.split('_')[0] for p in KLINES_DIR_TRADIER.glob('*_D.json')))
    kdir = KLINES_DIR_CRYPTO if market == 'crypto' else KLINES_DIR_TRADIER
    return [s for s in syms if (kdir / f"{s}_{'3m' if market=='crypto' else '5m'}.json").exists()][:limit]

# ═══ Test variations ═══
def generate_rounds():
    rounds = []
    # Round 1-6: Stoch parameter sweep (crypto)
    for sl, sk, sd in [(14,5,5), (14,3,3), (9,3,3), (21,5,5), (14,7,7), (9,5,3)]:
        rounds.append(('crypto', f'stoch_{sl}_{sk}_{sd}_3m15m_struct', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':sl,'stoch_k':sk,'stoch_d':sd,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':'structure','fee':0.04}))
    # Round 7-12: SL/TP ratio sweep (crypto, best stoch)
    for sl, tp in [(-1.0, 0.5), (-1.5, 0.75), (-2.0, 1.0), (-2.0, 1.5), (-3.0, 1.5), (-3.0, 2.0)]:
        rounds.append(('crypto', f'sltp_{abs(sl):.0f}_{tp:.1f}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':sl,'tp':tp,'exit_mode':'structure','entry_mode':'structure','fee':0.04}))
    # Round 13-18: Hold time sweep (crypto)
    for mh in [50, 80, 100, 150, 200, 300]:
        rounds.append(('crypto', f'hold_{mh}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':mh,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':'structure','fee':0.04}))
    # Round 19-24: Entry mode comparison (crypto)
    for em in ['structure', 'k_cross', 'structure_ha', 'dc_break']:
        rounds.append(('crypto', f'entry_{em}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':em,'fee':0.04}))
    # Round 25-30: Exit mode comparison (crypto)
    for ex in ['structure', 'k_cross', 'structure_or_kcross', 'ha_flip', 'structure_ha']:
        rounds.append(('crypto', f'exit_{ex}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':ex,'entry_mode':'structure','fee':0.04}))
    # Round 31-34: HA candle filter (crypto)
    for ha in [False, True]:
        rounds.append(('crypto', f'ha_{ha}_3m15m_struct', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':'structure_ha' if ha else 'structure','require_ha':ha,'fee':0.04}))
    # Round 35-38: Volume filter (crypto)
    for vol in [0, 1.0, 1.3, 2.0]:
        rounds.append(('crypto', f'vol_{vol}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':'structure','require_vol':vol,'fee':0.04}))
    # Round 39-42: EMA filter (crypto)
    for ema_p in [0, 20, 50, 200]:
        rounds.append(('crypto', f'ema_{ema_p}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':'structure','ema_filter':ema_p,'fee':0.04}))
    # Round 43-48: K entry threshold sweep (crypto)
    for kmax, kmin in [(60, 40), (65, 35), (70, 30), (75, 25), (80, 20), (90, 10)]:
        rounds.append(('crypto', f'kzone_{kmax}_{kmin}_3m15m', {'entry_tf':'3m','confirm_tf':'15m','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':150,'sl':-2.0,'tp':1.0,'exit_mode':'structure','entry_mode':'structure','k_entry_max':kmax,'k_entry_min':kmin,'fee':0.04}))
    # === TRADIER ROUNDS ===
    # Round 49-54: TF sweep (tradier)
    for etf, ctf in [('5m','15m'), ('5m','1h'), ('15m','1h'), ('15m',''), ('1h',''), ('D','')]:
        rounds.append(('tradier', f'tf_{etf}_{ctf or "none"}_struct', {'entry_tf':etf,'confirm_tf':ctf,'stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':60 if etf in ('15m','1h') else 100,'sl':-2.0,'tp':1.5,'exit_mode':'structure','entry_mode':'structure','fee':0.0}))
    # Round 55-60: Stoch params (tradier, best TF)
    for sl_p, sk, sd in [(14,5,5), (14,3,3), (9,3,3), (21,5,5), (14,7,7), (9,5,3)]:
        rounds.append(('tradier', f'stoch_{sl_p}_{sk}_{sd}_15m1h', {'entry_tf':'15m','confirm_tf':'1h','stoch_len':sl_p,'stoch_k':sk,'stoch_d':sd,'max_hold':60,'sl':-2.0,'tp':1.5,'exit_mode':'structure','entry_mode':'structure','fee':0.0}))
    # Round 61-66: SL/TP (tradier)
    for sl_v, tp_v in [(-1.0, 0.5), (-1.5, 1.0), (-2.0, 1.5), (-2.0, 2.0), (-3.0, 2.0), (-3.0, 3.0)]:
        rounds.append(('tradier', f'sltp_{abs(sl_v):.0f}_{tp_v:.1f}_15m1h', {'entry_tf':'15m','confirm_tf':'1h','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':60,'sl':sl_v,'tp':tp_v,'exit_mode':'structure','entry_mode':'structure','fee':0.0}))
    # Round 67-72: Entry/exit modes (tradier)
    for em, ex in [('structure','structure'), ('k_cross','k_cross'), ('structure_ha','structure_ha'), ('dc_break','structure'), ('structure','ha_flip'), ('structure','structure_or_kcross')]:
        rounds.append(('tradier', f'mode_{em}_{ex}_15m1h', {'entry_tf':'15m','confirm_tf':'1h','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':60,'sl':-2.0,'tp':1.5,'exit_mode':ex,'entry_mode':em,'fee':0.0}))
    # Round 73-76: Volume + HA (tradier)
    for vol, ha in [(0, False), (1.3, False), (0, True), (1.3, True)]:
        rounds.append(('tradier', f'vol{vol}_ha{ha}_15m1h', {'entry_tf':'15m','confirm_tf':'1h','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':60,'sl':-2.0,'tp':1.5,'exit_mode':'structure','entry_mode':'structure','require_vol':vol,'require_ha':ha,'fee':0.0}))
    # Round 77-80: Daily + intraday combo (tradier)
    for etf, ctf, mh, sl_v, tp_v in [('D','','15',-3.0,2.0), ('D','','10',-5.0,4.0), ('D','','20',-3.0,3.0), ('D','','8',-2.0,1.5)]:
        rounds.append(('tradier', f'daily_mh{mh}_sl{abs(sl_v):.0f}_tp{tp_v:.0f}', {'entry_tf':etf,'confirm_tf':ctf,'stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':int(mh),'sl':sl_v,'tp':tp_v,'exit_mode':'structure','entry_mode':'structure','fee':0.0}))
    # Round 81+: Cross-market best combos with EMA
    for ema_p in [0, 20, 50]:
        rounds.append(('tradier', f'ema{ema_p}_15m1h_struct', {'entry_tf':'15m','confirm_tf':'1h','stoch_len':14,'stoch_k':5,'stoch_d':5,'max_hold':60,'sl':-2.0,'tp':1.5,'exit_mode':'structure','entry_mode':'structure','ema_filter':ema_p,'fee':0.0}))
    return rounds

def main():
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    print(f"{'='*120}")
    print(f"BACKTEST MARATHON — Started {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Results → {CSV_PATH}")
    print(f"Touch {STOP_FILE} to stop gracefully")
    print(f"{'='*120}")
    crypto_syms = get_symbols('crypto', 50)
    tradier_syms = get_symbols('tradier', 80)
    print(f"Crypto: {len(crypto_syms)} symbols | Tradier: {len(tradier_syms)} symbols")
    rounds = generate_rounds()
    total_rounds = len(rounds)
    print(f"Total rounds: {total_rounds}")
    print(f"{'='*120}\n")
    cycle = 0
    start_time = time.time()
    end_time = start_time + 14 * 3600
    while time.time() < end_time:
        for ri, (market, label, params) in enumerate(rounds):
            if STOP_FILE.exists():
                print(f"\n{'='*120}\nSTOP FILE DETECTED — graceful shutdown\n{'='*120}")
                return
            if time.time() >= end_time:
                print(f"\n{'='*120}\n14 HOURS REACHED — marathon complete\n{'='*120}")
                return
            cycle_label = f"C{cycle}_{label}"
            syms = crypto_syms if market == 'crypto' else tradier_syms
            t0 = time.time()
            try:
                trades = run_backtest(syms, market, params)
                result = compute_metrics(trades, cycle_label, params)
                result['market'] = market
                elapsed = time.time() - t0
                append_csv(result)
                pf_str = f"{result['pf']:.2f}" if result['trades'] > 0 else "N/A"
                wr_str = f"{result['wr']:.1f}%" if result['trades'] > 0 else "N/A"
                marker = "***" if result.get('pf', 0) > 2.0 else "  " if result.get('pf', 0) > 1.3 else ""
                elapsed_total = (time.time() - start_time) / 3600
                print(f"[{elapsed_total:.1f}h] {ri+1}/{total_rounds} {market:7s} {label:<45s} trades={result['trades']:>6d} WR={wr_str:>6s} PF={pf_str:>5s} Tot={result.get('total_pnl',0):>+8.1f}% {elapsed:.1f}s {marker}")
            except Exception as e:
                print(f"[ERROR] {label}: {e}")
                traceback.print_exc()
        cycle += 1
        print(f"\n--- Cycle {cycle} complete ({(time.time()-start_time)/3600:.1f}h elapsed) — restarting with shuffled order ---\n")
        np.random.shuffle(rounds)
    print(f"\n{'='*120}\nMARATHON COMPLETE — {cycle} full cycles in {(time.time()-start_time)/3600:.1f} hours")
    print(f"Results in {CSV_PATH}")

if __name__ == '__main__':
    main()
