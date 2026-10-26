import json
import os
import time
from types import SimpleNamespace
from live_twins import vec_backstop as vb


def _st(open=None, ok=True, src="s", tail=9):
    return {"ok": ok, "valid": ok, "audit_only": False, "open": open, "src": src, "npz_tail": tail}


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


def test_position_resolver_prefers_exact_account_book_over_global_mirror():
    scoped = SimpleNamespace(positionAmt=2.0)
    stale = SimpleNamespace(positionAmt=7.0)
    key = "acct_a:BTCUSDT_LONG"
    manager = SimpleNamespace(
        positions_by_account={"acct_a": {key: scoped}},
        positions={key: stale},
    )
    assert vb.position_for_account(manager, "acct_a", key) is scoped


def test_position_resolver_rejects_account_key_mismatch():
    wrong_account = SimpleNamespace(positionAmt=7.0)
    key = "acct_b:BTCUSDT_LONG"
    manager = SimpleNamespace(
        positions_by_account={"acct_a": {key: wrong_account}},
        positions={key: wrong_account},
    )
    assert vb.position_for_account(manager, "acct_a", key) is None


def test_position_resolver_uses_same_account_service_book_then_global_fallback():
    scoped = SimpleNamespace(positionAmt=3.0)
    key = "acct_a:BTCUSDT_LONG"
    service = SimpleNamespace(positions_by_account={"acct_a": {key: scoped}}, positions={})
    manager = SimpleNamespace(positions_service=service, positions_by_account={}, positions={})
    assert vb.position_for_account(manager, "acct_a", key) is scoped
    global_position = SimpleNamespace(positionAmt=4.0)
    service.positions[key] = global_position
    service.positions_by_account["acct_a"].clear()
    assert vb.position_for_account(manager, "acct_a", key) is global_position


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


def test_tradier_recognizes_vec_fwd_reasons():
    # USER 2026-10-25: stocks backstop fires VEC_FWD_MISSING_* through
    # execute_now — the parity gate must treat them as vector-dictated
    # (never refused by a live opinion gate), same as the crypto twin.
    import tradier_manage as tm
    assert tm._tr_parity_vec_order("VEC_FWD_MISSING_OPEN fwd=A_LONG") is True
    assert tm._tr_parity_vec_order("VEC_FWD_MISSING_FLAT fwd=A_SHORT") is True
    assert tm._tr_parity_vec_order("vec_fwd_missing_open fwd=a_long") is True
    assert tm._tr_parity_vec_order("NATIVE_REPLAY_A") is False
