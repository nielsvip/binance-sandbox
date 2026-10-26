import asyncio
import json
from datetime import datetime, timezone

import pytest

from tools import stock_closed_15m_prepare as P


def raw_minutes(bucket):
    return [{'timestamp': bucket + minute * 60, 'open': 100, 'high': 120, 'low': 90, 'close': 110, 'volume': minute} for minute in range(13)]


def test_native_minutes_keep_exchange_time_and_never_admit_forming_or_missing_volume():
    bucket = int(datetime(2026, 10, 26, 19, 15, tzinfo=timezone.utc).timestamp())
    missing = {bucket + minute * 60 for minute in range(13)}
    rows = P.normalize_minutes(raw_minutes(bucket), missing, bucket + 780)
    assert len(rows) == 13 and rows[-1]['volume'] == 12
    assert rows[0]['high'] == 120 and rows[0]['low'] == 90
    malformed = [{'timestamp': bucket, 'open': 100, 'high': 120, 'low': 90, 'close': 110}]
    assert P.normalize_minutes(malformed, missing, bucket + 780) == []
    assert P.normalize_minutes(raw_minutes(bucket), missing, bucket + 30) == []
    naive = [dict(raw_minutes(bucket)[0], time='2026-10-26T15:15:00')]
    naive[0].pop('timestamp')
    assert P.normalize_minutes(naive, {bucket}, bucket + 780)[0]['timestamp'].startswith('2026-10-26T19:15:00')


def test_native_minute_merge_preserves_history_and_real_ohlc(tmp_path):
    path = tmp_path / 'klines_cache/tradier/TEST_1m.json'
    path.parent.mkdir(parents=True)
    old = {'timestamp': '2026-10-26T19:14:00Z', 'open': 80, 'high': 90, 'low': 70, 'close': 85, 'volume': 2}
    path.write_text(json.dumps([old]))
    bucket = 1793042100
    incoming = P.normalize_minutes(raw_minutes(bucket), {bucket + minute * 60 for minute in range(13)}, bucket + 780)
    assert P.merge_native_minutes(tmp_path, 'TEST', incoming) == 13
    result = json.loads(path.read_text())
    assert len(result) == 14 and old in result
    assert result[-1]['high'] == 120


@pytest.mark.asyncio
async def test_only_missing_prefixes_fetch_with_bounded_concurrency(tmp_path):
    bucket = 1793042100
    helpers = P.producer_helpers(P.ROOT)
    complete = P.normalize_minutes(raw_minutes(bucket), {bucket + minute * 60 for minute in range(13)}, bucket + 780)
    P.merge_native_minutes(tmp_path, 'READY', complete)
    class API:
        def __init__(self):
            self.active = self.maximum = 0
            self.calls = []
        async def get_timesales(self, symbol, interval, start):
            self.calls.append((symbol, interval, start))
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return raw_minutes(bucket)
    api = API()
    result = await P.prepare_cycle(tmp_path, api, bucket, ['READY'] + [f'MISSING{i}' for i in range(6)], helpers, now_fn=lambda: bucket + 790)
    assert result['ready'] == 7 and result['requested'] == 6
    assert api.maximum == 4
    assert all(interval == '1min' and start == '2026-10-26' for _, interval, start in api.calls)
    assert all(not P.missing_prefix(tmp_path, f'MISSING{i}', bucket, helpers) for i in range(6))


@pytest.mark.asyncio
async def test_expired_deadline_does_not_request_or_fabricate(tmp_path):
    class API:
        async def get_timesales(self, *args, **kwargs):
            raise AssertionError('API must not be called after deadline')
    bucket = 1793042100
    result = await P.prepare_cycle(tmp_path, API(), bucket, ['TEST'], P.producer_helpers(P.ROOT), now_fn=lambda: bucket + 896)
    assert result['ready'] == 0 and result['requested'] == 0
    assert result['results'][0]['status'] == 'DEADLINE'
    assert not (tmp_path / 'klines_cache/tradier/TEST_1m.json').exists()


@pytest.mark.asyncio
async def test_entire_disk_probe_network_merge_chain_has_bounded_concurrency(tmp_path, monkeypatch):
    import threading
    import time
    count = {'active': 0, 'maximum': 0}
    lock = threading.Lock()
    bucket = 1793042100
    def probe(*args):
        with lock:
            count['active'] += 1
            count['maximum'] = max(count['maximum'], count['active'])
        time.sleep(0.01)
        with lock:
            count['active'] -= 1
        return {bucket}
    monkeypatch.setattr(P, 'missing_prefix', probe)
    class API:
        async def get_timesales(self, *args, **kwargs):
            return []
    result = await P.prepare_cycle(tmp_path, API(), bucket, [f'TEST{i}' for i in range(12)], {}, now_fn=lambda: bucket + 790)
    assert count['maximum'] <= 4
    assert result['requested'] == 12


@pytest.mark.asyncio
async def test_genuine_closed5m_fills_sparse_native1m_prefix_without_expanding_or_forming(tmp_path):
    bucket = 1793042100
    class API:
        def __init__(self):
            self.calls = []
        async def get_timesales(self, symbol, interval, start):
            self.calls.append(interval)
            if interval == '1min':
                return [row for minute, row in enumerate(raw_minutes(bucket)) if minute != 8]
            return [{'timestamp': bucket + offset, 'open': 100, 'high': 190, 'low': 80, 'close': 115, 'volume': 500} for offset in (0, 300, 600)]
    api = API()
    helpers = P.producer_helpers(P.ROOT)
    result = await P.prepare_cycle(tmp_path, api, bucket, ['SPARSE'], helpers, now_fn=lambda: bucket + 790)
    assert result['ready'] == 1 and result['requests'] == 2
    assert api.calls == ['1min', '5min']
    coarse = json.loads((tmp_path / 'klines_cache/tradier/SPARSE_5m.json').read_text())
    assert len(coarse) == 1 and coarse[0]['high'] == 190
    assert coarse[0]['timestamp'] == datetime.fromtimestamp(bucket + 300, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    fine = json.loads((tmp_path / 'klines_cache/tradier/SPARSE_1m.json').read_text())
    assert len(fine) == 12
    assert not P.missing_prefix(tmp_path, 'SPARSE', bucket, helpers)
