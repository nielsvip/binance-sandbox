"""
vec_paths/wt_crossunder_final.py — WT crossunder/crossover final resort exit.

SOURCE: tradier_manage.py:~5273 (WT CROSSUNDER FINAL RESORT block, inside delta gate)
         tradier_manage.py:~5332 (standalone block, when DELTA_ENGINE is OFF)
         backtest_v8_engine.py:~3861 (V8 reason format: WT_CROSSUNDER_FINAL_V8_*)

LIVE REASON FORMAT (from /history/ — note: NO "_V8_" suffix in live):
  LONG exit: WT_CROSSUNDER_FINAL_5m_15m_1h{bool}_4h{bool}_D{bool}_g{N}%_hold{N}m_MANDATORY_REENTRY
  SHORT exit: WT_CROSSOVER_FINAL_5m_15m_1h{bool}_4h{bool}_D{bool}_g{N}%_hold{N}m_MANDATORY_REENTRY

V8 backtest reason format (from backtest_v8_engine.py:3883):
  WT_CROSSUNDER_FINAL_V8_5m_15m_1h{bool}_4h{bool}_D{bool}_g{N}%_MANDATORY_REENTRY

CONDITIONS (LONG exit):
    LTF (5m/3m wt1 < wt2) is going DOWN (against LONG)
    AND 15m is DOWN (wt1_15m < wt2_15m) OR overbought (wt1_15m > 95)
    AND at least 1 of (1h, 4h, D): wt1 < wt2 (against LONG)
    AND NOT parabolic uptrend (parabolic bypass — skip exit if price is in parabolic up)

CONDITIONS (SHORT exit / WT_CROSSOVER_FINAL):
    LTF (5m/3m wt1 > wt2) is going UP (against SHORT)
    AND 15m is UP (wt1_15m > wt2_15m) OR oversold (wt1_15m < -95)
    AND at least 1 of (1h, 4h, D): wt1 > wt2 (against SHORT)
    AND NOT parabolic downtrend

PARABOLIC BYPASS:
    tradier_manage.py calls _parabolic_state(ind, config) — a function that checks if
    adx_D, dc_position_D, bb_pct_b_D are all in extreme territory. Vec approximation:
    skip the bypass check (more conservative = fires more exits = safer for backtest).
    Config: WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED (default False in vec).

NOTE: This exit fires REGARDLESS of gain (no UNIVERSAL_NOLOSS_GATE check in live —
except when inside NOLOSS account gate). In vec, we honor cfg.UNIVERSAL_NOLOSS_GATE
for consistency with the rest of the engine, unless WT_CROSSUNDER_FINAL_NOLOSS_BYPASS=True.

RETURN TYPE:
    None — did not fire
    dict — {
        "reason": str,          # matches live pattern
        "path": str,            # "WT_CROSSUNDER_FINAL" or "WT_CROSSOVER_FINAL"
        "bypass_noloss": bool,  # False by default (live has NOLOSS gate ABOVE this exit)
        "xu_1h": bool,
        "xu_4h": bool,
        "xu_D": bool,
    }

CONFIG KEYS:
    WT_CROSSUNDER_FINAL_ENABLED: bool = True   (default ON matching live)
    WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED: bool = False  (vec skips the check by default)
    WT_CROSSUNDER_FINAL_NOLOSS_BYPASS: bool = False  (vec respects NOLOSS by default)
"""
from __future__ import annotations

from typing import Any, Optional


def check_wt_crossunder_final_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """WT crossunder final resort exit — LTF + 15m + ≥1 HTF against position.

    Mirrors tradier_manage.py:~5288–5321 (within delta gate) and ~5345–5372 (standalone).
    Fires for BOTH crypto (3m base) and tradier (5m base).

    Args:
        store:     _NPZStore
        bar_idx:   current bar index
        pos_state: _PositionState
        mode:      "crypto" or "tradier"
        cfg:       VecConfig

    Returns:
        dict or None.
    """
    if not getattr(cfg, "WT_CROSSUNDER_FINAL_ENABLED", True):
        return None
    if not pos_state.open:
        return None

    # LTF: 5m for tradier, 3m for crypto. Fall back to each other.
    if mode == "tradier":
        wt1_ltf = store.f("wt1_5m", bar_idx, 0.0) or store.f("wt1_3m", bar_idx, 0.0)
        wt2_ltf = store.f("wt2_5m", bar_idx, 0.0) or store.f("wt2_3m", bar_idx, 0.0)
        ltf_tag = "5m"
    else:
        wt1_ltf = store.f("wt1_3m", bar_idx, 0.0) or store.f("wt1_5m", bar_idx, 0.0)
        wt2_ltf = store.f("wt2_3m", bar_idx, 0.0) or store.f("wt2_5m", bar_idx, 0.0)
        ltf_tag = "3m"

    wt1_15m = store.f("wt1_15m", bar_idx, 0.0)
    wt2_15m = store.f("wt2_15m", bar_idx, 0.0)
    wt1_1h = store.f("wt1_1h", bar_idx, 0.0)
    wt2_1h = store.f("wt2_1h", bar_idx, 0.0)
    wt1_4h = store.f("wt1_4h", bar_idx, 0.0)
    wt2_4h = store.f("wt2_4h", bar_idx, 0.0)
    wt1_D = store.f("wt1_D", bar_idx, 0.0)
    wt2_D = store.f("wt2_D", bar_idx, 0.0)

    is_long = (pos_state.side == "LONG")
    gain = pos_state.gain_pct
    hold_min = 0.0
    try:
        _ts_arr = store.timestamps if hasattr(store, "timestamps") else store.arrays.get("timestamps")
        if _ts_arr is not None and pos_state.entry_ts > 0:
            hold_min = (float(_ts_arr[bar_idx]) - pos_state.entry_ts) / 60.0
    except Exception:
        hold_min = 0.0

    if is_long:
        ltf_down = wt1_ltf < wt2_ltf
        m15_confirm = (wt1_15m < wt2_15m) or (wt1_15m > 95)
        xu_1h = wt1_1h < wt2_1h
        xu_4h = wt1_4h < wt2_4h
        xu_D = wt1_D < wt2_D
        htf_against = xu_1h or xu_4h or xu_D

        if not (ltf_down and m15_confirm and htf_against):
            return None

        parabolic_bypass = getattr(cfg, "WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED", False)
        if parabolic_bypass:
            pp_up = _is_parabolic_up(store, bar_idx)
            if pp_up:
                return None

        reason = (
            f"WT_CROSSUNDER_FINAL_{ltf_tag}_15m"
            f"_1h{xu_1h}_4h{xu_4h}_D{xu_D}"
            f"_g{gain:.2f}%_hold{int(hold_min)}m_MANDATORY_REENTRY"
        )
        path = "WT_CROSSUNDER_FINAL"
    else:
        ltf_up = wt1_ltf > wt2_ltf
        m15_confirm = (wt1_15m > wt2_15m) or (wt1_15m < -95)
        xo_1h = wt1_1h > wt2_1h
        xo_4h = wt1_4h > wt2_4h
        xo_D = wt1_D > wt2_D
        htf_against = xo_1h or xo_4h or xo_D

        if not (ltf_up and m15_confirm and htf_against):
            return None

        parabolic_bypass = getattr(cfg, "WT_CROSSUNDER_FINAL_PARABOLIC_BYPASS_ENABLED", False)
        if parabolic_bypass:
            pp_dn = _is_parabolic_down(store, bar_idx)
            if pp_dn:
                return None

        xu_1h = xo_1h
        xu_4h = xo_4h
        xu_D = xo_D
        reason = (
            f"WT_CROSSOVER_FINAL_{ltf_tag}_15m"
            f"_1h{xu_1h}_4h{xu_4h}_D{xu_D}"
            f"_g{gain:.2f}%_hold{int(hold_min)}m_MANDATORY_REENTRY"
        )
        path = "WT_CROSSOVER_FINAL"

    return {
        "reason": reason,
        "path": path,
        "bypass_noloss": bool(getattr(cfg, "WT_CROSSUNDER_FINAL_NOLOSS_BYPASS", False)),
        "xu_1h": xu_1h,
        "xu_4h": xu_4h,
        "xu_D": xu_D,
    }


def _is_parabolic_up(store: Any, bar_idx: int) -> bool:
    """Approximate parabolic uptrend check (mirrors _parabolic_state in tradier_manage.py).

    Real logic requires adx_D + dc_position_D + bb_pct_b_D all extreme.
    Vec approximation: adx_D > 40 AND bb_pct_b_D > 0.9.
    Returns True if parabolic uptrend detected (skip exit).
    """
    adx_D = store.f("adx_D", bar_idx, 25.0)
    bb_pctb_D = store.f("bb_pct_b_D", bar_idx, 0.5)
    dc_pos_D = store.f("dc_position_D", bar_idx, 0.5)
    return adx_D > 40 and bb_pctb_D > 0.85 and dc_pos_D > 0.7


def _is_parabolic_down(store: Any, bar_idx: int) -> bool:
    """Approximate parabolic downtrend check."""
    adx_D = store.f("adx_D", bar_idx, 25.0)
    bb_pctb_D = store.f("bb_pct_b_D", bar_idx, 0.5)
    dc_pos_D = store.f("dc_position_D", bar_idx, 0.5)
    return adx_D > 40 and bb_pctb_D < 0.15 and dc_pos_D < 0.3
