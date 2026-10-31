import numpy as np
import pytest

from tools.v18_linknet_net_validation import _fractional_selection, analyze_arrays


def fixture_data():
    validation = {
        "validation_indices": np.arange(7, dtype=np.int64),
        "source_i": np.array([0, 0, 0, 1, 1, 2, 2], np.int32),
        "kind": np.array([0, 1, 1, 0, 1, 0, 1], np.int8),
        "labels": np.array([0, 1, 0, 1, 0, 0, 1], np.int8),
        "actual_NET_delta": np.array([-0.1, 0.2, -0.5, 0.2, -0.3, -0.1, 0.4]),
        "raw_NetV1_probability": np.array([0.2, 0.8, 0.3, 0.9, 0.4, 0.2, 0.6]),
        "fit_only_counts_probability": np.array([0.5, 0.2, 0.9, 0.7, 0.4, 0.5, 0.1]),
    }
    dataset = {
        "source_i": validation["source_i"].copy(),
        "kind": validation["kind"].copy(),
        "y": validation["labels"].copy(),
        "d": validation["actual_NET_delta"].copy(),
    }
    sources = {i: {"source_record_identity": f"{i:064x}"} for i in range(3)}
    calibration = {
        name: {scorer: [1.0, 0.0] for scorer in ("NetV1", "fit_only_counts")}
        for name in ("R_naked", "F_NET")
    }
    return validation, dataset, calibration, sources


def test_fractional_budget_shares_boundary_ties_and_hits_exact_quota():
    weights = _fractional_selection(np.array([0.9, 0.8, 0.8, 0.1]), 0.5)
    assert weights.sum() == pytest.approx(2.0)
    assert weights.tolist() == pytest.approx([1.0, 0.5, 0.5, 0.0])


def test_net_cell_and_source_row_reports_use_exact_source_i_and_unknown_negatives():
    validation, dataset, calibration, sources = fixture_data()
    report = analyze_arrays(validation, dataset, calibration, sources)
    assert report["validation_F_NET_examples"] == 4
    assert report["validation_F_NET_positive"] == 2
    assert report["validation_F_NET_positive_mass"] == pytest.approx(0.6)
    assert report["known_positive_source_rows"] == 3
    assert report["known_rescue_source_rows"] == 2
    net = report["models"]["NetV1"]
    assert net["per_cell_F_NET_discrimination"]["auc"] == pytest.approx(1.0)
    counts = report["models"]["fit_only_counts"]
    assert counts["per_cell_F_NET_discrimination"]["auc"] == pytest.approx(0.0)
    assert counts["per_cell_F_NET_budget_retention"]["top_20pct"]["observed_positive_count_retained"] == pytest.approx(0.0)
    top20 = net["per_cell_F_NET_budget_retention"]["top_20pct"]
    assert top20["selected_equivalent_n"] == pytest.approx(0.8)
    assert top20["observed_positive_count_retained"] == pytest.approx(0.8)
    assert top20["measured_positive_NET_label_mass_retained"] == pytest.approx(0.16)
    row20 = net["observed_candidate_row_budget_retention"]["top_20pct"]
    assert row20["represented_source_rows"] == 3
    assert row20["observed_positive_rows_total"] == 3
    assert row20["observed_rescue_rows_total"] == 2
    assert "not calibrated P(any candidate wins)" in report["row_score_semantics"]


def test_validation_arrays_must_match_exact_dataset_source_i():
    validation, dataset, calibration, sources = fixture_data()
    validation["source_i"] = validation["source_i"].copy()
    validation["source_i"][0] = 2
    with pytest.raises(ValueError, match="source_i does not match"):
        analyze_arrays(validation, dataset, calibration, sources)


def test_missing_source_sidecar_entry_is_rejected():
    validation, dataset, calibration, sources = fixture_data()
    del sources[2]
    with pytest.raises(ValueError, match="source_i does not resolve"):
        analyze_arrays(validation, dataset, calibration, sources)
