import ast
import asyncio
import datetime as dt
from pathlib import Path

import ez_positions_quick as epq


ROOT = Path(__file__).resolve().parents[1]
_TREE = ast.parse((ROOT / "backtest_v12_engine.py").read_text())
_PROXY_NODE = next(
    node
    for node in _TREE.body
    if isinstance(node, ast.ClassDef) and node.name == "_FrozenIndicatorReceiptProxy"
)
_PROXY_SCOPE = {}
exec(
    compile(ast.Module(body=[_PROXY_NODE], type_ignores=[]), "backtest_v12_engine.py", "exec"),
    _PROXY_SCOPE,
)
FrozenIndicatorReceiptProxy = _PROXY_SCOPE["_FrozenIndicatorReceiptProxy"]


def _manager(received_at, source_bar_open):
    manager = object.__new__(epq.FastDataManager)
    manager.redis_manager = None
    manager.trade_manager = None
    manager.positions_service = None
    source = dt.datetime.fromtimestamp(source_bar_open, dt.timezone.utc).isoformat()
    manager._cold_data = {
        "1000PEPEUSDC": {
            "timestamp": source,
            "timestamp_15m": source,
            "timestamp_1m": source,
            "timestamp_3m": source,
        }
    }
    manager.shared_proxy = FrozenIndicatorReceiptProxy()
    manager.shared_proxy.set_symbol(
        "1000PEPEUSDC",
        {
            "_tick_ts": float(received_at),
            "timestamp": source,
            "timestamp_15m": source,
            "timestamp_1m": source,
            "timestamp_3m": source,
            "current_price": 1.0,
            "k_1m": 50.0,
            "d_1m": 50.0,
            "k_3m": 50.0,
            "d_3m": 50.0,
        },
    )
    return manager


def test_completed_frozen_bar_uses_receipt_clock_without_rewriting_source_time(monkeypatch):
    bar_open = 1_790_853_300.0
    decision_clock = bar_open + 900.0
    source_time = dt.datetime.fromtimestamp(bar_open, dt.timezone.utc).isoformat()
    monkeypatch.setattr(epq.time, "time", lambda: decision_clock)
    manager = _manager(decision_clock, bar_open)

    metrics, indicators, *_rest, is_fresh = asyncio.run(manager.get_hot_state("1000PEPEUSDC"))

    assert is_fresh is True
    assert metrics["_tick_ts"] == decision_clock
    assert indicators["timestamp_15m"] == source_time
    assert indicators["timestamp"] == source_time
    assert indicators["timestamp_1m"] == source_time
    assert indicators["timestamp_3m"] == source_time


def test_bar_open_tick_receipt_is_stale_at_completed_bar_decision(monkeypatch):
    bar_open = 1_790_853_300.0
    decision_clock = bar_open + 900.0
    monkeypatch.setattr(epq.time, "time", lambda: decision_clock)
    manager = _manager(bar_open, bar_open)

    metrics, _indicators, *_rest, is_fresh = asyncio.run(manager.get_hot_state("1000PEPEUSDC"))

    assert metrics["_tick_ts"] == bar_open
    assert is_fresh is False
