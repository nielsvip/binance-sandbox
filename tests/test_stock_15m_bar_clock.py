import json
from datetime import datetime, timezone

from closed_bar_feed import atomic_json
from tools.crypto_15m_bar_clock import ObservedMarks
from tools.stock_15m_bar_clock import completed_stock_row, ingest_quotes, native_prefix, quote_collection_window, quote_mark, stock_symbols

START = int(datetime(2026, 10, 26, 19, 45, tzinfo=timezone.utc).timestamp())


def test_quotes_start_one_hour_before_regular_session():
    opening, closing = START - 6 * 3600 - 15 * 60, START + 15 * 60
    assert not quote_collection_window(opening - 3601, (opening, closing))
    assert quote_collection_window(opening - 3600, (opening, closing))
    assert quote_collection_window(opening - 1, (opening, closing))
    assert quote_collection_window(opening, (opening, closing))
    assert not quote_collection_window(closing, (opening, closing))


def test_fresh_response_uses_current_bid_ask_with_old_last_trade():
    observed = ObservedMarks()
    quote = {'bid': 100, 'ask': 102, 'last': 80, 'date': (START - 86400) * 1000}
    assert quote_mark(quote) == 101
    assert ingest_quotes(observed, {'A': quote}, START + 780, START + 780, 'actual_response') == 1
    assert observed.bars['A'][START + 780]['close'] == 101
    assert ingest_quotes(observed, {'A': dict(quote, received_at=START)}, None, START + 781, 'cached') == 0


def test_full_observed_window_and_two_minute_tail_are_required():
    bars = {START + minute * 60: {'open': 100 + minute, 'high': 102 + minute, 'low': 99 + minute, 'close': 101 + minute} for minute in range(15)}
    assert completed_stock_row(bars, {}, START, START + 899) is None
    result = completed_stock_row(bars, {}, START, START + 900)
    assert [result[key] for key in ('open', 'high', 'low', 'close')] == [100, 116, 99, 115]
    assert result['_producer'] == 'independent_stock_15m_clock'
    del bars[START + 840]
    assert completed_stock_row(bars, {}, START, START + 900) is None
    weekend = START + 5 * 86400
    assert completed_stock_row(bars, {}, weekend, weekend + 900) is None


def test_native_prefix_rejects_unverified_and_forming_snapshots(tmp_path):
    folder = tmp_path / 'klines_cache/tradier'
    records = [dict(timestamp=START + minute * 60, open=100, high=102, low=99, close=101, volume=0) for minute in range(15)]
    records[0]['_closed_verified_at'] = START + 60
    records[1].update(_closed_verified_at=START + 120, _provisional=True)
    atomic_json(folder / 'A_1m.json', records)
    rows = native_prefix(tmp_path, 'A', START)
    assert set(rows) == {(START, 60)}
    actual = [dict(records[0], timestamp=START + minute * 60, _closed_verified_at=START + (minute + 1) * 60) for minute in range(13)]
    atomic_json(folder / 'A_1m.json', actual)
    assert len(native_prefix(tmp_path, 'A', START)) == 13


def test_exact_stock_account_books_and_empty_books(tmp_path):
    atomic_json(tmp_path / 'symbols_trb_long.json', ['A', 'MSFT'])
    atomic_json(tmp_path / 'symbols_tra_short.json', ['A', 'TSLA'])
    assert stock_symbols(tmp_path) == ['A', 'MSFT', 'TSLA']
    atomic_json(tmp_path / 'symbols_trb_long.json', [])
    atomic_json(tmp_path / 'symbols_tra_short.json', [])
    assert stock_symbols(tmp_path) == []
