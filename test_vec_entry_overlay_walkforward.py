from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import vec_entry_overlay_walkforward as overlay
import audit_long_wait_wiring as long_wait_wiring


def test_episode_starts_only_fire_once_per_contiguous_signal():
    mask = np.array([False, True, True, False, True, False])
    assert overlay._episode_starts(mask).tolist() == [
        False,
        True,
        False,
        False,
        True,
        False,
    ]


def test_filter_and_direct_are_distinct_and_keep_sizing():
    control = type("C", (), {"entry_mult": np.array([0.0, 2.0, 0.0, 4.0])})()
    mask = np.array([True, True, True, False])
    direct = np.array([1.0, 2.0, 3.0, 4.0])
    assert overlay._entry_mult(mask, control, direct, "filter").tolist() == [
        0.0,
        2.0,
        0.0,
        0.0,
    ]
    assert overlay._entry_mult(mask, control, direct, "direct").tolist() == [
        1.0,
        0.0,
        0.0,
        0.0,
    ]


def test_gr_and_wt_grids_keep_roles_and_side_independent_params():
    gr = overlay._gr_candidates()
    wt = overlay._wt_candidates()
    assert {row.role for row in gr} == {"filter", "direct"}
    assert {row.role for row in wt} == {"filter", "direct"}
    assert len({row.label for row in gr}) == len(gr)
    assert len({row.label for row in wt}) == len(wt)


def test_stoch_grid_covers_thresholds_tfs_confirmations_and_roles():
    rows = overlay._stoch_candidates()
    assert {row.role for row in rows} == {"direct", "union-with-green"}
    assert {row.params["stoch_threshold"] for row in rows} == {
        15.0,
        20.0,
        25.0,
        30.0,
        35.0,
        40.0,
    }
    assert any(
        row.params["enabled_tfs"] == ["1h", "4h", "D"]
        and row.params["min_confirming_tfs"] == 2
        for row in rows
    )


def test_stoch_union_honors_target_and_add_semantics_with_capacity():
    candidate = overlay._candidate(
        "ENTRY_STOCH_HHHL",
        "union-with-green",
        stoch_threshold=20.0,
        enabled_tfs=["1h"],
        min_confirming_tfs=1,
    )
    mask = np.array([True, False, False])
    direct = np.array([3.0, 0.0, 0.0])
    control = type("C", (), {"entry_mult": np.zeros(3)})()
    green = type("G", (), {"entry_mult": np.array([2.0, 1.0, 0.0])})()
    target = type("Curve", (), {"semantics": "target"})()
    add = type("Curve", (), {"semantics": "add"})()
    assert overlay._candidate_entry_mult(
        candidate, mask, control, direct, target, green
    ).tolist() == [3.0, 1.0, 0.0]
    assert overlay._candidate_entry_mult(
        candidate, mask, control, direct, add, green
    ).tolist() == [5.0, 1.0, 0.0]


def test_bb_recovery_grid_covers_requested_ranges():
    rows = overlay._bb_recovery_candidates()
    assert len(rows) == 96
    assert {row.role for row in rows} == {"direct", "union-with-green"}
    assert {row.params["timeframe"] for row in rows} == {"15m", "1h", "4h"}
    assert {row.params["recovery_bars"] for row in rows} == {1, 2, 4, 8}
    assert {row.params["min_excursion_atr"] for row in rows} == {
        0.0,
        0.25,
        0.5,
        1.0,
    }


def test_failed_bb_recovery_is_side_mirrored_and_respects_excursion_deadline():
    upper = np.full(5, 110.0)
    lower = np.full(5, 90.0)
    atr = np.full(5, 2.0)
    valid = np.ones(5, dtype=bool)
    long_close = np.array([100.0, 88.0, 89.0, 91.0, 100.0])
    # 88 is one ATR below 90; recovery on the second later bar.
    assert overlay._failed_bb_recovery_events(
        long_close, upper, lower, atr, valid, "LONG", 2, 1.0
    ).tolist() == [False, False, False, True, False]
    assert not overlay._failed_bb_recovery_events(
        long_close, upper, lower, atr, valid, "LONG", 1, 1.0
    ).any()
    short_close = np.array([100.0, 112.0, 111.0, 109.0, 100.0])
    assert overlay._failed_bb_recovery_events(
        short_close, upper, lower, atr, valid, "SHORT", 2, 1.0
    ).tolist() == [False, False, False, True, False]


def test_delta_grid_is_direct_only_and_requested_dimensions_are_complete():
    rows = overlay._delta_candidates()
    assert len(rows) == 24
    assert {row.role for row in rows} == {"direct"}
    assert {row.params["min_favorable_tfs"] for row in rows} == {1, 2, 3, 4}
    assert {row.params["directional_retention_ratio"] for row in rows} == {
        0.25,
        0.5,
        0.75,
    }
    assert {row.params["structural_gate"] for row in rows} == {False, True}


def test_delta_speed_is_exact_side_mirror():
    velocity = np.array([2.0, -3.0, 0.0])
    acceleration = np.array([4.0, -2.0, 6.0])
    long_fav, long_opp = overlay._delta_side_speeds(
        velocity, acceleration, "LONG"
    )
    short_fav, short_opp = overlay._delta_side_speeds(
        velocity, acceleration, "SHORT"
    )
    assert long_fav.tolist() == short_opp.tolist()
    assert long_opp.tolist() == short_fav.tolist()


def test_dc_break_grid_covers_production_ranges_and_labeled_extensions():
    rows = overlay._dc_break_candidates()
    assert len(rows) == 192
    assert {row.role for row in rows} == {"direct", "union-with-green"}
    assert {row.params["timeframe"] for row in rows} == {
        "5m",
        "15m",
        "1h",
        "4h",
    }
    assert {row.params["buffer_fraction"] for row in rows} == {
        0.0,
        0.0005,
        0.001,
        0.002,
    }
    assert {row.params["require_1h_expansion"] for row in rows} == {False, True}
    assert {row.params["confirmation"] for row in rows} == {
        "none",
        "not-exhausted",
        "directional-stoch",
    }


def test_dc_break_mask_uses_prior_channel_and_is_exact_side_mirror():
    view = {
        "close": np.array([100.0, 101.0, 99.0, 98.0]),
        "stoch_k_5m": np.array([50.0, 60.0, 40.0, 30.0]),
        "stoch_d_5m": np.array([50.0, 50.0, 50.0, 40.0]),
        "stoch_k_15m": np.array([50.0, 60.0, 40.0, 30.0]),
        "stoch_d_15m": np.array([50.0, 50.0, 50.0, 40.0]),
        "dc_high_5m_prev": np.full(4, 100.0),
        "dc_low_5m_prev": np.full(4, 100.0),
        "dc_high_1h": np.array([100.0, 101.0, 101.0, 101.0]),
        "dc_high_1h_ant": np.full(4, 100.0),
        "dc_low_1h": np.array([100.0, 99.0, 99.0, 99.0]),
        "dc_low_1h_ant": np.full(4, 100.0),
    }
    long_masks = overlay._dc_break_masks(view, "LONG")
    short_masks = overlay._dc_break_masks(view, "SHORT")
    key = ("5m", 0.0, True, "directional-stoch")
    assert long_masks[key].tolist() == [False, True, False, False]
    assert short_masks[key].tolist() == [False, False, True, True]


def test_long_wait_reconstruction_grid_is_complete_and_side_mirrored():
    rows = overlay._long_wait_candidates()
    assert len(rows) == 1024
    assert {row.role for row in rows} == {"direct", "union-with-green"}
    view = {
        "close": np.array([101.0, 101.0]),
        "dc_low_5m": np.array([100.0, 100.0]),
        "dc_high_5m": np.array([102.0, 102.0]),
        "stoch_k_4h": np.array([30.0, 70.0]),
        "stoch_k_1h": np.array([30.0, 70.0]),
        "stoch_d_1h": np.array([20.0, 80.0]),
        "stoch_k_5m": np.array([30.0, 70.0]),
        "stoch_d_5m": np.array([20.0, 80.0]),
        "stoch_k_15m": np.array([30.0, 70.0]),
        "stoch_d_15m": np.array([20.0, 80.0]),
        "wt1_15m": np.array([2.0, -2.0]),
        "wt2_15m": np.array([1.0, -1.0]),
    }
    key = ("5m", 0.015, 50.0, 40.0, "two-of-three")
    assert overlay._long_wait_masks(view, "LONG")[key].tolist() == [True, False]
    assert overlay._long_wait_masks(view, "SHORT")[key].tolist() == [False, True]


def test_long_wait_switch_is_phantom_but_removed_path_is_historically_proven():
    result = long_wait_wiring.audit_repo()
    assert result["classification"] == "PHANTOM_SWITCH_REMOVED_HISTORICAL_SCORE_PATH"
    assert not result["switch_declared"]
    assert not result["switch_read"]
    assert not result["active_reason_present"]
    assert result["removed_score_reasons_proven"]
    assert result["historical_fill_rows"] > 0


def test_removed_bounce_reasons_are_separate_source_traceable_grids():
    bounce15 = overlay._bounce_candidates("ENTRY_BOUNCE_15M_LOW", "15m")
    bounce5 = overlay._bounce_candidates("ENTRY_BOUNCE_5M_LOW", "5m")
    assert len(bounce15) == len(bounce5) == 64
    assert {row.params["timeframe"] for row in bounce15} == {"15m"}
    assert {row.params["timeframe"] for row in bounce5} == {"5m"}
    assert any(
        row.params["distance"] == 0.015
        and not row.params["recovery_only"]
        and row.params["confirmation"] == "none"
        for row in bounce15
    )
    assert any(
        row.params["distance"] == 0.008
        and not row.params["recovery_only"]
        and row.params["confirmation"] == "none"
        for row in bounce5
    )


def test_bounce_masks_use_prior_channel_and_mirror_side():
    view = {
        "close": np.array([101.0, 99.0]),
        "dc_low_5m_prev": np.array([100.0, 100.0]),
        "dc_high_5m_prev": np.array([100.0, 100.0]),
        "stoch_k_5m": np.array([60.0, 40.0]),
        "stoch_d_5m": np.array([50.0, 50.0]),
        "stoch_k_15m": np.array([60.0, 40.0]),
        "stoch_d_15m": np.array([50.0, 50.0]),
    }
    source = ("5m", 0.015, False, "none")
    recovered = ("5m", 0.015, True, "none")
    assert overlay._bounce_masks(view, "LONG")[source].tolist() == [True, True]
    assert overlay._bounce_masks(view, "LONG")[recovered].tolist() == [True, False]
    assert overlay._bounce_masks(view, "SHORT")[source].tolist() == [True, True]
    assert overlay._bounce_masks(view, "SHORT")[recovered].tolist() == [False, True]


def test_removed_deep_value_and_turn_reasons_are_separate_mirrored_grids():
    assert len(overlay._deep_value_candidates()) == 8
    assert len(overlay._turn_1h_candidates()) == 24
    view = {
        "close": np.ones(4),
        "stoch_k_4h": np.array([30.0, 70.0, 55.0, 45.0]),
        "stoch_k_1h": np.array([30.0, 35.0, 70.0, 65.0]),
        "stoch_d_1h": np.array([40.0, 30.0, 60.0, 70.0]),
    }
    long_masks = overlay._score_reason_masks(view, "LONG")
    short_masks = overlay._score_reason_masks(view, "SHORT")
    assert long_masks[("4h-deep", 50.0, "source")].tolist() == [
        True, False, False, True
    ]
    assert short_masks[("4h-deep", 50.0, "source")].tolist() == [
        False, True, True, False
    ]
    assert long_masks[("1h-turn", 40.0, "rising-vs-prior")].tolist() == [
        False, True, False, False
    ]
    assert short_masks[("1h-turn", 40.0, "rising-vs-prior")].tolist() == [
        False, False, False, True
    ]
