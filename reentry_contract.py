"""Shared, side-aware mandatory re-entry state and opposition rules."""
from __future__ import annotations


def get_exit_value(mapping, position_key, symbol, default=0):
    """Read side-aware state first; symbol fallback supports legacy persisted state."""
    if not mapping:
        return default
    value = mapping.get(position_key)
    if value not in (None, 0, 0.0, ""):
        return value
    return mapping.get(symbol, default)


def set_exit_state(manager, position_key, timestamp, price):
    """Record without a symbol alias, so LONG and SHORT can never overwrite each other."""
    manager.last_exit_times[position_key] = timestamp
    manager.last_exit_prices[position_key] = price


def reentry_opposition(indicators, is_long, timeframes=("15m", "1h", "4h", "D")):
    """Return TFs where WT and/or Stoch momentum opposes the requested side."""
    opposed = []
    detail = {}
    for tf in timeframes:
        wt1 = float(indicators.get(f"wt1_{tf}", 0) or 0)
        wt2 = float(indicators.get(f"wt2_{tf}", 0) or 0)
        sk = float(indicators.get(f"stoch_k_{tf}", 50) or 50)
        sd = float(indicators.get(f"stoch_d_{tf}", 50) or 50)
        wt_against = (wt1 < wt2) if is_long else (wt1 > wt2)
        stoch_against = (sk < sd) if is_long else (sk > sd)
        if wt_against or stoch_against:
            opposed.append(tf)
        detail[tf] = {"wt": wt_against, "stoch": stoch_against}
    return opposed, detail


def update_reentry_trace(trace, position_key, is_long, exit_price, current_price, timestamp, opposed):
    """Persist every flat-bar observation and the worst favorable overshoot."""
    row = trace.setdefault(position_key, {
        "pending": True,
        "flat_bars": 0,
        "max_overshoot_pct": 0.0,
        "first_flat_ts": timestamp,
        "last_flat_ts": timestamp,
        "last_opposed_tfs": [],
    })
    row["flat_bars"] += 1
    row["pending"] = True
    row["last_flat_ts"] = timestamp
    row["last_opposed_tfs"] = list(opposed)
    if exit_price > 0 and current_price > 0:
        raw = (
            (current_price - exit_price) / exit_price * 100.0
            if is_long else (exit_price - current_price) / exit_price * 100.0
        )
        row["max_overshoot_pct"] = max(float(row["max_overshoot_pct"]), max(0.0, raw))
    return row
