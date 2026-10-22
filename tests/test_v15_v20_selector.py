import json
import pytest
from tools import v15_v20_selector as S


def _bm(tmp_path, cat_side="CRYPTO_LONG", rows=None, contract=S.CONTRACT):
    bm = {"meta": {"cat_side": cat_side, "contract": contract}, "switches": {}, "filters": {}, "rules": [], "rows": rows or {}}
    p = tmp_path / f"binding_map_{cat_side}.json"
    p.write_text(json.dumps(bm))
    return p


ROWS = {
    "ENTRY_REVERSAL_BOUNCE!BB_BOUNCE_ENTRY_TF=4h": {"family": "ENTRY", "dead_row": False, "cells": {
        "DC_BREAK_FILTER_TF=1h": {"verdict": "BINDS", "reason": "ENTRY_MASK", "anchor": "v12:16659"},
        "EMERGENCY_BRAKE_FILTER_TF=1h": {"verdict": "NEVER", "reason": "NO_ENGINE_READ", "anchor": "census"},
        "EMA_BLANKET_FILTER_FILTER_TF=D": {"verdict": "CONDITIONAL", "condition_keys": ["EMA_BLANKET_FILTER_ENABLED=True"], "reason": "MASTER_OFF"},
        "HTF_DIRECTION_GATE_ENABLED=True": {"verdict": "CONDITIONAL", "condition_keys": ["REENTRY_ENTRY_FILTER_ENABLED=True", "REENTRY_APPLY_ENTRY_GATES_ENABLED=True"], "condition_mode": "any", "reason": "REENTRY_MASKS_OFF"},
        "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h": {"verdict": "CONDITIONAL", "condition_keys": ["WIN_TRAIL_EROSION_PCT>0"], "reason": "DC_DAYTRADE_BRANCH"},
        "ADX_REGIME_FILTER_ENABLED=True": {"verdict": "INDIRECT", "reason": "DIFFERENT_STAGE"},
    }},
    "EXIT_STRUCTURAL!DEAD_SWITCH=1": {"family": "EXIT_STOP", "dead_row": True, "cells": {"DC_BREAK_FILTER_TF=1h": {"verdict": "NEVER", "reason": "DEAD_ROW"}}},
}


def test_load_refuses_other_cat_side(tmp_path):
    _bm(tmp_path, "CRYPTO_SHORT")
    with pytest.raises(S.CatSideMismatch):
        S.load_binding_map("CRYPTO_LONG", path=tmp_path / "binding_map_CRYPTO_SHORT.json")
    assert S.load_binding_map("CRYPTO_LONG", root=tmp_path) is None  # absent = fail-open
    _bm(tmp_path, "CRYPTO_LONG", contract="other")
    assert S.load_binding_map("CRYPTO_LONG", path=tmp_path / "binding_map_CRYPTO_LONG.json") is None


def test_cell_verdicts_resolve_conditionals_against_running_set(tmp_path):
    bm = json.loads(_bm(tmp_path, rows=ROWS).read_text())
    t, sw, c = "ENTRY_REVERSAL_BOUNCE", "BB_BOUNCE_ENTRY_TF", "4h"
    assert S.cell_verdict(bm, t, sw, c, "DC_BREAK_FILTER_TF=1h", {})[0] == "BINDS"
    assert S.cell_verdict(bm, t, sw, c, "EMERGENCY_BRAKE_FILTER_TF=1h", {})[:2] == ("NEVER", "NO_ENGINE_READ")
    assert S.cell_verdict(bm, t, sw, c, "EMA_BLANKET_FILTER_FILTER_TF=D", {"EMA_BLANKET_FILTER_ENABLED": False})[0] == "MASTER_OFF"
    assert S.cell_verdict(bm, t, sw, c, "EMA_BLANKET_FILTER_FILTER_TF=D", {"EMA_BLANKET_FILTER_ENABLED": "TRUE"})[0] == "BINDS"
    assert S.cell_verdict(bm, t, sw, c, "HTF_DIRECTION_GATE_ENABLED=True", {"REENTRY_APPLY_ENTRY_GATES_ENABLED": True})[0] == "BINDS"  # any-of
    assert S.cell_verdict(bm, t, sw, c, "HTF_DIRECTION_GATE_ENABLED=True", {})[0] == "MASTER_OFF"
    assert S.cell_verdict(bm, t, sw, c, "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h", {"WIN_TRAIL_EROSION_PCT": 0.5})[0] == "BINDS"
    assert S.cell_verdict(bm, t, sw, c, "PEAK_GIVEBACK_BE_EROSION_FILTER_TF=1h", {"WIN_TRAIL_EROSION_PCT": 0})[0] == "MASTER_OFF"
    assert S.cell_verdict(bm, t, sw, c, "ADX_REGIME_FILTER_ENABLED=True", {})[0] == "INDIRECT"
    assert S.cell_verdict(bm, t, sw, c, "BB_BOUNCE_ENTRY_TF=OFF", {})[:2] == ("NEVER", "SELF_PAIR")
    assert S.cell_verdict(bm, t, sw, c, "SOME_NEW_FILTER_TF=1h", {})[0] == "UNKNOWN"  # never NEVER by ignorance
    assert S.cell_verdict(bm, "EXIT_STRUCTURAL", "DEAD_SWITCH", "1", "DC_BREAK_FILTER_TF=1h", {})[:2] == ("NEVER", "DEAD_ROW")
    assert S.cell_verdict(None, t, sw, c, "DC_BREAK_FILTER_TF=1h", {})[0] == "UNKNOWN"


def _decide(bm, hdrs, **kw):
    base = dict(cat_side="CRYPTO_LONG", symside="BTCUSDT_LONG", rid="r1", floor_p=0.005, untried_p=0.05)
    base.update(kw)
    return S.decide_cells(hdrs, bm, "ENTRY_REVERSAL_BOUNCE", "BB_BOUNCE_ENTRY_TF", "4h", {}, **base)


def test_decide_never_is_structural_identity_and_floor_never_zero(tmp_path):
    bm = json.loads(_bm(tmp_path, rows=ROWS).read_text())
    hdrs = ["DC_BREAK_FILTER_TF=1h", "EMERGENCY_BRAKE_FILTER_TF=1h", "EMA_BLANKET_FILTER_FILTER_TF=D"]
    d = _decide(bm, hdrs)
    assert "DC_BREAK_FILTER_TF=1h" in d["keep"]  # BINDS, no model p -> p=1
    st = d["settled"]
    assert st["EMERGENCY_BRAKE_FILTER_TF=1h"]["reason_code"] in (S.RC_STRUCTURAL_IDENTITY, S.RC_KEEP_FLOOR)
    assert st["EMA_BLANKET_FILTER_FILTER_TF=D"]["reason_code"] in (S.RC_MASTER_OFF, S.RC_KEEP_FLOOR)
    for v in st.values():
        assert v["p"] >= S.HARD_MIN_FLOOR
    # floor can never be pushed below 1/1000
    d2 = _decide(bm, hdrs, floor_p=0.0)
    assert all(v["p"] >= S.HARD_MIN_FLOOR for v in d2["settled"].values())


def test_decide_floor_rescues_some_never_cells_across_many_draws(tmp_path):
    bm = json.loads(_bm(tmp_path, rows=ROWS).read_text())
    rescued = 0
    for i in range(4000):
        d = _decide(bm, ["EMERGENCY_BRAKE_FILTER_TF=1h"], rid=f"r{i}")
        rescued += 1 if d["keep"] else 0
    assert 0 < rescued < 100  # ~5/1000 of 4000 = ~20: the law "every combination keeps a chance" holds, cheaply


def test_decide_must_evidence_essential_always_kept(tmp_path):
    bm = json.loads(_bm(tmp_path, rows=ROWS).read_text())
    hdrs = ["EMERGENCY_BRAKE_FILTER_TF=1h", "EMA_BLANKET_FILTER_FILTER_TF=D", "ADX_REGIME_FILTER_ENABLED=True"]
    d = _decide(bm, hdrs, must_cells={"EMERGENCY_BRAKE_FILTER_TF=1h"}, evidence_cells={"EMA_BLANKET_FILTER_FILTER_TF=D"})
    assert d["settled"]["EMERGENCY_BRAKE_FILTER_TF=1h"]["reason_code"] == S.RC_KEEP_MUST
    assert d["settled"]["EMA_BLANKET_FILTER_FILTER_TF=D"]["reason_code"] == S.RC_KEEP_EVIDENCE
    d = _decide(bm, hdrs, essential=True)
    assert "EMA_BLANKET_FILTER_FILTER_TF=D" in d["keep"] and "ADX_REGIME_FILTER_ENABLED=True" in d["keep"]
    assert d["settled"]["EMA_BLANKET_FILTER_FILTER_TF=D"]["reason_code"] == S.RC_KEEP_ESSENTIAL


def test_decide_indirect_once_per_tab_and_model_p(tmp_path):
    bm = json.loads(_bm(tmp_path, rows=ROWS).read_text())
    seen = set()
    d1 = _decide(bm, ["ADX_REGIME_FILTER_ENABLED=True"], indirect_seen=seen)
    assert d1["keep"] == ["ADX_REGIME_FILTER_ENABLED=True"]
    outs = sum(1 for i in range(300) if not _decide(bm, ["ADX_REGIME_FILTER_ENABLED=True"], indirect_seen=seen, rid=f"x{i}")["keep"])
    assert outs > 250
    kept = sum(1 for i in range(1000) if _decide(bm, ["DC_BREAK_FILTER_TF=1h"], model_p={"DC_BREAK_FILTER_TF=1h": 0.2}, rid=f"m{i}")["keep"])
    assert 120 < kept < 280
    unt = sum(1 for i in range(1000) if _decide(bm, ["DC_BREAK_FILTER_TF=1h"], untried_cells={"DC_BREAK_FILTER_TF=1h"}, rid=f"u{i}")["keep"])
    assert 20 < unt < 90


def test_dead_end_state_literal():
    row = {"state": "SETTLED_OFF", "interval_mean_delta": [-3.0, -0.5], "n_syms": 12, "pos_syms": 0, "forward": {"pos": 0}, "live": {"pos": 0}}
    assert S.dead_end_state(row, "CRYPTO_LONG")
    assert not S.dead_end_state(row, "STOCKS_LONG")  # s_min 15
    assert not S.dead_end_state({**row, "pos_syms": 1}, "CRYPTO_LONG")
    assert not S.dead_end_state({**row, "interval_mean_delta": [-3.0, 0.1]}, "CRYPTO_LONG")
    assert not S.dead_end_state({**row, "state": "CONTESTED"}, "CRYPTO_LONG")
    assert not S.dead_end_state({**row, "live": {"pos": 2}}, "CRYPTO_LONG")
    assert not S.dead_end_state({}, "CRYPTO_LONG")


def test_must_refusal_ledger_and_skip():
    led = {}
    S.must_refuse(led, "A=1@F=o", "DEAD_VEC_PATH: wire07 purged", "sha1", "t")
    assert led["A=1@F=o"]["refused"] == "DEAD_VEC_PATH"
    kept, n = S.must_skip_refused(["A=1@F=o", "B=2"], led, "sha1")
    assert kept == ["B=2"] and n == 1
    kept, n = S.must_skip_refused(["A=1@F=o", "B=2"], led, "sha2")  # chain moved -> re-offered
    assert kept == ["A=1@F=o", "B=2"] and n == 0
    assert S.refusal_is_structural("LIVE_DEAD_KEY: x") and not S.refusal_is_structural("V17_DEADLINE")


def test_rank_must_by_interval_lower_bound_not_pos_syms():
    pa = {"sym_sides": ["A_LONG", "B_LONG", "C_LONG"], "rows": [{"key": "SW=1", "ci": [-4.0, -3.0], "pos_syms": ["A_LONG", "B_LONG"], "best_delta": 20.0}, {"key": "SW=2", "ci": [0.2, 1.0], "pos_syms": ["A_LONG"], "best_delta": 1.0}],
          "pairs": [{"key": "SW=3", "filter": "F=o", "lift_ci": [1.5, 3.0], "pos_syms": ["C_LONG"]}, {"key": "SW=4", "filter": "F=o", "lift_ci": [-0.1, 0.1], "pos_syms": ["A_LONG", "B_LONG", "C_LONG"]}]}
    out = S.rank_must(["SW=1", "SW=4@F=o", "SW=2", "SW=3@F=o", "UNKNOWN=9"], pa, "D_LONG")
    assert out[:3] == ["SW=3@F=o", "SW=2", "SW=4@F=o"] and out[-1] == "UNKNOWN=9"
    tie = S.rank_must(["X=1", "X=2"], {"rows": [], "pairs": []}, "D_LONG", linknet_p={"X=2": 0.9, "X=1": 0.1})
    assert tie == ["X=2", "X=1"]


def test_losing_switch_alternatives():
    losing = [{"sym_side": "EDU_LONG", "switch": "SWA", "value": "True", "ci": [-49.0, -33.0]}, {"sym_side": "OTHER_LONG", "switch": "SWB", "value": "1", "ci": [-2, -1]}]
    tpl = {"SWA": ["True", "False"], "SWB": ["0", "1", "2"]}
    assert S.losing_switch_alternatives({"SWA": True}, losing, tpl, "EDU_LONG") == ["SWA=False"]
    assert S.losing_switch_alternatives({"SWB": 1}, losing, tpl, "OTHER_LONG") == ["SWB=0", "SWB=2"]
    assert S.losing_switch_alternatives({}, losing, tpl, "NOBODY_LONG") == []


def test_budget_plan_never_skips_protected_and_budget_zero_is_full():
    cands = [{"id": "e", "cls": "ESSENTIAL", "rank": 0, "cost": 50}, {"id": "m1", "cls": "MUST", "rank": 0.1, "cost": 50}, {"id": "m2", "cls": "MUST", "rank": 0.2, "cost": 50}, {"id": "n1", "cls": "NAKED", "rank": 0.0, "cost": 10}, {"id": "c1", "cls": "CELL", "rank": 0.3, "cost": 10}, {"id": "c2", "cls": "CELL", "rank": 0.1, "cost": 10}]
    full = S.budget_plan(cands, 0)
    assert len(full["keep"]) == 6 and not full["skipped"]
    b = S.budget_plan(cands, 20)
    assert set(b["keep"]) >= {"e", "m1", "m2"} and "n1" in b["keep"]
    assert [s["id"] for s in b["skipped"]] == ["c2", "c1"] and all(s["reason_code"] == S.RC_BUDGET_SKIPPED for s in b["skipped"])
    assert S.budget_forced_profile(0) == {} and S.budget_forced_profile(5000)["V15_V17_ROUNDS"] == "0" and "V15_HOLDOUT_DAYS" not in S.budget_forced_profile(5000)
    assert S.board_budget_from_env({}) == 0 and S.board_budget_from_env({"V15_BOARD_BUDGET": "12000"}) == 12000 and S.board_budget_from_env({"V15_BOARD_BUDGET": "x"}) == 0


def test_settle_record_has_no_delta_field():
    r = S.settle_record("BTCUSDT_LONG", "T", "SW", "1", "F=o", {"reason_code": S.RC_STRUCTURAL_IDENTITY, "verdict": "NEVER", "why": "NO_ENGINE_READ", "p": 0.005}, "sha")
    assert r["label"] == "SETTLE" and r["cached"] is True and "delta" not in r and r["binding_verdict"] == "NEVER"


def test_template_cands_from_map(tmp_path):
    bm = json.loads(_bm(tmp_path, rows=ROWS).read_text())
    assert S.template_cands_from_map(bm) == {"BB_BOUNCE_ENTRY_TF": ["4h"], "DEAD_SWITCH": ["1"]}
