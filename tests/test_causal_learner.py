"""Causal learner core: ingest losing trades + negative cells, propose/confirm fixes, export condemned."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import v15_causal_learner as cl


def _progress(path, sym, cells):
    done = {}
    for i, (sw, dt) in enumerate(cells):
        done[f"ENTRY_REVERSAL_BOUNCE!{i}:{sw}=True"] = {
            "naked_delta": dt,
            "yellows": {f"H{i}": dt},
        }
    path.write_text(json.dumps({"symside": sym, "done": done}))


def test_parse_real_pilot_key_format():
    assert cl._parse_cell_key(
        "ENTRY_REVERSAL_BOUNCE!3:WT_15M_BOUNCE_OPEN_ENABLED=True"
    ) == (
        "ENTRY_REVERSAL_BOUNCE",
        "WT_15M_BOUNCE_OPEN_ENABLED",
        "True",
        "3",
    )
    assert (
        cl._parse_cell_key("ENTRY_CONFIRMATION_GATES!294:NEWBORN_LOSS_KILL_VEL_TF=")[1]
        == "NEWBORN_LOSS_KILL_VEL_TF"
    )
    assert (
        cl._parse_cell_key(
            "ENTRY_REVERSAL_BOUNCE!10:MARKET_QUALITY_SCORE_ENABLED=True"
        )[1]
        == "MARKET_QUALITY_SCORE_ENABLED"
    )
    assert cl._parse_cell_key("TAB!10:20=X") == ("", "", "", "")
    assert cl._parse_cell_key("not-a-key") == ("", "", "", "")


def test_sweep_negatives_confirm_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ROOT", tmp_path)
    pd = tmp_path / "prog"
    pd.mkdir()
    for ss in ["AAA_LONG", "BBB_LONG", "CCC_LONG"]:
        _progress(
            pd / f"{ss}_v14_progress.json", ss, [("BAD_SW", -2.0), ("BAD_SW", -1.5)]
        )
    db = str(tmp_path / "c.db")
    r = cl.ingest_progress(str(pd), db)
    assert r["lessons"] == 6 and r["causes"] == 12
    r2 = cl.ingest_progress(str(pd), db)
    assert r2 == {"lessons": 0, "causes": 0}
    out = cl.export_condemned(db, str(tmp_path / "cond.json"))
    p = json.loads(open(tmp_path / "cond.json").read())
    assert "BAD_SW" in p["switches"]["STOCKS_LONG"], out
    assert "ENTRY_REVERSAL_BOUNCE!BAD_SW=True@H0" in p["cells"]["STOCKS_LONG"]
    assert p["cells"]["STOCKS_LONG"]["ENTRY_REVERSAL_BOUNCE!BAD_SW=True@H0"]["causal"] is True
    assert any(
        f["target"] == "BAD_SW" and f["status"] == "confirmed"
        for f in cl.query("AAA_LONG", db)
    )


def test_live_loser_recorded_weak(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ROOT", tmp_path)
    monkeypatch.setattr(cl, "_live_set_switches", lambda ss: ["SW_A", "SW_B"])
    d = tmp_path / "data" / "per_trade_returns"
    d.mkdir(parents=True)
    (d / "trb_20261009.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-10-09T10:00:00+00:00",
                "account": "trb",
                "symbol": "A",
                "side": "LONG",
                "pnl_pct": -1.0,
                "fees_pct": 0.04,
            }
        )
        + "\n"
        + json.dumps(
            {
                "ts": "2026-10-09T11:00:00+00:00",
                "account": "trb",
                "symbol": "A",
                "side": "LONG",
                "pnl_pct": 2.0,
                "fees_pct": 0.04,
            }
        )
        + "\n"
    )
    db = str(tmp_path / "c.db")
    r = cl.ingest_live(db, "20261009", root=tmp_path)
    assert r == {"lessons": 1, "causes": 2}
    import sqlite3

    at = (
        sqlite3.connect(db)
        .execute("SELECT DISTINCT attribution FROM causes")
        .fetchall()
    )
    assert at == [("live_context",)]
    assert all(f["status"] == "proposed" for f in cl.query("A_LONG", db))


def test_autolift_on_positive_sweep_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ROOT", tmp_path)
    pd = tmp_path / "prog"
    pd.mkdir()
    for ss in ["AAA_LONG", "BBB_LONG", "CCC_LONG"]:
        _progress(pd / f"{ss}_v14_progress.json", ss, [("BAD_SW", -2.0)])
    db = str(tmp_path / "c.db")
    monkeypatch.setattr(cl, "_now", lambda: "2026-10-08T00:00:00+00:00")
    cl.ingest_progress(str(pd), db)
    assert any(
        f["target"] == "BAD_SW" and f["status"] == "confirmed"
        for f in cl.query("AAA_LONG", db)
    )
    pd2 = tmp_path / "prog2"
    pd2.mkdir()
    _progress(pd2 / "DDD_LONG_v14_progress.json", "DDD_LONG", [("BAD_SW", 3.0)])
    monkeypatch.setattr(cl, "_now", lambda: "2026-10-09T00:00:00+00:00")
    cl.ingest_progress(str(pd2), db)
    fixes = cl.query("AAA_LONG", db)
    assert any(
        f["target"] == "BAD_SW" and f["status"] == "rejected" for f in fixes
    ), fixes
    import sqlite3

    v = (
        sqlite3.connect(db)
        .execute("SELECT DISTINCT verdict FROM fix_outcomes")
        .fetchall()
    )
    assert v == [("hurt",)]


def test_export_agg_pos_veto(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ROOT", tmp_path)
    pd = tmp_path / "prog"
    pd.mkdir()
    for ss in ["AAA_LONG", "BBB_LONG", "CCC_LONG"]:
        _progress(pd / f"{ss}_v14_progress.json", ss, [("BAD_SW", -2.0)])
    db = str(tmp_path / "c.db")
    cl.ingest_progress(str(pd), db)
    (tmp_path / "avg_delta_pos_sym_cell.json").write_text(
        json.dumps(
            {
                "cat_sides": {
                    "STOCKS_LONG": {"ENTRY_REVERSAL_BOUNCE!BAD_SW=True@H0": {"pos_sym": 5}}
                }
            }
        )
    )
    cl.export_condemned(db, str(tmp_path / "cond.json"))
    p = json.loads(open(tmp_path / "cond.json").read())
    assert "ENTRY_REVERSAL_BOUNCE!BAD_SW=True@H0" not in p["cells"].get("STOCKS_LONG", {})


def test_export_merge_union(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "ROOT", tmp_path)
    db = str(tmp_path / "c.db")
    cl.connect(db).close()
    m = tmp_path / "mac.json"
    m.write_text(
        json.dumps(
            {
                "switches": {"STOCKS_LONG": ["M_SW"]},
                "cells": {
                    "STOCKS_LONG": {
                        "T!r@H": {"pos_sym": 0, "n_sym": 12, "avg_delta": -1.0}
                    }
                },
            }
        )
    )
    cl.export_condemned(db, str(tmp_path / "cond.json"), str(m))
    p = json.loads(open(tmp_path / "cond.json").read())
    assert (
        p["switches"] == {"STOCKS_LONG": ["M_SW"]}
        and "T!r@H" in p["cells"]["STOCKS_LONG"]
    )
