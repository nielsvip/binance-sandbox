import datetime

import numpy as np
import pytest

from vec_decisions.tradier_missing_stdev_parent import (
    completed_parent_snapshot_values,
    previous_completed_parent_values,
)


def test_previous_value_uses_final_observation_of_prior_daily_parent():
    current = [0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 0.5]
    tokens = [10, 10, 10, 20, 20, 20, 30]
    previous = previous_completed_parent_values(current, tokens)
    assert np.isnan(previous[:3]).all()
    np.testing.assert_allclose(previous[3:], [0.4, 0.4, 0.4, 0.9])


def test_parent_array_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="matching 1D arrays"):
        previous_completed_parent_values([0.2, 0.3], [10])


def test_missing_parent_token_resets_without_fabricating_predecessor():
    current = [0.2, 0.3, 0.4, 0.7]
    tokens = [10, 10, None, 20]
    previous = previous_completed_parent_values(current, tokens)
    assert np.isnan(previous).all()


def test_fixed_daily_bb_previous_matches_live_previous_row_contract():
    import pandas as pd

    from tradier_indicators import bb_features
    from vec_decisions.live_indicator_arrays import bb_fixed_arrays

    closes = np.asarray([80.0 + i * 0.37 + (i % 4) * 0.8 for i in range(32)])
    vector_current = bb_fixed_arrays(closes, "D")["bb_pct_b_D"]
    live_previous = np.asarray(
        [
            np.nan
            if index < 20
            else bb_features(pd.Series(closes[:index]), length=20, std_mult=2.0)[2]
            for index in range(len(closes))
        ],
        dtype=float,
    )
    np.testing.assert_allclose(vector_current[19:-1], live_previous[20:], equal_nan=True)


def test_completed_parent_pair_advances_only_at_new_available_token():
    def epoch(value):
        return datetime.datetime.fromisoformat(value).replace(tzinfo=datetime.timezone.utc).timestamp()

    current = [0.8, 0.8, 0.9, 0.95, 0.6, 0.5]
    tokens = [epoch("2026-01-21T00:00:00")] * 2 + [
        epoch("2026-01-22T00:00:00")
    ] * 2 + [epoch("2026-01-26T00:00:00")] * 2
    targets = [
        epoch("2026-01-21T21:00:00"),
        epoch("2026-01-21T21:15:00"),
        epoch("2026-01-22T20:45:00"),
        epoch("2026-01-22T21:00:00"),
        epoch("2026-01-26T14:45:00"),
        epoch("2026-01-26T21:00:00"),
    ]
    completed, previous = completed_parent_snapshot_values(
        current, tokens, targets, "D"
    )
    np.testing.assert_allclose(completed, [0.8, 0.8, 0.8, 0.95, 0.95, 0.5])
    assert np.isnan(previous[:3]).all()
    np.testing.assert_allclose(previous[3:], [0.8, 0.8, 0.95])


def test_completed_parent_values_do_not_depend_on_future_npz_rows():
    def epoch(value):
        return datetime.datetime.fromisoformat(value).replace(tzinfo=datetime.timezone.utc).timestamp()

    values = [0.8, 0.9, 0.7, 0.6, 0.5, 0.4]
    tokens = [epoch("2026-01-21T00:00:00")] * 2 + [
        epoch("2026-01-22T00:00:00")
    ] * 2 + [epoch("2026-01-26T00:00:00")] * 2
    target = [epoch("2026-01-21T21:00:00"), epoch("2026-01-21T21:15:00"), epoch("2026-01-22T20:45:00"), epoch("2026-01-22T21:00:00"), epoch("2026-01-26T14:45:00"), epoch("2026-01-26T21:00:00")]
    baseline = completed_parent_snapshot_values(values, tokens, target, "D")
    changed_future = completed_parent_snapshot_values(
        values[:4] + [0.99, 0.01], tokens, target, "D"
    )
    np.testing.assert_allclose(baseline[0][:4], changed_future[0][:4], equal_nan=True)
    np.testing.assert_allclose(baseline[1][:4], changed_future[1][:4], equal_nan=True)


def test_stock_step_npz_fields_use_exact_completed_source_frame():
    import pandas as pd

    from vec_decisions.stock_live_frames import StepFrames, _eval_step

    dates = pd.bdate_range("2026-09-21", periods=25, tz="UTC") + pd.Timedelta(hours=20)
    daily_close = np.linspace(90.0, 110.0, len(dates))
    daily_close[-1] = 112.0
    json_d = pd.DataFrame(
        {
            "open": daily_close - 0.5,
            "high": daily_close + 1.0,
            "low": daily_close - 1.0,
            "close": daily_close,
            "volume": np.full(len(dates), 1000.0),
        },
        index=dates,
    )
    friday = pd.Timestamp("2026-10-23 19:45:00", tz="UTC")
    monday = pd.Timestamp("2026-10-26 13:30:00", tz="UTC")
    f15 = pd.DataFrame(
        {
            "open": [120.0, 122.0],
            "high": [121.0, 123.0],
            "low": [119.0, 121.0],
            "close": [120.5, 122.5],
            "volume": [100.0, 100.0],
        },
        index=pd.DatetimeIndex([friday, monday]),
    )
    frames = StepFrames(f15, None, None, json_d)
    noop = lambda frame, timeframe: {}

    friday_values = _eval_step(frames, noop, ("D",), 0)["D"]
    monday_values = _eval_step(frames, noop, ("D",), 1)["D"]
    friday_frame = frames.bundle(0, ("D",))["D"]
    monday_frame = frames.bundle(1, ("D",))["D"]
    from tradier_indicators import bb_features, completed_candle_frame

    friday_completed = completed_candle_frame(
        friday_frame, "D", friday + pd.Timedelta(minutes=15)
    )
    monday_completed = completed_candle_frame(
        monday_frame, "D", monday + pd.Timedelta(minutes=15)
    )
    friday_expected = bb_features(friday_completed["close"], 20, 2.0)
    monday_expected = bb_features(monday_completed["close"], 20, 2.0)
    assert friday_values["_completed_bb_pct_b_D"] == pytest.approx(friday_expected[2])
    assert monday_values["_completed_bb_pct_b_D"] == pytest.approx(monday_expected[2])
    assert monday_values["_completed_bb_pct_b_D_prev"] == pytest.approx(
        bb_features(monday_completed["close"].iloc[:-1], 20, 2.0)[2]
    )
    assert friday_values["_completed_bb_pct_b_D"] != pytest.approx(
        monday_values["_completed_bb_pct_b_D"]
    )
