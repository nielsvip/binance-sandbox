"""Shared minimum-pass counting and native providers for reentry entry filters."""

import numpy as np


def min_pass_result(mask_results, min_pass=1):
    """Return (allowed, passed, required, active) for named optional predicates.

    A missing/None predicate is omitted, matching the live and vector convention
    that only an available, active gate contributes to the denominator.
    """
    values = [bool(value) for value in mask_results.values() if value is not None]
    required = min_pass_required(len(values), min_pass)
    if not values:
        return True, 0, required, 0
    passed = sum(values)
    return passed >= required, passed, required, len(values)


def min_pass_required(active_count, min_pass=1):
    """Clamp a configured minimum to [1, active_count], or zero if none active."""
    active_count = max(0, int(active_count))
    if not active_count:
        return 0
    try:
        requested = int(min_pass or 1)
    except (TypeError, ValueError, OverflowError):
        requested = 1
    return min(max(1, requested), active_count)


def min_pass_allowed(passed_count, active_count, min_pass=1):
    """Apply the shared minimum-pass rule to pre-counted scalar/array outcomes."""
    required = min_pass_required(active_count, min_pass)
    return not required or int(passed_count) >= required


def compare_mask_coverage(vector_names, native_masks):
    """Describe active producer-count differences without changing either lane."""
    vector = [str(name) for name in vector_names]
    native = [str(name) for name, value in native_masks.items() if value is not None]
    vector_set, native_set = set(vector), set(native)
    return {
        "vector_active_count": len(vector),
        "native_active_count": len(native),
        "counts_equal": len(vector) == len(native),
        "producer_sets_equal": vector_set == native_set,
        "missing_native": [name for name in vector if name not in native_set],
        "native_only": [name for name in native if name not in vector_set],
    }


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


def generic_filter_tf_live_masks(indicators, is_long, cfg, current_price):
    """Evaluate active entry-target FILTER_TF masks through the vector predicates.

    The native snapshot provides only current indicator values, so this emits
    scalar outcomes for fields present in that snapshot and uses the same
    `_cond` implementation as v12. It does not cover other entry-mask families.
    """
    import copy
    from vec_decisions import generic_filter_tf

    indicators = indicators or {}
    npz = {}
    for key, value in indicators.items():
        if value is None:
            continue
        try:
            npz[key] = np.asarray([float(value)], dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            continue

    def safe(data, key, n, default=0.0):
        if key in data:
            return np.asarray(data[key], dtype=np.float64)
        return np.full(n, default, dtype=np.float64)

    close = np.asarray([float(current_price or 0.0)], dtype=np.float64)
    gcfg = cfg
    if str(getattr(cfg, "MODE", "crypto")) == "tradier" and bool(
        getattr(cfg, "STOCKS_LIVE_ENTRY_STACK_ENABLED", False)
    ):
        gcfg = copy.copy(cfg)
        gcfg.BAR_PATTERNS_FILTER_TF = "OFF"
        if not bool(getattr(cfg, "EMA_BLANKET_FILTER_ENABLED", False)):
            gcfg.EMA_BLANKET_FILTER_FILTER_TF = "OFF"
    for tf_name, master_name in (
        ("EMA_BLANKET_FILTER_FILTER_TF", "EMA_BLANKET_FILTER_ENABLED"),
        ("BREAKOUT_RETEST_FILTER_TF", "BREAKOUT_RETEST_ARMED_ENABLED"),
        ("PEAK_GIVEBACK_BE_EROSION_FILTER_TF", "PEAK_GIVEBACK_PROTECTION_ENABLED"),
    ):
        if not bool(getattr(cfg, master_name, False)) and str(
            getattr(gcfg, tf_name, "OFF") or "OFF"
        ).strip().upper() != "OFF":
            if gcfg is cfg:
                gcfg = copy.copy(cfg)
            setattr(gcfg, tf_name, "OFF")
    masks = {}
    for name, (target, kind) in generic_filter_tf.FILTER_TF_MAP.items():
        if target != "entry":
            continue
        tf = str(getattr(gcfg, name, "OFF") or "OFF").strip()
        if tf.upper() == "OFF":
            continue
        result = generic_filter_tf._cond(
            kind, npz, 1, tf, is_long, close, safe
        )
        if result is not None:
            masks[name] = bool(np.asarray(result, dtype=bool)[0])
    return masks


def shared_simple_live_masks(indicators, is_long, cfg, current_price):
    """Evaluate the next shared, NPZ-portable entry masks on one live snapshot.

    Each result uses the same vector predicate called by v12. This covers only
    the listed shared gates; producer names remain explicit for parity audits.
    """
    from vec_decisions import bb_pullback_gate

    indicators = indicators or {}
    npz = {}
    for key, value in indicators.items():
        if value is None:
            continue
        try:
            npz[key] = np.asarray([float(value)], dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            continue

    def safe(data, key, n, default=0.0):
        if key in data:
            return np.asarray(data[key], dtype=np.float64)
        return np.full(n, default, dtype=np.float64)

    close = np.asarray([float(current_price or 0.0)], dtype=np.float64)
    masks = {}
    masks.update(_wave4_live_masks_from_npz(npz, is_long, cfg, close, safe))

    if str(getattr(cfg, "BB_PULLBACK_GATE_FILTER_TF", "OFF") or "OFF").strip().upper() != "OFF":
        blocked = bb_pullback_gate.bb_pullback_gate_vec(npz, 1, cfg, is_long)
        masks["BB_PULLBACK_GATE_FILTER_TF"] = not bool(np.asarray(blocked, dtype=bool)[0])
    if bool(getattr(cfg, "EMA50_15M_ENTRY_FILTER_ENABLED", False)):
        ema50 = float(np.asarray(npz.get("ema_50_15m", [0.0]))[0])
        price = float(close[0])
        pct = float(getattr(cfg, "EMA50_15M_ENTRY_FILTER_PCT", 0.0) or 0.0) / 100.0
        valid = ema50 > 0 and price > 0
        allowed = (price > ema50 * (1.0 + pct)) if is_long else (price < ema50 * (1.0 - pct))
        masks["EMA50_15M_ENTRY_FILTER_ENABLED"] = not (valid and not allowed)
    return masks


def _wave4_live_masks_from_npz(npz, is_long, cfg, close, safe):
    from vec_decisions import grey_wire_entries, wave4_families

    masks = {}
    for name, fn in (
        ("OI_CONFIRM_ENABLED", wave4_families.oi_confirm_entry_gate),
        ("EMA_BLANKET_FILTER_ENABLED", wave4_families.ema_blanket_entry_gate),
        ("HTF_DIRECTION_GATE_ENABLED", wave4_families.htf_direction_gate),
        ("WT_PERCENTILE_ENTRY_GATE_ENABLED", grey_wire_entries.wt_percentile_entry_gate),
    ):
        result = fn(npz, 1, is_long, cfg, close, safe)
        if result is not None:
            masks[name] = bool(np.asarray(result, dtype=bool)[0])
    return masks


def wave4_live_masks(indicators, is_long, cfg, current_price):
    """Evaluate only the four shared Wave 4 gates on a scalar live snapshot."""
    indicators = indicators or {}
    npz = {}
    for key, value in indicators.items():
        if value is None:
            continue
        try:
            npz[key] = np.asarray([float(value)], dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            continue

    def safe(data, key, n, default=0.0):
        if key in data:
            return np.asarray(data[key], dtype=np.float64)
        return np.full(n, default, dtype=np.float64)

    close = np.asarray([float(current_price or 0.0)], dtype=np.float64)
    return _wave4_live_masks_from_npz(npz, is_long, cfg, close, safe)
