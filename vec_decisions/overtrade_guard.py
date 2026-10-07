"""OVERTRADE_GUARD — vector twin of the live execute_now per-symbol per-UTC-day fill cap (Agent C2, staged b5b).

LIVE SOURCE: ez_manage.py:30405-30470 (crypto) / tradier_manage.py:13843-13900 (stocks): for an OPEN/AUGMENT/ENTRY action (not CLOSE/REDUCE) whose reason
carries none of the emergency tokens (RIDICULOUS, BREAK_REVERSE, ALL_TF_AGAINST, INTERVENTION, MANUAL, WT_3M_FORCE_OPEN, OBLIGATORY, MOMENTUM_WATCHDOG,
GOLDEN_RULE, WATCHDOG, DC_BREAKOUT, REENTRY [crypto]; stocks: + the mandatory-reentry queue flag) the order is BLOCKED once the key already has
>= TRADES_PER_SYM_PER_DAY_MAX executed fills today (UTC date) in data/history (types OPEN, AUGMENT, REENTRY, QUICK_OPEN, QUICK_AUGMENT; CLOSE/REDUCE not
counted). Defaults: config.py 6 / config_tradier.py 8.

VECTOR MAPPING: a vector OPEN/AUGMENT = one executed fill. An entry whose vector reason contains 'REENTRY' (HARDCODED_RALLY_REENTRY, REENTRY_MANDATORY) is
exempt (live: reentry reasons carry REENTRY / the mandatory-reentry flag) but still COUNTS toward the day total; every other vector entry reason (B_*, ENTRY_SIGNAL)
is a fresh entry and is capped. Vector reasons do not carry the live emergency tokens, so no further exemption can be modelled (documented approximation).
"""
from __future__ import annotations

EMERGENCY = ("RIDICULOUS", "BREAK_REVERSE", "ALL_TF_AGAINST", "INTERVENTION", "MANUAL", "WT_3M_FORCE_OPEN", "OBLIGATORY", "MOMENTUM_WATCHDOG",
             "GOLDEN_RULE", "WATCHDOG", "DC_BREAKOUT", "REENTRY")


def day_of(ts_value: float) -> int:
    t = float(ts_value)
    return int((t / 1000.0 if t > 1e11 else t) // 86400)


def blocked(cfg, day_counts: dict, day: int, entry_reason: str) -> bool:
    mx = int(getattr(cfg, 'TRADES_PER_SYM_PER_DAY_MAX', 8) or 0)
    if mx <= 0:
        return False
    r = str(entry_reason or '').upper()
    # stocks (tradier_manage.py:13843-13860): only RIDICULOUS/BREAK_REVERSE/ALL_TF_AGAINST/INTERVENTION/MANUAL/WT_3M_FORCE_OPEN/OBLIGATORY + the mandatory
    # price-cross RECLAIM route are exempt — ordinary REENTRY (incl. HARDCODED_RALLY) IS capped; crypto (ez_manage.py:30405) additionally exempts REENTRY.
    _tokens = EMERGENCY if str(getattr(cfg, 'MODE', 'crypto')) != 'tradier' else tuple(t for t in EMERGENCY if t not in ('REENTRY', 'MOMENTUM_WATCHDOG', 'GOLDEN_RULE', 'WATCHDOG', 'DC_BREAKOUT')) + ('RECLAIM',)
    if any(t in r for t in _tokens):
        return False
    return int(day_counts.get(day, 0)) >= mx
