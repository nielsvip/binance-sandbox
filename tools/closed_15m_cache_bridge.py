#!/usr/bin/env python3
import argparse
import fcntl
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from closed_bar_feed import is_verified_closed_native, merge_closed_history, preferred_closed_row, publish_closed_15m, timestamp_epoch


def apply_overlay(base, overlay):
    mode = overlay.parent.name
    from tools.npz_cutoffs import is_rth_bar_start
    symbol = overlay.stem
    history = base / 'klines_cache'
    if mode == 'tradier':
        history /= 'tradier'
    history /= f'{symbol}_15m.json'
    if not history.exists():
        return {'symbol': symbol, 'mode': mode, 'error': 'missing_full_history'}
    rows = json.loads(overlay.read_text())
    closed = [row for row in rows if timestamp_epoch(row['timestamp']) + 900 <= time.time() and row.get('_closed') and (mode != 'tradier' or is_rth_bar_start(timestamp_epoch(row['timestamp'])))]
    row = max(closed, key=lambda item: timestamp_epoch(item['timestamp'])) if closed else None
    with history.open('rb') as stream:
        stream.seek(max(0, history.stat().st_size - 32768))
        cache_tail = stream.read()
    for match in re.finditer(rb'\{[^{}]*\}', cache_tail):
        forming = json.loads(match.group())
        if timestamp_epoch(forming['timestamp']) + 900 > time.time() and not forming.get('_provisional'):
            merge_closed_history(history, dict(forming, _provisional=True, _closed=False, _finality_reason='current_unclosed_native'))
        if forming.get('_producer') == 'independent_crypto_15m_clock' and forming.get('_producer_generation') is None:
            merge_closed_history(history, dict(forming, _producer_generation=1))
    for match in reversed(list(re.finditer(rb'\{[^{}]*\}', cache_tail))):
        native = json.loads(match.group())
        stamp = timestamp_epoch(native['timestamp'])
        if mode == 'tradier' and not is_rth_bar_start(stamp):
            continue
        if stamp + 900 > time.time() or not (is_verified_closed_native(native, stamp + 900) or native.get('_producer') == 'independent_crypto_15m_clock'):
            continue
        if native.get('_producer') == 'independent_crypto_15m_clock' and native.get('_producer_generation') is None:
            native = dict(native, _producer_generation=1)
        if row is None or stamp > timestamp_epoch(row['timestamp']) or stamp == timestamp_epoch(row['timestamp']) and preferred_closed_row(row, native, stamp + 900) != row:
            publish_closed_15m(history, native, None, base, mode, symbol)
            row = native
        break
    canonical = base / 'klines_canonical' / f'{symbol}_15m.json'
    if mode == 'crypto' and canonical.exists():
        with canonical.open('rb') as stream:
            stream.seek(max(0, canonical.stat().st_size - 32768))
            tail = stream.read()
        for match in reversed(list(re.finditer(rb'\{[^{}]*\}', tail))):
            native = json.loads(match.group())
            start = timestamp_epoch(native['timestamp'])
            if start + 900 <= time.time() and is_verified_closed_native(native, start + 900, canonical):
                if row is not None and timestamp_epoch(row['timestamp']) > start:
                    break
                native = dict(native, _closed=True, _provisional=False, _closed_verified_at=float(native.get('_closed_verified_at') or max(canonical.stat().st_mtime, start + 900)), _source='binance_canonical_closed')
                if row is None or timestamp_epoch(row['timestamp']) < start or not is_verified_closed_native(row) or any(row.get(key) != native.get(key) for key in ('open', 'high', 'low', 'close', 'volume')):
                    publish_closed_15m(history, native, None, base, mode, symbol)
                    row = native
                break
    if row is None:
        return {'symbol': symbol, 'mode': mode, 'error': 'no_completed_row'}
    began = time.time()
    written = merge_closed_history(history, row)
    return {'symbol': symbol, 'mode': mode, 'bar_start_ts': timestamp_epoch(row['timestamp']), 'cache_available_at': time.time(), 'write_s': time.time() - began, 'written': written}


def run(base):
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='closedcache')
    applied, pending = {}, {}
    while True:
        for key, (future, stamp) in list(pending.items()):
            if not future.done():
                continue
            try:
                result = future.result()
                result['event'] = 'closed_15m_usual_cache'
                print(json.dumps(result), flush=True)
                if not result.get('error'):
                    history = base / 'klines_cache' / ('tradier/' if key.parent.name == 'tradier' else '') / f'{key.stem}_15m.json'
                    applied[key] = (stamp[0], stamp[1], history.stat().st_mtime_ns)
                else:
                    applied[key] = stamp
            except Exception as exc:
                print(json.dumps({'event': 'closed_cache_error', 'path': str(key), 'error': str(exc), 'at': time.time()}), flush=True)
            pending.pop(key)
        for mode in ('crypto', 'tradier'):
            for path in (base / 'data/closed_15m_bars' / mode).glob('*.json'):
                if path in pending:
                    continue
                canonical = base / 'klines_canonical' / f'{path.stem}_15m.json'
                history = base / 'klines_cache' / ('tradier/' if mode == 'tradier' else '') / f'{path.stem}_15m.json'
                stamp = (path.stat().st_mtime_ns, canonical.stat().st_mtime_ns if mode == 'crypto' and canonical.exists() else None, history.stat().st_mtime_ns if history.exists() else None)
                if applied.get(path) == stamp:
                    continue
                pending[path] = (pool.submit(apply_overlay, base, path), stamp)
        time.sleep(0.1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=str(ROOT))
    args = parser.parse_args()
    base = Path(args.base)
    with (base / 'data/.closed_15m_cache_bridge.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        run(base)
