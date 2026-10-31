import json

import pytest

from tools.account_projection_audit import (
    _canonical_sha256,
    _safe_event,
    _snapshot_for,
    _state_by_hash,
    run_audit,
)


def test_immutable_decision_state_hash_is_verified(tmp_path):
    root = tmp_path
    state = {"schema_version": 1, "sym_side": "QNTUSDT_LONG", "ok": True}
    state_hash = _canonical_sha256(state)
    path = (
        root
        / "data/vector_direct/decision_states"
        / state_hash[:2]
        / f"{state_hash}.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")))
    found, payload = _state_by_hash(root, state_hash)
    assert found == path
    assert payload == state
    path.write_text('{"ok":false}')
    with pytest.raises(ValueError, match="content hash mismatch"):
        _state_by_hash(root, state_hash)


def test_snapshot_pointer_and_content_hash_are_both_verified(tmp_path):
    root = tmp_path
    base = root / "data/vector_direct/account_execution"
    folder = base / "snapshots/ang/QNTUSDT_LONG"
    folder.mkdir(parents=True)
    snapshot = {
        "schema_version": 2,
        "account": "ang",
        "sym_side": "QNTUSDT_LONG",
        "qty": 1.0,
    }
    snapshot_hash = _canonical_sha256(snapshot)
    snapshot["snapshot_sha256"] = snapshot_hash
    snapshot_path = folder / f"{snapshot_hash}.json"
    snapshot_path.write_text(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    )
    pointer = {
        "snapshot_path": f"snapshots/ang/QNTUSDT_LONG/{snapshot_hash}.json",
        "snapshot_sha256": snapshot_hash,
    }
    pointer_path = folder / "latest.json"
    pointer_path.write_text(json.dumps(pointer))
    got_pointer, got_path, got_snapshot = _snapshot_for(root, "ang", "QNTUSDT_LONG")
    assert got_pointer == pointer_path
    assert got_path == snapshot_path
    assert got_snapshot["snapshot_sha256"] == snapshot_hash
    pointer["snapshot_sha256"] = "0" * 64
    pointer_path.write_text(json.dumps(pointer))
    with pytest.raises(ValueError, match="content-address verification failed"):
        _snapshot_for(root, "ang", "QNTUSDT_LONG")


def test_observation_event_projection_export_omits_return_fields():
    event = {
        "type": "REDUCE",
        "bar_exit": 1793414700,
        "qty": 0.2,
        "price": 10,
        "pnl_dollars": 5,
        "pnl_pct": 3,
        "pnl_is_lifecycle": True,
        "account_partial_projection": {"projection_only_not_a_fill": True},
    }
    exported = _safe_event(event, 1793414700)
    assert exported == {
        "type": "REDUCE",
        "bar_exit": 1793414700,
        "qty": 0.2,
        "price": 10,
        "lifecycle_terminal_marker": True,
        "account_partial_projection": {"projection_only_not_a_fill": True},
    }


def test_wrong_engine_identity_refuses_before_replay(tmp_path, monkeypatch):
    from tools import account_projection_audit as audit

    state = {"engine_ast_sha256": "0" * 64}
    monkeypatch.setattr(
        audit,
        "_snapshot_for",
        lambda *args: (
            tmp_path / "latest.json",
            tmp_path / "snapshot.json",
            {"state_sha256": "state-hash"},
        ),
    )
    monkeypatch.setattr(
        audit, "_state_by_hash", lambda *args: (tmp_path / "state.json", state)
    )
    engine = tmp_path / "engine.py"
    engine.write_text("def simulate_one():\n    return 1\n")
    result = run_audit(tmp_path, "ang", "QNTUSDT_LONG", engine_module_path=engine)
    assert result["status"] == "REFUSED_ENGINE_IDENTITY"
    assert "engine_identity_mismatch" in result["labels"]
    assert result["engine_identity"]["candidate_source_path"] == str(engine)


def test_supplied_matching_engine_is_loaded_for_audit_and_restored(
    tmp_path, monkeypatch
):
    import ast
    import sys

    from tools import account_projection_audit as audit

    engine = tmp_path / "engine.py"
    engine.write_text("def simulate_one():\n    return 17\n")
    tree = ast.parse(engine.read_text())
    expected = audit._sha256_bytes(ast.dump(tree, include_attributes=False).encode())
    state = {"engine_ast_sha256": expected}
    snapshot = {"state_sha256": "state-hash"}
    monkeypatch.setattr(
        audit,
        "_snapshot_for",
        lambda *args: (tmp_path / "latest.json", tmp_path / "snapshot.json", snapshot),
    )
    monkeypatch.setattr(
        audit, "_state_by_hash", lambda *args: (tmp_path / "state.json", state)
    )
    previous = sys.modules.get("v12_quick_engine")
    called = {}

    def fake_run(root, account, sym_side, **kwargs):
        import v12_quick_engine

        called["path"] = v12_quick_engine.__file__
        called["args"] = kwargs
        return {"status": "TESTED"}

    monkeypatch.setattr(audit, "_run_audit_with_engine", fake_run)
    result = run_audit(tmp_path, "ang", "QNTUSDT_LONG", engine_module_path=engine)
    assert result == {"status": "TESTED"}
    assert called["path"] == str(engine)
    assert called["args"]["engine_module_path"] == engine.resolve()
    assert sys.modules.get("v12_quick_engine") is previous
