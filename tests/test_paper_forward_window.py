import sqlite3

from tools.paper_forward_window import resolve_source_window


def _journal(path, frames):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE journal_meta(key TEXT PRIMARY KEY,value TEXT)")
        connection.executemany("INSERT INTO journal_meta VALUES(?,?)", [("host", "s5"), ("venue", "stocks"), ("retention_floor_sequence", "0")])
        connection.execute("CREATE TABLE frames(sequence INTEGER,host TEXT,venue TEXT,symbol TEXT,timeframe TEXT,source_ts REAL)")
        connection.executemany("INSERT INTO frames VALUES(?,?,?,?,?,?)", [(index, "s5", "stocks", "AAPL", "1m", timestamp) for index, timestamp in enumerate(frames, 1)])


def test_first_available_window_waits_then_resolves_same_fixed_bounds(tmp_path):
    journal = tmp_path / "native_journal_stocks.sqlite3"
    _journal(journal, [100.0, 160.0])
    window = {"window_id": "weekend-open", "source_timeframe": "1m", "not_before_source_ts": 90.0, "duration_seconds": 120.0, "anchor": "first_available_after"}

    waiting = resolve_source_window(journal, "s5", "stocks", "AAPL", window)

    assert not waiting["ready"]
    assert waiting["status"] == "WAITING_FOR_SOURCE_WINDOW_END"
    assert waiting["start_source_ts"] == 100.0
    assert waiting["end_source_ts"] == 220.0
    assert waiting["coverage_gap_seconds"] == 0.0

    with sqlite3.connect(journal) as connection:
        connection.execute("INSERT INTO frames VALUES(?,?,?,?,?,?)", (3, "s5", "stocks", "AAPL", "1m", 220.0))

    ready = resolve_source_window(journal, "s5", "stocks", "AAPL", window)

    assert ready["ready"]
    assert ready["status"] == "SOURCE_WINDOW_READY"
    assert (ready["start_source_ts"], ready["end_source_ts"]) == (100.0, 220.0)


def test_first_available_window_uses_first_source_after_not_before(tmp_path):
    journal = tmp_path / "native_journal_stocks.sqlite3"
    _journal(journal, [100.0, 160.0, 220.0, 280.0, 340.0])
    window = {"window_id": "market-open", "source_timeframe": "1m", "not_before_source_ts": 150.0, "duration_seconds": 120.0, "anchor": "first_available_after"}

    resolved = resolve_source_window(journal, "s5", "stocks", "AAPL", window)

    assert resolved["ready"]
    assert resolved["start_source_ts"] == 160.0
    assert resolved["end_source_ts"] == 280.0
    assert resolved["coverage_gap_seconds"] == 0.0


def test_first_available_window_does_not_claim_readiness_after_archive_prune(tmp_path):
    journal = tmp_path / "native_journal_stocks.sqlite3"
    _journal(journal, [100.0, 160.0, 220.0, 280.0])
    with sqlite3.connect(journal) as connection:
        connection.execute("UPDATE journal_meta SET value='2' WHERE key='retention_floor_sequence'")
    window = {"window_id": "market-open", "source_timeframe": "1m", "not_before_source_ts": 100.0, "duration_seconds": 120.0, "anchor": "first_available_after"}

    result = resolve_source_window(journal, "s5", "stocks", "AAPL", window)

    assert not result["ready"]
    assert result["status"] == "ARCHIVE_GAP"
