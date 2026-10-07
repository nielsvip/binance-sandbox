"""DYN_STRUCT_TRAIL — vec twin of tradier_manage.process_position DYN_STRUCT_TRAIL block (tradier_manage.py:10779-10806). [N4]
Monotone structure-following trail: lvl = running max of dc_low_{TF} (LONG) / running min of dc_high_{TF} (SHORT) while the position lives;
armed when gain >= DYN_STRUCT_TRAIL_MIN_GAIN_PCT (default 0.0 => armed from the start); CLOSE 100% when price crosses lvl
(LONG px <= lvl, SHORT px >= lvl); reason FROZEN_STOP_DYN_STRUCT_TRAIL_{tf}_g{gain}. Live default: DYN_STRUCT_TRAIL_ENABLED False in config/config_tradier
(the _cfg fallback in code is True — a missing field would turn it ON; the template swept row tests ON).
Stocks block; the crypto managers have no equivalent (ez_manage has no DYN_STRUCT_TRAIL read) — vector applies it for BOTH modes only when the switch is ON,
so a swept crypto row would show a vector-only effect: keep it STOCKS-only (apply only when cfg.MODE == 'tradier')."""
from __future__ import annotations


def step(cfg, is_long: bool, px: float, gain_pct: float, dc_cur: float, state: dict):
    """-> (fire, reason, new_state). dc_cur = dc_low_{TF}[i] (LONG) / dc_high_{TF}[i] (SHORT) at this bar (0 = missing)."""
    tf = str(getattr(cfg, "DYN_STRUCT_TRAIL_TF", "4h") or "4h")
    min_g = float(getattr(cfg, "DYN_STRUCT_TRAIL_MIN_GAIN_PCT", 0.0) or 0.0)
    st = dict(state or {})
    if not st:
        st = {"lvl": 0.0, "armed": min_g <= 0.0}
    if gain_pct >= min_g:
        st["armed"] = True
    if dc_cur > 0:
        if st["lvl"] <= 0:
            st["lvl"] = dc_cur
        elif is_long and dc_cur > st["lvl"]:
            st["lvl"] = dc_cur
        elif (not is_long) and dc_cur < st["lvl"]:
            st["lvl"] = dc_cur
    if st["armed"] and st["lvl"] > 0 and ((is_long and px <= st["lvl"]) or ((not is_long) and px >= st["lvl"])):
        return True, f"FROZEN_STOP_DYN_STRUCT_TRAIL_{tf}_g{gain_pct:.2f}", st
    return False, "", st
