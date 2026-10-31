"""Pure native-equivalent no-loss exit hold gate."""
from __future__ import annotations

import numpy as np


def _col(state,key,n,dtype,default=None):
    if key not in state:
        if default is None: raise ValueError(f"no-loss exit requires {key}")
        raw=np.full(n,default,dtype=dtype)
    else:
        raw=np.asarray(state[key],dtype=dtype)
        if raw.ndim==0: raw=np.full(n,raw.item(),dtype=dtype)
        raw=raw.reshape(-1)
        if raw.size!=n: raise ValueError(f"{key} length {raw.size} does not match n={n}")
    return raw


def noloss_exit_gate_scalar(gain,effective_noloss_floor,is_long,ratio_available=False,ratio=1.0,ratio_max=2.5,ratio_min=.4,is_hedge_pos=False,early_exit_fired=False):
    """Return the post-fast-exit no-loss hold result and effective floor."""
    floor=float(effective_noloss_floor)
    if ratio_available:
        overweight=(is_long and ratio>ratio_max) or ((not is_long) and ratio<ratio_min)
        if overweight and gain>.3: floor=.3
    blocked=bool(not early_exit_fired and floor>0.0 and gain<floor and not is_hedge_pos)
    return blocked,floor

def noloss_exit_gate_array(state,is_long,ratio_max=2.5,ratio_min=.4):
    """Return exact no-loss hold mask and floor after earlier exit-priority bypass."""
    if "gain" not in state: raise ValueError("no-loss exit requires gain")
    gain=np.asarray(state["gain"],dtype=float)
    if gain.ndim==0: gain=gain.reshape(1)
    gain=gain.reshape(-1)
    n=gain.size
    if not n: raise ValueError("gain must be nonempty")
    floor=_col(state,"effective_noloss_floor",n,float)
    ratio_available=_col(state,"ratio_available",n,bool,False)
    ratio=_col(state,"ratio",n,float,1.0)
    is_hedge=_col(state,"is_hedge_pos",n,bool,False)
    early=_col(state,"early_exit_fired",n,bool,False)
    overweight=((ratio>ratio_max) if is_long else (ratio<ratio_min)) & ratio_available
    effective=np.where(overweight & (gain>.3),.3,floor)
    hold=(~early) & (effective>0.0) & (gain<effective) & (~is_hedge)
    return {"hold":hold,"effective_floor":effective,"overweight_trim":overweight & (gain>.3),"higher_priority_exit":early}
