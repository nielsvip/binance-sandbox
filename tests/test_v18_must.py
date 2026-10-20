"""V18 MUST (USER LAW 2026-10-20): every switch=cand / switch=cand+filter=opt positive on >= 1 OTHER sym_side of THIS cat_side
and never evaluated on this one is evaluated at p = 1 (R0, vs the start set, greedy under the gates), settled with its
result + start-set sha, and forced into the main pass at p = 1."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402
from tools import v15_v18_must_index as M  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def _rec(ss, sheet, sw, c, label, delta, valid=True, trades=20):
    return {"sym_side": ss, "sheet": sheet, "switch": sw, "cand": c, "label": label, "delta": delta, "valid": valid, "trades": trades, "err": ""}


def test_item_keys_and_positive_gate():
    assert M.item_of(_rec("X_LONG", "EXIT_VELOCITY", "A", "1", "naked", 1.0)) == "A=1"
    assert M.item_of(_rec("X_LONG", "EXIT_VELOCITY", "A", "1", "F=2", 1.0)) == "A=1@F=2"
    for lab in ("JOINT:F=2+G=1", "ROW_DONE", "RECALC_CUMULATIVE", "hustle"):
        assert M.item_of(_rec("X_LONG", "EXIT_VELOCITY", "A", "1", lab, 1.0)) is None
    assert M.item_of(_rec("X_LONG", "V18", "A", "1", "naked", 1.0)) is None
    assert M.positive(_rec("X_LONG", "S", "A", "1", "naked", 0.5))
    assert not M.positive(_rec("X_LONG", "S", "A", "1", "naked", 0.5, trades=3))
    assert not M.positive(_rec("X_LONG", "S", "A", "1", "naked", 0.5, valid=False))
    assert not M.positive(_rec("X_LONG", "S", "A", "1", "naked", 0.0))


def test_build_index_per_cat_side_incremental(tmp_path):
    log = tmp_path / "logs" / "a.jsonl"
    log.parent.mkdir()
    lines = [_rec("DOTUSDT_LONG", "EXIT_VELOCITY", "A", "1", "naked", 1.5), _rec("ADAUSDT_LONG", "EXIT_VELOCITY", "A", "1", "F=2", 0.7), _rec("AMD_LONG", "EXIT_VELOCITY", "B", "2", "naked", 2.0), _rec("ADAUSDT_LONG", "EXIT_VELOCITY", "C", "3", "naked", -1.0)]
    log.write_text("".join(json.dumps(r) + "\n" for r in lines))
    out = tmp_path / "learn"
    st = M.build(str(log), out)
    cl = json.loads((out / "must_pos_CRYPTO_LONG.json").read_text())
    assert set(cl["items"]) == {"A=1", "A=1@F=2"} and cl["meta"]["cat_side"] == "CRYPTO_LONG"
    assert set(json.loads((out / "must_pos_STOCKS_LONG.json").read_text())["items"]) == {"B=2"}
    assert st["positives"] == 3
    with open(log, "a") as fh:
        fh.write(json.dumps(_rec("XRPUSDT_LONG", "EXIT_VELOCITY", "A", "1", "naked", 0.2)) + "\n")
    st2 = M.build(str(log), out)
    assert st2["positives"] == 1
    assert sorted(json.loads((out / "must_pos_CRYPTO_LONG.json").read_text())["items"]["A=1"]["syms"]) == ["DOTUSDT_LONG", "XRPUSDT_LONG"]


def test_must_items_subtracts_own_tries_and_settled_same_start():
    idx = {"A=1": {"syms": ["ADA_LONG"], "best": 1.0}, "B=2": {"syms": ["X_LONG"], "best": 3.0}, "C=3": {"syms": ["X_LONG"], "best": 0.5}, "D=4": {"syms": ["ME_LONG"], "best": 9.0}}
    dg = {"positives_all": [{"switch": "E", "cand": "5", "filter": "F", "opt": "1", "tried_on": ["Y_LONG"], "best": 2.0}, {"switch": "G", "cand": "6", "tried_on": ["ME_LONG"]}]}
    led = {"C=3": {"start_sha": "s1"}}
    out = V.must_items(idx, dg, "ME_LONG", {"A=1"}, led, "s1")
    assert out == ["B=2", "E=5@F=1"]  # A tried here, C settled on the same start, D positive only here, G tried here
    assert V.must_items(idx, None, "ME_LONG", set(), led, "s2") == ["B=2", "A=1", "C=3"]  # chain changed -> C again
    assert V.must_parts("E=5@F=1") == [("E", "5"), ("F", "1")] and V.must_parts("B=2") == [("B", "2")]


def test_load_must_index_strict(tmp_path, monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    assert V.load_must_index("CRYPTO_LONG")[0] == {}
    (tmp_path / "must_pos_CRYPTO_LONG.json").write_text(json.dumps({"meta": {"cat_side": "STOCKS_LONG"}, "items": {"A=1": {}}}))
    assert "MISMATCH" in V.load_must_index("CRYPTO_LONG")[1]


def test_pilot_must_wiring_pins():
    assert "_v18_must_on = bool(_V18P is not None and _V18P.must_on())" in SRC
    assert '_v18_must(_t.time() + float(os.environ.get("V18_MUST_S", "7200")))' in SRC
    assert SRC.index("_v18_must(_t.time()") < SRC.index("_v18_winners(_t.time()")
    assert '"MUST", deadline, ph, accept=_v18_accept, book=_v18) if cands else []' in SRC  # no max_prom / no top-K cap
    assert "[V18-MUST]" in SRC and '_v18["must"] = {' in SRC
    assert 'progress_path.parent / "v18_must" / f"{new_symside}.json"' in SRC
    # main pass: p = 1 for MUST rows/cells, after the floor (nothing can remove them)
    i_f = SRC.index("_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)")
    i_m = SRC.index('_V18P.floor_apply(info, lambda kind, h, _m=_v18_mk:')
    assert i_f < i_m


def test_positives_all_gz_index_form(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    d = {"_meta": {"cat_side": "CRYPTO_LONG"}, "sym_sides": ["A_LONG", "ME_LONG", "B_LONG"], "rows": [{"key": "S=1", "pos_syms": ["A_LONG"], "tried_on": [0, 1], "n_pos": 1}, {"key": "T=2", "pos_syms": ["B_LONG", "ME_LONG"], "tried_on": [2], "n_pos": 2}], "pairs": [{"key": "S=1", "filter": "F=o", "pos_syms": ["A_LONG"], "tried_on": [0], "n_pos": 1}]}
    with gzip.open(tmp_path / "positives_all_CRYPTO_LONG.json.gz", "wt") as fh:
        json.dump(d, fh)
    idx, tried, why = V.load_positives_all("CRYPTO_LONG", "ME_LONG")
    assert why.startswith("ok positives_all_CRYPTO_LONG.json.gz")
    assert tried == {"S=1"} and set(idx) == {"S=1", "T=2", "S=1@F=o"} and idx["T=2"]["syms"] == ["B_LONG"]
    assert V.must_items(idx, None, "ME_LONG", tried, {}, "x") == ["S=1@F=o", "T=2"] or V.must_items(idx, None, "ME_LONG", tried, {}, "x") == ["T=2", "S=1@F=o"]
    d["_meta"]["cat_side"] = "STOCKS_LONG"
    with gzip.open(tmp_path / "positives_all_CRYPTO_LONG.json.gz", "wt") as fh:
        json.dump(d, fh)
    assert "MISMATCH" in V.load_positives_all("CRYPTO_LONG", "ME_LONG")[2]


def test_pilot_reads_positives_all():
    assert "_V18P.load_positives_all(_v18_floor_cs, new_symside, ROOT)" in SRC
    assert "tried = _tried_items(own, new_symside) | set(_pa_tried)" in SRC


def test_positives_all_ignores_native_unconfirmed(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    d = {"_meta": {"cat_side": "STOCKS_LONG"}, "sym_sides": ["A_LONG", "ME_LONG"], "rows": [], "pairs": [], "native_unconfirmed": {"rows": [{"key": "N=1", "pos_syms": ["A_LONG"], "tried_on": [0]}], "pairs": [{"key": "N=1", "filter": "F=o", "pos_syms": ["A_LONG"]}]}}
    with gzip.open(tmp_path / "positives_all_STOCKS_LONG.json.gz", "wt") as fh:
        json.dump(d, fh)
    idx, tried, _ = V.load_positives_all("STOCKS_LONG", "ME_LONG")
    assert idx == {} and tried == set()  # native_unconfirmed is never a MUST source
