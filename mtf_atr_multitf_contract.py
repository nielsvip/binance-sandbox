"""Pure completed-parent 1h/4h/D agreement ATR-ratchet exit contract."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class MtfAtrParams:
    timeframes: tuple[str, ...]
    atr_mult: float
    min_profit_pct: float
    min_confirming_tfs: int

    def validate(self) -> None:
        if self.timeframes != ("1h", "4h", "D"):
            raise ValueError("MTF ATR requires completed 1h/4h/D")
        if self.atr_mult not in {1.5, 2., 2.5, 3., 4.}:
            raise ValueError("unsupported MTF ATR multiple")
        if self.min_profit_pct not in {0., .25, .5, 1.}:
            raise ValueError("unsupported MTF ATR profit gate")
        if self.min_confirming_tfs not in {1, 2}:
            raise ValueError("MTF ATR confirmations must be 1 or 2")


def params_from_recipe(recipe: Mapping[str, Any]) -> MtfAtrParams:
    if str(recipe.get("family") or "") != "EXIT_MTF_ATR_TRAIL":
        raise ValueError("recipe is not EXIT_MTF_ATR_TRAIL")
    raw = recipe.get("params")
    if not isinstance(raw, Mapping):
        raise ValueError("MTF ATR params are required")
    params = MtfAtrParams(
        tuple(str(item) for item in raw.get("timeframes", ())),
        float(raw.get("atr_mult")), float(raw.get("min_profit_pct")),
        int(raw.get("min_confirming_tfs")),
    )
    params.validate()
    return params


@dataclass(frozen=True)
class CompletedATRParent:
    timeframe: str
    source_ts: int
    observed_ts: int
    close: float
    atr: float


@dataclass(frozen=True)
class MtfAtrDecision:
    exit_event: bool
    reason: str | None
    reclaim_reference: float | None
    confirming_timeframes: tuple[str, ...]
    blockers: tuple[str, ...]


class MtfAtrMultitfExit:
    """I/O-free vector-equivalent from-entry ratchets, one position/side."""

    def __init__(self, params: MtfAtrParams, *, side: str):
        params.validate()
        self.params = params
        self.side = str(side).upper()
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        self.reset()

    def reset(self) -> None:
        self.trail_by_tf = {tf: float("nan") for tf in self.params.timeframes}
        self.adverse_by_tf = {tf: False for tf in self.params.timeframes}
        self.source_by_tf = {tf: 0 for tf in self.params.timeframes}
        self.signature_by_tf: dict[str, tuple[Any, ...]] = {}
        self.reclaim_reference = float("nan")

    def step(
        self,
        *,
        active: bool,
        entry_price: float,
        current_gain_pct: float,
        execution_high: float,
        execution_low: float,
        parents: Sequence[CompletedATRParent],
    ) -> MtfAtrDecision:
        if not active:
            self.reset()
            return MtfAtrDecision(False, None, None, (), ())
        if not all(isfinite(value) for value in (entry_price, current_gain_pct, execution_high, execution_low)) or entry_price <= 0:
            return MtfAtrDecision(False, None, None, (), ("INVALID_POSITION_OR_EXECUTION",))
        if self.side == "LONG":
            self.reclaim_reference = max(self.reclaim_reference, execution_high) if isfinite(self.reclaim_reference) else execution_high
        else:
            self.reclaim_reference = min(self.reclaim_reference, execution_low) if isfinite(self.reclaim_reference) else execution_low
        if not parents:
            return MtfAtrDecision(False, None, None, (), ())
        blockers: list[str] = []
        seen: set[str] = set()
        for parent in parents:
            if parent.timeframe not in self.params.timeframes or parent.timeframe in seen:
                blockers.append("INVALID_OR_DUPLICATE_TIMEFRAME")
                continue
            seen.add(parent.timeframe)
            signature = (parent.source_ts, parent.observed_ts, parent.close, parent.atr)
            if parent.source_ts <= 0 or parent.observed_ts <= 0 or parent.source_ts > parent.observed_ts:
                blockers.append(f"FUTURE_OR_INVALID_COMPLETED_PARENT:{parent.timeframe}")
                continue
            prior = self.signature_by_tf.get(parent.timeframe)
            if prior is not None and parent.source_ts < self.source_by_tf[parent.timeframe]:
                blockers.append(f"OUT_OF_ORDER_COMPLETED_PARENT:{parent.timeframe}")
                continue
            if prior is not None and parent.source_ts == self.source_by_tf[parent.timeframe]:
                if prior != signature:
                    blockers.append(f"MUTATED_COMPLETED_PARENT:{parent.timeframe}")
                continue
            self.signature_by_tf[parent.timeframe] = signature
            self.source_by_tf[parent.timeframe] = parent.source_ts
            if not isfinite(parent.close) or parent.close <= 0 or not isfinite(parent.atr) or parent.atr <= 0:
                self.adverse_by_tf[parent.timeframe] = False
                blockers.append(f"INVALID_COMPLETED_PARENT:{parent.timeframe}")
                continue
            previous = self.trail_by_tf[parent.timeframe]
            if self.side == "LONG":
                candidate = max(entry_price - self.params.atr_mult * parent.atr, parent.close - self.params.atr_mult * parent.atr)
                trail = max(previous, candidate) if isfinite(previous) else candidate
                adverse = parent.close < trail
            else:
                candidate = min(entry_price + self.params.atr_mult * parent.atr, parent.close + self.params.atr_mult * parent.atr)
                trail = min(previous, candidate) if isfinite(previous) else candidate
                adverse = parent.close > trail
            self.trail_by_tf[parent.timeframe] = trail
            self.adverse_by_tf[parent.timeframe] = adverse
        if blockers:
            return MtfAtrDecision(False, None, None, (), tuple(blockers))
        confirming = tuple(tf for tf in self.params.timeframes if self.adverse_by_tf[tf])
        if len(confirming) < self.params.min_confirming_tfs or current_gain_pct + 1e-12 < self.params.min_profit_pct:
            return MtfAtrDecision(False, None, None, confirming, ())
        return MtfAtrDecision(
            True,
            f"EXIT_MTF_ATR_TRAIL_{self.params.min_confirming_tfs}TF_x{self.params.atr_mult:g}_p{self.params.min_profit_pct:g}",
            self.reclaim_reference, confirming, (),
        )
