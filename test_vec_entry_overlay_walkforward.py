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
