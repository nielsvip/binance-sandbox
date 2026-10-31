import numpy as np

from vec_decisions.asr_delta_provider import replay_delta_tracker_arrays
from wt_dc_delta import DeltaTracker


def _fixture():
    n = 6
    ts = np.arange(1000, 1000 + n * 900, 900, dtype=np.int64)
    values = {
        "timestamps": ts,
        "wt1_15m": np.array([1, 2, 4, 3, 2, 1], dtype=float),
        "wt2_15m": np.zeros(n),
        "wt_score_15m": np.array([0, 1, 3, 2, 1, 0], dtype=float),
        "wt_velocity_15m": np.array([0, 1, 2, 1, 0, -1], dtype=float),
        "wt_bullish_15m": np.array([0, 1, 1, 1, 0, 0], dtype=np.int8),
        "wt_cross_15m": np.array([0, 1, 1, 0, -1, -1], dtype=np.int8),
        "dc_position_15m": np.array([0.5, 0.4, 0.3, 0.4, 0.6, 0.7]),
        "dc_width_15m": np.ones(n),
        "stoch_k_15m": np.array([50, 40, 30, 35, 60, 75], dtype=float),
        "stoch_d_15m": np.full(n, 50.0),
        "mfi_15m": np.full(n, 55.0),
        "close_15m": np.array([100, 101, 102, 101, 99, 98], dtype=float),
    }
    return ts, values


def test_adapter_uses_actual_tracker_in_timestamp_order_and_matches_direct_calls():
    ts, data = _fixture()
    cfg = {"tf_weights": {"15m": 1.0}, "entry_min_tf": 1, "rz_entry_enabled": False, "rz_exit_enabled": False}
    result = replay_delta_tracker_arrays(data, symbol="TEST", cfg=cfg, use_ltf=True, timestamps=ts, is_long=True)
    direct = DeltaTracker(cfg)
    for i in range(len(ts)):
        row = {key: value[i].item() for key, value in data.items() if key != "timestamps"}
        row.update({"_tick_ts": int(ts[i]), "ts": int(ts[i])})
        signal = direct.update("TEST", row, None)
        for field in result["signals"]:
            actual = getattr(signal, field)
            assert result["signals"][field][i] == actual
    assert result["state_scope"] == "fresh_tracker_replayed_from_first_supplied_row"
    assert "wt1_15m" in result["read_keys"]
    assert result["delta_signal_present"].all()
    assert result["delta_active_tfs"].shape == ts.shape


def test_prefix_outputs_do_not_depend_on_future_suffix():
    ts, data = _fixture()
    cfg = {"tf_weights": {"15m": 1.0}, "entry_min_tf": 1, "rz_entry_enabled": False, "rz_exit_enabled": False}
    full = replay_delta_tracker_arrays(data, symbol="TEST", cfg=cfg, use_ltf=True, timestamps=ts, is_long=True)
    prefix_data = {key: value[:4] for key, value in data.items()}
    prefix = replay_delta_tracker_arrays(prefix_data, symbol="TEST", cfg=cfg, use_ltf=True, timestamps=ts[:4], is_long=True)
    for field in full["signals"]:
        np.testing.assert_array_equal(full["signals"][field][:4], prefix["signals"][field])


def test_ltf_off_pairs_micro_aliases_to_actual_15m_arrays():
    ts, data = _fixture()
    cfg = {"tf_weights": {"3m": 1.0, "15m": 1.0}, "entry_min_tf": 1, "rz_entry_enabled": False, "rz_exit_enabled": False}
    result = replay_delta_tracker_arrays(data, symbol="TEST", cfg=cfg, use_ltf=False, timestamps=ts, is_long=True)
    assert "wt1_3m" in result["read_keys"]
    assert "wt1_3m" not in result["missing_read_keys"]
    assert "bb_lower_15m" in result["missing_read_keys"]
    assert result["position_context_supplied"] is False
