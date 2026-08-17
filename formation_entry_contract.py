"""Shared live/V8 admission contract for classic-formation entries."""
from __future__ import annotations


def formation_entry_consumer_admission(
    *, full_recipe_only_enabled: bool, action_already_claimed: bool = False,
) -> tuple[bool, str]:
    """Return whether formation may reach the normal order-claim handoff.

    Formation entry currently enters ``process_position`` only inside the
    isolated full-recipe envelope and only while no higher-priority flat-entry
    route has already claimed the bar.  Keep this policy pure so research can
    fail closed before an expensive exact replay when a GROUP cannot turn a
    selector event into an order.
    """
    if action_already_claimed:
        return False, "HIGHER_PRIORITY_ENTRY_ALREADY_CLAIMED"
    if not full_recipe_only_enabled:
        return False, "FORMATION_ENTRY_CONSUMER_REQUIRES_FULL_RECIPE_ONLY"
    return True, "FORMATION_ENTRY_CONSUMER_ADMITTED"
