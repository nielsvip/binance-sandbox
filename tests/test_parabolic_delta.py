"""Scalar DeltaTracker and paired per-bar PARABOLIC acceleration parity."""

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from vec_decisions.parabolic_delta import (
    DC_DELTA_FIELDS,
    DC_POSITION_FIELDS,
    TF_FIELDS,
    WT_DELTA_FIELDS,
    WT_STATE_FIELDS,
    normalize_bar_timestamp,
    snapshot_from_npz,
    step,
    step_native,
    validate_parabolic_inputs,
    validate_npz_inputs,
)
from wt_dc_delta import DEFAULT_CFG, DeltaTracker
from vec_decisions.live_exit_chain import step as live_exit_chain_step


def _bar_indicators(index, is_long):
    values = {}
    side = 1.0 if is_long else -1.0
    for tf_index, tf in enumerate(("15m", "1h", "4h", "D")):
        seed = 10.0 + tf_index * 3.0
        for field_index, field in enumerate(WT_DELTA_FIELDS):
            values[f"{field}_{tf}"] = seed + side * index * (0.1 + field_index * 0.03)
        for field_index, field in enumerate(DC_DELTA_FIELDS):
            values[f"{field}_{tf}"] = seed + side * index * (0.2 + field_index * 0.04)
            values[f"{field}_{tf}_prev"] = seed + side * max(index - 1, 0) * (0.2 + field_index * 0.04)
        for field_index, field in enumerate(DC_POSITION_FIELDS):
            values[f"{field}_{tf}"] = seed + side * index * (0.05 + field_index * 0.01)
        for field_index, field in enumerate(WT_STATE_FIELDS):
            values[f"{field}_{tf}"] = float((index + field_index + tf_index) % 3)
    values["timestamp_15m"] = 1_800_000_000 + index * 900
    return values


@pytest.mark.parametrize("is_long", [True, False])
def test_per_bar_recurrence_matches_scalar_delta_tracker_for_paired_15m(is_long):
    weights = dict(DEFAULT_CFG["tf_weights"])
    scalar = DeltaTracker(cfg={"tf_weights": weights})
    state = {}
    for index in range(20):
        indicators = _bar_indicators(index, is_long)
        for field in TF_FIELDS:
            source = indicators.get(f"{field}_15m")
            if source is not None:
                indicators[f"{field}_3m"] = source
            previous = indicators.get(f"{field}_prev_15m")
            if previous is not None:
                indicators[f"{field}_prev_3m"] = previous
            previous_dc = indicators.get(f"{field}_15m_prev")
            if previous_dc is not None:
                indicators[f"{field}_3m_prev"] = previous_dc
        indicators["_tick_ts"] = indicators["timestamp_15m"]
        scalar_signal = scalar.update("TESTUSDC", indicators, position_state=None)
        vector_signal = step(
            state,
            indicators,
            indicators["timestamp_15m"],
            weights,
            ltf_enabled=False,
        )
        assert vector_signal["bull_accel"] is bool(scalar_signal.bull_accel), index
        assert vector_signal["bear_accel"] is bool(scalar_signal.bear_accel), index
        assert vector_signal["total_bull"] == pytest.approx(scalar_signal.bull_speed)
        assert vector_signal["total_bear"] == pytest.approx(scalar_signal.bear_speed)


def test_repeated_bar_reuses_first_result_and_does_not_advance_state():
    state = {}
    first = step(state, _bar_indicators(1, True), 1_800_000_900, DEFAULT_CFG["tf_weights"])
    previous_totals = (state["previous_total_bull"], state["previous_total_bear"])
    repeated = step(state, _bar_indicators(1, True), 1_800_000_900, DEFAULT_CFG["tf_weights"])
    assert repeated == first
    assert (state["previous_total_bull"], state["previous_total_bear"]) == previous_totals


def test_ltf_on_keeps_actual_3m_arrays_instead_of_15m_pair():
    arrays = {
        "wt1_3m": np.array([1.0]),
        "wt1_15m": np.array([20.0]),
    }
    assert snapshot_from_npz(arrays, 0, ltf_enabled=True)["wt1_3m"] == 1.0
    assert snapshot_from_npz(arrays, 0, ltf_enabled=False)["wt1_3m"] == 20.0


def test_npz_validation_requires_full_recurrence_inputs_and_timestamp():
    n = 2
    arrays = {}
    for tf in ("15m", "1h", "4h", "D"):
        for field in TF_FIELDS:
            arrays[f"{field}_{tf}"] = np.ones(n)
        for field in DC_DELTA_FIELDS:
            arrays[f"{field}_{tf}_prev"] = np.ones(n)
    arrays["timestamps"] = np.array([1_800_000_000, 1_800_000_900])
    validate_npz_inputs(arrays, n, ltf_enabled=False)
    del arrays["wt1_4h"]
    with pytest.raises(ValueError, match="wt1_4h"):
        validate_npz_inputs(arrays, n, ltf_enabled=False)


def test_timestamp_accepts_epoch_or_iso_and_rejects_missing():
    assert normalize_bar_timestamp(123.0) == 123.0
    expected = datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()
    assert normalize_bar_timestamp("2026-10-31T00:00:00Z") == expected
    with pytest.raises(ValueError, match="completed-bar timestamp"):
        normalize_bar_timestamp(None)


def test_irregular_eligible_bars_reentry_and_duplicate_timestamp_keep_shared_state():
    state = {}
    rows = [_bar_indicators(i, True) for i in range(7)]
    outputs = [step(state, rows[i], rows[i]["timestamp_15m"], DEFAULT_CFG["tf_weights"]) for i in (0, 2, 2, 6)]
    assert outputs[1] == outputs[2]
    assert state["last_bar_timestamp"] == rows[6]["timestamp_15m"]
    assert len(state["previous_fields"]) > 0
    # Closing and reentering does not replace this external symbol/side state.
    state_map = {("TESTUSDC", True): state}
    before = state_map[("TESTUSDC", True)]
    step(before, rows[6], rows[6]["timestamp_15m"], DEFAULT_CFG["tf_weights"])
    assert state_map[("TESTUSDC", True)] is before


def test_native_state_key_is_symbol_side_and_same_bar_is_account_independent():
    states = {}
    indicators = _bar_indicators(3, True)
    first = step_native(states, "testusdc", True, indicators, False, DEFAULT_CFG["tf_weights"])
    # There is deliberately no account argument/key: a second account at the same
    # completed bar observes the one cached symbol/side evaluation.
    second = step_native(states, "TESTUSDC", True, indicators, False, DEFAULT_CFG["tf_weights"])
    assert second == first
    assert list(states) == [("TESTUSDC", True)]


def test_native_path_fails_closed_for_missing_timestamp_or_required_delta_field():
    with pytest.raises(ValueError, match="completed-bar timestamp"):
        step_native({}, "TESTUSDC", True, {"wt1_15m": 1.0}, False, DEFAULT_CFG["tf_weights"])
    missing = _bar_indicators(1, True)
    del missing["wt1_15m"]
    with pytest.raises(ValueError, match="wt1_15m"):
        step_native({}, "TESTUSDC", True, missing, False, DEFAULT_CFG["tf_weights"])


def test_ltf_on_parabolic_requires_real_3m_arrays_and_timestamp():
    arrays = _parabolic_npz(2)
    with pytest.raises(ValueError, match="missing NPZ fields|dc_high4_3m|timestamp_3m"):
        validate_parabolic_inputs(arrays, 2, ltf_enabled=True)


def _parabolic_npz(n=2):
    arrays = {"timestamps": np.array([1_800_000_000 + 900 * i for i in range(n)], dtype=float)}
    for i in range(n):
        row = _bar_indicators(i + 1, True)
        for tf in ("15m", "1h", "4h", "D"):
            for field in TF_FIELDS:
                arrays.setdefault(f"{field}_{tf}", np.zeros(n))[i] = row[f"{field}_{tf}"]
                if field in WT_DELTA_FIELDS:
                    arrays.setdefault(f"{field}_prev_{tf}", np.zeros(n))[i] = row[f"{field}_{tf}"] - 1.0
                elif field in DC_DELTA_FIELDS:
                    arrays.setdefault(f"{field}_{tf}_prev", np.zeros(n))[i] = row[f"{field}_{tf}_prev"]
    arrays.update({
        "dc_high4_15m": np.array([100.0, 100.0]),
        "dc_low4_15m": np.array([50.0, 50.0]),
        "low_15m": np.array([9.0, 9.0]),
        "low_15m_prev": np.array([10.0, 10.0]),
        "high_15m": np.array([11.0, 11.0]),
        "high_15m_prev": np.array([10.0, 10.0]),
        "k_15m": np.array([95.0, 95.0]),
        "k_15m_prev": np.array([94.0, 94.0]),
    })
    return arrays


def test_actual_live_exit_chain_parabolic_hook_wins_same_bar_srs_collision():
    npz = _parabolic_npz()
    P = {
        "n": 2, "is_long": True, "parabolic_npz": npz,
        "parabolic_ts": npz["timestamps"], "k1m": np.full(2, 50.0),
        "k1mp": np.full(2, 50.0), "k15": npz["k_15m"],
        "para_lvl": npz["dc_high4_15m"], "crack": np.array([True, True]),
        "para_now": npz["low_15m"], "para_prev": npz["low_15m_prev"],
        "htf_aligned": np.zeros(2), "srs_hi": np.array([100.0, 100.0]),
        "srs_lo": np.array([90.0, 90.0]), "k1h": np.array([80.0, 80.0]),
        "k1hp": np.array([81.0, 81.0]), "k15p": np.array([96.0, 96.0]),
        "w": {"1h": (np.ones(2), np.full(2, 2.0)), "15m": (np.ones(2), np.full(2, 2.0))},
        "srs_tf": "dc_4h",
    }
    cfg = SimpleNamespace(
        K1M_EXTREME_REVERSE_ENABLED=False, REENTRY_GRACE_MINUTES=30.0,
        PARABOLIC_EXIT_ENABLED=True, LIVE_5m_trading_ENABLED=False,
        DELTA_TF_WEIGHTS=dict(DEFAULT_CFG["tf_weights"]),
        STRUCTURAL_RANGE_SHIFT_EXIT=True, STRUCTURAL_RANGE_SHIFT_K_HIGH=75.0,
        STRUCTURAL_RANGE_SHIFT_K_LOW=25.0, STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS=100.0,
        HTF_EXIT_VETO_ENABLED=False, KEY_LEVEL_CRASH_ENABLED=False,
    )
    result = live_exit_chain_step(
        P, cfg, 0, 101.0,
        {"gain": 1.0, "peak": 1.0, "age_min": 900.0, "entry_price": 110.0,
         "was_augmented": False, "is_hedge": False, "parabolic_state": {}},
    )
    assert result and result[0].startswith("PARABOLIC_EXIT_LONG")
