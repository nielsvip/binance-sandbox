"""Pure causal contract shared by ladder research and the Tradier path.

This module intentionally contains no configuration, broker, file, or clock I/O.
Callers supply completed-parent observations and persist the returned state.  The
contract is deliberately small:

* D/4h/1h observations are usable only after their source close is available;
* simultaneous ladder requests resolve to one strongest *absolute* target;
* targets and reclaim size are capped at ``capacity_usd``;
* E02 is an opposite Donchian break on a newly completed 4h parent;
* a full exit latches a side-correct reclaim obligation; and
* an obligation is cleared only after a confirmed fill, never by a veto.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, MutableSet


LADDER_TIMEFRAMES = ("D", "4h", "1h")
DEFAULT_BASE_UNIT_USD = 2_000.0
DEFAULT_CAPACITY_USD = 16_000.0


def _side(value: str) -> str:
    side = str(value).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported position side: {value!r}")
    return side


def ladder_multiplier(
    pct_b: float,
    bottom: float,
    top: float,
    mode: str = "center_plateau",
    *,
    max_multiplier: float = 8.0,
    center: float = 0.5,
    below_bottom: float = 0.0,
    above_top: float | None = None,
) -> float:
    """Return a bounded rung multiplier for one completed parent.

    ``pct_b`` is already side-normalized: zero is the favorable/deep band and
    one is the shallow band.  Values below the lower band do not enter.  Values
    above the upper band retain the shallow rung.
    """
    try:
        pb = float(pct_b)
        lo = float(bottom)
        hi = float(top)
        cap = max(0.0, float(max_multiplier))
    except (TypeError, ValueError):
        return 0.0
    if not all(value == value for value in (pb, lo, hi, cap)):
        return 0.0
    if pb < 0.0:
        return min(cap, max(0.0, float(below_bottom)))
    if str(mode) == "flat":
        # USER 2026-08-06 (Bible §16.62A): "no quantity sizing, only ladder" —
        # every rung at or above the lower band is exactly one base unit;
        # depth never scales size.  The below-band protection stays active.
        return min(cap, 1.0)
    if pb > 1.0:
        value = hi if above_top is None else float(above_top)
        return min(cap, max(0.0, value))
    if str(mode) == "center_plateau":
        plateau = max(1e-6, min(0.999, float(center)))
        value = (
            lo
            if pb <= plateau
            else lo + (hi - lo) * ((pb - plateau) / (1.0 - plateau))
        )
    else:
        value = lo + (hi - lo) * pb
    return min(cap, max(0.0, value))


@dataclass(frozen=True)
class CompletedParentEvent:
    timeframe: str
    source_close_ts: int
    availability_ts: int
    pct_b: float
    wt_cross: str = "NONE"
    structure: bool = False

    @property
    def causal(self) -> bool:
        return (
            self.timeframe in LADDER_TIMEFRAMES
            and int(self.source_close_ts) > 0
            and int(self.source_close_ts) <= int(self.availability_ts)
        )

    @property
    def token(self) -> tuple[str, int]:
        return self.timeframe, int(self.source_close_ts)

    def fires(self, side: str, trigger: str) -> bool:
        if not self.causal:
            return False
        wanted = "BULL" if _side(side) == "LONG" else "BEAR"
        cross = str(self.wt_cross).upper() == wanted
        trigger = str(trigger).lower()
        if trigger == "green":
            return cross
        if trigger == "structure":
            return bool(self.structure)
        if trigger == "union":
            return cross or bool(self.structure)
        raise ValueError(f"unsupported ladder trigger: {trigger!r}")


@dataclass(frozen=True)
class TargetOrder:
    multiplier: float
    target_notional_usd: float
    add_notional_usd: float
    source_tokens: tuple[tuple[str, int], ...]


def strongest_absolute_target(
    events: Iterable[CompletedParentEvent],
    *,
    side: str,
    trigger: str,
    current_notional_usd: float,
    pairs: Mapping[str, tuple[float, float]],
    mode: str = "center_plateau",
    base_unit_usd: float = DEFAULT_BASE_UNIT_USD,
    capacity_usd: float = DEFAULT_CAPACITY_USD,
    seen: MutableSet[tuple[str, int]] | None = None,
) -> TargetOrder | None:
    """Resolve all newly completed parents to one absolute target.

    Every causal source token is consumed at most once.  Taking ``max`` is the
    parity-critical behavior: three parents on one availability batch must not
    become three additive orders.
    """
    _side(side)
    base = max(0.0, float(base_unit_usd))
    capacity = max(0.0, float(capacity_usd))
    current = max(0.0, float(current_notional_usd))
    max_mult = capacity / base if base > 0.0 else 0.0
    source_tokens: list[tuple[str, int]] = []
    strongest = 0.0
    for event in events:
        if not event.causal or (seen is not None and event.token in seen):
            continue
        # A completed parent is consumed whether it fires or not.  Otherwise a
        # persistent cross/structure field can be reinterpreted on every 5m row.
        if seen is not None:
            seen.add(event.token)
        if not event.fires(side, trigger):
            continue
        pair = pairs.get(event.timeframe)
        if pair is None:
            continue
        pb = float(event.pct_b)
        if _side(side) == "SHORT":
            pb = 1.0 - pb
        mult = ladder_multiplier(
            pb,
            pair[0],
            pair[1],
            mode,
            max_multiplier=max_mult,
        )
        if mult > 0.0:
            source_tokens.append(event.token)
            strongest = max(strongest, mult)
    if strongest <= 0.0:
        return None
    target = min(capacity, base * strongest)
    return TargetOrder(
        multiplier=strongest,
        target_notional_usd=target,
        add_notional_usd=min(capacity - min(current, capacity), max(0.0, target - current)),
        source_tokens=tuple(source_tokens),
    )


@dataclass(frozen=True)
class Completed4hE02:
    source_close_ts: int
    availability_ts: int
    close: float
    prior_low: float
    prior_high: float

    @property
    def causal(self) -> bool:
        return (
            int(self.source_close_ts) > 0
            and int(self.source_close_ts) <= int(self.availability_ts)
            and min(float(self.close), float(self.prior_low), float(self.prior_high)) > 0
        )

    @property
    def token(self) -> tuple[str, int]:
        return "4h", int(self.source_close_ts)


@dataclass(frozen=True)
class ExitSignal:
    source_token: tuple[str, int]
    reclaim_reference: float


def e02_exit_signal(
    event: Completed4hE02,
    *,
    side: str,
    seen: MutableSet[tuple[str, int]] | None = None,
) -> ExitSignal | None:
    """Return a completed-parent E02 break and its prior opposite-band top."""
    side = _side(side)
    if not event.causal or (seen is not None and event.token in seen):
        return None
    if seen is not None:
        seen.add(event.token)
    fired = (
        float(event.close) < float(event.prior_low)
        if side == "LONG"
        else float(event.close) > float(event.prior_high)
    )
    if not fired:
        return None
    return ExitSignal(
        source_token=event.token,
        reclaim_reference=(
            float(event.prior_high) if side == "LONG" else float(event.prior_low)
        ),
    )


def reclaim_reference_from_reason(reason: str, fallback: float) -> float:
    """Recover the immutable signal-time E02 reference from an order reason."""
    marker = "reclaim_ref"
    text = str(reason or "")
    start = text.find(marker)
    if start < 0:
        return float(fallback)
    start += len(marker)
    end = start
    allowed = set("0123456789+-.eE")
    while end < len(text) and text[end] in allowed:
        end += 1
    try:
        value = float(text[start:end])
    except (TypeError, ValueError):
        return float(fallback)
    return value if value > 0.0 else float(fallback)


@dataclass
class ReclaimObligation:
    side: str
    pending: bool = False
    exit_fill: float = 0.0
    prior_opposite_level: float = 0.0
    reclaim_level: float = 0.0
    target_notional_usd: float = 0.0
    exit_ts: int = 0
    favorable_gap_seen: bool = False

    def __post_init__(self) -> None:
        self.side = _side(self.side)

    def latch(
        self,
        *,
        exit_fill: float,
        prior_opposite_level: float,
        exited_notional_usd: float,
        exit_ts: int,
        base_unit_usd: float = DEFAULT_BASE_UNIT_USD,
        capacity_usd: float = DEFAULT_CAPACITY_USD,
    ) -> None:
        fill = float(exit_fill)
        opposite = float(prior_opposite_level)
        if fill <= 0.0:
            raise ValueError("exit fill must be positive")
        if opposite <= 0.0:
            opposite = fill
        self.pending = True
        self.exit_fill = fill
        self.prior_opposite_level = opposite
        self.reclaim_level = (
            max(fill, opposite) if self.side == "LONG" else min(fill, opposite)
        )
        self.target_notional_usd = min(
            max(0.0, float(capacity_usd)),
            max(float(base_unit_usd), float(exited_notional_usd)),
        )
        self.exit_ts = int(exit_ts)
        self.favorable_gap_seen = False

    def observe_price(self, price: float) -> None:
        """Remember that price offered a side-favorable lower/higher re-entry."""
        if not self.pending or self.exit_fill <= 0.0:
            return
        px = float(price)
        self.favorable_gap_seen = self.favorable_gap_seen or (
            px < self.exit_fill if self.side == "LONG" else px > self.exit_fill
        )

    def crossed(self, *, high: float, low: float) -> bool:
        if not self.pending or self.reclaim_level <= 0.0:
            return False
        return (
            float(high) >= self.reclaim_level
            if self.side == "LONG"
            else float(low) <= self.reclaim_level
        )

    def confirm_fill(self) -> None:
        self.pending = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ReclaimObligation":
        return cls(
            side=str(value.get("side", "")),
            pending=bool(value.get("pending", False)),
            exit_fill=float(value.get("exit_fill", 0.0) or 0.0),
            prior_opposite_level=float(
                value.get("prior_opposite_level", 0.0) or 0.0
            ),
            reclaim_level=float(value.get("reclaim_level", 0.0) or 0.0),
            target_notional_usd=float(
                value.get("target_notional_usd", 0.0) or 0.0
            ),
            exit_ts=int(value.get("exit_ts", 0) or 0),
            favorable_gap_seen=bool(value.get("favorable_gap_seen", False)),
        )


def next_strictly_later_index(
    availability_ts: Iterable[int], signal_index: int, right: int | None = None
) -> int | None:
    """Return the first row outside the signal's availability batch."""
    values = availability_ts
    if not hasattr(values, "__len__") or not hasattr(values, "__getitem__"):
        values = tuple(int(value) for value in values)
    stop = len(values) if right is None else min(len(values), int(right))
    signal_index = int(signal_index)
    if signal_index < 0 or signal_index >= stop:
        return None
    candidate = bisect_right(
        values,
        int(values[signal_index]),
        lo=signal_index + 1,
        hi=stop,
    )
    return candidate if candidate < stop else None
