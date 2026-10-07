"""GAIN_LADDER_AUGMENT — live-parity augment initiator (UAG ladder + pullback dip).

2026-09-28 PARITY EXTRACTION (user mandate "absolute parity; augment kicks in on dips
after 1% or obligatory after around 3%"). ONE pure per-bar core predicate consumed by
v12_quick_engine's position loop; no state, no numpy needed, no side effects.

LIVE SOURCE:
- ez_manage.py:31461-31525 — UNIVERSAL_AUGMENT_GAIN_GATE (2026-05-26 USER MANDATE):
  gain_since_last_add = (px - last_augmentation_price)/last_augmentation_price (entry
  price when the position has never augmented), side-adjusted; the ladder tier is
  AUGMENT_MIN_GAIN_PCT (config.py:135 / config_tradier.py:1559, default 3.0, fallback
  MIN_GAIN_TO_BUY_AGGRESSIVELY 3.0 — ez_manage.py:31484).
- ez_manage.py:31503-31515 + 30943-30990 — PULLBACK exception: PULLBACK_AUGMENT_ENABLED
  (default True) allows the augment when max_gain >= AUGMENT_MIN_GAIN_PCT and
  raw_gain >= 0.5*AUGMENT_MIN_GAIN_PCT and (max_gain - raw_gain) >=
  PULLBACK_AUGMENT_REVERSAL_MIN (default 1.0) — "the dip after +1%".
- config.py:132 / config_tradier.py:1557 — AUGMENT_ONLY_WHEN_PROFITABLE(_TRADIER):
  never augment a position with gain <= 0. BASE RULE, applied before the ladder.
- ez_manage.py:31736 — AUGMENTATION_COOLDOWN_SECONDS map applies to ALL augments at the
  choke point; vector converts seconds -> bars for the sim base timeframe.

NOT mirrored (no functional live read exists — do not fake): AUGMENT_FALLBACK_GAIN_PCT,
AUGMENT_BOUNCE_MIN_GAIN_PCT, AUGMENT_BREAKOUT_MIN_GAIN_PCT (tradier_manage.py:32293 is a
vigilance stub `_=_aug`, not logic).
"""
from __future__ import annotations


def effective_min_gain(config):
    """MIRROR of live_entry_gates.effective_min_gain (Agent D batch5, LG-19): AUGMENT_MIN_GAIN_PCT>0 = explicit override, else MIN_GAIN_TO_BUY_AGGRESSIVELY; floor 2.5."""
    try:
        a = float(getattr(config, "AUGMENT_MIN_GAIN_PCT", 0.0) or 0.0)
    except (TypeError, ValueError):
        a = 0.0
    try:
        m = float(getattr(config, "MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0) or 3.0)
    except (TypeError, ValueError):
        m = 3.0
    return max(2.5, a if a > 0 else m)



def ladder_params(config):
    """Resolve every threshold the live UAG block reads, with live defaults."""
    enabled = bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True))
    min_gain = effective_min_gain(config)
    pb_enabled = bool(getattr(config, "PULLBACK_AUGMENT_ENABLED", True))
    pb_reversal_min = float(getattr(config, "PULLBACK_AUGMENT_REVERSAL_MIN", 1.0) or 1.0)
    return enabled, min_gain, pb_enabled, pb_reversal_min


def profit_gate_ok(config, raw_gain_pct: float) -> bool:
    """AUGMENT_ONLY_WHEN_PROFITABLE(_TRADIER) base rule — never augment gain <= 0.
    Both venue flags default True live; either being True enforces the gate."""
    if bool(getattr(config, "AUGMENT_ONLY_WHEN_PROFITABLE", True)) or bool(getattr(config, "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER", True)):
        return raw_gain_pct > 0.0
    return True


def cooldown_bars(config, bar_minutes: float) -> int:
    """AUGMENTATION_COOLDOWN_SECONDS -> bars on the sim base TF (min 1 bar)."""
    cd_sec = float(getattr(config, "AUGMENTATION_COOLDOWN_SECONDS", 300.0) or 0.0)
    if cd_sec <= 0:
        return 1
    bm = max(float(bar_minutes or 1.0), 1e-9)
    bars = int(-(-cd_sec // (60.0 * bm)))  # ceil
    return max(bars, 1)


def gain_ladder_fire(config, is_long: bool, px: float, last_aug_px: float, raw_gain_pct: float, max_gain_pct: float):
    """PURE per-bar augment decision. Returns (fire: bool, reason: str).

    Caller enforces cooldown (cooldown_bars) and any per-position cap; this predicate
    is the ladder itself: obligatory tier when gain since the last add reaches
    AUGMENT_MIN_GAIN_PCT, or the pullback dip after a >=min_gain peak.
    """
    enabled, min_gain, pb_enabled, pb_reversal_min = ladder_params(config)
    if not enabled:
        return False, ""
    if not profit_gate_ok(config, raw_gain_pct):
        return False, ""
    if last_aug_px and last_aug_px > 0 and px > 0:
        gain_since = ((px - last_aug_px) / last_aug_px * 100.0) if is_long else ((last_aug_px - px) / last_aug_px * 100.0)
    else:
        gain_since = raw_gain_pct
    if gain_since >= min_gain:
        return True, f"UAG_LADDER gain_since_add {gain_since:+.2f}% >= {min_gain:.1f}%"
    if pb_enabled and max_gain_pct >= min_gain and raw_gain_pct >= 0.5 * min_gain and (max_gain_pct - raw_gain_pct) >= pb_reversal_min:
        return True, f"PULLBACK_AUG max {max_gain_pct:.2f}% -> {raw_gain_pct:.2f}% dip >= {pb_reversal_min:.1f}%"
    return False, ""
