import ast
import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Set

SOURCE = Path(__file__).resolve().parents[1] / "data/reports/position_chain_20261031/ez_positions_service.proposed.py"


def methods(**extra):
    tree = ast.parse(SOURCE.read_text())
    selected = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name in {"_ensure_trade_manager", "process_account_update"}]
    module = ast.Module(body=selected, type_ignores=[])
    env = {"asyncio": asyncio, "logger": logging.getLogger(__name__), "datetime": datetime, "timezone": timezone, "Optional": Optional, "Set": Set, "config": SimpleNamespace(VERBOSE_FETCH_LOGGING=False), "_failover_writer_is_standby": lambda account: False, **extra}
    exec(compile(module, str(SOURCE), "exec"), env)
    return env


def test_constructor_does_not_block_websocket_event_loop():
    async def scenario():
        ticks = []
        def construct(**kwargs):
            time.sleep(0.12)
            return "manager"
        env = methods(_get_trade_manager_class=lambda: construct)
        obj = SimpleNamespace(trade_manager=None, accounts={"ang": {}})
        task = asyncio.create_task(env["_ensure_trade_manager"](obj))
        await asyncio.sleep(0.02)
        ticks.append(task.done())
        await task
        assert ticks == [False] and obj.trade_manager == "manager"
    asyncio.run(scenario())


def test_cancelled_caller_cannot_spawn_duplicate_manager():
    async def scenario():
        built = []
        def construct(**kwargs):
            built.append(1)
            time.sleep(0.08)
            return "manager"
        env = methods(_get_trade_manager_class=lambda: construct)
        obj = SimpleNamespace(trade_manager=None, accounts={"ang": {}})
        task = asyncio.create_task(env["_ensure_trade_manager"](obj))
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await env["_ensure_trade_manager"](obj)
        assert built == [1] and obj.trade_manager == "manager"
    asyncio.run(scenario())


def test_confirmed_positions_persist_before_slow_stop_maintenance(tmp_path):
    async def scenario():
        calls, stop_release = [], asyncio.Event()
        async def save(*args, **kwargs):
            calls.append("save")
            return True
        async def noop(*args, **kwargs):
            pass
        async def update(account, raw, now, keys, positions, **kwargs):
            calls.append("update")
            keys.add("ang:X_LONG")
        async def stop(*args):
            calls.append("stop")
            await stop_release.wait()
        ready = asyncio.Event()
        ready.set()
        obj = SimpleNamespace(_loading_complete_event=ready, positions_by_account={"ang": {"ang:X_LONG": {}}}, extract_positions_data=lambda data: data, _get_expected_symbol_count=lambda: 0, _check_for_deletions=noop, _process_account_update_impl=update, _mark_positions_dirty=lambda: None, _broadcast_positions_to_redis=noop, get_position_file=lambda *args: tmp_path / "absent.json", _last_api_seen={}, _fixed_stop_fetch_cycle=stop)
        env = methods(save_positions_guarded=save)
        result = await asyncio.wait_for(env["process_account_update"](obj, "ang", [{}]), timeout=0.5)
        assert result == {"ang:X_LONG"} and calls[:2] == ["update", "save"]
        await asyncio.sleep(0)
        stop_release.set()
        await obj._fixed_stop_cycle_tasks["ang"]
        assert calls == ["update", "save", "stop"]
    asyncio.run(scenario())
