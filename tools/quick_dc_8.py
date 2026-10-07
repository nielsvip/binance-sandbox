#!/usr/bin/env python3
"""Quick 2x4 DC check on top of afternoon best - 8 evals per sym_side, seconds.
Design: load afternoon best per sym_side (30s baseline, QuickConfig + per_sym_active_config overrides),
then test 4 entry TFs (OFF/15m/1h/4h) for DAYTRADE_DC_STOP+TECHNICAL_DC_STOP (0.25% buffer) and
4 exit TFs for DAYTRADE_DC_TARGET+TECHNICAL_DC_TARGET (0.10% buffer), keep best entry+best exit.
OFF is baseline (no extra sim needed, counted as option), so on-top sims = 3+3=6, options = 8.
"""
import sys
import pathlib as _pl
sys.path.insert(0, '/home/niels/binance-sandbox')
import v12_quick_engine as v12, numpy as np, pathlib, json, glob
from pathlib import Path
ROOT=Path('/home/niels/binance-sandbox')
TFs=["OFF","15m","1h","4h"]
all_syms=set()
for d in [ROOT/"SPREADSHEETS/BEST/CRYPTO_LONG", ROOT/"SPREADSHEETS/BEST/CRYPTO_SHORT", ROOT/"SPREADSHEETS/BEST/STOCKS_LONG", ROOT/"SPREADSHEETS/BEST/STOCKS_SHORT"]:
    for p in d.glob("*.xlsx"):
        all_syms.add(p.stem.split("_bh")[0])
for p in [ROOT/"data/hourly_reconfig/per_sym_active_config.json", ROOT/"data/hourly_reconfig/per_sym_active_config_stocks.json"]:
    if p.exists():
        try:
            j=json.loads(pathlib.Path(p).read_text())
            for k in j:
                if not k.startswith("_"): all_syms.add(k)
        except: pass
npzs=set([Path(p).stem for p in glob.glob(str(ROOT/"backtest_v8/indicators/*.npz")) if "S3" not in p])
filtered=sorted([s for s in all_syms if s.replace("_LONG","").replace("_SHORT","") in npzs and s])
if len(filtered)<354:
    for sym in sorted(npzs):
        for side in ["_LONG","_SHORT"]:
            ss=sym+side
            if ss not in filtered:
                filtered.append(ss)
                if len(filtered)>=354: break
        if len(filtered)>=354: break
filtered=sorted(filtered)[:354]
print(f"Quick 2x4 DC {len(filtered)} TFs {TFs} (OFF baseline + 3 new per side = 6 sims, 8 options)")

afternoon_best={}
for p in [ROOT/"data/hourly_reconfig/per_sym_active_config.json", ROOT/"data/hourly_reconfig/per_sym_active_config_stocks.json"]:
    if p.exists():
        j=json.loads(pathlib.Path(p).read_text())
        for k,v in j.items():
            if k.startswith("_"): continue
            afternoon_best[k]=v.get("overrides",{})

import os
venue=os.environ.get("VENUE","all")
if venue=="crypto":
    filtered=[s for s in filtered if s.replace("_LONG","").replace("_SHORT","").endswith(("USDC","USDT","BUSD","FDUSD"))]
elif venue=="stocks":
    filtered=[s for s in filtered if not s.replace("_LONG","").replace("_SHORT","").endswith(("USDC","USDT","BUSD","FDUSD"))]
print(f"Venue {venue} -> {len(filtered)} syms: {filtered[:3]}")

import time
start=time.time()
results=[]

def _slice_30d(npz):
    try:
        ts=npz["timestamps"]
        if len(ts)>5000:
            cut=ts[-1]-30*24*3600
            idx=int(np.searchsorted(ts, cut))
            if 0<idx<len(ts)-100:
                sliced={k: npz[k][idx:] if getattr(npz[k], 'shape', None) and len(npz[k].shape)>0 and npz[k].shape[0]==len(ts) else npz[k] for k in npz.files}
                return sliced
    except Exception:
        pass
    return npz

def eval_sym(sym_side):
    sym=sym_side.replace("_LONG","").replace("_SHORT","")
    is_long=sym_side.endswith("_LONG")
    _npz_full=np.load(str(ROOT/f"backtest_v8/indicators/{sym}.npz"), allow_pickle=True)
    npz=_slice_30d(_npz_full)
    base_cfg=v12.QuickConfig()
    base_cfg.MODE='crypto' if sym.endswith(("USDC","USDT","BUSD","FDUSD")) else 'tradier'
    for k,v in afternoon_best.get(sym_side, {}).items():
        if hasattr(base_cfg, k):
            setattr(base_cfg, k, v)
    r0=v12.simulate_one(npz, sym, is_long, base_cfg)
    base_gain=r0['gain_pct_2000norm'] if r0 else 0
    best_gain=base_gain
    best_entry="OFF"
    best_exit="OFF"
    for tf in TFs:
        if tf=="OFF": continue
        cfg=v12.QuickConfig()
        cfg.MODE=base_cfg.MODE
        for k,v in afternoon_best.get(sym_side, {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        cfg.DAYTRADE_DC_STOP_TF=tf; cfg.DAYTRADE_DC_STOP_BUFFER_PCT=0.25
        cfg.TECHNICAL_DC_STOP_TF=tf; cfg.TECHNICAL_DC_STOP_BUFFER_PCT=0.25
        r=v12.simulate_one(npz, sym, is_long, cfg)
        if r and r['gain_pct_2000norm']>best_gain:
            best_gain=r['gain_pct_2000norm']
            best_entry=tf
    for tf in TFs:
        if tf=="OFF": continue
        cfg=v12.QuickConfig()
        cfg.MODE=base_cfg.MODE
        for k,v in afternoon_best.get(sym_side, {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        if best_entry!="OFF":
            cfg.DAYTRADE_DC_STOP_TF=best_entry; cfg.DAYTRADE_DC_STOP_BUFFER_PCT=0.25
            cfg.TECHNICAL_DC_STOP_TF=best_entry; cfg.TECHNICAL_DC_STOP_BUFFER_PCT=0.25
        cfg.DAYTRADE_DC_TARGET_TF=tf; cfg.DAYTRADE_DC_TARGET_BUFFER_PCT=0.10
        cfg.TECHNICAL_DC_TARGET_TF=tf; cfg.TECHNICAL_DC_TARGET_BUFFER_PCT=0.10
        r=v12.simulate_one(npz, sym, is_long, cfg)
        if r and r['gain_pct_2000norm']>best_gain:
            best_gain=r['gain_pct_2000norm']
            best_exit=tf
    delta=best_gain-base_gain
    return {"sym_side":sym_side, "base_gain":base_gain, "best_gain":best_gain, "delta":delta, "best_entry":best_entry, "best_exit":best_exit}

from concurrent.futures import ThreadPoolExecutor, as_completed
with ThreadPoolExecutor(max_workers=16) as ex:
    futs={ex.submit(eval_sym, s): s for s in filtered}
    done=0
    for fut in as_completed(futs):
        done+=1
        try:
            res=fut.result()
            results.append(res)
            if done%20==0 or res['delta']>0:
                print(f"{done}/{len(filtered)} {res['sym_side']} entry {res['best_entry']} exit {res['best_exit']} delta {res['delta']:.2f} {time.time()-start:.1f}s")
        except Exception as e:
            import traceback
            print(f"ERR {futs[fut]} {e} {traceback.format_exc()[:500]}")

out=ROOT/f"data/reports/quick_dc_8_{venue}.json"
out.write_text(json.dumps(results, indent=2))
print(f"saved {len(results)} to {out} {time.time()-start:.1f}s")
for _venue, _p in [("crypto", ROOT/"data/hourly_reconfig/per_sym_active_config.json"), ("stocks", ROOT/"data/hourly_reconfig/per_sym_active_config_stocks.json")]:
    if venue!="all" and venue!=_venue: continue
    _filtered_venue=[r for r in results if (r['sym_side'].replace("_LONG","").replace("_SHORT","").endswith(("USDC","USDT","BUSD","FDUSD")) if _venue=="crypto" else not r['sym_side'].replace("_LONG","").replace("_SHORT","").endswith(("USDC","USDT","BUSD","FDUSD")))]
    if not _filtered_venue: continue
    if _p.exists():
        j=json.loads(pathlib.Path(_p).read_text())
    else:
        j={}
    for r in _filtered_venue:
        if r['sym_side'] not in j: j[r['sym_side']]={}
        if 'overrides' not in j[r['sym_side']]: j[r['sym_side']]['overrides']={}
        j[r['sym_side']]['overrides']['DAYTRADE_DC_STOP_TF']=r['best_entry']
        j[r['sym_side']]['overrides']['TECHNICAL_DC_STOP_TF']=r['best_entry']
        j[r['sym_side']]['overrides']['DAYTRADE_DC_TARGET_TF']=r['best_exit']
        j[r['sym_side']]['overrides']['TECHNICAL_DC_TARGET_TF']=r['best_exit']
        j[r['sym_side']]['dc_best_gain']=r['best_gain']
        j[r['sym_side']]['dc_delta']=r['delta']
    pathlib.Path(_p).write_text(json.dumps(j, indent=2))
    print(f"updated live {_p} {len(_filtered_venue)}")
# also write splits for all
if venue=="all":
    for _v in ["crypto","stocks"]:
        _split=[r for r in results if (r['sym_side'].replace("_LONG","").replace("_SHORT","").endswith(("USDC","USDT","BUSD","FDUSD")) if _v=="crypto" else not r['sym_side'].replace("_LONG","").replace("_SHORT","").endswith(("USDC","USDT","BUSD","FDUSD")))]
        out2=ROOT/f"data/reports/quick_dc_8_{_v}.json"
        out2.write_text(json.dumps(_split, indent=2))
        print(f"saved split {len(_split)} to {out2}")
