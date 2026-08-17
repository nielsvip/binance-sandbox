"""Vector-only WT/DC entry scorer.

Keep NumPy research helpers out of :mod:`wt_dc_entry_scorer`: that module is
imported by the live and exact Tradier engines and therefore belongs to the
exact execution contract.  Changes here may invalidate vector research, but
must not make already-computed exact-engine rows stale.
"""

from __future__ import annotations

import numpy as np

from wt_dc_entry_scorer import _cross_label


def _vec_float_field(indicators: dict, key: str, n: int, default=np.nan):
    """Return one scorer input as a finite-shape float vector."""
    value = indicators.get(key)
    if value is None:
        return np.full(n, default, dtype=np.float64)
    array = np.asarray(value)
    if len(array) != n:
        raise ValueError(f"{key} length {len(array)} != {n}")
    try:
        result = array.astype(np.float64, copy=False)
    except (TypeError, ValueError):
        result = np.full(n, default, dtype=np.float64)
        for index, item in enumerate(array):
            try:
                result[index] = float(item)
            except (TypeError, ValueError):
                pass
    if np.isfinite(default):
        result = np.where(np.isfinite(result), result, default)
    return result


def _vec_cross_direction(indicators: dict, tf: str, n: int):
    """Vector equivalent of the scalar cross reader (+1 bull/-1 bear)."""
    result = np.zeros(n, dtype=np.int8)
    canonical = indicators.get(f"wt_cross_{tf}")
    if canonical is not None:
        array = np.asarray(canonical)
        if len(array) != n:
            raise ValueError(f"wt_cross_{tf} length {len(array)} != {n}")
        if array.dtype.kind in "iufb":
            numeric = np.asarray(array, dtype=np.float64)
            finite = np.isfinite(numeric)
            result[finite & (numeric > 0)] = 1
            result[finite & (numeric < 0)] = -1
        else:
            text = np.char.upper(np.char.strip(array.astype(str)))
            result[text == "BULL"] = 1
            result[text == "BEAR"] = -1
            unresolved = result == 0
            if np.any(unresolved):
                for index in np.flatnonzero(unresolved):
                    result[index] = (
                        1
                        if _cross_label(array[index]) == "BULL"
                        else -1
                        if _cross_label(array[index]) == "BEAR"
                        else 0
                    )
    unresolved = result == 0
    if np.any(unresolved):
        bull = _vec_float_field(
            indicators, f"wt_cross_bull_{tf}", n, default=0.0
        )
        bear = _vec_float_field(
            indicators, f"wt_cross_bear_{tf}", n, default=0.0
        )
        result[unresolved & np.isfinite(bull) & (bull != 0)] = 1
        unresolved = result == 0
        result[unresolved & np.isfinite(bear) & (bear != 0)] = -1
    return result


def score_entry_multitf_vec(
    indicators: dict,
    is_long: bool,
    *,
    n: int | None = None,
) -> np.ndarray:
    """Causal NumPy port of the production scalar multi-TF scorer."""
    if n is None:
        for key in (
            "wt1_D",
            "wt2_D",
            "wt1_4h",
            "wt2_4h",
            "dc_position_1h",
            "stoch_k_5m",
        ):
            if key in indicators:
                n = len(indicators[key])
                break
    if n is None:
        raise ValueError("cannot infer WT/DC vector length")
    n = int(n)
    wt1_d = _vec_float_field(indicators, "wt1_D", n)
    wt2_d = _vec_float_field(indicators, "wt2_D", n)
    wt1_4h = _vec_float_field(indicators, "wt1_4h", n)
    wt2_4h = _vec_float_field(indicators, "wt2_4h", n)
    dc_1h = _vec_float_field(indicators, "dc_position_1h", n, default=0.5)
    k_5m = _vec_float_field(indicators, "stoch_k_5m", n, default=50.0)
    cross_1h = _vec_cross_direction(indicators, "1h", n)

    valid = (
        np.isfinite(wt1_d)
        & np.isfinite(wt2_d)
        & np.isfinite(wt1_4h)
        & np.isfinite(wt2_4h)
        & np.isfinite(dc_1h)
        & np.isfinite(k_5m)
    )
    score = np.zeros(n, dtype=np.float64)
    if is_long:
        score += 25.0 * (wt1_d > wt2_d)
        score += 25.0 * (wt1_4h > wt2_4h)
        score += 30.0 * (cross_1h == 1)
        score += 10.0 * (dc_1h < 0.5)
        score += 10.0 * (k_5m < 40.0)
    else:
        score += 25.0 * (wt1_d < wt2_d)
        score += 25.0 * (wt1_4h < wt2_4h)
        score += 30.0 * (cross_1h == -1)
        score += 10.0 * (dc_1h > 0.5)
        score += 10.0 * (k_5m > 60.0)
    score[~valid] = 0.0
    return score
