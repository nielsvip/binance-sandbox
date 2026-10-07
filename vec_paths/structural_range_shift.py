"""
vec_paths/structural_range_shift.py — Structural Range Shift exit (SRS).

SOURCE (live): tradier_manage.py:~5129 (_check_exit_conditions SRS block)
SOURCE (v8):   backtest_v8_engine.py:~2236 (V8 relaxed/strict SRS block)

LIVE REASON FORMAT (from /history/ — note: NO "_V8_" suffix in live):
  LONG:  STRUCTURAL_RANGE_SHIFT_LONG_{tf}_top={high:.4f}_k1h={k1h:.0f}_k15m={k15m:.0f}_k5m={k5m:.0f}
  SHORT: STRUCTURAL_RANGE_SHIFT_SHORT_{tf}_bot={low:.4f}_k1h={k1h:.0f}_k15m={k15m:.0f}_k5m={k5m:.0f}
  Also seen (alt format):
    STRUCTURAL_RANGE_SHIFT_LONG_bb_4h_entry=82.31>75.37
    STRUCTURAL_RANGE_SHIFT_SHORT_bb_4h_entry=154.44<158.12

V8 reason format (backtest_v8_engine.py:2293):
  STRUCTURAL_RANGE_SHIFT_{LONG|SHORT}_V8_{tf}

CONDITIONS for LIVE LONG exit (mirrors tradier_manage.py:5150–5157):
    entry_price > bb_upper_{tf}  (entered outside upper band — stretched)
    AND price near bb_upper (within PROXIMITY_BPS / 10000 fraction)
    AND stoch_k_1h > K_HIGH  (overbought 1h)
    AND stoch_k_15m > K_HIGH (overbought 15m)
    AND stoch_k_5m < stoch_k_5m_prev  (5m stoch turning down — momentum fading)

CONDITIONS for LIVE SHORT exit (mirrors tradier_manage.py:5158–5165):
    entry_price < bb_lower_{tf}  (entered outside lower band — stretched)
    AND price near bb_lower (within PROXIMITY_BPS / 10000)
    AND stoch_k_1h < K_LOW   (oversold 1h)
    AND stoch_k_15m < K_LOW  (oversold 15m)
    AND stoch_k_5m > stoch_k_5m_prev  (5m stoch turning up — momentum reversing)

V8 CONDITIONS (stricter — backtest_v8_engine.py:2276-2289):
    Adds WT crossunder requirements: wt1_1h < wt2_1h, wt1_15m < wt2_15m, wt1_3m < wt2_3m
    Plus "entry > upper" precondition.

This module implements LIVE conditions (not V8) since we validate against /history/.
Set SRS_USE_V8_CASCADE=True to use the stricter V8 conditions for backtest isolation.

SRS bypasses the UNIVERSAL_NOLOSS_GATE in tradier_manage:
  _noloss_min_ts gate fires BELOW SRS (line 5218: "AFTER SRS so SRS can exit at a loss")
  SRS is the ONE tradier exit allowed to close at a loss.
  This is mirrored here: bypass_noloss=True for SRS.

RETURN TYPE:
    None — did not fire
    dict — {
        "reason": str,
        "path": "STRUCTURAL_RANGE_SHIFT_LONG" or "STRUCTURAL_RANGE_SHIFT_SHORT",
        "bypass_noloss": True,  (SRS is above NOLOSS gate in live)
    }

CONFIG KEYS (mirroring config_tradier.py):
    STRUCTURAL_RANGE_SHIFT_EXIT: bool = False     (default OFF — sweep-tunable)
    STRUCTURAL_RANGE_SHIFT_TF: str = "bb_1h"      (bb_1h/bb_4h/dc_1h/dc_4h/bb_D/dc_D)
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS: float = 100.0
    STRUCTURAL_RANGE_SHIFT_K_HIGH: float = 80.0   (live: 80, v8: 75 in some configs)
    STRUCTURAL_RANGE_SHIFT_K_LOW: float = 20.0
    SRS_USE_V8_CASCADE: bool = False  (if True, requires WT crossunder conditions too)
"""
from __future__ import annotations

from typing import Any, Optional

_TF_FIELD_MAP = {
    "bb_1h": ("bb_upper_1h", "bb_lower_1h"),
    "bb_4h": ("bb_upper_4h", "bb_lower_4h"),
    "bb_D":  ("bb_upper_D",  "bb_lower_D"),
    "dc_1h": ("dc_high_1h",  "dc_low_1h"),
    "dc_4h": ("dc_high_4h",  "dc_low_4h"),
    "dc_D":  ("dc_high_D",   "dc_low_D"),
}


def check_srs_exit(
    store: Any,
    bar_idx: int,
    pos_state: Any,
    mode: str,
    cfg: Any,
) -> Optional[dict]:
    """Structural Range Shift exit.

    Mirrors tradier_manage.py:~5129 (live) and backtest_v8_engine.py:~2236 (V8).
    SRS fires ABOVE the UNIVERSAL_NOLOSS_GATE — can close at a loss.

    Args:
        store:     _NPZStore
        bar_idx:   current bar index
        pos_state: _PositionState
        mode:      "crypto" or "tradier" (SRS is tradier-only in live, but we
                   allow crypto mode for V8 backtest parity — config gate controls it)
        cfg:       VecConfig

    Returns:
        dict with reason/path/bypass_noloss, or None.
    """
    if not getattr(cfg, "STRUCTURAL_RANGE_SHIFT_EXIT", False):
        return None
    if not pos_state.open:
        return None

    tf = getattr(cfg, "STRUCTURAL_RANGE_SHIFT_TF", "bb_1h")
    high_key, low_key = _TF_FIELD_MAP.get(tf, ("bb_upper_1h", "bb_lower_1h"))

    high = store.f(high_key, bar_idx, 0.0)
    low = store.f(low_key, bar_idx, 0.0)
    if high <= 0 or low <= 0:
        return None

    price = store.price(bar_idx)
    if price <= 0:
        return None

    entry = pos_state.entry_price
    if entry <= 0:
        return None

    prox_bps = float(getattr(cfg, "STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS", 100.0))
    band = prox_bps / 10000.0
    k_high = float(getattr(cfg, "STRUCTURAL_RANGE_SHIFT_K_HIGH", 80.0))
    k_low = float(getattr(cfg, "STRUCTURAL_RANGE_SHIFT_K_LOW", 20.0))
    use_v8 = bool(getattr(cfg, "SRS_USE_V8_CASCADE", False))

    k1h = store.f("stoch_k_1h", bar_idx, 50.0)
    k15m = store.f("stoch_k_15m", bar_idx, 50.0)
    k5m = store.f("stoch_k_5m", bar_idx, 50.0)
    prev_idx = max(0, bar_idx - 1)
    k5m_prev = store.f("stoch_k_5m", prev_idx, k5m)

    is_long = (pos_state.side == "LONG")

    if is_long:
        if entry <= high:
            return None
        prox = abs(price - high) / high <= band
        stoch_1h_ob = k1h > k_high
        stoch_15m_ob = k15m > k_high
        stoch_5m_dn = k5m < k5m_prev

        if use_v8:
            wt1_1h = store.f("wt1_1h", bar_idx, 0.0)
            wt2_1h = store.f("wt2_1h", bar_idx, 0.0)
            wt1_15m = store.f("wt1_15m", bar_idx, 0.0)
            wt2_15m = store.f("wt2_15m", bar_idx, 0.0)
            wt1_3m = store.f("wt1_3m", bar_idx, 0.0) or store.f("wt1_5m", bar_idx, 0.0)
            wt2_3m = store.f("wt2_3m", bar_idx, 0.0) or store.f("wt2_5m", bar_idx, 0.0)
            fires = (
                prox
                and (k1h >= k_high) and (k1h < store.f("stoch_k_1h", prev_idx, k1h))
                and wt1_1h < wt2_1h
                and (k15m >= k_high) and (k15m < store.f("stoch_k_15m", prev_idx, k15m))
                and wt1_15m < wt2_15m
                and wt1_3m < wt2_3m
            )
        else:
            fires = prox and stoch_1h_ob and stoch_15m_ob and stoch_5m_dn

        if not fires:
            return None

        reason = (
            f"STRUCTURAL_RANGE_SHIFT_LONG_{tf}"
            f"_top={high:.4f}_k1h={k1h:.0f}_k15m={k15m:.0f}_k5m={k5m:.0f}"
        )
        path = "STRUCTURAL_RANGE_SHIFT_LONG"
    else:
        if entry >= low:
            return None
        prox = abs(price - low) / low <= band
        stoch_1h_os = k1h < k_low
        stoch_15m_os = k15m < k_low
        stoch_5m_up = k5m > k5m_prev

        if use_v8:
            wt1_1h = store.f("wt1_1h", bar_idx, 0.0)
            wt2_1h = store.f("wt2_1h", bar_idx, 0.0)
            wt1_15m = store.f("wt1_15m", bar_idx, 0.0)
            wt2_15m = store.f("wt2_15m", bar_idx, 0.0)
            wt1_3m = store.f("wt1_3m", bar_idx, 0.0) or store.f("wt1_5m", bar_idx, 0.0)
            wt2_3m = store.f("wt2_3m", bar_idx, 0.0) or store.f("wt2_5m", bar_idx, 0.0)
            fires = (
                prox
                and (k1h <= k_low) and (k1h > store.f("stoch_k_1h", prev_idx, k1h))
                and wt1_1h > wt2_1h
                and (k15m <= k_low) and (k15m > store.f("stoch_k_15m", prev_idx, k15m))
                and wt1_15m > wt2_15m
                and wt1_3m > wt2_3m
            )
        else:
            fires = prox and stoch_1h_os and stoch_15m_os and stoch_5m_up

        if not fires:
            return None

        reason = (
            f"STRUCTURAL_RANGE_SHIFT_SHORT_{tf}"
            f"_bot={low:.4f}_k1h={k1h:.0f}_k15m={k15m:.0f}_k5m={k5m:.0f}"
        )
        path = "STRUCTURAL_RANGE_SHIFT_SHORT"

    return {
        "reason": reason,
        "path": path,
        "bypass_noloss": True,
    }
