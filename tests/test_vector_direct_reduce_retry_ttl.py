import ast
from pathlib import Path

import pytest

from tools import vector_direct_trader as vd


def _expire_done(done, done_ts, now):
    for key, sent_at in list(done_ts.items()):
        if now - sent_at > vd.BAR_S:
            done.discard(key)
            done_ts.pop(key, None)


def _plan(acct, done, prev_qty):
    ss = "HYPEUSDT_LONG"
    tail = 1793286000
    states = {
        ss: {
            "ok": True,
            "open": True,
            "npz_tail": tail,
            "engine_qty": 0.04,
            "engine_open_qty": 0.16,
            "acts_bar_ts": tail,
            "acts_last_bar": [
                {
                    "type": "REDUCE",
                    "qty_frac": 0.7812,
                    "qty": 0.12,
                    "qty_before": 0.16,
                    "reason": "PPL_TP",
                }
            ],
        }
    }
    return vd.plan_account(
        acct,
        "crypto",
        [ss],
        states,
        {ss: {"qty": 0.04, "price": 93.34}},
        tail + vd.BAR_S,
        done,
        lambda _acct, _ss: True,
        prev_qty=prev_qty,
    )


def test_sent_partial_reduce_repeats_after_all_action_done_ttl_expires():
    sent_at = 1793286919.0
    ang_key = "ang|HYPEUSDT_LONG|1793286000|REDUCE|0"
    inf_key = "inf|HYPEUSDT_LONG|1793286000|REDUCE|0"
    ang_done, inf_done = {ang_key}, {inf_key}
    ang_done_ts, inf_done_ts = {ang_key: sent_at}, {inf_key: sent_at}

    _expire_done(ang_done, ang_done_ts, sent_at + vd.BAR_S + 1)
    _expire_done(inf_done, inf_done_ts, sent_at + vd.BAR_S + 1)
    assert not ang_done and not inf_done

    ang_orders, _ = _plan("ang", ang_done, {})
    inf_orders, inf_notes = _plan("inf", inf_done, {"HYPEUSDT_LONG": 0.16})

    assert len(ang_orders) == 1
    assert ang_orders[0]["key"] == ang_key
    assert ang_orders[0]["act"] == "REDUCE"
    assert ang_orders[0]["qty"] == pytest.approx(0.031248)
    assert inf_orders == []
    assert ("HYPEUSDT_LONG", "REDUCE_ALREADY_ON_BROKER 0.16->0.04") in inf_notes


def test_production_ttl_expires_reduce_keys_without_action_filter():
    source = Path(vd.__file__).read_text()
    tree = ast.parse(source)
    loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Tuple)
        and len(node.target.elts) == 2
        and isinstance(node.target.elts[0], ast.Name)
        and node.target.elts[0].id == "k"
        and isinstance(node.target.elts[1], ast.Name)
        and node.target.elts[1].id == "t_done"
    ]
    assert len(loops) == 1
    loop_text = ast.unparse(loops[0])
    assert "now - t_done > BAR_S" in loop_text
    assert "self.done.discard(k)" in loop_text
    assert "self.done_ts.pop(k, None)" in loop_text
