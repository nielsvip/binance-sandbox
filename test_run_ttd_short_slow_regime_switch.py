import dataclasses
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import run_ttd_short_slow_regime_switch as study


def test_rebound_score_is_trailing_and_causal():
    close = np.array([10, 9, 8, 10, 7, 7, 14], dtype=float)
    score = study._rolling_max_rebound(close, 4)
    assert np.isnan(score[2])
    assert score[3] == .25
    assert score[5] == .25
    # The final future jump must not affect the preceding score.
    assert score[5] == study._rolling_max_rebound(close[:-1], 4)[5]
    assert score[6] == 1.0


def test_confirmed_state_requires_consecutive_completed_parents():
    score = np.array([.1, .9, .1, .9, .9, .2, .2])
    state = study._confirmed_state(score, .5, 2)
    assert state.tolist() == [0, 0, 0, 0, 1, 1, 0]


def test_state_mapping_changes_only_at_completed_parent_availability():
    data = SimpleNamespace(ts=np.arange(12, dtype=np.int64))
    h4 = SimpleNamespace(event_index=np.array([2, 6, 10]))
    mapped = study._state_to_execution(
        data, h4, np.array([0, 1, 0], dtype=np.uint8)
    )
    assert mapped.tolist() == [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0]


def test_switch_uses_exactly_one_whole_exit_book():
    n = 6
    entry = study.ladder.SignalData(
        entry_mult=np.zeros(n),
        event_tf=np.zeros(n, dtype=np.uint8),
        exit_event=np.array([1, 0, 1, 0, 1, 0], dtype=np.uint8),
        exit_ref=np.array([10, 10, 11, 11, 12, 12], dtype=float),
        causality={},
    )
    strict_event = np.array([0, 1, 0, 1, 0, 1], dtype=np.uint8)
    strict_ref = np.array([20, 21, 22, 23, 24, 25], dtype=float)
    high = np.array([1, 1, 0, 0, 1, 0], dtype=np.uint8)
    switched = study._switch_signals(
        entry, strict_event, strict_ref, high
    )
    assert switched.exit_event.tolist() == [1, 0, 0, 1, 1, 1]
    assert switched.exit_ref.tolist() == [10, 10, 22, 23, 12, 25]


def test_policy_signature_ignores_fold_specific_threshold():
    policy = study.SWITCH_POLICIES[0]
    a = {
        "definition": {
            "kind": "slow_correction_risk_switch",
            "policy": dataclasses.asdict(policy),
            "frozen_threshold": .1,
        }
    }
    b = {
        "definition": {
            **a["definition"],
            "frozen_threshold": .4,
        }
    }
    assert study._policy_signature(a) == study._policy_signature(b)


def test_policy_set_is_bounded_and_whole_book_only():
    assert study.ROOT == Path(__file__).resolve().parent
    assert len(study.SWITCH_POLICIES) == 12
    assert {p.high_risk_book for p in study.SWITCH_POLICIES} == {
        "E02_N30_CONTROL"
    }
    assert {p.low_risk_book for p in study.SWITCH_POLICIES} == {
        study.STRICT_LABEL
    }
