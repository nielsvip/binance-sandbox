"""Seventh disjoint source-exact V12 filter tranche.

Vector twins for the live HTF direction execution gate, portfolio ratio/PnL
gate, and WT entry-quality gates.  Callers supply lifecycle classifications as
state arrays; no reason-label inference is performed.  An enabled predicate
with absent or misaligned native data fails closed as unavailable.
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


def _fc(name, typ, default, grid, family, arrays, state, consumers, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays),
                         tuple(state), tuple(consumers), source)


_HTF_ARRAYS = ("wt1_D", "wt2_D", "wt1_4h", "wt2_4h", "wt1_1h", "wt2_1h", "close", "sma_200_D")
_HTF_STATE = ("candidate_mask", "is_open_target", "is_augment_target", "is_rz_entry", "is_v3_bypass", "is_ratio_recovery_bypass")
_RATIO_STATE = ("candidate_mask", "current_long_value", "current_short_value", "order_notional", "long_avg_gain", "short_avg_gain", "long_count", "short_count", "is_hedge", "is_reentry", "is_rz", "is_ratio_recovery")
_WT_STATE = ("candidate_mask", "is_exit", "is_hedge")


FIELD_CONTRACTS = {c.name: c for c in (
    _fc("HTF_DIRECTION_GATE_ENABLED", "bool", False, [False, True], "HTF_DIRECTION", _HTF_ARRAYS, _HTF_STATE, ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:11973-12027,12483-12510"),
    _fc("HTF_GATE_BYPASS_RZ", "bool", True, [False, True], "HTF_DIRECTION", _HTF_ARRAYS, _HTF_STATE, ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:12486-12500"),
    _fc("HTF_GATE_D_MANDATORY", "bool", False, [False, True], "HTF_DIRECTION", _HTF_ARRAYS, _HTF_STATE, ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:11973-12027"),
    _fc("HTF_GATE_MIN_CONFIRMATIONS", "int", 2, [2], "HTF_DIRECTION", _HTF_ARRAYS, _HTF_STATE, ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:11973-12027"),
    _fc("HTF_GATE_SIGNALS_SMA200D", "bool", True, [False, True], "HTF_DIRECTION", _HTF_ARRAYS, _HTF_STATE, ("ENTRY", "REENTRY", "AUGMENT"), "ez_positions_quick.py:11973-12027"),
    _fc("RATIO_PNL_DYNAMIC_GATES_ENABLED", "bool", True, [False, True], "RATIO_PNL", (), _RATIO_STATE, ("ENTRY", "AUGMENT"), "ez_positions_quick.py:12886-12963"),
    _fc("RATIO_PNL_GATE_SOFT_MIN", "float", 0.5, [0.5], "RATIO_PNL", (), _RATIO_STATE, ("ENTRY", "AUGMENT"), "ez_positions_quick.py:12912-12925"),
    _fc("RATIO_PNL_GATE_SOFT_MAX", "float", 2.0, [2.0], "RATIO_PNL", (), _RATIO_STATE, ("ENTRY", "AUGMENT"), "ez_positions_quick.py:12912-12925"),
    _fc("WT_MTF_VEL_GATE_ENABLED", "bool", True, [False, True], "WT_ENTRY", ("wt_velocity_up_count", "wt_velocity_down_count"), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2177-2187"),
    _fc("WT_MTF_VEL_MIN", "int", 2, [2], "WT_ENTRY", ("wt_velocity_up_count", "wt_velocity_down_count"), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2177-2187"),
    _fc("WT_CHOP_GATE_ENABLED", "bool", False, [False, True], "WT_ENTRY", ("wt_cross_count_{side}_{native}", "wt_cross_count_{side}_15m", "wt_cross_count_{side}_1h"), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2188-2200"),
    _fc("WT_CHOP_MAX", "int", 8, [8], "WT_ENTRY", ("wt_cross_count_{side}_{native}", "wt_cross_count_{side}_15m", "wt_cross_count_{side}_1h"), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2188-2200"),
    _fc("WT_COMPOSITE_DELTA_GATE_ENABLED", "bool", True, [False, True], "WT_ENTRY", ("wt_composite_delta",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2201-2211"),
    _fc("WT_COMPOSITE_DELTA_LONG_MIN", "float", -100.0, [-100.0], "WT_ENTRY", ("wt_composite_delta",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2201-2211"),
    _fc("WT_COMPOSITE_DELTA_SHORT_MAX", "float", 100.0, [100.0], "WT_ENTRY", ("wt_composite_delta",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2201-2211"),
    _fc("WT_EXHAUST_ENTRY_GATE_ENABLED", "bool", False, [False, True], "WT_ENTRY", ("wt_momentum_state_{native}", "wt_momentum_state_15m"), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2212-2221"),
    _fc("WT_PERCENTILE_ENTRY_GATE_ENABLED", "bool", False, [False, True], "WT_ENTRY", ("wt_percentile_D",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2222-2230"),
    _fc("WT_PERCENTILE_ENTRY_OB_D", "float", 90.0, [90.0], "WT_ENTRY", ("wt_percentile_D",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2222-2230"),
    _fc("WT_PERCENTILE_ENTRY_OS_D", "float", 10.0, [10.0], "WT_ENTRY", ("wt_percentile_D",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2222-2230"),
    _fc("WT_DIV_ENTRY_GATE_ENABLED", "bool", True, [False, True], "WT_ENTRY", ("wt_any_bear_div", "wt_any_bull_div"), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2231-2237"),
    _fc("R_G10_HTF_DIV_GATE_ENABLED", "bool", False, [False, True], "WT_ENTRY", ("wt_divergence_{configured_tf}",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2238-2254"),
    _fc("R_G10_HTF_DIV_TFS", "str", "4h,D", ["4h,D"], "WT_ENTRY", ("wt_divergence_{configured_tf}",), _WT_STATE, ("ENTRY",), "ez_positions_quick.py:2238-2254"),
)}


def _cfg(cfg, name, default=None):
    if default is None and name in FIELD_CONTRACTS:
        default = FIELD_CONTRACTS[name].default
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _n(*sources):
    for source in sources:
        for value in source.values():
            array = np.asarray(value)
            if array.ndim and len(array):
                return len(array)
    return 0


def _req(source, names, n):
    out, missing, bad = {}, [], []
    for name in names:
        if name not in source:
            missing.append(name); continue
        value = np.asarray(source[name])
        if value.ndim == 0 or len(value) != n:
            bad.append(name); continue
        out[name] = value
    if missing or bad:
        return None, f"missing={','.join(missing)};misaligned={','.join(bad)}"
    return out, ""


def _unavailable(n, reason):
    return MaskResult(np.zeros(n, bool), False, reason)


def _eligible_result(candidate, eligible, passes):
    return MaskResult(candidate & (~eligible | passes), True)


def htf_direction_filter_mask(arrays, state, is_long, cfg):
    """Exact live HTF gate with explicit lifecycle/bypass state."""
    n = _n(arrays, state)
    s, reason = _req(state, _HTF_STATE, n)
    if s is None:
        return _unavailable(n, reason)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "HTF_DIRECTION_GATE_ENABLED")):
        return MaskResult(candidate, True)
    applies = ((np.asarray(s["is_open_target"], bool) & bool(_cfg(cfg, "HTF_GATE_APPLY_TO_OPEN", True))) |
               (np.asarray(s["is_augment_target"], bool) & bool(_cfg(cfg, "HTF_GATE_APPLY_TO_AUGMENT", False))))
    bypass = np.asarray(s["is_v3_bypass"], bool) | np.asarray(s["is_ratio_recovery_bypass"], bool)
    if bool(_cfg(cfg, "HTF_GATE_BYPASS_RZ")):
        bypass |= np.asarray(s["is_rz_entry"], bool) & ~np.asarray(s["is_v3_bypass"], bool)
    applies &= ~bypass
    if not np.any(candidate & applies):
        return MaskResult(candidate, True)
    required = list(_HTF_ARRAYS[:6])
    include_sma = bool(_cfg(cfg, "HTF_GATE_SIGNALS_SMA200D"))
    if include_sma:
        required.extend(("close", "sma_200_D"))
    a, reason = _req(arrays, required, n)
    if a is None:
        return _unavailable(n, reason)
    aligned = []
    for tf in ("D", "4h", "1h"):
        w1, w2 = np.asarray(a[f"wt1_{tf}"], float), np.asarray(a[f"wt2_{tf}"], float)
        aligned.append(((w1 > w2) if is_long else (w1 < w2)) & (w1 != 0) & (w2 != 0))
    d_ok = aligned[0]
    met = sum(x.astype(int) for x in aligned)
    if include_sma:
        close, sma = np.asarray(a["close"], float), np.asarray(a["sma_200_D"], float)
        # Live excludes a nonpositive SMA from the signal denominator; it never counts as met.
        met += (((close > sma) if is_long else (close < sma)) & (sma > 0)).astype(int)
    passes = met >= int(_cfg(cfg, "HTF_GATE_MIN_CONFIRMATIONS"))
    if bool(_cfg(cfg, "HTF_GATE_D_MANDATORY")):
        passes &= d_ok
    return _eligible_result(candidate, applies, passes)


def ratio_pnl_filter_mask(state, is_long, cfg):
    """Exact OPEN/AUGMENT L/S ratio gate with optional PnL-tightened soft limits."""
    n = _n(state)
    s, reason = _req(state, _RATIO_STATE, n)
    if s is None:
        return _unavailable(n, reason)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "LS_RATIO_ENFORCE", False)):
        return MaskResult(candidate, True)
    eligible = ~(np.asarray(s["is_hedge"], bool) | np.asarray(s["is_reentry"], bool) |
                 np.asarray(s["is_rz"], bool) | np.asarray(s["is_ratio_recovery"], bool))
    lv = np.asarray(s["current_long_value"], float); sv = np.asarray(s["current_short_value"], float)
    eligible &= (lv + sv) >= float(_cfg(cfg, "LS_RATIO_TOTAL_MIN_VALUE", 50.0))
    order = np.asarray(s["order_notional"], float)
    new_lv = lv + (order if is_long else 0.0); new_sv = sv + (0.0 if is_long else order)
    ratio = np.divide(lv, sv, out=np.where(lv > 0, 999.0, 1.0), where=sv > 0)
    new_ratio = np.divide(new_lv, new_sv, out=np.full(n, 999.0), where=new_sv > 0)
    soft_min = np.full(n, float(_cfg(cfg, "LS_RATIO_MIN", .40)))
    soft_max = np.full(n, float(_cfg(cfg, "LS_RATIO_MAX", 2.50)))
    if bool(_cfg(cfg, "RATIO_PNL_DYNAMIC_GATES_ENABLED")):
        pnl_delta = np.asarray(s["long_avg_gain"], float) - np.asarray(s["short_avg_gain"], float)
        active = ((np.abs(pnl_delta) >= float(_cfg(cfg, "RATIO_PNL_DELTA_THRESHOLD", 3.0))) &
                  (np.asarray(s["long_count"], int) > 0) & (np.asarray(s["short_count"], int) > 0))
        soft_min = np.where(active & (pnl_delta > 0), np.maximum(soft_min, float(_cfg(cfg, "RATIO_PNL_GATE_SOFT_MIN"))), soft_min)
        soft_max = np.where(active & (pnl_delta < 0), np.minimum(soft_max, float(_cfg(cfg, "RATIO_PNL_GATE_SOFT_MAX"))), soft_max)
    if is_long:
        blocked = (new_ratio > float(_cfg(cfg, "LS_RATIO_HARD_MAX", 4.0))) | ((new_ratio > soft_max) & (ratio > soft_max))
    else:
        blocked = (new_ratio < float(_cfg(cfg, "LS_RATIO_HARD_MIN", .25))) | ((new_ratio < soft_min) & (ratio < soft_min))
    return _eligible_result(candidate, eligible, ~blocked)


def wt_entry_filter_mask(arrays, state, is_long, mode, cfg):
    """Conjunction of exact WT entry boycotts; crypto=3m, Tradier=5m native."""
    n = _n(arrays, state)
    s, reason = _req(state, _WT_STATE, n)
    if s is None:
        return _unavailable(n, reason)
    candidate = np.asarray(s["candidate_mask"], bool)
    eligible = ~(np.asarray(s["is_exit"], bool) | np.asarray(s["is_hedge"], bool))
    active = candidate & eligible
    if not np.any(active):
        return MaskResult(candidate, True)
    required = []
    if bool(_cfg(cfg, "WT_MTF_VEL_GATE_ENABLED")):
        required += ["wt_velocity_up_count", "wt_velocity_down_count"]
    if bool(_cfg(cfg, "WT_CHOP_GATE_ENABLED")):
        native = "3m" if mode == "crypto" else "5m"; side = "bull" if is_long else "bear"
        required += [f"wt_cross_count_{side}_{native}", f"wt_cross_count_{side}_15m", f"wt_cross_count_{side}_1h"]
    if bool(_cfg(cfg, "WT_COMPOSITE_DELTA_GATE_ENABLED")):
        required += ["wt_composite_delta"]
    if bool(_cfg(cfg, "WT_EXHAUST_ENTRY_GATE_ENABLED")):
        native = "3m" if mode == "crypto" else "5m"; required += [f"wt_momentum_state_{native}", "wt_momentum_state_15m"]
    if bool(_cfg(cfg, "WT_PERCENTILE_ENTRY_GATE_ENABLED")):
        required += ["wt_percentile_D"]
    if bool(_cfg(cfg, "WT_DIV_ENTRY_GATE_ENABLED")):
        required += ["wt_any_bear_div", "wt_any_bull_div"]
    div_tfs = []
    if bool(_cfg(cfg, "R_G10_HTF_DIV_GATE_ENABLED")):
        div_tfs = [x.strip() for x in str(_cfg(cfg, "R_G10_HTF_DIV_TFS") or "").split(",") if x.strip()]
        required += [f"wt_divergence_{tf}" for tf in div_tfs]
    a, reason = _req(arrays, tuple(dict.fromkeys(required)), n)
    if a is None:
        return _unavailable(n, reason)
    passes = np.ones(n, bool)
    if bool(_cfg(cfg, "WT_MTF_VEL_GATE_ENABLED")):
        count = np.asarray(a["wt_velocity_up_count" if is_long else "wt_velocity_down_count"], float)
        # Live sentinel -1 is missing. The vector contract treats it as unavailable data.
        if np.any(active & (count < 0)):
            return _unavailable(n, "invalid=wt_velocity_count_negative_sentinel")
        passes &= count >= int(_cfg(cfg, "WT_MTF_VEL_MIN"))
    if bool(_cfg(cfg, "WT_CHOP_GATE_ENABLED")):
        native = "3m" if mode == "crypto" else "5m"; side = "bull" if is_long else "bear"
        chop = sum((np.asarray(a[f"wt_cross_count_{side}_{tf}"], float) >= int(_cfg(cfg, "WT_CHOP_MAX"))).astype(int) for tf in (native, "15m", "1h"))
        passes &= chop < 2
    if bool(_cfg(cfg, "WT_COMPOSITE_DELTA_GATE_ENABLED")):
        delta = np.asarray(a["wt_composite_delta"], float)
        passes &= delta >= float(_cfg(cfg, "WT_COMPOSITE_DELTA_LONG_MIN")) if is_long else delta <= float(_cfg(cfg, "WT_COMPOSITE_DELTA_SHORT_MAX"))
    if bool(_cfg(cfg, "WT_EXHAUST_ENTRY_GATE_ENABLED")):
        native = "3m" if mode == "crypto" else "5m"
        target = "EXHAUST_UP" if is_long else "EXHAUST_DOWN"
        passes &= ~((np.char.upper(np.asarray(a[f"wt_momentum_state_{native}"], str)) == target) &
                    (np.char.upper(np.asarray(a["wt_momentum_state_15m"], str)) == target))
    if bool(_cfg(cfg, "WT_PERCENTILE_ENTRY_GATE_ENABLED")):
        pct = np.asarray(a["wt_percentile_D"], float)
        passes &= pct <= float(_cfg(cfg, "WT_PERCENTILE_ENTRY_OB_D")) if is_long else pct >= float(_cfg(cfg, "WT_PERCENTILE_ENTRY_OS_D"))
    if bool(_cfg(cfg, "WT_DIV_ENTRY_GATE_ENABLED")):
        passes &= ~np.asarray(a["wt_any_bear_div" if is_long else "wt_any_bull_div"], bool)
    if div_tfs:
        target = "BEAR" if is_long else "BULL"
        against = np.logical_or.reduce([np.char.upper(np.asarray(a[f"wt_divergence_{tf}"], str)) == target for tf in div_tfs])
        passes &= ~against
    return _eligible_result(candidate, eligible, passes)


LIFECYCLE_FILTER_APIS = {}
for name, contract in FIELD_CONTRACTS.items():
    api = "htf_direction_filter_mask" if contract.family == "HTF_DIRECTION" else ("ratio_pnl_filter_mask" if contract.family == "RATIO_PNL" else "wt_entry_filter_mask")
    LIFECYCLE_FILTER_APIS[name] = {consumer: api for consumer in contract.lifecycle_filter_consumers}


__all__ = ["FIELD_CONTRACTS", "LIFECYCLE_FILTER_APIS", "FieldContract", "MaskResult", "htf_direction_filter_mask", "ratio_pnl_filter_mask", "wt_entry_filter_mask"]
