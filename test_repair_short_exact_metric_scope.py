from __future__ import annotations

import json
import sqlite3

from tools import repair_short_exact_metric_scope as repair


def test_repair_adds_scope_without_changing_numeric_result(tmp_path, monkeypatch):
    root = tmp_path / "fleet"
    root.mkdir()
    con = sqlite3.connect(root / "queue.db")
    con.execute(
        """CREATE TABLE results (
             id INTEGER PRIMARY KEY,symbol TEXT,side TEXT,stage TEXT,status TEXT,
             strategy_return_pct REAL,bh_return_pct REAL,tim_pct REAL,
             payload_json TEXT
           )"""
    )
    payload = {
        "schedule_audit": {
            "time_in_market": {
                "actual_binary_pct": 4.2142468733,
                "actual_weighted_pct": 0.7631200384,
            }
        }
    }
    con.execute(
        """INSERT INTO results VALUES
           (1,'LRCX','SHORT','V8_EXACT_REPLAY','EXACT_PARITY_ONLY',
            9.6534061902,-69.9969408429,4.2142468733,?)""",
        (json.dumps(payload),),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(repair.fleet, "write_report", lambda _: None)

    receipt = repair.repair(root)

    con = sqlite3.connect(root / "queue.db")
    strategy, bh, tim, raw = con.execute(
        "SELECT strategy_return_pct,bh_return_pct,tim_pct,payload_json "
        "FROM results WHERE id=1"
    ).fetchone()
    con.close()
    scoped = json.loads(raw)
    assert (strategy, bh, tim) == (
        9.6534061902, -69.9969408429, 4.2142468733
    )
    assert scoped["metric_scope"] == (
        "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD"
    )
    assert scoped["return_unit"] == "FIXED_2000_USD_CAPITAL_RETURN_PCT"
    assert scoped["tim_weighted_pct"] == 0.7631200384
    assert receipt["numeric_result_unchanged"] is True
