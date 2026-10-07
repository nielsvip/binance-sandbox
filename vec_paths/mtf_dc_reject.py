"""Fail-closed vector companion for the exact/live MTF Donchian reject exit.

The live/exact route is independent of ``MTF_ARMED_ENTRY_ENABLED``: any open
position may arm outside the latest causally available parent Donchian band and
close after a re-cross within the configured timeframe-sized window. This
module gives ordinary vector entries that same state machine without enabling
the other MTF compound siblings.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mtf_exit_timing import dc_reject_step


SUPPORTED_DC_TIMEFRAMES = frozenset({"15m", "1h", "4h", "D"})


def build_direct_dc_reject_arrays(
    npz: dict[str, Any],
    n: int,
    is_long: bool,
    config: Any,
) -> dict[str, Any]:
    """Validate and expose direct DC inputs without zero-filled fallback."""
    requested = bool(getattr(config, "MTF_EXIT_USE_COMPOUND", False)) and bool(
        getattr(config, "MTF_DC_REJECT_EXIT_ENABLED", False)
    )
    result: dict[str, Any] = {
        "requested": requested,
        "enabled": False,
        "failures": (),
    }
    if not requested:
        return result

    failures: list[str] = []
    timeframe = str(getattr(config, "MTF_DC_REJECT_EXIT_TF", "") or "")
    try:
        lookback = int(getattr(config, "MTF_DC_REJECT_EXIT_LOOKBACK"))
    except (AttributeError, TypeError, ValueError):
        lookback = 0
    if timeframe not in SUPPORTED_DC_TIMEFRAMES:
        failures.append(f"unsupported_dc_timeframe:{timeframe or 'MISSING'}")
    if lookback < 1:
        failures.append("dc_lookback_must_be_positive")

    band_key = f"dc_{'high' if is_long else 'low'}_{timeframe}"
    raw_band = npz.get(band_key)
    raw_ts = npz.get("timestamps")
    if raw_band is None:
        failures.append(f"dc_band_missing:{band_key}")
    if raw_ts is None:
        failures.append("timestamps_missing")

    band = np.asarray(raw_band) if raw_band is not None else np.empty(0)
    timestamps = np.asarray(raw_ts) if raw_ts is not None else np.empty(0)
    if band.ndim != 1 or len(band) != n:
        failures.append(f"dc_band_length_mismatch:{band_key}")
    if timestamps.ndim != 1 or len(timestamps) != n:
        failures.append("timestamps_length_mismatch")

    if not failures:
        try:
            timestamps = timestamps.astype(np.float64, copy=False)
            band = band.astype(np.float64, copy=False)
        except (TypeError, ValueError):
            failures.append("dc_inputs_not_numeric")
    if not failures:
        if not np.all(np.isfinite(timestamps)) or np.any(timestamps <= 0):
            failures.append("timestamps_invalid")
        elif n > 1 and np.any(np.diff(timestamps) <= 0):
            failures.append("timestamps_not_strictly_increasing")
        if not np.any(np.isfinite(band) & (band > 0)):
            failures.append(f"dc_band_has_no_positive_values:{band_key}")

    # Repaired archives may carry explicit completed-parent provenance. If it
    # exists, a future parent source invalidates the entire screen.
    source_key = f"_completed_source_ts_{timeframe}"
    raw_source = npz.get(source_key)
    if not failures and raw_source is not None:
        source = np.asarray(raw_source)
        if source.ndim != 1 or len(source) != n:
            failures.append(f"completed_source_length_mismatch:{source_key}")
        else:
            try:
                source = source.astype(np.float64, copy=False)
            except (TypeError, ValueError):
                failures.append(f"completed_source_not_numeric:{source_key}")
            else:
                positive = source > 0
                if np.any(~np.isfinite(source[positive])):
                    failures.append(f"completed_source_invalid:{source_key}")
                elif np.any(source[positive] > timestamps[positive]):
                    failures.append(f"future_completed_source:{source_key}")

    if failures:
        result["failures"] = tuple(failures)
        return result
    result.update(
        enabled=True,
        timeframe=timeframe,
        lookback=lookback,
        band_key=band_key,
        timestamps=timestamps,
        band=np.where(np.isfinite(band) & (band > 0), band, 0.0),
    )
    return result


def direct_dc_reject_step(
    arrays: dict[str, Any],
    i: int,
    *,
    price: float,
    outside_ts: float,
    is_long: bool,
) -> tuple[float, bool]:
    """Advance one ordinary-position tick through the shared exact helper."""
    if not arrays.get("enabled"):
        return float(outside_ts or 0), False
    timestamps = arrays["timestamps"]
    band = arrays["band"]
    if i < 0 or i >= len(timestamps) or i >= len(band):
        return float(outside_ts or 0), False
    return dc_reject_step(
        outside_ts,
        now_ts=float(timestamps[i]),
        price=price,
        band=float(band[i]),
        lookback_bars=int(arrays["lookback"]),
        timeframe=str(arrays["timeframe"]),
        is_long=is_long,
    )
