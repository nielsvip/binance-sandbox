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
        item = value[self.index] if isinstance(value, np.ndarray) else value
        return item.item() if hasattr(item, "item") else item

    def __iter__(self):
        return iter(self.arrays)

    def __len__(self):
        return len(self.arrays)

    def get(self, key, default=None):
        if key not in self.arrays:
            return default
        return self[key]




class DeltaTrackerStream:
    """Own one native tracker and advance it once per supplied decision row."""

    def __init__(self, symbol, cfg, is_long):
        from wt_dc_delta import DeltaTracker

        self.symbol = str(symbol)
        self.cfg = cfg
        self.is_long = bool(is_long)
        self.tracker = DeltaTracker(cfg)
        self.last_timestamp = None
        self.update_count = 0

    def update(self, indicators, position_state=None):
        if not isinstance(indicators, Mapping):
            raise ValueError("indicators must be an explicit mapping")
        raw_ts = indicators.get("_tick_ts", indicators.get("ts"))
        if raw_ts is None:
            raise ValueError("DeltaTrackerStream requires explicit _tick_ts or ts")
        timestamp = int(float(raw_ts))
        if self.last_timestamp is not None and timestamp < self.last_timestamp:
            raise ValueError("DeltaTrackerStream timestamp moved backward")
        signal = self.tracker.update(self.symbol, indicators, position_state)
        self.last_timestamp = timestamp
        self.update_count += 1
        active_tfs = signal.bull_tf_count if self.is_long else signal.bear_tf_count
        entry_ok = (signal.entry_long or signal.zone_action == "BUY" or signal.pyramid_long) if self.is_long else (signal.entry_short or signal.zone_action == "SELL" or signal.pyramid_short)
        return {
            "signal": signal, "delta_signal_present": True,
            "delta_entry_ok": bool(entry_ok), "delta_active_tfs": int(active_tfs),
            "timestamp": timestamp, "update_count": self.update_count,
        }

def native_delta_tracker_config(config):
    """Build the exact DeltaTracker kwargs from FastDataManager.__init__."""
    return {
        "tf_weights": config.DELTA_TF_WEIGHTS,
        "entry_min_tf": config.DELTA_ENTRY_MIN_TF,
        "entry_speed_threshold": getattr(config, "DELTA_ENTRY_SPEED_THRESHOLD", 0.5),
        "exit_speed_decay_pct": getattr(config, "DELTA_EXIT_SPEED_DECAY_PCT", 50.0),
        "exit_min_tf_lost": config.DELTA_EXIT_MIN_TF_LOST,
        "exit_min_hold": getattr(config, "MIN_HOLD_BARS", 3),
        "pyramid_price_tolerance": config.DELTA_PYRAMID_PRICE_TOL,
        "pyramid_qty_mult": config.DELTA_PYRAMID_QTY_MULT,
        "pyramid_max": config.DELTA_PYRAMID_MAX,
        "rz_entry_enabled": config.RZ_ENTRY_ENABLED,
        "rz_exit_enabled": config.RZ_EXIT_ENABLED,
        "rz_top_bb": config.RZ_TOP_BB_THRESHOLD,
        "rz_bot_bb": config.RZ_BOT_BB_THRESHOLD,
        "rz_legs_min": config.RZ_LEGS_MIN,
        "rz_require_struct": config.RZ_REQUIRE_STRUCT,
        "rz_k_exit": config.RZ_K_EXIT,
        "rz_mfi_exit": config.RZ_MFI_EXIT,
        "rz_k_entry_max": config.RZ_K_ENTRY_MAX,
        "rz_baseline_bounce_short_enabled": getattr(config, "RZ_BASELINE_BOUNCE_SHORT_ENABLED", True),
        "exit_dominant_tf": (getattr(config, "DELTA_EXIT_TF", "15m") if getattr(config, "DELTA_EXIT_DOM_TF_ENABLED", False) else "ANY"),
        "structural_exit_gate_enabled": getattr(config, "STRUCTURAL_EXIT_GATE_ENABLED", True),
    }

def replay_delta_tracker_arrays(npz, *, symbol, cfg, use_ltf, timestamps,
                               is_long, position_states=None, warmup_mask=None,
                               external_context=None, producer_rows=None):
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

    producer_missing = set()
    if producer_rows is not None:
        if len(producer_rows) != n or any(not isinstance(row, Mapping) for row in producer_rows):
            raise ValueError("producer_rows must contain one indicator mapping per timestamp")
        producer_keys = set().union(*(set(row) for row in producer_rows))
        producer_missing = {key for key in producer_keys if any(key not in row for row in producer_rows)}
        selected = {key: np.asarray([row.get(key) for row in producer_rows], dtype=object)
                    for key in producer_keys if key not in ("timestamps",)}
        arrays = selected
    else:
        selected = paired_npz_view(npz, bool(use_ltf))
        arrays = decode_live_indicator_labels(selected)
        arrays = {key: np.asarray(value) for key, value in arrays.items()
                  if isinstance(value, (np.ndarray, list, tuple)) and np.asarray(value).ndim > 0
                  and np.asarray(value).shape[0] == n}
    if "_tick_ts" in arrays:
        tick_ts = np.asarray(arrays["_tick_ts"], dtype=np.int64).reshape(-1)
    else:
        tick_ts = ts
    if not np.array_equal(tick_ts, ts):
        raise ValueError("provided timestamps must match selected _tick_ts input when present")

    if external_context is None:
        external_context = {}
    if not isinstance(external_context, Mapping):
        raise ValueError("external_context must be an explicit mapping")
    external_arrays = {}
    for key, raw in external_context.items():
        value = np.asarray(raw)
        if value.ndim == 0:
            value = np.full(n, value.item())
        else:
            value = value.reshape(-1)
        if value.size != n:
            raise ValueError(f"external context {key} length {value.size} does not match n={n}")
        external_arrays[str(key)] = value
    if (cfg.get("rz_entry_enabled", True) or cfg.get("rz_exit_enabled", True)) and "current_price" not in arrays and "current_price" not in external_arrays:
        raise ValueError("active DeltaTracker red-zone path requires explicit current_price input")
    arrays.update(external_arrays)

    tracker = DeltaTracker(cfg)
    values = {name: np.zeros(n, dtype=bool) for name in _OUTPUT_FIELDS
              if name not in {"bull_speed", "bear_speed", "bull_tf_count", "bear_tf_count", "total_fields", "zone", "zone_action", "zone_reason", "tf_lost"}}
    values.update({name: np.zeros(n, dtype=float) for name in ("bull_speed", "bear_speed")})
    values.update({name: np.zeros(n, dtype=np.int64) for name in ("bull_tf_count", "bear_tf_count", "total_fields")})
    values.update({name: np.full(n, "", dtype=object) for name in ("zone", "zone_action", "zone_reason", "tf_lost")})
    read_keys = set()
    missing_read_keys = set(producer_missing)
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
