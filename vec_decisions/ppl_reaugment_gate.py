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


def allows_losing_position_increase(cfg, effective_gain_pct, max_gain_pct, position_qty, initial_qty, *, vector_exempt=False):
    """Mirror execute_now LOSING_POSITION_HARD_BLOCK and AUGMENT_LEVEL_GATE."""
    amount = abs(float(position_qty))
    initial = abs(float(initial_qty))
    gain = float(effective_gain_pct)
    peak = float(max_gain_pct)
    min_gain = float(getattr(cfg, "MIN_GAIN", 3.0))
    reversal = float(getattr(cfg, "PULLBACK_AUGMENT_REVERSAL_MIN", 1.0))
    pullback = bool(getattr(cfg, "PULLBACK_AUGMENT_ENABLED", True))
    if amount > 0.0 and gain < min_gain:
        pullback_ok = pullback and peak >= min_gain and gain >= 0.5 * min_gain and peak - gain >= reversal
        if not pullback_ok and not vector_exempt:
            return False, "LOSING_POSITION_HARD_BLOCK"
    if amount > 0.0 and initial > 0.0:
        augment_n = max(1, round(amount / initial))
        required = augment_n * min_gain
        if gain < required:
            pullback_level_ok = pullback and peak >= required and gain >= 0.5 * min_gain and peak - gain >= reversal
            if not pullback_level_ok:
                return False, "AUGMENT_LEVEL_GATE"
    return True, "OK"


def passes_universal_gain_since_add(cfg, is_long, px, last_aug_px, raw_gain_pct, max_gain_pct, reason):
    """Mirror execute_now UAGAIN, including the live typed reason tier."""
    if not bool(getattr(cfg, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True)):
        return True
    from vec_decisions.uagain_gate import effective_min_gain, uagain_gate_pass
    from vec_decisions.live_unw_gates import augment_tier_min_gain
    generic = float(effective_min_gain(cfg))
    minimum = float(augment_tier_min_gain(lambda key, default=None: getattr(cfg, key, default), reason, generic))
    return bool(uagain_gate_pass(cfg, is_long, float(px), float(last_aug_px), float(raw_gain_pct), float(max_gain_pct), minimum))


def projected_entry_nonnegative(is_long, entry_price, position_qty, add_qty, px):
    entry = float(entry_price)
    amount = abs(float(position_qty))
    add = abs(float(add_qty))
    price = float(px)
    if entry <= 0.0 or amount <= 0.0 or add <= 0.0 or price <= 0.0:
        return True
    projected = (amount * entry + add * price) / (amount + add)
    projected_gain = (price - projected) / projected * 100.0 if is_long else (projected - price) / projected * 100.0
    return projected_gain >= 0.0


def passes_raw_three_percent_gate(raw_gain_pct, position_value, *, reentry=False, vector_exempt=False):
    """Mirror execute_now's non-reentry, non-vector raw-gain 3% guard."""
    return not (float(position_value) > 0.0 and float(raw_gain_pct) < 3.0 and not reentry and not vector_exempt)


def passes_augment_guard(cfg, raw_gain_pct, position_value, start_position_size):
    """Mirror execute_now's later AUGMENT_GUARD threshold for positions above SPS."""
    return not (float(position_value) > float(start_position_size) and float(raw_gain_pct) < float(getattr(cfg, "MIN_GAIN", 3.0)))


def passes_short_sma_gate(cfg, is_long, px, sma_200_15m, position_value, start_position_size, rsi_4h, rsi_d, bb_pct_b_4h):
    """Mirror execute_now SHORT_SMA_GATE and its extreme-overbought override."""
    price = float(px)
    sma = float(sma_200_15m)
    if is_long or price <= 0.0 or sma <= 0.0 or price <= sma or float(position_value) < float(start_position_size):
        return True
    if not bool(getattr(cfg, "EXTREME_OB_OS_OVERRIDE_ENABLED", True)):
        return False
    return bool(
        float(rsi_4h) >= float(getattr(cfg, "EXTREME_OB_RSI_4H_MIN", 80.0))
        and float(rsi_d) >= float(getattr(cfg, "EXTREME_OB_RSI_D_MIN", 75.0))
        and float(bb_pct_b_4h) >= float(getattr(cfg, "EXTREME_OB_BB_PCT_B_4H_MIN", 1.0))
    )
