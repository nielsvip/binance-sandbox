from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import run_ttd_short_regime_exit_followup as study


class FakeZ(dict):
    @property
    def files(self):
        return list(self)


def _fixture():
    n = 24
    ts = np.arange(n, dtype=np.int64) * 3600
    close = np.linspace(100, 92, n)
    high = close + 1
    low = close - 1
    atr = np.ones(n)
    h = SimpleNamespace(
        tf="1h",
        event_index=np.arange(n, dtype=np.int64),
        source_ts=ts.copy(),
        open=close.copy(),
        high=high,
        low=low,
        close=close,
        atr=atr,
    )
    z = FakeZ()
    for key, values in {
        "close_4h": close,
        "ema_50_4h": np.full(n, 110.0),
        "wt1_4h": np.full(n, -10.0),
        "wt2_4h": np.full(n, 0.0),
        "close_D": close,
        "ema_20_D": np.full(n, 110.0),
        "timestamp_4h": ts,
        "timestamp_D": ts,
        "wt1_1h": np.full(n, -10.0),
        "wt2_1h": np.full(n, 0.0),
        "stoch_k_1h": np.full(n, 20.0),
    }.items():
        z[key] = np.asarray(values)
    data = SimpleNamespace(
        z=z,
        full_indices=np.arange(n, dtype=np.int64),
        ts=ts,
    )
    return data, h


def test_adverse_emergency_requires_no_intervening_pullback():
    data, h = _fixture()
    bundle = study.ExitBundle(
        "ADVERSE_TEST",
        ("adverse",),
        "1h",
        3,
        .5,
        50,
        3,
        4,
        3,
        0.0,
        5,
        .5,
        1.0,
    )
    # Force an upside break and uninterrupted continuation.
    h.close[8:12] = [96, 98, 100, 102]
    h.high[8:12] = h.close[8:12] + .5
    h.low[8:12] = h.close[8:12] - .5
    data.z["wt1_1h"][8:12] = 10
    event, _, audit = study.build_exit_signal(data, {"1h": h}, bundle)
    assert audit["signal_counts"]["adverse_emergency"] >= 1
    assert audit["causality"]["decision_parent_future_count"] == 0
    assert np.count_nonzero(event) >= 1


def test_gate_enforces_alpha_tim_reclaim_and_capacity():
    good = {
        "deployed_alpha_vs_bh_or_cash_pp": 1.0,
        "exposure_weighted_tim_pct": 70.0,
        "fill_ratio": 1.0,
        "bars_flat_beyond_reclaim": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "exit_count": 3,
    }
    assert study._gate(good, 65, 80) == []
    bad = {
        **good,
        "deployed_alpha_vs_bh_or_cash_pp": -1.0,
        "exposure_weighted_tim_pct": 81.0,
        "bars_flat_beyond_reclaim": 2,
    }
    assert study._gate(bad, 65, 80) == [
        "NOT_ABOVE_BH_OR_CASH",
        "TIM_OUTSIDE_65_80",
        "FORGOTTEN_RECLAIM",
    ]


def test_frozen_curves_are_the_verified_ttd_n30_entry_profiles():
    assert study.FROZEN_CURVES[1].label == "BLOCK069"
    assert study.FROZEN_CURVES[1].semantics == "target"
    assert study.FROZEN_CURVES[2].label == "ARC3_linear_structure_target"
    assert study.NPZ_SHA256.startswith("56d874e0")
