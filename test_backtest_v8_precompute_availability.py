import unittest

import numpy as np

from backtest_v8_precompute import (
    _availability_timestamps,
    _broadcast_asof_indices,
    _broadcast_values,
)


class ClosedBarAvailabilityTests(unittest.TestCase):
    def test_tradier_htf_uses_previous_completed_row(self):
        source = np.array([0, 3600, 7200, 10800], dtype=np.int64)
        target = np.array([3600, 4500, 7199, 7200, 9000], dtype=np.int64)
        idx = _broadcast_asof_indices(source, target, "1h", "tradier")
        np.testing.assert_array_equal(idx, [0, 0, 0, 1, 1])

        values = np.array([10, 20, 30, 40])
        np.testing.assert_array_equal(values[idx], [10, 10, 10, 20, 20])

    def test_raw_15m_keeps_close_label_asof_semantics(self):
        source = np.array([0, 900, 1800], dtype=np.int64)
        target = np.array([899, 900, 1200, 1800], dtype=np.int64)
        idx = _broadcast_asof_indices(source, target, "15m", "tradier")
        np.testing.assert_array_equal(idx, [0, 1, 1, 2])

    def test_htf_availability_timestamp_is_next_label(self):
        source = np.array([0, 3600, 7200, 10800], dtype=np.int64)
        target = np.array([3600, 7199, 7200, 10799], dtype=np.int64)
        idx = _broadcast_asof_indices(source, target, "1h", "tradier")
        available = _availability_timestamps(source, idx, "1h", "tradier")
        np.testing.assert_array_equal(available, [3600, 3600, 7200, 7200])
        self.assertTrue(np.all(available <= target))

    def test_mapping_is_prefix_invariant(self):
        full_source = np.array([0, 3600, 7200, 10800, 14400], dtype=np.int64)
        prefix_source = full_source[:4]
        target = np.arange(3600, 10801, 300, dtype=np.int64)
        full_idx = _broadcast_asof_indices(full_source, target, "1h", "tradier")
        prefix_idx = _broadcast_asof_indices(prefix_source, target, "1h", "tradier")
        np.testing.assert_array_equal(full_idx, prefix_idx)

    def test_warmup_is_empty_instead_of_future_row_zero(self):
        source = np.array([0, 3600, 7200], dtype=np.int64)
        target = np.array([0, 1800, 3599], dtype=np.int64)
        idx = _broadcast_asof_indices(source, target, "1h", "tradier")
        np.testing.assert_array_equal(idx, [-1, -1, -1])
        np.testing.assert_array_equal(_broadcast_values(np.array([10, 20, 30]), idx), [0, 0, 0])
        available = _availability_timestamps(source, idx, "1h", "tradier")
        np.testing.assert_array_equal(available, [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
