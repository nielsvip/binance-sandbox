"""Causal per-decision adapter for the composed ASR entry candidate scorer."""

from collections.abc import Mapping

import numpy as np

from vec_decisions.asr_ordered_composer import (
    assemble_asr_composer_inputs,
    bind_asr_npz_arrays,
    compose_asr_entry_candidate_array,
)
from vec_decisions.asr_entry_prefix import score_asr_entry_prefix_array


def _cfg(cfg, key, default=None):
    return cfg.get(key, default) if isinstance(cfg, Mapping) else getattr(cfg, key, default)


def _one_row(value, key):
    if isinstance(value, Mapping):
        return {name: _one_row(item, f"{key}.{name}") for name, item in value.items()}
    array = np.asarray(value)
    if array.ndim == 0:
        return array.reshape(1)
    array = array.reshape(-1)
    if array.size != 1:
        raise ValueError(f"{key} must describe exactly one decision row; got {array.size}")
    return array


class AsrEntryStream:
    """Prepare decoded NPZ arrays once and score sequential causal decisions.

    The caller supplies lifecycle, account, portfolio, regime, and cross-symbol
    context for each decision. When DeltaTracker is enabled, this object owns one
    native tracker instance and advances it once for each accepted index.
    """

    def __init__(self, npz, cfg, symbol, is_long, delta_tracker_cfg=None):
        if not isinstance(npz, Mapping):
            raise ValueError("npz must be an explicit mapping of frozen arrays")
        self.cfg = cfg
        self.symbol = str(symbol)
        self.is_long = bool(is_long)
        self.arrays = bind_asr_npz_arrays(npz, cfg)
        raw_ts = self.arrays.get("timestamps", self.arrays.get("_tick_ts"))
        if raw_ts is None:
            raise ValueError("ASR entry stream requires a timestamp array")
        self.timestamps = np.asarray(raw_ts, dtype=np.int64).reshape(-1)
        self.n = self.timestamps.size
        if self.n == 0 or np.any(np.diff(self.timestamps) < 0):
            raise ValueError("ASR entry stream requires non-empty ordered timestamps")
        for key, raw in self.arrays.items():
            array = np.asarray(raw)
            if array.ndim and array.shape[0] not in (1, self.n):
                raise ValueError(f"NPZ field {key} length {array.shape[0]} does not match {self.n} or a singleton static field")
        self.delta_stream = None
        if bool(_cfg(cfg, "DELTA_ENGINE_ENABLED", False)):
            if delta_tracker_cfg is None:
                raise ValueError("DELTA_ENGINE_ENABLED requires explicit delta_tracker_cfg")
            from vec_decisions.asr_delta_provider import DeltaTrackerNpZStream
            self.delta_stream = DeltaTrackerNpZStream(
                self.symbol, delta_tracker_cfg, self.is_long, npz,
                bool(_cfg(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)),
            )
        self.last_index = -1
        self.failed = False

    def _npz_row(self, index):
        row = {}
        for key, raw in self.arrays.items():
            array = np.asarray(raw)
            if array.ndim == 0:
                row[key] = array
            elif array.shape[0] == self.n:
                row[key] = array[index:index + 1]
            elif array.shape[0] == 1:
                row[key] = array.copy()
        row["timestamps"] = self.timestamps[index:index + 1]
        return row

    def update(self, index, prefix_context, late_causal_state, upstream_entry_state,
               lifecycle_state, position_state=None):
        index = int(index)
        if self.failed:
            raise RuntimeError("ASR entry stream is unusable after a failed update")
        if index != self.last_index + 1:
            raise ValueError(f"ASR entry stream requires sequential index {self.last_index + 1}, got {index}")
        if index < 0 or index >= self.n:
            raise IndexError(f"ASR entry stream index {index} outside [0,{self.n})")
        if not all(isinstance(value, Mapping) for value in
                   (prefix_context, late_causal_state, upstream_entry_state, lifecycle_state)):
            raise ValueError("ASR entry stream requires explicit prefix, late, upstream, and lifecycle mappings")
        if "is_hedge" not in prefix_context:
            raise ValueError("ASR entry stream requires explicit prefix_context.is_hedge")
        if "bounce_reentry_k_reset" not in prefix_context and not any(
            key in late_causal_state for key in ("true_lag", "prev_cross_price", "bounce_reentry_k_reset")
        ):
            raise ValueError("ASR entry stream requires persisted bounce_reentry_k_reset state")
        if "current_price" not in prefix_context or "current_price" not in late_causal_state:
            raise ValueError("ASR entry stream requires current_price in prefix and late context")
        row_npz = self._npz_row(index)
        prefix = {key: _one_row(value, f"prefix_context.{key}") for key, value in prefix_context.items()}
        late = {key: _one_row(value, f"late_causal_state.{key}")
                for key, value in late_causal_state.items() if key not in ("post_late_entry_state", "lifecycle_state")}
        life = {key: _one_row(value, f"lifecycle_state.{key}") for key, value in lifecycle_state.items()}
        try:
            price = np.asarray(late["current_price"], dtype=float).reshape(1)
            if not np.array_equal(np.asarray(prefix["current_price"], dtype=float).reshape(1), price, equal_nan=True):
                raise ValueError("prefix and late current_price must match")
            late["lifecycle_state"] = life
            late["score_before_dc"] = np.zeros(1, dtype=float)
            reentry_keys = ("true_lag", "prev_cross_price", "bounce_reentry_k_reset")
            if any(key in late for key in reentry_keys):
                missing = [key for key in reentry_keys if key not in late]
                if missing:
                    raise ValueError(f"incomplete reentry binding: {','.join(missing)}")
                from vec_decisions.asr_reentry_inputs import bind_asr_reentry_inputs
                reentry = bind_asr_reentry_inputs(
                    row_npz, self.cfg, life, 1, self.is_long, price, late["true_lag"],
                    late["prev_cross_price"], late["bounce_reentry_k_reset"], arrays_bound=True,
                )
                prefix.update(reentry["context"])
                prefix["htf_trend"] = reentry["htf_trend"]
                prefix["htf_trend_score"] = reentry["htf_trend_score"]
            prefix_result = score_asr_entry_prefix_array(
                row_npz, 1, self.is_long, self.cfg, prefix, arrays_bound=True
            )
            late["score_before_dc"] = prefix_result["score"]
            late_inputs = assemble_asr_composer_inputs(
                row_npz, self.cfg, late, 1, self.is_long, arrays_bound=True
            )
            post_late = late_causal_state.get("post_late_entry_state")
            post_late = {} if post_late is None else {
                key: _one_row(value, f"post_late_entry_state.{key}")
                for key, value in post_late.items()
            }
            if self.delta_stream is not None:
                delta = self.delta_stream.update_index(index, position_state, float(price[0]))
                signal = delta["signal"]
                post_late.update({
                    "current_price": price,
                    "delta_tracker_available": np.ones(1, dtype=bool),
                    "delta_signal_present": np.ones(1, dtype=bool),
                    "delta_entry_ok": np.asarray([delta["delta_entry_ok"]], dtype=bool),
                    "delta_active_tfs": np.asarray([delta["delta_active_tfs"]], dtype=float),
                    "delta_tracker_state_scope": "incremental_native_tracker",
                    "delta_tracker_signal": signal,
                })
            result = compose_asr_entry_candidate_array(
                prefix_result, late_inputs, self.is_long, upstream_entry_state,
                post_late_entry_state=post_late if post_late else None,
                lifecycle_state=life,
            )
            result["index"] = index
            result["timestamp"] = int(self.timestamps[index])
            result["complete_rate"] = False
            self.last_index = index
            return result
        except Exception:
            self.failed = True
            raise
