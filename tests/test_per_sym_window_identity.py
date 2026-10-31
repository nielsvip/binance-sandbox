"""Each per-sym store states its window, and SQLite itself refuses a row declaring another (USER 2026-10-31)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("win_id", ROOT / "tools" / "per_sym_window_identity.py")
W = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = W
spec.loader.exec_module(W)

ACTIVE = ("sym_side TEXT PRIMARY KEY, cat_side TEXT, updated_at TEXT, gain_30d REAL, overrides_json TEXT, "
          "full_config_json TEXT, meta_json TEXT")


def make_store(tmp_path, name, rows):
    d = tmp_path / "binance-sandbox" / "data" / "hourly_reconfig"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    con = sqlite3.connect(str(path))
    con.execute(f"CREATE TABLE per_sym_active ({ACTIVE})")
    con.execute("CREATE TABLE per_sym_history (id INTEGER PRIMARY KEY, sym_side TEXT, meta_json TEXT, full_config_json TEXT)")
    con.execute("CREATE TABLE kv_json (key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT, epoch REAL)")
    for ss, meta in rows:
        con.execute("INSERT INTO per_sym_active (sym_side, overrides_json, full_config_json, meta_json, gain_30d) VALUES (?,?,?,?,?)",
                    (ss, json.dumps({"X": 1}), json.dumps({"X": 1, "Y": 2}), json.dumps(meta), 4.2))
    con.commit()
    con.close()
    return path


def rows(path):
    con = sqlite3.connect(str(path))
    out = {ss: (json.loads(m), o, f, g) for ss, m, o, f, g in
           con.execute("select sym_side, meta_json, overrides_json, full_config_json, gain_30d from per_sym_active")}
    con.close()
    return out


def test_a_genuine_7d_row_that_inherited_a_30d_label_is_relabelled_truthfully(tmp_path):
    """The trc stager copies its 30D base's meta_json, so a real 7D row said window_days 30."""
    path = make_store(tmp_path, "per_sym_store_trc7d.db",
                      [("ACN_LONG", {"window_days": 30, "source": "v15_golive_headtohead"}), ("BMNR_SHORT", {"source": "switch_parity"})])
    W.process(path, tmp_path / "bk", dry=False)
    r = rows(path)
    assert r["ACN_LONG"][0]["window_days"] == 7
    assert r["ACN_LONG"][0]["base_window_days"] == 30 and r["ACN_LONG"][0]["base_source"] == "v15_golive_headtohead", \
        "the inherited base must stay visible, not be discarded"
    assert r["BMNR_SHORT"][0]["window_days"] == 7


def test_no_settings_value_is_ever_changed(tmp_path):
    path = make_store(tmp_path, "per_sym_store_trc7d.db", [("ACN_LONG", {"window_days": 30})])
    before = rows(path)["ACN_LONG"][1:]
    W.process(path, tmp_path / "bk", dry=False)
    assert rows(path)["ACN_LONG"][1:] == before, "overrides, full config and metrics must be untouched"


def test_the_30d_store_refuses_a_7d_row_and_the_7d_store_refuses_a_30d_row(tmp_path):
    for name, wrong in (("per_sym_store.db", 7), ("per_sym_store_trc7d.db", 30)):
        path = make_store(tmp_path / name, name, [("OK_LONG", {})])
        W.process(path, tmp_path / name / "bk", dry=False)
        con = sqlite3.connect(str(path))
        try:
            con.execute("INSERT INTO per_sym_active (sym_side, meta_json) VALUES (?, ?)", ("BAD", json.dumps({"window_days": wrong})))
            raise AssertionError(f"{name} accepted a {wrong}D row")
        except sqlite3.DatabaseError as exc:
            assert "WRONG_WINDOW" in str(exc)
        try:
            con.execute("UPDATE per_sym_active SET meta_json=? WHERE sym_side='OK_LONG'", (json.dumps({"window_days": wrong}),))
            raise AssertionError(f"{name} accepted an update to a {wrong}D label")
        except sqlite3.DatabaseError as exc:
            assert "WRONG_WINDOW" in str(exc)
        con.close()


def test_a_60d_row_cannot_enter_the_30d_store(tmp_path):
    path = make_store(tmp_path, "per_sym_store.db", [])
    W.process(path, tmp_path / "bk", dry=False)
    con = sqlite3.connect(str(path))
    try:
        con.execute("INSERT INTO per_sym_active (sym_side, meta_json) VALUES ('X', ?)", (json.dumps({"window_days": 60}),))
        raise AssertionError("a 60D row entered the 30D store")
    except sqlite3.DatabaseError as exc:
        assert "WRONG_WINDOW" in str(exc)
    con.close()


def test_writers_that_declare_their_own_window_or_none_still_work(tmp_path):
    """Every existing 30D writer records no window or 30; the legitimate 7D writer records 7."""
    p30 = make_store(tmp_path / "a", "per_sym_store.db", [])
    p7 = make_store(tmp_path / "b", "per_sym_store_trc7d.db", [])
    W.process(p30, tmp_path / "bk1", dry=False)
    W.process(p7, tmp_path / "bk2", dry=False)
    for path, ok_meta in ((p30, {}), (p30, {"window_days": 30}), (p7, {"window_days": 7})):
        con = sqlite3.connect(str(path))
        con.execute("INSERT INTO per_sym_active (sym_side, meta_json) VALUES (?, ?)", (f"S{len(ok_meta)}_{path.name}", json.dumps(ok_meta)))
        con.commit()
        con.close()


def test_the_store_identity_is_stamped_with_its_timestamps(tmp_path):
    path = make_store(tmp_path, "per_sym_store_trc7d.db", [])
    W.process(path, tmp_path / "bk", dry=False)
    con = sqlite3.connect(str(path))
    key, value, updated_at, epoch = con.execute("select key, value_json, updated_at, epoch from kv_json where key='store_identity'").fetchone()
    con.close()
    doc = json.loads(value)
    assert doc["window_days"] == 7 and doc["kind"] == "7d_trc" and doc["account"] == "trc" and doc["book"] == "trb"
    assert updated_at and epoch


def test_it_is_idempotent(tmp_path):
    path = make_store(tmp_path, "per_sym_store_trc7d.db", [("ACN_LONG", {"window_days": 30})])
    W.process(path, tmp_path / "bk", dry=False)
    first = rows(path)
    second = W.process(path, tmp_path / "bk", dry=False)
    assert rows(path) == first and second["relabelled"].get("per_sym_active", 0) == 0


def test_a_backup_is_taken_before_any_change(tmp_path):
    path = make_store(tmp_path, "per_sym_store_trc7d.db", [("ACN_LONG", {"window_days": 30})])
    report = W.process(path, tmp_path / "bk", dry=False)
    bk = Path(report["backup"])
    assert bk.is_file()
    con = sqlite3.connect(str(bk))
    assert json.loads(con.execute("select meta_json from per_sym_active").fetchone()[0])["window_days"] == 30, \
        "the backup must hold the PRE-change state"
    con.close()


def test_verify_proves_the_guard_and_changes_nothing(tmp_path):
    path = make_store(tmp_path, "per_sym_store_trc7d.db", [("ACN_LONG", {"window_days": 30})])
    W.process(path, tmp_path / "bk", dry=False)
    before = rows(path)
    out = W.verify(path)
    assert out["guard"].startswith("OK") and rows(path) == before, "the probe row must be rolled back"
    assert out["declared_windows"] == {"7": 1}


def test_an_unknown_store_is_never_guessed(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    p = d / "per_sym_store_mystery.db"
    sqlite3.connect(str(p)).close()
    assert "unknown store name" in W.process(p, tmp_path / "bk", dry=False)["skipped"]


def test_a_dry_run_writes_nothing(tmp_path):
    path = make_store(tmp_path, "per_sym_store_trc7d.db", [("ACN_LONG", {"window_days": 30})])
    before = rows(path)
    W.process(path, tmp_path / "bk", dry=True)
    assert rows(path) == before


def test_a_30d_store_gets_identity_and_guards_but_no_row_is_rewritten(tmp_path):
    """Rewriting meta_json on hundreds of live rows on five hosts would make the copies diverge."""
    path = make_store(tmp_path, "per_sym_store.db", [("A_LONG", {}), ("B_SHORT", {"source": "x"})])
    before = rows(path)
    report = W.process(path, tmp_path / "bk", dry=False)
    assert rows(path) == before, "a 30D store's rows must be left byte-for-byte as they were"
    assert report["relabel_rows"] is False and report["backup"].endswith(".revert.json")
    con = sqlite3.connect(str(path))
    try:
        con.execute("INSERT INTO per_sym_active (sym_side, meta_json) VALUES ('C', ?)", (json.dumps({"window_days": 7}),))
        raise AssertionError("the 30D guard must still refuse a 7D row")
    except sqlite3.DatabaseError as exc:
        assert "WRONG_WINDOW" in str(exc)
    con.close()


def test_the_light_backup_holds_an_exact_revert(tmp_path):
    path = make_store(tmp_path, "per_sym_store.db", [("A_LONG", {})])
    report = W.process(path, tmp_path / "bk", dry=False)
    rec = json.loads(Path(report["backup"]).read_text())
    con = sqlite3.connect(str(path))
    for sql in rec["revert_sql"]:
        con.execute(sql)
    con.commit()
    left = con.execute("select count(*) from sqlite_master where type='trigger' and name like 'guard_window_%'").fetchone()[0]
    kv = con.execute("select count(*) from kv_json where key='store_identity'").fetchone()[0]
    con.close()
    assert left == 0 and kv == 0, "running the recorded revert must return the store exactly to before"
