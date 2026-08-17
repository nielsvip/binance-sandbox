"""C5-only causal classification contract for repaired-matrix dead-knob probes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tools.c5_matrix_contract import C5_MATRIX_CONTRACT_VERSION


@dataclass(frozen=True)
class ProbeSpec:
    param: str
    low: Any
    high: Any
    activation_dependencies: tuple[str, ...]
    keys: tuple[str, ...]
    sides: tuple[str, ...] = ("LONG", "SHORT")
    path_prefix: str = ""
    trace_kind: str = "ACTION_ONLY"
    source_disposition: str = "CONNECTED"
    source_note: str = ""
    quantized_partial: bool = False

    @property
    def primary_key(self) -> str:
        return self.keys[0]


PROBE_SPECS = {
    "DELTA_EXIT_ACCEL_THRESHOLD": ProbeSpec(
        param="DELTA_EXIT_ACCEL_THRESHOLD",
        low=-0.15,
        high=-0.05,
        activation_dependencies=("DELTA_EXIT_ENABLED",),
        keys=("ACN_SHORT", "VT_LONG"),
        path_prefix="DELTA_EXIT_",
        trace_kind="DELTA_ACTION",
        source_disposition="CONNECTED_AFTER_C5_ACCEL_REPAIR",
        source_note=(
            "C5 repair reconnects the authoritative direction-specific "
            "z-speed acceleration contract: current causal rolling z-speed "
            "minus its configured lookback value gates decay, opposing "
            "pressure, and TF-loss symmetrically for LONG and SHORT."
        ),
    ),
    "DELTA_EXIT_DECAY_RATIO": ProbeSpec(
        param="DELTA_EXIT_DECAY_RATIO",
        low=0.15,
        high=0.45,
        activation_dependencies=("DELTA_EXIT_ENABLED",),
        keys=("ACN_SHORT", "VT_LONG"),
        path_prefix="DELTA_EXIT_",
        trace_kind="DELTA_ACTION",
        source_disposition="CONNECTED_AFTER_C5_ALIAS_REPAIR",
        source_note=(
            "C5 repair normalizes Tradier's fractional "
            "exit_speed_decay_ratio into the percent field consumed by the "
            "shared live/exact DeltaTracker. Exact pair proof is still required."
        ),
    ),
    "DELTA_EXIT_MIN_HOLD": ProbeSpec(
        param="DELTA_EXIT_MIN_HOLD",
        low=2,
        high=6,
        activation_dependencies=("DELTA_EXIT_ENABLED",),
        keys=("ACN_SHORT", "VT_LONG"),
        path_prefix="DELTA_EXIT_",
        trace_kind="DELTA_ACTION",
        source_disposition="CONNECTED_AFTER_C5_HOLD_REPAIR",
        source_note=(
            "Tradier now passes reliable position age as authoritative 15m "
            "bars and DeltaTracker applies exit_min_hold to acceleration-gated "
            "exit predicates. The current 4,320-minute stock-wide upstream "
            "floor dominates the 2–6 bar grid, so equality can still be a "
            "legitimate gated plateau rather than a disconnect."
        ),
    ),
    "DELTA_EXIT_MIN_TF_LOST": ProbeSpec(
        param="DELTA_EXIT_MIN_TF_LOST",
        low=1,
        high=3,
        activation_dependencies=("DELTA_EXIT_ENABLED",),
        keys=("ACN_SHORT", "VT_LONG"),
        path_prefix="DELTA_EXIT_",
        trace_kind="DELTA_ACTION",
        source_note=(
            "Consumed by live DeltaTracker for both LONG and SHORT exits; c4 "
            "tested only a one-trade window, so equality is not a disconnect."
        ),
    ),
    "DELTA_EXIT_OPPOSING_RATIO": ProbeSpec(
        param="DELTA_EXIT_OPPOSING_RATIO",
        low=0.75,
        high=2.25,
        activation_dependencies=("DELTA_EXIT_ENABLED",),
        keys=("ACN_SHORT", "VT_LONG"),
        path_prefix="DELTA_EXIT_",
        trace_kind="DELTA_ACTION",
        source_disposition="CONNECTED_AFTER_C5_RATIO_REPAIR",
        source_note=(
            "C5 repair applies exit_opposing_ratio symmetrically as "
            "opp_speed > current_speed * ratio for LONG and SHORT."
        ),
    ),
    "GR_HTF_DIRECT_EXIT_SCORE": ProbeSpec(
        param="GR_HTF_DIRECT_EXIT_SCORE",
        low=6.0,
        high=18.0,
        activation_dependencies=("GR_HTF_DIRECT_EXIT_ENABLED",),
        keys=("VT_LONG", "MU_LONG", "NVDA_LONG"),
        path_prefix="GR_HTF_DIRECT_EXIT",
        trace_kind="GR_HTF_EXIT",
        source_note=(
            "Connected to the opposite-direction GR score; discrete score "
            "support may legitimately leave two thresholds on one plateau."
        ),
    ),
    "LR_BAND_HARVEST_FRAC": ProbeSpec(
        param="LR_BAND_HARVEST_FRAC",
        low=0.125,
        high=0.375,
        activation_dependencies=("LR_BAND_HARVEST_ENABLED",),
        keys=("VT_LONG", "MU_LONG"),
        path_prefix="LR_BAND_HARVEST",
        trace_kind="PARTIAL_ACTION",
        source_note=(
            "Connected to partial close quantity. C4 fingerprints omitted "
            "partial quantity/cash flow, and share rounding can create genuine "
            "small-position plateaus; c5 action identity is mandatory."
        ),
        quantized_partial=True,
    ),
    "MTF_BB_REJECT_EXIT_ENABLED": ProbeSpec(
        param="MTF_BB_REJECT_EXIT_ENABLED",
        low=False,
        high=True,
        activation_dependencies=(),
        keys=("TTD_SHORT", "ACN_SHORT"),
        path_prefix="MTF_BB_REJECT",
        trace_kind="ACTION_ONLY",
        source_note=(
            "Connected inside the compound exit evaluator. It fires only after "
            "a BB tag and subsequent failure inside the configured lookback."
        ),
    ),
    "STDEV_REJECT_EXIT_RETURN": ProbeSpec(
        param="STDEV_REJECT_EXIT_RETURN",
        low=0.325,
        high=0.975,
        activation_dependencies=("STDEV_REJECT_EXIT_ENABLED",),
        keys=("VT_LONG", "TTD_SHORT", "ACN_SHORT", "LAC_SHORT"),
        path_prefix="STDEV_REJECT_EXIT",
        trace_kind="STDEV",
        source_note=(
            "Connected and c5-instrumented. Exact trace must show evaluations "
            "and whether either return threshold entered the trigger domain."
        ),
    ),
    "STDEV_REJECT_EXIT_ZONE": ProbeSpec(
        param="STDEV_REJECT_EXIT_ZONE",
        low=0.4,
        high=1.2,
        activation_dependencies=("STDEV_REJECT_EXIT_ENABLED",),
        keys=("VT_LONG", "TTD_SHORT", "ACN_SHORT", "LAC_SHORT"),
        path_prefix="STDEV_REJECT_EXIT",
        trace_kind="STDEV",
        source_note=(
            "Connected and c5-instrumented. Values outside observed pctB "
            "support are legitimate no-trigger plateaus, not dead wiring."
        ),
    ),
    "STRUCTURAL_RANGE_SHIFT_K_LOW": ProbeSpec(
        param="STRUCTURAL_RANGE_SHIFT_K_LOW",
        low=15.0,
        high=25.0,
        activation_dependencies=("STRUCTURAL_RANGE_SHIFT_EXIT",),
        keys=("TTD_SHORT", "ACN_SHORT"),
        sides=("SHORT",),
        path_prefix="STRUCTURAL_RANGE_SHIFT_SHORT",
        trace_kind="ACTION_ONLY",
        source_disposition="WRONG_SIDE_C4_EVIDENCE",
        source_note=(
            "K_LOW is consumed only by the SHORT range-bottom branch. The c4 "
            "collision evidence came from NVDA_LONG/VT_LONG and is therefore "
            "wrong-side/inapplicable, not a dead-knob result."
        ),
    ),
}


def validate_specs() -> None:
    expected = {
        "DELTA_EXIT_ACCEL_THRESHOLD",
        "DELTA_EXIT_DECAY_RATIO",
        "DELTA_EXIT_MIN_HOLD",
        "DELTA_EXIT_MIN_TF_LOST",
        "DELTA_EXIT_OPPOSING_RATIO",
        "GR_HTF_DIRECT_EXIT_SCORE",
        "LR_BAND_HARVEST_FRAC",
        "MTF_BB_REJECT_EXIT_ENABLED",
        "STDEV_REJECT_EXIT_RETURN",
        "STDEV_REJECT_EXIT_ZONE",
        "STRUCTURAL_RANGE_SHIFT_K_LOW",
    }
    if set(PROBE_SPECS) != expected:
        raise RuntimeError("c5 dead-knob probe registry drift")
    for name, spec in PROBE_SPECS.items():
        if name != spec.param or spec.low == spec.high or not spec.keys:
            raise RuntimeError(f"invalid c5 probe spec: {name}")
        for key in spec.keys:
            side = key.rsplit("_", 1)[-1]
            if side not in spec.sides:
                raise RuntimeError(f"wrong-side preregistration: {name} {key}")


def _receipt_safe(receipt: Mapping[str, Any]) -> bool:
    fingerprint = str(receipt.get("contract_fingerprint") or "")
    return bool(
        fingerprint.startswith(C5_MATRIX_CONTRACT_VERSION + ":")
        and receipt.get("structural_ok") is True
        and receipt.get("telemetry_complete") is True
        and (receipt.get("safety") or {}).get("pass") is True
    )


def classify_pair(
    spec: ProbeSpec,
    low_receipt: Mapping[str, Any],
    high_receipt: Mapping[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    """Classify one exact pair without conflating causality and promotion."""
    side = str(key).upper().rsplit("_", 1)[-1]
    if side not in spec.sides:
        classification = "WRONG_SIDE_INAPPLICABLE"
    elif spec.source_disposition in {
        "ORPHAN_LEGACY_UNCONSUMED",
        "LIVE_ALIAS_MISMATCH",
        "STOCK_REDUNDANT_ORPHAN",
    }:
        classification = "TRULY_INERT_OR_DISCONNECTED_SOURCE"
    elif not (_receipt_safe(low_receipt) and _receipt_safe(high_receipt)):
        classification = "INVALID_C5_EVIDENCE"
    elif low_receipt.get("effective_value") == high_receipt.get(
        "effective_value"
    ):
        classification = "ALIAS_OR_CONTROL"
    elif low_receipt.get("action_fingerprint") != high_receipt.get(
        "action_fingerprint"
    ):
        classification = "PAIRWISE_DISTINCT_C5_ACTION_PROOF"
    else:
        low_trigger = low_receipt.get("trigger") or {}
        high_trigger = high_receipt.get("trigger") or {}
        evaluations = [
            value
            for value in (
                low_trigger.get("evaluations"),
                high_trigger.get("evaluations"),
            )
            if value is not None
        ]
        fires = int(low_trigger.get("fires") or 0) + int(
            high_trigger.get("fires") or 0
        )
        if not evaluations or max(evaluations) == 0:
            classification = "INSUFFICIENT_TRIGGER_DATA"
        elif fires == 0:
            classification = "LEGITIMATE_SEMANTIC_PLATEAU_NO_BOUNDARY_CROSS"
        elif spec.quantized_partial:
            classification = "LEGITIMATE_QUANTITY_ROUNDING_PLATEAU"
        else:
            classification = "LEGITIMATE_SEMANTIC_PLATEAU"
    return {
        "param": spec.param,
        "key": key,
        "classification": classification,
        "causal_proof": classification == "PAIRWISE_DISTINCT_C5_ACTION_PROOF",
        "promotion_tim_pass": bool(
            low_receipt.get("tim_in_band") and high_receipt.get("tim_in_band")
        ),
        "source_disposition": spec.source_disposition,
    }


validate_specs()
