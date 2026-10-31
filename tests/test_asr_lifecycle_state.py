import numpy as np
import pytest

from vec_decisions.asr_lifecycle_state import build_lifecycle_arrays


def test_builds_predecision_causal_lifecycle_and_persists_exit_state():
    ts = np.arange(6, dtype=float) * 900 + 1000
    px = np.array([10, 10, 12, 11, 9, 9], dtype=float)
    events = [
        {"type": "OPEN", "bar": 1, "ts": ts[1], "price": 10.0, "qty": 2.0},
        {"type": "AUGMENT", "bar": 3, "ts": ts[3], "price": 12.0, "qty": 1.0},
        {"type": "REDUCE", "bar": 4, "ts": ts[4], "price": 11.0, "qty": 1.0},
    ]
    result = build_lifecycle_arrays(events, [], ts, px, True, {"position_exists": False})
    assert result["position_exists"].tolist() == [False, False, True, True, True, True]
    assert result["position_amt"].tolist() == [0.0, 0.0, 2.0, 2.0, 3.0, 2.0]
    assert result["avg_entry_price"][4] == pytest.approx(10.0 + 2.0 / 3.0)
    assert result["last_augmentation_ts"][3] == 0.0
    assert result["last_augmentation_ts"][4] == ts[3]
    assert result["last_reduction_price"][5] == 11.0
    assert result["gain_pct"][5] == pytest.approx((9 / (10 + 2 / 3) - 1) * 100)


def test_close_trade_is_applied_only_after_its_bar_and_retains_last_exit():
    ts = np.arange(5, dtype=float) * 900 + 1000
    px = np.full(5, 10.0)
    events = [{"type": "OPEN", "bar": 0, "ts": ts[0], "price": 10.0, "qty": 3.0}]
    trades = [{"type": "CLOSE", "bar_exit": 2, "ts": ts[2], "price": 11.0, "qty": 3.0}]
    result = build_lifecycle_arrays(events, trades, ts, px, True, {"position_exists": False})
    assert result["position_exists"].tolist() == [False, True, True, False, False]
    assert result["last_exit_ts"][2] == 0.0
    assert result["last_exit_ts"][3] == ts[2]
    assert result["recent_exit"][3]


def test_ambiguous_same_bar_trade_close_and_event_fails_closed():
    ts = np.array([1000.0, 1900.0])
    events = [
        {"type": "OPEN", "bar": 0, "ts": 1000.0, "price": 10.0, "qty": 1.0},
        {"type": "AUGMENT", "bar": 1, "ts": 1900.0, "price": 10.0, "qty": 1.0},
    ]
    trades = [{"type": "CLOSE", "bar_exit": 1, "ts": 1900.0, "price": 10.0, "qty": 2.0}]
    with pytest.raises(ValueError, match="ambiguous same-bar CLOSE"):
        build_lifecycle_arrays(events, trades, ts, np.array([10.0, 10.0]), True, {"position_exists": False})


def test_held_initial_state_requires_explicit_lifecycle_fields():
    with pytest.raises(ValueError, match="held initial_state missing position_amt"):
        build_lifecycle_arrays([], [], np.array([1000.0]), np.array([10.0]), True, {"position_exists": True})
