"""vec_parity_gates — pure predicates behind the 10 VEC-parity switches (USER 2026-10-24).

Every VEC_LAST_* intervention names a path to fix. These helpers decide the
gated verdicts from plain values so both live (ez_manage) and tests share one
implementation. All defaults reproduce current behavior (inert) except
VEC_DISAGREE_FLAT_OPEN_BLOCK, which implements the explicit zero-tolerance
order (no OPEN while the forward vec target is flat).
"""

import re

CIRCUIT_BREAKER_TOKEN = "CIRCUIT_BREAKER"
QUICK_REDUCE_TOKENS = ("QUICK_REDUCE", "QUICK_BB_RECOVERY")
PROTECTED_TOKENS = (
    "STOP",
    "TRAILING",
    "HEDGE",
    "EMERGENCY",
    "LIQUIDATION",
    "MARGIN",
    "MANUAL",
    "FORCE",
    "GUARANTEE",
    "OBLIGATORY",
)
OPEN_ACTIONS = ("OPEN", "AUGMENT")
BAR_S = 900.0
TTL_S_MIN = 60.0
TTL_S_DEFAULT = 300.0
TTL_S_MAX = 3600.0


def split_gates(detail):
    return [g for g in str(detail or "").split("+") if g]


def override_verdict(tag, detail, refuse, override_on, breaker_bypass_on):
    if tag != "VEC_LAST_OVERRIDE":
        return tag, detail, refuse
    if not override_on:
        if refuse:
            return "VEC_LAST_REFUSE", refuse, refuse
        return "VEC_LAST_CLEAN", "VEC_OVERRIDE_DISABLED", refuse
    if not breaker_bypass_on and CIRCUIT_BREAKER_TOKEN in split_gates(detail):
        if refuse:
            return "VEC_LAST_REFUSE", refuse, refuse
        return "VEC_LAST_CLEAN", "VEC_OVERRIDE_BREAKER_VETO", refuse
    return tag, detail, refuse


def is_quick_reduce_reason(reason):
    r = str(reason or "").upper()
    return any(t in r for t in QUICK_REDUCE_TOKENS)


def non_vector_exit_refused(
    reason,
    master_on,
    quick_gate_on,
    sym_side=None,
    action="CLOSE",
    ultimate_buffer_pct=None,
):
    if not master_on:
        return False
    if is_quick_reduce_reason(reason) and not quick_gate_on:
        return False
    if dictate_only_exempt(action, reason, sym_side, ultimate_buffer_pct):
        return False
    return True


def is_protected_reason(reason):
    r = str(reason or "").upper()
    return any(t in r for t in PROTECTED_TOKENS)


def flat_open_blocked(action, reason, target_flat, block_on):
    if not block_on or not target_flat:
        return False
    if str(action or "").upper() not in OPEN_ACTIONS:
        return False
    if is_protected_reason(reason):
        return False
    return True


def disagree_refuse(disagree, kill_on):
    return bool(disagree and kill_on)


def nonvectorizable_state_disagreement(action, reason, ultimate_buffer_pct=None):
    """Return an auditable reason for exact native paths absent from closed 15m state."""
    if str(action or "").upper() not in ("CLOSE", "QUICK_CLOSE"):
        return ""
    r = str(reason or "").upper()
    if re.match(r"^QUICK_DC4_(?:HIGH|LOW)_3M_GAIN_EROSION_STOP(?:_|$)", r):
        return "NATIVE_DC4_3M_INPUT_UNAVAILABLE_IN_15M_VECTOR"
    if mandatory_stop_exempt(action, reason, ultimate_buffer_pct):
        if any(t in r for t in ("ULTIMATE_DC_4H_HARD_STOP", "QUICK_ULTIMATE_DC_4H_HARD_STOP", "UNCOND_DC_LOW_4H_UNCOND_", "UNCOND_DC_HIGH_4H_UNCOND_")):
            return "NATIVE_DC4_HARD_STOP_PROTECTION"
        return "NATIVE_DC15_FIXED_STOP_INTRABAR_UNAVAILABLE_IN_CLOSED_15M_STATE"
    return ""


def pending_defer_on(value):
    return bool(value)


def pending_ttl_s(raw):
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return TTL_S_DEFAULT
    if secs != secs:
        return TTL_S_DEFAULT
    return min(TTL_S_MAX, max(TTL_S_MIN, secs))


def hold_expiry_flatten_on(value):
    return bool(value)


def confirm_release_ok(dictated, vec_order, require_board):
    if dictated:
        return True
    if vec_order and not require_board:
        return True
    return False


def _per_sym_knob_true(sym_side, knob):
    """Resolve the switch exactly like live: sym_side full set, then cat_side default."""
    try:
        if not sym_side or not knob:
            return None
        import per_sym_store as _pss

        full = _pss.get_overrides(str(sym_side))
        if isinstance(full, dict) and knob in full:
            return bool(full[knob])
    except Exception:
        pass
    # A missing per_sym row or omitted key inherits the current cat_side
    # template default. Ignoring that layer made default-ON live/vector twins
    # look non-vector when a sym_side had no explicit copy of the switch.
    try:
        raw = str(sym_side).rsplit("_", 1)
        if len(raw) == 2:
            symbol, side = raw
            import cat_side_defaults as _csd

            value = _csd.get_for(knob, symbol, side, None)
            if value is not None:
                return bool(value)
    except Exception:
        pass
    return None


def mandatory_stop_exempt(action, reason, ultimate_buffer_pct=None):
    """Allow only the two frozen DC stop laws through dictate-only parity."""
    if str(action or "").upper() not in ("CLOSE", "QUICK_CLOSE"):
        return False
    r = str(reason or "").upper()
    if r.startswith(("ULTIMATE_DC_4H_HARD_STOP_", "QUICK_ULTIMATE_DC_4H_HARD_STOP_")):
        # This reason is emitted only by producers whose trigger is hard-coded
        # at dc_4h ±0.25%; the caller's legacy config value is not authoritative.
        return True
    if r.startswith(("UNCOND_DC_LOW_4H_UNCOND_", "UNCOND_DC_HIGH_4H_UNCOND_")):
        # Tradier's unconditional 4h producer passes _udcs.DEFAULT_BUFFER_PCT,
        # fixed at 0.25%, directly to the shared long/short stop predicates.
        return True
    if not r.startswith("FIXED_STOP_"):
        return False
    # Tradier appends the requested full-close quantity before dictate-only.
    # Accept that final execution suffix while still checking the complete
    # producer reason and its DC reference.
    base = r"FIXED_STOP_(?:LONG|SHORT)_PX\d+(?:\.\d+)?_LVL\d+(?:\.\d+)?_G[-+]?\d+(?:\.\d+)?"
    qty = r"(?:_QTY_\d+(?:\.\d+)?)?"
    # The live producer omits the reference suffix for the canonical frozen
    # entry dc_15m ±0.25% law. Any explicit REF tag must match a non-tightened
    # dc_15m or dc_4h ±0.25% spec below.
    if "_REF" not in r:
        return re.fullmatch(base + qty, r) is not None
    # DC15 is the required entry-time stop at exactly ±0.25%; DC4 is accepted
    # only at the mandated 0.25% buffer. Stale configurable stops (for example
    # ``_REFdc_low.15m.b0.5``) must not be mistaken for either hard-stop law.
    return re.fullmatch(base + r"_REFDC_(?:LOW|HIGH)\.(?:15M|4H)\.B0\.25" + qty, r) is not None


def nonvectorizable_portfolio_action(action, reason):
    """Recognize account-wide ratio orders absent from a single-symbol NPZ.

    The decision bar is driven by aggregate account exposure; the board cannot
    replay it from one symbol's bars. Reduction sizing remains native-only.
    """
    act = str(action or "").upper()
    r = str(reason or "").upper()
    return (
        act == "REDUCE"
        and r.startswith(("RATIO_CLOSE_OW_", "RATIO_REDUCE_"))
    ) or (act == "AUGMENT" and r.startswith("RATIO_REBALANCE_AUGMENT_"))


def dictate_only_exempt(action, reason, sym_side=None, ultimate_buffer_pct=None):
    # USER 2026-10-25: ONLY vec parity decides — no family, lane, or dispatcher
    # exemption, QUICK included. Survivors are vec speech itself: board-confirmed
    # reasons, the board-measured per_sym switch dictate, and side-preserving
    # ratio sizing (RATIO_REDUCE_*/RATIO_REBALANCE_AUGMENT_* — USER 2026-10-25:
    # ratio balance always works, augments and reduces, never touches bar
    # parity). RATIO_CLOSE_OW_* is also a live account-level partial trim whose
    # decision requires portfolio exposure absent from a single-sym NPZ; it is
    # explicitly classified non-vectorizable when the action is REDUCE.
    # Emergencies, hedges, oversize trims, bookkeeping-as-trade: all blocked
    # unless the vector dictates them. Switch-dictated exits
    # (PEAK_GIVEBACK/DC4/BREAKEVEN families) pass on their per_sym master knob.
    # (BROKER_RECONCILED
    # infrastructure writes are not trades and never reach this gate as
    # actions.) Kill: VEC_DICTATE_ONLY_ENABLED=False.
    try:
        from live_twins import vector_enforce as _ve
    except Exception:
        return True
    try:
        _ru = str(reason or "").upper()
        if _ru.startswith(("RATIO_REDUCE_", "RATIO_REBALANCE_AUGMENT_")):
            return True
        if nonvectorizable_portfolio_action(action, reason):
            return True
        if mandatory_stop_exempt(action, reason, ultimate_buffer_pct):
            return True
        if _ve._is_board_confirmed(reason):
            return True
        if sym_side:
            # The live flat-key crypto reentry twin and v12 both use the same
            # group master. A reason emitted by that producer is valid only
            # for the encoded side and only when the selected per_sym/cat-side
            # switch is ON; without this, execute_now rejected the exact live
            # twin that CRYPTO_REENTRY_PATHWAYS_ENABLED authorized.
            _cr_reentry = re.match(
                r"^CRYPTO_REENTRY_PATHWAY_(?:HTF_WT_CHURN|TARGET_DC_RECROSS|SELL_TOP_RECROSS|HARDCODED_RALLY|MANDATORY_BLANKET|MANDATORY_FAVORABLE)_(LONG|SHORT)(?:_|$)",
                _ru,
            )
            if _cr_reentry and str(action or "").upper() in ("OPEN", "REENTRY"):
                _side = str(sym_side).rsplit("_", 1)[-1].upper()
                if _side == _cr_reentry.group(1) and _per_sym_knob_true(
                    sym_side, "CRYPTO_REENTRY_PATHWAYS_ENABLED"
                ) is True:
                    return True
            # The live GOLDEN_RULE loop emits GOLDEN_RULE_LONG/SHORT_multN;
            # v12's shared golden_loop.golden_entry mask is the equivalent
            # source (booked as GOLDEN_RULE_ENTRY). Honor that switch only
            # when the reason side matches this sym_side and its selected
            # per_sym/cat-side default is ON.
            _gr_match = re.match(
                r"^GOLDEN_RULE_(LONG|SHORT)_MULT\d+(?:\.\d+)?(?:_|$)",
                str(reason or "").upper(),
            )
            if _gr_match and str(action or "").upper() in ("OPEN", "AUGMENT"):
                _side = str(sym_side).rsplit("_", 1)[-1].upper()
                if _side == _gr_match.group(1) and _per_sym_knob_true(
                    sym_side, "GOLDEN_RULE_ENABLED"
                ) is True:
                    return True
            # per_sym switch honors the board that measured it: BT_<FAMILY>_... reasons carry the family whose
            # <FAMILY>_ENABLED knob the sym_side's full set may enable (e.g. BTCDOMUSDT_SHORT STOCH_XTREME_EXIT).
            _r = str(reason or "")
            _fam = _r[3:] if _r.startswith("BT_") else ""
            _knob = (_fam.split("_g=")[0] + "_ENABLED") if _fam else ""
            if _knob and _per_sym_knob_true(sym_side, _knob) is True:
                return True
            # USER 2026-10-25 (unlock): switch-dictated exit families pass exactly like BT_ paths —
            # their live reasons carry no BT_ prefix, so map prefix -> master knob explicitly.
            # The knob is the same one live itself gates on; per_sym True overrides global.
            for _pfx, _kb in (
                ("QUICK_PEAK_GIVEBACK_", "PEAK_GIVEBACK_PROTECTION_ENABLED"),
                ("PEAK_GIVEBACK_", "PEAK_GIVEBACK_PROTECTION_ENABLED"),
                ("QUICK_DC4_", "BREAKEVEN_DC_LOW4_ENABLED"),
                ("DC4_", "BREAKEVEN_DC_LOW4_ENABLED"),
                ("QUICK_BREAKEVEN_", "BREAKEVEN_GAIN_EROSION_ENABLED"),
                ("BREAKEVEN_GAIN_EROSION", "BREAKEVEN_GAIN_EROSION_ENABLED"),
                ("CYCLE_TP_TIERED_", "CYCLE_TP_TIERED_ENABLED"),
                ("WT_PERCENTILE_", "WT_PERCENTILE_EXIT_ENABLED"),
                ("WT_EXHAUST_", "WT_EXHAUST_EXIT_ENABLED"),
            ):
                if _r.startswith(_pfx) or _ru.startswith(_pfx):
                    if _per_sym_knob_true(sym_side, _kb) is True:
                        return True
                    break
    except Exception:
        return True
    return False


def exec_board_ok(action, open_on, close_on):
    act = str(action or "").upper()
    if "CLOSE" in act or "REDUCE" in act:
        return bool(close_on)
    return bool(open_on)
