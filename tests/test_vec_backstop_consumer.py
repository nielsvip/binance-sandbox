#!/usr/bin/env python3
"""Tests for tools/vec_backstop_consumer.py — every VEC_ backstop executes."""

import time

from tools import vec_backstop_consumer as consumer
from tools.vec_backstop_consumer import collect_owed


def _st(ss, side, mode="live", age_min=10, now=None):
    now = now if now is not None else time.time()
    return {
        "ss": ss,
        "mode": mode,
        "actionable": True,
        "bar_ts": now - age_min * 60,
        "target_state": {"side": side},
    }


def test_flat_live_open_pushes_close():
    now = time.time()
    cmds = collect_owed(
        {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", now=now)},
        {"flz:BTCUSDC_SHORT": 12.5},
        now,
        set(),
    )
    assert len(cmds) == 1
    c = cmds[0]
    assert c["symbol"] == "BTCUSDC" and c["position_side"] == "SHORT"
    assert c["side"] == "BUY" and c["quantity"] == 12.5
    assert c["reason"].startswith("VEC_DRIVEN_") and "EXIT_OWED" in c["reason"]


def test_long_close_uses_sell():
    now = time.time()
    cmds = collect_owed(
        {"WLDUSDC_LONG": _st("WLDUSDC_LONG", "flat", now=now)},
        {"flz:WLDUSDC_LONG": 28.8},
        now,
        set(),
    )
    assert len(cmds) == 1 and cmds[0]["side"] == "SELL"


def test_closed_position_no_push():
    now = time.time()
    assert (
        collect_owed(
            {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", now=now)},
            {"flz:BTCUSDC_SHORT": 0.0},
            now,
            set(),
        )
        == []
    )


def test_open_target_no_push():
    now = time.time()
    assert (
        collect_owed(
            {"XMRUSDT_LONG": _st("XMRUSDT_LONG", "long", now=now)},
            {"flz:XMRUSDT_LONG": 0.021},
            now,
            set(),
        )
        == []
    )


def test_stale_bar_no_push():
    now = time.time()
    assert (
        collect_owed(
            {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", age_min=90, now=now)},
            {"flz:BTCUSDC_SHORT": 12.5},
            now,
            set(),
        )
        == []
    )


def test_shadow_mode_no_push():
    now = time.time()
    assert (
        collect_owed(
            {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", mode="shadow", now=now)},
            {"flz:BTCUSDC_SHORT": 12.5},
            now,
            set(),
        )
        == []
    )


def test_idempotent_same_bar():
    now = time.time()
    states = {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", now=now)}
    pos = {"flz:BTCUSDC_SHORT": 12.5}
    pushed = set()
    first = collect_owed(states, pos, now, pushed)
    assert len(first) == 1
    pushed.add(first[0]["key"])
    assert collect_owed(states, pos, now, pushed) == []


def _install_run_inputs(monkeypatch, states, positions, pushed=None):
    pushed = set(pushed or ())
    saved = []
    events = []
    monkeypatch.setattr(consumer, "load_states", lambda _state_dir: states)
    monkeypatch.setattr(consumer, "load_live_positions", lambda _base, _account: positions)
    monkeypatch.setattr(consumer, "load_pushed", lambda: set(pushed))
    monkeypatch.setattr(consumer, "save_pushed", lambda values: saved.append(set(values)))
    monkeypatch.setattr(consumer, "log_decision", lambda entry: events.append(entry))
    return pushed, saved, events


def test_run_dry_run_is_logged_but_not_marked_pushed(monkeypatch):
    now = time.time()
    states = {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", now=now)}
    positions = {"flz:BTCUSDC_SHORT": 1.0}
    pushed, saved, events = _install_run_inputs(monkeypatch, states, positions)

    rec = consumer.run("/unused", "flz", redis_client=None, now_s=now, dry_run=True)

    assert rec["pushed"] == 0
    assert not saved and not pushed
    assert events[0]["kind"] == "VEC_BACKSTOP_DRY_RUN"
    assert events[0]["broker_execution_confirmed"] is False


def test_run_without_redis_remains_retryable(monkeypatch):
    now = time.time()
    states = {"BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", now=now)}
    positions = {"flz:BTCUSDC_SHORT": 1.0}
    pushed, saved, events = _install_run_inputs(monkeypatch, states, positions)

    rec = consumer.run("/unused", "flz", redis_client=None, now_s=now)

    assert rec["pushed"] == 0
    assert not saved and not pushed
    assert events[0]["kind"] == "VEC_BACKSTOP_HELD"
    assert events[0]["queue_confirmed"] is False


def test_run_marks_only_successfully_enqueued_commands(monkeypatch):
    now = time.time()
    states = {
        "BTCUSDC_SHORT": _st("BTCUSDC_SHORT", "flat", now=now),
        "ETHUSDC_LONG": _st("ETHUSDC_LONG", "flat", now=now),
    }
    positions = {"flz:BTCUSDC_SHORT": 1.0, "ang:ETHUSDC_LONG": 2.0}
    pushed, saved, events = _install_run_inputs(monkeypatch, states, positions)

    class Redis:
        def rpush(self, key, _payload):
            if key.endswith(":ang"):
                raise OSError("redis write failed")
            return 1

    rec = consumer.run("/unused", "flz", redis_client=Redis(), now_s=now)

    assert rec["pushed"] == 1 and rec["failed"] == 1
    assert len(saved) == 1
    assert len(saved[0]) == 1
    assert "BTCUSDC_SHORT|" in next(iter(saved[0]))
    assert all("ETHUSDC_LONG|" not in key for key in saved[0])
    assert events[0]["kind"] == "VEC_BACKSTOP_QUEUED"
    assert events[0]["broker_execution_confirmed"] is False
    assert events[1]["kind"] == "VEC_BACKSTOP_QUEUE_FAILED"
    assert events[1]["queue_confirmed"] is False
