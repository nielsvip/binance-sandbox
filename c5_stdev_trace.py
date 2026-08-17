"""Low-overhead c5 telemetry for raw STDEV exit evaluations."""

from __future__ import annotations

import atexit
from collections import Counter
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any


_COUNTERS: Counter[str] = Counter()
_SAMPLES: dict[str, dict[str, Any]] = {}
_LOCK = Lock()


def record_stdev_evaluation(
    *,
    path: str,
    symbol: str,
    side: str,
    timeframe: str,
    pct_b_previous: float,
    pct_b_current: float,
    wt_velocity_1h: float,
    fire: bool,
) -> None:
    key = f"{path}:{str(side).upper()}"
    with _LOCK:
        _COUNTERS[f"{key}:evaluated"] += 1
        if fire:
            _COUNTERS[f"{key}:fired"] += 1
        _SAMPLES.setdefault(
            key,
            {
                "path": path,
                "symbol": symbol,
                "side": str(side).upper(),
                "timeframe": timeframe,
                "pct_b_previous": pct_b_previous,
                "pct_b_current": pct_b_current,
                "wt_velocity_1h": wt_velocity_1h,
            },
        )


def stdev_trace_snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "counters": dict(sorted(_COUNTERS.items())),
            "first_raw_samples": dict(sorted(_SAMPLES.items())),
        }


def _write_snapshot() -> None:
    target = os.environ.get("V8_STDEV_C5_TRACE_FILE", "")
    if not target:
        return
    path = Path(target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(stdev_trace_snapshot(), sort_keys=True, indent=2) + "\n"
        )
    except OSError:
        pass


atexit.register(_write_snapshot)
