"""The state producer refuses a store that says it holds a different window (USER 2026-10-31)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(monkeypatch, window=None):
    if window is None:
        monkeypatch.delenv("PER_SYM_WINDOW_DAYS", raising=False)
    else:
        monkeypatch.setenv("PER_SYM_WINDOW_DAYS", str(window))
    spec = importlib.util.spec_from_file_location("fws_window", ROOT / "tools" / "v15_forward_state.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def store(tmp_path, name, window=None):
    p = tmp_path / name
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE kv_json (key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT, epoch REAL)")
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT)")
    if window is not None:
        con.execute("INSERT INTO kv_json (key, value_json) VALUES ('store_identity', ?)", (json.dumps({"window_days": window, "kind": f"{window}d"}),))
    con.commit()
    con.close()
    return p


def test_a_30d_lane_refuses_the_7d_store(tmp_path, monkeypatch):
    p7 = store(tmp_path, "per_sym_store_trc7d.db", 7)
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p7))
    m = load(monkeypatch)
    assert m.EXPECTED_WINDOW_DAYS == 30 and m.store_db() is None, "a 30D lane pointed at a 7D store must publish nothing"


def test_a_7d_lane_that_declares_7_reads_the_7d_store(tmp_path, monkeypatch):
    p7 = store(tmp_path, "per_sym_store_trc7d.db", 7)
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p7))
    assert load(monkeypatch, 7).store_db() == str(p7)


def test_a_7d_lane_refuses_the_30d_store(tmp_path, monkeypatch):
    p30 = store(tmp_path, "per_sym_store.db", 30)
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p30))
    assert load(monkeypatch, 7).store_db() is None


def test_the_matching_30d_store_resolves(tmp_path, monkeypatch):
    p30 = store(tmp_path, "per_sym_store.db", 30)
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p30))
    assert load(monkeypatch).store_db() == str(p30)


def test_an_unstamped_store_still_resolves_so_no_lane_is_cut_off(tmp_path, monkeypatch):
    p = store(tmp_path, "per_sym_store.db", None)
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p))
    assert load(monkeypatch).store_db() == str(p)


def _store_with_row(tmp_path, name, window, rows):
    p = store(tmp_path, name, window)
    con = sqlite3.connect(str(p))
    con.execute("DROP TABLE per_sym_active")
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT, updated_at TEXT, full_config_json TEXT, template_md5 TEXT, defaults_round TEXT)")
    con.execute("CREATE TABLE per_sym_history (id INTEGER PRIMARY KEY, sym_side TEXT, updated_at TEXT, full_config_json TEXT, template_md5 TEXT, defaults_round TEXT, epoch REAL)")
    for ss, full in rows:
        con.execute("INSERT INTO per_sym_active VALUES (?,?,?,?,?)", (ss, "t", json.dumps(full), "m", "r"))
    con.commit()
    con.close()
    return p


def test_a_7d_producer_never_falls_back_to_30d_derived_settings(tmp_path, monkeypatch):
    p7 = _store_with_row(tmp_path, "per_sym_store_7d.db", 7, [])
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p7))
    m = load(monkeypatch, 7)
    called = []
    monkeypatch.setattr(m, "_best_existing_settings", lambda ss, db: called.append(ss) or ({"X": 1}, "fallback:per_sym_full_sets.json", {}))
    settings, src, meta = m._load_overrides("UNSTAGED_LONG")
    assert settings == {} and src == "no_7d_set_staged" and called == [], "a 7D arm may only trade a staged 7D set"


def test_a_30d_producer_still_falls_back_as_the_30d_law_requires(tmp_path, monkeypatch):
    p30 = _store_with_row(tmp_path, "per_sym_store.db", 30, [])
    monkeypatch.setenv("PER_SYM_STORE_DB", str(p30))
    m = load(monkeypatch)
    monkeypatch.setattr(m, "_best_existing_settings", lambda ss, db: ({"X": 1}, "fallback:per_sym_full_sets.json", {}))
    settings, src, meta = m._load_overrides("NOBOARD_LONG")
    assert src == "fallback:per_sym_full_sets.json", "every 30D side still trades on its best existing settings"
