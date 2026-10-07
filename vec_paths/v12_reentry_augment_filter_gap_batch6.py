"""Sixth disjoint source-exact V12 filter tranche: Golden Rule enforcement.

This is the state-complete vector twin of the live OPEN/AUGMENT gate: exact
native base WT, exact DC/BB cascade levels, exact HTF vote oracle, activation,
cooldown and target-notional state. Missing enabled-path arrays fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

# 2026-10-06: consensus moved into shared vec_decisions.golden_loop (this module delegates).


@dataclass(frozen=True)
class FieldContract:
    name: str; value_type: str; default: Any; grid: tuple[Any, ...]; family: str
    required_arrays: tuple[str, ...]; required_state: tuple[str, ...]
    lifecycle_filter_consumers: tuple[str, ...]; source: str


@dataclass(frozen=True)
class GoldenDecision:
    mask: np.ndarray; multiplier: np.ndarray; target_usd: np.ndarray
    available: bool; reason: str = ""


def _fc(name,typ,default,grid,arrays,source):
    return FieldContract(name,typ,default,tuple(grid),"GOLDEN_RULE",tuple(arrays),("candidate_mask","cooldown_ready","current_notional"),("ENTRY","AUGMENT"),source)


_CASCADE=("close","wt1_{native}","wt2_{native}")+tuple(f"{stem}_{tf}" for tf in ("15m","1h","4h","D","W") for stem in ("dc_high","dc_low","bb_upper","bb_lower"))
_VOTE=("wt1_{tf}","wt2_{tf}","rsi_{tf}","mfi_{tf}","dc_position_{tf}","bb_pct_b_{tf}","relative_volume_{tf}","stoch_k_{tf}","dc_high_{tf}","dc_low_{tf}","bb_upper_{tf}","bb_lower_{tf}","close_{tf}")
_ALL=_CASCADE+_VOTE


FIELD_CONTRACTS={c.name:c for c in (
    _fc("GOLDEN_RULE_ACTIVATION_TF_LIST","List[str]",[],[],_ALL,"golden_rule_htf.py:104-153;vec_paths/golden_rule_enforce.py:220-231"),
    _fc("GOLDEN_RULE_BASE_USD","float",5.0,[5.0],_ALL,"ez_manage.py:_golden_rule_loop;vec_paths/golden_rule_enforce.py:105,316-321"),
    _fc("GOLDEN_RULE_BB_15M_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:107-129"),
    _fc("GOLDEN_RULE_BB_1H_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:109-177"),
    _fc("GOLDEN_RULE_BB_4H_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:111-178"),
    _fc("GOLDEN_RULE_BB_D_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:113-179"),
    _fc("GOLDEN_RULE_BB_W_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:115-180"),
    _fc("GOLDEN_RULE_DC_15M_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:106-129"),
    _fc("GOLDEN_RULE_DC_1H_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:108-177"),
    _fc("GOLDEN_RULE_DC_4H_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:110-178"),
    _fc("GOLDEN_RULE_DC_D_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:112-179"),
    _fc("GOLDEN_RULE_DC_W_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:114-180"),
    _fc("GOLDEN_RULE_ENABLED","bool",True,[False,True],_ALL,"vec_paths/golden_rule_enforce.py:77-79"),
    _fc("GOLDEN_RULE_ENTRY_TF_LIST","List[str]",[],[],_ALL,"golden_rule_htf.py:104-153;vec_paths/golden_rule_enforce.py:221-231"),
    _fc("GOLDEN_RULE_MULT_15M","float",1.0,[1.0],_ALL,"vec_paths/golden_rule_enforce.py:116,146-183"),
    _fc("GOLDEN_RULE_MULT_1H","float",1.5,[1.5],_ALL,"vec_paths/golden_rule_enforce.py:117,146-183"),
    _fc("GOLDEN_RULE_MULT_4H","float",2.0,[2.0],_ALL,"vec_paths/golden_rule_enforce.py:118,146-183"),
    _fc("GOLDEN_RULE_MULT_D","float",3.0,[3.0],_ALL,"vec_paths/golden_rule_enforce.py:119,146-183"),
    _fc("GOLDEN_RULE_MULT_W","float",4.0,[4.0],_ALL,"vec_paths/golden_rule_enforce.py:120,146-183"),
    _fc("GR_TOTAL_VOTE_SCORE_MIN","int",0,[0],_ALL,"golden_rule_htf.py:104-153;vec_paths/golden_rule_enforce.py:274-289"),
)}


def _cfg(cfg,name,default=None):
    if default is None and name in FIELD_CONTRACTS:default=FIELD_CONTRACTS[name].default
    return cfg.get(name,default) if isinstance(cfg,Mapping) else getattr(cfg,name,default)


def _n(*sources):
    for source in sources:
        for v in source.values():
            a=np.asarray(v)
            if a.ndim and len(a):return len(a)
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


def _unavailable(n,reason):return GoldenDecision(np.zeros(n,bool),np.zeros(n),np.zeros(n),False,reason)


def _list_cfg(cfg,name):
    raw=_cfg(cfg,name,[])
    if isinstance(raw,str):return [x.strip() for x in raw.split(',') if x.strip()]
    return [str(x) for x in raw if str(x)]


def golden_rule_decision(arrays,state,is_long,mode,cfg):
    # 2026-10-06 USER full-parity: delegate to shared vec_decisions.golden_loop (live breakout/retest
    # phases + consensus + veto + phase mults). Replaces TF-tier cascade (dead: required wt1_3m) and the
    # path_a|path_b UNION (live loop is trigger AND consensus AND NOT veto). API/contracts unchanged.
    n=_n(arrays,state);s,sr=_req(state,("candidate_mask","cooldown_ready","current_notional"),n)
    if s is None:return _unavailable(n,sr)
    if not bool(_cfg(cfg,"GOLDEN_RULE_ENABLED")):return GoldenDecision(np.zeros(n,bool),np.zeros(n),np.zeros(n),True)
    try:
        import vec_decisions.golden_loop as _gloop
    except Exception as _gloop_exc:
        return _unavailable(n,f"golden_loop import: {_gloop_exc}")
    try:
        _ts_raw = arrays.get("timestamps") if hasattr(arrays, "get") else None
        _ts = np.asarray(_ts_raw, dtype=float) if _ts_raw is not None and len(np.asarray(_ts_raw)) == n else (np.arange(n, dtype=float) * 900.0)
        _r = _gloop.golden_entry(arrays, _ts, bool(is_long), str(mode), cfg)
    except Exception as _gloop_run_exc:
        return _unavailable(n,f"golden_loop run: {_gloop_run_exc}")
    close = np.asarray(arrays.get("close"), float) if hasattr(arrays, "get") and arrays.get("close") is not None else np.zeros(n)
    target = np.asarray(_r["target_usd"], float)
    mask = np.asarray(s["candidate_mask"],bool)&np.asarray(s["cooldown_ready"],bool)&np.asarray(_r["fire"],bool)&(np.asarray(s["current_notional"],float)<target*.8)&(close>0)
    mult = np.asarray(_r["mult"], float)
    return GoldenDecision(mask,np.where(mask,mult,0),np.where(mask,target,0),True,"GOLDEN_RULE_ENTRY")


LIFECYCLE_FILTER_APIS={name:{consumer:"golden_rule_decision" for consumer in c.lifecycle_filter_consumers} for name,c in FIELD_CONTRACTS.items()}

__all__=["FIELD_CONTRACTS","LIFECYCLE_FILTER_APIS","FieldContract","GoldenDecision","golden_rule_decision"]
