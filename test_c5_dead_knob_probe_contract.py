from __future__ import annotations

from c5_dead_knob_probe_contract import PROBE_SPECS, classify_pair
from tools.c5_matrix_contract import C5_MATRIX_CONTRACT_VERSION


EXPECTED = {
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


def _safe_receipt(
    fingerprint: str = "3:exact-actions-v2:a",
    *,
    evaluations=10,
    fires=1,
    effective=1,
):
    return {
        "contract_fingerprint": C5_MATRIX_CONTRACT_VERSION + ":abc",
        "structural_ok": True,
        "telemetry_complete": True,
        "safety": {"pass": True},
        "action_fingerprint": fingerprint,
        "trigger": {"evaluations": evaluations, "fires": fires},
        "effective_value": effective,
        "tim_in_band": True,
    }


def test_registry_is_exact_and_side_correct():
    assert set(PROBE_SPECS) == EXPECTED
    assert PROBE_SPECS["STRUCTURAL_RANGE_SHIFT_K_LOW"].sides == ("SHORT",)
    assert all(
        key.endswith("_SHORT")
        for key in PROBE_SPECS["STRUCTURAL_RANGE_SHIFT_K_LOW"].keys
    )
    assert all(spec.low != spec.high for spec in PROBE_SPECS.values())


def test_reconnected_accel_accepts_distinct_exact_action_proof():
    spec = PROBE_SPECS["DELTA_EXIT_ACCEL_THRESHOLD"]
    got = classify_pair(
        spec,
        _safe_receipt("a", effective=spec.low),
        _safe_receipt("b", effective=spec.high),
        key="ACN_SHORT",
    )
    assert got["classification"] == "PAIRWISE_DISTINCT_C5_ACTION_PROOF"
    assert got["causal_proof"]


def test_wrong_side_is_inapplicable_not_dead():
    spec = PROBE_SPECS["STRUCTURAL_RANGE_SHIFT_K_LOW"]
    got = classify_pair(spec, {}, {}, key="VT_LONG")
    assert got["classification"] == "WRONG_SIDE_INAPPLICABLE"


def test_invalid_c5_evidence_fails_closed():
    spec = PROBE_SPECS["DELTA_EXIT_MIN_TF_LOST"]
    bad = _safe_receipt()
    bad["contract_fingerprint"] = "tradier-matrix-exec-c4:old"
    got = classify_pair(spec, bad, _safe_receipt(), key="ACN_SHORT")
    assert got["classification"] == "INVALID_C5_EVIDENCE"


def test_same_effective_value_is_alias_or_control():
    spec = PROBE_SPECS["DELTA_EXIT_MIN_TF_LOST"]
    got = classify_pair(
        spec,
        _safe_receipt("a", effective=2),
        _safe_receipt("b", effective=2),
        key="ACN_SHORT",
    )
    assert got["classification"] == "ALIAS_OR_CONTROL"


def test_distinct_exact_action_is_causal_proof():
    spec = PROBE_SPECS["DELTA_EXIT_MIN_TF_LOST"]
    got = classify_pair(
        spec,
        _safe_receipt("a", effective=1),
        _safe_receipt("b", effective=3),
        key="ACN_SHORT",
    )
    assert got["classification"] == "PAIRWISE_DISTINCT_C5_ACTION_PROOF"
    assert got["causal_proof"]
    assert got["promotion_tim_pass"]


def test_zero_evaluations_is_insufficient_trigger_data():
    spec = PROBE_SPECS["STDEV_REJECT_EXIT_ZONE"]
    got = classify_pair(
        spec,
        _safe_receipt("a", evaluations=0, fires=0, effective=0.4),
        _safe_receipt("a", evaluations=0, fires=0, effective=1.2),
        key="VT_LONG",
    )
    assert got["classification"] == "INSUFFICIENT_TRIGGER_DATA"


def test_evaluated_without_fire_is_legitimate_semantic_plateau():
    spec = PROBE_SPECS["STDEV_REJECT_EXIT_RETURN"]
    got = classify_pair(
        spec,
        _safe_receipt("a", evaluations=8, fires=0, effective=0.325),
        _safe_receipt("a", evaluations=8, fires=0, effective=0.975),
        key="VT_LONG",
    )
    assert (
        got["classification"]
        == "LEGITIMATE_SEMANTIC_PLATEAU_NO_BOUNDARY_CROSS"
    )


def test_partial_size_plateau_is_separate_class():
    spec = PROBE_SPECS["LR_BAND_HARVEST_FRAC"]
    got = classify_pair(
        spec,
        _safe_receipt("a", evaluations=4, fires=2, effective=0.125),
        _safe_receipt("a", evaluations=4, fires=2, effective=0.375),
        key="VT_LONG",
    )
    assert got["classification"] == "LEGITIMATE_QUANTITY_ROUNDING_PLATEAU"
