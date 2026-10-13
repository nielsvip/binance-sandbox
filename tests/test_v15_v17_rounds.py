"""V17 ROUNDS (USER 2026-10-13, bible §95): P1 filter probe -> P2 abbreviated worst_first (naked every row, yellows
DEFERRED_V17 = settled) -> P3 pinpoint (moved-chain retest, ablation route per filter category, focus) -> P4 trimmed tail.
Pure helpers are unit-tested; the wiring inside _spec_fill_workbook is pinned statically (the pilot runs on the fleet)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402
from tools.v15_row_guards import row_needs_recalc, scan_board_for_hollow  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_kill_switch_env_and_default():
    assert 'os.environ.get("V15_V17_ROUNDS", ' in SRC
    assert isinstance(P.V17_ON, bool)


def test_filter_key_classification():
    for k in ("MOM3_FILTER_TF", "HTF_DIRECTION_GATE_ENABLED", "COUNTER_TREND_ADD_BLOCK_ENABLED", "EXIT_VELOCITY_WT_MIN_TFS", "STOCH_K_ENTRY_VETO_ENABLED"):
        assert P.v17_is_filter_key(k), k
    for k in ("GOLDEN_RULE_BASE_USD", "DAYTRADE_DC_TARGET_TF", "REENTRY_WT15M_SIZE_MULT"):
        assert not P.v17_is_filter_key(k), k


def test_filter_off_value():
    assert P.v17_filter_off_value(True) is False
    assert P.v17_filter_off_value("True") is False
    assert P.v17_filter_off_value("1h", ["15m", "OFF", "4h"]) == "OFF"
    assert P.v17_filter_off_value(3, ["2", "4"]) is None  # no honest off value -> never stripped


def test_category_of_tab():
    assert P.v17_category_of_tab("ENTRY_BREAKOUT_CHANNEL") == "entry"
    assert P.v17_category_of_tab("REENTRY_ADAPTIVE") == "reentry"
    assert P.v17_category_of_tab("AUGMENT_TREND") == "augment"
    assert P.v17_category_of_tab("REDUCE_PROFIT_LOCK") == "exit"
    assert P.v17_category_of_tab("GLOBAL_RISK_GATES") is None


def test_top_filters_evidence_then_fallback():
    prior = {"filter_opt": {"X_FILTER_TF=OFF": {"pos_sym": 5, "promoted_sym": 3, "sum_pos_delta": 50.0}, "DEAD_FILTER_TF=1h": {"pos_sym": 0, "promoted_sym": 0, "sum_pos_delta": 0.0}, "GOLDEN_RULE_BASE_USD=5": {"pos_sym": 9, "promoted_sym": 9, "sum_pos_delta": 99.0}}}
    out = P.v17_top_filters(prior, {}, 5)
    assert out[0] == "X_FILTER_TF"
    assert "DEAD_FILTER_TF" not in out and "GOLDEN_RULE_BASE_USD" not in out
    assert len(out) == 5 and out[1:] == list(P.V17_FILTER_FALLBACK[:4])
    assert P.v17_top_filters({}, {}, 15) == list(P.V17_FILTER_FALLBACK)


def test_tab_order_measured_then_fallback():
    tabs = ["ENTRY_REVERSAL_BOUNCE", "EXIT_VELOCITY", "AUGMENT_TREND"]
    assert P.v17_tab_order(tabs, {}) == ["EXIT_VELOCITY", "ENTRY_REVERSAL_BOUNCE", "AUGMENT_TREND"]
    prior = {"switch_cand": {"A=1": {"sum_pos_delta": 10.0, "total_secs": 1.0}, "B=1": {"sum_pos_delta": 1.0, "total_secs": 10.0}}}
    keys = {"AUGMENT_TREND": ["A=1"], "EXIT_VELOCITY": ["B=1"], "ENTRY_REVERSAL_BOUNCE": []}
    out = P.v17_tab_order(tabs, prior, keys)
    assert out[0] == "AUGMENT_TREND" and out[1] == "EXIT_VELOCITY" and set(out) == set(tabs)


def test_killers_attribution():
    assert P.v17_killers(2.0, {"F1": -0.5, "F2": 1.9, "F3": 0.4, "F4": None}) == ["F1", "F3"]
    assert P.v17_killers(0.0, {"F1": -1.0}) == []
    assert P.v17_killers(1.0, {}, 0.5) == []


def test_ev_ranking_sources():
    assert P.v17_ev("A=1", {"A=1": {"mean_delta": 2.0, "pos_rate": 0.5}}) == 1.0
    assert P.v17_ev("A=1", {"A=1": 3.0}) == 3.0
    assert P.v17_ev("A=1", {}, {"switch_cand": {"A=1": {"mean_delta": 1.0, "pos_sym": 2, "n_sym": 4}}}) == 0.5
    assert P.v17_ev("A=1", {}, {}) == 0.0


def test_p3_budget_guaranteed_minimum_never_starved():
    # a long main pass can no longer starve the pinpoint round (A/B 2026-10-13: 3 of 4 boards got 0 s)
    assert P.v17_p3_budget("WINNER", 1400.0, 1500, 210) == 750.0
    assert P.v17_p3_budget("LOSER", 5000.0, 1500, 210) == 375.0
    assert P.v17_p3_budget("LOST_CAUSE", 5000.0, 1500, 210) == 150.0
    assert P.v17_p3_budget("WINNER", 0.0, 1500, 210) == 900.0  # ahead of target -> more, capped by V15_V17_P3_MAX_S
    assert "V15_V17_MIN_LIFT_PM" in SRC and "V15_V17_P2_YELLOW_S" in SRC


def test_deferred_v17_rows_are_settled_not_hollow():
    rec = {"delta": -0.4, "naked_delta": -0.4, "promoted": False, "reason": "", "complete": True, "policy": {"ps": 0, "tl": 0, "uw": None, "pilot": "x"}, "v17_deferred_filters": ["MOM3_FILTER_TF=OFF", "DC_BREAK_FILTER_TF=1h"], "yellows": {}}
    assert row_needs_recalc(rec, key="EXIT_VELOCITY!5:X=1") is None
    v17_rec = {"delta": 1.2, "promoted": True, "reason": "", "complete": True, "policy": {"ps": 0}, "vec_gain": 5.2, "cumulative_before": 4.0, "cumulative_after": 5.2, "v17_phase": "ABL"}
    sc = scan_board_for_hollow({"EXIT_VELOCITY!5:X=1": rec, "ABL!entry:BB_BOUNCE_ENTRY_TF=1h": v17_rec, "V17_PROBE!1:MOM3_FILTER_TF=OFF": v17_rec})
    assert sc["drop"] == []


def test_wiring_pins():
    # P2 deferral in _row_static, done record + note, sacred repass replaced, pinpoint replaces the plain focus call
    assert 'info["v17_deferred"] = _v17_def' in SRC
    assert 'progress["done"][key]["v17_deferred_filters"] = list(st["v17_deferred"])' in SRC
    assert 'V15_V17_SACRED_REPASS' in SRC
    assert "            _v17_pinpoint()\n        else:\n            _focus_round()" in SRC
    # order: probe before the main loop; pinpoint after; FFR restricted; post-stages capped before endgame
    assert SRC.index("            _v17_filter_probe()") < SRC.index("    for qi, (sname, rr, switch, cand) in enumerate(pending_queue):")
    assert SRC.index("_v17_cap_post()\n") < SRC.index("_endgame_filter_cycle()\n", SRC.index("_v17_cap_post()\n"))
    assert "str(sw2).strip() not in _v17_touched" in SRC
    # ablation route stages + resume-safe done keys + phase log line
    for tok in ("ABL_STRIP:", "ABL_DISCOVER:", "ABL_RESTORE1:", "ABL_ATTR:", "ABL_RESTORE:", "ABL_EXEMPT:", "ABL_PROXY:", "[ABLATION-NO-EXEMPT]", 'f"ABL!{cat}:', "[V17-PHASE]", '"V17_ROUNDS"'):
        assert tok in SRC, tok
    # every promotion re-uses the exact promoted set's own eval (greedy re-eval vs the latest chain)
    assert "_v17_promote(choice, how, prefix, ph)" in SRC


def test_yellow_frequency_rule():
    # USER 2026-10-13: p = max(1/200, min(1, pos_sym/pos_top)); unevidenced = 1 (fail-open); deterministic draw
    prior = {"filter_opt": {"A=1": {"pos_sym": 0, "n_sym": 50}, "B=1": {"pos_sym": 4, "n_sym": 50}, "C=1": {"pos_sym": 2, "n_sym": 50}, "D=1": {"pos_sym": 9, "n_sym": 3}}}
    top = P.v17_pos_top(prior, 10, 0.9)
    assert top == 4.0
    assert P.v17_cell_p(prior["filter_opt"]["A=1"], top) == 0.005
    assert P.v17_cell_p(prior["filter_opt"]["B=1"], top) == 1.0
    assert P.v17_cell_p(prior["filter_opt"]["C=1"], top) == 0.5
    assert P.v17_cell_p(prior["filter_opt"]["D=1"], top) == 1.0  # n < min -> unevidenced -> always
    assert P.v17_cell_p(None, top) == 1.0
    draws = [P.v17_cell_keep("CRYPTO_LONG", "EXIT_VELOCITY", "X", 1, "A=1", seq, 0.005) for seq in range(4000)]
    assert 5 <= sum(draws) <= 40  # ~1 in 200 over rounds
    assert P.v17_cell_keep("CRYPTO_LONG", "T", "X", 1, "A=1", 7, 0.3) == P.v17_cell_keep("CRYPTO_LONG", "T", "X", 1, "A=1", 7, 0.3)
    assert "V15_POSSYM_P_SAMPLING" in SRC and "[YELLOW-SAMPLE]" in SRC


def test_exemption_merge_and_tuple_ev():
    m = P.v17_merge_exemptions({"AUGMENT_AT_LOSS": ["F1"]}, {"AUGMENT_AT_LOSS": ["F2", "F1"], "B12": ["WT_DIV"]})
    assert m == {"AUGMENT_AT_LOSS": ["F1", "F2"], "B12": ["WT_DIV"]}
    assert P.v17_ev("A=1", {"A=1": (10, 2.0, 0.25)}) == 0.5


def test_filter_exemptions_final_set_round_trip():
    # a promoted exemption lands in the final override set in the engine's exact dict form and survives the progress /
    # publish JSON round trip unchanged (same sanitized set, same eval-cache key => identical evaluation)
    import json as _j
    ov = {"WT_DIV_ENTRY_GATE_ENABLED": True, "FILTER_EXEMPTIONS": P.v17_merge_exemptions({"B12": ["WT_DIV_ENTRY_GATE"]}, {"AUGMENT_AT_LOSS": ["AUGMENT_ONLY_WHEN_PROFITABLE"]})}
    s1, _ = P.sanitize_overrides(dict(ov), {})
    assert s1["FILTER_EXEMPTIONS"] == {"B12": ["WT_DIV_ENTRY_GATE"], "AUGMENT_AT_LOSS": ["AUGMENT_ONLY_WHEN_PROFITABLE"]}
    rt = _j.loads(_j.dumps({"final": {"overrides": s1}}))["final"]["overrides"]
    s2, _ = P.sanitize_overrides(rt, {})
    assert s2 == s1
    key = lambda o: tuple(sorted((k, str(v)) for k, v in o.items()))
    assert key(s1) == key(s2)
    assert "FILTER_EXEMPTIONS" in P.known_config_fields()


def test_filter_exemptions_string_and_global_only_key():
    s, w = P.sanitize_overrides({"FILTER_EXEMPTIONS": '{"B12": ["WT_DIV_ENTRY_GATE"]}', "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED": True, "X": "1"}, {})
    assert s["FILTER_EXEMPTIONS"] == {"B12": ["WT_DIV_ENTRY_GATE"]}
    assert "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED" not in s and "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED" in w
    s2, _ = P.sanitize_overrides({"FILTER_EXEMPTIONS": "{'A': ['F']}"}, {})
    assert s2["FILTER_EXEMPTIONS"] == {"A": ["F"]}
    s3, w3 = P.sanitize_overrides({"FILTER_EXEMPTIONS": "garbage{"}, {})
    assert "FILTER_EXEMPTIONS" not in s3 and "FILTER_EXEMPTIONS" in w3
    assert 'str(switch).strip() in GLOBAL_ONLY_KEYS' in SRC


def test_surgeon_wiring():
    # USER 2026-10-13: pinpoint = per-board surgeon (own ledger diagnosis -> symptom paths -> greedy verify -> re-diagnose)
    for tok in ("def _v17_diagnose():", "_TA.classify(", "_TA.missed_moves(", "def _v17_surgeon_paths(", "def _v17_surgeon(", '"SURGEON"', "[SURGEON]", "P3_SURGEON_", 'progress_key_unused' if False else '_v17.setdefault("surgeon", {})'):
        assert tok in SRC, tok
    assert SRC.index("_v17_surgeon(_t.time()") < SRC.index("_focus_round()\n                    _v17_phase_end(ph, budget_s=")


def test_switch_types_validator_wired():
    assert P._parse_opt_value("0.5", 3) == "0.5"  # no silent 0.5 -> 0
    assert P._parse_opt_value("2.0", 3) == 2
    ok, why = P._cand_compatible("EXIT_VELOCITY_WT_MIN_TFS", "D", {})
    assert not ok and why.startswith("TYPE_MISMATCH")
    assert P._switch_type_violation("EXIT_VELOCITY_WT_MIN_TFS", "D", {}).startswith("TYPE_MISMATCH")
    assert P._switch_type_violation("DAYTRADE_DC_TARGET_TF", "1h", {}) == ""
    import unittest.mock as _m
    with _m.patch("tools.switch_types.coerce", side_effect=RuntimeError("down")):
        assert P._types_coerce_reason("X", 1).startswith("TYPE_MISMATCH")  # fail closed


def test_regime_wiring():
    for tok in ("def _v17_regime():", "def _v17_recent_eval(", "def _v17_regime_pick(", "OLD_REGIME_ONLY", "old_set_recent", '_v17["regime"] = out', "[REGIME]", "pick=_v17_regime_pick"):
        assert tok in SRC, tok


def test_combo_engine_wiring_fail_open():
    for tok in ("def _v17_combo_search(", "def _v17_combo_append(", "import tools.v17_combo_engine as _ce", "_v17_combo_append(first)", "SURGEON:combo:"):
        assert tok in SRC, tok


def test_regime365_step_wiring():
    for tok in ("def _v17_regime365(", "V15_V17_365_S", "V15_V17_365_TOL_30D", '_v17.setdefault("regime365"', "[REGIME365]", "REGIME365 regime", "_v17_regime365()  # own hard time box"):
        assert tok in SRC, tok
    i = SRC.index("    def _v17_regime365(")
    body = SRC[i:SRC.index("    def _v17_sheet():", i)]
    assert "needs_redo" not in body and "quarantine(" not in body  # never REDO, never quarantine
