import numpy as np

from ez_trading_policy import check_htf_trend
from vec_decisions.asr_htf_trend import check_htf_trend_array


def test_array_matches_native_htf_trend_for_random_and_boundary_rows():
    rng = np.random.default_rng(417)
    n = 600
    inputs = {}
    for tf in ("1h", "4h", "D"):
        inputs[f"stoch_k_{tf}"] = rng.uniform(0, 100, n)
        inputs[f"stoch_d_{tf}"] = rng.uniform(0, 100, n)
        inputs[f"ha_{tf}"] = rng.choice(["green", "red", "neutral", "unknown"], n)
    for key in ("dc_basis_1h", "dc_basis_4h", "sma_200_1h", "high_1h", "high_1h_prev", "low_1h", "low_1h_prev", "high_4h", "high_4h_prev", "low_4h", "low_4h_prev"):
        inputs[key] = rng.uniform(0, 250, n)
    inputs["stoch_k_1h"][0] = np.nan
    inputs["ha_1h"][1] = "green"
    inputs["high_1h"][2] = inputs["high_1h_prev"][2]
    inputs["low_1h"][2] = inputs["low_1h_prev"][2]
    price = rng.uniform(0, 300, n)
    direction, score = check_htf_trend_array(inputs, price)
    expected = [check_htf_trend({key: value[i] for key, value in inputs.items()}, price[i]) for i in range(n)]
    assert direction.tolist() == [row[0] for row in expected]
    assert score.tolist() == [row[1] for row in expected]


def test_npz_aliases_and_native_missing_defaults_match_native_scalar():
    inputs = {
        "k_1h": np.array([60.0, np.nan]), "d_1h": np.array([50.0, 50.0]),
        "k_4h": np.array([60.0, 50.0]), "d_4h": np.array([50.0, 50.0]),
        "k_D": np.array([60.0, 50.0]), "d_D": np.array([50.0, 50.0]),
        "ha_1h": np.array(["green", "neutral"], dtype=object),
        "ha_4h": np.array(["green", "neutral"], dtype=object),
        "ha_D": np.array(["green", "neutral"], dtype=object),
    }
    price = np.array([100.0, 100.0])
    direction, score = check_htf_trend_array(inputs, price)
    native_inputs = [
        {"stoch_k_1h": 60.0, "stoch_d_1h": 50.0, "stoch_k_4h": 60.0, "stoch_d_4h": 50.0,
         "stoch_k_D": 60.0, "stoch_d_D": 50.0, "ha_1h": "green", "ha_4h": "green", "ha_D": "green"},
        {"stoch_k_1h": np.nan, "stoch_d_1h": 50.0, "stoch_k_4h": 50.0, "stoch_d_4h": 50.0,
         "stoch_k_D": 50.0, "stoch_d_D": 50.0, "ha_1h": "neutral", "ha_4h": "neutral", "ha_D": "neutral"},
    ]
    expected = [check_htf_trend(row, px) for row, px in zip(native_inputs, price)]
    assert direction.tolist() == [row[0] for row in expected]
    assert score.tolist() == [row[1] for row in expected]
