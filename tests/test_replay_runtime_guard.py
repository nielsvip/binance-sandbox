import json
import sqlite3

import pytest

from tools.replay_runtime_guard import _config_hash, assert_frozen_active_row, assert_runtime_paths

def test_replay_paths_reject_production_default_before_any_simulation(tmp_path, monkeypatch):
    code = tmp_path / "run" / "runtime" / "code"
    code.mkdir(parents=True)
    monkeypatch.setenv("V12_BASE_PATH", str(code))
    monkeypatch.delenv("BASE_PATH", raising=False)
    with pytest.raises(RuntimeError, match="BASE_PATH"):
        assert_runtime_paths(code, tmp_path / "run", {})
    monkeypatch.setenv("BASE_PATH", str(tmp_path / "production"))
    with pytest.raises(RuntimeError, match="BASE_PATH"):
        assert_runtime_paths(code, tmp_path / "run", {})

def test_run_paths_reject_escaped_state_and_symlinks(tmp_path, monkeypatch):
    run = tmp_path / "run"
    code = run / "code"
    code.mkdir(parents=True)
    for key in ("BASE_PATH", "V12_BASE_PATH"):
        monkeypatch.setenv(key, str(code))
    outside = tmp_path / "production.db"
    outside.touch()
    link = code / "state.db"
    link.symlink_to(outside)
    with pytest.raises(RuntimeError, match="escapes"):
        assert_runtime_paths(code, run, {"db": link})
    local = code / "local.db"
    local.touch()
    assert assert_runtime_paths(code, run, {"db": local}) == {"db": str(local)}

def test_frozen_db_must_contain_exact_full_set_not_empty_fallback(tmp_path):
    overrides = {"SWITCH": True, "THRESHOLD": 3.0}
    active = tmp_path / "active.json"
    active.write_text(json.dumps({"sym_side": "TEST_LONG", "overrides": overrides, "overrides_sha256": _config_hash(overrides)}))
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE per_sym_active (sym_side TEXT, full_config_json TEXT)")
    with pytest.raises(RuntimeError, match="missing or different"):
        assert_frozen_active_row(db, active)
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO per_sym_active VALUES (?,?)", ("TEST_LONG", json.dumps({"SWITCH": True})))
    with pytest.raises(RuntimeError, match="missing or different"):
        assert_frozen_active_row(db, active)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE per_sym_active SET full_config_json=?", (json.dumps(overrides),))
    assert assert_frozen_active_row(db, active)["full_config_sha256"] == _config_hash(overrides)


def test_derived_per_trade_paths_cannot_escape_even_with_correct_base(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.replay_runtime_guard import assert_state_module_paths
    code = tmp_path / "run" / "code"
    code.mkdir(parents=True)
    for key in ("BASE_PATH", "V12_BASE_PATH"):
        monkeypatch.setenv(key, str(code))
    writer = SimpleNamespace(_BASE=code, _OUT_DIR=code / "data" / "per_trade_returns", _ATTEMPT_DIR=code / "data" / "per_trade_attempts", _IDEM_DIR=code / "data" / "per_trade_returns" / "_idem")
    modules = {"per_trade_logger": writer}
    assert len(assert_state_module_paths(code, code.parent, modules)) == 4
    writer._OUT_DIR = tmp_path / "production" / "data"
    with pytest.raises(RuntimeError, match="escapes"):
        assert_state_module_paths(code, code.parent, modules)
    with pytest.raises(RuntimeError, match="must import per_trade_logger"):
        assert_state_module_paths(code, code.parent, {})
