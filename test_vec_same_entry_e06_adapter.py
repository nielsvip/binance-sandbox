from types import SimpleNamespace

import numpy as np

from tools.vec_same_entry_e06_adapter import _events, registry_grid


def test_e06_registry_grid_is_complete_and_reconciles_stale_field():
    rows = registry_grid()
    assert len(rows) == 192
    assert {row.lookback for row in rows} == {40, 60, 100, 160}
    assert {row.arm_z for row in rows} == {1.5, 2.0, 2.5, 3.0}
    assert {row.exit_z for row in rows} == {0.0, 0.5, 1.0}
    assert {row.effective_corr_gate for row in rows} == {0.25, 0.5, 0.7, 1.0}
    params = rows[0].result_params()
    assert "STALE_NAME" in params["registry_field_status"]
    assert params["effective_corr_gate"] == params["registry_rebound_atr_value"]


def test_e06_events_are_completed_htf_mapped_and_side_separate():
    n = 240
    ts = np.arange(1, n + 1, dtype=np.int64) * 14_400
    trend = np.linspace(100.0, 150.0, n)
    close = trend.copy()
    close[180:186] *= np.array([1.02, 1.04, 1.07, 1.10, 1.06, 1.01])
    high = close * 1.002
    low = close * 0.998
    htf = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=high,
        low=low,
        close=close,
    )
    data = SimpleNamespace(ts=ts)
    setting = next(
        row for row in registry_grid()
        if row.lookback == 40
        and row.arm_z == 1.5
        and row.exit_z == 1.0
        and row.effective_corr_gate == 0.25
    )
    long_events = _events(data, htf, "LONG", setting)
    short_events = _events(data, htf, "SHORT", setting)
    assert long_events.event.dtype == np.uint8
    assert short_events.event.dtype == np.uint8
    assert np.all(long_events.source[long_events.event > 0] <= ts[
        long_events.event > 0
    ])
    assert np.all(short_events.source[short_events.event > 0] <= ts[
        short_events.event > 0
    ])
    assert not np.array_equal(long_events.event, short_events.event)
