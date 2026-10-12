"""End-rerun repairs (USER 2026-10-12): the worst_first end rerun must analyze what is
wrong with the numbers and fix it (entries/exits/filters) instead of burning the
budget and quarantining.

Covers: TRADES promotion guard (chains must not filter to death), ESSENTIAL-ROWS
gate membership (switches that cannot move must not gate), ZERO_FORMULA essential
exemption (essential rows are measured, never skipped), and the 365D repair tool
(diagnose-ordered capped steps, inner budget, incremental dumps, tighten moves,
partial-resume in the pilot, strict-progress REDO).

The pilot is exec-sliced (its real code text) so these tests stay light; the 365
tool is imported directly (its top level is stdlib-only).
"""
import os
from pathlib import Path

import tools.v15_365_repair as R365

SRC = (Path(__file__).resolve().parent.parent / "v15_pilot.py").read_text()


def _exec_slice(start_marker, end_marker, **ns):
    seg = SRC[SRC.index(start_marker):SRC.index(end_marker)]
    g = dict(ns)
    exec(compile(seg, "<v15_pilot_slice>", "exec"), g)
    return g


def _veto_ns():
    return _exec_slice("def trades_guard_veto", "def repair_needs_redo", os=os, ADAPT_FLOOR_TRADES=10)


def _gate_ns():
    return _exec_slice("ESSENTIAL_GATE_SWITCHES = frozenset({", "_START_STAMP = None")


def _best365_ns():
    return _exec_slice("def _best_365_overrides", "def _run_365_repair")


# ---- 365 tool: diagnose_pair ----

def test_diagnose_short():
    assert R365.diagnose_pair({"trades": 5}, {"trades": 200, "max_dd_pct": 5}) == "short"
    assert R365.diagnose_pair({"trades": 30}, {"trades": 50, "max_dd_pct": 5}) == "short"


def test_diagnose_overtrade_dd_high_flat():
    assert R365.diagnose_pair({"trades": 222}, {"trades": 2501, "max_dd_pct": 63}) == "overtrade"
    assert R365.diagnose_pair({"trades": 100}, {"trades": 120, "max_dd_pct": 25}) == "dd_high"
    assert R365.diagnose_pair({"trades": 100}, {"trades": 120, "max_dd_pct": 10}) == "flat"


def test_order_candidates_family_first():
    raw = [("EXIT_STRUCTURAL:X=Y", {}), ("REDUCE_PROFIT_LOCK:A=B", {}), ("REENTRY_WINDOWED:C=D", {})]
    assert [x[0] for x in R365.order_candidates(raw, "overtrade")][0].startswith("REDUCE_PROFIT_LOCK")
    assert [x[0] for x in R365.order_candidates(raw, "flat")][0].startswith("EXIT_STRUCTURAL")


def test_score_prefers_qualified_pair():
    good = ({"gain_pct": 5.0, "trades": 30, "valid": True}, {"gain_pct": 8.0, "trades": 200, "valid": True})
    bad = ({"gain_pct": -38.0, "trades": 222, "valid": True}, {"gain_pct": -31.0, "trades": 2501, "valid": False})
    assert R365.ok_pair(good) and not R365.ok_pair(bad)
    assert R365.score(good) > R365.score(bad)


# ---- pilot: trades_guard_veto (real code) ----

def test_trades_veto_below_floor_vs_healthy_chain(monkeypatch):
    monkeypatch.delenv("V15_TRADES_GUARD", raising=False)
    v = _veto_ns()["trades_guard_veto"]
    assert v(5, 30) != ""
    assert "TRADES-guard" in v(5, 30)


def test_trades_veto_pass_and_escape(monkeypatch):
    monkeypatch.delenv("V15_TRADES_GUARD", raising=False)
    v = _veto_ns()["trades_guard_veto"]
    assert v(12, 30) == ""
    assert v(10, 30) == ""
    assert v(5, 3) == ""  # below-floor chain may climb
    assert v(None, 30) == ""


def test_trades_veto_env_killswitch(monkeypatch):
    monkeypatch.setenv("V15_TRADES_GUARD", "0")
    assert _veto_ns()["trades_guard_veto"](0, 30) == ""


def test_trades_veto_wired_at_both_promotion_sites():
    assert SRC.count("trades_guard_veto(_ctr, chain_trades)") >= 2
    assert SRC.count("chain_trades = int(") >= 3  # baseline init + repass update + main update


# ---- pilot: essential gate membership (real code) ----

def test_essential_gate_drops_unwired_tf_switches():
    g = _gate_ns()
    assert "EMA_9_21_FILTER_TFS" not in g["ESSENTIAL_GATE_SWITCHES"]
    assert "EMA_9_21_TIMEFRAME" not in g["ESSENTIAL_GATE_SWITCHES"]
    assert "EMA_9_21_FILTER_ENABLED" in g["ESSENTIAL_GATE_SWITCHES"]
    assert "EMA_BLANKET_FILTER_ENABLED" in g["ESSENTIAL_GATE_SWITCHES"]


def test_essential_gate_check_passes_proven_set():
    g = _gate_ns()
    check = g["essential_gate_check"]
    done = {"T!1:EMA_9_21_FILTER_ENABLED=True": {"switch": "EMA_9_21_FILTER_ENABLED", "delta": 1.5, "yellows": {}}}
    out = check(done, {"EMA_9_21_FILTER_ENABLED": True}, True, False)
    assert out["EMA_9_21_FILTER_ENABLED"]["status"] == "PASS"
    assert "EMA_9_21_FILTER_TFS" not in out and "EMA_9_21_TIMEFRAME" not in out


def test_zero_formula_exempts_essential_switches():
    i = SRC.index("ZERO_FORMULA_ROW: pos_sym=0")
    window = SRC[max(0, i - 600):i]
    assert "not in ESSENTIAL_GATE_SWITCHES" in window


# ---- pilot: 365 partial resume (real code) ----

def test_best_365_overrides_partial_and_final():
    b = _best365_ns()["_best_365_overrides"]
    ov, src = b({"steps": [{"applied": "s0", "overrides": {"A": 1}}, {"applied": "s1", "overrides": {"A": 2}}]})
    assert ov == {"A": 2} and src == "partial:s1"
    ov, src = b({"steps": [{"applied": "s0", "overrides": {"A": 1}}], "final": {"overrides": {"A": 9}}})
    assert ov == {"A": 9} and src == "final"
    assert b({}) == (None, "")
    assert b({"steps": [{"result": "no improving row"}]}) == (None, "")


def test_365_runner_persistent_partial_and_progress_redo():
    assert "v15_365_repair" in SRC[SRC.index("def _run_365_repair"):SRC.index("def _adapt_template_rows")]
    seg = SRC[SRC.index("def _run_365_repair"):SRC.index("def _adapt_template_rows")]
    assert "_best_365_overrides(_prep)" in seg
    assert "TemporaryDirectory" not in seg
    assert "_progress365" in SRC and "_redo365" in SRC
