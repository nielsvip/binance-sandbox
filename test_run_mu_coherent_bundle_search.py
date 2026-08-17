from __future__ import annotations

from tools import run_mu_coherent_bundle_search as target


def _metrics(**updates):
    row = {
        "capital_return_pct": 220.0,
        "bh_capital_return_pct": 50.0,
        "exposure_weighted_tim_pct": 72.0,
        "binary_tim_pct": 75.0,
        "max_drawdown_account_pct": 20.0,
        "minimum_account_equity_usd": 8_000.0,
        "exit_fills": 4,
        "reclaim_reentries": 3,
        "lower_or_higher_reentries": 1,
        "bars_flat_beyond_reclaim": 0,
        "future_htf_source_count": 0,
        "clamp_count": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
        "mandatory_reclaim_execution": (
            "RESTING_TOUCH_LEVEL_OR_ADVERSE_GAP_OPEN"
        ),
        "frozen_entry_schedule_sha256": "same",
    }
    row.update(updates)
    return row


def test_preregistered_entry_grid_is_bounded_unique_and_contains_requested_families():
    rows = target.preregistered_entries()
    assert len(rows) == 17
    assert len({row.label for row in rows}) == len(rows)
    assert {row.family for row in rows} == {
        "ENTRY_BAND_LADDER",
        "ENTRY_WT_DC",
        "ENTRY_GOLDEN_RULE",
    }
    assert all(row.curve.semantics == "target" for row in rows)


def test_strict_fold_gate_passes_complete_contract():
    passed, failures, evidence = target.gate_fold(
        _metrics(), _metrics(capital_return_pct=180.0), same_schedule=True
    )
    assert passed
    assert failures == []
    assert evidence["strategy_bh_multiple"] == 4.4
    assert evidence["alpha_vs_same_entry_hold_pp"] == 40.0


def test_fold_gate_rejects_control_loss_tim_and_reclaim_faults():
    passed, failures, _ = target.gate_fold(
        _metrics(
            capital_return_pct=170.0,
            exposure_weighted_tim_pct=81.0,
            bars_flat_beyond_reclaim=1,
        ),
        _metrics(capital_return_pct=180.0),
        same_schedule=False,
    )
    assert not passed
    assert {
        "positive_alpha_vs_same_entry_hold",
        "weighted_tim_65_80",
        "same_entry_schedule",
        "never_flat_beyond_reclaim",
    }.issubset(failures)


def test_fold_gate_requires_two_x_positive_side_bh_and_real_exit():
    passed, failures, _ = target.gate_fold(
        _metrics(
            capital_return_pct=90.0,
            bh_capital_return_pct=50.0,
            exit_fills=0,
        ),
        _metrics(capital_return_pct=80.0),
        same_schedule=True,
    )
    assert not passed
    assert "at_least_2x_side_bh" in failures
    assert "actual_top_exit" in failures
