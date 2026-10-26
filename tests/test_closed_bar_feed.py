import json
from datetime import datetime, timezone

import pytest

from closed_bar_feed import aggregate_closed_15m, atomic_json, publish_closed_15m, receive_closed_15m
from npz_tail_store import is_closed_bar, last_closed_15m_epoch


START = int(datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc).timestamp())


def minute_bars():
    return {START + minute * 60: {'open': 100 + minute, 'high': 102 + minute, 'low': 99 + minute, 'close': 101 + minute} for minute in range(16)}


def event():
    return {'symbol': 'TESTUSDT', 'mode': 'crypto', 'bar_start_ts': START, 'bar_close_ts': START + 900, 'published_at': START + 900, 'row': aggregate_closed_15m(minute_bars(), START, START + 900)}


def test_boundary_aggregates_previous_minutes_and_excludes_new_minute():
    assert aggregate_closed_15m(minute_bars(), START, START + 899.999) is None
    row = aggregate_closed_15m(minute_bars(), START, START + 900)
    assert [row[key] for key in ('open', 'high', 'low', 'close')] == [100, 116, 99, 115]
    assert row['_closed'] and row['_provisional']
    assert row['_bar_close_ts'] == START + 900
    assert 'volume' not in row
    incomplete = minute_bars()
    del incomplete[START + 7 * 60]
    assert aggregate_closed_15m(incomplete, START, START + 900) is None


def test_open_time_labels_are_closed_exactly_at_the_next_boundary():
    for delay in (0, 0.001, 30, 899.999):
        now = START + 900 + delay
        assert last_closed_15m_epoch(now) == START
        assert is_closed_bar(START, now)
        assert not is_closed_bar(START + 900, now)
    assert not is_closed_bar(START, START + 899.999)


def test_receiver_preserves_history_and_authoritative_exchange_row(tmp_path):
    path = tmp_path / 'klines_cache' / 'TESTUSDT_15m.json'
    native = dict(event()['row'], timestamp='2026-10-26T18:00:00+00:00', close=120, volume=7)
    native.pop('_provisional')
    older = dict(native, timestamp='2026-10-26T17:45:00+00:00')
    atomic_json(path, [older, native])
    receipt = receive_closed_15m(tmp_path, event(), now_s=START + 900)
    rows = json.loads(path.read_text())
    assert len(rows) == 2
    assert rows[-1]['close'] == 120 and rows[-1]['volume'] == 7
    assert receipt['bar_close_ts'] == START + 900
    assert (tmp_path / 'data' / 'closed_15m_receipts.jsonl').exists()
    with pytest.raises(ValueError, match='not closed'):
        receive_closed_15m(tmp_path, event(), now_s=START + 899.999)


def test_receiver_appends_completed_bar_without_replacing_other_rows(tmp_path):
    path = tmp_path / 'klines_cache' / 'tradier' / 'TESTUSDT_15m.json'
    old = dict(event()['row'], timestamp='2026-10-26T17:45:00.000000Z')
    current = dict(event()['row'], timestamp='2026-10-26T18:15:00.000000Z')
    atomic_json(path, [old, current])
    payload = dict(event(), mode='tradier')
    receive_closed_15m(tmp_path, payload, now_s=START + 900)
    rows = json.loads(path.read_text())
    assert len(rows) == 3 and rows[0] == old and rows[-1] == current
    assert rows[1]['timestamp'] == payload['row']['timestamp']


def test_failed_producer_write_does_not_ship_stale_provisional_row(tmp_path):
    path = tmp_path / 'TESTUSDT_15m.json'
    stale = dict(event()['row'], close=3)
    atomic_json(path, [stale])
    assert not publish_closed_15m(path, event()['row'], lambda *_: False, tmp_path, 'crypto', 'TESTUSDT')
    assert not list((tmp_path / 'data').rglob('*.json'))


def test_tail_watcher_accepts_boundary_without_thirty_second_wait(tmp_path):
    from tools.npz_tail_service import kline_last_close
    path = tmp_path / 'klines_cache' / 'TESTUSDT_15m.json'
    next_row = dict(event()['row'], timestamp='2026-10-26T18:15:00.000000Z')
    atomic_json(path, [event()['row'], next_row])
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 899.999) is None
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 900) == START
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 930) == START
