import datetime as dt
import hashlib
import json
import sqlite3
import zlib
from pathlib import Path

from tools.paper_native_feed import TIMEFRAME_PROVENANCE
from tools.paper_native_journal import _connect, append_capture, inspect_source, journal_status


def _stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _publish(folder, host, observed, rows, venue="crypto"):
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / f"{venue}.json"
    receipt = folder / f"{venue}.receipt.json"
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    snapshot.write_bytes(raw)
    receipt.write_text(json.dumps({"captured_at_utc": _stamp(observed), "host": host, "venue": venue,
                                   "source_sha256": hashlib.sha256(raw).hexdigest(), "verified_native_1m": False,
                                   "timeframe_provenance": TIMEFRAME_PROVENANCE[venue]}), encoding="utf-8")
    return snapshot, receipt


def _row(epoch, indicator=1, three_minute=None):
    return {"timestamp_1m": _stamp(epoch), "timestamp_3m": _stamp(three_minute or epoch), "close": indicator}


def test_native_journal_skips_aliased_1m_and_archives_eligible_3m_with_conflicts_isolated(tmp_path):
    observed = 2_000_000_000
    feed = tmp_path / "feed"
    db = tmp_path / "native_journal_crypto.sqlite3"
    snapshot, receipt = _publish(feed, "s1", observed, {"BTCUSDT": _row(observed - 10, 1), "ETHUSDT": _row(observed - 10, 2)})

    first = append_capture(db, snapshot, receipt, "s1", min_free_bytes=0, now=observed)
    assert first["inserted"] == 2
    assert first["blocked_timeframes"] == ["1m", "5m"]
    with sqlite3.connect(db) as connection:
        provenance = json.loads(connection.execute("SELECT provenance_json FROM frames WHERE symbol='BTCUSDT'").fetchone()[0])
    assert provenance["timeframe"] == "3m"
    assert provenance["status"] == "NATIVE_SOURCE"
    assert provenance["source_field"] == "timestamp_3m"
    assert provenance["producer_source_sha256"] == TIMEFRAME_PROVENANCE["crypto"]["3m"]["producer_source_sha256"]
    assert provenance["receipt_verified_native_1m"] is False
    with sqlite3.connect(db) as connection:
        source_index = [row[2] for row in connection.execute("PRAGMA index_info(frame_source_window)")]
    assert source_index == ["host", "venue", "symbol", "timeframe", "source_ts", "sequence"]

    changed_same_clock = {"BTCUSDT": _row(observed - 10, 9, observed - 10), "ETHUSDT": _row(observed - 10, 3, observed + 50)}
    snapshot, receipt = _publish(feed, "s1", observed + 60, changed_same_clock)
    second = append_capture(db, snapshot, receipt, "s1", min_free_bytes=0, now=observed + 60)

    assert second["status"] == "SOURCE_CONFLICT"
    assert second["inserted"] == 1
    assert second["conflicts"] == 1
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0] == 3
        payload = connection.execute("SELECT payload_blob FROM frames WHERE symbol='BTCUSDT'").fetchone()[0]
        assert json.loads(zlib.decompress(payload))["close"] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_conflicts").fetchone()[0] == 1
    assert journal_status(db)["source_conflicts"] == 1

    snapshot, receipt = _publish(feed, "s1", observed + 120,
                                 {"BTCUSDT": _row(observed + 110, 10), "ETHUSDT": _row(observed + 110, 11)})
    recovered = append_capture(db, snapshot, receipt, "s1", min_free_bytes=0, now=observed + 120)
    assert recovered["status"] == "OK"
    assert recovered["inserted"] == 2


def test_native_journal_hard_cap_is_explicit_and_sticky(tmp_path):
    observed = 2_000_000_000
    snapshot, receipt = _publish(tmp_path / "feed", "s1", observed, {"BTCUSDT": _row(observed - 10)})
    db = tmp_path / "native_journal_crypto.sqlite3"

    result = append_capture(db, snapshot, receipt, "s1", max_database_bytes=65_537, min_free_bytes=0, now=observed)

    assert result["status"] == "ARCHIVE_FULL"
    assert journal_status(db)["status"] == "ARCHIVE_FULL"
    blocked = append_capture(db, snapshot, receipt, "s1", max_database_bytes=1_000_000, min_free_bytes=0, now=observed)
    assert blocked["status"] == "ARCHIVE_FULL"


def test_native_journal_rejects_stale_capture_without_synthesizing_time(tmp_path):
    observed = 2_000_000_000
    snapshot, receipt = _publish(tmp_path / "feed", "s1", observed - 181, {"BTCUSDT": _row(observed - 10)})

    result = append_capture(tmp_path / "native_journal_crypto.sqlite3", snapshot, receipt, "s1", min_free_bytes=0, now=observed)

    assert result["status"] == "STALE_CAPTURE"
    assert result["inserted"] == 0
    assert not Path(tmp_path / "native_journal_crypto.sqlite3").exists()


def test_native_journal_rejects_fresh_known_aliased_1m_clock(tmp_path):
    observed = 2_000_000_000
    snapshot, receipt = _publish(tmp_path / "feed", "s1", observed, {"BTCUSDT": _row(observed - 10, 1)})
    data = json.loads(receipt.read_text())
    data["verified_native_1m"] = True
    data["timeframe_provenance"]["1m"].update(status="VERIFIED_NATIVE", source_field="timestamp_1m")
    receipt.write_text(json.dumps(data), encoding="utf-8")

    result = append_capture(tmp_path / "native_journal_crypto.sqlite3", snapshot, receipt, "s1", timeframes=("1m",), min_free_bytes=0, now=observed)

    assert result["status"] == "UNVERIFIED_NATIVE_PROVENANCE"
    assert result["provenance_gaps"] == ["1m"]
    assert result["inserted"] == 0
    assert not Path(tmp_path / "native_journal_crypto.sqlite3").exists()
    poll_status = json.loads(Path(f"{tmp_path / 'native_journal_crypto.sqlite3'}.status.json").read_text())
    assert poll_status["status"] == "UNVERIFIED_NATIVE_PROVENANCE"
    assert poll_status["provenance_gaps"] == ["1m"]
    assert poll_status["blocked_timeframes"] == ["1m"]


def test_native_journal_preserves_valid_3m_when_1m_is_a_provenance_gap(tmp_path):
    observed = 2_000_000_000
    rows = {"BTCUSDT": _row(observed - 10, 1, observed - 10)}
    snapshot, receipt = _publish(tmp_path / "feed", "s1", observed, rows)
    result = inspect_source(snapshot, receipt, "s1", timeframes=("1m", "3m"), now=observed)

    assert result["status"] == "READY"
    assert result["provenance_gaps"] == ["1m"]
    assert result["eligible_timeframes"] == ["3m"]
    assert [frame["timeframe"] for frame in result["candidates"]] == ["3m"]


def test_native_journal_accepts_native_stocks_5m_and_records_1m_gap(tmp_path):
    observed = 2_000_000_000
    row = {"timestamp_1m": _stamp(observed), "timestamp_5m": _stamp(observed - 10), "close": 10}
    snapshot, receipt = _publish(tmp_path / "feed", "s5", observed, {"AAPL": row}, venue="stocks")

    result = inspect_source(snapshot, receipt, "s5", timeframes=("1m", "3m", "5m"), now=observed)

    assert result["status"] == "READY"
    assert result["blocked_timeframes"] == ["1m"]
    assert result["eligible_timeframes"] == ["3m", "5m"]
    assert [frame["timeframe"] for frame in result["candidates"]] == ["5m"]


def test_native_journal_requires_producer_source_hash_proof(tmp_path):
    observed = 2_000_000_000
    snapshot, receipt = _publish(tmp_path / "feed", "s1", observed, {"BTCUSDT": _row(observed - 10)})
    data = json.loads(receipt.read_text())
    data["timeframe_provenance"]["3m"]["producer_source_sha256"] = "0" * 64
    receipt.write_text(json.dumps(data), encoding="utf-8")

    result = inspect_source(snapshot, receipt, "s1", timeframes=("3m",), now=observed)

    assert result["status"] == "UNVERIFIED_NATIVE_PROVENANCE"
    assert result["blocked_timeframes"] == ["3m"]


def test_native_journal_treats_legacy_receipt_without_provenance_as_blocked(tmp_path):
    observed = 2_000_000_000
    snapshot, receipt = _publish(tmp_path / "feed", "s1", observed, {"BTCUSDT": _row(observed - 10)})
    data = json.loads(receipt.read_text())
    data.pop("verified_native_1m")
    data.pop("timeframe_provenance")
    receipt.write_text(json.dumps(data), encoding="utf-8")

    result = inspect_source(snapshot, receipt, "s1", timeframes=("1m", "3m", "5m"), now=observed)

    assert result["status"] == "UNVERIFIED_NATIVE_PROVENANCE"
    assert result["blocked_timeframes"] == ["1m", "3m", "5m"]


def test_existing_journal_rows_migrate_to_unverified_provenance(tmp_path):
    database = tmp_path / "native_journal_crypto.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE payloads(input_hash TEXT PRIMARY KEY,payload_encoding TEXT,payload_blob BLOB,raw_bytes INTEGER,compressed_bytes INTEGER)")
        connection.execute("CREATE TABLE frame_index(sequence INTEGER PRIMARY KEY,observed_ts REAL NOT NULL,source_ts REAL NOT NULL,symbol TEXT NOT NULL,venue TEXT NOT NULL,host TEXT NOT NULL,timeframe TEXT NOT NULL,source_sha256 TEXT NOT NULL,input_hash TEXT NOT NULL,UNIQUE(host,source_ts,symbol,timeframe))")
        connection.execute("INSERT INTO payloads VALUES('h','zlib',X'00',1,1)")
        connection.execute("INSERT INTO frame_index VALUES(1,100,90,'BTCUSDT','crypto','s1','1m','source','h')")
        connection.execute("CREATE VIEW frames AS SELECT f.sequence,f.observed_ts,f.source_ts,f.symbol,f.venue,f.host,f.timeframe,f.source_sha256,f.input_hash,p.payload_encoding,p.payload_blob FROM frame_index f JOIN payloads p ON p.input_hash=f.input_hash")

    connection = _connect(database)
    try:
        row = connection.execute("SELECT provenance_json FROM frames WHERE sequence=1").fetchone()
    finally:
        connection.close()

    assert row == ("{}",)
