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
