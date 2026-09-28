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


from wt_dc_entry_scorer import CATEGORY_WEIGHTS as _CW


def _dvf(ind: dict, key: str, n: int, default=0.0):
    """Detailed-scorer float vector with scalar-matching default (nan->default)."""
    v = ind.get(key)
    if v is None:
        return np.full(n, float(default), dtype=np.float64)
    a = np.asarray(v)
    try:
        a = a.astype(np.float64, copy=False)
    except (TypeError, ValueError):
        return np.full(n, float(default), dtype=np.float64)
    if len(a) != n:
        a = a[:n] if len(a) > n else np.concatenate([a, np.full(n - len(a), float(default))])
    return np.where(np.isfinite(a), a, float(default))


def _dvi(ind: dict, key: str, n: int, default=0):
    """Detailed-scorer int vector — mirrors _safe_int (nan->default, else int())."""
    return _dvf(ind, key, n, float(default)).astype(np.int64)


def score_entry_detailed_vec(indicators: dict, is_long: bool, *, n: int) -> np.ndarray:
    """Vectorized FAITHFUL port of wt_dc_entry_scorer._score_long / _score_short (the
    slowdown/accel per-TF scorer). np.select mirrors each if/elif ladder exactly; category
    caps + CATEGORY_WEIGHTS + final 0-100 clamp identical to scalar. Parity proven by
    test_wt_dc_detailed_scorer_vec.py."""
    n = int(n)
    ind = indicators
    W = _CW
    if is_long:
        # 1. HTF (cap 30)
        htf = np.zeros(n)
        ba = _dvi(ind, "wt_bull_alignment", n)
        htf += np.select([ba >= 5, ba >= 4, ba >= 3, ba >= 2], [10.0, 7.0, 4.0, 2.0], 0.0)
        vu = _dvi(ind, "wt_velocity_up_count", n)
        htf += np.select([vu >= 5, vu >= 4, vu >= 3], [8.0, 5.0, 3.0], 0.0)
        ss = _dvi(ind, "wt_structure_4h", n) + _dvi(ind, "wt_structure_D", n)
        htf += np.select([ss == 2, ss == 1, ss >= 0], [5.0, 3.0, 1.0], 0.0)
        htf += np.where(_dvi(ind, "wt_composite_bias", n) == 1, 4.0, 0.0)
        htf += np.where(_dvi(ind, "wt_wave_phase_4h", n) == 1, 1.5, 0.0)
        htf += np.where(_dvi(ind, "wt_wave_phase_D", n) == 1, 1.5, 0.0)
        htf = np.minimum(htf, 30.0) * W["htf"]
        # 2. LTF (cap 20)
        ltf = np.zeros(n)
        ltf += np.where(_dvi(ind, "wt_cross_bull_1h", n) != 0, 8.0, 0.0)
        ltf += np.where(_dvi(ind, "wt_cross_bull_15m", n) != 0, 6.0, 0.0)
        ltf += np.where(_dvi(ind, "wt_cross_bull_5m", n) != 0, 4.0, 0.0)
        wv = _dvf(ind, "wt_velocity_1h", n, 0.0)
        ltf += np.where(wv > 0, np.minimum(wv / 3.0, 1.0) * 3.0, 0.0)
        m1 = _dvi(ind, "wt_momentum_state_1h", n)
        ltf += np.select([m1 == 2, m1 == 1], [2.0, 1.0], 0.0)
        ltf = np.minimum(ltf, 20.0) * W["ltf"]
        # 3. MOM (cap 15)
        mom = np.zeros(n)
        cv = _dvf(ind, "wt_cross_value_1h", n, 0.0)
        mom += np.select([cv > 60, cv > 30, cv > 0, cv > -30], [4.0, 3.0, 2.0, 1.0], 0.0)
        mom += np.where(_dvi(ind, "wt_cross_rising_1h", n) != 0, 3.0, 0.0)
        dl = cv - _dvf(ind, "wt_cross_prev_value_1h", n, 0.0)
        mom += np.select([dl > 20, dl > 5], [4.0, 2.0], 0.0)
        dv = _dvi(ind, "wt_divergence_1h", n)
        ds = _dvf(ind, "wt_divergence_strength_1h", n, 0.0)
        mom += np.where(dv == 1, 3.0 + np.minimum(ds, 1.0) * 1.0, 0.0)
        mom = np.minimum(mom, 15.0) * W["mom"]
        # 4. DCBB (clamp 0-15)
        dcbb = np.zeros(n)
        d1 = _dvf(ind, "dc_position_1h", n, 0.5)
        dcbb += np.select([d1 > 0.9, d1 > 0.7, d1 > 0.5, d1 > 0.3], [4.0, 3.0, 2.0, 1.0], 0.0)
        d4 = _dvf(ind, "dc_position_4h", n, 0.5)
        dcbb += np.select([d4 > 0.7, d4 > 0.5, d4 > 0.3], [3.0, 2.0, 1.0], 0.0)
        b4 = _dvf(ind, "bb_pct_b_4h", n, 0.5)
        dcbb += np.where(np.abs(b4) < 10, np.select([b4 < 0.2, b4 < 0.4, b4 < 0.6, b4 > 1.0], [5.0, 3.0, 1.0, -2.0], 0.0), 0.0)
        bd = _dvf(ind, "bb_pct_b_D", n, 0.5)
        dcbb += np.where(np.abs(bd) < 10, np.select([bd < 0.2, bd < 0.4], [3.0, 1.0], 0.0), 0.0)
        dcbb = np.clip(dcbb, 0.0, 15.0) * W["dcbb"]
        # 5. VOL (cap 10)
        vol = np.zeros(n)
        rv = _dvf(ind, "relative_volume_1h", n, 0.0)
        vol += np.select([rv >= 2.0, rv >= 1.5, rv >= 0.5], [4.0, 3.0, 1.0], 0.0)
        mf = _dvf(ind, "mfi_1h", n, 50.0)
        vol += np.select([mf > 80, mf > 60, mf > 40], [3.0, 2.0, 1.0], 0.0)
        vol += np.where(_dvi(ind, "stoch_crossover_15m", n) != 0, 2.0, 0.0)
        vol += np.where(_dvf(ind, "stoch_k_1h", n, 50.0) > 60, 1.0, 0.0)
        vol = np.minimum(vol, 10.0) * W["vol"]
        # 6. CTX (cap 10)
        ctx = np.zeros(n)
        cd = _dvf(ind, "close_D", n, 0.0); ed = _dvf(ind, "ema_200_D", n, 0.0)
        ctx += np.where((cd > 0) & (ed > 0) & (cd > ed), 5.0, 0.0)
        c1 = _dvf(ind, "close_1h", n, 0.0); e1 = _dvf(ind, "ema_20_1h", n, 0.0)
        ctx += np.where((c1 > 0) & (e1 > 0) & (c1 > e1), 2.0, 0.0)
        ctx += np.where(_dvi(ind, "ha_4h", n) == 1, 1.5, 0.0)
        ctx += np.where(_dvi(ind, "ha_D", n) == 1, 1.5, 0.0)
        ctx = np.minimum(ctx, 10.0) * W["ctx"]
    else:
        htf = np.zeros(n)
        ba = _dvi(ind, "wt_bear_alignment", n)
        htf += np.select([ba >= 5, ba >= 4, ba >= 3, ba >= 2], [10.0, 7.0, 4.0, 2.0], 0.0)
        vd = _dvi(ind, "wt_velocity_down_count", n)
        htf += np.select([vd >= 5, vd >= 4, vd >= 3], [8.0, 5.0, 3.0], 0.0)
        ss = _dvi(ind, "wt_structure_4h", n) + _dvi(ind, "wt_structure_D", n)
        htf += np.select([ss == -2, ss == -1, ss <= 0], [5.0, 3.0, 1.0], 0.0)
        htf += np.where(_dvi(ind, "wt_composite_bias", n) == -1, 4.0, 0.0)
        htf += np.where(_dvi(ind, "wt_wave_phase_4h", n) == -1, 1.5, 0.0)
        htf += np.where(_dvi(ind, "wt_wave_phase_D", n) == -1, 1.5, 0.0)
        htf = np.minimum(htf, 30.0) * W["htf"]
        ltf = np.zeros(n)
        ltf += np.where(_dvi(ind, "wt_cross_bear_1h", n) != 0, 8.0, 0.0)
        ltf += np.where(_dvi(ind, "wt_cross_bear_15m", n) != 0, 6.0, 0.0)
        ltf += np.where(_dvi(ind, "wt_cross_bear_5m", n) != 0, 4.0, 0.0)
        wv = _dvf(ind, "wt_velocity_1h", n, 0.0)
        ltf += np.where(wv < 0, np.minimum(np.abs(wv) / 3.0, 1.0) * 3.0, 0.0)
        m1 = _dvi(ind, "wt_momentum_state_1h", n)
        ltf += np.select([m1 == -2, m1 == -1], [2.0, 1.0], 0.0)
        ltf = np.minimum(ltf, 20.0) * W["ltf"]
        mom = np.zeros(n)
        cv = _dvf(ind, "wt_cross_value_1h", n, 0.0)
        mom += np.select([cv < -60, cv < -30, cv < 0, cv < 30], [4.0, 3.0, 2.0, 1.0], 0.0)
        mom += np.where(_dvi(ind, "wt_cross_rising_1h", n) == 0, 3.0, 0.0)
        dl = cv - _dvf(ind, "wt_cross_prev_value_1h", n, 0.0)
        mom += np.select([dl < -20, dl < -5], [4.0, 2.0], 0.0)
        dv = _dvi(ind, "wt_divergence_1h", n)
        ds = _dvf(ind, "wt_divergence_strength_1h", n, 0.0)
        mom += np.where(dv == -1, 3.0 + np.minimum(ds, 1.0) * 1.0, 0.0)
        mom = np.minimum(mom, 15.0) * W["mom"]
        dcbb = np.zeros(n)
        d1 = _dvf(ind, "dc_position_1h", n, 0.5)
        dcbb += np.select([d1 < 0.1, d1 < 0.3, d1 < 0.5, d1 < 0.7], [4.0, 3.0, 2.0, 1.0], 0.0)
        d4 = _dvf(ind, "dc_position_4h", n, 0.5)
        dcbb += np.select([d4 < 0.3, d4 < 0.5, d4 < 0.7], [3.0, 2.0, 1.0], 0.0)
        b4 = _dvf(ind, "bb_pct_b_4h", n, 0.5)
        dcbb += np.where(np.abs(b4) < 10, np.select([b4 > 0.8, b4 > 0.6, b4 > 0.4, b4 < 0.0], [5.0, 3.0, 1.0, -2.0], 0.0), 0.0)
        bd = _dvf(ind, "bb_pct_b_D", n, 0.5)
        dcbb += np.where(np.abs(bd) < 10, np.select([bd > 0.8, bd > 0.6], [3.0, 1.0], 0.0), 0.0)
        dcbb = np.clip(dcbb, 0.0, 15.0) * W["dcbb"]
        vol = np.zeros(n)
        rv = _dvf(ind, "relative_volume_1h", n, 0.0)
        vol += np.select([rv >= 2.0, rv >= 1.5, rv >= 0.5], [4.0, 3.0, 1.0], 0.0)
        mf = _dvf(ind, "mfi_1h", n, 50.0)
        vol += np.select([mf < 20, mf < 40, mf < 60], [3.0, 2.0, 1.0], 0.0)
        vol += np.where(_dvi(ind, "stoch_crossunder_15m", n) != 0, 2.0, 0.0)
        vol += np.where(_dvf(ind, "stoch_k_1h", n, 50.0) < 40, 1.0, 0.0)
        vol = np.minimum(vol, 10.0) * W["vol"]
        ctx = np.zeros(n)
        cd = _dvf(ind, "close_D", n, 0.0); ed = _dvf(ind, "ema_200_D", n, 0.0)
        ctx += np.where((cd > 0) & (ed > 0) & (cd < ed), 5.0, 0.0)
        c1 = _dvf(ind, "close_1h", n, 0.0); e1 = _dvf(ind, "ema_20_1h", n, 0.0)
        ctx += np.where((c1 > 0) & (e1 > 0) & (c1 < e1), 2.0, 0.0)
        ctx += np.where(_dvi(ind, "ha_4h", n) == 0, 1.5, 0.0)
        ctx += np.where(_dvi(ind, "ha_D", n) == 0, 1.5, 0.0)
        ctx = np.minimum(ctx, 10.0) * W["ctx"]
    return np.clip(htf + ltf + mom + dcbb + vol + ctx, 0.0, 100.0)


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
