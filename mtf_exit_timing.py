"""Pure timing helpers shared by live and simulated MTF compound exits."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def timeframe_seconds(timeframe: Any, default: int = 300) -> int:
    """Return the duration of one configured timeframe bar in seconds."""
    raw = str(timeframe or "").strip()
    if not raw:
        return int(default)
    unit = raw[-1]
    amount_raw = raw[:-1]
    try:
        amount = int(amount_raw) if amount_raw else 1
    except (TypeError, ValueError):
        return int(default)
    if amount <= 0:
        return int(default)
    multipliers = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
        "D": 24 * 60 * 60,
        "w": 7 * 24 * 60 * 60,
        "W": 7 * 24 * 60 * 60,
        # Calendar months vary. This is only a bounded event-age window.
        "M": 30 * 24 * 60 * 60,
    }
    multiplier = multipliers.get(unit)
    return amount * multiplier if multiplier is not None else int(default)


def indicator_event_timestamp(
    indicators: Mapping[str, Any],
    fallback_now: Optional[float] = None,
) -> float:
    """Prefer an injected simulation tick timestamp, then the indicator timestamp."""
    for key in ("_tick_ts", "ts"):
        try:
            value = float(indicators.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return float(fallback_now or 0)


def event_within_lookback(
    now_ts: float,
    event_ts: float,
    lookback_bars: int,
    timeframe: Any,
) -> bool:
    """Whether an earlier event remains eligible in a timeframe-sized window."""
    try:
        now = float(now_ts)
        event = float(event_ts)
    except (TypeError, ValueError):
        return False
    if now <= 0 or event <= 0:
        return False
    age = now - event
    window = max(1, int(lookback_bars)) * timeframe_seconds(timeframe)
    return 0 <= age <= window


def dc_reject_step(
    outside_ts: float,
    *,
    now_ts: float,
    price: float,
    band: float,
    lookback_bars: int,
    timeframe: Any,
    is_long: bool,
) -> tuple[float, bool]:
    """Advance the live MTF Donchian outside/re-cross state by one tick.

    The band must already be the latest causally available completed-parent
    channel.  LONG exits arm above the upper band and fire after moving back
    below it; SHORT exits are the exact side mirror.
    """
    try:
        prior = float(outside_ts or 0)
        now = float(now_ts)
        px = float(price)
        edge = float(band)
    except (TypeError, ValueError):
        return float(outside_ts or 0), False
    if now <= 0 or edge <= 0:
        return prior, False
    outside = px > edge if is_long else px < edge
    recross = px < edge if is_long else px > edge
    if outside:
        return now, False
    if recross and event_within_lookback(
        now,
        prior,
        lookback_bars,
        timeframe,
    ):
        return 0.0, True
    if recross:
        return 0.0, False
    return prior, False


def position_open_is_eligible(position_open_ts: float, min_open_ts: float) -> bool:
    """Apply the MTF restart/simulation-start gate to a position open time."""
    try:
        opened = float(position_open_ts)
        cutoff = float(min_open_ts)
    except (TypeError, ValueError):
        return False
    return opened > 0 and opened >= cutoff


def effective_mtf_min_open_ts(
    configured_min_open_ts: float,
    simulation_start_ts: Optional[float] = None,
) -> float:
    """Keep the live restart cutoff unless a backtest explicitly supplies its start."""
    if simulation_start_ts is not None and float(simulation_start_ts) > 0:
        return float(simulation_start_ts)
    return float(configured_min_open_ts or 0)
