import ast
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_stale_ang_snapshot_would_repeat_but_journaled_event_remains_done():
    sent_at = 1793286919.0
    ang_key = "ang|HYPEUSDT_LONG|1793286000|REDUCE|0"
    inf_key = "inf|HYPEUSDT_LONG|1793286000|REDUCE|0"
    ang_without_dedupe, _ = _plan("ang", set(), {})
    inf_without_dedupe, inf_notes = _plan("inf", set(), {"HYPEUSDT_LONG": 0.16})
    assert ang_without_dedupe[0]["qty"] == pytest.approx(0.031248)
    assert inf_without_dedupe == []
    assert ("HYPEUSDT_LONG", "REDUCE_ALREADY_ON_BROKER 0.16->0.04") in inf_notes

    ang_done, inf_done = {ang_key}, {inf_key}
    ang_done_ts, inf_done_ts = {}, {}
    _expire_done(ang_done, ang_done_ts, sent_at + vd.BAR_S + 1)
    _expire_done(inf_done, inf_done_ts, sent_at + vd.BAR_S + 1)
    ang_orders, _ = _plan("ang", ang_done, {})
    inf_orders, _ = _plan("inf", inf_done, {"HYPEUSDT_LONG": 0.16})

    assert ang_done == {ang_key}
    assert inf_done == {inf_key}
    assert ang_orders == []
    assert inf_orders == []


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


def test_open_key_still_expires_for_convergence_retry():
    key = "ang|HYPEUSDT_LONG|1793286000|OPEN"
    done, done_ts = {key}, {key: 100.0}

    _expire_done(done, done_ts, 100.0 + vd.BAR_S + 1)

    assert done == set()
    assert done_ts == {}


def test_existing_ledger_restores_successful_reduce_without_retry_timestamp(tmp_path):
    journal = tmp_path / "ledger.jsonl"
    reduce_key = "ang|HYPEUSDT_LONG|1793286000|REDUCE|0"
    open_key = "ang|HYPEUSDT_LONG|1793286900|OPEN"
    rows = [
        {"event": "ORDER", "key": reduce_key, "status": "SENT", "final": True, "ts": 100.0},
        {"event": "ORDER", "key": open_key, "status": "SENT", "final": True, "ts": 100.0},
    ]
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    holder = SimpleNamespace(done_ts={}, _ledger_path=lambda _ts=None: journal)

    done = vd.Trader._load_done(holder)

    assert reduce_key in done
    assert open_key in done
    assert reduce_key not in holder.done_ts
    assert holder.done_ts[open_key] == 100.0


def test_new_bar_partial_reduce_uses_distinct_key():
    old_key = "ang|HYPEUSDT_LONG|1793286000|REDUCE|0"
    ss = "HYPEUSDT_LONG"
    new_tail = 1793286900
    state = {
        ss: {
            "ok": True,
            "open": True,
            "npz_tail": new_tail,
            "engine_qty": 0.02,
            "engine_open_qty": 0.16,
            "acts_bar_ts": new_tail,
            "acts_last_bar": [
                {"type": "REDUCE", "qty_frac": 0.5, "qty": 0.02, "qty_before": 0.04, "reason": "NEXT_BAR_REDUCE"}
            ],
        }
    }

    orders, _ = vd.plan_account(
        "ang", "crypto", [ss], state, {ss: {"qty": 0.04, "price": 93.34}},
        new_tail, {old_key}, lambda _acct, _ss: True, prev_qty={},
    )

    assert len(orders) == 1
    assert orders[0]["key"] == "ang|HYPEUSDT_LONG|1793286900|REDUCE|0"


def test_successful_send_tracks_retry_ttl_only_for_open():
    source = Path(vd.__file__).read_text()
    tree = ast.parse(source)
    send_orders = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "send_orders"
    )
    assignments = [
        node for node in ast.walk(send_orders)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "done_ts"
            for target in node.targets
        )
        and ast.unparse(node.value) == "time.time()"
    ]
    assert len(assignments) == 2
    assert any(
        isinstance(node, ast.If)
        and ast.unparse(node.test) == "o['act'] == 'OPEN'"
        and any(assignment in list(ast.walk(node)) for assignment in assignments)
        for node in ast.walk(send_orders)
    )
