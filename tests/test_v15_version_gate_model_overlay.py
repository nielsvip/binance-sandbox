import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import v15_version_gate as gate


def write_bundle(root, bundle_id):
    expected = {}
    for name in sorted(gate.MODEL_ARTIFACT_NAMES):
        relative = Path("data/brain" if name.startswith("v18_brain_") else "data/learn") / name
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((bundle_id + ":" + name).encode())
        expected[name] = gate.sha256(path)
    manifest = root / gate.MODEL_BUNDLE_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema": "v15_nn_bundle/1", "bundle_id": bundle_id, "expected_sha256_by_filename": expected}))
    return gate.model_bundle_pin(root)


def comparison_spec(base, reference, candidate, runtime_file):
    runtime_code = base / "runtime_code"
    runtime_code.mkdir()
    (runtime_code / "runtime.py").write_bytes(runtime_file)
    runtime_manifest = runtime_code / "runtime_manifest.json"
    runtime_manifest.write_text(json.dumps({"schema": "v15_nn_runtime/1", "code_root": str(runtime_code), "files": {"runtime.py": hashlib.sha256(runtime_file).hexdigest()}}))
    reference_manifest = reference / gate.MODEL_BUNDLE_PATH
    candidate_manifest = candidate / gate.MODEL_BUNDLE_PATH
    spec_path = base / "spec.json"
    spec_path.write_text(json.dumps({
        "schema": "v15_nn_comparison/1",
        "comparison_id": "test",
        "runtime": {"path": str(runtime_manifest), "sha256": gate.sha256(runtime_manifest)},
        "reference": {"path": str(reference_manifest), "sha256": gate.sha256(reference_manifest)},
        "candidate": {"path": str(candidate_manifest), "sha256": gate.sha256(candidate_manifest)},
        "equal_runtime_flags": {"V15_REQUIRE_MODEL_BUNDLE": "1"},
        "reference_runtime_flags": {"V18_LINKNET_FIRST": "0"},
        "candidate_runtime_flags": {"V18_LINKNET_FIRST": "1"},
    }))
    return spec_path, runtime_code


def full_decision(sides, candidate_oos=120.0, candidate_wall=80.0, initial_pass=True):
    rows = [{"side": side, "oos_champ": 100.0, "oos_cand": candidate_oos, "wall_champ": 1000.0, "wall_cand": candidate_wall, "truth": True} for side in sides]
    return {"PASS": initial_pass, "harness_fail": False, "scored": sides, "truth_sides": 8, "rows": rows, "reasons": []}


class V15VersionGateModelOverlayTests(unittest.TestCase):
    def test_comparison_spec_pins_runtime_arm_flags_and_rejects_candidate_override_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            reference, candidate, champion = base / "reference", base / "candidate", base / "champion"
            reference_pin = write_bundle(reference, "reference")
            candidate_pin = write_bundle(candidate, "candidate")
            spec_path, runtime_code = comparison_spec(base, reference, candidate, b"current-runtime")
            champion.mkdir()
            (champion / "runtime.py").write_bytes(b"current-runtime")
            result = gate.model_comparison_spec_pin(spec_path, reference, candidate, reference_pin, candidate_pin, champion, {"V20_SELECTOR": "1"}, {"V18_LINKNET_FIRST": "1"})
            self.assertEqual(result["reference_flags"]["V18_LINKNET_FIRST"], "0")
            self.assertEqual(result["candidate_flags"]["V18_LINKNET_FIRST"], "1")
            self.assertEqual(result["reference_flags"]["V20_SELECTOR"], "1")
            self.assertEqual(result["candidate_flags"]["V20_SELECTOR"], "1")
            self.assertEqual(result["runtime"]["code_root"], str(runtime_code.resolve()))
            with self.assertRaisesRegex(ValueError, "conflicts with pinned candidate"):
                gate.model_comparison_spec_pin(spec_path, reference, candidate, reference_pin, candidate_pin, champion, {"V20_SELECTOR": "1"}, {"V18_LINKNET_FIRST": "0"})
            (champion / "runtime.py").write_bytes(b"different-runtime")
            with self.assertRaisesRegex(ValueError, "differs from pinned champion tree"):
                gate.model_comparison_spec_pin(spec_path, reference, candidate, reference_pin, candidate_pin, champion, {"V20_SELECTOR": "1"})

    def _finalize_with_real_model_roots(self, candidate_oos, candidate_wall, initial_pass):
        sides = [f"S{i}_LONG" for i in range(4)] + [f"T{i}_SHORT" for i in range(4)]
        snapshot = {"snapshot_id": "current8", "manifest_sha256": "a" * 64, "sides": sides}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            bench = base / "bench"
            gate_dir = base / "gate"
            champ_dir = gate_dir / "champion"
            candidate_dir = gate_dir / "candidates" / "test"
            candidate_tree = candidate_dir / "tree"
            candidate_tree.mkdir(parents=True)
            (champ_dir / "meta.json").parent.mkdir(parents=True)
            (champ_dir / "meta.json").write_text("{}")
            runtime_pin = {"files": {"runtime.py": hashlib.sha256(b"same-runtime").hexdigest()}}
            roots = {}
            pins = {}
            for label, bundle_id in (("gate_test_champ", "reference"), ("gate_test_cand", "candidate")):
                root = bench / "runs" / f"current8__{label}" / "root"
                root.mkdir(parents=True)
                (root / "runtime.py").write_bytes(b"same-runtime")
                pins[label] = write_bundle(root, bundle_id)
                roots[label] = root.parent
                receipt = {"manifest_sha256": pins[label]["manifest_sha256"], "artifact_sha256": pins[label]["artifact_sha256"]}
                (root.parent / "model_bundle_receipt.json").write_text(json.dumps(receipt))
            overlay = {
                "snapshot": snapshot,
                "baseline": pins["gate_test_champ"],
                "candidate": pins["gate_test_cand"],
                "comparison_spec": {"runtime": runtime_pin},
            }
            meta = {"id": "test", "state": "running", "model_overlay": overlay, "files": {}}
            with patch.object(gate, "BENCH", bench), \
                 patch.object(gate, "SNAP", "current8"), \
                 patch.object(gate, "GATE", gate_dir), \
                 patch.object(gate, "CHAMP", champ_dir), \
                 patch.object(gate, "DECISIONS", gate_dir / "decisions.jsonl"), \
                 patch.object(gate, "STATUS", gate_dir / "status.md"), \
                 patch.object(gate, "model_snapshot_pin", return_value=snapshot), \
                 patch.object(gate, "write_status"), \
                 patch.object(gate, "deploy", return_value={"complete": False}) as deploy, \
                 patch.object(gate, "deploy_complete", return_value=False):
                gate.finalize(candidate_dir, meta, full_decision(sides, candidate_oos, candidate_wall, initial_pass))
            return meta, deploy

    def test_finalize_real_run_roots_reject_quality_just_below_20_percent(self):
        meta, deploy = self._finalize_with_real_model_roots(119.99, 80.0, True)
        self.assertEqual(meta["state"], "FAIL")
        self.assertFalse(meta["decision"]["PASS"])
        self.assertFalse(meta["decision"]["model_overlay_acceptance"]["quality_pass"])
        deploy.assert_not_called()

    def test_finalize_real_run_roots_reject_exactly_90_percent_wall_reduction(self):
        meta, deploy = self._finalize_with_real_model_roots(120.0, 100.0, True)
        self.assertEqual(meta["state"], "FAIL")
        self.assertFalse(meta["decision"]["model_overlay_acceptance"]["speed_pass"])
        deploy.assert_not_called()

    def test_finalize_real_run_roots_keep_preexisting_canonical_fail(self):
        meta, deploy = self._finalize_with_real_model_roots(120.0, 80.0, False)
        self.assertEqual(meta["state"], "FAIL")
        self.assertFalse(meta["decision"]["PASS"])
        deploy.assert_not_called()

    def test_finalize_real_run_roots_allow_threshold_pass_to_canonical_gate_decision(self):
        meta, deploy = self._finalize_with_real_model_roots(120.0, 80.0, True)
        self.assertEqual(meta["state"], "PASS_DEPLOY_INCOMPLETE")
        self.assertTrue(meta["decision"]["PASS"])
        self.assertTrue(meta["decision"]["model_overlay_acceptance"]["run_root_model_hashes_verified"])
        deploy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
