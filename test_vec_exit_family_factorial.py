from types import SimpleNamespace

import numpy as np

from tools.vec_exit_family_factorial import (
    FactorialExitBook,
    Factors,
    _dc_reject_events,
    _hybrid_events,
)


class FakeZ:
    def __init__(self, rows):
        self.rows = rows
        self.files = list(rows)

    def __getitem__(self, key):
        return self.rows[key]


def _fixture():
    n = 8
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    rows = {}
    for tf in ("5m", "15m", "1h", "4h", "D"):
        rows[f"wt1_{tf}"] = np.zeros(n)
        rows[f"wt2_{tf}"] = np.ones(n)
    rows.update(
        {
            "dc_high_1h": np.full(n, 11.0),
            "dc_low_1h": np.full(n, 9.0),
            "dc_high_1h_prev": np.full(n, 11.0),
            "dc_low_1h_prev": np.full(n, 9.0),
            "bb_upper_1h": np.full(n, 11.0),
            "bb_lower_1h": np.full(n, 9.0),
            "stoch_k_1h": np.full(n, 50.0),
            "stoch_k_1h_prev": np.full(n, 50.0),
            "stoch_k_15m": np.full(n, 50.0),
            "stoch_k_15m_prev": np.full(n, 50.0),
            "stoch_k_5m": np.full(n, 50.0),
            "stoch_k_5m_prev": np.full(n, 50.0),
            "rsi_1h": np.full(n, 50.0),
            "rsi_4h": np.full(n, 50.0),
            "bb_pct_b_4h": np.full(n, 0.5),
        }
    )
    data = SimpleNamespace(
        symbol="TEST",
        ts=ts,
        close=np.full(n, 10.0),
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(rows),
    )
    h = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=np.array([10, 11, 12, 11, 10, 11, 12, 13.0]),
        low=np.array([9, 10, 11, 10, 9, 10, 11, 12.0]),
        close=np.array([10, 12, 10, 8, 10, 10, 10, 10.0]),
    )
    return data, h


def test_hybrid_is_side_mirrored_and_completed_causal():
    data, h = _fixture()
    long_events, long_sources = _hybrid_events(
        data, h, side="LONG", timeframe="1h"
    )
    short_events, short_sources = _hybrid_events(
        data, h, side="SHORT", timeframe="1h"
    )
    assert np.flatnonzero(long_events).tolist() == [3, 4]
    assert np.flatnonzero(short_events).tolist() == [1, 2, 5, 6, 7]
    assert all(source["1h"] <= data.ts[row] for row, source in long_sources.items())
    assert all(source["1h"] <= data.ts[row] for row, source in short_sources.items())


def test_dc_reject_requires_outside_then_later_recross():
    data, h = _fixture()
    data.close[:] = np.array([10, 12, 10, 10, 8, 10, 10, 10.0])
    long_events, _ = _dc_reject_events(data, h, side="LONG")
    short_events, _ = _dc_reject_events(data, h, side="SHORT")
    assert np.flatnonzero(long_events).tolist() == [2]
    assert np.flatnonzero(short_events).tolist() == [5]


def test_dc_reject_uses_execution_rows_and_current_completed_channel():
    data, h = _fixture()
    # No completed 1h close is outside the current 9/11 channel.  Exact/live
    # still arms on an intrahour breach and exits on the following 5m recross.
    h.close[:] = 10.0
    data.close[:] = np.array([10, 10, 8, 10, 10, 10, 10, 10.0])
    events, sources = _dc_reject_events(data, h, side="SHORT")
    assert np.flatnonzero(events).tolist() == [3]
    assert sources[3]["1h"] <= data.ts[3]


def test_dc_reject_expires_in_wall_time_not_completed_parent_count():
    data, h = _fixture()
    data.ts[:] = np.array(
        [300, 600, 900, 1200, 21000, 21300, 21600, 21900],
        dtype=np.int64,
    )
    data.close[:] = np.array([10, 10, 8, 8, 10, 10, 10, 10.0])
    events, _ = _dc_reject_events(
        data,
        h,
        side="SHORT",
        lookback=5,
    )
    assert not events.any()


def test_min_hold_is_measured_from_each_active_episode():
    data, h = _fixture()
    book = FactorialExitBook(
        data,
        {"5m": h, "15m": h, "1h": h, "4h": h, "D": h},
        side="LONG",
        factors=Factors(True, False, False, 10),
        hybrid_timeframe="1h",
    )
    assert (
        book.update_with_position(
            3, active=True, entry_price=10, current_gain_pct=0
        )
        is None
    )
    assert (
        book.update_with_position(
            4, active=True, entry_price=10, current_gain_pct=0
        )
        is None
    )
    # A flat observation resets the episode timer.
    assert (
        book.update_with_position(
            5, active=False, entry_price=np.nan, current_gain_pct=-np.inf
        )
        is None
    )
    assert book.active_since_row is None


def test_srs_short_requires_entry_below_lower_boundary_and_strict_stock_k():
    data, h = _fixture()
    rows = data.z.rows
    data.close[:] = 9.0
    rows["bb_lower_1h"][:] = 9.0
    rows["stoch_k_1h"][:] = 10.0
    rows["stoch_k_1h_prev"][:] = 9.0
    rows["stoch_k_15m"][:] = 10.0
    rows["stoch_k_15m_prev"][:] = 9.0
    rows["stoch_k_5m"][:] = 10.0
    rows["stoch_k_5m_prev"][:] = 9.0
    for tf in ("5m", "15m", "1h"):
        rows[f"wt1_{tf}"][:] = 1.0
        rows[f"wt2_{tf}"][:] = 0.0
    book = FactorialExitBook(
        data,
        {"5m": h, "15m": h, "1h": h, "4h": h, "D": h},
        side="SHORT",
        factors=Factors(False, False, True, 0),
        hybrid_timeframe="1h",
    )
    assert book._srs_fires(4, 8.5)
    assert not book._srs_fires(4, 9.5)


def test_srs_uses_current_stock_defaults_85_and_15():
    data, h = _fixture()
    rows = data.z.rows
    data.close[:] = 11.0
    rows["bb_upper_1h"][:] = 11.0
    rows["stoch_k_1h"][:] = 80.0
    rows["stoch_k_1h_prev"][:] = 81.0
    rows["stoch_k_15m"][:] = 80.0
    rows["stoch_k_15m_prev"][:] = 81.0
    rows["stoch_k_5m"][:] = 80.0
    rows["stoch_k_5m_prev"][:] = 81.0
    for tf in ("5m", "15m", "1h"):
        rows[f"wt1_{tf}"][:] = 0.0
        rows[f"wt2_{tf}"][:] = 1.0
    book = FactorialExitBook(
        data,
        {"5m": h, "15m": h, "1h": h, "4h": h, "D": h},
        side="LONG",
        factors=Factors(False, False, True, 0),
        hybrid_timeframe="1h",
    )
    assert not book._srs_fires(4, 11.5)
    rows["stoch_k_1h"][:] = 90.0
    rows["stoch_k_1h_prev"][:] = 91.0
    rows["stoch_k_15m"][:] = 90.0
    rows["stoch_k_15m_prev"][:] = 91.0
    rows["stoch_k_5m"][:] = 90.0
    rows["stoch_k_5m_prev"][:] = 91.0
    book = FactorialExitBook(
        data,
        {"5m": h, "15m": h, "1h": h, "4h": h, "D": h},
        side="LONG",
        factors=Factors(False, False, True, 0),
        hybrid_timeframe="1h",
    )
    assert book._srs_fires(4, 11.5)
