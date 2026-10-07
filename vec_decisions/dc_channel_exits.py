"""Shared DC-channel daytrade exits + WT lower-cross exit — ONE predicate set for
v12_quick_engine.simulate_one (per-bar scalars) and the live managers
(ez_manage.process_position, tradier_manage.process_position / _manage_daytrade_positions).

2026-09-29 grey-switch rewire: these switches existed only in the vector engine
(DAYTRADE_DC_STOP_TF / DAYTRADE_DC_TARGET_TF / WT_LOWER_CROSS_EXIT_TF) or only in the
tradier daytrade wing (DC_DAYTRADE_*_USE_DC(4)_15M legacy aliases). Both engines now
resolve the config through `resolve_daytrade_dc` and fire through the same scalar
predicates below, so a sheet flip means the same thing live and in the sweep.

Every live default is inert ('OFF' / False) -> zero live behaviour change until an
operator promotes a value.  Pure functions: no state, no I/O.
"""
from __future__ import annotations

import math
from typing import Any, Callable, List, Mapping, Optional, Tuple

_TF_ALIAS = {"5m": "3m"}


def parse_tf_list(raw: Any) -> List[str]:
    """'15m,1h' / '15m+1h' / '15m|1h' / '15m 1h' -> ['15m','1h']; 'OFF'/''/None -> []."""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s or s.upper() == "OFF":
        return []
    out: List[str] = []
    for p in s.replace("+", ",").replace("|", ",").replace(" ", ",").split(","):
        p = p.strip()
        if not p or p.upper() == "OFF":
            continue
        p = _TF_ALIAS.get(p, p)
        if p not in out:
            out.append(p)
    return out


def _f(v: Any, d: float) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def resolve_daytrade_dc(get: Callable[[str, Any], Any]) -> Tuple[list, list]:
    """Resolve the DC-channel daytrade exit spec from a config getter get(name, default).

    Returns (stop_specs, target_specs); each spec is a dict
      {tf, field_long, field_short, buf, mode, tag}
    mode 'list'  : DAYTRADE_DC_{STOP,TARGET}_TF path (vec 2026-09-26 semantics):
                   STOP  LONG px <= lvl*(1-buf) / SHORT px >= lvl*(1+buf), buf=DAYTRADE_DC_STOP_BUFFER_PCT%
                   TARGET LONG px >= lvl*(1-buf) / SHORT px <= lvl*(1+buf), buf=DAYTRADE_DC_TARGET_BUFFER_PCT%
    mode 'alias' : legacy DC_DAYTRADE_{STOP,TARGET}_USE_DC(4)_15M (+TRADIER_ twins) — tradier live
                   semantics (2026-09-24): STOP LONG px < dc / SHORT px > dc (no buffer);
                   TARGET |px-dc|/dc < DC_DAYTRADE_TARGET_DC_BUFFER_PCT (fraction, default 0.002);
                   DC4 wins over DC when both set (same precedence as live).  Aliases only apply
                   when the corresponding TF list is OFF (vec precedence).
    """
    stop_specs: list = []
    tgt_specs: list = []
    stop_buf = _f(get("DAYTRADE_DC_STOP_BUFFER_PCT", 0.25), 0.25) / 100.0
    tgt_buf = _f(get("DAYTRADE_DC_TARGET_BUFFER_PCT", 0.10), 0.10) / 100.0
    for tf in parse_tf_list(get("DAYTRADE_DC_STOP_TF", "OFF")):
        stop_specs.append({"tf": tf, "field_long": f"dc_low_{tf}", "field_short": f"dc_high_{tf}", "buf": stop_buf, "mode": "list", "tag": f"dc_{tf}"})
    for tf in parse_tf_list(get("DAYTRADE_DC_TARGET_TF", "OFF")):
        tgt_specs.append({"tf": tf, "field_long": f"dc_high_{tf}", "field_short": f"dc_low_{tf}", "buf": tgt_buf, "mode": "list", "tag": f"dc_{tf}"})

    def _b(name: str) -> bool:
        return bool(get(name, False)) or bool(get("TRADIER_" + name, False))

    if not stop_specs:
        if _b("DC_DAYTRADE_STOP_USE_DC4_15M"):
            stop_specs.append({"tf": "15m", "field_long": "dc_low4_15m", "field_short": "dc_high4_15m", "buf": 0.0, "mode": "alias", "tag": "dc4_15m"})
        if _b("DC_DAYTRADE_STOP_USE_DC_15M"):
            stop_specs.append({"tf": "15m", "field_long": "dc_low_15m", "field_short": "dc_high_15m", "buf": 0.0, "mode": "alias", "tag": "dc_15m", "fallback_of": "dc4_15m"})
    if not tgt_specs:
        # one knob, two names: whichever is overridden away from the 0.002 default wins
        # (DC_ name first) — reproduces tradier live getattr(DC_, getattr(TRADIER_, 0.002)).
        a_buf = _f(get("DC_DAYTRADE_TARGET_DC_BUFFER_PCT", 0.002), 0.002)
        if abs(a_buf - 0.002) <= 1e-12:
            a_buf = _f(get("TRADIER_DC_DAYTRADE_TARGET_DC_BUFFER_PCT", 0.002), 0.002)
        a_buf = a_buf or 0.002
        if _b("DC_DAYTRADE_TARGET_USE_DC4_15M"):
            tgt_specs.append({"tf": "15m", "field_long": "dc_high4_15m", "field_short": "dc_low4_15m", "buf": a_buf, "mode": "alias", "tag": "dc4_15m"})
        if _b("DC_DAYTRADE_TARGET_USE_DC_15M"):
            tgt_specs.append({"tf": "15m", "field_long": "dc_high_15m", "field_short": "dc_low_15m", "buf": a_buf, "mode": "alias", "tag": "dc_15m", "fallback_of": "dc4_15m"})
    return stop_specs, tgt_specs


def dc_stop_hit(px: float, lvl: float, is_long: bool, spec: Mapping[str, Any]) -> bool:
    if not (lvl > 0 and px > 0):
        return False
    buf = float(spec.get("buf", 0.0))
    if spec.get("mode") == "alias":
        return px < lvl if is_long else px > lvl
    return px <= lvl * (1 - buf) if is_long else px >= lvl * (1 + buf)


def dc_target_hit(px: float, lvl: float, is_long: bool, spec: Mapping[str, Any]) -> bool:
    if not (lvl > 0 and px > 0):
        return False
    buf = float(spec.get("buf", 0.0))
    if spec.get("mode") == "alias":
        return abs(px - lvl) / lvl < buf
    if spec.get("mode") == "list":
        # USER 2026-10-06 (director ruling 4): DAYTRADE_TARGET may only activate WITHIN buf (0.1%) of dc_high/dc_low — two-sided
        # proximity, live (ez_manage / tradier_manage) and vec (v12) through this one function. Was one-sided (px >= dc_high*(1-buf)),
        # which also fired on breakouts above the channel live (vec same-bar channel never exceeds the close, so vec is unchanged there).
        return abs(px - lvl) / lvl <= buf
    return px >= lvl * (1 - buf) if is_long else px <= lvl * (1 + buf)


def daytrade_dc_exit(px: float, is_long: bool, stop_specs: list, tgt_specs: list, level: Callable[[str], float]) -> Tuple[bool, str]:
    """First STOP spec that fires wins, then TARGET specs (vec order).  level(field) -> float.
    Alias DC spec with fallback_of=dc4_15m is only consulted when the DC4 level is missing (0),
    exactly like tradier live (_dc_stop == 0 -> use dc_*_15m)."""
    seen = {}
    for sp in stop_specs:
        lvl = _f(level(sp["field_long"] if is_long else sp["field_short"]), 0.0)
        seen[sp["tag"]] = lvl
        if sp.get("fallback_of") and seen.get(sp["fallback_of"], 0.0) > 0:
            continue
        if dc_stop_hit(px, lvl, is_long, sp):
            if sp["mode"] == "alias":
                return True, f"DT_DC_{'4_' if sp['tag'].startswith('dc4') else ''}15M_STOP dc={lvl:.6f}"
            side = "low -" if is_long else "high +"
            return True, f"DAYTRADE_STOP {sp['tag']}_{side.split()[0]} {side.split()[1]}{sp['buf'] * 100:.2f}% STOP_BUF"
    seen = {}
    for sp in tgt_specs:
        lvl = _f(level(sp["field_long"] if is_long else sp["field_short"]), 0.0)
        seen[sp["tag"]] = lvl
        if sp.get("fallback_of") and seen.get(sp["fallback_of"], 0.0) > 0:
            continue
        if dc_target_hit(px, lvl, is_long, sp):
            if sp["mode"] == "alias":
                return True, f"DT_DC_{'4_' if sp['tag'].startswith('dc4') else ''}15M_TARGET dc={lvl:.6f}"
            side = "high -" if is_long else "low +"
            return True, f"DAYTRADE_TARGET {sp['tag']}_{side.split()[0]} {side.split()[1]}{sp['buf'] * 100:.2f}% TARGET_BUF"
    return False, ""


def resolve_technical_dc(get: Callable[[str, Any], Any]) -> Tuple[list, list]:
    """Resolve the TECHNICAL DC-channel exit spec (v12 2026-09-26 semantics, EXIT_STRUCTURAL).

    Reads TECHNICAL_DC_STOP_TF / TECHNICAL_DC_TARGET_TF (+ _BUFFER_PCT). No master gate
    (vec has none) — OFF lists (= live default) resolve to ([], []) = inert. Multi-TF OR:
    exit if ANY listed TF breaches. 5m->3m alias, same as vec _parse_tf_list.
    STOP  LONG px <= dc_low_TF*(1-buf)  / SHORT px >= dc_high_TF*(1+buf)
    TARGET LONG px >= dc_high_TF*(1-buf) / SHORT px <= dc_low_TF*(1+buf)
    Known live-vs-vec delta: vec shifts the STOP channel one bar back when
    DC_PRIOR_BAR_CHANNEL=True; live uses the current indicator value (same
    approximation as the daytrade live twin). Parity delta is measured, not assumed.
    """
    stop_specs: list = []
    tgt_specs: list = []
    stop_buf = _f(get("TECHNICAL_DC_STOP_BUFFER_PCT", 0.25), 0.25) / 100.0
    tgt_buf = _f(get("TECHNICAL_DC_TARGET_BUFFER_PCT", 0.10), 0.10) / 100.0
    for tf in parse_tf_list(get("TECHNICAL_DC_STOP_TF", "OFF")):
        stop_specs.append({"tf": tf, "field_long": f"dc_low_{tf}", "field_short": f"dc_high_{tf}", "buf": stop_buf, "mode": "tech", "tag": f"dc_{tf}"})
    for tf in parse_tf_list(get("TECHNICAL_DC_TARGET_TF", "OFF")):
        tgt_specs.append({"tf": tf, "field_long": f"dc_high_{tf}", "field_short": f"dc_low_{tf}", "buf": tgt_buf, "mode": "tech", "tag": f"dc_{tf}"})
    return stop_specs, tgt_specs


def technical_dc_exit(px: float, is_long: bool, stop_specs: list, tgt_specs: list, level: Callable[[str], float]) -> Tuple[bool, str]:
    """First STOP spec that fires wins, then TARGET specs (vec order). level(field) -> float."""
    for sp in stop_specs:
        lvl = _f(level(sp["field_long"] if is_long else sp["field_short"]), 0.0)
        if dc_stop_hit(px, lvl, is_long, sp):
            side = "low -" if is_long else "high +"
            return True, f"TECHNICAL_STOP {sp['tag']}_{side.split()[0]} {side.split()[1]}{sp['buf'] * 100:.2f}% STOP_BUF"
    for sp in tgt_specs:
        lvl = _f(level(sp["field_long"] if is_long else sp["field_short"]), 0.0)
        if dc_target_hit(px, lvl, is_long, sp):
            side = "high -" if is_long else "low +"
            return True, f"TECHNICAL_TARGET {sp['tag']}_{side.split()[0]} {side.split()[1]}{sp['buf'] * 100:.2f}% TARGET_BUF"
    return False, ""


def wt_lower_cross_tf(raw: Any) -> Optional[str]:
    """WT_LOWER_CROSS_EXIT_TF -> tf or None when OFF."""
    s = str(raw or "OFF").strip()
    return None if (not s or s.upper() == "OFF") else s


def wt_lower_cross_fires(w1: float, w2: float, w1p: float, w2p: float, px: float, pxp: float, is_long: bool) -> bool:
    """LONG: wt1 crosses DOWN through wt2 (w1p>=w2p, w1<w2) AND price below previous bar close.
    SHORT: wt1 crosses UP (w1p<=w2p, w1>w2) AND price above previous close.  Any zero input
    (missing TF data) -> no fire (same as vec)."""
    if w1 == 0 or w2 == 0 or w1p == 0 or w2p == 0 or px <= 0 or pxp <= 0:
        return False
    if is_long:
        return w1 < w2 and w1p >= w2p and px < pxp
    return w1 > w2 and w1p <= w2p and px > pxp


def wt_lower_cross_live(ind: Mapping[str, Any], tf: str, px: float, is_long: bool) -> bool:
    """Live adapter: current wt1/wt2_{tf} vs wt1/wt2_{tf}_prev and price vs close_15m_prev
    (the vec engine's previous 15m base-bar close).  Missing *_prev keys -> no fire."""
    g = lambda k: _f((ind or {}).get(k), 0.0)
    # tradier snapshots carry the previous bar only as _completed_wt{1,2}_{tf}_prev
    gp = lambda k: g(k) or g("_completed_" + k)
    w1, w2, w1p, w2p = g(f"wt1_{tf}"), g(f"wt2_{tf}"), gp(f"wt1_{tf}_prev"), gp(f"wt2_{tf}_prev")
    if w1p and w2p:
        return wt_lower_cross_fires(w1, w2, w1p, w2p, float(px or 0.0), g("close_15m_prev"), is_long)
    # No prev-bar WT in the snapshot (stock NPZ replay / some tradier snapshots): fall back to the
    # indicator pipeline's own "crossed on the current TF bar" flag (wavetrend_intelligence
    # wt_cross_{bear,bull}_{tf} = recent cross with bars_ago==0), same cross definition
    # (wt1 vs wt2 sign change between the last two TF bars). Price leg unchanged.
    flag = g(f"wt_cross_bear_{tf}") if is_long else g(f"wt_cross_bull_{tf}")
    if flag < 0.5 or w1 == 0 or w2 == 0:
        return False
    px, pxp = float(px or 0.0), g("close_15m_prev")
    if px <= 0 or pxp <= 0:
        return False
    return (w1 < w2 and px < pxp) if is_long else (w1 > w2 and px > pxp)
