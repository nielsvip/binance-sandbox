#!/usr/bin/env python3
import argparse
import ast
import asyncio
import fcntl
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from closed_bar_feed import atomic_json, timestamp_epoch


def producer_helpers(base):
    path = Path(base) / 'tradier_prices.py'
    tree = ast.parse(path.read_text())
    names = {'_load_closed_15m_prefix', '_closed_15m_tradeable_symbols'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'Path': Path}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def missing_prefix(base, symbol, bucket, helpers):
    base = Path(base)
    dirs = [base / 'klines_cache' / 'tradier', base / 'klines_cache_gateway' / 'tradier', base / 'klines_canonical' / 'tradier']
    rows = helpers['_load_closed_15m_prefix'](symbol, bucket, dirs, '5m')
    covered = {minute for start, span in rows for minute in range(start, start + span, 60)}
    return {bucket + offset for offset in range(0, 780, 60)} - covered


def normalize_minutes(raw, missing, now_s, span_s=60):
    rows = []
    for item in raw or []:
        try:
            if item.get('timestamp') is not None:
                stamp = timestamp_epoch(item['timestamp'])
            else:
                parsed = datetime.fromisoformat(str(item['time']))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo('America/New_York'))
                stamp = int(parsed.timestamp())
            if stamp not in missing or stamp % span_s or stamp + span_s > now_s:
                continue
            values = {field: float(item[field]) for field in ('open', 'high', 'low', 'close', 'volume')}
            if not all(math.isfinite(value) for value in values.values()) or min(values[field] for field in ('open', 'high', 'low', 'close')) <= 0 or values['volume'] < 0:
                continue
            rows.append(dict(values, timestamp=datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), _source=f'tradier_native_{span_s // 60}min_prefix'))
        except (TypeError, ValueError, KeyError):
            continue
    return rows


def merge_native_minutes(base, symbol, incoming, timeframe='1m'):
    base = Path(base)
    if not incoming:
        return 0
    lock_path = base / 'data' / 'stock_prefix_locks' / f'{symbol}_{timeframe}.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    path = base / 'klines_cache' / 'tradier' / f'{symbol}_{timeframe}.json'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(existing, list):
            raise ValueError('existing one-minute history is invalid')
        merged = {timestamp_epoch(row['timestamp']): row for row in existing}
        for row in incoming:
            merged[timestamp_epoch(row['timestamp'])] = row
        if path.exists():
            latest = json.loads(path.read_text())
            for row in latest:
                stamp = timestamp_epoch(row['timestamp'])
                if stamp not in merged:
                    merged[stamp] = row
        atomic_json(path, [merged[stamp] for stamp in sorted(merged)])
    return len(incoming)


async def prepare_cycle(base, api, bucket, universe, helpers, now_fn=time.time, concurrency=4):
    semaphore = asyncio.Semaphore(concurrency)
    deadline = bucket + 895
    outcomes = []
    async def one(symbol):
        async with semaphore:
            result = {'symbol': symbol, 'bucket': bucket, 'missing_before': None, 'requested': False, 'request_count': 0, 'filled': 0}
            if deadline - now_fn() <= 0:
                result.update(status='DEADLINE', at=now_fn())
                return result
            missing = await asyncio.to_thread(missing_prefix, base, symbol, bucket, helpers)
            result['missing_before'] = len(missing)
            if not missing:
                result.update(status='READY', at=now_fn())
                return result
            remaining = deadline - now_fn()
            if remaining <= 0:
                result.update(status='DEADLINE', at=now_fn())
                return result
            result['requested'] = True
            result['request_count'] += 1
            began = now_fn()
            try:
                date = datetime.fromtimestamp(bucket, timezone.utc).strftime('%Y-%m-%d')
                raw = await asyncio.wait_for(api.get_timesales(symbol, interval='1min', start=date), timeout=min(20, remaining))
                rows = normalize_minutes(raw, missing, now_fn())
                result['filled'] = await asyncio.to_thread(merge_native_minutes, base, symbol, rows)
                left = await asyncio.to_thread(missing_prefix, base, symbol, bucket, helpers)
                coarse_starts = {bucket + ((stamp - bucket) // 300) * 300 for stamp in left if bucket + ((stamp - bucket) // 300) * 300 + 300 <= bucket + 780}
                remaining = deadline - now_fn()
                if left and coarse_starts and remaining > 0:
                    result['request_count'] += 1
                    raw_coarse = await asyncio.wait_for(api.get_timesales(symbol, interval='5min', start=date), timeout=min(20, remaining))
                    coarse = normalize_minutes(raw_coarse, coarse_starts, now_fn(), span_s=300)
                    result['coarse_filled'] = await asyncio.to_thread(merge_native_minutes, base, symbol, coarse, '5m')
                    left = await asyncio.to_thread(missing_prefix, base, symbol, bucket, helpers)
                result.update(status='READY' if not left else 'MISSING', missing_after=len(left), request_s=round(now_fn() - began, 3), at=now_fn())
            except Exception as exc:
                result.update(status='ERROR', error=f'{type(exc).__name__}: {exc}', at=now_fn())
            return result
    tasks = [asyncio.create_task(one(symbol)) for symbol in universe]
    try:
        for task in asyncio.as_completed(tasks):
            result = await task
            outcomes.append(result)
            print(json.dumps({'event': 'stock_prefix_result', **result}), flush=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    summary = {'bucket': bucket, 'at': now_fn(), 'universe': len(universe), 'ready': sum(result['status'] == 'READY' for result in outcomes), 'requested': sum(result['requested'] for result in outcomes), 'requests': sum(result['request_count'] for result in outcomes), 'results': outcomes}
    atomic_json(Path(base) / 'data' / 'reports' / 'stock_closed_15m_prepare_latest.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key != 'results'}), flush=True)
    return summary


def in_session(timestamp):
    local = datetime.fromtimestamp(timestamp, ZoneInfo('America/New_York'))
    return local.weekday() < 5 and (9, 30) <= (local.hour, local.minute) < (16, 0)


async def main(base, once_bucket=None):
    boot = time.time()
    print(json.dumps({'event': 'boot_import_start', 'at': boot}), flush=True)
    from utils import load_environment_from_gpg
    print(json.dumps({'event': 'utils_imported', 'at': time.time(), 'elapsed_s': time.time() - boot}), flush=True)
    load_environment_from_gpg(None)
    from config_tradier import TradierConfig
    print(json.dumps({'event': 'config_imported', 'at': time.time(), 'elapsed_s': time.time() - boot}), flush=True)
    from tradier_api import TradierAPIClient
    print(json.dumps({'event': 'api_imported', 'at': time.time(), 'elapsed_s': time.time() - boot}), flush=True)
    api = TradierAPIClient(TradierConfig(), account_key='tra')
    await api.connect()
    completed = set()
    try:
        while True:
            now = time.time()
            bucket = int(now // 900) * 900 if once_bucket is None else once_bucket
            if once_bucket is not None or (in_session(now) and 780 <= now - bucket < 895 and bucket not in completed):
                helpers = producer_helpers(base)
                universe = helpers['_closed_15m_tradeable_symbols'](base, [], True)
                await prepare_cycle(base, api, bucket, universe, helpers)
                completed.add(bucket)
                if once_bucket is not None:
                    return
            await asyncio.sleep(0.2 if 779 <= now - bucket < 781 else 1)
    finally:
        await api.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=str(ROOT))
    parser.add_argument('--once-bucket', type=int)
    args = parser.parse_args()
    lock_path = Path(args.base) / 'data' / '.stock_closed_15m_prepare.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        asyncio.run(main(args.base, args.once_bucket))
