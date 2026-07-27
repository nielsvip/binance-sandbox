import ctypes
import math

import numpy as np

from tools.vec_same_entry_partial_adapter import (
    PartialMetrics,
    _library,
    registry_grid,
)


def _terminal_oracle(side, entry_open, terminal_close, target, commission, slip):
    entry_px = entry_open * (1.0 + side * slip)
    qty = target / entry_px
    cash = 10_000.0 - side * target - commission * target
    exit_px = terminal_close * (1.0 - side * slip)
    notional = qty * exit_px
    cash += side * (notional - side * commission * notional)
    return 100.0 * (cash - 10_000.0) / 2_000.0


def _clock_fixture(side, *, slow_source=None):
    n = 120
    # Four source rows share one parent close, then three share the next.
    availability = np.concatenate(
        (
            np.full(4, 400, dtype=np.int64),
            np.full(3, 700, dtype=np.int64),
            1000 + np.arange(n - 7, dtype=np.int64) * 300,
        )
    )
    price = np.full(n, 10.0, dtype=np.float64)
    price[-1] = 12.0
    high = np.maximum(price, 10.2)
    low = np.minimum(price, 9.8)
    entry = np.zeros(n, dtype=np.float64)
    entry[0] = 4.0
    fast = np.zeros(n, dtype=np.uint8)
    slow = np.zeros(n, dtype=np.uint8)
    source = availability.copy()
    if slow_source is not None:
        slow[5] = 1
        source[5] = int(slow_source)
    ref = np.full(n, 10.0, dtype=np.float64)
    blank = np.full(n, np.nan, dtype=np.float64)
    regime = np.zeros(n, dtype=np.uint8)
    out = PartialMetrics()
    rc = _library().vec_same_entry_partial_scan(
        n, 0, n, side, 0,
        availability, price, high, low, price, entry,
        fast, source, ref,
        2, slow, source, blank, ref, regime,
        0.25, 0.0, 0, 0.0005, 0.0002, ctypes.byref(out),
    )
    return rc, out


def test_partial_registry_grid_arithmetic_is_192_not_128():
    rows = registry_grid()
    assert len(rows) == 192
    assert all(
        row.first_clip_fraction + row.second_clip_fraction <= 0.8300000001
        for row in rows
    )
    assert {row.fast_family for row in rows} == {"E05", "E06", "WT"}
    assert {row.slow_family for row in rows} == {"E01", "E02"}
    assert {row.regime_switch for row in rows} == {False, True}


def test_compiled_partial_has_two_fills_and_persistent_clip_reclaims():
    n = 120
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    price = np.full(n, 10.0, dtype=np.float64)
    high = np.full(n, 10.2, dtype=np.float64)
    low = np.full(n, 9.8, dtype=np.float64)
    entry = np.zeros(n, dtype=np.float64)
    entry[0] = 4.0
    fast = np.zeros(n, dtype=np.uint8)
    fast[[5, 10]] = 1
    slow = np.zeros(n, dtype=np.uint8)
    slow[20] = 1
    source = ts - 1
    ref = np.full(n, 10.0, dtype=np.float64)
    blank = np.full(n, np.nan, dtype=np.float64)
    regime = np.zeros(n, dtype=np.uint8)
    out = PartialMetrics()
    rc = _library().vec_same_entry_partial_scan(
        n, 0, n, 1, 0,
        ts, price, high, low, price, entry,
        fast, source, ref,
        2, slow, source, blank, ref, regime,
        0.25, 0.15, 0, 0.0005, 0.0005, ctypes.byref(out),
    )
    assert rc == 0
    assert out.partial_exit_fills == 2
    assert out.full_exit_fills == 1
    assert out.technical_exit_fills == 3
    assert out.reclaim_obligations_created == 3
    assert out.reclaim_obligations_filled == 3
    assert out.reclaim_obligations_unfilled_at_end == 0
    assert out.reclaim_reentries == 3
    assert out.realized_partial_gross_usd < 0
    assert out.realized_partial_net_usd < out.realized_partial_gross_usd
    assert out.entry_capacity_breach == 0
    assert out.future_htf_count == 0


def test_regime_switch_vetoes_fast_clips_when_daily_regime_aligned():
    n = 120
    ts = np.arange(1, n + 1, dtype=np.int64) * 300
    price = np.full(n, 10.0, dtype=np.float64)
    high = price + 0.2
    low = price - 0.2
    entry = np.zeros(n, dtype=np.float64)
    entry[0] = 4.0
    fast = np.zeros(n, dtype=np.uint8)
    fast[[5, 10]] = 1
    empty = np.zeros(n, dtype=np.uint8)
    source = ts - 1
    ref = price.copy()
    blank = np.full(n, np.nan, dtype=np.float64)
    regime = np.ones(n, dtype=np.uint8)
    out = PartialMetrics()
    rc = _library().vec_same_entry_partial_scan(
        n, 0, n, 1, 0,
        ts, price, high, low, price, entry,
        fast, source, ref,
        2, empty, source, blank, ref, regime,
        0.25, 0.15, 1, 0.0005, 0.0005, ctypes.byref(out),
    )
    assert rc == 0
    assert out.partial_exit_fills == 0
    assert out.regime_vetoed_partial_signals == 2


def test_terminal_open_long_and_short_match_single_liquidation_oracle():
    for side in (1, -1):
        rc, out = _clock_fixture(side)
        assert rc == 0
        expected = _terminal_oracle(
            side, 10.0, 12.0, 8_000.0, 0.0005, 0.0002
        )
        assert math.isclose(
            out.capital_return_pct, expected, rel_tol=0, abs_tol=1e-9
        )
        assert out.entry_fills == 1
        assert out.full_exit_fills == 0
        assert out.realized_full_net_usd != 0.0


def test_duplicate_parent_batch_fills_at_first_strictly_later_availability():
    rc, out = _clock_fixture(1)
    assert rc == 0
    # Signal at availability 400 cannot fill on any of its three siblings.
    # It fills once at the first row released at 700.
    assert out.entry_fills == 1
    assert math.isclose(out.requested_notional_usd, 8_000.0)


def test_native_and_synthetic_parent_close_sources_share_causal_fill_rule():
    synthetic_rc, synthetic = _clock_fixture(1, slow_source=400)
    native_rc, native = _clock_fixture(1, slow_source=700)
    assert synthetic_rc == native_rc == 0
    assert synthetic.future_htf_count == native.future_htf_count == 0
    assert synthetic.full_exit_fills == native.full_exit_fills == 1
    assert math.isclose(
        synthetic.capital_return_pct,
        native.capital_return_pct,
        rel_tol=0,
        abs_tol=1e-12,
    )
