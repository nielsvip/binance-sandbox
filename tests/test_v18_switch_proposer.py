"""tools/v18_switch_proposer.py (brief 20 §6.4): type classification A/B/C, NEVER refusal, quota, own-cat_side refusal,
PREDICTION label, calibration dedupe — all on in-memory fixtures (no data/learn files are read)."""
import collections
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v18_switch_proposer as P  # noqa: E402

CS = "CRYPTO_LONG"


def _bm(cs=CS):
    rows = {
        "ENTRY_CONFIRMATION_GATES!ENTRY_GATE_ENABLED=True": {"family": "ENTRY", "dead_row": False, "row": 3, "default_row": True, "cells": {}},
        "ENTRY_CONFIRMATION_GATES!ENTRY_GATE_ENABLED=False": {"family": "ENTRY", "dead_row": False, "row": 4, "default_row": False, "cells": {"MOM3_FILTER_TF=1h": {"verdict": "BINDS", "condition_keys": [], "reason": "ENTRY_MASK"}}},
        "AUGMENT_RISK_SIZING!SIZE_MULT=2": {"family": "SIZING", "dead_row": False, "row": 5, "default_row": True, "cells": {}},
        "AUGMENT_RISK_SIZING!SIZE_MULT=4": {"family": "SIZING", "dead_row": False, "row": 6, "default_row": False, "cells": {}},
        "AUGMENT_RISK_SIZING!SIZE_MULT=1": {"family": "SIZING", "dead_row": False, "row": 7, "default_row": False, "cells": {}},
        "EXIT_STRUCTURAL!DEAD_SW=1": {"family": "EXIT_STRUCT", "dead_row": True, "row": 8, "default_row": True, "cells": {}},
        "EXIT_STRUCTURAL!DEAD_SW=2": {"family": "EXIT_STRUCT", "dead_row": True, "row": 9, "default_row": False, "cells": {}},
        "STDEV_SLOPE_SIZING!UNWIRED_MULT=1": {"family": "SIZING", "dead_row": False, "row": 10, "default_row": True, "cells": {}},
        "STDEV_SLOPE_SIZING!UNWIRED_MULT=2": {"family": "SIZING", "dead_row": False, "row": 11, "default_row": False, "cells": {}},
    }
    return {
        "meta": {"cat_side": cs, "contract": "binding_map_v1", "masters_default": {}, "augment_gate_keys": []},
        "switches": {"ENTRY_GATE_ENABLED": {"family": "ENTRY", "masters": [], "dead": False, "stage": "entry"}, "SIZE_MULT": {"family": "SIZING", "masters": [], "dead": False, "stage": "sizing"}, "DEAD_SW": {"family": "EXIT_STRUCT", "masters": [], "dead": True}, "UNWIRED_MULT": {"family": "SIZING", "masters": [], "dead": False}},
        "filters": {"MOM3_FILTER_TF": {"class": "ENTRY_TF_MASK", "stages": ["ENTRY"], "master": None}, "HAIKU_WINNER_FILTER_TF": {"class": "NO_ENGINE_READ", "stages": [], "master": None}},
        "rows": rows,
    }


def _ins(cs=CS):
    bm = _bm(cs)
    return {
        "cs": cs, "bm": bm, "npz": None, "p0": 0.02, "sources": {},
        "linknet": {"meta": {"cat_side": cs}, "needs_row": [
            {"switch": "SIZE_MULT", "cand": "6", "tab": "AUGMENT_RISK_SIZING", "rule": "threshold_neighbour_above", "p_pos_pred": 0.09, "note": "one grid step above"},
            {"switch": "DEAD_SW", "cand": "3", "tab": "EXIT_STRUCTURAL", "rule": "threshold_neighbour_above", "p_pos_pred": 0.5},
            {"switch": "UNWIRED_MULT", "cand": "3", "tab": "STDEV_SLOPE_SIZING", "rule": "threshold_neighbour_above", "p_pos_pred": 0.5},
            {"switch": "ABLATION_DISABLE_X", "cand": "True", "tab": "GLOBAL_RISK_GATES", "rule": "level_sibling", "p_pos_pred": 0.9},
            {"switch": "SIZE_MULT", "cand": "4", "tab": "AUGMENT_RISK_SIZING", "rule": "threshold_neighbour_above", "p_pos_pred": 0.9},
        ]},
        "features": {"meta": {"cat_side": cs}, "settings": {"SIZE_MULT": {"1": {"kind": "num", "rel": "below", "q": 0.0}, "2": {"kind": "num", "rel": "default", "q": 0.5}, "4": {"kind": "num", "rel": "above", "q": 1.0}}},
                     "options": {"MOM3_FILTER_TF": {"OFF": {"rel": "default"}, "1h": {"rel": "other"}, "4h": {"rel": "other"}}, "HAIKU_WINNER_FILTER_TF": {"OFF": {"rel": "default"}, "1h": {"rel": "other"}}}},
        "cards": {"SIZE_MULT": {"default": "2", "cands": {"4": ["DAMA", 40, 6, 0.05, 0.2], "1": ["DAMA", 30, 1, 0.01, 0.1]}}, "ENTRY_GATE_ENABLED": {"default": "True", "cands": {"False": ["CONT", 50, 9, 0.1, 0.3]}}},
        "positives": {"_meta": {"cat_side": cs}, "sym_sides": [], "rows": [{"key": "SIZE_MULT=4", "n": 40, "n_pos": 8, "pos_syms": [f"S{i}USDT_LONG" for i in range(6)]}, {"key": "ENTRY_GATE_ENABLED=False", "n": 50, "n_pos": 12, "pos_syms": [f"S{i}USDT_LONG" for i in range(9)]}],
                      "pairs": [{"key": "ENTRY_GATE_ENABLED=False", "filter": "MOM3_FILTER_TF=4h", "n": 5, "n_pos": 2, "pos_syms": ["S1USDT_LONG"]}]},
        "needed": {"ENTRY_GATE_ENABLED=False": {"needed": ["MOM3_FILTER_TF=4h"], "intervals": {"MOM3_FILTER_TF=4h": [0.5, 2.0, 5, 5, "NEEDED"]}}},
        "elr": {}, "bible": {"SIZE_MULT": {"status": {"crypto": "WIRED_BOTH_UNPROVEN", "stocks": "WIRED_BOTH_UNPROVEN"}}, "UNWIRED_MULT": {"status": {"crypto": "VEC_ONLY", "stocks": "VEC_ONLY"}}},
        "code": {"CRYPTO": {"SIZE_MULT", "UNWIRED_MULT", "ENTRY_GATE_ENABLED"}, "STOCKS": {"SIZE_MULT"}, "QC": {"SIZE_MULT", "UNWIRED_MULT", "ENTRY_GATE_ENABLED"}},
    }


def _doc(**kw):
    return P.build_cat_side(_ins(), predicted_at="2026-10-22T20:00:00Z", **kw)


def test_type_classification():
    doc = _doc()
    by = collections.defaultdict(list)
    for p in doc["proposals"]:
        by[p["type"]].append(p)
    a = {(p["switch"], p["cand"]) for p in by["A"]}
    assert ("SIZE_MULT", "6") in a and ("SIZE_MULT", "3") in a  # needs_row + intermediate bold 2 -> adjacent positive 4
    assert all(not p["wiring_needed"] and p["filter"] is None for p in by["A"])
    b = {(p["switch"], p["cand"], p["filter"], p["opt"]) for p in by["B"]}
    assert ("ENTRY_GATE_ENABLED", "False", "MOM3_FILTER_TF", "4h") in b  # unshaded BINDS pair
    assert all(p["bind_verdict"]["verdict"] == "BINDS" and not p["wiring_needed"] for p in by["B"])
    assert by["C"] and all(p["wiring_needed"] for p in by["C"])
    for p in by["C"]:
        spec = p["wiring_spec"]
        assert {"vector", "live_crypto", "live_stocks", "config", "default_identity", "kill_switch"} <= set(spec)
        assert p["switch"] in spec["kill_switch"]
    assert by["B"][0]["needed_listed"] is True and by["B"][0]["support"] == 1.0


def test_never_and_artefact_refusal():
    doc = _doc()
    keys = {(p["switch"], p.get("filter")) for p in doc["proposals"]}
    assert not any(f == "HAIKU_WINNER_FILTER_TF" for _, f in keys)
    r = doc["meta"]["refused"]
    assert r["NEVER_verdict"] >= 1
    assert r["dead_row"] >= 1 and "DEAD_SW" not in {s for s, _ in keys}
    assert r["not_wired_needs_C"] >= 1 and "UNWIRED_MULT" not in {s for s, _ in keys}
    assert r["artefact_ABLATION_lever"] == 1
    assert r["already_in_template"] >= 2  # SIZE_MULT=4 row exists, MOM3_FILTER_TF=1h cell is shaded
    assert not any(p["type"] == "B" and p["filter"] == "MOM3_FILTER_TF" and p["opt"] == "1h" and p["switch"] == "ENTRY_GATE_ENABLED" for p in doc["proposals"])


def test_quota_and_mix():
    mk = lambda t, i, s: {"type": t, "switch": f"{t}{i}", "cand": "1", "filter": f"F{i}" if t == "B" else None, "opt": "x" if t == "B" else None, "p_pos_pred": s, "support": 1.0}
    cands = [mk("A", i, 0.9 - i / 100) for i in range(30)] + [mk("B", i, 0.1 - i / 1000) for i in range(30)] + [mk("C", i, 0.01) for i in range(8)]
    sel = P.select(cands, quota=20, min_b=5, max_c=5, min_a=5)
    n = collections.Counter(c["type"] for c in sel)
    assert len(sel) == 20 and n["C"] == 5 and n["B"] >= 5 and n["A"] == 10
    same = [{"type": "B", "switch": "R", "cand": "1", "filter": "F", "opt": o, "p_pos_pred": 0.5, "support": 1.0} for o in "abcd"]
    assert len(P.select(same, quota=20, min_b=5, max_c=0, min_a=0)) == 1  # one option per (switch, filter)
    assert len(P.select(cands, quota=3, min_b=5, max_c=5, min_a=5)) == 3


def test_cat_side_refusal(tmp_path):
    v3u = tmp_path / "v3u"
    v3u.mkdir()
    ins = _ins()
    (v3u / f"linknet_proposals_{CS}.json").write_text(json.dumps(ins["linknet"]))
    (tmp_path / f"binding_map_{CS}.json").write_text(json.dumps(_bm("CRYPTO_SHORT")))
    with pytest.raises(P.CatSideRefused):
        P.load_inputs(CS, learn_dir=tmp_path, v3u_dir=v3u)
    bad = dict(ins["linknet"], meta={"cat_side": "STOCKS_LONG"})
    (v3u / f"linknet_proposals_{CS}.json").write_text(json.dumps(bad))
    with pytest.raises(P.CatSideRefused):
        P.load_inputs(CS, learn_dir=tmp_path, v3u_dir=v3u)


def test_prediction_label_and_calibration(tmp_path):
    doc = _doc()
    assert doc["meta"]["cat_side"] == CS and "PREDICTION" in doc["meta"]["label"]
    assert doc["proposals"] and all(p["label"] == "PREDICTION" and p["model"] == P.MODEL and p["cat_side"] == CS and p["predicted_at"] for p in doc["proposals"])
    assert all(p["proposal_id"].startswith(f"pv3-{CS}-") for p in doc["proposals"])
    f = tmp_path / "cal.jsonl"
    n1 = P.append_calibration([doc], f)
    n2 = P.append_calibration([doc], f)
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    assert n1 == len(doc["proposals"]) and n2 == 0 and len(lines) == n1
    assert all(x["label"] == "PREDICTION" and x["realised"] is None and 0 <= x["p_pos_pred"] <= 1 for x in lines)
