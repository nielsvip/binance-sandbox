from copy import deepcopy

import numpy as np

from tools.run_lifecycle_combo_beam import (
    BEHAVIOR_FIELDS,
    Recipe,
    Stream,
    canonical_hash,
    rank_tuple,
    simulate_batch,
    strict_unique_rows,
)
from tools.vec_top_exit_campaign import ExecutionData


def _row(recipe_id: str, gain: float) -> dict:
    row = {field: 1 for field in BEHAVIOR_FIELDS}
    row.update(
        recipe_id=recipe_id,
        capital_return_pct=gain,
        gain_pct_per_month=gain / 2,
        max_drawdown_account_pct=3.0,
        time_in_market_pct=60.0,
        weighted_time_in_market_pct=25.0,
        closes_per_month=4.0,
    )
    return row


def test_repeated_stage_observation_is_one_logical_recipe():
    first = _row("recipe-a", 10.0)
    repeated = deepcopy(first)
    unique, receipt = strict_unique_rows([first, repeated])
    assert [row["recipe_id"] for row in unique] == ["recipe-a"]
    assert receipt["logical_recipes"] == 1
    assert receipt["duplicate_behavior_groups"] == 0


def test_cross_recipe_duplicate_behavior_is_quarantined():
    first = _row("recipe-a", 10.0)
    duplicate = _row("recipe-b", 10.0)
    distinct = _row("recipe-c", 11.0)
    unique, receipt = strict_unique_rows([first, duplicate, distinct])
    assert {row["recipe_id"] for row in unique} == {"recipe-a", "recipe-c"}
    representative = next(row for row in unique if row["recipe_id"] == "recipe-a")
    assert representative["param_ambiguity_count"] == 1
    assert receipt["behavior_unique_results"] == 2
    assert receipt["causally_unique_recipes"] == 1
    assert receipt["quarantined_duplicate_recipes"] == 1
    assert receipt["duplicate_behavior_groups"] == 1
    assert receipt["matrix_write_allowed"] is False
    digest = receipt.pop("receipt_sha256")
    assert digest == canonical_hash(receipt)


def test_beats_bh_with_real_closes_outranks_high_frequency_loser():
    winner = _row("winner", 12.0)
    winner.update(
        delta_gain_mo_vs_bh=10.0,
        bh_gain_pct_per_month=2.0,
        real_close_actions=7,
        capacity_clamps=0,
        activity_status="B&H_COMPARISON_INVALID_ACTIVITY",
    )
    churn = _row("churn", -2.0)
    churn.update(
        delta_gain_mo_vs_bh=-4.0,
        bh_gain_pct_per_month=2.0,
        real_close_actions=100,
        capacity_clamps=0,
        activity_status="PASS_MONTHLY_FLOOR",
    )
    assert rank_tuple(winner, 50, 80) > rank_tuple(churn, 50, 80)


def test_zero_close_leveraged_hold_does_not_pass_beats_bh_gate():
    hold = _row("hold", 15.0)
    hold.update(
        delta_gain_mo_vs_bh=13.0,
        real_close_actions=0,
        capacity_clamps=0,
        activity_status="B&H_COMPARISON_INVALID_ACTIVITY",
    )
    trading = _row("trading", 12.0)
    trading.update(
        delta_gain_mo_vs_bh=10.0,
        real_close_actions=7,
        capacity_clamps=0,
        activity_status="B&H_COMPARISON_INVALID_ACTIVITY",
    )
    assert rank_tuple(trading, 50, 80) > rank_tuple(hold, 50, 80)


def test_account_bankruptcy_is_never_ranked_above_surviving_result():
    bankrupt = _row("bankrupt", 100.0)
    bankrupt.update(
        delta_gain_mo_vs_bh=90.0,
        real_close_actions=12,
        capacity_clamps=0,
        max_drawdown_account_pct=105.0,
        activity_status="PASS_MONTHLY_FLOOR",
    )
    surviving = _row("surviving", 8.0)
    surviving.update(
        delta_gain_mo_vs_bh=6.0,
        real_close_actions=6,
        capacity_clamps=0,
        max_drawdown_account_pct=30.0,
        activity_status="B&H_COMPARISON_INVALID_ACTIVITY",
    )
    assert rank_tuple(surviving, 50, 80) > rank_tuple(bankrupt, 50, 80)


def test_exit_bar_cannot_also_supply_reentry_or_reclaim_evidence():
    ts = np.array([1_700_000_000, 1_700_000_300, 1_700_000_600], dtype=np.int64)
    data = ExecutionData(
        symbol="XYZ",
        path="memory",
        ts=ts,
        open=np.array([100.0, 110.0, 111.0]),
        high=np.array([101.0, 120.0, 112.0]),
        low=np.array([99.0, 109.0, 110.0]),
        close=np.array([100.0, 111.0, 111.0]),
        synthetic=np.zeros(3, dtype=bool),
        full_indices=np.arange(3),
        z=None,
        contract={},
    )
    never = np.zeros(3, dtype=bool)
    same_exit_bar = np.array([False, True, False])
    dc = [Stream("dc", "DC", {}, never)]
    wt = [Stream("wt", "WT", {}, same_exit_bar)]
    exits = [Stream("exit", "EXIT", {}, same_exit_bar)]
    recipe = Recipe(0, "NONE", -1, -1, 0.0, 0, "WT", 0, "ENTRY", 1, 1)
    [row] = simulate_batch(
        data,
        "LONG",
        [recipe],
        np.array([1.0, 0.0, 0.0]),
        dc,
        wt,
        [Stream("reduce", "REDUCE", {}, never)],
        exits,
        0.0,
        0.0,
    )
    assert row["full_exits"] == 1
    assert row["fills"] == 1
    assert row["time_in_market_pct"] == 100.0 / 3.0
