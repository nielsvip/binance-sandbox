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
    namespace = dict(Dict=dict, asyncio=asyncio, json=json, orjson=orjson, logging=logging, logger=logging.getLogger('test_closed'), os=os, tempfile=tempfile, time=time, OrderedDict=OrderedDict, ThreadPoolExecutor=ThreadPoolExecutor, datetime=datetime, timedelta=timedelta, timezone=timezone, Path=Path, BASE_PATH=base)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)
    return namespace


def minute_bars(start):
    return {start + minute * 60: {'open': 100.0 + minute, 'high': 101.0 + minute, 'low': 99.0 + minute, 'close': 100.5 + minute} for minute in range(15)}


@pytest.mark.parametrize('filename,name', [('ez_market_data.py', '_write_provisional_15m'), ('tradier_prices.py', '_write_provisional_stock_15m')])
def test_native_bar_and_completed_bar_survive_late_forming_writer(tmp_path, filename, name):
    ns = load_functions(filename, {name, name + '_unlocked'}, tmp_path)
    path = tmp_path / 'TEST_15m.json'
    native = {'timestamp': '2026-10-26T18:00:00+00:00', 'open': 1, 'high': 3, 'low': 1, 'close': 2, 'volume': 0, '_closed_verified_at': datetime(2026, 10, 26, 18, 15, tzinfo=timezone.utc).timestamp()}
    path.write_text(json.dumps([native]))
    provisional = dict(native, timestamp='2026-10-26T18:00:00.000000Z', close=999, _provisional=True)
    assert ns[name](path, provisional) is False
    assert json.loads(path.read_text()) == [native]
    completed = dict(provisional, _closed=True)
    path.write_text(json.dumps([completed]))
    assert ns[name](path, provisional) is False
    assert json.loads(path.read_text()) == [completed]


@pytest.mark.parametrize('filename,name', [('ez_market_data.py', '_write_provisional_15m'), ('tradier_prices.py', '_write_provisional_stock_15m')])
def test_unverified_zero_volume_never_blocks_completed_marks(tmp_path, filename, name):
    ns = load_functions(filename, {name, name + '_unlocked'}, tmp_path)
    path = tmp_path / 'TEST_15m.json'
    unverified = {'timestamp': '2026-10-26T18:00:00+00:00', 'open': 1, 'high': 3, 'low': 1, 'close': 2, 'volume': 0}
    path.write_text(json.dumps([unverified]))
    completed = dict(unverified, timestamp='2026-10-26T18:00:00.000000Z', close=999, _provisional=True, _closed=True)
    assert ns[name](path, completed)
    assert json.loads(path.read_text())[0]['close'] == 999


async def run_clock_once(ns, method, obj, now):
    async def stop(_):
        obj.running = False
    ns['time'] = SimpleNamespace(time=lambda: now, time_ns=time.time_ns)
    ns['asyncio'] = SimpleNamespace(get_running_loop=asyncio.get_running_loop, gather=asyncio.gather, sleep=stop, Semaphore=asyncio.Semaphore)
    from unittest.mock import patch
    with patch('closed_bar_feed.time.time', return_value=now):
        await ns[method](obj)


@pytest.mark.asyncio
async def test_crypto_publishes_previous_bucket_at_boundary_without_new_quote(tmp_path):
    names = {'_write_provisional_15m', '_write_provisional_15m_unlocked', 'closed_15m_writeback'}
    ns = load_functions('ez_market_data.py', names, tmp_path)
    start = int(datetime(2026, 10, 26, 18, 15, tzinfo=timezone.utc).timestamp())
    obj = SimpleNamespace(running=True, symbols=['TESTUSDT'], market=SimpleNamespace(data={'TESTUSDT': {'ohlc_1m': minute_bars(start)}}), _closed_15m_prepared={('TESTUSDT', start): {(stamp, 60): bar for stamp, bar in minute_bars(start).items() if stamp < start + 780}})
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
    obj = SimpleNamespace(running=True, symbols=['TEST'], config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=tmp_path / 'klines_cache/tradier'), _provisional_15m={'TEST': {'bucket_ts': start, 'minute_bars': minute_bars(start)}}, _closed_15m_prepared={('TEST', start): {(stamp, 60): bar for stamp, bar in minute_bars(start).items() if stamp < start + 780}})
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


@pytest.mark.parametrize('span,prefix_count,minute_start', [(180, 4, 12), (300, 2, 10)])
def test_exact_coarse_klines_plus_final_mark_minutes_preserve_ohlc(span, prefix_count, minute_start):
    from closed_bar_feed import aggregate_closed_15m
    start = 1793040300
    bars = {(start + index * span, span): {'open': 100 + index, 'high': 180 + index, 'low': 80 - index, 'close': 100.5 + index, '_span_s': span} for index in range(prefix_count)}
    bars.update({start + minute * 60: {'open': 100 + minute, 'high': 101 + minute, 'low': 99 + minute, 'close': 100.5 + minute} for minute in range(minute_start, 15)})
    row = aggregate_closed_15m(bars, start, start + 900)
    assert row['open'] == 100 and row['close'] == 114.5
    assert row['high'] == 180 + prefix_count - 1
    assert row['low'] == 80 - prefix_count + 1
    assert 'volume' not in row
    assert aggregate_closed_15m(bars, start, start + 899.999) is None
    bars.pop(start + 14 * 60)
    assert aggregate_closed_15m(bars, start, start + 900) is None


@pytest.mark.parametrize('filename,secondary,span', [('ez_market_data.py', '3m', 180), ('tradier_prices.py', '5m', 300)])
def test_min13_prefix_loader_uses_only_completed_genuine_ohlc(tmp_path, filename, secondary, span):
    ns = load_functions(filename, {'_load_closed_15m_prefix'}, tmp_path)
    start = 1793040300
    rows = []
    for offset in range(0, 900, span):
        rows.append({'timestamp': datetime.fromtimestamp(start + offset, timezone.utc).isoformat(), 'open': 100, 'high': 190, 'low': 80, 'close': 110})
    (tmp_path / f'TEST_{secondary}.json').write_text(json.dumps(rows))
    (tmp_path / 'TEST_1m.json').write_text(json.dumps([{'timestamp': datetime.fromtimestamp(start + 720, timezone.utc).isoformat(), 'open': 100, 'high': 120, 'low': 90, 'close': 110}]))
    result = ns['_load_closed_15m_prefix']('TEST', start, [tmp_path], secondary)
    assert result[(start, span)]['high'] == 190
    assert all(stamp + duration <= start + 780 for stamp, duration in result)
    assert (start + 720, 60) in result
    assert (start + 720, span) not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,stock', [('ez_market_data.py', False), ('tradier_prices.py', True)])
async def test_preparation_runs_at_minute13_and_loads_prefix_before_boundary(tmp_path, filename, stock):
    names = {'prepare_closed_15m_loop', '_load_closed_15m_prefix', '_closed_15m_tradeable_symbols'}
    ns = load_functions(filename, names, tmp_path)
    start = 1793040300
    cache = tmp_path / 'klines_cache'
    if stock:
        cache = cache / 'tradier'
    cache.mkdir(parents=True)
    rows = [dict(bar, timestamp=datetime.fromtimestamp(stamp, timezone.utc).isoformat()) for stamp, bar in minute_bars(start).items() if stamp < start + 780]
    (cache / 'TEST_1m.json').write_text(json.dumps(rows))
    obj = SimpleNamespace(running=True, symbols=['TEST'], config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=cache))
    await run_clock_once(ns, 'prepare_closed_15m_loop', obj, start + 780)
    assert len(obj._closed_15m_prepared[('TEST', start)]) == 13
    assert obj._closed_15m_universe == ['TEST']


@pytest.mark.asyncio
async def test_stock_capture_uses_fresh_receipt_while_preserving_old_exchange_timestamp(tmp_path):
    names = {'_write_provisional_15m', '_timestamp_epoch', '_tradier_15m_bucket'}
    ns = load_functions('tradier_prices.py', names, tmp_path)
    now = int(datetime(2026, 10, 26, 19, 13, tzinfo=timezone.utc).timestamp())
    ns['time'] = SimpleNamespace(time=lambda: now)
    ns['_write_provisional_stock_15m'] = lambda path, row: True
    bucket = now - 13 * 60
    obj = SimpleNamespace(config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=tmp_path), _provisional_15m={'FRESH': {'bucket_ts': bucket, 'minute_bars': {}}, 'STALE': {'bucket_ts': bucket, 'minute_bars': {}}}, _provisional_15m_sig={}, _provisional_write_sem=asyncio.Semaphore(16), fetcher_id='test')
    await ns['_write_provisional_15m'](obj, {'FRESH': {'price': 120, 'timestamp': now - 3600, 'received_at': now}, 'STALE': {'price': 999, 'timestamp': now - 3600, 'received_at': now - 30}})
    assert obj._provisional_15m['FRESH']['minute_bars'][now]['close'] == 120
    assert obj._provisional_15m['STALE']['minute_bars'] == {}
    await asyncio.gather(*obj._provisional_io_tasks.values())


@pytest.mark.asyncio
async def test_stock_fast_batch_updates_capture_cache_before_slow_batch_and_failed_cache_not_restamped(tmp_path):
    ns = load_functions('tradier_prices.py', {'fetch_quotes', '_timestamp_epoch'}, tmp_path)
    slow = asyncio.Event()
    old = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
    class API:
        async def get_quotes(self, symbols):
            if symbols == ['S50']:
                await slow.wait()
                return {}
            return {symbol: {'bid': 100, 'ask': 102, 'last': 90, 'volume': 1000, 'date': old * 1000} for symbol in symbols}
    cached = {'price': 10, 'timestamp': old, 'received_at': old}
    obj = SimpleNamespace(api_client=API(), symbols=[f'S{i}' for i in range(51)], price_cache={'S50': cached})
    task = asyncio.create_task(ns['fetch_quotes'](obj))
    for _ in range(20):
        if 'S0' in obj.price_cache:
            break
        await asyncio.sleep(0.005)
    assert not task.done()
    assert obj.price_cache['S0']['price'] == 101
    assert obj.price_cache['S0']['timestamp'].startswith('2020-01-01')
    assert obj.price_cache['S0']['received_at'] > old
    slow.set()
    result = await task
    assert result['S50'] is cached and result['S50']['received_at'] == old


@pytest.mark.parametrize('filename,writer', [('ez_market_data.py', '_write_provisional_15m'), ('tradier_prices.py', '_write_provisional_stock_15m')])
def test_full_history_forming_worker_refuses_critical_window_and_stale_queued_bar(tmp_path, filename, writer):
    ns = load_functions(filename, {'_forming_15m_allowed', '_write_forming_15m'}, tmp_path)
    base = int(datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc).timestamp())
    calls = []
    ns[writer] = lambda path, row: calls.append(row) or True
    row = {'timestamp': datetime.fromtimestamp(base, timezone.utc).isoformat()}
    for elapsed, allowed in [(0, False), (29.999, False), (30, True), (719.999, True), (720, False), (899.999, False)]:
        ns['time'] = SimpleNamespace(time=lambda elapsed=elapsed: base + elapsed)
        before = len(calls)
        assert ns['_write_forming_15m'](tmp_path / 'TEST_15m.json', row) is allowed
        assert len(calls) - before == int(allowed)
    ns['time'] = SimpleNamespace(time=lambda: base + 931)
    assert ns['_write_forming_15m'](tmp_path / 'TEST_15m.json', row) is False


@pytest.mark.asyncio
async def test_crypto_mark_sampler_captures_fresh_price_without_history_or_disk(tmp_path):
    ns = load_functions('ez_market_data.py', {'closed_15m_mark_capture_loop'}, tmp_path)
    now = int(datetime(2026, 10, 26, 19, 13, tzinfo=timezone.utc).timestamp())
    obj = SimpleNamespace(running=True, symbols=['LIVE', 'STALE'], price_buffer={'LIVE': {'p': 123, 't': now - 0.1}, 'STALE': {'p': 999, 't': now - 30}})
    await run_clock_once(ns, 'closed_15m_mark_capture_loop', obj, now)
    assert obj._closed_15m_mark_minutes['LIVE'][now]['close'] == 123
    assert 'STALE' not in obj._closed_15m_mark_minutes
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_stock_critical_capture_never_reads_history_or_writes_forming_file(tmp_path):
    names = {'_write_provisional_15m', '_timestamp_epoch', '_tradier_15m_bucket', '_forming_15m_allowed'}
    ns = load_functions('tradier_prices.py', names, tmp_path)
    now = int(datetime(2026, 10, 26, 19, 13, tzinfo=timezone.utc).timestamp())
    ns['time'] = SimpleNamespace(time=lambda: now)
    def forbidden(*args):
        raise AssertionError('critical mark capture must not access disk')
    ns['_load_cached_subbars'] = forbidden
    ns['_write_forming_15m'] = forbidden
    obj = SimpleNamespace(symbols=['LIVE'], config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=tmp_path), _provisional_15m={}, _provisional_15m_sig={}, _provisional_write_sem=asyncio.Semaphore(2), fetcher_id='test')
    await ns['_write_provisional_15m'](obj, {'LIVE': {'price': 120, 'timestamp': now - 3600, 'received_at': now}})
    assert obj._provisional_15m['LIVE']['minute_bars'][now]['close'] == 120
    assert obj._provisional_io_tasks == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_stock_forming_write_once_per_minute_tradeables_only_while_all_quotes_capture(tmp_path):
    names = {'_write_provisional_15m', '_timestamp_epoch', '_tradier_15m_bucket', '_forming_15m_allowed', '_write_forming_15m'}
    ns = load_functions('tradier_prices.py', names, tmp_path)
    base = int(datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc).timestamp())
    clock = [base + 120]
    ns['time'] = SimpleNamespace(time=lambda: clock[0])
    calls = []
    ns['_write_provisional_stock_15m'] = lambda path, row: calls.append(path.name) or True
    obj = SimpleNamespace(symbols=['LIVE', 'OTHER'], config=SimpleNamespace(BASE_PATH=tmp_path, KLINES_CACHE_DIR=tmp_path), _closed_15m_universe=['LIVE'], _provisional_15m={}, _provisional_15m_sig={}, _provisional_write_sem=asyncio.Semaphore(2), fetcher_id='test')
    for elapsed, price in [(120, 100), (150, 101), (180, 102)]:
        clock[0] = base + elapsed
        await ns['_write_provisional_15m'](obj, {symbol: {'price': price, 'timestamp': clock[0], 'received_at': clock[0]} for symbol in obj.symbols})
        await asyncio.gather(*obj._provisional_io_tasks.values())
    assert calls == ['LIVE_15m.json', 'LIVE_15m.json']
    assert obj._provisional_15m['OTHER']['minute_bars'][base + 180]['close'] == 102
