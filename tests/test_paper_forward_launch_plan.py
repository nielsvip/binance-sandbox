import json
import sqlite3
from tools import paper_forward_launch_plan as launch


def test_build_covers_all_active_settings_not_best_first_subset(tmp_path, monkeypatch):
    folder = tmp_path / "data/hourly_reconfig"
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "per_sym_store.db") as db:
        db.execute("CREATE TABLE per_sym_active(sym_side,cat_side,updated_at,full_config_json)")
        db.executemany("INSERT INTO per_sym_active VALUES(?,?,?,?)", [("A_LONG", "CRYPTO_LONG", "t", '{"X":true}'), ("B_SHORT", "STOCKS_SHORT", "t", '{"X":false}')])
    (tmp_path / "data/v15_best_first.json").write_text(json.dumps({"allowed": ["A_LONG", "C_LONG"]}))
    monkeypatch.setattr(launch, "inventory", lambda root: {"file_sha256": {}, "scenarios": []})
    plan = launch.build(tmp_path)
    assert {row["sym_side"] for row in plan["groups"]} == {"A_LONG", "B_SHORT"}
    assert plan["uncovered"] == [{"sym_side": "C_LONG", "reason": "NO_ACTIVE_SETTINGS"}]
    assert all(row["settings_sha256"] == launch.digest(row["effective_settings"]) for row in plan["groups"])
