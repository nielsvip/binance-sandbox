"""live_entry_gates — LIVE twin of the vectorized stock entry gates (Agent D, batch5_live_gates, 2026-10-01). PURE: no I/O, no state.

The vector engine (v12_quick_engine.simulate_one + vec_decisions/check_entry_candidates_stocks__*) applies these gates to fresh entries; in live they sat in
tradier_manage.should_enter_long/short (called only from commented-out code => dead) or were never wired. batch5 calls `check_entry_gates` from the one choke
point every stock entry passes (tradier_manage.queue_trade_action, next to COUNTER_TREND_ADD_BLOCK / EMA_BLANKET_FILTER) with the SAME predicates the vec uses.

Config is read through `get(name, default)` = the per-sym / cat_side resolver (tradier `_cfg`); a `_Cfg` adapter lets the shared vec predicates (which do
getattr(config, ...)) read through it. MASTER SWITCH `LIVE_ENTRY_GATES_ENABLED` (default False in config_tradier) => installing batch5 changes NOTHING until
the user turns it on (globally or per sym_side / cat_side).

effective_min_gain(get): MIN_GAIN_TO_BUY_AGGRESSIVELY as a real swept switch (floor 2.5, CLAUDE.md "NEVER below 2.5"). AUGMENT_MIN_GAIN_PCT (>0) stays an
explicit per-sym override; 0/absent => MIN_GAIN_TO_BUY_AGGRESSIVELY. Today both are 3.0 => effective 3.0 (neutral). Before: `AUGMENT_MIN_GAIN_PCT or MIN_GAIN...`
with AUGMENT_MIN_GAIN_PCT=3.0 always truthy, so sweeping MIN_GAIN_TO_BUY_AGGRESSIVELY alone never changed anything in live OR vec.
"""
from __future__ import annotations

from typing import Any, Callable, Tuple

MIN_GAIN_FLOOR = 2.5
_MISSING = object()
GATE_ACTIONS = ("OPEN",)  # fresh opens only; AUGMENT only for LH_HL when LH_HL_FILTER_AUGMENT_GATE_ENABLED (vec parity)


class _Cfg:
    """getattr(config, name, default) adapter over get(name, default); unknown name -> AttributeError so getattr defaults work."""
    def __init__(self, get: Callable[[str, Any], Any]):
        object.__setattr__(self, "_get", get)

    def __getattr__(self, name: str):
        v = object.__getattribute__(self, "_get")(name, _MISSING)
        if v is _MISSING:
            raise AttributeError(name)
        return v


def effective_min_gain(get: Callable[[str, Any], Any]) -> float:
    try:
        a = float(get("AUGMENT_MIN_GAIN_PCT", 0.0) or 0.0)
    except (TypeError, ValueError):
        a = 0.0
    try:
        m = float(get("MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0) or 3.0)
    except (TypeError, ValueError):
        m = 3.0
    return max(MIN_GAIN_FLOOR, a if a > 0 else m)


def check_entry_gates(get: Callable[[str, Any], Any], indicators: dict, is_long: bool, action: str) -> Tuple[bool, str]:
    """(blocked, tag). Fail-open on any error (caller also wraps). Disabled unless LIVE_ENTRY_GATES_ENABLED."""
    if not bool(get("LIVE_ENTRY_GATES_ENABLED", False)) or not indicators:
        return False, ""
    act = (action or "").upper()
    is_open = "OPEN" in act and "REOPEN" not in act
    is_aug = "AUGMENT" in act
    if not (is_open or is_aug):
        return False, ""
    import vec_decisions.check_entry_candidates_stocks__lh_hl_filter as _lh
    import vec_decisions.check_entry_candidates_stocks__ema_alignment_trend_htf_gates as _eg
    cfg = _Cfg(get)
    if is_open or bool(get("LH_HL_FILTER_AUGMENT_GATE_ENABLED", True)):
        b, why = _lh.check_lh_hl_filter(cfg, indicators, is_long)
        if b:
            return True, why
    if not is_open:
        return False, ""
    for name, fn in (("EMA_9_21", _eg.check_ema_9_21), ("ALIGNMENT", _eg.check_alignment_gate), ("TREND", _eg.check_trend_gate), ("HTF_CONF", _eg.check_htf_conf)):
        if not bool(get("LIVE_ENTRY_GATE_" + name, True)):
            continue
        if name == "EMA_9_21":
            tf = str(get("EMA_9_21_TIMEFRAME", "5m"))
            if f"ema_9_above_21_{tf}" not in indicators:  # live dict lacks the key until batch1 -> cannot judge: fail-open (vec has it in the NPZ)
                continue
        if name == "EMA_9_21":
            # NOT check_ema_9_21(): its `float(x or -1)` turns a stored 0.0 (= ema9 BELOW ema21) into -1 so LONG is never blocked (vec mask `e == 0.0` does block).
            if not bool(get("EMA_9_21_FILTER_ENABLED", False)):
                continue
            v = indicators.get(f"ema_9_above_21_{tf}")
            if v is not None and _eg._ema_9_21_blocks(float(v), is_long):
                return True, f"EMA_9_21_BLOCK_{'LONG' if is_long else 'SHORT'}_{tf}"
            continue
        b, why = fn(cfg, indicators, is_long)
        if b:
            return True, why
    return False, ""
