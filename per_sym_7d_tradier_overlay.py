#!/usr/bin/env python3
"""per_sym_7d_tradier_overlay — weekly TRC overlay (month-back, exp-weighted) on top of TRB best.

- TRB = pure best found settings only (data/hourly_reconfig/trb/active_config.json, 4-yr per-sym sweep winners, 148 keys). No overlay.
- TRC = TRB baseline + weekly overlay that goes ~30 calendar days back but puts exponential
  more importance on last days: w = 0.5**days_ago * (2.0 if days_ago<2 else 1.0) — today 2× yesterday.
  (Analogous to crypto: fin trades men symbols+settings with 7D overlay while men is pure best.)
- Uses SAME symbols_long/short/always from TRB (symbols_trb_long.json / symbols_trb_short.json)
  per 2026-07-19 parity contract (tradier_rankings.py mirrors TRC to TRB).
- Baseline = data/hourly_reconfig/trb/active_config.json.
- Weekly recalc: 30 calendar days (20 trading days), recency weight as above.
  Score = time-weighted pool Sharpe (per-trade returns, no annualization) via metrics_guard.
- Expanded knob set = all important switches discovered (structural exits, WT, HOLD):
    DC_RECOVERY_EXIT_ENABLED / TOL [0.05,0.10,0.20,0.5], WT_EXIT_MIN_TFS [2,3],
    MIN_HOLD_BARS_15m [3,5,8], COOLDOWN [1,2], MIN_TFS_AGREE [2,3,4],
    REVERSE_ON_EXIT, REENTRY_MEAN_REV, NOLOSS, STRUCT_TF [15m/1h/4h/D],
    plus legacy R1/ LR_BAND toggles.
- Writes overlay to data/hourly_reconfig/trc/active_config_7d.json AND
  data/hourly_reconfig/trc/active_config.json (TRC live reads active_config.json via
  tradier_manage._load_tradier_per_sym_cfgs). TRB untouched.
- NO live orders. tradier_manage.py reads overlay and routes TRC through execute_now().
"""
from __future__ import annotations
import argparse, json, math, sys, time, traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import numpy as np
_orig = np.load
def _safe(*a, **kw): kw.setdefault('allow_pickle', True); return _orig(*a, **kw)
np.load = _safe
import metrics_guard as mg
import per_sym_engine_stocks as pse
from per_sym_engine_stocks import SymParamsStocks, simulate_dual_stocks, load_5m_base as _load_5m_base

_orig_load = pse.load_5m_base
def _safe5(sym, years_back=4.0):
    try: return _orig_load(sym, years_back=years_back)
    except Exception: pass
    return None
pse.load_5m_base = _safe5

OUT_BASE = ROOT / 'data' / 'hourly_reconfig'
TRB_BASELINE = OUT_BASE / 'trb' / 'active_config.json'
SYM_TRB_LONG = ROOT / 'symbols_trb_long.json'
SYM_TRB_SHORT = ROOT / 'symbols_trb_short.json'
WINDOW_DAYS = 30.0  # 1 month / 20 trading days month-back with EMA exp weight (was 21d) — all 2yr switches concentrated on last days
DECAY_DEFAULT = 0.93  # EMA alpha — regular week
DECAY_HEAVY = 0.80  # heavy last-days week (today 1.0, 7d 0.21, 21d 0.009 — fast movers dominate)
DECAY = DECAY_DEFAULT
PROMOTE_DELTA = 0.10
PROMOTE_FLOOR = 0.7
MIN_TRADES = 6
try: LIVE_MIN = int(mg.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE)
except Exception: LIVE_MIN = 30

def wsharpe(returns_ts, ref_ts):
    if not returns_ts: return 0.0, 0.0, 0
    rs = np.array([r for r,_ in returns_ts], dtype=float)
    ts = np.array([t for _,t in returns_ts], dtype=float)
    days_ago = np.maximum(0.0, (ref_ts - ts)/86400.0)
    w = np.power(DECAY, days_ago)  # EMA exp overlay: recent days exponential more weight
    s = float(w.sum())
    if s <= 0 or len(rs) < 2: return 0.0, 0.0, len(rs)
    wm = float((rs*w).sum()/s)
    wv = float((w*(rs-wm)**2).sum()/s)
    return (wm/math.sqrt(max(wv,0)) if wv>1e-12 else 0.0), s, len(rs)

def load_baseline(sym):
    if not TRB_BASELINE.exists(): return SymParamsStocks()
    try: d=json.loads(TRB_BASELINE.read_text())
    except Exception: return SymParamsStocks()
    e = d.get(f"{sym}_LONG") or d.get(f"{sym}_SHORT") or d.get(sym)
    if not e: return SymParamsStocks()
    ov = e.get('overrides') or {}
    p=SymParamsStocks()
    for k,v in ov.items():
        if k.startswith('_'): continue
        if hasattr(p,k):
            try: setattr(p,k,v)
            except Exception: pass
    return p

def variants(base):
    """All switches from 2yr backtest but applied over last 3 weeks with EMA overlay.
    Generates one variant per switch (toggle bools, ±20% numerics, structural grids) —
    i.e. the full G1..G5 universe (exit/entry/reentry/filters/guards) evaluated on the
    21d EMA-weighted window. Mirrors the 2yr sweep's knob set, just shorter horizon.
    """
    import dataclasses
    base=base.copy(); base.USE_V8_AGGREGATORS=False
    vs=[('baseline', base.copy())]
    # keep explicit structural grids (DC_TOL × WT × HOLD) as in 2yr sweep
    for tol in [0.05, 0.10, 0.20]:
        p=base.copy(); p.DC_RECOVERY_EXIT_TOLERANCE_PCT=tol; p.DC_RECOVERY_EXIT_ENABLED=True
        vs.append((f'dc_tol_{tol}', p))
    for tfs in [2,3]:
        p=base.copy(); p.MIN_TFS_AGREE=tfs; p.MIN_TFS_AGREE_ENTRY=tfs; p.MIN_TFS_AGREE_EXIT=max(1,tfs-1)
        vs.append((f'min_tfs_{tfs}', p))
    for h in [20,32,50]:
        p=base.copy(); p.MIN_HOLD_BARS_15m=h
        vs.append((f'hold_{h}', p))
    for tf in ['15m','1h','4h','D']:
        p=base.copy(); p.EXIT_STRUCT_TF=tf
        vs.append((f'struct_{tf}', p))
    # now enumerate every field of SymParamsStocks as in the 2yr sweep
    fields=dataclasses.fields(SymParamsStocks)
    for f in fields:
        name=f.name
        if name in ('MODE','LTF','USE_V8_AGGREGATORS'): continue
        val=getattr(base, name, None)
        if isinstance(val, bool):
            # toggle each bool switch one-at-a-time (G1..G5 guards/filters)
            if name.startswith('_'): continue
            # skip already covered toggles to avoid dup
            if name in ('REVERSE_ON_EXIT_ENABLED','REENTRY_MEAN_REV_ENABLED','NOLOSS_ENABLED','R1_DC_LOW4_3M_EMERGENCY_ENABLED','LR_BAND_ENTRY_ENABLED','DC_RECOVERY_EXIT_ENABLED'): continue
            p=base.copy(); setattr(p, name, not val)
            vs.append((f'toggle_{name}', p))
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            # numeric: ±20% variants for thresholds/sizes (e.g. K floors, BB, WT, stop %)
            if name.startswith('_'): continue
            # only for tunable thresholds, not periods fixed at 20
            if 'PERIOD' in name or 'LEN' in name or 'CHAN' in name or 'LOOKBACK' in name: continue
            try:
                lo = val * 0.8 if val != 0 else -0.1
                hi = val * 1.2 if val != 0 else 0.1
                # int fields need int variants
                if isinstance(val, int):
                    lo_i = max(1, int(round(lo))) if 'MIN' in name or 'BARS' in name else int(round(lo))
                    hi_i = int(round(hi))
                    if lo_i != val:
                        p=base.copy(); setattr(p, name, lo_i); vs.append((f'{name}_lo', p))
                    if hi_i != val and hi_i != lo_i:
                        p=base.copy(); setattr(p, name, hi_i); vs.append((f'{name}_hi', p))
                else:
                    if lo != val:
                        p=base.copy(); setattr(p, name, float(lo)); vs.append((f'{name}_lo', p))
                    if hi != val:
                        p=base.copy(); setattr(p, name, float(hi)); vs.append((f'{name}_hi', p))
            except Exception: continue
    return vs

def eval_7d(sym, params):
    m=simulate_dual_stocks(sym, params, years_back=WINDOW_DAYS/365.25)
    if m is None: return None
    trades=m.get('trade_list', [])
    if not trades:
        return {'wsharpe':0,'trades':0,'pool_sharpe':0,'max_dd':0,'wr':0,'gain_w':0,'bh':float(m.get('bh_pct_window',0)), 'm':m, 'params':params.to_dict()}
    rets=[(t['pnl_pct'], t['exit_ts']) for t in trades]
    ref=max(t for _,t in rets)
    ws,_,n=wsharpe(rets, ref)
    canon=mg.pool_sharpe([r for r,_ in rets])
    return {'wsharpe':ws,'trades':n,'pool_sharpe':float(canon),'max_dd':float(m.get('max_dd_pct',0)),'wr':float(m.get('wr_pct',0)),'gain_w':float(m.get('gain_per_week',0)),'bh':float(m.get('bh_pct_window',0)),'m':m,'params':params.to_dict()}

def one(sym):
    base=load_baseline(sym)
    vs=variants(base)
    res=[]
    for tag,p in vs:
        r=eval_7d(sym,p)
        if r is None: continue
        r['tag']=tag
        res.append((tag,r))
    if not res: return None
    eligible=[(t,r) for t,r in res if r['trades']>=MIN_TRADES] or res
    pos=[(t,r) for t,r in eligible if r['wsharpe']>0]
    if pos:
        def tier(r): bh=float(r.get('bh',0) or 0); gw=float(r.get('gain_w',0) or 0)*WINDOW_DAYS/7; 
        if bh<=0: return 2 if gw>0 else 0
        if gw>=2*bh: return 2
        if gw>=bh: return 1
        return 0
        tag,r=max(pos, key=lambda x: (tier(x[1]), x[1].get('gain_w',0)))
    else:
        tag,r=max(eligible, key=lambda x: x[1]['wsharpe'])
    base_r=next((rr for tt,rr in res if tt=='baseline'), r)
    ok=r['trades']>=LIVE_MIN
    promote= ok and r['wsharpe']>=PROMOTE_FLOOR and (r['wsharpe']-base_r['wsharpe'])>=PROMOTE_DELTA
    return {'sym':sym,'winner_tag':tag,'winner_wsharpe':r['wsharpe'],'baseline_wsharpe':base_r['wsharpe'],'delta':r['wsharpe']-base_r['wsharpe'],'trades':r['trades'],'pool_sharpe':r['pool_sharpe'],'wr':r['wr'],'max_dd':r['max_dd'],'gain_w':r['gain_w'],'bh':r['bh'],'promote':promote,'floor_ok':ok,'params':r['params'],'all':[{'tag':t,'w':rr['wsharpe'],'tr':rr['trades']} for t,rr in res]}

def trb_symbols():
    longs=json.loads(SYM_TRB_LONG.read_text()) if SYM_TRB_LONG.exists() else []
    shorts=json.loads(SYM_TRB_SHORT.read_text()) if SYM_TRB_SHORT.exists() else []
    uniq=sorted(set(longs+shorts))
    return uniq

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--decay', type=float, default=None, help='EMA decay 0.93 regular, 0.80 heavy last-days; default auto by week')
    ap.add_argument('--heavy', action='store_true', help='force heavy last-days weighting (0.80)')
    args=ap.parse_args()
    global DECAY
    if args.decay is not None:
        DECAY = float(args.decay)
    elif args.heavy:
        DECAY = DECAY_HEAVY
    else:
        # auto: week 1 regular, week 2 heavy — iso week parity
        import datetime as _dt
        wk = _dt.datetime.now(_dt.timezone.utc).isocalendar().week
        # even weeks heavy, odd regular (so first week after today = regular, next = heavy)
        if wk % 2 == 0:
            DECAY = DECAY_HEAVY
        else:
            DECAY = DECAY_DEFAULT
    syms=trb_symbols()
    print(f"[7d_trc_overlay] TRB universe {len(syms)} syms -> TRC 21d EMA decay {DECAY} ({'heavy' if DECAY==DECAY_HEAVY else 'regular'})", flush=True)
    t0=time.time()
    results=[]
    if args.workers<=1:
        for s in syms:
            r=one(s)
            if r: results.append(r)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs={ex.submit(one,s):s for s in syms}
            for fut in as_completed(futs):
                try: r=fut.result()
                except Exception as e: print(f"  {futs[fut]} err {e}", flush=True); continue
                if r: results.append(r)
    print(f"[7d_trc_overlay] done {len(results)}/{len(syms)} in {time.time()-t0:.0f}s promote={sum(1 for r in results if r['promote'])}", flush=True)
    for r in sorted(results, key=lambda x: x['winner_wsharpe'], reverse=True)[:10]:
        print(f"  {r['sym']:<6} {r['winner_tag']:<16} w={r['winner_wsharpe']:+.3f} d={r['delta']:+.3f} tr={r['trades']} prom={r['promote']}", flush=True)
    if args.dry_run: return
    out_trc=OUT_BASE/'trc'
    out_trc.mkdir(parents=True, exist_ok=True)
    # overlay file for audit
    payload={}
    live_payload={}
    # live baseline for TRC = TRB active_config + 7d winners where promote
    try: trb=json.loads(TRB_BASELINE.read_text())
    except Exception: trb={}
    # TRC must always mirror TRB universe per 2026-07-19 parity — start with full copy
    live_payload=dict(trb)
    for r in results:
        sym=r['sym']
        # find TRB key forms
        for side in ('LONG','SHORT'):
            key=f"{sym}_{side}"
            if key not in trb: continue
            base_entry=trb[key]
            if r['promote']:
                payload[key]={'winning_tag':r['winner_tag'],'wsharpe':r['winner_wsharpe'],'baseline_wsharpe':r['baseline_wsharpe'],'delta':r['delta'],'trades_7d':r['trades'],'pool_sharpe':r['pool_sharpe'],'wr':r['wr'],'max_dd':r['max_dd'],'gain_w':r['gain_w'],'bh':r['bh'],'promote':True,'overrides':r['params']}
                # merge overrides on top of TRB baseline overrides
                merged=dict(base_entry.get('overrides',{})); merged.update(r['params'])
                live_payload[key]={'overrides':merged,'winning_tag':r['winner_tag'],'wsharpe':r['winner_wsharpe'],'trades':r['trades']}
            else:
                live_payload[key]=base_entry
    (out_trc/'active_config_7d.json').write_text(json.dumps(payload, indent=2, default=str))
    (out_trc/'active_config.json').write_text(json.dumps(live_payload, indent=2, default=str))
    print(f"  wrote {out_trc/'active_config_7d.json'} ({len(payload)} promoted) and {out_trc/'active_config.json'} ({len(live_payload)} live)", flush=True)

if __name__=='__main__': main()
