import numpy as np

from vec_decisions.ez_missing_b04_retest import dc_high_break_retest_mask


class Config:
    REENTRY_B04_DC_RETEST_ENABLED = True


def _safe(npz, key, n, default):
    value = npz.get(key)
    if value is None or len(value) != n:
        return np.full(n, default)
    return np.asarray(value)


def test_b04_predicate_switch_and_side_masks():
    long_npz = {
        "close": np.array([100.2, 98.9]),
        "dc_high_4h": np.array([102.0, 102.0]),
        "dc_high_4h_ant": np.array([100.0, 100.0]),
        "stoch_k_15m": np.array([62.0, 62.0]),
        "stoch_d_15m": np.array([58.0, 58.0]),
        "stoch_k_15m_prev": np.array([60.0, 60.0]),
    }
    short_npz = {
        "close": np.array([99.8, 101.1]),
        "dc_low_1h": np.array([98.0, 98.0]),
        "dc_low_1h_ant": np.array([100.0, 100.0]),
        "stoch_k_15m": np.array([38.0, 38.0]),
        "stoch_d_15m": np.array([42.0, 42.0]),
        "stoch_k_15m_prev": np.array([40.0, 40.0]),
    }
    cfg = Config()
    long_mask, long_mult, long_tf = dc_high_break_retest_mask(long_npz, 2, True, cfg, _safe)
    short_mask, short_mult, short_tf = dc_high_break_retest_mask(short_npz, 2, False, cfg, _safe)
    assert np.flatnonzero(long_mask).tolist() == [0]
    assert np.flatnonzero(short_mask).tolist() == [0]
    assert long_mult[0] == 2.0 and long_tf[0] == "4h"
    assert short_mult[0] == 1.5 and short_tf[0] == "1h"
    cfg.REENTRY_B04_DC_RETEST_ENABLED = False
    assert not dc_high_break_retest_mask(long_npz, 2, True, cfg, _safe)[0].any()
    assert not dc_high_break_retest_mask(short_npz, 2, False, cfg, _safe)[0].any()


def test_b04_uses_priority_first_match_and_missing_inputs_fail_open():
    npz = {
        "close": np.array([100.2]),
        "dc_high_4h": np.array([102.0]),
        "dc_high_4h_ant": np.array([100.0]),
        "dc_high_1h": np.array([103.0]),
        "dc_high_1h_ant": np.array([100.0]),
        "stoch_k_15m": np.array([62.0]),
        "stoch_d_15m": np.array([58.0]),
        "stoch_k_15m_prev": np.array([60.0]),
    }
    mask, mult, tf = dc_high_break_retest_mask(npz, 1, True, Config(), _safe)
    assert mask.tolist() == [True]
    assert mult.tolist() == [2.0]
    assert tf.tolist() == ["4h"]
    missing = {"close": np.array([100.2]), "dc_high_4h": np.array([102.0]), "dc_high_4h_ant": np.array([100.0])}
    assert not dc_high_break_retest_mask(missing, 1, True, Config(), _safe)[0].any()


def test_b04_matches_live_helper_when_ltf_stochastic_is_mapped_to_15m():
    from ez_manage import check_dc_high_break_retest

    cases = [
        (True, 100.2, 102.0, 100.0, 62.0, 58.0, 60.0),
        (False, 99.8, 98.0, 100.0, 38.0, 42.0, 40.0),
        (True, 103.0, 102.0, 100.0, 62.0, 58.0, 60.0),
    ]
    cfg = Config()
    for is_long, price, current, previous, k, d, k_prev in cases:
        npz = {
            "close": np.array([price]),
            "dc_high_4h": np.array([current]),
            "dc_high_4h_ant": np.array([previous]),
            "dc_low_4h": np.array([current]),
            "dc_low_4h_ant": np.array([previous]),
            "stoch_k_15m": np.array([k]),
            "stoch_d_15m": np.array([d]),
            "stoch_k_15m_prev": np.array([k_prev]),
        }
        indicators = {
            "k_3m": k,
            "d_3m": d,
            "k_3m_prev": k_prev,
            "dc_high_4h": current,
            "dc_high_4h_ant": previous,
            "dc_low_4h": current,
            "dc_low_4h_ant": previous,
        }
        live_fires, _, live_mult = check_dc_high_break_retest(indicators, price, is_long)
        vec_mask, vec_mult, _ = dc_high_break_retest_mask(npz, 1, is_long, cfg, _safe)
        assert bool(vec_mask[0]) == live_fires
        assert float(vec_mult[0]) == live_mult


def test_b04_uses_only_prior_bar_when_explicit_stochastic_previous_is_absent():
    npz = {
        "close": np.array([100.2, 100.3, 100.4]),
        "dc_high_4h": np.array([102.0, 102.0, 102.0]),
        "dc_high_4h_ant": np.array([100.0, 100.0, 100.0]),
        "stoch_k_15m": np.array([60.0, 62.0, 59.0]),
        "stoch_d_15m": np.array([58.0, 58.0, 58.0]),
    }
    mask, _, _ = dc_high_break_retest_mask(npz, 3, True, Config(), _safe)
    assert mask.tolist() == [False, True, False]
