# -*- coding: utf-8 -*-
"""ported_reduce — faithful numpy twins of ez_manage/tradier_manage reduce-lifecycle switches.
Owned by the reduce wiring agent (SWITCH_WIRING_GUIDE.md). Rules: IDENTICAL to live (same thresholds,
operators, TF; 15m floor, 3m/5m->15m), NO proxies, NO fabrication (BIBLE §19). Each switch block is
gated on its cfg value differing from the effective default and applies a REAL mask to the signal.
apply() is a pure passthrough when no switch is active (returns sig unchanged).

sig is the REDUCE signal. Add reduces with `sig = sig | fire`; block with `sig = sig & ~block`.

WORKLIST STATUS (5 switches) — determined by reading each live decision site 1:1:
  WIRED (0).
  VEC_UNSUPPORTED (5) — each requires position/portfolio/per-day state not available per-bar, is a
  reduce SIZE fraction (not a boolean fire), or the only live site is a dead stub. NEVER proxied:
    INTRADAY_RATIO_MAX_TRIMS_PER_DAY — tradier_manage.py:23675 gates on _trim_counts[today] >= max
                                        (per-day trim COUNT state; ez ABSENT). Not a per-bar predicate.
    INTRADAY_RATIO_TRIM_FRAC        — tradier_manage.py:23705 is the trim FRACTION inside the intraday
                                        L/S-ratio rebalance (portfolio/account-ratio state; ez ABSENT).
                                        A sizing param, not a boolean fire.
    PARTIAL_EXIT_FRAC               — ez:58938 reads position.unrealizedProfit (gain-gated) AND is the
                                        exit FRACTION (how much to reduce), not a fire mask; tradier:33353
                                        is a dead `_=` stub.
    QUICK_REDUCE_TECHNICAL_ONLY     — ez:58964 / tradier:33361 are dead `_=` stubs. A MODE flag that
                                        changes HOW other reduces behave (technical-only vs gain), with no
                                        standalone per-bar predicate.
    WT_REDUCE_FRAC_HIGH             — ez:59234 is a reduce FRACTION whose only site is a no-op `_=1` stub
                                        farm (compares abs(wt1_1h) > frac — a fraction misused as a WT
                                        threshold, result discarded); tradier:33569 is a dead `_=` stub.
                                        A reduce-size param, not a boolean fire.
"""
import numpy as np


def apply(npz, n, is_long, cfg, sig, _safe, close):
    # === All 5 REDUCE worklist switches are VEC_UNSUPPORTED (see module docstring). ===
    # None is a pure function of the bar's indicators, so there is no faithful per-bar mask to apply.
    # Passthrough — the honest 0 (BIBLE §19: never fabricate a delta).
    return sig
