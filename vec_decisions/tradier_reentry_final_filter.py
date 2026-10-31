"""Final entry-mask application for stock reentry fires."""
from __future__ import annotations


def allows_stock_reentry(fire, is_tradier, has_closed_before, entry_signal, filter_tf, mask, index):
    """Apply the native CIRCUIT_SHARPE_GATES_FILTER_TF predicate to a stock reentry."""
    if not fire:
        return False
    if not is_tradier or not has_closed_before or entry_signal:
        return True
    tf = str(filter_tf or "OFF").strip().upper()
    if tf in ("OFF", "15M") or mask is None:
        return True
    return bool(mask[index])
