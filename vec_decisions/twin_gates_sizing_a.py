"""twin_gates_sizing_a.py — SHARED scalar predicates for GLOBAL_RISK_GATES + entry-gate switches (batch A, 19 switches).

Single source of truth for the live twins of v12_quick_engine vector logic.
Live managers (ez_manage crypto, tradier_manage stocks) call these pure
functions; the vector engine keeps its own numpy code. Every formula below
mirrors an exact vec/live site (cited per function). Pure: no state, no I/O.

Conventions (dc_channel_exits style): get(name, default) config getter,
ind indicator dict, is_long side flag. Disabled switch -> neutral return
(False fire / False block / None mult) so defaults are inert.

Switch defaults used here are the EFFECTIVE values (QuickConfig field /
config.py / config_tradier.py), not the getattr fallbacks at the vec site
(which never bind when the dataclass field exists). Known live<->vec
default gaps are flagged in the report, not papered over here.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

Get = Callable[[str, Any], Any]


def _f(v: Any, d: float) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def _tfs(s: Any) -> list:
    return [t.strip() for t in str(s or "").split(",") if t.strip()]


def _k_base(ind: Mapping[str, Any], base: str, d: float = 50.0) -> float:
    m = ind or {}
    if f"k_{base}" in m:
        return _f(m.get(f"k_{base}"), d)
    return _f(m.get(f"stoch_k_{base}", d), d)


def rsi2_fires(get: Get, ind: Mapping[str, Any], is_long: bool) -> Tuple[bool, str]:
    """RSI2_ENABLED (vec v12:8480-86; live tradier:13240-51)."""
    if not bool(get("RSI2_ENABLED", True)):
        return False, ""
    m = ind or {}
    v = _f(m.get("rsi2_1h", m.get("rsi_1h", 50)), 50)
    thr = _f(get("RSI2_ENTRY_THRESHOLD", 3.0), 3.0)
    fire = (v < thr) if is_long else (v > (100.0 - thr))
    return fire, f"RSI2_ENTRY_v{v:.1f}_thr{thr:.1f}" if fire else ""


def connors_fires(get: Get, ind: Mapping[str, Any], is_long: bool) -> Tuple[bool, str]:
    """CONNORS_RSI_ENABLED (vec v12:8487-93; live tradier:13252-63)."""
    if not bool(get("CONNORS_RSI_ENABLED", False)):
        return False, ""
    m = ind or {}
    v = _f(m.get("connors_rsi_D", m.get("connors_rsi_1h", 50)), 50)
    thr = _f(get("CONNORS_RSI_ENTRY_THRESHOLD", 10.0), 10.0)
    fire = (v < thr) if is_long else (v > (100.0 - thr))
    return fire, f"CONNORS_RSI_ENTRY_v{v:.1f}_thr{thr:.1f}" if fire else ""


def clenow_blocks(get: Get, ind: Mapping[str, Any], is_long: bool, price: float) -> Tuple[bool, str]:
    """CLENOW_ENABLED entry veto (vec v12:9504-20, sma_200_1h leg).

    Vec: clenow_ok long = close > sma_200_1h (gate only binds when the
    array is populated). Scalar: sma<=0 -> pass (fail-open).
    Includes the CLENOW_GATE_ENABLED sub-gate (vec v12:9512-20).
    Known delta: stocks live (tradier _apply_research_only_live_gates)
    compares against sma200_D with strict </>; vec uses sma_200_1h.
    """
    if not bool(get("CLENOW_ENABLED", False)):
        return False, ""
    sma = _f((ind or {}).get("sma_200_1h", 0), 0)
    px = _f(price, 0)
    if sma > 0:
        if (is_long and px <= sma) or ((not is_long) and px >= sma):
            return True, f"CLENOW_SMA200_BLOCK_px{px:.4f}_sma{sma:.4f}"
    if bool(get("CLENOW_GATE_ENABLED", False)) and sma > 0 and px > 0:
        score = (px - sma) / sma * 100.0
        thr = _f(get("CLENOW_GATE_MIN_SCORE", 15.0), 15.0) / 5.0
        if (is_long and score < thr) or ((not is_long) and score > -thr):
            return True, f"CLENOW_GATE_BLOCK_sc{score:.2f}_thr{thr:.2f}"
    return False, ""


def confluence_blocks(get: Get, ind: Mapping[str, Any], is_long: bool, base: str) -> Tuple[bool, str]:
    """CONFLUENCE_MODE_ENABLED veto (live tradier:15367-81 3-vote approximation).

    Vec (v12:9126-29) requires CONFLUENCE_MIN_BLOCKS of the OR-blocks to
    agree; live has no block decomposition so both venues count 3 votes:
    WT alignment + stoch zone + MFI zone. base: '3m' crypto, '5m' stocks
    (stocks keeps the tradier wt1_3m->wt1_5m / stoch_k_3m->stoch_k_5m
    fallback order verbatim).
    """
    if not bool(get("CONFLUENCE_MODE_ENABLED", False)):
        return False, ""
    m = ind or {}
    need = int(_f(get("CONFLUENCE_MIN_BLOCKS", 2), 2))
    if base == "5m":
        w1 = _f(m.get("wt1_3m", m.get("wt1_5m", 0)), 0)
        w2 = _f(m.get("wt2_3m", m.get("wt2_5m", 0)), 0)
        k = _f(m.get("stoch_k_3m", m.get("stoch_k_5m", 50)), 50)
    else:
        w1 = _f(m.get("wt1_3m", 0), 0)
        w2 = _f(m.get("wt2_3m", 0), 0)
        k = _k_base(m, "3m")
    mfi = _f(m.get("mfi_1h", 50), 50)
    agree = 0
    if (is_long and w1 > w2) or ((not is_long) and w1 < w2):
        agree += 1
    if (is_long and k < 40) or ((not is_long) and k > 60):
        agree += 1
    if (is_long and mfi < 60) or ((not is_long) and mfi > 40):
        agree += 1
    if agree < need:
        return True, f"CONFLUENCE_BLOCK_agree{agree}_need{need}"
    return False, ""


def vel_exit_fires(get: Get, ind: Mapping[str, Any], is_long: bool) -> Tuple[bool, str]:
    """VEL_EXIT_ENABLED trigger (vec v12:9898, OR'd into _all_exit at v12:10157).

    Vec also ANDs VEC_VEL_EXIT_AS_TRIGGER (QuickConfig True); the twin
    reads it too so a flip stays faithful on both sides.
    """
    if not bool(get("VEL_EXIT_ENABLED", True)):
        return False, ""
    if not bool(get("VEC_VEL_EXIT_AS_TRIGGER", True)):
        return False, ""
    v = _f((ind or {}).get("wt_velocity_4h", 0), 0)
    fire = (v < -2.0) if is_long else (v > 2.0)
    return fire, f"VEL_EXIT_v4h{v:.2f}" if fire else ""


def vel_exit_gate_blocks(enabled: bool) -> bool:
    """VEL_EXIT research-gate leg (live tradier:15395-97): disabled -> exit blocked."""
    return not bool(enabled)


def wt_vel_decay_fires(get: Get, ind: Mapping[str, Any], is_long: bool, base: str) -> Tuple[bool, str]:
    """WT_VEL_DECAY_EXIT_ENABLED (vec v12:9905-15; live tradier:20232-42)."""
    if not bool(get("WT_VEL_DECAY_EXIT_ENABLED", False)):
        return False, ""
    m = ind or {}
    thr = _f(get("WT_VEL_DECAY_THRESHOLD", 1.0), 1.0)
    v1h = _f(m.get("wt_velocity_1h", 0), 0)
    prev = _f(m.get("wt_velocity_1h_prev", v1h), v1h)
    vb = _f(m.get("wt_velocity", m.get(f"wt_velocity_{base}", 0)), 0)
    if is_long:
        fire = (prev > thr * 2) and (v1h < thr) and (vb < prev * 0.5)
    else:
        fire = (prev < -thr * 2) and (v1h > -thr) and (vb > prev * 0.5)
    return fire, f"WT_VEL_DECAY_v1h{v1h:.2f}_prev{prev:.2f}" if fire else ""


def fh_enabled(get: Get, is_tradier: bool) -> bool:
    """FH flag select (vec v12:8763): tradier -> TRADIER_FH_MOMENTUM_ENABLED else FH_MOMENTUM_ENABLED."""
    return bool(get("TRADIER_FH_MOMENTUM_ENABLED", True)) if is_tradier else bool(get("FH_MOMENTUM_ENABLED", True))


def fh_momentum_fires(get: Get, ind: Mapping[str, Any], is_long: bool, now_min_utc: float, price: float, day_open: float) -> Tuple[bool, str]:
    """FH_MOMENTUM / TRADIER_FH_MOMENTUM block (vec v12:8763-83).

    Sub-knobs are TRADIER_FH_MOMENTUM_* on BOTH modes (vec reads no
    FH_MOMENTUM_* sub-knob here). Window = minutes since 13:30 UTC.
    """
    move = (price - day_open) / day_open * 100.0 if day_open > 0 else 0.0
    window = _f(get("TRADIER_FH_MOMENTUM_WINDOW_MINUTES", 60), 60)
    mins = _f(now_min_utc, -1) - (13 * 60 + 30)
    in_window = 0 <= mins <= window
    min_move = _f(get("TRADIER_FH_MOMENTUM_MIN_MOVE_PCT", 0.5), 0.5)
    moved = (move >= min_move) if is_long else (move <= -min_move)
    ok = in_window and moved
    if ok and bool(get("TRADIER_FH_MOMENTUM_DC_CONFIRM", True)):
        dc_max = _f(get("TRADIER_FH_MOMENTUM_DC_MAX_LONG", 0.33), 0.33)
        _fh_tf = str(get("FH_MOMENTUM_FILTER_TF", "15m") or "15m")
        if _fh_tf != "OFF":
            if _fh_tf not in ("3m", "5m", "15m", "1h", "4h", "D"):
                _fh_tf = "15m"
            _fh_dc = (ind or {}).get(f"dc_position_{_fh_tf}", None)
            if _fh_dc is None:
                _fh_dc = (ind or {}).get("dc_position_15m", 0.5)
            dc = _f(_fh_dc, 0.5)
            ok = ok and ((dc <= dc_max) if is_long else (dc >= (1 - dc_max)))
    if ok and bool(get("TRADIER_FH_MOMENTUM_MFI_CONFIRM", True)):
        mfi_min = _f(get("TRADIER_FH_MOMENTUM_MFI_MIN", 55.0), 55.0)
        mfi = _f((ind or {}).get("mfi_1h", 50), 50)
        ok = ok and ((mfi >= mfi_min) if is_long else (mfi <= (100 - mfi_min)))
    return ok, f"FH_MOMENTUM_move{move:.2f}_min{mins:.0f}" if ok else ""


def rz_bottom_fires(get: Get, ind: Mapping[str, Any], is_long: bool, base: str) -> Tuple[bool, str]:
    """RZ_K/MFI_ENTRY_BOTTOM under RZ_ENTRY_ENABLED master (vec v12:8675-81).

    Master effective default True crypto / False stocks (config.py True,
    config_tradier False); twin default True, stocks hook resolves False
    via TradierConfig. Thresholds RZ_K 10.0 / RZ_MFI 15.0 everywhere.
    """
    if not bool(get("RZ_ENTRY_ENABLED", True)):
        return False, ""
    kb = _f(get("RZ_K_ENTRY_BOTTOM", 10.0), 10.0)
    mb = _f(get("RZ_MFI_ENTRY_BOTTOM", 15.0), 15.0)
    k = _k_base(ind or {}, base)
    mfi = _f((ind or {}).get("mfi_1h", 50), 50)
    if is_long:
        fire = (k < kb) and (mfi < mb + 35)
    else:
        fire = (k > (100 - kb)) and (mfi > (100 - mb - 35))
    return fire, f"RZ_BOTTOM_k{k:.1f}_mfi{mfi:.1f}" if fire else ""


def squeeze_fires(ind: Mapping[str, Any], is_long: bool, base: str, prev_sq: Any = None) -> Tuple[bool, str]:
    """SQUEEZE_ENABLED release (vec v12:8656-65). Caller enables.

    sq from squeeze_on_15m (-> squeeze_on live fallback); prev from the
    explicit prev_sq arg, else squeeze_on_15m_prev, else sq (vec bar-0
    edge: roll seeds prev=cur -> no fire; same fail-closed here).
    """
    m = ind or {}
    sq_raw = m.get("squeeze_on_15m", m.get("squeeze_on", 0))
    sq = bool(sq_raw) if not isinstance(sq_raw, (int, float)) else float(sq_raw) > 0
    if prev_sq is None:
        prev_sq = m.get("squeeze_on_15m_prev", None)
    if prev_sq is None:
        prev = sq
    elif isinstance(prev_sq, (int, float)):
        prev = float(prev_sq) > 0
    else:
        prev = bool(prev_sq)
    released = (not sq) and prev
    k = _k_base(m, base)
    fire = released and ((k < 50) if is_long else (k > 50))
    return fire, f"SQUEEZE_RELEASE_k{k:.1f}" if fire else ""


def tradier_mfi_long_blocks(get: Get, ind: Mapping[str, Any], is_long: bool, is_tradier: bool) -> Tuple[bool, str]:
    """TRADIER_MFI_ENTRY_LONG veto (vec v12:9195-97). Tradier-mode + long only."""
    if not bool(get("TRADIER_MFI_ENTRY_LONG_ENABLED", False)):
        return False, ""
    if not (is_tradier and is_long):
        return False, ""
    mfi = _f((ind or {}).get("mfi_1h", 50), 50)
    thr = _f(get("TRADIER_MFI_ENTRY_LONG_TRADIER", 60.0), 60.0)
    if mfi >= thr:
        return True, f"TRADIER_MFI_LONG_BLOCK_mfi{mfi:.1f}_thr{thr:.1f}"
    return False, ""


def tradier_mi_fires(get: Get, ind: Mapping[str, Any], is_long: bool, base: str) -> Tuple[bool, str]:
    """TRADIER_MI_ENTRY_ENABLED_TRADIER votes (vec v12:8800-10)."""
    if not (bool(get("MI_ENTRY_ENABLED_TRADIER", False)) or bool(get("TRADIER_MI_ENTRY_ENABLED_TRADIER", False))):
        return False, ""
    m = ind or {}
    mfi = _f(m.get("mfi_1h", 50), 50)
    mfi_prev = _f(m.get("mfi_1h_prev", mfi), mfi)
    k = _k_base(m, base)
    kp = _f(m.get(f"k_{base}_prev", m.get(f"stoch_k_{base}_prev", k)), k)
    rsi = _f(m.get("rsi_1h", 50), 50)
    need = min(int(_f(get("TRADIER_MI_SUBSIGNAL_MIN_COUNT", 3), 3)), 3)
    if is_long:
        votes = int((mfi_prev < 30) and (mfi > mfi_prev)) + int(k > kp) + int(rsi > 30)
    else:
        votes = int((mfi_prev > 70) and (mfi < mfi_prev)) + int(k < kp) + int(rsi < 70)
    fire = votes >= need
    return fire, f"TRADIER_MI_votes{votes}_need{need}" if fire else ""


def tradier_rsi_short_fires(get: Get, ind: Mapping[str, Any], is_long: bool, is_tradier: bool) -> Tuple[bool, str]:
    """TRADIER_RSI_ENTRY_SHORT_TRADIER short leg (vec v12:8785-92). Long leg is a
    different switch (TRADIER_RSI_ENTRY_LONG_TRADIER, out of scope) -> long neutral."""
    if is_long or not is_tradier:
        return False, ""
    thr = _f(get("TRADIER_RSI_ENTRY_SHORT_TRADIER", 70.0), 70.0)
    if thr > 100:
        return False, ""
    rsi = _f((ind or {}).get("rsi_1h", 50), 50)
    rvol = _f((ind or {}).get("relative_volume_1h", 1.0), 1.0)
    rmin = _f(get("TRADIER_RSI_SHORT_REL_VOLUME_MIN", 2.4), 2.4)
    fire = (rsi > thr) and (rvol >= rmin)
    return fire, f"TRADIER_RSI_SHORT_rsi{rsi:.1f}_rvol{rvol:.2f}" if fire else ""


def regime_adaptive_mult(get: Get, adx_1h: float) -> Optional[float]:
    """REGIME_ADAPTIVE_ENABLED sizing mult (vec compute_regime_sizing_mult v12:10430-39).

    None when disabled so the caller falls through to existing sizing.
    NOTE: vec reads bb_width_pct_1h at v12:10432 but never uses it (dead
    read); the twin does not read it. enter/exit/in_range partition fully
    so the vec 1.0 else-branch is unreachable (mirrored as such).
    """
    if not bool(get("REGIME_ADAPTIVE_ENABLED", False)):
        return None
    adx = _f(adx_1h, 20)
    enter = adx >= _f(get("REGIME_ENTER_TRENDING_THRESHOLD", 30.0), 30.0)
    exit_ = adx < _f(get("REGIME_EXIT_TRENDING_THRESHOLD", 15.0), 15.0)
    if enter:
        return _f(get("REGIME_TRENDING_POSITION_SIZE_MULT", 1.5), 1.5)
    if exit_ or ((not enter) and (not exit_)):
        return _f(get("REGIME_RANGING_POSITION_SIZE_MULT", 0.5), 0.5)
    return 1.0


def mtf_atr_trail_on(get: Get) -> bool:
    """MTF_ATR_TRAIL_ENABLED_TRADIER gate (vec v12:12530-35; live tradier:11257-84).

    Vec-stocks requires compound AND MTF_ATR_TRAIL_ENABLED AND the
    _TRADIER flag; live-stocks currently requires only the first two.
    """
    if not bool(get("MTF_EXIT_USE_COMPOUND", False)):
        return False
    if not bool(get("MTF_ATR_TRAIL_ENABLED", False)):
        return False
    return bool(get("MTF_ATR_TRAIL_ENABLED_TRADIER", False))


def _sba_quality_reversal(ind: Mapping[str, Any], is_long: bool) -> Tuple[float, float]:
    """Scalar quality+reversal, verbatim math of vec_decisions/sba_bounce.py
    _market_quality_score_parts (which mirrors ez_positions_quick)."""
    m = ind or {}

    def sf(k: str, d: float) -> float:
        return _f(m.get(k, d), d)

    quality = 0.0
    reversal = 0.0
    bb_w_1h = sf("bb_width_1h", 10.0)
    bb_w_4h = sf("bb_width_4h", 10.0)
    if bb_w_1h < 6.5 and bb_w_4h < 10.0:
        quality += 1.5
    elif bb_w_1h < 9.0 and bb_w_4h < 13.0:
        quality += 0.5
    dc_w_1h = _f(m.get("dc_width_1h", m.get("dc_width", 8.0)), 8.0)
    dc_w_4h = sf("dc_width_4h", 12.0)
    if dc_w_1h < 7.0 and dc_w_4h < 12.0:
        quality += 0.5
    adx_1h = sf("adx_1h", 30)
    adx_4h = sf("adx_4h", 30)
    if adx_1h < 20 and adx_4h < 25:
        quality += 1.0
    elif adx_1h < 25:
        quality += 0.5
    sma200_1h = sf("sma_200_1h", 0)
    price = _f(m.get("current_price", m.get("close_1h", 0)), 0)
    pct_sma200 = ((price - sma200_1h) / sma200_1h * 100) if sma200_1h > 0 and price > 0 else -99
    if -3.0 < pct_sma200 < 3.0:
        quality += 1.0
    elif -5.0 < pct_sma200 < 5.0:
        quality += 0.5
    rvol_15m = sf("relative_volume_15m", 1.5)
    rvol_1h = sf("relative_volume_1h", 1.5)
    if rvol_1h < 0.95 and rvol_15m < 1.2:
        quality += 1.0
    elif rvol_1h < 1.2:
        quality += 0.5
    mfi_15m = sf("mfi_15m", 50)
    mfi_1h = sf("mfi_1h", 50)
    mfi_4h = sf("mfi_4h", 50)
    rsi_15m = sf("rsi_15m", 50)
    rsi_1h = sf("rsi_1h", 50)
    rsi_4h = sf("rsi_4h", 50)
    if is_long:
        if mfi_1h > 40 and mfi_4h > 40:
            quality += 1.0 if (mfi_1h > 50 and mfi_4h > 45) else 0.5
    else:
        if rsi_1h > 55 and rsi_4h > 50 and rvol_1h >= 1.0:
            quality += 1.0
        elif rsi_1h > 50 and rsi_4h > 45 and rvol_1h >= 1.0:
            quality += 0.5
    k_3m = sf("k_3m", 50)
    k_15m = sf("k_15m", 50)
    k_1h = sf("k_1h", 50)
    k_cross_15m = bool(m.get("stoch_crossover_15m" if is_long else "stoch_crossunder_15m", False))
    k_cross_1h = bool(m.get("stoch_crossover_1h" if is_long else "stoch_crossunder_1h", False))
    if k_cross_15m and (k_15m < 30 if is_long else k_15m > 70):
        reversal += 1.0
    if k_cross_1h and (k_1h < 35 if is_long else k_1h > 65):
        reversal += 0.5
    all_oversold = (is_long and k_3m < 25 and k_15m < 30 and k_1h < 40) or (not is_long and k_3m > 75 and k_15m > 70 and k_1h > 60)
    if all_oversold:
        reversal += 1.0
    wt_buy = "BUY" if is_long else "SELL"
    wt_15m = m.get("wt_signal_15m") == wt_buy
    wt_1h = m.get("wt_signal_1h") == wt_buy
    if wt_15m and wt_1h:
        reversal += 1.5
    elif wt_15m or wt_1h:
        reversal += 0.5
    want = "green" if is_long else "red"
    ha_flip_3m = m.get("ha_3m") == want and m.get("ha_3m_prev") != want
    ha_flip_15m = m.get("ha_15m") == want and m.get("ha_15m_prev") != want
    if ha_flip_3m and ha_flip_15m:
        reversal += 1.5
    elif ha_flip_3m or ha_flip_15m:
        reversal += 0.5
    div_tag = "BULL" if is_long else "BEAR"
    div_1h = m.get("wt_divergence_1h") == div_tag
    div_4h = m.get("wt_divergence_4h") == div_tag
    if div_4h:
        reversal += 1.5
    elif div_1h:
        reversal += 1.0
    exhaust_tag = "EXHAUST_DOWN" if is_long else "EXHAUST_UP"
    if m.get("wt_momentum_state_1h") == exhaust_tag:
        reversal += 0.5
    if m.get("wt_momentum_state_4h") == exhaust_tag:
        reversal += 0.5
    pat = m.get("bar_pattern_15m", "")
    if pat in (("hammer", "bullish_engulfing", "tweezer_bottom") if is_long else ("shooting_star", "bearish_engulfing", "tweezer_top")):
        reversal += 0.5
    if is_long:
        if mfi_15m < 30 and mfi_1h < 35:
            reversal += 1.0
        elif mfi_15m > mfi_1h and mfi_1h < 40:
            reversal += 0.5
    else:
        if rsi_15m > 70 and rsi_1h > 65 and rvol_15m >= 1.0:
            reversal += 1.0
        elif rsi_15m < rsi_1h and rsi_1h > 60:
            reversal += 0.5
    rsi2 = sf("rsi_2_1h", 50)
    if (rsi2 < 15 if is_long else rsi2 > 85):
        reversal += 0.5
    return quality, reversal


def sba_bounce_score(get: Get, ind: Mapping[str, Any], is_long: bool) -> Tuple[float, str]:
    """SBA bounce score 0-14 (vec v12:9599-9600 via sba_bounce_passes_vec;
    live ez_positions_quick._sba_bounce_score dealbreaker lattice)."""
    m = ind or {}
    adx_max = _f(get("SBA_ADX_MAX", 25.0), 25.0)
    if _f(m.get("adx_1h", 50), 50) > adx_max:
        return 0.0, "SBA_DEAL_ADX"
    if _f(m.get("bb_width_4h", 10.0), 10.0) > 16.0:
        return 0.0, "SBA_DEAL_BB4H"
    if _f(m.get("bb_width_1h", 10.0), 10.0) > 14.0:
        return 0.0, "SBA_DEAL_BB1H"
    if _f(m.get("dc_width_4h", 12.0), 12.0) > 18.0:
        return 0.0, "SBA_DEAL_DC4H"
    k15 = _f(m.get("k_15m", 50), 50)
    if is_long and k15 > 75:
        return 0.0, "SBA_DEAL_OVERBOUGHT"
    if (not is_long) and k15 < 25:
        return 0.0, "SBA_DEAL_OVERSOLD"
    ha1, ha4 = m.get("ha_1h"), m.get("ha_4h")
    if is_long and ha1 == "red" and ha4 == "red":
        return 0.0, "SBA_DEAL_HTF_DOWN"
    if (not is_long) and ha1 == "green" and ha4 == "green":
        return 0.0, "SBA_DEAL_HTF_UP"
    mfi_4h = _f(m.get("mfi_4h", 50), 50)
    mfi_1h = _f(m.get("mfi_1h", 50), 50)
    if is_long and mfi_4h < 25 and mfi_1h < 30:
        return 0.0, "SBA_DEAL_MFI_DRY"
    if (not is_long) and mfi_4h > 75 and mfi_1h > 70:
        return 0.0, "SBA_DEAL_MFI_FLOOD"
    q, r = _sba_quality_reversal(m, is_long)
    return q + r, f"SBA_SCORE_{q + r:.1f}"


def sba_veto_blocks(get: Get, ind: Mapping[str, Any], is_long: bool) -> Tuple[bool, str]:
    """SBA_BOUNCE veto (vec ANDs _sba_bounce_vec onto _base_entry)."""
    if not bool(get("SBA_BOUNCE_ENABLED", True)):
        return False, ""
    thr = _f(get("SBA_MIN_SCORE", 3.5), 3.5)
    score, why = sba_bounce_score(get, ind, is_long)
    if score < thr:
        return True, f"SBA_BOUNCE_BLOCK_{why}_thr{thr:.1f}"
    return False, ""


def kg_entry_allowed(get: Get, ind: Mapping[str, Any], is_long: bool) -> Optional[bool]:
    """KINDERGARTEN_CUMULATIVE_MODE evaluator (live tradier should_enter_long
    27760-27832 / should_enter_short 28126-28182; vec live_kindergarten_stocks).

    None = gate N/A (neither EMA_9_21_FILTER nor KINDERGARTEN_EMA_GATE on).
    NOTE: bool in code (QuickConfig/config_tradier/live all bool). Stocks
    side already wired live+vec; crypto has no vec/live cum consumer.
    """
    if not (bool(get("EMA_9_21_FILTER_ENABLED", False)) or bool(get("KINDERGARTEN_EMA_GATE_ENABLED", False))):
        return None
    m = ind or {}
    cum = bool(get("KINDERGARTEN_CUMULATIVE_MODE", True))
    tf1 = str(get("EMA_9_21_TIMEFRAME", "1h") or "1h")
    if not cum:
        raw = m.get(f"ema_9_above_21_{tf1}", -1)
        v = _f(raw if raw is not None else -1, -1)
        return (v != 0.0) if is_long else (v != 1.0)
    checks: list = []
    for tf in _tfs(get("EMA_9_21_FILTER_TFS", tf1)) or [tf1]:
        raw = m.get(f"ema_9_above_21_{tf}", None)
        if raw is not None:
            checks.append(("EMA9_21_" + tf, (float(raw) != 0.0) if is_long else (float(raw) != 1.0)))
    if bool(get("EMA_50_200_FILTER_ENABLED", False)):
        for tf in _tfs(get("EMA_50_200_TFS", get("EMA_50_200_TIMEFRAME", "D"))) or ["D"]:
            raw = m.get(f"ema_50_above_200_{tf}", m.get(f"ema_50_above_200_{tf.lower()}", None))
            if raw is not None:
                checks.append(("EMA50_200_" + tf, (float(raw) != 0.0) if is_long else (float(raw) != 1.0)))
    if bool(get("SMA_50_FILTER_ENABLED", False)):
        tf = str(get("SMA_50_TIMEFRAME", "D") or "D")
        sma = m.get(f"sma_50_{tf}", None)
        px = _f(m.get("current_price", 0), 0)
        if sma is not None and px > 0:
            checks.append(("SMA50_" + tf, (px > float(sma)) if is_long else (px < float(sma))))
    if bool(get("SMA_200_FILTER_ENABLED", False)):
        tf = str(get("SMA_200_TIMEFRAME", "D") or "D")
        sma = m.get(f"sma_200_{tf}", None)
        px = _f(m.get("current_price", 0), 0)
        if sma is not None and px > 0:
            checks.append(("SMA200_" + tf, (px > float(sma)) if is_long else (px < float(sma))))
    if not checks:
        return True
    strict = _tfs(get("KINDERGARTEN_STRICT_TFS", ""))
    if strict:
        if not all(v for k, v in checks if any(t in k for t in strict)):
            return False
    try:
        kc = get("KINDERGARTEN_CUMULATIVE_MIN_TFS", None)
        need = int(float(kc)) if kc is not None and int(float(kc or 0)) > 0 else max(1, int(float(get("EMA_9_21_FILTER_MIN_TFS", 1) or 1)))
    except (TypeError, ValueError):
        need = 1
    passing = sum(1 for _, ok in checks if ok)
    return passing >= min(need, len(checks))
