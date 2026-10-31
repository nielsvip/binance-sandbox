import numpy as np

from vec_paths.exit_tight_breakout_scorer import score_exit_tight_breakout, score_exit_tight_breakout_array


def _rows(n, seed):
    rng = np.random.default_rng(seed)
    return {"avg_entry": rng.uniform(80, 120, n), "current_price": rng.uniform(80, 120, n),
            "pnl_pct": rng.uniform(-2, 4, n), "dc_high_15m": rng.uniform(90, 115, n),
            "dc_low_15m": rng.uniform(85, 110, n), "k_1m": rng.uniform(0, 100, n),
            "k_1m_prev": rng.uniform(0, 100, n), "d_1m": rng.uniform(0, 100, n),
            "k_3m": rng.uniform(0, 100, n), "d_3m": rng.uniform(0, 100, n),
            "k_15m": rng.uniform(0, 100, n), "d_15m": rng.uniform(0, 100, n),
            "k_1h": rng.uniform(0, 100, n), "d_1h": rng.uniform(0, 100, n),
            "true_lag": rng.uniform(0, 20, n)}

def test_numpy_exit_scorer_matches_shared_native_scalar_both_sides():
    for side, is_long in (("long", True), ("short", False)):
        values = _rows(5000, 912 if is_long else 913)
        vector, masks = score_exit_tight_breakout_array(values, is_long)
        scalar = np.array([score_exit_tight_breakout(is_long=is_long, **{key: values[key][i].item() for key in values})[0] for i in range(5000)])
        np.testing.assert_array_equal(vector, scalar)
        assert vector.shape == (5000,)
        assert all(mask.shape == vector.shape for mask in masks.values())

def test_numpy_exit_scorer_thresholds_are_strict_and_priority_is_additive():
    values = {key: np.array([1.0, 1.0, 1.0, 1.0]) for key in ("avg_entry", "current_price", "pnl_pct", "dc_high_15m", "dc_low_15m", "k_1m", "k_1m_prev", "d_1m", "k_3m", "d_3m", "k_15m", "d_15m", "k_1h", "d_1h", "true_lag")}
    values.update({"avg_entry": np.array([98.5, 98.501, 1., 1.]), "dc_high_15m": np.ones(4) * 100,
                   "k_1m": np.array([50., 49., 40., 40.]), "k_1m_prev": np.array([51., 50., 41., 41.]),
                   "d_1m": np.array([50., 50., 50., 50.]), "k_3m": np.array([75., 74., 40., 40.]),
                   "d_3m": np.array([50., 50., 50., 50.]), "k_15m": np.array([40., 40., 40., 40.]),
                   "d_15m": np.array([50., 50., 50., 50.]), "k_1h": np.array([40., 40., 40., 40.]),
                   "d_1h": np.array([50., 50., 50., 50.]), "true_lag": np.array([10., 9., 9., 9.]),
                   "pnl_pct": np.array([.3, .3001, -.5, -.5001]), "current_price": np.array([99., 99., 99., 99.])})
    delta, masks = score_exit_tight_breakout_array(values, True)
    np.testing.assert_array_equal(masks["TIGHT_BREAKOUT_STOP_LONG"], [False, True, False, False])
    np.testing.assert_array_equal(masks["PROFIT_STALL"], [False, True, False, False])
    np.testing.assert_array_equal(masks["LOSS_MOM_CUT"], [False, False, False, True])
    np.testing.assert_array_equal(delta, [-5., -28., -5., -7.])
