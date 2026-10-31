#!/usr/bin/env python3
"""Run one isolated continuous paper scenario through the real scalar engine."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.paper_frame_stream import FrameJournal
from tools.paper_journal_session import JournalForwardSession
from tools.paper_snapshot_store import PaperSnapshotStore
from tools.paper_forward_window import resolve_source_window, validate_window

MAX_FRAMES = 1440
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_SYMBOL_BYTES = 256 * 1024
TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400, "W": 604800, "M": 2592000}


def _utc_epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = float(value)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("source timestamp must include a timezone")
        stamp = parsed.timestamp()
    else:
        raise ValueError("source timestamp missing or invalid")
    if stamp > 1e11:
        stamp /= 1000.0
    if not (stamp > 0 and stamp < float("inf")):
        raise ValueError("source timestamp must be positive and finite")
    return stamp


def _atomic_json(path, payload):
    path = Path(path)
    temp = path.with_name(path.name + ".pending")
    temp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def build_effective_settings(settings, candidate, nonvec_keys, sub15m_blocked, experiment_context, release_keys, mode, mask_fallbacks=None):
    release = set(release_keys)
    if release - set(candidate):
        raise ValueError("paper_mask_release may name candidate override keys only")
    if release and (not isinstance(experiment_context, dict) or not experiment_context):
        raise ValueError("paper_mask_release requires a shared nonempty experiment_context")
    release_nonvec = experiment_context.get("release_nonvectorizable", False)
    release_sub15m = experiment_context.get("release_sub15m", False)
    if not isinstance(release_nonvec, bool) or not isinstance(release_sub15m, bool):
        raise ValueError("experiment_context release flags must be booleans")
    blocked_nonvec = {}
    mask_fallbacks = mask_fallbacks or {}
    for key in nonvec_keys:
        current = settings.get(key, mask_fallbacks.get(key))
        if isinstance(current, bool):
            blocked_nonvec[key] = False
        elif isinstance(current, str) and key.endswith("_TF"):
            blocked_nonvec[key] = "None"
        elif isinstance(current, (list, tuple, set, frozenset)):
            blocked_nonvec[key] = type(current)()
        elif current is None and key.endswith("_ENABLED"):
            blocked_nonvec[key] = False
        elif current is None and key.endswith("_ACCOUNTS"):
            blocked_nonvec[key] = []
        else:
            raise ValueError(f"no known blocked baseline value for nonvectorizable knob {key}")
    blocked_guarded = {**blocked_nonvec, **sub15m_blocked}
    release_required = {key for key, value in candidate.items() if key in blocked_guarded and value != blocked_guarded[key]}
    if release_required - release:
        raise ValueError("candidate differs from blocked baseline without explicit paper_mask_release: " + ", ".join(sorted(release_required - release)))
    if release - (set(nonvec_keys) | set(sub15m_blocked)):
        raise ValueError("paper_mask_release includes a key outside the venue masks")
    mask_master_keys = {"PARITY_DISABLE_NON_VECTORIZABLE", "V12_PARITY_DISABLE_NON_VECTORIZABLE", "TRC_PARITY_DISABLE_NON_VECTORIZABLE", "LTF_SUB15M_TRADING_ENABLED", "TRADIER_LTF_SUB15M_TRADING_ENABLED"}
    if any(candidate.get(key) is True for key in mask_master_keys):
        raise ValueError("candidate may not override paper isolation mask masters")
    isolation_overrides = dict(blocked_nonvec)
    isolation_overrides.update(sub15m_blocked)
    paper_mask_release = {
        "PARITY_DISABLE_NON_VECTORIZABLE": not release_nonvec,
        "V12_PARITY_DISABLE_NON_VECTORIZABLE": not release_nonvec,
        "LTF_SUB15M_TRADING_ENABLED": release_sub15m,
    }
    if mode == "stocks":
        paper_mask_release["TRC_PARITY_DISABLE_NON_VECTORIZABLE"] = not release_nonvec
    effective = dict(settings)
    effective.update(isolation_overrides)
    effective.update(paper_mask_release)
    effective.update(candidate)
    return effective, isolation_overrides, paper_mask_release


class ContinuousJournalForwardSession(JournalForwardSession):
    """Replay the durable journal, then append distinct fresh source frames."""

    def __init__(self, *args, snapshot_path, receipt_path, symbol, expected_host, frame_timeframe, required_timeframes, poll_seconds, max_age_seconds, run_root, source_journal_path=None, experiment_window=None, max_frames=MAX_FRAMES, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot_path = Path(snapshot_path)
        self.receipt_path = Path(receipt_path)
        self.symbol = symbol
        self.expected_host = expected_host
        self.frame_timeframe = frame_timeframe
        self.required_timeframes = tuple(required_timeframes)
        self.poll_seconds = float(poll_seconds)
        self.max_age_seconds = float(max_age_seconds)
        self.run_root = Path(run_root)
        self.source_journal_path = Path(source_journal_path).resolve(strict=True) if source_journal_path else None
        self._source_db = None
        self._source_cursor = 0
        self.experiment_window = dict(experiment_window) if experiment_window else None
        self._window_complete = False
        self.coverage_gaps = []
        self.max_frames = int(max_frames)
        self.stop_requested = False
        self.wait_reason = "STARTING"
        self.observed_at_utc = None
        self.last_source_ts = None
        self._source_frame_count = 0
        last_payload = self.journal._db.execute("SELECT payload FROM frames ORDER BY sequence DESC LIMIT 1").fetchone()
        if last_payload:
            try:
                last = json.loads(last_payload[0])
                self._source_cursor = int(last.get(self.symbol, {}).get("observation", {}).get("source_sequence", 0))
            except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
                self._source_cursor = 0

    def _status(self, status, reason=None):
        payload = {
            "status": status,
            "reason": reason,
            "run_id": self._identity.get("run_id"),
            "epoch_id": self._identity.get("epoch_id"),
            "scenario_id": self._identity.get("scenario_id"),
            "sym_side": self._identity.get("sym_side"),
            "host": self._identity.get("host"),
            "pid": os.getpid(),
            "observed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "source_observed_at_utc": self.observed_at_utc,
            "last_source_timestamp_epoch_s": self.last_source_ts,
            "source_time_semantics": "native timeframe source timestamp; observed_at_utc is a separate local observation clock; no closed-bar claim",
            "journal_frames": self.journal._db.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
            "source_journal_cursor": self._source_cursor,
        }
        _atomic_json(self.run_root / "worker.status.json", payload)

    def _read_source_frame(self):
        from tools.paper_native_feed import validate_snapshot_bytes

        try:
            before = self.snapshot_path.stat()
            if before.st_size <= 0 or before.st_size > MAX_INPUT_BYTES:
                return None, "SNAPSHOT_SIZE_INVALID"
            raw = self.snapshot_path.read_bytes()
            after = self.snapshot_path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(raw) != before.st_size:
                return None, "SNAPSHOT_CHANGING"
            if self.receipt_path.stat().st_size > 65536:
                return None, "RECEIPT_TOO_LARGE"
            receipt_raw = self.receipt_path.read_bytes()
            receipt = json.loads(receipt_raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            digest = hashlib.sha256(raw).hexdigest()
            if receipt.get("source_sha256") != digest:
                return None, "SNAPSHOT_RECEIPT_MISMATCH"
            expected_venue = "crypto" if self.mode == "crypto" else "stocks"
            if receipt.get("venue") != expected_venue:
                return None, "FEED_VENUE_MISMATCH"
            if receipt.get("host") != self.expected_host:
                return None, "FEED_HOST_MISMATCH"
            snapshot = validate_snapshot_bytes(raw)
            indicators = snapshot.get(self.symbol)
            if not isinstance(indicators, dict):
                return None, "SYMBOL_MISSING"
            if len(json.dumps(indicators, separators=(",", ":"), allow_nan=False).encode()) > MAX_SYMBOL_BYTES:
                return None, "SYMBOL_RECORD_TOO_LARGE"
            observed = receipt.get("captured_at_utc")
            self.observed_at_utc = observed
            observed_epoch = _utc_epoch(observed)
            now = time.time()
            if observed_epoch > now + 5:
                return None, "OBSERVATION_CLOCK_IN_FUTURE"
            for timeframe in self.required_timeframes:
                field = f"timestamp_{timeframe}"
                try:
                    source_ts = _utc_epoch(indicators.get(field))
                except (TypeError, ValueError, OverflowError):
                    return None, f"WAITING_MISSING_TF_{timeframe}"
                age = now - source_ts
                max_age = self.max_age_seconds if timeframe == self.frame_timeframe else max(self.max_age_seconds, 2 * TIMEFRAME_SECONDS[timeframe])
                if age < -5:
                    return None, f"WAITING_FUTURE_TF_{timeframe}"
                if age > max_age:
                    return None, f"WAITING_STALE_TF_{timeframe}"
            try:
                source_ts = _utc_epoch(indicators.get(f"timestamp_{self.frame_timeframe}"))
            except (TypeError, ValueError, OverflowError):
                return None, f"WAITING_MISSING_TF_{self.frame_timeframe}"
            if source_ts > observed_epoch + 5:
                return None, f"WAITING_FUTURE_TF_{self.frame_timeframe}"
            frame = {
                "ts": source_ts,
                "indicators": indicators,
                "observation": {
                    "observed_at_utc": observed,
                    "source_sha256": digest,
                    "frame_timeframe": self.frame_timeframe,
                    "source_timestamp": indicators.get(f"timestamp_{self.frame_timeframe}"),
                    "source_time_semantics": "native source timestamp; closed-bar status unverified",
                },
            }
            self.observed_at_utc = observed
            return frame, None
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None, "FEED_UNAVAILABLE_OR_INVALID"

    def _open_source_journal(self):
        if self._source_db is None:
            if self.source_journal_path is None:
                return None
            self._source_db = sqlite3.connect(self.source_journal_path.as_uri() + "?mode=ro", uri=True, timeout=5)
            self._source_db.execute("PRAGMA query_only=ON")
        return self._source_db

    def _read_source_journal_frame(self, cursor=None):
        database = self._open_source_journal()
        if database is None:
            return None, "SOURCE_JOURNAL_DISABLED"
        metadata = dict(database.execute("SELECT key,value FROM journal_meta"))
        if int(metadata.get("retention_floor_sequence", "0")) > 0:
            raise RuntimeError("ARCHIVE_GAP: native source journal retention floor has advanced")
        status = metadata.get("status", "READY")
        if status == "ARCHIVE_GAP":
            raise RuntimeError("ARCHIVE_GAP: native source journal reports a gap")
        sequence_after = self._source_cursor if cursor is None else int(cursor)
        row = database.execute(
            "SELECT sequence,observed_ts,source_ts,source_sha256,input_hash,payload_encoding,payload_blob FROM frames WHERE sequence>? AND host=? AND venue=? AND symbol=? AND timeframe=? AND (? IS NULL OR source_ts>=?) AND (? IS NULL OR source_ts<=?) ORDER BY sequence LIMIT 1",
            (sequence_after, self.expected_host, "crypto" if self.mode == "crypto" else "stocks", self.symbol, self.frame_timeframe,
             self.experiment_window.get("start_source_ts") if self.experiment_window else None,
             self.experiment_window.get("start_source_ts") if self.experiment_window else None,
             self.experiment_window.get("end_source_ts") if self.experiment_window else None,
             self.experiment_window.get("end_source_ts") if self.experiment_window else None),
        ).fetchone()
        if row is None:
            if self.experiment_window:
                latest = database.execute("SELECT MAX(source_ts) FROM frames WHERE host=? AND venue=? AND symbol=? AND timeframe=?",
                    (self.expected_host, "crypto" if self.mode == "crypto" else "stocks", self.symbol, self.frame_timeframe)).fetchone()[0]
                if latest is not None and float(latest) >= float(self.experiment_window["end_source_ts"]):
                    self._window_complete = True
                    return None, "SOURCE_WINDOW_COMPLETE"
            return None, "WAITING_FOR_ARCHIVE_TAIL" if status not in {"ARCHIVE_FULL", "DISK_RESERVE_BLOCKED"} else status
        sequence, observed_ts, source_ts, source_hash, input_hash, encoding, blob = row
        gap = None
        if self.experiment_window and self._source_cursor == 0 and float(source_ts) > float(self.experiment_window["start_source_ts"]):
            gap = {"kind": "WINDOW_START_COVERAGE_GAP", "requested_start_source_ts": float(self.experiment_window["start_source_ts"]), "first_source_ts": float(source_ts), "missing_seconds": float(source_ts) - float(self.experiment_window["start_source_ts"])}
        if sequence <= int(metadata.get("retention_floor_sequence", "0")):
            raise RuntimeError("ARCHIVE_GAP: required native source sequence was pruned")
        if encoding != "zlib":
            raise RuntimeError("SOURCE_JOURNAL_ENCODING_UNSUPPORTED")
        try:
            raw = zlib.decompress(blob, max_length=MAX_SYMBOL_BYTES + 1)
        except TypeError:
            decompressor = zlib.decompressobj()
            raw = decompressor.decompress(blob, MAX_SYMBOL_BYTES + 1)
            if decompressor.unconsumed_tail or not decompressor.eof:
                raise RuntimeError("SOURCE_JOURNAL_PAYLOAD_TOO_LARGE_OR_TRUNCATED")
        except zlib.error as exc:
            raise RuntimeError("SOURCE_JOURNAL_PAYLOAD_CORRUPT") from exc
        if len(raw) > MAX_SYMBOL_BYTES or hashlib.sha256(raw).hexdigest() != input_hash:
            raise RuntimeError("SOURCE_JOURNAL_INPUT_HASH_MISMATCH")
        indicators = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(indicators, dict):
            raise RuntimeError("SOURCE_JOURNAL_INDICATORS_MALFORMED")
        observed_ts = float(observed_ts)
        source_ts = float(source_ts)
        if source_ts > observed_ts + 5 or time.time() < observed_ts - 5:
            raise RuntimeError("SOURCE_JOURNAL_TIMESTAMP_INVALID")
        conflicts = database.execute("SELECT reason FROM source_conflicts WHERE host=? AND venue=? AND symbol=? AND timeframe=? AND source_ts=? LIMIT 1",
            (self.expected_host, "crypto" if self.mode == "crypto" else "stocks", self.symbol, self.frame_timeframe, source_ts)).fetchone()
        if conflicts:
            self._window_complete = bool(self.experiment_window)
            return None, "DATA_GAP_SOURCE_CONFLICT:" + str(conflicts[0])
        try:
            indicator_source_ts = _utc_epoch(indicators.get(f"timestamp_{self.frame_timeframe}"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("SOURCE_JOURNAL_FRAME_TIMESTAMP_MISSING") from exc
        if abs(indicator_source_ts - source_ts) > 0.001:
            raise RuntimeError("SOURCE_JOURNAL_FRAME_TIMESTAMP_MISMATCH")
        for timeframe in self.required_timeframes:
            try:
                timeframe_ts = _utc_epoch(indicators.get(f"timestamp_{timeframe}"))
            except (TypeError, ValueError, OverflowError):
                return None, f"DATA_GAP_MISSING_TF_{timeframe}" if self.experiment_window else (None, f"WAITING_MISSING_TF_{timeframe}")
            if timeframe_ts > observed_ts + 5 or observed_ts - timeframe_ts > max(self.max_age_seconds, 2 * TIMEFRAME_SECONDS[timeframe]):
                return None, f"DATA_GAP_STALE_OR_FUTURE_TF_{timeframe}" if self.experiment_window else (None, f"WAITING_STALE_TF_{timeframe}")
        frame = {
            "ts": source_ts,
            "indicators": indicators,
            "source_sequence": int(sequence),
            "observation": {
                "observed_at_utc": datetime.fromtimestamp(observed_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "source_sha256": source_hash,
                "input_hash": input_hash,
                "source_sequence": int(sequence),
                "frame_timeframe": self.frame_timeframe,
                "source_timestamp": indicators.get(f"timestamp_{self.frame_timeframe}"),
                "source_time_semantics": "native source timestamp; closed-bar status unverified",
            },
        }
        if self.experiment_window and self._source_cursor == 0 and float(source_ts) > float(self.experiment_window["start_source_ts"]):
            frame["observation"]["window_start_coverage_gap"] = gap
            self.coverage_gaps.append(gap)
        return frame, None
    async def prime_first_frame(self):
        """Wait for one fresh input before engine initialization/assert_ready."""
        while True:
            if self.stop_requested:
                raise RuntimeError("stop requested before a fresh first source frame")
            frame, reason = self._read_source_journal_frame() if self.source_journal_path else self._read_source_frame()
            if frame is not None:
                self.journal.append_frame(frame["ts"], {self.symbol: {"indicators": frame["indicators"], "observation": frame["observation"]}})
                self._source_cursor = int(frame.get("source_sequence", 0))
                self.last_source_ts = frame["ts"]
                self._source_frame_count += 1
                self._status("RUNNING", "FIRST_FRESH_SOURCE_FRAME")
                return frame
            self.wait_reason = reason
            if reason.startswith("DATA_GAP_"):
                self._status("DATA_GAP", reason)
                return None
            if reason == "SOURCE_WINDOW_COMPLETE":
                self._status("DATA_GAP", "SOURCE_WINDOW_EMPTY")
                return None
            self._status("WAITING_FRESH_INPUT", reason)
            await asyncio.sleep(self.poll_seconds)

    async def frames(self):
        if self._frames is not None:
            raise RuntimeError("journal frames may only be consumed once")
        self._frames = self.journal.iter_frames()
        try:
            for frame in self._frames:
                if self._last_ts is not None and frame["ts"] <= self._last_ts:
                    raise RuntimeError("journal replay timestamps are not strictly increasing")
                self._current = frame
                self._last_ts = frame["ts"]
                yield frame
            self._exhausted = True
        finally:
            self._frames.close()
        while not self.stop_requested:
            if self._source_frame_count >= self.max_frames:
                self.wait_reason = "RESOURCE_LIMIT_MAX_FRAMES"
                self._status("RESOURCE_LIMIT_STOPPING", self.wait_reason)
                break
            frame, reason = self._read_source_journal_frame() if self.source_journal_path else self._read_source_frame()
            if frame is None:
                if reason.startswith("DATA_GAP_"):
                    self.wait_reason = reason
                    self._status("DATA_GAP", reason)
                    break
                if reason == "SOURCE_WINDOW_COMPLETE":
                    self.wait_reason = reason
                    self._status("WINDOW_COMPLETE", reason)
                    break
                self.wait_reason = reason
                self._status("WAITING_FRESH_INPUT", reason)
                await asyncio.sleep(self.poll_seconds)
                continue
            source_ts = float(frame["ts"])
            existing = self.journal._db.execute("SELECT input_hash FROM frames WHERE closed_ts=?", (source_ts,)).fetchone()
            payload = {self.symbol: {"indicators": frame["indicators"], "observation": frame["observation"]}}
            if existing:
                existing_payload = json.loads(self.journal._db.execute("SELECT payload FROM frames WHERE closed_ts=?", (source_ts,)).fetchone()[0])
                if existing_payload.get(self.symbol, {}).get("indicators") != frame["indicators"]:
                    raise RuntimeError(f"conflicting native source frame changed at {source_ts}")
                self._source_cursor = max(self._source_cursor, int(frame.get("source_sequence", 0)))
                await asyncio.sleep(self.poll_seconds)
                continue
            if self._last_ts is not None and source_ts <= self._last_ts:
                self._status("WAITING_FRESH_INPUT", "SOURCE_TIMESTAMP_NOT_ADVANCED")
                await asyncio.sleep(self.poll_seconds)
                continue
            self.journal.append_frame(source_ts, payload)
            if frame.get("source_sequence") is not None:
                self._source_cursor = int(frame["source_sequence"])
            row = {"sequence": self._source_frame_count + 1, "ts": source_ts, "payload": payload}
            self._current = row
            self._last_ts = source_ts
            self.last_source_ts = source_ts
            self._source_frame_count += 1
            self._status("RUNNING", "NEW_SOURCE_FRAME")
            yield row
        self._exhausted = True

    async def before_step(self, ts):
        if self._current is None or float(ts) != float(self._current["ts"]):
            raise RuntimeError("step timestamp does not match current journal input")
        if self._pending_ts is not None:
            raise RuntimeError("previous journal bar has not committed")
        payload = self._current["payload"]
        await self.apply_frame(payload, self.stores)
        for symbol, store in self.stores.items():
            self._validate_store_timestamps(symbol, store, float(ts), required_ts=float(ts))
        self._pending_ts = float(ts)

    async def after_step(self, ts):
        await super().after_step(ts)
        self._snapshot_sqlite()
        self.last_source_ts = float(ts)
        self._status("RUNNING", "RECEIPT_COMMITTED")

    def _snapshot_sqlite(self):
        target = self.run_root / "receipts.snapshot.db"
        pending = self.run_root / "receipts.snapshot.db.pending"
        if pending.exists():
            pending.unlink()
        destination = sqlite3.connect(pending)
        try:
            self.journal._db.backup(destination)
        finally:
            destination.close()
        os.replace(pending, target)

    async def finish(self):
        await super().finish()
        if self._source_db is not None:
            self._source_db.close()
        status = "DATA_GAP" if self.wait_reason.startswith("DATA_GAP_") else ("STOPPED_BY_REQUEST" if self.stop_requested else ("STOPPED_RESOURCE_LIMIT" if self.wait_reason == "RESOURCE_LIMIT_MAX_FRAMES" else "COMPLETED"))
        self._status(status, self.wait_reason)

CHILD = r'''
import asyncio, hashlib, json, os, sys
import signal
from datetime import datetime, timezone
from pathlib import Path
from tools.paper_worker_isolation import install, require_installed
from tools.paper_forward_worker import ContinuousJournalForwardSession, _atomic_json, build_effective_settings, validate_runtime_root
from tools.paper_frame_stream import FrameJournal
from tools.paper_snapshot_store import PaperSnapshotStore

root = Path(sys.argv[1]).resolve()
request = json.loads((root / "request.json").read_text(encoding="utf-8"))
source_journal = request.get("source_journal") or None
install(root, readonly_sqlite_paths=[source_journal] if source_journal else [])
def _persist_uncaught(exc_type, exc_value, exc_traceback):
    import traceback
    body = {"error_type": getattr(exc_type, "__name__", str(exc_type)), "error": str(exc_value), "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))}
    _atomic_json(root / "child_error.json", body)
sys.excepthook = _persist_uncaught
symbol = request["symbol"]
mode = request["mode"]
manifest = request["manifest"]
settings = request["full_settings"]
candidate = request["candidate_overrides"]
engine_root = Path(request["engine_root"]).resolve()
repo_root = Path(request["repo_root"]).resolve()
if engine_root.is_symlink() or not engine_root.is_dir() or not (engine_root / "backtest_v12_engine.py").is_file():
    raise RuntimeError("private engine_root must contain backtest_v12_engine.py")
if request.get("diagnostic_workspace_engine"):
    worker_code_root = Path(request["worker_code_root"]).resolve()
    if engine_root != worker_code_root or manifest.get("context", {}).get("evidence_label") != "DIAGNOSTIC_REPLAY" or manifest.get("context", {}).get("performance_claim") is not False:
        raise RuntimeError("workspace engine exception is limited to explicitly diagnostic no-performance runs")
    engine_runtime_manifest = None
else:
    engine_runtime_manifest = validate_runtime_root(engine_root)
sys.path.insert(0, str(engine_root))
import tools as _tools_pkg
_tools_pkg.__path__ = [str(engine_root / "tools"), str(repo_root / "tools")]
requested_effective = dict(settings)
requested_effective.update(candidate)

import backtest_v12_engine as engine
import config as crypto_config
import config_tradier as stock_config
import ez_manage
import tradier_manage

release = set(manifest.get("paper_mask_release", []))
if mode == "crypto":
    nonvec_keys = set(ez_manage._NON_VEC_KNOBS_EZ)
    sub15m_blocked = dict(ez_manage.SUB15M_BLOCKED_SWITCHES)
else:
    nonvec_keys = set(getattr(stock_config.TradierConfig, "PARITY_NON_VECTORIZABLE_SWITCHES", ()))
    sub15m_blocked = dict(tradier_manage.SUB15M_BLOCKED_SWITCHES)
experiment_context = manifest.get("experiment_context", {})
mask_fallbacks = {}
for key in nonvec_keys:
    fallback = getattr(crypto_config.Config, key, None) if mode == "crypto" else getattr(stock_config.TradierConfig, key, None)
    if fallback is not None:
        mask_fallbacks[key] = fallback
try:
    effective, isolation_overrides, paper_mask_release = build_effective_settings(
        settings, candidate, nonvec_keys, sub15m_blocked, experiment_context,
        release, mode, mask_fallbacks,
    )
except ValueError as exc:
    raise RuntimeError(str(exc)) from exc

code_path = Path(engine.__file__).resolve()
if not code_path.is_relative_to(engine_root):
    raise RuntimeError("imported engine did not come from private engine_root")
code_hash = hashlib.sha256(code_path.read_bytes()).hexdigest()
settings_hash = hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
symside = request["sym_side"]
identity = {
    "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
    "scenario_id": manifest["scenario_id"], "sym_side": symside,
    "settings_sha256": settings_hash, "code_sha256": code_hash,
    "plan_code_hash": manifest["plan_code_hash"],
    "base_settings_sha256": hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
    "requested_merged_settings_sha256": hashlib.sha256(json.dumps(requested_effective, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
    "candidate_sha256": hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
    "host": manifest["host"],
}
started_at = manifest.get("started_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
base_settings_path = root / "base_settings.json"
effective_settings_path = root / "effective_settings.json"
for path, value in ((base_settings_path, settings), (effective_settings_path, effective)):
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != body:
        raise RuntimeError(f"immutable settings snapshot differs: {path.name}")
    if not path.exists():
        with path.open("x", encoding="utf-8") as settings_file:
            settings_file.write(body)
result_manifest = {
    "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
    "scenario_id": manifest["scenario_id"], "sym_side": symside,
    "host": manifest["host"], "settings_sha256": settings_hash,
    "parent_settings_sha256": identity["base_settings_sha256"],
    "code_sha256": code_hash, "code_file_hashes": {"backtest_v12_engine.py": code_hash},
    "plan_code_hash": manifest["plan_code_hash"], "started_at": started_at,
    "candidate_overrides": candidate, "isolation_overrides": isolation_overrides,
    "paper_mask_release": paper_mask_release, "context": {**manifest.get("context", {}), "experiment_context": manifest.get("experiment_context", {})},
    "candidate_getter_resolution": {}, "worker_isolation": require_installed(root),
    "engine_root": str(engine_root),
    "engine_runtime_sha256": engine_runtime_manifest.get("runtime_sha256") if engine_runtime_manifest else None,
    "engine_runtime_kind": "IMMUTABLE_BUNDLE" if engine_runtime_manifest else "WORKSPACE_DIAGNOSTIC",
    "promotion_eligible": False,
    "execution_mode": "V12_REAL_EXECUTE=1; real manager execute_now path with process-local broker/network I/O denial",
    "source_time_semantics": "native source timeframe timestamp; observed_at_utc is separate; closed-bar status unverified",
    "experiment_window": manifest.get("experiment_window"),
    "experiment_window_resolution": manifest.get("experiment_window_resolution"),
    "source_journal": request.get("source_journal") or None,
    "effective_settings_file": effective_settings_path.name,
    "base_settings_file": base_settings_path.name,
    "finished": False, "status": "RUNNING",
}
result_manifest_path = root / "worker.result_manifest.json"
if result_manifest_path.exists():
    existing_result_manifest = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    if any(existing_result_manifest.get(key) != result_manifest.get(key) for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "settings_sha256", "code_sha256", "candidate_overrides")):
        raise RuntimeError("existing worker result manifest does not match current immutable run")
    result_manifest["started_at"] = existing_result_manifest.get("started_at", started_at)
_atomic_json(result_manifest_path, result_manifest)
spec_path = root / "spec.json"
spec_body = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
if spec_path.exists():
    if spec_path.read_text(encoding="utf-8") != spec_body:
        raise RuntimeError("immutable worker spec differs from existing run directory")
else:
    with spec_path.open("x", encoding="utf-8") as spec_file:
        spec_file.write(spec_body)

override_snapshot = engine._run_one_apply_overrides(effective, mode)
precedence_state = engine._run_one_set_precedence_on(symside, effective, mode)
if precedence_state.get("error"):
    raise RuntimeError("per-symbol settings precedence could not be installed: " + precedence_state["error"])

getter = ez_manage._psym_get if mode == "crypto" else tradier_manage._cfg
getter_receipts = {}
for key, requested_value in candidate.items():
    default = object()
    if mode == "crypto":
        resolved_value = getter(symbol, request["side"], key, default)
    else:
        resolved_value = getter(key, default, request.get("account", "trb"), symbol, request["side"])
    if resolved_value is default or resolved_value != requested_value:
        raise RuntimeError(f"effective per-symbol getter mismatch for {key}: requested={requested_value!r} resolved={resolved_value!r}")
    getter_receipts[key] = {"requested": requested_value, "resolved": resolved_value, "matches": True, "resolver": "ez_manage._psym_get" if mode == "crypto" else "tradier_manage._cfg"}
result_manifest["candidate_getter_resolution"] = getter_receipts
_atomic_json(result_manifest_path, result_manifest)
journal = FrameJournal(root / "receipts.sqlite3", identity)
first_row = journal._db.execute("SELECT closed_ts,payload FROM frames ORDER BY sequence LIMIT 1").fetchone()
if first_row:
    first_ts = float(first_row[0])
    first_payload = json.loads(first_row[1])[symbol]["indicators"]
    first_frame = {"ts": first_ts, "indicators": first_payload}
else:
    first_frame = None
stores = {symbol: PaperSnapshotStore([first_frame])} if first_frame else {}

async def apply_frame(payload, stores):
    for frame_symbol, packed in payload.items():
        if frame_symbol != symbol or not isinstance(packed, dict) or "indicators" not in packed:
            raise RuntimeError("journal frame has an unexpected symbol or malformed indicators")
        stores[frame_symbol].append(session._current["ts"], packed["indicators"])

session = ContinuousJournalForwardSession(
    journal, stores, apply_frame, code_path=code_path, mode=mode,
    snapshot_path=request["snapshot"], receipt_path=request["receipt"],
    source_journal_path=request.get("source_journal") or None,
    experiment_window=manifest.get("experiment_window"),
    symbol=symbol, expected_host=manifest["host"], frame_timeframe=manifest.get("frame_timeframe", "1m" if mode == "crypto" else "5m"),
    required_timeframes=manifest.get("required_timeframes", [manifest.get("frame_timeframe", "1m" if mode == "crypto" else "5m")]),
    poll_seconds=request["poll_seconds"], max_age_seconds=request["max_age_seconds"],
    run_root=root, max_frames=manifest.get("max_frames", 1440),
)
if not first_frame:
    first_frame = asyncio.run(session.prime_first_frame())
    if first_frame is None:
        session._snapshot_sqlite()
        result = {
            "status": "DATA_GAP", "evidence_kind": "independent_forward",
            "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
            "scenario_id": manifest["scenario_id"], "sym_side": symside,
            "mode": mode, "reason": session.wait_reason,
            "settings_sha256": settings_hash, "code_sha256": code_hash,
            "source_time_semantics": "native source timeframe timestamp; closed-bar status unverified",
            "promotion_eligible": False,
        }
        _atomic_json(root / "result.json", result)
        result_manifest.update(status="DATA_GAP", finished=True, ended_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), reason=session.wait_reason, frame_count=0, committed_receipts=0)
        _atomic_json(result_manifest_path, result_manifest)
        journal.close()
        engine._run_one_restore_overrides(override_snapshot, mode)
        engine._run_one_set_precedence_off(precedence_state)
        raise SystemExit(0)
    stores[symbol] = PaperSnapshotStore([{"ts": first_frame["ts"], "indicators": first_frame["indicators"]}])
    session.stores = stores
else:
    session._source_frame_count = journal._db.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    session.last_source_ts = float(journal._db.execute("SELECT closed_ts FROM frames ORDER BY sequence DESC LIMIT 1").fetchone()[0])
result_manifest["coverage_gaps"] = session.coverage_gaps
_atomic_json(result_manifest_path, result_manifest)
session._snapshot_sqlite()
result_manifest["status"] = "RUNNING"
_atomic_json(result_manifest_path, result_manifest)

def request_stop(_signum, _frame):
    session.stop_requested = True
signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)

start_date = datetime.fromtimestamp(float(first_frame["ts"]), timezone.utc).strftime("%Y-%m-%d")
try:
    if mode == "crypto":
        trades = asyncio.run(engine.run_simulation("crypto", request.get("account", "ang"), start_date, float(request.get("capital", 10000)), stores, "1m", forward_session=session))
    else:
        trades = asyncio.run(engine.run_simulation_tradier(request.get("account", "trb"), start_date, float(request.get("capital", 10000)), stores, "5m", forward_session=session))
    engine._compute_trade_pnl(trades)
    source_hashes = set()
    source_input_hashes = set()
    def _is_accounting_fill(event):
        event_type = str(event.get("type", "")).lower()
        recording_client_fill = (
            isinstance(event.get("params"), dict) and bool(event.get("position_key"))
            and event.get("position_before") is not None and event.get("position_after") is not None
            and float(event.get("price", 0) or 0) > 0 and float(event.get("quantity", 0) or 0) > 0
        )
        return str(event.get("status", "")).upper() == "FILLED" and not event.get("decision_only") and (event_type in {"eta", "webhook"} or recording_client_fill)
    receipt_events = {}
    for frame_row in journal.iter_frames():
        source_input_hashes.add(frame_row["input_hash"])
        observation = frame_row["payload"].get(symbol, {}).get("observation", {})
        if observation.get("source_sha256"):
            source_hashes.add(observation["source_sha256"])
        receipt = frame_row.get("receipt") or {}
        for event_index, source_event in enumerate(receipt.get("events", [])):
            if not _is_accounting_fill(source_event):
                continue
            match_fields = ("type", "status", "position_key", "action", "side", "quantity", "price", "timestamp")
            match_key = json.dumps({key: source_event.get(key) for key in match_fields}, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
            receipt_events.setdefault(match_key, []).append({
                "source_frame_sequence": frame_row["sequence"], "source_frame_input_hash": frame_row["input_hash"],
                "source_event_index": event_index, "source_event": source_event,
                "source_event_sha256": hashlib.sha256(json.dumps(source_event, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            })
    accounting_trades = []
    for trade in trades:
        event_type = str(trade.get("type", "")).lower()
        if not _is_accounting_fill(trade):
            continue
        match_fields = ("type", "status", "position_key", "action", "side", "quantity", "price", "timestamp")
        match_key = json.dumps({key: trade.get(key) for key in match_fields}, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
        source_reference = receipt_events.get(match_key, []).pop(0) if receipt_events.get(match_key) else None
        if source_reference is None:
            raise RuntimeError("accounting fill cannot be matched to an immutable journal receipt event")
        gross = trade.get("pnl_dollars_gross")
        net = trade.get("pnl_dollars")
        accounting_trades.append({
            **source_reference,
            "event_type": event_type or "recording_client", "status": "FILLED", "timestamp": trade.get("timestamp"),
            "position_key": trade.get("position_key"), "sym_side": trade.get("sym_side"),
            "symbol": trade.get("symbol"), "action": trade.get("action"), "reason": trade.get("reason"),
            "position_side": trade.get("position_side"), "side": trade.get("side"),
            "price": trade.get("price"), "quantity": trade.get("quantity"),
            "position_before": trade.get("position_before"), "position_after": trade.get("position_after"),
            "gross_pnl_dollars": gross, "net_pnl_dollars": net,
            "gross_pnl_pct": trade.get("pnl_pct_gross"), "net_pnl_pct": trade.get("pnl_pct"),
            "round_trip_cost_pct_simulated": trade.get("round_trip_cost_pct"),
            "fees_dollars_simulated": float(gross) - float(net) if gross is not None and net is not None else None,
            "commission_dollars_open_simulated": trade.get("commission_dollars_open"),
            "commission_dollars_close_simulated": trade.get("commission_dollars_close"),
            "pnl_currency": "USD_SIMULATED",
        })
    accounting = {
        "schema_version": 1, "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
        "scenario_id": manifest["scenario_id"], "sym_side": symside,
        "settings_sha256": settings_hash, "code_sha256": code_hash,
        "plan_code_hash": manifest["plan_code_hash"],
        "source_sha256s": sorted(source_hashes), "source_input_sha256s": sorted(source_input_hashes),
        "event_source": "backtest_v12_engine real manager execution fills (FILLED eta/webhook only)",
        "cost_model": "engine_simulated_round_trip_cost", "costs_are_exchange_actual": False,
        "trades": accounting_trades,
    }
    accounting_path = root / "accounting.json"
    accounting_body = json.dumps(accounting, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if accounting_path.exists():
        if accounting_path.read_text(encoding="utf-8") != accounting_body:
            raise RuntimeError("immutable accounting sidecar differs from existing run")
    else:
        with accounting_path.open("x", encoding="utf-8") as accounting_file:
            accounting_file.write(accounting_body)
            accounting_file.flush()
            os.fsync(accounting_file.fileno())
    accounting_sha256 = hashlib.sha256(accounting_body.encode("utf-8")).hexdigest()
    receipts = journal._db.execute("SELECT COUNT(*),SUM(receipt_hash IS NOT NULL) FROM frames").fetchone()
    if int(receipts[0]) != int(receipts[1] or 0):
        raise RuntimeError("engine returned without committing every source-frame receipt")
    result = {
        "status": "DATA_GAP" if session.wait_reason.startswith("DATA_GAP_") else ("STOPPED_BY_REQUEST" if session.stop_requested else ("STOPPED_RESOURCE_LIMIT" if session.wait_reason == "RESOURCE_LIMIT_MAX_FRAMES" else "COMPLETED")),
        "evidence_kind": "independent_forward", "run_id": manifest["run_id"],
        "epoch_id": manifest["epoch_id"], "scenario_id": manifest["scenario_id"],
        "sym_side": symside, "mode": mode, "frame_count": int(receipts[0]),
        "committed_receipts": int(receipts[1] or 0), "settings_sha256": settings_hash,
        "code_sha256": code_hash, "plan_code_hash": manifest["plan_code_hash"],
        "candidate_overrides": candidate, "override_issues": list(engine._RUN_ONE_OVERRIDE_ISSUES),
        "store_row_existed": bool(precedence_state.get("store_row_existed")),
        "source_time_semantics": "native source timeframe timestamp; closed-bar status unverified",
        "trades": trades, "receipt_db": str(root / "receipts.sqlite3"),
        "accounting_file": accounting_path.name, "accounting_sha256": accounting_sha256,
        "promotion_eligible": False,
    }
    _atomic_json(root / "result.json", result)
    result_manifest.update(status=result["status"], finished=True, ended_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), frame_count=result["frame_count"], committed_receipts=result["committed_receipts"], accounting_file=accounting_path.name, accounting_sha256=accounting_sha256)
    _atomic_json(result_manifest_path, result_manifest)
except BaseException as exc:
    import traceback
    _atomic_json(root / "child_error.json", {"error_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()})
    raise
finally:
    journal.close()
    engine._run_one_restore_overrides(override_snapshot, mode)
    engine._run_one_set_precedence_off(precedence_state)
'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_runtime_root(runtime_root):
    runtime_root = Path(runtime_root).resolve(strict=True)
    manifest_path = runtime_root / "runtime_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("engine_root must be an immutable content-addressed paper runtime")
    manifest = _read_json(manifest_path, 16 * 1024 * 1024)
    if manifest.get("runtime_sha256") != runtime_root.name:
        raise ValueError("runtime directory name does not match runtime manifest hash")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise ValueError("runtime manifest has no source file records")
    declared_map = manifest.get("file_sha256")
    if declared_map != {row.get("path"): row.get("sha256") for row in rows}:
        raise ValueError("runtime file_sha256 map disagrees with file records")
    digest = hashlib.sha256()
    seen = set()
    for row in rows:
        relative = Path(str(row.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not row.get("sha256"):
            raise ValueError("runtime manifest contains an invalid relative path")
        name = relative.as_posix()
        if name in seen:
            raise ValueError("runtime manifest contains duplicate paths")
        seen.add(name)
        path = runtime_root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o222:
            raise ValueError(f"runtime file is missing, linked, or writable: {name}")
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if file_digest != row["sha256"] or path.stat().st_size != int(row.get("bytes", -1)):
            raise ValueError(f"runtime file hash or size mismatch: {name}")
        for parent in path.parents:
            if parent == runtime_root.parent:
                break
            if parent.stat().st_mode & 0o222:
                raise ValueError(f"runtime directory is writable: {parent.relative_to(runtime_root)}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_digest))
    if digest.hexdigest() != manifest["runtime_sha256"]:
        raise ValueError("runtime source manifest does not match content-addressed hash")
    if runtime_root.stat().st_mode & 0o222:
        raise ValueError("runtime root is writable")
    engine_path = runtime_root / manifest.get("engine_path", "backtest_v12_engine.py")
    if hashlib.sha256(engine_path.read_bytes()).hexdigest() != manifest.get("engine_sha256"):
        raise ValueError("runtime engine hash does not match manifest")
    return manifest


def _read_json(path, limit=MAX_INPUT_BYTES):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")))


def _feed_path(path):
    path = Path(path).expanduser()
    return path.parent.resolve() / path.name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--follow", action="store_true", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sym-side", required=True)
    parser.add_argument("--venue", required=True, choices=("crypto", "stocks"))
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--source-journal", type=Path)
    parser.add_argument("--full-settings", type=Path)
    parser.add_argument("--candidate-overrides", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--max-age-seconds", type=float)
    parser.add_argument("--account")
    parser.add_argument("--capital", type=float, default=10000)
    args = parser.parse_args(argv)
    workspace_root = args.workspace_root.resolve()
    if not workspace_root.is_dir() or workspace_root.is_symlink():
        parser.error("workspace root must be a real directory")
    manifest = _read_json(args.manifest)
    if not isinstance(manifest, dict):
        parser.error("--manifest must contain a JSON object")
    required_identity = ("run_id", "epoch_id", "scenario_id", "plan_code_hash", "host")
    missing = [key for key in required_identity if not str(manifest.get(key, "")).strip()]
    if missing:
        parser.error("manifest missing required identity: " + ", ".join(missing))
    symbol, side = args.sym_side.rsplit("_", 1)
    if side not in {"LONG", "SHORT"} or not symbol:
        parser.error("--sym-side must end in _LONG or _SHORT")
    expected_venue = "crypto" if symbol.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "stocks"
    if expected_venue != args.venue:
        parser.error("venue does not match sym_side symbol convention")
    for key, expected in (("sym_side", args.sym_side), ("venue", args.venue)):
        if manifest.get(key, expected) != expected:
            parser.error(f"manifest {key} does not match CLI")
    snapshot = args.snapshot or Path(manifest.get("snapshot", ""))
    receipt = args.receipt or Path(manifest.get("receipt", ""))
    source_journal = args.source_journal or Path(manifest.get("source_journal", ""))
    full_settings_path = args.full_settings or Path(manifest.get("full_settings", ""))
    candidate_path = args.candidate_overrides or Path(manifest.get("candidate_overrides", ""))
    if not all(path and str(path) != "." for path in (full_settings_path, candidate_path)):
        parser.error("full_settings and candidate_overrides paths are required")
    if not source_journal and not all(path and str(path) != "." for path in (snapshot, receipt)):
        parser.error("snapshot and receipt are required unless --source-journal is supplied")
    source_journal_path = None
    if source_journal and str(source_journal) != ".":
        source_journal_path = source_journal.resolve() if source_journal.is_absolute() else (args.manifest.resolve().parent / source_journal).resolve()
        if source_journal_path.is_symlink() or not source_journal_path.is_file() or not source_journal_path.is_relative_to(workspace_root):
            parser.error("source journal must be a real file inside the workspace")
    engine_root_value = args.engine_root or Path(manifest.get("engine_root", ""))
    if not engine_root_value or str(engine_root_value) == ".":
        parser.error("--engine-root or manifest engine_root is required")
    if not engine_root_value.is_absolute():
        engine_root_value = args.manifest.resolve().parent / engine_root_value
    engine_root = engine_root_value.resolve()
    diagnostic_workspace_engine = engine_root == ROOT.resolve() and manifest.get("context", {}).get("evidence_label") == "DIAGNOSTIC_REPLAY" and manifest.get("context", {}).get("performance_claim") is False
    try:
        runtime_manifest = validate_runtime_root(engine_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if not diagnostic_workspace_engine:
            parser.error(f"engine_root must be an immutable verified paper runtime: {exc}")
        runtime_manifest = None
    if engine_root.is_symlink() or not (engine_root / "backtest_v12_engine.py").is_file():
        parser.error("engine_root must contain backtest_v12_engine.py")
    full_settings = _read_json(full_settings_path)
    candidate = _read_json(candidate_path)
    if not isinstance(full_settings, dict) or not full_settings or not isinstance(candidate, dict):
        parser.error("settings and candidate overrides must be JSON objects")
    if any(not isinstance(key, str) for key in (*full_settings, *candidate)):
        parser.error("settings keys must be strings")
    registered = manifest.get("registered_candidate_overrides")
    if isinstance(registered, dict) and registered != candidate:
        parser.error("candidate override file differs from registered scenario overrides")
    timeframe = str(manifest.get("frame_timeframe", "1m"))
    required_tfs = manifest.get("required_timeframes", [timeframe])
    if timeframe not in TIMEFRAME_SECONDS or not isinstance(required_tfs, list) or not required_tfs or any(tf not in TIMEFRAME_SECONDS for tf in required_tfs):
        parser.error("manifest has invalid frame_timeframe/required_timeframes")
    experiment_window = manifest.get("experiment_window")
    experiment_window_resolution = None
    if experiment_window is not None:
        try:
            experiment_window = validate_window(experiment_window, timeframe)
        except (TypeError, ValueError) as exc:
            parser.error(f"experiment_window is invalid: {exc}")
        if not source_journal_path:
            parser.error("experiment_window requires --source-journal")
        if experiment_window["window_mode"] == "first_available_after":
            experiment_window_resolution = resolve_source_window(source_journal_path, manifest["host"], args.venue, symbol, experiment_window)
            if experiment_window_resolution.get("status") != "SOURCE_WINDOW_READY":
                parser.error("source journal window is not ready: " + str(experiment_window_resolution.get("status")))
            experiment_window = {**experiment_window, "start_source_ts": experiment_window_resolution["start_source_ts"], "end_source_ts": experiment_window_resolution["end_source_ts"]}
    poll_seconds = float(args.poll_seconds if args.poll_seconds is not None else manifest.get("poll_seconds", 5))
    max_age_seconds = float(args.max_age_seconds if args.max_age_seconds is not None else manifest.get("max_age_seconds", 2 * TIMEFRAME_SECONDS[timeframe]))
    max_frames = int(manifest.get("max_frames", MAX_FRAMES))
    if not (0.25 <= poll_seconds <= 300 and TIMEFRAME_SECONDS[timeframe] <= max_age_seconds <= 7 * 86400):
        parser.error("poll/max_age setting is outside bounded limits")
    if not 2 <= max_frames <= MAX_FRAMES:
        parser.error(f"manifest max_frames must be between 2 and {MAX_FRAMES}")
    manifest = dict(manifest)
    manifest["frame_timeframe"] = timeframe
    manifest["required_timeframes"] = required_tfs
    manifest["max_frames"] = max_frames
    manifest["experiment_window"] = experiment_window
    manifest["experiment_window_resolution"] = experiment_window_resolution
    manifest["paper_mask_release"] = manifest.get("paper_mask_release", [])
    if not isinstance(manifest["paper_mask_release"], list) or any(not isinstance(key, str) for key in manifest["paper_mask_release"]):
        parser.error("paper_mask_release must be a list of candidate key names")
    manifest["experiment_context"] = manifest.get("experiment_context", {})
    if manifest["paper_mask_release"] and (not isinstance(manifest["experiment_context"], dict) or not manifest["experiment_context"]):
        parser.error("nonempty paper_mask_release requires a shared experiment_context object")
    output_root = (args.output_root or workspace_root / "data" / "paper_forward_lab" / "runs").resolve()
    if not output_root.is_relative_to(workspace_root):
        parser.error("output root must remain inside the repository workspace")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.run_dir.resolve() if args.run_dir else output_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10])
    if not run_dir.is_relative_to(workspace_root):
        parser.error("run directory must remain inside the repository workspace")
    if run_dir.exists():
        if not run_dir.is_dir() or run_dir.is_symlink():
            parser.error("run directory must be a real directory")
        os.chmod(run_dir, 0o700)
    else:
        run_dir.mkdir(parents=True, mode=0o700)
    spec = {
        **manifest,
        "sym_side": args.sym_side,
        "venue": args.venue,
        "snapshot": str(_feed_path(snapshot)) if snapshot and str(snapshot) != "." else "",
        "receipt": str(_feed_path(receipt)) if receipt and str(receipt) != "." else "",
        "source_journal": str(source_journal_path) if source_journal_path else "",
        "full_settings": str(Path(full_settings_path).resolve()),
        "candidate_overrides": str(Path(candidate_path).resolve()),
        "engine_root": str(engine_root),
        "engine_runtime_sha256": runtime_manifest.get("runtime_sha256") if runtime_manifest else None,
        "diagnostic_workspace_engine": diagnostic_workspace_engine,
        "frame_timeframe": timeframe,
        "required_timeframes": required_tfs,
        "poll_seconds": poll_seconds,
        "max_age_seconds": max_age_seconds,
    }
    spec_path = run_dir / "spec.json"
    spec_bytes = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if spec_path.exists() and spec_path.read_text(encoding="utf-8") != spec_bytes:
        parser.error("stable run directory already has a different immutable spec")
    if not spec_path.exists():
        with spec_path.open("x", encoding="utf-8") as handle:
            handle.write(spec_bytes)
    request = {"manifest": spec, "sym_side": args.sym_side, "symbol": symbol, "side": side, "mode": "crypto" if args.venue == "crypto" else "tradier", "full_settings": full_settings, "candidate_overrides": candidate, "engine_root": str(engine_root), "engine_runtime_sha256": runtime_manifest.get("runtime_sha256") if runtime_manifest else None, "diagnostic_workspace_engine": diagnostic_workspace_engine, "worker_code_root": str(ROOT.resolve()), "repo_root": str(workspace_root), "snapshot": str(_feed_path(snapshot)) if snapshot and str(snapshot) != "." else "", "receipt": str(_feed_path(receipt)) if receipt and str(receipt) != "." else "", "source_journal": str(source_journal_path) if source_journal_path else "", "poll_seconds": poll_seconds, "max_age_seconds": max_age_seconds, "account": args.account or manifest.get("account", "ang" if args.venue == "crypto" else "trb"), "capital": args.capital}
    request_path = run_dir / "request.json"
    request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if request_path.exists() and request_path.read_text(encoding="utf-8") != request_bytes:
        parser.error("stable run directory already has a different immutable request")
    if not request_path.exists():
        with request_path.open("x", encoding="utf-8") as handle:
            handle.write(request_bytes)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("V8_", "V12_", "PAPER_"))}
    env["V12_REAL_EXECUTE"] = "1"
    env["V8_RESEARCH_ALLOW_SIDE_KEYS"] = args.sym_side
    env.update({"PYTHONHASHSEED": "0", "V8_HASHSEED_LOCKED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT.resolve()) + os.pathsep + os.environ.get("PYTHONPATH", ""), "V12_BASE_PATH": str(run_dir), "BASE_PATH": str(run_dir), "EZ_LOG_DIR": str(run_dir), "TRADIER_API_LOG_DIR": str(run_dir), "TMPDIR": str(run_dir), "PAPER_FORWARD_ISOLATED": "1", "V8_FORCE_REAL": "1", "V12_LIVE_ONLY_PRODUCERS": "1", "V8_RATE_GUARD_DISABLED": "1", "V8_MAX_WALL_SECS": "0", "V8_ISOLATE_SIDE": side, "V8_LADDER_ONLY_SIDE": side, "V8_SWEEP_MODE": "1", "V12_PARITY_REPORT_EVERY": "1000000", "FORWARD_AUTO_PROMOTE": "0", "PER_SYM_STORE_SQLITE_DISABLED": "0", "V12_RUN_ONE_SET_PRECEDENCE": "1"})
    with (run_dir / "worker.stdout.log").open("ab") as stdout_handle, (run_dir / "worker.stderr.log").open("ab") as stderr_handle:
        process = subprocess.Popen([sys.executable, "-c", CHILD, str(run_dir)], cwd=workspace_root, env=env, stdout=stdout_handle, stderr=stderr_handle, close_fds=True)
        try:
            returncode = process.wait()
        except KeyboardInterrupt:
            process.send_signal(signal.SIGTERM)
            returncode = process.wait()
    result_path = run_dir / "result.json"
    if returncode or not result_path.exists():
        tail = (run_dir / "worker.stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()[-1:]
        result = {"status": "FAILED", "returncode": returncode, "run_dir": str(run_dir), "last_error": tail[-1] if tail else "worker exited without final receipt"}
        _atomic_json(run_dir / "worker.status.json", {"status": "FAILED", "reason": result["last_error"], "observed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "scenario_id": manifest["scenario_id"], "sym_side": args.sym_side, "host": manifest["host"]})
    else:
        result = _read_json(result_path)
        result["run_dir"] = str(run_dir)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result.get("status") in {"STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "COMPLETED", "DATA_GAP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
