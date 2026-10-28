import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kline_stamp as K


def test_canonical_and_inputs():
    assert K.canonical_stamp("2026-10-28T15:45:00.000000Z") == "2026-10-28T15:45:00.000000Z"
    assert K.canonical_stamp("2026-10-28T15:45:00+00:00") == "2026-10-28T15:45:00.000000Z"
    assert K.canonical_stamp("2026-10-28T15:45:00Z") == "2026-10-28T15:45:00.000000Z"
    assert K.canonical_stamp("2026-03-24T00:00:00-04:00") == "2026-03-24T04:00:00.000000Z"
    assert K.canonical_stamp("2026-10-28") == "2026-10-28T00:00:00.000000Z"
    assert K.canonical_stamp("2026-10-16.000000Z") == "2026-10-16T00:00:00.000000Z"
    assert K.canonical_stamp(1793213100) == "2026-10-28T18:45:00.000000Z"
    assert K.canonical_stamp(1793213100000) == "2026-10-28T18:45:00.000000Z"
    assert K.canonical_stamp(datetime(2026, 10, 28, 15, 45, tzinfo=timezone(timedelta(hours=-4)))) == "2026-10-28T19:45:00.000000Z"
    for bad in ("NaT", None, "", "garbage"):
        try:
            K.canonical_stamp(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_normalize_rows_convert_only_never_removes():
    rows = [{"timestamp": "2024-10-16", "close": 1}, {"timestamp": "2024-10-16T20:00:00.000000Z", "close": 2}, {"timestamp": "2024-10-17T20:00:00+00:00", "close": 3, "timestamp_dt": "NaT"}]
    out, st = K.normalize_rows(rows)
    assert len(out) == 3 and st["dropped"] == 0 and st["converted"] == 2
    assert all(K.is_canonical(r["timestamp"]) for r in out)
    assert out[2]["timestamp_dt"] == out[2]["timestamp"]


def test_normalize_rows_drops_day_duplicates_only_when_asked():
    rows = [{"timestamp": "2024-10-16", "close": 1}, {"timestamp": "2024-10-16T20:00:00.000000Z", "close": 2}, {"timestamp": "2024-10-18", "close": 4}]
    out, st = K.normalize_rows(rows, drop_day_duplicates=True)
    assert [r["close"] for r in out] == [2, 4] and st["dropped"] == 1
    assert out[1]["timestamp"] == "2024-10-18T00:00:00.000000Z"
