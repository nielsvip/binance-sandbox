#!/usr/bin/env python3
"""FAST full system test — direct array access, no per-bar dict rebuild."""
import numpy as np, sys, os, time, platform
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or '.')
from backtest_v8_harness import IndicatorStore
from pathlib import Path

IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")
stock_dir = BASE / "backtest_v4_tradier" / "indicators"
crypto_dir = BASE / "backtest_v4" / "indicators"

def run_system_fast(s, is_crypto=False):
    closes = s.arrays['close'].astype(np.float64)
    n = len(closes)
    wt1_D = s.arrays.get('wt1_D', np.zeros(n)).astype(np.float64)
    wt2_D = s.arrays.get('wt2_D', np.zeros(n)).astype(np.float64)
    wt1_4h = s.arrays.get('wt1_4h', np.zeros(n)).astype(np.float64)
    wt2_4h = s.arrays.get('wt2_4h', np.zeros(n)).astype(np.float64)
    wt_cross_1h = s.arrays.get('wt_cross_1h', np.array([''] * n, dtype=object))
    dc_1h = s.arrays.get('dc_position_1h', np.full(n, 0.5)).astype(np.float64)
    k_key = 'stoch_k_3m' if is_crypto else 'stoch_k_5m'
    k_5m = s.arrays.get(k_key, np.full(n, 50)).astype(np.float64)
    # Replace NaN
    np.nan_to_num(wt1_D, copy=False); np.nan_to_num(wt2_D, copy=False)
    np.nan_to_num(wt1_4h, copy=False); np.nan_to_num(wt2_4h, copy=False)
    np.nan_to_num(dc_1h, nan=0.5, copy=False); np.nan_to_num(k_5m, nan=50, copy=False)
    trades = []
    i = 0
    while i < n - 200:
        d_bull = wt1_D[i] > wt2_D[i]
        h4_bull = wt1_4h[i] > wt2_4h[i]
        d_bear = wt1_D[i] < wt2_D[i]
        h4_bear = wt1_4h[i] < wt2_4h[i]
        cross = str(wt_cross_1h[i])
        is_long = d_bull and h4_bull and cross == 'BULL' and dc_1h[i] < 0.50 and k_5m[i] < 40
        is_short = d_bear and h4_bear and cross == 'BEAR' and dc_1h[i] > 0.50 and k_5m[i] > 60
        if not is_long and not is_short:
            i += 1; continue
        entry_px = closes[i]; side = 1 if is_long else -1; max_gain = 0
        for j in range(1, 200):
            if i + j >= n: break
            px = closes[i+j]
            gain = side * (px - entry_px) / entry_px * 100
            if gain > max_gain: max_gain = gain
            cross_x = str(wt_cross_1h[i+j])
            exit_1h = (is_long and cross_x == 'BEAR') or (is_short and cross_x == 'BULL')
            exit_4h = (is_long and wt1_4h[i+j] < wt2_4h[i+j]) or (is_short and wt1_4h[i+j] > wt2_4h[i+j])
            exit_trail = max_gain > 2.0 and gain < max_gain * 0.4
            exit_stop = gain < -2.0
            if (exit_1h and exit_4h) or exit_trail or exit_stop or j == 199:
                trades.append(gain); i = i + j + 4; break
        else: i += 1
    return trades

t0 = time.time()
print("="*85, flush=True)
print("FULL SYSTEM: multi-TF WT/DC — D+4h trend → 1h cross → DC+K confirm → trail/stop", flush=True)
print("="*85, flush=True)

for label, npz_dir, is_crypto in [("STOCKS", stock_dir, False), ("CRYPTO", crypto_dir, True)]:
    if not npz_dir.exists():
        print(f"\n{label}: dir not found", flush=True); continue
    npz_files = sorted(npz_dir.glob('*.npz'))
    all_trades = []; results = []
    t_label = time.time()
    for k, npz in enumerate(npz_files):
        sym = npz.stem
        try:
            s = IndicatorStore(str(npz))
            if s.n_bars < 1000: continue
            trades = run_system_fast(s, is_crypto=is_crypto)
            if len(trades) < 2: continue
            a = np.array(trades)
            # Filter insane outliers (data glitches)
            a = a[(a > -50) & (a < 50)]
            if len(a) < 2: continue
            if is_crypto:
                days = s.n_bars / 96; sh = a.mean()/a.std()*np.sqrt(len(a)/days*365) if a.std()>0 else 0
            else:
                days = s.n_bars / 26 / 5; sh = a.mean()/a.std()*np.sqrt(len(a)/days*252) if a.std()>0 else 0
            results.append({'sym':sym,'n':len(a),'avg':a.mean(),'wr':np.sum(a>0)/len(a)*100,'sh':sh,'total':a.sum()})
            all_trades.extend(a.tolist())
            if (k+1) % 20 == 0:
                print(f"  [{label}] {k+1}/{len(npz_files)} processed ({time.time()-t_label:.0f}s)", flush=True)
        except Exception as e:
            pass
    
    print(f"\n{'='*85}", flush=True)
    print(f"{label}: {len(results)}/{len(npz_files)} symbols with trades ({time.time()-t_label:.0f}s)", flush=True)
    print(f"{'='*85}", flush=True)
    if not results: continue
    print(f"{'Sym':>14} | {'n':>4} | {'avg%':>5} | {'WR':>3} | {'Sh':>5} | {'Total%':>7}", flush=True)
    print("-"*55, flush=True)
    for r in sorted(results, key=lambda x:-x['sh'])[:10]:
        print(f"{r['sym']:>14} | {r['n']:>4} | {r['avg']:4.2f} | {r['wr']:3.0f} | {r['sh']:5.1f} | {r['total']:6.0f}", flush=True)
    print("..." * 15, flush=True)
    for r in sorted(results, key=lambda x:x['sh'])[:3]:
        print(f"{r['sym']:>14} | {r['n']:>4} | {r['avg']:4.2f} | {r['wr']:3.0f} | {r['sh']:5.1f} | {r['total']:6.0f}", flush=True)
    
    aa = np.array(all_trades)
    pos = len([r for r in results if r['sh']>0])
    if is_crypto:
        sh_all = aa.mean()/aa.std()*np.sqrt(len(aa)/(167000/96)*365) if aa.std()>0 else 0
    else:
        sh_all = aa.mean()/aa.std()*np.sqrt(len(aa)/(15000/26/5)*252) if aa.std()>0 else 0
    print(f"\n{label} TOTAL: {len(aa)} trades | avg={aa.mean():.2f}% | WR={np.sum(aa>0)/len(aa)*100:.0f}% | Sharpe={sh_all:.1f}", flush=True)
    print(f"  Positive: {pos}/{len(results)} ({pos/len(results)*100:.0f}%)", flush=True)
    print(f"  Winners avg={aa[aa>0].mean():.1f}% | Losers avg={aa[aa<0].mean():.1f}%", flush=True)
    if not is_crypto:
        pos_size = 7000
        annual = aa.sum() * pos_size / 100 / 70000 * 252 / (15000/26/5) * 100
        print(f"  ANNUAL on $70k ($7k/pos): {annual:.0f}%", flush=True)

print(f"\nDone in {time.time()-t0:.0f}s", flush=True)
