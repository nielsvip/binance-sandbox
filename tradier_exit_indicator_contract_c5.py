"""Candidate c5 raw-indicator contract for STDEV rejection exits.

This module is intentionally NOT imported by ``tradier_manage.py`` and is not
listed in the current c4 matrix fingerprint.  It is the tested implementation
for the controlled c5 cutover documented in
``data/reports/C5_STDEV_RAW_INDICATOR_MIGRATION_20260730.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


C5_CONTRACT_VERSION = "tradier-matrix-exec-c5-stdev-raw-20260730"


@dataclass(frozen=True)
class StdevExitSignal:
    fire: bool
    path: str
    timeframe: str
    pct_b_previous: float
    pct_b_current: float
    wt_velocity_1h: float


def _number(indicators: Mapping[str, Any], key: str, default: float) -> float:
    try:
        raw = indicators.get(key, default)
        return float(default if raw is None else raw)
    except (TypeError, ValueError):
        return float(default)


def completed_parent_value_pair(
    indicators: Mapping[str, Any],
    *,
    timeframe: str,
    state: dict,
    state_key: str,
) -> tuple[float, float]:
    """Return current/previous values across distinct completed HTF parents.

    The frozen stock NPZ carries the completed parent value and source token,
    but not a ``bb_pct_b_*_prev`` array. Forward-filled 5m rows must therefore
    retain the prior *distinct parent*, not use the current value as its own
    predecessor.
    """
    current = _number(indicators, f"bb_pct_b_{timeframe}", 0.5)
    explicit_previous = indicators.get(f"bb_pct_b_{timeframe}_prev")
    if explicit_previous is not None:
        return current, _number(
            indicators, f"bb_pct_b_{timeframe}_prev", current
        )
    token = indicators.get(
        f"_completed_source_ts_{timeframe}",
        indicators.get(f"timestamp_{timeframe}"),
    )
    prior = state.get(state_key)
    if prior is None:
        state[state_key] = {"token": token, "current": current, "previous": current}
        return current, current
    if token != prior.get("token"):
        previous = float(prior.get("current", current))
        state[state_key] = {
            "token": token,
            "current": current,
            "previous": previous,
        }
        return current, previous
    return current, float(prior.get("previous", current))


def stdev_reject_signal(
    indicators: Mapping[str, Any],
    *,
    is_long: bool,
    timeframe: str = "D",
    zone: float = 0.80,
    return_level: float = 0.65,
) -> StdevExitSignal:
    """Evaluate the existing STDEV_REJECT semantics on the raw snapshot."""
    current = _number(indicators, f"bb_pct_b_{timeframe}", 0.5)
    previous = _number(
        indicators, f"bb_pct_b_{timeframe}_prev", current
    )
    velocity = _number(indicators, "wt_velocity_1h", 0.0)
    if is_long:
        fire = previous >= zone and current < return_level and velocity < 0
    else:
        fire = (
            previous <= (1.0 - zone)
            and current > (1.0 - return_level)
            and velocity > 0
        )
    return StdevExitSignal(
        fire=fire,
        path="STDEV_REJECT_EXIT",
        timeframe=str(timeframe),
        pct_b_previous=previous,
        pct_b_current=current,
        wt_velocity_1h=velocity,
    )


def stdev_bb_rz_signal(
    indicators: Mapping[str, Any],
    *,
    is_long: bool,
    timeframe: str = "D",
) -> StdevExitSignal:
    """Evaluate the existing failed-breakout STDEV_BB_RZ semantics raw."""
    current = _number(indicators, f"bb_pct_b_{timeframe}", 0.5)
    previous = _number(
        indicators, f"bb_pct_b_{timeframe}_prev", current
    )
    velocity = _number(indicators, "wt_velocity_1h", 0.0)
    if is_long:
        fire = previous >= 1.0 and current < 1.0 and velocity < 0
    else:
        fire = previous <= 0.0 and current > 0.0 and velocity > 0
    return StdevExitSignal(
        fire=fire,
        path="STDEV_BB_RZ_EXIT",
        timeframe=str(timeframe),
        pct_b_previous=previous,
        pct_b_current=current,
        wt_velocity_1h=velocity,
    )
