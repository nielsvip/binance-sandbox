import numpy as np

from vec_decisions.mtf_gr_filter import gr_filter_pass_vec

def prepare(npz, n, is_long, cfg, close):
    import mtf_live_evaluator as native
    timeframes = [value.strip() for value in str(getattr(cfg, "MTF_ARMED_HTF_LIST", "1h,4h,D,W")).split(",") if value.strip()]
    fields = {f"{field}_{timeframe}{suffix}" for timeframe in timeframes for field in ("close", "dc_high", "dc_low", "bb_upper", "bb_lower", "wt1", "wt2") for suffix in ("", "_prev")}
    fields.update(("dc_pos_1h", "dc_pos_4h", "dc_pos_D", "atr_1h", "atr_1h_med_20", "atr_1h_sma_20"))
    arrays = {}
    for field in fields:
        if field not in npz:
            continue
        values = np.asarray(npz[field])
        if values.ndim != 1 or len(values) != n:
            raise ValueError(f"MTF arming input {field} is not aligned to the replay bars")
        arrays[field] = values
    votes = gr_filter_pass_vec(npz, n, is_long, cfg, close, min_tfs=int(getattr(cfg, "MTF_GR_MIN_TFS", 3)), min_ind=int(getattr(cfg, "MTF_GR_MIN_IND", 5))) if bool(getattr(cfg, "MTF_ENTRY_REQUIRE_GR_FILTER", True)) else np.ones(n, dtype=bool)
    return {"cfg": cfg, "side": "LONG" if is_long else "SHORT", "state": native._new_state(), "arrays": arrays, "votes": votes}

def step(prepared, index):
    import mtf_live_evaluator as native
    cfg = prepared["cfg"]
    if bool(getattr(cfg, "MTF_ENTRY_BLOCKED", False)):
        return False, "MTF_PER_SYM_BLOCKED"
    indicators = {field: values[index] for field, values in prepared["arrays"].items()}
    state, side = prepared["state"], prepared["side"]
    native.update_armed_state(state, indicators, side, cfg)
    if bool(getattr(cfg, "MTF_REQUIRE_ARMED_ANY", True)) and not native._armed_any_effective(state, indicators, side, cfg):
        return False, "MTF_NO_ARMED_STATE"
    if not bool(prepared["votes"][index]):
        return False, f"MTF_GR_FILTER_FAIL_{int(getattr(cfg, 'MTF_GR_MIN_TFS', 3))}tf_{int(getattr(cfg, 'MTF_GR_MIN_IND', 5))}ind"
    return True, ""
