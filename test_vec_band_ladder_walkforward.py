from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import vec_band_ladder_walkforward as ladder


def test_remembered_ladder_is_clipped_to_eight_x():
    assert ladder.ladder_mult(0.0, 10.0, 6.0, "linear") == 8.0
    assert ladder.ladder_mult(1.0, 10.0, 6.0, "linear") == 6.0


def test_below_lower_band_is_zero_and_above_is_top():
    assert ladder.ladder_mult(-0.01, 6.0, 4.0, "linear") == 0.0
    assert ladder.ladder_mult(1.01, 6.0, 4.0, "linear") == 4.0


def test_center_plateau_matches_live_function_shape():
    assert ladder.ladder_mult(0.2, 6.0, 4.0, "center_plateau") == 6.0
    assert ladder.ladder_mult(0.5, 6.0, 4.0, "center_plateau") == 6.0
    assert ladder.ladder_mult(1.0, 6.0, 4.0, "center_plateau") == 4.0


def test_block_generator_preserves_valid_pairs():
    curves = ladder._curves(17, 30)
    assert len(curves) >= 30
    for curve in curves:
        for tf in ladder.TF_ORDER:
            bottom, top = curve.pair(tf)
            assert bottom >= top >= 0


def test_short_cash_ledger_and_benchmark_are_side_aware():
    n = 100
    data = SimpleNamespace(
        ts=np.arange(n, dtype=np.int64),
        open=np.full(n, 100.0),
        high=np.full(n, 101.0),
        low=np.full(n, 89.0),
        close=np.linspace(100.0, 90.0, n),
    )
    entry = np.zeros(n)
    entry[0] = 1.0
    signals = ladder.SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    curve = ladder.Curve(
        "TEST",
        "linear",
        "green",
        "target",
        30.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    result = ladder._simulate(data, signals, curve, 0, n, 0.0, 0.0, "SHORT")
    assert result["side"] == "SHORT"
    assert result["capital_return_pct"] == 10.0
    assert result["bh_capital_return_pct"] == 10.0
    assert result["peak_post_fill_notional_usd"] == 2_000.0
    assert result["entry_capacity_breach"] is False


def test_short_reclaim_and_favorable_gap_are_exact_mirrors():
    n = 100
    close = np.full(n, 101.0)
    close[0:4] = [100.0, 100.0, 101.0, 101.0]
    close[4] = 97.0
    open_ = close.copy()
    open_[1] = 100.0
    open_[3] = 100.0
    open_[5] = 97.0
    high = close + 1.0
    high[4] = 103.0
    low = close - 1.0
    data = SimpleNamespace(
        ts=np.arange(n, dtype=np.int64),
        open=open_,
        high=high,
        low=low,
        close=close,
    )
    entry = np.zeros(n)
    entry[0] = 1.0
    exit_event = np.zeros(n, dtype=np.uint8)
    exit_event[2] = 1
    exit_ref = np.full(n, np.nan)
    exit_ref[2] = 98.0
    signals = ladder.SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=exit_event,
        exit_ref=exit_ref,
        causality={},
    )
    curve = ladder.Curve(
        "TEST",
        "linear",
        "green",
        "target",
        30.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    result = ladder._simulate(data, signals, curve, 0, n, 0.0, 0.0, "SHORT")
    assert result["exit_count"] == 1
    assert result["reclaim_reentries"] == 1
    assert result["bars_flat_beyond_reclaim"] == 0


def test_completed_htf_wt_state_trigger_is_persistent_and_causal():
    class FakeZ(dict):
        @property
        def files(self):
            return list(self)

    n = 120
    ts = np.arange(n, dtype=np.int64) * 300
    z = FakeZ()
    htfs = {}
    for slot, tf in enumerate(ladder.TF_ORDER):
        event_index = np.arange(1 + slot, n, 3 + slot, dtype=np.int64)
        source_ts = ts[event_index] - 1
        count = len(event_index)
        close = np.linspace(100.0, 110.0, count)
        htfs[tf] = ladder.top.HTFData(
            tf=tf,
            event_index=event_index,
            source_ts=source_ts,
            open=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            rsi=np.full(count, 50.0),
            atr=np.ones(count),
        )
        z[f"wt_cross_bull_{tf}"] = np.zeros(n, dtype=np.uint8)
        z[f"wt_cross_bear_{tf}"] = np.zeros(n, dtype=np.uint8)
        z[f"stoch_k_{tf}"] = np.full(n, 50.0)
        z[f"lrL_pct_b_{tf}"] = np.full(n, 0.5)
        z[f"wt1_{tf}"] = np.full(n, 2.0)
        z[f"wt2_{tf}"] = np.full(n, 1.0)
    data = ladder.top.ExecutionData(
        symbol="TEST",
        path="memory",
        ts=ts,
        open=np.full(n, 100.0),
        high=np.full(n, 101.0),
        low=np.full(n, 99.0),
        close=np.full(n, 100.0),
        synthetic=np.zeros(n, dtype=np.uint8),
        full_indices=np.arange(n, dtype=np.int64),
        z=z,
        contract={"valid": True},
    )
    curve = ladder.Curve(
        "STATE",
        "linear",
        "wt_state",
        "target",
        30.0,
        3.0,
        1.0,
        2.0,
        1.0,
        1.0,
        0.5,
    )
    signals = ladder._build_signals(data, htfs, curve, 30, "LONG")
    assert np.count_nonzero(signals.entry_mult) > 10
    assert all(
        row["source_timestamp_future_count"] == 0
        for row in signals.causality.values()
    )
    assert all(
        row["directional_wt_state_events"] == row["selected_events"]
        for row in signals.causality.values()
    )
