"""Shared scalar contract for the Bottom-A protective-trail exit family.

The vector label ``BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED`` denotes research-grid
breadth only.  Exact/live execution always resolves it to the single base
``BOTTOM_A_PROTECTIVE_TRAIL`` state machine defined here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from statistics import pstdev
from typing import Any, Mapping, MutableMapping


BASE_FAMILY = "BOTTOM_A_PROTECTIVE_TRAIL"
RESEARCH_ALIAS = "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"
SUPPORTED_FAMILIES = {BASE_FAMILY, RESEARCH_ALIAS}


def _number(values: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        raw = values.get(key, default)
        value = float(default if raw is None else raw)
        return value if math.isfinite(value) else float(default)
    except (TypeError, ValueError):
        return float(default)


def _source_ts(values: Mapping[str, Any], timeframe: str) -> int:
    raw = values.get(
        f"_completed_source_ts_{timeframe}",
        values.get(f"timestamp_{timeframe}"),
    )
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        try:
            return int(
                datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            )
        except (TypeError, ValueError):
            return 0


def _observation_ts(values: Mapping[str, Any]) -> int:
    for key in ("_tick_ts", "ts"):
        try:
            value = int(float(values.get(key, 0)))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return max((_source_ts(values, tf) for tf in ("5m", "15m", "1h", "4h", "D")), default=0)


@dataclass(frozen=True)
class ProtectiveTrailParams:
    arm_timeframe: str
    trail_timeframe: str
    mode: str
    break_buffer_atr: float = 0.0
    distance_mult: float = 2.0
    lookback: int = 20

    def validate(self) -> None:
        if self.arm_timeframe not in {"15m", "1h", "4h", "D"}:
            raise ValueError("protective arm timeframe must be 15m, 1h, 4h, or D")
        if self.trail_timeframe not in {"5m", "15m", "1h", "4h"}:
            raise ValueError("protective trail timeframe must be 5m, 15m, 1h, or 4h")
        if self.mode not in {"IMMEDIATE", "ATR", "STDEV", "DC"}:
            raise ValueError("unsupported protective trail mode")
        if float(self.break_buffer_atr) not in {0.0, 0.25, 0.5}:
            raise ValueError("unsupported protective break buffer")
        if float(self.distance_mult) <= 0.0 or int(self.lookback) < 4:
            raise ValueError("invalid protective trail distance/lookback")


@dataclass(frozen=True)
class ProtectiveTrailSignal:
    reason: str
    reclaim_reference: float
    arm_source_ts: int
    trail_source_ts: int


def params_from_recipe(exit_recipe: Mapping[str, Any]) -> ProtectiveTrailParams:
    """Resolve either research label to the one executable base family."""
    family = str(exit_recipe.get("family") or exit_recipe.get("name") or "").upper()
    if family not in SUPPORTED_FAMILIES:
        raise ValueError(f"unsupported protective-trail recipe family: {family!r}")
    raw = exit_recipe.get("params") or {}
    params = ProtectiveTrailParams(
        arm_timeframe=str(raw.get("arm_timeframe", "")),
        trail_timeframe=str(raw.get("trail_timeframe", "")),
        mode=str(raw.get("mode", "")).upper(),
        break_buffer_atr=float(raw.get("break_buffer_atr", 0.0)),
        distance_mult=float(raw.get("distance_mult", 2.0)),
        lookback=int(raw.get("lookback", 20)),
    )
    params.validate()
    return params


def recipe_overrides(exit_recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Translate a complete vector EXIT role into shared live/V8 base knobs."""
    params = params_from_recipe(exit_recipe)
    return {
        "BOTTOM_A_PROTECTIVE_TRAIL_ENABLED": True,
        "BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME": params.arm_timeframe,
        "BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME": params.trail_timeframe,
        "BOTTOM_A_PROTECTIVE_TRAIL_MODE": params.mode,
        "BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR": params.break_buffer_atr,
        "BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT": params.distance_mult,
        "BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK": params.lookback,
    }


def _event(values: Mapping[str, Any], timeframe: str) -> dict[str, float | int] | None:
    source = _source_ts(values, timeframe)
    availability = _observation_ts(values)
    if source <= 0 or availability <= 0 or source > availability:
        return None
    event = {
        "source": source,
        "close": _number(values, f"close_{timeframe}"),
        "high": _number(values, f"high_{timeframe}"),
        "low": _number(values, f"low_{timeframe}"),
        "atr": _number(values, f"atr_{timeframe}"),
    }
    if min(float(event["close"]), float(event["high"]), float(event["low"])) <= 0.0:
        return None
    return event


def protective_trail_step(
    state: MutableMapping[str, Any],
    indicators: Mapping[str, Any],
    *,
    side: str,
    active: bool,
    params: ProtectiveTrailParams,
) -> ProtectiveTrailSignal | None:
    """Observe one snapshot and possibly emit one completed-bar exit.

    Completed trail history is retained while flat so STDEV/DC distances have
    the same pre-entry warm-up available to the vector implementation.  Arm
    and trail parents are deduplicated by their immutable source timestamps.
    """
    params.validate()
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    is_long = side == "LONG"
    histories = state.setdefault("histories", {})

    # Ingest each distinct completed parent once.  Keep enough breadth for the
    # largest sanctioned extended-grid lookback plus one prior DC bar.
    for timeframe in {params.arm_timeframe, params.trail_timeframe}:
        event = _event(indicators, timeframe)
        if event is None:
            continue
        history = histories.setdefault(timeframe, [])
        if not history or int(history[-1]["source"]) != int(event["source"]):
            history.append(event)
            del history[:-81]

    if not active:
        arm_history = histories.get(params.arm_timeframe, [])
        if arm_history:
            # A structural parent completed while flat cannot arm a position
            # opened on a later execution row.
            state["last_arm_source_seen"] = int(arm_history[-1]["source"])
        state.update(
            active=False,
            armed=False,
            trail_level=None,
            reclaim_reference=None,
            arm_source_ts=0,
            arm_observation_ts=0,
            arm_trail_source_ts=0,
        )
        return None
    if not state.get("active", False):
        state.update(
            active=True,
            armed=False,
            trail_level=None,
            reclaim_reference=None,
            arm_source_ts=0,
            arm_observation_ts=0,
            arm_trail_source_ts=0,
        )

    arm_history = histories.get(params.arm_timeframe, [])
    current_arm = arm_history[-1] if arm_history else None
    arm_source = int(current_arm["source"]) if current_arm else 0
    new_arm_parent = arm_source > 0 and arm_source != int(state.get("last_arm_source_seen", 0))
    if new_arm_parent:
        state["last_arm_source_seen"] = arm_source
        prior_arm = arm_history[-2] if len(arm_history) >= 2 else None
        if prior_arm is not None and float(prior_arm["atr"]) > 0.0:
            buffer = float(params.break_buffer_atr) * float(prior_arm["atr"])
            adverse_break = (
                float(current_arm["low"]) < float(prior_arm["low"])
                and float(current_arm["close"]) < float(prior_arm["low"]) - buffer
                if is_long
                else float(current_arm["high"]) > float(prior_arm["high"])
                and float(current_arm["close"]) > float(prior_arm["high"]) + buffer
            )
            if adverse_break:
                trail_history = histories.get(params.trail_timeframe, [])
                latest_trail = trail_history[-1] if trail_history else current_arm
                reclaim = float(latest_trail["high"] if is_long else latest_trail["low"])
                state.update(
                    armed=True,
                    trail_level=None,
                    reclaim_reference=reclaim,
                    arm_source_ts=arm_source,
                    arm_observation_ts=_observation_ts(indicators),
                    arm_trail_source_ts=int(latest_trail.get("source", 0)),
                )
                if params.mode == "IMMEDIATE":
                    return ProtectiveTrailSignal(
                        reason="BOTTOM_A_IMMEDIATE_BREAK_DIAGNOSTIC",
                        reclaim_reference=reclaim,
                        arm_source_ts=arm_source,
                        trail_source_ts=0,
                    )
                # The adverse structural bar only arms a non-immediate trail.
                return None

    if not state.get("armed", False):
        return None
    trail_history = histories.get(params.trail_timeframe, [])
    if not trail_history:
        return None
    current = trail_history[-1]
    trail_source = int(current["source"])
    if trail_source == int(state.get("last_trail_source_evaluated", 0)):
        return None
    state["last_trail_source_evaluated"] = trail_source
    if (
        trail_source <= int(state.get("arm_trail_source_ts", 0))
        or _observation_ts(indicators) <= int(state.get("arm_observation_ts", 0))
    ):
        return None

    reclaim = float(state.get("reclaim_reference") or 0.0)
    reclaim = max(reclaim, float(current["high"])) if is_long else min(reclaim, float(current["low"]))
    state["reclaim_reference"] = reclaim
    lookback = int(params.lookback)
    if params.mode == "STDEV":
        if len(trail_history) < lookback:
            return None
        distance = pstdev(float(row["close"]) for row in trail_history[-lookback:])
        candidate = float(current["close"]) + (-1.0 if is_long else 1.0) * float(params.distance_mult) * distance
    elif params.mode == "ATR":
        distance = float(current["atr"])
        candidate = float(current["close"]) + (-1.0 if is_long else 1.0) * float(params.distance_mult) * distance
    elif params.mode == "DC":
        prior = trail_history[-(lookback + 1):-1]
        if len(prior) < lookback:
            return None
        candidate = min(float(row["low"]) for row in prior) if is_long else max(float(row["high"]) for row in prior)
    else:
        return None
    if not math.isfinite(candidate) or candidate <= 0.0:
        return None
    prior_level = state.get("trail_level")
    if prior_level is None:
        level = candidate
    else:
        level = max(float(prior_level), candidate) if is_long else min(float(prior_level), candidate)
    state["trail_level"] = level
    fired = float(current["close"]) <= level if is_long else float(current["close"]) >= level
    if not fired:
        return None
    return ProtectiveTrailSignal(
        reason=f"BOTTOM_A_{params.mode}_{params.trail_timeframe}",
        reclaim_reference=reclaim,
        arm_source_ts=int(state.get("arm_source_ts", 0)),
        trail_source_ts=trail_source,
    )
