import numpy as np
from vec_decisions.asr_input_adapter import paired_npz_view
from vec_decisions.golden_loop import _tf_ind_matrix

def trigger(price, high, low, k, previous_k, ha, is_long):
    rising = (k > previous_k) | (ha == "green")
    falling = (k < previous_k) | (ha == "red")
    return ((high > 0) & (price > 0) & (price > high) & rising) if is_long else ((low > 0) & (price > 0) & (price < low) & falling)

def _number(source, key, n, default=0.0):
    if key not in source:
        return np.full(n, default, dtype=float)
    values = np.asarray(source[key])
    if values.shape != (n,):
        raise ValueError(f"{key}: mandatory entry requires {n} aligned bars")
    def convert(value):
        try:
            return default if value is None else float(value)
        except (ValueError, TypeError):
            return default
    return np.fromiter((convert(value) for value in values), dtype=float, count=n)

def gr_vote_mask(arrays, n, is_long, close, minimum, min_tfs, min_ind):
    prepared = dict(arrays)
    errors = np.zeros(n, dtype=bool)
    for tf in ("3m", "15m", "1h", "4h", "D"):
        for field in ("wt1", "wt2", "rsi", "mfi", "dc_position", "dc_high", "dc_low", "bb_pct_b", "bb_upper", "bb_lower", "relative_volume", "stoch_k", "stoch_d", "adx", "macd_hist", "ha_color"):
            key = f"{field}_{tf}"
            default = 0. if field in ("wt1", "wt2", "dc_high", "dc_low", "bb_upper", "bb_lower", "macd_hist", "ha_color") else -1.
            if key not in arrays:
                continue
            raw = np.asarray(arrays[key], dtype=object)
            if raw.shape != (n,):
                raise ValueError(f"{key}: GR inputs must align to mandatory entry bars")
            values = np.full(n, default)
            for index, value in enumerate(raw):
                try:
                    values[index] = default if value is None else float(value)
                    if field == "ha_color" and np.isinf(values[index]):
                        errors[index] = True
                except (TypeError, ValueError):
                    if field == "ha_color":
                        values[index] = {"green": 1., "red": -1.}.get(str(value).lower(), 0.)
                    elif field != "macd_hist":
                        errors[index] = True
            prepared[key] = values
    scores = np.asarray([_tf_ind_matrix(prepared, tf, n, is_long, np.asarray(close), False, 0., 0.) for tf in ("3m", "15m", "1h", "4h", "D")])
    passed = np.ones(n, dtype=bool)
    if minimum > 0:
        passed &= scores.sum(axis=0) >= minimum
    if min_tfs > 0:
        passed &= (scores >= min_ind).sum(axis=0) >= min_tfs
    return passed | errors

def entry_mask(npz, n, is_long, cfg, close, apply_gr=True):
    if str(getattr(cfg, "MODE", "crypto")) != "crypto" or not bool(getattr(cfg, "TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED", True)):
        return np.zeros(n, dtype=bool)
    arrays = paired_npz_view(npz, bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)))
    k = _number(arrays, "k_3m", n, 50.)
    previous_k = _number(arrays, "k_3m_prev", n, np.nan)
    # Native safe_fetch_float defaults only absent/invalid cells, preserving NaN.
    if "k_3m_prev" not in arrays:
        previous_k = k.copy()
    else:
        raw = np.asarray(arrays["k_3m_prev"], dtype=object)
        for index, value in enumerate(raw):
            try:
                float(value)
            except (TypeError, ValueError):
                previous_k[index] = k[index]
    ha = np.asarray(arrays.get("ha_3m", np.full(n, "neutral")))
    if ha.shape != (n,):
        raise ValueError("ha_3m: mandatory entry requires aligned bars")
    if ha.dtype.kind in "biuf":
        ha = np.where(ha == 1, "green", np.where(ha == -1, "red", "neutral"))
    fire = trigger(np.asarray(close), _number(arrays, "dc_high_3m", n), _number(arrays, "dc_low_3m", n), k, previous_k, ha, is_long)
    minimum = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_VOTE_MIN", 15))
    min_tfs = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_MIN_TFS", 0))
    min_ind = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF", 5))
    if apply_gr and bool(getattr(cfg, "WT_3M_FORCE_OPEN_GR_GATE_ENABLED", True)) and (minimum > 0 or min_tfs > 0):
        fire &= gr_vote_mask(arrays, n, is_long, close, minimum, min_tfs, min_ind)
    return fire

def force_trigger(price, sma15, wt1, wt2, wt15_1, wt15_2, wt1h_1, wt1h_2, cross, previous_cross, is_long, pct, require_hh):
    distance = (sma15 > 0) & ((price > sma15 * (1. + pct)) if is_long else (price < sma15 * (1. - pct)))
    direction = (wt1 > wt2) if is_long else (wt1 < wt2)
    htf = ((wt15_1 > wt15_2) & (wt1h_1 > wt1h_2)) if is_long else ((wt15_1 < wt15_2) & (wt1h_1 < wt1h_2))
    htf &= (abs(wt15_1) > 1e-9) | (abs(wt15_2) > 1e-9) | (abs(wt1h_1) > 1e-9) | (abs(wt1h_2) > 1e-9)
    hh = ((cross > 0) & (previous_cross > 0) & ((cross > previous_cross) if is_long else (cross < previous_cross))) if require_hh else True
    return distance & direction & htf & hh & (price > 0)

def entry_masks(npz, n, is_long, cfg, close):
    raw_dc = entry_mask(npz, n, is_long, cfg, close, apply_gr=False)
    dc = raw_dc.copy()
    force = np.zeros(n, dtype=bool)
    if str(getattr(cfg, "MODE", "crypto")).lower() == "tradier":
        from vec_decisions.wt_force_open_stock import force_open_mask

        return dc, force_open_mask(npz, n, is_long, cfg, close)
    if str(getattr(cfg, "MODE", "crypto")) != "crypto" or not bool(getattr(cfg, "TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED", True)):
        return dc, force
    arrays = paired_npz_view(npz, bool(getattr(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)))
    if bool(getattr(cfg, "WT_3M_FORCE_OPEN_ENABLED", True)):
        values = [_number(arrays, key, n) for key in ("sma_200_15m", "wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h", "wt_crossover_value_3m", "wt_crossover_value_3m_prev")]
        force = ~raw_dc & force_trigger(np.asarray(close), *values, is_long, float(getattr(cfg, "WT_3M_FORCE_OPEN_SMA_PCT", 1.)) / 100., bool(getattr(cfg, "WT_3M_FORCE_OPEN_REQUIRE_HH_CROSS", True)))
    minimum = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_VOTE_MIN", 15))
    min_tfs = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_MIN_TFS", 0))
    min_ind = int(getattr(cfg, "WT_3M_FORCE_OPEN_GR_MIN_IND_PER_TF", 5))
    if bool(getattr(cfg, "WT_3M_FORCE_OPEN_GR_GATE_ENABLED", True)) and (minimum > 0 or min_tfs > 0):
        passed = gr_vote_mask(arrays, n, is_long, close, minimum, min_tfs, min_ind)
        dc &= passed
        force &= passed
    return dc, force
