"""USER 2026-10-07 ENDGAME wiring pins (static — the pilot runs on fleet, not Mac).

Order: per-row fill (filter defaults from moment 1, alternatives orange) ->
_final_filter_recheck -> _endgame_filter_cycle (REPAIR -> STRIP -> NAKED retest ->
one-by-one REAPPLY to bh+10) -> _diagnose_repair -> compliance -> DONE.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = (ROOT / "v15_pilot.py").read_text()


def _pos(needle, n=0):
    idx = -1
    for _ in range(n + 1):
        idx = SRC.find(needle, idx + 1)
        assert idx != -1, needle
    return idx


def test_endgame_defined_and_called_in_order():
    d = _pos("def _endgame_filter_cycle():")
    call = _pos("            _endgame_filter_cycle()\n")
    assert call > d
    assert _pos("\n            _final_filter_recheck()\n") < call < _pos("\n            _diagnose_repair()\n")  # 2026-10-14: FFR call nested under the V17 phase-cut branch


def test_env_gates_and_budget():
    for k in ("V15_ENDGAME", "V15_ENDGAME_S", "V15_ENDGAME_MAX_EVALS", "V15_ENDGAME_TARGET_PTS"):
        assert k in SRC, k
    assert "5400.0 - (t0 - _PILOT_T0) - 900.0" in SRC


def test_delta_log_labels_and_sheet():
    for lab in ("ENDGAME_REPAIR", "ENDGAME_STRIP", "ENDGAME_NAKED", "ENDGAME_REAPPLY_TRY", "ENDGAME_ADOPT"):
        assert lab in SRC, lab
    assert "ENDGAME_FILTER_CYCLE" in SRC


def test_greedy_and_nolies_rules():
    assert "g - work_gain > 1e-9" in SRC
    assert "NON_BINDING_ZERO" in SRC and "OFFSET_ZERO" in SRC
    assert 'ENTRY_SIGNAL' not in SRC


def test_repair_and_reconcile():
    assert "ABLATION_DISABLE_" in SRC and "<DROPPED-P0>" in SRC
    assert "_c_drop(*_c_where.pop(dk), dk)" in SRC


def test_resume_and_never_raises():
    assert 'progress["endgame"]' in SRC and '"result_key"' in SRC
    assert "except V15Shutdown:\n            raise" in SRC


def test_tier_share_report_only():
    assert "tier_share" in SRC and "DOMINANT>30%" in SRC
    assert "report-only: no auto-action" in SRC


def test_selflearn_wiring_pins():
    for k in ("V15_ENDGAME_EV_ORDER", "V15_ENDGAME_LEDGER"):
        assert k in SRC, k
    for fn in ("def _endgame_ev(", "def _endgame_rank(", "def _endgame_merge_knowledge("):
        assert fn in SRC, fn
    assert "_endgame_rank(list(S.keys())" in SRC
    assert "_endgame_rank(list(remaining.keys())" in SRC
    assert "endgame_ledger.jsonl" in SRC and "ENDGAME-PROOF" in SRC
    assert "_eg_entry_gain" in SRC and "_eg_lift" in SRC
    assert '["KNOWLEDGE"' in SRC


def test_no_lookahead_order():
    load = _pos("endgame_knowledge.json")
    ledger = _pos("endgame_ledger.jsonl")
    merge_write = _pos("endgame_knowledge.json", 1)
    assert load < ledger < merge_write


def _exec_pure():
    import re
    m = re.search(r"(def _endgame_ev\(.*?)(?=\ndef (?!_endgame_))", SRC, re.S)
    assert m, "pure block"
    ns = {}
    exec(compile(m.group(1), "endgame_pure", "exec"), ns)
    return ns


def test_ev_and_rank_behavior():
    ns = _exec_pure()
    ev, rank = ns["_endgame_ev"], ns["_endgame_rank"]
    assert ev("A", {}) is None
    assert ev("A", {"keys": {"A": {"n": 2, "sum": 9.0}}}) is None
    assert ev("A", {"keys": {"A": {"n": 3, "sum": 9.0}}}) == 3.0
    know = {"keys": {"B": {"n": 5, "sum": 10.0}, "A": {"n": 4, "sum": 40.0}, "C": {"n": 1, "sum": 99.0}}}
    assert rank(["B", "C", "A", "D"], know, False) == ["A", "B", "C", "D"]
    assert rank(["B", "C", "A", "D"], know, True) == ["A", "B", "C", "D"]
    assert rank(["B", "A"], {}, True) == ["A", "B"]


def test_merge_behavior():
    ns = _exec_pure()
    merge = ns["_endgame_merge_knowledge"]
    k = merge({}, [("S1", 2.0), ("S2", None), ("S1=X", -1.0)], [("F1", 0.5)], ["F9"], 1.5, True, True)
    assert k["keys"]["S1"] == {"n": 1, "sum": 2.0, "pos": 1}
    assert "S2" not in k["keys"]
    assert k["keys"]["F1"]["pos"] == 1 and k["keys"]["F9"]["dropped"] == 1
    assert k["workbooks"] == {"n": 1, "sum_lift": 1.5, "pos_lift": 1, "target_met": 1, "adopted": 1}
    k = merge(k, [("S1", -4.0)], [], [], -2.0, False, False)
    assert k["keys"]["S1"]["n"] == 2 and k["workbooks"]["pos_lift"] == 1
    assert k["workbooks"]["n"] == 2 and abs(k["workbooks"]["sum_lift"] - (-0.5)) < 1e-9


def test_adopt_guard_rejects_worse_accepts_better():
    ns = _exec_pure()
    v = ns["_endgame_adopt_verdict"]
    ok_res = {"gain_pct": 12.0, "valid": True, "trades": 30, "tim_pct": 40.0}
    assert v(ok_res, 10.0, 40.0, 30) == (True, "ok")
    assert v(dict(ok_res, gain_pct=10.0), 10.0, 40.0, 30)[0]  # equal = not worse
    ok, why = v(dict(ok_res, gain_pct=9.5), 10.0, 40.0, 30)
    assert not ok and why.startswith("worse lift -0.5")
    assert not v(dict(ok_res, valid=False), 1.0)[0]
    assert not v(None, 1.0)[0] and not v({"gain_pct": None}, 1.0)[0]
    assert not v(dict(ok_res, trades=5), 1.0, 40.0, 30)[0]
    assert v(dict(ok_res, trades=6), 1.0, 40.0, 5)[0]  # below-floor ref may move toward health
    assert not v(dict(ok_res, tim_pct=90.0), 1.0, 40.0, 30)[0]
    assert v(dict(ok_res, tim_pct=85.0), 1.0, 95.0, 30)[0]  # out-of-band ref moving toward band
    assert v(dict(ok_res, gain_pct=-1.0), None)[0]  # no reference -> health checks only


def test_best_seen_note_and_npz_reset():
    ns = _exec_pure()
    note = ns["_endgame_best_seen_note"]
    r = lambda g, **kw: dict({"gain_pct": g, "valid": True, "trades": 30, "tim_pct": 40.0}, **kw)
    b = note(None, "ENTRY", {"A": 1}, r(10.0), "npz1")
    assert b["gain"] == 10.0 and b["stage"] == "ENTRY" and b["overrides"] == {"A": 1}
    b = note(b, "ADOPT", {"A": 2}, r(8.0), "npz1")
    assert b["gain"] == 10.0 and b["overrides"] == {"A": 1}  # worse never replaces best
    b = note(b, "DIAG", {"A": 3}, r(15.0, tim_pct=95.0), "npz1")
    assert b["gain"] == 10.0  # guard-failing set never recorded
    b = note(b, "DIAG", {"A": 4}, r(11.0), "npz1")
    assert b["gain"] == 11.0 and b["stage"] == "DIAG"
    b = note(b, "ENTRY", {"A": 5}, r(3.0), "npz2")
    assert b["npz"] == "npz2" and b["gain"] == 3.0  # new window resets


def test_fleet_view_prefers_cat_side():
    ns = _exec_pure()
    view = ns["_endgame_fleet_view"]
    fleet = {"workbooks": {"n": 9}, "keys": {"A": {"n": 5, "sum": 5.0}, "B": {"n": 4, "sum": -4.0}},
             "by_cat_side": {"CRYPTO_LONG": {"keys": {"A": {"n": 3, "sum": -9.0}, "B": {"n": 2, "sum": 50.0}}}}}
    v = view(fleet, "CRYPTO_LONG")
    assert ns["_endgame_ev"]("A", v) == -3.0 and ns["_endgame_ev"]("B", v) == -1.0
    assert v["workbooks"]["n"] == 9 and view({}, "X") == {"keys": {}, "workbooks": {}}


def test_merge_knowledge_reject_fields():
    ns = _exec_pure()
    k = ns["_endgame_merge_knowledge"]({}, [], [], [], 0.0, False, False, -2.5, True)
    assert k["workbooks"]["rejected"] == 1 and k["workbooks"]["sum_cand_lift"] == -2.5 and k["workbooks"]["adopted"] == 0


def test_adopt_guard_wiring_pins():
    for k in ("V15_ENDGAME_ADOPT_GUARD", "[ENDGAME-REJECT]", "V15_DIAG_ADOPT_GUARD", "[DIAG-REJECT]", "V15_BEST_SEEN_GATE",
              "[BEST-SEEN-FALLBACK]", 'progress["best_seen"]', "ENDGAME_ENTRY", "REPAIR_CEIL", "V15_ENDGAME_KNOWLEDGE",
              "endgame_knowledge_fleet.json", "_endgame_fleet_view(_eg_fleet"):
        assert k in SRC, k
    adopt = _pos('ares, aerr = _eg_eval("ENDGAME_ADOPT"')
    assert _pos("_endgame_adopt_verdict(ares, _eg_ref_gain", 0) > adopt
    # the stripped discovery base is never assigned to the cumulative set
    assert "cumulative_overrides = sanitize_overrides(stripped" not in SRC and "cumulative_overrides = base" not in SRC
    gate = _pos("[BEST-SEEN-FALLBACK]")
    assert _pos("\n            _diagnose_repair()\n") < gate < _pos('print("[DONE-STEP] atomic save start"')
