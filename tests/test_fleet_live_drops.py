import json
import sqlite3

from tools import fleet_probe


def test_live_drops_require_matching_active_set_and_current_bar(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_probe, "SB", str(tmp_path))
    data = tmp_path / "data"
    (data / "hourly_reconfig").mkdir(parents=True)
    state_dir = data / "forward_state"
    state_dir.mkdir()
    db = sqlite3.connect(data / "hourly_reconfig" / "per_sym_store.db")
    db.execute("CREATE TABLE per_sym_active(sym_side TEXT, updated_at TEXT, gain_30d REAL, acc_gain_pct REAL)")
    for ss in ("GOODUSDT_LONG", "OLDUSDT_LONG", "WRONGUSDT_LONG"):
        db.execute("INSERT INTO per_sym_active VALUES (?,?,?,?)", (ss, "new", 20.0, 20.0))
    db.commit(); db.close()
    now = 1_800_000_910
    bar = (now // 900 - 1) * 900
    for ss, updated, tail in (("GOODUSDT_LONG", "new", bar), ("OLDUSDT_LONG", "new", bar - 900), ("WRONGUSDT_LONG", "old", bar)):
        (state_dir / f"{ss}.json").write_text(json.dumps({"sym_side": ss, "ok": True, "window_days": 30, "src": "per_sym_store.active", "settings_updated_at": updated, "npz_tail": tail, "gain_pct": 4.0, "settings_sha256": "verified"}))
    rows = fleet_probe._live_drops("crypto", now, {})
    assert [(r["symside"], r["drop_pp"]) for r in rows] == [("GOODUSDT_LONG", 16.0)]


def test_stock_drop_requires_state_at_least_as_new_as_npz(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_probe, "SB", str(tmp_path))
    data = tmp_path / "data"
    (data / "hourly_reconfig").mkdir(parents=True)
    (data / "forward_state").mkdir()
    db = sqlite3.connect(data / "hourly_reconfig" / "per_sym_store.db")
    db.execute("CREATE TABLE per_sym_active(sym_side TEXT, updated_at TEXT, gain_30d REAL, acc_gain_pct REAL)")
    db.execute("INSERT INTO per_sym_active VALUES (?,?,?,?)", ("AAPL_LONG", "new", 30.0, 30.0))
    db.commit(); db.close()
    now = 1_800_000_910
    bar = (now // 900 - 1) * 900
    (data / "forward_state" / "AAPL_LONG.json").write_text(json.dumps({"sym_side": "AAPL_LONG", "ok": True, "window_days": 30, "src": "per_sym_store.active", "settings_updated_at": "new", "npz_tail": bar, "gain_pct": 5.0}))
    assert len(fleet_probe._live_drops("stocks", now, {"AAPL": bar})) == 1
    assert fleet_probe._live_drops("stocks", now, {"AAPL": bar + 900}) == []
