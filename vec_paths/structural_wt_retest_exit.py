"""Exact state core for the tested structural-WT rebound exit.

Research/backtest only.  Semantics intentionally match
``tools.vec_structural_wt_rebound_experiment.structural_wt_rebound_signal``:

* a completed 4h close through the prior 4h low arms LONG (mirrored SHORT);
* pre-break price and WT1 anchors come from preceding completed 4h bars;
* later completed 1h bars form a lower rebound top in price and WT1;
* a distinct adverse 1h price bar plus WT1 rollover triggers;
* execution belongs at the next RTH open.

A structural break arms the path; it never exits immediately.  State is keyed
by both symbol and position side.
"""
from __future__ import annotations

import dataclasses
import math
from collections import deque
from typing import Any


@dataclasses.dataclass(frozen=True)
class StructuralWtParams:
    arm_tf: str = "4h"
    confirm_tf: str = "1h"
    rebound_atr: float = 0.5
    prebreak_lookback: int = 6
    max_wait_1h: int = 30

    def validate(self) -> None:
        if self.arm_tf not in {"1h", "4h", "D"}:
            raise ValueError("arm_tf must be 1h, 4h, or D")
        if self.confirm_tf not in {"15m", "1h", "4h"}:
            raise ValueError("confirm_tf must be 15m, 1h, or 4h")
        if self.prebreak_lookback < 2:
            raise ValueError("prebreak_lookback must be >=2")
        if self.max_wait_1h < 3:
            raise ValueError("max_wait_1h must be >=3")
        if self.rebound_atr < 0:
            raise ValueError("rebound_atr must be non-negative")


@dataclasses.dataclass(frozen=True)
class CompletedBar:
    timeframe: str
    source_ts: int
    observed_ts: int
    high: float
    low: float
    close: float
    wt1: float
    atr: float

    def validate(self) -> None:
        if not self.timeframe:
            raise ValueError("completed bar requires timeframe")
        if self.source_ts <= 0 or self.observed_ts <= 0:
            raise ValueError("timestamps must be positive")
        if self.source_ts > self.observed_ts:
            raise ValueError("completed HTF source timestamp is in the future")
        if not all(
            math.isfinite(value)
            for value in (self.high, self.low, self.close, self.wt1, self.atr)
        ):
            raise ValueError("completed bar contains non-finite fields")
        if self.high < self.low or self.close <= 0 or self.atr <= 0:
            raise ValueError("invalid completed bar prices/ATR")


@dataclasses.dataclass(frozen=True)
class ExitSignal:
    position_side: str
    source_ts: int
    observed_ts: int
    arm_source_ts: int
    price_anchor: float
    wt_anchor: float
    retest_price: float
    retest_wt1: float
    reason: str


@dataclasses.dataclass
class _PathState:
    phase: str = "TREND"
    just_armed: bool = False
    arm_source_ts: int = 0
    wait: int = 0
    arm_atr: float = math.nan
    price_anchor: float = math.nan
    wt_anchor: float = math.nan
    damage_extreme: float = math.nan
    rebound_price: float = math.nan
    rebound_wt: float = math.nan
    rebound_source_ts: int = 0

    def reset_arm(self) -> None:
        self.phase = "TREND"
        self.just_armed = False
        self.arm_source_ts = 0
        self.wait = 0
        self.arm_atr = math.nan
        self.price_anchor = math.nan
        self.wt_anchor = math.nan
        self.damage_extreme = math.nan
        self.rebound_price = math.nan
        self.rebound_wt = math.nan
        self.rebound_source_ts = 0


class StructuralWtRetestExitBook:
    """Causal state machine shared by vector and exact-engine routes."""

    def __init__(self, params: StructuralWtParams | None = None):
        self.params = params or StructuralWtParams()
        self.params.validate()
        self._history: dict[tuple[str, str], deque[CompletedBar]] = {}
        self._state: dict[str, _PathState] = {}

    @staticmethod
    def _key(symbol: str, position_side: str) -> str:
        symbol = str(symbol).upper().strip()
        side = str(position_side).upper().strip()
        if not symbol or side not in {"LONG", "SHORT"}:
            raise ValueError("state key requires symbol and LONG/SHORT side")
        return f"{symbol}:{side}"

    def state_snapshot(self, symbol: str, position_side: str) -> dict[str, Any]:
        state = self._state.setdefault(
            self._key(symbol, position_side), _PathState()
        )
        return dataclasses.asdict(state)

    def update(
        self,
        *,
        symbol: str,
        position_side: str,
        active: bool,
        bar: CompletedBar,
        role: str | None = None,
    ) -> ExitSignal | None:
        bar.validate()
        key = self._key(symbol, position_side)
        side = key.rsplit(":", 1)[1]
        role = str(role or "").upper() or None
        if role not in {None, "ARM", "CONFIRM"}:
            raise ValueError("role must be ARM, CONFIRM, or omitted")
        is_arm = role == "ARM" or (
            role is None and bar.timeframe == self.params.arm_tf
        )
        is_confirm = role == "CONFIRM" or (
            role is None and bar.timeframe == self.params.confirm_tf
        )
        history_key = (
            f"{bar.timeframe}:{role}"
            if role is not None
            else bar.timeframe
        )
        history = self._history.setdefault(
            (key, history_key),
            deque(maxlen=max(self.params.prebreak_lookback + 2, 4)),
        )
        state = self._state.setdefault(key, _PathState())
        if history and bar.source_ts < history[-1].source_ts:
            raise ValueError("completed HTF timestamps moved backwards")
        if history and bar.source_ts == history[-1].source_ts:
            return None
        if not active:
            state.reset_arm()
            history.append(bar)
            return None
        if not is_arm and not is_confirm:
            history.append(bar)
            return None

        if is_arm:
            previous = history[-1] if history else None
            prior = list(history)[-self.params.prebreak_lookback :]
            if (
                state.phase == "TREND"
                and previous is not None
                and len(prior) >= self.params.prebreak_lookback
            ):
                if side == "LONG":
                    structural_break = (
                        bar.low < previous.low and bar.close < previous.low
                    )
                else:
                    structural_break = (
                        bar.high > previous.high and bar.close > previous.high
                    )
                if structural_break:
                    state.phase = "WAIT_REBOUND"
                    state.just_armed = True
                    state.arm_source_ts = bar.source_ts
                    state.wait = 0
                    state.arm_atr = bar.atr
                    state.price_anchor = (
                        max(item.high for item in prior)
                        if side == "LONG"
                        else min(item.low for item in prior)
                    )
                    state.wt_anchor = (
                        max(item.wt1 for item in prior)
                        if side == "LONG"
                        else min(item.wt1 for item in prior)
                    )
                    state.rebound_source_ts = 0
            history.append(bar)
            return None

        # Confirm-TF semantics begin here.
        previous = history[-1] if history else None
        if state.phase == "TREND":
            history.append(bar)
            return None
        if state.just_armed:
            state.just_armed = False
            state.damage_extreme = (
                bar.low if side == "LONG" else bar.high
            )
            state.rebound_price = (
                -math.inf if side == "LONG" else math.inf
            )
            state.rebound_wt = -math.inf if side == "LONG" else math.inf
            history.append(bar)
            return None

        state.wait += 1
        if side == "LONG":
            state.damage_extreme = min(state.damage_extreme, bar.low)
            if bar.high >= state.rebound_price:
                state.rebound_price = bar.high
                state.rebound_wt = max(state.rebound_wt, bar.wt1)
                state.rebound_source_ts = bar.source_ts
            enough_rebound = (
                state.rebound_price - state.damage_extreme
                >= self.params.rebound_atr * state.arm_atr
            )
            lower_price_top = state.rebound_price < state.price_anchor
            lower_wt_top = state.rebound_wt < state.wt_anchor
            adverse_price = (
                previous is not None
                and bar.source_ts > state.rebound_source_ts > 0
                and bar.high < previous.high
                and bar.low < previous.low
                and bar.close < previous.close
            )
            wt_rollover = (
                previous is not None
                and bar.wt1 < previous.wt1 <= state.rebound_wt
            )
            invalid = (
                bar.close > state.price_anchor
                or bar.wt1 > state.wt_anchor
            )
            leaf = "LONG_LOWER_PRICE_TOP_LOWER_WT1_TOP"
        else:
            state.damage_extreme = max(state.damage_extreme, bar.high)
            if bar.low <= state.rebound_price:
                state.rebound_price = bar.low
                state.rebound_wt = min(state.rebound_wt, bar.wt1)
                state.rebound_source_ts = bar.source_ts
            enough_rebound = (
                state.damage_extreme - state.rebound_price
                >= self.params.rebound_atr * state.arm_atr
            )
            lower_price_top = state.rebound_price > state.price_anchor
            lower_wt_top = state.rebound_wt > state.wt_anchor
            adverse_price = (
                previous is not None
                and bar.source_ts > state.rebound_source_ts > 0
                and bar.high > previous.high
                and bar.low > previous.low
                and bar.close > previous.close
            )
            wt_rollover = (
                previous is not None
                and bar.wt1 > previous.wt1 >= state.rebound_wt
            )
            invalid = (
                bar.close < state.price_anchor
                or bar.wt1 < state.wt_anchor
            )
            leaf = "SHORT_HIGHER_PRICE_BOTTOM_HIGHER_WT1_BOTTOM"

        if (
            state.phase == "WAIT_REBOUND"
            and enough_rebound
            and lower_price_top
            and lower_wt_top
        ):
            state.phase = "WAIT_CONFIRM"
            history.append(bar)
            return None

        signal = None
        if state.phase == "WAIT_CONFIRM" and adverse_price and wt_rollover:
            signal = ExitSignal(
                position_side=side,
                source_ts=bar.source_ts,
                observed_ts=bar.observed_ts,
                arm_source_ts=state.arm_source_ts,
                price_anchor=state.price_anchor,
                wt_anchor=state.wt_anchor,
                retest_price=state.rebound_price,
                retest_wt1=state.rebound_wt,
                reason=(
                    f"V8_RESEARCH_STRUCT_WT_RETEST_EXIT__{leaf}"
                    f"__arm{state.arm_source_ts}__MANDATORY_REENTRY"
                ),
            )
            state.reset_arm()
        elif invalid or state.wait >= self.params.max_wait_1h:
            state.reset_arm()
        history.append(bar)
        return signal


def next_rth_fill_price(
    raw_open: float, position_side: str, slippage_bps_one_way: float
) -> float:
    raw_open = float(raw_open)
    side = str(position_side).upper()
    if raw_open <= 0 or side not in {"LONG", "SHORT"}:
        raise ValueError("positive open and LONG/SHORT side required")
    slip = float(slippage_bps_one_way) / 10_000.0
    return raw_open * (1.0 - slip if side == "LONG" else 1.0 + slip)
