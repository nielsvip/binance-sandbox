"""GENERIC FILTER_TF mechanism — Wave 2 (2026-09-28, acceleration order).

ONE mechanism, declaratively mapped: each *_FILTER_TF name gets a REAL, name-appropriate
per-TF condition from FILTERS_EXPLAINED (never a shared proxy — the deleted _batch1 farm
applied the same wt1>wt2 test to every name, which is exactly the echo disease).
OFF (default) = inert. Names whose docs are too vague for real semantics are NOT here —
they are DEAD_VEC'd with "no defined semantics" (honesty over coverage).

Targets: 'entry' masks AND into entry_sig; 'reduce_confirm' must hold on the bar for
partial reduces to fire; 'erosion_confirm' must hold for the WIN_TRAIL_EROSION close.

Conditions (side-resolved; fail-open where the TF array is missing/zero):
- bb_extreme: bb_pct_b_{tf} <= 0.20 (long, oversold) / >= 0.80 (short) — FILTERS_EXPLAINED
  "blocks pullback entries unless BB position confirms oversold/bought"; 0.20/0.80 are the
  standard %B extreme bands (documented choice).
- bb_reclaim: bb_pct_b_{tf} > 0.5 (long) / < 0.5 (short) — "price reclaiming mid-band".
- dc_break: close > dc_high_{tf} (long) / < dc_low_{tf} (short) — "Donchian break
  confirmation before breakout entry".
- wt_cross_side: wt1_{tf} > wt2_{tf} (long) / < (short) — "WT cross ladder confirmation".
- dc_breach_against: close <= dc_low_{tf} (long) / >= dc_high_{tf} (short) — reduce-side
  "when DC channel is breached against position, requires FILTER_TF confirmation".
- wt_against: wt1_{tf} < wt2_{tf} (long) / > (short) — exit-side confirmation that
  momentum is against the position ("requires FILTER_TF confirmation" on erosion exit).
"""
from __future__ import annotations

import numpy as np

# name -> (target, condition)
FILTER_TF_MAP = {
    # BB_PULLBACK_GATE_FILTER_TF: TF selector of the real BB_PULLBACK_GATE (vec_decisions/bb_pullback_gate.py), not a 2nd gate
    # 2026-09-29 alias-audit: legacy row switch with real per-TF BB data (bb_lower/upper_{tf});
    # classic band-bounce entry — low pierces lower band and close reclaims it (long), mirror short
    "BB_BOUNCE_ENTRY_TF": ("entry", "bb_bounce"),
    # Wave 4: single-TF blanket variant (the multi-TF blanket lives in wave4_families)
    "EMA_BLANKET_FILTER_FILTER_TF": ("entry", "ema_side"),
    # ── Wave 3 (2026-09-28): bar-pattern codes are authoritative from
    # backtest_v8_precompute.py:381-388 (mirrors ez_indicators.detect_bar_patterns);
    # bullish {1,3,5,7,9,11,16,18} / bearish {2,4,6,8,10,12,17,19}, 0=none.
    "BAR_PATTERNS_FILTER_TF": ("entry", "bar_pattern_side"),
    "BREAKOUT_RETEST_FILTER_TF": ("entry", "dc_retest_hold"),
    "CANDLE_PATTERN_STOPS_FILTER_TF": ("exit_confirm", "bar_pattern_against"),
    "EXIT_TOP_FADE_FILTER_TF": ("exit_confirm", "wt_top_fade"),
    # doc-equal alias of BREAKEVEN_GAIN_EROSION_FILTER_TF ("peak giveback / BE erosion") —
    # deltas will legitimately mirror; disclosed, not fabricated distinctness
    "PEAK_GIVEBACK_BE_EROSION_FILTER_TF": ("erosion_confirm", "wt_against"),
    "BB_RECOVERY_ENTRY_FILTER_TF": ("entry", "bb_reclaim"),
    "BB_RECOVERY_FILTER_TF": ("entry", "bb_reclaim"),  # doc: "same as above but generic"
    "DC_BREAK_FILTER_TF": ("entry", "dc_break"),
    # Batch 1 (2026-10-07 NONE wiring): live-faithful twins of the ez batch1 entry veto
    # (ez_manage._batch1_template_live_gate: TF-gated wt1>wt2 long / < short, OFF inert).
    # NOTE: same-name inline legs in v12 _batch1_template_wiring are DISABLED (passthrough
    # since the 2026-09-28 purge) — this MAP is the live path, do NOT add inline duplicates.
    "DELTA_ENGINE_FILTER_TF": ("entry", "wt_cross_side"),
    "DC_MOMENTUM_BOTA_SCORER_FILTER_TF": ("entry", "wt_cross_side"),
    "CIRCUIT_SHARPE_GATES_FILTER_TF": ("entry", "wt_cross_side"),
    "BT_WT_CROSS_LADDER_FILTER_TF": ("entry", "wt_cross_side"),
    "DC_BREACH_REDUCE_FILTER_TF": ("reduce_confirm", "dc_breach_against"),
    "BREAKEVEN_GAIN_EROSION_FILTER_TF": ("erosion_confirm", "wt_against"),
}


def _cond(kind, npz, n, tf, is_long, close, safe):
    if kind in ("bb_extreme", "bb_reclaim"):
        b = safe(npz, f"bb_pct_b_{tf}", n, np.nan)
        if kind == "bb_extreme":
            c = (b <= 0.20) if is_long else (b >= 0.80)
        else:
            c = (b > 0.5) if is_long else (b < 0.5)
        return np.where(np.isnan(b), True, c)
    if kind == "dc_break":
        # Donchian breakout = close beyond the PRIOR bar's channel (dc_*_{tf} includes the
        # current bar, so close > dc_high_{tf} is structurally impossible — proven 0-trades)
        key = (f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
        fallback = (f"dc_high_{tf}" if is_long else f"dc_low_{tf}")
        lvl = safe(npz, key, n, 0.0)
        if not np.any(lvl > 0):
            lvl = safe(npz, fallback, n, 0.0)
        c = (close > lvl) if is_long else (close < lvl)
        return np.where(lvl > 0, c, True)
    if kind in ("wt_cross_side", "wt_against"):
        w1 = safe(npz, f"wt1_{tf}", n, 0.0)
        w2 = safe(npz, f"wt2_{tf}", n, 0.0)
        both0 = (w1 == 0) & (w2 == 0)
        if kind == "wt_cross_side":
            c = (w1 > w2) if is_long else (w1 < w2)
        else:
            c = (w1 < w2) if is_long else (w1 > w2)
        return np.where(both0, True, c)
    if kind == "bb_bounce":
        band = safe(npz, f"bb_lower_{tf}" if is_long else f"bb_upper_{tf}", n, 0.0)
        lo = safe(npz, f"low_{tf}", n, 0.0)
        hi = safe(npz, f"high_{tf}", n, 0.0)
        if is_long:
            c = (band > 0) & (lo > 0) & (lo <= band) & (close > band)
        else:
            c = (band > 0) & (hi > 0) & (hi >= band) & (close < band)
        return np.where(band > 0, c, True)
    if kind == "ema_side":
        a = safe(npz, f"ema_9_above_21_{tf}", n, -1)
        above = a > 0.5
        missing = a < 0
        return np.where(missing, True, above if is_long else ~above)
    if kind in ("bar_pattern_side", "bar_pattern_against"):
        bp = safe(npz, f"bar_pattern_{tf}", n, -1)
        bull = np.isin(bp, (1, 3, 5, 7, 9, 11, 16, 18))
        bear = np.isin(bp, (2, 4, 6, 8, 10, 12, 17, 19))
        missing = bp < 0
        if kind == "bar_pattern_side":
            c = bull if is_long else bear
        else:  # pattern AGAINST the position confirms the stop
            c = bear if is_long else bull
        return np.where(missing, True, c)
    if kind == "dc_retest_hold":
        lvl = safe(npz, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev", n, 0.0)
        lo = safe(npz, f"low_{tf}", n, 0.0)
        hi = safe(npz, f"high_{tf}", n, 0.0)
        if is_long:
            c = (lo > 0) & (lo <= lvl) & (close > lvl)
        else:
            c = (hi > 0) & (hi >= lvl) & (close < lvl)
        return np.where(lvl > 0, c, True)
    if kind == "wt_top_fade":
        w1 = safe(npz, f"wt1_{tf}", n, 0.0)
        w1p = np.roll(w1, 1); w1p[0] = w1[0]
        both0 = (w1 == 0) & (w1p == 0)
        c = ((w1 > 60) & (w1 < w1p)) if is_long else ((w1 < -60) & (w1 > w1p))
        return np.where(both0, True, c)
    if kind == "dc_breach_against":
        # breach = close beyond the PRIOR bar's channel: dc_*_{tf} includes the current bar, so close <= dc_low_{tf}
        # ~never holds -> mask ~all False on every TF -> all reduces blocked, identical delta for 15m/1h/4h/D
        lvl = safe(npz, f"dc_low_{tf}_prev" if is_long else f"dc_high_{tf}_prev", n, 0.0)
        if not np.any(lvl > 0):
            lvl = safe(npz, f"dc_low_{tf}" if is_long else f"dc_high_{tf}", n, 0.0)
        c = (close <= lvl) if is_long else (close >= lvl)
        return np.where(lvl > 0, c, True)
    return None


def build_masks(npz, n, is_long, config, close, safe):
    """Returns {'entry': mask|None, 'reduce_confirm': mask|None, 'erosion_confirm': mask|None}.
    Each mask is the AND of every active (non-OFF) mapped filter for that target."""
    out = {"entry": None, "reduce_confirm": None, "erosion_confirm": None, "exit_confirm": None}
    for name, (target, kind) in FILTER_TF_MAP.items():
        tf = str(getattr(config, name, "OFF") or "OFF").strip()
        if tf.upper() == "OFF":
            continue
        m = _cond(kind, npz, n, tf, is_long, close, safe)
        if m is None:
            continue
        out[target] = m if out[target] is None else (out[target] & m)
    return out


def newborn_loss_kill_fires(config, is_long, age_min, gain_pct, vel_value):
    """Live-parity NEWBORN_LOSS_KILL (ez_manage.py:46524-46550): position younger than
    WINDOW_MIN with gain <= THRESHOLD, velocity-against confirm when required (V2);
    NEWBORN_LOSS_KILL_FILTER_TF selects the velocity TF (live NEWBORN_LOSS_KILL_VEL_TF)."""
    if not bool(getattr(config, "NEWBORN_LOSS_KILL_ENABLED", False)):
        return False
    window = float(getattr(config, "NEWBORN_LOSS_KILL_WINDOW_MIN", 30.0) or 30.0)
    thr = float(getattr(config, "NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT", -0.5))
    if not (0 <= age_min <= window and gain_pct <= thr):
        return False
    if bool(getattr(config, "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST", True)):
        return (vel_value < 0) if is_long else (vel_value > 0)
    return True
