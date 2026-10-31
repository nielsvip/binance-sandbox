"""Raw, append-only closed-bar snapshots for experimental paper evaluation.

Unlike IndicatorStore this store never synthesizes, aliases, or forward-fills
indicator fields. It makes no trading or performance claims.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

import numpy as np


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        return _plain(value.item())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=True)


class PaperSnapshotStore:
    """Minimal IndicatorStore-shaped container preserving each raw snapshot."""

    def __init__(self, frames=None):
        self.arrays: dict[str, np.ndarray] = {}
        self.timestamps = np.asarray([], dtype="float64")
        self.ts_to_idx: dict[float, int] = {}
        self._present: dict[str, np.ndarray] = {}
        self._kinds: dict[str, str] = {}
        self._snapshot_hashes: dict[float, str] = {}
        self._raw_snapshots: list[dict[str, Any]] = []
        self.has_3m = False
        self.has_5m = False
        for frame in frames or []:
            if isinstance(frame, Mapping) and "indicators" in frame:
                observed_ts = frame.get("observed_ts", frame.get("ts"))
                self.append(observed_ts, frame["indicators"])
            else:
                self.append(frame["ts"], frame.get("indicators", frame.get("payload", {})))

    @property
    def n_bars(self) -> int:
        return len(self.timestamps)

    @staticmethod
    def _kind(value: Any) -> str:
        if value is None or isinstance(value, (str, bytes)):
            return "object"
        if isinstance(value, bool):
            return "object"
        if isinstance(value, (int, float, np.integer, np.floating)):
            return "numeric"
        return "object"

    def append(self, observed_ts: float, indicators: Mapping[str, Any]) -> int:
        if isinstance(observed_ts, bool):
            raise ValueError("observed_ts must be numeric, not bool")
        ts = float(observed_ts)
        if not math.isfinite(ts) or ts <= 0:
            raise ValueError("observed_ts must be positive and finite")
        if not isinstance(indicators, Mapping):
            raise TypeError("indicators must be a mapping")
        raw = _plain(indicators)
        if "current_price" not in raw:
            raise ValueError("raw snapshot requires explicit current_price")
        price = raw["current_price"]
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError("current_price must be explicit, positive, and finite")
        encoded = _canonical(raw)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if ts in self._snapshot_hashes:
            if self._snapshot_hashes[ts] != digest:
                raise ValueError(f"conflicting changed snapshot at {ts}")
            return self.ts_to_idx[ts]
        if self.n_bars and ts <= float(self.timestamps[-1]):
            raise ValueError("observed timestamps must be strictly increasing")

        next_index = self.n_bars
        keys = set(self.arrays) | set(raw)
        for key in keys:
            present = key in raw
            value = raw.get(key)
            kind = self._kind(value) if present else self._kinds.get(key, "object")
            old_kind = self._kinds.get(key)
            if old_kind is None:
                self._kinds[key] = kind
                fill = np.nan if kind == "numeric" else None
                prior = np.full(next_index, fill, dtype="float64" if kind == "numeric" else object)
                self.arrays[key] = prior
                self._present[key] = np.zeros(next_index, dtype=bool)
            elif old_kind == "numeric" and present and kind != "numeric":
                self._kinds[key] = "object"
                self.arrays[key] = self.arrays[key].astype(object)
            elif old_kind == "object" and present and kind == "numeric":
                kind = "object"
            if present:
                if self._kinds[key] == "numeric":
                    self.arrays[key] = np.append(self.arrays[key], float(value))
                else:
                    self.arrays[key] = np.append(self.arrays[key], np.asarray([value], dtype=object))
            else:
                missing = np.nan if self._kinds[key] == "numeric" else None
                self.arrays[key] = np.append(self.arrays[key], missing)
            self._present[key] = np.append(self._present[key], present)

        self.timestamps = np.append(self.timestamps, ts)
        self.ts_to_idx[ts] = next_index
        self._snapshot_hashes[ts] = digest
        self._raw_snapshots.append(copy.deepcopy(raw))
        self.has_3m = self.has_3m or any(str(key).endswith("_3m") for key in raw)
        self.has_5m = self.has_5m or any(str(key).endswith("_5m") for key in raw)
        return next_index

    def get(self, key: str, idx: int, default=0):
        if key not in self.arrays or idx < 0 or idx >= self.n_bars or not self._present[key][idx]:
            return default
        value = self.arrays[key][idx]
        return value.item() if isinstance(value, np.generic) else copy.deepcopy(value)

    def price(self, idx: int) -> float:
        value = self.get("current_price", idx, None)
        if value is None or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
            return 0.0
        return float(value)

    def build_indicator_dict(self, idx: int) -> dict[str, Any]:
        if idx < 0 or idx >= self.n_bars:
            raise IndexError(idx)
        return copy.deepcopy(self._raw_snapshots[idx])
