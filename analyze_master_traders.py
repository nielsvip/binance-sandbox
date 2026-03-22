#!/usr/bin/env python3
"""
Finandy Master Trader Analysis
Parses trader trades from XLSX, matches to local klines, computes indicators at entry/exit,
and analyzes what strategies are working per trader.
"""
import json
import os
import re
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache")
XLSX_PATH = Path("/Users/niels/Downloads/finandy_copy_trading_master_traders.xlsx")

# Symbol mapping: Finandy uses "BTC/USDT" but our klines may be BTCUSDC or BTCUSDT
SYMBOL_MAP_CACHE = {}


def find_kline_symbol(finandy_symbol):
    """Map Finandy symbol like 'BTC/USDT' to our kline file prefix like 'BTCUSDC'."""
    if finandy_symbol in SYMBOL_MAP_CACHE:
        return SYMBOL_MAP_CACHE[finandy_symbol]
    base = finandy_symbol.replace("/", "")  # BTC/USDT -> BTCUSDT
    # Try exact match first
    if (KLINES_DIR / f"{base}_1h.json").exists():
        SYMBOL_MAP_CACHE[finandy_symbol] = base
        return base
    # Try USDC variant
    usdc = base.replace("USDT", "USDC")
    if (KLINES_DIR / f"{usdc}_1h.json").exists():
        SYMBOL_MAP_CACHE[finandy_symbol] = usdc
        return usdc
    # Try USDT variant if original was something else
    usdt = base.split("USD")[0] + "USDT"
    if (KLINES_DIR / f"{usdt}_1h.json").exists():
        SYMBOL_MAP_CACHE[finandy_symbol] = usdt
        return usdt
    SYMBOL_MAP_CACHE[finandy_symbol] = None
    return None


def load_klines(symbol, timeframe="1h"):
    """Load klines as DataFrame."""
    path = KLINES_DIR / f"{symbol}_{timeframe}.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def compute_indicators(df):
    """Compute technical indicators on kline DataFrame (RSI, Stoch, MACD, BB, SMA, EMA, ATR, volume metrics)."""
    if df is None or len(df) < 50:
        return df
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    v = df["volume"].values.astype(float)
    # RSI 14
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)
    avg_gain = pd.Series(gain).ewm(span=14, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(span=14, adjust=False).mean().values
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss > 0)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    # Stochastic %K/%D (14,3,3) — vectorized
    h_roll_max = pd.Series(h).rolling(14, min_periods=1).max().values
    l_roll_min = pd.Series(l).rolling(14, min_periods=1).min().values
    denom = h_roll_max - l_roll_min
    stoch_raw = np.where(denom > 0, (c - l_roll_min) / denom * 100, 50.0)
    df["stoch_k_raw"] = stoch_raw
    df["stoch_k"] = df["stoch_k_raw"].rolling(3).mean()
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()
    # MACD (12,26,9)
    ema12 = pd.Series(c).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(c).ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    # Bollinger Bands (20,2)
    df["bb_mid"] = pd.Series(c).rolling(20).mean()
    bb_std = pd.Series(c).rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_pct"] = (pd.Series(c) - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    # SMAs
    for period in [9, 20, 50, 200]:
        df[f"sma_{period}"] = pd.Series(c).rolling(period).mean()
    # EMAs
    for period in [9, 21, 50]:
        df[f"ema_{period}"] = pd.Series(c).ewm(span=period, adjust=False).mean()
    # ATR 14
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    df["atr_14"] = pd.Series(tr).rolling(14).mean()
    df["atr_pct"] = df["atr_14"] / pd.Series(c) * 100
    # Volume SMA
    df["vol_sma_20"] = pd.Series(v).rolling(20).mean()
    df["vol_ratio"] = pd.Series(v) / df["vol_sma_20"]
    # Price position relative to SMAs
    df["price_vs_sma20"] = (pd.Series(c) - df["sma_20"]) / df["sma_20"] * 100
    df["price_vs_sma50"] = (pd.Series(c) - df["sma_50"]) / df["sma_50"] * 100
    # Heikin-Ashi — vectorized approximation (exact recursive HA open is slow, use EMA approx)
    ha_close = (df["open"].values + h + l + c) / 4
    ha_open = np.empty_like(c)
    ha_open[0] = (df["open"].values[0] + c[0]) / 2
    for i in range(1, len(c)):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
    ha_green = ha_close > ha_open
    df["ha_green"] = ha_green
    # Consecutive HA candles — vectorized
    signs = np.where(ha_green, 1, -1)
    streak = np.empty_like(signs)
    streak[0] = signs[0]
    for i in range(1, len(signs)):
        if signs[i] == signs[i - 1]:
            streak[i] = streak[i - 1] + signs[i]
        else:
            streak[i] = signs[i]
    df["ha_streak"] = streak
    return df


def parse_pnl_pct(val):
    """Parse PnL percentage string like '+0.93%' or '-0.49%' to float."""
    if pd.isna(val):
        return np.nan
    s = str(val).replace("%", "").replace("+", "").replace(" ", "").replace("\xa0", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_pnl_usd(val):
    """Parse PnL USD value."""
    if pd.isna(val):
        return np.nan
    s = str(val).replace(" ", "").replace("\xa0", "").replace("+", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_volume(val):
    """Parse volume string with spaces as thousands separator."""
    if pd.isna(val):
        return np.nan
    s = str(val).replace(" ", "").replace("\xa0", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_price(val):
    """Parse price - handles weird format like '2930.005804100' which is actually embedded data."""
    if pd.isna(val):
        return np.nan
    s = str(val).replace(" ", "").replace("\xa0", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_datetime(val):
    """Parse datetime like '06:30:07 18.03.2026 UTC'."""
    if pd.isna(val) or str(val).strip() in ["", "(still open)", "nan"]:
        return None
    s = str(val).strip().replace(" UTC", "")
    try:
        return datetime.strptime(s, "%H:%M:%S %d.%m.%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def estimate_entry_time_from_exit_price(klines_df, exit_price, direction, pnl_pct, exit_time=None):
    """
    For trades without timestamps, try to estimate entry time by working backwards from exit price.
    If we know exit_price, direction and pnl_pct, we can estimate entry_price,
    then find when that price occurred in klines.
    """
    if klines_df is None or pd.isna(exit_price) or pd.isna(pnl_pct):
        return None, None
    # Estimate entry price from PnL%
    # For LONG: pnl_pct = (exit - entry) / entry * 100 * leverage... but we don't always know leverage applied to pnl
    # Actually pnl_pct is on the position, so: entry_price = exit_price / (1 + pnl_pct/100) for LONG
    # For SHORT: entry_price = exit_price / (1 - pnl_pct/100)
    try:
        if direction == "LONG":
            entry_price = exit_price / (1 + pnl_pct / 100)
        else:
            entry_price = exit_price / (1 - pnl_pct / 100)
    except (ZeroDivisionError, ValueError):
        return None, None
    return entry_price, None  # We can't reliably estimate time without more data


def get_indicators_at_time(klines_df, target_time):
    """Get the indicator values at a specific time from the klines DataFrame."""
    if klines_df is None or target_time is None:
        return {}
    target_time = pd.Timestamp(target_time).tz_localize("UTC") if pd.Timestamp(target_time).tzinfo is None else pd.Timestamp(target_time)
    # Find the candle at or just before target_time
    mask = klines_df["timestamp"] <= target_time
    if mask.sum() == 0:
        return {}
    idx = klines_df.loc[mask].index[-1]
    row = klines_df.loc[idx]
    indicators = {}
    for col in ["rsi_14", "stoch_k", "stoch_d", "macd", "macd_signal", "macd_hist", "bb_pct", "bb_upper", "bb_lower", "bb_mid", "sma_9", "sma_20", "sma_50", "sma_200", "ema_9", "ema_21", "ema_50", "atr_14", "atr_pct", "vol_ratio", "price_vs_sma20", "price_vs_sma50", "ha_green", "ha_streak"]:
        if col in row.index and not pd.isna(row[col]):
            indicators[col] = row[col]
    indicators["close"] = row["close"]
    indicators["volume"] = row["volume"]
    return indicators


def get_indicators_at_price(klines_df, target_price, direction="LONG", search_window_days=30):
    """Find closest price match in klines and return indicators there."""
    if klines_df is None or pd.isna(target_price) or target_price <= 0:
        return {}, None
    # Find candle with closest price
    price_diff = (klines_df["close"] - target_price).abs()
    best_idx = price_diff.idxmin()
    best_row = klines_df.loc[best_idx]
    pct_diff = abs(best_row["close"] - target_price) / target_price * 100
    if pct_diff > 5:  # More than 5% off - not a reliable match
        return {}, None
    indicators = {}
    for col in ["rsi_14", "stoch_k", "stoch_d", "macd", "macd_signal", "macd_hist", "bb_pct", "bb_upper", "bb_lower", "bb_mid", "sma_9", "sma_20", "sma_50", "sma_200", "ema_9", "ema_21", "ema_50", "atr_14", "atr_pct", "vol_ratio", "price_vs_sma20", "price_vs_sma50", "ha_green", "ha_streak"]:
        if col in best_row.index and not pd.isna(best_row[col]):
            indicators[col] = best_row[col]
    indicators["close"] = best_row["close"]
    indicators["matched_time"] = best_row["timestamp"]
    return indicators, best_row["timestamp"]


def parse_trader_sheet(xls, sheet_name):
    """Parse a single trader sheet into structured data."""
    df = pd.read_excel(xls, sheet_name=sheet_name, header=None)
    trader_info = {}
    trader_info["name"] = str(df.iloc[1, 1]) if not pd.isna(df.iloc[1, 1]) else sheet_name
    trader_info["master_id"] = str(df.iloc[2, 1]) if not pd.isna(df.iloc[2, 1]) else ""
    trader_info["balance"] = parse_volume(df.iloc[4, 1])
    trader_info["open_pnl"] = parse_pnl_usd(df.iloc[6, 1]) if len(df) > 6 else np.nan
    # Strategy info
    for i in range(len(df)):
        if str(df.iloc[i, 0]).strip() == "Averaging Orders Used":
            trader_info["averaging_30d"] = str(df.iloc[i, 2]) if not pd.isna(df.iloc[i, 2]) else ""
        if str(df.iloc[i, 0]).strip() == "Stop Loss Used":
            trader_info["stoploss_30d"] = str(df.iloc[i, 2]) if not pd.isna(df.iloc[i, 2]) else ""
        if str(df.iloc[i, 0]).strip() == "Signal Based Trades":
            trader_info["signal_based_30d"] = str(df.iloc[i, 2]) if not pd.isna(df.iloc[i, 2]) else ""
        if str(df.iloc[i, 0]).strip() == "Limit Orders":
            trader_info["limit_orders_30d"] = str(df.iloc[i, 2]) if not pd.isna(df.iloc[i, 2]) else ""
        if str(df.iloc[i, 0]).strip() == "Avg Leverage":
            trader_info["avg_leverage_30d"] = str(df.iloc[i, 2]) if not pd.isna(df.iloc[i, 2]) else ""
        if str(df.iloc[i, 0]).strip() == "Cumulative PnL ($)":
            trader_info["pnl_30d"] = parse_pnl_usd(df.iloc[i, 2])
    # Parse trades (row 29 onwards)
    trades = []
    for i in range(29, len(df)):
        row = df.iloc[i]
        symbol = str(row[1]).strip() if not pd.isna(row[1]) else ""
        if not symbol or "/" not in symbol:
            continue
        trade = {
            "position_id": str(row[0]),
            "symbol": symbol,
            "direction": str(row[2]).strip() if not pd.isna(row[2]) else "",
            "status": str(row[3]).strip() if not pd.isna(row[3]) else "",
            "margin_mode": str(row[4]).strip() if not pd.isna(row[4]) else "",
            "leverage": str(row[5]).strip() if not pd.isna(row[5]) else "",
            "amount": parse_volume(row[6]),
            "pnl_usd": parse_pnl_usd(row[7]),
            "pnl_pct": parse_pnl_pct(row[8]),
            "volume_usd": parse_volume(row[9]),
            "entry_time": parse_datetime(row[10]),
            "exit_time": parse_datetime(row[11]),
            "entry_price": parse_price(row[12]),
            "exit_price": parse_price(row[13]),
        }
        # Parse leverage number
        lev_str = trade["leverage"].replace("x", "")
        try:
            trade["leverage_num"] = int(lev_str)
        except ValueError:
            trade["leverage_num"] = 1
        trades.append(trade)
    return trader_info, trades


def analyze_trades():
    """Main analysis function."""
    xls = pd.ExcelFile(XLSX_PATH)
    sheets = [s for s in xls.sheet_names if s != "SUMMARY"]
    # Pre-load and compute indicators for all needed symbols
    klines_cache = {}
    all_traders = []
    all_trades = []
    print("=" * 100)
    print("FINANDY MASTER TRADER ANALYSIS — INDICATOR OVERLAY")
    print("=" * 100)
    for sheet in sheets:
        trader_info, trades = parse_trader_sheet(xls, sheet)
        trader_info["sheet"] = sheet
        all_traders.append(trader_info)
        for t in trades:
            t["trader"] = trader_info["name"]
            t["trader_sheet"] = sheet
        # Load klines for symbols in this trader's trades
        for trade in trades:
            sym = find_kline_symbol(trade["symbol"])
            if sym and sym not in klines_cache:
                for tf in ["1h"]:
                    kdf = load_klines(sym, tf)
                    if kdf is not None and len(kdf) > 50:
                        kdf = compute_indicators(kdf)
                        klines_cache[f"{sym}_{tf}"] = kdf
        all_trades.extend(trades)
    # Enrich trades with indicators
    enriched_count = 0
    for trade in all_trades:
        sym = find_kline_symbol(trade["symbol"])
        if not sym:
            trade["indicators_entry"] = {}
            trade["indicators_exit"] = {}
            continue
        kdf = klines_cache.get(f"{sym}_1h")
        # Get indicators at entry
        if trade["entry_time"]:
            trade["indicators_entry"] = get_indicators_at_time(kdf, trade["entry_time"])
            enriched_count += 1
        elif trade["exit_price"] and trade["pnl_pct"] is not None:
            # Estimate entry price and find in klines
            ep = trade["exit_price"]
            pnl = trade["pnl_pct"]
            d = trade["direction"]
            if d == "LONG":
                est_entry = ep / (1 + pnl / 100) if pnl != -100 else ep
            else:
                est_entry = ep / (1 - pnl / 100) if pnl != 100 else ep
            trade["est_entry_price"] = est_entry
            ind, matched_time = get_indicators_at_price(kdf, est_entry, d)
            trade["indicators_entry"] = ind
            trade["matched_entry_time"] = matched_time
            if ind:
                enriched_count += 1
        else:
            trade["indicators_entry"] = {}
        # Get indicators at exit
        if trade["exit_time"]:
            trade["indicators_exit"] = get_indicators_at_time(kdf, trade["exit_time"])
        elif trade["exit_price"]:
            ind, _ = get_indicators_at_price(kdf, trade["exit_price"], trade["direction"])
            trade["indicators_exit"] = ind
        else:
            trade["indicators_exit"] = {}
    print(f"\nTotal traders: {len(all_traders)}")
    print(f"Total trades: {len(all_trades)}")
    print(f"Trades with indicator data: {enriched_count}")
    closed_trades = [t for t in all_trades if t["status"] == "CLOSED"]
    winners = [t for t in closed_trades if t["pnl_usd"] is not None and t["pnl_usd"] > 0]
    losers = [t for t in closed_trades if t["pnl_usd"] is not None and t["pnl_usd"] < 0]
    print(f"Closed trades: {len(closed_trades)}, Winners: {len(winners)}, Losers: {len(losers)}")
    if closed_trades:
        print(f"Overall win rate: {len(winners)/len(closed_trades)*100:.1f}%")
    # =========================================================================
    # PER-TRADER ANALYSIS
    # =========================================================================
    print("\n" + "=" * 100)
    print("PER-TRADER BREAKDOWN")
    print("=" * 100)
    traders_by_sheet = {}
    for t in all_trades:
        key = t["trader_sheet"]
        if key not in traders_by_sheet:
            traders_by_sheet[key] = []
        traders_by_sheet[key].append(t)
    trader_summaries = []
    for trader_info in sorted(all_traders, key=lambda x: x.get("pnl_30d", 0) or 0, reverse=True):
        sheet = trader_info["sheet"]
        trades = traders_by_sheet.get(sheet, [])
        closed = [t for t in trades if t["status"] == "CLOSED"]
        if not closed:
            continue
        wins = [t for t in closed if t["pnl_usd"] is not None and t["pnl_usd"] > 0]
        losses = [t for t in closed if t["pnl_usd"] is not None and t["pnl_usd"] < 0]
        total_pnl = sum(t["pnl_usd"] for t in closed if t["pnl_usd"] is not None)
        avg_pnl_pct = np.mean([t["pnl_pct"] for t in closed if t["pnl_pct"] is not None])
        wr = len(wins) / len(closed) * 100 if closed else 0
        # Symbols traded
        symbols = list(set(t["symbol"] for t in closed))
        directions = [t["direction"] for t in closed]
        long_pct = directions.count("LONG") / len(directions) * 100 if directions else 0
        leverages = [t["leverage_num"] for t in closed if t.get("leverage_num")]
        avg_lev = np.mean(leverages) if leverages else 0
        # Duration analysis (for trades with timestamps)
        durations = []
        for t in closed:
            if t["entry_time"] and t["exit_time"]:
                dur = (t["exit_time"] - t["entry_time"]).total_seconds() / 3600
                if dur > 0:
                    durations.append(dur)
        print(f"\n{'─'*80}")
        print(f"TRADER: {trader_info['name']}")
        print(f"  Balance: ${trader_info.get('balance', '?')} | 30d PnL: ${trader_info.get('pnl_30d', '?')}")
        print(f"  Strategy: Avg={trader_info.get('averaging_30d', '?')}, SL={trader_info.get('stoploss_30d', '?')}, Signal={trader_info.get('signal_based_30d', '?')}, Limit={trader_info.get('limit_orders_30d', '?')}")
        print(f"  Trades: {len(closed)} closed | Win Rate: {wr:.0f}% | Total PnL: ${total_pnl:.2f} | Avg PnL%: {avg_pnl_pct:.2f}%")
        print(f"  Long%: {long_pct:.0f}% | Avg Leverage: {avg_lev:.0f}x | Symbols: {', '.join(symbols[:8])}")
        if durations:
            print(f"  Avg Duration: {np.mean(durations):.1f}h | Min: {min(durations):.2f}h | Max: {max(durations):.1f}h")
        # Indicator analysis at entry for winning trades
        win_entry_ind = [t["indicators_entry"] for t in wins if t["indicators_entry"]]
        loss_entry_ind = [t["indicators_entry"] for t in losses if t["indicators_entry"]]
        if win_entry_ind or loss_entry_ind:
            print(f"\n  INDICATOR ANALYSIS AT ENTRY:")
            for label, inds in [("WINNERS", win_entry_ind), ("LOSERS", loss_entry_ind)]:
                if not inds:
                    continue
                rsis = [i.get("rsi_14") for i in inds if "rsi_14" in i]
                stochs = [i.get("stoch_k") for i in inds if "stoch_k" in i]
                bb_pcts = [i.get("bb_pct") for i in inds if "bb_pct" in i]
                macds = [i.get("macd_hist") for i in inds if "macd_hist" in i]
                vol_rs = [i.get("vol_ratio") for i in inds if "vol_ratio" in i]
                sma20s = [i.get("price_vs_sma20") for i in inds if "price_vs_sma20" in i]
                ha_streaks = [i.get("ha_streak") for i in inds if "ha_streak" in i]
                atr_pcts = [i.get("atr_pct") for i in inds if "atr_pct" in i]
                print(f"    {label} ({len(inds)} with data):")
                if rsis:
                    print(f"      RSI:   avg={np.mean(rsis):.1f}  med={np.median(rsis):.1f}  [min={min(rsis):.1f}, max={max(rsis):.1f}]")
                if stochs:
                    print(f"      Stoch: avg={np.mean(stochs):.1f}  med={np.median(stochs):.1f}  [min={min(stochs):.1f}, max={max(stochs):.1f}]")
                if bb_pcts:
                    print(f"      BB%B:  avg={np.mean(bb_pcts):.2f}  med={np.median(bb_pcts):.2f}")
                if macds:
                    print(f"      MACD:  avg={np.mean(macds):.4f}  med={np.median(macds):.4f}")
                if vol_rs:
                    print(f"      VolR:  avg={np.mean(vol_rs):.2f}  med={np.median(vol_rs):.2f}")
                if sma20s:
                    print(f"      vs20:  avg={np.mean(sma20s):.2f}%  med={np.median(sma20s):.2f}%")
                if ha_streaks:
                    print(f"      HA:    avg={np.mean(ha_streaks):.1f}  med={np.median(ha_streaks):.1f}")
                if atr_pcts:
                    print(f"      ATR%:  avg={np.mean(atr_pcts):.2f}%")
        # Classify strategy
        strategy_hints = []
        if wr > 70 and avg_pnl_pct < 2:
            strategy_hints.append("SCALPER (high WR, small gains)")
        if len(symbols) <= 2:
            strategy_hints.append(f"FOCUSED ({', '.join(symbols)})")
        if avg_lev >= 20:
            strategy_hints.append("HIGH-LEVERAGE")
        if long_pct > 80:
            strategy_hints.append("LONG-BIASED")
        elif long_pct < 20:
            strategy_hints.append("SHORT-BIASED")
        else:
            strategy_hints.append("BIDIRECTIONAL")
        if trader_info.get("averaging_30d", "").replace("%", "").strip():
            try:
                avg_val = int(trader_info["averaging_30d"].replace("%", ""))
                if avg_val > 50:
                    strategy_hints.append("DCA/AVERAGING")
            except ValueError:
                pass
        if trader_info.get("stoploss_30d", "").replace("%", "").strip():
            try:
                sl_val = int(trader_info["stoploss_30d"].replace("%", ""))
                if sl_val > 50:
                    strategy_hints.append("STOP-LOSS DISCIPLINED")
                elif sl_val == 0:
                    strategy_hints.append("NO STOP-LOSS")
            except ValueError:
                pass
        if durations:
            avg_dur = np.mean(durations)
            if avg_dur < 1:
                strategy_hints.append("ULTRA-SHORT (<1h)")
            elif avg_dur < 4:
                strategy_hints.append("SHORT-TERM (1-4h)")
            elif avg_dur < 24:
                strategy_hints.append("INTRADAY (4-24h)")
            else:
                strategy_hints.append("SWING (>24h)")
        if strategy_hints:
            print(f"\n  STRATEGY: {' | '.join(strategy_hints)}")
        # Per-direction analysis
        for direction in ["LONG", "SHORT"]:
            dir_trades = [t for t in closed if t["direction"] == direction]
            if not dir_trades:
                continue
            dir_wins = [t for t in dir_trades if t["pnl_usd"] and t["pnl_usd"] > 0]
            dir_wr = len(dir_wins) / len(dir_trades) * 100
            dir_pnl = sum(t["pnl_usd"] for t in dir_trades if t["pnl_usd"])
            print(f"  {direction}: {len(dir_trades)} trades, WR={dir_wr:.0f}%, PnL=${dir_pnl:.2f}")
        # Store summary for cross-trader analysis
        trader_summaries.append({
            "name": trader_info["name"],
            "sheet": sheet,
            "balance": trader_info.get("balance", 0),
            "pnl_30d": trader_info.get("pnl_30d", 0),
            "win_rate": wr,
            "avg_pnl_pct": avg_pnl_pct,
            "total_pnl": total_pnl,
            "n_trades": len(closed),
            "long_pct": long_pct,
            "avg_leverage": avg_lev,
            "symbols": symbols,
            "strategy_hints": strategy_hints,
            "win_entry_indicators": win_entry_ind,
            "loss_entry_indicators": loss_entry_ind,
            "avg_duration_h": np.mean(durations) if durations else None,
        })
    # =========================================================================
    # CROSS-TRADER PATTERN ANALYSIS
    # =========================================================================
    print("\n" + "=" * 100)
    print("CROSS-TRADER PATTERN ANALYSIS — WHAT WORKS")
    print("=" * 100)
    # Aggregate all winning entry indicators
    all_win_ind = []
    all_loss_ind = []
    all_win_trades_detail = []
    all_loss_trades_detail = []
    for t in all_trades:
        if t["status"] != "CLOSED" or t["pnl_usd"] is None:
            continue
        if t["pnl_usd"] > 0 and t["indicators_entry"]:
            all_win_ind.append(t["indicators_entry"])
            all_win_trades_detail.append(t)
        elif t["pnl_usd"] < 0 and t["indicators_entry"]:
            all_loss_ind.append(t["indicators_entry"])
            all_loss_trades_detail.append(t)
    print(f"\nTrades with entry indicators: {len(all_win_ind)} winners, {len(all_loss_ind)} losers")
    if all_win_ind and all_loss_ind:
        print("\n--- WINNING vs LOSING ENTRIES (all traders combined) ---")
        for metric in ["rsi_14", "stoch_k", "bb_pct", "macd_hist", "vol_ratio", "price_vs_sma20", "price_vs_sma50", "ha_streak", "atr_pct"]:
            w_vals = [i.get(metric) for i in all_win_ind if metric in i]
            l_vals = [i.get(metric) for i in all_loss_ind if metric in i]
            if w_vals and l_vals:
                w_avg = np.mean(w_vals)
                l_avg = np.mean(l_vals)
                diff = w_avg - l_avg
                sig = "***" if abs(diff) > abs(w_avg) * 0.2 else "**" if abs(diff) > abs(w_avg) * 0.1 else ""
                print(f"  {metric:20s}  WIN avg={w_avg:8.2f}  LOSS avg={l_avg:8.2f}  diff={diff:+8.2f} {sig}")
    # Direction-specific analysis
    for direction in ["LONG", "SHORT"]:
        dir_wins = [t for t in all_win_trades_detail if t["direction"] == direction]
        dir_losses = [t for t in all_loss_trades_detail if t["direction"] == direction]
        if not dir_wins:
            continue
        print(f"\n--- {direction} ENTRIES ---")
        win_inds = [t["indicators_entry"] for t in dir_wins]
        loss_inds = [t["indicators_entry"] for t in dir_losses]
        for metric in ["rsi_14", "stoch_k", "bb_pct", "vol_ratio", "price_vs_sma20", "ha_streak"]:
            w_vals = [i.get(metric) for i in win_inds if metric in i]
            l_vals = [i.get(metric) for i in loss_inds if metric in i]
            if w_vals:
                w_avg = np.mean(w_vals)
                l_avg = np.mean(l_vals) if l_vals else float("nan")
                print(f"  {metric:20s}  WIN avg={w_avg:8.2f} (n={len(w_vals)})  LOSS avg={l_avg:8.2f} (n={len(l_vals)})")
    # =========================================================================
    # BEST STRATEGIES FOUND
    # =========================================================================
    print("\n" + "=" * 100)
    print("TOP STRATEGIES & ACTIONABLE FINDINGS")
    print("=" * 100)
    # Sort by total PnL
    top_traders = sorted(trader_summaries, key=lambda x: x["total_pnl"], reverse=True)[:10]
    print("\n--- TOP 10 TRADERS BY PnL (from sample trades) ---")
    for i, ts in enumerate(top_traders, 1):
        dur_str = f"{ts['avg_duration_h']:.1f}h" if ts["avg_duration_h"] else "?"
        print(f"  {i}. {ts['name'][:25]:25s} PnL=${ts['total_pnl']:8.2f}  WR={ts['win_rate']:5.1f}%  Lev={ts['avg_leverage']:2.0f}x  L/S={ts['long_pct']:3.0f}%/{100-ts['long_pct']:3.0f}%  Dur={dur_str}  [{' | '.join(ts['strategy_hints'][:3])}]")
    # Analyze common patterns among top traders
    top5 = top_traders[:5]
    print("\n--- COMMON PATTERNS IN TOP 5 PROFITABLE TRADERS ---")
    all_hints = []
    for ts in top5:
        all_hints.extend(ts["strategy_hints"])
    hint_counts = {}
    for h in all_hints:
        hint_counts[h] = hint_counts.get(h, 0) + 1
    for h, c in sorted(hint_counts.items(), key=lambda x: -x[1]):
        print(f"  {c}/5 traders: {h}")
    # Analyze the indicators that differentiate winners
    print("\n--- ENTRY CONDITIONS THAT SEPARATE WINNERS FROM LOSERS ---")
    # RSI zones
    if all_win_ind:
        long_win_rsi = [t["indicators_entry"].get("rsi_14") for t in all_win_trades_detail if t["direction"] == "LONG" and "rsi_14" in t["indicators_entry"]]
        short_win_rsi = [t["indicators_entry"].get("rsi_14") for t in all_win_trades_detail if t["direction"] == "SHORT" and "rsi_14" in t["indicators_entry"]]
        long_loss_rsi = [t["indicators_entry"].get("rsi_14") for t in all_loss_trades_detail if t["direction"] == "LONG" and "rsi_14" in t["indicators_entry"]]
        short_loss_rsi = [t["indicators_entry"].get("rsi_14") for t in all_loss_trades_detail if t["direction"] == "SHORT" and "rsi_14" in t["indicators_entry"]]
        if long_win_rsi:
            print(f"\n  LONG winners enter at RSI avg={np.mean(long_win_rsi):.1f} (n={len(long_win_rsi)})")
        if long_loss_rsi:
            print(f"  LONG losers enter at RSI avg={np.mean(long_loss_rsi):.1f} (n={len(long_loss_rsi)})")
        if short_win_rsi:
            print(f"  SHORT winners enter at RSI avg={np.mean(short_win_rsi):.1f} (n={len(short_win_rsi)})")
        if short_loss_rsi:
            print(f"  SHORT losers enter at RSI avg={np.mean(short_loss_rsi):.1f} (n={len(short_loss_rsi)})")
    # Stochastic zones
    if all_win_ind:
        long_win_stoch = [t["indicators_entry"].get("stoch_k") for t in all_win_trades_detail if t["direction"] == "LONG" and "stoch_k" in t["indicators_entry"]]
        short_win_stoch = [t["indicators_entry"].get("stoch_k") for t in all_win_trades_detail if t["direction"] == "SHORT" and "stoch_k" in t["indicators_entry"]]
        if long_win_stoch:
            below_30 = sum(1 for v in long_win_stoch if v < 30)
            above_70 = sum(1 for v in long_win_stoch if v > 70)
            print(f"\n  LONG winners: {below_30}/{len(long_win_stoch)} entered stoch<30, {above_70}/{len(long_win_stoch)} entered stoch>70")
        if short_win_stoch:
            below_30 = sum(1 for v in short_win_stoch if v < 30)
            above_70 = sum(1 for v in short_win_stoch if v > 70)
            print(f"  SHORT winners: {below_30}/{len(short_win_stoch)} entered stoch<30, {above_70}/{len(short_win_stoch)} entered stoch>70")
    # BB position
    if all_win_ind:
        for direction in ["LONG", "SHORT"]:
            win_bb = [t["indicators_entry"].get("bb_pct") for t in all_win_trades_detail if t["direction"] == direction and "bb_pct" in t["indicators_entry"]]
            if win_bb:
                below_0 = sum(1 for v in win_bb if v < 0)
                below_20 = sum(1 for v in win_bb if v < 0.2)
                above_80 = sum(1 for v in win_bb if v > 0.8)
                above_1 = sum(1 for v in win_bb if v > 1.0)
                print(f"  {direction} winners BB%B: below_band={below_0}/{len(win_bb)}, lower_20%={below_20}/{len(win_bb)}, upper_20%={above_80}/{len(win_bb)}, above_band={above_1}/{len(win_bb)}")
    # Volume analysis
    if all_win_ind:
        win_vol = [i.get("vol_ratio") for i in all_win_ind if "vol_ratio" in i]
        loss_vol = [i.get("vol_ratio") for i in all_loss_ind if "vol_ratio" in i]
        if win_vol and loss_vol:
            print(f"\n  Volume at entry: winners avg={np.mean(win_vol):.2f}x avg_vol, losers avg={np.mean(loss_vol):.2f}x avg_vol")
            high_vol_wins = sum(1 for v in win_vol if v > 1.5)
            high_vol_losses = sum(1 for v in loss_vol if v > 1.5)
            print(f"  High volume (>1.5x): {high_vol_wins}/{len(win_vol)} winners, {high_vol_losses}/{len(loss_vol)} losers")
    # HA streak analysis
    if all_win_ind:
        for direction in ["LONG", "SHORT"]:
            win_ha = [t["indicators_entry"].get("ha_streak") for t in all_win_trades_detail if t["direction"] == direction and "ha_streak" in t["indicators_entry"]]
            if win_ha:
                avg_streak = np.mean(win_ha)
                print(f"  {direction} winners: avg HA streak at entry = {avg_streak:.1f}")
    # =========================================================================
    # SPECIFIC STRATEGY PROFILES
    # =========================================================================
    print("\n" + "=" * 100)
    print("TRADER STRATEGY PROFILES (actionable for our system)")
    print("=" * 100)
    for ts in top_traders[:10]:
        name = ts["name"]
        trades = traders_by_sheet.get(ts["sheet"], [])
        closed = [t for t in trades if t["status"] == "CLOSED"]
        if not closed:
            continue
        print(f"\n{'─'*60}")
        print(f"  {name}")
        print(f"  {'─'*56}")
        # Timing analysis
        entry_hours = []
        for t in closed:
            if t["entry_time"]:
                entry_hours.append(t["entry_time"].hour)
        if entry_hours:
            from collections import Counter
            hour_counts = Counter(entry_hours)
            top_hours = hour_counts.most_common(3)
            print(f"  Peak entry hours (UTC): {', '.join(f'{h}:00 ({c}x)' for h, c in top_hours)}")
        # Size analysis
        volumes = [t["volume_usd"] for t in closed if t["volume_usd"]]
        if volumes:
            print(f"  Position sizes: avg=${np.mean(volumes):.0f}, med=${np.median(volumes):.0f}")
        # Win/loss size comparison
        win_sizes = [t["volume_usd"] for t in closed if t["pnl_usd"] and t["pnl_usd"] > 0 and t["volume_usd"]]
        loss_sizes = [t["volume_usd"] for t in closed if t["pnl_usd"] and t["pnl_usd"] < 0 and t["volume_usd"]]
        if win_sizes and loss_sizes:
            print(f"  Win size avg=${np.mean(win_sizes):.0f} vs Loss size avg=${np.mean(loss_sizes):.0f}")
        # PnL distribution
        pnls = [t["pnl_pct"] for t in closed if t["pnl_pct"] is not None]
        if pnls:
            print(f"  PnL% range: [{min(pnls):.2f}%, {max(pnls):.2f}%], avg={np.mean(pnls):.2f}%, med={np.median(pnls):.2f}%")
            # Target and stop
            wins_pnl = [t["pnl_pct"] for t in closed if t["pnl_pct"] is not None and t["pnl_pct"] > 0]
            losses_pnl = [t["pnl_pct"] for t in closed if t["pnl_pct"] is not None and t["pnl_pct"] < 0]
            if wins_pnl:
                print(f"  Typical take-profit: {np.median(wins_pnl):.2f}% (median)")
            if losses_pnl:
                print(f"  Typical stop-loss: {np.median(losses_pnl):.2f}% (median)")
        # Key indicator conditions at winning entries
        win_entries = [t for t in closed if t["pnl_usd"] and t["pnl_usd"] > 0 and t["indicators_entry"]]
        if win_entries:
            # Summarize key conditions
            conditions = []
            rsis = [t["indicators_entry"].get("rsi_14") for t in win_entries if "rsi_14" in t["indicators_entry"]]
            stochs = [t["indicators_entry"].get("stoch_k") for t in win_entries if "stoch_k" in t["indicators_entry"]]
            if rsis:
                conditions.append(f"RSI {np.mean(rsis):.0f}")
            if stochs:
                conditions.append(f"Stoch {np.mean(stochs):.0f}")
            if conditions:
                print(f"  Winning entry conditions: {', '.join(conditions)}")
    print("\n" + "=" * 100)
    print("DONE — Analysis complete")
    print("=" * 100)


if __name__ == "__main__":
    analyze_trades()
