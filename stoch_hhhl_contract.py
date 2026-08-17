"""Causal scalar contract for the vector ``ENTRY_STOCH_HHHL`` route.

The vector research mask is deliberately simple: each enabled completed parent
is HH/HL (or LL/LH for shorts), in the relevant stochastic extreme, and its K
is moving in the trade direction.  This module expresses exactly that decision
on completed-parent snapshots.  It contains no I/O, config lookup, clock use,
or manager mutation so the live adapter and V8 can use the same contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence


SUPPORTED_TIMEFRAMES = ("1h", "4h", "D")


@dataclass(frozen=True)
class CompletedParentSnapshot:
    """One completed parent and the preceding parent used by the vector mask."""

    timeframe: str
    source_close_ts: int
    high: float
    high_prev: float
    low: float
    low_prev: float
    stoch_k: float
    stoch_k_prev: float


@dataclass(frozen=True)
class StochHHHLDecision:
    """Decision plus the serializable state required for episode-start firing."""

    eligible: bool
    episode_start: bool
    active_timeframes: tuple[str, ...]
    blockers: tuple[str, ...]
    next_state: dict[str, Any]


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _normalise_config(
    enabled_tfs: Sequence[str], min_confirming_tfs: int, stoch_threshold: float
) -> tuple[tuple[str, ...], int, float]:
    enabled = tuple(dict.fromkeys(str(tf) for tf in enabled_tfs))
    if not enabled or any(tf not in SUPPORTED_TIMEFRAMES for tf in enabled):
        raise ValueError("enabled_tfs must be a non-empty subset of 1h, 4h, D")
    required = int(min_confirming_tfs)
    threshold = _finite(stoch_threshold)
    if not 1 <= required <= len(enabled):
        raise ValueError("min_confirming_tfs must be between 1 and enabled TF count")
    if threshold is None or not 0.0 <= threshold <= 50.0:
        raise ValueError("stoch_threshold must be finite and between 0 and 50")
    return enabled, required, threshold


def _snapshot_signature(snapshot: CompletedParentSnapshot) -> tuple[Any, ...]:
    """Identity of values expected to stay immutable for one completed parent."""
    return (
        int(snapshot.source_close_ts),
        float(snapshot.high),
        float(snapshot.high_prev),
        float(snapshot.low),
        float(snapshot.low_prev),
        float(snapshot.stoch_k),
        float(snapshot.stoch_k_prev),
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def evaluate_stoch_hhhl_direct(
    snapshots: Mapping[str, CompletedParentSnapshot],
    *,
    side: str,
    enabled_tfs: Sequence[str],
    min_confirming_tfs: int,
    stoch_threshold: float,
    asof_ts: int,
    prior_state: Mapping[str, Any] | None = None,
) -> StochHHHLDecision:
    """Evaluate one causal HHHL direct-entry observation.

    ``asof_ts`` is the decision tick's data-availability timestamp, never wall
    clock.  A source newer than that tick is rejected.  ``episode_start`` is
    true only for the aggregate false-to-true transition, precisely mirroring
    the vector route's ``episode_starts(mask)`` semantics.
    """
    enabled, required, threshold = _normalise_config(
        enabled_tfs, min_confirming_tfs, stoch_threshold
    )
    normalized_side = str(side).upper()
    if normalized_side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    try:
        observation_ts = int(asof_ts)
    except (TypeError, ValueError) as exc:
        raise ValueError("asof_ts must be a positive integer timestamp") from exc
    if observation_ts <= 0:
        raise ValueError("asof_ts must be a positive integer timestamp")

    old = dict(prior_state or {})
    last_asof = int(old.get("last_asof_ts", 0) or 0)
    if last_asof and observation_ts < last_asof:
        return StochHHHLDecision(
            eligible=False,
            episode_start=False,
            active_timeframes=(),
            blockers=("OUT_OF_ORDER_OBSERVATION",),
            next_state=old,
        )

    old_signatures = dict(old.get("snapshot_signatures", {}) or {})
    signatures = dict(old_signatures)
    active: list[str] = []
    blockers: list[str] = []
    for tf in enabled:
        snapshot = snapshots.get(tf)
        if snapshot is None:
            blockers.append(f"MISSING_COMPLETED_PARENT:{tf}")
            continue
        if snapshot.timeframe != tf:
            blockers.append(f"TIMEFRAME_MISMATCH:{tf}")
            continue
        source_ts = _positive_int(snapshot.source_close_ts)
        fields = (
            snapshot.high,
            snapshot.high_prev,
            snapshot.low,
            snapshot.low_prev,
            snapshot.stoch_k,
            snapshot.stoch_k_prev,
        )
        values = tuple(_finite(value) for value in fields)
        if source_ts is None or any(value is None for value in values):
            blockers.append(f"INVALID_COMPLETED_PARENT:{tf}")
            continue
        if source_ts > observation_ts:
            blockers.append(f"FUTURE_COMPLETED_PARENT:{tf}")
            continue
        high, high_prev, low, low_prev, stoch_k, stoch_k_prev = values
        signature = (
            source_ts, high, high_prev, low, low_prev, stoch_k, stoch_k_prev
        )
        previous = old_signatures.get(tf)
        if previous is not None and tuple(previous) != signature and int(previous[0]) == source_ts:
            blockers.append(f"MUTATED_COMPLETED_PARENT:{tf}")
            continue
        signatures[tf] = signature

        if normalized_side == "LONG":
            structure = (
                high > high_prev
                and low > low_prev
                and stoch_k <= threshold
                and stoch_k > stoch_k_prev
            )
        else:
            structure = (
                high < high_prev
                and low < low_prev
                and stoch_k >= 100.0 - threshold
                and stoch_k < stoch_k_prev
            )
        if structure:
            active.append(tf)

    eligible = len(active) >= required
    previously_eligible = bool(old.get("eligible", False))
    next_state = {
        "eligible": eligible,
        "last_asof_ts": observation_ts,
        "snapshot_signatures": signatures,
    }
    return StochHHHLDecision(
        eligible=eligible,
        episode_start=eligible and not previously_eligible,
        active_timeframes=tuple(active),
        blockers=tuple(blockers),
        next_state=next_state,
    )
