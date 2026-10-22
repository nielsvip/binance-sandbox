"""Ledger collateral for vector_position_status.py (USER 2026-10-22 failover).

Parity enforcers must resolve each sym_side's vector position status on EITHER
host (Mac offline -> S1 trades). These tests pin: local-first sources, S1 and
Mac layouts, synced-S1 verdict mirror, no network in the hot path, and honest
UNKNOWN (never invented).
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vector_position_status as vps  # noqa: E402


def _layout(tmp_path, name):
    r = tmp_path / name
    (r / "data" / "pure_board").mkdir(parents=True)
    (r / "data" / "vec_live" / "state").mkdir(parents=True)
    (r / "data" / "hourly_reconfig").mkdir(parents=True)
    (r / "config.py").write_text("x=1\n")
    return r


def _write_verdicts(r, fname, rows):
    with open(r / "data" / "pure_board" / fname, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _write_store(r, rows):
    con = sqlite3.connect(str(r / "data" / "hourly_reconfig" / "per_sym_store.db"))
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT PRIMARY KEY, acc_gain_pct REAL, updated_at TEXT)")
    con.executemany("INSERT INTO per_sym_active VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def test_converge_verdict_wins_and_stale_ignored(tmp_path):
    r = _layout(tmp_path, "mac")
    now = time.time()
    _write_verdicts(r, "intents_2026-10-22.jsonl", [
        {"ts": now - 100, "bar_ts": now - 200, "acct": "trb", "sym_side": "AAPL_LONG", "action": "CONVERGE_OPEN"},
        {"ts": now - 9000, "bar_ts": now - 9100, "acct": "trb", "sym_side": "OLD_LONG", "action": "CONVERGE_OPEN"},
    ])
    s = vps.status("AAPL_LONG", "trb", r, now)
    assert s["state"] == "OPEN" and s["source"] == "pure_board_verdict"
    s2 = vps.status("OLD_LONG", "trb", r, now)
    assert s2["state"] in ("AUTHORIZED", "UNKNOWN")


def test_s1_mirror_verdicts_consumed(tmp_path):
    r = _layout(tmp_path, "sandbox")
    now = time.time()
    _write_verdicts(r, "intents_s1_2026-10-22.jsonl", [
        {"ts": now - 200, "bar_ts": now - 300, "acct": "trb", "sym_side": "MU_SHORT", "action": "CONVERGE_CLOSE"},
    ])
    s = vps.status("MU_SHORT", "trb", r, now)
    assert s["state"] == "FLAT" and s["source"] == "pure_board_verdict"


def test_forward_hold_then_board_then_unknown(tmp_path):
    r = _layout(tmp_path, "mac")
    (r / "data" / "vec_live" / "state" / "X_LONG.json").write_text(json.dumps({"pos": 1, "reason": "h"}))
    _write_store(r, [("Y_LONG", 12.5, "2026-10-22T10:00:00+00:00")])
    assert vps.status("X_LONG", "", r)["state"] == "OPEN"
    b = vps.status("Y_LONG", "", r)
    assert b["state"] == "AUTHORIZED" and b["source"] == "per_sym_store"
    u = vps.status("ZZZ_LONG", "", r)
    assert u["state"] == "UNKNOWN" and u["source"] == "none"


def test_host_kind_layouts(tmp_path):
    assert vps.host_kind(tmp_path / "x" / "binance-sandbox") == "s1-sandbox"
    assert vps.host_kind(Path("/Users/niels/Documents/binance")) == "mac"


def test_no_network_in_hot_path(tmp_path, monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("network used in resolver hot path")

    monkeypatch.setattr(subprocess, "run", _boom)
    r = _layout(tmp_path, "mac")
    assert vps.status("NOPE_LONG", "trb", r)["state"] == "UNKNOWN"
    assert vps.converge_verdicts(r, "trb") == {}
    assert vps.forward_holds(r) == {}
    assert vps.board_set(r, "NOPE_LONG") == {}


def test_never_raises_on_garbage(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    assert vps.status("A_LONG", "trb", r)["state"] == "UNKNOWN"
    assert vps.host_kind(None) in ("mac", "s1-live", "s1-sandbox", "unknown")
