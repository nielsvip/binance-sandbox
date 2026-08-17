"""Small pure guards shared by live Tradier routing and exact backtests."""
from __future__ import annotations

from typing import Mapping


MANDATORY_RECLAIM_REASON_PREFIX = "MANDATORY_REENTRY_PRICE_CROSS"
ORDINARY_LADDER_TARGET_REASON_PREFIX = "LR_BAND_LADDER_PARITY_TARGET"


def is_mandatory_reclaim_reason(reason: object) -> bool:
    """Identify the exact post-exit price-cross reclaim route.

    Exit reasons also contain the broad ``MANDATORY_REENTRY`` token.  They must
    not inherit entry-gate or sizing bypasses, so this intentionally matches
    only the reason emitted after the price-cross and multi-TF opposition check.
    """
    return str(reason or "").strip().upper().startswith(
        MANDATORY_RECLAIM_REASON_PREFIX
    )


def is_ordinary_ladder_target_reason(reason: object) -> bool:
    """Identify the completed-parent ordinary ladder absolute-target route."""
    return str(reason or "").strip().upper().startswith(
        ORDINARY_LADDER_TARGET_REASON_PREFIX
    )


def flat_entry_data_error(
    indicators: Mapping[str, object] | None,
    *,
    has_position: bool,
    stateful_route_required: bool = False,
) -> bool:
    """Return whether a flat candidate has an unusable neutral short-TF view.

    Neutral/missing short-timeframe data may reject a new entry, but an
    existing position must always continue to its exit monitor.
    """
    if has_position or stateful_route_required:
        return False
    values = indicators or {}
    return values.get("k_1m") == 50.0 and values.get("k_5m") == 50.0
