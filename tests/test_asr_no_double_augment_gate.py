from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.asr_no_double_augment_gate import asr_no_double_augment_gate


def test_native_no_double_augment_boundaries_and_position_presence():
    cfg = SimpleNamespace(MIN_GAIN=0.5)
    state = {"position_exists": [True, True, True, False, True],
             "last_augmentation_present": [True, True, True, False, True],
             "minutes_since_augmentation": [23.999, 24.0, 1.0, 0.0, 0.0],
             "gain": [0.499, 0.499, 0.5, -10.0, 2.0]}
    result = asr_no_double_augment_gate(state, cfg, 5)
    assert result["blocked"].tolist() == [True, False, False, False, False]


def test_held_position_requires_explicit_augmentation_presence():
    with pytest.raises(ValueError, match="last_augmentation_present"):
        asr_no_double_augment_gate({"position_exists": [True]}, SimpleNamespace(MIN_GAIN=.5), 1)


def test_missing_time_or_gain_is_rejected_for_augmented_position():
    with pytest.raises(ValueError, match="minutes_since_augmentation,gain"):
        asr_no_double_augment_gate(
            {"position_exists": [True], "last_augmentation_present": [True]},
            SimpleNamespace(MIN_GAIN=.5), 1)


def test_flat_position_needs_no_augmentation_state():
    result = asr_no_double_augment_gate({"position_exists": [False]}, SimpleNamespace(MIN_GAIN=.5), 1)
    assert result["blocked"].tolist() == [False]
