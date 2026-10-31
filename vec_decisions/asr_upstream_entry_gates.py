"""Account/tracker-supplied ASR entry eligibility in native first-veto order."""
from __future__ import annotations

import numpy as np


def _bool_column(state,key,n):
    if key not in state:
        raise ValueError(f"ASR upstream entry gates require explicit {key}")
    raw=np.asarray(state[key],dtype=bool)
    if raw.ndim==0:
        raw=np.full(n,bool(raw),dtype=bool)
    raw=raw.reshape(-1)
    if raw.size!=n:
        raise ValueError(f"{key} length {raw.size} does not match n={n}")
    return raw


def _float_column(state,key,n):
    if key not in state:
        raise ValueError(f"ASR upstream entry gates require explicit {key}")
    raw=np.asarray(state[key],dtype=float)
    if raw.ndim==0:
        raw=np.full(n,float(raw),dtype=float)
    raw=raw.reshape(-1)
    if raw.size!=n:
        raise ValueError(f"{key} length {raw.size} does not match n={n}")
    return raw


def asr_upstream_entry_gates(state):
    """Compute native 3996–4014 first-failure masks from explicit state.

    Account membership and BTC-dedicated account checks are input facts from
    the same frozen native authority; this function never invents a union.
    """
    if not isinstance(state,dict):
        raise ValueError("state must be an explicit mapping")
    price=_float_column(state,"current_price",len(np.asarray(state.get("current_price",[])).reshape(-1)))
    n=price.size
    if n<1: raise ValueError("current_price must be nonempty")
    has_keys=_bool_column(state,"tradeable_key_set_nonempty",n)
    member=_bool_column(state,"tradeable_position_member",n)
    temp=_bool_column(state,"temp_key_member",n)
    position=_bool_column(state,"position_exists",n)
    amount=_float_column(state,"position_amt",n)
    btc_block=_bool_column(state,"btc_dedicated_account_blocked",n)
    allowed=_bool_column(state,"is_allowed",n)
    tradeable=~has_keys | member | temp
    masks={}
    reasons=np.full(n,"",dtype=object)
    def first(name,fire,reason):
        fire=np.asarray(fire,dtype=bool).reshape(-1)
        if fire.size!=n: raise ValueError(f"{name} length mismatch")
        chosen=(reasons=="") & fire
        masks[name]=chosen
        reasons[chosen]=reason
    first("NOT_TRADEABLE",~tradeable & (~position | (amount<=0.0)),"NOT A TRADEABLE KEY")
    first("BTC_HARD_BLOCKED",btc_block & (~position | (amount<=0.0)),"BTC_HARD_BLOCKED")
    first("NOT_ALLOWED",~allowed,"Not Allowed")
    first("NO_CURRENT_PRICE",price<=0.0,"No current_price")
    return {"eligible":reasons=="","reason":reasons,"tradeable":tradeable,"masks":masks}
