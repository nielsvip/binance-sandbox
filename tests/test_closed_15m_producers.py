import ast
import asyncio
import json
import logging
import os
import tempfile
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_functions(filename, names, base):
    tree = ast.parse((ROOT / filename).read_text())
    nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    namespace = dict(asyncio=asyncio, json=json, orjson=orjson, logging=logging, logger=logging.getLogger('test_closed'), os=os, tempfile=tempfile, time=time, OrderedDict=OrderedDict, ThreadPoolExecutor=ThreadPoolExecutor, datetime=datetime, timedelta=timedelta, timezone=timezone, Path=Path, BASE_PATH=base)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)
    return namespace


def minute_bars(start):
    return {start + minute * 60: {'open': 100.0 + minute, 'high': 101.0 + minute, 'low': 99.0 + minute, 'close': 100.5 + minute} for minute in range(15)}


@pytest.mark.parametrize('filename,name', [('ez_market_data.py', '_write_provisional_15m'), ('tradier_prices.py', '_write_provisional_stock_15m')])
def test_native_bar_and_completed_bar_survive_late_forming_writer(tmp_path, filename, name):
    ns = load_functions(filename, {name, name + '_unlocked'}, tmp_path)
    path = tmp_path / 'TEST_15m.json'
    native = {'timestamp': '2026-10-26T18:00:00+00:00', 'open': 1, 'high': 3, 'low': 1, 'close': 2, 'volume': 0}
    path.write_text(json.dumps([native]))
    provisional = dict(native, timestamp='2026-10-26T18:00:00.000000Z', close=999, _provisional=True)
    assert ns[name](path, provisional) is False
    assert json.loads(path.read_text()) == [native]
    completed = dict(provisional, _closed=True)
    path.write_text(json.dumps([completed]))
    assert ns[name](path, provisional) is False
    assert json.loads(path.read_text()) == [completed]


async def run_clock_once(ns, method, obj, now):
    async def stop(_):
        obj.running = False
    ns['time'] = SimpleNamespace(time=lambda: now, time_ns=time.time_ns)
    ns['asyncio'] = SimpleNamespace(get_running_loop=asyncio.get_running_loop, gather=asyncio.gather, sleep=stop)
    from unittest.mock import patch
    with patch('closed_bar_feed.time.time', return_value=now):
        await ns[method](obj)


@pytest.mark.asyncio
async def test_crypto_publishes_previous_bucket_at_boundary_without_new_quote(tmp_path):
    names = {'_write_provisional_15m', '_write_provisional_15m_unlocked', 'closed_15m_writeback'}
    ns = load_functions('ez_market_data.py', names, tmp_path)
    start = int(datetime(2026, 10, 26, 18, 15, tzinfo=timezone.utc).timestamp())
    obj = SimpleNamespace(running=True, symbols=['TESTUSDT'], market=SimpleNamespace(data={'TESTUSDT': {'ohlc_1m': minute_bars(start)}}))
    await run_clock_once(ns, 'closed_15m_writeback', obj, start + 900)
    event = json.loads(next((tmp_path / 'data/closed_15m_outbox/crypto').glob('*.json')).read_text())
    assert event['bar_start_ts'] == start and event['bar_close_ts'] == start + 900
    assert event['row']['open'] == 100 and event['row']['close'] == 114.5
    assert event['row']['_closed'] is True and event['row']['_provisional'] is True
    assert 'volume' not in event['row']


@pytest.mark.asyncio
async def test_stock_final_bucket_publishes_at_1600_even_without_new_quote(tmp_path):
    names = {'_write_provisional_stock_15m', '_write_provisional_stock_15m_unlocked', 'closed_15m_clock_loop'}
    ns = load_functions('tradier_prices.py', names, tmp_path)
    start = int(datetime(2026, 10, 26, 19, 45, tzinfo=timezone.utc).timestamp())
    obj = SimpleNamespace(running=True, config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=tmp_path / 'klines_cache/tradier'), _provisional_15m={'TEST': {'bucket_ts': start, 'minute_bars': minute_bars(start)}})
    await run_clock_once(ns, 'closed_15m_clock_loop', obj, start + 900)
    event = json.loads(next((tmp_path / 'data/closed_15m_outbox/tradier').glob('*.json')).read_text())
    assert event['bar_close_ts'] == start + 900
    assert event['row']['close'] == 114.5


@pytest.mark.asyncio
async def test_missing_minute_is_reported_and_never_published(tmp_path, caplog):
    names = {'_write_provisional_15m', '_write_provisional_15m_unlocked', 'closed_15m_writeback'}
    ns = load_functions('ez_market_data.py', names, tmp_path)
    start = int(datetime(2026, 10, 26, 18, 15, tzinfo=timezone.utc).timestamp())
    bars = minute_bars(start)
    bars.pop(start + 60)
    obj = SimpleNamespace(running=True, symbols=['TESTUSDT'], market=SimpleNamespace(data={'TESTUSDT': {'ohlc_1m': bars}}))
    await run_clock_once(ns, 'closed_15m_writeback', obj, start + 900)
    assert 'CLOSED_15M_MISSING' in caplog.text
    assert not (tmp_path / 'data/closed_15m_outbox').exists()


@pytest.mark.asyncio
async def test_stock_rollover_buffers_quote_without_waiting_for_disk(tmp_path):
    names = {'_write_provisional_15m', '_timestamp_epoch', '_tradier_15m_bucket'}
    ns = load_functions('tradier_prices.py', names, tmp_path)
    now = int(datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc).timestamp())
    def slow_write(path, row):
        time.sleep(0.2)
        return True
    ns['_write_provisional_stock_15m'] = slow_write
    ns['time'] = SimpleNamespace(time=lambda: now, time_ns=time.time_ns)
    obj = SimpleNamespace(config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=tmp_path), _provisional_15m={'TEST': {'bucket_ts': now - 900, 'minute_bars': minute_bars(now - 900)}}, _provisional_15m_sig={}, _provisional_write_sem=asyncio.Semaphore(16), fetcher_id='test')
    began = time.perf_counter()
    await ns['_write_provisional_15m'](obj, {'TEST': {'price': 120, 'timestamp': now}})
    assert time.perf_counter() - began < 0.1
    assert obj._provisional_15m['TEST']['bucket_ts'] == now
    assert ('TEST', now - 900) in obj._pending_closed_15m
    await asyncio.gather(*obj._provisional_io_tasks.values())


@pytest.mark.asyncio
async def test_stock_main_propagates_feed_failure_for_supervisor(tmp_path):
    ns = load_functions('tradier_prices.py', {'main'}, tmp_path)
    import signal
    stopped = []
    class FailedFetcher:
        async def start(self):
            raise RuntimeError('quote task failed')
        async def stop(self):
            stopped.append(True)
    ns.update(TradierPriceFetcher=FailedFetcher, signal=signal, stop_event=asyncio.Event())
    ns['asyncio'] = SimpleNamespace(get_running_loop=lambda: SimpleNamespace(add_signal_handler=lambda *args: None), create_task=asyncio.create_task, wait=asyncio.wait, FIRST_COMPLETED=asyncio.FIRST_COMPLETED, gather=asyncio.gather)
    with pytest.raises(RuntimeError, match='quote task failed'):
        await ns['main']()
    assert stopped == [True]


@pytest.mark.asyncio
async def test_stock_main_preserves_requested_shutdown(tmp_path):
    ns = load_functions('tradier_prices.py', {'main'}, tmp_path)
    import signal
    stopped = []
    class WaitingFetcher:
        async def start(self):
            await asyncio.Event().wait()
        async def stop(self):
            stopped.append(True)
    stop = asyncio.Event()
    stop.set()
    ns.update(TradierPriceFetcher=WaitingFetcher, signal=signal, stop_event=stop)
    ns['asyncio'] = SimpleNamespace(get_running_loop=lambda: SimpleNamespace(add_signal_handler=lambda *args: None), create_task=asyncio.create_task, wait=asyncio.wait, FIRST_COMPLETED=asyncio.FIRST_COMPLETED, gather=asyncio.gather)
    await ns['main']()
    assert stopped == [True]


def test_crypto_ohlc_hydration_preserves_high_low_and_skips_close_only_rows(tmp_path):
    import numpy as np
    names = {'load_ohlc_history'}
    ns = load_functions('ez_market_data.py', names, tmp_path)
    ns.update(KLINES_DIRS=[tmp_path], resilient_json_load=lambda path: json.loads(path.read_text()), np=np)
    path = tmp_path / 'TEST_1m.json'
    path.write_text(json.dumps([{'timestamp': '2026-10-26T18:30:00Z', 'open': 100, 'high': 120, 'low': 90, 'close': 110}, {'timestamp': '2026-10-26T18:31:00Z', 'close': 111}]))
    rows = ns['load_ohlc_history']('TEST', '1m')
    assert len(rows) == 1
    assert next(iter(rows.values())) == {'open': 100, 'high': 120, 'low': 90, 'close': 110}


def test_synthetic_price_cannot_overwrite_previous_closed_minute(tmp_path):
    names = {'update_synthetic', 'update_1m_candle', 'update_3m_candle'}
    ns = load_functions('ez_market_data.py', names, tmp_path)
    now = int(datetime(2026, 10, 26, 18, 45, tzinfo=timezone.utc).timestamp())
    prior = now - 60
    store = {'closes_1m': OrderedDict([(prior, 100)]), 'closes_3m': OrderedDict([(now - 180, 100)]), 'ohlc_1m': OrderedDict([(prior, {'open': 99, 'high': 101, 'low': 98, 'close': 100})])}
    obj = SimpleNamespace(data={'TEST': store})
    ns['time'] = SimpleNamespace(time=lambda: now)
    ns['update_synthetic'](obj, 'TEST', 999, now - 0.5)
    assert store['closes_1m'][prior] == 100
    assert store['ohlc_1m'][prior] == {'open': 99, 'high': 101, 'low': 98, 'close': 100}
    ns['update_synthetic'](obj, 'TEST', 110, now)
    assert store['closes_1m'][now] == 110 and store['closes_1m'][prior] == 100
    assert store['ohlc_1m'][prior]['close'] == 100
