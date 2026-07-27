from tools import run_mu_ladder_top_exit_contingency as campaign


def test_exit_contingency_is_small_and_preregistered():
    rows = campaign.preregistered_exit_settings()
    assert len(rows) == 14
    assert len({row.label for row in rows}) == len(rows)
    assert {row.family for row in rows} == {
        "E03_CONFIRMED_STRUCTURE_RETEST",
        "E06_REGRESSION_REENTRY",
        "E09_MTF_EXHAUSTION_STRUCTURE",
    }


def test_top_exit_gate_adds_same_entry_actual_exit_and_causality():
    metrics = {
        "capital_return_pct": 1000.0,
        "bh_capital_return_pct": 100.0,
        "exposure_weighted_tim_pct": 75.0,
        "insolvent": False,
        "minimum_account_equity_usd": 1.0,
        "entry_capacity_breach": False,
        "clamp_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "reclaim_obligations_unfilled_at_end": 0,
        "exit_count": 2,
    }
    passed, failures = campaign._gate(metrics, 2, 900.0, 0)
    assert passed
    assert not failures
    metrics["exit_count"] = 0
    passed, failures = campaign._gate(metrics, 2, 1100.0, 1)
    assert not passed
    assert {
        "not_above_same_entry_e02",
        "no_actual_top_exit",
        "future_htf_source",
    }.issubset(failures)
