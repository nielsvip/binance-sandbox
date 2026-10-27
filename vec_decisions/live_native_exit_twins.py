"""live_native_exit_twins — vector twins for two crypto native exits that live fires but no board can dictate.

vec_log_fixes 2026-10-27. Both paths are refused today at execute_now (VEC_LAST_REFUSE
POLICY_NON_VECTOR_EXIT_DISABLED) because v12_quick_engine never books them:

* IN_GAIN_TREND_EXIT  — ez_manage.py process_position "GAIN-TIERED PROFIT HARVEST" (~60546-60592):
  gain > 2 and not a TREND_ACCOUNTS account and not recently reduced; gain >= 10 needs the 1h
  stoch K below/above D with a red/green 1h HA candle, 5 <= gain < 10 needs a red/green 15m HA
  candle with the 15m stoch K below/above D; below 5 % it never fires.
* DELTA_EXIT_DC15M_FLOOR_BREAK — ez_manage.py (~60468-60500, "DC15M_FORCE_EXIT"): price through
  the 15m Donchian floor (LONG) / ceiling (SHORT) while the position is under water, gated by
  DELTA_EXIT_ENABLED. Core: vec_decisions/process_position_crypto__dc15m_force_exit.

Contract matches vec_decisions.grey_wire_exits: fn(c, g, st, is_long) -> (fire, reason) where
c(key, default) is the config getter, g(key, default) the current-bar indicator getter (live dict
or NPZ bar) and st the position state ({'gain', 'px', 'trend_account', 'recently_reduced'}).
Each predicate is gated by its OWN new switch (IN_GAIN_TREND_EXIT_ENABLED,
DC15M_FORCE_EXIT_ENABLED), default False = what live effectively does today (refused), so wiring
them changes no board and no live order until a board promotes True.
"""
from typing import Callable, List, Tuple

import vec_decisions.process_position_crypto__dc15m_force_exit as _dc15

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


def dc15m_force_exit(c, g, st, is_long) -> Tuple[bool, str]:
    if not bool(c("DELTA_EXIT_ENABLED", True)):
        return False, ""
    # NPZ dc_low_15m/dc_high_15m include the bar itself (measured 2026-10-27 on GRASS/DOGE/XMR/NMR: 0 of
    # 200,430 closes outside them), so the closed-bar twin reads the *_prev channel = the channel the live
    # price is compared with while the bar forms. A live snapshot without *_prev falls back to the plain key.
    lo = _f(g("dc_low_15m_prev", g("dc_low_15m", 0.0)))
    hi = _f(g("dc_high_15m_prev", g("dc_high_15m", 0.0)))
    fire = _dc15._dc15m_force_exit_fires(_f(st.get("px")), _f(st.get("gain")), lo, hi, is_long)
    return (True, "DELTA_EXIT_DC15M_FLOOR_BREAK") if fire else (False, "")


TWIN_EXITS = (
    ("DC15M_FORCE_EXIT_ENABLED", lambda c: bool(c("DC15M_FORCE_EXIT_ENABLED", False)), dc15m_force_exit),
    ("IN_GAIN_TREND_EXIT_ENABLED", lambda c: bool(c("IN_GAIN_TREND_EXIT_ENABLED", False)), in_gain_trend_exit),
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
