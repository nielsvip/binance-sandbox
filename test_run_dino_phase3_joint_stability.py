from dataclasses import replace

from tools import run_dino_phase3_joint_stability as joint
from tools import vec_band_ladder_walkforward as ladder


def _base_curve() -> ladder.Curve:
    return ladder.Curve(
        "BASE",
        "linear",
        "structure",
        "target",
        30.0,
        3.0,
        1.0,
        2.0,
        1.0,
        1.0,
        0.5,
    )


def test_preregistered_grid_is_compact_unique_and_reclaim_is_frozen():
    rows = joint.preregistered_grid()
    assert len(rows) == 54
    assert len({row.label for row in rows}) == 54
    assert {row.reclaim_cadence for row in rows} == {joint.FROZEN_RECLAIM}
    assert sum(row.density_overlay != "NONE" for row in rows) == 6


def test_coherent_transform_clips_every_rung_and_preserves_order():
    profile = next(
        row for row in joint.preregistered_grid() if "S267_C8" in row.label
    )
    curve = joint.transformed_curve(_base_curve(), profile)
    assert curve.d_bottom == 8.0
    assert curve.d_bottom >= curve.d_top
    assert curve.h4_bottom >= curve.h4_top
    assert curve.h1_bottom >= curve.h1_top
    assert max(
        curve.d_bottom,
        curve.d_top,
        curve.h4_bottom,
        curve.h4_top,
        curve.h1_bottom,
        curve.h1_top,
    ) <= profile.curve_cap_mult


def test_fold_pass_requires_both_controls_tim_and_actual_exit():
    evidence = {
        "actual_exit_fills": 2,
        "strategy_return_pct": 60.0,
        "bh_return_pct": 12.0,
        "same_entry_e02_return_pct": 52.0,
        "weighted_tim_pct": 75.0,
        "bars_flat_beyond_reclaim": 0,
        "future_htf_source_count": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "minimum_account_equity_usd": 9_000.0,
        "peak_notional_usd": 16_000.0,
    }
    assert joint.fold_pass(evidence)
    assert not joint.fold_pass(
        {**evidence, "strategy_return_pct": evidence["same_entry_e02_return_pct"]}
    )
    assert not joint.fold_pass({**evidence, "weighted_tim_pct": 69.99})
    assert not joint.fold_pass({**evidence, "actual_exit_fills": 0})


def test_selection_key_has_no_final_field_dependency():
    profile = joint.preregistered_grid()[0]
    evidence = [
        {
            "alpha_vs_same_entry_e02_pp": 2.0,
            "alpha_vs_bh_pp": 4.0,
            "weighted_tim_pct": 74.0,
        },
        {
            "alpha_vs_same_entry_e02_pp": 3.0,
            "alpha_vs_bh_pp": 5.0,
            "weighted_tim_pct": 76.0,
        },
    ]
    row = {
        "profile": {"label": profile.label},
        "discovery_evidence": evidence,
        "discovery_fold_gate_pass": [True, True],
        "discovery_strict": True,
    }
    key = joint._selection_key(row)
    assert key == joint._selection_key({**row, "final_evidence": object()})
