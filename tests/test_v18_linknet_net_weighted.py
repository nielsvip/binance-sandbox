import numpy as np
import pytest

from tools import v18_linknet_net_weighted as weighted


def sample_training_arrays():
    kinds = np.array([0, 0, 0, 0, 1, 1, 1, 1, 0, 1], np.int8)
    labels = np.array([1, 0, 0, 0, 0, 0, 0, 1, 0, 0], np.float64)
    base = np.ones(len(kinds), np.float64)
    fit_rows = np.arange(8, dtype=np.int64)
    return kinds, labels, base, fit_rows


def test_class_weights_use_only_fit_labels_not_validation_or_test_labels():
    kinds, labels, base, fit_rows = sample_training_arrays()
    original = weighted.fit_only_class_weights(kinds, labels, base, fit_rows)
    labels[8:] = 1.0 - labels[8:]
    changed_outside_fit = weighted.fit_only_class_weights(kinds, labels, base, fit_rows)
    assert original == changed_outside_fit


def test_fit_weight_mean_is_one_separately_for_each_kind():
    kinds, labels, base, fit_rows = sample_training_arrays()
    factors = weighted.fit_only_class_weights(kinds, labels, base, fit_rows)
    for kind, name in ((0, "R_naked"), (1, "F_NET")):
        rows = fit_rows[kinds[fit_rows] == kind]
        sample_factor = np.where(labels[rows] > 0.5, factors[kind]["positive"], factors[kind]["negative"])
        assert np.average(sample_factor, weights=base[rows]) == pytest.approx(1.0)
        assert factors[name]["base_mask_weighted_mean"] == pytest.approx(1.0)


def test_batch_weighting_changes_only_bce_mask_and_leaves_labels_and_amount_mask_untouched():
    kinds = np.array([0, 1, 0, 1], np.int8)
    labels = np.array([1, 1, 0, 0], np.float32)
    before_labels = labels.copy()
    mask = np.ones((4, 6), np.float32)
    before_mask = mask.copy()
    weights = {0: {"positive": 2.0, "negative": 0.5}, 1: {"positive": 3.0, "negative": 0.25}}
    after = weighted.apply_class_weights_to_batch(mask, kinds, labels, weights)
    assert np.array_equal(labels, before_labels)
    assert after[:, 0].tolist() == pytest.approx([2.0, 3.0, 0.5, 0.25])
    assert np.array_equal(after[:, 1:], before_mask[:, 1:])
    assert np.array_equal(mask, before_mask)


def test_forward_rows_are_not_class_weighted():
    kinds = np.array([0, 1], np.int8)
    labels = np.array([1, 0], np.float32)
    mask = np.ones((4, 6), np.float32)
    weights = {0: {"positive": 2.0, "negative": 0.5}, 1: {"positive": 3.0, "negative": 0.25}}
    after = weighted.apply_class_weights_to_batch(mask, kinds, labels, weights, primary_rows=2)
    assert after[:2, 0].tolist() == pytest.approx([2.0, 0.25])
    assert np.array_equal(after[2:], mask[2:])


def test_source_pins_match_original_net_and_linknet_modules():
    pins = weighted.verify_source_pins()
    assert pins["net_recipe_sha256"] == weighted.PINNED_NET_RECIPE_SHA256
    assert pins["linknet_sha256"] == weighted.PINNED_LINKNET_SHA256


def test_fit_rows_must_be_unique_and_have_both_classes_per_kind():
    kinds, labels, base, fit_rows = sample_training_arrays()
    with pytest.raises(ValueError, match="unique valid indices"):
        weighted.fit_only_class_weights(kinds, labels, base, np.r_[fit_rows, fit_rows[0]])
    labels[:4] = 0
    with pytest.raises(ValueError, match="both classes"):
        weighted.fit_only_class_weights(kinds, labels, base, fit_rows)
