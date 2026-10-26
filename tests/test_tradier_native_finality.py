import json

import pandas as pd

import tradier_klines as TK


START = 1793037600


def native_frame(now_s):
    raw = [{'timestamp': START, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 0}]
    return TK.mark_native_finality(TK.parse_intraday(raw), 900, now_s)


def existing_mark():
    return {'timestamp': pd.Timestamp(START, unit='s', tz='UTC').isoformat(), 'open': 10, 'high': 13, 'low': 8, 'close': 12, 'volume': 10, '_closed': True, '_provisional': True, '_producer': 'independent_stock_15m_clock'}


def test_native_close_proof_begins_only_when_full_interval_elapsed():
    forming = native_frame(START + 899.999).iloc[-1]
    assert not forming['_closed'] and forming['_provisional']
    assert pd.isna(forming['_closed_verified_at'])
    closed = native_frame(START + 900).iloc[-1]
    assert closed['_closed'] and not closed['_provisional']
    assert closed['_closed_verified_at'] == START + 900 and closed['volume'] == 0


def test_verified_native_replaces_completed_marks_and_publishes_ohlcv(tmp_path):
    path = tmp_path / 'klines_cache/tradier/TEST_15m.json'
    path.parent.mkdir(parents=True)
    mark = existing_mark()
    older = dict(mark, timestamp=pd.Timestamp(START - 900, unit='s', tz='UTC').isoformat())
    path.write_text(json.dumps([older, mark]))
    assert TK.export_atomic(native_frame(START + 901), path)
    rows = json.loads(path.read_text())
    assert len(rows) == 2 and rows[0] == older
    assert rows[-1]['close'] == 11 and rows[-1]['volume'] == 0
    assert rows[-1]['_closed_verified_at'] == START + 901
    assert not rows[-1]['_provisional']
    event = json.loads((tmp_path / f'data/closed_15m_outbox/tradier/TEST_{START}.json').read_text())
    assert event['row']['close'] == 11 and event['row']['volume'] == 0
    assert event['row']['_closed_verified_at'] == START + 901


def test_late_forming_native_cannot_replace_completed_ohlc(tmp_path):
    path = tmp_path / 'klines_cache/tradier/TEST_15m.json'
    path.parent.mkdir(parents=True)
    mark = existing_mark()
    path.write_text(json.dumps([mark]))
    assert TK.export_atomic(native_frame(START + 899), path)
    assert json.loads(path.read_text()) == [mark]
    assert not (tmp_path / 'data/closed_15m_outbox').exists()


def test_native_reply_with_after_hours_rows_publishes_final_regular_session_bar(tmp_path):
    final = int(pd.Timestamp('2026-10-26T19:45:00Z').timestamp())
    raw = [dict(timestamp=stamp, open=10, high=12, low=9, close=11, volume=0) for stamp in (final, final + 900)]
    frame = TK.mark_native_finality(TK.parse_intraday(raw), 900, final + 3600)
    path = tmp_path / 'klines_cache/tradier/TEST_15m.json'
    assert TK.export_atomic(frame, path)
    assert len(json.loads(path.read_text())) == 2
    outputs = list((tmp_path / 'data/closed_15m_outbox/tradier').glob('*.json'))
    assert len(outputs) == 1
    assert json.loads(outputs[0].read_text())['bar_start_ts'] == final
