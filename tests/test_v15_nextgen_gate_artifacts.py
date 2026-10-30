import json
from pathlib import Path

import pytest

from tools.v15_nextgen_gate_artifacts import inventory, overlay, resolve_models, verify_declared_bundle


def artifact(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def test_distinct_models_override_shared_snapshot_and_survive_next_candidate(tmp_path):
    relative = "data/brain/v18_brain_CRYPTO_LONG.json"
    champion, candidate, left, right, next_candidate = [tmp_path / name for name in ("champion", "candidate", "left", "right", "next")]
    artifact(champion, relative, {"weight": 1})
    artifact(candidate, relative, {"weight": 2})
    for root in (left, right):
        artifact(root, relative, {"weight": "shared_snapshot"})
    left_receipt = overlay(champion, left)
    right_receipt = overlay(candidate, right)
    assert json.loads((left / relative).read_text()) == {"weight": 1}
    assert json.loads((right / relative).read_text()) == {"weight": 2}
    assert left_receipt != right_receipt
    overlay(candidate, champion)
    overlay(champion, next_candidate)
    assert inventory(next_candidate) == right_receipt


def test_only_allowlisted_artifacts_copied_and_other_state_preserved(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    artifact(source, "data/learn/digest_STOCKS_SHORT.json", {"model": 2})
    artifact(source, "data/learn/production_STOCKS_SHORT.json", {"unsubmitted": 3})
    marker = artifact(target, "data/account.json", {"position": 42})
    overlay(source, target)
    assert not (target / "data/learn/production_STOCKS_SHORT.json").exists()
    assert json.loads(marker.read_text()) == {"position": 42}


@pytest.mark.parametrize("location", ["source_file", "source_parent", "destination_parent"])
def test_symlinks_refused(tmp_path, location):
    source, target, outside = tmp_path / "source", tmp_path / "target", tmp_path / "outside"
    relative = "data/brain/v18_brain_CRYPTO_LONG.json"
    original = artifact(source, relative, {"weight": 1})
    outside.mkdir()
    if location == "source_file":
        original.unlink()
        original.symlink_to(artifact(outside, "model.json", {"weight": 2}))
    elif location == "source_parent":
        original.unlink()
        original.parent.rmdir()
        original.parent.symlink_to(outside, target_is_directory=True)
    else:
        (target / "data").mkdir(parents=True)
        (target / "data/brain").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        overlay(source, target)


def test_malformed_model_refused_before_copy(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    artifact(source, "data/brain/v18_brain_CRYPTO_LONG.json", [])
    with pytest.raises(ValueError, match="JSON object"):
        overlay(source, target)
    assert not target.exists()


def load_repair(name, monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "tools"))
    spec = importlib.util.spec_from_file_location("isolated_" + name, root / "data/reports/nextgen_gate_artifact_repair" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_bench_build_overlays_candidate_after_snapshot(tmp_path, monkeypatch):
    module = load_repair("v15_npz_bench", monkeypatch)
    monkeypatch.setattr(module, "freeze_live_baseline", lambda snap: None)
    monkeypatch.delenv("BENCH_MOVE_DATA", raising=False)
    snapshot, candidate, run = [tmp_path / name for name in ("snapshot", "candidate", "run")]
    run.mkdir()
    relative = "data/learn/digest_STOCKS_LONG.json"
    artifact(snapshot, relative, {"model": "old"})
    artifact(candidate, relative, {"model": "new"})
    root = module.build_root(snapshot, run, candidate)
    assert json.loads((root / relative).read_text()) == {"model": "new"}
    assert json.loads((snapshot / relative).read_text()) == {"model": "old"}
    receipt = json.loads((run / "model_artifacts.json").read_text())
    assert receipt["overlaid"] == inventory(candidate)


def test_actual_gate_tree_copy_retains_models(tmp_path, monkeypatch):
    module = load_repair("v15_version_gate", monkeypatch)
    source, champion, candidate = [tmp_path / name for name in ("source", "champion", "candidate")]
    relative = "data/brain/v18_brain_STOCKS_SHORT.json"
    artifact(source, relative, {"weight": 9})
    module.rsync_tree(source, champion)
    module.rsync_tree(champion, candidate)
    assert inventory(source) == inventory(champion) == inventory(candidate)


def resolver_fixture(root):
    import shutil
    origin = Path(__file__).resolve().parents[1] / "tools/v15_v18_planner.py"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(origin, root / "tools/v15_v18_planner.py")
    for category in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        for sub, prefix in (("brain", "v18_brain"), ("learn", "digest")):
            artifact(root, f"tools/v18_data_{sub}_{prefix}_{category}.json", {"version": "flat"})


def test_actual_resolver_proves_precedence_and_bundle_mismatch(tmp_path):
    resolver_fixture(tmp_path)
    old = resolve_models(tmp_path, {})
    expected = {name: entry["sha256"] for name, entry in old["models"].items()}
    artifact(tmp_path, "data/brain/v18_brain_CRYPTO_LONG.json", {"version": "new"})
    new = resolve_models(tmp_path, {})
    assert new["models"]["v18_brain_CRYPTO_LONG.json"]["path"].startswith("data/brain/")
    with pytest.raises(ValueError, match="intended bundle"):
        resolve_models(tmp_path, {}, expected)


def test_actual_resolver_refuses_external_env_override(tmp_path):
    root = tmp_path / "root"
    resolver_fixture(root)
    outside = tmp_path / "live_models"
    artifact(outside, "v18_brain_CRYPTO_LONG.json", {"version": "live"})
    with pytest.raises(ValueError):
        resolve_models(root, {"V18_BRAIN_DIR": str(outside)})


def test_actual_resolver_missing_override_cannot_fall_back(tmp_path):
    resolver_fixture(tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_models(tmp_path, {"V18_DIGEST_DIR": "missing_digest_dir"})


def test_missing_model_destination_cannot_be_successful_deploy(tmp_path, monkeypatch):
    module = load_repair("v15_version_gate", monkeypatch)
    gate, candidate, target = [tmp_path / name for name in ("gate", "candidate", "target")]
    gate.mkdir()
    (target / "data").mkdir(parents=True)
    relative = "data/brain/v18_brain_CRYPTO_LONG.json"
    artifact(candidate / "tree", relative, {"model": 2})
    monkeypatch.setattr(module, "GATE", gate)
    monkeypatch.setattr(module, "deploy_targets", lambda: [("local", target, False)])
    records = module.deploy(candidate, {"id": "fixture", "env": {}, "files": {relative: "ignored_by_deploy"}})
    failure = next(record for record in records if record["file"] == relative)
    assert failure["ok"] is False
    assert failure["reason"] == "destination parent missing"
    assert not module.deploy_complete(records)


def declare_bundle(root):
    resolved = resolve_models(root, {})
    return artifact(root, "tools/v18_model_bundle.json", {"schema": "v15_nn_bundle/1", "bundle_id": "fixture", "expected_sha256_by_filename": {name: entry["sha256"] for name, entry in resolved["models"].items()}})


def test_declared_bundle_requires_all_eight_and_exact_hashes(tmp_path):
    resolver_fixture(tmp_path)
    manifest = declare_bundle(tmp_path)
    assert verify_declared_bundle(tmp_path, {})["bundle_id"] == "fixture"
    document = json.loads(manifest.read_text())
    document["expected_sha256_by_filename"].pop("digest_STOCKS_SHORT.json")
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="all eight"):
        verify_declared_bundle(tmp_path, {})


def test_legacy_optional_but_required_nn_cannot_omit_declaration(tmp_path):
    assert verify_declared_bundle(tmp_path, {}) is None
    with pytest.raises(ValueError, match="declared model bundle"):
        verify_declared_bundle(tmp_path, {"V15_REQUIRE_MODEL_BUNDLE": "1"})


def test_actual_bench_refuses_wrong_bundle_before_starting_pilot(tmp_path, monkeypatch):
    import types
    module = load_repair("v15_npz_bench", monkeypatch)
    root, run = tmp_path / "root", tmp_path / "run"
    resolver_fixture(root)
    declare_bundle(root)
    artifact(root, "data/brain/v18_brain_CRYPTO_LONG.json", {"wrong_model": True})
    (root / "v15_pilot.py").write_text("")
    monkeypatch.setattr(module, "build_root", lambda *args: root)
    monkeypatch.setattr(module, "mem_avail_mb", lambda: 100000)
    def forbidden_launch(*args, **kwargs):
        raise AssertionError("pilot launched before validating model bundle")
    monkeypatch.setattr(module.subprocess, "Popen", forbidden_launch)
    args = types.SimpleNamespace(code=str(root), env_file=None, set=[], window_days=30, only=None, _running={}, parallel=1, min_free_mb=1, workers=1)
    with pytest.raises(ValueError, match="intended bundle"):
        module._cmd_run_body(args, tmp_path / "snapshot", {"sides": ["COIN_SHORT"], "npz": {"COIN": {}}, "env": {}}, run)
