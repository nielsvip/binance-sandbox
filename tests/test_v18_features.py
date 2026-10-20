"""tests for tools/v18_features.py (E8b): every template key featurised, unknown tokens listed, value buckets, cat_side context."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import v18_features as FT  # noqa: E402

TIDX = {"cat_side": "CRYPTO_LONG", "path": "fixture", "md5": "0" * 32,
        "tabs": {"EXIT_STRUCTURAL": {"rows": [[3, "DC_LOW_FROZEN_STOP_TF", "15m", True], [4, "DC_LOW_FROZEN_STOP_TF", "1h", False],
                                              [5, "BB_FROZEN_STOP_FIELD", "lower", True], [6, "STOP_LOSS_PCT", "2", True], [7, "STOP_LOSS_PCT", "4", False],
                                              [8, "STOP_LOSS_PCT", "1", False], [9, "CHANNEL_REENTRY_STOP_FIELD", "dc_high", True],
                                              [10, "ZORBLAX_QUUX_ENABLED", "True", True]],
                                     "yellows": ["EMA_9_21_FILTER_ENABLED=True", "DC_BREAK_FILTER_TF=4h", "DC_BREAK_FILTER_TF=OFF"]},
                 "ENTRY_REVERSAL_BOUNCE": {"rows": [[3, "WT_15M_BOUNCE_OPEN_ENABLED", "False", True]], "yellows": ["DC_BREAK_FILTER_TF=1h"]}},
        "switches": {"DC_LOW_FROZEN_STOP_TF": ["15m", "1h"], "BB_FROZEN_STOP_FIELD": ["lower"], "STOP_LOSS_PCT": ["2", "4", "1"],
                     "CHANNEL_REENTRY_STOP_FIELD": ["dc_high"], "ZORBLAX_QUUX_ENABLED": ["True"], "WT_15M_BOUNCE_OPEN_ENABLED": ["False"]},
        "defaults": {"DC_LOW_FROZEN_STOP_TF": "15m", "BB_FROZEN_STOP_FIELD": "lower", "STOP_LOSS_PCT": "2", "CHANNEL_REENTRY_STOP_FIELD": "dc_high",
                     "ZORBLAX_QUUX_ENABLED": "True", "WT_15M_BOUNCE_OPEN_ENABLED": "False"}}


def ctx(cs="CRYPTO_LONG"):
    return FT.Context(cs, tidx=dict(TIDX, cat_side=cs), bible={"STOP_LOSS_PCT": {"kind": "exit", "sides": ["LONG", "SHORT"]}},
                      deps={"masters": {"STOP_LOSS_PCT": ["STOP_LOSS_ENABLED"]}}, tfsem={"keys": {}}, fdict={"DC_BREAK_FILTER_TF": {"type": "TF"}})


def test_every_template_key_featurised_and_unknown_tokens_listed():
    doc = FT.build("CRYPTO_LONG", ctx=ctx())
    cov = doc["coverage"]
    assert cov["all_featurised"] and cov["settings"] == cov["settings_expected"] == 9
    assert cov["options"] == cov["options_expected"] == 4
    assert "ZORBLAX" in cov["unknown_tokens"] and "QUUX" in cov["unknown_tokens"]  # listed, never dropped
    feats = FT.features("ZORBLAX_QUUX_ENABLED", "True", ctx())
    assert ("S.tok:ZORBLAX", 0.5) in feats  # and still a feature


def test_attributes_family_tf_role_level():
    c = ctx()
    a = FT.name_attrs("DC_LOW_FROZEN_STOP_TF", c)
    assert a["family"] == ["dc"] and "stop" in a["role"] and a["level"] == ["low"] and a["tabs"] == ["EXIT_STRUCTURAL"] and "exit" in a["tab_roles"]
    a2 = FT.name_attrs("WT_15M_BOUNCE_OPEN_ENABLED", c)
    assert "wt" in a2["family"] and a2["tf"] == ["15m"] and "entry" in a2["tab_roles"]
    a3 = FT.name_attrs("STOP_LOSS_PCT", c)
    assert a3["parents"] == ["STOP_LOSS_ENABLED"] and "exit" in a3["role"]
    f = FT.name_attrs("DC_BREAK_FILTER_TF", c, is_filter=True)
    assert "filter" in f["role"] and set(f["tabs"]) == {"EXIT_STRUCTURAL", "ENTRY_REVERSAL_BOUNCE"} and f["ftype"] == "TF"


def test_value_buckets_numeric_quantile_and_out_of_span():
    c = ctx()
    b = FT.value_bucket("STOP_LOSS_PCT", "4", c)
    assert b["kind"] == "num" and b["q"] == 1.0 and b["rel"] == "above"
    b0 = FT.value_bucket("STOP_LOSS_PCT", "2", c)
    assert b0["q"] == 0.5 and b0["rel"] == "default"
    out = FT.value_bucket("STOP_LOSS_PCT", "5", c)
    assert out.get("out_of_span") and out["q"] > 1.0
    tf = FT.value_bucket("DC_LOW_FROZEN_STOP_TF", "1h", c)
    assert tf["kind"] == "tf" and tf["tfs"] == ["1h"] and tf["tf_rank"] == FT.TF_RANK["1h"]
    en = FT.value_bucket("CHANNEL_REENTRY_STOP_FIELD", "bb_basis", c)
    assert en["kind"] == "enum" and en["levels"] == ["basis"] and en["families"] == ["bb"]
    assert FT.value_bucket("BB_FROZEN_STOP_FIELD", "lower", c)["levels"] == ["low"]


def test_id_features_first_and_cat_side_context():
    f = FT.features("STOP_LOSS_PCT", "4", ctx())
    assert f[0] == ("S.sw:STOP_LOSS_PCT", 1.0) and f[1] == ("S.set:STOP_LOSS_PCT=4", 1.0)
    g = FT.features("DC_BREAK_FILTER_TF", "4h", ctx(), is_filter=True)
    assert g[0][0] == "F.fl:DC_BREAK_FILTER_TF" and g[1][0] == "F.fo:DC_BREAK_FILTER_TF=4h"
    assert any(x[0] == "S.vqx" for x in f)
    assert ctx("STOCKS_SHORT").side == "SHORT" and ctx("STOCKS_SHORT").venue == "STOCKS"


# ------------------------------------------------------------------ E8d: chain context encoding

def test_canon_value_one_spelling():
    assert FT.canon_value(True) == "True" and FT.canon_value("true") == "True" and FT.canon_value(False) == "False"
    assert FT.canon_value(1.0) == "1" and FT.canon_value("1.0") == "1" and FT.canon_value("2") == FT.canon_value(2)
    assert FT.canon_value(0.25) == "0.25" and FT.canon_value("15m") == "15m" and FT.canon_value(" OFF ") == "OFF"
    assert FT.canon_value(None) == "None"


def test_context_encoder_diff_vs_bold_and_filter_signature():
    enc = FT.ContextEncoder(ctx())
    full = {"STOP_LOSS_PCT": 2.0,                     # == bold "2" -> not context
            "DC_LOW_FROZEN_STOP_TF": "1h",            # switch differs from bold -> X.s:
            "DC_BREAK_FILTER_TF": "4h",               # yellow filter key without a bold, active -> X.f:
            "EMA_9_21_FILTER_ENABLED": False,         # filter without a bold, inactive -> not context
            "NOT_IN_TEMPLATE": 7, "ABLATION_DISABLE_X": True, "MODE": "x",
            "WT_15M_BOUNCE_OPEN_ENABLED": "Requires the open to bounce off the band"}  # description text, ignored + counted
    d = enc.diff(full)
    assert d == {"DC_LOW_FROZEN_STOP_TF": "1h", "DC_BREAK_FILTER_TF": "4h"}
    assert enc.ignored_values["WT_15M_BOUNCE_OPEN_ENABLED"] == 1
    f = enc.feats(d)
    assert f == ("X.f:DC_BREAK_FILTER_TF=4h", "X.s:DC_LOW_FROZEN_STOP_TF=1h")
    assert FT.ContextEncoder.filter_signature(f) == ("X.f:DC_BREAK_FILTER_TF=4h",)
    # promotions overlay: back to bold removes, a new value adds, '<absent>' (deleted key) = bold
    d2 = enc.overlay(d, {"DC_LOW_FROZEN_STOP_TF": "15m", "STOP_LOSS_PCT": "4", "DC_BREAK_FILTER_TF": "<absent>"})
    assert d2 == {"STOP_LOSS_PCT": "4"}
    assert enc.diff({}) == {} and enc.feats({}) == ()
