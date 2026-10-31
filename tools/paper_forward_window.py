#!/usr/bin/env python3
"""Read-only resolution of fixed and first-available paper journal windows."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path


def validate_window(window, expected_timeframe=None):
    if not isinstance(window, dict) or not str(window.get("window_id", "")).strip():
        raise ValueError("experiment_window requires window_id")
    timeframe = window.get("source_timeframe")
    if not isinstance(timeframe, str) or not timeframe or (expected_timeframe and timeframe != expected_timeframe):
        raise ValueError("experiment_window source_timeframe mismatch")
    if "anchor" not in window:
        start = _number(window.get("start_source_ts"), "start_source_ts")
        end = _number(window.get("end_source_ts"), "end_source_ts")
        if start <= 0 or end < start:
            raise ValueError("experiment_window bounds must be positive and increasing")
        return {**window, "start_source_ts": start, "end_source_ts": end, "window_mode": "fixed"}
    if window.get("anchor") != "first_available_after":
        raise ValueError("unsupported experiment_window anchor")
    not_before = _number(window.get("not_before_source_ts"), "not_before_source_ts")
    duration = _number(window.get("duration_seconds"), "duration_seconds")
    if not_before <= 0 or duration <= 0:
        raise ValueError("dynamic experiment_window bounds must be positive")
    return {**window, "not_before_source_ts": not_before, "duration_seconds": duration, "window_mode": "first_available_after"}


def resolve_source_window(database, host, venue, symbol, window):
    """Resolve a journal window without writes; return explicit wait/readiness status."""
    try:
        normalized = validate_window(window)
    except (TypeError, ValueError) as exc:
        return {"ready": False, "status": "INVALID_EXPERIMENT_WINDOW", "detail": str(exc)}
    database = Path(database)
    if not database.is_file():
        return {"ready": False, "status": "WAITING_FOR_SOURCE_JOURNAL"}
    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            metadata = dict(connection.execute("SELECT key,value FROM journal_meta"))
            if int(metadata.get("retention_floor_sequence", "0")) > 0:
                return {"ready": False, "status": "ARCHIVE_GAP"}
            if metadata.get("host") != host or metadata.get("venue") != venue:
                return {"ready": False, "status": "SOURCE_JOURNAL_HOST_MISMATCH"}
            timeframe = normalized["source_timeframe"]
            if normalized["window_mode"] == "fixed":
                start = normalized["start_source_ts"]
                end = normalized["end_source_ts"]
            else:
                row = connection.execute("SELECT MIN(source_ts) FROM frames WHERE host=? AND venue=? AND symbol=? AND timeframe=? AND source_ts>=?",
                                         (host, venue, symbol, timeframe, normalized["not_before_source_ts"])).fetchone()
                if row is None or row[0] is None:
                    return {"ready": False, "status": "WAITING_FOR_SOURCE_WINDOW_START", "window_id": normalized["window_id"]}
                start = float(row[0])
                end = start + normalized["duration_seconds"]
            first, latest, count = connection.execute("SELECT MIN(source_ts),MAX(source_ts),COUNT(*) FROM frames WHERE host=? AND venue=? AND symbol=? AND timeframe=? AND source_ts BETWEEN ? AND ?",
                                                      (host, venue, symbol, timeframe, start, end)).fetchone()
            overall_latest = connection.execute("SELECT MAX(source_ts) FROM frames WHERE host=? AND venue=? AND symbol=? AND timeframe=?",
                                                (host, venue, symbol, timeframe)).fetchone()[0]
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return {"ready": False, "status": "SOURCE_JOURNAL_INVALID"}
    result = {"window_id": normalized["window_id"], "source_timeframe": timeframe,
              "start_source_ts": float(start), "end_source_ts": float(end),
              "first_source_ts": float(first) if first is not None else None,
              "latest_in_window_source_ts": float(latest) if latest is not None else None,
              "latest_source_ts": float(overall_latest) if overall_latest is not None else None,
              "frame_count": int(count or 0), "coverage_gap_seconds": max(0.0, float(first) - float(start)) if first is not None else None}
    if first is None:
        return {**result, "ready": False, "status": "WAITING_FOR_SOURCE_WINDOW_START"}
    if overall_latest is None or float(overall_latest) < end:
        return {**result, "ready": False, "status": "WAITING_FOR_SOURCE_WINDOW_END"}
    return {**result, "ready": True, "status": "SOURCE_WINDOW_READY"}


def _number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result
