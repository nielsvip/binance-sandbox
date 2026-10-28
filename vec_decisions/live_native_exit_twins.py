"""live_native_exit_twins — vector twins for two crypto native exits that live fires but no board can dictate.

vec_log_fixes 2026-10-27. Both paths are refused today at execute_now (VEC_LAST_REFUSE
POLICY_NON_VECTOR_EXIT_DISABLED) because v12_quick_engine never books them:

* IN_GAIN_TREND_EXIT  — ez_manage.py process_position "GAIN-TIERED PROFIT HARVEST" (~60546-60592):
  gain > 2 and not a TREND_ACCOUNTS account and not recently reduced; gain >= 10 needs the 1h
  stoch K below/above D with a red/green 1h HA candle, 5 <= gain < 10 needs a red/green 15m HA
  candle with the 15m stoch K below/above D; below 5 % it never fires.
* DELTA_EXIT_DC15M_FLOOR_BREAK — ez_manage.py (~60468-60500, "DC15M_FORCE_EXIT"): price through
  the 15m Donchian floor (LONG) / ceiling (SHORT) while the position is under water (2026-10-28: own switch
  only, skipped inside MIN_HOLD_BARS, also stocks; see dc15m_evaluate). Core:
  vec_decisions/process_position_crypto__dc15m_force_exit.

Contract matches vec_decisions.grey_wire_exits: fn(c, g, st, is_long) -> (fire, reason) where
c(key, default) is the config getter, g(key, default) the current-bar indicator getter (live dict
or NPZ bar) and st the position state ({'gain', 'px', 'trend_account', 'recently_reduced'}).
Each predicate is gated by its OWN new switch (IN_GAIN_TREND_EXIT_ENABLED,
DC15M_FORCE_EXIT_ENABLED), default False = what live effectively does today (refused), so wiring
them changes no board and no live order until a board promotes True.
"""
from typing import Callable, List, Tuple

import vec_decisions.process_position_crypto__dc15m_force_exit as _dc15
import vec_decisions.min_hold_gate as _mhg
import vec_decisions.check_exit_candidates_crypto__wt_cross_exit as _wtx

_HA = {-1: "red", 0: "neutral", 1: "green"}


def _f(v, d=0.0) -> float:
    try:
        return float(d) if v is None else float(v)
    except (TypeError, ValueError):
        return float(d)


def ha_color(v) -> str:
    """live string ('red'/'green') or NPZ int8 (-1 red, 0 neutral, 1 green; backtest_v8_harness _INT_DECODE['ha'])."""
    if isinstance(v, str):
        return v.strip().lower()
    try:
        return _HA.get(int(v), "")
    except (TypeError, ValueError):
        return ""


def in_gain_trend_tier(gain: float, is_long: bool, k_1h: float, d_1h: float, ha_1h: str, k_15m: float, d_15m: float, ha_15m: str) -> Tuple[bool, str]:
    """Pure tier decision of the live block (caller has already required gain > 2)."""
    if gain >= 10.0:
        fire = (is_long and k_1h < d_1h and ha_1h == "red") or ((not is_long) and k_1h > d_1h and ha_1h == "green")
        return fire, "BIG_WINNER_HTF"
    if gain >= 5.0:
        fire = (is_long and ha_15m == "red" and k_15m < d_15m) or ((not is_long) and ha_15m == "green" and k_15m > d_15m)
        return fire, "MED_WINNER_15M"
    return False, "HOLD"


def in_gain_trend_exit(c, g, st, is_long) -> Tuple[bool, str]:
    gain = _f(st.get("gain"))
    if not gain > 2.0 or st.get("trend_account") or st.get("recently_reduced"):
        return False, ""
    ha_1h = ha_color(g("ha_1h", g("ha_color_1h", "")))
    ha_15m = ha_color(g("ha_15m", g("ha_color_15m", "")))
    fire, tier = in_gain_trend_tier(gain, is_long, _f(g("k_1h", 50.0), 50.0), _f(g("d_1h", 50.0), 50.0), ha_1h, _f(g("k_15m", 50.0), 50.0), _f(g("d_15m", 50.0), 50.0), ha_15m)
    return (True, f"IN_GAIN_TREND_EXIT_{tier}") if fire else (False, "")


# USER 2026-10-28 (verbatim): "DELTA_EXIT_DC15M_FLOOR_BREAK should be tested as a switch if it works it works for a
# sym_side but it needs to be wired correctly so it does move the needle in both vec and live identically.;
# DC15M_FORCE_EXIT_ENABLED sounds like the same thing - make sure this is tested under the same name in stocks and
# crypto - it should always be skipped if min_hold is not yet reached".
# ONE decision for crypto live (ez_manage), stocks live (tradier_manage) and the vector (v12_quick_engine, both venues):
#   switch DC15M_FORCE_EXIT_ENABLED -> price through the 15m Donchian floor/ceiling while red
#   -> SKIPPED while the position is younger than MIN_HOLD_BARS (vec_decisions/min_hold_gate, the reason is NOT in its
#   exempt families) -> CLOSE. When armed the close may book/execute red: the vector marks it DC15M_MARKER for
#   vec_decisions/net_exit_gate, live passes dc15m_loss_close_allowed() to execute_now's net-gain floor.
# NOT the FIXED_STOP (frozen entry-bar dc_15m -0.25%, always on, min-hold exempt) -- this one reads the CURRENT channel.
DC15M_SWITCH = "DC15M_FORCE_EXIT_ENABLED"
DC15M_REASON = "DELTA_EXIT_DC15M_FLOOR_BREAK"
DC15M_MARKER = "DC15M_FORCE_EXIT_ARMED"


class _CfgView:
    """min_hold_gate reads getattr(cfg, KEY); expose a c(key, default) getter the same way on both sides."""

    def __init__(self, c):
        self._c = c

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        return self._c(key, None)


def dc15m_armed(c) -> bool:
    """The switch alone. DELTA_EXIT_ENABLED is deliberately NOT a co-master (decoupled 2026-10-28): it is False in
    config_tradier and the tradier QuickConfig, so coupling would make the stocks row a structural zero (Bible 14.3) and
    the operator ordered the same switch tested in stocks and crypto. Every set is False today, so nothing changes."""
    try:
        return bool(c(DC15M_SWITCH, False))
    except Exception:
        return False


def dc15m_min_hold_refuses(c, age_s) -> Tuple[bool, str]:
    """The shared MIN_HOLD gate for this reason. age_s = seconds since the position opened (None/<0 = unknown -> never
    held, the gate's AGE_UNKNOWN rule). live=False on both sides on purpose: the live-only exemptions are board/bookkeeping
    tokens this reason never carries, so both sides ask the identical question."""
    if age_s is None:
        return False, "AGE_UNKNOWN"
    return _mhg.refuse(age_s, _CfgView(c), action="CLOSE", reason=DC15M_REASON, live=False)


def dc15m_evaluate(c, g, st, is_long) -> Tuple[bool, str, str]:
    """(fire, reason, detail). detail names a min-hold skip so live can log it; the vector only uses fire/reason."""
    # NPZ dc_low_15m/dc_high_15m include the bar itself (measured 2026-10-27 on GRASS/DOGE/XMR/NMR: 0 of
    # 200,430 closes outside them), so the closed-bar twin reads the *_prev channel = the channel the live
    # price is compared with while the bar forms. A live snapshot without *_prev falls back to the plain key.
    lo = _f(g("dc_low_15m_prev", g("dc_low_15m", 0.0)))
    hi = _f(g("dc_high_15m_prev", g("dc_high_15m", 0.0)))
    if not _dc15._dc15m_force_exit_fires(_f(st.get("px")), _f(st.get("gain")), lo, hi, is_long):
        return False, "", ""
    _age = st.get("hold_age_s", st.get("age_s"))
    held, why = dc15m_min_hold_refuses(c, _age)
    if held:
        return False, "", why
    return True, DC15M_REASON, ""


def dc15m_force_exit(c, g, st, is_long) -> Tuple[bool, str]:
    fire, reason, _why = dc15m_evaluate(c, g, st, is_long)
    return fire, reason


def dc15m_loss_close_allowed(reason, armed_now: bool) -> bool:
    """Live twin of the vector's mandatory_stop_kind=DC15M_MARKER: an armed DC15M floor-break close may close red."""
    return bool(armed_now) and str(reason or "").upper().startswith(DC15M_REASON)


TWIN_EXITS = (
    ("DC15M_FORCE_EXIT_ENABLED", lambda c: bool(c("DC15M_FORCE_EXIT_ENABLED", False)), dc15m_force_exit),
    ("IN_GAIN_TREND_EXIT_ENABLED", lambda c: bool(c("IN_GAIN_TREND_EXIT_ENABLED", False)), in_gain_trend_exit),
    # 2026-10-28: live WT_CROSS_EXIT (ez_positions_quick check_exit_candidates) — own switch WT_CROSS_EXIT_VEC_ENABLED, default False
    ("WT_CROSS_EXIT_VEC_ENABLED", _wtx.wt_cross_exit_armed, _wtx.wt_cross_exit_twin),
)


def active_exits(c: Callable) -> List[Callable]:
    """predicates whose switch is ON (empty at the defaults -> zero cost, zero ledger change)."""
    out = []
    for _name, on, fn in TWIN_EXITS:
        try:
            if on(c):
                out.append(fn)
        except Exception:
            continue
    return out
