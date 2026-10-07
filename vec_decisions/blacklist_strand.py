# -*- coding: utf-8 -*-
"""blacklist_strand — vector twin of the BLACKLIST_SYMBOLS strand behavior.

Live (crypto; tradier_manage has stub reads only):
  * ez_manage.py execute_trade_action BACKTEST_CHANGE_46: augments on a blacklisted
    symbol are refused (``BLOCKED_BLACKLISTED_<symbol>``); reductions still allowed,
    fresh OPENs are NOT blocked (the entry path never checks the list).
  * ez_manage.py process_position BACKTEST_CHANGE_46: a blacklisted symbol short-circuits
    to ``NO_ACTION:BLACKLISTED_<symbol>`` — no exits, no reduces, no stop management.
    The position strands (opens, then is never managed).

Vector twin: when the simulated symbol is blacklisted, the bar walk opens entries
normally but skips ALL position management (exits/augments/reduces); the window-end
FINAL_MTM flattens for accounting. ``is_blacklisted()`` replicates live's exact
membership test (``symbol in getattr(config, "BLACKLIST_SYMBOLS", [])``), tolerating a
vec-side ``_LONG``/``_SHORT`` suffix live symbols never carry.
"""
from __future__ import annotations


def _bare(sym) -> str:
    s = str(sym or "")
    for sfx in ("_LONG", "_SHORT"):
        if s.endswith(sfx):
            return s[: -len(sfx)]
    return s


def is_blacklisted(sym, cfg) -> bool:
    """True iff live would strand this symbol (exact live membership semantics)."""
    try:
        if str(getattr(cfg, "MODE", "crypto")) == "tradier":
            return False  # vec-side venue scoping: live stocks never reads the list (stub only)
    except Exception:
        pass
    try:
        bl = getattr(cfg, "BLACKLIST_SYMBOLS", [])
    except Exception:
        return False
    if not bl:
        return False
    try:
        if isinstance(bl, (list, tuple, set, frozenset)):
            return _bare(sym) in bl
        return _bare(sym) in str(bl)
    except Exception:
        return False
