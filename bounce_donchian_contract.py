"""Causal scalar contract for vector ``ENTRY_BOUNCE_{5M,15M}_LOW``.

The research route is proximity to a *prior completed Donchian channel*,
optionally confirmed by directional 5m/15m stochastic values.  It is not the
similarly named live STDEV/BB path.  This I/O-free module is therefore the only
permitted route identity for a future live/V8 adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


CONFIRMATIONS = {"none", "stoch5", "stoch15", "two-of-two"}
TIMEFRAMES = {"5m", "15m"}


@dataclass(frozen=True)
class CausalScalar:
    value: float
    source_close_ts: int


@dataclass(frozen=True)
class BounceInputs:
    """Exact scalar inputs corresponding to one vector-mask row."""

    price: CausalScalar
    prior_channel: CausalScalar
    stoch_k_5m: CausalScalar | None = None
    stoch_d_5m: CausalScalar | None = None
    stoch_k_15m: CausalScalar | None = None
    stoch_d_15m: CausalScalar | None = None


@dataclass(frozen=True)
class BounceDecision:
    eligible: bool
    episode_start: bool
    proximity: float | None
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


def _read_scalar(
    scalar: CausalScalar | None, name: str, asof_ts: int
) -> tuple[float | None, int | None, str | None]:
    if scalar is None:
        return None, None, f"MISSING_COMPLETED_INPUT:{name}"
    value, source_ts = _finite(scalar.value), _positive_int(scalar.source_close_ts)
    if value is None or source_ts is None:
        return None, None, f"INVALID_COMPLETED_INPUT:{name}"
    if source_ts > asof_ts:
        return None, None, f"FUTURE_COMPLETED_INPUT:{name}"
    return value, source_ts, None


def _normalise(
    *, timeframe: str, distance: float, recovery_only: bool, confirmation: str
) -> tuple[str, float, bool, str]:
    tf = str(timeframe)
    if tf not in TIMEFRAMES:
        raise ValueError("timeframe must be 5m or 15m")
    parsed_distance = _finite(distance)
    if parsed_distance is None or not 0.0 < parsed_distance <= 0.10:
        raise ValueError("distance must be finite and in (0, 0.10]")
    mode = str(confirmation)
    if mode not in CONFIRMATIONS:
        raise ValueError("confirmation must be none, stoch5, stoch15, or two-of-two")
    return tf, parsed_distance, bool(recovery_only), mode


def evaluate_bounce_donchian_direct(
    inputs: BounceInputs,
    *,
    side: str,
    timeframe: str,
    distance: float,
    recovery_only: bool,
    confirmation: str,
    asof_ts: int,
    prior_state: Mapping[str, Any] | None = None,
) -> BounceDecision:
    """Evaluate one vector-equivalent causal Bounce observation.

    ``episode_start`` follows the vector's aggregate mask transition exactly.
    Every used input must be a completed source no newer than ``asof_ts``;
    changing a channel value under the same source identity fails closed.
    """
    _normalise(
        timeframe=timeframe,
        distance=distance,
        recovery_only=recovery_only,
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
        return BounceDecision(False, False, None, ("OUT_OF_ORDER_OBSERVATION",), old)

    price, price_ts, price_error = _read_scalar(inputs.price, "price", observation_ts)
    channel, channel_ts, channel_error = _read_scalar(
        inputs.prior_channel, "prior_channel", observation_ts
    )
    errors = [error for error in (price_error, channel_error) if error]
    if channel is not None and channel <= 0:
        errors.append("INVALID_COMPLETED_INPUT:prior_channel")
    used: dict[str, tuple[float, int]] = {}
    if price is not None and price_ts is not None:
        used["price"] = (price, price_ts)
    if channel is not None and channel_ts is not None:
        used["prior_channel"] = (channel, channel_ts)

    required_pairs = {
        "stoch5": (("stoch_k_5m", inputs.stoch_k_5m), ("stoch_d_5m", inputs.stoch_d_5m)),
        "stoch15": (("stoch_k_15m", inputs.stoch_k_15m), ("stoch_d_15m", inputs.stoch_d_15m)),
    }
    required_modes = (
        () if confirmation == "none" else
        ("stoch5",) if confirmation == "stoch5" else
        ("stoch15",) if confirmation == "stoch15" else
        ("stoch5", "stoch15")
    )
    for mode in required_modes:
        for name, scalar in required_pairs[mode]:
            value, source_ts, error = _read_scalar(scalar, name, observation_ts)
            if error:
                errors.append(error)
            elif value is not None and source_ts is not None:
                used[name] = (value, source_ts)

    old_signatures = dict(old.get("input_signatures", {}) or {})
    signatures = dict(old_signatures)
    for name, signature in used.items():
        previous = old_signatures.get(name)
        previous_ts = _positive_int(previous[1]) if isinstance(previous, (list, tuple)) and len(previous) == 2 else None
        if previous is not None and previous_ts == signature[1] and tuple(previous) != signature:
            errors.append(f"MUTATED_COMPLETED_INPUT:{name}")
            continue
        signatures[name] = signature

    if errors:
        return BounceDecision(
            False, False, None, tuple(dict.fromkeys(errors)),
            {**old, "last_asof_ts": observation_ts, "input_signatures": signatures},
        )

    assert price is not None and channel is not None
    if normalized_side == "LONG":
        proximity = (price - channel) / channel
        in_range = proximity < float(distance)
        recovered = price >= channel
        directional_5 = used.get("stoch_k_5m", (0.0, 0))[0] > used.get("stoch_d_5m", (0.0, 0))[0]
        directional_15 = used.get("stoch_k_15m", (0.0, 0))[0] > used.get("stoch_d_15m", (0.0, 0))[0]
    else:
        proximity = (channel - price) / channel
        in_range = proximity < float(distance)
        recovered = price <= channel
        directional_5 = used.get("stoch_k_5m", (0.0, 0))[0] < used.get("stoch_d_5m", (0.0, 0))[0]
        directional_15 = used.get("stoch_k_15m", (0.0, 0))[0] < used.get("stoch_d_15m", (0.0, 0))[0]
    confirmed = (
        True if confirmation == "none" else directional_5 if confirmation == "stoch5" else
        directional_15 if confirmation == "stoch15" else directional_5 and directional_15
    )
    eligible = in_range and (not recovery_only or recovered) and confirmed
    next_state = {
        "eligible": eligible,
        "last_asof_ts": observation_ts,
        "input_signatures": signatures,
    }
    return BounceDecision(
        eligible, eligible and not bool(old.get("eligible", False)), proximity,
        (), next_state,
    )
