"""Source-exact vector twins for a second V12 re-entry/augment gap tranche.

The functions in this module are pure: callers supply persisted indicator
arrays and explicit lifecycle state arrays.  Missing or length-mismatched data
returns ``available=False`` with all causal outputs disabled.  No timeframe
substitution, synthetic signal, hash, reason-label, or external/live lookup is
performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class FieldContract:
    name: str
    value_type: str
    default: Any
    grid: tuple[Any, ...]
    family: str
    required_arrays: tuple[str, ...]
    required_state: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class MaskResult:
    mask: np.ndarray
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class DecisionResult:
    mask: np.ndarray
    size_mult: np.ndarray
    score: np.ndarray
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class StructureFlipResult:
    exit_mask: np.ndarray
    reentry_mask: np.ndarray
    available: bool
    reason: str = ""


def _fc(name, typ, default, grid, family, arrays, state, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays), tuple(state), source)


_OBL_ARRAYS = (
    "close", "ema_50_15m", "ema_50_15m_prev", "stoch_k_15m",
    "dc_high4_3m", "dc_low4_3m", "wt_bullish_3m", "wt_bullish_15m",
    "wt_bullish_1h", "wt_bullish_4h", "wt_bullish_D",
)
_OBL_STATE = ("candidate_mask", "exit_price", "prev_close")
_FRESH_ARRAYS = ("wt_cross_bars_ago_3m", "wt_cross_bars_ago_15m", "wt_cross_bars_ago_1h")
_B16_SMA_ARRAYS = ("close", "sma_200_1h", "k_1h", "k_4h", "wt1_1h", "wt2_1h", "wt_velocity_1h")
_B16_MID_ARRAYS = ("close", "dc_high_1h", "dc_low_1h", "wt1_15m", "wt2_15m", "wt_velocity_15m", "k_15m")
_RECLAIM_ARRAYS = ("close", "ema_9_15m", "k_3m", "k_3m_prev", "wt1_15m", "wt2_15m", "wt_velocity_15m", "k_15m")
_GR_ARRAYS = tuple(
    ["close", "dc_high_15m", "dc_low_15m"]
    + [f"{kind}_{tf}{suffix}" for tf in ("1h", "4h", "D") for kind in ("high", "low") for suffix in ("", "_prev")]
    + [f"wt{x}_{tf}" for tf in ("1h", "4h", "D") for x in (1, 2)]
)
_POST_ARRAYS = ("bar_atr_rank_1h", "bar_atr_rank_4h", "bar_atr_rank_D")
_WT15_ARRAYS = ("wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h", "wt1_4h", "wt2_4h") + _POST_ARRAYS
_STRUCT_ARRAYS = ("close", "high_15m", "high_15m_prev", "low_15m", "low_15m_prev", "dc_basis_4h")
_TIER_ARRAYS = ("close", "k_3m", "k_3m_prev", "k_1m", "d_1m")


FIELD_CONTRACTS: dict[str, FieldContract] = {
    c.name: c for c in (
        _fc("OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT", "float", 1.0, [1.0], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:312"),
        _fc("OBLIGATORY_REENTRY_ENABLED", "bool", True, [False, True], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:266"),
        _fc("OBLIGATORY_REENTRY_K15_HIGH_BLOCK", "float", 95.0, [95.0], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:333"),
        _fc("OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC", "float", 0.5, [0.5], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:334"),
        _fc("OBLIGATORY_REENTRY_LONG_ENABLED", "bool", True, [False, True], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:268-270"),
        _fc("OBLIGATORY_REENTRY_SCORE_TIER1", "int", 40, [40], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:307"),
        _fc("OBLIGATORY_REENTRY_SCORE_TIER2", "int", 30, [30], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:308"),
        _fc("OBLIGATORY_REENTRY_SCORE_TIER3", "int", 30, [30], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:309"),
        _fc("OBLIGATORY_REENTRY_SHORT_ENABLED", "bool", True, [False, True], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:268-270"),
        _fc("OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK", "float", 5.0, [5.0], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:339"),
        _fc("OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC", "float", 0.5, [0.5], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:340"),
        _fc("OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT", "float", 1.5, [1.5], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:310-311"),
        _fc("OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT", "float", 1.5, [1.5], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:310-311"),
        _fc("OBLIGATORY_REENTRY_SMA_FIELD", "str", "ema_50", ["ema_50"], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:277"),
        _fc("OBLIGATORY_REENTRY_SMA_TF", "str", "15m", ["15m"], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:276"),
        _fc("OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED", "int", 3, [3], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:305"),
        _fc("OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED", "int", 1, [1], "OBLIGATORY_REENTRY", _OBL_ARRAYS, _OBL_STATE, "ez_reentry.py:306"),
        _fc("REENTRY_CROSS_FRESHNESS_ENABLED", "bool", False, [False, True], "CROSS_FRESHNESS", _FRESH_ARRAYS, ("candidate_mask",), "ez_positions_quick.py:16663"),
        _fc("REENTRY_CROSS_MAX_BARS_AGO", "int", 5, [2, 3, 5], "CROSS_FRESHNESS", _FRESH_ARRAYS, ("candidate_mask",), "ez_positions_quick.py:16664-16670"),
        _fc("REENTRY_B16_MIDRANGE_ENABLED", "bool", True, [False, True], "B16_MIDRANGE", _B16_MID_ARRAYS, (), "position_evaluator.py:222-230,370-379"),
        _fc("REENTRY_B16_SMA200_PULLBACK_ENABLED", "bool", True, [False, True], "B16_SMA200", _B16_SMA_ARRAYS, (), "ez_positions_quick.py:16784"),
        _fc("REENTRY_B16_SMA200_PROX_PCT", "float", 0.005, [0.005], "B16_SMA200", _B16_SMA_ARRAYS, (), "ez_positions_quick.py:16789"),
        _fc("REENTRY_B16_SIZE_MULT_STRONG", "float", 3.0, [3.0], "B16_SMA200", _B16_SMA_ARRAYS, (), "ez_positions_quick.py:16795"),
        _fc("REENTRY_B16_SIZE_MULT_WEAK", "float", 1.5, [1.5], "B16_SMA200", _B16_SMA_ARRAYS, (), "ez_positions_quick.py:16795"),
        _fc("REENTRY_EXIT_RECLAIM_ENABLED", "bool", True, [False, True], "EXIT_RECLAIM", _RECLAIM_ARRAYS, ("candidate_mask", "exit_price"), "ez_manage.py:32857"),
        _fc("REENTRY_EXIT_RECLAIM_BUFFER_PCT", "float", 0.2, [0.2], "EXIT_RECLAIM", _RECLAIM_ARRAYS, ("candidate_mask", "exit_price"), "ez_manage.py:32860-32886"),
        _fc("REENTRY_GR_HLHH_MODE", "str", "OR", ["OR", "HH", "HL"], "OVERDUE_GR", _GR_ARRAYS, ("candidate_mask",), "tradier_manage.py:20179-20202"),
        _fc("REENTRY_GR_MIN_TFS", "int", 2, [1, 2, 3], "OVERDUE_GR", _GR_ARRAYS, ("candidate_mask",), "tradier_manage.py:20180-20202"),
        _fc("REENTRY_POST_CONSOL_ENABLED", "bool", True, [False, True], "POST_CONSOL", _POST_ARRAYS, ("candidate_mask", "base_multiplier"), "ez_manage.py:36491"),
        _fc("REENTRY_POST_CONSOL_ATR_THRESHOLD", "float", 0.15, [0.15], "POST_CONSOL", _POST_ARRAYS, ("candidate_mask", "base_multiplier"), "ez_manage.py:36493"),
        _fc("REENTRY_POST_CONSOL_TFS_REQUIRED", "int", 2, [2], "POST_CONSOL", _POST_ARRAYS, ("candidate_mask", "base_multiplier"), "ez_manage.py:36495"),
        _fc("REENTRY_POST_CONSOL_MULT", "float", 1.5, [1.5], "POST_CONSOL", _POST_ARRAYS, ("candidate_mask", "base_multiplier"), "ez_manage.py:36505"),
        _fc("REENTRY_WT15M_CROSS_ENABLED", "bool", True, [False, True], "WT15M_REENTRY", _WT15_ARRAYS, ("candidate_mask", "price_ready"), "ez_manage.py:36513"),
        _fc("REENTRY_WT15M_HTF_FAVOR_REQUIRED", "bool", True, [False, True], "WT15M_REENTRY", _WT15_ARRAYS, ("candidate_mask", "price_ready"), "ez_manage.py:36531"),
        _fc("REENTRY_WT15M_SIZE_MULT", "float", 1.5, [1.5], "WT15M_REENTRY", _WT15_ARRAYS, ("candidate_mask", "price_ready"), "ez_manage.py:36543"),
        _fc("SCALP_V3_AUG_BE_STOP_ENABLED", "bool", True, [False, True], "SCALP_V3_AUG_BE_STOP", (), ("candidate_mask", "is_v3", "position_amount", "has_augmentation", "gain_pct", "max_gain_pct"), "ez_positions_quick.py:18454"),
        _fc("SCALP_V3_AUG_BE_STOP_PCT", "float", 0.1, [0.1], "SCALP_V3_AUG_BE_STOP", (), ("candidate_mask", "is_v3", "position_amount", "has_augmentation", "gain_pct", "max_gain_pct"), "ez_positions_quick.py:18456-18477"),
        _fc("STRUCTURE_FLIP_REENTRY_ENABLED", "bool", False, [False, True], "STRUCTURE_FLIP", _STRUCT_ARRAYS, ("candidate_mask", "has_exit_signature", "signature_changed", "market_open", "has_opposing_position"), "tradier_manage.py:6607-6630,13296-13319"),
        _fc("STRUCTURE_FLIP_REENTRY_TF", "str", "15m", ["15m"], "STRUCTURE_FLIP", _STRUCT_ARRAYS, ("candidate_mask", "has_exit_signature", "signature_changed", "market_open", "has_opposing_position"), "tradier_manage.py:6608,13297"),
        _fc("STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED", "bool", False, [False, True], "STRUCTURE_FLIP", _STRUCT_ARRAYS, ("candidate_mask", "has_exit_signature", "signature_changed", "market_open", "has_opposing_position"), "tradier_manage.py:6609,13298"),
        _fc("STRUCTURE_FLIP_REENTRY_BASIS_TF", "str", "4h", ["4h"], "STRUCTURE_FLIP", _STRUCT_ARRAYS, ("candidate_mask", "has_exit_signature", "signature_changed", "market_open", "has_opposing_position"), "tradier_manage.py:6610,13299"),
        _fc("GUARANTEED_REENTRY_TIGHT_STOP_ENABLED", "bool", True, [False, True], "GUARANTEED_TIGHT_STOP", (), ("candidate_mask", "position_amount", "position_min_qty", "is_guaranteed_reentry", "gain_pct", "age_seconds"), "ez_manage.py:43128"),
        _fc("GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S", "float", 1800.0, [1800.0], "GUARANTEED_TIGHT_STOP", (), ("candidate_mask", "position_amount", "position_min_qty", "is_guaranteed_reentry", "gain_pct", "age_seconds"), "ez_manage.py:43164"),
        _fc("GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S", "float", 60.0, [60.0], "GUARANTEED_TIGHT_STOP", (), ("candidate_mask", "position_amount", "position_min_qty", "is_guaranteed_reentry", "gain_pct", "age_seconds"), "ez_manage.py:43161"),
        _fc("GUARANTEED_REENTRY_TIGHT_STOP_PCT", "float", 0.5, [0.5], "GUARANTEED_TIGHT_STOP", (), ("candidate_mask", "position_amount", "position_min_qty", "is_guaranteed_reentry", "gain_pct", "age_seconds"), "ez_manage.py:43146"),
        _fc("BOUNCE_REENTRY_ENABLED", "bool", True, [False, True], "BOUNCE_REENTRY_PARENT", (), ("candidate_mask", "is_exit", "minutes_since_reduction"), "ez_positions_quick.py:3100,3106"),
        _fc("REENTRY_PRICE_IMPROVE_PCT", "float", 0.08, [0.08], "REENTRY_PRICE_IMPROVEMENT", ("close",), ("candidate_mask", "reentry_level"), "ez_manage.py:36028-36047"),
        _fc("REENTRY_TIER2_MAX_MINUTES", "float", 120.0, [120.0], "TIERED_REENTRY", _TIER_ARRAYS, ("candidate_mask", "exit_price", "minutes_since_exit"), "ez_positions_quick.py:15500"),
        _fc("REENTRY_TIER1_SIZE_MULT", "float", 1.5, [1.5], "TIERED_REENTRY", _TIER_ARRAYS, ("candidate_mask", "exit_price", "minutes_since_exit"), "ez_positions_quick.py:16091"),
        _fc("REENTRY_EXHAUSTED_PARTIAL_ENABLED", "bool", True, [False, True], "TIERED_REENTRY", _TIER_ARRAYS, ("candidate_mask", "exit_price", "minutes_since_exit"), "ez_positions_quick.py:15512"),
    )
}


def _cfg(cfg: Any, name: str) -> Any:
    default = FIELD_CONTRACTS[name].default
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _n(*sources: Mapping[str, Any]) -> int:
    for source in sources:
        for value in source.values():
            arr = np.asarray(value)
            if arr.ndim and len(arr):
                return len(arr)
    return 0


def _require(source: Mapping[str, Any], names: Sequence[str], n: int):
    out, missing, bad = {}, [], []
    for name in names:
        if name not in source:
            missing.append(name); continue
        arr = np.asarray(source[name])
        if arr.ndim == 0 or len(arr) != n:
            bad.append(name); continue
        out[name] = arr
    if missing or bad:
        reason = ("missing=" + ",".join(missing) if missing else "")
        reason += ((";" if reason else "") + "misaligned=" + ",".join(bad) if bad else "")
        return None, reason
    return out, ""


def _mask_unavailable(n: int, reason: str) -> MaskResult:
    return MaskResult(np.zeros(n, bool), False, reason)


def _decision_unavailable(n: int, reason: str) -> DecisionResult:
    return DecisionResult(np.zeros(n, bool), np.zeros(n, float), np.zeros(n, float), False, reason)


def obligatory_reentry_decision(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> DecisionResult:
    n = _n(arrays, state)
    sma_tf, sma_field = str(_cfg(cfg, "OBLIGATORY_REENTRY_SMA_TF")), str(_cfg(cfg, "OBLIGATORY_REENTRY_SMA_FIELD"))
    names = tuple(x for x in _OBL_ARRAYS if not x.startswith("ema_50_15m")) + (f"{sma_field}_{sma_tf}", f"{sma_field}_{sma_tf}_prev")
    a, ar = _require(arrays, names, n); s, sr = _require(state, _OBL_STATE, n)
    if a is None or s is None: return _decision_unavailable(n, ar or sr)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "OBLIGATORY_REENTRY_ENABLED")) or not bool(_cfg(cfg, "OBLIGATORY_REENTRY_LONG_ENABLED" if is_long else "OBLIGATORY_REENTRY_SHORT_ENABLED")):
        return DecisionResult(np.zeros(n, bool), np.zeros(n), np.zeros(n), True)
    close, exit_px, prev_close = np.asarray(a["close"], float), np.asarray(s["exit_price"], float), np.asarray(s["prev_close"], float)
    sma, sma_prev = np.asarray(a[f"{sma_field}_{sma_tf}"], float), np.asarray(a[f"{sma_field}_{sma_tf}_prev"], float)
    bounce = (sma > 0) & ((close > sma) if is_long else (close < sma)) & (((prev_close <= 0) | (prev_close < sma_prev)) if is_long else ((prev_close <= 0) | (prev_close > sma_prev)))
    htf = sum((np.asarray(a[f"wt_bullish_{tf}"], bool) if is_long else ~np.asarray(a[f"wt_bullish_{tf}"], bool)).astype(np.int8) for tf in ("3m", "15m", "1h", "4h", "D"))
    align3 = np.asarray(a["wt_bullish_3m"], bool) if is_long else ~np.asarray(a["wt_bullish_3m"], bool)
    dc = np.asarray(a["dc_high4_3m" if is_long else "dc_low4_3m"], float)
    dc_break = (exit_px > 0) & (dc > 0) & ((close >= exit_px) & (close >= dc) if is_long else (close <= exit_px) & (close <= dc))
    t1 = candidate & bounce & (htf >= int(_cfg(cfg, "OBLIGATORY_REENTRY_TIER1_HTF_REQUIRED")))
    t2 = candidate & ~t1 & align3 & (htf >= int(_cfg(cfg, "OBLIGATORY_REENTRY_TIER2_HTF_REQUIRED")))
    t3 = candidate & ~t1 & ~t2 & dc_break
    mask = t1 | t2 | t3
    mult = np.zeros(n, float); score = np.zeros(n, float)
    mult[t1] = float(_cfg(cfg, "OBLIGATORY_REENTRY_SMA_BOUNCE_SIZE_MULT" if is_long else "OBLIGATORY_REENTRY_SHORT_SMA_BOUNCE_SIZE_MULT"))
    mult[t2 | t3] = float(_cfg(cfg, "OBLIGATORY_REENTRY_DEFAULT_SIZE_MULT"))
    score[t1], score[t2], score[t3] = int(_cfg(cfg, "OBLIGATORY_REENTRY_SCORE_TIER1")), int(_cfg(cfg, "OBLIGATORY_REENTRY_SCORE_TIER2")), int(_cfg(cfg, "OBLIGATORY_REENTRY_SCORE_TIER3"))
    k15 = np.asarray(a["stoch_k_15m"], float)
    extreme = (k15 > float(_cfg(cfg, "OBLIGATORY_REENTRY_K15_HIGH_BLOCK"))) if is_long else (k15 < float(_cfg(cfg, "OBLIGATORY_REENTRY_SHORT_K15_LOW_BLOCK")))
    frac = float(_cfg(cfg, "OBLIGATORY_REENTRY_K15_HIGH_SIZE_FRAC" if is_long else "OBLIGATORY_REENTRY_SHORT_K15_LOW_SIZE_FRAC"))
    mult[mask & extreme] *= frac
    return DecisionResult(mask, mult, score, True)


def cross_freshness_gate(arrays: Mapping[str, Any], state: Mapping[str, Any], cfg: Any) -> MaskResult:
    n = _n(arrays, state); a, ar = _require(arrays, _FRESH_ARRAYS, n); s, sr = _require(state, ("candidate_mask",), n)
    if a is None or s is None: return _mask_unavailable(n, ar or sr)
    candidate = np.asarray(s["candidate_mask"], bool)
    if not bool(_cfg(cfg, "REENTRY_CROSS_FRESHNESS_ENABLED")): return MaskResult(candidate.copy(), True)
    maximum = int(_cfg(cfg, "REENTRY_CROSS_MAX_BARS_AGO"))
    fresh = np.zeros(n, bool)
    for tf in ("3m", "15m", "1h"):
        bars = np.asarray(a[f"wt_cross_bars_ago_{tf}"], float); fresh |= (bars >= 0) & (bars < maximum)
    return MaskResult(candidate & fresh, True)


def b16_midrange_mask(arrays: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _n(arrays); a, reason = _require(arrays, _B16_MID_ARRAYS, n)
    if a is None: return _mask_unavailable(n, reason)
    if not bool(_cfg(cfg, "REENTRY_B16_MIDRANGE_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    close, hi, lo = (np.asarray(a[k], float) for k in ("close", "dc_high_1h", "dc_low_1h"))
    valid = (hi > 0) & (lo > 0); pos = np.where(valid, (close - lo) / np.maximum(hi - lo, 1e-9), 0.5)
    w1, w2, vel, k = (np.asarray(a[x], float) for x in ("wt1_15m", "wt2_15m", "wt_velocity_15m", "k_15m"))
    mask = valid & ((pos > .5) & (w1 > w2) & (vel > 0) & (k < 78) if is_long else (pos < .5) & (w1 < w2) & (vel < 0) & (k > 22))
    return MaskResult(mask, True)


def b16_sma200_decision(arrays: Mapping[str, Any], is_long: bool, cfg: Any) -> DecisionResult:
    n = _n(arrays); a, reason = _require(arrays, _B16_SMA_ARRAYS, n)
    if a is None: return _decision_unavailable(n, reason)
    if not bool(_cfg(cfg, "REENTRY_B16_SMA200_PULLBACK_ENABLED")): return DecisionResult(np.zeros(n, bool), np.zeros(n), np.zeros(n), True)
    close, sma = np.asarray(a["close"], float), np.asarray(a["sma_200_1h"], float)
    prox = np.divide(np.abs(close - sma), sma, out=np.full(n, np.inf), where=sma > 0)
    w1, w2, k1, k4, vel = (np.asarray(a[x], float) for x in ("wt1_1h", "wt2_1h", "k_1h", "k_4h", "wt_velocity_1h"))
    mask = (sma > 0) & (close > 0) & (prox <= float(_cfg(cfg, "REENTRY_B16_SMA200_PROX_PCT")))
    mask &= ((w1 > w2) & (k1 > 35) & (k4 > 35)) if is_long else ((w1 < w2) & (k1 < 65) & (k4 < 65))
    strong = (vel > 1) if is_long else (vel < -1)
    mult = np.where(mask, np.where(strong, float(_cfg(cfg, "REENTRY_B16_SIZE_MULT_STRONG")), float(_cfg(cfg, "REENTRY_B16_SIZE_MULT_WEAK"))), 0.0)
    return DecisionResult(mask, mult, np.where(mask, 88.0, 0.0), True)


def exit_reclaim_mask(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _n(arrays, state); a, ar = _require(arrays, _RECLAIM_ARRAYS, n); s, sr = _require(state, ("candidate_mask", "exit_price"), n)
    if a is None or s is None: return _mask_unavailable(n, ar or sr)
    if not bool(_cfg(cfg, "REENTRY_EXIT_RECLAIM_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    c, ex = np.asarray(a["close"], float), np.asarray(s["exit_price"], float); buf = float(_cfg(cfg, "REENTRY_EXIT_RECLAIM_BUFFER_PCT")) / 100
    above = (c >= ex * (1 + buf)) if is_long else (c <= ex * (1 - buf))
    sma = np.asarray(a["ema_9_15m"], float); sma_ok = (sma <= 0) | ((c > sma) if is_long else (c < sma))  # 2026-10-04 ALL-15M: sma_200_1m has no 15m equiv — ema_9_15m approx (both sides)
    k_raw, kp_raw = np.asarray(a["k_3m"], float), np.asarray(a["k_3m_prev"], float)
    k = np.where(k_raw == 0, 50.0, k_raw); kp = np.where(kp_raw == 0, k, kp_raw); mom = (k > kp) if is_long else (k < kp)
    w1, w2, vel, k15 = (np.asarray(a[x], float) for x in ("wt1_15m", "wt2_15m", "wt_velocity_15m", "k_15m"))
    k15 = np.where(k15 == 0, 50.0, k15)
    wt = ((w1 > w2) & (vel > 0) & (k15 < 80)) if is_long else ((w1 < w2) & (vel < 0) & (k15 > 20))
    return MaskResult(np.asarray(s["candidate_mask"], bool) & (ex > 0) & above & sma_ok & mom & wt, True)


def overdue_gr_mask(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _n(arrays, state); a, ar = _require(arrays, _GR_ARRAYS, n); s, sr = _require(state, ("candidate_mask",), n)
    if a is None or s is None: return _mask_unavailable(n, ar or sr)
    close = np.asarray(a["close"], float); dc = np.asarray(a["dc_high_15m" if is_long else "dc_low_15m"], float)
    dc_ok = (dc > 0) & ((close >= dc) if is_long else (close <= dc)); mode = str(_cfg(cfg, "REENTRY_GR_HLHH_MODE")).upper()
    count = np.zeros(n, np.int8)
    for tf in ("1h", "4h", "D"):
        hi, hip, lo, lop = (np.asarray(a[f"{x}_{tf}{suf}"], float) for x, suf in (("high", ""), ("high", "_prev"), ("low", ""), ("low", "_prev")))
        hh, hl = (hi > 0) & (hip > 0) & (hi > hip), (lo > 0) & (lop > 0) & (lo > lop)
        w1, w2 = np.asarray(a[f"wt1_{tf}"], float), np.asarray(a[f"wt2_{tf}"], float); wt = (w1 > w2) if is_long else (w1 < w2)
        structure = hh if mode == "HH" else hl if mode == "HL" else (hh | hl)
        count += (structure & wt).astype(np.int8)
    return MaskResult(np.asarray(s["candidate_mask"], bool) & (dc_ok | (count >= int(_cfg(cfg, "REENTRY_GR_MIN_TFS")))), True)


def post_consolidation_decision(arrays: Mapping[str, Any], state: Mapping[str, Any], cfg: Any) -> DecisionResult:
    n = _n(arrays, state); a, ar = _require(arrays, _POST_ARRAYS, n); s, sr = _require(state, ("candidate_mask", "base_multiplier"), n)
    if a is None or s is None: return _decision_unavailable(n, ar or sr)
    candidate, base = np.asarray(s["candidate_mask"], bool), np.asarray(s["base_multiplier"], float); mult = base.copy()
    if bool(_cfg(cfg, "REENTRY_POST_CONSOL_ENABLED")):
        threshold = float(_cfg(cfg, "REENTRY_POST_CONSOL_ATR_THRESHOLD"))
        ranks = [np.asarray(a[f"bar_atr_rank_{tf}"], float) for tf in ("1h", "4h", "D")]
        # Live uses ``value or 0.5``; exact zero therefore means the neutral
        # default, not maximum compression.
        count = sum((np.where(rank == 0, 0.5, rank) < threshold).astype(np.int8) for rank in ranks)
        mult[candidate & (count >= int(_cfg(cfg, "REENTRY_POST_CONSOL_TFS_REQUIRED")))] *= float(_cfg(cfg, "REENTRY_POST_CONSOL_MULT"))
    return DecisionResult(candidate.copy(), np.where(candidate, mult, 0), np.zeros(n), True)


def wt15m_reentry_decision(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> DecisionResult:
    n = _n(arrays, state); a, ar = _require(arrays, _WT15_ARRAYS, n); s, sr = _require(state, ("candidate_mask", "price_ready"), n)
    if a is None or s is None: return _decision_unavailable(n, ar or sr)
    if not bool(_cfg(cfg, "REENTRY_WT15M_CROSS_ENABLED")): return DecisionResult(np.zeros(n, bool), np.zeros(n), np.zeros(n), True)
    w15, s15 = np.asarray(a["wt1_15m"], float), np.asarray(a["wt2_15m"], float); aligned = (w15 > s15) if is_long else (w15 < s15)
    htf = ((np.asarray(a["wt1_1h"], float) > np.asarray(a["wt2_1h"], float)) | (np.asarray(a["wt1_4h"], float) > np.asarray(a["wt2_4h"], float))) if is_long else ((np.asarray(a["wt1_1h"], float) < np.asarray(a["wt2_1h"], float)) | (np.asarray(a["wt1_4h"], float) < np.asarray(a["wt2_4h"], float)))
    mask = np.asarray(s["candidate_mask"], bool) & np.asarray(s["price_ready"], bool) & aligned & (htf | (not bool(_cfg(cfg, "REENTRY_WT15M_HTF_FAVOR_REQUIRED"))))
    post_state = {"candidate_mask": mask, "base_multiplier": np.full(n, float(_cfg(cfg, "REENTRY_WT15M_SIZE_MULT")))}
    result = post_consolidation_decision(a, post_state, cfg)
    return DecisionResult(mask, result.size_mult, np.where(mask, 85.0, 0.0), result.available, result.reason)


def scalp_v3_aug_be_stop_mask(state: Mapping[str, Any], cfg: Any) -> MaskResult:
    n = _n(state); names = ("candidate_mask", "is_v3", "position_amount", "has_augmentation", "gain_pct", "max_gain_pct"); s, reason = _require(state, names, n)
    if s is None: return _mask_unavailable(n, reason)
    if not bool(_cfg(cfg, "SCALP_V3_AUG_BE_STOP_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    mask = np.asarray(s["candidate_mask"], bool) & np.asarray(s["is_v3"], bool) & (np.asarray(s["position_amount"], float) > 0) & np.asarray(s["has_augmentation"], bool)
    mask &= (np.asarray(s["max_gain_pct"], float) >= 1.0) & (np.asarray(s["gain_pct"], float) <= float(_cfg(cfg, "SCALP_V3_AUG_BE_STOP_PCT")))
    return MaskResult(mask, True)


def structure_flip_masks(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> StructureFlipResult:
    n = _n(arrays, state); tf, btf = str(_cfg(cfg, "STRUCTURE_FLIP_REENTRY_TF")), str(_cfg(cfg, "STRUCTURE_FLIP_REENTRY_BASIS_TF"))
    names = ("close", f"high_{tf}", f"high_{tf}_prev", f"low_{tf}", f"low_{tf}_prev", f"dc_basis_{btf}")
    states = ("candidate_mask", "has_exit_signature", "signature_changed", "market_open", "has_opposing_position")
    a, ar = _require(arrays, names, n); s, sr = _require(state, states, n)
    if a is None or s is None: return StructureFlipResult(np.zeros(n, bool), np.zeros(n, bool), False, ar or sr)
    if not bool(_cfg(cfg, "STRUCTURE_FLIP_REENTRY_ENABLED")): return StructureFlipResult(np.zeros(n, bool), np.zeros(n, bool), True)
    hi, hip, lo, lop = (np.asarray(a[x], float) for x in (f"high_{tf}", f"high_{tf}_prev", f"low_{tf}", f"low_{tf}_prev")); valid = (hi > 0) & (hip > 0) & (lo > 0) & (lop > 0)
    favorable = valid & (((hi > hip) & (lo > lop)) if is_long else ((hi < hip) & (lo < lop))); adverse = valid & (((hi < hip) & (lo < lop)) if is_long else ((hi > hip) & (lo > lop)))
    basis, close = np.asarray(a[f"dc_basis_{btf}"], float), np.asarray(a["close"], float)
    basis_ok = np.ones(n, bool) if not bool(_cfg(cfg, "STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED")) else (basis > 0) & ((close > basis) if is_long else (close < basis))
    candidate = np.asarray(s["candidate_mask"], bool); exit_mask = candidate & adverse & basis_ok
    reentry = candidate & favorable & basis_ok & np.asarray(s["has_exit_signature"], bool) & np.asarray(s["signature_changed"], bool) & np.asarray(s["market_open"], bool) & ~np.asarray(s["has_opposing_position"], bool)
    return StructureFlipResult(exit_mask, reentry, True)


def guaranteed_tight_stop_mask(state: Mapping[str, Any], cfg: Any) -> MaskResult:
    n = _n(state); names = ("candidate_mask", "position_amount", "position_min_qty", "is_guaranteed_reentry", "gain_pct", "age_seconds"); s, reason = _require(state, names, n)
    if s is None: return _mask_unavailable(n, reason)
    if not bool(_cfg(cfg, "GUARANTEED_REENTRY_TIGHT_STOP_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    age = np.asarray(s["age_seconds"], float); stop = -abs(float(_cfg(cfg, "GUARANTEED_REENTRY_TIGHT_STOP_PCT")))
    mask = np.asarray(s["candidate_mask"], bool) & (np.asarray(s["position_amount"], float) > np.asarray(s["position_min_qty"], float)) & np.asarray(s["is_guaranteed_reentry"], bool)
    mask &= (age >= float(_cfg(cfg, "GUARANTEED_REENTRY_TIGHT_STOP_MIN_AGE_S"))) & (age <= float(_cfg(cfg, "GUARANTEED_REENTRY_TIGHT_STOP_MAX_AGE_S"))) & (np.asarray(s["gain_pct"], float) <= stop)
    return MaskResult(mask, True)


def bounce_reentry_parent_mask(state: Mapping[str, Any], cfg: Any) -> MaskResult:
    """Exact parent/lifecycle admission before the stateful K-reset latch."""
    n = _n(state); names = ("candidate_mask", "is_exit", "minutes_since_reduction"); s, reason = _require(state, names, n)
    if s is None: return _mask_unavailable(n, reason)
    if not bool(_cfg(cfg, "BOUNCE_REENTRY_ENABLED")): return MaskResult(np.zeros(n, bool), True)
    minutes = np.asarray(s["minutes_since_reduction"], float)
    return MaskResult(np.asarray(s["candidate_mask"], bool) & ~np.asarray(s["is_exit"], bool) & (minutes > 0) & (minutes < 120), True)


def reentry_price_improvement_gate(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _n(arrays, state); a, ar = _require(arrays, ("close",), n); s, sr = _require(state, ("candidate_mask", "reentry_level"), n)
    if a is None or s is None: return _mask_unavailable(n, ar or sr)
    close, level = np.asarray(a["close"], float), np.asarray(s["reentry_level"], float); pct = float(_cfg(cfg, "REENTRY_PRICE_IMPROVE_PCT")) / 100.0
    favorable = (close > level) if is_long else (close < level)
    improved = (close <= level * (1 - pct)) if is_long else (close >= level * (1 + pct))
    return MaskResult(np.asarray(s["candidate_mask"], bool) & ((level <= 0) | favorable | improved), True)


def tiered_reentry_decision(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> DecisionResult:
    n = _n(arrays, state); a, ar = _require(arrays, _TIER_ARRAYS, n); s, sr = _require(state, ("candidate_mask", "exit_price", "minutes_since_exit"), n)
    if a is None or s is None: return _decision_unavailable(n, ar or sr)
    candidate, close, ex, mins = np.asarray(s["candidate_mask"], bool), np.asarray(a["close"], float), np.asarray(s["exit_price"], float), np.asarray(s["minutes_since_exit"], float)
    k, kp, k1, d1 = (np.asarray(a[x], float) for x in ("k_3m", "k_3m_prev", "k_1m", "d_1m")); exhausted = (k > 95) if is_long else (k < 5)
    cross = (ex > 0) & ((close > ex * 1.001) if is_long else (close < ex * .999)); trend = (close > ex * 1.003) if is_long else (close < ex * .997)
    momentum = ((k > kp) | (k1 > d1)) if is_long else ((k < kp) | (k1 < d1))
    partial = candidate & cross & exhausted & bool(_cfg(cfg, "REENTRY_EXHAUSTED_PARTIAL_ENABLED")); tier1 = candidate & cross & ~exhausted
    # The literal 0.3% tier-2 path is dominated by the preceding 0.1% cross
    # branch.  It remains here for source parity but is not claimed as a wired
    # field; its two disconnected knobs are documented as rejected below.
    tier2 = candidate & ~cross & trend & momentum & ~exhausted & (mins >= 10.0); forced = candidate & ~cross & ~tier2 & ~exhausted & (mins >= float(_cfg(cfg, "REENTRY_TIER2_MAX_MINUTES"))) & (ex > 0)
    mask = partial | tier1 | tier2 | forced; mult = np.zeros(n); mult[tier1] = float(_cfg(cfg, "REENTRY_TIER1_SIZE_MULT")); mult[partial] = 1.0; mult[tier2] = 1.0; mult[forced] = .5
    score = np.zeros(n); score[partial], score[tier1], score[tier2], score[forced] = 12, 20, 18, 15
    return DecisionResult(mask, mult, score, True)


__all__ = [
    "FIELD_CONTRACTS", "DecisionResult", "FieldContract", "MaskResult", "StructureFlipResult",
    "b16_midrange_mask", "b16_sma200_decision", "bounce_reentry_parent_mask", "cross_freshness_gate", "exit_reclaim_mask",
    "guaranteed_tight_stop_mask", "obligatory_reentry_decision", "overdue_gr_mask",
    "post_consolidation_decision", "reentry_price_improvement_gate", "scalp_v3_aug_be_stop_mask", "structure_flip_masks",
    "tiered_reentry_decision", "wt15m_reentry_decision",
]
