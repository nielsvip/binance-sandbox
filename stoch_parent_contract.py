"""Shared causal scalar routes for 1h stochastic turns and 4h deep value."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


TURN_FAMILY = "ENTRY_1H_TURN_UP"
DEEP_FAMILY = "ENTRY_4H_DEEP_VALUE"
TURN_DEFINITIONS = {"rising-vs-prior", "cross-d", "either"}
TURN_THRESHOLDS = {20.0, 40.0, 60.0, 80.0}
DEEP_THRESHOLDS = {20.0, 35.0, 50.0, 65.0}


@dataclass(frozen=True)
class CompletedStochParent:
    timeframe: str
    source_close_ts: int
    stoch_k: float
    stoch_d: float | None = None
    stoch_k_prev: float | None = None
    previous_source_close_ts: int | None = None


@dataclass(frozen=True)
class StochParentDecision:
    eligible: bool
    episode_start: bool
    event_pulse: bool
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


def _config(family: str, threshold: float, turn_definition: str | None) -> tuple[str, float, str | None, str]:
    route = str(family)
    parsed_threshold = _finite(threshold)
    if route == TURN_FAMILY:
        if parsed_threshold not in TURN_THRESHOLDS:
            raise ValueError("1h turn threshold must be one of 20, 40, 60, 80")
        definition = str(turn_definition)
        if definition not in TURN_DEFINITIONS:
            raise ValueError("turn_definition must be rising-vs-prior, cross-d, or either")
        return route, parsed_threshold, definition, "1h"
    if route == DEEP_FAMILY:
        if parsed_threshold not in DEEP_THRESHOLDS:
            raise ValueError("4h deep threshold must be one of 20, 35, 50, 65")
        if turn_definition not in (None, ""):
            raise ValueError("4h deep value does not accept turn_definition")
        return route, parsed_threshold, None, "4h"
    raise ValueError("family must be ENTRY_1H_TURN_UP or ENTRY_4H_DEEP_VALUE")


def evaluate_completed_stoch_direct(
    parent: CompletedStochParent,
    *,
    family: str,
    side: str,
    stoch_k_threshold: float,
    turn_definition: str | None = None,
    asof_ts: int,
    prior_state: Mapping[str, Any] | None = None,
) -> StochParentDecision:
    """Evaluate the exact vector scalar state with causal episode semantics.

    The vector's ``rising-vs-prior`` 1h mask is true only on the first base
    row that receives a new completed 1h parent.  ``event_pulse`` reproduces
    that distinction; K-vs-D and 4h deep states remain normal level states.
    """
    route, threshold, definition, required_tf = _config(
        family, stoch_k_threshold, turn_definition
    )
    normalized_side = str(side).upper()
    if normalized_side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    observation = _positive_int(asof_ts)
    if observation is None:
        raise ValueError("asof_ts must be a positive integer timestamp")
    old = dict(prior_state or {})
    if int(old.get("last_asof_ts", 0) or 0) > observation:
        return StochParentDecision(False, False, False, ("OUT_OF_ORDER_OBSERVATION",), old)
    if parent.timeframe != required_tf:
        return StochParentDecision(False, False, False, ("TIMEFRAME_MISMATCH",), {**old, "last_asof_ts": observation})
    source = _positive_int(parent.source_close_ts)
    k = _finite(parent.stoch_k)
    blockers: list[str] = []
    if source is None or k is None:
        blockers.append("INVALID_COMPLETED_PARENT")
    elif source > observation:
        blockers.append("FUTURE_COMPLETED_PARENT")

    needs_prior = route == TURN_FAMILY and definition in {"rising-vs-prior", "either"}
    needs_d = route == TURN_FAMILY and definition in {"cross-d", "either"}
    prior_k = _finite(parent.stoch_k_prev) if needs_prior else None
    prior_source = _positive_int(parent.previous_source_close_ts) if needs_prior else None
    d = _finite(parent.stoch_d) if needs_d else None
    if needs_prior and (prior_k is None or prior_source is None):
        blockers.append("MISSING_PRIOR_COMPLETED_PARENT")
    elif needs_prior and (prior_source >= (source or 0) or prior_source > observation):
        blockers.append("INVALID_PRIOR_COMPLETED_PARENT_ORDER")
    if needs_d and d is None:
        blockers.append("MISSING_COMPLETED_STOCH_D")

    signature = (source, k, d, prior_k, prior_source)
    old_signature = old.get("parent_signature")
    if (
        source is not None and old_signature is not None
        and isinstance(old_signature, (list, tuple)) and len(old_signature) == 5
        and _positive_int(old_signature[0]) == source
        and tuple(old_signature) != signature
    ):
        blockers.append("MUTATED_COMPLETED_PARENT")
    if blockers:
        return StochParentDecision(
            False, False, False, tuple(dict.fromkeys(blockers)),
            {**old, "last_asof_ts": observation},
        )

    assert source is not None and k is not None
    new_parent = source != _positive_int(old.get("last_source_ts"))
    if normalized_side == "LONG":
        zone = k < threshold
        rising = bool(prior_k is not None and k > prior_k)
        cross_d = bool(d is not None and k > d)
    else:
        zone = k > 100.0 - threshold
        rising = bool(prior_k is not None and k < prior_k)
        cross_d = bool(d is not None and k < d)

    if route == DEEP_FAMILY:
        event_pulse = False
        eligible = zone
    else:
        event_pulse = zone and rising and new_parent
        if definition == "rising-vs-prior":
            eligible = event_pulse
        elif definition == "cross-d":
            eligible = zone and cross_d
        else:
            eligible = event_pulse or (zone and cross_d)
    next_state = {
        "eligible": eligible,
        "last_asof_ts": observation,
        "last_source_ts": source,
        "parent_signature": signature,
    }
    return StochParentDecision(
        eligible=eligible,
        episode_start=eligible and not bool(old.get("eligible", False)),
        event_pulse=event_pulse,
        blockers=(),
        next_state=next_state,
    )
