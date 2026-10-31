import numpy as np

from vec_decisions.asr_score_core import apply_score_prefix_scalar, apply_score_prefix_vec


class Cfg:
    WT_COMPOSITE_SCORING_ENABLED = True
    WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
    WT_COMPOSITE_ENTRY_STRONG = 50
    WT_COMPOSITE_ENTRY_GOOD = 30
    WT_COMPOSITE_ENTRY_OK = 10
    R_S1_WT_COMPOSITE_DELTA_USE_ENABLED = True
    R_S1_WT_COMPOSITE_DELTA_THR = 50
    R_S2_WT_ADAPTIVE_OS_ENABLED = True
    R_S2_WT_PCT_OS_LONG = 10
    R_S2_WT_PCT_OB_SHORT = 90
    DC_MOMENT_ENABLED = True
    DC_MOMENT_STRONG_THRESHOLD = 40
    DC_MOMENT_STRONG_BONUS = 10
    DC_MOMENT_OPPOSITE_PENALTY = -15
    WT_COMPOSITE_DELTA_SCORE_ENABLED = True
    WT_COMPOSITE_DELTA_SCORE_THRESHOLD = 50
    WT_COMPOSITE_DELTA_SCORE_BONUS = 3


def _indicator_row(arrays, i):
    return {key: np.asarray(value)[i].item() for key, value in arrays.items()}


def test_ordered_prefix_scalar_matches_vector_both_sides_and_boundaries():
    arrays = {
        "wt_composite_long": np.array([50, 30, 10, np.nan, 49]),
        "wt_composite_short": np.array([50, 30, 10, np.nan, 51]),
        "wt_oversold_tf_count": np.array([2, 0, 3, 2, 1]),
        "wt_overbought_tf_count": np.array([0, 2, 3, 2, 1]),
        "wt_hl_count": np.array([2, 2, 0, 2, 2]),
        "wt_lh_count": np.array([0, 2, 3, 2, 2]),
        "wt_composite_delta": np.array([51, -51, 50, np.nan, -60]),
        "wt_percentile_15m": np.array([9, 91, 10, np.nan, 95]),
        "wt_any_bull_div": np.array([1, 0, 0, 0, 1]),
        "wt_any_bear_div": np.array([0, 1, 0, 0, 1]),
        "wt_bull_cross_count": np.array([2, 1, 2, 3, 0]),
        "wt_bear_cross_count": np.array([1, 2, 2, 3, 0]),
        "0dc_moment": np.array([41, -41, 40, np.nan, -70]),
    }
    for is_long in (True, False):
        vector, _ = apply_score_prefix_vec(arrays, 5, is_long, Cfg)
        for i in range(5):
            scalar, _ = apply_score_prefix_scalar(
                0, [], _indicator_row(arrays, i), {}, is_long, Cfg, True,
                _indicator_row(arrays, i).get("wt_bull_cross_count", 0),
                _indicator_row(arrays, i).get("wt_bear_cross_count", 0),
            )
            assert vector[i] == scalar


def test_scalar_and_vector_preserve_metric_fallback_and_reason_order():
    scalar, reasons = apply_score_prefix_scalar(
        1.5,
        [],
        {"wt_composite_long": 50, "wt_bull_cross_count": 2},
        {"0dc_moment": 41},
        True,
        Cfg,
        True,
        2,
        0,
    )
    vector, _ = apply_score_prefix_vec(
        {"wt_composite_long": np.array([50]), "wt_bull_cross_count": np.array([2])},
        1, True, Cfg, score=np.array([1.5]), metrics={"0dc_moment": np.array([41])}
    )
    assert vector[0] == scalar
    assert scalar == 1.5 + 8 + 10 + 3
    assert reasons == [
        "WT_STRONG(L=50,+8)",
        "WT_MULTI_BULL_X(2,+3)",
        "DC_MOMENT_STRONG_L(41,+10)",
    ]
