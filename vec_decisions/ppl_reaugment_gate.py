"""Native execute_now gates that apply to PPL re-augment actions."""


def effective_gain(cfg, raw_gain_pct, ppl_fired):
    raw = float(raw_gain_pct)
    if not ppl_fired or not bool(getattr(cfg, "EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED", True)):
        return raw
    try:
        frac = float(getattr(cfg, "PARTIAL_PROFIT_LOCK_FRAC", 0.5))
    except (TypeError, ValueError):
        return raw
    if not 0.0 < frac < 1.0:
        return raw
    return raw / (1.0 - frac)


def allows_reaugment(cfg, raw_gain_pct, effective_gain_pct, max_gain_pct, position_amt, *, vector_exempt=False):
    """Mirror the execute_now losing-position and raw 3% guards for PPL re-augments.

    This handles only the two gates with directly represented per-bar state. Later
    execute_now gates (augment depth, gain since last add, execution size caps) remain
    separate and require their own state and tests.
    """
    raw_gain = float(raw_gain_pct)
    eff_gain = float(effective_gain_pct)
    max_gain = float(max_gain_pct)
    amt = abs(float(position_amt))
    min_gain = float(getattr(cfg, "MIN_GAIN", 3.0))
    min_qty = float(getattr(cfg, "MIN_POSITION_QTY", 0.0) or 0.0)
    if amt > min_qty and eff_gain < min_gain:
        pb_floor = 0.5 * min_gain
        pb_rev = float(getattr(cfg, "PULLBACK_AUGMENT_REVERSAL_MIN", 1.0))
        pb_ok = bool(getattr(cfg, "PULLBACK_AUGMENT_ENABLED", True)) and max_gain >= min_gain and eff_gain >= pb_floor and (max_gain - eff_gain) >= pb_rev
        if not pb_ok and not vector_exempt:
            return False, "LOSING_POSITION_HARD_BLOCK"
    if raw_gain < 3.0 and not vector_exempt:
        return False, "EXEC_AUGMENT_GATE_3PCT"
    return True, "OK"
