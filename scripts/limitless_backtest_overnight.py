#!/usr/bin/env python3
"""Overnight backtest — fetch extended klines from Binance, compute indicators, test projections.

Fetches 7 days of 3m klines per symbol, computes indicators at each candle,
projects price 5/10/15 minutes ahead, compares to actual. Logs results for morning review.

Run: python scripts/limitless_backtest_overnight.py
Output: data/poly/limitless_trader/backtest_results.json
"""

import asyncio
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp

SYMBOLS = {
    "BTCUSDC": "BTC", "ETHUSDC": "ETH", "SOLUSDC": "SOL",
    "XRPUSDC": "XRP", "DOGEUSDT": "DOGE", "BNBUSDC": "BNB",
    "ADAUSDC": "ADA", "AVAXUSDC": "AVAX", "LINKUSDC": "LINK",
    "SUIUSDT": "SUI",
}
HORIZONS = [5, 10, 15]  # minutes ahead
STRIKE_OFFSETS = [-0.5, -0.3, -0.2, -0.1, 0.1, 0.2, 0.3, 0.5]  # % from current
MIN_EDGE = 0.10  # probability edge to trigger bet
OUTPUT = Path("./data/poly/limitless_trader/backtest_overnight.json")
OUTPUT.parent.mkdir(parents=True, exist_ok=True)


async def fetch_klines(session, symbol, interval="3m", days=7):
    """Fetch multiple days of klines by paging."""
    all_klines = []
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 86400 * 1000
    while start_time < end_time:
        params = {"symbol": symbol, "interval": interval, "startTime": start_time, "limit": 1000}
        async with session.get("https://api.binance.com/api/v3/klines", params=params) as r:
            data = await r.json()
        if not data:
            break
        for k in data:
            all_klines.append({"timestamp": k[0], "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
        start_time = data[-1][0] + 1
        await asyncio.sleep(0.1)
    return all_klines


def compute_indicators(klines, idx):
    if idx < 80:
        return None
    closes = [k["close"] for k in klines[max(0, idx - 300):idx + 1]]
    highs = [k["high"] for k in klines[max(0, idx - 300):idx + 1]]
    lows = [k["low"] for k in klines[max(0, idx - 300):idx + 1]]
    price = closes[-1]
    if not price:
        return None
    result = {"current_price": price}
    for label, period in [("1m", 5), ("3m", 14), ("15m", 70), ("1h", 280)]:
        n = min(period, len(closes))
        if n < 5:
            continue
        wh, wl, wc = highs[-n:], lows[-n:], closes[-n:]
        hh, ll = max(wh), min(wl)
        rng = hh - ll if hh != ll else 1
        k = (wc[-1] - ll) / rng * 100
        k_prev = (wc[-2] - ll) / rng * 100 if len(wc) > 1 else k
        d = (k + k_prev + ((wc[-3] - ll) / rng * 100 if len(wc) > 2 else k)) / 3
        result[f"stoch_k_{label}"] = k
        result[f"k_{label}_prev"] = k_prev
        result[f"stoch_d_{label}"] = d
    for label, period in [("3m", 14), ("15m", 70), ("1h", 280)]:
        n = min(period, len(closes))
        if n < 5:
            continue
        y = closes[-n:]
        x_mean = (n - 1) / 2
        y_mean = sum(y) / n
        num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        result[f"lr_trend_{label}"] = num / den if den else 0
    for label, period in [("3m", 14), ("15m", 70)]:
        n = min(period, len(closes) - 1)
        if n < 2:
            continue
        trs = []
        for i in range(-n, 0):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        result[f"atr_{label}"] = sum(trs) / len(trs)
    for label, period in [("3m", 14), ("15m", 70)]:
        n = min(period, len(highs))
        result[f"dc_high_{label}"] = max(highs[-n:])
        result[f"dc_low_{label}"] = min(lows[-n:])
    if len(closes) >= 4:
        o, h, l, c = klines[idx]["open"], klines[idx]["high"], klines[idx]["low"], klines[idx]["close"]
        po, pc2 = klines[idx - 1]["open"], klines[idx - 1]["close"]
        result["ha_3m"] = "green" if (o + h + l + c) / 4 > (po + pc2) / 2 else "red"
        result["ha_15m"] = result["ha_3m"]
        result["ha_1h"] = result["ha_3m"]
    for label, period in [("3m", 14), ("15m", 70)]:
        n = min(period, len(closes))
        if n < 5:
            continue
        ema = sum(closes[-n:]) / n
        result[f"wt_score_{label}"] = (price - ema) / (price * 0.001) if price else 0
    if len(closes) >= 3:
        result["velocity"] = (closes[-1] - closes[-2]) - (closes[-2] - closes[-3])
    return result


def project_price(indicators, minutes_ahead):
    price = indicators.get("current_price", 0)
    if not price:
        return 0, 0
    slope_per_min, w_total = 0, 0
    for tf, cm, w in [("3m", 3, 0.40), ("15m", 15, 0.30), ("1h", 60, 0.20)]:
        s = indicators.get(f"lr_trend_{tf}", 0)
        if s:
            slope_per_min += (s / cm) * w
            w_total += w
    if w_total > 0:
        slope_per_min /= w_total
    lr_proj = price + slope_per_min * minutes_ahead
    stoch_adj = 0
    for tf, w in [("1m", 0.35), ("3m", 0.30), ("15m", 0.20), ("1h", 0.15)]:
        k = indicators.get(f"stoch_k_{tf}", 50)
        k_prev = indicators.get(f"k_{tf}_prev", k)
        dk = k - k_prev
        if k > 85 and dk < 0:
            stoch_adj -= w * 0.3
        elif k > 85:
            stoch_adj += w * 0.1
        elif k < 15 and dk > 0:
            stoch_adj += w * 0.3
        elif k < 15:
            stoch_adj -= w * 0.1
        else:
            stoch_adj += w * (dk / 30)
    atr = indicators.get("atr_3m", price * 0.003) if minutes_ahead <= 10 else indicators.get("atr_15m", price * 0.003)
    stoch_adj *= atr
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
    projected = lr_proj + stoch_adj + dc_cap + ha_adj + wt_adj + vel_adj
    components = [slope_per_min * minutes_ahead, stoch_adj, dc_cap, ha_adj]
    bull = sum(1 for c in components if c > 0)
    bear = sum(1 for c in components if c < 0)
    conf = abs(bull - bear) / max(len(components), 1)
    return projected, conf


def get_prob(indicators, strike, minutes):
    price = indicators.get("current_price", 0)
    if not price:
        return 0.5
    proj, conf = project_price(indicators, minutes)
    if proj <= 0:
        return 0.5
    dist = proj - strike
    atr = indicators.get("atr_3m", price * 0.003) if minutes <= 10 else indicators.get("atr_15m", price * 0.003)
    if not atr:
        atr = price * 0.003
    unc = atr * math.sqrt(max(1, minutes / 3)) * 0.5
    if unc < 0.001:
        return 1.0 if dist > 0 else 0.0
    z = dist / unc
    steep = 1.5 + conf * 1.5
    return max(0.01, min(0.99, 1.0 / (1.0 + math.exp(-steep * z))))


async def backtest_symbol(session, binance_sym, ticker):
    print(f"Fetching {binance_sym}...")
    klines = await fetch_klines(session, binance_sym, "3m", days=7)
    print(f"  {len(klines)} candles ({klines[0]['timestamp']} → {klines[-1]['timestamp']})")
    results = {}
    for horizon in HORIZONS:
        candles_ahead = horizon // 3
        stats = {"total": 0, "correct": 0, "wrong": 0, "skipped": 0, "pnl": 0.0, "bets": []}
        for i in range(80, len(klines) - candles_ahead):
            ind = compute_indicators(klines, i)
            if not ind:
                continue
            price = ind["current_price"]
            future = klines[i + candles_ahead]["close"]
            for offset in STRIKE_OFFSETS:
                strike = price * (1 + offset / 100)
                prob = get_prob(ind, strike, horizon)
                if prob > 0.5 + MIN_EDGE:
                    actual_yes = future > strike
                    correct = actual_yes
                    pnl = (1.0 / prob - 1) * 5 if correct else -5
                elif prob < 0.5 - MIN_EDGE:
                    actual_yes = future > strike
                    correct = not actual_yes
                    pnl = (1.0 / (1 - prob) - 1) * 5 if correct else -5
                else:
                    stats["skipped"] += 1
                    continue
                stats["total"] += 1
                stats["pnl"] += pnl
                if correct:
                    stats["correct"] += 1
                else:
                    stats["wrong"] += 1
        wr = stats["correct"] / stats["total"] * 100 if stats["total"] else 0
        roi = stats["pnl"] / (stats["total"] * 5) * 100 if stats["total"] else 0
        results[f"{horizon}min"] = {"win_rate": round(wr, 1), "total": stats["total"], "correct": stats["correct"], "wrong": stats["wrong"], "skipped": stats["skipped"], "pnl": round(stats["pnl"], 2), "roi_pct": round(roi, 2)}
        print(f"  {horizon:2d}min: {wr:.1f}% WR | {stats['correct']}W/{stats['wrong']}L | pnl=${stats['pnl']:.2f} ROI={roi:.1f}% | {stats['total']} bets")
    return results


async def main():
    print(f"=== OVERNIGHT BACKTEST — {len(SYMBOLS)} symbols × {len(HORIZONS)} horizons ===")
    print(f"Fetching 7 days of 3m klines from Binance...\n")
    all_results = {}
    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        for binance_sym, ticker in SYMBOLS.items():
            results = await backtest_symbol(session, binance_sym, ticker)
            all_results[ticker] = results
            print()
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Ticker':>6s} | {'5min WR':>8s} {'5min ROI':>9s} | {'10min WR':>8s} {'10min ROI':>9s} | {'15min WR':>8s} {'15min ROI':>9s}")
    print("-" * 70)
    for ticker, results in all_results.items():
        r5 = results.get("5min", {})
        r10 = results.get("10min", {})
        r15 = results.get("15min", {})
        print(f"{ticker:>6s} | {r5.get('win_rate',0):>7.1f}% {r5.get('roi_pct',0):>+8.1f}% | {r10.get('win_rate',0):>7.1f}% {r10.get('roi_pct',0):>+8.1f}% | {r15.get('win_rate',0):>7.1f}% {r15.get('roi_pct',0):>+8.1f}%")
    with open(OUTPUT, "w") as f:
        json.dump({"run_at": datetime.now(timezone.utc).isoformat(), "symbols": list(SYMBOLS.values()), "horizons": HORIZONS, "results": all_results}, f, indent=2)
    print(f"\nResults saved to {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
