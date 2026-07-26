import math
from types import SimpleNamespace

import numpy as np

from tools.vec_same_entry_exit_adapter import (
    StaticExitBook,
    WtMtfParams,
    _entry_schedule_hash,
    build_donchian_book,
    build_wt_mtf_book,
    e02_grid,
    simulate,
    simulate_structural_compiled,
    structural_grid,
    wt_grid,
)
from vec_paths.structural_wt_retest_exit import StructuralWtParams


class FakeZ:
    def __init__(self, rows):
        self.rows = rows
        self.files = list(rows)

    def __getitem__(self, key):
        return self.rows[key]


def _fake_data(n=12):
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    return SimpleNamespace(
        symbol="TEST",
        ts=ts,
        high=np.linspace(10, 12, n),
        low=np.linspace(9, 11, n),
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ(
            {
                "wt1_1h": np.array([0, 10, 30, 70, 60, 40, 20, 10, 0, 0, 0, 0], float),
                "wt2_1h": np.array([0, 5, 20, 60, 62, 50, 30, 20, 10, 0, 0, 0], float),
            }
        ),
    )


def test_wt_mtf_is_edge_triggered_and_causal():
    data = _fake_data()
    htf = SimpleNamespace(
        event_index=np.arange(12, dtype=np.int64),
        source_ts=data.ts.copy(),
        high=data.high.copy(),
        low=data.low.copy(),
        close=(data.high + data.low) / 2,
    )
    book = build_wt_mtf_book(
        data,
        {"1h": htf},
        WtMtfParams(
            timeframes=("1h",),
            min_against_tfs=1,
            extreme=65,
            velocity=0,
            recent_extreme_bars=4,
        ),
        side="LONG",
    )
    rows = np.flatnonzero(book.events)
    assert rows.tolist() == [4]
    decision = book.update(4, active=True)
    assert decision is not None
    assert decision.source_timestamps["1h"] <= data.ts[4]
    assert book.update(5, active=True) is None


def test_wt_grid_is_exact_five_tf_bounded_contract():
    rows = wt_grid()
    assert len(rows) == 4 * 4 * 4 * 4
    assert {row.timeframes for row in rows} == {
        ("15m", "1h", "4h", "D", "W")
    }
    assert {row.min_against_tfs for row in rows} == {1, 2, 3, 4}
    assert {row.extreme for row in rows} == {45.0, 55.0, 65.0, 75.0}
    assert {row.velocity for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert {row.profit_gate_pct for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert all(not row.require_fast_structure for row in rows)


def test_weekly_wt_event_uses_completed_source_only():
    data = _fake_data()
    data.z = FakeZ(
        {
            "wt1_W": data.z["wt1_1h"],
            "wt2_W": data.z["wt2_1h"],
        }
    )
    htf = SimpleNamespace(
        event_index=np.arange(12, dtype=np.int64),
        source_ts=data.ts - 1,
        high=data.high.copy(),
        low=data.low.copy(),
        close=(data.high + data.low) / 2,
    )
    book = build_wt_mtf_book(
        data,
        {"W": htf},
        WtMtfParams(
            timeframes=("W",),
            min_against_tfs=1,
            extreme=65,
            velocity=0,
            recent_extreme_bars=4,
        ),
        side="LONG",
    )
    decision = book.update(4, active=True)
    assert decision is not None
    assert decision.source_timestamps["W"] < data.ts[4]


def test_static_book_never_crosses_side_or_fires_flat():
    events = np.array([0, 1, 0], dtype=np.uint8)
    refs = np.array([math.nan, 11.0, math.nan])
    book = StaticExitBook("TEST_EXIT", events, refs, {1: {"1h": 600}})
    assert book.update(1, active=False) is None
    assert book.update(1, active=True).reason == "TEST_EXIT"


def test_e02_grid_is_exact_bounded_contract():
    rows = e02_grid()
    assert len(rows) == 3 * 7 * 4
    assert {row["timeframe"] for row in rows} == {"1h", "4h", "D"}
    assert {row["lookback"] for row in rows} == {10, 15, 20, 30, 40, 55, 80}
    assert {row["profit_gate_pct"] for row in rows} == {0.0, 0.25, 0.5, 1.0}
    assert all("5m" not in str(row) for row in rows)


def test_completed_donchian_book_is_causal_and_side_mirrored():
    n = 120
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    low = np.full(n, 10.0)
    high = np.full(n, 20.0)
    close = np.full(n, 15.0)
    close[10] = 9.0
    close[11] = 21.0
    data = SimpleNamespace(
        ts=ts,
        high=high,
        low=low,
        close=close,
    )
    htf = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=high,
        low=low,
        close=close,
    )
    long_book = build_donchian_book(
        data, {"1h": htf}, timeframe="1h", lookback=10, side="LONG"
    )
    short_book = build_donchian_book(
        data, {"1h": htf}, timeframe="1h", lookback=10, side="SHORT"
    )
    assert np.flatnonzero(long_book.events).tolist() == [10]
    assert np.flatnonzero(short_book.events).tolist() == [11]
    assert long_book.update(10, active=True).reclaim_reference == 20.0
    assert short_book.update(11, active=True).reclaim_reference == 10.0
    assert long_book.update(10, active=True).source_timestamps["1h"] < ts[10]
    assert short_book.update(11, active=True).source_timestamps["1h"] < ts[11]


def test_entry_schedule_hash_changes_only_when_entry_schedule_changes():
    data = SimpleNamespace(ts=np.array([1, 2, 3], dtype=np.int64))
    curve = SimpleNamespace()
    # dataclasses.asdict needs an actual ladder curve.
    from tools.vec_band_ladder_walkforward import Curve, SignalData

    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    first = SignalData(
        entry_mult=np.array([0.0, 2.0, 0.0]),
        event_tf=np.zeros((3, 3), dtype=np.uint8),
        exit_event=np.zeros(3, dtype=np.uint8),
        exit_ref=np.full(3, np.nan),
        causality={},
    )
    second = SignalData(
        entry_mult=np.array([0.0, 3.0, 0.0]),
        event_tf=first.event_tf,
        exit_event=first.exit_event,
        exit_ref=first.exit_ref,
        causality={},
    )
    assert _entry_schedule_hash(data, first, curve, 0, 3) == _entry_schedule_hash(
        data, first, curve, 0, 3
    )
    assert _entry_schedule_hash(data, first, curve, 0, 3) != _entry_schedule_hash(
        data, second, curve, 0, 3
    )


def test_structural_registry_grid_is_complete_and_hour_normalized():
    rows = structural_grid()
    assert len(rows) == 768
    observed = {
        (
            params.arm_tf,
            params.confirm_tf,
            params.rebound_atr,
            params.prebreak_lookback,
            wait_hours,
            profit_gate,
            params.max_wait_1h,
        )
        for params, profit_gate, wait_hours in rows
    }
    assert ("1h", "15m", 0.5, 3, 12, 0.25, 48) in observed
    assert ("4h", "1h", 4.0, 10, 48, 1.0, 48) in observed


def test_compiled_structural_scanner_matches_python_oracle():
    from tools.vec_band_ladder_walkforward import Curve, SignalData
    from tools.vec_same_entry_exit_adapter import StructuralWtExitBookAdapter

    n = 120
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    open_ = np.full(n, 10.0)
    high = np.full(n, 10.2)
    low = np.full(n, 9.8)
    close = np.full(n, 10.0)
    wt4h = np.zeros(n)
    wt1h = np.zeros(n)
    arm_rows = np.array([2, 4, 6, 8], dtype=np.int64)
    confirm_rows = np.array([9, 10, 11], dtype=np.int64)
    arm_high = np.array([12.0, 13.0, 14.0, 11.0])
    arm_low = np.array([10.0, 10.5, 11.0, 9.0])
    arm_close = np.array([11.0, 11.5, 12.0, 9.5])
    arm_wt = np.array([20.0, 25.0, 30.0, 10.0])
    confirm_high = np.array([9.0, 10.0, 9.0])
    confirm_low = np.array([8.0, 8.5, 8.2])
    confirm_close = np.array([8.5, 9.5, 9.0])
    confirm_wt = np.array([5.0, 15.0, 10.0])
    wt4h[arm_rows] = arm_wt
    wt1h[confirm_rows] = confirm_wt
    data = SimpleNamespace(
        symbol="PARITY",
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        full_indices=np.arange(n, dtype=np.int64),
        z=FakeZ({"wt1_4h": wt4h, "wt1_1h": wt1h}),
    )
    htfs = {
        "4h": SimpleNamespace(
            event_index=arm_rows,
            source_ts=ts[arm_rows] - 1,
            high=arm_high,
            low=arm_low,
            close=arm_close,
            atr=np.ones(len(arm_rows)),
        ),
        "1h": SimpleNamespace(
            event_index=confirm_rows,
            source_ts=ts[confirm_rows] - 1,
            high=confirm_high,
            low=confirm_low,
            close=confirm_close,
            atr=np.ones(len(confirm_rows)),
        ),
    }
    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    entry = np.zeros(n)
    entry[0] = 4.0
    signals = SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    params = StructuralWtParams(
        arm_tf="4h",
        confirm_tf="1h",
        rebound_atr=0.5,
        prebreak_lookback=3,
        max_wait_1h=12,
    )
    python_result = simulate(
        data,
        signals,
        curve,
        StructuralWtExitBookAdapter(data, htfs, params, side="LONG"),
        0,
        n,
        0.0005,
        0.0005,
        side="LONG",
        profit_gate_pct=0.0,
    )
    compiled_result = simulate_structural_compiled(
        data,
        signals,
        curve,
        htfs,
        params,
        0,
        n,
        0.0005,
        0.0005,
        side="LONG",
        profit_gate_pct=0.0,
    )
    for key in (
        "capital_return_pct",
        "bh_capital_return_pct",
        "binary_tim_pct",
        "exposure_weighted_tim_pct",
        "max_drawdown_account_pct",
        "minimum_account_equity_usd",
        "peak_post_fill_notional_usd",
        "requested_notional_usd",
        "filled_notional_usd",
    ):
        assert math.isclose(
            compiled_result[key], python_result[key], abs_tol=1e-9
        ), key
    for key in (
        "signals",
        "rejected_by_profit_gate",
        "exit_fills",
        "entry_fills",
        "lower_or_higher_reentries",
        "reclaim_reentries",
        "clamp_count",
        "bars_flat_beyond_reclaim",
    ):
        assert compiled_result[key] == python_result[key], key


def test_partial_clip_keeps_runner_and_reclaims_clip():
    from tools.vec_band_ladder_walkforward import Curve, SignalData

    n = 120
    data = SimpleNamespace(
        ts=np.arange(1, n + 1, dtype=np.int64) * 300,
        open=np.full(n, 10.0),
        high=np.full(n, 10.2),
        low=np.full(n, 9.8),
        close=np.full(n, 10.0),
    )
    curve = Curve("x", "linear", "green", "target", 30, 8, 4, 6, 3, 3, 1)
    entry = np.zeros(n)
    entry[0] = 4.0
    signals = SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    events = np.zeros(n, dtype=np.uint8)
    events[10] = 1
    refs = np.full(n, np.nan)
    refs[10] = 10.0
    result = simulate(
        data,
        signals,
        curve,
        StaticExitBook("WT_PARTIAL", events, refs, {10: {"1h": 3000}}),
        0,
        n,
        0.0,
        0.0,
        side="LONG",
        partial_exit_fraction=0.25,
        runner_exit_book=StaticExitBook(
            "E02_DONCHIAN_4H_N30",
            np.zeros(n, dtype=np.uint8),
            np.full(n, np.nan),
            {},
        ),
    )
    assert result["partial_exit_fills"] == 1
    assert result["clip_reclaim_reentries"] == 1
    assert result["runner_exit_fills"] == 0
    assert result["clip_obligations_unfilled_at_end"] == 0
    assert result["insolvent"] is False
    assert result["entry_capacity_breach"] is False
    assert result["peak_post_fill_notional_usd"] <= 16_000.0
