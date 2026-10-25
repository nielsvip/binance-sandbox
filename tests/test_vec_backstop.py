import json
import os
import time
from live_twins import vec_backstop as vb


def _st(open=None, ok=True, src="s", tail=9):
    return {"ok": ok, "open": open, "src": src, "npz_tail": tail}


def test_agree_open_no_action():
    assert vb.divergence(True, _st(True)) == (None, "")


def test_agree_flat_no_action():
    assert vb.divergence(False, _st(False)) == (None, "")


def test_missing_open_reason_carries_path():
    act, reason = vb.divergence(False, _st(True, src="f.json:cum", tail=123))
    assert act == "OPEN"
    assert reason == "VEC_FWD_MISSING_OPEN:f.json:cum@123"


def test_missing_flat_reason_carries_path():
    act, reason = vb.divergence(True, _st(False, src="f.json:cum", tail=123))
    assert act == "FLAT"
    assert reason == "VEC_FWD_MISSING_FLAT:f.json:cum@123"


def test_unknown_target_never_acts():
    assert vb.divergence(True, _st(None))[0] is None
    assert vb.divergence(False, _st(None))[0] is None
    assert vb.divergence(True, _st(True, ok=False))[0] is None
    assert vb.divergence(False, None)[0] is None


def test_due_once_per_bar():
    assert vb.due("ZZZ_T_SIDE", "b1") is True
    assert vb.due("ZZZ_T_SIDE", "b1") is False
    assert vb.due("ZZZ_T_SIDE", "b2") is True


def test_read_state_fresh_and_stale(tmp_path):
    (tmp_path / "data" / "forward_state").mkdir(parents=True)
    p = tmp_path / "data" / "forward_state" / "A_LONG.json"
    p.write_text(json.dumps(_st(True)))
    assert vb.read_state("A_LONG", base_path=tmp_path)["open"] is True
    old = time.time() - 99999
    os.utime(p, (old, old))
    assert vb.read_state("A_LONG", base_path=tmp_path) is None
    assert vb.read_state("NOPE_LONG", base_path=tmp_path) is None
