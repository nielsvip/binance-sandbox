"""USER 2026-10-23 v15_pending sweep: pv3 C3 (docs/v15_pending/22_SWITCH_PROPOSALS_V3_20261022.md §3), wired.

REENTRY_MASK_ARM_<FAM>_ENABLED (default False each): apply ONE entry-mask family to reentry fires, independent of
REENTRY_ENTRY_FILTER_ENABLED (the lenient all-mask count) and REENTRY_APPLY_ENTRY_GATES_ENABLED (the whole gate stack).
An armed family whose <FAM>_FILTER_TF is OFF in the running set does nothing (same as for fresh entries); master-gated
families follow their master. No switch True -> None -> today's behaviour byte for byte.
"""
from vec_decisions import filter_tf_gates as _ftg
from vec_decisions import generic_filter_tf as _gft

FAMILIES = {
    "CIRCUIT_SHARPE_GATES": "CIRCUIT_SHARPE_GATES_FILTER_TF",
    "DC_BREAK": "DC_BREAK_FILTER_TF",
    "BREAKOUT_RETEST": "BREAKOUT_RETEST_FILTER_TF",
    "DC_MOMENTUM_BOTA_SCORER": "DC_MOMENTUM_BOTA_SCORER_FILTER_TF",
    "DELTA_ENGINE": "DELTA_ENGINE_FILTER_TF",
    "MOM3": "MOM3_FILTER_TF",
    "MOMENTUM_BREAKOUT": "MOMENTUM_BREAKOUT_FILTER_TF",
}
MASTERS = {"BREAKOUT_RETEST_FILTER_TF": "BREAKOUT_RETEST_ARMED_ENABLED"}
# Explicit getattr literals (not f-string-built): the switch bible counts a
# read as an uppercase literal that is a direct getattr argument, and the
# codebase convention is explicit names everywhere.
ARM_SWITCHES = {
    "CIRCUIT_SHARPE_GATES": "REENTRY_MASK_ARM_CIRCUIT_SHARPE_GATES_ENABLED",
    "DC_BREAK": "REENTRY_MASK_ARM_DC_BREAK_ENABLED",
    "BREAKOUT_RETEST": "REENTRY_MASK_ARM_BREAKOUT_RETEST_ENABLED",
    "DC_MOMENTUM_BOTA_SCORER": "REENTRY_MASK_ARM_DC_MOMENTUM_BOTA_SCORER_ENABLED",
    "DELTA_ENGINE": "REENTRY_MASK_ARM_DELTA_ENGINE_ENABLED",
    "MOM3": "REENTRY_MASK_ARM_MOM3_ENABLED",
    "MOMENTUM_BREAKOUT": "REENTRY_MASK_ARM_MOMENTUM_BREAKOUT_ENABLED",
}


def armed(cfg) -> list:
    out = []
    if bool(getattr(cfg, "REENTRY_MASK_ARM_CIRCUIT_SHARPE_GATES_ENABLED", False)):
        out.append("CIRCUIT_SHARPE_GATES")
    if bool(getattr(cfg, "REENTRY_MASK_ARM_DC_BREAK_ENABLED", False)):
        out.append("DC_BREAK")
    if bool(getattr(cfg, "REENTRY_MASK_ARM_BREAKOUT_RETEST_ENABLED", False)):
        out.append("BREAKOUT_RETEST")
    if bool(getattr(cfg, "REENTRY_MASK_ARM_DC_MOMENTUM_BOTA_SCORER_ENABLED", False)):
        out.append("DC_MOMENTUM_BOTA_SCORER")
    if bool(getattr(cfg, "REENTRY_MASK_ARM_DELTA_ENGINE_ENABLED", False)):
        out.append("DELTA_ENGINE")
    if bool(getattr(cfg, "REENTRY_MASK_ARM_MOM3_ENABLED", False)):
        out.append("MOM3")
    if bool(getattr(cfg, "REENTRY_MASK_ARM_MOMENTUM_BREAKOUT_ENABLED", False)):
        out.append("MOMENTUM_BREAKOUT")
    return out


def _snap_num(ind, key, default=0.0):
    try:
        return float((ind or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _live_wt_cross_side(is_long, ind, tf):
    w1 = _snap_num(ind, f"wt1_{tf}")
    w2 = _snap_num(ind, f"wt2_{tf}")
    if w1 == 0 and w2 == 0:
        return True
    return (w1 > w2) if is_long else (w1 < w2)


def _live_dc_break(is_long, ind, tf, px):
    lvl = _snap_num(ind, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
    if not lvl > 0:
        lvl = _snap_num(ind, f"dc_high_{tf}" if is_long else f"dc_low_{tf}")
    if not lvl > 0:
        return True
    return (px > lvl) if is_long else (px < lvl)


def _live_dc_retest_hold(is_long, ind, tf, px):
    lvl = _snap_num(ind, f"dc_high_{tf}_prev" if is_long else f"dc_low_{tf}_prev")
    if not lvl > 0:
        return True
    if is_long:
        lo = _snap_num(ind, f"low_{tf}")
        return lo > 0 and lo <= lvl and px > lvl
    hi = _snap_num(ind, f"high_{tf}")
    return hi > 0 and hi >= lvl and px < lvl


def _live_mom3_pct(ind, tf, px):
    c3 = _snap_num(ind, f"close_3bar_{tf}")
    if not c3 > 0:
        return None
    return (px - c3) / c3 * 100.0


def _live_mom3(is_long, ind, tf, px, get):
    m = _live_mom3_pct(ind, tf, px)
    if m is None:
        return True
    try:
        thr_l = float(get("MOM3_LONG_THRESHOLD", -1.0))
    except (TypeError, ValueError):
        thr_l = -1.0
    try:
        thr_s = float(get("MOM3_SHORT_THRESHOLD", 1.0))
    except (TypeError, ValueError):
        thr_s = 1.0
    return (m < thr_l) if is_long else (m > thr_s)


def _live_momentum_breakout(is_long, ind, tf, px):
    m = _live_mom3_pct(ind, tf, px)
    if m is None:
        return True
    return (m > 0.0) if is_long else (m < 0.0)


def live_allows(get, is_long, ind, px):
    """Snapshot twin of armed_mask for live reentry paths (ez dispatch +
    tradier W2 gate). get(key, default) is the per-sym getter, ind the live
    indicator snapshot, px the current price. (True, '') when no arm switch
    is on. Missing keys fail OPEN per family, exactly like the vector's
    np.where(missing, True, ...) — a host without the key cannot veto on it.
    Bar-level agreement (snapshot vs completed-bar mask) is watched, not
    assumed: these switches default False everywhere until replay-proven."""
    fams = [b for b, sw in ARM_SWITCHES.items() if bool(get(sw, False))]
    if not fams:
        return True, ""
    for base in fams:
        name = FAMILIES[base]
        tf = str(get(name, "OFF") or "OFF").strip()
        if tf.upper() == "OFF" or (name in MASTERS and not bool(get(MASTERS[name], False))):
            continue
        if name in (
            "CIRCUIT_SHARPE_GATES_FILTER_TF",
            "DC_MOMENTUM_BOTA_SCORER_FILTER_TF",
            "DELTA_ENGINE_FILTER_TF",
        ):
            ok = _live_wt_cross_side(is_long, ind, tf)
        elif name == "DC_BREAK_FILTER_TF":
            ok = _live_dc_break(is_long, ind, tf, px)
        elif name == "BREAKOUT_RETEST_FILTER_TF":
            ok = _live_dc_retest_hold(is_long, ind, tf, px)
        elif name == "MOM3_FILTER_TF":
            ok = _live_mom3(is_long, ind, tf, px, get)
        elif name == "MOMENTUM_BREAKOUT_FILTER_TF":
            ok = _live_momentum_breakout(is_long, ind, tf, px)
        else:
            ok = True
        if not ok:
            return False, f"REENTRY_MASK_ARM_{base}_TF{tf}"
    return True, ""


def armed_mask(npz, n, is_long, cfg, close, safe):
    m = None
    for base in armed(cfg):
        name = FAMILIES[base]
        tf = str(getattr(cfg, name, "OFF") or "OFF").strip()
        if tf.upper() == "OFF" or (name in MASTERS and not bool(getattr(cfg, MASTERS[name], False))):
            continue
        if name in _gft.FILTER_TF_MAP:
            mk = _gft._cond(_gft.FILTER_TF_MAP[name][1], npz, n, tf, is_long, close, safe)
        elif name == "MOM3_FILTER_TF":
            mk = _ftg.mom3_entry_gate(npz, n, is_long, cfg, close, safe)
        elif name == "MOMENTUM_BREAKOUT_FILTER_TF":
            mk = _ftg.momentum_breakout_gate(npz, n, is_long, cfg, close, safe)
        else:
            mk = None
        if mk is None:
            continue
        m = mk if m is None else (m & mk)
    return m
