"""Fifth disjoint source-exact V12 filter tranche.

The predicates use exact venue/timeframe fields. Missing enabled-path data is
reported unavailable and never replaced by another timeframe or a fabricated
constant series.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np


@dataclass(frozen=True)
class FieldContract:
    name: str; value_type: str; default: Any; grid: tuple[Any, ...]; family: str
    required_arrays: tuple[str, ...]; required_state: tuple[str, ...]
    lifecycle_filter_consumers: tuple[str, ...]; source: str


@dataclass(frozen=True)
class MaskResult:
    mask: np.ndarray; available: bool; reason: str = ""


@dataclass(frozen=True)
class TrailResult:
    exit_mask: np.ndarray; trail: np.ndarray; available: bool; reason: str = ""


def _fc(name, typ, default, grid, family, arrays, state, consumers, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays), tuple(state), tuple(consumers), source)


_FUND_WT = tuple(f"wt{x}_{tf}" for tf in ("15m", "1h", "4h", "D") for x in (1, 2))
_ARMED = ("close_{tf}", "dc_high_{tf}", "dc_low_{tf}", "bb_upper_{tf}", "bb_lower_{tf}", "wt1_{tf}", "wt2_{tf}")
_GR_EXIT = ("wt1_{tf}", "wt2_{tf}", "rsi_{tf}", "mfi_{tf}", "dc_pct_{tf}", "bb_pct_b_{tf}", "relative_volume_{tf}", "k_{tf}", "d_{tf}", "adx_{tf}", "macd_hist_{tf}", "ha_color_{tf}")


FIELD_CONTRACTS = {c.name: c for c in (
    _fc("BB_PULLBACK_GATE_TF", "str", "15m", ["15m"], "BB_PULLBACK_FILTER", ("bb_pct_b_{tf}",), ("candidate_mask",), ("ENTRY",), "vec_paths/bb_pullback_gate.py:13-28"),
    _fc("FUNDING_GATE_ENABLED", "bool", True, [True, False], "FUNDING_FILTER", ("funding_rate_{native}",) + _FUND_WT, ("candidate_mask",), ("ENTRY",), "vec_paths/funding_gate.py:45-84"),
    _fc("FUNDING_GATE_LONG_MAX", "float", .0005, [.0005], "FUNDING_FILTER", ("funding_rate_{native}",) + _FUND_WT, ("candidate_mask",), ("ENTRY",), "vec_paths/funding_gate.py:45-54"),
    _fc("FUNDING_GATE_MTF_LONG_MAX_BULL_TFS", "int", 0, [0], "FUNDING_FILTER", ("funding_rate_{native}",) + _FUND_WT, ("candidate_mask",), ("ENTRY",), "vec_paths/funding_gate.py:51-54"),
    _fc("FUNDING_GATE_MTF_REQUIRED", "bool", True, [False, True], "FUNDING_FILTER", ("funding_rate_{native}",) + _FUND_WT, ("candidate_mask",), ("ENTRY",), "vec_paths/funding_gate.py:47-54,59-66"),
    _fc("FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS", "int", 1, [1], "FUNDING_FILTER", ("funding_rate_{native}",) + _FUND_WT, ("candidate_mask",), ("ENTRY",), "vec_paths/funding_gate.py:63-66"),
    _fc("FUNDING_GATE_SHORT_MIN", "float", -.0005, [-.0005], "FUNDING_FILTER", ("funding_rate_{native}",) + _FUND_WT, ("candidate_mask",), ("ENTRY",), "vec_paths/funding_gate.py:57-66"),
    _fc("MTF_ARMED_BANDTYPES", "str", "dc,bb,wt", ["dc,bb,wt"], "MTF_ARMED_FILTER", _ARMED, ("candidate_mask",), ("ENTRY", "AUGMENT"), "vec_paths/mtf_armed_entries.py:284-368"),
    _fc("MTF_ARMED_HTF_LIST", "str", "1h,4h,D,W", ["1h,4h,D,W"], "MTF_ARMED_FILTER", _ARMED, ("candidate_mask",), ("ENTRY", "AUGMENT"), "vec_paths/mtf_armed_entries.py:284-368"),
    _fc("MTF_ATR_TRAIL_TF", "str", "15m", ["15m"], "MTF_COMPOUND_EXIT", ("close", "atr_{tf}"), ("candidate_mask", "entry_price", "previous_trail"), ("EXIT",), "vec_paths/mtf_armed_entries.py:143-213"),
    _fc("MTF_GR_EXIT_MIN_IND", "int", 5, [5], "MTF_GR_EXIT_FILTER", _GR_EXIT, ("candidate_mask",), ("EXIT",), "v12_wide_engine.py:6812-6842;mtf_live_evaluator.py:488-503"),
    _fc("MTF_GR_EXIT_MIN_TFS", "int", 3, [3], "MTF_GR_EXIT_FILTER", _GR_EXIT, ("candidate_mask",), ("EXIT",), "v12_wide_engine.py:6812-6842;mtf_live_evaluator.py:488-503"),
    _fc("MTF_WT_CROSS_EXIT_DIRECT_ENABLED", "bool", False, [True, False], "MTF_WT_EXIT_FILTER", ("wt1_15m", "wt2_15m", "wt1_15m_prev", "wt2_15m_prev", "wt1_1h", "wt2_1h", "wt1_1h_prev", "wt2_1h_prev"), ("candidate_mask",), ("EXIT",), "vec_paths/mtf_armed_entries.py:231-243"),
    _fc("MTF_WT_CROSS_EXIT_TF", "str", "15m", ["15m"], "MTF_WT_EXIT_FILTER", ("wt1_15m", "wt2_15m", "wt1_15m_prev", "wt2_15m_prev", "wt1_1h", "wt2_1h", "wt1_1h_prev", "wt2_1h_prev"), ("candidate_mask",), ("EXIT",), "vec_paths/mtf_armed_entries.py:233-243,247-257"),
)}


def _cfg(cfg, name, default=None):
    if default is None and name in FIELD_CONTRACTS: default = FIELD_CONTRACTS[name].default
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _n(*sources):
    for source in sources:
        for v in source.values():
            a=np.asarray(v)
            if a.ndim and len(a): return len(a)
    return 0


def _req(source,names,n):
    out={};missing=[];bad=[]
    for name in names:
        if name not in source:missing.append(name);continue
        a=np.asarray(source[name])
        if a.ndim==0 or len(a)!=n:bad.append(name);continue
        out[name]=a
    if missing or bad:return None,f"missing={','.join(missing)};misaligned={','.join(bad)}"
    return out,""


def _na(n,r):return MaskResult(np.zeros(n,bool),False,r)


def bb_pullback_filter_mask(arrays,state,is_long,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask",),n)
    if s is None:return _na(n,sr)
    candidate=np.asarray(s["candidate_mask"],bool)
    if not bool(_cfg(cfg,"BB_PULLBACK_GATE_ENABLED",False)):return MaskResult(candidate.copy(),True)
    tf=str(_cfg(cfg,"BB_PULLBACK_GATE_TF"));a,ar=_req(arrays,(f"bb_pct_b_{tf}",),n)
    if a is None:return _na(n,ar)
    bb=np.asarray(a[f"bb_pct_b_{tf}"],float);ok=bb<=float(_cfg(cfg,"BB_PULLBACK_GATE_LONG_MAX",.30)) if is_long else bb>=float(_cfg(cfg,"BB_PULLBACK_GATE_SHORT_MIN",.70));return MaskResult(candidate&np.isfinite(bb)&ok,True)


def funding_entry_filter_mask(arrays,state,is_long,mode,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask",),n)
    if s is None:return _na(n,sr)
    candidate=np.asarray(s["candidate_mask"],bool)
    # Funding is a crypto-only filter.  Applying it to Tradier stock NPZs
    # either consumes synthetic/missing funding arrays or vetoes every stock
    # entry before the declared entry switch can be evaluated.
    if str(mode).lower() != "crypto":
        return MaskResult(candidate.copy(), True, "funding filter not applicable to Tradier")
    if not bool(_cfg(cfg,"FUNDING_GATE_ENABLED")):return MaskResult(candidate.copy(),True)
    native="3m" if mode=="crypto" else "5m";required=(f"funding_rate_{native}",)
    if bool(_cfg(cfg,"FUNDING_GATE_MTF_REQUIRED")):required+=_FUND_WT
    a,ar=_req(arrays,required,n)
    if a is None:return _na(n,ar)
    funding=np.asarray(a[f"funding_rate_{native}"],float)
    if is_long:extreme=funding>=float(_cfg(cfg,"FUNDING_GATE_LONG_MAX"));limit=int(_cfg(cfg,"FUNDING_GATE_MTF_LONG_MAX_BULL_TFS"))
    else:extreme=funding<=float(_cfg(cfg,"FUNDING_GATE_SHORT_MIN"));limit=int(_cfg(cfg,"FUNDING_GATE_MTF_SHORT_MAX_BEAR_TFS"))
    if bool(_cfg(cfg,"FUNDING_GATE_MTF_REQUIRED")):
        aligned=np.zeros(n,np.int8)
        for tf in ("15m","1h","4h","D"):
            w1,w2=np.asarray(a[f"wt1_{tf}"],float),np.asarray(a[f"wt2_{tf}"],float);aligned+=((w1>w2) if is_long else (w1<w2)).astype(np.int8)
        extreme &= aligned<=limit
    return MaskResult(candidate&~extreme,True)


def _ffill(on,off,initial):
    out=np.zeros(len(on),bool);active=bool(initial)
    for i in range(len(on)):
        if on[i]:active=True
        if off[i]:active=False
        out[i]=active
    return out


def mtf_armed_filter_mask(arrays,state,is_long,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask",),n)
    if s is None:return _na(n,sr)
    candidate=np.asarray(s["candidate_mask"],bool)
    if not bool(_cfg(cfg,"MTF_ARMED_ENTRY_ENABLED",False)):return MaskResult(np.zeros(n,bool),True)
    tfs=tuple(x.strip() for x in str(_cfg(cfg,"MTF_ARMED_HTF_LIST")).split(',') if x.strip());bands=tuple(x.strip() for x in str(_cfg(cfg,"MTF_ARMED_BANDTYPES")).split(',') if x.strip())
    names=[]
    for tf in tfs:
        names.append(f"close_{tf}")
        for bt in bands:
            if bt=="dc":names.extend((f"dc_high_{tf}",f"dc_low_{tf}"))
            elif bt=="bb":names.extend((f"bb_upper_{tf}",f"bb_lower_{tf}"))
            elif bt=="wt":names.extend((f"wt1_{tf}",f"wt2_{tf}"))
    a,ar=_req(arrays,tuple(dict.fromkeys(names)),n)
    if a is None:return _na(n,ar)
    armed_any=np.zeros(n,bool)
    for tf in tfs:
        close=np.asarray(a[f"close_{tf}"],float)
        for bt in bands:
            if bt=="wt":
                up,dn=np.asarray(a[f"wt1_{tf}"],float),np.asarray(a[f"wt2_{tf}"],float);upp=np.r_[up[0],up[:-1]];dnp=np.r_[dn[0],dn[:-1]];on=(up>dn)&(upp<=dnp) if is_long else (up<dn)&(upp>=dnp);off=(up<dn)&(upp>=dnp) if is_long else (up>dn)&(upp<=dnp);initial=(up[0]>dn[0]) if is_long else (up[0]<dn[0])
            else:
                high=np.asarray(a[f"{'dc_high' if bt=='dc' else 'bb_upper'}_{tf}"],float);low=np.asarray(a[f"{'dc_low' if bt=='dc' else 'bb_lower'}_{tf}"],float);up,dn=(high,low) if is_long else (low,high);upp=np.r_[up[0],up[:-1]];dnp=np.r_[dn[0],dn[:-1]];on=(close>upp)&(upp>0) if is_long else (close<upp)&(upp>0);off=(close<dnp)&(dnp>0) if is_long else (close>dnp)&(dnp>0);initial=(close[0]>dn[0]) if is_long else (close[0]<dn[0])
            armed=_ffill(on,off,initial)
            if bool(_cfg(cfg,"MTF_ARMED_WT_DIRECTION_SUSPEND_ENABLED",True)):
                w1=np.asarray(a.get(f"wt1_{tf}",np.zeros(n)),float)
                if f"wt1_{tf}" not in a:return _na(n,f"missing=wt1_{tf}")
                wp=np.r_[w1[0],w1[:-1]];armed&=(w1>wp) if is_long else (w1<wp)
            armed_any|=armed
    return MaskResult(candidate&armed_any,True)


def mtf_atr_trail_exit(arrays,state,is_long,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask","entry_price","previous_trail"),n)
    if s is None:return TrailResult(np.zeros(n,bool),np.zeros(n),False,sr)
    candidate=np.asarray(s["candidate_mask"],bool);prev=np.asarray(s["previous_trail"],float)
    if not bool(_cfg(cfg,"MTF_ATR_TRAIL_ENABLED",True)):return TrailResult(np.zeros(n,bool),prev.copy(),True)
    tf=str(_cfg(cfg,"MTF_ATR_TRAIL_TF"));a,ar=_req(arrays,("close",f"atr_{tf}"),n)
    if a is None:return TrailResult(np.zeros(n,bool),np.zeros(n),False,ar)
    close,atr,entry=np.asarray(a["close"],float),np.asarray(a[f"atr_{tf}"],float),np.asarray(s["entry_price"],float);mult=float(_cfg(cfg,"MTF_ATR_TRAIL_MULT",2));raw=np.maximum(entry-mult*atr,close-mult*atr) if is_long else np.minimum(entry+mult*atr,close+mult*atr);trail=np.where(prev>0,np.maximum(prev,raw) if is_long else np.minimum(prev,raw),raw);hit=(close<trail) if is_long else (close>trail);return TrailResult(candidate&(atr>0)&(entry>0)&hit,trail,True)


def mtf_gr_exit_filter_mask(arrays,state,is_long,mode,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask",),n)
    if s is None:return _na(n,sr)
    candidate=np.asarray(s["candidate_mask"],bool)
    if not bool(_cfg(cfg,"MTF_GR_EXIT_GATE_ENABLED",True)):return MaskResult(candidate.copy(),True)
    tfs=("3m","15m","1h","4h","D","W") if mode=="crypto" else ("5m","15m","1h","4h","D","W");names=tuple(t.format(tf=tf) for tf in tfs for t in _GR_EXIT);a,ar=_req(arrays,names,n)
    if a is None:return _na(n,ar)
    passed=np.zeros(n,np.int8);minimum=int(_cfg(cfg,"MTF_GR_EXIT_MIN_IND"))
    for tf in tfs:
        w1,w2,rsi,mfi,dc,bb,rv,k,d,adx,macd,ha=(np.asarray(a[t.format(tf=tf)],float) for t in _GR_EXIT);against=not is_long;score=(((w1!=0)&(w2!=0)&((w1>w2) if against else (w1<w2)))).astype(np.int8);score+=(rsi>0)&((rsi>50) if against else (rsi<50));score+=(mfi>0)&((mfi>50) if against else (mfi<50));score+=(dc<.65) if against else (dc>.35);score+=(bb<.75) if against else (bb>.25);score+=rv>1;score+=((k>0)&(k<80)) if against else (k>20);score+=adx>20;score+=(macd>0) if against else (macd<0);score+=(ha>0) if against else (ha<0);score+=(k>d) if against else (k<d);passed+=score>=minimum
    return MaskResult(candidate&(passed>=int(_cfg(cfg,"MTF_GR_EXIT_MIN_TFS"))),True)


def mtf_wt_direct_exit_mask(arrays,state,is_long,cfg):
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask",),n)
    if s is None:return _na(n,sr)
    candidate=np.asarray(s["candidate_mask"],bool)
    if not (bool(_cfg(cfg,"MTF_WT_CROSS_EXIT_DIRECT_ENABLED")) and bool(_cfg(cfg,"MTF_WT_CROSS_EXIT_ENABLED",True))):return MaskResult(np.zeros(n,bool),True)
    selected=str(_cfg(cfg,"MTF_WT_CROSS_EXIT_TF"));tfs=("15m","1h") if selected=="either" else (selected,);names=tuple(f"wt{x}_{tf}{suffix}" for tf in tfs for suffix in ("","_prev") for x in (1,2));a,ar=_req(arrays,names,n)
    if a is None:return _na(n,ar)
    fire=np.zeros(n,bool)
    for tf in tfs:
        w1,w2,w1p,w2p=(np.asarray(a[k],float) for k in (f"wt1_{tf}",f"wt2_{tf}",f"wt1_{tf}_prev",f"wt2_{tf}_prev"));fire|=((w1<w2)&(w1p>=w2p)) if is_long else ((w1>w2)&(w1p<=w2p))
    return MaskResult(candidate&fire,True)


_FAMILY_API={"BB_PULLBACK_FILTER":"bb_pullback_filter_mask","FUNDING_FILTER":"funding_entry_filter_mask","MTF_ARMED_FILTER":"mtf_armed_filter_mask","MTF_COMPOUND_EXIT":"mtf_atr_trail_exit","MTF_GR_EXIT_FILTER":"mtf_gr_exit_filter_mask","MTF_WT_EXIT_FILTER":"mtf_wt_direct_exit_mask"}
LIFECYCLE_FILTER_APIS={n:{x:_FAMILY_API[c.family] for x in c.lifecycle_filter_consumers} for n,c in FIELD_CONTRACTS.items()}

__all__=["FIELD_CONTRACTS","LIFECYCLE_FILTER_APIS","FieldContract","MaskResult","TrailResult","bb_pullback_filter_mask","funding_entry_filter_mask","mtf_armed_filter_mask","mtf_atr_trail_exit","mtf_gr_exit_filter_mask","mtf_wt_direct_exit_mask"]
