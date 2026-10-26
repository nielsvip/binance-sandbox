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
    native['_closed_verified_at'] = START + 900
    older = dict(native, timestamp='2026-10-26T17:45:00+00:00')
    atomic_json(path, [older, native])
    receipt = receive_closed_15m(tmp_path, event(), now_s=START + 900)
    from npz_live_generator import _load_klines_df
    rows = _load_klines_df(path)
    assert len(rows) == 2
    assert rows.iloc[-1]['close'] == 120 and rows.iloc[-1]['volume'] == 7
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
    from npz_live_generator import _load_klines_df
    rows = _load_klines_df(path)
    assert len(rows) == 3
    assert rows.iloc[0]['close'] == old['close']
    assert rows.iloc[1]['close'] == payload['row']['close']
    assert json.loads(path.read_text()) == [old, current]


def test_completed_overlay_is_available_without_waiting_for_history_rewrite(tmp_path):
    path = tmp_path / 'TESTUSDT_15m.json'
    stale = dict(event()['row'], close=3)
    atomic_json(path, [stale])
    assert publish_closed_15m(path, event()['row'], lambda *_: False, tmp_path, 'crypto', 'TESTUSDT')
    published = json.loads(next((tmp_path / 'data/closed_15m_outbox/crypto').glob('*.json')).read_text())
    assert published['row']['close'] == event()['row']['close']
    assert json.loads(path.read_text()) == [stale]


def test_tail_watcher_accepts_boundary_without_thirty_second_wait(tmp_path):
    from tools.npz_tail_service import kline_last_close
    path = tmp_path / 'klines_cache' / 'TESTUSDT_15m.json'
    next_row = dict(event()['row'], timestamp='2026-10-26T18:15:00.000000Z')
    atomic_json(path, [event()['row'], next_row])
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 899.999) is None
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 900) == START
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 930) == START


def test_volume_alone_does_not_override_completed_mark_row(tmp_path):
    from closed_bar_feed import preferred_closed_row
    unverified = dict(event()['row'], close=999, volume=8, _provisional=False)
    assert preferred_closed_row(unverified, event()['row'], START + 900)['close'] == event()['row']['close']
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    atomic_json(path, [unverified])
    publish_closed_15m(path, event()['row'], None, tmp_path, 'crypto', 'TESTUSDT')
    envelope = json.loads(next((tmp_path / 'data/closed_15m_outbox/crypto').glob('*.json')).read_text())
    assert envelope['row']['close'] == event()['row']['close']


def test_verified_native_zero_volume_replaces_marks_and_accepts_newer_correction(tmp_path):
    from closed_bar_feed import preferred_closed_row
    mark = event()['row']
    native = dict(mark, close=120, volume=0, _provisional=False, _closed_verified_at=START + 901)
    assert preferred_closed_row(native, mark, START + 900) is native
    assert preferred_closed_row(mark, native, START + 900) is native
    correction = dict(native, close=125, _closed_verified_at=START + 902)
    assert preferred_closed_row(native, correction, START + 900) is correction
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    atomic_json(path, [mark])
    receive_closed_15m(tmp_path, event(), now_s=START + 905)
    receive_closed_15m(tmp_path, dict(event(), row=native), now_s=START + 905)
    receive_closed_15m(tmp_path, dict(event(), row=correction), now_s=START + 905)
    receive_closed_15m(tmp_path, event(), now_s=START + 905)
    overlay = json.loads((tmp_path / 'data/closed_15m_bars/crypto/TESTUSDT.json').read_text())
    assert overlay[-1]['close'] == 125


def test_trusted_closed_canonical_source_has_explicit_finality(tmp_path):
    native = dict(event()['row'], close=130, volume=9, _provisional=False)
    canonical = tmp_path / 'klines_canonical/TESTUSDT_15m.json'
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    atomic_json(canonical, [native])
    atomic_json(path, [event()['row']])
    publish_closed_15m(path, event()['row'], None, tmp_path, 'crypto', 'TESTUSDT')
    envelope = json.loads(next((tmp_path / 'data/closed_15m_outbox/crypto').glob('*.json')).read_text())
    assert envelope['row']['close'] == 130
    assert envelope['row']['_source'] == 'binance_canonical_closed'
    assert envelope['row']['_closed_verified_at'] >= START + 900


def test_unverified_overlay_native_cannot_block_a_verified_correction(tmp_path):
    unverified = dict(event()['row'], close=999, volume=100, _provisional=False)
    native = dict(unverified, close=120, _closed_verified_at=START + 901)
    path = tmp_path / 'data/closed_15m_bars/crypto/TESTUSDT.json'
    atomic_json(path, [unverified])
    receive_closed_15m(tmp_path, dict(event(), row=native), now_s=START + 905, require_history=False)
    assert json.loads(path.read_text())[-1]['close'] == 120


def test_npz_loader_uses_completed_marks_over_unverified_cached_volume(tmp_path):
    from npz_live_generator import _load_klines_df
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    atomic_json(path, [dict(event()['row'], close=999, volume=100, _provisional=False)])
    atomic_json(tmp_path / 'data/closed_15m_bars/crypto/TESTUSDT.json', [event()['row']])
    assert _load_klines_df(path).iloc[-1]['close'] == event()['row']['close']


def test_npz_loader_preserves_verified_canonical_closed_candle(tmp_path):
    from npz_live_generator import _load_klines_df
    path = tmp_path / 'klines_canonical/TESTUSDT_15m.json'
    atomic_json(path, [dict(event()['row'], close=120, volume=7, _provisional=False)])
    atomic_json(tmp_path / 'data/closed_15m_bars/crypto/TESTUSDT.json', [event()['row']])
    assert _load_klines_df(path).iloc[-1]['close'] == 120


def test_overlay_newer_than_cache_is_visible_after_a_missing_next_bar(tmp_path):
    from tools.npz_tail_service import kline_last_close
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    atomic_json(path, [dict(event()['row'], timestamp='2026-10-26T17:45:00.000000Z')])
    atomic_json(tmp_path / 'data/closed_15m_bars/crypto/TESTUSDT.json', [event()['row']])
    assert kline_last_close(tmp_path, 'TESTUSDT', 'crypto', now_s=START + 1800) == START


def test_outbox_retains_selected_independent_row_and_first_publication(tmp_path):
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    independent = dict(event()['row'], _producer='independent_crypto_15m_clock')
    publish_closed_15m(path, independent, None, tmp_path, 'crypto', 'TESTUSDT')
    outbox = tmp_path / f'data/closed_15m_outbox/crypto/TESTUSDT_{START}.json'
    first = json.loads(outbox.read_text())
    publish_closed_15m(path, dict(event()['row'], close=999), None, tmp_path, 'crypto', 'TESTUSDT')
    second = json.loads(outbox.read_text())
    assert second['row'] == independent
    assert second['published_at'] == first['published_at']
    assert second['first_published_at'] == first['first_published_at']


def test_fast_tail_preserves_full_history_and_forming_next_row(tmp_path):
    from closed_bar_feed import merge_closed_history
    path = tmp_path / 'klines_cache/TESTUSDT_15m.json'
    rows = [dict(event()['row'], timestamp=START - (6000 - index) * 900) for index in range(6001)]
    rows.append(dict(event()['row'], timestamp=START + 900))
    atomic_json(path, rows)
    prefix = path.read_bytes()[:100000]
    incoming = dict(event()['row'], close=150, _producer='independent_crypto_15m_clock')
    assert merge_closed_history(path, incoming)
    result = json.loads(path.read_text())
    assert len(result) == len(rows)
    assert result[:-2] == rows[:-2]
    assert result[-2] == incoming and result[-1] == rows[-1]
    assert path.read_bytes()[:100000] == prefix
    verified = dict(incoming, close=155, volume=0, _provisional=False, _closed_verified_at=START + 901)
    assert merge_closed_history(path, verified)
    assert merge_closed_history(path, incoming)
    assert json.loads(path.read_text())[-2] == verified


def test_new_clock_generation_wins_until_verified_native_is_available():
    from closed_bar_feed import preferred_closed_row
    old = dict(event()['row'], _producer='independent_crypto_15m_clock')
    current = dict(old, close=130, _producer_generation=2)
    assert preferred_closed_row(current, old, START + 900) is current
    assert preferred_closed_row(old, current, START + 900) is current
    native = dict(old, close=140, volume=0, _provisional=False, _closed_verified_at=START + 901)
    assert preferred_closed_row(current, native, START + 900) is native
    assert preferred_closed_row(native, current, START + 900) is native


def test_linux_tail_copy_fallback_preserves_history_without_json_reparse(tmp_path, monkeypatch):
    import sys
    from closed_bar_feed import merge_closed_history
    path = tmp_path / 'TESTUSDT_15m.json'
    rows = [dict(event()['row'], timestamp=START - (6000 - index) * 900) for index in range(6001)]
    atomic_json(path, rows)
    monkeypatch.setattr(sys, 'platform', 'linux')
    incoming = dict(event()['row'], close=175)
    assert merge_closed_history(path, incoming)
    result = json.loads(path.read_text())
    assert result[:-1] == rows[:-1] and result[-1] == incoming
