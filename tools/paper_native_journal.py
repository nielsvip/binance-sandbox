#!/usr/bin/env python3
"""Bounded per-host native-feed journal for identical paper-worker inputs.

The collector reads only atomically published paper_native_feed snapshots and
receipts. It stores each fresh symbol/timeframe frame once, never refreshes a
source timestamp, never replaces a frame, and stops at the configured database
cap. Pruning is explicit and advances a retention floor for readers to check.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import zlib
from pathlib import Path

try:
    from .paper_native_feed import MAX_BYTES, SOURCE_BY_HOST, TIMEFRAMES, TIMEFRAME_PROVENANCE, validate_snapshot_bytes
except ImportError:
    from paper_native_feed import MAX_BYTES, SOURCE_BY_HOST, TIMEFRAMES, TIMEFRAME_PROVENANCE, validate_snapshot_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEFRAMES = ("1m", "3m", "5m")
ALLOWED_JOURNAL_TIMEFRAMES = ("1m", "3m", "5m", "15m")
DEFAULT_MAX_DB_BYTES = 1024**3
DEFAULT_DISK_RESERVE_BYTES = 8 * 1024**3
DEFAULT_MAX_CAPTURE_AGE_SECONDS = 180
MAX_RECEIPT_BYTES = 64 * 1024
META_RESERVE_BYTES = 64 * 1024


class JournalError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _epoch(value):
    if isinstance(value, bool):
        raise ValueError("timestamp cannot be bool")
    if isinstance(value, (int, float)):
        result = float(value)
        if result > 1e11:
            result /= 1000.0
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        result = parsed.timestamp()
    else:
        raise ValueError("timestamp missing or invalid")
    if not result > 0 or result == float("inf") or result != result:
        raise ValueError("timestamp must be finite and positive")
    return result


def _timeframe_seconds(timeframe):
    return {"1m": 60, "3m": 180, "5m": 300, "15m": 900}[timeframe]


def _read_stable_file(path, limit):
    path = Path(path)
    before = path.stat()
    if before.st_size <= 0 or before.st_size > limit:
        raise JournalError(f"input size is invalid: {path}")
    raw = path.read_bytes()
    after = path.stat()
    if len(raw) != before.st_size or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise JournalError(f"input changed while reading: {path}")
    return raw


def _connect(database):
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA wal_autocheckpoint=256")
    connection.execute("PRAGMA journal_size_limit=8388608")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("""CREATE TABLE IF NOT EXISTS journal_meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS payloads (
        input_hash TEXT PRIMARY KEY, payload_encoding TEXT NOT NULL CHECK(payload_encoding='zlib'),
        payload_blob BLOB NOT NULL, raw_bytes INTEGER NOT NULL, compressed_bytes INTEGER NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS frame_index (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, observed_ts REAL NOT NULL, source_ts REAL NOT NULL,
        symbol TEXT NOT NULL, venue TEXT NOT NULL, host TEXT NOT NULL, timeframe TEXT NOT NULL,
        source_sha256 TEXT NOT NULL, input_hash TEXT NOT NULL REFERENCES payloads(input_hash),
        provenance_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(host,source_ts,symbol,timeframe)
    )""")
    frame_columns = {row[1] for row in connection.execute("PRAGMA table_info(frame_index)")}
    if "provenance_json" not in frame_columns:
        connection.execute("ALTER TABLE frame_index ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'")
    connection.execute("""CREATE TABLE IF NOT EXISTS source_conflicts (
        conflict_id INTEGER PRIMARY KEY AUTOINCREMENT, observed_ts REAL NOT NULL, source_ts REAL NOT NULL,
        symbol TEXT NOT NULL, venue TEXT NOT NULL, host TEXT NOT NULL, timeframe TEXT NOT NULL,
        existing_input_hash TEXT, incoming_input_hash TEXT NOT NULL, reason TEXT NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS frame_symbol_tf_seq ON frame_index(host,venue,symbol,timeframe,sequence)")
    view_columns = {row[1] for row in connection.execute("PRAGMA table_info(frames)")}
    if "provenance_json" not in view_columns:
        connection.execute("DROP VIEW IF EXISTS frames")
    connection.execute("""CREATE VIEW IF NOT EXISTS frames AS
        SELECT f.sequence,f.observed_ts,f.source_ts,f.symbol,f.venue,f.host,f.timeframe,
               f.source_sha256,f.input_hash,f.provenance_json,p.payload_encoding,p.payload_blob
        FROM frame_index AS f JOIN payloads AS p ON p.input_hash=f.input_hash""")
    connection.execute("INSERT OR IGNORE INTO journal_meta(key,value) VALUES('schema_version','2')")
    connection.execute("INSERT OR IGNORE INTO journal_meta(key,value) VALUES('retention_floor_sequence','0')")
    connection.execute("UPDATE journal_meta SET value='2' WHERE key='schema_version'")
    connection.commit()
    return connection


def _db_bytes(database):
    database = Path(database)
    return sum(path.stat().st_size for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")) if path.exists())


def _set_meta(connection, values):
    connection.executemany("INSERT INTO journal_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(str(key), str(value)) for key, value in values.items()])


def inspect_source(snapshot_path, receipt_path, host, timeframes=DEFAULT_TIMEFRAMES, now=None,
                   max_capture_age_seconds=DEFAULT_MAX_CAPTURE_AGE_SECONDS):
    host = str(host).strip().lower()
    if host not in SOURCE_BY_HOST:
        raise JournalError("host must be one of s1, s2, s5, s6")
    expected_venue = SOURCE_BY_HOST[host][0]
    timeframes = tuple(timeframes)
    if not timeframes or any(timeframe not in ALLOWED_JOURNAL_TIMEFRAMES for timeframe in timeframes):
        raise JournalError("timeframes must be selected from 1m, 3m, 5m, 15m")
    now = time.time() if now is None else float(now)
    raw = _read_stable_file(snapshot_path, MAX_BYTES)
    receipt_raw = _read_stable_file(receipt_path, MAX_RECEIPT_BYTES)
    try:
        receipt = json.loads(receipt_raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        snapshot = validate_snapshot_bytes(raw)
        observed_ts = _epoch(receipt.get("captured_at_utc"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise JournalError("snapshot or receipt is invalid") from exc
    if _digest(raw) != receipt.get("source_sha256"):
        raise JournalError("snapshot receipt hash mismatch")
    if receipt.get("host") != host or receipt.get("venue") != expected_venue:
        raise JournalError("receipt host/venue does not match requested journal")
    if observed_ts > now + 5:
        raise JournalError("receipt observation time is in the future")
    if now - observed_ts > max_capture_age_seconds:
        return {"status": "STALE_CAPTURE", "host": host, "venue": expected_venue, "observed_ts": observed_ts,
                "source_sha256": _digest(raw), "candidates": [], "stale_count": 0, "invalid_count": 0}
    provenance = receipt.get("timeframe_provenance")
    expected_provenance = TIMEFRAME_PROVENANCE[expected_venue]
    verified_timeframes, provenance_gaps = [], []
    for timeframe in timeframes:
        entry = provenance.get(timeframe) if isinstance(provenance, dict) else None
        expected_entry = expected_provenance.get(timeframe)
        if timeframe == "1m":
            verified = (receipt.get("verified_native_1m") is True and isinstance(entry, dict)
                        and isinstance(expected_entry, dict) and expected_entry.get("status") == "VERIFIED_NATIVE"
                        and entry.get("status") == "VERIFIED_NATIVE" and entry.get("source_field") == "timestamp_1m"
                        and entry.get("producer_source_path") == expected_entry.get("producer_source_path")
                        and entry.get("producer_source_sha256") == expected_entry.get("producer_source_sha256"))
        else:
            verified = (isinstance(entry, dict) and isinstance(expected_entry, dict)
                        and expected_entry.get("status") == "NATIVE_SOURCE" and entry.get("status") == "NATIVE_SOURCE"
                        and entry.get("source_field") == f"timestamp_{timeframe}"
                        and entry.get("source_field") == expected_entry.get("source_field")
                        and entry.get("producer_source_path") == expected_entry.get("producer_source_path")
                        and entry.get("producer_source_sha256") == expected_entry.get("producer_source_sha256"))
        if verified:
            verified_timeframes.append(timeframe)
        else:
            provenance_gaps.append(timeframe)
    candidates, stale_count, invalid_count = [], 0, 0
    if provenance_gaps:
        invalid_count += sum(1 for symbol in snapshot if not symbol.startswith("_")) * len(provenance_gaps)
    if not verified_timeframes:
        return {"status": "UNVERIFIED_NATIVE_PROVENANCE", "host": host, "venue": expected_venue,
                "observed_ts": observed_ts, "source_sha256": _digest(raw), "candidates": [],
                "stale_count": 0, "invalid_count": invalid_count, "provenance_gaps": provenance_gaps,
                "blocked_timeframes": provenance_gaps, "eligible_timeframes": []}
    for symbol, row in snapshot.items():
        if symbol.startswith("_") or not isinstance(row, dict):
            continue
        payload = _canonical(row)
        input_hash = _digest(payload)
        compressed = zlib.compress(payload, level=6)
        for timeframe in verified_timeframes:
            try:
                source_ts = _epoch(row.get(f"timestamp_{timeframe}"))
            except (ValueError, TypeError, OverflowError):
                invalid_count += 1
                continue
            if source_ts > observed_ts + 5:
                invalid_count += 1
                continue
            if observed_ts - source_ts > 2 * _timeframe_seconds(timeframe):
                stale_count += 1
                continue
            provenance_entry = provenance[timeframe]
            archived_provenance = {"schema_version": 1, "timeframe": timeframe,
                                   "status": provenance_entry["status"],
                                   "source_field": provenance_entry["source_field"],
                                   "producer_source_path": provenance_entry["producer_source_path"],
                                   "producer_source_sha256": provenance_entry["producer_source_sha256"],
                                   "receipt_verified_native_1m": receipt.get("verified_native_1m") is True}
            candidates.append({"observed_ts": observed_ts, "source_ts": source_ts, "symbol": symbol,
                               "venue": expected_venue, "host": host, "timeframe": timeframe,
                               "source_sha256": _digest(raw), "input_hash": input_hash,
                               "payload_blob": compressed, "raw_bytes": len(payload),
                               "provenance_json": _canonical(archived_provenance).decode("utf-8")})
    return {"status": "READY" if candidates else "NO_FRESH_FRAMES", "host": host, "venue": expected_venue,
            "observed_ts": observed_ts, "source_sha256": _digest(raw), "candidates": candidates,
            "stale_count": stale_count, "invalid_count": invalid_count, "provenance_gaps": provenance_gaps,
            "blocked_timeframes": provenance_gaps, "eligible_timeframes": verified_timeframes}


def _append_capture(database, snapshot_path, receipt_path, host, timeframes=DEFAULT_TIMEFRAMES,
                    max_database_bytes=DEFAULT_MAX_DB_BYTES, min_free_bytes=DEFAULT_DISK_RESERVE_BYTES,
                    max_capture_age_seconds=DEFAULT_MAX_CAPTURE_AGE_SECONDS, now=None):
    database = Path(database).expanduser().resolve()
    if max_database_bytes <= META_RESERVE_BYTES or min_free_bytes < 0:
        raise ValueError("database cap or disk reserve is invalid")
    now = time.time() if now is None else float(now)
    source = inspect_source(snapshot_path, receipt_path, host, timeframes, now, max_capture_age_seconds)
    if source["status"] != "READY":
        return {key: value for key, value in source.items() if key != "candidates"} | {"inserted": 0, "duplicates": 0}
    free_bytes = shutil.disk_usage(database.parent).free if database.parent.exists() else shutil.disk_usage(database.parent.parent).free
    if free_bytes < min_free_bytes:
        return {"status": "DISK_RESERVE_BLOCKED", "host": source["host"], "venue": source["venue"],
                "free_bytes": free_bytes, "required_free_bytes": min_free_bytes, "inserted": 0, "duplicates": 0,
                "source_sha256": source["source_sha256"], "blocked_timeframes": source["blocked_timeframes"]}
    connection = _connect(database)
    inserted = duplicates = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        previous_status = connection.execute("SELECT value FROM journal_meta WHERE key='status'").fetchone()
        if previous_status and previous_status[0] == "ARCHIVE_FULL":
            connection.execute("ROLLBACK")
            return {"status": "ARCHIVE_FULL", "host": source["host"], "venue": source["venue"], "inserted": 0, "duplicates": 0,
                    "source_sha256": source["source_sha256"], "database_bytes": _db_bytes(database),
                    "blocked_timeframes": source["blocked_timeframes"]}
        pending, conflicts = [], []
        for candidate in source["candidates"]:
            existing = connection.execute("SELECT input_hash FROM frame_index WHERE host=? AND source_ts=? AND symbol=? AND timeframe=?",
                                          (candidate["host"], candidate["source_ts"], candidate["symbol"], candidate["timeframe"])).fetchone()
            if existing:
                if existing[0] != candidate["input_hash"]:
                    conflicts.append({"candidate": candidate, "existing_input_hash": existing[0], "reason": "SAME_SOURCE_TIMESTAMP_CHANGED_PAYLOAD"})
                duplicates += 1
                continue
            last = connection.execute("SELECT MAX(source_ts) FROM frame_index WHERE host=? AND symbol=? AND timeframe=?",
                                      (candidate["host"], candidate["symbol"], candidate["timeframe"])).fetchone()[0]
            if last is not None and candidate["source_ts"] <= last:
                conflicts.append({"candidate": candidate, "existing_input_hash": None, "reason": "OUT_OF_ORDER_SOURCE_TIMESTAMP"})
                continue
            pending.append(candidate)
        for conflict in conflicts:
            candidate = conflict["candidate"]
            connection.execute("INSERT INTO source_conflicts(observed_ts,source_ts,symbol,venue,host,timeframe,existing_input_hash,incoming_input_hash,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                               (candidate["observed_ts"], candidate["source_ts"], candidate["symbol"], candidate["venue"], candidate["host"], candidate["timeframe"], conflict["existing_input_hash"], candidate["input_hash"], conflict["reason"]))
        unique_payloads = {}
        for candidate in pending:
            unique_payloads[candidate["input_hash"]] = (candidate["payload_blob"], candidate["raw_bytes"])
        current_bytes = _db_bytes(database)
        estimated_growth = META_RESERVE_BYTES + 2 * sum(len(blob) + raw_bytes + 2048 for blob, raw_bytes in unique_payloads.values()) + 1024 * len(pending)
        if current_bytes + estimated_growth > max_database_bytes:
            _set_meta(connection, {"status": "ARCHIVE_FULL", "last_observed_ts": source["observed_ts"], "last_source_sha256": source["source_sha256"],
                                   "max_database_bytes": max_database_bytes})
            connection.execute("COMMIT")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {"status": "ARCHIVE_FULL", "host": source["host"], "venue": source["venue"], "inserted": 0,
                    "duplicates": duplicates, "source_sha256": source["source_sha256"], "database_bytes": _db_bytes(database),
                    "max_database_bytes": max_database_bytes, "blocked_timeframes": source["blocked_timeframes"]}
        if free_bytes - estimated_growth < min_free_bytes:
            _set_meta(connection, {"status": "DISK_RESERVE_BLOCKED", "last_observed_ts": source["observed_ts"], "last_source_sha256": source["source_sha256"]})
            connection.execute("COMMIT")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {"status": "DISK_RESERVE_BLOCKED", "host": source["host"], "venue": source["venue"], "inserted": 0,
                    "duplicates": duplicates, "free_bytes": free_bytes, "required_free_bytes": min_free_bytes,
                    "blocked_timeframes": source["blocked_timeframes"]}
        for input_hash, (blob, raw_bytes) in unique_payloads.items():
            connection.execute("INSERT OR IGNORE INTO payloads(input_hash,payload_encoding,payload_blob,raw_bytes,compressed_bytes) VALUES(?,?,?,?,?)",
                               (input_hash, "zlib", sqlite3.Binary(blob), raw_bytes, len(blob)))
        for candidate in pending:
            connection.execute("INSERT INTO frame_index(observed_ts,source_ts,symbol,venue,host,timeframe,source_sha256,input_hash,provenance_json) VALUES(?,?,?,?,?,?,?,?,?)",
                               (candidate["observed_ts"], candidate["source_ts"], candidate["symbol"], candidate["venue"], candidate["host"], candidate["timeframe"], candidate["source_sha256"], candidate["input_hash"], candidate["provenance_json"]))
            inserted += 1
        if _db_bytes(database) > max_database_bytes:
            connection.execute("ROLLBACK")
            connection.execute("BEGIN IMMEDIATE")
            _set_meta(connection, {"status": "ARCHIVE_FULL", "last_error": "actual transaction growth exceeded configured cap",
                                   "max_database_bytes": max_database_bytes})
            connection.execute("COMMIT")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {"status": "ARCHIVE_FULL", "host": source["host"], "venue": source["venue"], "inserted": 0,
                    "duplicates": duplicates, "database_bytes": _db_bytes(database), "max_database_bytes": max_database_bytes,
                    "blocked_timeframes": source["blocked_timeframes"]}
        last_sequence = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM frame_index").fetchone()[0]
        _set_meta(connection, {"status": "SOURCE_CONFLICT" if conflicts else "OK", "host": source["host"], "venue": source["venue"], "last_observed_ts": source["observed_ts"],
                               "last_source_sha256": source["source_sha256"], "latest_sequence": last_sequence,
                               "last_stale_count": source["stale_count"], "last_invalid_count": source["invalid_count"],
                               "last_conflict_count": len(conflicts),
                               "retention_floor_sequence": _meta_value(connection, "retention_floor_sequence", "0")})
        connection.execute("COMMIT")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database_bytes = _db_bytes(database)
        return {"status": "SOURCE_CONFLICT" if conflicts else "OK" if inserted else "NO_NEW_FRAMES", "host": source["host"], "venue": source["venue"],
                "inserted": inserted, "duplicates": duplicates, "stale_count": source["stale_count"],
                "invalid_count": source["invalid_count"], "latest_sequence": last_sequence,
                "source_sha256": source["source_sha256"], "database_bytes": database_bytes, "conflicts": len(conflicts),
                "provenance_gaps": source["provenance_gaps"], "blocked_timeframes": source["blocked_timeframes"],
                "eligible_timeframes": source["eligible_timeframes"]}
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _write_poll_status(database, result, now=None):
    database = Path(database)
    status_path = Path(f"{database}.status.json")
    if status_path.is_symlink():
        raise JournalError("journal poll status path may not be a symlink")
    status_path.parent.mkdir(parents=True, exist_ok=True)
    observed = time.time() if now is None else float(now)
    status = {key: value for key, value in result.items() if key != "candidates"}
    status["poll_observed_at_utc"] = dt.datetime.fromtimestamp(observed, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    encoded = json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n"
    temporary = status_path.with_name(f".{status_path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, status_path)
    return status


def append_capture(database, snapshot_path, receipt_path, host, timeframes=DEFAULT_TIMEFRAMES,
                   max_database_bytes=DEFAULT_MAX_DB_BYTES, min_free_bytes=DEFAULT_DISK_RESERVE_BYTES,
                   max_capture_age_seconds=DEFAULT_MAX_CAPTURE_AGE_SECONDS, now=None):
    database = Path(database).expanduser().resolve()
    try:
        result = _append_capture(database, snapshot_path, receipt_path, host, timeframes,
                                 max_database_bytes, min_free_bytes, max_capture_age_seconds, now)
    except Exception as exc:
        _write_poll_status(database, {"status": "INPUT_ERROR", "host": host, "error": f"{type(exc).__name__}: {exc}"}, now)
        raise
    _write_poll_status(database, result, now)
    return result


def _meta_value(connection, key, default=None):
    row = connection.execute("SELECT value FROM journal_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def journal_status(database):
    database = Path(database).resolve(strict=True)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        meta = {key: value for key, value in connection.execute("SELECT key,value FROM journal_meta")}
        count = connection.execute("SELECT COUNT(*) FROM frame_index").fetchone()[0]
        oldest = connection.execute("SELECT MIN(sequence) FROM frame_index").fetchone()[0]
        latest = connection.execute("SELECT MAX(sequence) FROM frame_index").fetchone()[0]
        by_timeframe = dict(connection.execute("SELECT timeframe,COUNT(*) FROM frame_index GROUP BY timeframe").fetchall())
        conflict_count = connection.execute("SELECT COUNT(*) FROM source_conflicts").fetchone()[0]
    finally:
        connection.close()
    return {"host": meta.get("host"), "venue": meta.get("venue"), "status": meta.get("status", "EMPTY"),
            "frame_count": count, "oldest_sequence": oldest, "latest_sequence": latest,
            "retention_floor_sequence": int(meta.get("retention_floor_sequence", "0")),
            "source_conflicts": conflict_count,
            "last_observed_ts": float(meta["last_observed_ts"]) if "last_observed_ts" in meta else None,
            "timeframes": by_timeframe, "database_bytes": _db_bytes(database)}


def prune_before_sequence(database, before_sequence):
    """Explicitly prune history; readers anchored before the new floor must fail closed."""
    if isinstance(before_sequence, bool) or int(before_sequence) < 1:
        raise ValueError("before_sequence must be a positive sequence")
    connection = _connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        old_floor = int(_meta_value(connection, "retention_floor_sequence", "0"))
        floor = max(old_floor, int(before_sequence) - 1)
        connection.execute("DELETE FROM frame_index WHERE sequence < ?", (int(before_sequence),))
        connection.execute("DELETE FROM payloads WHERE input_hash NOT IN (SELECT DISTINCT input_hash FROM frame_index)")
        _set_meta(connection, {"retention_floor_sequence": floor, "status": "RETENTION_PRUNED"})
        connection.execute("COMMIT")
        connection.execute("PRAGMA incremental_vacuum")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"status": "RETENTION_PRUNED", "retention_floor_sequence": floor, **journal_status(database)}
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, choices=tuple(SOURCE_BY_HOST))
    parser.add_argument("--base", type=Path, default=ROOT / "data" / "paper_forward_lab")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--max-db-bytes", type=int, default=DEFAULT_MAX_DB_BYTES)
    parser.add_argument("--min-free-bytes", type=int, default=DEFAULT_DISK_RESERVE_BYTES)
    parser.add_argument("--max-capture-age-seconds", type=float, default=DEFAULT_MAX_CAPTURE_AGE_SECONDS)
    parser.add_argument("--poll-seconds", type=float, default=60)
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES), help="native source clocks: 1m, 3m, 5m, 15m")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--prune-before-sequence", type=int)
    args = parser.parse_args(argv)
    venue = SOURCE_BY_HOST[args.host][0]
    database = args.database or args.base / f"native_journal_{venue}.sqlite3"
    if args.status:
        print(json.dumps(journal_status(database), sort_keys=True), flush=True)
        return 0
    if args.prune_before_sequence is not None:
        print(json.dumps(prune_before_sequence(database, args.prune_before_sequence), sort_keys=True), flush=True)
        return 0
    snapshot = args.snapshot or args.base / "feed" / f"{venue}.json"
    receipt = args.receipt or args.base / "feed" / f"{venue}.receipt.json"
    timeframes = tuple(value.strip() for value in args.timeframes.split(",") if value.strip())
    while True:
        result = append_capture(database, snapshot, receipt, args.host, timeframes, args.max_db_bytes, args.min_free_bytes, args.max_capture_age_seconds)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once or result.get("status") in {"ARCHIVE_FULL", "DISK_RESERVE_BLOCKED"}:
            break
        time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
