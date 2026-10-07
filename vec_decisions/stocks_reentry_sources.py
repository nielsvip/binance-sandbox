"""STOCKS_REENTRY_SOURCES — vector twins of the 15m+-computable pathways of the live stock reentry ladder (Agent C2, queue item 002).

LIVE SOURCE: tradier_manage.py StockStrategy.evaluate_reentry (ladder order, first hit wins): MU_CORRECTION (MU only, default off) -> HARDCODED_RALLY (21580-21610,
already in simulate_one) -> PRICE_CROSS_BACK (21612-21656) -> RECOVERY_AUG (positionAmt>0 only) -> [hardcool TRADIER_REENTRY_HARDCOOL_MIN 15m, 21750] -> TIER2_CHASE
(trend 0.3% past exit; needs k5m = inert) -> EMA200_1H_BOUNCE (21790-21808) -> BOUNCE_REENTRY_K_RESET (k5m: inert) -> WT_2of3 (needs 5m: inert).
2026-10-07 USER KILL: TIER2_FORCED (time-based reopen) DELETED everywhere.
Implemented here (all use 15m+ arrays only): PRICE_CROSS_BACK (favorable cross OR within PRICE_CROSS_BACK_BAND_PCT=0.3% of the exit price, age < PRICE_CROSS_BACK_MAX_AGE_MIN),
EMA200_1H_BOUNCE. Inert by rule (3m/5m data absent): TIER2_CHASE momentum leg, BOUNCE_REENTRY_K_RESET, WT_2of3, the stoch gates.
"""
from __future__ import annotations


def fires(cfg, is_long: bool, px: float, i: int, last_exit_px: float, age_min: float, ema200, ema200_prev) -> str:
    """Return the pathway name that fires on this bar ('' = none)."""
    if not (last_exit_px and last_exit_px > 0 and px and px > 0):
        return ''
    hardcool = float(getattr(cfg, 'TRADIER_REENTRY_HARDCOOL_MIN', 15.0) or 0.0)
    if bool(getattr(cfg, 'PRICE_CROSS_BACK_REENTRY_ENABLED', True)):
        mx = float(getattr(cfg, 'PRICE_CROSS_BACK_MAX_AGE_MIN', 525_600_000.0) or 0.0)
        band = float(getattr(cfg, 'PRICE_CROSS_BACK_BAND_PCT', 0.3) or 0.0)
        if 0.0 <= age_min < mx:
            fav = (px >= last_exit_px) if is_long else (px <= last_exit_px)
            dist = abs(px - last_exit_px) / last_exit_px * 100.0
            if fav or dist <= band:
                return 'PRICE_CROSS_BACK'
    # 2026-10-07 USER KILL: TIER2_FORCED (time-based reopen) DELETED — reentry is ALWAYS technical and ONLY on trend continuation.
    if ema200 is not None and i < len(ema200):
        e = float(ema200[i])
        if e > 0:
            ep = float(ema200_prev[i]) if ema200_prev is not None and i < len(ema200_prev) and ema200_prev[i] > 0 else e
            if is_long:
                if px > e and (px <= e * 1.002 or px <= ep * 1.002):
                    return 'EMA200_1H_BOUNCE'
            else:
                if px < e and (px >= e * 0.998 or px >= ep * 0.998):
                    return 'EMA200_1H_BOUNCE'
    return ''
