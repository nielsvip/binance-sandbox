#!/usr/bin/env python3
"""Backtest the Limitless price projection model against historical klines.

Simulates: "Will $TICKER be above $STRIKE in X minutes?"
For each candle, computes indicators, projects price, then checks actual outcome.
Measures prediction accuracy at different confidence thresholds.
"""

import json
import math
import redis
from collections import defaultdict
from datetime import datetime, timezone


def load_klines(symbol: str) -> list:
    r = redis.Redis(decode_responses=True)
    raw = r.get(f"klines:{symbol}:3m")
    if not raw:
        return []
    return json.loads(raw).get("klines", [])


def compute_indicators(klines: list, idx: int) -> dict:
    """Compute a subset of the 236 indicators from raw klines at position idx.
    Uses lookback from klines[0:idx+1]. Returns dict matching hot_metrics format."""
    if idx < 60:
        return {}
    closes = [k["close"] for k in klines[max(0, idx - 200):idx + 1]]
    highs = [k["high"] for k in klines[max(0, idx - 200):idx + 1]]
    lows = [k["low"] for k in klines[max(0, idx - 200):idx + 1]]
    volumes = [k["volume"] for k in klines[max(0, idx - 200):idx + 1]]
    price = closes[-1]
    prev_price = closes[-2] if len(closes) > 1 else price
    result = {"current_price": price, "prev_price": prev_price}
    # --- Stochastic RSI for multiple "timeframes" using different periods ---
    for label, period in [("1m", 5), ("3m", 14), ("15m", 70), ("1h", 280)]:
        n = min(period, len(closes))
        if n < 5:
            continue
        window_h = highs[-n:]
        window_l = lows[-n:]
        window_c = closes[-n:]
        hh = max(window_h)
        ll = min(window_l)
        rng = hh - ll if hh != ll else 1
        k = (window_c[-1] - ll) / rng * 100
        k_prev = (window_c[-2] - ll) / rng * 100 if len(window_c) > 1 else k
        d = (k + k_prev + (window_c[-3] - ll) / rng * 100 if len(window_c) > 2 else k) / 3 if len(window_c) > 2 else k
        result[f"stoch_k_{label}"] = k
        result[f"k_{label}_prev"] = k_prev
        result[f"stoch_d_{label}"] = d
    # --- Linear regression slope ---
    for label, period in [("3m", 14), ("15m", 70), ("1h", 280)]:
        n = min(period, len(closes))
        if n < 5:
            continue
        y = closes[-n:]
        x_mean = (n - 1) / 2
        y_mean = sum(y) / n
        num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den else 0
        result[f"lr_trend_{label}"] = slope
    # --- ATR ---
    for label, period in [("3m", 14), ("15m", 70)]:
        n = min(period, len(closes) - 1)
        if n < 2:
            continue
        trs = []
        for i in range(-n, 0):
            h = highs[i]
            l = lows[i]
            pc = closes[i - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        result[f"atr_{label}"] = sum(trs) / len(trs) if trs else price * 0.003
    # --- Donchian channels ---
    for label, period in [("3m", 14), ("15m", 70)]:
        n = min(period, len(highs))
        result[f"dc_high_{label}"] = max(highs[-n:])
        result[f"dc_low_{label}"] = min(lows[-n:])
    # --- Heikin-Ashi ---
    if len(closes) >= 4:
        o, h, l, c = klines[idx]["open"], klines[idx]["high"], klines[idx]["low"], klines[idx]["close"]
        po, ph, pl, pc2 = klines[idx-1]["open"], klines[idx-1]["high"], klines[idx-1]["low"], klines[idx-1]["close"]
        ha_c = (o + h + l + c) / 4
        ha_o = (po + pc2) / 2
        result["ha_3m"] = "green" if ha_c > ha_o else "red"
        ppo = klines[idx-2]["open"] if idx > 1 else po
        ppc = klines[idx-2]["close"] if idx > 1 else pc2
        prev_ha_c = (po + ph + pl + pc2) / 4
        prev_ha_o = (ppo + ppc) / 2
        result["ha_3m_prev"] = "green" if prev_ha_c > prev_ha_o else "red"
        result["ha_15m"] = result["ha_3m"]
        result["ha_1h"] = result["ha_3m"]
    # --- WaveTrend (simplified) ---
    for label, period in [("3m", 14), ("15m", 70)]:
        n = min(period, len(closes))
        if n < 5:
            continue
        ema = sum(closes[-n:]) / n
        diff = price - ema
        result[f"wt_score_{label}"] = diff / (price * 0.001) if price else 0
    # --- Velocity ---
    if len(closes) >= 3:
        result["velocity"] = (closes[-1] - closes[-2]) - (closes[-2] - closes[-3])
    return result


def project_price_bt(indicators: dict, minutes_ahead: float) -> tuple:
    """Same projection logic as the live trader."""
    price = indicators.get("current_price", 0)
    if not price:
        return 0, 0
    slope_per_min = 0
    w_total = 0
    for tf, candle_min, w in [("3m", 3, 0.40), ("15m", 15, 0.30), ("1h", 60, 0.20)]:
        s = indicators.get(f"lr_trend_{tf}", 0)
        if s:
            slope_per_min += (s / candle_min) * w
            w_total += w
    if w_total > 0:
        slope_per_min /= w_total
    lr_proj = price + slope_per_min * minutes_ahead
    stoch_adj = 0
    for tf, w in [("1m", 0.35), ("3m", 0.30), ("15m", 0.20), ("1h", 0.15)]:
        k = indicators.get(f"stoch_k_{tf}", 50)
        k_prev = indicators.get(f"k_{tf}_prev", k)
        delta_k = k - k_prev
        if k > 85 and delta_k < 0:
            stoch_adj -= w * 0.3
        elif k > 85 and delta_k > 0:
            stoch_adj += w * 0.1
        elif k < 15 and delta_k > 0:
            stoch_adj += w * 0.3
        elif k < 15 and delta_k < 0:
            stoch_adj -= w * 0.1
        else:
            stoch_adj += w * (delta_k / 30)
    atr = indicators.get("atr_3m", price * 0.003)
    stoch_price_adj = stoch_adj * atr
    tf_dc = "3m" if minutes_ahead <= 10 else "15m"
    dc_h = indicators.get(f"dc_high_{tf_dc}", price * 1.1)
    dc_l = indicators.get(f"dc_low_{tf_dc}", price * 0.9)
    dc_range = dc_h - dc_l if dc_h > dc_l else 1
    dc_pos = (price - dc_l) / dc_range
    dc_cap = -0.15 * atr if dc_pos > 0.9 else (0.15 * atr if dc_pos < 0.1 else 0)
    ha_score = sum(1 if indicators.get(f"ha_{tf}") == "green" else (-1 if indicators.get(f"ha_{tf}") == "red" else 0) for tf in ["3m", "15m", "1h"])
    ha_adj = ha_score * 0.05 * atr
    wt_adj = sum(indicators.get(f"wt_score_{tf}", 0) * 0.005 * atr for tf in ["3m", "15m"])
    vel_adj = indicators.get("velocity", 0) * 0.02 * atr
    projected = lr_proj + stoch_price_adj + dc_cap + ha_adj + wt_adj + vel_adj
    components = [slope_per_min * minutes_ahead, stoch_price_adj, dc_cap, ha_adj]
    bullish = sum(1 for c in components if c > 0)
    bearish = sum(1 for c in components if c < 0)
    confidence = abs(bullish - bearish) / max(len(components), 1)
    return projected, confidence


def indicator_prob_bt(indicators: dict, strike: float, minutes: float) -> float:
    price = indicators.get("current_price", 0)
    if not price:
        return 0.5
    projected, confidence = project_price_bt(indicators, minutes)
    if projected <= 0:
        return 0.5
    proj_distance = projected - strike
    atr = indicators.get("atr_3m", price * 0.003) if minutes <= 10 else indicators.get("atr_15m", price * 0.003)
    if not atr:
        atr = price * 0.003
    time_scale = math.sqrt(max(1, minutes / 3))
    uncertainty = atr * time_scale * 0.5
    if uncertainty < 0.001:
        return 1.0 if proj_distance > 0 else 0.0
    z = proj_distance / uncertainty
    steepness = 1.5 + confidence * 1.5
    prob = 1.0 / (1.0 + math.exp(-steepness * z))
    return max(0.01, min(0.99, prob))


def backtest(symbol: str, minutes_ahead: int = 5, min_edge: float = 0.10):
    klines = load_klines(symbol)
    if not klines:
        print(f"No klines for {symbol}")
        return
    candles_ahead = minutes_ahead // 3
    results = {"total": 0, "correct": 0, "wrong": 0, "skipped": 0, "by_confidence": defaultdict(lambda: {"correct": 0, "wrong": 0})}
    for i in range(60, len(klines) - candles_ahead):
        ind = compute_indicators(klines, i)
        if not ind:
            continue
        price = ind["current_price"]
        actual_future = klines[i + candles_ahead]["close"]
        # Simulate Limitless-style bet: "price above strike?"
        # Test at different strikes relative to current price
        for strike_offset_pct in [-0.3, -0.2, -0.1, 0.1, 0.2, 0.3]:
            strike = price * (1 + strike_offset_pct / 100)
            prob = indicator_prob_bt(ind, strike, minutes_ahead)
            # Would we bet?
            if prob > 0.5 + min_edge:
                bet_yes = True
                actual_yes = actual_future > strike
                correct = (bet_yes == actual_yes)
            elif prob < 0.5 - min_edge:
                bet_yes = False
                actual_yes = actual_future > strike
                correct = (bet_yes == actual_yes)
            else:
                results["skipped"] += 1
                continue
            results["total"] += 1
            if correct:
                results["correct"] += 1
            else:
                results["wrong"] += 1
            # Track by confidence bucket
            conf_bucket = f"{int(abs(prob - 0.5) * 200)}%"
            results["by_confidence"][conf_bucket]["correct" if correct else "wrong"] += 1
    wr = results["correct"] / results["total"] * 100 if results["total"] else 0
    print(f"\n=== BACKTEST {symbol} | {minutes_ahead}min ahead | min_edge={min_edge:.0%} ===")
    print(f"Total bets: {results['total']} | Correct: {results['correct']} | Wrong: {results['wrong']} | Skipped: {results['skipped']}")
    print(f"WIN RATE: {wr:.1f}%")
    print(f"\nBy confidence:")
    for conf in sorted(results["by_confidence"].keys(), key=lambda x: int(x.replace("%", ""))):
        d = results["by_confidence"][conf]
        total = d["correct"] + d["wrong"]
        acc = d["correct"] / total * 100 if total else 0
        print(f"  edge={conf:>4s}: {d['correct']:>4d}W / {d['wrong']:>4d}L = {acc:.1f}% ({total} bets)")
    return wr


if __name__ == "__main__":
    for sym in ["BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC", "DOGEUSDC"]:
        for mins in [5, 10, 15]:
            backtest(sym, minutes_ahead=mins, min_edge=0.10)
