"""Pure Bottom-B delayed lower-top exit contract.

This is a narrow, named adapter over the already-vector-parity structural WT
state machine.  Bottom-B arms on the adverse break and exits only after the
later rebound/lower-top confirmation; it deliberately has no emergency brake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from vec_paths.structural_wt_retest_exit import (
    CompletedBar,
    ExitSignal,
    StructuralWtParams,
    StructuralWtRetestExitBook,
)


BASE_FAMILY = "BOTTOM_B_DELAYED_LOWER_TOP"
RESEARCH_ALIAS = "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED"


@dataclass(frozen=True)
class BottomBDecision:
    reason: str
    source_ts: int
    arm_source_ts: int
    reclaim_reference: float


def params_from_recipe(recipe: Mapping[str, Any]) -> StructuralWtParams:
    """Validate the exact frozen Bottom-B parameter envelope, fail closed."""
    family = str(recipe.get("family") or "")
    if family not in {BASE_FAMILY, RESEARCH_ALIAS}:
        raise ValueError("recipe is not a Bottom-B delayed lower-top exit")
    raw = recipe.get("params")
    if not isinstance(raw, Mapping):
        raise ValueError("Bottom-B recipe params are required")
    required = {
        "arm_tf", "confirm_tf", "rebound_atr", "prebreak_lookback",
        "max_wait_1h", "max_wait_hours", "confirmation_mode",
        "confirmation_bars", "emergency_modes", "emergency_adverse_atr",
        "emergency_adverse_stdev", "emergency_continued_bars",
        "arm_break_mode", "arm_break_threshold",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError(f"Bottom-B params missing: {sorted(missing)}")
    modes = tuple(str(value) for value in raw["emergency_modes"])
    if modes or any(float(raw[name]) != 0.0 for name in (
        "emergency_adverse_atr", "emergency_adverse_stdev", "emergency_continued_bars"
    )):
        raise ValueError("Bottom-B must not include Bottom-C emergency behavior")
    params = StructuralWtParams(
        arm_tf=str(raw["arm_tf"]),
        confirm_tf=str(raw["confirm_tf"]),
        rebound_atr=float(raw["rebound_atr"]),
        prebreak_lookback=int(raw["prebreak_lookback"]),
        max_wait_1h=int(raw["max_wait_1h"]),
        confirmation_mode=str(raw["confirmation_mode"]),
        confirmation_bars=int(raw["confirmation_bars"]),
        emergency_modes=modes,
        emergency_adverse_atr=float(raw["emergency_adverse_atr"]),
        emergency_adverse_stdev=float(raw["emergency_adverse_stdev"]),
        emergency_continued_bars=int(raw["emergency_continued_bars"]),
        arm_break_mode=str(raw["arm_break_mode"]),
        arm_break_threshold=float(raw["arm_break_threshold"]),
    )
    params.validate()
    bars_per_hour = {"5m": 12, "15m": 4, "1h": 1, "4h": .25}[params.confirm_tf]
    expected_wait = max(3, int(float(raw["max_wait_hours"]) * bars_per_hour))
    if params.max_wait_1h != expected_wait:
        raise ValueError("max_wait_1h is inconsistent with selected confirm timeframe/hours")
    return params


class BottomBDelayedLowerTop:
    """Stateful but I/O-free bridge used identically by live and V8 adapters."""

    def __init__(self, recipe: Mapping[str, Any]):
        self.params = params_from_recipe(recipe)
        self._book = StructuralWtRetestExitBook(self.params)

    def update(
        self,
        *,
        symbol: str,
        position_side: str,
        active: bool,
        bar: CompletedBar,
        role: str,
    ) -> BottomBDecision | None:
        signal: ExitSignal | None = self._book.update(
            symbol=symbol, position_side=position_side, active=active,
            bar=bar, role=role,
        )
        if signal is None:
            return None
        return BottomBDecision(
            reason=f"BOTTOM_B_DELAYED_LOWER_TOP__{signal.reason}",
            source_ts=signal.source_ts,
            arm_source_ts=signal.arm_source_ts,
            reclaim_reference=signal.retest_price,
        )
