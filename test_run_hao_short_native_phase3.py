from __future__ import annotations

from tools.run_hao_short_native_phase3 import (
    ENTRY_FILTERS,
    LADDER_PROFILES,
    candidate_id,
    final_evaluation_candidates,
    freeze_discovery,
    normalize_fold,
    preregistered_candidates,
    rank_key,
    should_request_short_reclaim,
)


def _raw(
    strategy: float,
    *,
    tim: float = 75.0,
    bh: float = 10.0,
) -> dict:
    return {
        "capital_return_pct": strategy,
        "bh_capital_return_pct": bh,
        "exposure_weighted_tim_pct": tim,
        "binary_tim_pct": tim,
        "max_drawdown_account_pct": 20.0,
        "minimum_account_equity_usd": 8_000.0,
        "peak_post_fill_notional_usd": 12_000.0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "entry_fills": 3,
        "exit_fills": 2,
        "reclaim_reentries": 1,
        "vetoed_ladder_requests": 4,
        "vetoed_reclaim_rows": 2,
        "future_htf_source_count": 0,
        "reclaim_obligation_open_at_end": False,
    }


def _candidate(cid: str, family: str, score: float, strict: bool) -> dict:
    evidence = [
        {
            "fold_gate_pass": strict,
            "strategy_return_pct": score,
            "opportunity_benchmark_pct": 1.0,
            "exit_contribution_vs_same_entry_e02_pp": score,
            "entry_contribution_vs_ungated_e02_pp": score,
            "max_drawdown_account_pct": 20.0,
            "weighted_tim_pct": 75.0,
        },
        {
            "fold_gate_pass": strict,
            "strategy_return_pct": score,
            "opportunity_benchmark_pct": 1.0,
            "exit_contribution_vs_same_entry_e02_pp": score,
            "entry_contribution_vs_ungated_e02_pp": score,
            "max_drawdown_account_pct": 20.0,
            "weighted_tim_pct": 75.0,
        },
    ]
    return {
        "candidate_id": cid,
        "exit_family": family,
        "discovery_fold_evidence": evidence,
        "discovery_all_folds_strict": strict,
    }


def test_phase3_grid_is_bounded_and_stable() -> None:
    rows = preregistered_candidates()
    assert len(rows) == 3 * 8 * 2 == 48
    assert len({row["candidate_id"] for row in rows}) == 48
    assert len(LADDER_PROFILES) == 3
    assert len(ENTRY_FILTERS) == 8
    assert candidate_id(
        LADDER_PROFILES[0], ENTRY_FILTERS[0], "EXIT_E02_DONCHIAN"
    ) == rows[0]["candidate_id"]


def test_e05_gate_requires_same_entry_e02_but_e02_does_not() -> None:
    baseline = _raw(50.0)
    same_entry = _raw(30.0)
    e02 = normalize_fold(
        _raw(20.0),
        fold=1,
        baseline_e02=baseline,
        same_entry_e02=_raw(20.0),
        exit_family="EXIT_E02_DONCHIAN",
    )
    assert e02["fold_gate_pass"]
    assert e02["beats_same_entry_e02"] is None

    losing_e05 = normalize_fold(
        _raw(25.0),
        fold=1,
        baseline_e02=baseline,
        same_entry_e02=same_entry,
        exit_family="EXIT_E05_DIVERGENCE_RETEST",
    )
    assert not losing_e05["fold_gate_pass"]
    assert losing_e05["beats_same_entry_e02"] is False

    winning_e05 = normalize_fold(
        _raw(35.0),
        fold=1,
        baseline_e02=baseline,
        same_entry_e02=same_entry,
        exit_family="EXIT_E05_DIVERGENCE_RETEST",
    )
    assert winning_e05["fold_gate_pass"]


def test_fold_gate_requires_tim_solvency_capacity_and_no_future_source() -> None:
    raw = _raw(20.0, tim=60.0)
    raw["future_htf_source_count"] = 1
    raw["entry_capacity_breach"] = True
    row = normalize_fold(
        raw,
        fold=1,
        baseline_e02=_raw(10.0),
        same_entry_e02=_raw(20.0),
        exit_family="EXIT_E02_DONCHIAN",
    )
    assert not row["fold_gate_pass"]
    assert not row["tim_gate_pass"]
    assert not row["capacity_ok"]
    assert row["future_htf_source_count"] == 1


def test_vetoed_reclaim_touch_does_not_fill_later_above_level() -> None:
    level = 100.0
    assert not should_request_short_reclaim(
        reclaim_level=level,
        bar_low=99.0,
        bar_close=99.5,
        entry_gate=False,
    )
    # Once the veto clears, a recovery above the level is not a delayed market
    # short. The stored obligation waits for another touch/cross.
    assert not should_request_short_reclaim(
        reclaim_level=level,
        bar_low=101.0,
        bar_close=102.0,
        entry_gate=True,
    )
    assert should_request_short_reclaim(
        reclaim_level=level,
        bar_low=99.0,
        bar_close=101.0,
        entry_gate=True,
    )


def test_fold_gate_requires_an_actual_exit() -> None:
    raw = _raw(20.0)
    raw["exit_fills"] = 0
    row = normalize_fold(
        raw,
        fold=3,
        baseline_e02=_raw(10.0),
        same_entry_e02=_raw(15.0),
        exit_family="EXIT_E05_DIVERGENCE_RETEST",
    )
    assert not row["fold_gate_pass"]


def test_discovery_freeze_is_final_blind_and_exact_bounded() -> None:
    rows = [
        _candidate("e02-gray", "EXIT_E02_DONCHIAN", 2.0, False),
        _candidate("e05-gray", "EXIT_E05_DIVERGENCE_RETEST", 3.0, False),
    ]
    gray = freeze_discovery(rows)
    assert {row["candidate_id"] for row in gray} == {
        "e02-gray",
        "e05-gray",
    }
    assert final_evaluation_candidates(gray) == []
    strict_rows = rows + [
        _candidate("strict-low", "EXIT_E02_DONCHIAN", 10.0, True),
        _candidate("strict-high", "EXIT_E05_DIVERGENCE_RETEST", 20.0, True),
    ]
    frozen = freeze_discovery(strict_rows)
    assert [row["candidate_id"] for row in frozen] == ["strict-high"]
    assert final_evaluation_candidates(frozen) == frozen
    assert rank_key(frozen[0]) < rank_key(strict_rows[-2])

    contaminated = [{**rows[0], "untouched_final": {"return": 999}}]
    try:
        freeze_discovery(contaminated)
    except ValueError as exc:
        assert "final-fold" in str(exc)
    else:
        raise AssertionError("phase3 freeze accepted final metrics")
