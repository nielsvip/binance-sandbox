import math
import os

import numpy as np
import pandas as pd

# tradier_api initializes a rotating logger at import time.  Keep this unit
# test hermetic instead of relying on the user's writable home-directory log.
os.environ.setdefault("TRADIER_API_LOG_DIR", "/tmp/tradier_completed_snapshot_tests")
os.environ.setdefault("EZ_LOG_DIR", "/tmp/tradier_completed_snapshot_tests")

import tradier_indicators as ti

from config_tradier import TradierConfig
from tools.exposure_ladder import (
    PREPARED_DIRECT_ENTRY_OFF,
    PREPARED_DIRECT_EXIT_OFF,
    all_entries_off,
    all_exits_off,
)
from tradier_indicators import (
    IndicatorCalculator,
    _snapshot_available_at,
    produce_completed_candle_snapshot,
)


def _frame(tf: str, *, current_open=None) -> pd.DataFrame:
    if current_open is None:
        current_open = pd.Timestamp("2026-01-02 09:30", tz="America/New_York")
    freq = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "D": "D"}[tf]
    if tf == "D":
        stamps = pd.date_range("2025-09-01 16:00", periods=80, freq=freq, tz="America/New_York")
    elif tf == "4h":
        # Stock-session 4h parents are 09:30→12:45 and 12:45→16:00;
        # arbitrary clock-4h labels are not valid parent identities.
        days = pd.bdate_range("2025-09-01", periods=40, tz="America/New_York")
        stamps = pd.DatetimeIndex([
            *(day + pd.Timedelta(hours=9, minutes=30) for day in days),
            *(day + pd.Timedelta(hours=12, minutes=45) for day in days),
        ]).sort_values()
    else:
        stamps = pd.date_range(end=current_open, periods=80, freq=freq)
    close = 100.0 + np.arange(len(stamps)) * .08 + np.sin(np.arange(len(stamps)) / 2.7) * 2.0
    return pd.DataFrame({
        "timestamp": stamps.tz_convert("UTC"),
        "open": close - .25,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000.0 + np.arange(len(stamps)),
    })


def test_every_required_timeframe_uses_only_the_latest_completed_candle_and_full_feature_schema():
    required = {
        "timeframe", "source_ts", "previous_source_ts", "open", "high", "low", "close", "high_prev", "low_prev",
        "stoch_k", "stoch_d", "stoch_k_prev", "stoch_d_prev", "wt1", "wt2", "wt1_prev", "wt2_prev",
        "wt_cross", "dc_high", "dc_low", "dc_high_prev", "dc_low_prev", "bb_upper", "bb_lower", "atr",
    }
    for tf in ("5m", "15m", "1h", "4h", "D"):
        frame = _frame(tf)
        # The final row is deliberately an in-progress absurd mark.  Its
        # availability is after the as-of time, so it cannot alter the result.
        frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [900., 999., 1., 999.]
        asof = _snapshot_available_at(frame["timestamp"].iloc[-2], tf)
        snapshot = produce_completed_candle_snapshot(frame, tf, asof)
        assert snapshot is not None
        assert set(snapshot.__dataclass_fields__) == required
        assert snapshot.source_ts == int(asof.timestamp())
        assert snapshot.previous_source_ts is not None and snapshot.previous_source_ts < snapshot.source_ts
        assert snapshot.close == frame["close"].iloc[-2]
        assert snapshot.high == frame["high"].iloc[-2]
        assert snapshot.close != 999.0
        assert snapshot.wt_cross in {"BULL", "BEAR", "NONE"}
        for field in ("stoch_k", "stoch_d", "wt1", "wt2", "dc_high", "dc_low", "dc_high_prev", "dc_low_prev", "bb_upper", "bb_lower", "atr"):
            assert math.isfinite(getattr(snapshot, field)), (tf, field, getattr(snapshot, field))


def test_completed_current_donchian_is_distinct_from_prior_channel_for_long_wait_vs_bounce():
    frame = _frame("15m")
    # Make the completed signal bar a new high. Long Wait's completed-current
    # channel must include it; Bounce's prior channel must exclude it.
    frame.loc[frame.index[-2], "high"] = 500.0
    asof = _snapshot_available_at(frame["timestamp"].iloc[-2], "15m")
    snapshot = produce_completed_candle_snapshot(frame, "15m", asof)
    assert snapshot is not None
    assert snapshot.dc_high == 500.0
    assert snapshot.dc_high_prev is not None and snapshot.dc_high_prev < 500.0


def test_indicator_calculator_snapshots_raw_frame_before_current_mark_mutates_it(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    current_open = now.floor("5min")
    frame = _frame("5m", current_open=current_open)
    unmarked_penultimate = float(frame["close"].iloc[-2])
    monkeypatch.setattr(ti.config, "COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED", True)
    result = IndicatorCalculator().compute(
        frame, "TEST", "5m", mark_price=999.0, mark_ts=now.to_pydatetime(), mid_run=False,
    )
    assert result["close_5m"] == 999.0  # legacy/current display field is mark-adjusted
    assert result["_completed_close_5m"] == unmarked_penultimate
    assert result["_completed_close_5m"] != result["close_5m"]
    assert result["_completed_source_ts_5m"] <= int(now.timestamp())


def test_default_off_hot_path_does_not_call_producer_but_direct_demand_does(monkeypatch):
    now = pd.Timestamp.now(tz="UTC")
    frame = _frame("5m", current_open=now.floor("5min"))
    calls = []
    original = ti.produce_completed_candle_snapshot

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(ti, "produce_completed_candle_snapshot", spy)
    monkeypatch.setattr(ti.config, "COMPLETED_CANDLE_SNAPSHOT_DIRECT_ENABLED", False)
    monkeypatch.setattr(ti.config, "LONG_WAIT_DIRECT_ENABLED", False)
    result = IndicatorCalculator().compute(frame.copy(), "TEST", "5m", None, now.to_pydatetime(), False)
    assert calls == [] and "_completed_close_5m" not in result
    monkeypatch.setattr(ti.config, "LONG_WAIT_DIRECT_ENABLED", True)
    result = IndicatorCalculator().compute(frame.copy(), "TEST", "5m", None, now.to_pydatetime(), False)
    assert len(calls) == 1 and "_completed_close_5m" in result


def test_prepared_route_knobs_are_default_off_and_ladder_clearers_include_all_action_toggles():
    cfg = TradierConfig()
    for name in (*PREPARED_DIRECT_ENTRY_OFF, *PREPARED_DIRECT_EXIT_OFF):
        assert getattr(cfg, name) is False
    entry_off = all_entries_off()
    exit_off = all_exits_off()
    assert all(entry_off[name] is False for name in PREPARED_DIRECT_ENTRY_OFF)
    assert all(exit_off[name] is False for name in PREPARED_DIRECT_EXIT_OFF)
