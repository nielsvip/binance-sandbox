"""
vec_paths/sentiment_fade.py — SENTIMENT_FADE reduce/close path (vec proxy).

LIVE SOURCE:
    tradier_manage.py:7502 inside SentimentManager._manage_individual_position()
    Companion to sentiment_boost.py (entry-side augment).

LIVE BEHAVIOR (scalar, real-time):
    1. SentimentManager computes ideal_qty via calculate_quantity_complex() using
       current market_sentiment_score (inter-symbol breadth, cross-scraper, 0-100).
    2. For each open position, if the live sentiment_score has fallen from the
       "ideal" value stored at open time toward a lower current value:
         For LONG: sentiment degraded (bull→neutral / bull→bear).
         For SHORT: sentiment degraded (bear→neutral / bear→bull).
    3. Fires a PARTIAL or full REDUCE/CLOSE depending on severity.
    4. Reason format: "SENTIMENT_FADE ideal={ideal} cur={cur} loc={loc}"
       where ideal and cur are integers (0-100).
    5. 118 live events in 30 days — dominant tradier REDUCE reason and top
       contributor to low E2E live-vs-backtest match rate.

WHY THIS IS A PROXY:
    sentiment_score is NOT precomputed in the NPZ. It is a daily, context-dependent
    inter-symbol momentum score derived from a live scraper that aggregates sector
    breadth, options flow, and cross-symbol WT breadth at runtime. There is no
    forward-filled daily column for it in backtest_v8_precompute.py yet.

    PROXY APPROACH — use RSI divergence from 50 + MACD crossunder as degraded-
    sentiment proxy:
      For LONG:  RSI_14_5m falling below SENTIMENT_FADE_RSI_LONG_THR (default 45.0)
                 AND macd_crossunder_5m fires → sentiment degrading for long.
      For SHORT: RSI_14_5m rising above SENTIMENT_FADE_RSI_SHORT_THR (default 55.0)
                 AND macd_crossover_5m fires → sentiment degrading for short.

    Rationale: RSI diverging from neutral + MACD cross confirm that price momentum
    has shifted against the position direction — the same market dynamic that drives
    a real sentiment_score drop. Both fields are present in all tradier NPZ files.

# PROXY — real sentiment_score not in NPZ; replace after next NPZ regen includes
# sentiment_score field.

NPZ FIELDS USED:
    rsi_14_5m         — 14-bar RSI on 5m bars (standard, present in all tradier NPZ)
    macd_crossunder_5m — 1 on bar where MACD line crosses under signal line (5m)
    macd_crossover_5m  — 1 on bar where MACD line crosses over signal line (5m)

NPZ REGEN NOTE:
    Add sentiment_score (daily per-symbol, range 0-100, from sector momentum) to
    backtest_v8_precompute.py precompute field list. Once present, replace the RSI/
    MACD proxy below with a direct ideal vs cur delta check matching live logic.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


def check_sentiment_fade(
    store,
    bar_idx: int,
    pos_state,
    mode: str,
    cfg,
) -> Optional[Dict[str, Any]]:
    """SENTIMENT_FADE reduce path — tradier only (stocks), default OFF (proxy).

    Fires when the RSI+MACD proxy signals that sentiment has degraded against
    the current open position direction.

    Args:
        store:     _NPZStore for this symbol.
        bar_idx:   Current bar index.
        pos_state: _PositionState (must be open=True with side set).
        mode:      "crypto" or "tradier". Only active for "tradier".
        cfg:       VecConfig with SENTIMENT_FADE_PROXY_ENABLED + threshold fields.

    Returns:
        dict if SENTIMENT_FADE_PROXY fires, None otherwise.
        dict keys:
            side    — "LONG" or "SHORT"
            reason  — str (matches live SENTIMENT_FADE format where possible)
            reduce  — bool (always True when returned)
    """
    # ── Mode guard — sentiment_score path only exists in tradier_manage.py ──
    if mode != "tradier":
        return None
    if not getattr(cfg, "SENTIMENT_FADE_PROXY_ENABLED", False):
        return None
    if pos_state is None or not pos_state.open:
        return None
    side = pos_state.side
    is_long = (side == "LONG")
    rsi_long_thr = float(getattr(cfg, "SENTIMENT_FADE_RSI_LONG_THR", 45.0))
    rsi_short_thr = float(getattr(cfg, "SENTIMENT_FADE_RSI_SHORT_THR", 55.0))
    # ── Read proxy fields ──
    rsi = store.f("rsi_14_5m", bar_idx, 50.0)
    macd_crossunder = store.f("macd_crossunder_5m", bar_idx, 0.0)
    macd_crossover = store.f("macd_crossover_5m", bar_idx, 0.0)
    # ── Proxy condition: RSI diverging from neutral + MACD cross against side ──
    fade_detected = False
    if is_long:
        # RSI falling below long_thr (from above 50) AND MACD crossed under signal
        if rsi < rsi_long_thr and macd_crossunder >= 1.0:
            fade_detected = True
    else:
        # RSI rising above short_thr (from below 50) AND MACD crossed over signal
        if rsi > rsi_short_thr and macd_crossover >= 1.0:
            fade_detected = True
    if not fade_detected:
        return None
    # ── 2026-05-21 USER MANDATE — LTF-contradiction guard (mirror of live tradier_manage.py:~8458) ──
    # If LTF (5m+15m wt cross + 5m close direction) still aligns WITH the position, REFUSE fade.
    # Gated by SENTIMENT_FADE_LTF_GUARD_ENABLED (default False to preserve baseline behavior — sweep
    # this on/off to validate the live edit shipped 2026-05-21 19:32 SNDK fix).
    if getattr(cfg, "SENTIMENT_FADE_LTF_GUARD_ENABLED", False):
        wt1_5 = store.f("wt1_5m", bar_idx, 0.0)
        wt2_5 = store.f("wt2_5m", bar_idx, 0.0)
        wt1_15 = store.f("wt1_15m", bar_idx, 0.0)
        wt2_15 = store.f("wt2_15m", bar_idx, 0.0)
        close_now = store.f("close_5m", bar_idx, 0.0)
        close_prev = store.f("close_5m", max(bar_idx - 1, 0), 0.0)
        wt5_with = (wt1_5 > wt2_5) if is_long else (wt1_5 < wt2_5)
        wt15_with = (wt1_15 > wt2_15) if is_long else (wt1_15 < wt2_15)
        price_with = (close_now > close_prev > 0) if is_long else (0 < close_now < close_prev)
        with_count = int(wt5_with) + int(wt15_with) + int(price_with)
        if with_count >= 2:
            return None
    # ── Reason format mirrors live: "SENTIMENT_FADE ideal=N cur=N loc=X.X" ──
    # Proxy: encode RSI as cur, rsi_long/short_thr as ideal (approximate integers)
    ideal = int(rsi_long_thr) if is_long else int(rsi_short_thr)
    cur = int(round(rsi))
    reason = (
        f"SENTIMENT_FADE_PROXY_rsi={rsi:.1f}_macd_cross"
        f" ideal={ideal} cur={cur} loc={rsi:.1f}"
    )
    return {
        "side": side,
        "reason": reason,
        "reduce": True,
    }


def check_sentiment_fade_vec(
    store,
    bar_idx_arr: np.ndarray,
    pos_open_arr: np.ndarray,
    pos_side_arr: np.ndarray,
    mode: str,
    cfg,
) -> np.ndarray:
    """Vectorized SENTIMENT_FADE_PROXY path — tradier only.

    Computes the fade signal for a batch of (bar_idx, position) pairs in one
    array pass. Used by the sweep engine when V8_USE_VEC_ALL=1.

    Args:
        store:        _NPZStore for this symbol.
        bar_idx_arr:  shape (N,) int — bar index per sample.
        pos_open_arr: shape (N,) bool — True if position is open at that bar.
        pos_side_arr: shape (N,) object — "LONG" or "SHORT" per sample.
        mode:         "crypto" or "tradier".
        cfg:          VecConfig (SENTIMENT_FADE_PROXY_ENABLED, threshold fields).

    Returns:
        shape (N,) bool — True where SENTIMENT_FADE_PROXY would fire.
    """
    n = len(bar_idx_arr)
    result = np.zeros(n, dtype=bool)
    # ── Mode + enabled guard ──
    if mode != "tradier":
        return result
    if not getattr(cfg, "SENTIMENT_FADE_PROXY_ENABLED", False):
        return result
    rsi_long_thr = float(getattr(cfg, "SENTIMENT_FADE_RSI_LONG_THR", 45.0))
    rsi_short_thr = float(getattr(cfg, "SENTIMENT_FADE_RSI_SHORT_THR", 55.0))
    # ── Batch-fetch proxy fields ──
    rsi_arr = np.array(
        [store.f("rsi_14_5m", int(i), 50.0) for i in bar_idx_arr],
        dtype=float,
    )
    xu_arr = np.array(
        [store.f("macd_crossunder_5m", int(i), 0.0) for i in bar_idx_arr],
        dtype=float,
    )
    xo_arr = np.array(
        [store.f("macd_crossover_5m", int(i), 0.0) for i in bar_idx_arr],
        dtype=float,
    )
    is_long_arr = np.array([s == "LONG" for s in pos_side_arr], dtype=bool)
    open_arr = np.asarray(pos_open_arr, dtype=bool)
    # ── LONG fade: RSI < long_thr AND macd_crossunder ──
    long_fade = (
        open_arr
        & is_long_arr
        & (rsi_arr < rsi_long_thr)
        & (xu_arr >= 1.0)
    )
    # ── SHORT fade: RSI > short_thr AND macd_crossover ──
    short_fade = (
        open_arr
        & ~is_long_arr
        & (rsi_arr > rsi_short_thr)
        & (xo_arr >= 1.0)
    )
    result = long_fade | short_fade
    # ── 2026-05-21 LTF-contradiction guard (vec batch — mirrors scalar guard above) ──
    if getattr(cfg, "SENTIMENT_FADE_LTF_GUARD_ENABLED", False) and result.any():
        wt1_5_arr = np.array([store.f("wt1_5m", int(i), 0.0) for i in bar_idx_arr], dtype=float)
        wt2_5_arr = np.array([store.f("wt2_5m", int(i), 0.0) for i in bar_idx_arr], dtype=float)
        wt1_15_arr = np.array([store.f("wt1_15m", int(i), 0.0) for i in bar_idx_arr], dtype=float)
        wt2_15_arr = np.array([store.f("wt2_15m", int(i), 0.0) for i in bar_idx_arr], dtype=float)
        close_now = np.array([store.f("close_5m", int(i), 0.0) for i in bar_idx_arr], dtype=float)
        close_prev = np.array([store.f("close_5m", max(int(i) - 1, 0), 0.0) for i in bar_idx_arr], dtype=float)
        wt5_with = np.where(is_long_arr, wt1_5_arr > wt2_5_arr, wt1_5_arr < wt2_5_arr)
        wt15_with = np.where(is_long_arr, wt1_15_arr > wt2_15_arr, wt1_15_arr < wt2_15_arr)
        price_with = np.where(
            is_long_arr,
            (close_now > close_prev) & (close_prev > 0),
            (close_now < close_prev) & (close_now > 0),
        )
        with_count = wt5_with.astype(int) + wt15_with.astype(int) + price_with.astype(int)
        ltf_block = with_count >= 2
        result = result & ~ltf_block
    return result


# FUTURE: replace proxy with real sentiment_score field once NPZ regen runs.
# Required NPZ field: sentiment_score (daily, per-symbol, 0-100 scale, from sector
# momentum). Add to backtest_v8_precompute.py precompute list.
# Once available, re-implement check_sentiment_fade() as:
#   ideal = store.f("sentiment_score_at_open", bar_idx, 50.0)  # stored at open
#   cur   = store.f("sentiment_score", bar_idx, 50.0)
#   reason = f"SENTIMENT_FADE ideal={int(ideal)} cur={int(cur)} loc={cur:.1f}"
# and remove RSI/MACD proxy entirely.
