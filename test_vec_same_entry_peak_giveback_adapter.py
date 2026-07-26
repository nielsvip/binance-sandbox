import ctypes
from types import SimpleNamespace

import numpy as np

from tools import vec_same_entry_peak_giveback_adapter as peak


def test_peak_giveback_registry_grid_and_live_mismatch():
    rows = peak.registry_grid()
    assert len(rows) == 60
    assert {row.arm_gain_pct for row in rows} == {0.5, 1.0, 2.0, 4.0, 8.0}
    assert {row.giveback_fraction for row in rows} == {
        0.20,
        0.33,
        0.50,
        0.67,
    }
    assert {row.reduce_fraction for row in rows} == {0.25, 0.5, 1.0}


def _synthetic_scan(reduce_fraction):
    n = 220
    ts = np.arange(n, dtype=np.int64) * 300
    close = np.full(n, 100.0)
    close[2:40] = np.linspace(100.0, 110.0, 38)
    close[40:] = 104.0
    open_ = close.copy()
    high = close + 0.2
    low = close - 0.2
    entry = np.zeros(n)
    entry[0] = 1.0
    zero = np.zeros(n, dtype=np.uint8)
    zero_source = np.zeros(n, dtype=np.int64)
    blank = np.full(n, np.nan)
    out = peak.compiled.PartialMetrics()
    rc = peak._library().vec_same_entry_peak_giveback_scan(
        n,
        0,
        n,
        1,
        1,
        ts,
        open_,
        high,
        low,
        close,
        entry,
        2,
        zero,
        zero_source,
        blank,
        blank,
        2.0,
        0.5,
        reduce_fraction,
        0.0,
        0.0,
        ctypes.byref(out),
    )
    assert rc == 0
    return out


def test_peak_giveback_partial_is_bounded_and_reports_realized_pnl():
    out = _synthetic_scan(0.5)
    assert out.partial_signals == 1
    assert out.partial_exit_fills == 1
    assert out.full_exit_fills == 0
    assert out.realized_partial_net_usd > 0
    assert out.reclaim_obligations_created == 1


def test_peak_giveback_fraction_one_is_full_exit_not_partial():
    out = _synthetic_scan(1.0)
    assert out.partial_signals == 1
    assert out.partial_exit_fills == 0
    assert out.full_exit_fills == 1
    assert out.realized_full_net_usd > 0


def test_every_fold_gate_rejects_missing_exit_or_bad_exposure():
    evidence = {
        "peak_giveback_signals": 1,
        "actual_exit_fills": 1,
        "alpha_vs_bh_pp": 1.0,
        "alpha_vs_same_entry_e02_pp": 0.5,
        "weighted_tim_pct": 75.0,
        "unfilled_obligations": 0,
        "bars_flat_beyond_reclaim": 0,
        "future_htf_source_count": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
    }
    assert peak._fold_pass(evidence, 70.0, 80.0)
    broken = dict(evidence, actual_exit_fills=0)
    assert not peak._fold_pass(broken, 70.0, 80.0)
    broken = dict(evidence, weighted_tim_pct=80.1)
    assert not peak._fold_pass(broken, 70.0, 80.0)
