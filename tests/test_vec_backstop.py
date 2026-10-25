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


def test_fresh_state_edge_only(tmp_path):
    (tmp_path / "data" / "forward_state").mkdir(parents=True)
    p = tmp_path / "data" / "forward_state" / "B_SHORT.json"
    p.write_text(json.dumps(dict(_st(True), sym_side="B_SHORT", ts="t")))
    os.utime(p, (1000000000, 1000000000))
    first = vb.fresh_state("B_SHORT", base_path=tmp_path, max_age_s=0)
    assert first is not None and first["open"] is True
    assert vb.fresh_state("B_SHORT", base_path=tmp_path, max_age_s=0) is None
    p.write_text(json.dumps(dict(_st(False, tail=10), sym_side="B_SHORT", ts="t2")))
    os.utime(p, (1000000001, 1000000001))
    second = vb.fresh_state("B_SHORT", base_path=tmp_path, max_age_s=0)
    assert second is not None and second["open"] is False


def test_record_appends_jsonl(tmp_path):
    vb.record(tmp_path, {"ts": 1.0, "key": "k", "action": "FLAT"})
    lines = (tmp_path / "data" / "vec_backstop_actions.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["action"] == "FLAT"


def test_read_state_fresh_and_stale(tmp_path):
    (tmp_path / "data" / "forward_state").mkdir(parents=True)
    p = tmp_path / "data" / "forward_state" / "A_LONG.json"
    p.write_text(json.dumps(_st(True)))
    assert vb.read_state("A_LONG", base_path=tmp_path)["open"] is True
    old = time.time() - 99999
    os.utime(p, (old, old))
    assert vb.read_state("A_LONG", base_path=tmp_path) is None
    assert vb.read_state("NOPE_LONG", base_path=tmp_path) is None
