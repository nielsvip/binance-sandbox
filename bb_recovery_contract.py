"""Completed-bar failed Bollinger break/recovery entry contract."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class BBRecoveryParams:
    timeframe: str
    recovery_bars: int
    min_excursion_atr: float

    def validate(self) -> None:
        if self.timeframe not in {"15m", "1h", "4h"}:
            raise ValueError("BB recovery timeframe must be 15m, 1h, or 4h")
        if self.recovery_bars not in {1, 2, 4, 8}:
            raise ValueError("recovery_bars must be a selected vector value")
        if self.min_excursion_atr not in {0.0, .25, .5, 1.0}:
            raise ValueError("min_excursion_atr must be a selected vector value")


@dataclass(frozen=True)
class CompletedBBBar:
    timeframe: str
    source_ts: int
    observed_ts: int
    close: float
    bb_upper: float
    bb_lower: float
    atr: float


@dataclass(frozen=True)
class BBRecoveryDecision:
    entry_event: bool
    blockers: tuple[str, ...]
    armed_age: int | None


class BBRecoveryEntry:
    """I/O-free vector-identical state machine, one instance per symbol/side."""

    def __init__(self, params: BBRecoveryParams, *, side: str):
        params.validate()
        self.params = params
        self.side = str(side).upper()
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        self._armed_age: int | None = None
        self._last_source_ts = 0
        self._last_signature: tuple[Any, ...] | None = None

    def step(self, bar: CompletedBBBar) -> BBRecoveryDecision:
        if bar.timeframe != self.params.timeframe:
            return BBRecoveryDecision(False, ("TIMEFRAME_MISMATCH",), self._armed_age)
        signature = (
            int(bar.source_ts), float(bar.close), float(bar.bb_upper),
            float(bar.bb_lower), float(bar.atr), int(bar.observed_ts),
        )
        if not all(isfinite(value) for value in signature[1:5]) or bar.source_ts <= 0 or bar.observed_ts <= 0:
            self._armed_age = None
            return BBRecoveryDecision(False, ("INVALID_COMPLETED_BAR",), self._armed_age)
        if bar.source_ts > bar.observed_ts:
            return BBRecoveryDecision(False, ("FUTURE_COMPLETED_BAR",), self._armed_age)
        if self._last_source_ts and bar.source_ts < self._last_source_ts:
            return BBRecoveryDecision(False, ("OUT_OF_ORDER_COMPLETED_BAR",), self._armed_age)
        if bar.source_ts == self._last_source_ts:
            if self._last_signature != signature:
                return BBRecoveryDecision(False, ("MUTATED_COMPLETED_BAR",), self._armed_age)
            return BBRecoveryDecision(False, (), self._armed_age)
        self._last_source_ts = int(bar.source_ts)
        self._last_signature = signature
        if bar.bb_upper <= bar.bb_lower or bar.atr <= 0:
            self._armed_age = None
            return BBRecoveryDecision(False, ("INVALID_COMPLETED_BAR",), self._armed_age)
        if self.side == "LONG":
            outside = bar.close <= bar.bb_lower - self.params.min_excursion_atr * bar.atr
            recovered = bar.close >= bar.bb_lower
        else:
            outside = bar.close >= bar.bb_upper + self.params.min_excursion_atr * bar.atr
            recovered = bar.close <= bar.bb_upper
        if outside:
            self._armed_age = 0
            return BBRecoveryDecision(False, (), self._armed_age)
        if self._armed_age is None:
            return BBRecoveryDecision(False, (), self._armed_age)
        self._armed_age += 1
        if recovered:
            self._armed_age = None
            return BBRecoveryDecision(True, (), self._armed_age)
        if self._armed_age >= self.params.recovery_bars:
            self._armed_age = None
        return BBRecoveryDecision(False, (), self._armed_age)
