import json
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_CACHE_WRITES = ThreadPoolExecutor(max_workers=2, thread_name_prefix='closed15mcache')


def write_history_outside_boundary(writer, path, row):
    while True:
        phase = time.time() % 900
        if 30 <= phase < 720:
            return writer(path, row)
        time.sleep(min(1.0, (30 - phase) if phase < 30 else (930 - phase)))


def timestamp_epoch(value):
    if isinstance(value, (int, float)):
        return int(value / 1000 if value > 1e11 else value)
    return int(datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())


def aggregate_closed_15m(bars, bucket_ts, now_s):
    bucket_ts = int(bucket_ts)
    if float(now_s) < bucket_ts + 900:
        return None
    options = {}
    for key, bar in bars.items():
        try:
            start = int(key[0] if isinstance(key, tuple) else key)
            span = int(bar.get('_span_s', 60))
            if span not in (60, 180, 300) or start < bucket_ts or start + span > bucket_ts + 900:
                continue
            prices = [float(bar[field]) for field in ('open', 'high', 'low', 'close')]
            if not all(math.isfinite(price) and price > 0 for price in prices):
                continue
            options.setdefault(start, []).append((span, bar))
        except (TypeError, ValueError, KeyError):
            continue
    memo = {}
    def tile(start):
        if start == bucket_ts + 900:
            return []
        if start in memo:
            return memo[start]
        for span, bar in sorted(options.get(start, []), key=lambda item: (not str(item[1].get('_source', '')).startswith('kline_'), item[0])):
            rest = tile(start + span)
            if rest is not None:
                memo[start] = [bar] + rest
                return memo[start]
        memo[start] = None
        return None
    values = tile(bucket_ts)
    if not values:
        return None
    return {'timestamp': datetime.fromtimestamp(bucket_ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'open': float(values[0]['open']), 'high': max(float(bar['high']) for bar in values), 'low': min(float(bar['low']) for bar in values), 'close': float(values[-1]['close']), '_provisional': True, '_closed': True, '_source': 'mark_resample', '_bar_close_ts': bucket_ts + 900}


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, separators=(',', ':'), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def is_verified_closed_native(row, bar_close_ts=None, source_path=None):
    try:
        close = timestamp_epoch(row['timestamp']) + 900 if bar_close_ts is None else int(bar_close_ts)
        volume = float(row['volume'])
        if not math.isfinite(volume) or volume < 0 or row.get('_provisional') or row.get('provisional'):
            return False
        proof = float(row.get('_closed_verified_at') or 0)
        canonical = source_path is not None and 'klines_canonical' in Path(source_path).parts
        return (math.isfinite(proof) and proof >= close) or canonical
    except (TypeError, ValueError, KeyError):
        return False


def preferred_closed_row(previous, incoming, bar_close_ts=None):
    old_native = is_verified_closed_native(previous, bar_close_ts)
    new_native = is_verified_closed_native(incoming, bar_close_ts)
    if old_native and not new_native:
        return previous
    if old_native and new_native:
        if float(previous.get('_closed_verified_at') or 0) > float(incoming.get('_closed_verified_at') or 0):
            return previous
    if not old_native and not new_native and previous.get('_producer') == 'independent_crypto_15m_clock' and incoming.get('_producer') != 'independent_crypto_15m_clock':
        return previous
    if not old_native and not new_native and previous.get('_producer') == incoming.get('_producer') == 'independent_crypto_15m_clock' and int(previous.get('_producer_generation', 1)) > int(incoming.get('_producer_generation', 1)):
        return previous
    return incoming


def merge_closed_history(path, row):
    import fcntl
    path = Path(path)
    if not path.exists():
        return False
    lock_path = path.with_name(f'.{path.name}.mark.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fast = merge_closed_tail_locked(path, row)
        if fast is not None:
            return fast
        rows = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(rows, list):
            raise ValueError('invalid full history')
        start = timestamp_epoch(row['timestamp'])
        for index, previous in enumerate(rows):
            if timestamp_epoch(previous['timestamp']) == start:
                rows[index] = preferred_closed_row(previous, row, start + 900)
                break
        else:
            rows.append(row)
        rows.sort(key=lambda item: timestamp_epoch(item['timestamp']))
        atomic_json(path, rows)
    return True


def merge_closed_tail_locked(path, row):
    import ctypes
    import sys
    path = Path(path)
    with path.open('rb') as stream:
        size = path.stat().st_size
        offset = max(0, size - 32768)
        stream.seek(offset)
        tail = stream.read()
    matches = list(re.finditer(rb'\{[^{}]*\}', tail))
    if not matches or not tail.rstrip().endswith(b']'):
        return None
    begin = matches[0].start()
    try:
        rows = json.loads(b'[' + tail[begin:])
        start = timestamp_epoch(row['timestamp'])
        if start < timestamp_epoch(rows[0]['timestamp']):
            return None
        for index, previous in enumerate(rows):
            if timestamp_epoch(previous['timestamp']) == start:
                selected = preferred_closed_row(previous, row, start + 900)
                if selected == previous:
                    return True
                rows[index] = selected
                break
        else:
            rows.append(row)
        rows.sort(key=lambda item: timestamp_epoch(item['timestamp']))
        suffix = json.dumps(rows, separators=(',', ':'), allow_nan=False).encode()[1:]
    except (ValueError, TypeError, KeyError):
        return None
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{time.time_ns()}.clone')
    if sys.platform == 'darwin':
        clonefile = ctypes.CDLL(None, use_errno=True).clonefile
        clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        clonefile.restype = ctypes.c_int
        if clonefile(os.fsencode(path), os.fsencode(temporary), 0) != 0:
            return None
    else:
        import fcntl
        import shutil
        try:
            with path.open('rb') as source, temporary.open('xb') as target:
                fcntl.ioctl(target.fileno(), 0x40049409, source.fileno())
        except OSError:
            temporary.unlink(missing_ok=True)
            shutil.copyfile(path, temporary)
    try:
        with temporary.open('r+b') as stream:
            stream.seek(offset + begin)
            stream.write(suffix)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def publish_closed_15m(path, row, writer, base_path, mode, symbol):
    start = timestamp_epoch(row['timestamp'])
    chosen = dict(row)
    candidates = [Path(path)]
    if mode == 'crypto':
        candidates.append(Path(base_path) / 'klines_canonical' / f'{symbol}_15m.json')
    for candidate_path in candidates:
        try:
            with candidate_path.open('rb') as stream:
                stream.seek(max(0, candidate_path.stat().st_size - 16384))
                recent = stream.read().decode('utf-8')
            for match in reversed(list(re.finditer(r'\{[^{}]*\}', recent))):
                candidate = json.loads(match.group())
                if timestamp_epoch(candidate.get('timestamp')) != start or not is_verified_closed_native(candidate, start + 900, candidate_path):
                    continue
                candidate = dict(candidate, _closed=True, _closed_verified_at=float(candidate.get('_closed_verified_at') or max(candidate_path.stat().st_mtime, start + 900)), _provisional=False)
                if 'klines_canonical' in candidate_path.parts:
                    candidate['_source'] = 'binance_canonical_closed'
                chosen = preferred_closed_row(candidate, chosen, start + 900)
                break
        except (OSError, ValueError, TypeError):
            continue
    event = {'symbol': symbol, 'mode': mode, 'bar_start_ts': start, 'bar_close_ts': start + 900, 'published_at': time.time(), 'row': chosen}
    receipt = receive_closed_15m(base_path, event, require_history=False)
    event['row'] = receipt['row']
    destination = Path(base_path) / 'data' / 'closed_15m_outbox' / mode / f'{symbol}_{start}.json'
    import fcntl
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(destination.read_text())
            event['first_published_at'] = min(float(previous.get('first_published_at', previous['published_at'])), event['published_at'])
            if previous['row'] == event['row']:
                event['published_at'] = previous['published_at']
        except (OSError, ValueError, TypeError, KeyError):
            event['first_published_at'] = event['published_at']
        atomic_json(destination, event)
    if writer is not None:
        _CACHE_WRITES.submit(write_history_outside_boundary, writer, path, event['row'])
    return True


def receive_closed_15m(base_path, event, now_s=None, require_history=True):
    import fcntl
    now_s = time.time() if now_s is None else float(now_s)
    symbol, mode = event['symbol'], event['mode']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', symbol) or mode not in ('crypto', 'tradier'):
        raise ValueError('invalid symbol or mode')
    start = int(event['bar_start_ts'])
    if start % 900 or int(event['bar_close_ts']) != start + 900 or now_s < start + 900:
        raise ValueError('bar has not closed')
    row = dict(event['row'])
    if timestamp_epoch(row['timestamp']) != start:
        raise ValueError('row timestamp differs from envelope')
    if not all(math.isfinite(float(row[key])) and float(row[key]) > 0 for key in ('open', 'high', 'low', 'close')):
        raise ValueError('invalid OHLC')
    root = Path(base_path)
    cache = root / 'klines_cache'
    if mode == 'tradier':
        cache = cache / 'tradier'
    history = cache / f'{symbol}_15m.json'
    if require_history and not history.exists():
        raise ValueError('missing history; refusing to create a one-bar cache')
    path = root / 'data' / 'closed_15m_bars' / mode / f'{symbol}.json'
    lock = root / 'data' / 'closed_15m_locks' / f'{mode}_{symbol}.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        rows = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(rows, list):
            raise ValueError('invalid cache')
        for index, previous in enumerate(rows):
            if timestamp_epoch(previous.get('timestamp')) == start:
                row = preferred_closed_row(previous, row, start + 900)
                rows[index] = row
                break
        else:
            rows.append(row)
        rows.sort(key=lambda item: timestamp_epoch(item['timestamp']))
        atomic_json(path, rows[-96:])
    receipt = {'symbol': symbol, 'mode': mode, 'bar_start_ts': start, 'bar_close_ts': start + 900, 'published_at': event['published_at'], 'received_at': time.time(), 'path': str(path)}
    ledger = root / 'data' / 'closed_15m_receipts.jsonl'
    with ledger.open('a') as stream:
        stream.write(json.dumps(receipt, separators=(',', ':')) + '\n')
    return dict(receipt, row=row)
