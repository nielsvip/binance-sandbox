"""Single Tradier parameter resolver for the native and vector PPL paths."""


def stock_ppl_params(get):
    enabled = bool(get("PARTIAL_PROFIT_LOCK_ENABLED", False))
    min_gain = float(get("PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER", 0.5) or 0.5)
    arm_gain = float(get("PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER", 0.75) or 0.75)
    be_buffer = float(get("PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER", 0.02) or 0.02)
    frac = float(get("PARTIAL_PROFIT_LOCK_FRAC_TRADIER", 0.5) or 0.5)
    return enabled, min_gain, arm_gain, be_buffer, frac
