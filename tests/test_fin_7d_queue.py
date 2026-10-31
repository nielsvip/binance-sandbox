"""fin's 7D baseline is exactly what men trades (USER 2026-10-31)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fin_q", ROOT / "tools" / "fin_7d_queue.py")
Q = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = Q
spec.loader.exec_module(Q)


def fixture(tmp_path, rows, keys):
    db = tmp_path / "per_sym_store.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT, updated_at TEXT, overrides_json TEXT, defaults_snapshot_json TEXT, full_config_json TEXT)")
    for ss, ov, d, f in rows:
        con.execute("INSERT INTO per_sym_active VALUES (?,?,?,?,?)", (ss, "2026-10-30T00:00:00Z", json.dumps(ov), json.dumps(d), json.dumps(f)))
    con.commit()
    con.close()
    tk = tmp_path / "tradeable_keys.json"
    tk.write_text(json.dumps(keys))
    return tk, db


def test_a_row_whose_full_config_dropped_a_retired_key_is_admitted(tmp_path):
    """MIN_HOLD_BARS_BEFORE_EXIT lives in defaults/overrides but the publisher strips it from full_config."""
    d = {"A": 1, "MIN_HOLD_BARS_BEFORE_EXIT": 4}
    ov = {"A": 2, "MIN_HOLD_BARS_BEFORE_EXIT": 6}
    full = {"A": 2}
    tk, db = fixture(tmp_path, [("XUSDT_LONG", ov, d, full)], ["men:XUSDT_LONG"])
    doc = Q.build(tk, db)
    assert doc["accounts"]["fin"] == ["XUSDT_LONG"]
    assert doc["_meta"]["retired_keys_left_out"] == {"MIN_HOLD_BARS_BEFORE_EXIT": 1}


def test_the_start_reproduces_exactly_what_men_trades(tmp_path):
    d = {"A": 1, "B": 1, "C": 1}
    full = {"A": 1, "B": 5, "C": 1, "D": 9}
    tk, db = fixture(tmp_path, [("XUSDT_LONG", {"B": 4}, d, full)], ["men:XUSDT_LONG"])
    start = Q.build(tk, db)["sym_sides"]["XUSDT_LONG"]["overrides"]
    assert {**d, **start} == {**d, **full}, "defaults + start must equal men's traded config"
    assert start == {"B": 5, "D": 9}, "the traded value wins over the row's stale overrides"


def test_only_the_control_accounts_tradeable_keys_are_queued(tmp_path):
    rows = [(ss, {}, {"A": 1}, {"A": 2}) for ss in ("MENUSDT_LONG", "ANGUSDT_LONG", "FINUSDT_LONG")]
    tk, db = fixture(tmp_path, rows, ["men:MENUSDT_LONG", "ang:ANGUSDT_LONG", "fin:FINUSDT_LONG"])
    assert Q.build(tk, db)["accounts"]["fin"] == ["MENUSDT_LONG"]


def test_a_side_with_no_30d_row_is_reported_never_invented(tmp_path):
    tk, db = fixture(tmp_path, [], ["men:PENDLEUSDT_LONG"])
    doc = Q.build(tk, db)
    assert doc["accounts"]["fin"] == [] and doc["_meta"]["missing_baseline"][0]["sym_side"] == "PENDLEUSDT_LONG"


def test_the_queue_is_in_the_launchers_format(tmp_path):
    tk, db = fixture(tmp_path, [("XUSDT_LONG", {}, {"A": 1}, {"A": 2})], ["men:XUSDT_LONG"])
    e = Q.build(tk, db)["sym_sides"]["XUSDT_LONG"]
    for k in ("cat_side", "overrides", "source_kind", "sql_baseline_ok", "source_db_sha256", "source_updated_at", "source_row_sha256"):
        assert k in e
    assert e["cat_side"] == "CRYPTO_LONG" and e["source_kind"] == "per_sym_sql"
