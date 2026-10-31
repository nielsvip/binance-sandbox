"""A finished fin 7D board becomes a real 7D row in per_sym_store_7d.db (USER 2026-10-31)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("fin_stage_one", ROOT / "tools" / "fin_7d_stage_one.py")
S = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = S
spec.loader.exec_module(S)

COLS = ("sym_side TEXT PRIMARY KEY, cat_side TEXT, updated_at TEXT, gain_30d REAL, overrides_json TEXT, "
        "defaults_snapshot_json TEXT, full_config_json TEXT, meta_json TEXT")


def base(tmp_path, meta=None):
    d = tmp_path / "base" / "x" / "data" / "hourly_reconfig"
    d.mkdir(parents=True)
    p = d / "per_sym_store.db"
    con = sqlite3.connect(str(p))
    con.execute(f"CREATE TABLE per_sym_active ({COLS})")
    con.execute("INSERT INTO per_sym_active VALUES (?,?,?,?,?,?,?,?)",
                ("AWEUSDT_LONG", "CRYPTO_LONG", "2026-10-30T10:00:00Z", 6.5, json.dumps({"A": 30}),
                 json.dumps({"A": 1, "B": 2}), json.dumps({"A": 30, "B": 2}),
                 json.dumps(meta if meta is not None else {"window_days": 30, "source": "v15_golive_headtohead"})))
    con.commit()
    con.close()
    return p


def store_path(tmp_path):
    d = tmp_path / "store" / "x" / "data" / "hourly_reconfig"
    d.mkdir(parents=True)
    return d / "per_sym_store_7d.db"


def row(path, ss="AWEUSDT_LONG"):
    con = sqlite3.connect(str(path))
    r = con.execute("select overrides_json, full_config_json, meta_json, gain_30d from per_sym_active where sym_side=?", (ss,)).fetchone()
    con.close()
    return [json.loads(r[0]), json.loads(r[1]), json.loads(r[2]), r[3]]


def test_the_7d_overrides_are_laid_on_the_30d_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    st = store_path(tmp_path)
    S.stage("AWEUSDT_LONG", {"A": 7}, base(tmp_path), st)
    ov, full, meta, _ = row(st)
    assert ov == {"A": 7}
    assert full == {"A": 7, "B": 2}, "7D overrides on top of the defaults snapshot, never the 30D overrides"


def test_the_row_declares_7d_and_keeps_its_base_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    st = store_path(tmp_path)
    S.stage("AWEUSDT_LONG", {"A": 7}, base(tmp_path), st)
    meta = row(st)[2]
    assert meta["window_days"] == 7 and meta["account"] == "fin" and meta["book"] == "men"
    assert meta["base_window_days"] == 30 and meta["base_source"] == "v15_golive_headtohead"


def test_the_store_is_stamped_7d_fin_and_refuses_a_30d_row(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    st = store_path(tmp_path)
    S.stage("AWEUSDT_LONG", {"A": 7}, base(tmp_path), st)
    con = sqlite3.connect(str(st))
    ident = json.loads(con.execute("select value_json from kv_json where key='store_identity'").fetchone()[0])
    assert ident["window_days"] == 7 and ident["kind"] == "7d_fin"
    try:
        con.execute("INSERT INTO per_sym_active (sym_side, meta_json) VALUES ('X', ?)", (json.dumps({"window_days": 30}),))
        raise AssertionError("a 30D row entered the fin 7D store")
    except sqlite3.DatabaseError as exc:
        assert "WRONG_WINDOW" in str(exc)
    con.close()


def test_restaging_replaces_the_row_and_still_passes_the_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    st = store_path(tmp_path)
    b = base(tmp_path)
    S.stage("AWEUSDT_LONG", {"A": 7}, b, st)
    S.stage("AWEUSDT_LONG", {"A": 8}, b, st)
    con = sqlite3.connect(str(st))
    n = con.execute("select count(*) from per_sym_active").fetchone()[0]
    con.close()
    assert n == 1 and row(st)[0] == {"A": 8}


def test_a_side_without_a_30d_base_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        S.stage("NOBASE_LONG", {"A": 7}, base(tmp_path), store_path(tmp_path))
        raise AssertionError("staged with no base")
    except ValueError as exc:
        assert "no 30D base row" in str(exc)


def test_empty_overrides_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for bad in ({}, None):
        try:
            S.stage("AWEUSDT_LONG", bad, base(tmp_path / str(bad)), store_path(tmp_path / str(bad)))
            raise AssertionError("staged empty overrides")
        except ValueError as exc:
            assert "no 7D cumulative_overrides" in str(exc)


def test_the_7d_store_is_readable_only_by_a_7d_producer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    st = store_path(tmp_path)
    S.stage("AWEUSDT_LONG", {"A": 7}, base(tmp_path), st)
    monkeypatch.setenv("PER_SYM_STORE_DB", str(st))
    for window, expect in ((7, str(st)), (30, None)):
        monkeypatch.setenv("PER_SYM_WINDOW_DAYS", str(window))
        spec2 = importlib.util.spec_from_file_location(f"fws_{window}", ROOT / "tools" / "v15_forward_state.py")
        m = importlib.util.module_from_spec(spec2)
        sys.modules[spec2.name] = m
        spec2.loader.exec_module(m)
        assert m.store_db() == expect, f"a {window}D producer must {'read' if expect else 'refuse'} the fin 7D store"
