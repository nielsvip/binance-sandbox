#!/usr/bin/env python3
"""Deterministic wiring audit for DC_TIER_AUG_ENABLED and its active block."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def audit_sources(config_source: str, manage_source: str) -> dict:
    declared = "DC_TIER_AUG_ENABLED:" in config_source
    read = "getattr(config, 'DC_TIER_AUG_ENABLED'" in manage_source
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
        "evaluate_augment_present": function,
        "evaluate_augment_routed": routed,
        "tier_reason_present": tier_reason,
        "hardcoded_source_settings_present": hardcoded,
        "classification": (
            "PHANTOM_SWITCH_ACTIVE_UNCONDITIONAL_FUNCTION"
            if not declared and not read and function and routed and tier_reason and hardcoded
            else "REQUIRES_FRESH_MANUAL_AUDIT"
        ),
    }


def audit_repo(root: Path = ROOT) -> dict:
    return audit_sources(
        (root / "config_tradier.py").read_text(),
        (root / "tradier_manage.py").read_text(),
    )


if __name__ == "__main__":
    print(json.dumps(audit_repo(), indent=2, sort_keys=True))
