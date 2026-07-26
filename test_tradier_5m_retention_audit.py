import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from tools.tradier_5m_retention_audit import _npz_stats, _retention_errors, _source_stats


def bar(timestamp):
    return {
        "timestamp": timestamp,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1.0,
    }


class Tradier5mRetentionAuditTests(unittest.TestCase):
    def test_append_only_growth_passes(self):
        current = {
            "rows": 3,
            "start": "2026-01-01",
            "end": "2026-01-03",
            "monotonic": True,
            "duplicates": 0,
        }
        prior = {"rows": 2, "start": "2026-01-01", "end": "2026-01-02"}
        self.assertEqual(_retention_errors("MU", current, prior), [])

    def test_shrink_or_history_loss_fails(self):
        current = {
            "rows": 1,
            "start": "2026-01-02",
            "end": "2026-01-02",
            "monotonic": True,
            "duplicates": 0,
        }
        prior = {"rows": 2, "start": "2026-01-01", "end": "2026-01-03"}
        errors = _retention_errors("MU", current, prior)
        self.assertTrue(any("shrank" in error for error in errors))
        self.assertTrue(any("start advanced" in error for error in errors))
        self.assertTrue(any("end regressed" in error for error in errors))

    def test_bootstrap_missing_native_remains_allowed_until_first_bar(self):
        missing = {"rows": 0}
        self.assertEqual(
            _retention_errors("NEW", missing, None, allow_missing_native=True),
            [],
        )
        self.assertEqual(
            _retention_errors(
                "NEW",
                missing,
                {"rows": 0, "start": None, "end": None},
                allow_missing_native=True,
            ),
            [],
        )

    def test_source_rejects_duplicate_or_unsorted_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MU_5m.json"
            path.write_text(json.dumps([bar("2026-01-02"), bar("2026-01-01"), bar("2026-01-01")]))
            stats = _source_stats(path)
            self.assertFalse(stats["monotonic"])
            self.assertEqual(stats["duplicates"], 1)

    def test_npz_parent_provenance_accepts_bounded_containing_bar_lag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epoch = lambda value: int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            ts = np.asarray([
                epoch("2026-01-01T10:20:00.000000Z"),
                epoch("2026-01-01T10:25:00.000000Z"),
                epoch("2026-01-01T10:30:00.000000Z"),
            ])
            npz = root / "MU.npz"
            np.savez(
                npz,
                timestamps=ts,
                close=np.asarray([100.0, 150.0, 150.0]),
                synthetic_5m=np.ones(3, dtype=np.int8),
                synthetic_5m_parent_close_ts=np.full(3, epoch("2026-01-01T10:30:00.000000Z")),
            )
            stats = _npz_stats(npz)
            self.assertEqual(stats["parent_lag_min_s"], 0)
            self.assertEqual(stats["parent_lag_max_s"], 600)
            self.assertTrue(stats["parent_lag_valid"])


if __name__ == "__main__":
    unittest.main()
