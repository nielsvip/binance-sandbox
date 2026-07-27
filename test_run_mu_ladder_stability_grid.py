from tools import run_mu_ladder_stability_grid as grid


def test_preregistered_grid_is_stable_and_exact_candidates_use_close_confirm():
    rows = grid.preregistered_candidates()
    assert len(rows) >= 200
    assert len({row.label for row in rows}) == len(rows)
    assert {row.reclaim_cadence for row in rows} == {
        "close_confirm_next_availability",
        "intrabar_touch_next_availability",
    }
    assert all(
        row.exact_eligible
        == (row.reclaim_cadence == "close_confirm_next_availability")
        for row in rows
    )
    assert all(row.curve.semantics == "target" for row in rows)
    assert all(
        max(
            row.curve.d_bottom,
            row.curve.d_top,
            row.curve.h4_bottom,
            row.curve.h4_top,
            row.curve.h1_bottom,
            row.curve.h1_top,
        )
        <= row.effective_cap_x
        for row in rows
    )


def test_gate_requires_alpha_tim_solvency_capacity_and_reclaim():
    good = {
        "capital_return_pct": 500.0,
        "bh_capital_return_pct": 44.0,
        "exposure_weighted_tim_pct": 75.0,
        "insolvent": False,
        "minimum_account_equity_usd": 1.0,
        "entry_capacity_breach": False,
        "clamp_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "reclaim_obligations_unfilled_at_end": 0,
    }
    passed, failures = grid._gate(good, 1)
    assert passed
    assert not failures
    bad = dict(good)
    bad.update(
        {
            "exposure_weighted_tim_pct": 80.01,
            "clamp_count": grid.MAX_CLAMPS_PER_FOLD + 1,
            "bars_flat_beyond_reclaim": 1,
        }
    )
    passed, failures = grid._gate(bad, 1)
    assert not passed
    assert {
        "weighted_tim_outside_70_80",
        "clamps_above_preregistered_limit",
        "flat_beyond_reclaim",
    }.issubset(failures)
