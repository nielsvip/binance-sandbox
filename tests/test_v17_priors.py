import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import v17_priors
from tools import v17_priors_build as B

DOC = {
    "meta": {"schema_version": 1, "rows_total": 10},
    "CRYPTO_LONG": {
        "single": {
            "A_ENABLED=False": {"n": 20, "pos": 10, "neg": 2, "mean": 1.0, "verdict": "USEFUL"},
            "B_TF=D": {"n": 20, "pos": 0, "neg": 12, "mean": -3.0, "verdict": "HARMFUL"},
            "C_MAX=2.0": {"n": 20, "pos": 5, "neg": 0, "mean": 0.2, "verdict": "USEFUL"},
        },
        "blocks": [{"switch": "S_ENABLED=True", "switch_key": "S_ENABLED", "filter": "F_FILTER_TF", "filter_on_value": "4h", "n": 5, "pos_sym": 3,
                    "mean_unlock": 1.2, "exemption_override": {"FILTER_EXEMPTIONS": {"S": ["F_FILTER_TF"]}}, "has_exemption_key": True}],
        "synergy": [], "anti_synergy": [], "deprioritise": [{"key": "B_TF=D", "verdict": "HARMFUL"}], "learner_conflicts": [],
    },
}


def _write(tmp_path):
    p = tmp_path / "v17_priors.json"
    p.write_text(json.dumps(DOC))
    return p


def test_load_and_missing_cat_side(tmp_path):
    p = _write(tmp_path)
    d = v17_priors.load("CRYPTO_LONG", p)
    assert "A_ENABLED=False" in d["single"]
    e = v17_priors.load("STOCKS_SHORT", p)
    assert e["single"] == {} and e["blocks"] == []


def test_missing_file_fails_open(tmp_path):
    d = v17_priors.load("CRYPTO_LONG", tmp_path / "nope.json")
    assert d["single"] == {}
    assert v17_priors.p_eval("CRYPTO_LONG", "X=1", tmp_path / "nope.json") == 1.0


def test_normalized_lookup(tmp_path):
    p = _write(tmp_path)
    assert v17_priors.single("CRYPTO_LONG", "C_MAX=2", p)["pos"] == 5
    assert v17_priors.single("CRYPTO_LONG", "A_ENABLED=false", p)["pos"] == 10


def test_p_eval_range(tmp_path):
    p = _write(tmp_path)
    assert v17_priors.p_eval("CRYPTO_LONG", "A_ENABLED=False", p) == 1.0
    assert abs(v17_priors.p_eval("CRYPTO_LONG", "B_TF=D", p) - 1 / 200) < 1e-12
    mid = v17_priors.p_eval("CRYPTO_LONG", "C_MAX=2.0", p)
    assert 1 / 200 < mid < 1.0
    assert v17_priors.p_eval("CRYPTO_LONG", "UNSEEN=1", p) == 1.0


def test_blocks_and_exemptions(tmp_path):
    p = _write(tmp_path)
    ex = v17_priors.exemptions_for("CRYPTO_LONG", "S_ENABLED", p)
    assert ex and ex[0]["exemption_override"] == {"FILTER_EXEMPTIONS": {"S": ["F_FILTER_TF"]}}
    assert v17_priors.is_deprioritised("CRYPTO_LONG", "B_TF=D", p)
    assert not v17_priors.is_deprioritised("CRYPTO_LONG", "A_ENABLED=False", p)


def test_build_single_per_sym_then_across():
    rows = [
        ["CRYPTO_LONG", "K=1", "AUSDT_LONG", [2, 4.0, 2.0, 1.0, 0.0, 0.0, 0, 0.0, 2, 4.0, 0]],
        ["CRYPTO_LONG", "K=1", "BUSDT_LONG", [1, -1.0, 0.0, 0.0, 0.0, 0.0, 1, -1.0, 0, 0.0, 0]],
        ["CRYPTO_LONG", "K=1", "CUSDT_LONG", [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0, 0.0, 3]],
    ]
    s = B.build_single(rows)["CRYPTO_LONG"]["K=1"]
    assert s["n"] == 2 and s["pos"] == 1 and s["neg"] == 1 and s["invalid"] == 1
    assert s["mean"] == 0.5 and s["n_sym_new"] == 1 and s["n_sym_old_only"] == 1


def test_build_blocks_condition_and_exemption():
    rows = [
        ["STOCKS_LONG", "S_ENABLED=True", "F_FILTER_TF", "4h", "X_LONG", [1, 2.0, 0.0]],
        ["STOCKS_LONG", "S_ENABLED=True", "F_FILTER_TF", "4h", "Y_LONG", [1, 1.0, -0.5]],
        ["STOCKS_LONG", "S_ENABLED=True", "F_FILTER_TF", "4h", "Z_LONG", [1, -1.0, 0.0]],
    ]
    ex = {"S_ENABLED": {"F_FILTER_TF": {"FILTER_EXEMPTIONS": {"S": ["F_FILTER_TF"]}}}}
    out, tot = B.build_blocks(rows, ex, 10)
    b = out["STOCKS_LONG"][0]
    assert b["pos_sym"] == 2 and b["n"] == 3
    assert b["exemption_override"] == {"FILTER_EXEMPTIONS": {"S": ["F_FILTER_TF"]}}


def test_build_synergy():
    rows = [["CRYPTO_SHORT", "A=1", "B=1", "QUSDT_SHORT", [1, 5.0, 1.0, 1.0, 3.0]],
            ["CRYPTO_SHORT", "A=1", "B=1", "RUSDT_SHORT", [1, -2.0, 0.0, 0.0, -2.0]]]
    syn, anti, tot = B.build_synergy(rows, 10)
    assert syn["CRYPTO_SHORT"][0]["syn_sym"] == 1
    assert anti["CRYPTO_SHORT"][0]["anti_sym"] == 1


def test_verdicts():
    assert B.verdict(2, 2, 0, 0, 1.0) == "INSUFFICIENT"
    assert B.verdict(12, 0, 0, 12, 0.0) == "DEAD"
    assert B.verdict(12, 0, 5, 7, -1.0) == "HARMFUL"
    assert B.verdict(12, 4, 1, 7, 0.5) == "USEFUL"
