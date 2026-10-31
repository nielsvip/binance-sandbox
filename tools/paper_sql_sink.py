"""Persist raw paper forward receipts as immutable SQL decisions."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


class PaperSQLSink:
    """Write raw per-step events and positions for an independent-forward run."""

    def __init__(self, scenario_db: Any, run_id: str):
        self.db = scenario_db
        self.run_id = str(run_id)
        rows = self.db.query("SELECT run_id,evidence_kind FROM runs WHERE run_id=?", (self.run_id,))
        if not rows:
            raise ValueError(f"unknown paper SQL run {self.run_id}")
        if rows[0]["evidence_kind"] != "independent_forward":
            raise ValueError("paper SQL sink requires an independent_forward run")

    @staticmethod
    def _decision_id(run_id: str, source_ts: str, event_index: int) -> str:
        identity = json.dumps([run_id, source_ts, event_index], separators=(",", ":"))
        return "paper-step-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def record_step(self, ts: Any, receipt: Mapping[str, Any]) -> list[str]:
        """Store receipt events without inferring trades, fills, or returns."""
        source_ts = str(ts)
        events = receipt.get("events", [])
        positions = receipt.get("positions", {})
        if not isinstance(events, list) or not isinstance(positions, Mapping):
            raise ValueError("paper receipt must contain an events list and positions mapping")
        rows = events if events else [None]
        identifiers = []
        with self.db.transaction():
            for index, event in enumerate(rows):
                action = "NO_ACTION" if event is None else (
                    event.get("action", event.get("event_type", event.get("type", "RAW_EVENT")))
                    if isinstance(event, Mapping) else "RAW_EVENT"
                )
                decision_id = self._decision_id(self.run_id, source_ts, index)
                self.db.add_decision(
                    decision_id=decision_id,
                    run_id=self.run_id,
                    source_ts=source_ts,
                    action=str(action),
                    payload={"event_index": index, "event": event, "positions": positions},
                )
                identifiers.append(decision_id)
        return identifiers
