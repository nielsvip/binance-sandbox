import numpy as np
import pytest
import json
from types import SimpleNamespace

from tools import v18_linknet as linknet


def test_calibration_ignores_test_labels_and_splits_row_cell_heads():
    validation_rows = np.arange(120)
    test_rows = np.arange(120, 180)
    kind = np.tile([0, 1], 90)
    labels = np.tile([0, 0, 1, 1], 45).astype(float)
    dataset = {"kind": kind, "y": labels.copy()}
    scores = {"network": np.linspace(0.05, 0.95, 120), "hybrid": np.linspace(0.1, 0.8, 120)}
    before = linknet.validation_platt(scores, dataset, validation_rows, test_rows)
    dataset["y"][test_rows] = 1 - dataset["y"][test_rows]
    after = linknet.validation_platt(scores, dataset, validation_rows, test_rows)
    assert before == after
    assert before["network"]["R"] == linknet.fit_platt(scores["network"][kind[validation_rows] == 0], labels[validation_rows][kind[validation_rows] == 0])
    dataset["y"][validation_rows] = 1 - dataset["y"][validation_rows]
    assert linknet.validation_platt(scores, dataset, validation_rows, test_rows) != before


def test_calibration_rejects_test_overlap():
    with pytest.raises(ValueError, match="overlap"):
        linknet.validation_platt({"network": np.array([0.1, 0.9])}, {"kind": np.array([0, 1]), "y": np.array([0, 1])}, np.array([0, 1]), np.array([1]))


def test_sparse_validation_uses_identity_calibration():
    result = linknet.validation_platt({"network": np.array([0.1, 0.9])}, {"kind": np.array([0, 1]), "y": np.array([0, 1])}, np.array([0, 1]), np.array([2]))
    assert result == {"network": {"R": (1.0, 0.0), "F": (1.0, 0.0)}}


@pytest.mark.parametrize("report", [{}, {"platt_on_test": {"v1": {"R": [1, 0]}}}, {"platt_on_validation": {"v1": {"R": [1, 0]}}}])
def test_legacy_calibration_requires_matched_retraining(report):
    with pytest.raises(ValueError, match="retrain matched"):
        linknet.require_validation_platt(report)


def test_validation_report_preserves_calibration_and_provenance():
    report = {"platt_on_validation": {"v1": {"R": [1, 0]}}, "calibration_source": "chronological validation"}
    assert linknet.require_validation_platt(report) == report["platt_on_validation"]


def test_redecide_refuses_legacy_before_training(tmp_path):
    (tmp_path / "report_v2_CRYPTO_LONG.json").write_text(json.dumps({"platt_on_test": {"v1": {"R": [1, 0]}}}))
    with pytest.raises(ValueError, match="retrain matched"):
        linknet.cmd_redecide(SimpleNamespace(cat_side=["CRYPTO_LONG"], ds_dir=tmp_path))


def test_scorer_refuses_legacy_before_loading_models(tmp_path):
    (tmp_path / "report_v2_CRYPTO_LONG.json").write_text(json.dumps({"platt_on_test": {"v1": {"R": [1, 0]}}}))
    (tmp_path / "production_CRYPTO_LONG.json").write_text(json.dumps({"cat_side": "CRYPTO_LONG", "production_scorer": "v1", "platt": {"R": [1, 0]}}))
    with pytest.raises(ValueError, match="retrain matched"):
        linknet.Scorer2("CRYPTO_LONG", ds_dir=tmp_path)


def test_production_choice_is_independent_of_test_metrics_and_labels():
    validation = {"F": {name: {"auc_v2": 0.8, "auc_v1": 0.7} for _, name in linknet.GATE_SUBSETS}}
    choices = []
    for test in ({}, {"F": {"never_tried_pair": {"auc_v2": 0.0, "auc_v1": 1.0}}}, {"labels": [1, 0, 0], "F": {"never_tried_context": {"auc_v2": 1.0, "auc_v1": 0.0}}}):
        report = {"selection": {"production_scorer": "v2"}, "validation": validation, "test": test}
        choices.append(linknet.v1_gate(report))
        assert report["selection"]["v1_gate"]["test_used_for_selection"] is False
    assert choices == ["v2"] * 3


def test_missing_validation_cannot_be_rescued_by_good_test_metrics():
    report = {"selection": {"production_scorer": "v2"}, "test": {"F": {name: {"auc_v2": 1.0, "auc_v1": 0.0} for _, name in linknet.GATE_SUBSETS}}}
    assert linknet.v1_gate(report) == "v1"
