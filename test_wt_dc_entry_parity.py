import numpy as np

from vec_paths.wt_dc_entry import compute_wt_dc_entry_vec, score_entry_multitf_vec
from wt_dc_entry_scorer import score_entry_multitf


def _fixture(cross):
    return {
        "wt1_D": 10.0,
        "wt2_D": 0.0,
        "wt1_4h": 10.0,
        "wt2_4h": 0.0,
        "wt1_1h": 10.0,
        "wt2_1h": 0.0,
        "wt_cross_1h": cross,
        "dc_position_1h": 0.25,
        "stoch_k_5m": 25.0,
        "k_5m": 25.0,
    }


def _npz(rows):
    keys = rows[0]
    return {k: np.asarray([row[k] for row in rows]) for k in keys}


def test_numeric_and_string_cross_encodings_score_identically():
    for cross in ("BULL", 1, 1.0, "1"):
        score, reason = score_entry_multitf(_fixture(cross), True)
        assert score == 100.0
        assert "1h_cross_BULL" in reason
    for cross in ("BEAR", -1, -1.0, "-1"):
        row = _fixture(cross)
        row.update(
            wt1_D=-10.0,
            wt2_D=0.0,
            wt1_4h=-10.0,
            wt2_4h=0.0,
            wt1_1h=-10.0,
            wt2_1h=0.0,
            dc_position_1h=0.75,
            stoch_k_5m=75.0,
            k_5m=75.0,
        )
        score, reason = score_entry_multitf(row, False)
        assert score == 100.0
        assert "1h_cross_BEAR" in reason


def test_vector_score_matches_scalar_on_signed_npz_rows():
    rows = []
    for cross, d_bull, h4_bull, dc, k in [
        (1, True, True, 0.25, 20.0),
        (0, True, False, 0.75, 80.0),
        (-1, False, False, 0.75, 80.0),
        (0, False, True, 0.25, 20.0),
    ]:
        rows.append(
            {
                "wt1_D": 1.0 if d_bull else -1.0,
                "wt2_D": 0.0,
                "wt1_4h": 1.0 if h4_bull else -1.0,
                "wt2_4h": 0.0,
                "wt1_1h": 1.0 if d_bull else -1.0,
                "wt2_1h": 0.0,
                "wt_cross_1h": cross,
                "dc_position_1h": dc,
                "stoch_k_5m": k,
                "k_5m": k,
            }
        )
    data = _npz(rows)
    for is_long in (True, False):
        vec = score_entry_multitf_vec(data, len(rows), is_long)
        scalar = np.asarray(
            [score_entry_multitf(row, is_long)[0] for row in rows],
            dtype=np.float32,
        )
        np.testing.assert_array_equal(vec, scalar)


def test_path_gates_are_side_isolated_and_threshold_is_not_an_alias():
    long = _fixture(1)
    short = {
        **_fixture(-1),
        "wt1_D": -10.0,
        "wt1_4h": -10.0,
        "wt1_1h": -10.0,
        "dc_position_1h": 0.75,
        "stoch_k_5m": 75.0,
        "k_5m": 75.0,
    }
    data = _npz([long, short])
    long_mask, score_l = compute_wt_dc_entry_vec(
        data, 2, True, threshold=75, htf_gate="4h_D", htf_align_required=2
    )
    short_mask, score_s = compute_wt_dc_entry_vec(
        data, 2, False, threshold=75, htf_gate="4h_D", htf_align_required=2
    )
    np.testing.assert_array_equal(long_mask, [True, False])
    np.testing.assert_array_equal(short_mask, [False, True])
    np.testing.assert_array_equal(score_l, [100, 0])
    np.testing.assert_array_equal(score_s, [0, 100])


def test_path_enabled_switch_really_disables_only_wt_dc_mask():
    data = _npz([_fixture(1)])
    enabled, _ = compute_wt_dc_entry_vec(data, 1, True, threshold=45)
    disabled, _ = compute_wt_dc_entry_vec(
        data, 1, True, threshold=45, path_enabled=False
    )
    assert enabled.tolist() == [True]
    assert disabled.tolist() == [False]
