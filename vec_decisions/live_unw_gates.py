"""live_unw_gates (UNW-L, 2026-10-01) — LIVE twins of switches that only existed in the vector (v12_quick_engine).
Every function here is a pure predicate over the live indicator dict + a config getter `get(key, default)` (the live per-sym aware resolver).
Defaults == the vector defaults == OFF/neutral, so importing and calling these changes nothing until a sym_side wins a sweep.
Parity with the vector is pinned by tests/test_live_unw_gates.py (scalar vs the numpy expressions copied from v12_quick_engine, plus a
source-drift guard that fails if the vector expression changes).

  WT_DC_TF_COMBO            -> wtdc_combo_tfs            (v12_quick_engine._wtdc_combo_resolve)
  AUGMENT_BULL_KILL_ENABLED -> augment_bull_kill_blocked (v12_quick_engine ~10088: augment_sig & ~(close_D>sma_20_D & wt1_4h>BULL_HOLD_WT_THR))
  BULL_HOLD_EXIT_DELAY_BARS -> exit_hold_blocked         (v12_quick_engine ~9970: technical exits suppressed on D-bull bars)
  BEAR_HOLD_EXIT_DELAY_BARS -> exit_hold_blocked         (v12_quick_engine ~9990: short-only, close_D<sma_20_D & wt1_4h<BEAR_HOLD_WT_THR)
"""

# live close/reduce reasons that are the TECHNICAL exit family the vector folds into _all_exit (delta/vel/srs/sat/rz/stoch_1h/mfi_flip/wt_cu/mi/
# vel_decay/mtf_gr/gr_htf/formation/stdev/parabolic). Protective/loss exits are NEVER held (R1/R2/hedge/vigilance/noloss/manual/stop/intervention).
TECH_EXIT_TOKENS = (
    "DELTA_EXIT", "VEL_EXIT", "VELOCITY", "STRUCTURAL_RANGE_SHIFT", "SRS", "SAT_EXIT", "RZ_EXIT", "STOCH_1H", "MFI_FLIP", "WT_CROSS", "WT_CU",
    "MI_EXIT", "VEL_DECAY", "MTF_GR", "GR_HTF", "FORMATION", "STDEV_BREAKOUT", "PARABOLIC", "TECHNICAL_EXIT",
)
PROTECTIVE_TOKENS = ("NOLOSS", "HEDGE", "R1_", "R2_", "VIGILANCE", "MANUAL", "STOP", "INTERVENTION", "RIDICULOUS", "BREAK_REVERSE", "LIQUIDAT", "EMERGENCY", "GAP_RISK", "HOPELESS")


def _f(v, d=0.0):
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


def wtdc_combo_tfs(get):
    """Returns (entry_tf, htf, htf2) when WT_DC_TF_COMBO is a non-default 'A_B_C' shorthand, else None (callers keep WT_DC_TF_ENTRY/HTF/HTF2)."""
    c = str(get("WT_DC_TF_COMBO", "1h_4h_D") or "1h_4h_D")
    if c == "1h_4h_D":
        return None
    p = c.split("_")
    if len(p) >= 3:
        return p[0], p[1], p[2]
    return None


def _sma20_d(ind):
    close_d = _f((ind or {}).get("close_D"))
    s = _f((ind or {}).get("sma_20_D"))
    if s == close_d or s == 0:
        s = _f((ind or {}).get("sma20_D"), close_d)
        if s == 0:
            s = close_d
    return close_d, s


def _wt4h(ind, fallback_wt_4h):
    w = _f((ind or {}).get("wt1_4h"))
    if fallback_wt_4h and w == 0.0:
        w = _f((ind or {}).get("wt_4h"))
    return w


def d_bull(ind, thr, fallback_wt_4h=True):
    close_d, sma = _sma20_d(ind)
    return close_d > sma and _wt4h(ind, fallback_wt_4h) > thr


def d_bear(ind, thr, fallback_wt_4h=True):
    close_d, sma = _sma20_d(ind)
    return close_d < sma and _wt4h(ind, fallback_wt_4h) < thr


def augment_bull_kill_blocked(get, ind):
    """True -> block this AUGMENT (vector: augment_sig & ~bull; wt1_4h only, no wt_4h fallback)."""
    if not bool(get("AUGMENT_BULL_KILL_ENABLED", False)):
        return False
    return d_bull(ind, _f(get("BULL_HOLD_WT_THR", -53.0), -53.0), fallback_wt_4h=False)


def is_technical_exit_reason(reason):
    r = str(reason or "").upper()
    if any(t in r for t in PROTECTIVE_TOKENS):
        return False
    return any(t in r for t in TECH_EXIT_TOKENS)


def exit_hold_blocked(get, ind, is_long, reason):
    """True -> hold: suppress this technical exit (BULL_HOLD: any side on D-bull; BEAR_HOLD: shorts only on D-bear). Delay bars > 0 is the on switch."""
    if not is_technical_exit_reason(reason):
        return False
    if int(_f(get("BULL_HOLD_EXIT_DELAY_BARS", 0), 0)) > 0 and d_bull(ind, _f(get("BULL_HOLD_WT_THR", -53.0), -53.0)):
        return True
    if (not is_long) and int(_f(get("BEAR_HOLD_EXIT_DELAY_BARS", 0), 0)) > 0 and d_bear(ind, _f(get("BEAR_HOLD_WT_THR", 53.0), 53.0)):
        return True
    return False


# ── typed augment min-gain (AUGMENT_BOUNCE_MIN_GAIN_PCT / AUGMENT_BREAKOUT_MIN_GAIN_PCT replace the generic MIN_GAIN_TO_BUY_AGGRESSIVELY / AUGMENT_MIN_GAIN_PCT tier) ──
# master AUGMENT_TYPED_MIN_GAIN_ENABLED default False => generic tier unchanged (today's live + vector). The vector twin MUST call these same functions.
BOUNCE_TOKENS = ("BOUNCE", "WT_CROSS")                      # live crypto AUG_B_WT_CROSS_*, stocks WT_D_BOUNCE_AUG
BREAKOUT_TOKENS = ("BREAKOUT", "BLOWPAST", "DC_TIER", "PYRAMID", "DC_BREAK")  # live crypto AUG_A_BLOWPAST_*, stocks DC_TIER{n}_*_AUG


def augment_type(reason):
    r = str(reason or "").upper()
    if any(t in r for t in BREAKOUT_TOKENS):
        return "breakout"
    if any(t in r for t in BOUNCE_TOKENS):
        return "bounce"
    return None


def augment_tier_min_gain(get, reason, generic):
    """The min gain-since-last-add tier for this augment. master off or untyped reason -> `generic` (effective_min_gain, floor 2.5). No floor on the typed keys (swept values 0.25-6)."""
    if not bool(get("AUGMENT_TYPED_MIN_GAIN_ENABLED", False)):
        return generic
    t = augment_type(reason)
    if t == "bounce":
        return _f(get("AUGMENT_BOUNCE_MIN_GAIN_PCT", 0.5), 0.5)
    if t == "breakout":
        return _f(get("AUGMENT_BREAKOUT_MIN_GAIN_PCT", 2.0), 2.0)
    return generic


# ── WT_COMPOSITE_ENTRY_BLOCK (+ master WT_COMPOSITE_HTF_GATE): hard entry/augment block of ez_positions_quick.rate() lines 2493-2512, twin of vec_decisions/wt_composite_gate.block_mask ──
def wt_composite_block(get, ind, is_long):
    """True -> block fresh entry / augment: alignment < 3 OR composite < WT_COMPOSITE_ENTRY_BLOCK (default -20). Active iff WT_COMPOSITE_HTF_GATE and a WT_COMPOSITE_SCORING_ENABLED(_TRADIER)."""
    if not bool(get("WT_COMPOSITE_HTF_GATE", False)):
        return False
    if not (bool(get("WT_COMPOSITE_SCORING_ENABLED", False)) or bool(get("WT_COMPOSITE_SCORING_ENABLED_TRADIER", False))):
        return False
    align = _f((ind or {}).get("wt_bull_alignment" if is_long else "wt_bear_alignment"))
    comp = _f((ind or {}).get("wt_composite_long" if is_long else "wt_composite_short"))
    return align < 3 or comp < _f(get("WT_COMPOSITE_ENTRY_BLOCK", -20.0), -20.0)
