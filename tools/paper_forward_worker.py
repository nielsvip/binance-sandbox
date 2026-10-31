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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.paper_frame_stream import FrameJournal
from tools.paper_journal_session import JournalForwardSession
from tools.paper_snapshot_store import PaperSnapshotStore

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


class ContinuousJournalForwardSession(JournalForwardSession):
    """Replay the durable journal, then append distinct fresh source frames."""

    def __init__(self, *args, snapshot_path, receipt_path, symbol, frame_timeframe, required_timeframes, poll_seconds, max_age_seconds, run_root, max_frames=MAX_FRAMES, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot_path = Path(snapshot_path)
        self.receipt_path = Path(receipt_path)
        self.symbol = symbol
        self.frame_timeframe = frame_timeframe
        self.required_timeframes = tuple(required_timeframes)
        self.poll_seconds = float(poll_seconds)
        self.max_age_seconds = float(max_age_seconds)
        self.run_root = Path(run_root)
        self.max_frames = int(max_frames)
        self.stop_requested = False
        self.wait_reason = "STARTING"
        self.observed_at_utc = None
        self.last_source_ts = None
        self._source_frame_count = 0

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
            "last_source_timestamp_epoch_s": self.last_source_ts,
            "source_time_semantics": "native timeframe source timestamp; observed_at_utc is a separate local observation clock; no closed-bar claim",
            "journal_frames": self.journal._db.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
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
            snapshot = validate_snapshot_bytes(raw)
            indicators = snapshot.get(self.symbol)
            if not isinstance(indicators, dict):
                return None, "SYMBOL_MISSING"
            if len(json.dumps(indicators, separators=(",", ":"), allow_nan=False).encode()) > MAX_SYMBOL_BYTES:
                return None, "SYMBOL_RECORD_TOO_LARGE"
            observed = receipt.get("captured_at_utc")
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

    async def prime_first_frame(self):
        """Wait for one fresh input before engine initialization/assert_ready."""
        while True:
            if self.stop_requested:
                raise RuntimeError("stop requested before a fresh first source frame")
            frame, reason = self._read_source_frame()
            if frame is not None:
                self.journal.append_frame(frame["ts"], {self.symbol: {"indicators": frame["indicators"], "observation": frame["observation"]}})
                self.last_source_ts = frame["ts"]
                self._source_frame_count += 1
                self._status("RUNNING", "FIRST_FRESH_SOURCE_FRAME")
                return frame
            self.wait_reason = reason
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
            frame, reason = self._read_source_frame()
            if frame is None:
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
                await asyncio.sleep(self.poll_seconds)
                continue
            if self._last_ts is not None and source_ts <= self._last_ts:
                self._status("WAITING_FRESH_INPUT", "SOURCE_TIMESTAMP_NOT_ADVANCED")
                await asyncio.sleep(self.poll_seconds)
                continue
            self.journal.append_frame(source_ts, payload)
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
        status = "STOPPED_BY_REQUEST" if self.stop_requested else ("STOPPED_RESOURCE_LIMIT" if self.wait_reason == "RESOURCE_LIMIT_MAX_FRAMES" else "COMPLETED")
        self._status(status, self.wait_reason)

CHILD = r'''
import asyncio, hashlib, json, os, sys
import signal
from datetime import datetime, timezone
from pathlib import Path
from tools.paper_worker_isolation import install

root = Path(sys.argv[1]).resolve()
install(root)
request = json.loads((root / "request.json").read_text(encoding="utf-8"))
symbol = request["symbol"]
mode = request["mode"]
manifest = request["manifest"]
settings = request["full_settings"]
candidate = request["candidate_overrides"]
requested_effective = dict(settings)
requested_effective.update(candidate)

import backtest_v12_engine as engine
from tools.paper_forward_worker import ContinuousJournalForwardSession
from tools.paper_frame_stream import FrameJournal
from tools.paper_snapshot_store import PaperSnapshotStore
import config as crypto_config
import config_tradier as stock_config
import ez_manage
import tradier_manage

release = set(manifest.get("paper_mask_release", []))
if release - set(candidate):
    raise RuntimeError("paper_mask_release may name candidate override keys only")
if any(candidate.get(key) in (True, "True", "true") for key in release if key in ez_manage._NON_VEC_KNOBS_EZ or key in getattr(stock_config.TradierConfig, "PARITY_NON_VECTORIZABLE_SWITCHES", ())):
    nonvec_release = True
else:
    nonvec_release = False
if any(candidate.get(key) in (True, "True", "true") for key in release if key in ez_manage.SUB15M_BLOCKED_SWITCHES or key in tradier_manage.SUB15M_BLOCKED_SWITCHES):
    sub15m_release = True
else:
    sub15m_release = False
isolation_overrides = {}
nonvec_keys = set(ez_manage._NON_VEC_KNOBS_EZ) | set(getattr(stock_config.TradierConfig, "PARITY_NON_VECTORIZABLE_SWITCHES", ()))
sub15m_blocked = {**ez_manage.SUB15M_BLOCKED_SWITCHES, **tradier_manage.SUB15M_BLOCKED_SWITCHES}
for key in nonvec_keys:
    if key not in release:
        current = settings.get(key, getattr(crypto_config.Config, key, getattr(stock_config.TradierConfig, key, None)))
        if isinstance(current, bool):
            isolation_overrides[key] = False
        elif isinstance(current, str) and key.endswith("_TF"):
            isolation_overrides[key] = "None"
        elif isinstance(current, (list, tuple, set, frozenset)):
            isolation_overrides[key] = type(current)()
        elif current is None and key.endswith("_ENABLED"):
            isolation_overrides[key] = False
        else:
            raise RuntimeError(f"no known blocked baseline value for nonvectorizable knob {key}")
for key, blocked_value in sub15m_blocked.items():
    if key not in release:
        isolation_overrides[key] = blocked_value
guarded_true = {key for key, value in candidate.items() if value in (True, "True", "true") and (key in nonvec_keys or key in sub15m_blocked)}
if guarded_true - release:
    raise RuntimeError("guarded candidate key requires explicit paper_mask_release: " + ", ".join(sorted(guarded_true - release)))
mask_master_keys = {"PARITY_DISABLE_NON_VECTORIZABLE", "V12_PARITY_DISABLE_NON_VECTORIZABLE", "TRC_PARITY_DISABLE_NON_VECTORIZABLE", "LTF_SUB15M_TRADING_ENABLED", "TRADIER_LTF_SUB15M_TRADING_ENABLED"}
if any(candidate.get(key) is True for key in mask_master_keys):
    raise RuntimeError("candidate may not override paper isolation mask masters")
paper_mask_release = {}
if nonvec_release:
    paper_mask_release.update(PARITY_DISABLE_NON_VECTORIZABLE=False, V12_PARITY_DISABLE_NON_VECTORIZABLE=False, TRC_PARITY_DISABLE_NON_VECTORIZABLE=False)
else:
    isolation_overrides.update(PARITY_DISABLE_NON_VECTORIZABLE=True, V12_PARITY_DISABLE_NON_VECTORIZABLE=True, TRC_PARITY_DISABLE_NON_VECTORIZABLE=True)
if sub15m_release:
    paper_mask_release.update(LTF_SUB15M_TRADING_ENABLED=True, TRADIER_LTF_SUB15M_TRADING_ENABLED=True)
else:
    isolation_overrides.update(LTF_SUB15M_TRADING_ENABLED=False, TRADIER_LTF_SUB15M_TRADING_ENABLED=False)
effective = dict(settings)
effective.update(isolation_overrides)
effective.update(paper_mask_release)
effective.update(candidate)

code_path = Path(engine.__file__).resolve()
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
    "paper_mask_release": paper_mask_release, "context": manifest.get("context", {}),
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
for key, requested_value in candidate.items():
    default = requested_value
    if mode == "crypto":
        resolved_value = getter(symbol, request["side"], key, default)
    else:
        resolved_value = getter(key, default, request.get("account", "trb"), symbol, request["side"])
    if resolved_value != requested_value:
        raise RuntimeError(f"effective per-symbol getter mismatch for {key}: requested={requested_value!r} resolved={resolved_value!r}")
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
    symbol=symbol, frame_timeframe=manifest.get("frame_timeframe", "1m" if mode == "crypto" else "5m"),
    required_timeframes=manifest.get("required_timeframes", [manifest.get("frame_timeframe", "1m" if mode == "crypto" else "5m")]),
    poll_seconds=request["poll_seconds"], max_age_seconds=request["max_age_seconds"],
    run_root=root, max_frames=manifest.get("max_frames", 1440),
)
if not first_frame:
    first_frame = asyncio.run(session.prime_first_frame())
    stores[symbol] = PaperSnapshotStore([{"ts": first_frame["ts"], "indicators": first_frame["indicators"]}])
    session.stores = stores
else:
    session._source_frame_count = journal._db.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    session.last_source_ts = float(journal._db.execute("SELECT closed_ts FROM frames ORDER BY sequence DESC LIMIT 1").fetchone()[0])
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
    receipts = journal._db.execute("SELECT COUNT(*),SUM(receipt_hash IS NOT NULL) FROM frames").fetchone()
    if int(receipts[0]) != int(receipts[1] or 0):
        raise RuntimeError("engine returned without committing every source-frame receipt")
    result = {
        "status": "STOPPED_BY_REQUEST" if session.stop_requested else ("STOPPED_RESOURCE_LIMIT" if session.wait_reason == "RESOURCE_LIMIT_MAX_FRAMES" else "COMPLETED"),
        "evidence_kind": "independent_forward", "run_id": manifest["run_id"],
        "epoch_id": manifest["epoch_id"], "scenario_id": manifest["scenario_id"],
        "sym_side": symside, "mode": mode, "frame_count": int(receipts[0]),
        "committed_receipts": int(receipts[1] or 0), "settings_sha256": settings_hash,
        "code_sha256": code_hash, "plan_code_hash": manifest["plan_code_hash"],
        "candidate_overrides": candidate, "override_issues": list(engine._RUN_ONE_OVERRIDE_ISSUES),
        "store_row_existed": bool(precedence_state.get("store_row_existed")),
        "source_time_semantics": "native source timeframe timestamp; closed-bar status unverified",
        "trades": trades, "receipt_db": str(root / "receipts.sqlite3"),
        "promotion_eligible": False,
    }
    _atomic_json(root / "result.json", result)
    result_manifest.update(status=result["status"], finished=True, ended_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), frame_count=result["frame_count"], committed_receipts=result["committed_receipts"])
    _atomic_json(result_manifest_path, result_manifest)
finally:
    journal.close()
    engine._run_one_restore_overrides(override_snapshot, mode)
    engine._run_one_set_precedence_off(precedence_state)
'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read_json(path, limit=MAX_INPUT_BYTES):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")))


def load_frames(path, symbol):
    frames = []
    total = 0
    with Path(path).open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            total += len(raw)
            if total > MAX_INPUT_BYTES:
                raise ValueError("frame stream exceeds 32 MiB")
            if not raw.strip():
                continue
            frame = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")))
            if not isinstance(frame, dict) or set(frame) != {"ts", "indicators"}:
                raise ValueError(f"frame line {line_number} must contain only ts and indicators")
            if isinstance(frame["ts"], bool) or not isinstance(frame["ts"], (int, float)):
                raise ValueError(f"frame line {line_number} timestamp must be epoch seconds")
            ts = float(frame["ts"])
            if not (ts > 0 and ts < float("inf")) or (frames and ts <= frames[-1]["ts"]):
                raise ValueError(f"frame line {line_number} timestamp is invalid or not increasing")
            indicators = frame["indicators"]
            if not isinstance(indicators, dict) or "current_price" not in indicators:
                raise ValueError(f"frame line {line_number} lacks explicit indicators/current_price")
            frames.append({"ts": ts, "indicators": indicators})
            if len(frames) > MAX_FRAMES:
                raise ValueError(f"frame stream exceeds {MAX_FRAMES} frames")
    if len(frames) < 2:
        raise ValueError("independent forward worker requires at least two distinct closed frames")
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--follow", action="store_true", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sym-side", required=True)
    parser.add_argument("--venue", required=True, choices=("crypto", "stocks"))
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--full-settings", type=Path)
    parser.add_argument("--candidate-overrides", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "paper_forward_lab" / "runs")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--max-age-seconds", type=float)
    parser.add_argument("--account")
    parser.add_argument("--capital", type=float, default=10000)
    args = parser.parse_args(argv)
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
    full_settings_path = args.full_settings or Path(manifest.get("full_settings", ""))
    candidate_path = args.candidate_overrides or Path(manifest.get("candidate_overrides", ""))
    if not all(path and str(path) != "." for path in (snapshot, receipt, full_settings_path, candidate_path)):
        parser.error("snapshot, receipt, full_settings, and candidate_overrides paths are required")
    full_settings = _read_json(full_settings_path)
    candidate = _read_json(candidate_path)
    if not isinstance(full_settings, dict) or not full_settings or not isinstance(candidate, dict):
        parser.error("settings and candidate overrides must be JSON objects")
    if any(not isinstance(key, str) for key in (*full_settings, *candidate)):
        parser.error("settings keys must be strings")
    registered = manifest.get("registered_candidate_overrides")
    if isinstance(registered, dict) and registered != candidate:
        parser.error("candidate override file differs from registered scenario overrides")
    timeframe = str(manifest.get("frame_timeframe", "1m" if args.venue == "crypto" else "5m"))
    required_tfs = manifest.get("required_timeframes", [timeframe])
    if timeframe not in TIMEFRAME_SECONDS or not isinstance(required_tfs, list) or not required_tfs or any(tf not in TIMEFRAME_SECONDS for tf in required_tfs):
        parser.error("manifest has invalid frame_timeframe/required_timeframes")
    poll_seconds = float(args.poll_seconds if args.poll_seconds is not None else manifest.get("poll_seconds", 5))
    max_age_seconds = float(args.max_age_seconds if args.max_age_seconds is not None else manifest.get("max_age_seconds", 2 * TIMEFRAME_SECONDS[timeframe]))
    if not (0.25 <= poll_seconds <= 300 and TIMEFRAME_SECONDS[timeframe] <= max_age_seconds <= 7 * 86400):
        parser.error("poll/max_age setting is outside bounded limits")
    manifest = dict(manifest)
    manifest["frame_timeframe"] = timeframe
    manifest["required_timeframes"] = required_tfs
    manifest["paper_mask_release"] = manifest.get("paper_mask_release", [])
    if not isinstance(manifest["paper_mask_release"], list) or any(not isinstance(key, str) for key in manifest["paper_mask_release"]):
        parser.error("paper_mask_release must be a list of candidate key names")
    output_root = args.output_root.resolve()
    if not output_root.is_relative_to(ROOT.resolve()):
        parser.error("output root must remain inside the repository workspace")
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.run_dir.resolve() if args.run_dir else output_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10])
    if not run_dir.is_relative_to(ROOT.resolve()):
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
        "snapshot": str(Path(snapshot).resolve()),
        "receipt": str(Path(receipt).resolve()),
        "full_settings": str(Path(full_settings_path).resolve()),
        "candidate_overrides": str(Path(candidate_path).resolve()),
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
    request = {"manifest": spec, "sym_side": args.sym_side, "symbol": symbol, "side": side, "mode": "crypto" if args.venue == "crypto" else "tradier", "full_settings": full_settings, "candidate_overrides": candidate, "snapshot": str(Path(snapshot).resolve()), "receipt": str(Path(receipt).resolve()), "poll_seconds": poll_seconds, "max_age_seconds": max_age_seconds, "account": args.account or manifest.get("account", "ang" if args.venue == "crypto" else "trb"), "capital": args.capital}
    request_path = run_dir / "request.json"
    request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if request_path.exists() and request_path.read_text(encoding="utf-8") != request_bytes:
        parser.error("stable run directory already has a different immutable request")
    if not request_path.exists():
        with request_path.open("x", encoding="utf-8") as handle:
            handle.write(request_bytes)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("V8_", "V12_", "PAPER_"))}
    env.update({"PYTHONHASHSEED": "0", "V8_HASHSEED_LOCKED": "1", "PYTHONDONTWRITEBYTECODE": "1", "V12_BASE_PATH": str(run_dir), "BASE_PATH": str(run_dir), "EZ_LOG_DIR": str(run_dir), "TRADIER_API_LOG_DIR": str(run_dir), "TMPDIR": str(run_dir), "PAPER_FORWARD_ISOLATED": "1", "V8_FORCE_REAL": "1", "V12_LIVE_ONLY_PRODUCERS": "1", "V8_RATE_GUARD_DISABLED": "1", "V8_MAX_WALL_SECS": "0", "V8_ISOLATE_SIDE": side, "V8_LADDER_ONLY_SIDE": side, "V8_SWEEP_MODE": "1", "FORWARD_AUTO_PROMOTE": "0", "PER_SYM_STORE_SQLITE_DISABLED": "0", "V12_RUN_ONE_SET_PRECEDENCE": "1"})
    with (run_dir / "worker.stdout.log").open("ab") as stdout_handle, (run_dir / "worker.stderr.log").open("ab") as stderr_handle:
        process = subprocess.Popen([sys.executable, "-c", CHILD, str(run_dir)], cwd=ROOT, env=env, stdout=stdout_handle, stderr=stderr_handle, close_fds=True)
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
    return 0 if result.get("status") == "COMPLETED_MULTI_FRAME_ENGINE_RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
