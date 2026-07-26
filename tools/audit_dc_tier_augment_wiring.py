#!/usr/bin/env python3
"""Deterministic wiring audit for DC_TIER_AUG_ENABLED and its active block."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def audit_sources(
    config_source: str,
    manage_source: str,
    gate_source: str = "",
) -> dict:
    declared = "DC_TIER_AUG_ENABLED:" in config_source
    read = (
        "_dc_tier_aug_enabled(config)" in manage_source
        and 'getattr(config_obj, "DC_TIER_AUG_ENABLED", True)' in gate_source
    )
    default_true = "DC_TIER_AUG_ENABLED: bool = True" in config_source
    disabled_reason = '"DC_TIER_AUG_DISABLED"' in manage_source
    function = "async def evaluate_augment(" in manage_source
    tier_reason = "DC_TIER{active_tier}_" in manage_source
    hardcoded = (
        "buf = 0.001" in manage_source
        and "tier_mults = {1: 1.0, 2: 2.0, 3: 3.0, 4: 5.0}" in manage_source
        and "current_value < target_value * 0.75" in manage_source
    )
    routed = ".evaluate_augment(" in manage_source
    return {
        "inventory_switch": "DC_TIER_AUG_ENABLED",
        "switch_declared": declared,
        "switch_read": read,
        "switch_default_true": default_true,
        "disabled_reason_present": disabled_reason,
        "evaluate_augment_present": function,
        "evaluate_augment_routed": routed,
        "tier_reason_present": tier_reason,
        "hardcoded_source_settings_present": hardcoded,
        "classification": (
            "CONNECTED_DEFAULT_TRUE_EXISTING_BEHAVIOR_PRESERVED"
            if declared and read and default_true and disabled_reason
            and function and routed and tier_reason and hardcoded
            else "PHANTOM_SWITCH_ACTIVE_UNCONDITIONAL_FUNCTION"
            if not declared and not read and function and routed and tier_reason and hardcoded
            else "REQUIRES_FRESH_MANUAL_AUDIT"
        ),
    }


def audit_repo(root: Path = ROOT) -> dict:
    return audit_sources(
        (root / "config_tradier.py").read_text(),
        (root / "tradier_manage.py").read_text(),
        (root / "tradier_augment_gates.py").read_text(),
    )


if __name__ == "__main__":
    print(json.dumps(audit_repo(), indent=2, sort_keys=True))
