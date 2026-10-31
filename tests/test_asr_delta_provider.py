import numpy as np

from vec_decisions.asr_delta_provider import replay_delta_tracker_arrays
from wt_dc_delta import DeltaTracker
from types import SimpleNamespace

from vec_decisions.asr_delta_provider import native_delta_tracker_config


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


def test_native_config_adapter_matches_fast_data_manager_field_mapping():
    config = SimpleNamespace(
        DELTA_TF_WEIGHTS={"3m": 3.0}, DELTA_ENTRY_MIN_TF=3,
        DELTA_ENTRY_SPEED_THRESHOLD=0.7, DELTA_EXIT_SPEED_DECAY_PCT=44.0,
        DELTA_EXIT_MIN_TF_LOST=2, MIN_HOLD_BARS=4,
        DELTA_PYRAMID_PRICE_TOL=0.02, DELTA_PYRAMID_QTY_MULT=1.5,
        DELTA_PYRAMID_MAX=8, RZ_ENTRY_ENABLED=True, RZ_EXIT_ENABLED=False,
        RZ_TOP_BB_THRESHOLD=0.85, RZ_BOT_BB_THRESHOLD=0.15, RZ_LEGS_MIN=20.0,
        RZ_REQUIRE_STRUCT=False, RZ_K_EXIT=90.0, RZ_MFI_EXIT=85.0, RZ_K_ENTRY_MAX=50.0,
        RZ_BASELINE_BOUNCE_SHORT_ENABLED=False, DELTA_EXIT_TF="1h",
        DELTA_EXIT_DOM_TF_ENABLED=True, STRUCTURAL_EXIT_GATE_ENABLED=False,
    )
    assert native_delta_tracker_config(config) == {
        "tf_weights": {"3m": 3.0}, "entry_min_tf": 3, "entry_speed_threshold": 0.7,
        "exit_speed_decay_pct": 44.0, "exit_min_tf_lost": 2, "exit_min_hold": 4,
        "pyramid_price_tolerance": 0.02, "pyramid_qty_mult": 1.5, "pyramid_max": 8,
        "rz_entry_enabled": True, "rz_exit_enabled": False, "rz_top_bb": 0.85,
        "rz_bot_bb": 0.15, "rz_legs_min": 20.0, "rz_require_struct": False,
        "rz_k_exit": 90.0, "rz_mfi_exit": 85.0, "rz_k_entry_max": 50.0,
        "rz_baseline_bounce_short_enabled": False, "exit_dominant_tf": "1h",
        "structural_exit_gate_enabled": False,
    }

def test_active_redzone_requires_explicit_current_price():
    ts, data = _fixture()
    cfg = {"tf_weights": {"15m": 1.0}, "rz_entry_enabled": True, "rz_exit_enabled": False}
    with np.testing.assert_raises_regex(ValueError, "explicit current_price"):
        replay_delta_tracker_arrays(data, symbol="TEST", cfg=cfg, use_ltf=True,
                                    timestamps=ts, is_long=True, position_states=[None] * len(ts))


def test_actual_indicator_rows_override_raw_npz_aliases_and_report_absent_fields():
    ts = np.array([900, 1800, 2700], dtype=np.int64)
    raw = {"timestamps": ts, "wt1_15m": np.array([0.0, 0.0, 0.0]), "wt2_15m": np.zeros(3)}
    rows = [
        {"_tick_ts": int(ts[i]), "ts": int(ts[i]), "wt1_15m": 0.0, "wt2_15m": 0.0,
         "wt1_3m": float(i * 10), "wt2_3m": 0.0, "current_price": 100.0}
        for i in range(3)
    ]
    cfg = {"tf_weights": {"3m": 1.0}, "entry_min_tf": 1, "rz_entry_enabled": False, "rz_exit_enabled": False}
    result = replay_delta_tracker_arrays(
        raw, symbol="TEST", cfg=cfg, use_ltf=True, timestamps=ts, is_long=True,
        position_states=[None] * 3, producer_rows=rows
    )
    assert result["signals"]["bull_speed"][1] > 0
    assert "wt1_3m" in result["read_keys"]
    assert "dc_high_3m" in result["missing_read_keys"]


def test_incremental_stream_uses_one_tracker_and_rejects_time_reversal():
    from vec_decisions.asr_delta_provider import DeltaTrackerStream
    ts, data = _fixture()
    cfg = {"tf_weights": {"15m": 1.0}, "rz_entry_enabled": False, "rz_exit_enabled": False}
    stream = DeltaTrackerStream("TEST", cfg, True)
    observed = []
    for i, stamp in enumerate(ts):
        row = {key: value[i].item() for key, value in data.items() if key != "timestamps"}
        row.update({"_tick_ts": int(stamp), "ts": int(stamp)})
        observed.append(stream.update(row, None))
    assert stream.update_count == len(ts)
    assert observed[-1]["timestamp"] == int(ts[-1])
    assert observed[-1]["delta_signal_present"] is True
    with np.testing.assert_raises_regex(ValueError, "moved backward"):
        stream.update({"_tick_ts": int(ts[-2])}, None)
