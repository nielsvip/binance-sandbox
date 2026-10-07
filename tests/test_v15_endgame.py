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
    call = _pos("\n        _endgame_filter_cycle()\n")
    assert call > d
    assert _pos("\n        _final_filter_recheck()\n") < call < _pos("\n            _diagnose_repair()\n")


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
