"""Append-only durable input journal and async timeline protocol for paper runs.

This module stores source frames and bar receipts only. It contains no trading
logic and does not calculate strategy or performance metrics.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, AsyncIterator, Mapping


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class FrameJournal:
    """SQLite journal for exact forward inputs, run identity, and receipts."""

    def __init__(self, path: str | Path, identity: Mapping[str, Any]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.identity = _canonical(dict(identity))
        required = ("settings_sha256", "code_sha256", "scenario_id")
        if any(not str(identity.get(key, "")).strip() for key in required):
            raise ValueError("journal identity requires nonempty settings_sha256, code_sha256, and scenario_id")
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS frames (
            sequence INTEGER PRIMARY KEY, closed_ts REAL UNIQUE NOT NULL,
            payload TEXT NOT NULL, input_hash TEXT NOT NULL,
            receipt_hash TEXT, receipt_json TEXT)""")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(frames)")}
        if "receipt_json" not in columns:
            self._db.execute("ALTER TABLE frames ADD COLUMN receipt_json TEXT")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
            if row is None:
                self._db.execute("INSERT INTO metadata(key,value) VALUES('identity',?)", (self.identity,))
            elif row[0] != self.identity:
                raise ValueError("journal identity mismatch (settings/code/scenario epoch changed)")
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def append_frame(self, closed_ts: float, payload: Any) -> int:
        """Durably append an input; exact duplicate retries return its sequence."""
        if isinstance(closed_ts, bool):
            raise ValueError("closed_ts must be a numeric timestamp, not bool")
        ts = float(closed_ts)
        if not (ts > 0 and math.isfinite(ts)):
            raise ValueError("closed_ts must be positive and finite")
        body = _canonical(payload)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            existing = self._db.execute(
                "SELECT sequence,input_hash FROM frames WHERE closed_ts=?", (ts,)
            ).fetchone()
            if existing:
                if existing[1] != digest:
                    raise ValueError(f"conflicting duplicate frame at {ts}")
                self._db.execute("COMMIT")
                return int(existing[0])
            previous = self._db.execute(
                "SELECT closed_ts FROM frames ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if previous and ts <= float(previous[0]):
                raise ValueError("frame timestamps must be strictly increasing")
            cursor = self._db.execute(
                "INSERT INTO frames(closed_ts,payload,input_hash) VALUES(?,?,?)",
                (ts, body, digest),
            )
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return int(cursor.lastrowid)

    def record_receipt(self, closed_ts: float, receipt: Any) -> str:
        """Commit an idempotent hash of the completed bar's engine receipt."""
        if isinstance(closed_ts, bool):
            raise ValueError("closed_ts must be a numeric timestamp, not bool")
        ts = float(closed_ts)
        if not math.isfinite(ts) or ts <= 0:
            raise ValueError("closed_ts must be positive and finite")
        receipt_json = _canonical(receipt)
        digest = hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT receipt_hash FROM frames WHERE closed_ts=?", (ts,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no journal input at {ts}")
            if row[0] is not None and row[0] != digest:
                raise ValueError(f"conflicting receipt at {ts}")
            if row[0] is None:
                self._db.execute(
                    "UPDATE frames SET receipt_hash=?,receipt_json=? WHERE closed_ts=?",
                    (digest, receipt_json, ts),
                )
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return digest

    def frames(self) -> list[dict[str, Any]]:
        """Replay original JSON inputs in durable sequence order."""
        return list(self.iter_frames())

    def iter_frames(self):
        """Lazily replay durable input rows without materializing the journal."""
        cursor = self._db.execute(
            "SELECT sequence,closed_ts,payload,input_hash,receipt_hash,receipt_json FROM frames ORDER BY sequence"
        )
        try:
            for seq, ts, body, digest, receipt_hash, receipt_json in cursor:
                yield {
                    "sequence": int(seq), "ts": float(ts), "payload": json.loads(body),
                    "input_hash": digest, "receipt_hash": receipt_hash,
                    "receipt": json.loads(receipt_json) if receipt_json is not None else None,
                }
        finally:
            cursor.close()


class _ForwardSteps:
    def __init__(self, timeline: list, session: Any):
        self.timeline = timeline
        self.session = session
        self._source = session.frames().__aiter__()
        self._step = 0
        self._pending_ts = None
        self._last_ts = None
        self._finished = False

    def __aiter__(self):
        return self

    async def aclose(self):
        close = getattr(self._source, "aclose", None)
        if close is not None:
            await close()

    async def __anext__(self):
        if self._finished:
            raise StopAsyncIteration
        # Advancing means the consumer completed the prior loop body. If the
        # body raised or broke, no subsequent __anext__ occurs, so no receipt.
        if self._pending_ts is not None:
            await self.session.after_step(self._pending_ts)
            self._pending_ts = None
        try:
            frame = await self._source.__anext__()
        except StopAsyncIteration:
            self._finished = True
            raise
        ts = float(frame["ts"])
        if isinstance(frame["ts"], bool) or not math.isfinite(ts) or ts <= 0:
            raise ValueError("forward frame timestamp must be positive and finite")
        if self._last_ts is not None and ts <= self._last_ts:
            raise ValueError("forward frame timestamps must be strictly increasing")
        await self.session.before_step(ts)
        self.timeline.append(ts)
        self._last_ts = ts
        self._pending_ts = ts
        result = (self._step, ts)
        self._step += 1
        return result

async def historical_or_forward_steps(
    timeline: list, forward_session: Any = None
) -> AsyncIterator[tuple[int, float]]:
    """Yield indexed timestamps; historical mode is a read-only enumerate."""
    if forward_session is None:
        for step, ts in enumerate(timeline):
            yield step, ts
        return
    timeline.clear()
    steps = _ForwardSteps(timeline, forward_session)
    try:
        async for item in steps:
            yield item
    finally:
        # No implicit finish: break/cancellation must not certify a complete run.
        await steps.aclose()
