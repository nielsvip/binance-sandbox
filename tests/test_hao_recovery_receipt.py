import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = (
    ROOT
    / "data/reports/vec_research/hao_recovery_20260727T0010Z"
)


def _read(name):
    return json.loads((RECOVERY / name).read_text())


def test_recovery_candidate_is_isolated_and_causal():
    summary = _read("recovery_summary.json")
    build = _read("build_validation_receipt.json")

    assert summary["status"] == "QUARANTINED_NOT_PROMOTED"
    assert summary["canonical_modified"] is False
    assert build["canonical_unchanged"] is True
    assert build["missing_required"] == []
    assert set(build["future_htf_rows"].values()) == {0}
    assert build["real_jun8_high"] == 2.04


def test_recovery_keeps_native_and_interpolated_provenance_separate():
    summary = _read("recovery_summary.json")
    synthetic = _read("build_validation_receipt.json")["synthetic_5m"]

    assert synthetic["native_count"] == summary["npz"]["native_5m_rows"]
    assert synthetic["count"] == summary["npz"]["interpolated_5m_rows"]
    assert synthetic["count"] > 0
    assert synthetic["parent_future_rows"] > 0
    assert "interpolation provenance" in synthetic["note"]


def test_failed_controls_cannot_be_promoted():
    summary = _read("recovery_summary.json")
    ladder = summary["vector_ladder_e02"]
    top_exit = summary["top_exit_quick"]

    assert ladder["strategy_bh_multiple"] > 10
    assert ladder["control_failure"] is True
    assert ladder["insolvent_folds"] > 0
    assert ladder["matrix_eligible"] is False
    assert top_exit["strategy_compounded_gain_pct"] == -100.0
    assert top_exit["matrix_eligible"] is False
