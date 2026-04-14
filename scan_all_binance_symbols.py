#!/usr/bin/env python3
"""
Comprehensive Binance Futures Symbol Scanner & Ranker

1. Fetch ALL active Binance USDT-M futures symbols
2. Filter: liquid (>$5M daily volume), listed >6 months
3. Download 15m klines (last 30 days) for ranking indicators
4. Run ALL ranking systems (WT composite, stoch multi-TF, band, sentiment, etc.)
5. Cross-reference manipulation_flags.json — exclude suspects
6. Compare to current symbols.json → suggest additions & removals
"""
import json
import time
import sys
import os
import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import requests
import numpy as np
import pandas as pd

BASE_PATH = Path("/Users/niels/Documents/binance")
SYMBOLS_JSON = BASE_PATH / "symbols.json"
MANIP_FLAGS = BASE_PATH / "data" / "manipulation_flags.json"
SIGNAL_TRACKER = BASE_PATH / "data" / "signal_tracker" / "signals.jsonl"
OUTPUT_FILE = BASE_PATH / "data" / "symbol_scan_results.json"
KLINES_CACHE_DIR = BASE_PATH / "klines_cache"


def fetch_exchange_info():
    """Fetch all USDT-M perpetual futures from Binance."""
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    info = resp.json()
    symbols = []
    now = time.time() * 1000
    six_months_ms = 180 * 24 * 3600 * 1000
    for s in info["symbols"]:
        if s["contractType"] != "PERPETUAL":
            continue
        if s["status"] != "TRADING":
            continue
        if not s["symbol"].endswith("USDT"):
            continue
        # Check listing time
        listing_ts = s.get("onboardDate", 0)
        if listing_ts > 0 and (now - listing_ts) < six_months_ms:
            continue  # Too new
        symbols.append({
            "symbol": s["symbol"],
            "listing_ts": listing_ts,
            "age_days": int((now - listing_ts) / (24 * 3600 * 1000)) if listing_ts > 0 else 9999,
            "price_precision": s.get("pricePrecision", 8),
            "qty_precision": s.get("quantityPrecision", 8),
        })
    return symbols


def fetch_24h_tickers():
    """Fetch 24h volume for all futures symbols."""
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    tickers = {}
    for t in resp.json():
        tickers[t["symbol"]] = {
            "volume_usd": float(t.get("quoteVolume", 0)),
            "price": float(t.get("lastPrice", 0)),
            "price_change_pct": float(t.get("priceChangePercent", 0)),
            "high": float(t.get("highPrice", 0)),
            "low": float(t.get("lowPrice", 0)),
            "trades": int(t.get("count", 0)),
        }
    return tickers


def fetch_klines(symbol, interval="15m", limit=500):
    """Fetch klines from Binance."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code == 400:
        return None  # Invalid symbol
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_vol", "taker_buy_quote", "ignore"])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def compute_stoch(close, high, low, k_period=14, d_period=3):
    """Stochastic K and D."""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    denom = highest_high - lowest_low
    k = np.where(denom > 0, (close - lowest_low) / denom * 100, 50)
    k = pd.Series(k, index=close.index)
    d = k.rolling(d_period).mean()
    return k.iloc[-1], d.iloc[-1]


def compute_rsi(close, period=14):
    """RSI."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def compute_wt(close, high, low, n1=10, n2=21):
    """WaveTrend oscillator."""
    hlc3 = (high + low + close) / 3
    esa = hlc3.ewm(span=n1, adjust=False).mean()
    d = (hlc3 - esa).abs().ewm(span=n1, adjust=False).mean()
    ci = (hlc3 - esa) / (0.015 * d)
    ci = ci.replace([np.inf, -np.inf], 0).fillna(0)
    wt1 = ci.ewm(span=n2, adjust=False).mean()
    wt2 = wt1.rolling(4).mean()
    return wt1.iloc[-1], wt2.iloc[-1]


def compute_bb(close, period=20, std_mult=2.0):
    """Bollinger Bands."""
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper.iloc[-1], lower.iloc[-1], sma.iloc[-1]


def compute_sma200(close):
    """SMA 200."""
    if len(close) < 200:
        return 0
    return close.rolling(200).mean().iloc[-1]


def rank_symbol(df_15m, df_1h=None, df_4h=None):
    """Compute all ranking scores for a symbol. Returns dict of scores."""
    if df_15m is None or len(df_15m) < 50:
        return None
    close = df_15m["close"]
    high = df_15m["high"]
    low = df_15m["low"]
    price = close.iloc[-1]
    # Build 1h and 4h by resampling if not provided
    if df_1h is None and len(df_15m) >= 8:
        df_1h = df_15m.set_index("timestamp").resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    if df_4h is None and len(df_15m) >= 32:
        df_4h = df_15m.set_index("timestamp").resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    scores = {"price": price}
    # ── Stoch multi-TF ──
    k15, d15 = compute_stoch(close, high, low)
    scores["stoch_k_15m"] = k15
    scores["stoch_d_15m"] = d15
    if df_1h is not None and len(df_1h) >= 20:
        k1h, d1h = compute_stoch(df_1h["close"], df_1h["high"], df_1h["low"])
        scores["stoch_k_1h"] = k1h
        scores["stoch_d_1h"] = d1h
    else:
        scores["stoch_k_1h"] = 50; scores["stoch_d_1h"] = 50
    if df_4h is not None and len(df_4h) >= 20:
        k4h, d4h = compute_stoch(df_4h["close"], df_4h["high"], df_4h["low"])
        scores["stoch_k_4h"] = k4h
        scores["stoch_d_4h"] = d4h
    else:
        scores["stoch_k_4h"] = 50; scores["stoch_d_4h"] = 50
    # ── RSI ──
    scores["rsi_15m"] = compute_rsi(close)
    if df_1h is not None and len(df_1h) >= 20:
        scores["rsi_1h"] = compute_rsi(df_1h["close"])
    # ── WaveTrend ──
    wt1_15m, wt2_15m = compute_wt(close, high, low)
    scores["wt1_15m"] = wt1_15m; scores["wt2_15m"] = wt2_15m
    if df_1h is not None and len(df_1h) >= 30:
        wt1_1h, wt2_1h = compute_wt(df_1h["close"], df_1h["high"], df_1h["low"])
        scores["wt1_1h"] = wt1_1h; scores["wt2_1h"] = wt2_1h
    else:
        scores["wt1_1h"] = 0; scores["wt2_1h"] = 0
    if df_4h is not None and len(df_4h) >= 30:
        wt1_4h, wt2_4h = compute_wt(df_4h["close"], df_4h["high"], df_4h["low"])
        scores["wt1_4h"] = wt1_4h; scores["wt2_4h"] = wt2_4h
    else:
        scores["wt1_4h"] = 0; scores["wt2_4h"] = 0
    # ── WT Composite (best predictor from backtest) ──
    wt_composite = (wt1_15m - wt2_15m) * 1.0 + (scores["wt1_1h"] - scores["wt2_1h"]) * 2.0 + (scores["wt1_4h"] - scores["wt2_4h"]) * 3.0
    scores["wt_composite"] = wt_composite
    # ── Stoch Multi-TF composite ──
    stoch_composite = (k15 - d15) * 1.0 + (scores["stoch_k_1h"] - scores["stoch_d_1h"]) * 2.0 + (scores["stoch_k_4h"] - scores["stoch_d_4h"]) * 3.0
    scores["stoch_composite"] = stoch_composite
    # ── BB Band score ──
    bb_up, bb_lo, bb_mid = compute_bb(close)
    span = bb_up - bb_lo if bb_up > bb_lo else 1
    bb_frac = (price - bb_lo) / span
    scores["band_score_15m"] = 100 - bb_frac * 200
    if df_1h is not None and len(df_1h) >= 25:
        bu1h, bl1h, _ = compute_bb(df_1h["close"])
        s1h = bu1h - bl1h if bu1h > bl1h else 1
        scores["band_score_1h"] = 100 - ((price - bl1h) / s1h) * 200
    if df_4h is not None and len(df_4h) >= 25:
        bu4h, bl4h, _ = compute_bb(df_4h["close"])
        s4h = bu4h - bl4h if bu4h > bl4h else 1
        scores["band_score_4h"] = 100 - ((price - bl4h) / s4h) * 200
    # Weighted band score
    scores["band_score"] = scores.get("band_score_4h", 0) * 0.4 + scores.get("band_score_1h", 0) * 0.4 + scores["band_score_15m"] * 0.2
    # ── SMA200 distance ──
    sma200 = compute_sma200(close)
    scores["sma200_15m"] = sma200
    scores["sma200_dist_pct"] = ((price - sma200) / sma200 * 100) if sma200 > 0 else 0
    # ── Trend (LR slope proxy) ──
    if len(close) >= 50:
        x = np.arange(50)
        y = close.iloc[-50:].values
        if np.std(y) > 0:
            slope = np.polyfit(x, y, 1)[0]
            scores["trend_slope_15m"] = slope / price * 100  # normalized pct per bar
        else:
            scores["trend_slope_15m"] = 0
    # ── Relative Volume ──
    vol = df_15m["volume"]
    avg_vol = vol.iloc[-200:].mean() if len(vol) >= 200 else vol.mean()
    scores["relative_volume"] = vol.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    # ── Volatility (ATR proxy) ──
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    scores["atr_pct"] = (atr / price * 100) if price > 0 else 0
    # ── Composite ranking score (for add/remove decision) ──
    # Positive = good long candidate, negative = good short candidate
    # For symbol selection, we want abs(score) high = strong signal either way
    scores["signal_strength"] = abs(wt_composite) + abs(stoch_composite) * 0.3
    return scores


def load_manipulation_flags():
    """Load manipulation flags and build a suspect score per symbol."""
    flags = {}
    # Current active flags
    if MANIP_FLAGS.exists():
        with open(MANIP_FLAGS) as f:
            data = json.load(f)
        for sym, info in data.items():
            clean_sym = sym.replace("USDC", "USDT") if sym.endswith("USDC") else sym
            severity = info.get("severity", 0)
            reasons = info.get("reasons", [])
            flags[clean_sym] = {"severity": severity, "reasons": reasons, "flag_count": 1, "source": "active"}
    # Historical signals from tracker
    if SIGNAL_TRACKER.exists():
        with open(SIGNAL_TRACKER) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                sym = entry.get("symbol", "")
                if not sym:
                    continue
                clean_sym = sym.replace("USDC", "USDT") if sym.endswith("USDC") else sym
                if clean_sym in flags:
                    flags[clean_sym]["flag_count"] += 1
                else:
                    flags[clean_sym] = {"severity": entry.get("severity", 1), "reasons": entry.get("reasons", []), "flag_count": 1, "source": "historical"}
    return flags


def main():
    print(f"\n{'#'*80}")
    print(f"  BINANCE FUTURES SYMBOL SCANNER & RANKER")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'#'*80}\n")
    # Step 1: Fetch exchange info
    print("1. Fetching Binance exchange info...")
    all_symbols = fetch_exchange_info()
    print(f"   Found {len(all_symbols)} USDT perpetual futures (listed >6 months)")
    # Step 2: Fetch 24h volume
    print("2. Fetching 24h tickers...")
    tickers = fetch_24h_tickers()
    # Merge and filter by liquidity
    MIN_DAILY_VOL_USD = 5_000_000  # $5M minimum daily volume
    candidates = []
    for s in all_symbols:
        sym = s["symbol"]
        t = tickers.get(sym, {})
        vol_usd = t.get("volume_usd", 0)
        if vol_usd < MIN_DAILY_VOL_USD:
            continue
        s.update(t)
        s["volume_usd"] = vol_usd
        candidates.append(s)
    candidates.sort(key=lambda x: x["volume_usd"], reverse=True)
    print(f"   {len(candidates)} symbols pass liquidity filter (>${MIN_DAILY_VOL_USD/1e6:.0f}M daily)")
    # Step 3: Load current symbols.json
    current_symbols = set()
    if SYMBOLS_JSON.exists():
        with open(SYMBOLS_JSON) as f:
            current_symbols = set(json.load(f))
    print(f"   Current symbols.json: {len(current_symbols)} symbols")
    # Step 4: Load manipulation flags
    print("3. Loading manipulation flags...")
    manip_flags = load_manipulation_flags()
    manip_suspects = {sym for sym, info in manip_flags.items() if info["flag_count"] >= 3 or info["severity"] >= 3}
    print(f"   {len(manip_flags)} symbols with manipulation flags, {len(manip_suspects)} suspects (>=3 flags or severity>=3)")
    # Step 5: Fetch klines and rank
    print(f"\n4. Fetching 15m klines & ranking {len(candidates)} symbols...")
    ranked = []
    errors = 0
    for i, s in enumerate(candidates):
        sym = s["symbol"]
        try:
            df_15m = fetch_klines(sym, "15m", 500)  # ~5 days of 15m data
            if df_15m is None or len(df_15m) < 50:
                errors += 1
                continue
            scores = rank_symbol(df_15m)
            if scores is None:
                errors += 1
                continue
            entry = {**s, **scores}
            entry["in_current"] = sym in current_symbols
            entry["is_suspect"] = sym in manip_suspects
            entry["manip_flag_count"] = manip_flags.get(sym, {}).get("flag_count", 0)
            entry["manip_severity"] = manip_flags.get(sym, {}).get("severity", 0)
            entry["manip_reasons"] = manip_flags.get(sym, {}).get("reasons", [])
            ranked.append(entry)
        except Exception as e:
            errors += 1
            if errors < 5:
                print(f"   ERROR {sym}: {e}")
        if (i + 1) % 25 == 0:
            print(f"   Processed {i+1}/{len(candidates)} ({errors} errors)...")
            time.sleep(0.5)  # Rate limit
        else:
            time.sleep(0.1)
    print(f"   Ranked {len(ranked)} symbols ({errors} errors)")
    # Step 6: Analysis
    print(f"\n{'='*80}")
    print(f"  ANALYSIS RESULTS")
    print(f"{'='*80}")
    # Categorize
    in_current_good = []  # In symbols.json, good metrics
    in_current_bad = []   # In symbols.json, poor metrics (remove candidates)
    not_in_good = []      # Not in symbols.json, good metrics (add candidates)
    not_in_bad = []       # Not in symbols.json, poor metrics (skip)
    for r in ranked:
        sym = r["symbol"]
        vol = r["volume_usd"]
        signal = r.get("signal_strength", 0)
        atr_pct = r.get("atr_pct", 0)
        is_suspect = r["is_suspect"]
        # Quality score: combination of volume, signal strength, volatility
        # Good symbol = liquid + volatile enough to trade + strong signals + not manipulated
        quality = 0
        quality += min(vol / 50_000_000, 3.0) * 30  # Volume: up to 90 pts for $150M+
        quality += min(atr_pct, 3.0) * 10            # Volatility: up to 30 pts for 3%+ ATR
        quality += min(signal / 20, 2.0) * 15         # Signal strength: up to 30 pts
        if is_suspect:
            quality -= 40  # Manipulation penalty
        if r["manip_flag_count"] >= 2:
            quality -= 20
        r["quality_score"] = quality
        if r["in_current"]:
            if quality < 20 or is_suspect:
                in_current_bad.append(r)
            else:
                in_current_good.append(r)
        else:
            if quality >= 50 and not is_suspect:
                not_in_good.append(r)
            else:
                not_in_bad.append(r)
    # Sort
    in_current_bad.sort(key=lambda x: x["quality_score"])
    not_in_good.sort(key=lambda x: x["quality_score"], reverse=True)
    # ── MANIPULATION SUSPECTS IN CURRENT SYMBOLS ──
    suspect_in_current = [r for r in ranked if r["in_current"] and r["is_suspect"]]
    if suspect_in_current:
        print(f"\n  ⚠️  MANIPULATION SUSPECTS IN CURRENT SYMBOLS ({len(suspect_in_current)}):")
        print(f"  {'Symbol':20s} {'Vol($M)':>8s} {'Flags':>5s} {'Sev':>4s} {'Quality':>8s} Reasons")
        print(f"  {'-'*20} {'-'*8} {'-'*5} {'-'*4} {'-'*8} {'-'*30}")
        for r in suspect_in_current:
            reasons = ", ".join(r["manip_reasons"][:3]) if r["manip_reasons"] else "-"
            print(f"  {r['symbol']:20s} {r['volume_usd']/1e6:8.1f} {r['manip_flag_count']:5d} {r['manip_severity']:4d} {r['quality_score']:8.1f} {reasons}")
    # ── REMOVE CANDIDATES ──
    print(f"\n  🔴 REMOVE CANDIDATES — Low quality or suspect ({len(in_current_bad)}):")
    print(f"  {'Symbol':20s} {'Vol($M)':>8s} {'ATR%':>6s} {'Signal':>7s} {'Quality':>8s} {'Reason':30s}")
    print(f"  {'-'*20} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*30}")
    for r in in_current_bad[:30]:
        reason = "MANIPULATED" if r["is_suspect"] else ("LOW_VOL" if r["volume_usd"] < 10e6 else "LOW_QUALITY")
        print(f"  {r['symbol']:20s} {r['volume_usd']/1e6:8.1f} {r.get('atr_pct',0):6.2f} {r.get('signal_strength',0):7.1f} {r['quality_score']:8.1f} {reason}")
    # ── ADD CANDIDATES ──
    print(f"\n  🟢 ADD CANDIDATES — High quality, not in symbols.json ({len(not_in_good)}):")
    print(f"  {'Symbol':20s} {'Vol($M)':>8s} {'ATR%':>6s} {'Signal':>7s} {'Quality':>8s} {'Age(d)':>7s}")
    print(f"  {'-'*20} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*7}")
    for r in not_in_good[:30]:
        print(f"  {r['symbol']:20s} {r['volume_usd']/1e6:8.1f} {r.get('atr_pct',0):6.2f} {r.get('signal_strength',0):7.1f} {r['quality_score']:8.1f} {r['age_days']:7d}")
    # ── CURRENT SYMBOLS NOT FOUND ON BINANCE ──
    all_scanned = {r["symbol"] for r in ranked}
    missing_from_scan = current_symbols - all_scanned
    # Filter out USDC pairs (they wouldn't match)
    missing_usdt = {s for s in missing_from_scan if s.endswith("USDT")}
    if missing_usdt:
        print(f"\n  ❓ CURRENT SYMBOLS NOT FOUND IN SCAN ({len(missing_usdt)}):")
        for s in sorted(missing_usdt):
            print(f"     {s}")
    # ── SUMMARY STATS ──
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"  Total scanned:           {len(ranked)}")
    print(f"  Currently in symbols.json: {len(current_symbols)}")
    print(f"  In current + good:       {len(in_current_good)}")
    print(f"  In current + bad:        {len(in_current_bad)} ← REMOVE")
    print(f"  Not in current + good:   {len(not_in_good)} ← ADD")
    print(f"  Not in current + bad:    {len(not_in_bad)} (skip)")
    print(f"  Manipulation suspects:   {len(manip_suspects)}")
    if not_in_good:
        print(f"\n  Top 10 ADD suggestions: {', '.join(r['symbol'] for r in not_in_good[:10])}")
    if in_current_bad:
        print(f"  Top 10 REMOVE suggestions: {', '.join(r['symbol'] for r in in_current_bad[:10])}")
    # Save full results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_scanned": len(ranked),
            "current_count": len(current_symbols),
            "add_candidates": len(not_in_good),
            "remove_candidates": len(in_current_bad),
            "manipulation_suspects": len(manip_suspects),
        },
        "add": [{"symbol": r["symbol"], "volume_usd": r["volume_usd"], "quality": r["quality_score"], "atr_pct": r.get("atr_pct", 0), "signal_strength": r.get("signal_strength", 0), "age_days": r["age_days"]} for r in not_in_good[:50]],
        "remove": [{"symbol": r["symbol"], "volume_usd": r["volume_usd"], "quality": r["quality_score"], "reason": "MANIPULATED" if r["is_suspect"] else "LOW_QUALITY", "manip_flags": r["manip_flag_count"]} for r in in_current_bad],
        "suspects_in_current": [{"symbol": r["symbol"], "flags": r["manip_flag_count"], "severity": r["manip_severity"], "reasons": r["manip_reasons"]} for r in suspect_in_current],
        "all_ranked": [{"symbol": r["symbol"], "volume_usd": r["volume_usd"], "quality": r["quality_score"], "in_current": r["in_current"], "is_suspect": r["is_suspect"], "wt_composite": r.get("wt_composite", 0), "stoch_composite": r.get("stoch_composite", 0), "band_score": r.get("band_score", 0), "atr_pct": r.get("atr_pct", 0), "signal_strength": r.get("signal_strength", 0)} for r in sorted(ranked, key=lambda x: x["quality_score"], reverse=True)],
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Full results saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
