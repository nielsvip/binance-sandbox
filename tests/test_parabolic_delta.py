"""Scalar DeltaTracker and paired per-bar PARABOLIC acceleration parity."""

from datetime import datetime, timezone

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
    validate_npz_inputs,
)
from wt_dc_delta import DEFAULT_CFG, DeltaTracker


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
