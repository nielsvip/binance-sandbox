"""V17 2026-10-13: the new switches must survive the go-live registrar and reach the live vec twins.

Dry-run only (never writes per_sym_store / books / ledger)."""
import inspect
import json

import switch_parity as sp
import tradier_vec_exact as tve

FX = {"MOM3": ["ADX_REGIME_FILTER_ENABLED"], "REENTRY": ["DELTA_REENTRY_FILTER_ENABLED"], "AUGMENT_SIGNAL": ["UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED"]}
SET = {
    "FILTER_EXEMPTIONS": FX,
    "AUGMENT_INTO_REENTRY_ENABLED": True,
    "REENTRY_AUGMENT_SIZE_MULT": 0.5,
    "REENTRY_AUGMENT_REQUIRE_HIGHER_LOW": False,
    "ENTRY_LH_TRIGGER_ENABLED": True,
    "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED": True,
}


def test_coerce_structured_dict_string_and_bad():
    assert sp.coerce_structured("FILTER_EXEMPTIONS", FX) == (True, FX)
    assert sp.coerce_structured("FILTER_EXEMPTIONS", json.dumps(FX)) == (True, FX)
    assert sp.coerce_structured("FILTER_EXEMPTIONS", {"MOM3": "A, B,A", "X": []}) == (True, {"MOM3": ["A", "B"]})
    assert sp.coerce_structured("FILTER_EXEMPTIONS", "")[1] == {}
    assert sp.coerce_structured("FILTER_EXEMPTIONS", 5)[0] is False
    assert sp.coerce_structured("FILTER_EXEMPTIONS", "{bad")[0] is False


def _reg(ss, ov):
    return sp.register_workbook_result(ss, dict(ov), {"n_promoted": 1}, dry_run=True, diff_only=True, require_qualified=False)


def test_dict_key_no_longer_refuses_whole_set_crypto_and_stocks():
    for ss in ("ADAUSDC_LONG", "AAPL_SHORT"):
        r = _reg(ss, SET)
        assert "gates passed" in str(r.get("reason")), r.get("reason")
        w = r["would_register"]
        assert w["FILTER_EXEMPTIONS"] == FX
        assert w["AUGMENT_INTO_REENTRY_ENABLED"] is True and w["REENTRY_AUGMENT_SIZE_MULT"] == 0.5
        assert w["ENTRY_LH_TRIGGER_ENABLED"] is True
        assert "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED" not in w  # global-only live kill switch never goes per-sym
        assert r["diff_only"]["global_only_dropped"] == ["AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED"]


def test_empty_exemptions_is_default_and_string_form_accepted():
    r = _reg("ADAUSDC_LONG", {"FILTER_EXEMPTIONS": {}})
    assert "nothing to register" in str(r.get("reason")), r.get("reason")
    r = _reg("ADAUSDC_LONG", {"FILTER_EXEMPTIONS": json.dumps(FX)})
    assert r["would_register"]["FILTER_EXEMPTIONS"] == FX
    r = _reg("ADAUSDC_LONG", {"FILTER_EXEMPTIONS": 7, "AUGMENT_INTO_REENTRY_ENABLED": True})
    assert "type-gate refused ALL" in str(r.get("reason"))  # a malformed value still refuses (fail-closed)


def test_tradier_oracle_forwards_carry_and_live_action_divides_it_out():
    assert "carry_aug_qty" in inspect.getsource(tve.VecExactOracle.decide)
    assert tve.live_action_for({"type": "OPEN", "qty": 15.0, "carry_aug_qty": 5.0}, 0.0) == ("OPEN", 10.0)
    assert tve.live_action_for({"type": "OPEN", "qty": 15.0}, 0.0) == ("OPEN", 15.0)
