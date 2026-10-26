"""Project-only BB-width candidate for DELTA_GATE_BB_SQUEEZE.

This module is deliberately not imported by a live manager or quick engine.
Config comments define the BB squeeze score as band width divided by price,
and the 15m/1h thresholds as strict ``score < threshold`` entry conditions.
The candidate derives that score from causal band arrays. The live delta
confirmation state has no frozen-NPZ twin, so the combined veto returns None
unless its caller supplies an explicit confirmation mask.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def _get(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, Mapping):
        return config.get(key, default)
    return getattr(config, key, default)


def _series(source: Mapping[str, Any], key: str, n: int) -> np.ndarray | None:
    try:
        if key not in source:
            return None
        values = np.asarray(source[key], dtype=float).reshape(-1)
        if values.size < n:
            return None
        return values[:n]
    except (TypeError, ValueError, KeyError):
        return None


def bb_squeeze_width_ratio(npz: Mapping[str, Any], n: int, timeframe: str) -> np.ndarray | None:
    """Return causal (upper-lower)/midpoint ratio; absent/invalid bands -> None."""
    upper = _series(npz, f"bb_upper_{timeframe}", n)
    lower = _series(npz, f"bb_lower_{timeframe}", n)
    if upper is None or lower is None:
        return None
    midpoint = (upper + lower) / 2.0
    valid = np.isfinite(upper) & np.isfinite(lower) & (midpoint > 0) & (upper >= lower)
    if not np.all(valid):
        return None
    return (upper - lower) / midpoint


def bb_squeeze_threshold_candidates(
    npz: Mapping[str, Any], n: int, config: Any
) -> dict[str, np.ndarray | None]:
    """Return separate 15m/1h threshold masks; no default values are promoted."""
    result: dict[str, np.ndarray | None] = {}
    for timeframe in ("15m", "1h"):
        ratio = bb_squeeze_width_ratio(npz, n, timeframe)
        try:
            threshold = float(_get(config, f"BB_SQUEEZE_THRESHOLD_{timeframe.upper()}", None))
        except (TypeError, ValueError):
            result[timeframe] = None
            continue
        if ratio is None or not np.isfinite(threshold) or threshold <= 0:
            result[timeframe] = None
            continue
        result[timeframe] = ratio < threshold
    return result


def delta_gate_bb_squeeze_candidate_masks(
    npz: Mapping[str, Any],
    n: int,
    config: Any,
    delta_confirmed: Any = None,
) -> dict[str, np.ndarray | None]:
    """Per-timeframe candidate veto masks; no unmeasured cross-TF combination.

    Each mask vetoes entries when that timeframe is squeezed and explicit delta
    confirmation is false. The function is not wired to production because the
    frozen NPZ currently lacks the delta confirmation series used by the live
    tracker. Returning separate masks leaves their eventual combination to
    measured switch options rather than assuming AND/OR semantics here.
    """
    try:
        if not bool(_get(config, "DELTA_GATE_BB_SQUEEZE", True)):
            return {"15m": None, "1h": None}
        candidates = bb_squeeze_threshold_candidates(npz, n, config)
        if delta_confirmed is None:
            return {"15m": None, "1h": None}
        confirmed = np.asarray(delta_confirmed, dtype=bool).reshape(-1)
        if confirmed.size < n:
            return {"15m": None, "1h": None}
        return {
            timeframe: None if squeeze is None else squeeze & ~confirmed[:n]
            for timeframe, squeeze in candidates.items()
        }
    except (TypeError, ValueError):
        return {"15m": None, "1h": None}
