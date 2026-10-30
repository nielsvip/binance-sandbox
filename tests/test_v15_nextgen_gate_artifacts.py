import json
from pathlib import Path

import pytest

from tools.v15_nextgen_gate_artifacts import inventory, overlay


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
