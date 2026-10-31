"""Shared predicates for the native WT winner/pullback augment producer."""

from __future__ import annotations


def aligned(wt1: float, wt2: float, is_long: bool) -> bool:
    return wt1 > wt2 if is_long else wt1 < wt2


def producer(
    is_long: bool,
    gain_pct: float,
    peak_gain_pct: float,
    min_gain_pct: float,
    wt1_ltf: float,
    wt2_ltf: float,
    wt1_15m: float,
    wt2_15m: float,
    trough_structure_15m: float,
    peak_structure_15m: float,
) -> tuple[bool, str]:
    wt_ok = aligned(wt1_ltf, wt2_ltf, is_long) and aligned(
        wt1_15m, wt2_15m, is_long
    )
    structure_bounce = (
        trough_structure_15m > 0 if is_long else peak_structure_15m < 0
    )
    pullback = peak_gain_pct - gain_pct >= 0.5
    bounce = structure_bounce or (pullback and wt_ok)
    if gain_pct >= min_gain_pct and wt_ok:
        return True, "WINNER_WT_AUGMENT"
    if gain_pct >= 0.5 * min_gain_pct and bounce:
        return True, "BOUNCE_AUGMENT"
    return False, ""


def final_gate(
    is_long: bool,
    gain_pct: float,
    min_gain_pct: float,
    wt1_ltf: float,
    wt2_ltf: float,
    wt1_15m: float,
    wt2_15m: float,
) -> bool:
    ltf_aligned = aligned(wt1_ltf, wt2_ltf, is_long)
    fifteen_aligned = aligned(wt1_15m, wt2_15m, is_long)
    return ltf_aligned and (fifteen_aligned or gain_pct >= min_gain_pct)
