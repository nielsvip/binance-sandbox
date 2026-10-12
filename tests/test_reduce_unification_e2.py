"""E2 REDUCE unification: ONE counting semantic in metrics + chart paths.

Unified semantic: a realised close is ANY ledger row carrying realised pnl
(pnl_dollars present), regardless of type label. REDUCE_TO_FLAT / profit-lock
REDUCE rows exit to flat (qty 0) with full close economics and are genuine
closes; position bookkeeping without pnl (OPEN/AUGMENT, partial REDUCE,
companion markers) never counts. hires_chart._split_ledger consumes
metrics_guard.realised_closes so header trades == embedded TRADES always.
"""

import sys

sys.path.insert(0, ".")

import pytest

from metrics_guard import pool_sharpe, pool_sharpe_from_ledger, realised_closes
from tools.opt.hires_chart import _split_ledger

_TS = [1000.0 + i for i in range(40)]


def _close(pnl_pct, pnl_dollars, be, seed=False):
    t = {
        "type": "CLOSE",
        "bar_entry": be,
        "bar_exit": be + 1,
        "entry_price": 10.0,
        "exit_price": 10.1,
        "qty": 1.0,
        "pnl_pct": pnl_pct,
        "pnl_dollars": pnl_dollars,
        "deployed": 10.0,
        "ts": 1000.0 + be,
        "entry_reason": "B_KZONE",
        "exit_reason": "WT_CROSS_EXIT_1h_bear",
    }
    if seed:
        t["seeded_prewindow"] = True
    return t


def _reduce_flat(pnl_pct, pnl_dollars, be, reason="REDUCE_TO_FLAT"):
    return {
        "type": "REDUCE",
        "reason": reason,
        "exit_reason": reason,
        "ts": 1000.0 + be,
        "price": 10.1,
        "qty": 0.0,
        "bar_entry": be,
        "bar_exit": be + 1,
        "entry_price": 10.0,
        "exit_price": 10.1,
        "entry_reason": "B_MOM3",
        "pnl_pct": pnl_pct,
        "pnl_dollars": pnl_dollars,
        "deployed": 10.0,
    }


def _open(be=0):
    return {
        "type": "OPEN",
        "ts": 1000.0 + be,
        "price": 10.0,
        "qty": 1.0,
        "deployed": 10.0,
        "reason": "B_MOM3",
    }


def _augment(be=0):
    return {
        "type": "AUGMENT",
        "ts": 1000.0 + be,
        "price": 10.0,
        "qty": 1.0,
        "deployed": 10.0,
        "reason": "AUG_TREND",
    }


def _partial_reduce(be=0):
    return {
        "type": "REDUCE",
        "ts": 1000.0 + be,
        "price": 10.0,
        "qty": 0.5,
        "pos_deployed": 5.0,
        "reason": "PPL_PARTIAL",
    }


def _companion_reduce(be=0):
    return {
        "type": "REDUCE",
        "ts": 1000.0 + be,
        "price": 10.0,
        "qty": 0.0,
        "pos_deployed": 10.0,
        "reason": "WT_CROSS_EXIT_1h_bear",
    }


def _mixed_ledger():
    return [
        _open(0),
        _close(1.0, 1.0, 2),
        _augment(3),
        _reduce_flat(0.62, 0.25, 5),
        _partial_reduce(6),
        _close(-0.5, -0.5, 8),
        _companion_reduce(9),
        _close(9.9, 9.9, 11, seed=True),
        _reduce_flat(2.63, 1.2, 14, reason="HLR_TOP_EXIT_SELL_TOP_g=2.68%"),
        "not-a-dict",
        None,
    ]


def test_reduce_to_flat_counts_in_both_paths():
    ledger = _mixed_ledger()
    real = realised_closes(ledger)
    assert len(real) == 4, real
    assert [t["pnl_dollars"] for t in real] == [1.0, 0.25, -0.5, 1.2]
    trades, events = _split_ledger(ledger, _TS)
    assert len(trades) == 4  # header prints len(trades)
    assert {id(t) for t in trades} == {id(t) for t in real}
    assert len(events) == 4  # OPEN + AUGMENT + partial + companion
    assert all(e.get("pnl_dollars") is None for e in events)


def test_profit_lock_reduce_is_a_close_not_an_event():
    row = _reduce_flat(2.63, 1.2, 14, reason="HLR_TOP_EXIT_SELL_TOP_g=2.68%")
    trades, events = _split_ledger([_open(0), row], _TS)
    assert trades == [row]
    assert len(events) == 1 and events[0]["type"] == "OPEN"


def test_predicate_is_type_agnostic_like_engine_sums():
    weird = dict(_close(1.0, 1.0, 2))
    weird["type"] = "OPEN"
    assert realised_closes([weird]) == [weird]
    trades, events = _split_ledger([weird], _TS)
    assert trades == [weird] and events == []


def test_no_double_count_dollars_or_denominators():
    ledger = _mixed_ledger()
    trades, _ = _split_ledger(ledger, _TS)
    full_pnl = sum(
        float(t.get("pnl_dollars") or 0.0) for t in ledger if isinstance(t, dict)
    )
    trade_pnl = sum(float(t.get("pnl_dollars") or 0.0) for t in trades)
    seed_pnl = 9.9  # the seeded row carries pnl but must never count
    assert trade_pnl == pytest.approx(full_pnl - seed_pnl)
    wr = 100.0 * sum(float(t.get("pnl_dollars") or 0) > 0 for t in trades) / len(trades)
    assert wr == 100.0 * 3 / 4
    assert pool_sharpe_from_ledger(ledger) == pool_sharpe([1.0, 0.62, -0.5, 2.63])


def test_split_matches_realised_ids_on_all_row_shapes():
    ledger = _mixed_ledger()
    real = realised_closes(ledger)
    trades, events = _split_ledger(ledger, _TS)
    assert len(trades) == len(real) == 4
    assert {id(t) for t in trades} == {id(t) for t in real}
    assert all(isinstance(t, dict) for t in trades)
    assert all(t.get("pnl_dollars") is not None for t in trades)


def test_companion_and_partial_reduces_stay_events():
    ledger = [_partial_reduce(6), _companion_reduce(9), _open(0)]
    assert realised_closes(ledger) == []
    trades, events = _split_ledger(ledger, _TS)
    assert trades == []
    assert len(events) == 3
    assert all("bar" in e for e in events)  # chart datasets need bar on events
