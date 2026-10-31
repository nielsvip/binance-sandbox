import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace


def test_frozen_replay_never_starts_external_feed_workers():
    source = ast.parse(Path('backtest_v12_engine.py').read_text())
    node = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_isolate_frozen_data_feeds')
    scope = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'backtest_v12_engine.py', 'exec'), scope)
    async def external_feed(self):
        raise AssertionError('external market feed reached during frozen replay')
    class Bridge:
        _hot_path_loop = _cold_data_loop = _redis_fallback_loop = _refresh_from_source = external_feed
        def trading_decision(self):
            return 'preserved'
    class Service:
        _periodic_phantom_kill = external_feed
    class Manager:
        _maintain_shared_memory_connection = _maintain_cold_data_sync = external_feed
    scope['_isolate_frozen_data_feeds'](SimpleNamespace(IndicatorsBridge=Bridge, PositionService=Service), SimpleNamespace(FastDataManager=Manager))
    async def exercise():
        for obj, names in ((Bridge(), ('_hot_path_loop', '_cold_data_loop', '_redis_fallback_loop', '_refresh_from_source')), (Manager(), ('_maintain_shared_memory_connection', '_maintain_cold_data_sync')), (Service(), ('_periodic_phantom_kill',))):
            for name in names:
                assert await getattr(obj, name)() is None
    asyncio.run(exercise())
    assert Bridge().trading_decision() == 'preserved'
