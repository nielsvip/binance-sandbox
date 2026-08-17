#!/usr/bin/env python3
"""Frozen causal online controller for entry-request density.

This module is research-only.  It conditions one already-frozen entry
schedule using only completed source-E02 schedule/account state.  It does not
read prices as market features: OHLC is used solely to determine whether a
previously latched reclaim price was touched.

The controller is intentionally one preregistered policy, not a parameter
search.  Its density level is updated only after a complete 126-slot (roughly
one trading month) 1h window.  A new window can therefore use the immediately
preceding completed window, never its own future or a later validation fold.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any

import numpy as np


HARD_MAX_MULT = 8.0


@dataclasses.dataclass(frozen=True)
class Policy:
    name: str = "online_wf126_v1"
    target_utilization: float = 0.75
    utilization_deadband: float = 0.05
    window_completed_1h_slots: int = 126
    drought_completed_1h_slots: int = 8
    drought_scale: float = 1.25
    min_level: int = -2
    max_level: int = 2
    # Index is level + 2, for levels -2, -1, 0, +1, +2.
    request_scale: tuple[float, ...] = (0.75, 0.875, 1.0, 1.25, 1.5)
    request_gap_slots: tuple[int, ...] = (3, 2, 1, 0, 0)
    request_cap_mult: tuple[float, ...] = (5.0, 6.0, 8.0, 8.0, 8.0)
    underfilled_floor_mult: tuple[float, ...] = (0.0, 0.0, 0.0, 4.0, 6.0)

    def validate(self) -> None:
        if not 0 < self.target_utilization < 1:
            raise ValueError("target utilization must be a fraction")
        if not 0 < self.utilization_deadband < self.target_utilization:
            raise ValueError("invalid utilization deadband")
        if self.window_completed_1h_slots < 2:
            raise ValueError("online window must contain completed history")
        if self.drought_completed_1h_slots < 1 or self.drought_scale < 1:
            raise ValueError("invalid drought rule")
        if (self.min_level, self.max_level) != (-2, 2):
            raise ValueError("v1 has five globally frozen density levels")
        arrays = (
            self.request_scale,
            self.request_gap_slots,
            self.request_cap_mult,
            self.underfilled_floor_mult,
        )
        if any(len(values) != 5 for values in arrays):
            raise ValueError("every level table must have five values")
        if any(
            value < 0 or value > HARD_MAX_MULT
            for value in self.request_cap_mult
        ):
            raise ValueError("request cap outside hard capacity")
        if any(
            value < 0 or value > HARD_MAX_MULT
            for value in self.underfilled_floor_mult
        ):
            raise ValueError("floor outside hard capacity")


def policy_grid() -> list[Policy]:
    """Return the single preregistered v1 controller."""
    policy = Policy()
    policy.validate()
    return [policy]


def _level_index(level: int) -> int:
    if level < -2 or level > 2:
        raise ValueError("density level outside frozen table")
    return level + 2


def _update_level(
    level: int,
    *,
    occupied_slots: int,
    completed_slots: int,
    accepted_entries: int,
    realized_exits: int,
    request_drought_slots: int,
    policy: Policy,
) -> tuple[int, dict[str, Any]]:
    """Update from one fully completed prior window.

    Entry/exit density only determines whether an out-of-band correction gets
    a second step.  It cannot reverse the direction set by utilization error.
    """
    if completed_slots != policy.window_completed_1h_slots:
        raise ValueError("only a full completed controller window may update")
    utilization = occupied_slots / completed_slots
    error = policy.target_utilization - utilization
    density_delta_per_100 = (
        100.0 * (accepted_entries - realized_exits) / completed_slots
    )
    step = 0
    reason = "inside_deadband"
    if error > policy.utilization_deadband:
        step = 1
        reason = "underfilled"
        if density_delta_per_100 <= 0 and (
            request_drought_slots >= policy.drought_completed_1h_slots
        ):
            step = 2
            reason = "underfilled_exit_heavy_and_drought"
    elif error < -policy.utilization_deadband:
        step = -1
        reason = "overfilled"
        if density_delta_per_100 > 0:
            step = -2
            reason = "overfilled_entry_heavy"
    updated = int(
        min(policy.max_level, max(policy.min_level, level + step))
    )
    return updated, {
        "prior_level": level,
        "new_level": updated,
        "step": step,
        "reason": reason,
        "completed_slots": completed_slots,
        "occupied_slots": occupied_slots,
        "utilization": utilization,
        "utilization_error_vs_75": error,
        "accepted_entries": accepted_entries,
        "realized_source_e02_exits": realized_exits,
        "entry_minus_exit_density_per_100h": density_delta_per_100,
        "request_drought_slots_at_boundary": request_drought_slots,
    }


def apply_policy(
    entry_mult: np.ndarray,
    source_exit_event: np.ndarray,
    source_exit_ref: np.ndarray,
    bar_open: np.ndarray,
    bar_high: np.ndarray,
    bar_low: np.ndarray,
    completed_1h_slot: np.ndarray,
    policy: Policy,
    *,
    side: str,
    target_semantics: bool,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply the online controller without reading future window state."""
    policy.validate()
    side = side.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(side)
    arrays = [
        np.asarray(entry_mult, dtype=np.float64),
        np.asarray(source_exit_event, dtype=bool),
        np.asarray(source_exit_ref, dtype=np.float64),
        np.asarray(bar_open, dtype=np.float64),
        np.asarray(bar_high, dtype=np.float64),
        np.asarray(bar_low, dtype=np.float64),
        np.asarray(completed_1h_slot, dtype=np.int64),
    ]
    if len({len(values) for values in arrays}) != 1:
        raise ValueError("all controller arrays must align")
    if np.any(np.diff(arrays[-1]) < 0):
        raise ValueError("completed 1h slots must be monotonic")
    raw, exits, refs, opens, highs, lows, slots = arrays
    out = np.zeros(len(raw), dtype=np.float64)

    density_level = 0
    scheduled_mult = 0.0
    prior_exit_mult = 0.0
    reclaim_anchor = math.nan
    reclaim_outstanding = False
    last_accepted_slot: int | None = None

    current_slot: int | None = None
    slot_was_occupied = False
    window_slots = window_occupied = 0
    window_entries = window_exits = 0
    updates: list[dict[str, Any]] = []

    raw_requests = accepted_requests = density_rejections = 0
    drought_boosts = 0
    reclaim_created = reclaim_emitted = reclaim_better_price_fills = 0

    def request_drought(slot: int) -> int:
        if last_accepted_slot is None:
            return policy.drought_completed_1h_slots
        return max(0, slot - last_accepted_slot)

    def close_completed_slot(next_slot: int) -> None:
        nonlocal density_level, window_slots, window_occupied
        nonlocal window_entries, window_exits, slot_was_occupied
        if current_slot is None:
            return
        window_slots += 1
        window_occupied += int(slot_was_occupied)
        if window_slots == policy.window_completed_1h_slots:
            density_level, evidence = _update_level(
                density_level,
                occupied_slots=window_occupied,
                completed_slots=window_slots,
                accepted_entries=window_entries,
                realized_exits=window_exits,
                request_drought_slots=request_drought(next_slot),
                policy=policy,
            )
            evidence["effective_from_completed_1h_slot"] = next_slot
            updates.append(evidence)
            window_slots = window_occupied = 0
            window_entries = window_exits = 0
        slot_was_occupied = False

    for idx in range(len(raw)):
        slot = int(slots[idx])
        if current_slot is None:
            current_slot = slot
        elif slot != current_slot:
            close_completed_slot(slot)
            current_slot = slot

        # Source E02 state is deliberately exogenous to the candidate exit.
        if exits[idx] and scheduled_mult > 0:
            prior_exit_mult = scheduled_mult
            scheduled_mult = 0.0
            ref = float(refs[idx])
            if math.isfinite(ref) and not reclaim_outstanding:
                reclaim_anchor = ref
                reclaim_outstanding = True
                reclaim_created += 1
            window_exits += 1
            # Do not exit and emit a fresh schedule request on one signal bar.
            continue

        # A latched reclaim is mandatory and bypasses density/gap decisions.
        if reclaim_outstanding and math.isfinite(reclaim_anchor):
            touched = (
                opens[idx] >= reclaim_anchor or highs[idx] >= reclaim_anchor
                if side == "LONG"
                else opens[idx] <= reclaim_anchor or lows[idx] <= reclaim_anchor
            )
            if touched:
                value = min(HARD_MAX_MULT, max(0.0, prior_exit_mult))
                if value > 0:
                    out[idx] = value
                    scheduled_mult = value
                    last_accepted_slot = slot
                    window_entries += 1
                    accepted_requests += 1
                    reclaim_emitted += 1
                    reclaim_outstanding = False
                    reclaim_anchor = math.nan
                    slot_was_occupied = True
                    continue

        if raw[idx] <= 0:
            slot_was_occupied |= scheduled_mult > 0
            continue
        raw_requests += 1
        level_idx = _level_index(density_level)
        drought = request_drought(slot)
        gap = int(policy.request_gap_slots[level_idx])
        if (
            last_accepted_slot is not None
            and drought <= gap
            and drought < policy.drought_completed_1h_slots
        ):
            density_rejections += 1
            slot_was_occupied |= scheduled_mult > 0
            continue

        value = float(raw[idx]) * policy.request_scale[level_idx]
        value = max(value, policy.underfilled_floor_mult[level_idx])
        if drought >= policy.drought_completed_1h_slots:
            value *= policy.drought_scale
            drought_boosts += 1
        value = min(
            HARD_MAX_MULT, policy.request_cap_mult[level_idx], max(0.0, value)
        )
        if value <= 0:
            density_rejections += 1
            slot_was_occupied |= scheduled_mult > 0
            continue
        out[idx] = value
        scheduled_mult = (
            max(scheduled_mult, value)
            if target_semantics
            else min(HARD_MAX_MULT, scheduled_mult + value)
        )
        last_accepted_slot = slot
        window_entries += 1
        accepted_requests += 1
        if reclaim_outstanding:
            # A lower-price frozen-family fill satisfies, rather than forgets,
            # the previously latched reclaim obligation.
            reclaim_outstanding = False
            reclaim_anchor = math.nan
            reclaim_better_price_fills += 1
        slot_was_occupied = scheduled_mult > 0

    # The current slot is intentionally not used to update the controller:
    # it may be incomplete at the artifact boundary.
    return out, {
        "policy": dataclasses.asdict(policy),
        "controller_updates": updates,
        "raw_requests": raw_requests,
        "accepted_requests": accepted_requests,
        "rejected_by_density": density_rejections,
        "drought_boosts": drought_boosts,
        "reclaim_obligations_created": reclaim_created,
        "reclaim_requests_emitted": reclaim_emitted,
        "reclaim_obligations_filled_at_better_price": (
            reclaim_better_price_fills
        ),
        "reclaim_obligations_unfilled_at_end": int(reclaim_outstanding),
        "reclaim_anchor_overwrite_count": 0,
        "final_density_level": density_level,
        "partial_window_completed_slots": window_slots,
        "max_output_mult": float(np.max(out)) if len(out) else 0.0,
        "hard_max_mult": HARD_MAX_MULT,
        "source_exit_state": "frozen source E02 only",
        "market_regime_features": False,
        "symbol_specific_thresholds": False,
        "future_window_reads": 0,
    }
