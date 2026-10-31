import datetime as dt
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from tools.paper_native_feed import (
    SOURCE_BY_HOST,
    capture_once,
    summarize_timestamps,
    validate_snapshot_bytes,
)


class PaperNativeFeedTests(unittest.TestCase):
    def test_source_mapping_is_fixed_by_host_pool(self):
        self.assertEqual(SOURCE_BY_HOST["s1"][0:2], ("crypto", "10.0.0.4"))
        self.assertEqual(SOURCE_BY_HOST["s2"][0:2], ("crypto", "10.0.0.4"))
        self.assertEqual(SOURCE_BY_HOST["s5"][0:2], ("stocks", "10.0.0.3"))
        self.assertEqual(SOURCE_BY_HOST["s6"][0:2], ("stocks", "10.0.0.3"))

    def test_snapshot_requires_symbol_object_records(self):
        parsed = validate_snapshot_bytes(b'{"BTCUSDC":{"timestamp_3m":"2026-10-31T00:00:00Z"},"_meta":{}}')
        self.assertIn("BTCUSDC", parsed)
        for raw in (b"{", b"[]", b"{}", b'{"BTCUSDC":[]}', b'{"bad symbol":{}}'):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    validate_snapshot_bytes(raw)

    def test_timestamp_summary_reports_source_times_and_content_age(self):
        captured = dt.datetime(2026, 10, 31, 0, 10, tzinfo=dt.timezone.utc).timestamp()
        snapshot = {
            "AAA": {"timestamp_3m": "2026-10-31T00:00:00Z"},
            "BBB": {"timestamp_3m": "2026-10-31T00:05:00+00:00"},
            "_metadata": {"timestamp_3m": "2026-10-31T00:09:00Z"},
        }
        summary = summarize_timestamps(snapshot, captured)["3m"]
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["min_source_timestamp"], "2026-10-31T00:00:00Z")
        self.assertEqual(summary["max_source_timestamp"], "2026-10-31T00:05:00Z")
        self.assertEqual(summary["median_content_age_s"], 600.0)

    def test_future_and_malformed_timestamps_are_invalid_fields(self):
        captured = dt.datetime(2026, 10, 31, 0, 10, tzinfo=dt.timezone.utc).timestamp()
        snapshot = {
            "AAA": {"timestamp_1m": "2026-10-31T00:11:00Z"},
            "BBB": {"timestamp_1m": "not-a-timestamp"},
            "CCC": {"timestamp_1m": "2026-10-31T00:09:00"},
        }
        summary = summarize_timestamps(snapshot, captured)["1m"]
        self.assertEqual(summary["count"], 0)
        self.assertEqual(summary["invalid_count"], 3)
        self.assertIsNone(summary["max_source_timestamp"])
        self.assertIsNone(summary["median_content_age_s"])

    def test_capture_uses_local_s2_copy_and_publishes_matching_receipt(self):
        raw = b'{"BTCUSDC":{"timestamp_3m":"2026-10-31T00:05:00Z"}}'
        captured = dt.datetime(2026, 10, 31, 0, 10, tzinfo=dt.timezone.utc).timestamp()
        seen = {}

        def fake_rsync(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs
            Path(command[-1]).write_bytes(raw)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "binance-sandbox"
            base.mkdir()
            receipt = capture_once("s2", base, runner=fake_rsync, clock=lambda: captured)
            feed = base / "data" / "paper_forward_lab" / "feed"
            snapshot = (feed / "crypto.json").read_bytes()
            saved_receipt = json.loads((feed / "crypto.receipt.json").read_text())
        self.assertEqual(seen["command"][0], "rsync")
        self.assertNotIn("-e", seen["command"])
        self.assertIn(SOURCE_BY_HOST["s2"][2], seen["command"])
        self.assertEqual(seen["kwargs"]["timeout"], 20)
        self.assertEqual(hashlib.sha256(snapshot).hexdigest(), saved_receipt["source_sha256"])
        self.assertEqual(saved_receipt, receipt)
        self.assertFalse(saved_receipt["verified_native_1m"])
        self.assertEqual(saved_receipt["timeframe_provenance"]["1m"]["status"], "KNOWN_ALIAS")


if __name__ == "__main__":
    unittest.main()
