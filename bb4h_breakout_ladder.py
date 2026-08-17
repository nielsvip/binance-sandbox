"""Stateful 4h Bollinger breakout entry ladder.

The module is deliberately side-effect free.  Managers own the small per-position
state dictionary and call :func:`next_stage` from their normal evaluation loop.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def next_stage(
    state: Dict[str, Any],
    indicators: Dict[str, Any],
    price: float,
    *,
    enabled: bool,
    position_exists: bool,
    is_long: bool,
    now: float,
    breakout_pct: float = 0.25,
    basis_pct: float = 0.50,
    wt_cross_pct: float = 0.25,
    target_usd: float = 2000.0,
    stock: bool = False,
    stock_max_notional_usd: float = 2000.0,
    max_stock_shares: int = 1,
) -> Optional[dict]:
    """Return one newly-triggered ladder stage, or ``None``.

    Stage 1 requires an observed crossing of bb_pct_b_4h from <= 1 to > 1;
    this prevents a restart from treating an already-extended symbol as a new
    breakout.  Later stages are strictly ordered: basis, then WT cross.
    """
    if not enabled or not is_long or price <= 0:
        return None
    if stock and price > stock_max_notional_usd:
        # A stock order may never exceed the explicit per-share affordability cap.
        # Binance/fractional callers do not take this branch.
        return None
    if not state:
        state.update({"stage": 0, "last_bb": _f(indicators.get("bb_pct_b_4h"), 0.5)})
        return None

    stage = int(state.get("stage", 0) or 0)
    bb = _f(indicators.get("bb_pct_b_4h"), 0.5)
    prev_bb = _f(indicators.get("bb_pct_b_4h_prev"), state.get("last_bb", 0.5))
    breakout = prev_bb <= 1.0 and bb > 1.0
    state["last_bb"] = bb

    if stage == 0 and breakout:
        fraction = breakout_pct
        name = "BREAKOUT"
    elif stage == 1 and position_exists and now > _f(state.get("breakout_ts"), now):
        basis = _f(indicators.get("dc_basis_4h"))
        if basis <= 0 or price > basis:
            return None
        fraction = basis_pct
        name = "DC_BASIS_4H"
    elif stage == 2 and position_exists:
        wt1 = _f(indicators.get("wt1_1h"))
        wt2 = _f(indicators.get("wt2_1h"))
        prev_wt1 = _f(indicators.get("wt1_1h_prev"), wt2)
        # Indicator snapshots commonly expose only wt1_4h_prev; use the
        # current wt2 as the prior crossover baseline rather than inventing a
        # wt2 value equal to wt1 (which would create false crosses).
        prev_wt2 = _f(indicators.get("wt2_1h_prev"), wt2)
        if not (prev_wt1 <= prev_wt2 and wt1 > wt2):
            return None
        fraction = wt_cross_pct
        name = "WT_1H_CROSS"
    else:
        return None

    if stock:
        # Whole-share sizing: ordinary stocks use the $ target; a high-priced
        # stock whose target buys only one share is capped at that one share.
        share_cap = max(1, int(target_usd / price))
        if share_cap <= max_stock_shares:
            share_cap = max_stock_shares
        allocated = _f(state.get("stock_shares"))
        qty = min(max(0.0, share_cap - allocated), max(1.0, float(int(share_cap * fraction))))
        if qty <= 0:
            return None
        state["stock_shares"] = allocated + qty
    else:
        qty = (target_usd * fraction) / price

    if stage == 0:
        state.update({"stage": 1, "breakout_ts": now})
    elif stage == 1:
        state.update({"stage": 2, "basis_ts": now})
    else:
        state.update({"stage": 3, "wt_ts": now})

    return {"stage": stage, "name": name, "fraction": fraction, "quantity": qty}
