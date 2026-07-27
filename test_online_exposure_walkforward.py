import copy

import numpy as np

from tools.online_exposure_walkforward import Policy, _update_level, apply_policy


def _arrays(n):
    return {
        "entry_mult": np.zeros(n),
        "source_exit_event": np.zeros(n, dtype=bool),
        "source_exit_ref": np.full(n, np.nan),
        "bar_open": np.full(n, 10.0),
        "bar_high": np.full(n, 10.0),
        "bar_low": np.full(n, 10.0),
        "completed_1h_slot": np.arange(n, dtype=np.int64),
    }


def _apply(values, policy=None):
    return apply_policy(
        **values,
        policy=policy or Policy(window_completed_1h_slots=4),
        side="LONG",
        target_semantics=True,
    )


def test_update_direction_is_set_by_prior_window_utilization():
    policy = Policy(window_completed_1h_slots=4)
    up, under = _update_level(
        0,
        occupied_slots=1,
        completed_slots=4,
        accepted_entries=0,
        realized_exits=1,
        request_drought_slots=8,
        policy=policy,
    )
    down, over = _update_level(
        0,
        occupied_slots=4,
        completed_slots=4,
        accepted_entries=2,
        realized_exits=0,
        request_drought_slots=0,
        policy=policy,
    )
    assert up == 2
    assert down == -2
    assert under["utilization_error_vs_75"] > 0
    assert over["utilization_error_vs_75"] < 0


def test_future_mutation_cannot_change_controller_prefix():
    policy = Policy(window_completed_1h_slots=4)
    base = _arrays(12)
    base["entry_mult"][[0, 4, 8]] = 1.0
    first, first_audit = _apply(base, policy)
    changed = copy.deepcopy(base)
    changed["entry_mult"][8:] = 8.0
    changed["source_exit_event"][9:] = True
    changed["source_exit_ref"][9:] = 999.0
    changed["bar_high"][9:] = 1e9
    second, second_audit = _apply(changed, policy)
    np.testing.assert_array_equal(first[:8], second[:8])
    assert first_audit["controller_updates"][:1] == (
        second_audit["controller_updates"][:1]
    )


def test_reclaim_is_latched_until_touch_and_emitted_without_raw_request():
    values = _arrays(8)
    values["entry_mult"][0] = 4.0
    values["source_exit_event"][2] = True
    values["source_exit_ref"][2] = 11.0
    values["bar_high"][3:6] = 10.5
    values["bar_high"][6] = 11.0
    out, audit = _apply(values)
    assert out[6] == 5.0  # first request gets the globally frozen drought boost
    assert audit["reclaim_obligations_created"] == 1
    assert audit["reclaim_requests_emitted"] == 1
    assert audit["reclaim_obligations_unfilled_at_end"] == 0
    assert audit["reclaim_anchor_overwrite_count"] == 0


def test_better_price_entry_satisfies_latched_reclaim():
    values = _arrays(8)
    values["entry_mult"][0] = 4.0
    values["source_exit_event"][2] = True
    values["source_exit_ref"][2] = 11.0
    values["entry_mult"][4] = 2.0
    out, audit = _apply(values)
    assert out[4] > 0
    assert audit["reclaim_obligations_filled_at_better_price"] == 1
    assert audit["reclaim_obligations_unfilled_at_end"] == 0


def test_hard_capacity_and_completed_window_audit():
    values = _arrays(15)
    values["entry_mult"][:] = 100.0
    out, audit = _apply(values, Policy(window_completed_1h_slots=4))
    assert np.max(out) <= 8.0
    assert audit["future_window_reads"] == 0
    assert audit["controller_updates"]
    assert all(
        row["completed_slots"] == 4
        for row in audit["controller_updates"]
    )
