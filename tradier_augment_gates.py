"""Pure switch gates shared by live and deterministic wiring tests."""
from __future__ import annotations

from typing import Any


def dc_tier_aug_enabled(config_obj: Any) -> bool:
    """Return the DC-tier gate; missing legacy config preserves enabled behavior."""
    return bool(getattr(config_obj, "DC_TIER_AUG_ENABLED", True))
