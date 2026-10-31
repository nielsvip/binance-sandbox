import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.v22_bench_prepare import overlay_bundle, prepare, read_bundle, verify_pilot_root


CATEGORIES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
PREFIXES = ("v18_brain", "digest", "needed_filters", "switch_profile", "positives_all", "binding_map", "linknet_proposals", "must_pos")
SIDES = ("AAAUSDC_LONG", "BBBUSDC_SHORT", "CCCUSDC_LONG", "DDDUSDC_SHORT", "IBM_LONG", "TMO_LONG", "COIN_SHORT", "CDE_SHORT")


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class V22BenchPrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parent = self.root / "snap_parent"
        self.parent.mkdir()
        (self.parent / "npz").mkdir()
        (self.parent / "starts").mkdir()
        (self.parent / "templates").mkdir()
        (self.parent / "data/live_baseline_snapshots").mkdir(parents=True)
        npz, starts, templates = {}, {}, {}
        for side in SIDES:
            symbol = side.rsplit("_", 1)[0]
            payload = f"npz:{symbol}".encode()
            path = self.parent / "npz" / f"{symbol}.npz"
            path.write_bytes(payload)
            npz[symbol] = {"md5": hashlib.md5(payload).hexdigest(), "bytes": len(payload), "days_15m": 40}
            start_payload = json.dumps({"side": side}).encode()
            start = self.parent / "starts" / f"{side}_autopsy_base.json"
            start.write_bytes(start_payload)
            starts[side] = hashlib.md5(start_payload).hexdigest()
        for name in ("TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx", "TEMPLATE_STOCKS_LONG.xlsx", "TEMPLATE_STOCKS_SHORT.xlsx"):
            payload = name.encode()
            (self.parent / "templates" / name).write_bytes(payload)
            templates[name] = hashlib.md5(payload).hexdigest()
        (self.parent / "data/live_baseline_snapshots/manifest.json").write_text('{"anchor":"frozen"}')
        self.parent_manifest = {"sides": list(SIDES), "npz": npz, "starts": starts, "templates": templates, "data_files": 1, "env": {"V15_HOLDOUT_DAYS": "7", "V15_CUSTOM": "kept"}}
        (self.parent / "manifest.json").write_text(json.dumps(self.parent_manifest))
        self.bundles = {}
        self.codes = {}
        self.spec = {"schema": "v15_nn_comparison/1", "comparison_id": "unit_v22", "equal_runtime_flags": {"V15_REQUIRE_MODEL_BUNDLE": "1"}, "reference_runtime_flags": {"V18_LINKNET_FIRST": "0", "V18_LINKNET_BUNDLE_REQUIRED": "0", "V15_REQUIRE_MODEL_BUNDLE": "1", "V15_NN_REFERENCE_LEGACY_POLICY": "1"}, "candidate_runtime_flags": {"V18_LINKNET_FIRST": "1", "V18_LINKNET_BUNDLE_REQUIRED": "1", "V15_REQUIRE_MODEL_BUNDLE": "1"}}
        verifier_root = self.root / "review/tools"
        verifier_root.mkdir(parents=True)
        (self.root / "review/reference_policy_receipt.json").write_text("{}")
        (verifier_root / "v15_nextgen_gate_artifacts.py").write_bytes((Path(__file__).parents[1] / "tools/v15_nextgen_gate_artifacts.py").read_bytes())
        self.spec["reference_policy"] = {"path": str(self.root / "review/reference_policy_receipt.json")}
        for arm in ("reference", "candidate"):
            bundle_root = self.root / f"{arm}_bundle"
            (bundle_root / "tools").mkdir(parents=True)
            (bundle_root / "data/brain").mkdir(parents=True)
            (bundle_root / "data/learn").mkdir(parents=True)
            expected = {}
            for prefix in PREFIXES:
                for category in CATEGORIES:
                    suffix = ".json.gz" if prefix == "positives_all" else ".json"
                    name = f"{prefix}_{category}{suffix}"
                    rel = ("data/brain/" if prefix == "v18_brain" else "data/learn/") + name
                    payload = json.dumps({"arm": arm, "name": name}).encode()
                    (bundle_root / rel).write_bytes(payload)
                    expected[name] = digest(payload)
            declaration = {"schema": "v15_nn_bundle/1", "bundle_id": arm, "linknet_activation": {"schema": "compiled_linknet_first/1", "by_category": {cat: {"production_scorer": "legacy_compiled_output"} for cat in CATEGORIES}}, "required_runtime_flags": {"V18_LINKNET_FIRST": "1", "V18_LINKNET_BUNDLE_REQUIRED": "1"}, "expected_sha256_by_filename": expected}
            if arm == "reference":
                declaration["required_runtime_flags"] = {}
            manifest_path = bundle_root / "tools/v18_model_bundle.json"
            manifest_path.write_text(json.dumps(declaration))
            code_root = self.root / f"{arm}_code"
            (code_root / "tools").mkdir(parents=True)
            (code_root / "tools/v15_nextgen_gate_artifacts.py").write_bytes((Path(__file__).parents[1] / "tools/v15_nextgen_gate_artifacts.py").read_bytes())
            (code_root / "tools/v15_v18_planner.py").write_text("from pathlib import Path\ndef learn_file(name, root, sub):\n    return Path(root) / ('data/brain' if sub == 'brain' else 'data/learn') / name\n")
            (code_root / "tools/v18_brain.py").write_text("# compatibility stub\n")
            (code_root / "tools/v18_model_bundle.json").write_bytes(manifest_path.read_bytes())
            (code_root / "v15_pilot.py").write_text("# frozen pilot stub\n")
            # build_root excludes data from code and then copies the snapshot data over it.
            (code_root / "data").mkdir()
            (code_root / "data/ignored.txt").write_text("must not survive build_root")
            self.bundles[arm] = bundle_root
            self.codes[arm] = code_root
            self.spec[arm] = {"path": str(manifest_path), "sha256": digest(manifest_path.read_bytes())}
        self.spec["compatibility"] = {"tools/v15_v18_planner.py": digest((self.codes["candidate"] / "tools/v15_v18_planner.py").read_bytes()), "tools/v18_brain.py": digest((self.codes["candidate"] / "tools/v18_brain.py").read_bytes())}
        runtime_manifest_path = self.root / "runtime_manifest.json"
        runtime_manifest = {"schema": "v15_nn_runtime/1", "files": {"v15_pilot.py": digest((self.codes["candidate"] / "v15_pilot.py").read_bytes())}}
        runtime_manifest_path.write_text(json.dumps(runtime_manifest))
        self.spec["runtime"] = {"path": str(runtime_manifest_path), "sha256": digest(runtime_manifest_path.read_bytes())}
        self.spec_path = self.root / "comparison_spec.json"
        self.spec_path.write_text(json.dumps(self.spec))
        self.bench = self.root / "bench"
        self.bench.mkdir()
        self.output = self.root / "reviews/derived"

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_creates_pinned_private_snapshots_and_resolves_after_data_overlay(self):
        parent_manifest_bytes = (self.parent / "manifest.json").read_bytes()
        parent_manifest_inode = (self.parent / "manifest.json").stat().st_ino
        report_path, report = prepare(self.parent, self.spec_path, self.codes["reference"], self.codes["candidate"], self.output, self.bench)
        self.assertTrue(report_path.is_file())
        self.assertFalse(report["launch_started"])
        self.assertEqual(report["sides"], list(SIDES))
        ref = Path(report["arms"]["reference"]["snapshot_path"])
        cand = Path(report["arms"]["candidate"]["snapshot_path"])
        self.assertNotEqual(ref, cand)
        self.assertEqual((self.parent / "manifest.json").read_bytes(), parent_manifest_bytes)
        self.assertEqual((self.parent / "manifest.json").stat().st_ino, parent_manifest_inode)
        ref_manifest_bytes = (ref / "manifest.json").read_bytes()
        ref_manifest_inode = (ref / "manifest.json").stat().st_ino
        cand_manifest_inode = (cand / "manifest.json").stat().st_ino
        self.assertNotEqual(ref_manifest_inode, parent_manifest_inode)
        self.assertNotEqual(cand_manifest_inode, parent_manifest_inode)
        self.assertNotEqual(ref_manifest_inode, cand_manifest_inode)
        self.assertEqual((ref / "starts" / f"{SIDES[0]}_autopsy_base.json").read_bytes(), (self.parent / "starts" / f"{SIDES[0]}_autopsy_base.json").read_bytes())
        self.assertEqual((self.parent / "data/live_baseline_snapshots/manifest.json").read_text(), '{"anchor":"frozen"}')
        for arm, snapshot in (("reference", ref), ("candidate", cand)):
            resolved = report["arms"][arm]["resolved_after_build_root"]
            self.assertEqual(len(resolved["resolved_artifact_sha256"]), 32)
            self.assertIn("same code-exclude then snapshot-data-copy sequence", resolved["verification"])
            self.assertEqual((snapshot / "data/live_baseline_snapshots/manifest.json").read_text(), '{"anchor":"frozen"}')
            expected = json.loads((self.bundles[arm] / "tools/v18_model_bundle.json").read_text())["expected_sha256_by_filename"]
            for name, expected_sha in expected.items():
                rel = ("brain/" if name.startswith("v18_brain_") else "learn/") + name
                self.assertEqual(digest((snapshot / "data" / rel).read_bytes()), expected_sha)
        overlay_receipt = overlay_bundle(read_bundle(self.bundles["candidate"]), cand / "data")
        self.assertEqual(len(overlay_receipt), 32)
        self.assertEqual((ref / "manifest.json").read_bytes(), ref_manifest_bytes)
        self.assertEqual((ref / "manifest.json").stat().st_ino, ref_manifest_inode)
        self.assertEqual(report["arms"]["reference"]["shared_inputs_match_parent"], True)
        self.assertEqual(report["arms"]["candidate"]["shared_inputs_match_parent"], True)
        self.assertIn("--min-disk-mb 20000 --min-free-mb 7000", report["arms"]["candidate"]["command"])
        self.assertEqual(len(report["arms"]["reference"]["resolved_after_build_root"]["artifact_verifier_sha256"]), 64)
        actual_root = self.root / "actual_candidate_pilot_root"
        shutil.copytree(self.codes["candidate"], actual_root)
        shutil.rmtree(actual_root / "data")
        shutil.copytree(cand / "data", actual_root / "data")
        actual_receipt = verify_pilot_root(actual_root, self.spec_path, "candidate")
        self.assertEqual(actual_receipt["arm"], "candidate")
        self.assertEqual(actual_receipt["common_runtime_files_verified"], 3)
        self.assertEqual(len(actual_receipt["resolved_artifact_sha256"]), 32)
        self.assertEqual((self.parent / "npz" / f"{SIDES[0].rsplit('_', 1)[0]}.npz").stat().st_ino, (ref / "npz" / f"{SIDES[0].rsplit('_', 1)[0]}.npz").stat().st_ino)

    def test_refuses_modified_declared_artifact_before_staging(self):
        target = self.bundles["candidate"] / "data/brain/v18_brain_CRYPTO_LONG.json"
        target.write_text('{"tampered":true}')
        with self.assertRaisesRegex(ValueError, "does not match declaration"):
            prepare(self.parent, self.spec_path, self.codes["reference"], self.codes["candidate"], self.output, self.bench)

    def test_refuses_incomplete_parent_membership(self):
        manifest = dict(self.parent_manifest)
        manifest["sides"] = manifest["sides"][:-1]
        (self.parent / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "exactly eight"):
            prepare(self.parent, self.spec_path, self.codes["reference"], self.codes["candidate"], self.output, self.bench)


if __name__ == "__main__":
    unittest.main()
