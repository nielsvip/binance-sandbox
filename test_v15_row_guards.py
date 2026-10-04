"""Unit tests for tools/v15_row_guards hollow-board scan (2026-10-03 hollow-fix).

A v15 row must never advance without its per-switch yellow filters calculated.
Fixtures below are real record shapes from the 3-day hollow run
(data/reports/lifecycle_pilot/RLCUSDT_SHORT_v14_progress.json, round
v15_run25_20261002): 656 SKIPPED_SAMPLING rows + tab-level-era evaluated rows,
all with complete=null, published as final.

Run: python test_v15_row_guards.py  (no NPZ, no network, no live process)."""
import tools.v15_row_guards as G


SPEC = {"cats": {"CRYPTO_SHORT": {"ENTRY_REVERSAL_BOUNCE": {"HTF4_CONF_FILTER_TF": {}, "UNRELATED_ZZ_QQ": {}}}}}


def test_policy_skip_flagged():
    rec = {"delta": None, "promoted": False, "reason": "SKIPPED_SAMPLING(pos_sym=0)", "yellows": {}, "possym": None, "complete": None, "npz": None}
    why = G.row_needs_recalc(rec, key="ENTRY_REVERSAL_BOUNCE!7:HTF4_CONF=False", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC)
    assert why is not None and why.startswith("policy-skip"), why


def test_structural_skip_stands():
    rec = {"delta": None, "promoted": False, "reason": "NOT_WIRED_VEC: no reachable vectorized read and the ledger never moved (v15_zero_audit)", "complete": None}
    assert G.row_needs_recalc(rec, key="ENTRY_REVERSAL_BOUNCE!3:EZ_MANAGE_THROTTLER_RATE=0", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC) is None
    rec2 = {"delta": None, "promoted": False, "reason": "TYPE_MISMATCH: WT_X expects bool, cand '1h'", "complete": True}
    assert G.row_needs_recalc(rec2, key="EXIT_VELOCITY!9:WT_X=1h", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC) is None


def test_tablevel_era_evaluated_row_flagged():
    rec = {"delta": None, "promoted": False, "reason": "", "yellows": {}, "yellow_reasons": {}, "naked_delta": None, "trades": None, "complete": None}
    why = G.row_needs_recalc(rec, key="ENTRY_REVERSAL_BOUNCE!5:HTF4_CONF=True", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC)
    assert why is not None and why.startswith("tablevel-era"), why


def test_no_overlap_row_stands():
    rec = {"delta": 0.5, "promoted": False, "reason": "", "yellows": {"A_B_C=1": 0.5}, "complete": True}
    assert G.row_needs_recalc(rec, key="ENTRY_REVERSAL_BOUNCE!5:ZZTOP_QQ=1", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC) is None


def test_stamped_clean_row_stands():
    rec = {"delta": 0.2, "promoted": False, "reason": "", "yellows": {"HTF4_CONF_FILTER_TF=4h": 0.2}, "complete": True, "policy": G.policy_stamp(False, False, "uw1")}
    assert G.row_needs_recalc(rec, key="ENTRY_REVERSAL_BOUNCE!5:HTF4_CONF=True", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC) is None


def test_sampled_cells_flagged_despite_complete():
    rec = {"delta": 0.1, "promoted": False, "reason": "", "yellows": {}, "complete": True, "sampled_out_filters": ["HTF4_CONF_FILTER_TF=4h"]}
    why = G.row_needs_recalc(rec, key="ENTRY_REVERSAL_BOUNCE!5:HTF4_CONF=True", cat_side="CRYPTO_SHORT", tablevel_spec=SPEC)
    assert why is not None and why.startswith("sampled-cells"), why


def test_incomplete_and_corrupt_flagged():
    assert G.row_needs_recalc({"complete": False}) == "incomplete-pending"
    assert G.row_needs_recalc(None) == "corrupt-record"
    assert G.row_needs_recalc("x") == "corrupt-record"


def test_scan_board_tally():
    done = {
        "ENTRY_REVERSAL_BOUNCE!7:HTF4_CONF=False": {"reason": "SKIPPED_SAMPLING(pos_sym=0)", "complete": None},
        "ENTRY_REVERSAL_BOUNCE!5:HTF4_CONF=True": {"reason": "", "yellows": {}, "complete": None},
        "ENTRY_REVERSAL_BOUNCE!3:EZ_MANAGE_THROTTLER_RATE=0": {"reason": "NOT_WIRED_VEC: audit", "complete": None},
    }
    out = G.scan_board_for_hollow(done, cat_side="CRYPTO_SHORT", tablevel_spec=SPEC)
    assert out["total"] == 3 and len(out["drop"]) == 2, out
    assert out["tally"].get("policy-skip") == 1 and out["tally"].get("tablevel-era") == 1, out


def test_token_rule_twin():
    assert G.yellow_want_tokens("HTF4_CONF", "HTF4_CONF_FILTER_TF") is True
    assert G.yellow_want_tokens("HTF4_CONF", "HTF4_CONFIRM_FILTER_TF") is False
    assert G.yellow_want_tokens("WT_LOWER_CROSS_EXIT_TF", "WT_CROSS_EXIT_APPLIES_TO_WINNERS") is True


def test_tried_settled():
    verdict = {"gain_pct": None, "valid": False, "invalid_reason": "override X=2.0 incompatible with bool field (rejected before eval)"}
    assert G.tried_settled(verdict, "") is True
    assert G.tried_settled({"gain_pct": 1.0, "valid": True}, "") is True
    assert G.tried_settled(None, "TIMEOUT 10s") is False
    assert G.tried_settled(None, "ERR boom") is False
    assert G.tried_settled(None, "") is False


def test_is_gate_casualty():
    gate = {"verdict": "IMPOSSIBLE", "impossible_reasons": ["RULE#3: 4 hollow/incomplete rows with uncalculated yellows — refusing publish"]}
    assert G.is_gate_casualty(gate) is True
    strategy = {"verdict": "IMPOSSIBLE", "impossible_reasons": ["365D: invalid:DD 38.9% >30% (vomit)", "365D: gain -31.3"]}
    assert G.is_gate_casualty(strategy) is False
    assert G.is_gate_casualty({"verdict": None}) is False
    assert G.is_gate_casualty(None) is False


def test_verdict_row_settles_composition():
    from v15_pilot import delta_vs_result
    res = {"gain_pct": None, "valid": False, "trades": 5, "invalid_reason": "override X=2.0 incompatible with bool field (rejected before eval)"}
    d, promotable, reason = delta_vs_result(res, 1.0)
    assert d is None and promotable is False and reason
    assert G.tried_settled(res, "") is True
    assert G.tried_settled(None, "TIMEOUT 10s") is False
    ok = {"gain_pct": 1.5, "valid": True, "trades": 40}
    assert delta_vs_result(ok, 1.0) == (0.5, True, "")


def test_npz_midrun_same_id_no_refuse():
    a = {"path": "p", "mtime_ns": 1, "size": 2, "n": 3, "ts_first": 4, "ts_last": 5, "md5": "aaa"}
    assert G.npz_changed(a, dict(a)) is False
    assert G.npz_changed(None, a) is False
    assert G.npz_changed(a, None) is False


def test_npz_midrun_swap_detected():
    a = {"path": "p", "mtime_ns": 1, "size": 2, "n": 3, "ts_first": 4, "ts_last": 5, "md5": "aaa"}
    for k, v in (("mtime_ns", 9), ("size", 9), ("n", 9), ("ts_last", 9), ("ts_first", 9), ("path", "q")):
        b = dict(a)
        b[k] = v
        assert G.npz_changed(a, b) is True, k
    b = dict(a)
    b["md5"] = "bbb"
    assert G.npz_changed(a, b) is False


if __name__ == "__main__":
    for _n, _f in sorted([(k, v) for k, v in globals().items() if k.startswith("test_")]):
        _f()
        print(f"PASS {_n}")
    print("ALL TESTS PASSED")
