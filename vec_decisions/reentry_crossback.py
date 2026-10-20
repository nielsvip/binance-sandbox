"""REENTRY_CROSSBACK — the ONE condition that arms every reentry pathway after an exit (USER 2026-10-20 law).

"Mandatory reentry only operates if the exit price is passed." After an exit, a reentry (any pathway: HARDCODED_RALLY, HTF_WT_CHURN,
SRS / PRICE_CROSS_BACK, LEGACY_PSR, OBLIGATORY, TIER, STOP_RECLAIM, GAP morning rebuy, …) may fire ONLY on a bar where price has passed
the exit price again in the trade's favourable direction by the configured buffer:
    LONG  : px >= exit_px * (1 + REENTRY_EXIT_RECLAIM_BUFFER_PCT / 100)
    SHORT : px <= exit_px * (1 - REENTRY_EXIT_RECLAIM_BUFFER_PCT / 100)
(buffer default 0.2 %, the existing B00 EXIT_RECLAIM semantics: vec_decisions/lane_vec_reentry2.reclaim_buffer_mask, ez_manage B00).
A pathway that fires without the cross-back is NOT a reentry: it is a fresh entry and must come from the fresh-entry signal with
every entry gate (in the vector: entry_sig + the order guards incl. BOTTOM/TOP). Kill switch REENTRY_REQUIRES_EXIT_CROSSBACK_ENABLED
(default True). Shared by v12_quick_engine (bar loop), tradier_manage and ez_manage (live REENTRY dispatch).
"""
from __future__ import annotations

from typing import Any, Callable

Get = Callable[[str, Any], Any]


def _f(v: Any, d: float) -> float:
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


def law_enabled(get: Get) -> bool:
    v = get("REENTRY_REQUIRES_EXIT_CROSSBACK_ENABLED", True)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def buffer_pct(get: Get) -> float:
    return max(0.0, _f(get("REENTRY_EXIT_RECLAIM_BUFFER_PCT", 0.2), 0.2))


def crossed_back(get: Get, is_long: bool, px: float, exit_px: float) -> bool:
    """True when price has passed the exit price again in the favourable direction (with the buffer)."""
    px, ex = _f(px, 0.0), _f(exit_px, 0.0)
    if px <= 0 or ex <= 0:
        return False
    b = buffer_pct(get) / 100.0
    return px >= ex * (1.0 + b) if is_long else px <= ex * (1.0 - b)


def allows_reentry(get: Get, is_long: bool, px: float, exit_px: float) -> bool:
    """Gate for a reentry pathway fire: law off -> always; law on -> only on a cross-back bar."""
    return (not law_enabled(get)) or crossed_back(get, is_long, px, exit_px)
