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

def entry_mask(npz, n, is_long, cfg, close):
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
    if bool(getattr(cfg, "WT_3M_FORCE_OPEN_GR_GATE_ENABLED", True)) and (minimum > 0 or min_tfs > 0):
        scores = np.asarray([_tf_ind_matrix(arrays, tf, n, is_long, np.asarray(close), False, 0., 0.) for tf in ("3m", "15m", "1h", "4h", "D")])
        if minimum > 0:
            fire &= scores.sum(axis=0) >= minimum
        if min_tfs > 0:
            fire &= (scores >= min_ind).sum(axis=0) >= min_tfs
    return fire
