"""Tradier RULE_A_RETEST entry mask on the frozen 15-minute decision grid.

The live rule's sub-15m stochastic trigger is mapped to the available 15m
stochastic arrays under SWITCH_WIRING_GUIDE.md §4. No 3m/5m series is inferred.
The caller owns flat-position and tradeability gates and emits the OPEN reason.
"""
import numpy as np
from vec_decisions.process_position_stocks__rule_a_retest import (
    _rule_a_retest_fires,
    check_rule_a_retest_vec,
)


def rule_a_retest_mask(npz, n, is_long, cfg, safe):
    """Return bars where the stock RULE_A_RETEST entry predicate fires."""
    close = safe(npz, "close", n, 0.0)
    k_15m = safe(npz, "stoch_k_15m", n, 50.0)
    d_15m = safe(npz, "stoch_d_15m", n, 50.0)
    k_15m_prev = np.roll(k_15m, 1)
    if n:
        k_15m_prev[0] = k_15m[0]
    return check_rule_a_retest_vec(
        cfg,
        close,
        safe(npz, "wt1_D", n, 0.0),
        safe(npz, "wt2_D", n, 0.0),
        safe(npz, "wt1_W", n, 0.0),
        safe(npz, "wt2_W", n, 0.0),
        safe(npz, "dc_basis_D", n, 0.0),
        safe(npz, "atr_D", n, 0.0),
        safe(npz, "wt1_15m", n, 0.0),
        safe(npz, "wt2_15m", n, 0.0),
        safe(npz, "wt1_1h", n, 0.0),
        safe(npz, "wt2_1h", n, 0.0),
        k_15m,
        d_15m,
        k_15m_prev,
        is_long,
    )


def rule_a_retest_live(config, indicators, current_price, is_long):
    """Live scalar adapter using 15m indicator snapshots and the shared core."""
    if not bool(getattr(config, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
        return False, ""
    indicators = indicators or {}
    try:
        k_15m = indicators.get("stoch_k_15m")
        if k_15m is None:
            k_15m = indicators.get("k_15m")
        d_15m = indicators.get("stoch_d_15m")
        if d_15m is None:
            d_15m = indicators.get("d_15m")
        k_15m = float(50.0 if k_15m is None else k_15m)
        d_15m = float(50.0 if d_15m is None else d_15m)
        k_15m_prev = indicators.get("stoch_k_15m_prev", indicators.get("k_15m_prev"))
        if k_15m_prev is None:
            return False, ""
        k_15m_prev = float(k_15m_prev)
        price = float(current_price or 0.0)
        fields = (
            float(indicators.get("wt1_D", 0.0) or 0.0),
            float(indicators.get("wt2_D", 0.0) or 0.0),
            float(indicators.get("wt1_W", 0.0) or 0.0),
            float(indicators.get("wt2_W", 0.0) or 0.0),
            float(indicators.get("dc_basis_D", 0.0) or 0.0),
            float(indicators.get("atr_D", 0.0) or 0.0),
            float(indicators.get("wt1_15m", 0.0) or 0.0),
            float(indicators.get("wt2_15m", 0.0) or 0.0),
            float(indicators.get("wt1_1h", 0.0) or 0.0),
            float(indicators.get("wt2_1h", 0.0) or 0.0),
        )
        mult = float(getattr(config, "BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT", 0.30))
    except (TypeError, ValueError):
        return False, ""
    fire = _rule_a_retest_fires(
        price, *fields, k_15m, d_15m, k_15m_prev, is_long, mult
    )
    if not fire:
        return False, ""
    return True, f"RULE_A_RETEST_{'LONG' if is_long else 'SHORT'}_px{price:.4f}"
