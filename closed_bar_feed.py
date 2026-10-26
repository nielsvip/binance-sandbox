import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def aggregate_closed_15m(bars, bucket_ts, now_s):
    bucket_ts = int(bucket_ts)
    if float(now_s) < bucket_ts + 900:
        return None
    selected = {int(ts): bar for ts, bar in bars.items() if bucket_ts <= int(ts) < bucket_ts + 900}
    if any(bucket_ts + offset not in selected for offset in range(0, 900, 60)):
        return None
    values = [selected[bucket_ts + offset] for offset in range(0, 900, 60)]
    try:
        prices = [float(bar[key]) for bar in values for key in ('open', 'high', 'low', 'close')]
        if not all(math.isfinite(price) and price > 0 for price in prices):
            return None
        return {'timestamp': datetime.fromtimestamp(bucket_ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'open': float(values[0]['open']), 'high': max(float(bar['high']) for bar in values), 'low': min(float(bar['low']) for bar in values), 'close': float(values[-1]['close']), '_provisional': True, '_closed': True, '_source': 'mark_resample', '_bar_close_ts': bucket_ts + 900}
    except (KeyError, TypeError, ValueError):
        return None


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


def publish_closed_15m(path, row, writer, base_path, mode, symbol):
    written = writer(path, row)
    rows = json.loads(Path(path).read_text())
    chosen = next((item for item in reversed(rows) if item.get('timestamp') == row['timestamp']), None)
    if chosen is None or (not written and not (chosen.get('volume') is not None and not chosen.get('_provisional'))):
        return False
    start = int(datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp())
    event = {'symbol': symbol, 'mode': mode, 'bar_start_ts': start, 'bar_close_ts': start + 900, 'published_at': time.time(), 'row': chosen}
    destination = Path(base_path) / 'data' / 'closed_15m_outbox' / mode / f'{symbol}_{start}.json'
    atomic_json(destination, event)
    return True


def receive_closed_15m(base_path, event, now_s=None):
    import fcntl
    now_s = time.time() if now_s is None else float(now_s)
    symbol, mode = event['symbol'], event['mode']
    if not symbol.isalnum() or mode not in ('crypto', 'tradier'):
        raise ValueError('invalid symbol or mode')
    start = int(event['bar_start_ts'])
    if start % 900 or int(event['bar_close_ts']) != start + 900 or now_s < start + 900:
        raise ValueError('bar has not closed')
    row = dict(event['row'])
    if int(datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()) != start:
        raise ValueError('row timestamp differs from envelope')
    if not all(math.isfinite(float(row[key])) and float(row[key]) > 0 for key in ('open', 'high', 'low', 'close')):
        raise ValueError('invalid OHLC')
    root = Path(base_path)
    cache = root / 'klines_cache'
    if mode == 'tradier':
        cache = cache / 'tradier'
    path = cache / f'{symbol}_15m.json'
    if not path.exists():
        raise ValueError('missing history; refusing to create a one-bar cache')
    lock = root / 'data' / 'closed_15m_locks' / f'{mode}_{symbol}.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        rows = json.loads(path.read_text())
        if not isinstance(rows, list):
            raise ValueError('invalid cache')
        for index, previous in enumerate(rows):
            if previous.get('timestamp') == row['timestamp']:
                if previous.get('volume') is not None and not previous.get('_provisional'):
                    row = previous
                rows[index] = row
                break
        else:
            rows.append(row)
        rows.sort(key=lambda item: item.get('timestamp', ''))
        atomic_json(path, rows)
    receipt = {'symbol': symbol, 'mode': mode, 'bar_start_ts': start, 'bar_close_ts': start + 900, 'published_at': event['published_at'], 'received_at': time.time(), 'path': str(path)}
    ledger = root / 'data' / 'closed_15m_receipts.jsonl'
    with ledger.open('a') as stream:
        stream.write(json.dumps(receipt, separators=(',', ':')) + '\n')
    return receipt
