"""Deterministic per-decision-bar DeltaTracker acceleration for PARABOLIC_EXIT."""

from datetime import datetime, timezone

import numpy as np

from wt_dc_delta import (
    DC_DELTA_FIELDS,
    DC_POSITION_FIELDS,
    DEFAULT_CFG,
    WT_DELTA_FIELDS,
)

WT_STATE_FIELDS = ("wt_bullish", "wt_cross_bull", "wt_cross_bear", "wt_momentum_state")
TF_FIELDS = (*WT_DELTA_FIELDS, *DC_DELTA_FIELDS, *DC_POSITION_FIELDS, *WT_STATE_FIELDS)


def _as_float(value):
    if value is None:
        return None
    if isinstance(value, str):
        states = {
            "BEAR": -1.0,
            "BULL": 1.0,
            "SHORT": -1.0,
            "LONG": 1.0,
            "RED": -1.0,
            "GREEN": 1.0,
            "LH": -1.0,
            "LL": -1.0,
            "HH": 1.0,
            "HL": 1.0,
            "CONTRACTING": -1.0,
            "EXPANDING": 1.0,
            "EXHAUST_DOWN": -2.0,
            "IMPULSE_DOWN": -1.0,
            "NEUTRAL": 0.0,
            "NONE": 0.0,
            "IMPULSE_UP": 1.0,
            "EXHAUST_UP": 2.0,
        }
        normalized = value.strip().upper()
        if normalized in states:
            return states[normalized]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_bar_timestamp(value):
    """Normalize the completed-bar timestamp; missing values are unsupported."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    raise ValueError("PARABOLIC_DELTA needs a completed-bar timestamp")


def _read_at(npz, key, index):
    value = npz.get(key)
    if value is None:
        return None
    try:
        return value[index]
    except (IndexError, TypeError):
        return None


def snapshot_from_npz(npz, index, ltf_enabled=False):
    """Read only fields consumed by DeltaTracker.update, pairing 3m to 15m off-LTF."""
    snapshot = {}
    for field in TF_FIELDS:
        for tf in ("3m", "15m", "1h", "4h", "D"):
            source_tf = "3m" if tf == "3m" and ltf_enabled else ("15m" if tf == "3m" else tf)
            current_key = f"{field}_{source_tf}"
            current = _read_at(npz, current_key, index)
            if current is not None:
                snapshot[f"{field}_{tf}"] = current
            if field in WT_DELTA_FIELDS:
                previous = _read_at(npz, f"{field}_prev_{source_tf}", index)
                if previous is not None:
                    snapshot[f"{field}_prev_{tf}"] = previous
            elif field in DC_DELTA_FIELDS:
                previous = _read_at(npz, f"{field}_{source_tf}_prev", index)
                if previous is not None:
                    snapshot[f"{field}_{tf}_prev"] = previous
    return snapshot


def validate_npz_inputs(npz, n, ltf_enabled=False):
    """Require the actual field families used by the scalar DeltaTracker update."""
    missing = []
    tfs = ("3m", "15m", "1h", "4h", "D")
    for tf in tfs:
        source_tf = "3m" if tf == "3m" and ltf_enabled else ("15m" if tf == "3m" else tf)
        for field in TF_FIELDS:
            key = f"{field}_{source_tf}"
            value = npz.get(key)
            if value is None or getattr(value, "__len__", None) is None or len(value) != n:
                missing.append(key)
    ts_key = "timestamps" if npz.get("timestamps") is not None else "timestamp_15m"
    ts_values = npz.get(ts_key)
    if ts_values is None or getattr(ts_values, "__len__", None) is None or len(ts_values) != n:
        missing.append("timestamps_or_timestamp_15m")
        for field in DC_DELTA_FIELDS:
            key = f"{field}_{source_tf}_prev"
            value = npz.get(key)
            if value is None or getattr(value, "__len__", None) is None or len(value) != n:
                missing.append(key)
    if missing:
        raise ValueError(f"PARABOLIC_DELTA missing NPZ fields: {','.join(sorted(set(missing)))}")
    return True


def step(state, indicators, bar_timestamp, tf_weights=None, ltf_enabled=False):
    """Advance the scalar DeltaTracker speed comparison once per completed bar.

    The same bar returns its cached result. State contains previous per-field
    values and the prior aggregate speeds; callers scope it to one symbol/side
    position lifecycle.
    """
    timestamp = normalize_bar_timestamp(bar_timestamp)
    if state.get("last_bar_timestamp") == timestamp:
        return state["last_result"]
    snapshot = dict(indicators or {})
    if not ltf_enabled:
        for field in TF_FIELDS:
            current = snapshot.get(f"{field}_15m")
            previous = snapshot.get(f"{field}_prev_15m")
            dc_previous = snapshot.get(f"{field}_15m_prev")
            if current is not None:
                snapshot[f"{field}_3m"] = current
            if previous is not None:
                snapshot[f"{field}_prev_3m"] = previous
            if dc_previous is not None:
                snapshot[f"{field}_3m_prev"] = dc_previous
    weights = tf_weights or DEFAULT_CFG["tf_weights"]
    fields_before = state.setdefault("previous_fields", {})
    updates = {}
    total_bull = 0.0
    total_bear = 0.0
    for tf, weight in weights.items():
        tf_bull = 0.0
        tf_bear = 0.0
        count = 0
        for field in WT_DELTA_FIELDS:
            key = f"{field}_{tf}"
            current = _as_float(snapshot.get(key))
            if current is None:
                continue
            previous = _as_float(snapshot.get(f"{field}_prev_{tf}"))
            if previous is None:
                previous = fields_before.get(key)
            if previous is not None:
                delta = current - previous
                if delta > 0:
                    tf_bull += delta
                else:
                    tf_bear += -delta
                count += 1
            updates[key] = current
        for field in DC_DELTA_FIELDS:
            current = _as_float(snapshot.get(f"{field}_{tf}"))
            previous = _as_float(snapshot.get(f"{field}_{tf}_prev"))
            if current is not None and previous is not None:
                delta = current - previous
                if delta > 0:
                    tf_bull += delta
                else:
                    tf_bear += -delta
                count += 1
        for field in DC_POSITION_FIELDS:
            key = f"{field}_{tf}"
            current = _as_float(snapshot.get(key))
            if current is not None:
                previous = fields_before.get(key)
                if previous is not None:
                    delta = current - previous
                    if delta > 0:
                        tf_bull += delta
                    else:
                        tf_bear += -delta
                    count += 1
                updates[key] = current
        for field in WT_STATE_FIELDS:
            key = f"{field}_{tf}"
            current = _as_float(snapshot.get(key))
            if current is not None:
                previous = fields_before.get(key)
                if previous is not None:
                    delta = current - previous
                    if delta > 0:
                        tf_bull += delta
                    else:
                        tf_bear += -delta
                    count += 1
                updates[key] = current
        if count > 0:
            tf_bull /= count
            tf_bear /= count
        total_bull += tf_bull * float(weight)
        total_bear += tf_bear * float(weight)
    previous_bull = float(state.get("previous_total_bull", 0.0))
    previous_bear = float(state.get("previous_total_bear", 0.0))
    result = {
        "bull_accel": bool(total_bull > previous_bull * 1.05),
        "bear_accel": bool(total_bear > previous_bear * 1.05),
        "total_bull": total_bull,
        "total_bear": total_bear,
    }
    fields_before.update(updates)
    state["previous_total_bull"] = total_bull
    state["previous_total_bear"] = total_bear
    state["last_bar_timestamp"] = timestamp
    state["last_result"] = result
    return result


def step_npz(state, npz, index, bar_timestamp, tf_weights=None, ltf_enabled=False):
    return step(
        state,
        snapshot_from_npz(npz, index, ltf_enabled),
        bar_timestamp,
        tf_weights,
        ltf_enabled,
    )
