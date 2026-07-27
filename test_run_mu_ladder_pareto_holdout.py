from tools.run_mu_ladder_pareto_holdout import (
    MAX_DRAWDOWN_DETERIORATION_PP,
    compare_fold,
    tim_gap,
)


def _metrics(**overrides):
    row = {
        "capital_return_pct": 300.0,
        "bh_capital_return_pct": 100.0,
        "exposure_weighted_tim_pct": 75.0,
        "max_drawdown_account_pct": 20.0,
        "minimum_account_equity_usd": 8_500.0,
        "clamp_count": 0,
        "fill_ratio": 1.0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "bars_flat_beyond_reclaim": 0,
        "reclaim_obligations_unfilled_at_end": 0,
    }
    row.update(overrides)
    return row


def test_tim_gap_is_distance_to_closed_band():
    assert tim_gap(69.0) == 1.0
    assert tim_gap(70.0) == 0.0
    assert tim_gap(75.0) == 0.0
    assert tim_gap(80.0) == 0.0
    assert tim_gap(83.5) == 3.5


def test_bounded_return_sacrifice_passes_with_mechanical_repair():
    candidate = _metrics(capital_return_pct=750.0)
    source = _metrics(
        capital_return_pct=1_000.0,
        exposure_weighted_tim_pct=91.0,
        clamp_count=150,
        fill_ratio=0.25,
    )
    result = compare_fold(candidate, source)
    assert result["pass"]
    assert result["pareto_dimensions"]["source_return_retention"] == 0.75
    assert result["checks"]["return_sacrifice_compensated"]


def test_return_sacrifice_below_75_percent_fails_without_score():
    candidate = _metrics(capital_return_pct=749.0)
    source = _metrics(
        capital_return_pct=1_000.0,
        exposure_weighted_tim_pct=91.0,
        clamp_count=150,
    )
    result = compare_fold(candidate, source)
    assert not result["pass"]
    assert "source_return_retention" in result["failures"]


def test_out_of_band_candidate_fails_even_with_more_return():
    candidate = _metrics(
        capital_return_pct=2_000.0,
        exposure_weighted_tim_pct=80.01,
    )
    source = _metrics(
        capital_return_pct=1_000.0,
        exposure_weighted_tim_pct=91.0,
    )
    result = compare_fold(candidate, source)
    assert not result["pass"]
    assert "weighted_tim_70_80" in result["failures"]


def test_risk_bounds_are_independent_of_return():
    source = _metrics(max_drawdown_account_pct=10.0)
    candidate = _metrics(
        capital_return_pct=2_000.0,
        max_drawdown_account_pct=(
            10.0 + MAX_DRAWDOWN_DETERIORATION_PP + 0.01
        ),
    )
    result = compare_fold(candidate, source)
    assert not result["pass"]
    assert "bounded_drawdown_deterioration" in result["failures"]


def test_reclaim_and_capacity_fail_closed():
    source = _metrics(exposure_weighted_tim_pct=90.0)
    candidate = _metrics(
        bars_flat_beyond_reclaim=1,
        entry_capacity_breach=True,
    )
    result = compare_fold(candidate, source)
    assert not result["pass"]
    assert "no_flat_beyond_reclaim" in result["failures"]
    assert "no_capacity_breach" in result["failures"]
