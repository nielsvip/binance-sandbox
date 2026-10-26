#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import redis
from closed_bar_feed import aggregate_closed_15m, atomic_json, is_verified_closed_native, merge_closed_history, publish_closed_15m, timestamp_epoch

STABLE = ('USDT', 'USDC', 'USD1', 'USDS', 'BUSD', 'FDUSD', 'TUSD', 'DAI')


def tradeable_symbols(base):
    base = Path(base)
    try:
        rows = json.loads((base / 'tradeable_keys.json').read_text())
        if not isinstance(rows, list) or not all(isinstance(item, str) for item in rows):
            raise ValueError('invalid tradeable book')
        symbols = {item.split(':')[-1].rsplit('_', 1)[0] for item in rows}
        return sorted(symbol for symbol in symbols if symbol.endswith(STABLE)), 'tradeable_keys'
    except (OSError, ValueError, TypeError):
        rows = json.loads((base / 'symbols_active.json').read_text())
        if not isinstance(rows, list):
            raise ValueError('invalid fallback active book')
        return sorted({symbol for symbol in rows if isinstance(symbol, str) and symbol.endswith(STABLE)}), 'symbols_active_fallback'


def source_timestamp(value):
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1e11 else float(value)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


class ObservedMarks:
    def __init__(self):
        self.bars = {}
        self.latest = {}
        self.pruned_minute = None
    def ingest(self, key, raw, now):
        try:
            symbol = key.decode().split(':', 1)[1] if isinstance(key, bytes) else key.split(':', 1)[1]
            item = json.loads(raw)
            if item.get('symbol') and item['symbol'] != symbol:
                return False
            price = float(item['price'])
            stamp = source_timestamp(item['timestamp'])
            if not math.isfinite(price) or price <= 0 or not math.isfinite(stamp) or not 0 <= now - stamp <= 5:
                return False
            if stamp < self.latest.get(symbol, -1):
                return False
            self.latest[symbol] = stamp
            minute = int(stamp // 60) * 60
            bars = self.bars.setdefault(symbol, {})
            bar = bars.get(minute)
            if bar is None:
                bars[minute] = {'open': price, 'high': price, 'low': price, 'close': price, '_source': 'observed_mark_1m', '_first_source_ts': stamp, '_last_source_ts': stamp}
            else:
                bar.update(high=max(bar['high'], price), low=min(bar['low'], price), close=price, _last_source_ts=stamp)
            return True
        except (TypeError, ValueError, KeyError, AttributeError):
            return False
    def prune(self, now):
        minute = int(now // 60) * 60
        if minute == self.pruned_minute:
            return
        self.pruned_minute = minute
        cutoff = minute - 2700
        for symbol, bars in list(self.bars.items()):
            self.bars[symbol] = {stamp: bar for stamp, bar in bars.items() if stamp >= cutoff}


def recent_rows(path):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(max(0, Path(path).stat().st_size - 16384))
            tail = stream.read().decode('utf-8')
        return [json.loads(match.group()) for match in re.finditer(r'\{[^{}]*\}', tail)]
    except (OSError, ValueError, TypeError):
        return []


def native_inputs(base, symbol, start):
    base = Path(base)
    prefix = {}
    for tf, span in [('1m', 60), ('3m', 180)]:
        for folder in ('klines_canonical', 'klines_cache', 'klines_cache_gateway'):
            for row in recent_rows(base / folder / f'{symbol}_{tf}.json'):
                try:
                    stamp = timestamp_epoch(row['timestamp'])
                    if not start <= stamp or stamp + span > start + 780:
                        continue
                    bar = {field: float(row[field]) for field in ('open', 'high', 'low', 'close')}
                    if not all(math.isfinite(value) and value > 0 for value in bar.values()):
                        continue
                    prefix.setdefault((stamp, span), dict(bar, _span_s=span, _source=f'kline_{tf}'))
                except (TypeError, ValueError, KeyError):
                    continue
            if len({minute for stamp, width in prefix for minute in range(stamp, stamp + width, 60)}) >= 13:
                break
    canonical_path = base / 'klines_canonical' / f'{symbol}_15m.json'
    canonical = None
    for row in reversed(recent_rows(canonical_path)):
        try:
            if timestamp_epoch(row['timestamp']) == start and is_verified_closed_native(row, start + 900, canonical_path) and time.time() >= start + 900:
                canonical = dict(row, _closed=True, _provisional=False, _closed_verified_at=time.time(), _source='binance_canonical_closed')
                break
        except (TypeError, ValueError, KeyError):
            continue
    return prefix, canonical


def build_completed(observed, prefix, start, now):
    if now < start + 900:
        return None, {'reason': 'forming'}
    native_coverage = {minute for stamp, span in prefix for minute in range(stamp, stamp + span, 60)}
    bars = dict(prefix)
    for stamp, row in observed.items():
        if start <= stamp < start + 900:
            bars[stamp] = row
    row = aggregate_closed_15m(bars, start, now)
    missing_prefix = [start + offset for offset in range(0, 780, 60) if start + offset not in native_coverage and start + offset not in observed]
    missing_tail = [stamp for stamp in (start + 780, start + 840) if stamp not in observed]
    details = {'native_prefix_minutes': len(native_coverage), 'observed_prefix_minutes': sum(start + offset in observed for offset in range(0, 780, 60)), 'missing_prefix_minutes': missing_prefix, 'missing_tail_minutes': missing_tail}
    if row is None:
        details['reason'] = 'missing_prefix' if missing_prefix else 'missing_mark_tail' if missing_tail else 'noncontiguous_coverage'
        return None, details
    row.update(_producer='independent_crypto_15m_clock', _native_prefix_minutes=len(native_coverage), _observed_prefix_minutes=details['observed_prefix_minutes'], _observed_tail_minutes=2)
    return row, details


def run(base, poll_s=0.1):
    base = Path(base)
    store = redis.Redis(host='127.0.0.1', port=6379, db=0, socket_connect_timeout=0.5, socket_timeout=0.5)
    observed = ObservedMarks()
    readers = ThreadPoolExecutor(max_workers=4, thread_name_prefix='clockprefix')
    publishers = ThreadPoolExecutor(max_workers=8, thread_name_prefix='clockpublish')
    native, preparation, pending, emitted = {}, {}, {}, {}
    keys, universe, source = [], [], ''
    refreshed = books_at = status_at = 0
    print(json.dumps({'event': 'crypto_clock_started', 'at': time.time(), 'pid': __import__('os').getpid()}), flush=True)
    while True:
        began = now = time.time()
        bucket = int(now // 900) * 900
        target = bucket - 900
        try:
            if now - refreshed >= 60:
                keys = list(store.scan_iter(match='mark_price:*', count=1000))
                refreshed = now
            if now - books_at >= 1:
                universe, source = tradeable_symbols(base)
                books_at = now
            values = store.mget(keys) if keys else []
            fresh = sum(observed.ingest(key, value, now) for key, value in zip(keys, values) if value)
            observed.prune(now)
            starts = [target] + ([bucket] if now - bucket >= 780 else [])
            for start in starts:
                for symbol in universe:
                    key = (symbol, start)
                    if key not in native and key not in preparation:
                        preparation[key] = readers.submit(native_inputs, base, symbol, start)
            for key, future in list(preparation.items()):
                if future.done():
                    native[key] = future.result()
                    preparation.pop(key)
            for key, future in list(pending.items()):
                if future.done():
                    try:
                        future.result()
                        emitted[key] = time.time()
                        print(json.dumps({'event': 'crypto_clock_published', 'symbol': key[0], 'bar_start_ts': key[1], 'available_at': emitted[key], 'latency_s': emitted[key] - key[1] - 900}), flush=True)
                    except Exception as exc:
                        print(json.dumps({'event': 'crypto_clock_publish_error', 'symbol': key[0], 'error': str(exc), 'at': time.time()}), flush=True)
                    pending.pop(key)
            reasons = {}
            for symbol in universe:
                key = (symbol, target)
                if key in emitted or key in pending:
                    continue
                prefix, canonical = native.get(key, ({}, None))
                row, details = build_completed(observed.bars.get(symbol, {}), prefix, target, now)
                if canonical is not None:
                    row = dict(canonical, _producer='independent_crypto_15m_clock')
                if row is None:
                    reason = details['reason']
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
                pending[key] = publishers.submit(publish_closed_15m, base / 'klines_cache' / f'{symbol}_15m.json', row, merge_closed_history, base, 'crypto', symbol)
            if now - status_at >= 5:
                status = {'at': now, 'pid': __import__('os').getpid(), 'event': 'crypto_clock_status', 'redis_keys': len(keys), 'fresh_marks': fresh, 'symbols_observed': len(observed.bars), 'book': source, 'universe': len(universe), 'bar_start_ts': target, 'published': sum((symbol, target) in emitted for symbol in universe), 'pending': sum((symbol, target) in pending for symbol in universe), 'missing_reasons': reasons, 'loop_s': time.time() - began}
                atomic_json(base / 'data/reports/crypto_15m_clock_latest.json', status)
                print(json.dumps(status), flush=True)
                status_at = now
            for mapping in (native, emitted):
                for key in list(mapping):
                    if key[1] < bucket - 2700:
                        mapping.pop(key, None)
        except Exception as exc:
            print(json.dumps({'event': 'crypto_clock_error', 'at': time.time(), 'error': f'{type(exc).__name__}: {exc}'}), flush=True)
            refreshed = 0
            time.sleep(0.5)
        time.sleep(max(0, poll_s - (time.time() - began)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=str(ROOT))
    args = parser.parse_args()
    lock = Path(args.base) / 'data/.crypto_15m_bar_clock.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        run(args.base)
