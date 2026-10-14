import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stale_set_halt as ssh  # noqa: E402

DAY = 86400.0


def _iso(e):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))


def _tagday(e):
    return time.strftime("%Y%m%d", time.gmtime(e))


def _env(tmp_path, monkeypatch):
    db = tmp_path / "per_sym_store.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT PRIMARY KEY, cat_side TEXT, updated_at TEXT, winning_tag TEXT, campaign_ts REAL, overrides_json TEXT, meta_json TEXT)")
    con.execute("CREATE TABLE per_sym_history (id INTEGER PRIMARY KEY AUTOINCREMENT, sym_side TEXT, updated_at TEXT, epoch REAL, winning_tag TEXT)")
    con.commit()
    con.close()
    monkeypatch.setenv("PER_SYM_STORE_DB", str(db))
    monkeypatch.setenv("STALE_SET_HALT_NO_PIN", "1")
    ssh._CACHE.clear()
    return SimpleNamespace(BASE_PATH=str(tmp_path), STALE_SET_HALT_ENABLED=True, STALE_SET_HALT_DAYS=10.0, STALE_SET_HALT_LOSSES=2), db


def _set_row(db, ss, apply_epoch, tag, overrides):
    con = sqlite3.connect(db)
    con.execute("INSERT OR REPLACE INTO per_sym_active VALUES (?,?,?,?,?,?,?)", (ss, "CRYPTO_LONG", _iso(apply_epoch), tag, apply_epoch, json.dumps(overrides), json.dumps({"winning_tag": tag})))
    con.commit()
    con.close()
    ssh._CACHE.clear()


def _close(cfg, ss_sym, side, gain, ts, venue="ez", acct="ang"):
    ssh.record_close(venue, acct, ss_sym, side, gain, "TEST_EXIT", cfg=cfg, ts=ts)
    ssh._CACHE.clear()


def test_old_set_two_losses_blocks_opens_not_closes(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _set_row(db, "ABCUSDT_LONG", now - 20 * DAY, f"v15_30D_{_tagday(now - 20 * DAY)}", {"X": 1})
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 3 * DAY)
    _close(cfg, "ABCUSDT", "LONG", -0.2, now - 2 * DAY)
    for act in ("OPEN", "AUGMENT", "REENTRY_OPEN", "BUY"):
        blk, why = ssh.entry_gate("ez", "ABCUSDT", "LONG", act, cfg=cfg)
        assert blk and "STALE_SET_HALT" in why, act
    for act in ("CLOSE", "REDUCE", "HEDGE_CLOSE", "PARTIAL_CLOSE"):
        assert ssh.entry_gate("ez", "ABCUSDT", "LONG", act, cfg=cfg) == (False, ""), act
    assert ssh.entry_gate("ez", "ABCUSDT", "SHORT", "OPEN", cfg=cfg) == (False, "")  # other side untouched
    halts = json.loads((tmp_path / "data" / "stale_set_halt" / "halts_ez.json").read_text())["halts"]
    assert "ABCUSDT_LONG" in halts and halts["ABCUSDT_LONG"]["halted_at"]
    alerts = (tmp_path / "data" / "alerts" / "stale_set_halt_ez.jsonl").read_text().splitlines()
    assert json.loads(alerts[0])["event"] == "HALT"


def test_halt_fires_on_the_second_losing_close_and_survives_restart(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _set_row(db, "ABCUSDT_SHORT", now - 15 * DAY, f"v15_30D_{_tagday(now - 15 * DAY)}", {"X": 1})
    assert ssh.record_close("ez", "fin", "ABCUSDT", "SHORT", -1.0, "R", cfg=cfg, ts=now - 60) is None
    ssh._CACHE.clear()
    rec = ssh.record_close("ez", "men", "ABCUSDT", "SHORT", -0.3, "R", cfg=cfg, ts=now - 30)
    assert rec and rec["sym_side"] == "ABCUSDT_SHORT"
    ssh._CACHE.clear()  # "restart": no in-memory state
    assert ssh.entry_gate("ez", "ABCUSDT", "SHORT", "OPEN", cfg=cfg)[0]


def test_fresh_set_two_losses_not_halted(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _set_row(db, "ABCUSDT_LONG", now - 2 * DAY, f"v15_30D_{_tagday(now - 2 * DAY)}", {"X": 1})
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - DAY)
    _close(cfg, "ABCUSDT", "LONG", -0.4, now - 0.5 * DAY)
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg) == (False, "")
    assert not (tmp_path / "data" / "stale_set_halt" / "halts_ez.json").exists()


def test_old_set_loss_then_win_not_halted_and_fee_eaten_win_is_loss(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _set_row(db, "ABCUSDT_LONG", now - 30 * DAY, f"v15_30D_{_tagday(now - 30 * DAY)}", {"X": 1})
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 3 * DAY)
    _close(cfg, "ABCUSDT", "LONG", 0.6, now - 2 * DAY)
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg) == (False, "")
    _close(cfg, "ABCUSDT", "LONG", 0.02, now - DAY)  # +0.02 gross - 0.04 fee = net loss
    _close(cfg, "ABCUSDT", "LONG", -0.1, now - 0.5 * DAY)
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg)[0]


def test_losses_before_the_active_apply_do_not_count(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 25 * DAY)
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 24 * DAY)
    _set_row(db, "ABCUSDT_LONG", now - 20 * DAY, f"v15_30D_{_tagday(now - 20 * DAY)}", {"X": 1})
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg) == (False, "")


def test_release_after_new_published_and_applied_board_resets_counter(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _set_row(db, "ABCUSDT_LONG", now - 20 * DAY, f"v15_30D_{_tagday(now - 20 * DAY)}", {"X": 1})
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 3 * DAY)
    _close(cfg, "ABCUSDT", "LONG", -0.2, now - 2 * DAY)
    assert ssh.check("ez", "ABCUSDT", "LONG", cfg=cfg, now=now - DAY, pin=False)
    # re-stamp of the SAME set after the halt (e.g. artefact strip) must NOT release
    _set_row(db, "ABCUSDT_LONG", now - 0.5 * DAY, f"v15_30D_{_tagday(now - 20 * DAY)}", {"X": 1})
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg)[0]
    # new board published AFTER the halt, applied after it, different set -> released
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "published_selection.json").write_text(json.dumps({"sym_sides": {"ABCUSDT_LONG": {"published_at": _iso(now - 0.4 * DAY), "overrides_md5": "x"}}}))
    _set_row(db, "ABCUSDT_LONG", now - 0.3 * DAY, f"v15_30D_{_tagday(now)}", {"X": 2})
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg) == (False, "")
    assert ssh.load_halts("ez", cfg) == {}
    _close(cfg, "ABCUSDT", "LONG", -0.9, now - 0.1 * DAY)
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg) == (False, "")
    events = [json.loads(x)["event"] for x in (tmp_path / "data" / "alerts" / "stale_set_halt_ez.jsonl").read_text().splitlines()]
    assert events == ["HALT", "RELEASE"]


def test_board_published_before_the_halt_does_not_release(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _set_row(db, "ABCUSDT_LONG", now - 20 * DAY, f"v15_30D_{_tagday(now - 20 * DAY)}", {"X": 1})
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 3 * DAY)
    _close(cfg, "ABCUSDT", "LONG", -0.2, now - 2 * DAY)
    assert ssh.check("ez", "ABCUSDT", "LONG", cfg=cfg, now=now - DAY, pin=False)
    (tmp_path / "data" / "published_selection.json").write_text(json.dumps({"sym_sides": {"ABCUSDT_LONG": {"published_at": _iso(now - 5 * DAY)}}}))
    _set_row(db, "ABCUSDT_LONG", now - 0.3 * DAY, f"v15_30D_{_tagday(now)}", {"X": 2})
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg)[0]


def test_no_set_is_cat_side_defaults_fresh(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    now = time.time()
    _close(cfg, "ZZZ", "LONG", -1.0, now - 3 * DAY, venue="tradier", acct="trb")
    _close(cfg, "ZZZ", "LONG", -1.0, now - 2 * DAY, venue="tradier", acct="trb")
    assert ssh.entry_gate("tradier", "ZZZ", "LONG", "OPEN", cfg=cfg, is_entry=True) == (False, "")


def test_kill_switch_off(tmp_path, monkeypatch):
    cfg, db = _env(tmp_path, monkeypatch)
    cfg.STALE_SET_HALT_ENABLED = False
    now = time.time()
    _set_row(db, "ABCUSDT_LONG", now - 20 * DAY, "v15_30D_20200101", {"X": 1})
    _close(cfg, "ABCUSDT", "LONG", -0.5, now - 3 * DAY)
    _close(cfg, "ABCUSDT", "LONG", -0.2, now - 2 * DAY)
    assert ssh.entry_gate("ez", "ABCUSDT", "LONG", "OPEN", cfg=cfg) == (False, "")


def test_recalc_anchor_sources():
    now = time.time()
    a = ssh.recalc_anchor({"campaign_ts": now - DAY, "winning_tag": "backfill", "meta_json": "{}"}, None, [{"epoch": now - 30 * DAY, "winning_tag": "v15_30D_20200105"}, {"epoch": now - 2 * DAY, "winning_tag": "backfill"}])
    assert a["source"] == "history_board_tag"
    b = ssh.recalc_anchor({"campaign_ts": now - DAY, "winning_tag": "clean_ONLY_CROSSES_v33", "meta_json": json.dumps({"updated_at": _iso(now - 12 * DAY)})}, None, [])
    assert b["source"] == "earliest_write_stamp" and abs(b["recalc_epoch"] - (now - 12 * DAY)) < 2
    c = ssh.recalc_anchor({"campaign_ts": now, "winning_tag": "continuous_1787170684"}, None, None)
    assert c["recalc_epoch"] == 1787170684.0
    assert ssh.recalc_anchor(None)["source"] == "cat_side_defaults"


def test_ledger_row_filters():
    full = {"ts": "2026-10-13T00:00:00Z", "account": "ang", "symbol": "X", "side": "LONG", "pnl_pct": 1.4, "fees_pct": 0.04, "reason": "EXIT_VELOCITY_WT against-long g-0.34% |VEC_EXACT"}
    r = ssh.ledger_row_to_close(full, "ez")
    assert r["src"] == "ledger_reason_g" and r["gain_pct"] == -0.34 and r["net_pct"] == -0.38
    assert ssh.ledger_row_to_close({**full, "reason": "CYCLE_TP_TIERED_T1_1pct"}, "ez") is None
    assert ssh.ledger_row_to_close({**full, "reason": "DC_BREACH_REDUCE_x"}, "ez") is None
    assert ssh.ledger_row_to_close({**full, "reason": "REDUCE_TO_FLAT |VEC_EXACT"}, "ez")["src"] == "ledger_pnl"
    st = ssh.ledger_row_to_close({**full, "account": "trb", "fees_pct": 0.0, "pnl_pct": -0.31, "reason": "UNCOND_DC_LOW_15M_g0.08_qty_999999"}, "tradier")
    assert st["gain_pct"] == -0.31 and st["net_pct"] == -0.31


def test_pin_script_inserts_top_and_removes(tmp_path):
    p = tmp_path / "pins.json"
    p.write_text(json.dumps({"sides": ["A_LONG", "B_SHORT"], "hosts": [], "_doc": "keep"}))
    out = subprocess.run([sys.executable, "-c", ssh._PIN_SCRIPT, str(p), "B_SHORT", "add"], capture_output=True, text=True, check=True).stdout
    d = json.loads(p.read_text())
    assert d["sides"] == ["B_SHORT", "A_LONG"] and d["_doc"] == "keep" and json.loads(out)["ok"]
    subprocess.run([sys.executable, "-c", ssh._PIN_SCRIPT, str(p), "B_SHORT", "remove"], check=True, capture_output=True)
    assert json.loads(p.read_text())["sides"] == ["A_LONG"]


def test_is_entry_action():
    assert ssh.is_entry_action("OPEN") and ssh.is_entry_action("AUGMENT") and ssh.is_entry_action("REENTRY") and ssh.is_entry_action("BUY")
    assert not ssh.is_entry_action("CLOSE") and not ssh.is_entry_action("REDUCE") and not ssh.is_entry_action("HEDGE_CLOSE") and not ssh.is_entry_action(None)
