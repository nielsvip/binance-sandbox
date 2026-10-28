#!/usr/bin/env python3
import argparse
import asyncio
import fcntl
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import redis
from closed_bar_feed import aggregate_closed_15m, atomic_json, merge_closed_history, publish_closed_15m, timestamp_epoch
from tools.crypto_15m_bar_clock import ObservedMarks, emit, recent_rows
from tools.npz_cutoffs import is_rth_bar_start, stock_session_bounds


def stock_symbols(base):
    symbols = set()
    for account in ('tra', 'trb', 'trc'):
        for side in ('long', 'short'):
            path = Path(base) / f'symbols_{account}_{side}.json'
            if path.exists():
                rows = json.loads(path.read_text())
                if not isinstance(rows, list) or not all(isinstance(symbol, str) for symbol in rows):
                    raise ValueError(f'invalid stock book {path.name}')
                symbols.update(rows)
    return sorted(symbols)


def quote_mark(quote):
    bid, ask, last = (float(quote.get(key) or 0) for key in ('bid', 'ask', 'last'))
    price = (bid + ask) / 2 if 0 < bid <= ask else last
    return price if math.isfinite(price) and price > 0 else None


def quote_collection_window(now, session_bounds):
    return session_bounds is not None and session_bounds[0] - 3600 <= now < session_bounds[1]


def ingest_quotes(observed, quotes, received_at, now, source):
    count = 0
    for symbol, quote in quotes.items():
        try:
            price = quote_mark(quote)
            if price is None:
                continue
            stamp = received_at if received_at is not None else quote.get('received_at')
            if stamp is None:
                continue
            if observed.ingest(f'mark_price:{symbol}', json.dumps({'price': price, 'timestamp': stamp}), now):
                minute = int(observed.latest[symbol] // 60) * 60
                observed.bars[symbol][minute]['_quote_source'] = source
                count += 1
        except (TypeError, ValueError, KeyError):
            continue
    return count


def ingest_stream_event(observed, event, now):
    if not isinstance(event, dict) or event.get('type') not in ('quote', 'trade', 'tradex', 'timesale'):
        return 0
    symbol = event.get('symbol')
    if not symbol:
        return 0
    quote = dict(event, last=event.get('last') or event.get('price'))
    source_stamp = max((float(event.get(key) or 0) for key in ('biddate', 'askdate', 'date')), default=0)
    return ingest_quotes(observed, {symbol: quote}, source_stamp or now, now, 'tradier_stream')


async def stream_quote_events(api, observed, symbols_now, status):
    import websockets
    while True:
        try:
            now = time.time()
            day = datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date()
            if not quote_collection_window(now, stock_session_bounds(day)) or not symbols_now():
                status['connected'] = False
                await asyncio.sleep(1)
                continue
            session_id = await api.create_market_session()
            if not session_id:
                raise RuntimeError('market stream session unavailable')
            endpoint = str(api.config.TRADIER_WS_URL).rstrip('/') + '/markets/events'
            async with websockets.connect(endpoint, ping_interval=20, ping_timeout=20) as socket:
                status['connected'] = True
                status['error'] = None
                subscribed = ()
                while True:
                    wanted = tuple(symbols_now())
                    if wanted != subscribed:
                        await socket.send(json.dumps({'symbols': list(wanted), 'sessionid': session_id, 'filter': ['quote', 'trade', 'timesale'], 'validOnly': True}))
                        subscribed = wanted
                        status['subscribed'] = len(wanted)
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    events = json.loads(message)
                    for event in events if isinstance(events, list) else [events]:
                        if isinstance(event, dict) and event.get('error'):
                            raise RuntimeError(str(event['error'])[:120])
                        if ingest_stream_event(observed, event, time.time()):
                            status['last_quote_at'] = time.time()
                            status['accepted'] = status.get('accepted', 0) + 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status['connected'] = False
            status['error'] = f'{type(exc).__name__}: {str(exc)[:120]}'
            await asyncio.sleep(15)


def completed_stock_row(observed, prefix, start, now):
    if not is_rth_bar_start(start):
        return None
    bars = dict(prefix)
    bars.update({stamp: row for stamp, row in observed.items() if start <= stamp < start + 900})
    row = aggregate_closed_15m(bars, start, now)
    if row is not None:
        row.update(_producer='independent_stock_15m_clock', _producer_generation=2, _producer_pid=__import__('os').getpid(), _observed_minutes=sum(start <= stamp < start + 900 for stamp in observed), _native_prefix_intervals=len(prefix))
    return row


def native_prefix(base, symbol, start):
    rows = {}
    for interval, span in [('1m', 60), ('5m', 300)]:
        for folder in ('klines_canonical', 'klines_cache', 'klines_cache_gateway'):
            path = Path(base) / folder / 'tradier' / f'{symbol}_{interval}.json'
            for row in recent_rows(path):
                try:
                    stamp = timestamp_epoch(row['timestamp'])
                    if stamp < start or stamp + span > start + 780 or row.get('_provisional') or row.get('provisional'):
                        continue
                    prepared = row.get('_source') == f'tradier_native_{span // 60}min_prefix'
                    if folder != 'klines_canonical' and not prepared and float(row.get('_closed_verified_at') or 0) < stamp + span:
                        continue
                    prices = {key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
                    if all(math.isfinite(price) and price > 0 for price in prices.values()):
                        rows.setdefault((stamp, span), dict(prices, _span_s=span, _source=f'kline_{interval}'))
                except (TypeError, ValueError, KeyError):
                    continue
    return rows


async def main(base):
    base = Path(base)
    emit({'event': 'stock_clock_boot', 'at': time.time(), 'pid': __import__('os').getpid()})
    from utils import load_environment_from_gpg
    load_environment_from_gpg(None)
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    api = TradierAPIClient(TradierConfig(), account_key='tra')
    await api.connect()
    store = redis.Redis(host='127.0.0.1', port=6379, db=0, socket_connect_timeout=0.2, socket_timeout=0.2)
    observed = ObservedMarks()
    stream_status = {'connected': False, 'subscribed': 0, 'accepted': 0, 'last_quote_at': None, 'error': None}
    readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='stockprefix')
    publishers = ThreadPoolExecutor(max_workers=8, thread_name_prefix='stockpublish')
    loop = asyncio.get_running_loop()
    quote_tasks, native, preparing, pending, emitted = {}, {}, {}, {}, set()
    symbols, books_at, status_at, direct_at = [], 0, 0, 0
    local_quote_mtime, local_quote_at = 0, 0
    semaphore = asyncio.Semaphore(4)
    stream_task = asyncio.create_task(stream_quote_events(api, observed, lambda: symbols, stream_status)) if os.environ.get('STOCK_CLOCK_STREAM') == '1' else None
    async def fetch_batch(batch):
        async with semaphore:
            began = time.time()
            try:
                quotes = await asyncio.wait_for(api.get_quotes(batch), timeout=8)
                received = time.time()
                accepted = ingest_quotes(observed, quotes, received, received, 'tradier_direct_quote_response')
                return {'received_at': received, 'requested': len(batch), 'accepted': accepted, 'elapsed_s': received - began}
            except Exception as exc:
                return {'received_at': time.time(), 'requested': len(batch), 'accepted': 0, 'error': str(exc)}
    try:
        while True:
            began = now = time.time()
            bucket = int(now // 900) * 900
            target = bucket - 900
            if now - books_at >= 1:
                symbols = stock_symbols(base)
                books_at = now
            day = datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date()
            bounds = stock_session_bounds(day)
            in_session = bounds is not None and bounds[0] <= now < bounds[1]
            collecting_quotes = quote_collection_window(now, bounds)
            fresh = 0
            if collecting_quotes:
                try:
                    local_path = base / 'data/stock_quotes_latest.json'
                    local_mtime = local_path.stat().st_mtime_ns
                    if local_mtime != local_quote_mtime:
                        local_quote_mtime = local_mtime
                        payload = json.loads(local_path.read_text())
                        quotes = payload.get('data') or {}
                        local_quote_at = float(payload.get('received_at') or 0)
                        fresh += ingest_quotes(observed, {symbol: quotes[symbol] for symbol in symbols if symbol in quotes}, None, now, 's5_local_quote_collector')
                except (OSError, ValueError, TypeError):
                    pass
                try:
                    raw = store.get('tradier_prices_latest')
                    payload = json.loads(raw) if raw else {}
                    quotes = payload.get('data', payload)
                    fresh += ingest_quotes(observed, {symbol: quotes[symbol] for symbol in symbols if symbol in quotes}, None, now, 'tradier_prices_received_at')
                except (OSError, ValueError, TypeError, redis.RedisError):
                    pass
                if now - direct_at >= 10 and now - local_quote_at > 8:
                    missing = [symbol for symbol in symbols if now - observed.latest.get(symbol, 0) > 5]
                    for offset in range(0, len(missing), 50):
                        batch = missing[offset:offset + 50]
                        key = tuple(batch)
                        if key not in quote_tasks:
                            quote_tasks[key] = asyncio.create_task(fetch_batch(batch))
                    direct_at = now
            for key, task in list(quote_tasks.items()):
                if task.done():
                    emit({'event': 'stock_clock_quote_batch', **task.result()})
                    quote_tasks.pop(key)
            observed.prune(now)
            starts = ([bucket] if in_session and now - bucket >= 780 else []) + ([target] if is_rth_bar_start(target) else [])
            for start in starts:
                for symbol in symbols:
                    key = (symbol, start)
                    if key not in native and key not in preparing:
                        preparing[key] = loop.run_in_executor(readers, native_prefix, base, symbol, start)
            for key, future in list(preparing.items()):
                if future.done():
                    native[key] = future.result()
                    preparing.pop(key)
            for key, future in list(pending.items()):
                if future.done():
                    try:
                        future.result()
                        emitted.add(key)
                        emit({'event': 'stock_clock_published', 'symbol': key[0], 'bar_start_ts': key[1], 'available_at': time.time(), 'latency_s': time.time() - key[1] - 900})
                    except Exception as exc:
                        emit({'event': 'stock_clock_publish_error', 'symbol': key[0], 'error': str(exc), 'at': time.time()})
                    pending.pop(key)
            missing = []
            if is_rth_bar_start(target):
                for symbol in symbols:
                    key = (symbol, target)
                    if key in emitted or key in pending:
                        continue
                    row = completed_stock_row(observed.bars.get(symbol, {}), native.get(key, {}), target, now)
                    if row is None:
                        missing.append(symbol)
                        continue
                    pending[key] = loop.run_in_executor(publishers, publish_closed_15m, base / 'klines_cache/tradier' / f'{symbol}_15m.json', row, merge_closed_history, base, 'tradier', symbol)
            if now - status_at >= 5:
                status = {'at': now, 'pid': __import__('os').getpid(), 'event': 'stock_clock_status', 'in_session': in_session, 'collecting_quotes': collecting_quotes, 'session_bounds': bounds, 'universe': len(symbols), 'fresh_redis_quotes': fresh, 'symbols_observed': len(observed.bars), 'bar_start_ts': target, 'published': sum((symbol, target) in emitted for symbol in symbols), 'missing_coverage': missing, 'quote_batches_inflight': len(quote_tasks), 'stream': dict(stream_status), 'loop_s': time.time() - began}
                try:
                    atomic_json(base / 'data/reports/stock_15m_clock_latest.json', status)
                except OSError:
                    pass
                emit(status)
                status_at = now
            for mapping in (native,):
                for key in list(mapping):
                    if key[1] < bucket - 2700:
                        mapping.pop(key)
            emitted = {key for key in emitted if key[1] >= bucket - 2700}
            await asyncio.sleep(max(0, 0.1 - (time.time() - began)))
    finally:
        if stream_task is not None:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)
        for task in quote_tasks.values():
            task.cancel()
        await api.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=str(ROOT))
    args = parser.parse_args()
    base = Path(args.base)
    with (base / 'data/.stock_15m_bar_clock.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        asyncio.run(main(base))
