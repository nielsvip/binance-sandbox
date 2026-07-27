#!/usr/bin/env python3
"""Final-blind confirmation after correcting finite-fold reclaim semantics.

V2 showed that C_STAGE7_25/7_375/7_5 satisfied every numerical discovery gate
but were rejected because a valid resting reclaim order existed beyond the
fold boundary even though its stored price had not been reached. V3 freezes
those three already registered rows. An untouched resting obligation is valid;
a due/touched unfilled obligation still fails closed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_hao_short_exposure_phase4 as campaign
from tools import run_hao_short_exposure_phase4_confirm as boundary


CONTRACT = "HAO_SHORT_EXPOSURE_PHASE4_V3_RECLAIM_CONFIRM"
TARGETS = (7.25, 7.375, 7.5)


def policies() -> tuple[campaign.ExposurePolicy, ...]:
    rows = [boundary.CONFIRMATION_POLICIES[0]]
    rows.extend(
        x
        for x in boundary.CONFIRMATION_POLICIES[1:]
        if x.impulse_stage_target_mult in TARGETS
    )
    return tuple(rows)


POLICIES = policies()


def configure() -> None:
    campaign.CONTRACT = CONTRACT
    campaign.EXPOSURE_POLICIES = POLICIES


def main() -> int:
    configure()
    return campaign.main()


if __name__ == "__main__":
    raise SystemExit(main())
