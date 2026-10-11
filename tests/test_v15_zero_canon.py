"""Canonical cross-run identity + zero/same-delta detector (USER 2026-10-11).

Row numbers shift daily BY OBLIGATION — identity is ONLY TAB!SWITCH=value
(canonical: 2.0==2, TRUE==True, OFF==off). Detector flags multiple same/0
deltas (red flag + red cell + red tab) without touching the hot loop.
"""
import v15_pilot as P


def test_canon_val_bool():
    assert P._canon_val(True) == "TRUE"
    assert P._canon_val(False) == "FALSE"
    assert P._canon_val("true") == "TRUE"
    assert P._canon_val("FALSE") == "FALSE"


def test_canon_val_numeric():
    assert P._canon_val(2.0) == "2"
    assert P._canon_val("2") == "2"
    assert P._canon_val("0.10") == "0.1"
    assert P._canon_val(0.5) == "0.5"
    assert P._canon_val("999999") == "999999"


def test_canon_val_strings_preserved():
    assert P._canon_val("OFF") == "OFF"
    assert P._canon_val("off") == "OFF"
    assert P._canon_val("15m") == "15m"
    assert P._canon_val("1h_4h") == "1h_4h"


def test_canon_key_row_independent():
    a = P._canon_key_of_done("REENTRY_WINDOWED!7:REENTRY_FILTER_MIN_PASS=3")
    b = P._canon_key_of_done("REENTRY_WINDOWED!42:REENTRY_FILTER_MIN_PASS=3.0")
    assert a == b == "REENTRY_WINDOWED!REENTRY_FILTER_MIN_PASS=3"
    assert P._canon_key_of_done("garbage-no-bang") is None


def test_lookup_exact_first():
    done = {"T!7:SW=True": {"delta": 1.0}}
    rec, key = P._done_canon_lookup(done, "T", "SW", True, row=7)
    assert key == "T!7:SW=True" and rec["delta"] == 1.0


def test_lookup_moved_row():
    done = {"T!7:SW=2.0": {"delta": 5.0}}  # template moved SW to row 42 + reformatted
    rec, key = P._done_canon_lookup(done, "T", "SW", 2, row=42)
    assert key == "T!7:SW=2.0" and rec["delta"] == 5.0


def test_lookup_dup_prefers_embedded_row():
    done = {"T!35:SW=2.0": {"delta": 1.0}, "T!37:SW=2": {"delta": 2.0}}
    rec, key = P._done_canon_lookup(done, "T", "SW", 2, row=37)
    assert key == "T!37:SW=2" and rec["delta"] == 2.0
    rec, key = P._done_canon_lookup(done, "T", "SW", 2, row=99)
    assert key == "T!35:SW=2.0"  # deterministic first-sorted fallback


def test_lookup_miss():
    rec, key = P._done_canon_lookup({"T!7:SW=True": {}}, "T", "OTHER", True, row=7)
    assert (rec, key) == (None, None)
    assert P._done_canon_lookup({}, "T", "SW", 1) == (None, None)


def _ord(vals, tab="T", sw="SW"):
    return [(f"{tab}!{sw}={i}", v) for i, v in enumerate(vals)]


def test_zero_dead_knob():
    by = {"MAX_AUG": [(f"T!MAX_AUG={v}", 0.0) for v in ("0", "1", "2", "5", "10")]}
    a = P._zero_rules_eval("T", _ord([0.0] * 5), by)
    assert [x["rule"] for x in a] == ["DEAD_KNOB"]


def test_zero_dead_knob_needs_3():
    by = {"SW": [("T!SW=0", 0.0), ("T!SW=1", 0.0)]}
    assert P._zero_rules_eval("T", _ord([0.0, 1.5]), by) == []


def test_zero_same_run():
    vals = [1.0] + [2.5] * 6 + [3.0]
    a = P._zero_rules_eval("T", _ord(vals), {})
    assert "SAME_RUN" in [x["rule"] for x in a]


def test_zero_same_run_needs_6():
    assert P._zero_rules_eval("T", _ord([2.5] * 5 + [1.0]), {}) == []


def test_zero_tab():
    vals = [0.0] * 18 + [1.0, 2.0]
    a = P._zero_rules_eval("T", _ord(vals), {})
    assert "ZERO_TAB" in [x["rule"] for x in a]


def test_zero_tab_needs_20():
    a = P._zero_rules_eval("T", _ord([0.0] * 19), {})
    assert "ZERO_TAB" not in [x["rule"] for x in a]  # SAME_RUN still fires: 19 identical is flaggable
    assert "SAME_RUN" in [x["rule"] for x in a]


def test_zero_varied_clean():
    vals = [round(0.5 * i - 3.0, 2) for i in range(25)]
    assert P._zero_rules_eval("T", _ord(vals), {}) == []


def test_zero_skip_knob():
    sk = {"UNWIRED_SW": ["T!UNWIRED_SW=0", "T!UNWIRED_SW=1", "T!UNWIRED_SW=2"]}
    a = P._zero_rules_eval("T", _ord([1.0, 2.0]), {}, sk)
    assert [x["rule"] for x in a] == ["SKIP_KNOB"]


def test_zero_skip_knob_needs_3():
    sk = {"SW": ["T!SW=0", "T!SW=1"]}
    assert P._zero_rules_eval("T", _ord([1.0, 2.0]), {}, sk) == []


def test_rec_measured():
    assert P._rec_measured({"vec_gain": 0.0, "trades": 0}) is True
    assert P._rec_measured({"vec_gain": None, "trades": None, "yellows": {"F=1": 2.0}}) is True
    assert P._rec_measured({"naked_delta": -1.0}) is True
    assert P._rec_measured({"delta": 0.0, "reason": "DEAD_VEC_PATH: x", "vec_gain": None, "trades": None, "yellows": {}}) is False
    assert P._rec_measured({}) is False
    assert P._rec_measured(None) is False


def test_bh_half_red():
    a = P._zero_bh_eval(20.04, 45.13)
    assert a is not None and a["rule"] == "BH_HALF"


def test_bh_half_boundary():
    assert P._zero_bh_eval(38.145, 76.29) is None  # exactly 0.5x = not red
    assert P._zero_bh_eval(38.14, 76.29)["rule"] == "BH_HALF"
    assert P._zero_bh_eval(42.38, 76.29) is None


def test_bh_nonpositive():
    assert P._zero_bh_eval(-1.0, 0.0)["rule"] == "BH_HALF"
    assert P._zero_bh_eval(2.0, -5.0) is None
    assert P._zero_bh_eval("x", None) is None
