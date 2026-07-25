import json

from tools import switch_matrix_digest as digest


def test_load_vec_research_prefers_newest_artifact_per_window(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    old = root / "top_exit_20260725T100000Z_MU_LONG"
    new = root / "top_exit_20260725T110000Z_MU_LONG"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    payload = {
        "start": "2024-01-01",
        "end_exclusive": None,
        "contract_valid": True,
        "policy_top20": [{"strategy": "old"}],
    }
    (old / "digest_MU_LONG.json").write_text(json.dumps(payload))
    payload["policy_top20"] = [{"strategy": "new"}]
    (new / "digest_MU_LONG.json").write_text(json.dumps(payload))
    # File mtimes, not directory names, define freshness.
    (old / "digest_MU_LONG.json").touch()
    (new / "digest_MU_LONG.json").touch()
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_vec_research(("MU_LONG",))

    assert len(rows["MU_LONG"]) == 1
    assert digest.vec_candidate(rows["MU_LONG"][0])["strategy"] == "new"


def test_load_vec_research_surfaces_quarantine_without_digest(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    directory = (
        reports
        / "vec_research"
        / "top_exit_20260725T100000Z_HAO_SHORT_QUARANTINE"
    )
    directory.mkdir(parents=True)
    (directory / "quarantine.json").write_text(
        json.dumps({"reason": "invalid HTF alias"})
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_vec_research(("HAO_SHORT",))

    assert len(rows["HAO_SHORT"]) == 1
    assert rows["HAO_SHORT"][0]["contract_valid"] is False
    assert rows["HAO_SHORT"][0]["invalid_data_diagnostic"] is True
    assert digest.vec_candidate(rows["HAO_SHORT"][0]) is None


def test_load_exact_replays_keys_latest_summary_by_source(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    source = root / "top_exit_source_MU_LONG"
    source.mkdir(parents=True)
    first = root / "v8_exact_replay_1_MU_LONG"
    second = root / "v8_exact_replay_2_MU_LONG"
    first.mkdir()
    second.mkdir()
    (first / "run_summary.json").write_text(
        json.dumps({"source_artifact": str(source), "status": "FAIL"})
    )
    (second / "run_summary.json").write_text(
        json.dumps({"source_artifact": str(source), "status": "PASS"})
    )
    (first / "run_summary.json").touch()
    (second / "run_summary.json").touch()
    monkeypatch.setattr(digest, "REPORTS", reports)

    rows = digest.load_exact_replays()

    assert rows[str(source.resolve())]["status"] == "PASS"


def test_load_latest_robust_walk_forward_prefers_newest(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    old = root / "walkforward_top_exit_1_MU_LONG"
    new = root / "walkforward_top_exit_2_MU_LONG"
    old.mkdir(parents=True)
    new.mkdir()
    name = "walkforward_digest_MU_LONG.json"
    (old / name).write_text(json.dumps({"run_id": "old"}))
    (new / name).write_text(json.dumps({"run_id": "new"}))
    (old / name).touch()
    (new / name).touch()
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_latest_robust_walk_forward(("MU_LONG",))

    assert rows["MU_LONG"]["run_id"] == "new"


def test_load_latest_partial_regime_walk_forward(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    root = reports / "vec_research"
    directory = root / "partial_regime_walkforward_1_VT_LONG"
    directory.mkdir(parents=True)
    (directory / "digest_VT_LONG.json").write_text(
        json.dumps({"run_id": "vt-partial"})
    )
    monkeypatch.setattr(digest, "REPORTS", reports)
    monkeypatch.setattr(digest, "BASE", tmp_path)

    rows = digest.load_latest_partial_regime_walk_forward(("VT_LONG",))

    assert rows["VT_LONG"]["run_id"] == "vt-partial"
