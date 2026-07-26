from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import vec_entry_overlay_walkforward as overlay


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
