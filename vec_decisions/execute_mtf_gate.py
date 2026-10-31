from vec_decisions.watchdog_mtf_gate import prepare, step

def execution_passes(prepared, index, *, action, reason, price, now, process_started_at, account_key=""):
    cfg = prepared["cfg"]
    act = str(action).upper()
    text = str(reason or "")
    upper = text.upper()
    if not bool(getattr(cfg, "MTF_ARMED_ENTRY_ENABLED", False)) or not any(token in act for token in ("OPEN", "AUGMENT", "ENTRY")):
        return True, ""
    strong = bool(getattr(cfg, "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", True)) and "NOT A TRADEABLE KEY" not in upper and ("QUICK_OPEN" in act or any(token in upper for token in ("STRONG_BUY", "QUICK_OPEN", "FORCE_HA_4H_ABOVE_BASIS", "TRADEABLE_KEYS_MANDATORY", "WT_3M_FORCE_OPEN", "DC_BREAKOUT", "DC_HIGH_3M", "DC_LOW_3M", "SCALP_V3_OPEN", "BAR_BREAK", "MOMENTUM_SMA15M_WATCHDOG", "LR_BAND", "MTF_ARROW")))
    guaranteed = bool(getattr(cfg, "GUARANTEED_REENTRY_AUGMENT_ENABLED", True)) and ("REENTRY" in act or "REENTRY" in upper or "AUGMENT" in act)
    momentum = False
    if bool(getattr(cfg, "BREAKOUT_DC1H_BYPASS_ENABLED", True)) and price > 0:
        key = "dc_high_1h_prev" if prepared["side"] == "LONG" else "dc_low_1h_prev"
        values = prepared["arrays"].get(key)
        level = float(values[index]) if values is not None else 0.0
        momentum = level > 0 and (price > level if prepared["side"] == "LONG" else price < level)
    if "FOTEST_PERSYM_BASELINE" in text or "OBLIGATORY_OPEN" in text or str(account_key).lower() == "flz" or "MOMENTUM_WATCHDOG" in text:
        momentum = True
    seconds = float(getattr(cfg, "COLD_START_OPEN_BYPASS_SUPPRESS_SEC", 60.0))
    cold = seconds > 0 and float(now) - float(process_started_at) < seconds
    if cold and "REENTRY" not in upper and "PRICE_CROSS" not in upper:
        strong = momentum = guaranteed = False
    skip_short = prepared["side"] == "SHORT" and bool(getattr(cfg, "MTF_ARMED_ENTRY_SKIP_SHORT", True)) and not cold
    if strong or guaranteed or momentum or skip_short:
        return True, ""
    return step(prepared, index)
