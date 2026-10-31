import json
import tempfile
import unittest
from pathlib import Path

from tools.v22_inference_profile import profile


class V22InferenceProfileTest(unittest.TestCase):
    def test_streams_delta_and_coverage_metrics_without_combining_scopes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            delta_path = root / "delta.jsonl"
            delta_path.write_text(
                "\n".join(
                    [
                        json.dumps({"ts": "2026-10-31T01:00:00Z", "label": "BOARD_CTX"}),
                        json.dumps({"ts": "2026-10-31T01:00:02Z", "label": "ROW_DONE", "row_secs": 2.0}),
                        json.dumps({"ts": "2026-10-31T01:00:01Z", "label": "naked", "cached": False, "secs": 0.75}),
                        json.dumps({"ts": "2026-10-31T01:00:03Z", "label": "yellow", "cached": True, "secs": 0.25}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            attempts_path = root / "attempts.jsonl"
            attempts_path.write_text(json.dumps({"engine_execution_attempted": True, "wall_seconds": 3.5}) + "\n", encoding="utf-8")
            log_path = root / "pilot.log"
            log_path.write_text("2026-10-31T01:00:04Z [V17-PHASE] P2 wall=8.0\n", encoding="utf-8")
            launch_path = root / "launch.json"
            launch_path.write_text(json.dumps({"at_utc": "2026-10-31T01:00:00Z"}), encoding="utf-8")
            terminal_path = root / "terminal.json"
            terminal_path.write_text(json.dumps({"at_utc": "2026-10-31T01:00:10Z"}), encoding="utf-8")
            result = profile(
                {
                    "run_id": "unit",
                    "sources": [
                        {"kind": "delta_log", "path": str(delta_path)},
                        {"kind": "coverage_attempts", "path": str(attempts_path)},
                        {"kind": "pilot_log", "path": str(log_path)},
                        {"kind": "launch_receipt", "path": str(launch_path)},
                        {"kind": "terminal_receipt", "path": str(terminal_path)},
                    ],
                }
            )
        measured = result["measured"]
        self.assertEqual(measured["row_secs_sum"]["sum_s"], 2.0)
        self.assertEqual(measured["delta_eval_secs_uncached_sum"]["sum_s"], 0.75)
        self.assertEqual(measured["delta_eval_secs_cached_sum"]["sum_s"], 0.25)
        self.assertEqual(measured["coverage_eval_wall_seconds_sum"]["sum_s"], 3.5)
        self.assertEqual(measured["coverage_engine_attempts"], 1)
        self.assertEqual(measured["pilot_phase_wall_s"]["P2"]["sum_s"], 8.0)
        self.assertEqual(measured["timestamp_span_s"], 10.0)
        self.assertEqual(measured["campaign_wall_s_from_launch_terminal"], 10.0)
        self.assertIsNone(measured.get("whole_board_wall_s"))

    def test_model_hash_must_be_explicitly_consumed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "run_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "models": [
                            {"model_sha256": "abc", "status": "loaded"},
                            {"model_sha256": "def", "status": "staged"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = profile({"sources": [{"kind": "run_manifest", "path": str(manifest_path)}]})
        identity = result["model_identity"]
        self.assertEqual(identity["consumed_identity_status"], "measured_from_manifest")
        self.assertEqual([row["sha256"] for row in identity["consumed_model_hashes"]], ["abc"])
        self.assertEqual({row["sha256"] for row in identity["declared_model_hashes"]}, {"abc", "def"})

    def test_missing_model_identity_is_unknown(self):
        result = profile({"sources": []})
        self.assertEqual(result["model_identity"]["consumed_identity_status"], "unknown")
        self.assertEqual(result["model_identity"]["consumed_model_hashes"], [])


if __name__ == "__main__":
    unittest.main()
