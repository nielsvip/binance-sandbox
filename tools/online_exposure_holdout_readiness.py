#!/usr/bin/env python3
"""Fail-closed monitor for the post-freeze PBF online-controller holdout.

This command never launches a backtest and never changes the NPZ.  It verifies
that the frozen prefix is unchanged, counts completed 1h slots strictly after
the freeze, and reports whether the preregistered two-window/calendar gate has
accrued.  A not-ready or invalid state exits 3.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


PREFIX_KEYS = (
    "timestamps",
    "open_5m",
    "high_5m",
    "low_5m",
    "close_5m",
)


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def prefix_sha256(npz_path: Path, freeze_ts: int) -> tuple[str, int]:
    """Hash the immutable execution prefix through ``freeze_ts``."""
    digest = hashlib.sha256()
    with np.load(npz_path, allow_pickle=False) as payload:
        ts = np.asarray(payload["timestamps"], dtype=np.int64)
        keep = ts <= int(freeze_ts)
        count = int(np.count_nonzero(keep))
        for key in PREFIX_KEYS:
            values = np.ascontiguousarray(np.asarray(payload[key])[keep])
            digest.update(key.encode())
            digest.update(values.dtype.str.encode())
            digest.update(str(values.shape).encode())
            digest.update(values.tobytes())
    return digest.hexdigest(), count


def holdout_readiness(npz_path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    freeze = _utc(receipt["freeze_end_utc"])
    not_before = _utc(receipt["holdout_not_before_utc"])
    freeze_ts = int(freeze.timestamp())
    not_before_ts = int(not_before.timestamp())
    current_prefix, prefix_rows = prefix_sha256(npz_path, freeze_ts)
    with np.load(npz_path, allow_pickle=False) as payload:
        ts = np.asarray(payload["timestamps"], dtype=np.int64)
        h1 = np.asarray(payload["timestamp_1h"], dtype=np.int64)
        latest_ts = int(ts[-1]) if len(ts) else 0
        completed_post_freeze = np.unique(
            h1[(ts >= not_before_ts) & (h1 >= not_before_ts)]
        )
    slots = int(len(completed_post_freeze))
    window = int(receipt["controller_window_completed_1h_slots"])
    required_windows = int(receipt["required_complete_windows"])
    complete_windows = slots // window
    prefix_ok = current_prefix == receipt["frozen_prefix_sha256"]
    calendar_ok = latest_ts >= not_before_ts
    windows_ok = complete_windows >= required_windows
    blocked = []
    if not prefix_ok:
        blocked.append("FROZEN_PREFIX_DRIFT")
    if not windows_ok:
        blocked.append(
            f"NEED_{required_windows * window - slots}_MORE_COMPLETED_1H_SLOTS"
        )
    if not calendar_ok:
        blocked.append(f"CALENDAR_HOLDOUT_BEFORE_{not_before.isoformat()}")
    ready = prefix_ok and windows_ok and calendar_ok
    return {
        "status": "READY_FOR_FROZEN_EVALUATION" if ready else "BLOCKED",
        "launch_performed": False,
        "tuning_performed": False,
        "symbol": receipt["symbol"],
        "npz": str(npz_path),
        "freeze_end_utc": freeze.isoformat(),
        "latest_execution_utc": (
            datetime.fromtimestamp(latest_ts, timezone.utc).isoformat()
            if latest_ts
            else None
        ),
        "holdout_not_before_utc": not_before.isoformat(),
        "frozen_prefix_rows": prefix_rows,
        "frozen_prefix_sha256_expected": receipt["frozen_prefix_sha256"],
        "frozen_prefix_sha256_current": current_prefix,
        "frozen_prefix_valid": prefix_ok,
        "post_freeze_completed_1h_slots": slots,
        "controller_window_completed_1h_slots": window,
        "complete_controller_windows": complete_windows,
        "required_complete_windows": required_windows,
        "remaining_completed_1h_slots": max(
            0, required_windows * window - slots
        ),
        "blocked_reasons": blocked,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npz", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    result = holdout_readiness(args.npz, receipt)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["status"] == "READY_FOR_FROZEN_EVALUATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
