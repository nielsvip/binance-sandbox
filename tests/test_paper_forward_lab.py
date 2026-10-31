import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.paper_forward_lab import audit_snapshot, build_plan

class PaperForwardLabTests(unittest.TestCase):
    def test_market_timestamp_not_file_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text(json.dumps({"BTCUSDC": {"timestamp_1m": 900, "timestamp_3m": 1201, "timestamp_5m": 1000}}))
            report = audit_snapshot(path, ["BTCUSDC"], now=1200)
            self.assertEqual([row["status"] for row in report["rows"]], ["STALE", "FUTURE_TIMESTAMP", "TIMESTAMP_RECENT", "MISSING_OR_INVALID_TIMESTAMP"])
            self.assertFalse(report["causal_inputs_verified"])

    def test_bounded_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text("x" * 100)
            with self.assertRaisesRegex(ValueError, "bounded"):
                audit_snapshot(path, [], now=1200, max_bytes=32)

    def test_plan_pairs_and_settings_epoch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "data"
            (data / "hourly_reconfig").mkdir(parents=True)
            (data / "v15_best_first.json").write_text(json.dumps({"allowed": ["BTCUSDC_LONG", "AAPL_SHORT"]}))
            database = data / "hourly_reconfig" / "per_sym_store.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE per_sym_active(sym_side,cat_side,updated_at,full_config_json)")
                connection.execute("INSERT INTO per_sym_active VALUES(?,?,?,?)", ("BTCUSDC_LONG", "CRYPTO_LONG", "2026-10-31T00:00:00Z", '{"FOO":true}'))
            catalog = {"file_sha256": {"ez_manage.py": "version1"}, "scenarios": [{"scenario_id": name, "venue": "crypto", "side": "LONG"} for name in ("baseline", "one-switch")]}
            with patch("tools.paper_forward_lab.inventory", return_value=catalog):
                first = build_plan(root)
                self.assertEqual(first["groups"][0]["scenario_ids"], ["baseline", "one-switch"])
                self.assertEqual(first["uncovered"], [{"sym_side": "AAPL_SHORT", "reason": "NO_ACTIVE_SETTINGS"}])
                self.assertEqual(first["running_scenarios"], 0)
                self.assertFalse(first["promotion_enabled"])
                with sqlite3.connect(database) as connection:
                    connection.execute("UPDATE per_sym_active SET full_config_json=?", ('{"FOO":false}',))
                second = build_plan(root)
                self.assertNotEqual(first["groups"][0]["epoch_id"], second["groups"][0]["epoch_id"])
                self.assertEqual(first["groups"][0]["host"], second["groups"][0]["host"])

if __name__ == "__main__":
    unittest.main()
