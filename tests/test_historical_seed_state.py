import numpy as np
import pytest

from tools.historical_seed_state import (
    position_mark_at_cutoff,
    resolve_cutoff_index,
    validate_historical_seed,
)


def _seed(position=None):
    return {
        "snapshot_kind": "historical_cutoff",
        "account": "ang",
        "as_of_bar_ts": 1800,
        "as_of_decision_ts": 2700,
        "source": "native-prefix-checkpoint",
        "positions": {
            "ang:BTCUSDT_LONG": position or {
                "positionAmt": 2.0,
                "entry_price": 100.0,
                "position_side": "LONG",
                "opened_at": "1970-01-01T00:10:00Z",
                "max_gain": 8.5,
                "last_augmentation_time": "1970-01-01T00:20:00Z",
                "last_reduction_time": None,
            }
        },
    }


def test_resolve_exact_second_and_millisecond_cutoffs():
    assert resolve_cutoff_index(np.array([0, 900, 1800, 2700]), 1800) == (2, 1800.0)
    assert resolve_cutoff_index(np.array([1_700_000_000_000, 1_700_000_900_000, 1_700_001_800_000]), 1_700_001_800) == (2, 1_700_001_800.0)


def test_resolve_cutoff_rejects_off_grid_and_unsorted_inputs():
    with pytest.raises(ValueError, match="exact stored bar"):
        resolve_cutoff_index(np.array([0, 900, 1800]), 1800.5)
    with pytest.raises(ValueError, match="strictly increasing"):
        resolve_cutoff_index(np.array([0, 1800, 900]), 1800)


def test_seed_header_requires_exact_account_and_completed_cutoff_clock():
    seed = _seed()
    result = validate_historical_seed(seed, "ang", 1800, 900)
    assert result["bar_ts"] == 1800
    assert result["decision_ts"] == 2700
    with pytest.raises(ValueError, match="account"):
        validate_historical_seed(seed, "inf", 1800, 900)
    with pytest.raises(ValueError, match="scored cutoff"):
        validate_historical_seed(seed, "ang", 900, 900)
    seed["as_of_decision_ts"] = 1800
    with pytest.raises(ValueError, match="completed cutoff bar"):
        validate_historical_seed(seed, "ang", 1800, 900)


def test_historical_seed_rejects_non_historical_or_future_lifecycle_state():
    seed = _seed()
    seed["snapshot_kind"] = "live_current"
    with pytest.raises(ValueError, match="snapshot_kind"):
        validate_historical_seed(seed, "ang", 1800, 900)
    seed = _seed()
    seed["positions"]["ang:BTCUSDT_LONG"]["last_augmentation_time"] = "1970-01-01T00:50:00Z"
    with pytest.raises(ValueError, match="future last_augmentation_time"):
        validate_historical_seed(seed, "ang", 1800, 900)


def test_cutoff_mark_uses_exact_cutoff_price_for_both_sides_without_mutating_lifecycle():
    original = {
        "positionAmt": 2.0,
        "entry_price": 100.0,
        "position_side": "LONG",
        "opened_at": "1970-01-01T00:10:00Z",
        "max_gain": 8.5,
        "last_augmentation_time": "1970-01-01T00:20:00Z",
    }
    before = dict(original)
    timestamps = np.array([0, 900, 1800, 2700], dtype=np.float64)
    prices = np.array([90, 95, 110, 120], dtype=np.float64)
    long_state = position_mark_at_cutoff(original, timestamps, lambda idx: prices[idx], 1800)
    assert long_state == {"index": 2, "bar_ts": 1800.0, "mark_price": 110.0, "gain": 10.0}
    assert original == before
    short = dict(original, position_side="SHORT")
    short_state = position_mark_at_cutoff(short, timestamps, lambda idx: prices[idx], 1800)
    assert short_state["gain"] == -10.0


def test_cutoff_mark_rejects_missing_price_and_invalid_side():
    timestamps = np.array([0, 900, 1800], dtype=np.float64)
    base = {"entry_price": 100, "position_side": "LONG"}
    with pytest.raises(ValueError, match="cutoff price"):
        position_mark_at_cutoff(base, timestamps, lambda idx: 0, 1800)
    with pytest.raises(ValueError, match="position_side"):
        position_mark_at_cutoff(dict(base, position_side="BOTH"), timestamps, lambda idx: 101, 1800)
