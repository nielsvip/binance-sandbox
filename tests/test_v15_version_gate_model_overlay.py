import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import v15_version_gate as gate


TRAINED_AT = "2026-10-16T16:45:00Z"


def test_snapshot():
    sides = [f"S{i}_LONG" for i in range(4)] + [f"T{i}_SHORT" for i in range(4)]
    symbols = [side.rsplit("_", 1)[0] for side in sides]
    return {"snapshot_id": "current8", "manifest_sha256": "a" * 64, "sides": sides, "holdout_days": 7, "npz_sha256_by_symbol": {symbol: "b" * 64 for symbol in symbols}, "oos_start_by_side": {side: "2026-10-23T21:00:00Z" for side in sides}}


def write_bundle(root, bundle_id, trained_at=TRAINED_AT):
    expected = {}
    for name in sorted(gate.MODEL_ARTIFACT_NAMES):
        relative = Path("data/brain" if name.startswith("v18_brain_") else "data/learn") / name
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("v18_brain_"):
            value = {"_meta": {"last_ts": trained_at}}
        elif name.startswith("digest_"):
            value = {"meta": {"era_cut": trained_at}}
        elif name.startswith("linknet_proposals_"):
            value = {"meta": {"corpus_last_ts": trained_at}}
        else:
            value = {"payload": bundle_id + ":" + name}
        path.write_text(json.dumps(value))
        expected[name] = gate.sha256(path)
    manifest = root / gate.MODEL_BUNDLE_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema": "v15_nn_bundle/1", "bundle_id": bundle_id, "expected_sha256_by_filename": expected}))
    return gate.model_bundle_pin(root)


def make_champion_tree(root, reference_root, runtime_bytes=b"same-runtime"):
    root.mkdir(parents=True, exist_ok=True)
    tools = root / "tools"
    tools.mkdir()
    planner = tools / "v15_v18_planner.py"
    planner.write_text("""from pathlib import Path
import os
def learn_file(name, root=None, sub="learn"):
    base = Path(root or Path(__file__).resolve().parents[1])
    env = os.environ.get("V18_DIGEST_DIR" if sub == "learn" else "V18_BRAIN_DIR")
    first = (Path(env) if env else base / "data" / sub) / name
    if first.exists() or env:
        return first
    alt = base / "tools" / "v18_data" / sub / name
    if alt.exists():
        return alt
    flat = base / "tools" / f"v18_data_{sub}_{name}"
    return flat if flat.exists() else first
""")
    for name in sorted(gate.MODEL_ARTIFACT_NAMES):
        sub = "brain" if name.startswith("v18_brain_") else "learn"
        source = reference_root / "data" / sub / name
        target = tools / f"v18_data_{sub}_{name}"
        shutil.copyfile(source, target)
    (root / "runtime.py").write_bytes(runtime_bytes)
    return root


def make_runtime_spec(base, reference, candidate, champion_tree, snapshot, runtime_bytes=b"same-runtime", candidate_trained_at=TRAINED_AT):
    runtime_manifest = base / "runtime_manifest.json"
    runtime_manifest.write_text(json.dumps({"schema": "v15_nn_runtime/1", "code_root": str(champion_tree), "files": {"runtime.py": hashlib.sha256(runtime_bytes).hexdigest()}, "symlinks": {}}))
    reference_manifest = reference / gate.MODEL_BUNDLE_PATH
    candidate_manifest = candidate / gate.MODEL_BUNDLE_PATH
    champion_meta = base / "champion_meta.json"
    champion_meta.write_text(json.dumps({"flags": {"V20_SELECTOR": "1"}, "env": {}}))
    resolved = gate.champion_resolved_model_inventory(champion_tree, {"V20_SELECTOR": "1"})
    spec_path = base / "spec.json"
    ref_train = gate.model_training_market_max_by_category(reference)
    can_train = gate.model_training_market_max_by_category(candidate)
    if candidate_trained_at != TRAINED_AT:
        can_train = {key: candidate_trained_at for key in can_train}
    spec_path.write_text(json.dumps({
        "schema": "v15_nn_comparison/1",
        "comparison_id": "test",
        "runtime": {"path": str(runtime_manifest), "sha256": gate.sha256(runtime_manifest)},
        "reference": {"path": str(reference_manifest), "sha256": gate.sha256(reference_manifest)},
        "candidate": {"path": str(candidate_manifest), "sha256": gate.sha256(candidate_manifest)},
        "baseline_provenance": {"mode": "current_champion_resolver", "champion_meta_sha256": gate.sha256(champion_meta), "resolved_artifacts": resolved},
        "equal_runtime_flags": {},
        "reference_runtime_flags": {"V18_LINKNET_FIRST": "0", "V18_LINKNET_BUNDLE_REQUIRED": "0"},
        "candidate_runtime_flags": {"V18_LINKNET_FIRST": "1", "V18_LINKNET_BUNDLE_REQUIRED": "1"},
        "oos_holdout_days": 7,
        "training_market_max_by_category": {"reference": ref_train, "candidate": can_train},
        "snapshot": {key: snapshot[key] for key in ("snapshot_id", "manifest_sha256", "npz_sha256_by_symbol", "oos_start_by_side")},
        "promotion_eligible": True,
    }))
    return spec_path, runtime_manifest, champion_meta


def full_decision(sides, candidate_oos=120.0, candidate_wall=80.0, initial_pass=True):
    rows = [{"side": side, "oos_champ": 100.0, "oos_cand": candidate_oos, "wall_champ": 1000.0, "wall_cand": candidate_wall, "truth": True} for side in sides]
    return {"PASS": initial_pass, "harness_fail": False, "scored": sides, "truth_sides": 8, "rows": rows, "reasons": []}


class V15VersionGateModelOverlayTests(unittest.TestCase):
    def test_comparison_spec_pins_current_resolver_arm_flags_and_rejects_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            reference, candidate = base / "reference", base / "candidate"
            write_bundle(reference, "reference")
            write_bundle(candidate, "candidate")
            champion = make_champion_tree(base / "champion/tree", reference)
            snapshot = test_snapshot()
            spec_path, _, champion_meta = make_runtime_spec(base, reference, candidate, champion, snapshot)
            gate_champ = base / "gate/champion"
            gate_champ.mkdir(parents=True)
            shutil.copyfile(champion_meta, gate_champ / "meta.json")
            with patch.object(gate, "CHAMP", gate_champ):
                result = gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, {"V20_SELECTOR": "1"}, {"V18_LINKNET_FIRST": "1"})
                self.assertEqual(result["reference_flags"]["V18_LINKNET_FIRST"], "0")
                self.assertEqual(result["candidate_flags"]["V18_LINKNET_FIRST"], "1")
                self.assertEqual(result["reference_flags"]["V20_SELECTOR"], "1")
                self.assertEqual(result["candidate_flags"]["V20_SELECTOR"], "1")
                with self.assertRaisesRegex(ValueError, "conflicts with pinned candidate"):
                    gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, {"V20_SELECTOR": "1"}, {"V18_LINKNET_FIRST": "0"})
                (champion / "runtime.py").write_bytes(b"different-runtime")
                with self.assertRaisesRegex(ValueError, "comparison runtime file fails its declared SHA256"):
                    gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, {"V20_SELECTOR": "1"})

    def test_fresh_model_training_cutoff_is_rejected_against_exact_holdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            reference, candidate = base / "reference", base / "candidate"
            write_bundle(reference, "reference")
            write_bundle(candidate, "fresh", "2026-10-30T01:00:00Z")
            champion = make_champion_tree(base / "champion/tree", reference)
            snapshot = test_snapshot()
            spec_path, _, champion_meta = make_runtime_spec(base, reference, candidate, champion, snapshot, candidate_trained_at="2026-10-30T01:00:00Z")
            gate_champ = base / "gate/champion"
            gate_champ.mkdir(parents=True)
            shutil.copyfile(champion_meta, gate_champ / "meta.json")
            with patch.object(gate, "CHAMP", gate_champ), self.assertRaisesRegex(ValueError, "training market cutoff overlaps exact OOS holdout"):
                gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, {"V20_SELECTOR": "1"})

    def _finalize_with_real_model_roots(self, candidate_oos, candidate_wall, initial_pass):
        snapshot = test_snapshot()
        sides = snapshot["sides"]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            bench = base / "bench"
            gate_dir = base / "gate"
            champ_dir = gate_dir / "champion"
            champ_tree = champ_dir / "tree"
            candidate_dir = gate_dir / "candidates" / "test"
            candidate_tree = candidate_dir / "tree"
            champ_dir.mkdir(parents=True)
            candidate_tree.mkdir(parents=True)
            reference_source = base / "reference_source"
            ref_pin = write_bundle(reference_source, "reference")
            candidate_pin = write_bundle(candidate_tree, "candidate")
            make_champion_tree(champ_tree, reference_source)
            (champ_dir / "meta.json").write_text(json.dumps({"flags": {"V20_SELECTOR": "1"}, "env": {}}))
            spec_path, _, _ = make_runtime_spec(base, reference_source, candidate_tree, champ_tree, snapshot)
            with patch.object(gate, "CHAMP", champ_dir):
                spec_pin = gate.model_comparison_spec_pin(spec_path, reference_source, candidate_tree, ref_pin, candidate_pin, snapshot, champ_tree, {"V20_SELECTOR": "1"})
            pins = {}
            for label, bundle_id in (("gate_test_champ", "reference"), ("gate_test_cand", "candidate")):
                run_root = bench / "runs" / f"current8__{label}" / "root"
                run_root.mkdir(parents=True)
                shutil.copyfile(champ_tree / "runtime.py", run_root / "runtime.py")
                pins[label] = write_bundle(run_root, bundle_id)
                run_root.parent.mkdir(parents=True, exist_ok=True)
                receipt = {"manifest_sha256": pins[label]["manifest_sha256"], "artifact_sha256": pins[label]["artifact_sha256"]}
                (run_root.parent / "model_bundle_receipt.json").write_text(json.dumps(receipt))
            overlay = {
                "snapshot": snapshot,
                "baseline": pins["gate_test_champ"],
                "candidate": pins["gate_test_cand"],
                "baseline_source_path": str(reference_source),
                "candidate_flags": spec_pin["candidate_flags"],
                "reference_flags": spec_pin["reference_flags"],
                "base_flags": spec_pin["base_flags"],
                "comparison_spec": spec_pin,
            }
            meta = {"id": "test", "state": "running", "model_overlay": overlay, "files": {}, "flags": spec_pin["candidate_flags"], "env": {}}
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
        self.assertTrue(meta["decision"]["model_overlay_acceptance"]["training_windows_verified"])
        self.assertTrue(meta["decision"]["model_overlay_acceptance"]["run_root_model_hashes_verified"])
        deploy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
