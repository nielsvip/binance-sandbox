#!/usr/bin/env python3
"""poly_backtest.py — Polymarket strategy backtester (4 strategies).

Data source: Active Polymarket markets with full historical price series via CLOB API.
Closed markets don't expose price history, so we walk-forward on active markets.

Strategies:
  arb       — YES+NO arbitrage (combined price < 0.97 = risk-free profit)
  indicator — Stochastic RSI momentum (mirrors poly_manage.py entry logic)
  oracle    — Oracle lag detection vs Binance for BTC/ETH price markets
  mm        — Market making spread simulation

Usage:
  python poly_backtest.py                           # all strategies, 100 markets
  python poly_backtest.py --strategy arb
  python poly_backtest.py --strategy oracle --oracle-symbol BTCUSDC
  python poly_backtest.py --markets 150 --fidelity 60
  python poly_backtest.py --no-cache                # force re-download
"""

import argparse
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_backtest")

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
BINANCE_URL = "https://api.binance.com/api/v3"
CACHE_DIR = Path("./data/poly/backtest_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

POLY_FEE_RATE = 0.02
SLIPPAGE = 0.005
ARB_THRESHOLD = 0.97
TP_PCT = 0.15
DC_PERIOD = 20
STOCH_LEN = 14
STOCH_SMOOTH_K = 3
STOCH_SMOOTH_D = 3
MM_SPREAD_PCT = 0.02
MM_MAX_INVENTORY = 200
ORACLE_MIN_MOVE_PCT = 0.005
ORACLE_LAG_WINDOW = 5
REQUEST_DELAY = 0.25
MIN_HISTORY_BARS = 50


# ── Cache Helpers ──────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return CACHE_DIR / f"{safe[:120]}.json"

def _cache_get(key: str) -> Optional[object]:
    p = _cache_path(key)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _cache_set(key: str, data: object):
    try:
        with open(_cache_path(key), "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Cache write failed: {e}")


# ── Data Fetching ──────────────────────────────────────────────────────────────

def fetch_markets(limit: int = 100, use_cache: bool = True, active_only: bool = True) -> List[dict]:
    status = "active" if active_only else "all"
    key = f"markets_{status}_{limit}"
    if use_cache:
        cached = _cache_get(key)
        if cached:
            logger.info(f"Cache hit: {len(cached)} markets")
            return cached
    results = []
    offset = 0
    per_page = min(100, limit)
    params_base = {"order": "volumeNum", "ascending": "false"}
    if active_only:
        params_base["active"] = "true"
        params_base["closed"] = "false"
    while len(results) < limit:
        try:
            params = {**params_base, "limit": per_page, "offset": offset}
            resp = requests.get(f"{GAMMA_URL}/markets", params=params, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            offset += per_page
            if len(batch) < per_page:
                break
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.error(f"fetch_markets error at offset {offset}: {e}")
            break
    results = results[:limit]
    _cache_set(key, results)
    logger.info(f"Fetched {len(results)} markets")
    return results

def fetch_price_history(token_id: str, fidelity_mins: int = 60, use_cache: bool = True) -> List[dict]:
    key = f"hist_{token_id[:30]}_{fidelity_mins}"
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    try:
        resp = requests.get(f"{CLOB_URL}/prices-history", params={"market": token_id, "interval": "max", "fidelity": fidelity_mins}, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("history", [])
        _cache_set(key, data)
        time.sleep(REQUEST_DELAY)
        return data
    except Exception as e:
        logger.warning(f"fetch_price_history {token_id[:20]}: {e}")
        return []

def fetch_binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int, use_cache: bool = True) -> List[list]:
    key = f"bnb_{symbol}_{interval}_{start_ms}_{end_ms}"
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    all_klines = []
    params = {"symbol": symbol, "interval": interval, "limit": 1000, "startTime": start_ms, "endTime": end_ms}
    while True:
        try:
            resp = requests.get(f"{BINANCE_URL}/klines", params=params, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_klines.extend(batch)
            if len(batch) < 1000:
                break
            params["startTime"] = batch[-1][0] + 1
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.error(f"Binance klines error: {e}")
            break
    _cache_set(key, all_klines)
    return all_klines


# ── Data Preparation ──────────────────────────────────────────────────────────

def parse_market(m: dict) -> Optional[dict]:
    try:
        token_ids = json.loads(m.get("clobTokenIds", "[]"))
        if len(token_ids) < 2:
            return None
        events = m.get("events", [])
        ticker = events[0].get("ticker", "") if events else ""
        if not ticker:
            ticker = m.get("slug", f"PM:{m['id']}")
        return {"market_id": m["id"], "question": m.get("question", ""), "ticker": ticker, "yes_token": token_ids[0], "no_token": token_ids[1], "volume": float(m.get("volumeNum", 0) or 0), "liquidity": float(m.get("liquidityNum", 0) or 0), "end_date": m.get("endDateIso"), "last_price": float(m.get("lastTradePrice", 0.5) or 0.5)}
    except Exception as e:
        logger.debug(f"parse_market failed: {e}")
        return None

def history_to_series(history: List[dict]) -> pd.Series:
    if not history:
        return pd.Series(dtype=float)
    df = pd.DataFrame(history)
    df["ts"] = pd.to_datetime(df["t"], unit="s", utc=True)
    df["price"] = df["p"].astype(float).clip(0.001, 0.999)
    df = df.sort_values("ts").drop_duplicates("ts").set_index("ts")
    return df["price"]

def resample_to_ohlc(series: pd.Series, freq: str = "1h") -> pd.DataFrame:
    if series.empty:
        return pd.DataFrame()
    ohlc = series.resample(freq).ohlc().dropna()
    return ohlc

def compute_stoch_rsi(close: pd.Series) -> Tuple[pd.Series, pd.Series]:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=STOCH_LEN - 1, min_periods=STOCH_LEN).mean()
    loss = (-delta.clip(upper=0)).ewm(com=STOCH_LEN - 1, min_periods=STOCH_LEN).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    rsi_min = rsi.rolling(STOCH_LEN).min()
    rsi_max = rsi.rolling(STOCH_LEN).max()
    stoch_raw = 100 * (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    k = stoch_raw.rolling(STOCH_SMOOTH_K).mean()
    d = k.rolling(STOCH_SMOOTH_D).mean()
    return k, d

def compute_dc(high: pd.Series, low: pd.Series) -> Tuple[pd.Series, pd.Series]:
    return high.rolling(DC_PERIOD).max(), low.rolling(DC_PERIOD).min()

def compute_ema(close: pd.Series, period: int = 20) -> pd.Series:
    return close.ewm(span=period, min_periods=period).mean()

def bar_freq(fidelity_mins: int) -> str:
    return f"{fidelity_mins}min" if fidelity_mins < 60 else f"{fidelity_mins // 60}h"


# ── Strategy 1: ARB (Momentum proxy + live scanner note) ─────────────────────
# True YES+NO arb needs live order book (ASK_YES + ASK_NO < $1).
# Last-trade prices always sum to $1 by CLOB design, so historical arb can't be
# detected from price history alone. Instead we test the closest proxy:
# momentum scalps on large bars — the same moves that create stale-order arb pockets.
# Separately run poly_arb_scanner.py to detect live order book opportunities.

ARB_MOMENTUM_THRESHOLD = 0.015  # 1.5% bar move required to "see" an arb opportunity
ARB_REVERT_BARS = 3              # expect partial reversion within 3 bars

def run_arb_strategy(markets: List[dict], fidelity_mins: int, use_cache: bool) -> dict:
    """Arb proxy: momentum scalp on large price bars (proxy for stale-order arb pockets).

    True YES+NO arb (buying both sides < $1) needs live order book data.
    This simulates what an arb bot captures: rapid moves where market is slow to update.
    A positive Sharpe here means the market has enough oracle lag / stale orders to exploit.
    """
    logger.info("Running ARB (momentum proxy) strategy...")
    trades = []
    markets_checked = 0
    freq = bar_freq(fidelity_mins)

    for mkt in markets:
        parsed = parse_market(mkt)
        if not parsed:
            continue
        hist = fetch_price_history(parsed["yes_token"], fidelity_mins, use_cache)
        if len(hist) < 30:
            continue
        series = history_to_series(hist)
        ohlc = resample_to_ohlc(series, freq)
        if len(ohlc) < 20:
            continue
        markets_checked += 1
        closes = ohlc["close"].values
        for i in range(1, len(closes) - ARB_REVERT_BARS):
            bar_move = (closes[i] - closes[i - 1]) / closes[i - 1]
            if abs(bar_move) < ARB_MOMENTUM_THRESHOLD:
                continue
            direction = 1 if bar_move > 0 else -1
            entry_p = closes[i] * (1 + SLIPPAGE)
            exit_idx = min(i + ARB_REVERT_BARS, len(closes) - 1)
            exit_p = closes[exit_idx]
            raw_pnl = direction * (exit_p - entry_p)
            fee = POLY_FEE_RATE * max(raw_pnl, 0)
            trades.append({"market": parsed["ticker"][:30], "bar_move_pct": round(bar_move * 100, 2), "entry": round(entry_p, 4), "exit": round(exit_p, 4), "net_pnl": round(raw_pnl - fee, 4), "pnl_pct": round((raw_pnl - fee) / entry_p, 4)})

    return _compile_results("ARB", trades, markets_checked, {"momentum_threshold_pct": f"{ARB_MOMENTUM_THRESHOLD*100:.1f}%", "exit_after_bars": ARB_REVERT_BARS, "note": "Proxy for live order-book arb. Run poly_arb_scanner.py for real-time YES+NO order book scanning."})


# ── Strategy 2: INDICATOR (Stoch RSI Momentum) ────────────────────────────────

def run_indicator_strategy(markets: List[dict], fidelity_mins: int, use_cache: bool) -> dict:
    """Walk-forward stochastic RSI momentum on YES token price series."""
    logger.info("Running INDICATOR strategy...")
    trades = []
    markets_checked = 0
    freq = bar_freq(fidelity_mins)

    for mkt in markets:
        parsed = parse_market(mkt)
        if not parsed:
            continue
        hist = fetch_price_history(parsed["yes_token"], fidelity_mins, use_cache)
        if len(hist) < MIN_HISTORY_BARS:
            continue
        series = history_to_series(hist)
        ohlc = resample_to_ohlc(series, freq)
        if len(ohlc) < MIN_HISTORY_BARS:
            continue
        avg_price = float(ohlc["close"].mean())
        if avg_price < 0.05 or avg_price > 0.95:
            continue
        markets_checked += 1
        k, _ = compute_stoch_rsi(ohlc["close"])
        dc_high, dc_low = compute_dc(ohlc["high"], ohlc["low"])
        ema20 = compute_ema(ohlc["close"])
        open_pos = None
        for i in range(30, len(ohlc)):
            price = ohlc["close"].iloc[i]
            k_val = k.iloc[i]
            em = ema20.iloc[i]
            if math.isnan(k_val) or math.isnan(em):
                continue
            if open_pos is not None:
                entry_p, side, entry_i = open_pos
                dc_l = dc_low.iloc[i]
                dc_h = dc_high.iloc[i]
                tp_p = entry_p * (1 + TP_PCT) if side == "YES" else entry_p * (1 - TP_PCT)
                sl_yes = side == "YES" and not math.isnan(dc_l) and price < dc_l
                sl_no = side == "NO" and not math.isnan(dc_h) and price > dc_h
                tp_hit = (side == "YES" and price >= tp_p) or (side == "NO" and price <= tp_p)
                max_hold = (i - entry_i) > 72
                if tp_hit or sl_yes or sl_no or max_hold or i == len(ohlc) - 1:
                    raw_pnl = (price - entry_p) if side == "YES" else (entry_p - price)
                    fee = POLY_FEE_RATE * max(raw_pnl, 0)
                    net = raw_pnl - fee
                    trades.append({"market": parsed["ticker"][:30], "side": side, "entry": round(entry_p, 4), "exit": round(price, 4), "net_pnl": round(net, 4), "pnl_pct": round(net / entry_p, 4), "reason": "TP" if tp_hit else ("SL" if (sl_yes or sl_no) else "MAXHOLD")})
                    open_pos = None
            if open_pos is None and 0.25 < price < 0.75:
                t_up = price > em
                if k_val < 20 and t_up:
                    open_pos = (price * (1 + SLIPPAGE), "YES", i)
                elif k_val > 80 and not t_up:
                    open_pos = ((1 - price) * (1 + SLIPPAGE), "NO", i)

    return _compile_results("INDICATOR", trades, markets_checked, {"tp_pct": TP_PCT, "entry": "StochRSI K<20 (YES) | K>80 (NO)", "exit": "15% TP | DC channel SL | 72-bar max hold"})


# ── Strategy 3: ORACLE LAG ────────────────────────────────────────────────────

def run_oracle_strategy(markets: List[dict], fidelity_mins: int, use_cache: bool, oracle_symbol: str = "BTCUSDC") -> dict:
    """Measure Binance→Polymarket information lag on crypto price markets."""
    logger.info(f"Running ORACLE LAG strategy vs {oracle_symbol}...")
    keywords = ["btc", "bitcoin", "eth", "ethereum", "crypto", "price", "above", "below", "$", "usd"]
    crypto_mkts = [m for m in markets if any(kw in m.get("question", "").lower() for kw in keywords)]
    logger.info(f"Found {len(crypto_mkts)} crypto-related markets out of {len(markets)}")
    if not crypto_mkts:
        return {"strategy": "ORACLE", "error": "No BTC/ETH price markets in active set — try --markets 300 or add broader crypto markets", "markets_checked": 0, "trades": 0}

    oracle_fidelity = max(fidelity_mins, 5)
    bnb_interval = f"{oracle_fidelity}m" if oracle_fidelity < 60 else "1h"
    lag_stats = []
    trades = []
    markets_checked = 0

    for mkt in crypto_mkts[:40]:
        parsed = parse_market(mkt)
        if not parsed:
            continue
        poly_hist = fetch_price_history(parsed["yes_token"], oracle_fidelity, use_cache)
        if len(poly_hist) < 20:
            continue
        poly_s = history_to_series(poly_hist)
        if poly_s.empty:
            continue
        start_ms = int(poly_s.index[0].timestamp() * 1000)
        end_ms = int(poly_s.index[-1].timestamp() * 1000)
        bnb_klines = fetch_binance_klines(oracle_symbol, bnb_interval, start_ms, end_ms, use_cache)
        if len(bnb_klines) < 20:
            continue
        markets_checked += 1
        bnb_df = pd.DataFrame(bnb_klines, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "qav", "trades", "tbav", "tqav", "ignore"])
        bnb_df["ts"] = pd.to_datetime(bnb_df["open_time"], unit="ms", utc=True)
        bnb_s = bnb_df.set_index("ts")["close"].astype(float)
        resample_rule = f"{oracle_fidelity}min" if oracle_fidelity < 60 else f"{oracle_fidelity // 60}h"
        poly_r = poly_s.resample(resample_rule).last().dropna()
        bnb_r = bnb_s.resample(resample_rule).last().dropna()
        merged = pd.DataFrame({"poly": poly_r, "bnb": bnb_r}).dropna()
        if len(merged) < 15:
            continue
        merged["poly_chg"] = merged["poly"].pct_change()
        merged["bnb_chg"] = merged["bnb"].pct_change()
        corrs = {lag: merged["poly_chg"].corr(merged["bnb_chg"].shift(lag)) for lag in range(0, ORACLE_LAG_WINDOW + 1)}
        corrs = {k: v for k, v in corrs.items() if not math.isnan(v)}
        if not corrs:
            continue
        best_lag = max(corrs, key=lambda l: abs(corrs[l]))
        lag_stats.append({"market": parsed["ticker"][:40], "question": parsed["question"][:60], "best_lag_bars": best_lag, "best_corr": round(corrs[best_lag], 3), "lag_mins": best_lag * oracle_fidelity, "corr_at_lag0": round(corrs.get(0, 0), 3)})
        signal_mask = (merged["bnb_chg"].abs() > ORACLE_MIN_MOVE_PCT) & (merged["poly_chg"].abs() < ORACLE_MIN_MOVE_PCT * 0.3)
        signal_rows = merged[signal_mask]
        for idx_pos in range(len(signal_rows) - best_lag - 1):
            row = signal_rows.iloc[idx_pos]
            pos_in_merged = merged.index.get_indexer([signal_rows.index[idx_pos]], method="nearest")[0]
            if pos_in_merged + best_lag + 1 >= len(merged):
                continue
            direction = 1 if row["bnb_chg"] > 0 else -1
            entry_p = merged.iloc[pos_in_merged]["poly"] * (1 + SLIPPAGE)
            exit_p = merged.iloc[pos_in_merged + best_lag + 1]["poly"]
            raw_pnl = direction * (exit_p - entry_p)
            fee = POLY_FEE_RATE * max(raw_pnl, 0)
            trades.append({"market": parsed["ticker"][:30], "lag_bars": best_lag, "bnb_move_pct": round(row["bnb_chg"] * 100, 3), "net_pnl": round(raw_pnl - fee, 4), "pnl_pct": round((raw_pnl - fee) / entry_p, 4)})

    avg_lag = round(float(np.mean([s["lag_mins"] for s in lag_stats])), 1) if lag_stats else 0
    avg_corr = round(float(np.mean([abs(s["best_corr"]) for s in lag_stats])), 3) if lag_stats else 0
    extra = {"oracle_symbol": oracle_symbol, "avg_lag_minutes": avg_lag, "avg_correlation_at_best_lag": avg_corr, "oracle_fidelity_mins": oracle_fidelity, "lag_distribution": lag_stats[:10], "interpretation": f"Polymarket lags Binance by ~{avg_lag}min on average. If lag > fidelity, oracle strategy has structural edge."}
    return _compile_results("ORACLE", trades, markets_checked, extra)


# ── Strategy 4: MARKET MAKING ─────────────────────────────────────────────────

def run_mm_strategy(markets: List[dict], fidelity_mins: int, use_cache: bool) -> dict:
    """Simulate posting bid/ask at MM_SPREAD_PCT spread around midpoint."""
    logger.info("Running MARKET MAKING strategy...")
    trades = []
    markets_checked = 0
    freq = bar_freq(fidelity_mins)

    for mkt in markets:
        parsed = parse_market(mkt)
        if not parsed or parsed["volume"] < 10000:
            continue
        hist = fetch_price_history(parsed["yes_token"], fidelity_mins, use_cache)
        if len(hist) < 20:
            continue
        series = history_to_series(hist)
        if len(series) < 20:
            continue
        markets_checked += 1
        ohlc = resample_to_ohlc(series, freq)
        prices = ohlc["close"].values
        half_spread = MM_SPREAD_PCT / 2
        inventory = 0.0
        avg_entry = 0.0
        spread_income = 0.0
        last_price = prices[-1]

        for i in range(1, len(prices)):
            prev_p = prices[i - 1]
            curr_p = prices[i]
            bid = prev_p - half_spread
            ask = prev_p + half_spread
            if curr_p <= bid and inventory < MM_MAX_INVENTORY:
                qty = 10.0
                if inventory == 0:
                    avg_entry = bid
                else:
                    avg_entry = (avg_entry * inventory + bid * qty) / (inventory + qty)
                inventory += qty
                spread_income += half_spread * qty
            if curr_p >= ask and inventory > 0:
                qty_sold = min(10.0, inventory)
                spread_income += half_spread * qty_sold
                inventory -= qty_sold

        inventory_pnl = inventory * (last_price - avg_entry) if inventory > 0 and avg_entry > 0 else 0.0
        fee = POLY_FEE_RATE * max(inventory_pnl, 0)
        net = spread_income + inventory_pnl - fee
        trades.append({"market": parsed["ticker"][:30], "spread_income": round(spread_income, 4), "inventory_pnl": round(inventory_pnl, 4), "final_inventory": round(inventory, 1), "net_pnl": round(net, 4), "pnl_pct": round(net / max(parsed["volume"] * 0.001, 1), 4)})

    total_spread = sum(t["spread_income"] for t in trades)
    total_inv = sum(t["inventory_pnl"] for t in trades)
    extra = {"mm_spread_pct": f"{MM_SPREAD_PCT*100:.1f}%", "total_spread_income": round(total_spread, 2), "total_inventory_pnl": round(total_inv, 2), "note": "Polymarket pays maker rebates on top — actual income higher than simulated"}
    return _compile_results("MM", trades, markets_checked, extra)


# ── Result Compiler & Report ───────────────────────────────────────────────────

def _compile_results(strategy: str, trades: List[dict], markets_checked: int, extra: dict = None) -> dict:
    base = {"strategy": strategy, "markets_checked": markets_checked, "trades": len(trades), "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl_per_trade": 0.0, "avg_pnl_pct": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "best_trade": 0.0, "worst_trade": 0.0}
    base.update(extra or {})
    if not trades:
        return base
    pnls = [t["net_pnl"] for t in trades]
    pnl_pcts = [t.get("pnl_pct", 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    avg_pnl = float(np.mean(pnls))
    std_pnl = float(np.std(pnls)) if len(pnls) > 1 else 1e-9
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    max_dd = float(np.min(cumulative - running_max))
    base.update({"win_rate": round(wins / len(trades) * 100, 1), "total_pnl": round(float(np.sum(pnls)), 4), "avg_pnl_per_trade": round(avg_pnl, 4), "avg_pnl_pct": round(float(np.mean(pnl_pcts)) * 100, 2), "sharpe": round((avg_pnl / std_pnl), 2) if std_pnl > 0 else 0,  # per-trade pool_sharpe (sqrt(252) stripped 2026-04-29 per CLAUDE.md rule 4) "max_drawdown": round(max_dd, 4), "best_trade": round(max(pnls), 4), "worst_trade": round(min(pnls), 4), "sample_trades": trades[:5]})
    return base

def print_report(results: List[dict]):
    print("\n" + "=" * 72)
    print("  POLYMARKET BACKTEST REPORT")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 72)
    skip_keys = {"strategy", "markets_checked", "trades", "win_rate", "total_pnl", "avg_pnl_per_trade", "avg_pnl_pct", "sharpe", "max_drawdown", "best_trade", "worst_trade", "sample_trades", "error", "lag_distribution"}
    for r in results:
        print(f"\n── {r['strategy']} Strategy {'─' * (60 - len(r['strategy']))}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  Markets checked : {r['markets_checked']}")
        print(f"  Total trades    : {r['trades']}")
        print(f"  Win rate        : {r['win_rate']:.1f}%")
        print(f"  Total PnL       : ${r['total_pnl']:.4f}")
        print(f"  Avg PnL/trade   : ${r['avg_pnl_per_trade']:.4f}  ({r['avg_pnl_pct']:.2f}%)")
        print(f"  Sharpe ratio    : {r['sharpe']:.2f}")
        print(f"  Max drawdown    : ${r['max_drawdown']:.4f}")
        print(f"  Best / Worst    : ${r['best_trade']:.4f} / ${r['worst_trade']:.4f}")
        for k, v in r.items():
            if k in skip_keys or isinstance(v, (list, dict)):
                continue
            print(f"  {k:<24}: {v}")
        if r.get("strategy") == "ORACLE" and r.get("lag_distribution"):
            print("  Lag breakdown (top markets):")
            for ls in r["lag_distribution"][:6]:
                print(f"    lag={ls['lag_mins']:>4}min  corr={ls['best_corr']:>6.3f}  {ls['question'][:45]}")
        if r.get("sample_trades"):
            print("  Sample trades:")
            for t in r["sample_trades"][:3]:
                print(f"    {t}")
    print("\n" + "=" * 72)
    print("  STRATEGY RANKING")
    print("=" * 72)
    viable = [r for r in results if isinstance(r.get("trades"), int) and r["trades"] > 0 and "error" not in r]
    if not viable:
        print("  No trades generated — check data or broaden market set.")
    else:
        print(f"  {'Strategy':<12} {'Trades':>7} {'Win%':>7} {'Total PnL':>11} {'Sharpe':>8}  Verdict")
        for r in sorted(viable, key=lambda x: x.get("sharpe", 0), reverse=True):
            sharpe = r.get("sharpe", 0)
            win = r.get("win_rate", 0)
            verdict = "DEPLOY" if sharpe > 1.5 and win > 55 else ("TEST LIVE" if sharpe > 0.8 else ("MARGINAL" if sharpe > 0 else "SKIP"))
            print(f"  {r['strategy']:<12} {r['trades']:>7} {r['win_rate']:>6.1f}% ${r['total_pnl']:>10.4f} {sharpe:>7.2f}  {verdict}")
    print("=" * 72 + "\n")

def save_report(results: List[dict]):
    out = Path("./data/poly/backtest_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    clean = [{k: v for k, v in r.items() if k not in ("sample_trades",)} for r in results]
    with open(out, "w") as f:
        json.dump(clean, f, indent=2, default=str)
    logger.info(f"Full report saved → {out}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket strategy backtester")
    parser.add_argument("--strategy", choices=["arb", "indicator", "oracle", "mm", "all"], default="all")
    parser.add_argument("--markets", type=int, default=100, help="Number of active markets to analyse")
    parser.add_argument("--fidelity", type=int, default=60, help="Bar size in minutes (5, 15, 60 recommended)")
    parser.add_argument("--oracle-symbol", default="BTCUSDC", help="Binance symbol for oracle lag (default BTCUSDC)")
    parser.add_argument("--no-cache", action="store_true", help="Force re-download all data")
    args = parser.parse_args()
    use_cache = not args.no_cache

    logger.info(f"Fetching {args.markets} active markets (cache={'ON' if use_cache else 'OFF'})...")
    markets = fetch_markets(args.markets, use_cache, active_only=True)
    if not markets:
        logger.error("No markets returned from Gamma API.")
        return

    results = []
    if args.strategy in ("arb", "all"):
        results.append(run_arb_strategy(markets, args.fidelity, use_cache))
    if args.strategy in ("indicator", "all"):
        results.append(run_indicator_strategy(markets, args.fidelity, use_cache))
    if args.strategy in ("oracle", "all"):
        results.append(run_oracle_strategy(markets, args.fidelity, use_cache, args.oracle_symbol))
    if args.strategy in ("mm", "all"):
        results.append(run_mm_strategy(markets, args.fidelity, use_cache))

    print_report(results)
    save_report(results)

if __name__ == "__main__":
    main()
