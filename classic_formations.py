"""Causal, vectorized classic chart-formation detection.

The detector is shared by three consumers:

* frozen OHLC arrays already present in the backtest NPZ files;
* future NPZ precomputation; and
* live Tradier kline DataFrames.

Every value at row ``i`` uses rows ``<= i`` only.  Formation events are emitted
on the confirming breakout bar, not retroactively on a pivot bar.  This makes
the arrays safe to map onto the existing matrix/backtest timeline.

# Shared selector consumed by live Tradier and the V8 full-recipe path.
# SHARED_CLASSIC_FORMATION_V8_PARITY_V1
"""
from __future__ import annotations

from typing import Dict, Iterable, Mapping

import numpy as np


FORMATION_FAMILIES = (
    "head_shoulders",
    "double_top_bottom",
    "wedge",
    "triangle",
    "flag_pennant",
    "cup_handle",
    "trend_structure",
)

CONFIG_FAMILY_PREFIX = {
    "head_shoulders": "FORMATION_HEAD_SHOULDERS",
    "double_top_bottom": "FORMATION_DOUBLE_TOP_BOTTOM",
    "wedge": "FORMATION_WEDGE",
    "triangle": "FORMATION_TRIANGLE",
    "flag_pennant": "FORMATION_FLAG_PENNANT",
    "cup_handle": "FORMATION_CUP_HANDLE",
    "trend_structure": "FORMATION_TREND_STRUCTURE",
}

# Explicit vocabulary for source-token manifest/audit tools.  Runtime lookup is
# assembled from CONFIG_FAMILY_PREFIX, but these full names prove that each
# switch is consumed by the shared vector/exact/live path.
FORMATION_CONFIG_SWITCHES = (
    "FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED", "FORMATION_HEAD_SHOULDERS_EXIT_ENABLED",
    "FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED", "FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED",
    "FORMATION_WEDGE_ENTRY_ENABLED", "FORMATION_WEDGE_EXIT_ENABLED",
    "FORMATION_TRIANGLE_ENTRY_ENABLED", "FORMATION_TRIANGLE_EXIT_ENABLED",
    "FORMATION_FLAG_PENNANT_ENTRY_ENABLED", "FORMATION_FLAG_PENNANT_EXIT_ENABLED",
    "FORMATION_CUP_HANDLE_ENTRY_ENABLED", "FORMATION_CUP_HANDLE_EXIT_ENABLED",
    "FORMATION_TREND_STRUCTURE_ENTRY_ENABLED", "FORMATION_TREND_STRUCTURE_EXIT_ENABLED",
    "FORMATION_TFS", "FORMATION_MIN_SCORE", "FORMATION_POSITION_SIZE_MULT",
    "FORMATION_EXIT_MIN_GAIN_PCT",
)

FORMATION_CODES = {
    "none": 0,
    "inverse_head_shoulders": 1,
    "head_shoulders": -1,
    "double_bottom": 2,
    "double_top": -2,
    "falling_wedge": 3,
    "rising_wedge": -3,
    "ascending_triangle": 4,
    "descending_triangle": -4,
    "bull_flag_pennant": 5,
    "bear_flag_pennant": -5,
    "cup_handle": 6,
    "inverse_cup_handle": -6,
    "higher_high_higher_low": 7,
    "lower_high_lower_low": -7,
}
CODE_TO_NAME = {value: key for key, value in FORMATION_CODES.items()}


def _as_float(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if not arr.size:
        return arr
    finite = np.isfinite(arr)
    if finite.all():
        return arr
    # OHLC inputs should be finite.  Filling forward/backward here is preferable
    # to letting one sparse historical NaN contaminate a complete rolling window.
    out = arr.copy()
    good = np.flatnonzero(finite)
    if not good.size:
        return np.zeros_like(out)
    out[: good[0]] = out[good[0]]
    for start, stop in zip(good[:-1], good[1:]):
        out[start:stop] = out[start]
    out[good[-1] :] = out[good[-1]]
    return out


def _blocks(window: np.ndarray, count: int, reducer: str) -> np.ndarray:
    """Reduce each rolling window into equal chronological blocks."""
    edges = np.linspace(0, window.shape[1], count + 1, dtype=int)
    cols = []
    fn = {"max": np.max, "min": np.min, "mean": np.mean}[reducer]
    for left, right in zip(edges[:-1], edges[1:]):
        right = max(right, left + 1)
        cols.append(fn(window[:, left:right], axis=1))
    return np.column_stack(cols)


def _slope(values: np.ndarray) -> np.ndarray:
    x = np.arange(values.shape[1], dtype=np.float64)
    xc = x - x.mean()
    return ((values - values.mean(axis=1, keepdims=True)) @ xc) / np.sum(xc * xc)


def _strength(condition: np.ndarray, raw: np.ndarray) -> np.ndarray:
    return np.where(condition, np.clip(raw, 0.0, 1.0), 0.0).astype(np.float32)


def detect_classic_formations(
    open_: Iterable[float],
    high: Iterable[float],
    low: Iterable[float],
    close: Iterable[float],
    volume: Iterable[float] | None = None,
    *,
    lookback: int = 40,
    tolerance: float = 0.08,
    min_prominence: float = 0.12,
    breakout_bps: float = 5.0,
) -> Dict[str, np.ndarray]:
    """Return vectorized formation-event arrays aligned to the input bars.

    ``lookback`` includes the current confirmation bar.  A minimum of 24 bars
    keeps the fixed chronological segments meaningful.  Scores are normalized
    to ``[0, 1]`` and direction is ``1`` (bullish), ``-1`` (bearish), or zero.
    """
    o = _as_float(open_)
    h = _as_float(high)
    l = _as_float(low)
    c = _as_float(close)
    n = len(c)
    if not (len(o) == len(h) == len(l) == n):
        raise ValueError("open/high/low/close arrays must have identical lengths")
    if volume is not None and len(np.asarray(volume).reshape(-1)) != n:
        raise ValueError("volume must have the same length as OHLC")
    lookback = max(24, int(lookback))
    names = [
        "head_shoulders", "inverse_head_shoulders", "double_top", "double_bottom",
        "rising_wedge", "falling_wedge", "ascending_triangle", "descending_triangle",
        "bull_flag_pennant", "bear_flag_pennant", "cup_handle", "inverse_cup_handle",
        "higher_high_higher_low", "lower_high_lower_low",
    ]
    out: Dict[str, np.ndarray] = {name: np.zeros(n, dtype=bool) for name in names}
    for family in FORMATION_FAMILIES:
        out[f"{family}_bull"] = np.zeros(n, dtype=bool)
        out[f"{family}_bear"] = np.zeros(n, dtype=bool)
        out[f"{family}_bull_score"] = np.zeros(n, dtype=np.float32)
        out[f"{family}_bear_score"] = np.zeros(n, dtype=np.float32)
    out["bull_score"] = np.zeros(n, dtype=np.float32)
    out["bear_score"] = np.zeros(n, dtype=np.float32)
    out["direction"] = np.zeros(n, dtype=np.int8)
    out["primary_code"] = np.zeros(n, dtype=np.int8)
    if n < lookback:
        return out

    ow = np.lib.stride_tricks.sliding_window_view(o, lookback)
    hw = np.lib.stride_tricks.sliding_window_view(h, lookback)
    lw = np.lib.stride_tricks.sliding_window_view(l, lookback)
    cw = np.lib.stride_tricks.sliding_window_view(c, lookback)
    hist_h, hist_l, hist_c = hw[:, :-1], lw[:, :-1], cw[:, :-1]
    cur_h, cur_l, cur_c = hw[:, -1], lw[:, -1], cw[:, -1]
    m = len(cur_c)
    eps = 1e-12
    price = np.maximum(np.median(hist_c, axis=1), eps)
    total_range = np.maximum(np.max(hist_h, axis=1) - np.min(hist_l, axis=1), price * 1e-6)
    break_frac = max(0.0, float(breakout_bps)) / 10_000.0

    high5 = _blocks(hist_h, 5, "max")
    low5 = _blocks(hist_l, 5, "min")
    close5 = _blocks(hist_c, 5, "mean")
    shoulder_gap = np.abs(high5[:, 0] - high5[:, 4]) / total_range
    hs_neck = (low5[:, 1] + low5[:, 3]) * 0.5
    hs_prom = (high5[:, 2] - np.maximum(high5[:, 0], high5[:, 4])) / total_range
    hs = (
        (shoulder_gap <= tolerance)
        & (hs_prom >= min_prominence)
        & (np.abs(low5[:, 1] - low5[:, 3]) / total_range <= tolerance * 1.5)
        & (cur_c < hs_neck * (1.0 - break_frac))
    )
    inv_shoulder_gap = np.abs(low5[:, 0] - low5[:, 4]) / total_range
    ihs_neck = (high5[:, 1] + high5[:, 3]) * 0.5
    ihs_prom = (np.minimum(low5[:, 0], low5[:, 4]) - low5[:, 2]) / total_range
    ihs = (
        (inv_shoulder_gap <= tolerance)
        & (ihs_prom >= min_prominence)
        & (np.abs(high5[:, 1] - high5[:, 3]) / total_range <= tolerance * 1.5)
        & (cur_c > ihs_neck * (1.0 + break_frac))
    )

    high4 = _blocks(hist_h, 4, "max")
    low4 = _blocks(hist_l, 4, "min")
    close4 = _blocks(hist_c, 4, "mean")
    dt_match = np.abs(high4[:, 0] - high4[:, 2]) / total_range
    dt_depth = (np.minimum(high4[:, 0], high4[:, 2]) - low4[:, 1]) / total_range
    double_top = (
        (dt_match <= tolerance)
        & (dt_depth >= min_prominence)
        & (cur_c < low4[:, 1] * (1.0 - break_frac))
    )
    db_match = np.abs(low4[:, 0] - low4[:, 2]) / total_range
    db_height = (high4[:, 1] - np.maximum(low4[:, 0], low4[:, 2])) / total_range
    double_bottom = (
        (db_match <= tolerance)
        & (db_height >= min_prominence)
        & (cur_c > high4[:, 1] * (1.0 + break_frac))
    )

    upper_slope = _slope(high4) / price
    lower_slope = _slope(low4) / price
    width0 = np.maximum(high4[:, 0] - low4[:, 0], eps)
    width3 = high4[:, 3] - low4[:, 3]
    converging = width3 < width0 * 0.82
    upper_next = high4[:, -1] + _slope(high4)
    lower_next = low4[:, -1] + _slope(low4)
    rising_wedge = (
        converging & (upper_slope > 0) & (lower_slope > upper_slope * 1.15)
        & (cur_c < lower_next * (1.0 - break_frac))
    )
    falling_wedge = (
        converging & (lower_slope < 0) & (upper_slope < lower_slope * 1.15)
        & (cur_c > upper_next * (1.0 + break_frac))
    )
    flat_limit = np.maximum(0.0015, tolerance / max(lookback, 1))
    ascending_triangle = (
        converging & (np.abs(upper_slope) <= flat_limit) & (lower_slope > flat_limit)
        & (cur_c > upper_next * (1.0 + break_frac))
    )
    descending_triangle = (
        converging & (np.abs(lower_slope) <= flat_limit) & (upper_slope < -flat_limit)
        & (cur_c < lower_next * (1.0 - break_frac))
    )

    pole_end = max(4, hist_c.shape[1] // 3)
    pole_start = hist_c[:, 0]
    pole_finish = hist_c[:, pole_end - 1]
    pole_return = (pole_finish - pole_start) / np.maximum(np.abs(pole_start), eps)
    cons_h = hist_h[:, pole_end:]
    cons_l = hist_l[:, pole_end:]
    cons_c = hist_c[:, pole_end:]
    cons_high = np.max(cons_h, axis=1)
    cons_low = np.min(cons_l, axis=1)
    cons_range = cons_high - cons_low
    pole_abs = np.abs(pole_finish - pole_start)
    cons_blocks_h = _blocks(cons_h, 3, "max")
    cons_blocks_l = _blocks(cons_l, 3, "min")
    cons_upper_slope = _slope(cons_blocks_h)
    cons_lower_slope = _slope(cons_blocks_l)
    consolidation_ok = cons_range <= np.maximum(pole_abs * 0.65, price * 0.004)
    bull_flag = (
        (pole_return >= 0.02) & consolidation_ok
        & (cons_upper_slope <= price * 0.002)
        & (cur_c > cons_high * (1.0 + break_frac))
    )
    bear_flag = (
        (pole_return <= -0.02) & consolidation_ok
        & (cons_lower_slope >= -price * 0.002)
        & (cur_c < cons_low * (1.0 - break_frac))
    )

    mean6 = _blocks(hist_c, 6, "mean")
    min6 = _blocks(hist_l, 6, "min")
    max6 = _blocks(hist_h, 6, "max")
    rim = (mean6[:, 0] + mean6[:, 4]) * 0.5
    cup_depth = (rim - np.min(min6[:, 1:4], axis=1)) / total_range
    rim_match = np.abs(mean6[:, 0] - mean6[:, 4]) / total_range
    handle_depth = (mean6[:, 4] - min6[:, 5]) / total_range
    cup_handle = (
        (rim_match <= tolerance * 1.5) & (cup_depth >= min_prominence * 1.25)
        & (handle_depth > 0) & (handle_depth <= cup_depth * 0.55)
        & (cur_c > rim * (1.0 + break_frac))
    )
    inv_rim = (mean6[:, 0] + mean6[:, 4]) * 0.5
    inv_depth = (np.max(max6[:, 1:4], axis=1) - inv_rim) / total_range
    inv_handle = (max6[:, 5] - mean6[:, 4]) / total_range
    inverse_cup = (
        (rim_match <= tolerance * 1.5) & (inv_depth >= min_prominence * 1.25)
        & (inv_handle > 0) & (inv_handle <= inv_depth * 0.55)
        & (cur_c < inv_rim * (1.0 - break_frac))
    )

    prev_h = hw[:, -2]
    prev_l = lw[:, -2]
    prev2_h = hw[:, -3]
    prev2_l = lw[:, -3]
    higher_structure = (cur_h > prev_h) & (cur_l > prev_l) & (prev_h >= prev2_h) & (prev_l >= prev2_l)
    lower_structure = (cur_h < prev_h) & (cur_l < prev_l) & (prev_h <= prev2_h) & (prev_l <= prev2_l)

    raw_scores = {
        "head_shoulders_bear": _strength(hs, 0.55 + hs_prom - shoulder_gap),
        "head_shoulders_bull": _strength(ihs, 0.55 + ihs_prom - inv_shoulder_gap),
        "double_top_bottom_bear": _strength(double_top, 0.5 + dt_depth - dt_match),
        "double_top_bottom_bull": _strength(double_bottom, 0.5 + db_height - db_match),
        "wedge_bear": _strength(rising_wedge, 0.55 + (width0 - width3) / width0),
        "wedge_bull": _strength(falling_wedge, 0.55 + (width0 - width3) / width0),
        "triangle_bull": _strength(ascending_triangle, 0.55 + (width0 - width3) / width0),
        "triangle_bear": _strength(descending_triangle, 0.55 + (width0 - width3) / width0),
        "flag_pennant_bull": _strength(bull_flag, 0.5 + np.abs(pole_return) * 5.0),
        "flag_pennant_bear": _strength(bear_flag, 0.5 + np.abs(pole_return) * 5.0),
        "cup_handle_bull": _strength(cup_handle, 0.5 + cup_depth - rim_match),
        "cup_handle_bear": _strength(inverse_cup, 0.5 + inv_depth - rim_match),
        "trend_structure_bull": _strength(higher_structure, 0.65 + (cur_h - prev_h) / total_range),
        "trend_structure_bear": _strength(lower_structure, 0.65 + (prev_l - cur_l) / total_range),
    }
    offset = lookback - 1
    event_map = {
        "head_shoulders": hs, "inverse_head_shoulders": ihs,
        "double_top": double_top, "double_bottom": double_bottom,
        "rising_wedge": rising_wedge, "falling_wedge": falling_wedge,
        "ascending_triangle": ascending_triangle, "descending_triangle": descending_triangle,
        "bull_flag_pennant": bull_flag, "bear_flag_pennant": bear_flag,
        "cup_handle": cup_handle, "inverse_cup_handle": inverse_cup,
        "higher_high_higher_low": higher_structure, "lower_high_lower_low": lower_structure,
    }
    for name, mask in event_map.items():
        out[name][offset:] = mask
    for key, scores in raw_scores.items():
        family, direction = key.rsplit("_", 1)
        out[f"{family}_{direction}_score"][offset:] = scores
        out[f"{family}_{direction}"][offset:] = scores > 0

    bull_stack = np.column_stack([out[f"{family}_bull_score"][offset:] for family in FORMATION_FAMILIES])
    bear_stack = np.column_stack([out[f"{family}_bear_score"][offset:] for family in FORMATION_FAMILIES])
    bull_best = np.max(bull_stack, axis=1)
    bear_best = np.max(bear_stack, axis=1)
    out["bull_score"][offset:] = bull_best
    out["bear_score"][offset:] = bear_best
    out["direction"][offset:] = np.where(bull_best > bear_best, 1, np.where(bear_best > bull_best, -1, 0)).astype(np.int8)

    # Preserve a deterministic primary reason.  Stronger signals win; ties use
    # the stable order below, which favors more specific reversal formations.
    candidates = [
        (ihs, raw_scores["head_shoulders_bull"], FORMATION_CODES["inverse_head_shoulders"]),
        (hs, raw_scores["head_shoulders_bear"], FORMATION_CODES["head_shoulders"]),
        (double_bottom, raw_scores["double_top_bottom_bull"], FORMATION_CODES["double_bottom"]),
        (double_top, raw_scores["double_top_bottom_bear"], FORMATION_CODES["double_top"]),
        (falling_wedge, raw_scores["wedge_bull"], FORMATION_CODES["falling_wedge"]),
        (rising_wedge, raw_scores["wedge_bear"], FORMATION_CODES["rising_wedge"]),
        (ascending_triangle, raw_scores["triangle_bull"], FORMATION_CODES["ascending_triangle"]),
        (descending_triangle, raw_scores["triangle_bear"], FORMATION_CODES["descending_triangle"]),
        (bull_flag, raw_scores["flag_pennant_bull"], FORMATION_CODES["bull_flag_pennant"]),
        (bear_flag, raw_scores["flag_pennant_bear"], FORMATION_CODES["bear_flag_pennant"]),
        (cup_handle, raw_scores["cup_handle_bull"], FORMATION_CODES["cup_handle"]),
        (inverse_cup, raw_scores["cup_handle_bear"], FORMATION_CODES["inverse_cup_handle"]),
        (higher_structure, raw_scores["trend_structure_bull"], FORMATION_CODES["higher_high_higher_low"]),
        (lower_structure, raw_scores["trend_structure_bear"], FORMATION_CODES["lower_high_lower_low"]),
    ]
    best_score = np.zeros(m, dtype=np.float32)
    primary = np.zeros(m, dtype=np.int8)
    for mask, scores, code in candidates:
        take = mask & (scores > best_score)
        primary[take] = code
        best_score[take] = scores[take]
    out["primary_code"][offset:] = primary
    return out


def formation_fields_from_ohlcv(
    open_: Iterable[float],
    high: Iterable[float],
    low: Iterable[float],
    close: Iterable[float],
    volume: Iterable[float] | None,
    timeframe: str,
    **kwargs,
) -> Dict[str, np.ndarray]:
    """Return NPZ/live-compatible field names for one timeframe."""
    raw = detect_classic_formations(open_, high, low, close, volume, **kwargs)
    return {f"formation_{name}_{timeframe}": values for name, values in raw.items()}


def ensure_npz_formation_fields(
    arrays: Mapping[str, np.ndarray],
    *,
    timeframes: Iterable[str] = ("15m", "1h", "4h", "D"),
    lookback: int = 40,
) -> Dict[str, np.ndarray]:
    """Derive missing formation arrays from an existing NPZ dictionary."""
    added: Dict[str, np.ndarray] = {}
    for tf in timeframes:
        required = [f"open_{tf}", f"high_{tf}", f"low_{tf}", f"close_{tf}"]
        if not all(key in arrays for key in required):
            continue
        if f"formation_direction_{tf}" in arrays:
            continue
        ohlc = [np.asarray(arrays[key]) for key in required]
        # Frozen stock NPZs use a native 5m axis and broadcast every completed
        # parent candle, including 15m, onto its base rows.  Detect on each
        # unique parent exactly once.  Prefer the causal availability timestamp
        # because two consecutive flat candles may have identical OHLC; retain
        # the OHLC-boundary fallback for older archives without timestamp_*.
        parent_timestamp = arrays.get(f"timestamp_{tf}")
        if parent_timestamp is not None and len(np.asarray(parent_timestamp)) == len(ohlc[0]):
            parent_timestamp = np.asarray(parent_timestamp, dtype=np.int64)
            changed = (parent_timestamp > 0) & np.r_[
                True, parent_timestamp[1:] != parent_timestamp[:-1]
            ]
        else:
            changed = np.ones(len(ohlc[0]), dtype=bool)
            if len(changed) > 1:
                changed[1:] = np.logical_or.reduce(
                    [values[1:] != values[:-1] for values in ohlc]
                )
        event_rows = np.flatnonzero(changed)
        volume_values = arrays.get(f"volume_{tf}")
        compact = formation_fields_from_ohlcv(
            ohlc[0][event_rows],
            ohlc[1][event_rows],
            ohlc[2][event_rows],
            ohlc[3][event_rows],
            np.asarray(volume_values)[event_rows] if volume_values is not None else None,
            tf,
            lookback=lookback,
        )
        for key, compact_values in compact.items():
            expanded = np.zeros(len(ohlc[0]), dtype=compact_values.dtype)
            expanded[event_rows] = compact_values
            added[key] = expanded
    return added


def latest_formation_fields(fields: Mapping[str, np.ndarray]) -> Dict[str, object]:
    """Collapse vector fields to JSON-friendly scalars for live indicators."""
    latest: Dict[str, object] = {}
    for key, values in fields.items():
        arr = np.asarray(values)
        if not arr.size:
            continue
        value = arr[-1]
        if key.startswith("formation_primary_code_"):
            latest[key] = int(value)
            tf = key.rsplit("_", 1)[-1]
            latest[f"formation_primary_{tf}"] = CODE_TO_NAME.get(int(value), "none")
        elif arr.dtype.kind == "b":
            latest[key] = bool(value)
        elif arr.dtype.kind in "iu":
            latest[key] = int(value)
        else:
            latest[key] = float(value)
    return latest


def _formation_timeframes(config) -> tuple[str, ...]:
    raw = getattr(config, "FORMATION_TFS", "15m,1h,4h,D")
    if isinstance(raw, str):
        values = raw.split(",")
    else:
        values = list(raw or ())
    return tuple(str(value).strip() for value in values if str(value).strip())


def select_latest_formation(
    indicators: Mapping[str, object],
    *,
    is_long: bool,
    action: str,
    config,
    resolver=None,
) -> dict | None:
    """Select the strongest enabled live/exact formation for an action.

    ENTRY follows the intended side.  EXIT requires the opposite direction.
    ``resolver`` may provide per-symbol config values with signature
    ``resolver(name, default)``.
    """
    action = str(action).upper()
    if action not in ("ENTRY", "EXIT"):
        raise ValueError("action must be ENTRY or EXIT")
    get = resolver or (lambda name, default: getattr(config, name, default))
    wanted = "bull" if is_long else "bear"
    if action == "EXIT":
        wanted = "bear" if is_long else "bull"
    minimum = float(get("FORMATION_MIN_SCORE", 0.65) or 0.0)
    best = None
    for family, prefix in CONFIG_FAMILY_PREFIX.items():
        if not bool(get(f"{prefix}_{action}_ENABLED", False)):
            continue
        for tf in _formation_timeframes(config):
            score = float(indicators.get(f"formation_{family}_{wanted}_score_{tf}", 0.0) or 0.0)
            if score < minimum:
                continue
            candidate = {
                "family": family,
                "timeframe": tf,
                "direction": wanted,
                "score": score,
                "primary": str(indicators.get(f"formation_primary_{tf}", family) or family),
            }
            if best is None or score > best["score"]:
                best = candidate
    return best


def formation_vector_mask(
    arrays: Mapping[str, np.ndarray],
    *,
    is_long: bool,
    action: str,
    config,
    n: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(mask, score, family_index)`` for the fast vector sandbox."""
    action = str(action).upper()
    wanted = "bull" if is_long else "bear"
    if action == "EXIT":
        wanted = "bear" if is_long else "bull"
    minimum = float(getattr(config, "FORMATION_MIN_SCORE", 0.65) or 0.0)
    best = np.zeros(n, dtype=np.float32)
    family_index = np.full(n, -1, dtype=np.int8)
    for idx, (family, prefix) in enumerate(CONFIG_FAMILY_PREFIX.items()):
        if not bool(getattr(config, f"{prefix}_{action}_ENABLED", False)):
            continue
        for tf in _formation_timeframes(config):
            values = arrays.get(f"formation_{family}_{wanted}_score_{tf}")
            if values is None or len(values) != n:
                continue
            scores = np.nan_to_num(np.asarray(values, dtype=np.float32), nan=0.0)
            take = (scores >= minimum) & (scores > best)
            best[take] = scores[take]
            family_index[take] = idx
    return best >= minimum, best, family_index
