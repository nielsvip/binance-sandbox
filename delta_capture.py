"""Project candidate for compact, lossless DELTA_GATE_BB_SQUEEZE capture."""
from __future__ import annotations

import json
import math
import sqlite3
import zlib
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA = "live_delta_capture_v1"
MAX_SPOOL_BYTES = 1_000_000_000
REQUIRED_SIGNAL_FIELDS = (
    "entry_long", "entry_short", "bull_speed", "bear_speed",
    "bull_tf_count", "bear_tf_count", "total_fields",
)


class DeltaInputCapture(Mapping):
    """Read-only mapping that retains only keys the tracker actually requests."""

    def __init__(self, source: Mapping[str, Any]):
        self.source = source
        self.captured: dict[str, Any] = {}

    def get(self, key: Any, default: Any = None) -> Any:
        value = self.source.get(key, default)
        self.captured[str(key)] = value
        return value

    def __getitem__(self, key: Any) -> Any:
        value = self.source[key]
        self.captured[str(key)] = value
        return value

    def __iter__(self):
        return iter(self.source)

    def __len__(self) -> int:
        return len(self.source)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def capture_timestamp(indicators: Mapping[str, Any]) -> int:
    """Accept only `_tick_ts` at a completed 3m epoch boundary; otherwise 0."""
    try:
        stamp = int(float(indicators.get("_tick_ts") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    if stamp > 100_000_000_000:
        stamp //= 1000
    raw_bar_time = indicators.get("timestamp")
    if raw_bar_time is None:
        return 0
    try:
        if isinstance(raw_bar_time, (int, float)):
            bar_stamp = int(float(raw_bar_time))
            if bar_stamp > 100_000_000_000:
                bar_stamp //= 1000
        else:
            parsed = datetime.fromisoformat(str(raw_bar_time).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return 0
            bar_stamp = int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0
    return stamp if stamp > 0 and stamp % 180 == 0 and bar_stamp == stamp else 0


def capture_db_path(root: str | Path, symbol: str, source: str, timestamp: int) -> Path:
    """Route captures into UTC-day shards; refuse a new day with old backlog."""
    if source not in {"quick", "order"}:
        raise ValueError(f"unsupported capture source: {source}")
    day = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    for child in base.iterdir():
        if child.is_dir() and len(child.name) == 10 and child.name < day:
            raise RuntimeError(f"capture paused: older unconfirmed spool day {child.name}")
    return base / day / f"{str(symbol).upper()}.{source}.sqlite"


def _spool_bytes(root: Path) -> int:
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file()) if root.exists() else 0


def _connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE IF NOT EXISTS delta_capture (timestamp INTEGER PRIMARY KEY, payload BLOB NOT NULL)")
    db.commit()
    return db


def _load_rows(paths: str | Path | list[str | Path] | tuple[str | Path, ...]) -> list[dict[str, Any]]:
    sources = [paths] if isinstance(paths, (str, Path)) else list(paths)
    values = []
    for path in sources:
        with _connect(path) as db:
            values.extend(db.execute("SELECT timestamp,payload FROM delta_capture ORDER BY timestamp").fetchall())
    values.sort(key=lambda item: int(item[0]))
    rows = [json.loads(zlib.decompress(payload).decode("utf-8")) for _, payload in values]
    if any(right["timestamp"] <= left["timestamp"] for left, right in zip(rows, rows[1:])):
        raise ValueError("daily capture shards overlap or are out of order")
    return rows


def capture_live_delta_update(path: str | Path, *, symbol: str, timestamp: int,
                              indicators: Mapping[str, Any], signal: Any,
                              position_state: Mapping[str, Any] | None = None) -> None:
    """Persist a tracker result and only the indicator keys it actually read."""
    timestamp = int(timestamp)
    if timestamp <= 0 or timestamp % 180:
        raise ValueError("timestamp must be a completed 3m epoch boundary")
    sig = {field: _json_value(getattr(signal, field, None)) for field in REQUIRED_SIGNAL_FIELDS}
    if any(value is None for value in sig.values()):
        raise ValueError("DeltaSignal lacks required captured output fields")
    row = {
        "schema": SCHEMA,
        "symbol": str(symbol).upper(),
        "timestamp": timestamp,
        "indicators": {str(key): _json_value(value) for key, value in indicators.items()},
        "delta_signal": sig,
        "position_state": ({str(key): _json_value(value) for key, value in position_state.items()}
                            if position_state is not None else None),
    }
    payload = zlib.compress(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"), 6)
    target = Path(path)
    spool_root = target.parent.parent
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect(target) as db:
        latest = db.execute("SELECT MAX(timestamp) FROM delta_capture").fetchone()[0]
        if latest is not None and timestamp < int(latest):
            raise ValueError("capture timestamp moved backwards")
        if latest == timestamp:
            return
        if _spool_bytes(spool_root) + len(payload) > MAX_SPOOL_BYTES:
            raise RuntimeError("capture paused: local spool reached 1 GB cap")
        db.execute("INSERT OR IGNORE INTO delta_capture(timestamp,payload) VALUES(?,?)", (timestamp, payload))
        db.commit()


def assess_capture(path: str | Path | list[str | Path] | tuple[str | Path, ...], *, symbol: str, min_days: int = 30,
                   bar_seconds: int = 180) -> dict[str, Any]:
    """Require monotonic, dense authentic 3m captures; never fill gaps."""
    target_symbol = str(symbol).upper()
    rows = _load_rows(path)
    if any(row.get("schema") != SCHEMA or row.get("symbol") != target_symbol for row in rows):
        raise ValueError("schema/symbol mismatch")
    timestamps = [int(row["timestamp"]) for row in rows]
    if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("capture timestamps must be strictly increasing")
    span = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
    expected = max(1, span // int(bar_seconds) + 1)
    observed = len(timestamps)
    complete = (span >= int(min_days) * 86400 and observed >= expected * 0.98
                and all(all(row["delta_signal"].get(k) is not None for k in REQUIRED_SIGNAL_FIELDS)
                        for row in rows))
    return {"symbol": target_symbol, "rows": observed, "span_seconds": span,
            "expected_bars": expected, "coverage": observed / expected,
            "ready_for_vectorization": bool(complete),
            "activation_note": "ready" if complete else "capture more authentic history; leave gate inactive"}


def export_capture_npz(source: str | Path | list[str | Path] | tuple[str | Path, ...], destination: str | Path, *, symbol: str,
                       min_days: int = 30) -> dict[str, Any]:
    """Write a separate 3m sidecar only after full-history validation."""
    target_symbol = str(symbol).upper()
    report = assess_capture(source, symbol=target_symbol, min_days=min_days)
    if not report["ready_for_vectorization"]:
        raise ValueError("capture lacks required 30d authentic history")
    rows = _load_rows(source)
    arrays = {
        "live_delta_timestamp": np.asarray([int(row["timestamp"]) for row in rows], dtype=np.int64),
        "live_delta_entry_long": np.asarray([bool(row["delta_signal"]["entry_long"]) for row in rows], dtype=np.bool_),
        "live_delta_entry_short": np.asarray([bool(row["delta_signal"]["entry_short"]) for row in rows], dtype=np.bool_),
        "live_delta_bull_speed": np.asarray([float(row["delta_signal"]["bull_speed"]) for row in rows], dtype=np.float64),
        "live_delta_bear_speed": np.asarray([float(row["delta_signal"]["bear_speed"]) for row in rows], dtype=np.float64),
        "live_delta_bull_tf_count": np.asarray([int(row["delta_signal"]["bull_tf_count"]) for row in rows], dtype=np.int16),
        "live_delta_bear_tf_count": np.asarray([int(row["delta_signal"]["bear_tf_count"]) for row in rows], dtype=np.int16),
        "live_delta_total_fields": np.asarray([int(row["delta_signal"]["total_fields"]) for row in rows], dtype=np.int16),
    }
    common_keys = set.intersection(*(set(row["indicators"]) for row in rows)) if rows else set()
    for key in sorted(common_keys):
        values = [row["indicators"][key] for row in rows]
        if any(value is None or isinstance(value, str) for value in values):
            continue
        try:
            arrays[f"live_delta_input__{key}"] = np.asarray(values, dtype=np.float64)
        except (TypeError, ValueError):
            continue
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.npz")
    np.savez_compressed(temp, **arrays)
    temp.replace(target)
    return {**report, "npz": str(target), "fields": sorted(arrays)}
