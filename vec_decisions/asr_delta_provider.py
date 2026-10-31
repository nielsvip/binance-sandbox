"""Causal adapter for the native stateful WT/DC DeltaTracker."""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from vec_decisions.asr_input_adapter import paired_npz_view
from vec_decisions.live_indicator_labels import decode_live_indicator_labels


_OUTPUT_FIELDS = (
    "entry_long", "entry_short", "exit_long", "exit_short",
    "exit_pending_long", "exit_pending_short", "exit_retest_long",
    "exit_retest_short", "pyramid_long", "pyramid_short",
    "post_consolidation", "bull_speed", "bear_speed", "bull_tf_count",
    "bear_tf_count", "total_fields", "zone", "zone_action", "zone_reason",
    "tf_lost", "reentry_if_momentum",
)


class _Row(Mapping):
    def __init__(self, arrays, index):
        self.arrays = arrays
        self.index = index

    def __getitem__(self, key):
        value = self.arrays[key]
        return value[self.index].item() if isinstance(value, np.ndarray) else value

    def __iter__(self):
        return iter(self.arrays)

    def __len__(self):
        return len(self.arrays)

    def get(self, key, default=None):
        if key not in self.arrays:
            return default
        return self[key]


def replay_delta_tracker_arrays(npz, *, symbol, cfg, use_ltf, timestamps,
                               is_long, position_states=None, warmup_mask=None):
    """Run the authoritative DeltaTracker in timestamp order over raw columns.

    The adapter does not approximate its recurrent velocity/exit state with a
    stateless array formula. It calls the shipped DeltaTracker.update once for
    each supplied observation. Caller supplies exact account lifecycle state;
    None means the native no-position context for that row. Missing NPZ fields
    remain missing so native field defaults, and their limits, stay visible.
    """
    from wt_dc_delta import DeltaTracker

    ts = np.asarray(timestamps, dtype=np.int64).reshape(-1)
    n = ts.size
    if n == 0:
        raise ValueError("timestamps must be non-empty")
    if np.any(np.diff(ts) < 0):
        raise ValueError("timestamps must be nondecreasing")
    position_context_supplied = position_states is not None
    if position_states is None:
        position_states = [None] * n
    if len(position_states) != n:
        raise ValueError("position_states length must match timestamps")
    if warmup_mask is None:
        warmup = np.zeros(n, dtype=bool)
    else:
        warmup = np.asarray(warmup_mask, dtype=bool).reshape(-1)
        if warmup.size != n:
            raise ValueError("warmup_mask length must match timestamps")

    selected = paired_npz_view(npz, bool(use_ltf))
    arrays = decode_live_indicator_labels(selected)
    arrays = {key: np.asarray(value) for key, value in arrays.items()
              if isinstance(value, (np.ndarray, list, tuple)) and np.asarray(value).ndim > 0}
    for key, value in arrays.items():
        if value.shape[0] != n:
            raise ValueError(f"{key}: first dimension {value.shape[0]} does not match n={n}")
    if "_tick_ts" in arrays:
        tick_ts = np.asarray(arrays["_tick_ts"], dtype=np.int64).reshape(-1)
    else:
        tick_ts = ts
    if not np.array_equal(tick_ts, ts):
        raise ValueError("provided timestamps must match selected _tick_ts input when present")

    tracker = DeltaTracker(cfg)
    values = {name: np.zeros(n, dtype=bool) for name in _OUTPUT_FIELDS
              if name not in {"bull_speed", "bear_speed", "bull_tf_count", "bear_tf_count", "total_fields", "zone", "zone_action", "zone_reason", "tf_lost"}}
    values.update({name: np.zeros(n, dtype=float) for name in ("bull_speed", "bear_speed")})
    values.update({name: np.zeros(n, dtype=np.int64) for name in ("bull_tf_count", "bear_tf_count", "total_fields")})
    values.update({name: np.full(n, "", dtype=object) for name in ("zone", "zone_action", "zone_reason", "tf_lost")})
    read_keys = set()
    missing_read_keys = set()
    row_arrays = dict(arrays)
    row_arrays["_tick_ts"] = ts
    row_arrays["ts"] = ts
    class _ObservedRow(_Row):
        def get(self, key, default=None):
            read_keys.add(key)
            if key not in self.arrays:
                missing_read_keys.add(key)
            return super().get(key, default)

    for i in range(n):
        row = _ObservedRow(row_arrays, i)
        signal = tracker.update(str(symbol), row, position_states[i])
        for name in _OUTPUT_FIELDS:
            item = getattr(signal, name)
            values[name][i] = item
        if warmup[i]:
            for name in values:
                if values[name].dtype == object:
                    values[name][i] = ""
                else:
                    values[name][i] = False if values[name].dtype == bool else 0
    position_side = "LONG" if bool(is_long) else "SHORT"
    entry_ok = (values["entry_long"] | (values["zone_action"] == "BUY") | values["pyramid_long"]) if position_side == "LONG" else (values["entry_short"] | (values["zone_action"] == "SELL") | values["pyramid_short"])
    active_tfs = values["bull_tf_count"] if position_side == "LONG" else values["bear_tf_count"]
    return {
        "signals": values,
        "delta_entry_ok": entry_ok,
        "delta_active_tfs": active_tfs,
        "delta_signal_present": np.ones(n, dtype=bool),
        "tracker": tracker,
        "read_keys": tuple(sorted(read_keys)),
        "missing_read_keys": tuple(sorted(missing_read_keys)),
        "warmup_mask": warmup.copy(),
        "position_context_supplied": position_context_supplied,
        "state_scope": "fresh_tracker_replayed_from_first_supplied_row",
    }
