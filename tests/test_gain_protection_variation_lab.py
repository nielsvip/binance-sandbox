from tools.gain_protection_variation_lab import candidate_configs, confirmed_stop_loser_counts


def test_candidates_are_template_backed_fee_positive_and_set_gain_protection_only():
    rows = candidate_configs({"PARTIAL_PROFIT_LOCK_ENABLED": False}, 0.08)
    assert len(rows) == 3
    assert all(row["theoretical_gross_lifecycle_floor_pct"] > 0.08 for row in rows)
    assert all(row["overrides"]["PARTIAL_PROFIT_LOCK_ENABLED"] is True for row in rows)
    assert all(row["overrides"]["PARTIAL_PROFIT_LOCK_FRAC"] == 0.5 for row in rows)
    assert all("FIXED_STOP" not in key for row in rows for key in row["overrides"])


def test_candidates_skip_identical_published_ppl_set():
    base = {
        "PARTIAL_PROFIT_LOCK_ENABLED": True,
        "PARTIAL_PROFIT_LOCK_GAIN_PCT": 1.5,
        "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": 1.75,
        "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": 0.1,
        "PARTIAL_PROFIT_LOCK_FRAC": 0.5,
    }
    rows = candidate_configs(base, 0.08)
    assert len(rows) == 2
    assert all(row["overrides"] != base for row in rows)


def test_selection_uses_confirmed_fixed_stop_loss_fill_count():
    inventory = {"per_symbol": {"AAA": {"position_sides": {
        "LONG": {"fixed_stop_exit_fill_loss_count": 3},
        "SHORT": {"fixed_stop_exit_fill_loss_count": 2},
    }}, "BBB": {"position_sides": {"LONG": {"fixed_stop_exit_fill_loss_count": 4}}}}}
    assert confirmed_stop_loser_counts(inventory, 3) == {"AAA_LONG": 3, "BBB_LONG": 4}
