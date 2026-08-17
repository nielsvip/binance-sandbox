"""Shared scalar predicate for the frozen WDAY_SHORT composite entry recipe.

This is a newly named strategy.  It deliberately does not revive the removed
``LONG_WAIT`` label described by BACKTEST_BIBLE.md section 15.15.

Both live Tradier and V8 call this module through
``ez_positions_quick.check_entry_candidates_for_account``.  The predicate is
fail-closed: the master switch is off by default and every required indicator
must be present and finite.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional


PREFIX = "ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1"


def wday_short_recipe_overrides() -> dict[str, Any]:
    """Return the canonical config overrides for the frozen WDAY_SHORT recipe."""
    return {
        f"{PREFIX}_ENABLED": True,
        f"{PREFIX}_SYMBOLS": ("WDAY",),
        f"{PREFIX}_SIDE": "SHORT",
        f"{PREFIX}_BOUNCE_TIMEFRAME": "5m",
        f"{PREFIX}_BOUNCE_DISTANCE": 0.015,
        f"{PREFIX}_DEEP_K4H": 50.0,
        f"{PREFIX}_TURN_K1H": 40.0,
        f"{PREFIX}_CONFIRMATION_MIN": 2,
    }


def _setting(cfg: Any, name: str, default: Any) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _number(values: Mapping[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name not in values:
            continue
        try:
            value = float(values[name])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def evaluate_bounce_deep_turn_composite_v1(
    indicators: Mapping[str, Any],
    side: str,
    current_price: float,
    cfg: Any,
    *,
    symbol: str = "",
) -> Optional[dict[str, Any]]:
    """Evaluate one completed observation using the research predicate exactly.

    The SHORT mirror uses ``100 - deep`` and ``100 - turn``, matching
    ``tools/vec_entry_overlay_walkforward.py::_long_wait_masks``.  A returned
    dictionary is an entry decision; ``None`` means no entry.
    """
    if not bool(_setting(cfg, f"{PREFIX}_ENABLED", False)):
        return None

    side = str(side).upper()
    configured_side = str(_setting(cfg, f"{PREFIX}_SIDE", "SHORT")).upper()
    if side not in {"LONG", "SHORT"} or side != configured_side:
        return None

    configured_symbols = _setting(cfg, f"{PREFIX}_SYMBOLS", ("WDAY",))
    if isinstance(configured_symbols, str):
        configured_symbols = tuple(
            part.strip().upper() for part in configured_symbols.split(",") if part.strip()
        )
    else:
        configured_symbols = tuple(str(part).upper() for part in configured_symbols or ())
    symbol = str(symbol).upper()
    if configured_symbols and symbol not in configured_symbols:
        return None

    timeframe = str(_setting(cfg, f"{PREFIX}_BOUNCE_TIMEFRAME", "5m"))
    if timeframe not in {"5m", "15m"}:
        return None
    try:
        distance_limit = float(_setting(cfg, f"{PREFIX}_BOUNCE_DISTANCE", 0.015))
        deep_k4h = float(_setting(cfg, f"{PREFIX}_DEEP_K4H", 50.0))
        turn_k1h = float(_setting(cfg, f"{PREFIX}_TURN_K1H", 40.0))
        confirmation_min = int(_setting(cfg, f"{PREFIX}_CONFIRMATION_MIN", 2))
        price = float(current_price)
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(price)
        or price <= 0
        or not math.isfinite(distance_limit)
        or distance_limit < 0
        or confirmation_min < 1
        or confirmation_min > 3
    ):
        return None

    k4 = _number(indicators, "k_4h", "stoch_k_4h")
    k1 = _number(indicators, "k_1h", "stoch_k_1h")
    k1_prev = _number(indicators, "k_1h_prev", "stoch_k_1h_prev")
    d1 = _number(indicators, "d_1h", "stoch_d_1h")
    k5 = _number(indicators, "k_5m", "stoch_k_5m")
    d5 = _number(indicators, "d_5m", "stoch_d_5m")
    k15 = _number(indicators, "k_15m", "stoch_k_15m")
    d15 = _number(indicators, "d_15m", "stoch_d_15m")
    wt1 = _number(indicators, "wt1_15m")
    wt2 = _number(indicators, "wt2_15m")
    required = (k4, k1, k1_prev, d1, k5, d5, k15, d15, wt1, wt2)
    if any(value is None for value in required):
        return None

    if side == "LONG":
        channel = _number(indicators, f"dc_low_{timeframe}")
        if channel is None or channel <= 0 or price < channel:
            return None
        bounce_distance = (price - channel) / channel
        bounce = bounce_distance <= distance_limit
        deep = k4 < deep_k4h
        turn = (k1 < turn_k1h) and (k1 > k1_prev or k1 > d1)
        votes = int(k5 > d5) + int(k15 > d15) + int(wt1 > wt2)
    else:
        channel = _number(indicators, f"dc_high_{timeframe}")
        if channel is None or channel <= 0 or price > channel:
            return None
        bounce_distance = (channel - price) / channel
        bounce = bounce_distance <= distance_limit
        deep = k4 > 100.0 - deep_k4h
        turn_threshold = 100.0 - turn_k1h
        turn = (k1 > turn_threshold) and (k1 < k1_prev or k1 < d1)
        votes = int(k5 < d5) + int(k15 < d15) + int(wt1 < wt2)

    if not (bounce and deep and turn and votes >= confirmation_min):
        return None

    reason = (
        f"{PREFIX}_{side}_tf={timeframe}_dist={bounce_distance:.6f}_"
        f"k4={k4:.2f}_k1={k1:.2f}_votes={votes}of3"
    )
    return {
        "entry": True,
        "side": side,
        "symbol": symbol,
        "reason": reason,
        "bounce_distance": bounce_distance,
        "confirmation_votes": votes,
    }

