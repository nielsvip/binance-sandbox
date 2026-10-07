# -*- coding: utf-8 -*-
"""target_dc_reentry_gate — staged vec twin control for TARGET-DC immediate reentry.

LANE aug-reentry (LG-04). Status: STAGED ONLY — the parent performs the
coordinated cut. Do NOT import from the deployed engine until merged.

BACKGROUND (T1, see ../T1_LG04_REENTRY_MODEL.md §1 row 2):
The 2026-09-26 user mandate "if exit was TARGET just before dc_high (long) /
dc_low (short) and price keeps rising/falling, reentry immediate — no
cooldowns" exists in the vector engine at THREE unconditional sites
(v12_quick_engine.simulate_one):
  (a) cooldown bypass just before the `cd > 0` gate;
  (b) `fire = True` reentry leg next to HARDCODED_RALLY;
  (c) close-site `cd = 0` (instead of cooldown_bars) right after appending
      the TARGET CLOSE trade.
Live (ez_manage / tradier_manage) has NO equivalent: no TARGET-conditioned
reentry exists on either venue (grep-verified 2026-10-04). The vec behavior
is therefore vec-only and hardcoded ON with no switch — it cannot be swept,
cannot be disabled for parity measurement, and its ledger share is unknown.

THIS MODULE centralizes the predicate (one definition, no drift between the
three sites) behind ONE master switch:

  TARGET_DC_IMMEDIATE_REENTRY_ENABLED (default True = today's behavior)

Default True keeps the baseline bit-identical (neutral stage); False is the
sweep/test option that restores standard cooldown + entry-gate behavior after
TARGET exits. The live-side equivalent is specified in
../LIVE_SPEC_TARGET_DC.md for Agent D; when live implements it, this master
stays the vec-side control and the two are parity-tested against each other.

BIBLE §39 COMPLIANCE: real predicate over real ledger state (last exit reason
+ exit price + current price + side) + call sites at the three existing
heuristic sites (the logic lives ONCE here). No getattr no-ops, no mask
perturbations. Integrity: master True reproduces today's ledger exactly
(proven by neutral-stage fingerprints in ../T2_PROOF.md); master False must
change the ledger only via removed TARGET reentries (same proof file).
"""
from __future__ import annotations


MASTER = "TARGET_DC_IMMEDIATE_REENTRY_ENABLED"


def enabled(cfg) -> bool:
    """Master switch. Default True = today's hardcoded behavior (neutral)."""
    return bool(getattr(cfg, MASTER, True))


def is_target_dc_reason(reason) -> bool:
    """True when an exit reason is a TARGET-DC-channel exit.

    Mirrors the three inline checks verbatim: 'TARGET' in reason and 'dc_'
    in reason.lower(). Covers DAYTRADE_DC_TARGET_* reasons
    (vec_decisions.dc_channel_exits.daytrade_dc_exit).
    """
    try:
        r = str(reason or "")
    except Exception:
        return False
    return ("TARGET" in r) and ("dc_" in r.lower())


def last_exit(trades):
    """(exit_price, exit_reason) of the most recent CLOSE trade, else (0.0, '')."""
    try:
        if not trades:
            return 0.0, ""
        t = trades[-1]
        px = float(t.get("exit_price", 0) or 0)
        rs = str(t.get("exit_reason", "") or t.get("reason", ""))
        return px, rs
    except Exception:
        return 0.0, ""


def trend_continues(is_long: bool, px: float, exit_px: float) -> bool:
    """Price keeps trending past the TARGET exit level (strict, live slope idiom)."""
    try:
        if not (exit_px and exit_px > 0 and px and px > 0):
            return False
        return (px > exit_px) if is_long else (px < exit_px)
    except Exception:
        return False


def fires(cfg, is_long: bool, px: float, trades) -> bool:
    """Full predicate: master ON + last exit was TARGET-DC + trend continues."""
    if not enabled(cfg):
        return False
    try:
        if not (trades and len(trades)):
            return False
        exit_px, reason = last_exit(trades)
        if not is_target_dc_reason(reason):
            return False
        return trend_continues(is_long, px, exit_px)
    except Exception:
        return False
