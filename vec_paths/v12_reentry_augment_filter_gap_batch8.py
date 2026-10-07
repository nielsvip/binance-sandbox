"""Eighth disjoint source-exact V12 entry scoring/filter tranche.

This module twins the live WT-composite, DC-moment and R-S1/2/3/4/5/7
entry-score semantics.  It returns both the executable mask (for the WT HTF
hard gate) and additive score delta.  Crypto's native route is 3m and
Tradier's is 5m; exact enabled inputs are mandatory and never proxied.
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
class ScoreResult:
    mask: np.ndarray; score_delta: np.ndarray; available: bool; reason: str = ""


def _fc(name, typ, default, grid, family, arrays, source):
    return FieldContract(name, typ, default, tuple(grid), family, tuple(arrays),
                         ("candidate_mask", "is_exit"), ("ENTRY",), source)


_COMP = ("wt_bull_alignment", "wt_bear_alignment", "wt_composite_long", "wt_composite_short",
         "wt_oversold_tf_count", "wt_overbought_tf_count", "wt_hl_count", "wt_lh_count",
         "wt_bull_cross_count", "wt_bear_cross_count", "wt_any_bull_div", "wt_any_bear_div")
_DIV = ("wt_divergence_{native}", "wt_divergence_15m", "wt_divergence_1h", "wt_divergence_4h", "wt_divergence_D")
_HHLL = ("high_{configured_native_tf}", "high_{configured_native_tf}_prev", "low_{configured_native_tf}",
         "low_{configured_native_tf}_prev", "wt_structure_{configured_native_tf}", "k_{configured_native_tf}", "k_{configured_native_tf}_prev")


FIELD_CONTRACTS = {c.name: c for c in (
    _fc("WT_COMPOSITE_SCORING_ENABLED", "bool", True, [False, True], "WT_COMPOSITE", _COMP, "ez_positions_quick.py:2399-2480"),
    _fc("WT_COMPOSITE_HTF_GATE", "bool", False, [False, True], "WT_COMPOSITE", _COMP[:4], "ez_positions_quick.py:2399-2430"),
    _fc("WT_COMPOSITE_ENTRY_BLOCK", "float", -20.0, [-20.0], "WT_COMPOSITE", _COMP[:4], "ez_positions_quick.py:2418-2430"),
    _fc("WT_COMPOSITE_ENTRY_STRONG", "float", 50.0, [50.0], "WT_COMPOSITE", _COMP, "ez_positions_quick.py:2432-2480"),
    _fc("WT_COMPOSITE_ENTRY_GOOD", "float", 30.0, [30.0], "WT_COMPOSITE", _COMP, "ez_positions_quick.py:2432-2480"),
    _fc("WT_COMPOSITE_ENTRY_OK", "float", 10.0, [10.0], "WT_COMPOSITE", _COMP, "ez_positions_quick.py:2432-2480"),
    _fc("DC_MOMENT_ENABLED", "bool", False, [False, True], "DC_MOMENT", ("0dc_moment",), "ez_positions_quick.py:2481-2496"),
    _fc("DC_MOMENT_STRONG_THRESHOLD", "float", 40.0, [40.0], "DC_MOMENT", ("0dc_moment",), "ez_positions_quick.py:2481-2496"),
    _fc("DC_MOMENT_STRONG_BONUS", "float", 10.0, [10.0], "DC_MOMENT", ("0dc_moment",), "ez_positions_quick.py:2481-2496"),
    _fc("DC_MOMENT_OPPOSITE_PENALTY", "float", -15.0, [-15.0], "DC_MOMENT", ("0dc_moment",), "ez_positions_quick.py:2481-2496"),
    _fc("WT_COMPOSITE_DELTA_SCORE_ENABLED", "bool", True, [False, True], "WT_DELTA_SCORE", ("wt_composite_delta",), "ez_positions_quick.py:2497-2506"),
    _fc("WT_COMPOSITE_DELTA_SCORE_THRESHOLD", "float", 50.0, [50.0], "WT_DELTA_SCORE", ("wt_composite_delta",), "ez_positions_quick.py:2497-2506"),
    _fc("WT_COMPOSITE_DELTA_SCORE_BONUS", "float", 3.0, [3.0], "WT_DELTA_SCORE", ("wt_composite_delta",), "ez_positions_quick.py:2497-2506"),
    _fc("R_S1_WT_COMPOSITE_DELTA_USE_ENABLED", "bool", False, [False, True], "R_S1", ("wt_composite_delta",), "ez_positions_quick.py:2440-2450"),
    _fc("R_S1_WT_COMPOSITE_DELTA_THR", "float", 50.0, [50.0], "R_S1", ("wt_composite_delta",), "ez_positions_quick.py:2440-2450"),
    _fc("R_S2_WT_ADAPTIVE_OS_ENABLED", "bool", False, [False, True], "R_S2", ("wt_percentile_15m",), "ez_positions_quick.py:2451-2463"),
    _fc("R_S2_WT_PCT_OS_LONG", "float", 10.0, [10.0], "R_S2", ("wt_percentile_15m",), "ez_positions_quick.py:2451-2463"),
    _fc("R_S2_WT_PCT_OB_SHORT", "float", 90.0, [90.0], "R_S2", ("wt_percentile_15m",), "ez_positions_quick.py:2451-2463"),
    _fc("R_S3_DIV_STACK_ENABLED", "bool", False, [False, True], "R_S3", _DIV, "ez_positions_quick.py:2507-2546"),
    _fc("R_S3_HIDDEN_BONUS", "float", 25.0, [25.0], "R_S3", _DIV, "ez_positions_quick.py:2507-2546"),
    _fc("R_S3_MAIN_PENALTY", "float", -20.0, [-20.0], "R_S3", _DIV, "ez_positions_quick.py:2507-2546"),
    _fc("R_S3_HTF_WEIGHT_ENABLED", "bool", False, [False, True], "R_S3", _DIV, "ez_positions_quick.py:2507-2546"),
    _fc("R_S3_TF_WEIGHT_3M", "float", 0.25, [0.25], "R_S3", _DIV, "ez_positions_quick.py:2512-2518"),
    _fc("R_S3_TF_WEIGHT_15M", "float", 0.5, [0.5], "R_S3", _DIV, "ez_positions_quick.py:2512-2518"),
    _fc("R_S3_TF_WEIGHT_1H", "float", 1.0, [1.0], "R_S3", _DIV, "ez_positions_quick.py:2512-2518"),
    _fc("R_S3_TF_WEIGHT_4H", "float", 2.5, [2.5], "R_S3", _DIV, "ez_positions_quick.py:2512-2518"),
    _fc("R_S3_TF_WEIGHT_D", "float", 4.0, [4.0], "R_S3", _DIV, "ez_positions_quick.py:2512-2518"),
    _fc("R_S4_HA_STREAK_ENABLED", "bool", False, [False, True], "R_S4", ("ha_streak_{configured_tf}", "ha_{configured_tf}"), "ez_positions_quick.py:2585-2595"),
    _fc("R_S4_HA_STREAK_TF", "str", "1h", ["1h"], "R_S4", ("ha_streak_{configured_tf}", "ha_{configured_tf}"), "ez_positions_quick.py:2585-2595"),
    _fc("R_S4_HA_STREAK_WEIGHT", "float", 5.0, [5.0], "R_S4", ("ha_streak_{configured_tf}", "ha_{configured_tf}"), "ez_positions_quick.py:2585-2595"),
    _fc("R_S5_SENT_VEL_ENABLED", "bool", False, [False, True], "R_S5", ("0sentiment_velocity",), "ez_positions_quick.py:2596-2603"),
    _fc("R_S5_SENT_VEL_PCT_THR", "float", 75.0, [75.0], "R_S5", ("0sentiment_velocity",), "ez_positions_quick.py:2596-2603"),
    _fc("R_S5_SENT_VEL_BONUS", "float", 5.0, [5.0], "R_S5", ("0sentiment_velocity",), "ez_positions_quick.py:2596-2603"),
    _fc("R_S7_HHLL_STACK_ENABLED", "bool", False, [False, True], "R_S7", _HHLL, "ez_positions_quick.py:2547-2584"),
    _fc("R_S7_HHLL_TFS", "str", "15m,1h,4h,D", ["15m,1h,4h,D"], "R_S7", _HHLL, "ez_positions_quick.py:2547-2584"),
    _fc("R_S7_HHLL_MIN_INDICATORS", "int", 2, [2], "R_S7", _HHLL, "ez_positions_quick.py:2547-2584"),
    _fc("R_S7_HHLL_BONUS_PER_TF", "float", 3.0, [3.0], "R_S7", _HHLL, "ez_positions_quick.py:2547-2584"),
    _fc("R_S7_HHLL_MIN_TFS_FOR_BONUS", "int", 2, [2], "R_S7", _HHLL, "ez_positions_quick.py:2547-2584"),
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


def _unavailable(n, reason): return ScoreResult(np.zeros(n, bool), np.zeros(n), False, reason)


def _tf(raw, native):
    value = str(raw).strip()
    return native if value in ("3m", "5m", "native") else value


def entry_score_filter_decision(arrays, state, is_long, mode, cfg):
    """Return exact entry admission and score delta for all batch-8 fields."""
    n = _n(arrays, state); s, reason = _req(state, ("candidate_mask", "is_exit"), n)
    if s is None: return _unavailable(n, reason)
    candidate = np.asarray(s["candidate_mask"], bool); eligible = candidate & ~np.asarray(s["is_exit"], bool)
    score = np.zeros(n); passes = np.ones(n, bool); native = "3m" if mode == "crypto" else "5m"
    if not np.any(eligible): return ScoreResult(candidate, score, True)

    comp_enabled = bool(_cfg(cfg, "WT_COMPOSITE_SCORING_ENABLED")); comp_gate = comp_enabled and bool(_cfg(cfg, "WT_COMPOSITE_HTF_GATE"))
    if comp_enabled:
        a, reason = _req(arrays, _COMP, n)
        if a is None: return _unavailable(n, reason)
        align = np.asarray(a["wt_bull_alignment" if is_long else "wt_bear_alignment"], float)
        comp = np.asarray(a["wt_composite_long" if is_long else "wt_composite_short"], float)
        if comp_gate: passes &= (align >= 3) & (comp >= float(_cfg(cfg, "WT_COMPOSITE_ENTRY_BLOCK")))
        strong, good, okay = (float(_cfg(cfg, x)) for x in ("WT_COMPOSITE_ENTRY_STRONG", "WT_COMPOSITE_ENTRY_GOOD", "WT_COMPOSITE_ENTRY_OK"))
        score += np.where(comp >= strong, 8, np.where(comp >= good, 5, np.where(comp >= okay, 2, 0)))
        score += 5 * (np.asarray(a["wt_oversold_tf_count" if is_long else "wt_overbought_tf_count"], float) >= 2)
        score += 5 * (np.asarray(a["wt_hl_count" if is_long else "wt_lh_count"], float) >= 2)
        score += 10 * np.asarray(a["wt_any_bull_div" if is_long else "wt_any_bear_div"], bool)
        score += 3 * (np.asarray(a["wt_bull_cross_count" if is_long else "wt_bear_cross_count"], float) >= 2)
        if bool(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED")):
            d, reason = _req(arrays, ("wt_composite_delta",), n)
            if d is None: return _unavailable(n, reason)
            delta = np.asarray(d["wt_composite_delta"], float); threshold = float(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_THR"))
            score += 6 * ((delta > threshold) if is_long else (delta < -threshold))
        if bool(_cfg(cfg, "R_S2_WT_ADAPTIVE_OS_ENABLED")):
            d, reason = _req(arrays, ("wt_percentile_15m",), n)
            if d is None: return _unavailable(n, reason)
            pct = np.asarray(d["wt_percentile_15m"], float)
            score += 7 * ((pct < float(_cfg(cfg, "R_S2_WT_PCT_OS_LONG"))) if is_long else (pct > float(_cfg(cfg, "R_S2_WT_PCT_OB_SHORT"))))

    if bool(_cfg(cfg, "DC_MOMENT_ENABLED")):
        a, reason = _req(arrays, ("0dc_moment",), n)
        if a is None: return _unavailable(n, reason)
        value = np.asarray(a["0dc_moment"], float); threshold = float(_cfg(cfg, "DC_MOMENT_STRONG_THRESHOLD"))
        aligned = value > threshold if is_long else value < -threshold; against = value < -threshold if is_long else value > threshold
        score += aligned * float(_cfg(cfg, "DC_MOMENT_STRONG_BONUS")) + against * float(_cfg(cfg, "DC_MOMENT_OPPOSITE_PENALTY"))

    if bool(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_ENABLED")):
        a, reason = _req(arrays, ("wt_composite_delta",), n)
        if a is None: return _unavailable(n, reason)
        value = np.asarray(a["wt_composite_delta"], float); threshold = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_THRESHOLD"))
        score += float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_BONUS")) * ((value > threshold) if is_long else (value < -threshold))

    if bool(_cfg(cfg, "R_S3_DIV_STACK_ENABLED")):
        tfs = (native, "15m", "1h", "4h", "D"); names = tuple(f"wt_divergence_{tf}" for tf in tfs)
        a, reason = _req(arrays, names, n)
        if a is None: return _unavailable(n, reason)
        weighted = bool(_cfg(cfg, "R_S3_HTF_WEIGHT_ENABLED"))
        keys = ("3M", "15M", "1H", "4H", "D")
        weights = [float(_cfg(cfg, f"R_S3_TF_WEIGHT_{key}")) for key in keys]
        hidden_count = np.zeros(n, int); hidden_weight = np.zeros(n); main_count = np.zeros(n, int); main_weight = np.zeros(n)
        hidden_target, main_target = (("HIDDEN_BULL", "BEAR") if is_long else ("HIDDEN_BEAR", "BULL"))
        for tf, weight in zip(tfs, weights):
            div = np.char.upper(np.asarray(a[f"wt_divergence_{tf}"], str)); hidden = div == hidden_target
            main = (div == main_target) & (weighted or tf in ("15m", "1h"))
            hidden_count += hidden; hidden_weight += hidden * weight; main_count += main; main_weight += main * weight
        hidden_scale = np.divide(hidden_weight, hidden_count, out=np.zeros(n), where=hidden_count > 0) if weighted else np.ones(n)
        score += (hidden_count >= 2) * float(_cfg(cfg, "R_S3_HIDDEN_BONUS")) * hidden_scale
        score += (main_count >= 1) * float(_cfg(cfg, "R_S3_MAIN_PENALTY")) * (main_weight if weighted else 1.0)

    if bool(_cfg(cfg, "R_S7_HHLL_STACK_ENABLED")):
        tfs = [_tf(x, native) for x in str(_cfg(cfg, "R_S7_HHLL_TFS") or "").split(",") if x.strip()]
        names = tuple(dict.fromkeys(f"{stem}_{tf}{suffix}" for tf in tfs for stem, suffix in (("high", ""), ("high", "_prev"), ("low", ""), ("low", "_prev"), ("wt_structure", ""), ("k", ""), ("k", "_prev"))))
        a, reason = _req(arrays, names, n)
        if a is None: return _unavailable(n, reason)
        confirmed = np.zeros(n, int); min_ind = int(_cfg(cfg, "R_S7_HHLL_MIN_INDICATORS"))
        for tf in tfs:
            hi, hip, lo, lop = (np.asarray(a[x], float) for x in (f"high_{tf}", f"high_{tf}_prev", f"low_{tf}", f"low_{tf}_prev"))
            price = (hi > 0) & (hip > 0) & (lo > 0) & (lop > 0) & (((hi > hip) & (lo > lop)) if is_long else ((hi < hip) & (lo < lop)))
            structure = np.char.upper(np.asarray(a[f"wt_structure_{tf}"], str)) == ("HH" if is_long else "LL")
            k, kp = np.asarray(a[f"k_{tf}"], float), np.asarray(a[f"k_{tf}_prev"], float)
            confirmed += (price.astype(int) + structure.astype(int) + ((k > kp) if is_long else (k < kp)).astype(int)) >= min_ind
        score += (confirmed >= int(_cfg(cfg, "R_S7_HHLL_MIN_TFS_FOR_BONUS"))) * confirmed * float(_cfg(cfg, "R_S7_HHLL_BONUS_PER_TF"))

    if bool(_cfg(cfg, "R_S4_HA_STREAK_ENABLED")):
        tf = _tf(_cfg(cfg, "R_S4_HA_STREAK_TF"), native); names = (f"ha_streak_{tf}", f"ha_{tf}")
        a, reason = _req(arrays, names, n)
        if a is None: return _unavailable(n, reason)
        streak = np.asarray(a[names[0]], float); color = np.char.lower(np.asarray(a[names[1]], str))
        aligned = color == ("green" if is_long else "red")
        score += aligned * (streak > 0) * float(_cfg(cfg, "R_S4_HA_STREAK_WEIGHT")) * np.minimum(streak, 5)

    if bool(_cfg(cfg, "R_S5_SENT_VEL_ENABLED")):
        a, reason = _req(arrays, ("0sentiment_velocity",), n)
        if a is None: return _unavailable(n, reason)
        velocity = np.asarray(a["0sentiment_velocity"], float); aligned = velocity > 0 if is_long else velocity < 0
        score += aligned * (np.abs(velocity) >= float(_cfg(cfg, "R_S5_SENT_VEL_PCT_THR"))) * float(_cfg(cfg, "R_S5_SENT_VEL_BONUS"))

    score = np.where(eligible, score, 0.0)
    return ScoreResult(candidate & (~eligible | passes), score, True)


LIFECYCLE_FILTER_APIS = {name: {"ENTRY": "entry_score_filter_decision"} for name in FIELD_CONTRACTS}

__all__ = ["FIELD_CONTRACTS", "LIFECYCLE_FILTER_APIS", "FieldContract", "ScoreResult", "entry_score_filter_decision"]
