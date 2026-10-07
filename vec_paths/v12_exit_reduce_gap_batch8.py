"""Exact hedge sizing/admission and shared close filters, batch 8."""
from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence
import numpy as np


@dataclass(frozen=True)
class ExitReduceBatch8Result:
    masks: Mapping[str, np.ndarray]
    values: Mapping[str, np.ndarray]
    missing_arrays: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class LifecycleActionResult:
    masks: Mapping[str, np.ndarray]


def _cfg(c: Any, name: str, default: Any) -> Any:
    return c.get(name, default) if isinstance(c, Mapping) else getattr(c, name, default)


def _state(v: Any, n: int, name: str, dtype: Any) -> np.ndarray:
    a=np.asarray(v,dtype=dtype)
    if a.ndim==0:return np.full(n,a.item(),dtype=dtype)
    if a.ndim!=1 or len(a)!=n:raise ValueError(f"{name} must be scalar or shape ({n},)")
    return a


def evaluate_gap_batch8(
    npz: Mapping[str, Any], config: Any, *, position_side: str,
    gain_pct: Any, position_age_seconds: Any, current_price: Any,
    position_qty: Any=1.0, position_min_qty: Any=.001,
    origin_position_value_usd: Any=0.0, requested_hedge_qty: Any=0.0,
    requested_hedge_notional_usd: Any=0.0, existing_hedge_notional_usd: Any=0.0,
    opposite_position_qty: Any=0.0, close_or_reduce_requested: Any=False,
    close_reason_bypass: Any=False, hedge_daily_count: Any=0,
    elapsed_since_hedge_seconds: Any=np.inf, completed_lock_age_seconds: Any=np.inf,
    is_last_resort: Any=False, elected_hedge_succeeded: Any=False,
    effective_hedge_ratio: Any=0.0, momentum_primary_qty: Any=0.0,
    ha_3m: Any="neutral", max_position_size_usd: Any=np.inf,
    start_position_size_usd: Any=0.0, bar_count: int|None=None,
) -> ExitReduceBatch8Result:
    side=str(position_side).upper();
    if side not in {"LONG","SHORT"}:raise ValueError("position_side must be LONG or SHORT")
    long=side=="LONG"
    if bar_count is None:
        n=next((len(np.asarray(v)) for v in npz.values() if np.asarray(v).ndim==1),None)
        if n is None:raise ValueError("bar_count required without persisted arrays")
    else:n=int(bar_count)
    gain=_state(gain_pct,n,"gain_pct",float); age=_state(position_age_seconds,n,"position_age_seconds",float)
    price=_state(current_price,n,"current_price",float); qty=np.abs(_state(position_qty,n,"position_qty",float)); minq=np.abs(_state(position_min_qty,n,"position_min_qty",float))
    origin=np.abs(_state(origin_position_value_usd,n,"origin_position_value_usd",float)); reqq=np.abs(_state(requested_hedge_qty,n,"requested_hedge_qty",float)); reqn=np.abs(_state(requested_hedge_notional_usd,n,"requested_hedge_notional_usd",float)); existing=np.abs(_state(existing_hedge_notional_usd,n,"existing_hedge_notional_usd",float))
    oppq=np.abs(_state(opposite_position_qty,n,"opposite_position_qty",float)); close_req=_state(close_or_reduce_requested,n,"close_or_reduce_requested",bool); bypass=_state(close_reason_bypass,n,"close_reason_bypass",bool)
    daily=_state(hedge_daily_count,n,"hedge_daily_count",int); since=_state(elapsed_since_hedge_seconds,n,"elapsed_since_hedge_seconds",float); lockage=_state(completed_lock_age_seconds,n,"completed_lock_age_seconds",float)
    last=_state(is_last_resort,n,"is_last_resort",bool); elected=_state(elected_hedge_succeeded,n,"elected_hedge_succeeded",bool); ratio=_state(effective_hedge_ratio,n,"effective_hedge_ratio",float); primary=np.abs(_state(momentum_primary_qty,n,"momentum_primary_qty",float))
    ha=np.char.lower(_state(ha_3m,n,"ha_3m",object).astype(str)); maxusd=_state(max_position_size_usd,n,"max_position_size_usd",float); startusd=_state(start_position_size_usd,n,"start_position_size_usd",float)
    masks={};values={};missing={}
    def load(path:str,keys:Sequence[str]):
        out={};bad=[]
        for k in keys:
            a=npz.get(k)
            if a is None or np.asarray(a).ndim!=1 or len(np.asarray(a))!=n:bad.append(k);continue
            try:out[k]=np.asarray(a,dtype=float)
            except (TypeError,ValueError):bad.append(k)
        if bad:missing[path]=tuple(bad);return None
        return out

    # Shared position_evaluator hedge cap plus live absolute hard-cap path.
    pct=float(_cfg(config,"HEDGE_MAX_PCT_OF_LOSER",1.0)); abs_cap=float(_cfg(config,"HEDGE_MAX_ABSOLUTE_USD",100000.0))
    max_qty=np.where(price>0,origin*pct/price,0.0)
    values["hedge_size_cap_qty"]=np.where((origin>0)&(reqq>max_qty),max_qty,reqq)
    hard_notional=np.minimum(origin*pct,abs_cap)
    values["hedge_hard_cap_notional_usd"]=np.minimum(reqn,hard_notional)
    values["same_symbol_scan_qty"]=qty*float(_cfg(config,"HEDGE_SAME_SYMBOL_PCT",1.0))
    masks["hedge_capacity_available"]=np.isfinite(hard_notional)&(hard_notional>0)&(existing<hard_notional)

    # Binary opposite-position existence guard used by both wrapper and execute_now.
    masks["opposite_loser_close_block"]=(bool(_cfg(config,"OPPOSITE_LOSER_HEDGE_PROTECT_ENABLED",True))&close_req&~bypass&(oppq>.0001))

    # Redis/in-memory daily cap and cooldown after fresh adverse WT15 cross.
    a=load("wt15_same_hedge",("wt1_15m","wt2_15m","wt_cross_bars_ago_15m"))
    wt15=np.zeros(n,bool)
    cross_raw=npz.get("wt_cross_15m")
    cross_ok=cross_raw is not None and np.asarray(cross_raw).ndim==1 and len(np.asarray(cross_raw))==n
    if not cross_ok:
        missing["wt15_same_hedge"] = tuple(dict.fromkeys(missing.get("wt15_same_hedge",()) + ("wt_cross_15m",)))
    if a is not None and cross_ok:
        against=(a["wt1_15m"]<a["wt2_15m"]) if long else (a["wt1_15m"]>a["wt2_15m"])
        expected="BEAR" if long else "BULL"
        fresh=(a["wt_cross_bars_ago_15m"]<=5)&(np.char.upper(np.asarray(cross_raw).astype(str))==expected)
        cap=int(_cfg(config,"WT_15M_SAME_HEDGE_DAILY_CAP",2)); cooldown=int(_cfg(config,"WT_15M_SAME_HEDGE_COOLDOWN_SEC",1800))
        wt15=against&fresh&(daily<cap)&(since>=cooldown)
    masks["wt15_same_hedge_open"]=wt15

    # Completed lockout is a universal hedge-attempt debounce.
    lockout=int(_cfg(config,"HEDGE_COMPLETED_LOCKOUT_SECONDS",60))
    masks["hedge_completed_lock_block"]=lockage<lockout

    # Last-resort admission.  Only the ENABLED setting in this catalog is actually read.
    last_actual=last|((ratio>=1.0)&~elected)
    masks["quick_same_symbol_last_resort_block"]=last_actual&~bool(_cfg(config,"QUICK_HEDGE_SAME_SYM_LAST_RESORT_ENABLED",True))

    # Misnamed ENABLE_FAST_RISER_REDUCE controls a real AUGMENT route.
    a=load("fast_riser_augment",("close_3m_prev","low_3m","low_3m_prev","stoch_k_3m","stoch_d_3m"))
    fast=np.zeros(n,bool);fast_qty=np.zeros(n,float)
    if bool(_cfg(config,"ENABLE_FAST_RISER_REDUCE",True)) and bool(_cfg(config,"FAST_RISER_DOUBLE_ENABLED",False)) and not bool(_cfg(config,"ABLATION_DISABLE_FAST_RISER",False)) and a is not None:
        jump=np.where(a["close_3m_prev"]>0,(price-a["close_3m_prev"])/a["close_3m_prev"],0)
        setup=(a["close_3m_prev"]>0)&(qty>2*minq)&(gain>.8)&(a["low_3m"]>0)&(a["low_3m_prev"]>0)
        quick=(price>a["close_3m_prev"])&(np.abs(jump)>=.002)&(ha=="green")&(a["low_3m"]<a["low_3m_prev"]) if long else (price<a["close_3m_prev"])&(np.abs(jump)>=.002)&(ha=="red")&(a["low_3m"]>a["low_3m_prev"])
        newborn=(age<float(_cfg(config,"NEW_POSITION_MIN_AGE_SECONDS",180)))&(gain>float(_cfg(config,"NEW_POSITION_MAX_LOSS_THRESHOLD",-.7)))
        stoch=((a["stoch_k_3m"]<60)&(a["stoch_k_3m"]>a["stoch_d_3m"])) if long else ((a["stoch_k_3m"]>40)&(a["stoch_k_3m"]<a["stoch_d_3m"]))
        fast_qty=np.maximum(qty,np.where(price>0,2*startusd/price,0))
        maxqty=np.where(price>0,maxusd/price,np.inf)
        fast=setup&quick&~newborn&stoch&(fast_qty>0)&(qty+fast_qty<=maxqty)
    masks["fast_riser_augment"]=fast;values["fast_riser_augment_qty"]=np.where(fast,fast_qty,0)

    # Momentum-rider hedge ratio is a direct quantity multiplier.
    values["momentum_rider_hedge_qty"]=primary*float(_cfg(config,"MOMENTUM_RIDER_HEDGE_RATIO",1.2))
    masks["obligatory_hedge_loop_enabled"]=np.full(n,bool(_cfg(config,"OBLIGATORY_HEDGE_OR_CLOSE_LOOP_ENABLED",True)))
    return ExitReduceBatch8Result(MappingProxyType(masks),MappingProxyType(values),MappingProxyType(missing))


def consume_lifecycle_actions(r:ExitReduceBatch8Result)->LifecycleActionResult:
    m=r.masks;z=np.zeros_like(m["fast_riser_augment"])
    return LifecycleActionResult(MappingProxyType({"ENTRY":z,"REENTRY":z,"AUGMENT":m["fast_riser_augment"],"REDUCE":z,"EXIT":z,"STOP":z,"HEDGE_OPEN":(m["wt15_same_hedge_open"]|m["obligatory_hedge_loop_enabled"])&~m["hedge_completed_lock_block"]&~m["quick_same_symbol_last_resort_block"]&m["hedge_capacity_available"],"EXIT_FILTER_BLOCK":m["opposite_loser_close_block"],"REDUCE_FILTER_BLOCK":m["opposite_loser_close_block"]}))


__all__=["ExitReduceBatch8Result","LifecycleActionResult","consume_lifecycle_actions","evaluate_gap_batch8"]
