import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import v20_candidate_refresh as refresh


def inputs(tmp_path):
    context = tmp_path / "snapshot"
    for cat_side in refresh.CAT_SIDES:
        template = context / "SPREADSHEETS" / f"TEMPLATE_{cat_side}.xlsx"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_bytes(b"snapshot template")
        join = context / "data/learn/linknet/v2" / f"ctxjoin_{cat_side}.pkl.gz"
        join.parent.mkdir(parents=True, exist_ok=True)
        join.write_bytes(b"snapshot context")
    spills = tmp_path / "spills"
    spills.mkdir()
    (spills / "spill_test.tsv").write_text("frozen evidence")
    return context, spills


def test_plan_is_read_only_and_routes_all_outputs_to_candidate(tmp_path):
    context, spills = inputs(tmp_path)
    run = tmp_path / "candidate"
    candidate = refresh.plan(run, [spills], context, "2026-10-22T00:00:00Z")
    assert not run.exists()
    assert candidate["status"] == "planned"
    assert candidate["eligibility"]["status"] == "unverified_not_promotable"
    for command in candidate["commands"]:
        for flag in ("--out-dir", "--tmp-dir", "--ds-dir", "--state", "--report"):
            if flag in command:
                assert Path(command[command.index(flag) + 1]).is_relative_to(run)


def test_context_symlinks_and_existing_outputs_are_refused(tmp_path):
    context, spills = inputs(tmp_path)
    (context / "link").symlink_to(spills)
    with pytest.raises(ValueError, match="symlinks"):
        refresh.plan(tmp_path / "candidate", [spills], context, "2026-10-22T00:00:00Z")
    (context / "link").unlink()
    (context / "model_v2_CRYPTO_LONG.npz").write_bytes(b"old model")
    with pytest.raises(ValueError, match="pre-existing"):
        refresh.plan(tmp_path / "candidate", [spills], context, "2026-10-22T00:00:00Z")


def test_failed_phase_persists_incomplete_manifest(tmp_path, monkeypatch):
    context, spills = inputs(tmp_path)
    candidate = refresh.plan(tmp_path / "candidate", [spills], context, "2026-10-22T00:00:00Z")
    monkeypatch.setattr(refresh.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=9))
    with pytest.raises(RuntimeError, match="phase 0"):
        refresh.execute(candidate)
    manifest = json.loads((tmp_path / "candidate/manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["phases"][0]["status"] == "failed"
    assert manifest["deployed"] is False


def test_zero_exit_cannot_claim_build_without_required_artifacts(tmp_path, monkeypatch):
    context, spills = inputs(tmp_path)
    candidate = refresh.plan(tmp_path / "candidate", [spills], context, "2026-10-22T00:00:00Z")
    monkeypatch.setattr(refresh.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(ValueError, match="missing required artifacts"):
        refresh.execute(candidate)
    assert json.loads((tmp_path / "candidate/manifest.json").read_text())["status"] == "failed"


def test_mutated_source_disqualifies_candidate(tmp_path, monkeypatch):
    context, spills = inputs(tmp_path)
    candidate = refresh.plan(tmp_path / "candidate", [spills], context, "2026-10-22T00:00:00Z")
    def mutate(*args, **kwargs):
        (spills / "spill_test.tsv").write_text("changed evidence")
        (Path(candidate["run_dir"]) / "brain").mkdir(exist_ok=True)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(refresh.subprocess, "run", mutate)
    with pytest.raises(ValueError, match="source changed"):
        refresh.execute(candidate)
