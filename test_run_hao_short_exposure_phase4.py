from __future__ import annotations

from tools.run_hao_short_exposure_phase4 import (
    EXPOSURE_POLICIES,
    EXIT_FAMILIES,
    candidate_id,
    final_evaluation_candidates,
    freeze_discovery,
    normalize_fold,
    preregistered_candidates,
    reclaim_fill_allowed,
)


def _raw(strategy: float, *, tim: float = 75.0, bh: float = 10.0) -> dict:
    return {
        "capital_return_pct": strategy,
        "bh_capital_return_pct": bh,
        "exposure_weighted_tim_pct": tim,
        "binary_tim_pct": 80.0,
        "max_drawdown_account_pct": 20.0,
        "minimum_account_equity_usd": 8_000.0,
        "peak_post_fill_notional_usd": 16_000.0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "entry_fills": 5,
        "exit_fills": 2,
        "reclaim_reentries": 1,
        "staged_adds": 1,
        "impulse_adds": 1,
        "reclaim_above_reference_rejections": 0,
        "future_htf_source_count": 0,
        "reclaim_obligation_open_at_end": False,
    }


def _candidate(cid: str, family: str, strict: bool) -> dict:
    fold = {
        "fold_gate_pass": strict,
        "strategy_return_pct": 40.0,
        "opportunity_benchmark_pct": 10.0,
        "exposure_contribution_vs_phase3_pp": 10.0,
        "exit_contribution_vs_same_entry_e02_pp": 5.0,
        "weighted_tim_pct": 75.0,
        "max_drawdown_account_pct": 20.0,
    }
    return {
        "candidate_id": cid,
        "exit_family": family,
        "discovery_fold_evidence": [dict(fold), dict(fold)],
        "discovery_all_folds_strict": strict,
    }


def test_phase4_grid_is_bounded_stable_and_starts_with_phase3_source() -> None:
    rows = preregistered_candidates()
    assert len(rows) == len(EXPOSURE_POLICIES) * 2 == 26
    assert len({row["candidate_id"] for row in rows}) == 26
    source = EXPOSURE_POLICIES[0]
    assert source.label == "P00_SOURCE_0875_C6"
    assert source.ladder_scale == 0.875
    assert source.ladder_cap_mult == 6.0
    assert candidate_id(source, EXIT_FAMILIES[0]) == rows[0]["candidate_id"]


def test_reclaim_never_fills_above_stored_short_reference() -> None:
    assert reclaim_fill_allowed(opening_fill_px=99.0, stored_reference=100.0)
    assert reclaim_fill_allowed(opening_fill_px=100.0, stored_reference=100.0)
    assert not reclaim_fill_allowed(
        opening_fill_px=100.01, stored_reference=100.0
    )


def test_gate_requires_exposure_benchmark_source_and_same_entry_control() -> None:
    row = normalize_fold(
        _raw(50.0),
        fold=1,
        source_phase3=_raw(40.0),
        same_entry_e02=_raw(45.0),
        exit_family="EXIT_E05_DIVERGENCE_RETEST",
    )
    assert row["fold_gate_pass"]
    assert row["beats_phase3_same_exit_control"]
    assert row["beats_same_entry_e02"]

    for changed in (
        _raw(50.0, tim=69.9),
        _raw(50.0, tim=80.1),
        _raw(9.0),
    ):
        failed = normalize_fold(
            changed,
            fold=1,
            source_phase3=_raw(40.0),
            same_entry_e02=_raw(45.0),
            exit_family="EXIT_E05_DIVERGENCE_RETEST",
        )
        assert not failed["fold_gate_pass"]

    no_source_edge = normalize_fold(
        _raw(39.0),
        fold=1,
        source_phase3=_raw(40.0),
        same_entry_e02=_raw(20.0),
        exit_family="EXIT_E02_DONCHIAN",
    )
    assert not no_source_edge["fold_gate_pass"]


def test_freeze_is_final_blind_and_gray_rows_cannot_open_final() -> None:
    rows = [
        _candidate("gray-e02", EXIT_FAMILIES[0], False),
        _candidate("gray-e05", EXIT_FAMILIES[1], False),
    ]
    frozen = freeze_discovery(rows)
    assert {x["candidate_id"] for x in frozen} == {"gray-e02", "gray-e05"}
    assert final_evaluation_candidates(frozen) == []

    strict = _candidate("strict", EXIT_FAMILIES[0], True)
    frozen = freeze_discovery(rows + [strict])
    assert [x["candidate_id"] for x in frozen] == ["strict"]
    assert final_evaluation_candidates(frozen) == frozen

    try:
        freeze_discovery([{**rows[0], "exact_v3": {"return": 999}}])
    except ValueError as exc:
        assert "final/exact" in str(exc)
    else:
        raise AssertionError("phase4 freeze accepted an exact field")
