from types import SimpleNamespace

import numpy as np
import pytest

from tools.research_availability_clock import (
    AvailabilityClockError,
    apply_availability_clock,
    build_availability_clock,
    next_strictly_later_index,
)


class FakeZ(dict):
    @property
    def files(self):
        return list(self)


def _data(*, parent, synthetic=None):
    base = 1_800_000_000
    source = base + np.arange(6, dtype=np.int64) * 300
    synthetic = (
        np.array([1, 1, 1, 0, 0, 0], dtype=np.uint8)
        if synthetic is None
        else np.asarray(synthetic, dtype=np.uint8)
    )
    return SimpleNamespace(
        ts=source.copy(),
        open=np.arange(6, dtype=float) + 10.0,
        high=np.arange(6, dtype=float) + 11.0,
        low=np.arange(6, dtype=float) + 9.0,
        close=np.arange(6, dtype=float) + 10.5,
        synthetic=synthetic,
        full_indices=np.arange(6, dtype=np.int64),
        z=FakeZ(
            timestamps=source,
            synthetic_5m_parent_close_ts=np.asarray(parent, dtype=np.int64),
        ),
    )


def test_parent_clock_uses_10_5_0_minute_lags_and_keeps_all_rows():
    base = 1_800_000_000
    source = base + np.arange(6, dtype=np.int64) * 300
    parent = source.copy()
    parent[:3] = base + 600
    data = _data(parent=parent)
    clock = build_availability_clock(data)

    assert list(clock.availability_ts[:3]) == [base + 600] * 3
    assert list(clock.source_row_index[:3]) == [0, 1, 2]
    assert list(clock.availability_ts[:3] - clock.source_ts[:3]) == [
        600,
        300,
        0,
    ]
    assert len(clock.availability_ts) == 6
    assert clock.contract()["shared_availability_rows"] == 2


def test_next_rth_fill_skips_siblings_released_at_same_parent_close():
    availability = np.array([100, 100, 100, 200, 300], dtype=np.int64)
    assert next_strictly_later_index(availability, 0, len(availability)) == 3
    assert next_strictly_later_index(availability, 1, len(availability)) == 3
    assert next_strictly_later_index(availability, 2, len(availability)) == 3
    assert next_strictly_later_index(availability, 4, len(availability)) is None


def test_apply_clock_preserves_source_row_identity_and_price_pairing():
    base = 1_800_000_000
    source = base + np.arange(6, dtype=np.int64) * 300
    # Row 0 is released after native row 1. Sorting only timestamps would
    # either expose it early or lose identity; the clock retains both.
    parent = source.copy()
    parent[0] = source[2]
    data = _data(parent=parent, synthetic=[1, 0, 0, 0, 0, 0])
    apply_availability_clock(data)

    assert list(data.source_row_index[:3]) == [1, 0, 2]
    assert list(data.open[:3]) == [11.0, 10.0, 12.0]
    assert list(data.ts[:3]) == [source[1], source[2], source[2]]


def test_missing_or_early_parent_close_fails_closed():
    base = 1_800_000_000
    source = base + np.arange(6, dtype=np.int64) * 300
    missing = _data(parent=source)
    del missing.z["synthetic_5m_parent_close_ts"]
    with pytest.raises(AvailabilityClockError, match="require"):
        build_availability_clock(missing)

    early = _data(parent=source - 1)
    with pytest.raises(AvailabilityClockError, match="precedes"):
        build_availability_clock(early)


def test_clock_hash_binds_availability_and_source_identity():
    base = 1_800_000_000
    source = base + np.arange(6, dtype=np.int64) * 300
    first = build_availability_clock(_data(parent=source))
    changed_parent = source.copy()
    changed_parent[0] += 300
    second = build_availability_clock(_data(parent=changed_parent))
    assert first.rows_sha256 != second.rows_sha256
