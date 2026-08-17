import pytest
import numpy as np

from tools import run_classic_formation_group_exact_v8 as exact
from tools import classic_formation_group_entry_canary as canary


def recipe():
    return {
        "key": "ABC_LONG", "GROUP_VECTOR_REPLAY": {
            "train": {"return_pct": 5, "trades": 11, "alpha_vs_bh_pp": 1, "formation_entry_action_count": 1, "formation_exit_action_count": 1, "event_reason_families": [{"reason_family": "CLASSIC_FORMATION_ENTRY_WEDGE", "count": 1}, {"reason_family": "CLASSIC_FORMATION_EXIT_TRIANGLE", "count": 1}]},
            "holdout": {"return_pct": 6, "bh_return_pct": 2, "trades": 12, "alpha_vs_bh_pp": 4, "formation_entry_action_count": 2, "formation_exit_action_count": 2, "event_reason_families": [{"reason_family": "CLASSIC_FORMATION_ENTRY_WEDGE", "count": 2}, {"reason_family": "CLASSIC_FORMATION_EXIT_TRIANGLE", "count": 2}]},
        }, "FORMATION_ENTRY_EXIT_OVERLAY": {"entry_arm": "wedge_entry_1h", "exit_arm": "triangle_exit_1h", "timeframe": "1h"},
    }


def test_vector_gate_requires_positive_return_closes_bh_and_both_selected_routes_in_both_windows():
    exact.vector_gate(recipe())
    bad = recipe()
    bad["GROUP_VECTOR_REPLAY"]["holdout"]["formation_exit_action_count"] = 0
    with pytest.raises(ValueError, match="GROUP_VECTOR_GATE_FAILED:holdout:FORMATION_EXIT_ACTIONS_ZERO"):
        exact.vector_gate(bad)


def test_exact_audit_requires_side_isolation_routes_closes_bh_capacity_and_fills():
    rows = [
        {"position_side": "LONG", "action": "OPEN", "reason": "CLASSIC_FORMATION_ENTRY_WEDGE_score.8", "requested_qty": 1, "executed_qty": 1},
        {"position_side": "LONG", "action": "CLOSE", "exit_reason": "CLASSIC_FORMATION_EXIT_TRIANGLE_score.8"},
    ]
    result = exact.exact_audit(recipe(), {"real_closes": 11, "gain_pct": 3, "opens_short": 0, "max_open_notional": 1000}, rows, 0)
    assert result["status"] == "PASS"
    assert result["promotion_allowed"] is False
    assert result["formation_entry_route_actions"] == 1
    assert result["formation_exit_route_actions"] == 1


def test_selector_recipe_and_contract_keep_group_out_of_full_recipe_only_mode():
    value = recipe()
    value["GROUP_EXACT_HOLDOUT_END_DATE"] = "2026-07-24"
    out = exact.selector_recipe(value, {"FORMATION_MIN_SCORE": .7})
    assert out["ENTRY"]["family"] == "ENTRY_CLASSIC_FORMATION_WEDGE"
    assert out["EXIT"]["family"] == "EXIT_CLASSIC_FORMATION_TRIANGLE"
    assert "FULL_RECIPE_ONLY" not in exact.CONTRACT


def test_group_entry_canary_rejects_selector_when_shared_consumer_requires_isolation():
    value = recipe()
    result = canary.evaluate(value, {"FORMATION_WEDGE_ENTRY_ENABLED": True})
    assert result["pass"] is False
    assert result["shared_consumer_reason"] == "FORMATION_ENTRY_CONSUMER_REQUIRES_FULL_RECIPE_ONLY"
    assert result["blockers"] == [
        "GROUP_SELECTED_FORMATION_ENTRY_UNREACHABLE:FORMATION_ENTRY_CONSUMER_REQUIRES_FULL_RECIPE_ONLY"
    ]


def test_group_entry_canary_accepts_only_the_actual_shared_consumer_envelope():
    result = canary.evaluate(recipe(), {
        "FORMATION_WEDGE_ENTRY_ENABLED": True,
        "FULL_RECIPE_ONLY_ENABLED": True,
    })
    assert result["pass"] is True


def test_real_v8_and_tradier_consumers_use_the_same_entry_admission_contract():
    for path in ("backtest_v8_engine.py", "tradier_manage.py"):
        assert "formation_entry_consumer_admission(" in open(path).read()


def test_runner_revalidates_active_config_at_execution_boundary():
    source = open("tools/run_classic_formation_group_exact_v8.py").read()
    assert source.count("group.verify_active_config_binding(") >= 2
    assert '"V8_CLASSIC_FORMATION_FIELDS": "1"' in source
    assert '"V8_LADDER_ONLY_SIDE": side' in source


def test_exact_holdout_endpoint_is_derived_from_bound_npz_and_bar_count(tmp_path):
    path = tmp_path / "ABC.npz"
    np.savez(path, timestamps=np.array([1735600000, 1764547200, 1764633600], dtype=np.int64))
    value = recipe()
    value["GROUP_VECTOR_REPLAY"]["holdout"]["bars"] = 2
    assert exact.bound_holdout_end_date(value, path) == "2025-12-03"
    value["GROUP_VECTOR_REPLAY"]["holdout"]["bars"] = 3
    with pytest.raises(ValueError, match="GROUP_EXACT_HOLDOUT_BAR_COUNT_MISMATCH"):
        exact.bound_holdout_end_date(value, path)
