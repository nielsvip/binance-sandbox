"""Causal scalar contract for the vector/live-parity WT_DC entry route."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class CausalScalar:
    value: Any
    source_close_ts: int


@dataclass(frozen=True)
class CausalPair:
    left: Any
    right: Any
    source_close_ts: int


@dataclass(frozen=True)
class WTDCInputs:
    wt_d: CausalPair
    wt_4h: CausalPair
    wt_cross_1h: CausalScalar
    dc_position_1h: CausalScalar
    score_stoch_k_5m: CausalScalar
    gate_k_5m: CausalScalar
    wt_1h: CausalPair | None = None


@dataclass(frozen=True)
class WTDCDecision:
    eligible: bool
    episode_start: bool
    score: float | None
    blockers: tuple[str, ...]
    next_state: dict[str, Any]


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _cross(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip().upper()
        if text in {"BULL", "BEAR", "NONE"}:
            return text
        try:
            value = float(text)
        except (TypeError, ValueError):
            return ""
    numeric = _finite(value)
    if numeric is None:
        return ""
    return "BULL" if numeric > 0 else "BEAR" if numeric < 0 else "NONE"


def _config(
    threshold: float, htf_gate: str, htf_align_required: int, combined_stoch_gate: float
) -> tuple[float, str, int, float]:
    score_threshold = _finite(threshold)
    stoch_gate = _finite(combined_stoch_gate)
    gate = str(htf_gate or "none").lower()
    try:
        alignment = int(htf_align_required)
    except (TypeError, ValueError) as exc:
        raise ValueError("htf_align_required must be an integer") from exc
    if score_threshold not in {20.0, 35.0, 45.0, 60.0, 75.0, 85.0}:
        raise ValueError("WT_DC threshold is not a selected vector value")
    if gate not in {"none", "1h", "4h", "4h_d"}:
        raise ValueError("htf_gate must be none, 1h, 4h, or 4h_D")
    if not 0 <= alignment <= 3:
        raise ValueError("htf_align_required must be in [0, 3]")
    if stoch_gate not in {60.0, 80.0, 100.0}:
        raise ValueError("combined_stoch_gate is not a selected vector value")
    return score_threshold, gate, alignment, stoch_gate


def _read_pair(pair: CausalPair | None, name: str, asof: int):
    if pair is None:
        return None, f"MISSING_COMPLETED_INPUT:{name}"
    left, right, ts = _finite(pair.left), _finite(pair.right), _positive_int(pair.source_close_ts)
    if left is None or right is None or ts is None:
        return None, f"INVALID_COMPLETED_INPUT:{name}"
    if ts > asof:
        return None, f"FUTURE_COMPLETED_INPUT:{name}"
    return (left, right, ts), None


def _read_scalar(scalar: CausalScalar | None, name: str, asof: int):
    if scalar is None:
        return None, f"MISSING_COMPLETED_INPUT:{name}"
    value, ts = _finite(scalar.value), _positive_int(scalar.source_close_ts)
    if value is None or ts is None:
        return None, f"INVALID_COMPLETED_INPUT:{name}"
    if ts > asof:
        return None, f"FUTURE_COMPLETED_INPUT:{name}"
    return (value, ts), None


def evaluate_wt_dc_direct(
    inputs: WTDCInputs,
    *,
    side: str,
    threshold: float,
    htf_gate: str,
    htf_align_required: int,
    combined_stoch_gate: float,
    asof_ts: int,
    prior_state: Mapping[str, Any] | None = None,
) -> WTDCDecision:
    """Evaluate the vector WT_DC score/gates on immutable completed inputs."""
    threshold, htf_gate, required, stoch_gate = _config(
        threshold, htf_gate, htf_align_required, combined_stoch_gate
    )
    normalized_side = str(side).upper()
    if normalized_side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    asof = _positive_int(asof_ts)
    if asof is None:
        raise ValueError("asof_ts must be positive")
    old = dict(prior_state or {})
    if int(old.get("last_asof_ts", 0) or 0) > asof:
        return WTDCDecision(False, False, None, ("OUT_OF_ORDER_OBSERVATION",), old)
    values: dict[str, tuple[Any, ...]] = {}
    blockers: list[str] = []
    for name, item in (("wt_d", inputs.wt_d), ("wt_4h", inputs.wt_4h)):
        result, error = _read_pair(item, name, asof)
        if error: blockers.append(error)
        else: values[name] = result
    for name, item in (
        ("dc_position_1h", inputs.dc_position_1h),
        ("score_stoch_k_5m", inputs.score_stoch_k_5m),
        ("gate_k_5m", inputs.gate_k_5m),
    ):
        result, error = _read_scalar(item, name, asof)
        if error: blockers.append(error)
        else: values[name] = result
    cross_ts = _positive_int(inputs.wt_cross_1h.source_close_ts)
    cross = _cross(inputs.wt_cross_1h.value)
    if cross_ts is None or not cross:
        blockers.append("INVALID_COMPLETED_INPUT:wt_cross_1h")
    elif cross_ts > asof:
        blockers.append("FUTURE_COMPLETED_INPUT:wt_cross_1h")
    else:
        values["wt_cross_1h"] = (cross, cross_ts)
    needs_1h = htf_gate == "1h" or required > 0
    if needs_1h:
        result, error = _read_pair(inputs.wt_1h, "wt_1h", asof)
        if error: blockers.append(error)
        else: values["wt_1h"] = result

    signatures = {name: tuple(value) for name, value in values.items()}
    old_signatures = dict(old.get("input_signatures", {}) or {})
    for name, signature in signatures.items():
        prior = old_signatures.get(name)
        source_index = -1
        if prior is not None and isinstance(prior, (tuple, list)) and len(prior) == len(signature):
            try:
                same_source = int(prior[source_index]) == int(signature[source_index])
            except (TypeError, ValueError):
                same_source = False
            if same_source and tuple(prior) != signature:
                blockers.append(f"MUTATED_COMPLETED_INPUT:{name}")
    if blockers:
        return WTDCDecision(False, False, None, tuple(dict.fromkeys(blockers)), {**old, "last_asof_ts": asof, "input_signatures": signatures})

    d1, d2, _ = values["wt_d"]
    h41, h42, _ = values["wt_4h"]
    dc, _ = values["dc_position_1h"]
    score_k, _ = values["score_stoch_k_5m"]
    gate_k, _ = values["gate_k_5m"]
    is_long = normalized_side == "LONG"
    score = (
        25.0 * ((d1 > d2) if is_long else (d1 < d2))
        + 25.0 * ((h41 > h42) if is_long else (h41 < h42))
        + 30.0 * (values["wt_cross_1h"][0] == ("BULL" if is_long else "BEAR"))
        + 10.0 * ((dc < .5) if is_long else (dc > .5))
        + 10.0 * ((score_k < 40.0) if is_long else (score_k > 60.0))
    )
    eligible = score >= threshold
    eligible &= gate_k <= 100.0 if is_long else gate_k >= 0.0
    if htf_gate != "none":
        if htf_gate == "1h":
            g1, g2, _ = values["wt_1h"]
            eligible &= (g1 >= g2) if is_long else (g1 <= g2)
        else:
            eligible &= (h41 >= h42) if is_long else (h41 <= h42)
            if htf_gate == "4h_d":
                eligible &= (d1 >= d2) if is_long else (d1 <= d2)
    if required:
        aligned = 0
        for name in ("wt_1h", "wt_4h", "wt_d"):
            left, right, _ = values[name]
            aligned += int((left > right) if is_long else (left < right))
        eligible &= aligned >= required
    if stoch_gate < 100.0:
        eligible &= gate_k < stoch_gate if is_long else gate_k > 100.0 - stoch_gate
    next_state = {"eligible": bool(eligible), "last_asof_ts": asof, "input_signatures": signatures}
    return WTDCDecision(bool(eligible), bool(eligible) and not bool(old.get("eligible", False)), score, (), next_state)
