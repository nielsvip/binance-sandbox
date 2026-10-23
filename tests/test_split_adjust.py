"""Split adjustment: forward/reverse/multi/no-split/missing-data (USER 2026-10-23).

Pure-function pins for split_adjust.py. All series are synthetic (no network);
the REAL NVDA 10:1 2024-06-10 demonstration lives in /tmp (throwaway probe).
Gain linkage: calculate_weighted_gains (ez_rankings.py:3525) is a weighted
per-bar pct_change, so asserting max |pct_change| collapses across the split
bar pins exactly the corruption ranked scores would otherwise ingest.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import split_adjust as SA  # noqa: E402


def _frame(closes, volumes=None, start=date(2024, 6, 3)):
    idx = [start + timedelta(days=i) for i in range(len(closes))]
    data = {"close_time": pd.to_datetime(idx), "open": closes, "high": closes, "low": closes, "close": closes}
    data["volume"] = volumes if volumes is not None else [1000.0] * len(closes)
    return pd.DataFrame(data)


def _max_jump(frame):
    return float(pd.to_numeric(frame["close"], errors="coerce").pct_change().abs().max() * 100.0)


def test_forward_split_removes_minus_90_jump():
    raw = _frame([1000.0, 1010.0, 1005.0, 101.0, 102.0, 101.5])
    adj, rep = SA.apply_split_adjustment(raw, [{"ex_date": "2024-06-06", "factor": 10.0}], symbol="NVDA")
    assert rep["adjusted"] is True and rep["verified"] is True and rep["n_splits"] == 1
    assert _max_jump(raw) > 80.0
    assert _max_jump(adj) < 2.0
    assert adj["close"].iloc[0] == pytest.approx(100.0)
    assert adj["close"].iloc[3] == pytest.approx(101.0)
    assert adj["volume"].iloc[0] == pytest.approx(10000.0)
    assert adj["volume"].iloc[3] == pytest.approx(1000.0)


def test_reverse_split_removes_plus_1900_jump():
    raw = _frame([5.0, 5.1, 4.9, 100.0, 101.0, 99.0])
    adj, rep = SA.apply_split_adjustment(raw, [{"ex_date": "2024-06-06", "factor": 0.05}], symbol="XYZ")
    assert rep["adjusted"] is True and rep["verified"] is True
    assert _max_jump(raw) > 1000.0
    assert _max_jump(adj) < 5.0
    assert adj["close"].iloc[0] == pytest.approx(100.0)
    assert adj["close"].iloc[3] == pytest.approx(100.0)


def test_no_split_control_is_identical_and_verified():
    raw = _frame([50.0, 51.0, 50.5, 52.0, 51.0])
    adj, rep = SA.apply_split_adjustment(raw, [], symbol="AAPL")
    assert rep["adjusted"] is False and rep["verified"] is True and rep["reason"] is None
    pd.testing.assert_frame_equal(adj, raw)


def test_multi_split_chain_compounds():
    raw = _frame([400.0, 404.0, 101.0, 102.0, 10.2, 10.3])
    splits = [{"ex_date": "2024-06-05", "factor": 4.0}, {"ex_date": "2024-06-07", "factor": 10.0}]
    adj, rep = SA.apply_split_adjustment(raw, splits, symbol="NVDA")
    assert rep["n_splits"] == 2 and _max_jump(adj) < 2.0
    assert adj["close"].iloc[0] == pytest.approx(10.0)
    assert adj["close"].iloc[2] == pytest.approx(10.1)
    assert adj["volume"].iloc[0] == pytest.approx(40000.0)


def test_fractional_split_factor():
    raw = _frame([30.0, 30.3, 20.2, 20.4])
    adj, rep = SA.apply_split_adjustment(raw, [{"ex_date": "2024-06-05", "factor": 1.5}], symbol="NVDA")
    assert rep["adjusted"] is True and _max_jump(adj) < 2.0
    assert adj["close"].iloc[0] == pytest.approx(20.0)


def test_missing_data_fails_closed():
    raw = _frame([100.0, 101.0, 10.0, 10.1])
    adj, rep = SA.apply_split_adjustment(raw, None, symbol="NVDA")
    assert rep["adjusted"] is False and rep["verified"] is False and rep["reason"] == SA.NO_COVERAGE
    pd.testing.assert_frame_equal(adj, raw)


def test_all_invalid_events_fail_closed():
    raw = _frame([100.0, 101.0, 10.0, 10.1])
    bad = [{"ex_date": "2024-06-05", "factor": 0.0}, {"ex_date": "not-a-date", "factor": 10.0}, {"ex_date": "2024-06-05", "factor": 1.0}]
    adj, rep = SA.apply_split_adjustment(raw, bad, symbol="NVDA")
    assert rep["verified"] is False and rep["adjusted"] is False and rep["reason"] == "ALL_EVENTS_INVALID"
    assert rep["n_invalid_dropped"] == 3
    pd.testing.assert_frame_equal(adj, raw)


def test_split_on_non_trading_day():
    friday = date(2024, 6, 7)
    raw = _frame([1000.0, 101.0, 101.5, 102.0], start=friday)
    adj, rep = SA.apply_split_adjustment(raw, [{"ex_date": "2024-06-08", "factor": 10.0}], symbol="NVDA")
    assert rep["adjusted"] is True and _max_jump(adj) < 2.0
    assert adj["close"].iloc[0] == pytest.approx(100.0)
    assert adj["close"].iloc[1] == pytest.approx(101.0)


def test_dollar_volume_invariant_and_input_untouched():
    raw = _frame([1000.0, 1010.0, 101.0, 102.0], volumes=[100.0, 110.0, 5000.0, 5100.0])
    snapshot = raw.copy(deep=True)
    adj, _ = SA.apply_split_adjustment(raw, [{"ex_date": "2024-06-05", "factor": 10.0}], symbol="NVDA")
    pd.testing.assert_frame_equal(raw, snapshot)
    for i in range(len(raw)):
        assert adj["close"].iloc[i] * adj["volume"].iloc[i] == pytest.approx(raw["close"].iloc[i] * raw["volume"].iloc[i])


def test_to_equity_symbol():
    assert SA.to_equity_symbol("NVDAUSDT") == "NVDA"
    assert SA.to_equity_symbol("AAPLUSDC") == "AAPL"
    assert SA.to_equity_symbol("NVDA") == "NVDA"
    assert SA.to_equity_symbol("") is None
    assert SA.to_equity_symbol(None) is None
