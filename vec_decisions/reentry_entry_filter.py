"""Shared minimum-pass counting and native providers for reentry entry filters."""


def min_pass_result(mask_results, min_pass=1):
    """Return (allowed, passed, required, active) for named optional predicates.

    A missing/None predicate is omitted, matching the live and vector convention
    that only an available, active gate contributes to the denominator.
    """
    values = [bool(value) for value in mask_results.values() if value is not None]
    if not values:
        return True, 0, 0, 0
    try:
        requested = int(min_pass or 1)
    except (TypeError, ValueError, OverflowError):
        requested = 1
    required = min(max(1, requested), len(values))
    passed = sum(values)
    return passed >= required, passed, required, len(values)


def live_15m_masks(indicators, is_long):
    """Build the existing scalar Tradier REENTRY_ENTRY_FILTER predicates.

    Key-presence checks, fallback values, and comparisons match the live block
    in tradier_manage.process_position. These three predicates alone do not
    represent v12's wider `_entry_filter_masks` stack.
    """
    indicators = indicators or {}
    masks = {}
    if "wt1_15m" in indicators and "wt2_15m" in indicators:
        wt1 = float(indicators.get("wt1_15m", 0) or 0)
        wt2 = float(indicators.get("wt2_15m", 0) or 0)
        masks["wt_cross_15m"] = wt1 > wt2 if is_long else wt1 < wt2
    if "k_15m" in indicators:
        k15 = float(indicators.get("k_15m", 50) or 50)
        masks["stoch_k_15m"] = k15 <= 80 if is_long else k15 >= 20
    if "rsi_15m" in indicators:
        rsi15 = float(indicators.get("rsi_15m", 50) or 50)
        masks["rsi_15m"] = rsi15 <= 70 if is_long else rsi15 >= 30
    return masks
