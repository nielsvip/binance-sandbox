"""AUG_GAIN_GATE — vec twin of the live execute_trade_action AUGMENT gain gate (ez_manage.py ~24930-24997, crypto).

LIVE (every AUGMENT on an existing position whose value exceeds MIN_POSITION_SIZE):
  gain < 0                                   -> BLOCKED_LOSER_KILL (never augment a loser)
  gain < thr                                 -> BLOCKED_GAIN_GATE
  thr = MIN_GAIN (3.0); thr = 0.5*MIN_GAIN only at a BOUNCE:
        price beyond the position's last reduction price (LONG below / SHORT above)
    AND wt1_1h vs wt2_1h aligned with the side
    AND 15m wt structure: LONG trough 'HL' / SHORT peak 'LH'
This is a second gate on top of UNIVERSAL_AUGMENT_GAIN_GATE (vec_decisions/uagain_gate). The vec modelled only the first, so the
PULLBACK_AUG ladder augmented at 1.5-3% where live refuses (parity-loop-crypto it13 SNX: BLOCKED_GAIN_GATE_2.14%_lt_3.00%).
NPZ encodes wt_trough_structure_15m / wt_peak_structure_15m as int8 (+1 HL / -1 LL, +1 HH / -1 LH; vec_decisions/grey_wire_exits).
"""
from __future__ import annotations


def threshold(cfg, is_long: bool, px: float, last_exit_px: float, wt1_1h: float, wt2_1h: float, trough_15m: float, peak_15m: float) -> float:
    # USER 2026-10-24: per-sym board tier (never GLOBAL MIN_GAIN); the bounce leg
    # tiers off AUGMENT_BOUNCE_MIN_GAIN_PCT via the shared typed dispatcher — the
    # same numbers live enforces in execute_trade_action's GAIN_GATE.
    from vec_decisions.uagain_gate import effective_min_gain as _eff
    generic = float(_eff(cfg))
    below = last_exit_px > 0 and ((is_long and px < last_exit_px) or ((not is_long) and px > last_exit_px))
    h1 = (wt1_1h > wt2_1h) if is_long else (wt1_1h < wt2_1h)
    bounce = (trough_15m > 0) if is_long else (peak_15m < 0)
    if below and h1 and bounce:
        try:
            from vec_decisions.live_unw_gates import augment_tier_min_gain as _tier
            return float(_tier(lambda k, d=None: getattr(cfg, k, d), "BOUNCE_AUG", generic))
        except Exception:
            return generic * 0.5
    return generic


def passes(cfg, is_long: bool, px: float, raw_gain_pct: float, pos_value: float, last_exit_px: float,
           wt1_1h: float, wt2_1h: float, trough_15m: float, peak_15m: float) -> bool:
    if pos_value <= float(getattr(cfg, "MIN_POSITION_SIZE", 1.0) or 1.0):
        return True  # live foothold branch (separate pile-on rules), not the gain gate
    if raw_gain_pct < 0.0:
        return False
    return raw_gain_pct >= threshold(cfg, is_long, px, last_exit_px, wt1_1h, wt2_1h, trough_15m, peak_15m)

def latest_reduction_price(trades, events) -> float:
    candidates = []
    for rows, event_types, price_keys in (
        (trades, {"CLOSE"}, ("exit_price", "price")),
        (events, {"REDUCE"}, ("price", "exit_price")),
    ):
        for row in reversed(rows):
            if str(row.get("type", "")).upper() not in event_types:
                continue
            price = 0.0
            for key in price_keys:
                try:
                    price = float(row.get(key, 0) or 0)
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0:
                    break
            try:
                timestamp = float(row.get("ts", row.get("exit_ts", 0)) or 0)
            except (TypeError, ValueError):
                timestamp = 0.0
            if price > 0:
                candidates.append((timestamp, 1 if rows is events else 0, price))
            break
    return max(candidates)[2] if candidates else 0.0

