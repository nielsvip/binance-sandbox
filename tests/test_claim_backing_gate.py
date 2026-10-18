#!/usr/bin/env python3
"""The claim-backing gate must refuse a fabricated gain and accept a backed one.

USER 2026-10-18: "NOTHING EVER EVER CAN CLAIM A GAIN THAT IS NOT COMPLETELY BACKED UP WITH TRADE BY TRADE XLSX
AND HTML AND JSON EVER". These tests pin the only behaviour that makes that true: the claim is checked against a
board's OWN per-trade rows before the number reaches any live surface, and the check is fail-closed.

Fixture arithmetic (never asserted, always derived from the project's own helpers): one OPEN of 10 @ $10 = $100
cost basis, one realised close of +$25 -> gain = 25 / 100 * 100 = 25.0%.
"""
import json
import os
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")

SS = "ZZTEST_LONG"
BOARD = f"{SS}_bh1_gain25_t1_30d_matrix"
TRADES = [{"type": "CLOSE", "ts": 2.0, "bar_entry": 1, "bar_exit": 3, "entry_price": 10.0, "exit_price": 12.5,
           "qty": 10.0, "deployed": 100.0, "pnl_dollars": 25.0, "pnl_pct": 25.0, "exit_reason": "TEST_CLOSE"}]
EVENTS = [{"type": "OPEN", "ts": 1.0, "bar": 1, "price": 10.0, "qty": 10.0, "entry_reason": "TEST_OPEN"}]
CLOSE = [10.0, 10.5, 11.0, 12.5, 12.0]


def _write_board(root: Path, *, with_xlsx=True, header_gain=None, header_trades=None):
    d = root / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    d.mkdir(parents=True, exist_ok=True)
    from tools.opt.chart_truth import recompute

    truth = recompute(TRADES, EVENTS, CLOSE, True)
    g = truth["gain_pct"] if header_gain is None else header_gain
    n = truth["trades"] if header_trades is None else header_trades
    html = (
        "<html><body>\n"
        f'<div class="metrics">{SS} gain {g:.4f}% BH 1.00% trades {n}</div>\n'
        "<script>\n"
        "const CLOSE = " + json.dumps(CLOSE) + ";\n"
        "const LABELS = " + json.dumps([str(i) for i in range(len(CLOSE))]) + ";\n"
        "const TRADES = " + json.dumps(TRADES) + ";\n"
        "const EVENTS = " + json.dumps(EVENTS) + ";\n"
        "</script></body></html>\n"
    )
    (d / f"{BOARD}.html").write_text(html, encoding="utf-8")
    (d / f"{BOARD}_manifest.json").write_text(json.dumps({"symside": SS, "final_name": f"{BOARD}.xlsx",
                                                          "metrics": {"gain_pct": truth["gain_pct"], "trades": truth["trades"]}}))
    if with_xlsx:
        wb = openpyxl.Workbook()
        wb.active["A1"] = SS
        for i in range(12):
            wb.create_sheet(f"TAB{i}")
        wb.save(d / f"{SS}_26101801_bh1_gain25_t1_30d_matrix.xlsx")  # publish-round token must not break the pairing
    return truth


def _ev(gain, trades=1, tim=50.0, dd=2.0):
    return {"n_promoted": 1, "valid": True, "gain_pct": gain, "trades": trades, "tim_pct": tim,
            "max_dd_pct": dd, "pool_sharpe": None, "bh_pct": 1.0, "baseline_gain": 0.0}


def _check(root, gain, **kw):
    import claim_backing_gate as G

    return G.check_claim_backed(SS, _ev(gain), root=root, use_cache=False, **kw)


def test_truth_is_the_projects_own_arithmetic(tmp_path):
    """The fixture's gain is whatever evaluate_v12 says it is — the test never hardcodes the number."""
    truth = _write_board(tmp_path)
    assert truth["trades"] == 1
    assert abs(truth["gain_pct"] - 25.0) < 1e-9, truth
    assert abs(truth["peak_capital"] - 100.0) < 1e-9, truth


def test_accepts_a_claim_its_own_trades_reproduce(tmp_path):
    truth = _write_board(tmp_path)
    ok, why, rec = _check(tmp_path, truth["gain_pct"])
    assert ok, (why, rec)
    assert rec["verdict"] == "BACKED" and rec["reason"] == "TRIO_REPRODUCES", rec
    assert rec["json_source"] == "EMBEDDED" and abs(rec["d_gain"]) < 1e-6, rec


def test_accepts_inside_tolerance_refuses_outside(tmp_path):
    truth = _write_board(tmp_path)
    g = truth["gain_pct"]
    assert _check(tmp_path, g + 0.40, tol=0.48)[0] is True
    assert _check(tmp_path, g + 0.60, tol=0.48)[0] is False


def test_refuses_a_fabricated_gain(tmp_path):
    """The board says 25%. A claim of 500% must not be registrable."""
    _write_board(tmp_path)
    ok, why, rec = _check(tmp_path, 500.0)
    assert not ok
    assert rec["verdict"] == "CONTRADICTED" and rec["reason"] == "NO_BOARD_REPRODUCES", rec
    assert abs(rec["d_gain"] + 475.0) < 1e-6, rec


def test_refuses_a_sign_flip(tmp_path):
    """Claimed positive, own trades negative — the 81-of-248 class, named separately."""
    neg = [dict(TRADES[0], pnl_dollars=-25.0, pnl_pct=-25.0)]
    d = tmp_path / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    _write_board(tmp_path)
    txt = (d / f"{BOARD}.html").read_text()
    from tools.opt.chart_truth import recompute

    t2 = recompute(neg, EVENTS, CLOSE, True)
    txt = txt.replace("const TRADES = " + json.dumps(TRADES), "const TRADES = " + json.dumps(neg))
    txt = txt.replace(f"gain {25.0:.4f}%", f"gain {t2['gain_pct']:.4f}%")
    (d / f"{BOARD}.html").write_text(txt)
    ok, why, rec = _check(tmp_path, 25.0)
    assert not ok and rec["reason"] == "SIGN_FLIP", rec


def test_refuses_when_the_xlsx_leg_is_missing(tmp_path):
    """The number reproduces, but the operator's trio does not exist — still not backed."""
    truth = _write_board(tmp_path, with_xlsx=False)
    ok, why, rec = _check(tmp_path, truth["gain_pct"])
    assert not ok and rec["reason"] == "TRIO_INCOMPLETE" and "XLSX" in rec["missing"], rec


def test_refuses_when_the_chart_header_lies(tmp_path):
    """A chart whose header disagrees with its own arrays is not evidence (chart_truth.verify_html)."""
    truth = _write_board(tmp_path, header_gain=99.0)
    ok, why, rec = _check(tmp_path, truth["gain_pct"])
    assert not ok and "HTML_HEADER" in (rec.get("missing") or []), rec


def test_refuses_when_there_is_no_evidence_at_all(tmp_path):
    (tmp_path / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL").mkdir(parents=True, exist_ok=True)
    ok, why, rec = _check(tmp_path, 25.0)
    assert not ok and rec["reason"] == "NO_EVIDENCE", rec


def test_refuses_a_truncated_xlsx(tmp_path):
    """v15_pilot._atomic_save's own validity test: an OOM-truncated 225 KB workbook is not a board."""
    truth = _write_board(tmp_path)
    d = tmp_path / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    (d / f"{SS}_26101801_bh1_gain25_t1_30d_matrix.xlsx").write_bytes(b"PK\x03\x04 truncated")
    ok, why, rec = _check(tmp_path, truth["gain_pct"])
    assert not ok and "XLSX" in (rec.get("missing") or []), rec


def test_standalone_json_is_absent_until_a_sidecar_exists(tmp_path):
    truth = _write_board(tmp_path)
    assert _check(tmp_path, truth["gain_pct"], require_sidecar=True)[0] is False
    d = tmp_path / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    (d / f"{BOARD}_trades.json").write_text(json.dumps({"symside": SS, "trades": TRADES, "events": EVENTS}))
    ok, why, rec = _check(tmp_path, truth["gain_pct"], require_sidecar=True)
    assert ok and rec["json_source"] == "SIDECAR", (why, rec)


# ---------------------------------------------------------------- the registrar

def _register(gain, env):
    """register_workbook_result(dry_run=True) — gates run, nothing is ever written."""
    import importlib

    import switch_parity as SP

    importlib.reload(SP)
    old = {k: os.environ.get(k) for k in ("CLAIM_BACKING_GATE", "CLAIM_BACKING_TOL", "SWITCH_PARITY_REGISTER")}
    os.environ.pop("SWITCH_PARITY_REGISTER", None)
    os.environ.update(env)
    try:
        ss, ov = _real_backed_symside()
        snap = SP._cat_defaults(SP.cat_side_of(ss))
        k = next((x for x in sorted(ov) if x in snap and not isinstance(snap[x], (dict, list))), None)
        if k is None:
            pytest.skip(f"no override of {ss} is a live cat_side key on this host")
        return SP.register_workbook_result(ss, {k: ov[k]}, _ev(gain, trades=20, tim=50.0), dry_run=True, require_qualified=True)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


_CACHED = {}


def _real_backed_symside():
    """One sym_side this host can actually back, with its stored set — resolved, never assumed."""
    if _CACHED:
        return _CACHED["ss"], _CACHED["ov"]
    import sqlite3

    import claim_backing_gate as G

    db = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
    if not db.exists():
        pytest.skip("per_sym_store.db absent on this host")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    idx = G.index_artifacts()
    cache = G.Cache(G.CACHE, enabled=True)
    for ss, ovj, g in con.execute("select sym_side, overrides_json, acc_gain_pct from per_sym_active"
                                  " where acc_gain_pct > 0 and updated_at >= '2026-10-15' order by updated_at desc"):
        rec = G.judge({"source": "t", "sym_side": ss, "gain_pct": g, "trades": None}, idx.get(ss, {}), cache, 0.48, False, 40)
        if rec["verdict"] == "BACKED":
            try:
                ov = json.loads(ovj or "{}")
            except Exception:
                continue
            if ov:
                cache.save()
                _CACHED.update({"ss": ss, "ov": ov, "gain": float(rec["evidence_gain_pct"])})
                con.close()
                return ss, ov
    con.close()
    pytest.skip("no BACKED live claim on this host to register against")


def test_registrar_is_inert_by_default():
    """The change must be OFF until the operator turns it on — no new refusal path in production today."""
    _real_backed_symside()
    rep = _register(_CACHED["gain"], {})
    assert "claim_backing" not in rep, rep


def test_registrar_accepts_a_backed_claim():
    _real_backed_symside()
    rep = _register(_CACHED["gain"], {"CLAIM_BACKING_GATE": "1"})
    assert rep.get("claim_backing", {}).get("verdict") == "BACKED", rep
    assert "claim-backing gate refused" not in str(rep.get("reason") or ""), rep


def test_registrar_refuses_a_fabricated_claim():
    _real_backed_symside()
    rep = _register(_CACHED["gain"] + 500.0, {"CLAIM_BACKING_GATE": "1"})
    assert rep.get("registered") is False
    assert str(rep.get("reason") or "").startswith("claim-backing gate refused"), rep
    assert rep["claim_backing"]["verdict"] in ("CONTRADICTED", "UNBACKED"), rep


def test_registrar_warn_mode_records_without_refusing():
    _real_backed_symside()
    rep = _register(_CACHED["gain"] + 500.0, {"CLAIM_BACKING_GATE": "warn"})
    assert rep["claim_backing"]["verdict"] in ("CONTRADICTED", "UNBACKED"), rep
    assert "claim-backing gate refused" not in str(rep.get("reason") or ""), rep


def test_registrar_fails_closed_when_the_gate_cannot_run(tmp_path):
    """An unimportable gate must refuse, never wave the claim through."""
    import importlib

    import switch_parity as SP

    importlib.reload(SP)
    os.environ["CLAIM_BACKING_GATE"] = "1"
    try:
        rep = SP.register_workbook_result("ZZNOSUCH_LONG", {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0},
                                          _ev(10.0, trades=20), dry_run=True, root=tmp_path)
        assert rep.get("registered") is not True, rep
        assert rep.get("reason"), rep
    finally:
        os.environ.pop("CLAIM_BACKING_GATE", None)
