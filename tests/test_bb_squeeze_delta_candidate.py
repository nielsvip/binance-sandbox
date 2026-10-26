import numpy as np

from vec_decisions.bb_squeeze_delta_candidate import (
    bb_squeeze_threshold_candidates,
    bb_squeeze_width_ratio,
    delta_gate_bb_squeeze_candidate_masks,
)


def _npz(width_15m=0.02, width_1h=0.04, n=3):
    mid = np.full(n, 100.0)
    data = {}
    for tf, width in (("15m", width_15m), ("1h", width_1h)):
        half = mid * width / 2.0
        data[f"bb_upper_{tf}"] = mid + half
        data[f"bb_lower_{tf}"] = mid - half
    return data


def test_width_ratio_uses_band_width_over_midpoint():
    got = bb_squeeze_width_ratio(_npz(width_15m=0.025), 3, "15m")
    np.testing.assert_allclose(got, [0.025] * 3)


def test_threshold_candidates_are_independent_and_strict():
    masks = bb_squeeze_threshold_candidates(
        _npz(width_15m=0.024, width_1h=0.03),
        3,
        {"BB_SQUEEZE_THRESHOLD_15M": 0.025, "BB_SQUEEZE_THRESHOLD_1H": 0.03},
    )
    np.testing.assert_array_equal(masks["15m"], [True] * 3)
    np.testing.assert_array_equal(masks["1h"], [False] * 3)


def test_candidate_veto_requires_real_delta_confirmation():
    cfg = {
        "DELTA_GATE_BB_SQUEEZE": True,
        "BB_SQUEEZE_THRESHOLD_15M": 0.025,
        "BB_SQUEEZE_THRESHOLD_1H": 0.03,
    }
    assert delta_gate_bb_squeeze_candidate_masks(_npz(), 3, cfg)["15m"] is None
    got = delta_gate_bb_squeeze_candidate_masks(
        _npz(), 3, cfg, delta_confirmed=np.array([False, True, False])
    )
    np.testing.assert_array_equal(got["15m"], [True, False, True])
    np.testing.assert_array_equal(got["1h"], [False, False, False])


def test_missing_band_data_returns_none_without_fabricated_mask():
    assert bb_squeeze_width_ratio({}, 3, "15m") is None
    masks = bb_squeeze_threshold_candidates({}, 3, {})
    assert masks == {"15m": None, "1h": None}


def test_disabled_gate_is_no_change():
    cfg = {"DELTA_GATE_BB_SQUEEZE": False}
    got = delta_gate_bb_squeeze_candidate_masks(_npz(), 3, cfg, [False] * 3)
    assert got == {"15m": None, "1h": None}
