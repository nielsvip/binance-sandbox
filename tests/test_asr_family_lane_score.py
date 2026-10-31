from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_family_lane_score import apply_family_lane_score_array, family_lane_masks_array


def _cfg():
    return SimpleNamespace(
        MOM3_ENTRY_ENABLED=True, MOM3_LONG_THRESHOLD=-1.0, MOM3_SHORT_THRESHOLD=1.0,
        STRUCTURAL_RANGE_SHIFT_EXIT=True, STRUCTURAL_RANGE_SHIFT_TF="bb_1h",
        REENTRY_B15_STRONG_TREND_ENABLED=True, REENTRY_B14_HA_TREND_ENABLED=True,
        REENTRY_B12_WT_MOM_ENABLED=True, K_ZONE_ENTRY_ENABLED=True,
        K_ZONE_LONG_THRESHOLD=35.0, K_ZONE_SHORT_THRESHOLD=65.0,
    )


def test_family_masks_match_actual_native_family_lane_tags_both_sides():
    from ez_positions_quick import _family_lane_tags
    rng = np.random.default_rng(901)
    n = 300
    values = {
        "close": rng.uniform(80.0, 120.0, n),
        "close_15m": rng.uniform(80.0, 120.0, n),
        "close_3bar_15m": rng.uniform(80.0, 120.0, n),
        "bb_pct_b_1h": rng.uniform(0.0, 1.0, n),
        "dc_high_4h": rng.uniform(90.0, 110.0, n),
        "dc_low_4h": rng.uniform(90.0, 110.0, n),
        "wt_velocity_1h": rng.uniform(-4.0, 4.0, n),
        "wt_velocity_15m": rng.uniform(-4.0, 4.0, n),
        "stoch_k_1h": rng.uniform(0.0, 100.0, n),
        "k_15m": rng.uniform(0.0, 100.0, n),
        "ha_15m": rng.choice(["green", "red", "neutral"], n),
        "ha_1h": rng.choice(["green", "red", "neutral"], n),
        "wt1_15m": rng.uniform(-5.0, 5.0, n),
        "wt2_15m": rng.uniform(-5.0, 5.0, n),
        "wt1_1h": rng.uniform(-5.0, 5.0, n),
        "wt2_1h": rng.uniform(-5.0, 5.0, n),
    }
    cfg = _cfg()
    get = lambda key, default: getattr(cfg, key, default)
    for is_long in (True, False):
        masks = family_lane_masks_array(values, values["close"], is_long, cfg)
        for i in range(n):
            ind = {key: arr[i] for key, arr in values.items()}
            tags = set(_family_lane_tags(ind, float(values["close"][i]), is_long, get))
            assert masks["B_MOM3_FAMILY"][i] == ("B_MOM3_FAMILY" in tags)
            assert masks["B_SRS_ENTRY_FAMILY"][i] == ("B_SRS_ENTRY_FAMILY" in tags)
            assert masks["B15_FAMILY"][i] == ("B15_FAMILY" in tags)
            assert masks["B14_FAMILY"][i] == ("B14_FAMILY" in tags)
            assert masks["B12_FAMILY"][i] == ("B12_FAMILY" in tags)
            assert masks["B_KZONE_FAMILY"][i] == ("B_KZONE_FAMILY" in tags)


def test_family_score_is_plus_35_for_every_native_tag():
    cfg = _cfg()
    values = {
        "close": np.array([100.0]), "close_15m": np.array([100.0]),
        "close_3bar_15m": np.array([102.0]), "bb_pct_b_1h": np.array([0.5]),
        "dc_high_4h": np.array([90.0]), "dc_low_4h": np.array([110.0]),
        "wt_velocity_1h": np.array([3.0]), "wt_velocity_15m": np.array([3.0]),
        "stoch_k_1h": np.array([50.0]), "k_15m": np.array([50.0]),
        "ha_15m": np.array(["green"], object), "ha_1h": np.array(["green"], object),
        "wt1_15m": np.array([2.0]), "wt2_15m": np.array([1.0]),
        "wt1_1h": np.array([2.0]), "wt2_1h": np.array([1.0]),
    }
    masks = family_lane_masks_array(values, values["close"], True, cfg)
    score, applied = apply_family_lane_score_array(values, values["close"], True, cfg, [10.0])
    assert all(np.array_equal(applied[key], masks[key]) for key in masks)
    assert score[0] == 10.0 + 35.0 * sum(bool(m[0]) for m in masks.values())
    assert applied["FAMILY_LANE_COUNT"][0] == sum(bool(m[0]) for m in masks.values())
    assert applied["B_SRS_ENTRY_FAMILY"][0]
