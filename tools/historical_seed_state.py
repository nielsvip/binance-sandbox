from datetime import datetime, timezone
import math
import numpy as np


def _epoch_seconds(value):
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(value, (int, float, np.integer, np.floating)):
        stamp = float(value)
        if not math.isfinite(stamp):
            raise ValueError("snapshot timestamp must be finite")
        return stamp / 1000.0 if abs(stamp) > 1e11 else stamp
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    raise ValueError("snapshot timestamp is missing or invalid")


def resolve_cutoff_index(timestamps, cutoff_bar_ts, tolerance_seconds=1e-6):
    values = np.asarray(timestamps, dtype=np.float64)
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("historical seed requires a finite one-dimensional timestamp array")
    if np.any(np.diff(values) <= 0):
        raise ValueError("historical seed timestamps must be strictly increasing")
    scale = 1000.0 if float(np.max(np.abs(values))) > 1e11 else 1.0
    seconds = values / scale
    target = _epoch_seconds(cutoff_bar_ts)
    idx = int(np.searchsorted(seconds, target, side="left"))
    if idx >= seconds.size or abs(float(seconds[idx]) - target) > tolerance_seconds:
        raise ValueError("historical seed cutoff is not an exact stored bar")
    return idx, float(seconds[idx])


def validate_historical_seed(seed, account, cutoff_bar_ts, bar_seconds=900.0):
    if not isinstance(seed, dict) or seed.get("snapshot_kind") != "historical_cutoff":
        raise ValueError("historical seed requires snapshot_kind=historical_cutoff")
    if str(seed.get("account", "")).strip().lower() != str(account).strip().lower():
        raise ValueError("historical seed account does not match replay account")
    expected_bar_ts = _epoch_seconds(cutoff_bar_ts)
    snapshot_bar_ts = _epoch_seconds(seed.get("as_of_bar_ts"))
    if abs(snapshot_bar_ts - expected_bar_ts) > 1e-6:
        raise ValueError("historical seed bar timestamp does not match scored cutoff")
    duration = float(bar_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("historical seed bar duration must be positive and finite")
    expected_decision_ts = expected_bar_ts + duration
    snapshot_decision_ts = _epoch_seconds(seed.get("as_of_decision_ts"))
    if abs(snapshot_decision_ts - expected_decision_ts) > 1e-6:
        raise ValueError("historical seed decision timestamp does not match completed cutoff bar")
    source = str(seed.get("source", "")).strip()
    if not source:
        raise ValueError("historical seed source provenance is required")
    positions = seed.get("positions")
    if not isinstance(positions, dict):
        raise ValueError("historical seed positions must be an object")
    lifecycle_times = (
        "opened_at",
        "last_augmentation_time",
        "last_reduction_time",
        "reduced_at",
        "last_updated",
        "mark_price_last_updated",
        "prev_gain_last_updated",
    )
    for position_key, position in positions.items():
        if not isinstance(position, dict):
            raise ValueError(f"historical seed position {position_key} must be an object")
        if float(position.get("positionAmt", 0) or 0) <= 0:
            raise ValueError(f"historical seed position {position_key} must have positive quantity")
        if float(position.get("entry_price", 0) or 0) <= 0:
            raise ValueError(f"historical seed position {position_key} must have positive entry price")
        if not position.get("opened_at"):
            raise ValueError(f"historical seed position {position_key} is missing opened_at")
        for field in lifecycle_times:
            value = position.get(field)
            if value in (None, ""):
                continue
            if _epoch_seconds(value) > expected_decision_ts + 1e-6:
                raise ValueError(f"historical seed position {position_key} has future {field}")
    return {
        "account": str(account).strip().lower(),
        "bar_ts": expected_bar_ts,
        "decision_ts": expected_decision_ts,
        "bar_seconds": duration,
        "source": source,
    }


def position_mark_at_cutoff(position, timestamps, price_at_index, cutoff_bar_ts):
    if not isinstance(position, dict):
        raise ValueError("historical seed position must be an object")
    entry = float(position.get("entry_price", 0) or 0)
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError("historical seed entry price must be positive and finite")
    idx, bar_ts = resolve_cutoff_index(timestamps, cutoff_bar_ts)
    mark = float(price_at_index(idx))
    if not math.isfinite(mark) or mark <= 0:
        raise ValueError("historical seed cutoff price must be positive and finite")
    side = str(position.get("position_side", "")).strip().upper()
    if side == "LONG":
        gain = (mark - entry) / entry * 100.0
    elif side == "SHORT":
        gain = (entry - mark) / entry * 100.0
    else:
        raise ValueError("historical seed position_side must be LONG or SHORT")
    return {"index": idx, "bar_ts": bar_ts, "mark_price": mark, "gain": gain}
