"""Causal HLR top-exit confirmation from AdvancedSignalRater."""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np


def _cfg(cfg, name, default):
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _col(values, key, n, default=0.0, dtype=float):
    raw=values.get(key)
    if raw is None:
        return np.full(n,default,dtype=dtype)
    try:
        arr=np.asarray(raw,dtype=dtype)
    except (TypeError,ValueError) as error:
        raise ValueError(f"invalid HLR exit input {key}") from error
    if arr.ndim==0:
        arr=np.full(n,arr.item(),dtype=dtype)
    arr=arr.reshape(-1)
    if arr.size!=n:
        raise ValueError(f"{key} length {arr.size} does not match n={n}")
    return arr


def hlr_top_exit_scalar(values, cfg, is_long):
    """Return native-equivalent `(fire, multiplier, confirmed timeframes)` per row."""
    pnl=float(values["pnl_pct"])
    minimum=float(_cfg(cfg,"NOLOSS_MIN_PROFIT_PCT",.30))
    if not bool(_cfg(cfg,"HLR_TOP_EXIT_ENABLED",True)) or pnl<minimum or pnl<float(_cfg(cfg,"HLR_TOP_MIN_GAIN_PCT",1.5)):
        return False,1.0,()
    exhaust="EXHAUST_UP" if is_long else "EXHAUST_DOWN"
    divergence="BEAR" if is_long else "BULL"
    peak_bad="LH" if is_long else "HH"
    tfs=[]
    mult=1.0
    v1=float(values.get("wt_velocity_1h",0.0))
    threshold1=float(_cfg(cfg,"HLR_TOP_VEL_1H_THRESH",-1.0))
    if (is_long and v1<threshold1) or ((not is_long) and v1>abs(threshold1)):
        tfs.append("1h")
        mult=max(mult,float(_cfg(cfg,"HLR_REENTRY_MULT_1H",1.5)))
    v4=float(values.get("wt_velocity_4h",0.0))
    m4=str(values.get("wt_momentum_state_4h","") or "")
    d4=str(values.get("wt_divergence_4h","") or "")
    p4=str(values.get("wt_peak_structure_4h","") or "")
    f4=(((is_long and v4<float(_cfg(cfg,"HLR_TOP_VEL_4H_THRESH",0.0))) or ((not is_long) and v4>0.0)) or m4==exhaust or d4==divergence or p4==peak_bad)
    if f4:
        tfs.append("4h")
        mult=max(mult,float(_cfg(cfg,"HLR_REENTRY_MULT_4H",2.0)))
    vd=float(values.get("wt_velocity_D",0.0))
    md=str(values.get("wt_momentum_state_D","") or "")
    dd=str(values.get("wt_divergence_D","") or "")
    fD=(((is_long and vd<float(_cfg(cfg,"HLR_TOP_VEL_D_THRESH",0.0))) or ((not is_long) and vd>0.0)) or md==exhaust or dd==divergence)
    if fD:
        tfs.append("D")
        mult=max(mult,float(_cfg(cfg,"HLR_REENTRY_MULT_D",2.5)))
    vw=float(values.get("wt_velocity_W",0.0))
    mw=str(values.get("wt_momentum_state_W","") or "")
    fW=((is_long and vw<0.0) or ((not is_long) and vw>0.0)) or mw==exhaust
    if fW:
        tfs.append("W")
        mult=max(mult,float(_cfg(cfg,"HLR_REENTRY_MULT_W",3.0)))
    required=int(_cfg(cfg,"HLR_TOP_MIN_TFS",2))
    has_higher=any(tf in ("4h","D","W") for tf in tfs)
    fire=len(tfs)>=required and has_higher
    return fire,mult if fire else 1.0,tuple(tfs)

def hlr_top_exit_array(values,cfg,is_long):
    """Return per-row HLR fire, highest configured reentry multiplier and TF masks."""
    if "pnl_pct" not in values:
        raise ValueError("HLR exit requires pnl_pct")
    n=np.asarray(values["pnl_pct"]).size
    required=("pnl_pct","wt_velocity_1h","wt_velocity_4h","wt_velocity_D","wt_velocity_W",
              "wt_momentum_state_4h","wt_momentum_state_D","wt_momentum_state_W",
              "wt_divergence_4h","wt_divergence_D","wt_peak_structure_4h")
    data={key:_col(values,key,n,0.0 if "velocity" in key or key=="pnl_pct" else "",float if "velocity" in key or key=="pnl_pct" else object) for key in required}
    eligible=bool(_cfg(cfg,"HLR_TOP_EXIT_ENABLED",True)) & (data["pnl_pct"]>=float(_cfg(cfg,"NOLOSS_MIN_PROFIT_PCT",.30))) & (data["pnl_pct"]>=float(_cfg(cfg,"HLR_TOP_MIN_GAIN_PCT",1.5)))
    exhaust="EXHAUST_UP" if is_long else "EXHAUST_DOWN"
    divergence="BEAR" if is_long else "BULL"
    peak_bad="LH" if is_long else "HH"
    masks={}
    mult=np.ones(n,dtype=float)
    v1=data["wt_velocity_1h"]
    thr1=float(_cfg(cfg,"HLR_TOP_VEL_1H_THRESH",-1.0))
    masks["1h"]=eligible & ((v1<thr1) if is_long else (v1>abs(thr1)))
    v4=data["wt_velocity_4h"]
    m4=data["wt_momentum_state_4h"]
    d4=data["wt_divergence_4h"]
    p4=data["wt_peak_structure_4h"]
    v4_against=(v4<float(_cfg(cfg,"HLR_TOP_VEL_4H_THRESH",0.0))) if is_long else (v4>0.0)
    masks["4h"]=eligible & (v4_against | (m4==exhaust) | (d4==divergence) | (p4==peak_bad))
    vd=data["wt_velocity_D"]
    md=data["wt_momentum_state_D"]
    dd=data["wt_divergence_D"]
    masks["D"]=eligible & (((vd<float(_cfg(cfg,"HLR_TOP_VEL_D_THRESH",0.0))) if is_long else (vd>0.0)) | (md==exhaust) | (dd==divergence))
    vw=data["wt_velocity_W"]
    mw=data["wt_momentum_state_W"]
    masks["W"]=eligible & (((vw<0.0) if is_long else (vw>0.0)) | (mw==exhaust))
    for tf,flag in masks.items():
        mult=np.where(flag,np.maximum(mult,float(_cfg(cfg,f"HLR_REENTRY_MULT_{tf}",{ "1h":1.5,"4h":2.0,"D":2.5,"W":3.0}[tf]))),mult)
    hits=sum(flag.astype(np.int8) for flag in masks.values())
    high=masks["4h"]|masks["D"]|masks["W"]
    fire=eligible & (hits>=int(_cfg(cfg,"HLR_TOP_MIN_TFS",2))) & high
    mult=np.where(fire,mult,1.0)
    return {"fire":fire,"reentry_multiplier":mult,"masks":masks}
