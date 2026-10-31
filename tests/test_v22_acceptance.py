import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from tools.v22_acceptance import comparison_identity_sha256, evaluate


def make_payload(directory):
    identity = {
        "snapshot_id": "snap_20261030T210717Z_current8_r2",
        "snapshot_manifest_sha256": "a" * 64,
        "runtime_sha256": "b" * 64,
        "engine_sha256": "c" * 64,
        "evaluation_policy_sha256": "d" * 64,
    }
    sides = ["A_LONG", "B_LONG", "C_LONG", "D_LONG", "E_SHORT", "F_SHORT", "G_SHORT", "H_SHORT"]
    snapshot_dir = Path(directory) / "snap_20261030T210717Z_current8_r2"
    snapshot_dir.mkdir()
    snapshot_path = snapshot_dir / "manifest.json"
    raw = json.dumps({"sides": sides}, sort_keys=True).encode()
    snapshot_path.write_bytes(raw)
    manifest_sha = hashlib.sha256(raw).hexdigest()
    identity["snapshot_manifest_sha256"] = manifest_sha
    models = {"baseline": {}, "candidate": {}}
    results = {}
    for index, side in enumerate(sides):
        baseline_model = hashlib.sha256(f"base-{side}".encode()).hexdigest()
        candidate_model = hashlib.sha256(f"candidate-{side}".encode()).hexdigest()
        models["baseline"][side] = baseline_model
        models["candidate"][side] = candidate_model
        results[side] = {
            "baseline": {
                "identity": dict(identity),
                "model_sha256": baseline_model,
                "training_window_end_utc": "2026-10-20T00:00:00Z",
                "oos_start_utc": "2026-10-23T00:00:00Z",
                "oos_gain_pct": -10.0,
                "wall_seconds": 1000.0,
            },
            "candidate": {
                "identity": dict(identity),
                "model_sha256": candidate_model,
                "training_window_end_utc": "2026-10-20T00:00:00Z",
                "oos_start_utc": "2026-10-23T00:00:00Z",
                "oos_gain_pct": -8.0,
                "wall_seconds": 90.0,
            },
        }
    score_kind = "oos_gain_pct"
    return {
        "schema": "v22_nn_acceptance_input/1",
        "comparison_id": "review-v22-001",
        "snapshot_manifest": {"path": str(snapshot_path), "sha256": manifest_sha},
        "common_identity": identity,
        "score_kind": score_kind,
        "s2_gate_decision": {
            "status": "PASS",
            "baseline_version": "v21",
            "candidate_version": "v22",
            "decision_id": "s2-20261031-001",
            "evidence_sha256": "e" * 64,
            "input_identity_sha256": comparison_identity_sha256(identity, set(sides), score_kind),
            "baseline_model_sha256_by_side": models["baseline"],
            "candidate_model_sha256_by_side": models["candidate"],
        },
        "sides": results,
    }


class V22AcceptanceTests(unittest.TestCase):
    def test_aggregate_negative_baseline_is_loss_reduction_not_profit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate(make_payload(directory))
        self.assertTrue(result["accepted"])
        self.assertEqual(result["aggregate_oos_quality_change_pct"], 20.0)
        self.assertEqual(result["quality_change_label"], "aggregate_loss_reduction_pct")
        self.assertFalse(result["aggregate_candidate_is_profitable"])

    def test_aggregate_thresholds_and_per_side_regression_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            for side in payload["sides"]:
                payload["sides"][side]["candidate"]["oos_gain_pct"] = -7.0
            payload["sides"]["A_LONG"]["candidate"]["oos_gain_pct"] = -12.0
            result = evaluate(payload)
        self.assertTrue(result["quality_pass"])
        self.assertTrue(result["sides"]["A_LONG"]["side_regression"])

    def test_time_threshold_is_strictly_greater_than_ninety_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            for side in payload["sides"]:
                payload["sides"][side]["candidate"]["wall_seconds"] = 100.0
            result = evaluate(payload)
        self.assertFalse(result["speed_pass"])
        self.assertFalse(result["accepted"])

    def test_rejects_incomplete_snapshot_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["sides"].pop("H_SHORT")
            with self.assertRaisesRegex(ValueError, "coverage differs"):
                evaluate(payload)

    def test_rejects_s2_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["s2_gate_decision"]["input_identity_sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "input identity"):
                evaluate(payload)

    def test_failed_gate_is_reported_as_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["s2_gate_decision"]["status"] = "FAIL"
            result = evaluate(payload)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["s2_gate_pass"])

    def test_zero_aggregate_baseline_has_undefined_relative_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            for side in payload["sides"]:
                payload["sides"][side]["baseline"]["oos_gain_pct"] = 0.0
            with self.assertRaisesRegex(ValueError, "aggregate baseline is zero"):
                evaluate(payload)

    def test_requires_oos_score_not_fit_score(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["score_kind"] = "fit_gain_pct"
            with self.assertRaisesRegex(ValueError, "actual OOS"):
                evaluate(payload)

    def test_training_window_must_end_before_oos(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["sides"]["B_LONG"]["candidate"]["training_window_end_utc"] = "2026-10-23T00:00:00Z"
            with self.assertRaisesRegex(ValueError, "overlaps or reaches"):
                evaluate(payload)

    def test_s2_model_identity_must_match_consumed_model(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["s2_gate_decision"]["candidate_model_sha256_by_side"]["A_LONG"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "consumed candidate model identities"):
                evaluate(payload)

    def test_non_finite_measurement_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = make_payload(directory)
            payload["sides"]["C_LONG"]["candidate"]["oos_gain_pct"] = math.nan
            with self.assertRaisesRegex(ValueError, "must be finite"):
                evaluate(payload)


if __name__ == "__main__":
    unittest.main()
