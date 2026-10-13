"""SMART ROUTE (USER 2026-10-13): ablation first, abbreviated worst_first, enlarged PINPOINT, self-improving knowledge.
Pure-function pins for tools/v15_smart_route.py + source pins for the v15_pilot wiring (bible §93)."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import v15_smart_route as SR  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_canon_twins_match_pilot():
    import v15_pilot as P
    for tab, sw, c in (("EXIT_VELOCITY", "wt_x", "2.0"), ("ENTRY_X", "A_B", True), ("T", "K", "off"), ("T", "K", "15m,1h"), ("T", "K", 0.25)):
        assert SR.canon_key(tab, sw, c) == P._canon_key(tab, sw, c)
    for s in ("KMNOUSDT_SHORT", "AAPL_LONG", "BTCUSDC_LONG", "BWXT_SHORT"):
        assert SR.cat_side_of(s) == P.map_key_for_symside(s)


def test_ablation_candidates_only_active_strippable_filters():
    running = {"MOM3_FILTER_TF": "1h", "HTF_DIRECTION_GATE_ENABLED": True, "WT_DIV_ENTRY_GATE_ENABLED": False, "DC_BREAK_FILTER_TF": "OFF", "ABLATION_DISABLE_X_FILTER": True, "FIXED_STOP_GATE": True, "BTC_HEDGE_REQUIRE_WT_3M": True, "RSI_FILTER_LEVEL": 30, "PLAIN_SWITCH": True}
    got = SR.ablation_candidates(running, {}, filt_names=set(), knowledge_filters={})
    keys = [k for k, _, _ in got]
    assert keys == ["HTF_DIRECTION_GATE_ENABLED", "MOM3_FILTER_TF"]
    assert dict((k, off) for k, _, off in got) == {"HTF_DIRECTION_GATE_ENABLED": False, "MOM3_FILTER_TF": "OFF"}
    # a template filter without a blocker token qualifies through filt_names; numeric thresholds never (no unambiguous off)
    got2 = SR.ablation_candidates(running, {}, filt_names={"PLAIN_SWITCH", "RSI_FILTER_LEVEL"})
    assert [k for k, _, _ in got2][0] == "PLAIN_SWITCH" and "RSI_FILTER_LEVEL" not in [k for k, _, _ in got2]


def test_ablation_candidates_rank_by_knowledge_and_cap():
    running = {"A_FILTER_TF": "1h", "B_GATE_ENABLED": True, "C_FILTER_TF": "4h"}
    kf = {SR.canon_filter("C_FILTER_TF", "4h"): {"unmask_win": 2, "blocking": 3}, SR.canon_filter("B_GATE_ENABLED", True): {"blocking": 1}}
    got = SR.ablation_candidates(running, {}, knowledge_filters=kf, cap=2)
    assert [k for k, _, _ in got] == ["C_FILTER_TF", "B_GATE_ENABLED"]


def test_classify_strip_blocking_and_helps():
    base = {"gain_pct": 1.0, "trades": 40, "valid": True, "tim_pct": 30.0}
    c = SR.classify_strip(base, {"gain_pct": 0.5, "trades": 48, "valid": True, "tim_pct": 35.0})
    assert c["blocking"] and not c["helps"] and c["d_trades"] == 8 and c["d_tim"] == pytest.approx(5.0)
    c = SR.classify_strip(base, {"gain_pct": 1.4, "trades": 41, "valid": True})
    assert c["helps"] and not c["blocking"]
    starved = {"gain_pct": 0.0, "trades": 0, "valid": False}
    c = SR.classify_strip(starved, {"gain_pct": -0.2, "trades": 1, "valid": True})
    assert c["blocking"] and not c["helps"]
    c = SR.classify_strip(base, {"gain_pct": 3.0, "trades": 40, "valid": False})
    assert not c["helps"]


def test_unmask_order_most_trades_first():
    strips = {"A=1H": {"blocking": True, "d_trades": 5, "d_gain": -1.0}, "B=TRUE": {"blocking": False, "helps": True, "d_trades": 0, "d_gain": 0.4}, "C=4H": {"blocking": True, "d_trades": 20, "d_gain": -2.0}, "D=OFF": {"blocking": False, "helps": False}}
    assert SR.unmask_order(strips) == ["C=4H", "A=1H", "B=TRUE"]


def test_probe_rows_paths_only_enabling_first_knowledge_first():
    rows = [("AUGMENT_TREND", 3, "AUG_X_ENABLED", "True"), ("EXIT_VELOCITY", 4, "EXIT_A_TF", "OFF"), ("ENTRY_REVERSAL_BOUNCE", 5, "ENTRY_B_ENABLED", "True"), ("ENTRY_REVERSAL_BOUNCE", 6, "ENTRY_C_ENABLED", "True"), ("REENTRY_WINDOWED", 7, "RE_D", "2")]
    running = {"EXIT_A_TF": "1h", "ENTRY_B_ENABLED": False, "ENTRY_C_ENABLED": True, "RE_D": 1}
    got = SR.probe_rows(rows, running, {})
    assert ("AUGMENT_TREND", 3, "AUG_X_ENABLED", "True") not in got  # not a path tab
    assert ("ENTRY_REVERSAL_BOUNCE", 6, "ENTRY_C_ENABLED", "True") not in got  # already the running value
    assert got[0] == ("ENTRY_REVERSAL_BOUNCE", 5, "ENTRY_B_ENABLED", "True")  # enabling first
    kr = {SR.canon_key("REENTRY_WINDOWED", "RE_D", "2"): {"masked_win": 1}}
    assert SR.probe_rows(rows, running, {}, knowledge_rows=kr)[0] == ("REENTRY_WINDOWED", 7, "RE_D", "2")


def test_route_row_policy_modes():
    H = ["F1=OFF", "F2=1h"]
    kr = {SR.canon_key("EXIT_VELOCITY", "HOT_SW", "True"): {"n": 3, "pos": 2, "promoted": 1}, SR.canon_key("EXIT_VELOCITY", "COLD_SW", "True"): {"n": 9, "pos": 0, "promoted": 0, "yellow_pos": 0}}
    every = 10 ** 9  # rotation slice practically never hits
    m, kept, dfr, meta = SR.route_row_policy(kr, "EXIT_VELOCITY", "COLD_SW", "True", H, 5, 1, every, exempt=False)
    assert (m, kept, dfr) == ("abbrev", [], H) and meta["why"].startswith("ROUTE_ABBREV")
    m, kept, dfr, meta = SR.route_row_policy(kr, "EXIT_VELOCITY", "HOT_SW", "True", H, 5, 1, every, exempt=False)
    assert (m, kept, dfr) == ("full", H, []) and meta["why"].startswith("ROUTE_HOT")
    m, kept, dfr, _ = SR.route_row_policy(kr, "EXIT_VELOCITY", "COLD_SW", "True", H, 5, 1, every, exempt=True)
    assert (m, kept, dfr) == ("full", H, [])
    masked = {SR.canon_key("EXIT_VELOCITY", "COLD_SW", "True")}
    m, kept, dfr, meta = SR.route_row_policy(kr, "EXIT_VELOCITY", "COLD_SW", "True", H, 5, 1, every, exempt=False, masked=masked)
    assert m == "full" and meta["why"] == "ROUTE_MASKED_PATH"
    m, kept, dfr, meta = SR.route_row_policy({}, "EXIT_VELOCITY", "NEW_SW", "1", H, 5, 1, 1, exempt=False)
    assert m == "full" and meta["why"].startswith("ROUTE_ROTATE")
    assert SR.route_row_policy({}, "T", "S", "1", [], 1, 1, every, False)[0] == "full"


def test_rotation_is_deterministic_and_about_one_in_every():
    hits = sum(SR.rotation_hit(r, "EXIT_VELOCITY", 7, 25) for r in range(3, 5003))
    assert hits == sum(SR.rotation_hit(r, "EXIT_VELOCITY", 7, 25) for r in range(3, 5003))
    assert 120 <= hits <= 280


def test_should_revive_only_binding_and_not_deeply_negative():
    assert not SR.should_revive(0.0, False)  # non-binding zero: switch+filter == filter alone
    assert not SR.should_revive(0.0, None)
    assert SR.should_revive(0.0, True)  # ledger moved, offsetting zero
    assert SR.should_revive(0.3, None)
    assert SR.should_revive(-0.4, True, revive_min=-1.0)
    assert not SR.should_revive(-3.0, True, revive_min=-1.0)
    assert SR.should_revive(-3.0, True, revive_min=-1.0, sacred=True)
    assert not SR.should_revive(None, True)


def _rec(sym="KMNOUSDT_SHORT", key="abc"):
    return {"symside": sym, "cat_side": SR.cat_side_of(sym), "result_key": key, "rows": {SR.canon_key("EXIT_VELOCITY", "A", "True"): {"delta": 0.5, "promoted": True, "pos_yellows": ["F1=OFF"]}, SR.canon_key("EXIT_VELOCITY", "B", "1"): {"delta": 0.0, "promoted": False, "pos_yellows": [], "masked": True, "masked_win": True}}, "filters": {"F9=1H": {"blocking": True, "helps": False, "unmask_win": True}}, "final_gain": 5.0, "bh": 3.0, "baseline_gain": 0.0, "stage_secs": {"round1": 60.0}}


def test_merge_record_idempotent_and_counts():
    k = SR.empty_knowledge()
    assert SR.merge_record(k, _rec())
    assert not SR.merge_record(k, _rec())  # same board twice = no double count
    assert SR.merge_record(k, _rec(key="def"))
    c = SR.knowledge_for(k, "CRYPTO_SHORT")
    a = c["rows"][SR.canon_key("EXIT_VELOCITY", "A", "True")]
    assert (a["n"], a["pos"], a["promoted"], a["yellow_pos"]) == (2, 2, 2, 2) and SR.row_is_hot(a)
    b = c["rows"][SR.canon_key("EXIT_VELOCITY", "B", "1")]
    assert (b["zero"], b["masked"], b["masked_win"]) == (2, 2, 2) and SR.row_is_hot(b)
    assert c["filters"]["F9=1H"]["blocking"] == 2 and c["filters"]["F1=OFF"]["yellow_pos"] == 2
    assert c["boards"]["n"] == 2 and c["boards"]["beat_bh"] == 2 and c["boards"]["stage_secs"]["round1"]["sum"] == 120.0


def test_knowledge_roundtrip_and_corrupt_fallback(tmp_path):
    p = tmp_path / "k.json"
    k = SR.empty_knowledge()
    SR.merge_record(k, _rec())
    SR.save_knowledge(k, p)
    assert SR.knowledge_for(SR.load_knowledge(p), "CRYPTO_SHORT")["boards"]["n"] == 1
    p.write_text("{not json")
    assert SR.load_knowledge(p)["cat_sides"] == {}


def test_record_from_progress_reads_done_route_and_endgame():
    prog = {"initial_baseline_gain": 0.0, "cumulative_gain": 4.0, "bh": 2.0, "cumulative_overrides": {"MOM3_FILTER_TF": "1h"},
            "done": {"EXIT_VELOCITY!44:WT_X=15m": {"delta": 0.7, "naked_delta": 0.2, "promoted": True, "yellows": {"MOM3_FILTER_TF=1h": 0.5, "Q=OFF": -0.1}},
                     "ENTRY_REVERSAL_BOUNCE!5:P=True": {"delta": 0.0, "promoted": False, "yellows": {}},
                     "ROUTE!1:ABL:X=False": {"delta": 1.0, "promoted": True}, "FOCUS!1:W": {"delta": 1.0}},
            "smart_route": {"masked_paths": [SR.canon_key("ENTRY_REVERSAL_BOUNCE", "P", "True")], "masked_wins": [SR.canon_key("ENTRY_REVERSAL_BOUNCE", "P", "True")], "strips": {"X=TRUE": {"blocking": True, "helps": False}}, "unmask_adopted": ["X=TRUE"], "stage_secs": {"ablation": 3.0}},
            "endgame": {"strip_gain": 1.0, "reapplied": [["MOM3_FILTER_TF", 2.0]]}}
    r = SR.record_from_progress("KMNOUSDT_SHORT", prog)
    assert set(r["rows"]) == {SR.canon_key("EXIT_VELOCITY", "WT_X", "15m"), SR.canon_key("ENTRY_REVERSAL_BOUNCE", "P", "True")}
    assert r["rows"][SR.canon_key("EXIT_VELOCITY", "WT_X", "15m")]["pos_yellows"] == ["MOM3_FILTER_TF=1h"]
    assert r["rows"][SR.canon_key("ENTRY_REVERSAL_BOUNCE", "P", "True")]["masked_win"] is True
    assert r["filters"]["X=TRUE"] == {"blocking": True, "helps": False, "unmask_win": True}
    assert r["filters"][SR.canon_filter("MOM3_FILTER_TF", "1h")]["reapply_pos"] is True
    assert r["stage_secs"] == {"ablation": 3.0} and r["final_gain"] == 4.0


BOARDS = sorted((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL").glob("KMNOUSDT_SHORT*_30d_matrix.xlsx"))


@pytest.mark.skipif(not BOARDS, reason="finished KMNOUSDT_SHORT board not present")
def test_mined_evidence_zero_rows_never_carry_positive_yellows():
    r = SR.record_from_workbook(str(BOARDS[0]))
    st = r["stats"]
    assert st["g_zero"] > 0.9 * st["g_numeric"]  # 2717/2861: the masked chain left almost every row non-binding
    assert st["zero_rows_with_pos_yellow"] == 0  # the evidence behind ROUTE_ABBREV + revive-on-binding
    assert st["yellow_cells"] > 100000 and st["g_pos"] < 50
    assert any(SR.row_is_hot(e) for e in r["rows"].values())


def test_mine_cli_roundtrip(tmp_path):
    if not BOARDS:
        pytest.skip("no finished board")
    k, l = tmp_path / "k.json", tmp_path / "l.jsonl"
    assert SR.main(["mine", "--xlsx", str(BOARDS[0]), "--progress", str(tmp_path / "none*.json"), "--knowledge", str(k), "--ledger", str(l)]) == 0
    assert SR.main(["mine", "--xlsx", str(BOARDS[0]), "--progress", str(tmp_path / "none*.json"), "--knowledge", str(k), "--ledger", str(l)]) == 0
    know = json.loads(k.read_text())
    assert len(know["boards"]) == 1 and len(l.read_text().splitlines()) == 1  # idempotent


def test_pilot_wiring_pins():
    assert '_ROUTE_ON = os.environ.get("V15_SMART_ROUTE", "1") == "1"' in SRC
    i_abl, i_loop = SRC.index("        _route_ablation_round()\n"), SRC.index("    for qi, (sname, rr, switch, cand) in enumerate(pending_queue):")
    assert i_abl < i_loop
    i_pin, i_focus = SRC.index("            _route_pinpoint_round()\n"), SRC.index("        _focus_round()\n")
    assert i_pin < i_focus
    assert 'list(v.get("route_deferred_filters") or [])' in SRC  # FOCUS earns the deferred cells of movers
    assert "_sr.route_row_policy(" in SRC and "_sr.should_revive(" in SRC and "_route_learn()" in SRC
    assert '"route_deferred_filters"' in SRC and "ROUTE!{len(proms)}" in SRC


def test_endgame_no_regress_guard_present():
    assert 'os.environ.get("V15_ENDGAME_NO_REGRESS", "1") == "1"' in SRC
    assert "float(ares[\"gain_pct\"]) < _pre_strip_gain - 1e-9" in SRC
    assert "if _eg_regress is None and ares is not None" in SRC


def test_route_deferred_rows_stay_settled_for_rule3():
    from tools.v15_row_guards import row_needs_recalc
    rec = {"delta": 0.0, "promoted": False, "reason": "", "complete": True, "policy": {"ps": 0}, "route_mode": "abbrev", "route_deferred_filters": ["F1=OFF", "F2=1h"]}
    assert row_needs_recalc(rec, key="EXIT_VELOCITY!5:COLD_SW=True") is None
    rec2 = {"delta": 1.0, "promoted": True, "promoted_how": "ablation_unmask_all", "reason": "", "vec_gain": 3.0, "cumulative_before": 2.0, "cumulative_after": 3.0, "complete": True, "policy": {"ps": 0}}
    assert row_needs_recalc(rec2, key="ROUTE!1:ABL:X=False") is None
