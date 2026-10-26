import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from v15_preopen_7d_select import select


def _db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT PRIMARY KEY, updated_at TEXT, overrides_json TEXT, defaults_snapshot_json TEXT, full_config_json TEXT)")
    for ss, overrides in rows.items():
        defaults = {"X": 0, "Y": True}
        con.execute("INSERT INTO per_sym_active VALUES (?,?,?,?,?)", (ss, "2026-10-26T01:00:00Z", json.dumps(overrides), json.dumps(defaults), json.dumps({**defaults, **overrides})))
    con.commit()
    con.close()


def test_every_tradeable_side_with_sql_baseline_enters_without_history(tmp_path):
    now = dt.datetime(2026, 10, 26, 2, tzinfo=dt.timezone.utc).timestamp()
    (tmp_path / "symbols_men_long.json").write_text("[]")
    (tmp_path / "symbols_men_short.json").write_text(json.dumps(["AAAUSDT", "CCCUSDT", "DDDUSDT"]))
    (tmp_path / "symbols_fin_long.json").write_text("[]")
    (tmp_path / "symbols_fin_short.json").write_text("[]")
    (tmp_path / "symbols_trb_long.json").write_text("[]")
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(["fin:AAAUSDT_SHORT", "fin:CCCUSDT_SHORT", "fin:DDDUSDT_SHORT"]))
    db = tmp_path / "source.db"
    _db(db, {"AAAUSDT_SHORT": {"X": 1}, "CCCUSDT_SHORT": {"X": 2}})
    got = select(tmp_path, now, {"sym_sides": {}}, source_db=db)
    assert got["accounts"]["fin"] == ["AAAUSDT_SHORT", "CCCUSDT_SHORT"]
    assert got["_meta"]["missing_sql_baseline"] == ["DDDUSDT_SHORT"]
    assert got["sym_sides"]["AAAUSDT_SHORT"]["source_kind"] == "per_sym_sql"
    assert got["sym_sides"]["AAAUSDT_SHORT"]["overrides"] == {"X": 1}


def test_empty_trb_side_does_not_invent_trc_tradeability(tmp_path):
    (tmp_path / "symbols_men_long.json").write_text("[]")
    (tmp_path / "symbols_men_short.json").write_text("[]")
    (tmp_path / "symbols_trb_long.json").write_text("[]")
    (tmp_path / "symbols_trb_short.json").write_text("[]")
    (tmp_path / "tradeable_keys.json").write_text("[]")
    db = tmp_path / "source.db"
    _db(db, {"XYZ_LONG": {"X": 1}})
    got = select(tmp_path, dt.datetime(2026, 10, 26, 2, tzinfo=dt.timezone.utc).timestamp(), {}, source_db=db)
    assert got["accounts"]["trc"] == []
