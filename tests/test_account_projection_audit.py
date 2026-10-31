import json

import pytest

from tools.account_projection_audit import _canonical_sha256, _safe_event, _snapshot_for, _state_by_hash


def test_immutable_decision_state_hash_is_verified(tmp_path):
    root = tmp_path
    state = {"schema_version": 1, "sym_side": "QNTUSDT_LONG", "ok": True}
    state_hash = _canonical_sha256(state)
    path = root / "data/vector_direct/decision_states" / state_hash[:2] / f"{state_hash}.json"
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
    snapshot = {"schema_version": 2, "account": "ang", "sym_side": "QNTUSDT_LONG", "qty": 1.0}
    snapshot_hash = _canonical_sha256(snapshot)
    snapshot["snapshot_sha256"] = snapshot_hash
    snapshot_path = folder / f"{snapshot_hash}.json"
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    pointer = {"snapshot_path": f"snapshots/ang/QNTUSDT_LONG/{snapshot_hash}.json", "snapshot_sha256": snapshot_hash}
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
