"""Exact NPZ-to-completed-snapshot adapter for Tradier V8.

This module is intentionally independent of the V8 engine bootstrap so its
causality contract can be tested without importing the long-running engine.
It reads immutable arrays only; the mark-adjusted indicator dictionary is an
output target, never an input source.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, MutableMapping


COMPLETED_SNAPSHOT_TIMEFRAMES = ("5m", "15m", "1h", "4h", "D")


def _raw(store: Any, key: str, idx: int) -> Any:
    arrays = getattr(store, "arrays", {})
    if key not in arrays:
        raise ValueError(f"MISSING_NPZ_COMPLETED_INPUT:{key}")
    values = arrays[key]
    if getattr(values, "ndim", 1) == 0 or idx < 0 or idx >= len(values):
        raise ValueError(f"MISSING_NPZ_COMPLETED_INPUT:{key}")
    value = values[idx]
    if hasattr(value, "item"):
        value = value.item()
    return value


def _number(store: Any, key: str, idx: int) -> float:
    value = _raw(store, key, idx)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_NPZ_COMPLETED_INPUT:{key}") from exc
    if not isfinite(parsed):
        raise ValueError(f"INVALID_NPZ_COMPLETED_INPUT:{key}")
    return parsed


def _source(store: Any, tf: str, idx: int) -> int:
    source = int(_number(store, f"timestamp_{tf}", idx))
    if source <= 0:
        raise ValueError(f"INVALID_NPZ_COMPLETED_INPUT:timestamp_{tf}")
    return source


def _previous_parent_index(store: Any, tf: str, idx: int, source: int) -> int:
    cache = getattr(store, "__dict__", {}).setdefault(
        "_v8_completed_previous_parent_cache", {}
    )
    cache_key = (tf, source)
    cached = cache.get(cache_key)
    if cached is not None:
        return int(cached)
    prior = idx - 1
    while prior >= 0:
        try:
            candidate = _source(store, tf, prior)
        except ValueError:
            prior -= 1
            continue
        if candidate != source:
            cache[cache_key] = prior
            return prior
        prior -= 1
    raise ValueError(f"MISSING_NPZ_PREVIOUS_PARENT:{tf}")


def _cross(store: Any, tf: str, idx: int) -> str:
    value = _raw(store, f"wt_cross_{tf}", idx)
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"BULL", "BEAR", "NONE"}:
            return normalized
        try:
            value = float(normalized)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"INVALID_NPZ_COMPLETED_INPUT:wt_cross_{tf}"
            ) from exc
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INVALID_NPZ_COMPLETED_INPUT:wt_cross_{tf}") from exc
    if not isfinite(numeric):
        raise ValueError(f"INVALID_NPZ_COMPLETED_INPUT:wt_cross_{tf}")
    return "BULL" if numeric > 0 else "BEAR" if numeric < 0 else "NONE"


def inject_completed_snapshot_v8(
    store: Any,
    idx: int,
    indicators: MutableMapping[str, Any],
    *,
    enabled: bool,
    decision_ts: int | float,
) -> dict[str, str]:
    """Inject complete exact snapshots and return per-TF omission reasons.

    A timeframe is all-or-nothing.  Any missing, invalid, future, or
    previous-parent input omits that namespace, causing live route contracts
    to fail closed rather than silently consume a generic/current-mark value.
    """
    if not enabled:
        return {}
    omissions: dict[str, str] = {}
    current_stems = (
        "open", "high", "low", "close", "stoch_k", "stoch_d",
        "wt1", "wt2", "dc_high", "dc_low", "bb_upper", "bb_lower", "atr",
    )
    direct_previous_stems = (
        "high", "low", "stoch_k", "stoch_d", "dc_high", "dc_low",
    )
    try:
        asof = int(float(decision_ts))
    except (TypeError, ValueError):
        asof = 0
    for tf in COMPLETED_SNAPSHOT_TIMEFRAMES:
        prefix = f"_completed_"
        staged: dict[str, Any] = {}
        try:
            source = _source(store, tf, idx)
            if asof <= 0 or source > asof:
                raise ValueError(f"FUTURE_NPZ_COMPLETED_INPUT:timestamp_{tf}")
            prior_idx = _previous_parent_index(store, tf, idx, source)
            prior_source = _source(store, tf, prior_idx)
            if prior_source >= source or prior_source > asof:
                raise ValueError(f"INVALID_NPZ_PREVIOUS_PARENT:timestamp_{tf}")
            staged[f"{prefix}source_ts_{tf}"] = source
            staged[f"{prefix}source_ts_{tf}_prev"] = prior_source
            for stem in current_stems:
                staged[f"{prefix}{stem}_{tf}"] = _number(
                    store, f"{stem}_{tf}", idx
                )
            for stem in direct_previous_stems:
                staged[f"{prefix}{stem}_{tf}_prev"] = _number(
                    store, f"{stem}_{tf}_prev", idx
                )
            # Frozen NPZs do not carry explicit previous-parent WT arrays.
            # Read the immutable value at the actual prior distinct source;
            # never use idx-1, which is usually the same forward-filled parent.
            for stem in ("wt1", "wt2"):
                staged[f"{prefix}{stem}_{tf}_prev"] = _number(
                    store, f"{stem}_{tf}", prior_idx
                )
            staged[f"{prefix}wt_cross_{tf}"] = _cross(store, tf, idx)
        except (TypeError, ValueError) as exc:
            omissions[tf] = str(exc)
            continue
        indicators.update(staged)
    return omissions
