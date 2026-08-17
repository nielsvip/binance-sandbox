"""Typed, source-backed overrides for Tradier parameter sweep grids.

Historical sweep metadata is useful evidence, but it is not authoritative for
the active type, unit, or control value.  These exceptional grids are defined
from the current ``TradierConfig`` value and the live/exact consumer semantics.
They intentionally contain no wall-clock values.

The normal manifest builder still validates every value through
``sweep_value_semantics``.  This module only supplies the small set of grids
whose historical proposal became stale or was the wrong type.
"""
from __future__ import annotations

from typing import Any


# Evidence is kept beside the values so a future config change cannot turn a
# repair into an unexplained magic number.
TRADIER_GRID_CONTRACTS: dict[str, dict[str, Any]] = {
    "EXIT_SCORER_MIN_CONDITIONS": {
        "values": [3, 4, 5],
        "evidence": (
            "wt_dc_exit_scorer.score_exit is an N-of-5 gate and documents "
            "3=loose through 5=all; config_tradier declares the same sweep."
        ),
    },
    "GOLDEN_RULE_HTF_MIN_TFS": {
        "values": list(range(0, 7)),
        "evidence": (
            "golden_rule_htf has exactly six Tradier TFs; _run_gate defines "
            "min_tfs<=0 as GATE_OFF."
        ),
    },
    "GOLDEN_RULE_MIN_IND": {
        "values": list(range(1, 12)),
        "evidence": (
            "golden_rule_htf._ind_score implements eleven indicator votes per "
            "TF, so the exact integer domain is 1..11."
        ),
    },
    "NOLOSS_MIN_PROFIT_PCT_TRADIER": {
        "values": [0.0, 0.01, 1.0, 3.0],
        "evidence": (
            "tradier_manage compares this value directly with gain in percent; "
            "0 disables the gate, active config is 0.01, and live read fallbacks "
            "are 1.0/3.0 percent."
        ),
    },
    "REENTRY_RALLY_HTF_MIN": {
        "values": [0, 1, 2, 3],
        "evidence": (
            "tradier_manage counts favorable 1h/4h/D votes (zero through three) "
            "and compares the integer count to this value."
        ),
    },
    "REENTRY_RALLY_K15M_MAX": {
        "values": [20.0, 40.0, 100.0],
        "evidence": (
            "config_tradier documents 100=disabled, 40=moderate and 20=strict; "
            "tradier_manage consumes it on the 0..100 stochastic scale."
        ),
    },
    "RZ_BOT_BB_THRESHOLD": {
        "values": [0.15, 0.375],
        "evidence": (
            "tradier_manage consumes normalized bb_pct_b; 0.15 is its explicit "
            "fallback and 0.375 is the active TradierConfig control."
        ),
    },
    "SCALP_MIN_MOVE_PCT": {
        "values": [0.0, 0.003],
        "evidence": (
            "tradier_manage compares abs(price-ema)/ema directly to this value; "
            "0 removes the move gate and active config 0.003 means 0.3%. "
            "Legacy 0.15..0.45 values were the documented percent/fraction bug."
        ),
    },
    "STRUCTURAL_RANGE_SHIFT_K_HIGH": {
        "values": [75.0, 80.0, 85.0],
        "evidence": (
            "exact-engine fallback is 75, live fallback is 80, and active "
            "TradierConfig is 85 on the 0..100 stochastic scale."
        ),
    },
    "STRUCTURAL_RANGE_SHIFT_K_LOW": {
        "values": [15.0, 20.0, 25.0],
        "evidence": (
            "active TradierConfig is 15, live fallback is 20, and exact-engine "
            "fallback is 25 on the 0..100 stochastic scale."
        ),
    },
    "TRADIER_MIN_HOLD_MINUTES": {
        "values": [0.0, 240.0, 4320.0],
        "evidence": (
            "zero disables the hold fence in simulation setup, live/exact reads "
            "fall back to 240 minutes, and active TradierConfig is 4320 minutes."
        ),
    },
    "TRC_NOLOSS_MIN_PROFIT_PCT": {
        "values": [0.0, 0.01],
        "evidence": (
            "tradier_manage maps this TRC value to "
            "NOLOSS_MIN_PROFIT_PCT_TRADIER; 0 disables that percent gate and "
            "0.01 is the active destination control."
        ),
    },
    "WT_3M_FORCE_OPEN_TARGET_USD": {
        "values": [2000.0, 4000.0, 16000.0],
        "evidence": (
            "active target is $2k, live force-open per-fire cap defaults to $4k, "
            "and the exact Tradier harness enforces a $16k strategy capacity."
        ),
    },
    "WT_DC_ENTRY_THRESHOLD": {
        "values": [0.0, 35.0, 43.0, 45.0, 55.0, 75.0],
        "evidence": (
            "zero disables the score threshold in the consumer, 45 is active "
            "TradierConfig, and 35/43/55/75 are observed typed historical "
            "controls retained by the baseline spec."
        ),
    },
}


def grid_contract(name: str) -> dict[str, Any] | None:
    """Return a copy so callers cannot mutate the source contract."""
    row = TRADIER_GRID_CONTRACTS.get(name)
    if row is None:
        return None
    return {"values": list(row["values"]), "evidence": str(row["evidence"])}

