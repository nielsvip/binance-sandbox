import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import v15_version_gate as gate


TRAINED_AT = "2026-10-16T16:45:00Z"
BASE_FLAGS = {"V18_BRAIN": "1", "V20_SELECTOR": "1"}


def test_snapshot():
    sides = [f"S{i}_LONG" for i in range(4)] + [f"T{i}_SHORT" for i in range(4)]
    symbols = [side.rsplit("_", 1)[0] for side in sides]
    return {"snapshot_id": "current8", "manifest_sha256": "a" * 64, "sides": sides, "holdout_days": 7, "npz_sha256_by_symbol": {symbol: "b" * 64 for symbol in symbols}, "oos_start_by_side": {side: "2026-10-23T21:00:00Z" for side in sides}, "start_sha256_by_side": {side: hashlib.sha256(side.encode()).hexdigest() for side in sides}, "template_sha256_by_name": {f"TEMPLATE_{market}_{side}.xlsx": hashlib.sha256(f"{market}_{side}".encode()).hexdigest() for market in ("CRYPTO", "STOCKS") for side in ("LONG", "SHORT")}}


def write_bundle(root, bundle_id, trained_at=TRAINED_AT):
    expected = {}
    for name in sorted(gate.MODEL_ARTIFACT_NAMES):
        relative = Path("data/brain" if name.startswith("v18_brain_") else "data/learn") / name
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("v18_brain_"):
            value = {"_meta": {"last_ts": trained_at, "at": trained_at}}
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
    tools.mkdir(exist_ok=True)
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
    (root / "v15_pilot.py").write_text("# pinned test pilot\n")
    (root / "runtime.py").write_bytes(runtime_bytes)
    return root


def make_runtime_spec(base, reference, candidate, champion_tree, snapshot, runtime_bytes=b"same-runtime", candidate_trained_at=TRAINED_AT, promotion_eligible=True):
    runtime_manifest = base / "runtime_manifest.json"
    runtime_files = {path.relative_to(champion_tree).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in champion_tree.rglob("*") if path.is_file() and not path.is_symlink()}
    runtime_manifest.write_text(json.dumps({"schema": "v15_nn_runtime/1", "scope": "exact test tree inventory", "code_root": str(champion_tree), "files": runtime_files, "symlinks": {}}))
    reference_manifest = reference / gate.MODEL_BUNDLE_PATH
    candidate_manifest = candidate / gate.MODEL_BUNDLE_PATH
    champion_meta = base / "champion_meta.json"
    champion_meta.write_text(json.dumps({"flags": {"V18_BRAIN": "1", "V20_SELECTOR": "1"}, "env": {}}))
    resolved = gate.champion_resolved_model_inventory(champion_tree, {"V18_BRAIN": "1", "V20_SELECTOR": "1"})
    spec_path = base / "spec.json"
    ref_train = gate.model_training_market_max_by_category(reference)
    can_train = gate.model_training_market_max_by_category(candidate)
    eligibility = None
    if promotion_eligible:
        candidate_pin = gate.model_bundle_pin(candidate)
        proof = {"schema": "v18_brain_authority_eligibility/1", "snapshot_manifest_sha256": snapshot["manifest_sha256"], "sides": snapshot["sides"], "min_unique_evidence_by_side": {side: 101 for side in snapshot["sides"]}, "candidate_brain_sha256_by_category": {category: candidate_pin["artifact_sha256"][f"data/brain/v18_brain_{category}.json"] for category in gate.MODEL_CATEGORIES}}
        proof_path = base / "authority_eligibility.json"
        proof_path.write_text(json.dumps(proof))
        eligibility = {"path": str(proof_path), "sha256": gate.sha256(proof_path)}
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
        "snapshot": {key: snapshot[key] for key in ("snapshot_id", "manifest_sha256", "npz_sha256_by_symbol", "oos_start_by_side", "start_sha256_by_side", "template_sha256_by_name")},
        "promotion_eligible": promotion_eligible,
        "authority_eligibility": eligibility,
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
                result = gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS, {"V18_LINKNET_FIRST": "1"})
                self.assertEqual(result["reference_flags"]["V18_LINKNET_FIRST"], "0")
                self.assertEqual(result["candidate_flags"]["V18_LINKNET_FIRST"], "1")
                self.assertEqual(result["reference_flags"]["V20_SELECTOR"], "1")
                self.assertEqual(result["candidate_flags"]["V20_SELECTOR"], "1")
                with self.assertRaisesRegex(ValueError, "reference runtime flag V18_BRAIN differs"):
                    spec = json.loads(spec_path.read_text())
                    spec["reference_runtime_flags"]["V18_BRAIN"] = "0"
                    spec_path.write_text(json.dumps(spec))
                    gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS)
                spec = json.loads(spec_path.read_text())
                spec["reference_runtime_flags"].pop("V18_BRAIN")
                spec_path.write_text(json.dumps(spec))
                with self.assertRaisesRegex(ValueError, "conflicts with pinned candidate"):
                    gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS, {"V18_LINKNET_FIRST": "0"})
                with self.assertRaisesRegex(ValueError, "not declared in the pinned comparison spec"):
                    gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS, {"V20_PAIR_LIFT": "0"})
                (champion / "runtime.py").write_bytes(b"different-runtime")
                with self.assertRaisesRegex(ValueError, "comparison runtime file fails its declared SHA256"):
                    gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS)

    def test_promotion_boolean_without_101_proof_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            reference, candidate = base / "reference", base / "candidate"
            write_bundle(reference, "reference")
            write_bundle(candidate, "candidate")
            champion = make_champion_tree(base / "champion/tree", reference)
            snapshot = test_snapshot()
            spec_path, _, champion_meta = make_runtime_spec(base, reference, candidate, champion, snapshot)
            spec = json.loads(spec_path.read_text())
            spec["authority_eligibility"] = None
            spec_path.write_text(json.dumps(spec))
            gate_champ = base / "gate/champion"
            gate_champ.mkdir(parents=True)
            shutil.copyfile(champion_meta, gate_champ / "meta.json")
            with patch.object(gate, "CHAMP", gate_champ), self.assertRaisesRegex(ValueError, "101-unique-evidence proof receipt"):
                gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS)

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
                gate.model_comparison_spec_pin(spec_path, reference, candidate, gate.model_bundle_pin(reference), gate.model_bundle_pin(candidate), snapshot, champion, BASE_FLAGS)

    def _finalize_with_real_model_roots(self, candidate_oos, candidate_wall, initial_pass, promotion_eligible=True, corrupt_evidence=None, complete_deploy=False):
        snapshot = test_snapshot()
        sides = snapshot["sides"]
        snapshot["npz_sha256_by_symbol"] = {symbol: hashlib.sha256(symbol.encode()).hexdigest() for symbol in snapshot["npz_sha256_by_symbol"]}
        snapshot["template_sha256_by_name"] = {name: hashlib.sha256(name.encode()).hexdigest() for name in snapshot["template_sha256_by_name"]}
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
            make_champion_tree(candidate_tree, reference_source)
            (champ_dir / "meta.json").write_text(json.dumps({"flags": {"V18_BRAIN": "1", "V20_SELECTOR": "1"}, "env": {}}))
            spec_path, _, _ = make_runtime_spec(base, reference_source, candidate_tree, champ_tree, snapshot, promotion_eligible=promotion_eligible)
            with patch.object(gate, "CHAMP", champ_dir):
                spec_pin = gate.model_comparison_spec_pin(spec_path, reference_source, candidate_tree, ref_pin, candidate_pin, snapshot, champ_tree, BASE_FLAGS)
            pins = {}
            for label, bundle_id in (("gate_test_champ", "reference"), ("gate_test_cand", "candidate")):
                run_root = bench / "runs" / f"current8__{label}" / "root"
                shutil.copytree(champ_tree, run_root, dirs_exist_ok=True)
                pins[label] = write_bundle(run_root, bundle_id)
                run_root.parent.mkdir(parents=True, exist_ok=True)
                npz_dir = run_root / "backtest_v8" / "indicators"
                npz_dir.mkdir(parents=True)
                for symbol in snapshot["npz_sha256_by_symbol"]:
                    npz_path = npz_dir / f"{symbol}.npz"
                    npz_path.write_bytes(symbol.encode())
                for name in snapshot["template_sha256_by_name"]:
                    for parent in (run_root / "SPREADSHEETS", run_root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"):
                        parent.mkdir(parents=True, exist_ok=True)
                        (parent / name).write_bytes(name.encode())
                receipt = {"manifest_sha256": pins[label]["manifest_sha256"], "artifact_sha256": pins[label]["artifact_sha256"], "snapshot_inputs": {key: snapshot[key] for key in ("npz_sha256_by_symbol", "template_sha256_by_name", "start_sha256_by_side")}}
                (run_root.parent / "model_bundle_receipt.json").write_text(json.dumps(receipt))
            for arm, label, code_root in (("baseline", "gate_test_champ", champ_tree), ("candidate", "gate_test_cand", candidate_tree)):
                run_dir = bench / "runs" / f"current8__{label}"
                flags = {"V18_BRAIN": "1", "V20_SELECTOR": "1", "V15_BRAIN": "0", "V15_BRAIN_NAKED": "0"}
                flags.update({"V18_LINKNET_FIRST": "0", "V18_LINKNET_BUNDLE_REQUIRED": "0"} if arm == "baseline" else {"V18_LINKNET_FIRST": "1", "V18_LINKNET_BUNDLE_REQUIRED": "1"})
                rows = {}
                for side in sides:
                    venue = "crypto" if side.startswith("S") else "stocks"
                    category = f"{'CRYPTO' if venue == 'crypto' else 'STOCKS'}_{side.rsplit('_', 1)[-1]}"
                    brain = json.loads((run_dir / "root" / "data" / "brain" / f"v18_brain_{category}.json").read_text())
                    v18 = {"brain": {"model_at": brain["_meta"]["at"]}, "v20": {"why": "binding_map ok"}}
                    if arm == "candidate":
                        v18["linknet_first"] = {"proposals": 0, "booked_cells": 0, "cells_forced": 0, "why": "ok linknet proposals=0"}
                    side_dir = run_dir / side / "progress"
                    side_dir.mkdir(parents=True)
                    (side_dir / f"{side}_30d_progress.json").write_text(json.dumps({"symside": side, "window_days": 30, "v18": v18}))
                    rows[side] = {"rc": 0, "wall_s": (1000.0 if arm == "baseline" else candidate_wall) / len(sides), "final_gain": 1.0, "oos_final": 1.0}
                nn_env = {k: v for k, v in flags.items() if k.startswith(("V18_", "V20_"))}
                result_payload = {"snap": "current8", "label": label, "code": str(code_root), "pilot_md5": gate.md5(run_dir / "root" / "v15_pilot.py"), "set": ["V15_HOLDOUT_DAYS=7", *(f"{k}={v}" for k, v in flags.items())], "window_days_fit": 30, "holdout_days": 7, "nn_env_sha256": hashlib.sha256(json.dumps(nn_env, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "board_wall_s": 1000.0 if arm == "baseline" else candidate_wall, "retry_count": 0, "results": rows}
                if corrupt_evidence == label:
                    result_payload["code"] = "/wrong/tree"
                (run_dir / "results.json").write_text(json.dumps(result_payload))
            overlay = {
                "snapshot": snapshot,
                "baseline": pins["gate_test_champ"],
                "candidate": pins["gate_test_cand"],
                "baseline_source_path": str(reference_source),
                "candidate_code_root": str(candidate_tree.resolve()),
                "candidate_flags": spec_pin["candidate_flags"],
                "reference_flags": spec_pin["reference_flags"],
                "base_flags": spec_pin["base_flags"],
                "comparison_spec": spec_pin,
            }
            model_files = {gate.MODEL_BUNDLE_PATH: "manifest"}
            model_files.update({relative: "artifact" for relative in candidate_pin["artifact_sha256"]})
            meta = {"id": "test", "state": "running", "model_overlay": overlay, "files": model_files, "flags": spec_pin["candidate_flags"], "env": {}}
            with patch.object(gate, "BENCH", bench), \
                 patch.object(gate, "SNAP", "current8"), \
                 patch.object(gate, "GATE", gate_dir), \
                 patch.object(gate, "CHAMP", champ_dir), \
                 patch.object(gate, "DECISIONS", gate_dir / "decisions.jsonl"), \
                 patch.object(gate, "STATUS", gate_dir / "status.md"), \
                 patch.object(gate, "model_snapshot_pin", return_value=snapshot), \
                patch.object(gate, "snap_manifest", return_value={"admission": {"admission": {side: {"venue": "crypto" if side.startswith("S") else "stocks"} for side in sides}}}), \
                 patch.object(gate, "write_status"), \
                 patch.object(gate, "deploy", return_value=[{"ok": True}]) as deploy, \
                 patch.object(gate, "deploy_complete", return_value=complete_deploy), \
                 patch.object(gate, "rsync_tree"):
                gate.finalize(candidate_dir, meta, full_decision(sides, candidate_oos, candidate_wall, initial_pass))
                if complete_deploy:
                    self.assertEqual(gate.model_bundle_pin(champ_tree), meta["model_overlay"]["candidate"])
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

    def test_finalize_refuses_results_bound_to_other_code(self):
        meta, deploy = self._finalize_with_real_model_roots(120.0, 80.0, True, corrupt_evidence="gate_test_cand")
        self.assertEqual(meta["state"], "FAIL")
        self.assertIn("results.json snapshot/label/code", json.dumps(meta["decision"].get("reasons")))
        deploy.assert_not_called()

    def test_finalize_refuses_unproven_promotion_eligibility(self):
        meta, deploy = self._finalize_with_real_model_roots(120.0, 80.0, True, promotion_eligible=False)
        self.assertEqual(meta["state"], "FAIL")
        deploy.assert_not_called()

    def test_finalize_installs_and_verifies_model_bundle_in_champion_before_metadata(self):
        meta, deploy = self._finalize_with_real_model_roots(120.0, 80.0, True, complete_deploy=True)
        self.assertEqual(meta["state"], "PASS")
        deploy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
