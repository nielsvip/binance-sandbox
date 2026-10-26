import json
from pathlib import Path

from tools.crypto_15m_bar_clock import ObservedMarks, build_completed, tradeable_symbols

START = 1793045700


def marks():
    return {START + minute * 60: {'open': 100 + minute, 'high': 102 + minute, 'low': 99 + minute, 'close': 101 + minute, '_source': 'observed_mark_1m'} for minute in range(15)}


def test_mark_capture_uses_actual_timestamp_freshness_and_real_ohlc():
    memory = ObservedMarks()
    for stamp, price in [(START + 1.1, 100), (START + 2.1, 105), (START + 3.1, 99)]:
        assert memory.ingest(b'mark_price:TESTUSDT', json.dumps({'price': price, 'timestamp': stamp}), stamp + 0.1)
    bar = memory.bars['TESTUSDT'][START]
    assert [bar[k] for k in ('open', 'high', 'low', 'close')] == [100, 105, 99, 99]
    assert not memory.ingest('mark_price:TESTUSDT', json.dumps({'price': 999, 'timestamp': START}), START + 30)
    assert not memory.ingest('mark_price:TESTUSDT', json.dumps({'price': 999, 'timestamp': START + 40}), START + 30)
    assert not memory.ingest('mark_price:TESTUSDT', json.dumps({'symbol': 'WRONG', 'price': 999, 'timestamp': START + 30}), START + 30)
    assert bar['close'] == 99


def test_all_available_marks_are_retained_before_a_symbol_becomes_tradeable(tmp_path):
    memory = ObservedMarks()
    for minute in range(15):
        stamp = START + minute * 60 + 1
        assert memory.ingest('mark_price:NEWUSDT', json.dumps({'price': 100 + minute, 'timestamp': stamp}), stamp + 0.1)
    (tmp_path / 'tradeable_keys.json').write_text(json.dumps(['flz:NEWUSDT_LONG']))
    universe, source = tradeable_symbols(tmp_path)
    assert universe == ['NEWUSDT'] and source == 'tradeable_keys'
    row, detail = build_completed(memory.bars['NEWUSDT'], {}, START, START + 900)
    assert row['close'] == 114 and detail['observed_prefix_minutes'] == 13


def test_first13_prefix_gaps_use_only_actually_observed_marks():
    observed = marks()
    row, detail = build_completed(observed, {}, START, START + 900)
    assert row is not None and row['_observed_prefix_minutes'] == 13
    assert row['_producer'] == 'independent_crypto_15m_clock'
    del observed[START + 7 * 60]
    row, detail = build_completed(observed, {}, START, START + 900)
    assert row is None and detail['reason'] == 'missing_prefix'
    assert detail['missing_prefix_minutes'] == [START + 7 * 60]


def test_last2_minutes_must_exist_and_forming_bar_is_never_published():
    prefix = {(START + minute * 60, 60): dict(bar, _source='kline_1m') for minute, bar in enumerate(marks().values()) if minute < 13}
    observed = {stamp: bar for stamp, bar in marks().items() if stamp >= START + 780}
    assert build_completed(observed, prefix, START, START + 899.999)[0] is None
    row, _ = build_completed(observed, prefix, START, START + 900)
    assert row['close'] == 115
    del observed[START + 780]
    row, detail = build_completed(observed, prefix, START, START + 900)
    assert row is None and detail['reason'] == 'missing_mark_tail'


def test_genuine_coarse_native_prefix_wins_over_observed_fallback_without_expansion():
    prefix = {(START + minute * 180, 180): {'open': 100, 'high': 500, 'low': 80, 'close': 110, '_span_s': 180, '_source': 'kline_3m'} for minute in range(4)}
    row, detail = build_completed(marks(), prefix, START, START + 900)
    assert row['high'] == 500 and row['low'] == 80
    assert detail['native_prefix_minutes'] == 12
    assert row['close'] == 115 and 'volume' not in row


def test_tradeable_book_is_exact_and_fallback_only_when_unreadable(tmp_path):
    (tmp_path / 'symbols_active.json').write_text(json.dumps(['ACTIVEUSDT']))
    (tmp_path / 'tradeable_keys.json').write_text(json.dumps(['flz:TESTUSDT_LONG', 'trb:AAPL_LONG']))
    assert tradeable_symbols(tmp_path) == (['TESTUSDT'], 'tradeable_keys')
    (tmp_path / 'tradeable_keys.json').write_text('[]')
    assert tradeable_symbols(tmp_path) == ([], 'tradeable_keys')
    (tmp_path / 'tradeable_keys.json').write_text('invalid')
    assert tradeable_symbols(tmp_path) == (['ACTIVEUSDT'], 'symbols_active_fallback')


def test_retention_covers_three_bars_without_flat_seeding():
    memory = ObservedMarks()
    for minute in range(60):
        stamp = START + minute * 60 + 1
        memory.ingest('mark_price:TESTUSDT', json.dumps({'price': 100, 'timestamp': stamp}), stamp + 0.1)
    memory.prune(START + 3600)
    assert min(memory.bars['TESTUSDT']) >= START + 900
    assert len(memory.bars['TESTUSDT']) == 45
