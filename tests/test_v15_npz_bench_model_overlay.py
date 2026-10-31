import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import v15_npz_bench as bench


def create_bundle(root, marker):
    root = Path(root)
    artifacts = {}
    for name in sorted(bench.MODEL_ARTIFACT_NAMES):
        relative = Path("data/brain" if name.startswith("v18_brain_") else "data/learn") / name
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{marker}:{name}".encode()
        target.write_bytes(payload)
        artifacts[name] = hashlib.sha256(payload).hexdigest()
    declaration = {"bundle_id": marker, "expected_sha256_by_filename": artifacts}
    manifest = root / "tools" / "v18_model_bundle.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(declaration, sort_keys=True))
    return declaration


class V15ModelOverlayTests(unittest.TestCase):
    def test_build_root_overlays_only_pinned_models_and_keeps_common_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            (source / ".venv").mkdir(parents=True)
            code = base / "code"
            (code / "tools").mkdir(parents=True)
            (code / "v15_pilot.py").write_text("pilot")
            (code / "tools" / "unchanged.py").write_text("runtime")
            snap = base / "snap"
            (snap / "data" / "learn").mkdir(parents=True)
            (snap / "data" / "learn" / "unchanged.json").write_text("parent-data")
            (snap / "npz").mkdir()
            (snap / "npz" / "BTCUSDT.npz").write_bytes(b"frozen-npz")
            (snap / "templates").mkdir()
            (snap / "templates" / "TEMPLATE_CRYPTO_LONG.xlsx").write_bytes(b"frozen-template")
            reference = base / "reference_bundle"
            candidate = base / "candidate_bundle"
            create_bundle(reference, "reference")
            create_bundle(candidate, "candidate")
            reference_run = base / "reference_run"
            candidate_run = base / "candidate_run"
            reference_run.mkdir()
            candidate_run.mkdir()
            with patch.object(bench, "SRC", source), patch.object(bench, "freeze_live_baseline", return_value=False):
                reference_root = bench.build_root(snap, reference_run, code, reference)
                candidate_root = bench.build_root(snap, candidate_run, code, candidate)
            model_name = "v18_brain_CRYPTO_LONG.json"
            self.assertNotEqual((reference_root / "data/brain" / model_name).read_bytes(), (candidate_root / "data/brain" / model_name).read_bytes())
            for root in (reference_root, candidate_root):
                self.assertEqual((root / "data/learn/unchanged.json").read_text(), "parent-data")
                self.assertEqual((root / "backtest_v8/indicators/BTCUSDT.npz").read_bytes(), b"frozen-npz")
                self.assertEqual((root / "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx").read_bytes(), b"frozen-template")
                declaration = json.loads((root / "tools/v18_model_bundle.json").read_text())
                self.assertEqual(len(declaration["expected_sha256_by_filename"]), 32)
                receipt = json.loads((root.parent / "model_bundle_receipt.json").read_text())
                self.assertEqual(receipt["bundle_id"], "reference" if root == reference_root else "candidate")
                self.assertEqual(len(receipt["artifact_sha256"]), 32)
            self.assertEqual((snap / "data/learn/unchanged.json").read_text(), "parent-data")
            self.assertFalse((snap / "data/tools/v18_model_bundle.json").exists())

    def test_missing_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            bundle = base / "bundle"
            create_bundle(bundle, "missing")
            (bundle / "data/learn/linknet_proposals_CRYPTO_LONG.json").unlink()
            with self.assertRaisesRegex(ValueError, "missing or fails"):
                bench.model_bundle_artifacts(bundle)

    def test_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            create_bundle(bundle, "mismatch")
            (bundle / "data/learn/linknet_proposals_CRYPTO_LONG.json").write_text("changed")
            with self.assertRaisesRegex(ValueError, "missing or fails"):
                bench.model_bundle_artifacts(bundle)


if __name__ == "__main__":
    unittest.main()
