import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace


def test_native_queue_finishes_before_clock_advances():
    source = ast.parse(Path('ez_manage.py').read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'OrderQueue')
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'process_orders')
    logger = SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None)
    scope = {'asyncio': asyncio, 'logger': logger, 'construct_position_key': lambda a, s, p: f'{a}:{s}_{p}'}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'ez_manage.py', 'exec'), scope)
    async def run():
        clock = [100]
        fills = []
        async def handle_order(order):
            for _ in range(4):
                await asyncio.sleep(0)
            fills.append(clock[0])
        owner = SimpleNamespace(_orders=asyncio.Queue(), handle_order=handle_order)
        worker = asyncio.create_task(scope['process_orders'](owner))
        await owner._orders.put({'account_key':'ang','symbol':'TEST','position_side':'LONG','quantity':1,'current_price':1})
        await owner._orders.join()
        clock[0] = 1000
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        assert fills == [100]
    asyncio.run(run())
