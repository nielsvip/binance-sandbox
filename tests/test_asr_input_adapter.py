import numpy as np

from vec_decisions.asr_input_adapter import paired_indicator_view, paired_npz_view, selected_timeframe


def test_off_policy_overwrites_contradictory_micro_values_with_real_15m_peers_without_mutation():
    indicators = {"k_3m": 3.0, "k_3m_prev": 2.0, "k_15m": 77.0, "k_15m_prev": 70.0,
                  "wt1_3m": -5.0, "wt1_15m": 25.0, "dc_high4_1m": 1.0, "dc_high4_15m": 20.0,
                  "rsi_1h": 61.0}
    metrics = {"k_1m": 1.0, "k_1m_prev": .5, "k_15m": 77.0, "k_15m_prev": 70.0, "wt_velocity_3m": -4.0,
               "wt_velocity_15m": 11.0, "high_5m": 2.0}
    ind_view, metrics_view, missing = paired_indicator_view(indicators, metrics, False)
    assert not missing
    assert ind_view["k_3m"] == 77.0 and ind_view["k_3m_prev"] == 70.0
    assert ind_view["wt1_3m"] == 25.0 and ind_view["dc_high4_1m"] == 20.0
    assert metrics_view["k_1m"] == 77.0 and metrics_view["k_1m_prev"] == 70.0
    assert metrics_view["wt_velocity_3m"] == 11.0
    assert "high_5m" not in metrics_view
    assert indicators["k_3m"] == 3.0 and metrics["k_1m"] == 1.0
    assert ind_view["rsi_1h"] == 61.0


def test_on_policy_keeps_actual_micro_values_and_reports_missing_required_fields():
    indicators = {"k_3m": 3.0, "k_15m": 77.0}
    ind_view, metrics_view, missing = paired_indicator_view(indicators, {}, True)
    assert ind_view["k_3m"] == 3.0 and ind_view["k_15m"] == 77.0
    assert metrics_view == {}
    assert set(missing) == {"k_1m", "k_1m_prev", "d_1m", "d_3m", "k_3m_prev", "wt1_3m", "wt2_3m", "dc_basis_3m", "dc_high_3m", "dc_low_3m"}


def test_array_hydration_and_dynamic_timeframe_selection_use_same_policy():
    arrays = {"k_3m": np.array([3.0]), "k_15m": np.array([77.0]), "k_3m_prev": np.array([2.0]),
              "k_15m_prev": np.array([70.0]), "wt1_5m": np.array([5.0]), "wt1_15m": np.array([25.0]),
              "dc_high_1m": np.array([1.0]), "dc_high_15m": np.array([20.0]), "close": np.array([10.0])}
    paired = paired_npz_view(arrays, False)
    assert paired["k_3m"].tolist() == [77.0]
    assert paired["k_3m_prev"].tolist() == [70.0]
    assert paired["wt1_5m"].tolist() == [25.0]
    assert paired["dc_high_1m"].tolist() == [20.0]
    assert paired["close"].tolist() == [10.0]
    assert paired_npz_view(arrays, True)["k_3m"].tolist() == [3.0]
    assert selected_timeframe("1m", False) == "15m"
    assert selected_timeframe("3m", False) == "15m"
    assert selected_timeframe("1m", True) == "1m"
    assert selected_timeframe("4h", False) == "4h"


def test_ltf_off_hydrates_missing_micro_keys_from_real_15m_only_schema():
    indicators = {"k_15m": 71.0, "k_15m_prev": 66.0, "d_15m": 60.0,
                  "wt1_15m": 20.0, "wt2_15m": 10.0,
                  "dc_basis_15m": 90.0, "dc_high_15m": 110.0, "dc_low_15m": 80.0}
    ind_view, metrics_view, missing = paired_indicator_view(indicators, {}, False)
    assert missing == ()
    for timeframe in ("1m", "3m", "5m"):
        assert ind_view[f"k_{timeframe}"] == 71.0
        assert ind_view[f"k_{timeframe}_prev"] == 66.0
        assert ind_view[f"d_{timeframe}"] == 60.0
    assert ind_view["wt1_3m"] == 20.0 and ind_view["wt2_1m"] == 10.0
    assert ind_view["dc_basis_3m"] == 90.0 and ind_view["dc_high_3m"] == 110.0
    assert metrics_view == {}
    assert "k_3m" not in indicators


def test_ltf_off_overwrites_contradictory_existing_array_micro_with_15m_peer():
    arrays = {"k_3m": np.array([2.0, 3.0]), "k_15m": np.array([70.0, 71.0]),
              "d_15m": np.array([60.0, 61.0])}
    paired = paired_npz_view(arrays, False)
    assert paired["k_3m"].tolist() == [70.0, 71.0]
    assert paired["k_1m"].tolist() == [70.0, 71.0]
    assert paired["k_5m"].tolist() == [70.0, 71.0]
    assert paired["d_3m"].tolist() == [60.0, 61.0]
    assert arrays["k_3m"].tolist() == [2.0, 3.0]
