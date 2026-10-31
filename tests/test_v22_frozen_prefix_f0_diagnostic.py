import importlib.util
import json
from pathlib import Path

import pytest

from tools import v18_brain as brain


spec = importlib.util.spec_from_file_location("frozen_prefix_test_helper", Path(__file__).parents[1] / "data/reports/v22_director_20261031/frozen_prefix_f0_diagnostic.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def row(timestamp, delta=0.4):
    return {"e": "row", "s": "BTCUSDC_LONG", "b": "BTCUSDC_LONG|" + timestamp, "sh": "ENTRY_REVERSAL_BOUNCE", "k": "BB_BOUNCE_ENTRY_TF=4h", "market_end": brain.epoch_of(timestamp), "t": timestamp, "source_record_identity": "a" * 64, "n": [-0.1, 1, 0.0, None, None, None, None, None, 1.0, 1], "y": [["FILTER=True", delta, 1, 0.0, None, None, None, None, None, 1.0, delta - 0.1]]}


def test_evaluation_labels_cannot_change_frozen_weights_tables_or_predictions(tmp_path):
    results = []
    for name, delta in (("positive", 0.4), ("negative", -0.4)):
        work = tmp_path / name
        work.mkdir()
        report = {}
        cls = diagnostic.diagnostic_class(brain, work, report, {})
        model = cls("CRYPTO_LONG", brain.Buckets(), diagnostic.CUT)
        model.handle(row("2026-10-09T00:00:00Z"), "2026-10-09T00:00:00Z")
        model.handle(row("2026-10-10T00:00:00Z", delta), "2026-10-10T00:00:00Z")
        model.finish()
        result = report["CRYPTO_LONG"]
        assert result["state_unchanged"]
        assert result["prefix_F0_updates"] == 1
        assert result["runtime_F0"]["positive"] == int(delta > 0)
        results.append(result)
    assert results[0]["runtime_F0"]["mean_probability"] == results[1]["runtime_F0"]["mean_probability"]
    assert results[0]["frozen_state_before_sha256"] == results[1]["frozen_state_before_sha256"]
    assert results[0]["identity_label_sha256"] != results[1]["identity_label_sha256"]


def test_future_market_end_excluded_even_when_event_timestamp_is_before_cut(tmp_path):
    population = {}
    cls = diagnostic.diagnostic_class(brain, tmp_path, population, {})
    model = cls("CRYPTO_LONG", brain.Buckets(), diagnostic.CUT)
    event = row("2026-10-10T00:00:00Z")
    model.handle(event, "2026-10-09T00:00:00Z")
    assert model.h.n["F0"] == 0
    assert model.diag["training_event_excluded_unknown_or_postcut_market"] == 1
    assert not model.g.nodes


def test_brier_logloss_bins_and_retention_have_known_values():
    metric = diagnostic.Metrics()
    metric.add(0.25, True, 0.4, 0.5)
    metric.add(0.75, False, -0.4, 1.0)
    result = metric.report()
    assert result["brier"] == pytest.approx(0.5625)
    assert result["logloss"] == pytest.approx(1.3862943611198906)
    assert result["expected_evaluation_retention"] == 0.75
    assert result["expected_positive_recall"] == 0.5
    assert result["expected_positive_delta_retention"] == 0.5
    assert sum(bucket["n"] for bucket in result["reliability_bins"]) == 2
