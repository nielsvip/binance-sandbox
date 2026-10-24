"""Stocks swing-budget entry knob core (USER 2026-10-24: template-governed entries).

Single source of truth for the ENABLE_TR_SWING_ENTRIES knob, an exact mirror
of the live gate (tradier_manage.is_swing_entry_allowed, knob half only):
  knob is True  -> entries flow (live still applies the ratio-adjusted budget
                   arithmetic downstream; that math reads the live market
                   snapshot + scraper sentiment + movement intensity, none of
                   which exists in a frozen NPZ, so vec is budget-blind past
                   the knob by construction).
  knob not True -> live blocks every stocks OPEN except mandatory price-cross
                   reentries (MANDATORY_REENTRY_PRICE_CROSS exit+cur in the
                   favourable direction). Vec mirrors this by masking the
                   fresh-entry signal; in-loop reentry fires never read
                   entry_sig, so mandatory cross-back reentries keep flowing
                   on both legs.
Tradier-only: crypto live (ez_manage) has no such gate, so in crypto MODE the
knob is inert even when the field exists on the config.
Gate switch: ENABLE_TR_SWING_ENTRIES (caller-owned, like every family).
"""
from __future__ import annotations


def knob_allows(cfg) -> bool:
    try:
        mode = getattr(cfg, "MODE", "crypto")
    except Exception:
        mode = "crypto"
    if mode != "tradier":
        return True
    try:
        return getattr(cfg, "ENABLE_TR_SWING_ENTRIES", False) is True
    except Exception:
        return False


def knob_allows_scalar(cfg) -> bool:
    return bool(knob_allows(cfg))


def entry_mask_vec(cfg, n):
    import numpy as np

    if knob_allows(cfg):
        return None
    return np.zeros(int(n), dtype=bool)
