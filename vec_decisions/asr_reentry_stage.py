"""Causal per-bar port of the AdvancedSignalRater mandatory-reentry stage."""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np


def _col(values, key, n, default, dtype=float):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=dtype)
    array = np.asarray(raw, dtype=dtype)
    if array.ndim == 0:
        array = np.full(n, array.item(), dtype=dtype)
    array = array.reshape(-1)
    if array.size != n:
        raise ValueError(f"{key} length {array.size} does not match n={n}")
    return array


def _truth(value):
    return bool(value)


def apply_asr_reentry_entry_stage(indicators, context, is_long, cfg, score, active=None):
    """Apply native 5675–6052 stateful reentry and first entry-moment stage.

    `context` is one causal simulation stream for a single symbol/side/account
    position key. `bounce_reentry_k_reset` is the state entering this window and
    is returned for persistence into the next window. The lifecycle arrays must
    come from accepted simulation history; this function does not seed a flat
    account or infer tracker state from future rows.
    """
    from ez_reentry import evaluate_obligatory_reentry
    if not isinstance(context, Mapping):
        raise ValueError("ASR reentry context must be a mapping")
    n = np.asarray(score).reshape(-1).size
    result = np.asarray(score, dtype=float).reshape(-1).copy()
    if n < 1 or result.size != n:
        raise ValueError("ASR reentry score must have a nonempty vector")
    required = ("current_price", "last_reduction_price", "minutes_since_reduction", "position_amt",
                "prev_cross_price", "vol_ok", "vol_strong", "rel_vol_3m", "pnl_pct",
                "trend_is_bullish", "trend_is_bearish")
    missing = [key for key in required if key not in context]
    if missing:
        raise ValueError(f"ASR reentry missing lifecycle context: {','.join(missing)}")
    px = _col(context, "current_price", n, 0.0)
    last_red_px = _col(context, "last_reduction_price", n, 0.0)
    mins_red = _col(context, "minutes_since_reduction", n, 0.0)
    pos_amt = _col(context, "position_amt", n, 0.0)
    prev_cross = _col(context, "prev_cross_price", n, 0.0)
    vol_ok = _col(context, "vol_ok", n, False, bool)
    vol_strong = _col(context, "vol_strong", n, False, bool)
    rel_vol = _col(context, "rel_vol_3m", n, 0.0)
    pnl = _col(context, "pnl_pct", n, 0.0)
    trend_long = _col(context, "trend_is_bullish", n, False, bool)
    trend_short = _col(context, "trend_is_bearish", n, False, bool)
    active_mask = np.ones(n, dtype=bool) if active is None else _col({"active": active}, "active", n, False, bool)
    k1 = _col(indicators, "k_1m", n, 50.0)
    k1p = _col(indicators, "k_1m_prev", n, 50.0)
    d1 = _col(indicators, "d_1m", n, 50.0)
    k3 = _col(indicators, "k_3m", n, 50.0)
    k3p = _col(indicators, "k_3m_prev", n, 50.0)
    d3 = _col(indicators, "d_3m", n, 50.0)
    k15 = _col(indicators, "k_15m", n, 50.0)
    k15p = _col(indicators, "k_15m_prev", n, 50.0)
    d15 = _col(indicators, "d_15m", n, 50.0)
    lag = _col(context, "true_lag", n, 999.0)
    k1co = (k1 > d1) & (lag < 10.0) & (k1p <= d1) & (k1 < 30.0)
    k1cu = (k1 < d1) & (lag < 10.0) & (k1p >= d1) & (k1 > 70.0)
    k3co = (k3 > d3) & (k3p <= d3) & (k3 < 30.0)
    k3cu = (k3 < d3) & (k3p >= d3) & (k3 > 70.0)
    k15co = (k15 > d15) & (k15p <= d15) & (k15 < 30.0)
    k15cu = (k15 < d15) & (k15p >= d15) & (k15 > 70.0)
    dc_high3 = _col(indicators, "dc_high_3m", n, 0.0)
    dc_low3 = _col(indicators, "dc_low_3m", n, 0.0)
    dc_high15 = _col(indicators, "dc_high_15m", n, 0.0)
    dc_low15 = _col(indicators, "dc_low_15m", n, 0.0)
    sma15 = _col(indicators, "sma_200_15m", n, 0.0)
    ema15 = _col(indicators, "ema_50_15m", n, 0.0)
    initial_reset = bool(context.get("bounce_reentry_k_reset", False))
    reset = initial_reset
    enabled = bool(getattr(cfg, "BOUNCE_REENTRY_ENABLED", True))
    reset_long = float(getattr(cfg, "BOUNCE_REENTRY_K_RESET_LONG", 35))
    reset_short = float(getattr(cfg, "BOUNCE_REENTRY_K_RESET_SHORT", 65))
    min_t2 = float(getattr(cfg, "REENTRY_TIER2_MIN_MINUTES", 10.0))
    price_t2 = float(getattr(cfg, "REENTRY_TIER2_PRICE_PCT", 0.003))
    tier1 = np.zeros(n, dtype=bool)
    tier2 = np.zeros(n, dtype=bool)
    forced = np.zeros(n, dtype=bool)
    force_reentry = np.zeros(n, dtype=bool)
    bounce_wait = np.zeros(n, dtype=bool)
    good_entry = np.zeros(n, dtype=bool)
    for i in range(n):
        if not active_mask[i]:
            continue
        row = {}
        for key, raw in indicators.items():
            try:
                arr = np.asarray(raw)
                if arr.ndim and arr.size == n:
                    value = arr.reshape(-1)[i]
                    row[key] = value.item() if hasattr(value, "item") else value
                else:
                    row[key] = raw
            except Exception:
                row[key] = raw
        if enabled and not (mins_red[i] < 120.0 and mins_red[i] > 0.0) and mins_red[i] >= 120.0:
            reset = False
        if enabled and mins_red[i] < 120.0 and mins_red[i] > 0.0:
            reset_now = (is_long and k3[i] < reset_long) or ((not is_long) and k3[i] > reset_short)
            reset = bool(reset or reset_now)
            if reset:
                tier1[i] = True
                result[i] += 15.0
            elif last_red_px[i] > 0.0 and ((is_long and px[i] >= last_red_px[i]) or ((not is_long) and px[i] <= last_red_px[i])):
                aligned = {tf: ((is_long and _truth(row.get(f"wt_bullish_{tf}", False))) or ((not is_long) and (not _truth(row.get(f"wt_bullish_{tf}", False))))) for tf in ("3m", "15m", "1h", "4h", "D")}
                wt3, wt15 = aligned["3m"], aligned["15m"]
                htf = sum(int(aligned[tf]) for tf in ("1h", "4h", "D"))
                continuation = mins_red[i] < 5.0 and wt3 and htf >= 1
                min_signal = wt3 and wt15 and htf >= 1
                req_k = bool(getattr(cfg, "MANDATORY_REENTRY_REQUIRE_K_NOT_EXTREME", True))
                hi = float(getattr(cfg, "MANDATORY_REENTRY_K_HIGH_BLOCK", 80.0))
                lo = float(getattr(cfg, "MANDATORY_REENTRY_K_LOW_BLOCK", 20.0))
                k_block = ((is_long and k3[i] >= hi) or ((not is_long) and k3[i] <= lo)) if req_k else False
                if k_block and bool(getattr(cfg, "PARABOLIC_PROTECTION_ENABLED", True)):
                    r4 = float(row.get("rsi_4h", 50) or 50)
                    r1 = float(row.get("rsi_1h", 50) or 50)
                    bb = float(row.get("bb_pct_b_4h", 0.5) or 0.5)
                    up = r4 >= float(getattr(cfg, "PARABOLIC_RSI_4H_MIN", 70.0)) and r1 >= float(getattr(cfg, "PARABOLIC_RSI_1H_MIN", 65.0)) and bb >= float(getattr(cfg, "PARABOLIC_BB_PCT_B_4H_MIN", 0.90))
                    down = r4 <= float(getattr(cfg, "PARABOLIC_RSI_4H_MAX", 30.0)) and r1 <= float(getattr(cfg, "PARABOLIC_RSI_1H_MAX", 35.0)) and bb <= float(getattr(cfg, "PARABOLIC_BB_PCT_B_4H_MAX", 0.10))
                    if (is_long and up) or ((not is_long) and down):
                        k_block = False
                obl_ok, _, _, obl_score = evaluate_obligatory_reentry(row, float(px[i] or 0.0), float(last_red_px[i] or 0.0), float(prev_cross[i] or 0.0), bool(is_long), cfg)
                if obl_ok:
                    tier1[i] = True
                    result[i] += obl_score
                elif k_block:
                    pass
                elif min_signal or continuation:
                    tier1[i] = True
                    result[i] += 30.0
                elif (bool(getattr(cfg, "MANDATORY_REENTRY_ALLOW_WT0_STRONG_CROSS", False))
                      and abs(px[i] / last_red_px[i] - 1.0) > 0.003 and wt3 and htf >= 1):
                    tier1[i] = True
                    result[i] += 20.0
            elif not reset and last_red_px[i] > 0.0 and mins_red[i] >= min_t2:
                trend = (is_long and px[i] > last_red_px[i] * (1.0 + price_t2)) or ((not is_long) and px[i] < last_red_px[i] * (1.0 - price_t2))
                momentum = (is_long and k3[i] > k3p[i] and k1[i] > d1[i]) or ((not is_long) and k3[i] < k3p[i] and k1[i] < d1[i])
                not_exhausted = not ((is_long and k3[i] > 95.0) or ((not is_long) and k3[i] < 5.0))
                if trend and momentum and not_exhausted:
                    tier2[i] = True
                    result[i] += 12.0
                else:
                    crossed = (is_long and px[i] >= last_red_px[i]) or ((not is_long) and px[i] <= last_red_px[i] and pos_amt[i] == 0.0)
                    if crossed:
                        count = sum(int(((is_long and _truth(row.get(f"wt_bullish_{tf}", False))) or ((not is_long) and (not _truth(row.get(f"wt_bullish_{tf}", False)))))) for tf in ("3m", "15m", "1h", "4h"))
                        if count >= 3:
                            tier1[i] = True
                            result[i] += 20.0
                        elif count >= 2 and abs(px[i] / last_red_px[i] - 1.0) > 0.003:
                            tier2[i] = True
                            result[i] += 15.0
                    bounce_wait[i] = not (tier1[i] or tier2[i])
        if bounce_wait[i]:
            continue
        if pos_amt[i] == 0.0:
            lt_ok, _, _, lt_score = evaluate_obligatory_reentry(row, float(px[i] or 0.0), float(last_red_px[i] or 0.0), float(prev_cross[i] or 0.0), bool(is_long), cfg)
            if lt_ok:
                tier1[i] = True
                result[i] += lt_score
        if last_red_px[i] > 0.0 and mins_red[i] < 1200.0:
            if is_long and px[i] > last_red_px[i] * 0.998:
                force_reentry[i] = k1[i] > d1[i] or k3[i] > k3p[i]
            elif not is_long and px[i] < last_red_px[i] * 1.002:
                force_reentry[i] = k1[i] < d1[i] or k3[i] < k3p[i]
        if tier1[i] or tier2[i]:
            good_entry[i] = True
        elif is_long:
            k1co_i = bool(k1co[i])
            k3co_i = bool(k3co[i])
            good_entry[i] = (k3[i] < 25.0 or ((((k1[i] < 15.0 and k1[i] > k1p[i]) or k1co_i) and lag[i] < 10.0 and vol_ok[i]) or (k3co_i and vol_ok[i]) or (force_reentry[i] and k3[i] < 90.0) or (k15[i] < 35.0 and k3[i] < 35.0)))
            if bool(getattr(cfg, "BEAR_MARKET_MODE", False)) and k15[i] >= 15.0 and not force_reentry[i]:
                good_entry[i] = False
        else:
            k1cu_i = bool(k1cu[i])
            k3cu_i = bool(k3cu[i])
            good_entry[i] = (k3[i] > 75.0 or ((((k1[i] > 85.0 and k1[i] < k1p[i]) or k1cu_i) and lag[i] < 10.0 and vol_ok[i]) or (k3cu_i and vol_ok[i]) or (force_reentry[i] and k3[i] > 10.0) or (k15[i] > 70.0 and k15[i] < k15p[i] and k3[i] > 60.0)))
        if not good_entry[i]:
            continue
        if force_reentry[i]:
            result[i] += 10.0
        if bool(getattr(cfg, "BEAR_MARKET_MODE", False)):
            result[i] += -15.0 if is_long else 10.0
        if vol_strong[i]:
            result[i] += 8.0
        elif not vol_ok[i]:
            result[i] -= 5.0
        if is_long and not trend_long[i] or (not is_long and not trend_short[i]):
            result[i] *= 0.3
        if is_long:
            k1co_i = bool(k1co[i])
            k3co_i = bool(k3co[i])
            k15co_i = bool(k15co[i])
            if (k1co_i and k3[i] < 80.0 and lag[i] < 10.0) or (k3co_i and k15[i] > 80.0):
                result[i] += 5.0
                if (k15[i] > 50.0 and k3[i] > d3[i]) or (k15[i] < 30.0 and k3[i] > d3[i]): result[i] += 3.0
            if k15[i] > 50.0 and k1[i] < 40.0 and k3[i] > d3[i]: result[i] += 5.0
            if k15co_i: result[i] += 4.0
            dcw = ((dc_high15[i] - dc_low15[i]) / dc_low15[i] * 100.0) if dc_low15[i] > 0 else 0.0
            near = abs(px[i] - sma15[i]) / sma15[i] < 0.015 if sma15[i] > 0 else False
            if dcw > 5.0 and near and k15[i] < 30.0 and k3co_i: result[i] += 25.0
            elif px[i] > dc_high15[i] and _col(indicators, "k_1h", n, 50.0)[i] > _col(indicators, "d_1h", n, 50.0)[i] and k15[i] > k15p[i]: result[i] += 10.0
            elif k15[i] > 90.0 and k15[i] < k15p[i]: result[i] -= 5.0
        else:
            k1cu_i = bool(k1cu[i])
            k3cu_i = bool(k3cu[i])
            k15cu_i = bool(k15cu[i])
            if (k1cu_i and k3[i] > 20.0 and lag[i] < 10.0) or (k3cu_i and k15[i] > 20.0):
                result[i] += 5.0
                if (k15[i] < 50.0 and k3[i] < d3[i]) or (k15[i] > 70.0 and k3[i] < d3[i]): result[i] += 3.0
            if k15cu_i: result[i] += 4.0
            dcw = ((dc_high15[i] - dc_low15[i]) / dc_low15[i] * 100.0) if dc_low15[i] > 0 else 0.0
            near = abs(px[i] - sma15[i]) / sma15[i] < 0.015 if sma15[i] > 0 else False
            if dcw > 5.0 and near and k15[i] > 70.0 and k3cu_i: result[i] += 25.0
            elif px[i] < dc_low15[i] and _col(indicators, "k_1h", n, 50.0)[i] < _col(indicators, "d_1h", n, 50.0)[i] and k15[i] < k15p[i]: result[i] += 10.0
            if k15[i] < 10.0 or px[i] < dc_low15[i]: result[i] -= 5.0
    return {"score": result, "tier1_forced": tier1, "tier2_chase": tier2,
            "force_reentry": force_reentry, "bounce_wait": bounce_wait,
            "good_entry": good_entry, "entry_allowed": active_mask & ~bounce_wait & good_entry,
            "bounce_reentry_k_reset": reset}
