import tempfile
import unittest
from pathlib import Path

import numpy as np

from backtest_v8_harness import IndicatorStore, is_tradier_rth_ts


class IndicatorStoreIntegrityTests(unittest.TestCase):
    def _store(self, **arrays):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "TEST.npz"
        n = len(arrays["timestamps"])
        defaults = {
            "close": np.full(n, 100.0, dtype=np.float32),
            "wt_score_1h": np.ones(n, dtype=np.float32),
        }
        defaults.update(arrays)
        np.savez(path, **defaults)
        return IndicatorStore(str(path))

    def test_zero_event_flags_are_not_forward_filled(self):
        store = self._store(
            timestamps=np.arange(6, dtype=np.int64),
            wt_cross_bull_1h=np.array([0, 1, 0, 0, 1, 0], dtype=np.int8),
            wt_cross_bear_1h=np.array([0, 0, 1, 0, 0, 0], dtype=np.int8),
        )
        np.testing.assert_array_equal(
            store.arrays["wt_cross_bull_1h"], [0, 1, 0, 0, 1, 0]
        )
        np.testing.assert_array_equal(
            store.arrays["wt_cross_bear_1h"], [0, 0, 1, 0, 0, 0]
        )

    def test_zero_direction_state_is_not_changed_to_bullish(self):
        store = self._store(
            timestamps=np.arange(5, dtype=np.int64),
            wt_bullish_1h=np.array([0, 1, 0, 1, 0], dtype=np.int8),
        )
        np.testing.assert_array_equal(
            store.arrays["wt_bullish_1h"], [0, 1, 0, 1, 0]
        )

    def test_nan_is_still_forward_filled(self):
        store = self._store(
            timestamps=np.arange(5, dtype=np.int64),
            rsi_1h=np.array([np.nan, 42.0, np.nan, 51.0, np.nan], dtype=np.float32),
        )
        got = store.arrays["rsi_1h"]
        self.assertTrue(np.isnan(got[0]))
        np.testing.assert_allclose(got[1:], [42.0, 42.0, 51.0, 51.0])

    def test_neutral_string_state_is_not_forward_filled(self):
        store = self._store(
            timestamps=np.arange(5, dtype=np.int64),
            wt_divergence_1h=np.array([0, 1, 0, -1, 0], dtype=np.int8),
        )
        np.testing.assert_array_equal(
            store.arrays["wt_divergence_1h"],
            ["", "BULL", "", "BEAR", ""],
        )


class TradierSessionIntegrityTests(unittest.TestCase):
    @staticmethod
    def _ts(value):
        from datetime import datetime, timezone

        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()

    def test_summer_rth_uses_dst_utc_offset(self):
        self.assertTrue(is_tradier_rth_ts(self._ts("2026-07-15T13:30:00")))
        self.assertFalse(is_tradier_rth_ts(self._ts("2026-07-15T12:30:00")))

    def test_winter_rth_uses_standard_utc_offset(self):
        self.assertFalse(is_tradier_rth_ts(self._ts("2026-01-15T13:30:00")))
        self.assertTrue(is_tradier_rth_ts(self._ts("2026-01-15T14:30:00")))
        self.assertTrue(is_tradier_rth_ts(self._ts("2026-01-15T21:00:00")))


if __name__ == "__main__":
    unittest.main()
