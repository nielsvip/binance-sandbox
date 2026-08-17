"""C5 scorer-to-fill tracing for GR_HTF direct entry/exit handoffs."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any


C5_GR_TRACE_VERSION = "gr-htf-handoff-v1"

_COUNTERS: Counter[str] = Counter()
_LOCK = Lock()
_VALID_STAGES = {
    "scorer_called",
    "scorer_pass",
    "candidate",
    "post_veto_pass",
    "dispatch",
    "wrapper_received",
    "wrapper_blocked",
    "executed",
}


def trace_gr_htf(stage: str, **fields: Any) -> None:
    """Count a handoff stage and optionally append a compact JSON trace event."""
    if stage not in _VALID_STAGES:
        raise ValueError(f"unknown GR_HTF trace stage: {stage}")
    side = str(fields.get("position_side") or fields.get("side") or "").upper()
    path = str(fields.get("path") or "GR_HTF").upper()
    with _LOCK:
        _COUNTERS[stage] += 1
        _COUNTERS[f"{path}:{stage}"] += 1
        if side:
            _COUNTERS[f"{path}:{side}:{stage}"] += 1
    target = os.environ.get("V8_GR_HTF_TRACE_FILE", "")
    if not target:
        return
    event = {
        "trace_version": C5_GR_TRACE_VERSION,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        **{
            str(key): value
            for key, value in fields.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        },
    }
    path_obj = Path(target)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path_obj.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def gr_htf_trace_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(sorted(_COUNTERS.items()))


def reset_gr_htf_trace() -> None:
    with _LOCK:
        _COUNTERS.clear()
