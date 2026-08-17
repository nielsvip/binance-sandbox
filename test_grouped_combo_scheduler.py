from tools.grouped_combo_scheduler import (
    ACTION_GROUPS,
    Arm,
    LossProof,
    PRIORITY_KEYS,
    classify_action_group,
    plan_recipes,
    receipt_owner,
)


def arm(param, group, priority, **overrides):
    return Arm(
        param=param,
        value_json=str(priority),
        overrides=overrides or {param: priority},
        action_group=group,
        family=param.split("_")[0],
        priority=priority,
        receipt=f"receipt-{param}",
    )


def test_priority_scope_has_ranked_ten_per_side_mandatory_and_pilots():
    assert len(PRIORITY_KEYS) == 24
    assert "IBIT_LONG" in PRIORITY_KEYS
    assert "PLTR_SHORT" in PRIORITY_KEYS
    assert PRIORITY_KEYS[-2:] == ("MU_LONG", "NVDA_LONG")
    assert sum(key.endswith("_LONG") for key in PRIORITY_KEYS) == 13
    assert sum(key.endswith("_SHORT") for key in PRIORITY_KEYS) == 11


def test_inventory_classification_separates_five_causal_groups():
    assert classify_action_group(
        {"param": "WT_DC_ENTRY_THRESHOLD", "group": "ENTRY"}
    ) == "ENTRY"
    assert classify_action_group(
        {"param": "BOUNCE_AUGMENT_ENABLED", "group": "ENTRY"}
    ) == "AUGMENT"
    assert classify_action_group(
        {"param": "REGIME_WT_REDUCE_FRAC", "group": "EXIT"}
    ) == "REDUCE"
    assert classify_action_group(
        {"param": "MTF_DC_REJECT_EXIT_ENABLED", "group": "EXIT"}
    ) == "EXIT"
    assert classify_action_group(
        {"param": "MANDATORY_RECLAIM_ENABLED", "group": "ENTRY"}
    ) == "REENTER"


def test_every_available_path_gets_within_group_interaction():
    arms = [
        arm("ENTRY_A", "ENTRY", 9),
        arm("ENTRY_B", "ENTRY", 8),
        arm("ENTRY_C", "ENTRY", -4),
        arm("EXIT_A", "EXIT", 7),
        arm("EXIT_B", "EXIT", 6),
    ]
    recipes = plan_recipes("MU_LONG", arms)
    within_members = {
        member.param
        for recipe in recipes
        if recipe.stage.startswith("WITHIN_")
        for member in recipe.members
    }
    assert within_members == {member.param for member in arms}
    # Negative single evidence does not silently remove ENTRY_C.
    assert any(
        member.param == "ENTRY_C"
        for recipe in recipes
        for member in recipe.members
    )
    assert any(
        recipe.stage == "CROSS_GROUP_COVERAGE"
        and {member.param for member in recipe.members}
        == {"ENTRY_C", "EXIT_A"}
        for recipe in recipes
    )


def test_cross_group_champion_and_mandatory_loo_are_planned():
    arms = [
        arm(f"{group}_A", group, 10 - index)
        for index, group in enumerate(ACTION_GROUPS)
    ]
    recipes = plan_recipes("ACN_SHORT", arms)
    full = [row for row in recipes if row.stage == "CROSS_GROUP_CHAMPION"]
    loo = [row for row in recipes if row.stage == "LEAVE_ONE_OUT"]
    assert len(full) == 1
    assert len(full[0].members) == 5
    assert len(loo) == 5
    assert all(row.parents == (full[0].recipe_id,) for row in loo)
    assert {row.omitted_arm for row in loo} == {
        member.arm_id for member in full[0].members
    }


def test_recipe_ids_are_deterministic_and_window_independent():
    arms = [arm("ENTRY_A", "ENTRY", 4), arm("EXIT_A", "EXIT", 3)]
    first = plan_recipes("VT_LONG", arms)
    second = plan_recipes("VT_LONG", list(reversed(arms)))
    assert [row.recipe_id for row in first] == [row.recipe_id for row in second]
    assert [row.logical_value_json for row in first] == [
        row.logical_value_json for row in second
    ]
    replacement_receipt = Arm(
        **{**arms[0].__dict__, "receipt": "newer-full-window-receipt"}
    )
    replaced = plan_recipes("VT_LONG", [replacement_receipt, arms[1]])
    assert [row.logical_value_json for row in first] == [
        row.logical_value_json for row in replaced
    ]


def test_conflicting_overrides_are_not_scheduled_together():
    recipes = plan_recipes(
        "TTD_SHORT",
        [
            arm("ENTRY_A", "ENTRY", 2, SHARED=True),
            arm("EXIT_A", "EXIT", 1, SHARED=False),
        ],
    )
    assert recipes == []


def test_sparse_sampling_requires_complete_loss_proof():
    candidate = arm("EXIT_ALWAYS_BAD", "EXIT", -10)
    incomplete = LossProof(
        param=candidate.param,
        applicable_timeframes=("5m", "1h", "D"),
        tested_timeframes=("5m", "1h"),
        registered_values=("1", "2", "3"),
        tested_values=("1", "2", "3"),
        uniformly_negative_batches=2,
        companions_complete=True,
        interactions_complete=True,
        exact_current_contract=True,
        valid_capital_accounting=True,
    )
    assert incomplete.sampling_denominator == 1

    complete = LossProof(
        **{
            **incomplete.__dict__,
            "tested_timeframes": incomplete.applicable_timeframes,
            "uniformly_negative_batches": 1,
        }
    )
    assert complete.sampling_denominator == 5
    second_batch = LossProof(
        **{**complete.__dict__, "uniformly_negative_batches": 2}
    )
    assert second_batch.sampling_denominator == 10


def test_completed_recipe_is_not_replanned():
    arms = [arm("ENTRY_A", "ENTRY", 4), arm("EXIT_A", "EXIT", 3)]
    recipes = plan_recipes("IBIT_LONG", arms)
    assert recipes
    remaining = plan_recipes(
        "IBIT_LONG",
        arms,
        completed_recipe_ids={recipes[0].recipe_id},
    )
    assert recipes[0].recipe_id not in {row.recipe_id for row in remaining}


def test_key_with_no_eligible_seed_arms_skips_cleanly():
    assert plan_recipes("AAPL_LONG", []) == []


def test_single_group_without_compatible_peer_does_not_build_empty_cross_recipe():
    assert plan_recipes(
        "AAPL_LONG", [arm("ENTRY_ONLY", "ENTRY", 1)]
    ) == []


def test_receipt_owner_prefers_valid_full_window_over_newer_one_year():
    common = {
        "tier": "ENGINE",
        "validation_status": "PASS",
        "contract_fingerprint": "fp",
        "capital_accounting_version": "cap-v1",
        "trades_fingerprint": "trades",
    }
    full = {**common, "campaign": "full", "years": 2.4, "ts": "2026-07-01"}
    one_year = {
        **common,
        "campaign": "one-year",
        "years": 1.0,
        "ts": "2026-07-31",
    }
    assert receipt_owner(
        [one_year, full],
        current_fingerprint="fp",
        capital_accounting_version="cap-v1",
    ) == full


def test_one_year_fills_only_when_full_receipt_is_invalid_or_missing():
    invalid_full = {
        "tier": "ENGINE",
        "validation_status": "PASS",
        "contract_fingerprint": "old-fp",
        "capital_accounting_version": "cap-v1",
        "trades_fingerprint": "trades",
        "years": 2.2,
    }
    one_year = {
        **invalid_full,
        "contract_fingerprint": "fp",
        "campaign": "one-year",
        "years": 1.0,
    }
    assert receipt_owner(
        [invalid_full, one_year],
        current_fingerprint="fp",
        capital_accounting_version="cap-v1",
    ) == one_year
