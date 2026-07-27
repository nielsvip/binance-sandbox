#!/usr/bin/env python3
"""Preregistered causal schedule/account-state exposure policies.

This is deliberately separate from the broad-market regime classifier.  The
only inputs are the already-frozen entry request schedule, its completed-bar
source E02 exit/reclaim state, and completed 1h request-event time.  Policies
are global and fixed before any final fold is revealed.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any

import numpy as np


HARD_MAX_MULT = 8.0
LOW_UTIL = 0
MID_UTIL = 1
HIGH_UTIL = 2
STATE_NAMES = {LOW_UTIL: "UNDERFILLED", MID_UTIL: "NEUTRAL", HIGH_UTIL: "SATURATED"}


@dataclasses.dataclass(frozen=True)
class Policy:
    name: str
    scale: tuple[float, float, float]
    min_gap_completed_1h_bars: tuple[int, int, int]
    cap_mult: tuple[float, float, float]
    underfilled_floor_mult: float
    drought_completed_1h_bars: int
    drought_scale: float
    reclaim_obligation_scale: float

    def validate(self) -> None:
        if not (self.scale[LOW_UTIL] >= self.scale[MID_UTIL] >= self.scale[HIGH_UTIL]):
            raise ValueError("state scales must decrease with utilization")
        if not (
            self.min_gap_completed_1h_bars[LOW_UTIL]
            <= self.min_gap_completed_1h_bars[MID_UTIL]
            <= self.min_gap_completed_1h_bars[HIGH_UTIL]
        ):
            raise ValueError("entry gaps must increase with utilization")
        if not (
            HARD_MAX_MULT
            >= self.cap_mult[LOW_UTIL]
            >= self.cap_mult[MID_UTIL]
            >= self.cap_mult[HIGH_UTIL]
            > 0
        ):
            raise ValueError("caps must decrease with utilization and stay <=8x")
        if not 0 <= self.underfilled_floor_mult <= HARD_MAX_MULT:
            raise ValueError("underfilled floor outside capacity")
        if self.drought_completed_1h_bars < 1 or self.drought_scale < 1:
            raise ValueError("invalid drought rule")
        if self.reclaim_obligation_scale < 1:
            raise ValueError("reclaim scale may not weaken a mandatory obligation")


def policy_grid() -> list[Policy]:
    """Tiny fixed global grid, preregistered before final-fold evaluation."""
    rows = [
        Policy(
            "state_refill_gentle",
            (1.25, 1.0, 0.875),
            (0, 0, 1),
            (8.0, 8.0, 6.0),
            4.0,
            8,
            1.25,
            1.0,
        ),
        Policy(
            "state_refill_balanced",
            (1.5, 1.0, 0.75),
            (0, 1, 2),
            (8.0, 6.0, 5.0),
            5.0,
            10,
            1.375,
            1.125,
        ),
        Policy(
            "state_refill_strong",
            (1.75, 1.0, 0.625),
            (0, 1, 3),
            (8.0, 6.0, 4.0),
            6.0,
            12,
            1.5,
            1.25,
        ),
        Policy(
            "state_reclaim_priority",
            (1.25, 1.0, 0.875),
            (0, 0, 1),
            (8.0, 8.0, 6.0),
            4.0,
            8,
            1.25,
            1.5,
        ),
    ]
    for row in rows:
        row.validate()
    return rows


def _util_state(mult: float) -> int:
    util = max(0.0, mult) / HARD_MAX_MULT
    if util < 0.50:
        return LOW_UTIL
    if util < 0.75:
        return MID_UTIL
    return HIGH_UTIL


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
    """Condition a single frozen request schedule using causal state only.

    ``source_exit_*`` is the frozen source-E02 schedule state, never the exit
    candidate being ranked.  This keeps every candidate's entry schedule
    identical while allowing causal capacity/reclaim state to influence the
    density of that one frozen family.
    """
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
        raise ValueError("all state arrays must align")
    if np.any(np.diff(arrays[-1]) < 0):
        raise ValueError("completed 1h slots must be monotonic")
    raw, exits, refs, opens, highs, lows, slots = arrays
    out = np.zeros(len(raw), dtype=np.float64)
    scheduled_mult = 0.0
    prior_exit_mult = 0.0
    reclaim_anchor = math.nan
    reclaim_outstanding = False
    last_accepted_slot: int | None = None
    requests = accepted = rejected = drought_boosts = obligation_requests = 0
    obligations_created = obligations_filled = 0
    by_state = {
        name: {"requests": 0, "accepted": 0}
        for name in STATE_NAMES.values()
    }

    for idx in range(len(raw)):
        if reclaim_outstanding and math.isfinite(reclaim_anchor):
            touched = (
                opens[idx] >= reclaim_anchor or highs[idx] >= reclaim_anchor
                if side == "LONG"
                else opens[idx] <= reclaim_anchor or lows[idx] <= reclaim_anchor
            )
            if touched:
                scheduled_mult = min(HARD_MAX_MULT, prior_exit_mult)
                reclaim_outstanding = False
                reclaim_anchor = math.nan
                obligations_filled += 1
        # An exogenous source exit can only create one obligation while the
        # schedule proxy is filled.  Signals emitted while flat cannot replace
        # or erase an already-latched anchor.
        if exits[idx] and scheduled_mult > 0 and not reclaim_outstanding:
            prior_exit_mult = scheduled_mult
            scheduled_mult = 0.0
            ref = float(refs[idx])
            if math.isfinite(ref):
                reclaim_anchor = ref
                reclaim_outstanding = True
                obligations_created += 1
        if raw[idx] <= 0:
            continue

        requests += 1
        state = _util_state(scheduled_mult)
        state_name = STATE_NAMES[state]
        by_state[state_name]["requests"] += 1
        slot = int(slots[idx])
        gap = policy.min_gap_completed_1h_bars[state]
        if (
            last_accepted_slot is not None
            and slot - last_accepted_slot <= gap
        ):
            rejected += 1
            continue

        value = float(raw[idx]) * policy.scale[state]
        if state == LOW_UTIL:
            value = max(value, policy.underfilled_floor_mult)
        drought = (
            last_accepted_slot is None
            or slot - last_accepted_slot >= policy.drought_completed_1h_bars
        )
        if drought:
            value *= policy.drought_scale
            drought_boosts += 1
        if reclaim_outstanding:
            value *= policy.reclaim_obligation_scale
            obligation_requests += 1
        value = min(max(0.0, value), policy.cap_mult[state], HARD_MAX_MULT)
        if value <= 0:
            rejected += 1
            continue
        out[idx] = value
        accepted += 1
        by_state[state_name]["accepted"] += 1
        last_accepted_slot = slot
        scheduled_mult = (
            max(scheduled_mult, value)
            if target_semantics
            else min(HARD_MAX_MULT, scheduled_mult + value)
        )

    return out, {
        "policy": dataclasses.asdict(policy),
        "requests": requests,
        "accepted": accepted,
        "rejected_by_density": rejected,
        "drought_boosts": drought_boosts,
        "reclaim_obligation_requests": obligation_requests,
        "reclaim_obligations_created": obligations_created,
        "reclaim_obligations_filled": obligations_filled,
        "reclaim_obligations_unfilled_at_end": int(reclaim_outstanding),
        "reclaim_anchor_overwrite_count": 0,
        "max_output_mult": float(np.max(out)) if len(out) else 0.0,
        "hard_max_mult": HARD_MAX_MULT,
        "utilization_thresholds": [0.50, 0.75],
        "source_exit_state": "frozen source E02 only",
        "by_state": by_state,
    }
