"""Experimental adapter joining a durable frame journal to the scalar engine.

The isolation receipt is a launcher assertion, not proof that this process is
network-isolated. This module contains no strategy or PnL calculations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from tools.paper_frame_stream import FrameJournal
from tools import paper_worker_isolation


_SACRED_FIELDS = (
    "positionAmt", "quantity", "entry_price", "max_gain", "opened_at",
    "entry_ts", "position_side", "symbol", "was_reentered", "was_reduced",
    "is_reduced", "is_hedge", "hedge_for", "fixed_stop_price",
    "fixed_stop_anchor", "fixed_stop_armed", "exchange_stop_order_id",
)
_SOURCE_TIME_KEYS = {"timestamp", "ts", "_tick_ts"}


def _time_key(key: str) -> bool:
    return key in _SOURCE_TIME_KEYS or key.startswith("timestamp_") or key.startswith("age_")


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite value in paper receipt")
        return value
    raise TypeError(f"non-JSON value in paper receipt: {type(value).__name__}")


def preserve_source_times(mutated: dict, original: Mapping) -> dict:
    """Restore source timestamp/age fields exactly and remove generated ones."""
    saved = {key: value for key, value in original.items() if _time_key(str(key))}
    for key in list(mutated):
        if _time_key(str(key)):
            del mutated[key]
    mutated.update(saved)
    return mutated


class JournalForwardSession:
    """Feed durable closed-bar frames into existing stores without engine resets."""

    def __init__(
        self,
        journal: FrameJournal,
        stores: Mapping[str, Any],
        apply_frame,
        *,
        code_path: str | Path,
        mode: str,
    ):
        self.journal = journal
        self.stores = stores
        self.apply_frame = apply_frame
        self.code_path = Path(code_path)
        self.mode = mode
        self._identity = json.loads(journal.identity)
        self._frames = None
        self._cursor = 0
        self._current = None
        self._pending_ts = None
        self._last_ts = None
        self._exhausted = False
        self._finished = False
        self._executed_trades = None
        self._positions = None
        self._event_offset = 0

    def assert_ready(self, mode: str, stores: Mapping[str, Any]) -> None:
        if mode not in {"crypto", "tradier"} or mode != self.mode:
            raise RuntimeError("paper journal session mode mismatch")
        if stores is not self.stores:
            raise RuntimeError("paper journal session stores mismatch")
        required = {
            "PAPER_FORWARD_ISOLATED": "1",
            "V8_FORCE_REAL": "1",
            "V12_LIVE_ONLY_PRODUCERS": "1",
        }
        wrong = [key for key, value in required.items() if os.environ.get(key) != value]
        if wrong:
            raise RuntimeError("paper forward environment missing required markers: " + ", ".join(wrong))
        replay_ingest = (
            "V8_VEC_EXACT_INGEST", "V8_VEC_REPLAY", "V8_VEC_INGEST",
            "V12_VEC_REPLAY", "V12_VEC_INGEST", "V12_PARITY_QUICK_LEDGER_REPLAY",
        )
        active = [key for key in replay_ingest if os.environ.get(key) == "1"]
        if active:
            raise RuntimeError("paper forward rejects vector replay/ingest: " + ", ".join(active))
        isolation_receipt = paper_worker_isolation.require_installed()
        if isolation_receipt.get("network_denied") is not True or not isolation_receipt.get("write_root"):
            raise RuntimeError("paper forward requires an installed worker isolation hook")
        if not self.code_path.is_file():
            raise RuntimeError("engine code path is unavailable for journal identity check")
        code_hash = hashlib.sha256(self.code_path.read_bytes()).hexdigest()
        if code_hash != self._identity.get("code_sha256"):
            raise RuntimeError("paper journal code hash does not match engine file")
        for store in stores.values():
            if not hasattr(store, "ts_to_idx") or not hasattr(store, "timestamps"):
                raise RuntimeError("paper forward store lacks timestamp index")

    def bind(self, executed_trades: list, positions: Mapping[str, Any]) -> None:
        if self._executed_trades is not None:
            raise RuntimeError("paper forward session is already bound")
        self._executed_trades = executed_trades
        self._positions = positions
        self._event_offset = len(executed_trades)

    async def frames(self):
        if self._frames is not None:
            raise RuntimeError("paper journal frames may only be consumed once")
        self._frames = self.journal.frames()
        for frame in self._frames:
            if self._last_ts is not None and frame["ts"] <= self._last_ts:
                raise RuntimeError("journal replay timestamps are not strictly increasing")
            self._current = frame
            self._last_ts = frame["ts"]
            yield frame
        self._exhausted = True

    async def before_step(self, ts: float) -> None:
        if self._executed_trades is None or self._positions is None:
            raise RuntimeError("paper forward session must bind engine state before stepping")
        if self._current is None or float(ts) != self._current["ts"]:
            raise RuntimeError("step timestamp does not match current journal input")
        if self._pending_ts is not None:
            raise RuntimeError("previous journal bar has not committed")
        await self.apply_frame(self._current["payload"], self.stores)
        for symbol, store in self.stores.items():
            idx = store.ts_to_idx.get(ts, -1)
            if idx < 0:
                raise RuntimeError(f"frame {ts} missing from store {symbol}")
            stamp = float(store.timestamps[idx])
            if stamp > 1e11:
                stamp /= 1000.0
            if stamp != float(ts):
                raise RuntimeError(f"store {symbol} timestamp index mismatch at {ts}")
            for source_ts in store.timestamps[: idx + 1]:
                normalized = float(source_ts) / 1000.0 if float(source_ts) > 1e11 else float(source_ts)
                if normalized > float(ts):
                    raise RuntimeError(f"store {symbol} has a future row in the evaluated prefix")
        self._pending_ts = float(ts)

    def _position_snapshot(self) -> dict:
        rows = {}
        for key, position in sorted(self._positions.items(), key=lambda item: str(item[0])):
            if isinstance(position, Mapping):
                row = {field: position.get(field) for field in _SACRED_FIELDS}
            else:
                row = {field: getattr(position, field, None) for field in _SACRED_FIELDS}
            rows[str(key)] = _json_value(row)
        return rows

    async def after_step(self, ts: float) -> None:
        if self._pending_ts != float(ts) or self._current is None:
            raise RuntimeError("no matching pending journal step")
        receipt = {
            "events": _json_value(self._executed_trades[self._event_offset:]),
            "positions": self._position_snapshot(),
        }
        self.journal.record_receipt(ts, receipt)
        self._event_offset = len(self._executed_trades)
        self._cursor += 1
        self._pending_ts = None
        self._current = None

    async def finish(self) -> None:
        if not self._exhausted or self._pending_ts is not None:
            raise RuntimeError("paper session may finish only after normal exhausted replay")
        if self._finished:
            return
        self._finished = True
