import numpy as np
import pytest

from tools import state_aware_exposure_grid as state


def _inputs(n=12):
    return {
        "entry_mult": np.array([1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0], dtype=float),
        "source_exit_event": np.zeros(n, dtype=bool),
        "source_exit_ref": np.full(n, np.nan),
        "bar_open": np.full(n, 10.0),
        "bar_high": np.full(n, 10.5),
        "bar_low": np.full(n, 9.5),
        "completed_1h_slot": np.arange(n, dtype=np.int64),
    }


def test_grid_is_small_monotonic_and_capacity_bounded():
    assert len(state.policy_grid()) == 4
    for row in state.policy_grid():
        row.validate()
        assert row.scale[0] >= row.scale[1] >= row.scale[2]
        assert row.min_gap_completed_1h_bars[0] <= row.min_gap_completed_1h_bars[2]
        assert max(row.cap_mult) <= 8


def test_policy_is_prefix_causal_and_bounded():
    args = _inputs()
    policy = state.policy_grid()[0]
    full, audit = state.apply_policy(
        **args, policy=policy, side="LONG", target_semantics=True
    )
    cut = 7
    prefix, _ = state.apply_policy(
        **{key: value[:cut] for key, value in args.items()},
        policy=policy,
        side="LONG",
        target_semantics=True,
    )
    np.testing.assert_array_equal(full[:cut], prefix)
    assert np.max(full) <= 8
    assert audit["source_exit_state"] == "frozen source E02 only"


def test_future_mutation_cannot_change_discovery_schedule():
    args = _inputs()
    policy = state.policy_grid()[2]
    base, _ = state.apply_policy(
        **args, policy=policy, side="LONG", target_semantics=True
    )
    mutated = {key: value.copy() for key, value in args.items()}
    mutated["entry_mult"][8:] = 8
    mutated["source_exit_event"][9:] = True
    changed, _ = state.apply_policy(
        **mutated, policy=policy, side="LONG", target_semantics=True
    )
    np.testing.assert_array_equal(base[:8], changed[:8])


def test_reclaim_state_is_side_mirrored_and_uses_completed_source_event():
    args = _inputs()
    args["source_exit_event"][2] = True
    args["source_exit_ref"][2] = 11.0
    args["bar_high"][3] = 10.9
    args["bar_high"][4] = 11.1
    out, audit = state.apply_policy(
        **args,
        policy=state.policy_grid()[-1],
        side="LONG",
        target_semantics=True,
    )
    assert audit["reclaim_obligation_requests"] >= 1
    assert np.max(out) <= 8


def test_reclaim_anchor_latches_and_is_not_silently_replaced():
    args = _inputs()
    args["source_exit_event"][2] = True
    args["source_exit_ref"][2] = 11.0
    args["source_exit_event"][3] = True
    args["source_exit_ref"][3] = 99.0
    args["bar_high"][3:] = 10.9
    _, audit = state.apply_policy(
        **args,
        policy=state.policy_grid()[0],
        side="LONG",
        target_semantics=True,
    )
    assert audit["reclaim_obligations_created"] == 1
    assert audit["reclaim_obligations_unfilled_at_end"] == 1
    assert audit["reclaim_anchor_overwrite_count"] == 0


def test_rejects_nonmonotonic_slots():
    args = _inputs()
    args["completed_1h_slot"][5] = -1
    with pytest.raises(ValueError, match="monotonic"):
        state.apply_policy(
            **args,
            policy=state.policy_grid()[0],
            side="LONG",
            target_semantics=True,
        )
