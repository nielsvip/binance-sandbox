import datetime as dt
import hashlib
import json
import sqlite3
import zlib
from pathlib import Path

from tools.paper_native_journal import append_capture, journal_status


def _stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _publish(folder, host, observed, rows):
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "crypto.json"
    receipt = folder / "crypto.receipt.json"
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    snapshot.write_bytes(raw)
    receipt.write_text(json.dumps({"captured_at_utc": _stamp(observed), "host": host, "venue": "crypto",
                                   "source_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    return snapshot, receipt


def _row(epoch, indicator=1, three_minute=None):
    return {"timestamp_1m": _stamp(epoch), "timestamp_3m": _stamp(three_minute or epoch), "close": indicator}


def test_native_journal_uses_default_1m_clock_and_conflict_does_not_block_other_symbols(tmp_path):
    observed = 2_000_000_000
    feed = tmp_path / "feed"
    db = tmp_path / "native_journal_crypto.sqlite3"
    snapshot, receipt = _publish(feed, "s1", observed, {"BTCUSDT": _row(observed - 10, 1), "ETHUSDT": _row(observed - 10, 2)})

    first = append_capture(db, snapshot, receipt, "s1", min_free_bytes=0, now=observed)
    assert first["inserted"] == 2

    changed_same_clock = {"BTCUSDT": _row(observed - 10, 9, observed - 10), "ETHUSDT": _row(observed + 50, 3, observed - 10)}
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
