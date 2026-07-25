import ctypes
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import vec_top_exit_campaign as base  # noqa: E402
import vec_top_exit_walkforward as wf  # noqa: E402


def _htf(
    high,
    low,
    close,
    *,
    source_ts=None,
    rsi=None,
    atr=None,
    tf="4h",
):
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    return base.HTFData(
        tf=tf,
        event_index=np.arange(n, dtype=np.int64),
        source_ts=np.asarray(
            source_ts if source_ts is not None else np.arange(1, n + 1),
            dtype=np.int64,
        ),
        open=np.asarray(close, dtype=np.float64),
        high=high,
        low=low,
        close=close,
        rsi=np.asarray(rsi if rsi is not None else np.full(n, 50.0), dtype=np.float64),
        atr=np.asarray(atr if atr is not None else np.ones(n), dtype=np.float64),
    )


def test_e03_requires_distinct_arm_rebound_and_failed_retest_bars():
    h = _htf(
        high=[110, 108, 106, 108, 107, 106],
        low=[100, 98, 96, 99, 95, 94],
        close=[108, 100, 98, 107, 98, 95],
    )
    event, ref = wf._e03_signal(
        h,
        side=1,
        confirm_bars=2,
        damage_atr=1.0,
        rebound_atr=0.5,
        max_wait=8,
    )
    assert np.flatnonzero(event).tolist() == [4]
    assert ref[4] == 108
    assert not event[2]  # arm
    assert not event[3]  # favorable rebound/retest


def test_e09_never_triggers_before_completed_htf_arm():
    arm = _htf(
        high=[101, 101, 121, 121],
        low=[99, 99, 119, 119],
        close=[100, 100, 120, 120],
        source_ts=[4, 8, 12, 16],
        rsi=[50, 50, 80, 50],
        atr=[1, 1, 1, 1],
        tf="4h",
    )
    hours = np.arange(1, 21)
    high = np.full(20, 100.0)
    low = np.full(20, 99.0)
    close = np.full(20, 99.5)
    # Adverse structure exists before the 12h arm, but must be ignored.
    high[8], low[8], close[8] = 98, 97, 97
    # The first post-arm adverse completed 1h bar can trigger.
    high[11], low[11], close[11] = 105, 100, 104
    high[12], low[12], close[12] = 104, 99, 99
    trigger = _htf(
        high,
        low,
        close,
        source_ts=hours,
        atr=np.ones(20),
        tf="1h",
    )
    event, ref = wf._e09_signal(
        arm,
        trigger,
        side=1,
        rsi_threshold=70,
        extension_atr=1.0,
        expiry_1h=12,
        confirm_bars=1,
    )
    assert not np.any(event[:12])
    assert np.flatnonzero(event).tolist() == [12]
    assert ref[12] == 105


def test_robust_selection_penalizes_single_period_outlier():
    stable = [
        {
            "equity_ratio_vs_bh": 1.10,
            "tim_rth_pct": 75,
            "mandatory_reclaim_policy": True,
        }
        for _ in range(3)
    ]
    outlier = [
        {
            "equity_ratio_vs_bh": ratio,
            "tim_rth_pct": 75,
            "mandatory_reclaim_policy": True,
        }
        for ratio in (0.80, 0.80, 3.0)
    ]
    stable_score, _ = wf._selection_score(stable)
    outlier_score, _ = wf._selection_score(outlier)
    assert stable_score > outlier_score


def test_scanner_cost_matches_engine_round_trip_entry_value_model():
    lib = base._compile_scanner()
    n = 2
    ts = np.ascontiguousarray([0, 300], dtype=np.int64)
    px = np.ascontiguousarray([100.0, 100.0], dtype=np.float64)
    blank = np.ascontiguousarray([np.nan, np.nan], dtype=np.float64)
    zeros = np.ascontiguousarray([0, 0], dtype=np.uint8)
    atr = np.ascontiguousarray([1.0, 1.0], dtype=np.float64)
    metrics = base.ScanMetrics()
    rc = lib.vec_top_exit_scan(
        n,
        1,
        2,
        0,
        ts,
        px,
        px,
        px,
        px,
        atr,
        zeros,
        blank,
        blank,
        zeros,
        0.0,
        0.0,
        0.0005,  # 5bp one-way -> engine 10bp round-trip at close
        0.0,
        ctypes.byref(metrics),
    )
    assert rc == 0
    assert abs(metrics.final_equity - 0.999) < 1e-12


def test_scanner_matches_independent_engine_dollar_ledger_across_reentry():
    """Do not use the Python vector replay as the oracle.

    This reproduces the faithful engine's dollar ledger directly: deploy all
    acknowledged equity, realize price P&L, then subtract the round-trip fee on
    entry notional.  The second quantity therefore depends on realized equity,
    never on a schedule's cached ``equity_after_fill`` field.
    """
    lib = base._compile_scanner()
    n = 7
    ts = np.ascontiguousarray(np.arange(n, dtype=np.int64) * 300)
    open_px = np.ascontiguousarray(
        np.array([100, 102, 98, 101, 103, 99, 100], dtype=np.float64)
    )
    high = np.ascontiguousarray(open_px + 1.0)
    low = np.ascontiguousarray(open_px - 1.0)
    close = np.ascontiguousarray(open_px.copy())
    atr = np.ascontiguousarray(np.ones(n, dtype=np.float64))
    exit_event = np.ascontiguousarray(
        np.array([0, 1, 0, 0, 0, 0, 0], dtype=np.uint8)
    )
    blank = np.ascontiguousarray(np.full(n, np.nan, dtype=np.float64))
    lower = np.ascontiguousarray(np.zeros(n, dtype=np.uint8))
    metrics = base.ScanMetrics()
    rc = lib.vec_top_exit_scan(
        n,
        1,
        2,
        1,  # zero-buffer E10 reclaim
        ts,
        open_px,
        high,
        low,
        close,
        atr,
        exit_event,
        blank,
        blank,
        lower,
        0.0,
        0.0,
        0.0005,
        0.0,
        ctypes.byref(metrics),
    )
    assert rc == 0

    # Engine-dollar oracle, independent of both vector implementations.
    equity_usd = 2_000.0
    first_entry = 100.0
    first_qty = equity_usd / first_entry
    equity_usd += (98.0 - first_entry) * first_qty
    equity_usd -= 0.001 * first_entry * first_qty
    # Reclaim is observed at close 103 and fills at the next bar's open 99.
    second_entry = 99.0
    second_qty = equity_usd / second_entry
    equity_usd += (100.0 - second_entry) * second_qty
    equity_usd -= 0.001 * second_entry * second_qty

    assert abs(metrics.final_equity * 2_000.0 - equity_usd) < 1e-9


def test_vt_known_source_gap_is_not_hidden_by_interpolated_timestamps():
    assert wf.KNOWN_SOURCE_GAPS["VT"] == (
        (wf.date(2026, 3, 30), wf.date(2026, 6, 8)),
    )


def test_e06_regression_features_are_prior_only():
    close = np.exp(np.linspace(4.0, 4.5, 40))
    original = wf._rolling_regression_prior(close, 10)
    changed = close.copy()
    changed[20:] *= 5.0
    perturbed = wf._rolling_regression_prior(changed, 10)
    for original_feature, perturbed_feature in zip(original, perturbed):
        assert original_feature[20] == perturbed_feature[20]


def test_e08_cannot_exit_before_mfe_activation_then_locks_profit():
    lib = base._compile_scanner()
    n = 6
    ts = np.ascontiguousarray(np.arange(n, dtype=np.int64) * 300)
    open_px = np.ascontiguousarray(np.full(n, 100.0))
    high = np.ascontiguousarray([100, 101, 103, 104, 104, 104], dtype=np.float64)
    low = np.ascontiguousarray([99, 100, 102, 102, 100, 99], dtype=np.float64)
    close = np.ascontiguousarray([100, 101, 103, 103, 101, 100], dtype=np.float64)
    atr = np.ascontiguousarray(np.ones(n))
    completed = np.ascontiguousarray(np.ones(n, dtype=np.uint8))
    q = np.ascontiguousarray(np.full(n, 2.0))
    k = np.ascontiguousarray(np.full(n, 1.5))
    no_reentry = np.ascontiguousarray(np.zeros(n, dtype=np.uint8))
    metrics = base.ScanMetrics()
    rc = lib.vec_top_exit_scan(
        n,
        1,
        3,
        0,
        ts,
        open_px,
        high,
        low,
        close,
        atr,
        completed,
        q,
        k,
        no_reentry,
        0.0,
        0.0,
        0.0,
        0.0,
        ctypes.byref(metrics),
    )
    assert rc == 0
    assert metrics.technical_exits == 1

    data = base.ExecutionData(
        symbol="TEST",
        path="memory",
        ts=ts,
        open=open_px,
        high=high,
        low=low,
        close=close,
        synthetic=np.zeros(n, dtype=np.uint8),
        full_indices=np.arange(n, dtype=np.int64),
        z=None,
        contract={"valid": True},
    )
    candidate = base.ExitCandidate(
        family="E08_MFE_PROFIT_LOCK",
        label="E08_TEST",
        params={},
        exit_mode=3,
        exit_event=completed,
        raw_stop=q,
        struct_ref=k,
        atr_exec=atr,
    )
    bh = base._side_bh(data, 1, 0.0, 0.0)
    compiled = base._scan(
        lib,
        data,
        candidate,
        no_reentry,
        1,
        ("NONE", 0, 0.0, 0.0),
        0.0,
        0.0,
        bh,
    )
    reference, _events = base._reference_replay(
        data,
        candidate,
        no_reentry,
        1,
        ("NONE", 0, 0.0, 0.0),
        0.0,
        0.0,
    )
    assert base._parity_audit(compiled, reference)["status"] == "PASS"
