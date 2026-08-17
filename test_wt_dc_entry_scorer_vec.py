import types

import numpy as np

from vec_paths.live_entry_engine import live_entry_engine_passes_vec
from v8_vec_sweep import _path_scoped_entry_union
from wt_dc_entry_scorer import score_entry
from wt_dc_entry_scorer_vec import score_entry_multitf_vec


def _row(arrays, index):
    result = {}
    for key, value in arrays.items():
        item = np.asarray(value)[index]
        result[key] = item.item() if isinstance(item, np.generic) else item
    return result


def test_vector_base_score_matches_production_scalar_for_both_sides():
    arrays = {
        "wt1_D": np.array([2, -2, 2, -2, 2, -2, np.nan], dtype=float),
        "wt2_D": np.zeros(7),
        "wt1_4h": np.array([2, -2, -2, 2, 2, -2, 2], dtype=float),
        "wt2_4h": np.zeros(7),
        "wt_cross_1h": np.array(
            ["BULL", "BEAR", 1, -1, 0, "", "BULL"], dtype=object
        ),
        "wt_cross_bull_1h": np.array([0, 0, 0, 0, 1, 0, 0]),
        "wt_cross_bear_1h": np.array([0, 0, 0, 0, 0, 1, 0]),
        "dc_position_1h": np.array([0.2, 0.8, 0.8, 0.2, 0.49, 0.51, 0.2]),
        "stoch_k_5m": np.array([20, 80, 80, 20, 39, 61, 20], dtype=float),
    }
    for is_long in (True, False):
        actual = score_entry_multitf_vec(arrays, is_long)
        expected = np.array(
            [score_entry(_row(arrays, i), is_long)[0] for i in range(7)]
        )
        np.testing.assert_array_equal(actual, expected)


def test_vector_base_score_is_causal_and_missing_required_fields_fail_closed():
    arrays = {
        "wt1_D": np.ones(5),
        "wt2_D": np.zeros(5),
        "wt1_4h": np.ones(5),
        "wt2_4h": np.zeros(5),
        "wt_cross_1h": np.ones(5),
        "dc_position_1h": np.full(5, 0.2),
        "stoch_k_5m": np.full(5, 20.0),
    }
    before = score_entry_multitf_vec(arrays, True)
    mutated = {key: value.copy() for key, value in arrays.items()}
    for value in mutated.values():
        value[3:] = 999
    after = score_entry_multitf_vec(mutated, True)
    np.testing.assert_array_equal(before[:3], after[:3])

    missing_daily = dict(arrays)
    missing_daily.pop("wt1_D")
    np.testing.assert_array_equal(
        score_entry_multitf_vec(missing_daily, True), np.zeros(5)
    )


def test_live_engine_threshold_uses_vector_base_score_when_engines_are_quiet():
    cfg = types.SimpleNamespace(
        LIVE_ENTRY_ENGINE_ENABLED=True,
        LIVE_ENTRY_ENGINE_WT_ENABLED=False,
        LIVE_ENTRY_ENGINE_STOCH_ENABLED=False,
        LIVE_ENTRY_ENGINE_DC_ENABLED=False,
        LIVE_ENTRY_ENGINE_HTF_ENABLED=False,
        LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED=False,
        LIVE_ENTRY_ENGINE_MIN_SCORE=0.5,
        LIVE_ENTRY_ENGINE_BOOST_SCORE=8.0,
        WT_DC_ENTRY_THRESHOLD=65.0,
    )
    assert live_entry_engine_passes_vec(
        {}, 0, "LONG", base_score=60.0, cfg=cfg, mode="tradier"
    )[0] is False
    passed, final, reasons = live_entry_engine_passes_vec(
        {}, 0, "LONG", base_score=65.0, cfg=cfg, mode="tradier"
    )
    assert passed is True
    assert final == 65.0
    assert reasons == []


def test_high_wt_dc_threshold_cannot_veto_an_independent_entry_path():
    any_path, wt_dc_path = _path_scoped_entry_union(
        non_wt_dc_trigger=True,
        wt_dc_score=0.0,
        wt_dc_threshold=65.0,
        wt_dc_path_enabled=True,
        wt_dc_htf_block=False,
    )
    assert any_path is True
    assert wt_dc_path is False

    any_path, wt_dc_path = _path_scoped_entry_union(
        non_wt_dc_trigger=False,
        wt_dc_score=60.0,
        wt_dc_threshold=65.0,
        wt_dc_path_enabled=True,
        wt_dc_htf_block=False,
    )
    assert (any_path, wt_dc_path) == (False, False)

    any_path, wt_dc_path = _path_scoped_entry_union(
        non_wt_dc_trigger=False,
        wt_dc_score=65.0,
        wt_dc_threshold=65.0,
        wt_dc_path_enabled=True,
        wt_dc_htf_block=False,
    )
    assert (any_path, wt_dc_path) == (True, True)
