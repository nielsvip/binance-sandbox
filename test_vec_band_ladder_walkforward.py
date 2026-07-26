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
