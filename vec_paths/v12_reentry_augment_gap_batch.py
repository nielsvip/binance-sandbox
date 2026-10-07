"""Exact vector predicates for a disjoint V12 re-entry/augment gap tranche.

This module does not dispatch orders or mutate positions.  It mirrors bounded,
pure decision sub-gates from ``ez_positions_quick.py``, ``ez_manage.py`` and
``tradier_reentry_wt_contract.py``.  Missing or misaligned persisted arrays are
reported as unavailable and return an all-false mask; no neutral defaults,
timeframe proxies, fabricated bars, hashes, or reason-only effects are used.
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
    source: str


@dataclass(frozen=True)
class MaskResult:
    mask: np.ndarray
    available: bool
    reason: str = ""


def _fc(name, value_type, default, grid, family, arrays, source):
    return FieldContract(name, value_type, default, tuple(grid), family, tuple(arrays), source)


FIELD_CONTRACTS: dict[str, FieldContract] = {
    c.name: c for c in (
        _fc("BOUNCE_REENTRY_K_RESET_LONG", "int", 35, [35], "BOUNCE_RESET", ["stoch_k_3m"], "ez_positions_quick.py:3107"),
        _fc("BOUNCE_REENTRY_K_RESET_SHORT", "int", 65, [65], "BOUNCE_RESET", ["stoch_k_3m"], "ez_positions_quick.py:3108"),
        _fc("MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME", "bool", True, [False, True], "MANDATORY_PRICE_CROSS", ["stoch_k_3m"], "ez_positions_quick.py:3130"),
        _fc("MANDATORY_REENTRY_K_HIGH_BLOCK", "float", 80.0, [80.0], "MANDATORY_PRICE_CROSS", ["stoch_k_3m"], "ez_positions_quick.py:3131"),
        _fc("MANDATORY_REENTRY_K_LOW_BLOCK", "float", 20.0, [20.0], "MANDATORY_PRICE_CROSS", ["stoch_k_3m"], "ez_positions_quick.py:3132"),
        _fc("MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS", "bool", False, [False, True], "MANDATORY_PRICE_CROSS", ["close", "wt1_3m", "wt2_3m"], "ez_positions_quick.py:3133,3175"),
        _fc("GUARANTEED_REENTRY_STRICT_CONFIRMATION", "bool", True, [False, True], "GUARANTEED_STRICT", ["stoch_k_3m"], "ez_positions_quick.py:16509-16525"),
        _fc("GUARANTEED_REENTRY_K_HIGH_BLOCK", "float", 80.0, [80.0], "GUARANTEED_STRICT", ["stoch_k_3m"], "ez_positions_quick.py:16511"),
        _fc("GUARANTEED_REENTRY_K_LOW_BLOCK", "float", 20.0, [20.0], "GUARANTEED_STRICT", ["stoch_k_3m"], "ez_positions_quick.py:16512"),
        _fc("GUARANTEED_REENTRY_K_FAVORABLE_LOW", "float", 30.0, [30.0], "GUARANTEED_STRICT", ["stoch_k_3m"], "ez_positions_quick.py:16513"),
        _fc("GUARANTEED_REENTRY_K_FAVORABLE_HIGH", "float", 70.0, [70.0], "GUARANTEED_STRICT", ["stoch_k_3m"], "ez_positions_quick.py:16514"),
        _fc("MANDATORY_REENTRY_WT_FILTER_TF_MODE", "str", "15m_only", ["15m_only"], "MANDATORY_WT", ["wt1_15m", "wt2_15m", "wt_velocity_15m"], "tradier_reentry_wt_contract.py:31-75"),
        _fc("MANDATORY_REENTRY_WT_FILTER_MIN_TFS", "int", 1, [1], "MANDATORY_WT", ["wt1_15m", "wt2_15m"], "tradier_reentry_wt_contract.py:31-75"),
        _fc("MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP", "bool", False, [False, True], "MANDATORY_WT", ["wt1_15m", "wt2_15m"], "tradier_reentry_wt_contract.py:57-64"),
        _fc("MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO", "float", 0.9, [0.9], "MANDATORY_WT", ["wt_velocity_15m"], "tradier_reentry_wt_contract.py:65-70"),
        _fc("MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY", "float", 0.0, [0.0], "MANDATORY_WT", ["wt_velocity_15m"], "tradier_reentry_wt_contract.py:65-70"),
        _fc("REENTRY2_DC_BREAK_ALLOW_15M", "bool", True, [False, True], "REENTRY2_DC_BREAK", ["dc_high_15m", "dc_low_15m"], "ez_manage.py:36119,36135-36147"),
        _fc("REENTRY2_DC_BREAK_REQUIRE_K_FILTER", "bool", True, [False, True], "REENTRY2_DC_BREAK", ["k_3m", "d_3m"], "ez_manage.py:36120,36127-36147"),
        _fc("REENTRY2_DC_BREAK_REQUIRE_WT_FILTER", "bool", False, [False, True], "REENTRY2_DC_BREAK", ["wt1_3m", "wt2_3m"], "ez_manage.py:36121,36127-36147"),
        _fc("REENTRY2_DC_BREAK_FILTER_TF", "str", "3m", ["3m"], "REENTRY2_DC_BREAK", ["k_3m", "d_3m", "wt1_3m", "wt2_3m"], "ez_manage.py:36122,36127-36147"),
        _fc("AUGMENT_ONLY_WHEN_PROFITABLE", "bool", True, [False, True], "AUGMENT_ADMISSION", [], "ez_positions_quick.py:13164-13166"),
        _fc("HTF_GATE_APPLY_TO_AUGMENT", "bool", True, [False, True], "AUGMENT_ADMISSION", ["wt1_D", "wt2_D", "wt1_4h", "wt2_4h", "wt1_1h", "wt2_1h", "sma_200_D", "close"], "ez_positions_quick.py:11968-12008,12480"),
    )
}


def _cfg(cfg: Any, name: str) -> Any:
    contract = FIELD_CONTRACTS.get(name)
    default = contract.default if contract else None
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _length(arrays: Mapping[str, Any]) -> int:
    for value in arrays.values():
        arr = np.asarray(value)
        if arr.ndim and len(arr):
            return len(arr)
    return 0


def _require(arrays: Mapping[str, Any], names: Sequence[str], n: int) -> tuple[dict[str, np.ndarray] | None, str]:
    out: dict[str, np.ndarray] = {}
    missing: list[str] = []
    bad_length: list[str] = []
    for name in names:
        if name not in arrays:
            missing.append(name)
            continue
        value = np.asarray(arrays[name])
        if value.ndim == 0 or len(value) != n:
            bad_length.append(name)
            continue
        out[name] = value
    if missing or bad_length:
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if bad_length:
            parts.append("misaligned=" + ",".join(bad_length))
        return None, ";".join(parts)
    return out, ""


def _unavailable(n: int, reason: str) -> MaskResult:
    return MaskResult(np.zeros(n, dtype=bool), False, reason)


def _roll(values: np.ndarray) -> np.ndarray:
    out = np.roll(np.asarray(values), 1)
    out[0] = values[0]
    return out


def _aligned(w1: np.ndarray, w2: np.ndarray, is_long: bool) -> np.ndarray:
    return (w1 > w2) if is_long else (w1 < w2)


def bounce_reset_mask(arrays: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    """Exact instantaneous K-reset predicate; lifecycle latch remains caller-owned."""
    n = _length(arrays)
    got, reason = _require(arrays, ("stoch_k_3m",), n)
    if got is None:
        return _unavailable(n, reason)
    k = np.asarray(got["stoch_k_3m"], dtype=float)
    threshold = float(_cfg(cfg, "BOUNCE_REENTRY_K_RESET_LONG" if is_long else "BOUNCE_REENTRY_K_RESET_SHORT"))
    return MaskResult((k < threshold) if is_long else (k > threshold), True)


def mandatory_wt_filter_mask(arrays: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    mode = str(_cfg(cfg, "MANDATORY_REENTRY_WT_FILTER_TF_MODE") or "15m_only").lower().replace(" ", "")
    tfs = ("5m",) if mode in {"5m", "5m_only"} else ("15m",) if mode in {"15m", "15m_only"} else ("5m", "15m")
    n = _length(arrays)
    names = tuple(name for tf in tfs for name in (f"wt1_{tf}", f"wt2_{tf}", f"wt_velocity_{tf}"))
    got, reason = _require(arrays, names, n)
    if got is None:
        return _unavailable(n, reason)
    require_flip = bool(_cfg(cfg, "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP"))
    ratio = max(0.0, float(_cfg(cfg, "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO")))
    min_velocity = max(0.0, float(_cfg(cfg, "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY")))
    qualifying = np.zeros(n, dtype=np.int16)
    for tf in tfs:
        w1 = np.asarray(got[f"wt1_{tf}"], dtype=float)
        w2 = np.asarray(got[f"wt2_{tf}"], dtype=float)
        velocity = np.asarray(got[f"wt_velocity_{tf}"], dtype=float)
        favorable = _aligned(w1, w2, is_long)
        was_favorable = _aligned(_roll(w1), _roll(w2), is_long)
        cross_key = f"wt_cross_bull_{tf}" if is_long else f"wt_cross_bear_{tf}"
        explicit = np.asarray(arrays[cross_key], dtype=bool) if cross_key in arrays and len(np.asarray(arrays[cross_key])) == n else np.zeros(n, dtype=bool)
        flipped = explicit | (favorable & ~was_favorable)
        previous_velocity = _roll(velocity)
        favorable_velocity = (velocity >= min_velocity) if is_long else (velocity <= -min_velocity)
        not_slowing = np.abs(velocity) >= np.maximum(min_velocity, np.abs(previous_velocity) * ratio)
        qualifying += (favorable & (flipped if require_flip else True) & favorable_velocity & not_slowing).astype(np.int16)
    required = max(1, min(int(_cfg(cfg, "MANDATORY_REENTRY_WT_FILTER_MIN_TFS")), len(tfs)))
    return MaskResult(qualifying >= required, True)


def mandatory_price_cross_mask(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    """Legacy mandatory price-cross subgate, before obligatory-reentry override."""
    n = _length(arrays)
    names = ["close", "stoch_k_3m"] + [f"wt{x}_{tf}" for tf in ("3m", "15m", "1h", "4h", "D") for x in (1, 2)]
    got, reason = _require(arrays, names, n)
    states, state_reason = _require(state, ("exit_price", "minutes_since_reduction"), n)
    if got is None or states is None:
        return _unavailable(n, reason or state_reason)
    close = np.asarray(got["close"], dtype=float)
    exit_price = np.asarray(states["exit_price"], dtype=float)
    minutes = np.asarray(states["minutes_since_reduction"], dtype=float)
    aligned = {tf: _aligned(np.asarray(got[f"wt1_{tf}"], float), np.asarray(got[f"wt2_{tf}"], float), is_long) for tf in ("3m", "15m", "1h", "4h", "D")}
    htf = aligned["1h"].astype(int) + aligned["4h"].astype(int) + aligned["D"].astype(int)
    price_cross = (close >= exit_price) if is_long else (close <= exit_price)
    minimum_signal = aligned["3m"] & aligned["15m"] & (htf >= 1)
    continuation = (minutes < 5.0) & aligned["3m"] & (htf >= 1)
    k = np.asarray(got["stoch_k_3m"], float)
    if bool(_cfg(cfg, "MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME")):
        k_block = (k >= float(_cfg(cfg, "MANDATORY_REENTRY_K_HIGH_BLOCK"))) if is_long else (k <= float(_cfg(cfg, "MANDATORY_REENTRY_K_LOW_BLOCK")))
    else:
        k_block = np.zeros(n, dtype=bool)
    strong = np.zeros(n, dtype=bool)
    if bool(_cfg(cfg, "MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS")):
        past = np.divide(np.abs(close - exit_price), exit_price, out=np.zeros(n), where=exit_price > 0) > 0.003
        strong = past & aligned["3m"] & (htf >= 1)
    mask = (exit_price > 0) & price_cross & ~k_block & (minimum_signal | continuation | strong)
    return MaskResult(mask, True)


def guaranteed_strict_mask(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _length(arrays)
    names = ["close", "stoch_k_3m"] + [f"wt{x}_{tf}" for tf in ("3m", "15m", "1h", "4h", "D") for x in (1, 2)]
    got, reason = _require(arrays, names, n)
    states, state_reason = _require(state, ("candidate_mask", "elapsed_seconds", "exit_price"), n)
    if got is None or states is None:
        return _unavailable(n, reason or state_reason)
    candidate = np.asarray(states["candidate_mask"], dtype=bool)
    if not bool(_cfg(cfg, "GUARANTEED_REENTRY_STRICT_CONFIRMATION")):
        return MaskResult(candidate.copy(), True)
    aligned = {tf: _aligned(np.asarray(got[f"wt1_{tf}"], float), np.asarray(got[f"wt2_{tf}"], float), is_long) for tf in ("3m", "15m", "1h", "4h", "D")}
    htf = aligned["1h"].astype(int) + aligned["4h"].astype(int) + aligned["D"].astype(int)
    full_stack = aligned["3m"] & aligned["15m"] & (htf >= 2)
    k = np.asarray(got["stoch_k_3m"], float)
    adverse = (k >= float(_cfg(cfg, "GUARANTEED_REENTRY_K_HIGH_BLOCK"))) if is_long else (k <= float(_cfg(cfg, "GUARANTEED_REENTRY_K_LOW_BLOCK")))
    favorable = (k <= float(_cfg(cfg, "GUARANTEED_REENTRY_K_FAVORABLE_LOW"))) if is_long else (k >= float(_cfg(cfg, "GUARANTEED_REENTRY_K_FAVORABLE_HIGH")))
    close = np.asarray(got["close"], float)
    exit_price = np.asarray(states["exit_price"], float)
    continued = (np.asarray(states["elapsed_seconds"], float) < 300.0) & aligned["3m"] & (htf >= 1) & ((close >= exit_price) if is_long else (close <= exit_price))
    database_signal = aligned["3m"] & aligned["15m"] & (htf >= 1)
    admitted = ~adverse & (continued | (database_signal & (full_stack | favorable)))
    return MaskResult(candidate & admitted, True)


def reentry2_dc_break_mask(arrays: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _length(arrays)
    tf = str(_cfg(cfg, "REENTRY2_DC_BREAK_FILTER_TF"))
    names = ["close", "dc_high_3m", "dc_low_3m", "dc_high4_3m", "dc_low4_3m", "dc_high_1h", "dc_low_1h"]
    if bool(_cfg(cfg, "REENTRY2_DC_BREAK_ALLOW_15M")):
        names += ["dc_high_15m", "dc_low_15m"]
    if bool(_cfg(cfg, "REENTRY2_DC_BREAK_REQUIRE_K_FILTER")):
        names += [f"k_{tf}", f"d_{tf}"]
    if bool(_cfg(cfg, "REENTRY2_DC_BREAK_REQUIRE_WT_FILTER")):
        names += [f"wt1_{tf}", f"wt2_{tf}"]
    got, reason = _require(arrays, names, n)
    if got is None:
        return _unavailable(n, reason)
    close = np.asarray(got["close"], float)
    high3, low3 = np.asarray(got["dc_high_3m"], float), np.asarray(got["dc_low_3m"], float)
    high4, low4 = np.asarray(got["dc_high4_3m"], float), np.asarray(got["dc_low4_3m"], float)
    direct = ((high3 > 0) & (close > high3 * 1.001)) | ((high4 > 0) & (close > high4 * 1.001)) if is_long else ((low3 > 0) & (close < low3 * 0.999)) | ((low4 > 0) & (close < low4 * 0.999))
    high1, low1 = np.asarray(got["dc_high_1h"], float), np.asarray(got["dc_low_1h"], float)
    parent = (high1 > 0) & (close > high1 * 1.001) if is_long else (low1 > 0) & (close < low1 * 0.999)
    if bool(_cfg(cfg, "REENTRY2_DC_BREAK_ALLOW_15M")):
        h15, l15 = np.asarray(got["dc_high_15m"], float), np.asarray(got["dc_low_15m"], float)
        parent |= (h15 > 0) & (close > h15 * 1.001) if is_long else (l15 > 0) & (close < l15 * 0.999)
    filt = np.ones(n, dtype=bool)
    if bool(_cfg(cfg, "REENTRY2_DC_BREAK_REQUIRE_K_FILTER")):
        k, d = np.asarray(got[f"k_{tf}"], float), np.asarray(got[f"d_{tf}"], float)
        filt &= (k > d) if is_long else (k < d)
    if bool(_cfg(cfg, "REENTRY2_DC_BREAK_REQUIRE_WT_FILTER")):
        w1, w2 = np.asarray(got[f"wt1_{tf}"], float), np.asarray(got[f"wt2_{tf}"], float)
        filt &= _aligned(w1, w2, is_long)
    return MaskResult(direct | (parent & filt), True)


def augment_admission_mask(arrays: Mapping[str, Any], state: Mapping[str, Any], is_long: bool, cfg: Any) -> MaskResult:
    n = _length(arrays) or _length(state)
    states, reason = _require(state, ("candidate_mask", "gain_pct", "position_amount", "is_hedge", "is_sba", "is_reentry_reason"), n)
    if states is None:
        return _unavailable(n, reason)
    mask = np.asarray(states["candidate_mask"], bool).copy()
    if bool(_cfg(cfg, "AUGMENT_ONLY_WHEN_PROFITABLE")):
        gain = np.asarray(states["gain_pct"], float)
        amount = np.asarray(states["position_amount"], float)
        exempt = np.asarray(states["is_hedge"], bool) | np.asarray(states["is_sba"], bool) | np.asarray(states["is_reentry_reason"], bool)
        mask &= ~((gain < 0) & (amount > 0) & ~exempt)
    if not bool(_cfg(cfg, "HTF_GATE_APPLY_TO_AUGMENT")):
        return MaskResult(mask, True)
    names = ("close", "wt1_D", "wt2_D", "wt1_4h", "wt2_4h", "wt1_1h", "wt2_1h", "sma_200_D")
    got, reason = _require(arrays, names, n)
    if got is None:
        return _unavailable(n, reason)
    votes = np.zeros(n, dtype=np.int16)
    d_ok = _aligned(np.asarray(got["wt1_D"], float), np.asarray(got["wt2_D"], float), is_long) & (np.asarray(got["wt1_D"]) != 0) & (np.asarray(got["wt2_D"]) != 0)
    votes += d_ok
    for tf in ("4h", "1h"):
        w1, w2 = np.asarray(got[f"wt1_{tf}"], float), np.asarray(got[f"wt2_{tf}"], float)
        votes += (_aligned(w1, w2, is_long) & (w1 != 0) & (w2 != 0)).astype(np.int16)
    sma = np.asarray(got["sma_200_D"], float)
    close = np.asarray(got["close"], float)
    include_sma = bool(_cfg(cfg, "HTF_GATE_SIGNALS_SMA200D")) if _cfg(cfg, "HTF_GATE_SIGNALS_SMA200D") is not None else True
    if include_sma:
        votes += (((close > sma) if is_long else (close < sma)) & (sma > 0)).astype(np.int16)
    minimum = int(_cfg(cfg, "HTF_GATE_MIN_CONFIRMATIONS") or 3)
    d_required = True if _cfg(cfg, "HTF_GATE_D_MANDATORY") is None else bool(_cfg(cfg, "HTF_GATE_D_MANDATORY"))
    gate = (votes >= minimum) & (d_ok if d_required else True)
    return MaskResult(mask & gate, True)


__all__ = [
    "FIELD_CONTRACTS", "FieldContract", "MaskResult", "augment_admission_mask",
    "bounce_reset_mask", "guaranteed_strict_mask", "mandatory_price_cross_mask",
    "mandatory_wt_filter_mask", "reentry2_dc_break_mask",
]
