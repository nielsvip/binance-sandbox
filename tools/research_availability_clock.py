#!/usr/bin/env python3
"""Shared causal clock for research over interpolated execution rows.

Native execution rows become available at their own timestamp.  A synthetic
5m row derived from a 15m parent is not available until the recorded parent
close.  Rows are ordered by ``(availability_ts, source_row_index)``; the
source-row tie breaker is deliberately retained so several synthetic rows
released at one parent close are never collapsed.

This module is research/backtest-only.  It does not rewrite an NPZ.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np


CLOCK_CONTRACT = "SYNTHETIC_PARENT_CLOSE_AVAILABILITY_V1"


class AvailabilityClockError(ValueError):
    pass


@dataclass(frozen=True)
class AvailabilityClock:
    availability_ts: np.ndarray
    source_ts: np.ndarray
    source_row_index: np.ndarray
    synthetic: np.ndarray
    order: np.ndarray
    rows_sha256: str

    def contract(self) -> dict[str, Any]:
        lag = self.availability_ts - self.source_ts
        synthetic_lag = lag[self.synthetic.astype(bool)]
        duplicate_availability = int(
            len(self.availability_ts)
            - len(np.unique(self.availability_ts))
        )
        return {
            "kind": CLOCK_CONTRACT,
            "ordering": "availability_ts_then_source_row_index",
            "native_availability": "native_source_timestamp",
            "synthetic_availability": "synthetic_5m_parent_close_ts",
            "rows": int(len(self.availability_ts)),
            "synthetic_rows": int(np.count_nonzero(self.synthetic)),
            "shared_availability_rows": duplicate_availability,
            "parent_lag_min_seconds": (
                int(synthetic_lag.min()) if len(synthetic_lag) else 0
            ),
            "parent_lag_max_seconds": (
                int(synthetic_lag.max()) if len(synthetic_lag) else 0
            ),
            "rows_sha256": self.rows_sha256,
        }


def _clock_hash(
    availability_ts: np.ndarray,
    source_ts: np.ndarray,
    source_row_index: np.ndarray,
    synthetic: np.ndarray,
) -> str:
    # Explicit little-endian encodings make the fingerprint platform stable.
    digest = hashlib.sha256()
    digest.update(CLOCK_CONTRACT.encode("ascii") + b"\0")
    for arr, dtype in (
        (availability_ts, "<i8"),
        (source_ts, "<i8"),
        (source_row_index, "<i8"),
        (synthetic, "u1"),
    ):
        values = np.ascontiguousarray(arr, dtype=dtype)
        digest.update(len(values).to_bytes(8, "little"))
        digest.update(values.tobytes())
    return digest.hexdigest()


def build_availability_clock(data: Any) -> AvailabilityClock:
    """Build a stable clock for the rows currently selected in ``data``."""
    source_row_index = np.asarray(data.full_indices, dtype=np.int64)
    files = set(getattr(data.z, "files", ()))
    if "timestamps" in files:
        source_ts = np.asarray(data.z["timestamps"], dtype=np.int64)[
            source_row_index
        ]
    elif hasattr(data, "source_ts"):
        source_ts = np.asarray(data.source_ts, dtype=np.int64)
    else:
        # Small audit fixtures and already filtered execution views may expose
        # their source timestamps directly without duplicating the full NPZ
        # ``timestamps`` array.
        source_ts = np.asarray(data.ts, dtype=np.int64)
    synthetic = np.asarray(data.synthetic, dtype=np.uint8).astype(bool)
    if not (
        len(source_row_index) == len(source_ts) == len(synthetic)
    ):
        raise AvailabilityClockError("execution clock arrays have unequal lengths")
    parent_key = "synthetic_5m_parent_close_ts"
    if synthetic.any() and parent_key not in files:
        raise AvailabilityClockError(
            "synthetic rows require synthetic_5m_parent_close_ts"
        )
    parent = (
        np.asarray(data.z[parent_key], dtype=np.int64)[source_row_index]
        if parent_key in files
        else source_ts
    )
    availability = np.where(synthetic, parent, source_ts).astype(np.int64)
    if np.any(availability <= 0):
        raise AvailabilityClockError("execution availability must be positive")
    if np.any(availability < source_ts):
        raise AvailabilityClockError(
            "execution availability precedes its source timestamp"
        )
    if len(np.unique(source_row_index)) != len(source_row_index):
        raise AvailabilityClockError("source_row_index identity is not unique")
    order = np.lexsort((source_row_index, availability)).astype(np.int64)
    availability = np.ascontiguousarray(availability[order])
    source_ts = np.ascontiguousarray(source_ts[order])
    source_row_index = np.ascontiguousarray(source_row_index[order])
    synthetic_u8 = np.ascontiguousarray(
        synthetic[order].astype(np.uint8)
    )
    return AvailabilityClock(
        availability_ts=availability,
        source_ts=source_ts,
        source_row_index=source_row_index,
        synthetic=synthetic_u8,
        order=np.ascontiguousarray(order),
        rows_sha256=_clock_hash(
            availability,
            source_ts,
            source_row_index,
            synthetic_u8,
        ),
    )


def apply_availability_clock(data: Any) -> AvailabilityClock:
    """Reorder an in-memory execution view without changing its source NPZ."""
    clock = build_availability_clock(data)
    order = clock.order
    for field in ("open", "high", "low", "close"):
        setattr(
            data,
            field,
            np.ascontiguousarray(np.asarray(getattr(data, field))[order]),
        )
    data.synthetic = clock.synthetic
    data.full_indices = clock.source_row_index
    # ``ts`` means observation time throughout vector research. Preserve the
    # printed source timestamp separately for replay/source-row validation.
    data.ts = clock.availability_ts
    data.source_ts = clock.source_ts
    data.availability_ts = clock.availability_ts
    data.source_row_index = clock.source_row_index
    data.availability_clock = clock.contract()
    return clock


def next_strictly_later_index(
    availability_ts: np.ndarray, signal_index: int, right: int
) -> int | None:
    """Return the first row after a signal's availability batch."""
    values = np.asarray(availability_ts, dtype=np.int64)
    candidate = int(
        np.searchsorted(
            values,
            int(values[int(signal_index)]),
            side="right",
        )
    )
    return candidate if candidate < int(right) else None
