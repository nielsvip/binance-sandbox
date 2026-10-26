import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.v15_preopen_7d_fleet import _defaults_proof, _market_window, allocate, tick


HOSTS = [
    {"name": "s1", "venues": ["crypto"], "sides": ["SHORT"]},
    {"name": "s2", "venues": ["crypto"], "sides": ["LONG"]},
    {"name": "s5", "venues": ["stocks"], "sides": ["LONG"]},
    {"name": "s6", "venues": ["stocks"], "sides": ["SHORT"]},
]


def test_fleet_places_each_category_and_reassigns_deleted_host():
    queue = {"sym_sides": {
        "AAAUSDT_SHORT": {"cat_side": "CRYPTO_SHORT"},
        "BBBUSDT_LONG": {"cat_side": "CRYPTO_LONG"},
        "AAPL_LONG": {"cat_side": "STOCKS_LONG"},
        "NVDA_SHORT": {"cat_side": "STOCKS_SHORT"},
    }}
    first = allocate(queue, HOSTS, {"s1", "s2", "s5", "s6"})
    assert {ss: row["host"] for ss, row in first.items()} == {
        "AAAUSDT_SHORT": "s1", "BBBUSDT_LONG": "s2",
        "AAPL_LONG": "s5", "NVDA_SHORT": "s6",
    }
    second = allocate(queue, HOSTS, {"s1", "s2", "s5"}, first)
    assert second["NVDA_SHORT"]["host"] == "s5"
    assert second["AAPL_LONG"]["host"] == "s5"


def test_requires_completed_four_category_default_chain(tmp_path):
    now = dt.datetime(2026, 10, 26, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    assert _market_window(now)
    assert not _market_window(now.replace(hour=9, minute=30))
    day = now.strftime("%Y%m%d")
    stamp = tmp_path / "data/daily_chain" / f"{day}.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text(json.dumps({"date": day, "status": "DONE", "steps_ok": "1,2,3,4"}))
    assert _defaults_proof(tmp_path, now) is None
    for cat in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        template = tmp_path / "SPREADSHEETS/TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cat}.xlsx"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_bytes(b"template")
    stamp.write_text(json.dumps({"date": day, "status": "DONE", "steps_ok": "1,2,3,4,6"}))
    proof = _defaults_proof(tmp_path, now)
    assert proof and set(proof["template_sha256"]) == {
        "CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"}


def test_scheduler_dry_run_waits_for_defaults_then_records_host_plan(tmp_path, monkeypatch):
    now = dt.datetime(2026, 10, 26, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    stats = {h["name"]: {"running": {}} for h in HOSTS}
    waiting = tick(tmp_path, now, HOSTS, stats, dry_run=True)
    assert waiting["phase"] == "waiting_for_four_cat_side_defaults"
    day = now.strftime("%Y%m%d")
    stamp = tmp_path / "data/daily_chain" / f"{day}.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text(json.dumps({"date": day, "status": "DONE", "steps_ok": "1,2,3,4,6"}))
    for cat in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        template = tmp_path / "SPREADSHEETS/TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cat}.xlsx"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_bytes(b"template")
    published = tmp_path / "data/published_selection.json"
    published.write_text("{}")
    monkeypatch.setattr("tools.v15_preopen_7d_fleet.select", lambda *_: {
        "_meta": {"n": 1, "window_days": 7},
        "accounts": {"fin": ["AAAUSDT_SHORT"], "trc": []},
        "sym_sides": {"AAAUSDT_SHORT": {"cat_side": "CRYPTO_SHORT", "overrides": {"X": 1}}},
    })
    plan = tick(tmp_path, now, HOSTS, stats, dry_run=True)
    assert plan["by_host"]["s1"] == 1
    assert plan["suppress_30d"] is False
    assert not (tmp_path / "data/preopen_7d/fleet" / day).exists()
