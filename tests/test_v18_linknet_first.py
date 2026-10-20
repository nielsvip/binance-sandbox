"""V18_LINKNET_FIRST (default OFF): the untried pairs' first trials come from data/learn/linknet_proposals_<CS>.json
`template_untried` (ranked by p_pos_pred, own cat_side, HAS_TEMPLATE_ROW only; needs_row never), forced at p = 1 where the
row's template has that column; the uniform untried/floor draw stays for the rest; each such eval books linknet_rank and
p_pos_pred in the delta log. Predictions are never adopted — only the real engine under the gates."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_load_ranks_by_p_and_excludes_needs_row(tmp_path, monkeypatch):
    monkeypatch.setenv("V18_DIGEST_DIR", str(tmp_path))
    monkeypatch.delenv("V18_LINKNET_TOP", raising=False)
    d = {"meta": {"cat_side": "CRYPTO_LONG"}, "template_untried": [
        {"switch": "A", "cand": "1", "filter": "F", "opt": "x", "status": "HAS_TEMPLATE_ROW", "p_pos_pred": 0.2},
        {"switch": "B", "cand": "2", "filter": "G", "opt": "y", "status": "HAS_TEMPLATE_ROW", "p_pos_pred": 0.9},
        {"switch": "C", "cand": "3", "filter": "H", "opt": "z", "status": "NEEDS_ROW", "p_pos_pred": 0.99}], "needs_row": [{"switch": "Z", "cand": "9", "filter": "Q", "opt": "1"}]}
    (tmp_path / "linknet_proposals_CRYPTO_LONG.json").write_text(json.dumps(d))
    m, why = V.load_linknet_first("CRYPTO_LONG")
    assert why.startswith("ok") and m[("B=2", "G=y")]["rank"] == 1 and m[("A=1", "F=x")]["rank"] == 2
    assert ("C=3", "H=z") not in m and ("Z=9", "Q=1") not in m
    assert len(V.load_linknet_first("CRYPTO_LONG", top=1)[0]) == 1
    d["meta"]["cat_side"] = "STOCKS_LONG"
    (tmp_path / "linknet_proposals_CRYPTO_LONG.json").write_text(json.dumps(d))
    assert "MISMATCH" in V.load_linknet_first("CRYPTO_LONG")[1]


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("V18_LINKNET_FIRST", raising=False)
    assert V.linknet_first_on() is False


def test_pilot_wiring_pins():
    assert "_v18_ln_on = bool(_V18P is not None and _V18P.linknet_first_on())" in SRC
    assert '_v18_ln_book[(sname, rr, str(_h))] = {"linknet_rank": _e["rank"], "p_pos_pred": _e["p_pos_pred"]}' in SRC
    assert "out.update(_v18_ln_book.get((sname, rr, str(label))) or {})" in SRC
    assert "[V18-LINKNET]" in SRC
    i_f = SRC.index("_V18P.floor_apply(info, _v18_floor_keep_fn(sname, switch, cand), _v18_floor_p)")
    i_l = SRC.index("_V18P.floor_apply(info, lambda kind, h, _m=_v18_lk:")
    assert i_f < i_l
