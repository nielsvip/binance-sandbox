#!/usr/bin/env python3
"""Deterministic wiring audit for the stale DC_BREAK_ENTRY_ENABLED registry row."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def audit_sources(config_source: str, manage_source: str) -> dict[str, Any]:
    named_switch_declared = "DC_BREAK_ENTRY_ENABLED:" in config_source
    named_switch_read = "getattr(config, 'DC_BREAK_ENTRY_ENABLED'" in manage_source
    disabled_branch_read = (
        "getattr(config, 'DC_BREAK_ENTRY_DISABLED', True)" in manage_source
    )
    daytrade_switch_declared = "DC_DAYTRADE_ENABLED:" in config_source
    daytrade_switch_read = (
        "getattr(config, 'DC_DAYTRADE_ENABLED', True)" in manage_source
    )
    daytrade_launched = (
        "self.daytrade_wing = StockDaytradeWing(self)" in manage_source
        and "self.daytrade_wing.run_loop()" in manage_source
    )
    disconnected = (
        not named_switch_declared
        and not named_switch_read
        and disabled_branch_read
    )
    return {
        "registry_switch": "DC_BREAK_ENTRY_ENABLED",
        "registry_switch_declared": named_switch_declared,
        "registry_switch_read": named_switch_read,
        "disabled_swing_branch_fail_closed": disabled_branch_read,
        "separate_daytrade_switch_declared": daytrade_switch_declared,
        "separate_daytrade_switch_read": daytrade_switch_read,
        "separate_daytrade_loop_launched": daytrade_launched,
        "classification": (
            "DISCONNECTED_STALE_REGISTRY_ROW"
            if disconnected
            else "REQUIRES_FRESH_MANUAL_AUDIT"
        ),
        "research_reconstruction_only": disconnected,
    }


def audit_repo(root: Path = ROOT) -> dict[str, Any]:
    return audit_sources(
        (root / "config_tradier.py").read_text(),
        (root / "tradier_manage.py").read_text(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    payload = audit_repo(args.root.resolve())
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0 if payload["classification"] == "DISCONNECTED_STALE_REGISTRY_ROW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
