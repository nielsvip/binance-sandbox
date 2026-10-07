"""
vec_paths/ratio_size.py — RATIO_BOOST_L / RATIO_CUT_L sizing multiplier (vec approximation).

SOURCE: tradier_manage.py:9337-9383 (execute_now RATIO-AWARE SIZING block)

Live logic summary:
  - At every execute_now call for an augment/entry, compute L/S ratio across ALL
    open positions in the same account.
  - _r = sum(LONG_value) / max(sum(SHORT_value), 1.0)
  - BEAR_SCENARIO_SYMBOLS (GLD, USO, etc.) flip their contribution (LONG = short bet).
  - If effective_long and _r < _r_min (0.50): quantity *= 1.5 + reason RATIO_BOOST_L
  - If effective_long and _r > _r_max (2.00): quantity *= 0.6 + reason RATIO_CUT_L
  - If not effective_long (SHORT) and _r > _r_max: quantity *= 1.5 + reason RATIO_BOOST_S
  - If not effective_long and _r < _r_min: quantity *= 0.6 + reason RATIO_CUT_S
  - Safety gate: TRADIER_RATIO_REQUIRE_MIN_GAIN=False → always allow boost.

Vec approximation:
  - No live portfolio snapshot. backtest sets calculate_unified_market_ratio=0.5 (neutral).
  - We implement this as a neutral λ=0.5 ratio → NEITHER boost NOR cut fires by default.
  - The portfolio_state dict tracks positions by symbol→{side, qty, price} and is updated
    by the caller (VecEngine) on every open/close/augment.
  - With real position tracking we can compute the L/S ratio at each bar.

LIMITATIONS vs live:
  - No cross-account aggregation (real engine has per-account ratio).
  - BEAR_SCENARIO_SYMBOLS from config — configurable via VecConfig.
  - Live ratio is "live position values", vec uses position_qty * entry_price (approx).
  - Min-gain gate: TRADIER_RATIO_REQUIRE_MIN_GAIN is off by default, so boost is allowed.

See validate_against_live.py for accuracy measurements.
"""
from __future__ import annotations
from typing import Dict, Any, Optional, Set

# Default BEAR_SCENARIO_SYMBOLS (from config_tradier.py:473)
DEFAULT_BEAR_SCENARIO_SYMBOLS: Set[str] = {
    "GLD", "PHYS", "SLV", "USO", "UNG", "DBO", "BNO", "XLE", "VDE", "FENY",
    "GDX", "GDXJ", "SIL", "OIH", "XOP", "ERX", "UCO", "BOIL", "DRIP", "UVXY",
    "SQQQ", "SDOW", "SPXS", "SH", "PSQ", "DOG", "RWM", "DRV", "TZA", "FAZ",
    "DUST", "DXYZ",
}


def compute_size_multiplier(
    portfolio_state: Dict[str, Dict[str, Any]],
    symbol: str,
    side: str,
    cfg,
) -> float:
    """Compute L/S ratio-based size multiplier (mirrors execute_now:9337-9383).

    Parameters
    ----------
    portfolio_state : dict
        {position_key: {side, qty, price}} — all currently open positions in
        the account. position_key format: "SYMBOL_LONG" or "SYMBOL_SHORT".
    symbol : str
        The symbol being traded.
    side : str
        "LONG" or "SHORT".
    cfg : VecConfig
        Config with:
          RATIO_BOOST_ENABLED: bool (default True) — mirror RATIO_AWARE_SIZING
          LS_RATIO_MIN: float (default 0.50)
          LS_RATIO_MAX: float (default 2.00)
          TRADIER_RATIO_REQUIRE_MIN_GAIN: bool (default False)
          TRADIER_RATIO_BOOST_MIN_GAIN_PCT: float (default 1.0)
          BEAR_SCENARIO_SYMBOLS: set (default DEFAULT_BEAR_SCENARIO_SYMBOLS)

    Returns
    -------
    float: size multiplier (1.0 = no change, 1.5 = boost, 0.6 = cut)
    """
    if not getattr(cfg, "RATIO_BOOST_ENABLED", True):
        return 1.0

    bear_set: Set[str] = getattr(cfg, "BEAR_SCENARIO_SYMBOLS", DEFAULT_BEAR_SCENARIO_SYMBOLS)
    r_min = float(getattr(cfg, "LS_RATIO_MIN", 0.50))
    r_max = float(getattr(cfg, "LS_RATIO_MAX", 2.00))
    ratio_gain_gate = bool(getattr(cfg, "TRADIER_RATIO_REQUIRE_MIN_GAIN", False))
    ratio_min_gain = float(getattr(cfg, "TRADIER_RATIO_BOOST_MIN_GAIN_PCT", 1.0))

    long_val = 0.0
    short_val = 0.0

    for pk, pos_info in portfolio_state.items():
        _sym = pk.rsplit("_", 1)[0] if "_" in pk else pk
        _pos_side = pk.rsplit("_", 1)[1] if "_" in pk else "LONG"
        _qty = float(pos_info.get("qty", 0.0))
        _price = float(pos_info.get("price", 0.0))
        if _qty <= 0 or _price <= 0:
            continue
        _v = _qty * _price
        _is_bear = _sym.upper() in bear_set
        if _pos_side == "LONG":
            if _is_bear:
                short_val += _v
            else:
                long_val += _v
        elif _pos_side == "SHORT":
            if _is_bear:
                long_val += _v
            else:
                short_val += _v

    _r = long_val / max(short_val, 1.0)

    _this_bear = symbol.upper() in bear_set
    _effective_long = (side == "LONG" and not _this_bear) or (side == "SHORT" and _this_bear)

    # Allow boost regardless of gain gate when TRADIER_RATIO_REQUIRE_MIN_GAIN=False (default).
    _allow_boost = not ratio_gain_gate  # gain gate unsupported in vec (no live position gain)

    if _effective_long and _r < r_min and _allow_boost:
        return 1.5  # RATIO_BOOST_L
    elif not _effective_long and _r > r_max and _allow_boost:
        return 1.5  # RATIO_BOOST_S
    elif _effective_long and _r > r_max:
        return 0.6  # RATIO_CUT_L
    elif not _effective_long and _r < r_min:
        return 0.6  # RATIO_CUT_S

    return 1.0


def ratio_reason_tag(mult: float, r: float, side: str, symbol: str, cfg) -> str:
    """Return a reason tag matching live format: |RATIO_BOOST_L(R=0.50) etc."""
    bear_set: Set[str] = getattr(cfg, "BEAR_SCENARIO_SYMBOLS", DEFAULT_BEAR_SCENARIO_SYMBOLS)
    _this_bear = symbol.upper() in bear_set
    _effective_long = (side == "LONG" and not _this_bear) or (side == "SHORT" and _this_bear)
    if mult > 1.0:
        label = "RATIO_BOOST_L" if _effective_long else "RATIO_BOOST_S"
    elif mult < 1.0:
        label = "RATIO_CUT_L" if _effective_long else "RATIO_CUT_S"
    else:
        return ""
    return f"|{label}(R={r:.2f})"
