"""Causal contract for the quarantined vector ``ENTRY_LONG_WAIT_ENABLED``.

``LONG_WAIT`` was a removed score label, not a live order route.  The vector
reconstruction is nevertheless a precise *compound* predicate: a completed
Donchian proximity, 4h stochastic deep-value state, 1h turn state, and a
selected directional confirmation all have to be true on the same causal
observation.  This module records that predicate for a future explicit
live/V8 adapter; it never asserts that the deleted label remains live.

It intentionally does not call :mod:`bounce_donchian_contract`.  Their shared
notion of a directional Donchian proximity is not sufficient composition:
the reconstruction uses a latest completed (not prior-channel) value, an
inclusive distance boundary, and WT/two-of-three confirmation modes that the
stand-alone Bounce route does not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


TIMEFRAMES = {"5m", "15m"}
DISTANCES = {0.004, 0.008, 0.015, 0.025}
DEEP_K4H_VALUES = {20.0, 35.0, 50.0, 65.0}
TURN_K1H_VALUES = {20.0, 40.0, 60.0, 80.0}
CONFIRMATIONS = {"stoch5", "stoch15", "wt15", "two-of-three"}


@dataclass(frozen=True)
class CausalScalar:
    value: float
    source_close_ts: int


@dataclass(frozen=True)
class LongWaitInputs:
    """All scalar inputs for one base-bar aligned vector observation."""

    price: CausalScalar
    completed_channel: CausalScalar
    stoch_k_4h: CausalScalar
    stoch_k_1h: CausalScalar
    stoch_k_1h_previous_row: CausalScalar
    stoch_d_1h: CausalScalar
    stoch_k_5m: CausalScalar | None = None
    stoch_d_5m: CausalScalar | None = None
    stoch_k_15m: CausalScalar | None = None
    stoch_d_15m: CausalScalar | None = None
    wt1_15m: CausalScalar | None = None
    wt2_15m: CausalScalar | None = None


@dataclass(frozen=True)
class LongWaitDecision:
    eligible: bool
    episode_start: bool
    proximity: float | None
    confirmation_votes: int | None
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


def _scalar(
    item: CausalScalar | None, name: str, asof_ts: int
) -> tuple[float | None, int | None, str | None]:
    if item is None:
        return None, None, f"MISSING_COMPLETED_INPUT:{name}"
    value = _finite(item.value)
    source_ts = _positive_int(item.source_close_ts)
    if value is None or source_ts is None:
        return None, None, f"INVALID_COMPLETED_INPUT:{name}"
    if source_ts > asof_ts:
        return None, None, f"FUTURE_COMPLETED_INPUT:{name}"
    return value, source_ts, None


def _params(
    *, bounce_timeframe: str, bounce_distance: float, deep_k4h: float,
    turn_k1h: float, confirmation: str,
) -> tuple[str, float, float, float, str]:
    timeframe = str(bounce_timeframe)
    distance, deep, turn = (
        _finite(bounce_distance), _finite(deep_k4h), _finite(turn_k1h)
    )
    mode = str(confirmation)
    if timeframe not in TIMEFRAMES:
        raise ValueError("bounce_timeframe must be 5m or 15m")
    if distance not in DISTANCES:
        raise ValueError("bounce_distance is not a vector candidate value")
    if deep not in DEEP_K4H_VALUES:
        raise ValueError("deep_k4h is not a vector candidate value")
    if turn not in TURN_K1H_VALUES:
        raise ValueError("turn_k1h is not a vector candidate value")
    if mode not in CONFIRMATIONS:
        raise ValueError("confirmation is not a vector candidate value")
    return timeframe, distance, deep, turn, mode


def params_from_recipe(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonically serialize a frozen selected ENTRY envelope."""
    if str(entry.get("family") or "") != "ENTRY_LONG_WAIT_ENABLED":
        raise ValueError("entry family must be ENTRY_LONG_WAIT_ENABLED")
    raw = entry.get("params")
    if not isinstance(raw, Mapping):
        raise ValueError("entry params must be a mapping")
    tf, distance, deep, turn, confirmation = _params(
        bounce_timeframe=raw.get("bounce_timeframe"),
        bounce_distance=raw.get("bounce_distance"),
        deep_k4h=raw.get("deep_k4h"),
        turn_k1h=raw.get("turn_k1h"),
        confirmation=raw.get("confirmation"),
    )
    return {
        "bounce_timeframe": tf,
        "bounce_distance": distance,
        "deep_k4h": deep,
        "turn_k1h": turn,
        "confirmation": confirmation,
    }


def evaluate_long_wait_direct(
    inputs: LongWaitInputs,
    *,
    side: str,
    bounce_timeframe: str,
    bounce_distance: float,
    deep_k4h: float,
    turn_k1h: float,
    confirmation: str,
    asof_ts: int,
    prior_state: Mapping[str, Any] | None = None,
) -> LongWaitDecision:
    """Evaluate the exact vector compound mask from completed scalar inputs.

    ``stoch_k_1h_previous_row`` is deliberately the prior *aligned vector
    row*, not necessarily a distinct 1h parent.  That preserves the vector
    turn expression exactly when a completed 1h value is forward-filled over
    several base bars.  An entry is only emitted on the aggregate false-to-
    true transition, matching ``_episode_starts`` in the vector campaign.
    """
    _, distance, deep, turn, mode = _params(
        bounce_timeframe=bounce_timeframe,
        bounce_distance=bounce_distance,
        deep_k4h=deep_k4h,
        turn_k1h=turn_k1h,
        confirmation=confirmation,
    )
    normalized_side = str(side).upper()
    if normalized_side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    observation_ts = _positive_int(asof_ts)
    if observation_ts is None:
        raise ValueError("asof_ts must be a positive integer timestamp")
    old = dict(prior_state or {})
    if int(old.get("last_asof_ts", 0) or 0) > observation_ts:
        return LongWaitDecision(False, False, None, None, ("OUT_OF_ORDER_OBSERVATION",), old)

    required: list[tuple[str, CausalScalar | None]] = [
        ("price", inputs.price),
        ("completed_channel", inputs.completed_channel),
        ("stoch_k_4h", inputs.stoch_k_4h),
        ("stoch_k_1h", inputs.stoch_k_1h),
        ("stoch_k_1h_previous_row", inputs.stoch_k_1h_previous_row),
        ("stoch_d_1h", inputs.stoch_d_1h),
    ]
    if mode in {"stoch5", "two-of-three"}:
        required.extend((("stoch_k_5m", inputs.stoch_k_5m), ("stoch_d_5m", inputs.stoch_d_5m)))
    if mode in {"stoch15", "two-of-three"}:
        required.extend((("stoch_k_15m", inputs.stoch_k_15m), ("stoch_d_15m", inputs.stoch_d_15m)))
    if mode in {"wt15", "two-of-three"}:
        required.extend((("wt1_15m", inputs.wt1_15m), ("wt2_15m", inputs.wt2_15m)))

    values: dict[str, float] = {}
    signatures: dict[str, tuple[float, int]] = dict(old.get("input_signatures", {}) or {})
    old_signatures = dict(old.get("input_signatures", {}) or {})
    errors: list[str] = []
    for name, item in required:
        value, source_ts, error = _scalar(item, name, observation_ts)
        if error:
            errors.append(error)
            continue
        assert value is not None and source_ts is not None
        signature = (value, source_ts)
        previous = old_signatures.get(name)
        previous_ts = (
            _positive_int(previous[1])
            if isinstance(previous, (list, tuple)) and len(previous) == 2 else None
        )
        if previous is not None and previous_ts == source_ts and tuple(previous) != signature:
            errors.append(f"MUTATED_COMPLETED_INPUT:{name}")
            continue
        values[name] = value
        signatures[name] = signature
    if values.get("completed_channel", 1.0) <= 0:
        errors.append("INVALID_COMPLETED_INPUT:completed_channel")
    if errors:
        return LongWaitDecision(
            False, False, None, None, tuple(dict.fromkeys(errors)),
            {**old, "last_asof_ts": observation_ts, "input_signatures": signatures},
        )

    price, channel = values["price"], values["completed_channel"]
    k4, k1 = values["stoch_k_4h"], values["stoch_k_1h"]
    previous_k1, d1 = values["stoch_k_1h_previous_row"], values["stoch_d_1h"]
    if normalized_side == "LONG":
        proximity = (price - channel) / channel
        bounce = price >= channel and proximity <= distance
        deep_state = k4 < deep
        turn_state = k1 < turn and (k1 > previous_k1 or k1 > d1)
        compare = lambda a, b: a > b
    else:
        proximity = (channel - price) / channel
        bounce = price <= channel and proximity <= distance
        deep_state = k4 > 100.0 - deep
        turn_state = k1 > 100.0 - turn and (k1 < previous_k1 or k1 < d1)
        compare = lambda a, b: a < b

    confirmation_masks: dict[str, bool] = {}
    if mode in {"stoch5", "two-of-three"}:
        confirmation_masks["stoch5"] = compare(values["stoch_k_5m"], values["stoch_d_5m"])
    if mode in {"stoch15", "two-of-three"}:
        confirmation_masks["stoch15"] = compare(values["stoch_k_15m"], values["stoch_d_15m"])
    if mode in {"wt15", "two-of-three"}:
        confirmation_masks["wt15"] = compare(values["wt1_15m"], values["wt2_15m"])
    votes = sum(bool(value) for value in confirmation_masks.values())
    confirmed = votes >= 2 if mode == "two-of-three" else bool(confirmation_masks[mode])
    eligible = bounce and deep_state and turn_state and confirmed
    next_state = {
        "eligible": eligible,
        "last_asof_ts": observation_ts,
        "input_signatures": signatures,
    }
    return LongWaitDecision(
        eligible, eligible and not bool(old.get("eligible", False)), proximity,
        votes, (), next_state,
    )
