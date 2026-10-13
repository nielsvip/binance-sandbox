"""V17 2026-10-13: FILTER_EXEMPTIONS parser/merge + AUGMENT_INTO_REENTRY live sizing (crypto vec_exact, stocks tradier_vec_exact).

Engine-level proofs (baseline neutrality on real NPZ, effect runs) live in tools/v17_engine_ab.py (worker NPZ); these tests
cover the pure helpers and the live sizing gates (default OFF = byte-identical live sizing).
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import vec_decisions.filter_exemptions as FX  # noqa: E402


def test_parse_empty_and_garbage_is_noop():
    assert FX.parse({}) == {}
    assert FX.parse(None) == {}
    assert FX.parse("x") == {}
    assert FX.parse({"MOM3": [], "REENTRY": [], "AUGMENT": []}) == {}
    assert FX.parse({"REENTRY": ["NOT_A_FILTER_KEY"], "AUGMENT_SIGNAL": ["MIN_GAIN_TO_BUY_AGGRESSIVELY"]}) == {}


def test_parse_kinds():
    some_filter = sorted(FX.FILTER_KEYS)[0]
    m = FX.parse({"MOM3": [some_filter], "REENTRY": some_filter, "AUGMENT_SIGNAL": ["UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED"], "AUGMENT": ["HTF_GATE_APPLY_TO_AUGMENT"]})
    assert m["ENTRY"] == {"MOM3": [some_filter]}
    assert m["REENTRY"] == [some_filter]
    assert m["AUGMENT"]["SIGNAL"] == {"UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED"}
    assert m["AUGMENT"]["*"] == {"HTF_GATE_APPLY_TO_AUGMENT"}


def test_validate_reports_ignored_pairs():
    bad = FX.validate({"REENTRY": ["NOPE"], "AUGMENT_LADDER": ["NOPE2"]})
    assert {b[1] for b in bad} == {"NOPE", "NOPE2"}


def test_aug_exempt_any_active_source():
    fx = {"SIGNAL": {"UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED"}}
    assert FX.aug_exempt(fx, ("SIGNAL",), "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED")
    assert not FX.aug_exempt(fx, ("LADDER",), "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED")
    assert not FX.aug_exempt({}, ("SIGNAL",), "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED")
    assert FX.aug_exempt({"*": {"HTF_GATE_APPLY_TO_AUGMENT"}}, ("LADDER",), "HTF_GATE_APPLY_TO_AUGMENT")
    assert not FX.aug_exempt({"*": {"HTF_GATE_APPLY_TO_AUGMENT"}}, (), "HTF_GATE_APPLY_TO_AUGMENT")


def test_merge_neutral_and_selection():
    sel = np.array([True, False, True, False])
    a = np.array([True, True, False, False])
    b = np.array([False, False, True, True])
    assert FX.merge(None, None, sel, 4, False) is None
    assert FX.merge(a, b, sel, 4, False).tolist() == [False, True, True, False]
    assert FX.merge(None, b, sel, 4, False).tolist() == [False, False, True, False]
    assert FX.merge(a, None, sel, 4, True).tolist() == [True, True, True, False]


def test_pass_b_cfg_forces_off_and_never_touches_original():
    class C:
        A_FILTER = True
        B_FILTER_TF = "1h"
        THR = 2.0
        FILTER_EXEMPTIONS = {"X": ["A_FILTER"]}
        ENTRY_ISOLATE_FAMILY = ""

    c = C()
    c2 = FX.pass_b_cfg(c, ["A_FILTER", "B_FILTER_TF", "THR"], isolate="MOM3")
    assert c2.A_FILTER is False and c2.B_FILTER_TF == "OFF" and c2.THR == 2.0
    assert c2.FILTER_EXEMPTIONS == {} and c2.ENTRY_ISOLATE_FAMILY == "MOM3" and c2._FX_PROBE_SINK == {}
    assert c.A_FILTER is True and c.B_FILTER_TF == "1h" and c.FILTER_EXEMPTIONS == {"X": ["A_FILTER"]}


def test_vec_exact_live_sizing_carry_gated_by_live_switch():
    from live_twins import vec_exact as VX

    class Cfg:
        PARITY_VEC_EXACT_LIVE_SIZING = True
        START_POSITION_SIZE = 50.0
        AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED = False

    q0, ov0 = VX.live_sizing_args("OPEN", 1.0, 10.0, Cfg())
    q1, _ = VX.live_sizing_args("OPEN", 1.0, 10.0, Cfg(), carry_mult=1.0)
    assert (q0, ov0) == (5.0, None) and q1 == 5.0  # live switch OFF -> carry ignored (byte-identical sizing)
    Cfg.AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED = True
    q2, ov2 = VX.live_sizing_args("OPEN", 1.0, 10.0, Cfg(), carry_mult=1.0)
    assert q2 == 10.0 and ov2 is None
    q3, _ = VX.live_sizing_args("AUGMENT", 1.0, 10.0, Cfg(), carry_mult=1.0)
    assert q3 == 5.0  # carry applies to OPEN only
    q4, ov4 = VX.live_sizing_args("CLOSE", 3.0, 10.0, Cfg(), carry_mult=1.0)
    assert (q4, ov4) == (3.0, 3.0)


def test_tradier_vec_exact_open_carry_divided_out_when_live_off(monkeypatch):
    import tradier_vec_exact as TVE

    ev = {"type": "OPEN", "qty": 20.0, "carry_aug_qty": 10.0, "reason": "X"}
    monkeypatch.setattr(TVE, "_carry_live_on", lambda: False)
    assert TVE.live_action_for(ev, 0.0) == ("OPEN", 10.0)
    monkeypatch.setattr(TVE, "_carry_live_on", lambda: True)
    assert TVE.live_action_for(ev, 0.0) == ("OPEN", 20.0)
    assert TVE.live_action_for({"type": "OPEN", "qty": 7.0, "reason": "Y"}, 0.0) == ("OPEN", 7.0)
    assert TVE.live_action_for(ev, 5.0) is None


def test_config_defaults_are_off():
    from config import Config
    from config_tradier import TradierConfig

    for c in (Config(), TradierConfig()):
        assert c.FILTER_EXEMPTIONS == {}
        assert c.AUGMENT_INTO_REENTRY_ENABLED is False
        assert c.AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED is False


def test_quickconfig_defaults_are_off():
    import v12_quick_engine as V

    q = V.QuickConfig()
    assert q.FILTER_EXEMPTIONS == {}
    assert q.AUGMENT_INTO_REENTRY_ENABLED is False
    assert q.REENTRY_AUGMENT_SIZE_MULT == 1.0
    assert q.REENTRY_AUGMENT_REQUIRE_HIGHER_LOW is True
