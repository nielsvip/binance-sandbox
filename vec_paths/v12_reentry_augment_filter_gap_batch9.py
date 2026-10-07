"""Ninth disjoint source-exact V12 ENTRY/REENTRY/AUGMENT tranche.

The generic score switches here are crypto-only in the live source and use
the real 3m route.  Tradier has separately named ``*_TRADIER`` switches, so
this module reports an honest unsupported-mode result instead of mapping the
generic fields to 5m.  OI and HTF execution predicates cover the live
OPEN/REENTRY/AUGMENT action set.  Enabled missing inputs fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from vec_paths.v12_reentry_augment_filter_gap_batch7 import htf_direction_filter_mask


@dataclass(frozen=True)
class FieldContract:
    name: str; value_type: str; default: Any; grid: tuple[Any, ...]; family: str
    required_arrays: tuple[str, ...]; required_state: tuple[str, ...]
    lifecycle_filter_consumers: tuple[str, ...]; supported_modes: tuple[str, ...]; source: str


@dataclass(frozen=True)
class EntryDecision:
    mask: np.ndarray; score_delta: np.ndarray; available: bool; reason: str = ""


def _fc(name, typ, default, grid, family, arrays, state, consumers, modes, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays), tuple(state), tuple(consumers), tuple(modes), source)


_BASE_STATE = ("candidate_mask", "is_exit", "mts_bottom_score", "mts_entry_quality")
_MTS = ()
_MF = ("high_3m", "low_3m", "atr_3m", "relative_volume_3m", "relative_volume_15m", "k_3m")
_MI = ("wt_trough_structure_1h", "wt_trough_structure_4h", "wt_peak_structure_1h", "wt_peak_structure_4h", "wt_momentum_state_1h", "wt_momentum_state_4h", "wt_divergence_1h")
_KZ = ("k_3m", "k_3m_prev", "ha_3m", "ha_3m_prev", "ha_15m")
_SENT = ("0sentiment_rank", "0ranking_points")
_OI = ("oi_change_1h_pct", "close", "close_1h_prev")
_HTF = ("wt1_D", "wt2_D", "wt1_4h", "wt2_4h", "wt1_1h", "wt2_1h", "close", "sma_200_D")
_HTF_STATE = ("candidate_mask", "is_open_target", "is_augment_target", "is_rz_entry", "is_v3_bypass", "is_ratio_recovery_bypass")


FIELD_CONTRACTS = {c.name: c for c in (
    _fc("MTS_GATE_ENABLED", "bool", True, [False, True], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2683-2703;analyze_multi_tf_state:693-897"),
    _fc("MTS_BOTTOM_MIN", "float", 15.0, [15.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2683-2703"),
    _fc("MTS_BOTTOM_MIN_SHORT", "float", 10.0, [10.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2683-2703"),
    _fc("MTS_ENTRY_QUALITY_MIN", "float", 8.0, [8.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2683-2703"),
    _fc("MTS_ENTRY_QUALITY_MIN_SHORT", "float", 5.0, [5.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2683-2703"),
    _fc("MTS_BOTTOM_STRONG_THRESHOLD", "float", 40.0, [40.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2695-2703"),
    _fc("MTS_BOTTOM_BONUS_THRESHOLD", "float", 25.0, [25.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2695-2703"),
    _fc("MTS_ENTRY_QUALITY_STRONG", "float", 40.0, [40.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2695-2703"),
    _fc("MTS_ENTRY_QUALITY_BONUS", "float", 25.0, [25.0], "MTS_ENTRY", _MTS, _BASE_STATE, ("ENTRY",), ("crypto",), "ez_positions_quick.py:2695-2703"),
    _fc("MI_ENTRY_ENABLED", "bool", False, [False, True], "MI_ENTRY", _MI, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2704-2724"),
    _fc("MI_ENTRY_STRUCT_BONUS", "int", 10, [10], "MI_ENTRY", _MI, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2704-2724"),
    _fc("MI_ENTRY_EXHAUST_BONUS", "int", 8, [8], "MI_ENTRY", _MI, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2704-2724"),
    _fc("K_ZONE_ENTRY_BONUS", "int", 25, [25], "K_ZONE_ENTRY", _KZ, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2866-2883"),
    _fc("MOMENTUM_FADE_ENABLED", "bool", False, [False, True], "MOMENTUM_FADE", _MF, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2892-2910"),
    _fc("MOMENTUM_FADE_BODY_ATR_MIN", "float", 2.0, [2.0], "MOMENTUM_FADE", _MF, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2892-2910"),
    _fc("MOMENTUM_FADE_VOL_MIN", "float", 2.0, [2.0], "MOMENTUM_FADE", _MF, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2892-2910"),
    _fc("MOMENTUM_FADE_K_ZONE", "bool", True, [False, True], "MOMENTUM_FADE", _MF, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2892-2910"),
    _fc("MOMENTUM_FADE_SCORE_BONUS", "int", 35, [35], "MOMENTUM_FADE", _MF, ("candidate_mask", "is_exit"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2892-2910"),
    _fc("SENTIMENT_TOP_N_GATE_ENABLED", "bool", False, [False, True], "SENTIMENT_RANK", _SENT, ("candidate_mask", "is_exit", "is_hedge"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2160-2176"),
    _fc("SENTIMENT_TOP_N", "int", 20, [20], "SENTIMENT_RANK", _SENT, ("candidate_mask", "is_exit", "is_hedge"), ("ENTRY",), ("crypto",), "ez_positions_quick.py:2160-2176"),
    _fc("OI_CONFIRM_ENABLED", "bool", True, [False, True], "OI_CONFIRM", _OI, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), ("crypto", "tradier"), "ez_positions_quick.py:12304,12503-12542"),
    _fc("OI_CONFIRM_MIN_CHANGE_PCT", "float", 0.5, [0.5], "OI_CONFIRM", _OI, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), ("crypto", "tradier"), "ez_positions_quick.py:12503-12542"),
    _fc("OI_CONFIRM_MIN_PRICE_PCT", "float", 0.3, [0.3], "OI_CONFIRM", _OI, ("candidate_mask",), ("ENTRY", "REENTRY", "AUGMENT"), ("crypto", "tradier"), "ez_positions_quick.py:12503-12542"),
    _fc("HTF_GATE_APPLY_TO_OPEN", "bool", True, [False, True], "HTF_OPEN_ROUTE", _HTF, _HTF_STATE, ("ENTRY", "REENTRY"), ("crypto", "tradier"), "ez_positions_quick.py:12478-12503;batch7.htf_direction_filter_mask"),
)}


def _cfg(cfg, name, default=None):
    if default is None and name in FIELD_CONTRACTS: default = FIELD_CONTRACTS[name].default
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _n(*sources):
    for source in sources:
        for value in source.values():
            array = np.asarray(value)
            if array.ndim and len(array): return len(array)
    return 0


def _req(source, names, n):
    out, missing, bad = {}, [], []
    for name in names:
        if name not in source: missing.append(name); continue
        value = np.asarray(source[name])
        if value.ndim == 0 or len(value) != n: bad.append(name); continue
        out[name] = value
    if missing or bad: return None, f"missing={','.join(missing)};misaligned={','.join(bad)}"
    return out, ""


def _unavailable(n, reason): return EntryDecision(np.zeros(n, bool), np.zeros(n), False, reason)


def crypto_entry_score_decision(arrays, state, is_long, mode, cfg):
    """Exact generic crypto score path; Tradier generic fields are semantic N/A."""
    n = _n(arrays, state)
    if mode != "crypto": return _unavailable(n, "unsupported_mode=tradier;use_*_TRADIER_fields")
    s, reason = _req(state, ("candidate_mask", "is_exit", "mts_bottom_score", "mts_entry_quality"), n)
    if s is None: return _unavailable(n, reason)
    candidate = np.asarray(s["candidate_mask"], bool); eligible = candidate & ~np.asarray(s["is_exit"], bool)
    if not np.any(eligible): return EntryDecision(candidate, np.zeros(n), True)
    bottom = np.asarray(s["mts_bottom_score"], float); quality = np.asarray(s["mts_entry_quality"], float)
    passes = np.ones(n, bool); score = np.zeros(n)
    if bool(_cfg(cfg, "MTS_GATE_ENABLED")):
        bmin = float(_cfg(cfg, "MTS_BOTTOM_MIN" if is_long else "MTS_BOTTOM_MIN_SHORT"))
        qmin = float(_cfg(cfg, "MTS_ENTRY_QUALITY_MIN" if is_long else "MTS_ENTRY_QUALITY_MIN_SHORT"))
        passes &= (bottom >= bmin) & (quality >= qmin)
    score += np.where(bottom > float(_cfg(cfg, "MTS_BOTTOM_STRONG_THRESHOLD")), 8,
                      np.where(bottom > float(_cfg(cfg, "MTS_BOTTOM_BONUS_THRESHOLD")), 4, 0))
    score += np.where(quality > float(_cfg(cfg, "MTS_ENTRY_QUALITY_STRONG")), 5,
                      np.where(quality > float(_cfg(cfg, "MTS_ENTRY_QUALITY_BONUS")), 2, 0))

    if bool(_cfg(cfg, "MI_ENTRY_ENABLED")):
        a, reason = _req(arrays, _MI, n)
        if a is None: return _unavailable(n, reason)
        struct = float(_cfg(cfg, "MI_ENTRY_STRUCT_BONUS")); exhaust = float(_cfg(cfg, "MI_ENTRY_EXHAUST_BONUS"))
        if is_long:
            score += struct * ((np.asarray(a["wt_trough_structure_1h"], str) == "HL").astype(int) + (np.asarray(a["wt_trough_structure_4h"], str) == "HL").astype(int))
            score += exhaust * ((np.asarray(a["wt_momentum_state_1h"], str) == "EXHAUST_DOWN").astype(int) + (np.asarray(a["wt_momentum_state_4h"], str) == "EXHAUST_DOWN").astype(int) + (np.asarray(a["wt_divergence_1h"], str) == "BULL").astype(int))
        else:
            score += struct * ((np.asarray(a["wt_peak_structure_1h"], str) == "LH").astype(int) + (np.asarray(a["wt_peak_structure_4h"], str) == "LH").astype(int))
            score += exhaust * ((np.asarray(a["wt_momentum_state_1h"], str) == "EXHAUST_UP").astype(int) + (np.asarray(a["wt_momentum_state_4h"], str) == "EXHAUST_UP").astype(int) + (np.asarray(a["wt_divergence_1h"], str) == "BEAR").astype(int))

    if bool(_cfg(cfg, "K_ZONE_ENTRY_ENABLED", True)):
        a, reason = _req(arrays, _KZ, n)
        if a is None: return _unavailable(n, reason)
        k, prev = np.asarray(a["k_3m"], float), np.asarray(a["k_3m_prev"], float)
        ha, hap, ha15 = (np.char.lower(np.asarray(a[x], str)) for x in ("ha_3m", "ha_3m_prev", "ha_15m"))
        if is_long:
            event = (k < float(_cfg(cfg, "K_ZONE_LONG_THRESHOLD", 35))) & (k > prev) & (((ha == "green") & (hap != "green")) | ((ha == "green") & (ha15 == "green")))
        else:
            event = (k > float(_cfg(cfg, "K_ZONE_SHORT_THRESHOLD", 65))) & (k < prev) & (((ha == "red") & (hap != "red")) | ((ha == "red") & (ha15 == "red")))
        score += event * float(_cfg(cfg, "K_ZONE_ENTRY_BONUS"))

    if bool(_cfg(cfg, "MOMENTUM_FADE_ENABLED")):
        a, reason = _req(arrays, _MF, n)
        if a is None: return _unavailable(n, reason)
        high, low, atr = (np.asarray(a[x], float) for x in ("high_3m", "low_3m", "atr_3m"))
        ratio = np.divide(high - low, atr, out=np.zeros(n), where=(atr > 0) & (high > 0) & (low > 0))
        volume = np.maximum(np.asarray(a["relative_volume_3m"], float), np.asarray(a["relative_volume_15m"], float))
        event = (ratio >= float(_cfg(cfg, "MOMENTUM_FADE_BODY_ATR_MIN"))) & (volume >= float(_cfg(cfg, "MOMENTUM_FADE_VOL_MIN")))
        if bool(_cfg(cfg, "MOMENTUM_FADE_K_ZONE")):
            k = np.asarray(a["k_3m"], float); event &= (k < 40) if is_long else (k > 60)
        score += event * float(_cfg(cfg, "MOMENTUM_FADE_SCORE_BONUS"))

    if bool(_cfg(cfg, "SENTIMENT_TOP_N_GATE_ENABLED")):
        ss, reason = _req(state, ("is_hedge",), n)
        if ss is None: return _unavailable(n, reason)
        a, reason = _req(arrays, _SENT, n)
        if a is None: return _unavailable(n, reason)
        applies = eligible & ~np.asarray(ss["is_hedge"], bool); rank = np.asarray(a["0sentiment_rank"], int)
        points = np.asarray(a["0ranking_points"], float); top = int(_cfg(cfg, "SENTIMENT_TOP_N"))
        blocked = ((rank > 0) & (rank > top)) if is_long else ((rank > 0) & (points >= 0) & (points > top * 100.0 / 350.0))
        passes &= ~applies | ~blocked

    score = np.where(eligible, score, 0)
    return EntryDecision(candidate & (~eligible | passes), score, True)


def oi_confirmation_mask(arrays, state, is_long, cfg):
    """Exact four-quadrant OI×price execution veto."""
    n = _n(arrays, state); s, reason = _req(state, ("candidate_mask",), n)
    if s is None: return _unavailable(n, reason)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "OI_CONFIRM_ENABLED")): return EntryDecision(candidate, np.zeros(n), True)
    a, reason = _req(arrays, _OI, n)
    if a is None: return _unavailable(n, reason)
    oi = np.asarray(a["oi_change_1h_pct"], float); close = np.asarray(a["close"], float); prev = np.asarray(a["close_1h_prev"], float)
    if np.any(candidate & (prev <= 0)): return _unavailable(n, "invalid=close_1h_prev_nonpositive")
    price_change = np.divide(close - prev, prev, out=np.zeros(n), where=prev > 0) * 100.0
    active = (np.abs(oi) >= float(_cfg(cfg, "OI_CONFIRM_MIN_CHANGE_PCT"))) & (np.abs(price_change) >= float(_cfg(cfg, "OI_CONFIRM_MIN_PRICE_PCT")))
    blocked = active & (((price_change > 0) != (oi > 0)) if is_long else ((price_change > 0) == (oi > 0)))
    return EntryDecision(candidate & ~blocked, np.zeros(n), True)


def htf_open_route_mask(arrays, state, is_long, cfg):
    result = htf_direction_filter_mask(arrays, state, is_long, cfg)
    return EntryDecision(result.mask, np.zeros(len(result.mask)), result.available, result.reason)


LIFECYCLE_FILTER_APIS = {}
for name, contract in FIELD_CONTRACTS.items():
    api = "oi_confirmation_mask" if contract.family == "OI_CONFIRM" else ("htf_open_route_mask" if contract.family == "HTF_OPEN_ROUTE" else "crypto_entry_score_decision")
    LIFECYCLE_FILTER_APIS[name] = {consumer: api for consumer in contract.lifecycle_filter_consumers}

__all__ = ["FIELD_CONTRACTS", "LIFECYCLE_FILTER_APIS", "EntryDecision", "crypto_entry_score_decision", "oi_confirmation_mask", "htf_open_route_mask"]
