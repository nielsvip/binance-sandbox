import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import v22_baseline_prepare as prepare


class V22BaselinePrepareTests(unittest.TestCase):
    def test_effective_defaults_uses_actual_json_fallback_and_records_loader_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            defaults = {"CRYPTO_LONG": {"A": True}, "CRYPTO_SHORT": {}, "STOCKS_LONG": {}, "STOCKS_SHORT": {}}
            settings = root / "data/per_sym_settings.json"
            settings.write_text(json.dumps(defaults))
            loader = root / "per_sym_settings.py"
            store_module = root / "per_sym_store.py"
            loader.write_text("loader")
            store_module.write_text("store")
            selected, proof = prepare.effective_defaults(
                root,
                root / "side",
                {"PER_SYM_STORE_DB": str(root / "missing.db")},
                "CRYPTO_LONG",
                {"per_sym_settings.py": prepare.sha(loader), "per_sym_store.py": prepare.sha(store_module)},
            )
            self.assertEqual(selected, defaults["CRYPTO_LONG"])
            self.assertEqual(proof["effective_source_kind"], "data/per_sym_settings.json fallback")
            self.assertEqual(proof["settings_sha256"], prepare.sha(settings))
            self.assertEqual(proof["effective_defaults_sha256"], hashlib.sha256(b'{"A":true}').hexdigest())

    def test_effective_defaults_uses_sql_primary_when_valid_row_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            settings = root / "data/per_sym_settings.json"
            settings.write_text(json.dumps({"CRYPTO_LONG": {"A": False}}))
            database = root / "store.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE kv_json (key TEXT PRIMARY KEY, value_json TEXT)")
            connection.execute(
                "INSERT INTO kv_json VALUES (?, ?)",
                ("per_sym_settings", json.dumps({"CRYPTO_LONG": {"A": True}})),
            )
            connection.commit()
            connection.close()
            loader = root / "per_sym_settings.py"
            store_module = root / "per_sym_store.py"
            loader.write_text("loader")
            store_module.write_text("store")
            selected, proof = prepare.effective_defaults(
                root,
                root / "side",
                {"PER_SYM_STORE_DB": str(database)},
                "CRYPTO_LONG",
                {"per_sym_settings.py": prepare.sha(loader), "per_sym_store.py": prepare.sha(store_module)},
            )
            self.assertEqual(selected, {"A": True})
            self.assertEqual(proof["effective_source_kind"], "sqlite.kv_json[per_sym_settings]")
            self.assertNotEqual(proof["settings_sha256"], prepare.sha(settings))

    def test_side_inputs_require_exact_start_live_set_and_npz_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "starts").mkdir()
            (root / "npz").mkdir()
            (root / "data/live_baseline_snapshots").mkdir(parents=True)
            side = "1000PEPEUSDC_LONG"
            start = root / "starts" / f"{side}_autopsy_base.json"
            live = root / "data/live_baseline_snapshots" / f"{side}.json"
            npz = root / "npz/1000PEPEUSDC.npz"
            payload = b'{"X":1}'
            start.write_bytes(payload)
            live.write_bytes(payload)
            npz.write_bytes(b"npz")
            live_hash = prepare.sha(live)
            manifest = {
                "starts": {side: hashlib.md5(payload).hexdigest()},
                "npz": {"1000PEPEUSDC": {"sha256": prepare.sha(npz)}},
                "frozen_data_sha256": {f"data/live_baseline_snapshots/{side}.json": live_hash},
            }
            admission = {
                "sides": [side],
                "admission": {side: {"venue": "crypto"}},
                "baseline_sha256": {side: live_hash},
            }
            live_manifest = {"sets": {side: live_hash}}
            result = prepare.verify_side_inputs(root, manifest, admission, live_manifest, side)
            self.assertEqual(result["cat_side"], "CRYPTO_LONG")
            self.assertEqual(result["npz_sha256"], prepare.sha(npz))
            live.write_bytes(b'{"X":2}')
            with self.assertRaisesRegex(ValueError, "source anchor"):
                prepare.verify_side_inputs(root, manifest, admission, live_manifest, side)


if __name__ == "__main__":
    unittest.main()
