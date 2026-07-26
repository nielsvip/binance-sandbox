from types import SimpleNamespace

import numpy as np

from tools import vec_same_entry_e05_adapter as e05


def test_e05_registry_grid_matches_active_candidate_function():
    rows = e05.registry_grid()
    assert len(rows) == 108
    assert {row.pivot_radius for row in rows} == {2, 3, 5}
    assert {row.divergence_min for row in rows} == {3.0, 5.0, 8.0}
    assert {row.break_buffer_atr for row in rows} == {0.0, 0.25}
    assert {row.rebound_atr for row in rows} == {0.25, 0.5}
    assert {row.max_wait_bars for row in rows} == {8, 12, 20}
    assert all(not row.result_params()["first_break_exit"] for row in rows)


def test_e05_exits_after_break_rebound_and_rollover_not_first_break(
    monkeypatch,
):
    n = 20
    ts = np.arange(1, n + 1, dtype=np.int64) * 14_400
    high = np.full(n, 101.0)
    low = np.full(n, 99.0)
    close = np.full(n, 100.0)
    rsi = np.full(n, 50.0)
    atr = np.full(n, 2.0)
    high[2], rsi[2] = 105.0, 70.0
    high[6], rsi[6] = 110.0, 60.0
    low[2:7] = [98.0, 97.0, 96.0, 95.0, 96.0]
    # Divergence is confirmed at row 8. Structural break occurs at row 9.
    close[8], close[9] = 100.0, 94.0
    high[9], low[9] = 95.0, 93.0
    # Row 10 rebounds by >0.5 ATR but has not rolled over.
    high[10], low[10], close[10] = 97.0, 94.0, 96.0
    # Row 11 is the later lower-high + close-below-prior-low rollover.
    high[11], low[11], close[11] = 96.0, 91.0, 92.0
    h4 = SimpleNamespace(
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts - 1,
        high=high,
        low=low,
        close=close,
        rsi=rsi,
        atr=atr,
    )
    data = SimpleNamespace(ts=ts)
    monkeypatch.setattr(
        e05.base,
        "_confirmed_pivots",
        lambda _values, _side, _radius: [(4, 2), (8, 6)],
    )
    setting = e05.E05Setting(2, 5.0, 0.0, 0.5, 12)

    events = e05._events(data, h4, "LONG", setting)

    assert events.event[9] == 0
    assert events.event[10] == 0
    assert events.event[11] == 1
    assert np.all(events.source[events.event > 0] <= ts[events.event > 0])


def test_e05_every_fold_gate_requires_signals_exits_alpha_tim_and_reclaim():
    evidence = {
        "raw_completed_signal_events": 2,
        "actual_exit_fills": 1,
        "alpha_vs_bh_pp": 1.0,
        "alpha_vs_same_entry_e02_pp": 0.5,
        "weighted_tim_pct": 75.0,
        "unfilled_obligations": 0,
        "insolvent": False,
    }
    assert e05._fold_pass(evidence, 70.0, 80.0)
    for field, value in (
        ("raw_completed_signal_events", 0),
        ("actual_exit_fills", 0),
        ("alpha_vs_bh_pp", 0.0),
        ("alpha_vs_same_entry_e02_pp", 0.0),
        ("weighted_tim_pct", 69.9),
        ("unfilled_obligations", 1),
        ("insolvent", True),
    ):
        broken = dict(evidence)
        broken[field] = value
        assert not e05._fold_pass(broken, 70.0, 80.0)
