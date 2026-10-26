import datetime as dt
import json
import sqlite3
import shutil
from zoneinfo import ZoneInfo

import pytest

from tools.v15_preopen_7d_stage import digest, file_md5, stage
from tools.v15_preopen_7d_runtime import assert_ready


def _fixture(tmp_path):
    ss = "AAAUSDT_SHORT"
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    source = {"X": 1}
    queue = {"_meta": {"window_days": 7, "n": 1}, "accounts": {"fin": [ss], "trc": []},
             "sym_sides": {ss: {"manifest_ok": True, "overrides": source}}}
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(queue))
    result_dir = tmp_path / "results"
    progress_dir = result_dir / ss / "progress"
    out_dir = result_dir / ss / "out"
    progress_dir.mkdir(parents=True)
    out_dir.mkdir()
    overrides = {"X": 2}
    defaults = {"X": 1, "Y": True}
    full = {**defaults, **overrides}
    (progress_dir / f"{ss}_7d_progress.json").write_text(json.dumps({
        "window_days": 7, "source_30d_sha256": digest(source), "cumulative_overrides": overrides,
        "result_done_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "final_path": str(out_dir / f"{ss}_7d_matrix.xlsx"),
        "defaults_snapshot_7d": defaults, "full_config_7d": full,
        "live_verified": {"gain_pct": 2.0, "trades": 12, "parity": [True, ""], "reason": None},
    }))
    workbook = out_dir / f"{ss}_7d_matrix.xlsx"
    workbook.write_bytes(b"sample workbook")
    manifest = {"symside": ss, "window_days": 7, "source_30d_sha256": digest(source),
                "full_config_sha256": digest(full), "overrides_md5": digest(overrides, "md5"),
                "final_name": workbook.name, "file_md5": file_md5(workbook),
                "metrics": {"valid": True, "trades": 12, "gain_pct": 2.0},
                "npz_md5": "npz", "pilot_md5": "pilot", "npz_age_min": 3,
                "published_utc": f"{today}T08:00:00+00:00"}
    (out_dir / f"{ss}_7d_manifest.json").write_text(json.dumps(manifest))
    db = tmp_path / "source.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE per_sym_active (sym_side TEXT PRIMARY KEY, updated_at TEXT,
        winning_tag TEXT, acc_gain_pct REAL, trades INTEGER, pool_sharpe REAL, max_dd_pct REAL,
        overrides_json TEXT, defaults_snapshot_json TEXT, full_config_json TEXT, meta_json TEXT)""")
    con.execute("INSERT INTO per_sym_active (sym_side, full_config_json, meta_json) VALUES (?,?,?)",
                (ss, json.dumps(defaults), "{}"))
    con.execute("INSERT INTO per_sym_active (sym_side, full_config_json, meta_json) VALUES (?,?,?)",
                ("BBBUSDT_LONG", json.dumps({"X": 9}), "{}"))
    con.commit()
    con.close()
    source_sets = tmp_path / "sets.json"
    source_sets.write_text(json.dumps({ss: {"mode": "live", "account": ["fin"], "overrides": source,
                                          "set_tier": "30D_BEST"},
                                      "BBBUSDT_LONG": {"mode": "live", "account": ["fin"],
                                                       "overrides": {"X": 9}, "set_tier": "30D_BEST"}}))
    published = tmp_path / "published.json"
    published.write_text(json.dumps({"sym_sides": {
        ss: {"manifest_ok": True, "overrides": source},
        "BBBUSDT_LONG": {"manifest_ok": True, "overrides": {"X": 9}},
    }}))
    stocks = tmp_path / "stocks.json"
    stocks.write_text("[]")
    return today, queue_path, result_dir, db, source_sets, stocks, published


def test_stage_creates_isolated_db_and_7d_vector_set(tmp_path):
    args = _fixture(tmp_path)
    today, queue, results, db, sets, stocks, published = args
    report = stage(queue, results, db, sets, stocks, tmp_path / "stage", today, published)
    assert report["stage_only"] is True
    assert report["selected_7d"] == 1
    assert report["fallback_30d"] == 0
    con = sqlite3.connect(tmp_path / "stage/per_sym_store_7d.db")
    row = con.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side='AAAUSDT_SHORT'").fetchone()
    con.close()
    assert json.loads(row[0]) == {"X": 2, "Y": True}
    original = sqlite3.connect(db)
    assert json.loads(original.execute("SELECT full_config_json FROM per_sym_active").fetchone()[0]) == {"X": 1, "Y": True}
    original.close()
    produced = json.loads((tmp_path / "stage/vec_driven_sets.json").read_text())
    assert produced["AAAUSDT_SHORT"]["source"] == "7d_optimized"
    assert "BBBUSDT_LONG" not in produced


def test_stage_refuses_missing_or_unverified_result(tmp_path):
    today, queue, results, db, sets, stocks, published = _fixture(tmp_path)
    manifest = next((results / "AAAUSDT_SHORT/out").glob("*_manifest.json"))
    data = json.loads(manifest.read_text())
    data["npz_age_min"] = 45
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="stale"):
        stage(queue, results, db, sets, stocks, tmp_path / "stage", today, published)
    assert not (tmp_path / "stage/per_sym_store_7d.db").exists()


def test_stage_requires_fresh_premarket_replay_when_requested(tmp_path):
    today, queue, results, db, sets, stocks, published = _fixture(tmp_path)
    doc = json.loads(queue.read_text())
    doc["_meta"]["require_replay"] = True
    queue.write_text(json.dumps(doc))
    with pytest.raises(FileNotFoundError):
        stage(queue, results, db, sets, stocks, tmp_path / "stage", today, published)
    full = {"X": 2, "Y": True}
    cutoff = dt.datetime.fromisoformat(today).replace(hour=9, minute=15, tzinfo=ZoneInfo("America/New_York"))
    replay = {"sym_side": "AAAUSDT_SHORT", "valid": True,
              "optimized_full_sha256": digest(full), "source_30d_sha256": digest({"X": 1}),
              "bar_cutoff_et": cutoff.isoformat(),
              "npz_last_bar_utc": cutoff.astimezone(dt.timezone.utc).isoformat()}
    (results / "AAAUSDT_SHORT/replay.json").write_text(json.dumps(replay))
    report = stage(queue, results, db, sets, stocks, tmp_path / "stage", today, published)
    assert report["require_replay"] is True
    assert report["replay_count"] == 1


def test_stage_does_not_mark_mismatched_30d_fallback_verified(tmp_path):
    today, queue, results, db, sets, stocks, published = _fixture(tmp_path)
    selection = json.loads(published.read_text())
    selection["sym_sides"]["BBBUSDT_LONG"]["overrides"] = {"X": 8}
    published.write_text(json.dumps(selection))
    report = stage(queue, results, db, sets, stocks, tmp_path / "stage", today, published)
    assert report["fallback_30d"] == 0
    staged = json.loads((tmp_path / "stage/vec_driven_sets.json").read_text())
    assert "BBBUSDT_LONG" not in staged


def test_runtime_requires_matching_db_and_vector_sets(tmp_path, monkeypatch):
    today, queue, results, db, sets, stocks, published = _fixture(tmp_path)
    stage_dir = tmp_path / "stage"
    stage(queue, results, db, sets, stocks, stage_dir, today, published)
    lane = tmp_path / "data/vec_live_7d"
    lane.mkdir(parents=True)
    for name in ("vec_driven_sets.json", "vec_driven.json", "validation.json"):
        shutil.copy2(stage_dir / name, lane / name)
    monkeypatch.setenv("VEC_LIVE_SCOPE", "7d")
    monkeypatch.setenv("PER_SYM_STORE_DB", str(stage_dir / "per_sym_store_7d.db"))
    assert_ready("fin", tmp_path)
    with pytest.raises(RuntimeError, match="FIN/TRC"):
        assert_ready("men", tmp_path)
    (lane / "vec_driven_sets.json").write_text("{}")
    with pytest.raises(RuntimeError, match="differ"):
        assert_ready("fin", tmp_path)
