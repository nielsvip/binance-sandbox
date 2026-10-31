import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import v18_linknet as linknet


def objective(probability, labels, parameters):
    probability = np.clip(probability, 1e-7, 1 - 1e-7)
    logits = parameters[0] * np.log(probability / (1 - probability)) + parameters[1]
    return float((np.logaddexp(0.0, logits) - labels * logits).mean())


def test_exact_stocks_short_validation_prevents_divergent_saved_calibration():
    path = Path(__file__).parent / "fixtures/v18_linknet_stocks_short_validation_counts.npz"
    metadata = json.loads(path.with_suffix(".json").read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["fixture_sha256"]
    with np.load(path, allow_pickle=False) as fixture:
        probability, labels = fixture["p"], fixture["y"]
    assert len(labels) == 3647 and labels.sum() == 104
    parameters = linknet.fit_platt(probability, labels)
    assert np.isfinite(parameters).all()
    assert objective(probability, labels, parameters) < objective(probability, labels, (1.0, 0.0)) - 0.01
    assert objective(probability, labels, parameters) < objective(probability, labels, metadata["original_saved_Platt"])
    calibrated = linknet.apply_platt(probability, parameters)
    assert 0 < calibrated.min() < calibrated.max() < 1


@pytest.mark.parametrize("probability", [np.full(120, 0.0), np.full(120, 1.0), np.full(120, 0.2), np.r_[np.full(60, 1e-300), np.full(60, 1 - 1e-15)]])
def test_extreme_and_constant_logits_have_finite_nonworsening_fit(probability):
    labels = np.tile([0.0, 0.0, 1.0], 40)
    parameters = linknet.fit_platt(probability, labels)
    assert np.isfinite(parameters).all()
    assert objective(probability, labels, parameters) <= objective(probability, labels, (1.0, 0.0))


def test_separable_scores_and_zero_iteration_preserve_best_finite_iterate():
    probability = np.r_[np.full(100, 0.1), np.full(100, 0.9)]
    labels = np.r_[np.zeros(100), np.ones(100)]
    assert linknet.fit_platt(probability, labels, iters=0) == (1.0, 0.0)
    for iterations in (1, 3, 50):
        parameters = linknet.fit_platt(probability, labels, iters=iterations)
        assert np.isfinite(parameters).all()
        assert objective(probability, labels, parameters) <= objective(probability, labels, (1.0, 0.0))


def test_singular_newton_system_returns_finite_identity(monkeypatch):
    def singular(*args):
        raise np.linalg.LinAlgError("singular test system")
    monkeypatch.setattr(np.linalg, "solve", singular)
    assert linknet.fit_platt(np.full(100, 0.2), np.tile([0, 1], 50)) == (1.0, 0.0)


@pytest.mark.parametrize("probability,labels", [(np.array([0.1, np.nan]), np.array([0, 1])), (np.array([0.1, 0.9]), np.array([0, np.inf])), (np.array([0.1]), np.array([0, 1])), (np.array([0.1, 0.9]), np.array([0, 2]))])
def test_invalid_calibration_data_is_rejected(probability, labels):
    with pytest.raises(ValueError, match="finite probability/label vectors"):
        linknet.fit_platt(probability, labels)
