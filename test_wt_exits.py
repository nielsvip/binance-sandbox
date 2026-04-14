#!/usr/bin/env python3
"""Test WT cross exits vs fixed TP on ranked stocks from Mar 15."""
import json, numpy as np, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
backtest_engine.KLINES_DIR = Path('/Users/niels/Documents/binance/klines_cache/tradier')
from backtest_engine import precompute_real_indicators

stocks = [('SNDK','LONG'),('USO','LONG'),('AVGO','LONG'),('MU','LONG'),('ETHD','SHORT'),('SBIT','SHORT'),('OLED','SHORT'),('FIVN','SHORT')]
entry_ts = int(datetime(2026,3,3,14,30,tzinfo=timezone.utc).timestamp())  # Mar 3 — maximize forward data
results = {}
for sym, side in stocks:
    is_long = side == 'LONG'
    tfs = {}
    for tf in ['5m','15m','1h','4h']:
        pre = precompute_real_indicators(sym, tf)
        if pre: tfs[tf] = pre
    if '1h' not in tfs: print(f'{sym}: no 1h'); continue
    pre_1h = tfs['1h']; c_1h = pre_1h['indicators']['current_price']
    h_1h = pre_1h['indicators'].get('high_1h', c_1h); l_1h = pre_1h['indicators'].get('low_1h', c_1h)
    ts_1h = (pre_1h['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64))
    si = np.searchsorted(ts_1h, entry_ts, side='right')
    if si >= len(c_1h) - 5: print(f'{sym}: too short (si={si}, n={len(c_1h)})'); continue
    scan = min(si + 30, len(c_1h))
    entry_idx = si + (int(np.argmin(l_1h[si:scan])) if is_long else int(np.argmax(h_1h[si:scan])))
    ep = float(c_1h[entry_idx]); edt = datetime.fromtimestamp(int(ts_1h[entry_idx]), tz=timezone.utc)
    print(f'\n{sym} {side}: entry=${ep:.2f} on {edt.strftime("%m/%d %H:%M")}')
    for label, etf, ctf in [('TP_1.5%','1h',None),('WT_5m','5m',None),('WT_5m+15m','5m','15m'),('WT_15m','15m',None),('WT_15m+1h','15m','1h'),('WT_1h','1h',None),('WT_1h+4h','1h','4h')]:
        if etf not in tfs: continue
        epre = tfs[etf]; ec = epre['indicators'].get('current_price')
        eh = epre['indicators'].get(f'high_{etf}', ec); el = epre['indicators'].get(f'low_{etf}', ec)
        wk = f'wt_cross_bear_{etf}' if is_long else f'wt_cross_bull_{etf}'
        wt = epre['indicators'].get(wk)
        ets = (epre['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64))
        ei = np.searchsorted(ets, ts_1h[entry_idx], side='right')
        if ei >= len(ec) - 5: continue
        cwt = None
        if ctf and ctf in tfs:
            cp = tfs[ctf]; ck = f'wt_cross_bear_{ctf}' if is_long else f'wt_cross_bull_{ctf}'
            ca = cp['indicators'].get(ck)
            if ca is not None:
                ct = (cp['df']['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64))
                cm = np.searchsorted(ct, ets, side='right') - 1; np.clip(cm, 0, len(ca)-1, out=cm)
                cwt = ca[cm]
        mg = 0.0; xr = None; xb = None
        for bi in range(ei+1, len(ec)):
            _c = float(ec[bi]) if isinstance(ec[bi], (int,float,np.floating)) else 0
            _h = float(eh[bi]) if isinstance(eh[bi], (int,float,np.floating)) else _c
            _l = float(el[bi]) if isinstance(el[bi], (int,float,np.floating)) else _c
            if _c <= 0: continue
            hp = (_h-ep)/ep if is_long else (ep-_l)/ep; pnl = (_c-ep)/ep if is_long else (ep-_c)/ep
            mg = max(mg, hp)
            if label == 'TP_1.5%':
                if hp >= 0.015: xr = 0.015; xb = bi; break
                elif mg >= 0.005 and pnl <= 0.003: xr = pnl; xb = bi; break
            else:
                wf = wt is not None and bi < len(wt) and float(wt[bi]) > 0
                cf = cwt is None or (bi < len(cwt) and float(cwt[bi]) > 0)
                if pnl > 0.003 and wf and cf: xr = pnl; xb = bi; break
                if mg >= 0.005 and pnl <= 0.003: xr = pnl; xb = bi; break
        if xr is not None and xb is not None:
            xdt = datetime.fromtimestamp(int(ets[xb]), tz=timezone.utc)
            hrs = (xdt - edt).total_seconds() / 3600
            if label not in results: results[label] = []
            results[label].append(xr)
            print(f'  {label:18s}: {xr*100:>+6.2f}%  in {hrs:>5.0f}h  exit {xdt.strftime("%m/%d %H:%M")}')
        else:
            lc = float(ec[-1]) if len(ec) > 0 else 0
            ur = (lc-ep)/ep if is_long else (ep-lc)/ep
            print(f'  {label:18s}: OPEN  unrealized {ur*100:>+6.2f}%')

print(f'\n{"="*60}')
print(f'{"Strategy":18s} {"Avg":>8s} {"Max":>8s} {"N":>4s}')
for lb in ['TP_1.5%','WT_5m','WT_5m+15m','WT_15m','WT_15m+1h','WT_1h','WT_1h+4h']:
    if lb in results and results[lb]:
        r = np.array(results[lb])
        print(f'{lb:18s} {np.mean(r)*100:>+7.3f}% {np.max(r)*100:>+7.3f}% {len(r):>4d}')
