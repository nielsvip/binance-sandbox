#!/usr/bin/env python3
"""Final-blind boundary confirmation for HAO_SHORT exposure phase 4.

Phase-4 V1 left FINAL sealed and found its coherent 7x stage 0.45 percentage
points below the D2 TIM floor. This separately preregistered cohort brackets
that discovery-only boundary. It imports the audited V1 executor; it does not
change entry, exit, clock, fill, reclaim, benchmark, capacity, or gate logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_hao_short_exposure_phase4 as campaign


CONTRACT = "HAO_SHORT_EXPOSURE_PHASE4_V2_BOUNDARY_CONFIRM"
BOUNDARY_TARGETS = (7.125, 7.25, 7.375, 7.5)


def confirmation_policies() -> tuple[campaign.ExposurePolicy, ...]:
    source = campaign.EXPOSURE_POLICIES[0]
    rows = [source]
    for target in BOUNDARY_TARGETS:
        suffix = str(target).replace(".", "_")
        rows.append(
            campaign.ExposurePolicy(
                f"C_STAGE{suffix}_REST_ADD1",
                ladder_scale=0.875,
                ladder_cap_mult=8.0,
                initial_seed_mult=4.0,
                impulse_stage_target_mult=target,
                resting_reclaim=True,
                impulse_add_mult=1.0,
                max_impulse_adds_per_position=1,
            )
        )
    return tuple(rows)


CONFIRMATION_POLICIES = confirmation_policies()


def configure() -> None:
    campaign.CONTRACT = CONTRACT
    campaign.EXPOSURE_POLICIES = CONFIRMATION_POLICIES


def main() -> int:
    configure()
    return campaign.main()


if __name__ == "__main__":
    raise SystemExit(main())
