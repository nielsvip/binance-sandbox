"""SRS range-edge permission core (USER 2026-10-24 unlock: wire missing producers).

Single source of truth for the B_SRS_ENTRY family, an exact mirror of the
vector predicate (v12_quick_engine.py:11033-11049):
  srs_block = pctb>=0.97 (LONG) / pctb<=0.03 (SHORT) on the TF leg
  blocks["B_SRS_ENTRY"] = NOT srs_block
This is an entry PERMISSION, not a trigger (~90% of bars pass): the live use
is a VETO that refuses fresh OPEN claims at the range extreme, placed in the
process_position _veto chain beside DELTA_GATE/ENTRY_SCORE/K_ZONE. Wiring it
as an opener would flood entries the vector never takes.
TF map mirrors the vector tf_map_srs; TF leg default bb_1h. Gate switch:
STRUCTURAL_RANGE_SHIFT_EXIT (caller-owned). Missing pctb key = fail-open
(permission granted, today's behaviour).
"""
from __future__ import annotations

TF_MAP_SRS = {
    "bb_1h": "bb_pct_b_1h",
    "bb_4h": "bb_pct_b_4h",
    "bb_D": "bb_pct_b_D",
    "dc_1h": "bb_pct_b_1h",
    "dc_4h": "bb_pct_b_4h",
    "dc_D": "bb_pct_b_D",
}


def pctb_key_for_tf(tf_raw: object, default: str = "bb_1h") -> str:
    try:
        return TF_MAP_SRS.get(str(tf_raw or default), TF_MAP_SRS[default])
    except Exception:
        return "bb_pct_b_1h"


def srs_blocked_scalar(pctb, is_long) -> bool:
    try:
        v = float(pctb)
    except (TypeError, ValueError):
        return False
    if bool(is_long):
        return bool(v >= 0.97)
    return bool(v <= 0.03)


def srs_blocked_mask_vec(pctb, is_long):
    import numpy as np

    v = np.asarray(pctb, dtype=float)
    if bool(is_long):
        return v >= 0.97
    return v <= 0.03
